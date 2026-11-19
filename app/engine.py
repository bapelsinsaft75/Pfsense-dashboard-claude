"""Samlar data från pfSense, UniFi och NetFlow och bygger svaret till dashboarden."""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict, deque

from .config import Config, is_public
from .geoip import GeoIP
from .history import History
from .netflow import Flow, FlowParser
from .pfsense import Conn, parse_arp, parse_interfaces, parse_lease, parse_state, wan_counters
from .unifi import parse_client, parse_device

log = logging.getLogger("engine")

PERIODS = {"24h": (86400, 1800), "7d": (7 * 86400, 7200), "30d": (30 * 86400, 43200)}
PROTOS = {1: "icmp", 6: "tcp", 17: "udp", 58: "icmpv6"}
NETFLOW_WINDOW = 60

TYPE_RULES = [
    ("console", r"ps[345]|playstation|xbox|switch-?oled|nintendo|steamdeck"),
    ("tv", r"\btv\b|-tv|tv-|apple-?tv|chromecast|roku|shield|bravia|webos|tizen|firetv|fire-tv"),
    ("phone", r"iphone|galaxy|pixel|android|oneplus|xiaomi|huawei|phone|mobil"),
    ("tablet", r"ipad|tab\b|tablet|kindle"),
    ("laptop", r"macbook|laptop|thinkpad|notebook|surface|bärbar"),
    ("server", r"unraid|nas|server|synology|truenas|proxmox|home-?assistant|homeassistant|\bha\b|raspberry|pi\d?\b"),
    ("speaker", r"sonos|homepod|echo|alexa|nest-?(mini|audio)|speaker|högtalare"),
    ("printer", r"printer|skrivare|\bhp\b|epson|brother|canon"),
    ("robot", r"roborock|roomba|robot|dammsug|vacuum|deebot"),
    ("iot", r"hue|shelly|tasmota|esp|tuya|ikea|tradfri|plug|bulb|lamp|sensor|camera|kamera|doorbell"),
    ("desktop", r"desktop|pc\b|-pc|imac|workstation|dator"),
]


def guess_type(*hints: str | None, kind: str | None = None) -> str:
    text = " ".join(h for h in hints if h).lower()
    for t, pattern in TYPE_RULES:
        if re.search(pattern, text):
            return t
    return "laptop" if kind == "wifi" else "desktop"


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.geo = GeoIP(cfg)
        if cfg.demo:
            db_path = ":memory:"
        else:
            os.makedirs(cfg.data_dir, exist_ok=True)
            db_path = os.path.join(cfg.data_dir, "history.sqlite")
        self.history = History(db_path)

        self.clients: dict[str, dict] = {}  # cid (mac) → info från pfSense
        self.ip_to_cid: dict[str, str] = {}
        self.unifi_clients: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}

        self.rates: dict[str, tuple[float, float]] = {}
        self.wan_rate: tuple[float, float] | None = None
        self._wan_prev: tuple[float, int, int] | None = None
        self.wan_ips: set[str] = set()

        self.conns: dict[tuple, Conn] = {}
        self._prev_bytes: dict[tuple, tuple[int, int]] = {}
        self._prev_states_ts: float | None = None
        self.state_bytes = False
        self.inbound_ports: dict[tuple, float] = {}

        self.nf_parser = FlowParser()
        self.nf_last = 0.0
        self.nf_flows = 0
        self.nf_window: deque = deque()

        self.status = {
            "pfsense": {"enabled": bool(cfg.pfsense_url), "ok": False, "error": None, "last": None},
            "unifi": {"enabled": cfg.unifi_enabled, "ok": False, "error": None, "last": None},
            "netflow": {"enabled": cfg.netflow_port > 0, "ok": False, "error": None, "last": None},
            "geoip": self.geo.status,
        }
        self.raw: dict[str, list] = {}

    # ── hjälp ──
    def cid_for_ip(self, ip: str) -> str:
        return self.ip_to_cid.get(ip) or f"ip-{ip}"

    def history_source(self) -> str:
        if self.cfg.traffic_source in ("netflow", "states"):
            return self.cfg.traffic_source
        return "netflow" if time.time() - self.nf_last < 180 else "states"

    def mark(self, name: str, ok: bool, error: str | None = None) -> None:
        st = self.status[name]
        st["ok"], st["error"] = ok, error
        if ok:
            st["last"] = time.time()

    # ── pfSense ──
    def update_clients(self, leases: list[dict], arp: list[dict]) -> None:
        self.raw["leases"], self.raw["arp"] = leases[:50], arp[:50]
        now = time.time()
        seen_online: set[str] = set()
        for row in leases:
            lease = parse_lease(row)
            if not lease:
                continue
            c = self.clients.setdefault(lease["mac"], {"mac": lease["mac"]})
            c.update(ip=lease["ip"], dhcp_hostname=lease["hostname"], descr=lease["descr"])
            if lease["online"]:
                seen_online.add(lease["mac"])
        for row in arp:
            a = parse_arp(row)
            if not a or not self.cfg.is_lan(a["ip"]):
                continue
            c = self.clients.setdefault(a["mac"], {"mac": a["mac"]})
            c["ip"] = a["ip"]
            if a["hostname"] and a["hostname"] != "?":
                c["arp_hostname"] = a["hostname"]
            if not a["expired"]:
                seen_online.add(a["mac"])
        for mac, c in self.clients.items():
            c["online"] = mac in seen_online
            if c["online"]:
                c["last_seen"] = now
        self._rebuild_ip_map()
        self.history.remember_clients(
            [(mac, mac, c.get("ip"), self.display_name(mac), int(c.get("last_seen") or now))
             for mac, c in self.clients.items() if c.get("online")]
        )

    def _rebuild_ip_map(self) -> None:
        m: dict[str, str] = {}
        for mac, u in self.unifi_clients.items():
            if u.get("ip"):
                m[u["ip"]] = mac
        for mac, c in self.clients.items():
            if c.get("ip"):
                m[c["ip"]] = mac
        self.ip_to_cid = m

    def update_interfaces(self, rows: list[dict]) -> None:
        self.raw["interfaces"] = rows[:20]
        wan, ips = parse_interfaces(rows, self.cfg.wan_interface)
        self.wan_ips = ips
        if not wan:
            return
        counters = wan_counters(wan)
        now = time.time()
        if counters and self._wan_prev:
            t0, rx0, tx0 = self._wan_prev
            dt = now - t0
            if dt > 0 and counters[0] >= rx0 and counters[1] >= tx0:
                self.wan_rate = ((counters[0] - rx0) * 8 / dt, (counters[1] - tx0) * 8 / dt)
        if counters:
            self._wan_prev = (now, counters[0], counters[1])

    def update_states(self, rows: list[dict]) -> None:
        self.raw["states"] = rows[:50]
        now = time.time()
        conns: dict[tuple, Conn] = {}
        for row in rows:
            c = parse_state(row, self.cfg, self.wan_ips)
            if not c:
                continue
            old = conns.get(c.key)
            if old:  # samma anslutning syns på både LAN och WAN: behåll största räknarna
                old.up = max(old.up or 0, c.up or 0) if (old.up is not None or c.up is not None) else None
                old.down = max(old.down or 0, c.down or 0) if (old.down is not None or c.down is not None) else None
            else:
                conns[c.key] = c
        self.state_bytes = any(c.up is not None or c.down is not None for c in conns.values())

        first_poll = self._prev_states_ts is None
        dt = now - (self._prev_states_ts or now)
        record_bytes = self.history_source() == "states"
        per_cid: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        new_bytes: dict[tuple, tuple[int, int]] = {}
        for key, c in conns.items():
            up, down = c.up or 0, c.down or 0
            new_bytes[key] = (up, down)
            prev = self._prev_bytes.get(key)
            is_new = prev is None
            if first_poll:
                d_up = d_down = 0
            elif prev:
                d_up, d_down = max(0, up - prev[0]), max(0, down - prev[1])
            else:
                d_up, d_down = up, down
            cid = self.cid_for_ip(c.lan_ip)
            per_cid[cid][0] += d_down
            per_cid[cid][1] += d_up
            if c.initiated == "in" and c.lan_port is not None:
                self.inbound_ports[(c.lan_ip, c.lan_port, c.proto)] = now
            cc = self.geo.country(c.remote_ip) or ""
            self.history.add(
                cid, cc, c.initiated,
                d_up if record_bytes else 0, d_down if record_bytes else 0,
                conns=1 if (is_new and not first_poll) or first_poll else 0, ts=now,
            )
        self._prev_bytes = new_bytes
        self._prev_states_ts = now
        self.conns = conns
        if self.state_bytes and dt > 0:
            self.rates = {cid: (v[0] * 8 / dt, v[1] * 8 / dt) for cid, v in per_cid.items()}
        for k, t in list(self.inbound_ports.items()):
            if now - t > 3600:
                del self.inbound_ports[k]

    # ── NetFlow ──
    def on_flows(self, flows: list[Flow]) -> None:
        now = time.time()
        self.nf_last = now
        self.nf_flows += len(flows)
        self.mark("netflow", True)
        record = self.history_source() == "netflow"
        is_lan = self.cfg.is_lan
        for f in flows:
            if is_lan(f.src) and not is_lan(f.dst) and is_public(f.dst):
                lan, lport, rem, rport, up, down = f.src, f.sport, f.dst, f.dport, f.bytes, 0
            elif is_lan(f.dst) and not is_lan(f.src) and is_public(f.src):
                lan, lport, rem, rport, up, down = f.dst, f.dport, f.src, f.sport, 0, f.bytes
            else:
                continue
            proto = PROTOS.get(f.proto, str(f.proto))
            direction = self._flow_direction(proto, lan, lport, rem, rport)
            cid = self.cid_for_ip(lan)
            self.nf_window.append((now, cid, up, down))
            if record:
                self.history.add(cid, self.geo.country(rem) or "", direction, up, down,
                                 conns=1 if up else 0, ts=now)

    def _flow_direction(self, proto: str, lan: str, lport: int, rem: str, rport: int) -> str:
        c = self.conns.get((proto, lan, lport, rem, rport))
        if c:
            return c.initiated
        if (lan, lport, proto) in self.inbound_ports:
            return "in"
        if lport and rport and lport < 1024 <= rport:
            return "in"
        return "out"

    def tick(self) -> None:
        """Körs varannan sekund: räkna live-bandbredd från NetFlow om tillståndstabellen saknar bytes."""
        now = time.time()
        while self.nf_window and now - self.nf_window[0][0] > NETFLOW_WINDOW:
            self.nf_window.popleft()
        if self.status["netflow"]["enabled"] and now - self.nf_last > 300:
            self.status["netflow"]["ok"] = False
            self.status["netflow"]["error"] = (
                "Inga flöden mottagna än. Kontrollera softflowd i pfSense." if not self.nf_last
                else "Inga flöden på över 5 minuter."
            )
        if not self.state_bytes and self.nf_window:
            agg: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
            for _, cid, up, down in self.nf_window:
                agg[cid][0] += down
                agg[cid][1] += up
            self.rates = {cid: (v[0] * 8 / NETFLOW_WINDOW, v[1] * 8 / NETFLOW_WINDOW) for cid, v in agg.items()}

    # ── UniFi ──
    def update_unifi(self, sta: list[dict], dev: list[dict]) -> None:
        self.raw["unifi_clients"], self.raw["unifi_devices"] = sta[:50], dev[:20]
        clients = {}
        for row in sta:
            info = parse_client(row)
            if info:
                clients[info["mac"]] = info
        devices = {}
        for row in dev:
            d = parse_device(row)
            if d:
                devices[d["id"]] = d
        self.unifi_clients, self.devices = clients, devices
        self._rebuild_ip_map()

    # ── svar till webbsidan ──
    def display_name(self, cid: str, known: dict | None = None) -> str:
        c = self.clients.get(cid, {})
        u = self.unifi_clients.get(cid, {})
        for v in (u.get("alias"), c.get("descr"), c.get("dhcp_hostname"), u.get("hostname"), c.get("arp_hostname"),
                  (known or {}).get("name")):
            if v and v not in ("?", "*"):
                return str(v)
        ip = c.get("ip") or u.get("ip") or (known or {}).get("ip")
        return ip or (cid[3:] if cid.startswith("ip-") else cid)

    def summary(self, period: str = "24h") -> dict:
        now = time.time()
        span, step = PERIODS.get(period, PERIODS["24h"])
        since = int(now - span)
        totals = self.history.client_totals(since)
        known = self.history.known_clients(since)

        cids = set(self.clients) | set(self.unifi_clients) | set(totals)
        clients = []
        for cid in cids:
            c = self.clients.get(cid, {})
            u = self.unifi_clients.get(cid)
            k = known.get(cid, {})
            ip = c.get("ip") or (u or {}).get("ip") or k.get("ip") or (cid[3:] if cid.startswith("ip-") else None)
            if ip and not self.cfg.is_lan(ip):
                continue
            up, down = totals.get(cid, (0, 0))
            rd, ru = self.rates.get(cid, (0.0, 0.0))
            online = bool(c.get("online")) or u is not None
            name = self.display_name(cid, k)
            item = {
                "id": cid,
                "name": name,
                "ip": ip,
                "mac": cid if not cid.startswith("ip-") else None,
                "online": online,
                "down_bps": rd if online else 0,
                "up_bps": ru if online else 0,
                "bytes": up + down,
                "bytes_up": up,
                "bytes_down": down,
                "conn": None,
            }
            if u:
                conn = {"kind": u["kind"], "dev": u.get("dev")}
                if u["kind"] == "wired":
                    conn["port"] = u.get("port")
                else:
                    conn.update(band=u.get("band"), ssid=u.get("ssid"), rssi=u.get("rssi"))
                dev = self.devices.get(u.get("dev") or "")
                conn["dev_name"] = dev["name"] if dev else None
                item["conn"] = conn
            item["type"] = guess_type(name, c.get("dhcp_hostname"), (u or {}).get("hostname"), (u or {}).get("oui"),
                                      kind=(u or {}).get("kind"))
            clients.append(item)
        clients.sort(key=lambda x: (x["bytes"], x["down_bps"] + x["up_bps"]), reverse=True)

        flows = self.history.country_flows(since)
        index = {(f["cc"], f["dir"]): f for f in flows}
        for c in self.conns.values():  # aktiva anslutningar syns direkt, även innan historik finns
            cc = self.geo.country(c.remote_ip)
            if not cc:
                continue
            f = index.get((cc, c.initiated))
            cid = self.cid_for_ip(c.lan_ip)
            if not f:
                f = index[(cc, c.initiated)] = {"cc": cc, "dir": c.initiated, "bytes": 0, "conns": 0, "clients": []}
                flows.append(f)
            if cid not in f["clients"]:
                f["clients"].append(cid)
        active: dict[tuple, int] = defaultdict(int)
        for c in self.conns.values():
            cc = self.geo.country(c.remote_ip)
            if cc:
                active[(cc, c.initiated)] += 1
        for f in flows:
            f["active"] = active.get((f["cc"], f["dir"]), 0)

        online = [c for c in clients if c["online"]]
        if self.wan_rate:
            now_down, now_up = self.wan_rate
        else:
            now_down = sum(c["down_bps"] for c in clients)
            now_up = sum(c["up_bps"] for c in clients)

        return {
            "mode": "demo" if self.cfg.demo else "live",
            "generated": now,
            "period": period if period in PERIODS else "24h",
            "home": {"name": self.cfg.home_city, "lat": self.cfg.home_lat, "lon": self.cfg.home_lon},
            "traffic_source": self.history_source(),
            "status": self.status,
            "kpi": {
                "total_bytes": sum(c["bytes"] for c in clients),
                "now_down_bps": now_down,
                "now_up_bps": now_up,
                "clients_online": len(online),
                "clients_total": len(clients),
                "wifi": sum(1 for c in online if c["conn"] and c["conn"]["kind"] == "wifi"),
                "wired": sum(1 for c in online if c["conn"] and c["conn"]["kind"] == "wired"),
                "unknown_link": sum(1 for c in online if not c["conn"]),
                "active_conns": len(self.conns),
            },
            "clients": clients,
            "flows": [f for f in flows if f["cc"]],
            "devices": self.device_list(),
            "series": self.history.series(since, step, now),
            "series_step": step,
        }

    def device_list(self) -> list[dict]:
        out = []
        for d in self.devices.values():
            members = [(mac, u) for mac, u in self.unifi_clients.items() if u.get("dev") == d["id"]]
            item = {
                "id": d["id"], "kind": d["kind"], "name": d["name"], "model": d["model"], "online": d["online"],
                "clients": [mac for mac, _ in members],
            }
            parent = self.devices.get(d.get("uplink_mac") or "")
            if d["kind"] == "switch":
                used = {}
                for mac, u in members:
                    if u.get("port"):
                        used[str(u["port"])] = self.display_name(mac)
                infra = {}
                for other in self.devices.values():
                    if other.get("uplink_mac") == d["id"] and other.get("uplink_port"):
                        infra[str(other["uplink_port"])] = other["name"]
                if d.get("own_uplink"):
                    infra[str(d["own_uplink"])] = f"Upplänk till {parent['name']}" if parent else "Upplänk"
                unknown = [p for p in d["ports_up"] if str(p) not in used and str(p) not in infra]
                item.update(ports=d["ports"], used=used, infra=infra, unknown=[str(p) for p in unknown],
                            uplink=d.get("own_uplink"))
            else:
                bands: dict[str, int] = defaultdict(int)
                ssids = set()
                for _, u in members:
                    bands[u.get("band") or "okänt band"] += 1
                    if u.get("ssid"):
                        ssids.add(u["ssid"])
                item.update(bands=dict(bands), ssids=sorted(ssids),
                            uplink=f"{parent['name']}, port {d['uplink_port']}" if parent and d.get("uplink_port") else None)
            out.append(item)
        out.sort(key=lambda x: (x["kind"] != "switch", x["name"].lower()))
        return out
