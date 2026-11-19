"""Samlar data från pfSense, UniFi och NetFlow och bygger svaret till dashboarden."""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict, deque

import statistics

from .alerts import Alerts
from .config import Config, is_public
from .geoip import GeoIP
from .history import History
from .mailer import Mailer
from .netflow import Flow, FlowParser
from .pfsense import (Conn, guess_initiator, parse_arp, parse_gateway, parse_interfaces, parse_lease, parse_openvpn_client,
                      parse_state, wan_counters)
from .plex import PlexServer
from .unraid import Unraid
from .settings import Settings
from .slack import Slack
from .syslog import parse_filterlog, parse_suricata
from .unifi import parse_client, parse_device

log = logging.getLogger("engine")

PERIODS = {"24h": (86400, 1800), "7d": (7 * 86400, 7200), "30d": (30 * 86400, 43200)}
PROTOS = {1: "icmp", 6: "tcp", 17: "udp", 58: "icmpv6"}
NETFLOW_WINDOW = 60
PFB_WINDOW = 600

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
        self.settings = Settings(cfg, persist=not cfg.demo)
        self.mailer = Mailer(cfg)
        self.slack = Slack(cfg)
        self.unraids = [Unraid(u["name"], u["url"], u["key"], cfg.unraid_verify_ssl) for u in cfg.unraid_servers]
        self.plex_servers = [PlexServer(i, p["name"], p["url"], p["token"], cfg.plex_verify_ssl)
                             for i, p in enumerate(cfg.plex_servers)]
        self.alerts = Alerts(self.history, self.mailer, self.settings, self.slack)
        self.gateways: dict[str, dict] = {}
        self.vpn_clients: dict[str, dict] = {}
        self._confirm: dict[str, dict] = {}
        self._clients_polled = False
        self._pfb_hits: dict[str, deque] = defaultdict(deque)
        self.syslog_lines = 0
        self.vpn_ifaces: dict[str, dict] = {}
        self.aliases: dict[str, str] = self.history.aliases()
        self.syslog_last = 0.0

        self.clients: dict[str, dict] = {}  # cid (mac) → info från pfSense
        self.ip_to_cid: dict[str, str] = {}
        self.unifi_clients: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}

        self.rates: dict[str, tuple[float, float]] = {}  # används av webbsidan
        self.state_rates: dict[str, tuple[float, float]] = {}
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
            "vpn": {"enabled": False, "ok": False, "error": None, "last": None, "label": "VPN", "detail": None},
            "syslog": {"enabled": cfg.syslog_port > 0, "ok": False, "error": None, "last": None},
            "geoip": self.geo.status,
            "email": self.mailer.status,
            "slack": self.slack.status,
        }
        self.raw: dict[str, list] = {}

    # ── hjälp ──
    def cid_for_ip(self, ip: str) -> str:
        return self.ip_to_cid.get(ip) or f"ip-{ip}"

    def history_source(self) -> str:
        if self.cfg.traffic_source in ("netflow", "states"):
            return self.cfg.traffic_source
        return "netflow" if time.time() - self.nf_last < 180 else "states"

    def confirm(self, key: str, bad: bool, now: float | None = None) -> bool:
        """Bekräfta ett fel med upprepade kontroller innan det larmar.

        Ett fel räknas en gång per kontrollintervall. När det har setts confirm_count gånger i rad är det bekräftat.
        En lyckad kontroll nollställer räkningen.
        """
        now = now or time.time()
        if not bad:
            self._confirm.pop(key, None)
            return False
        interval = self.settings.get("confirm_interval")
        st = self._confirm.get(key)
        if st is None:
            st = self._confirm[key] = {"first": now, "last": now, "n": 1}
        elif now - st["last"] >= interval * 0.9:
            st["n"] += 1
            st["last"] = now
        return st["n"] >= self.settings.get("confirm_count")

    def confirm_note(self, key: str) -> str:
        st = self._confirm.get(key)
        if not st:
            return ""
        mins = max(1, round((st["last"] - st["first"]) / 60)) if st["n"] > 1 else 0
        return f" Bekräftat efter {st['n']} kontroller" + (f" under {mins} min." if mins else ".")

    def mark(self, name: str, ok: bool, error: str | None = None) -> None:
        st = self.status[name]
        st["ok"], st["error"] = ok, error
        now = time.time()
        key = f"source:{name}"
        if ok:
            st["last"] = now
            if key in self._confirm:
                self.confirm(key, False)
            self.alerts.resolve(key, "Kontakten är återställd.")
        elif name in ("pfsense", "unifi") and self.confirm(key, True, now):
            what = "pfSense" if name == "pfsense" else "UniFi"
            self.alerts.raise_(key, "critical" if name == "pfsense" else "warning", "source_down", f"{what} svarar inte",
                               f"Dashboarden får inte svar från {what}. Senaste fel: {error}.{self.confirm_note(key)}")

    # ── pfSense ──
    def update_clients(self, leases: list[dict], arp: list[dict]) -> None:
        self.raw["leases"], self.raw["arp"] = leases[:50], arp[:50]
        now = time.time()
        seen_online: set[str] = set()
        known_before = set(self.history.known_clients(0)) if self._clients_polled else None
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
        if known_before is not None and known_before and self.settings.get("new_device_alerts"):
            for mac, c in self.clients.items():
                if c.get("online") and mac not in known_before:
                    self.alerts.raise_(f"new:{mac}", "warning", "new_device", f"Ny enhet i nätverket: {self.display_name(mac)}",
                                       f"{self.display_name(mac)} ({c.get('ip')}, MAC {mac.upper()}) har anslutit för första gången.")
        self._clients_polled = True
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
        # OpenVPN-interface (ovpncN) används som reserv om OpenVPN-status saknas
        self.vpn_ifaces = {
            str(r.get("descr") or r.get("name")): {"name": str(r.get("descr") or r.get("name")),
                                                  "up": str(r.get("status", "")).lower() == "up",
                                                  "status": f"interface {r.get('hwif')} {r.get('status')}",
                                                  "remote": r.get("gateway")}
            for r in rows if str(r.get("hwif", "")).startswith(("ovpnc", "ovpns", "tun_wg", "wg"))
        }
        if not self.vpn_clients:
            self._update_vpn_status()
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
                remote=c.remote_ip, port=c.remote_port if c.initiated == "out" else c.lan_port,
            )
        self._prev_bytes = new_bytes
        self._prev_states_ts = now
        self.conns = conns
        if self.state_bytes and dt > 0:
            self.state_rates = {cid: (v[0] * 8 / dt, v[1] * 8 / dt) for cid, v in per_cid.items()}
            if not self._netflow_live():
                self.rates = self.state_rates
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
                                 conns=1 if up else 0, ts=now, remote=rem,
                                 port=rport if direction == "out" else lport)

    def _flow_direction(self, proto: str, lan: str, lport: int, rem: str, rport: int) -> str:
        c = self.conns.get((proto, lan, lport, rem, rport))
        if c:
            return c.initiated
        if (lan, lport, proto) in self.inbound_ports:
            return "in"
        return guess_initiator(lport or None, rport or None, "out")

    def tick(self) -> None:
        """Körs varannan sekund: räkna live-bandbredd från NetFlow om tillståndstabellen saknar bytes."""
        now = time.time()
        while self.nf_window and now - self.nf_window[0][0] > NETFLOW_WINDOW:
            self.nf_window.popleft()
        if self.status["netflow"]["enabled"] and now - self.nf_last > 300:
            p = self.nf_parser
            port = self.cfg.netflow_port
            if not p.received:
                err = (f"Inga NetFlow-paket har kommit fram till UDP-port {port}. Kontrollera att softflowd körs "
                       f"(Status → Services) och skickar till dashboardens IP-adress, och att containern har den IP-adressen.")
            elif now - p.last_received > 300:
                err = f"Inga NetFlow-paket på över 5 minuter (senast från {p.last_exporter})."
            elif not p.flows:
                vers = ", ".join(f"v{v}" for v in p.versions) or "okänd version"
                if p.no_template and not p.templates:
                    err = (f"Tar emot paket från {p.last_exporter} ({vers}) men väntar på mallen som beskriver flödena. "
                           f"softflowd skickar den med jämna mellanrum, vänta några minuter.")
                elif p.errors and not p.packets:
                    err = f"Tar emot paket från {p.last_exporter} men kan inte tolka dem ({vers}). Använd NetFlow version 9."
                else:
                    err = (f"Tar emot paket från {p.last_exporter} ({vers}), men de innehåller inga flöden mellan "
                           f"LAN ({', '.join(map(str, self.cfg.lan_subnets))}) och internet. Stämmer LAN_SUBNETS?")
            else:
                err = "Inga flöden på över 5 minuter."
            self.status["netflow"]["ok"] = False
            self.status["netflow"]["error"] = err
        if self._netflow_live():
            agg: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
            for _, cid, up, down in self.nf_window:
                agg[cid][0] += down
                agg[cid][1] += up
            self.rates = {cid: (v[0] * 8 / NETFLOW_WINDOW, v[1] * 8 / NETFLOW_WINDOW) for cid, v in agg.items()}
        elif self.state_bytes:
            self.rates = self.state_rates

    def _netflow_live(self) -> bool:
        """NetFlow ger bandbredden när det tas emot flöden, annars räknarna i tillståndstabellen."""
        return self.cfg.traffic_source != "states" and time.time() - self.nf_last < 180

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
        for d in devices.values():
            key = f"unifi:{d['id']}"
            if self.confirm(key, not d["online"]):
                self.alerts.raise_(key, "warning", "unifi_offline", f"{d['name']} är offline",
                                   f"UniFi-enheten {d['name']} ({d['model']}) svarar inte. Klienter som är anslutna via den kan sakna nätverk."
                                   + self.confirm_note(key))
            elif d["online"]:
                self.alerts.resolve(key, f"{d['name']} är online igen.")

    # ── gateways och VPN ──
    def update_gateways(self, rows: list[dict]) -> None:
        self.raw["gateways"] = rows[:20]
        seen = {}
        for row in rows:
            g = parse_gateway(row)
            if g:
                seen[g["name"]] = g
        for name, g in seen.items():
            what = "VPN-anslutningen" if g["vpn"] else "Gatewayen"
            key = f"gw:{name}"
            if self.confirm(key, g["state"] == "down"):
                self.alerts.raise_(key, "critical", "vpn_down" if g["vpn"] else "gateway_down", f"{what} {name} är nere",
                                   f"pfSense rapporterar att {name} är nere (status: {g['status']}"
                                   f"{', förlust ' + g['loss'] if g['loss'] else ''}).{self.confirm_note(key)}")
            elif g["state"] != "down":
                self.alerts.resolve(key, f"{name} är uppe igen.")
            dkey = f"gwdeg:{name}"
            if g["state"] == "degraded":
                self.alerts.raise_(dkey, "warning", "gateway_degraded", f"{what} {name} har problem",
                                   f"Hög paketförlust eller fördröjning på {name} (förlust {g['loss'] or '?'}, fördröjning {g['delay'] or '?'}).")
            else:
                self.alerts.resolve(dkey)
        self.gateways = seen

    def update_openvpn(self, rows: list[dict]) -> None:
        self.raw["openvpn"] = rows[:20]
        seen = {}
        for row in rows:
            v = parse_openvpn_client(row)
            if v:
                seen[v["name"]] = v
        for name, v in seen.items():
            key = f"ovpn:{name}"
            if self.confirm(key, not v["up"]):
                self.alerts.raise_(key, "critical", "vpn_down", f"VPN-anslutningen {name} är nere",
                                   f"OpenVPN-klienten {name} har status \"{v['status']}\".{self.confirm_note(key)}")
            elif v["up"]:
                self.alerts.resolve(key, f"{name} är ansluten igen.")
        self.vpn_clients = seen
        self._update_vpn_status()

    def _update_vpn_status(self) -> None:
        st = self.status["vpn"]
        clients = list(self.vpn_clients.values()) or list(self.vpn_ifaces.values())
        if not clients:
            st.update(enabled=False, ok=False, error=None, detail=None, label="VPN")
            return
        down = [c for c in clients if not c["up"]]
        st["enabled"] = True
        st["label"] = "VPN"
        parts = [f"{c['name']}: {'ansluten' if c['up'] else 'inte ansluten'} ({c['status']})"
                 + (f", server {c['remote']}" if c.get("remote") else "") for c in clients]
        st["detail"] = "; ".join(parts)
        if down:
            st.update(ok=False, error="; ".join(p for p, c in zip(parts, clients) if not c["up"]))
        else:
            st.update(ok=True, error=None, last=time.time())

    # ── syslog: Suricata och pfBlockerNG ──
    def on_syslog(self, line: str) -> None:
        now = time.time()
        self.syslog_lines += 1
        self.syslog_last = now
        self.mark("syslog", True)
        is_lan = self.cfg.is_lan
        sa = parse_suricata(line)
        if sa:
            lan = sa.src if is_lan(sa.src) else sa.dst if is_lan(sa.dst) else ""
            remote = sa.dst if lan == sa.src else sa.src
            direction = "out" if lan and lan == sa.src else "in"
            cid = self.cid_for_ip(lan) if lan else ""
            cc = self.geo.country(remote) or ""
            self.history.add_security("suricata", sa.signature, sa.priority, cid, remote, cc, direction, ts=now)
            if sa.priority <= self.settings.get("suricata_min_priority"):
                who = self.display_name(cid) if cid else (lan or sa.dst)
                level = "critical" if sa.priority == 1 else "warning"
                self.alerts.raise_(
                    f"suricata:{sa.sid}", level, "suricata", f"Suricata: {sa.signature}",
                    f"Prioritet {sa.priority} ({sa.classification}). {sa.src}{':' + str(sa.sport) if sa.sport else ''} → "
                    f"{sa.dst}{':' + str(sa.dport) if sa.dport else ''} ({sa.proto}). Berör {who}"
                    f"{', ' + cc if cc else ''}.")
            return
        fe = parse_filterlog(line)
        if not fe or fe.action != "block":
            return
        source = "pfblockerng" if fe.tracker.startswith(self.cfg.pfblocker_tracker_prefix) else "firewall"
        if is_lan(fe.src) and not is_lan(fe.dst):
            cid, remote, direction = self.cid_for_ip(fe.src), fe.dst, "out"
        elif not is_lan(fe.src):
            cid, remote, direction = (self.cid_for_ip(fe.dst) if is_lan(fe.dst) else ""), fe.src, "in"
        else:
            return
        cc = self.geo.country(remote) or ""
        self.history.add_security(source, fe.interface, 0, cid, remote if direction == "out" else "", cc, direction, ts=now)
        if source == "pfblockerng" and direction == "out":
            hits = self._pfb_hits[cid]
            hits.append((now, remote))
            while hits and now - hits[0][0] > PFB_WINDOW:
                hits.popleft()
            threshold = self.settings.get("pfblocker_threshold")
            if len(hits) >= threshold:
                name = self.display_name(cid)
                targets = sorted({r for _, r in hits})
                self.alerts.raise_(
                    f"pfb:{cid}", "warning", "pfblocker", f"{name} försöker nå blockerade adresser",
                    f"pfBlockerNG har stoppat {len(hits)} försök från {name} ({fe.src}) de senaste 10 minuterna, "
                    f"till bland annat {', '.join(targets[:5])}. Det kan tyda på skadlig kod eller reklam/spårning.")

    # ── kontroller som körs regelbundet ──
    def check_sources(self, now: float | None = None) -> None:
        now = now or time.time()
        self.alerts.expire(("suricata", "pfblocker"), 3600, now)
        if self.status["syslog"]["enabled"] and self.syslog_last and now - self.syslog_last > 1800:
            self.status["syslog"].update(ok=False, error="Ingen syslog från pfSense på över 30 minuter.")

    # ── Unraid och Plex ──
    def check_unraid(self, u) -> None:
        name = u.status.get("name") or "Unraid"
        key = f"unraid:{u.url}"
        if self.confirm(key, not u.status["ok"]):
            self.alerts.raise_(key, "critical", "unraid_down", f"{name} svarar inte",
                               f"Dashboarden får inte svar från Unraid-servern {name}. {u.status.get('error') or ''}"
                               + self.confirm_note(key), source=name)
            return
        if not u.status["ok"]:
            return
        self.alerts.resolve(key, f"{name} svarar igen.")
        level = u.status.get("level")
        pkey = f"unraidp:{u.url}"
        if self.confirm(pkey, level in ("err", "warn")):
            self.alerts.raise_(pkey, "critical" if level == "err" else "warning", "unraid_problem",
                               f"{name}: {'fel' if level == 'err' else 'varning'}",
                               f"{u.status.get('detail')}.{self.confirm_note(pkey)}", source=name)
        elif level == "ok":
            self.alerts.resolve(pkey, f"{name}: allt ser bra ut igen.")
        # övervakade containrar och virtuella maskiner
        running = {c["name"]: c for c in u.data.get("containers", [])}
        vms = {v["name"]: v for v in u.data.get("vms", [])}
        for kind, items, label in (("containers", running, "Containern"), ("vms", vms, "Den virtuella maskinen")):
            if kind == "vms" and "vms" not in u.data:
                continue
            for item in self.settings.watched(name, kind):
                obj = items.get(item)
                up = bool(obj) and (obj.get("state") == "running" if kind == "containers" else obj.get("running"))
                wkey = f"watch:{name}:{kind}:{item}"
                if self.confirm(wkey, not up):
                    why = "finns inte längre" if not obj else (obj.get("status") or obj.get("state") or "har stannat")
                    self.alerts.raise_(wkey, "critical", "unraid_watch" if kind == "containers" else "unraid_vm",
                                       f"{label} {item} på {name} har stannat",
                                       f"{label} {item} på {name} är inte igång ({why}).{self.confirm_note(wkey)}",
                                       source=name)
                elif up:
                    self.alerts.resolve(wkey, f"{item} på {name} är igång igen.")
        # sluta larma för sådant som inte längre övervakas
        prefix = f"watch:{name}:"
        for k in [k for k in self._confirm if k.startswith(prefix)]:
            _, _, kind, item = k.split(":", 3)
            if item not in self.settings.watched(name, kind):
                self.confirm(k, False)
                self.alerts.resolve(k, "Övervakningen är avstängd.")

    def check_plex(self, p) -> None:
        key = f"plex:{p.url}"
        if self.confirm(key, not p.status["ok"]):
            self.alerts.raise_(key, "critical", "plex_down", f"{p.name} svarar inte",
                               f"{p.status.get('error') or 'Plex-servern svarar inte.'}{self.confirm_note(key)}",
                               source=p.name)
        elif p.status["ok"]:
            self.alerts.resolve(key, f"{p.name} svarar igen.")

    def check_data_anomalies(self, now: float | None = None) -> None:
        """Larma när en klient har använt betydligt mer data senaste dygnet än den brukar."""
        now = now or time.time()
        factor = float(self.settings.get("anomaly_factor"))
        min_bytes = float(self.settings.get("anomaly_min_gb")) * 1e9
        days = min(14, int(self.settings.get("retention_days")))
        first = self.history.first_traffic()
        if days < 4 or not first:
            return
        full_days = int((now - first) // 86400)
        if full_days < 4:
            return
        for cid, per_day in self.history.daily_totals(now, days).items():
            last = per_day[0]
            baseline_days = [v for v in per_day[1:full_days]]
            if len(baseline_days) < 3:
                continue
            typical = statistics.median(baseline_days)
            key = f"data:{cid}"
            name = self.display_name(cid)
            if last >= min_bytes and last > factor * max(typical, 1):
                ratio = last / typical if typical else None
                self.alerts.raise_(
                    key, "warning", "data_anomaly", f"{name} använder ovanligt mycket data",
                    f"{name} har använt {last / 1e9:.1f} GB de senaste 24 timmarna. Normalt är "
                    f"{typical / 1e9:.1f} GB per dygn" + (f" ({ratio:.0f} gånger mer än vanligt)." if ratio else "."))
            elif self.alerts.is_active(key) and last < factor * max(typical, 1) * 0.8:
                self.alerts.resolve(key, f"{name} är tillbaka på normal nivå.")

    def country_detail(self, cc: str, period: str, ptr: dict[str, str | None] | None = None) -> dict:
        span, _ = PERIODS.get(period, PERIODS["24h"])
        now = time.time()
        d = self.history.country_detail(cc, int(now - span))

        def client_info(cid: str) -> dict:
            c = self.clients.get(cid, {})
            u = self.unifi_clients.get(cid, {})
            ip = c.get("ip") or u.get("ip") or (cid[3:] if cid.startswith("ip-") else None)
            return {"id": cid, "name": self.display_name(cid), "ip": ip}

        # aktiva anslutningar just nu
        active: dict[tuple, dict] = {}
        for c in self.conns.values():
            if self.geo.country(c.remote_ip) != cc:
                continue
            key = (c.remote_ip, c.initiated)
            a = active.setdefault(key, {"conns": 0, "ports": set(), "cids": set()})
            a["conns"] += 1
            a["ports"].add(c.remote_port if c.initiated == "out" else c.lan_port)
            a["cids"].add(self.cid_for_ip(c.lan_ip))

        clients: dict[str, dict] = {}
        for row in d["clients"]:
            ci = clients.setdefault(row["cid"], {**client_info(row["cid"]), "out": 0, "in": 0, "bytes": 0, "conns": 0})
            ci[row["dir"]] += row["up"] + row["down"]
            ci["bytes"] += row["up"] + row["down"]
            ci["conns"] += row["conns"]

        remotes = []
        seen = set()
        for r in d["remotes"]:
            key = (r["ip"], r["dir"])
            seen.add(key)
            a = active.get(key)
            ports = set(r["ports"]) | (a["ports"] if a else set())
            cids = [x for x in r["cids"] if x] + ([x for x in a["cids"] if x not in r["cids"]] if a else [])
            remotes.append({"ip": r["ip"], "dir": r["dir"], "ports": sorted(p for p in ports if p)[:8],
                            "bytes": r["up"] + r["down"], "up": r["up"], "down": r["down"], "conns": r["conns"],
                            "active": a["conns"] if a else 0, "last": now if a else r["last"], "cids": cids})
        for (ip, direction), a in active.items():
            if (ip, direction) not in seen:
                remotes.append({"ip": ip, "dir": direction, "ports": sorted(p for p in a["ports"] if p)[:8], "bytes": 0,
                                "up": 0, "down": 0, "conns": a["conns"], "active": a["conns"], "last": now,
                                "cids": sorted(a["cids"])})
        for cid in {c for r in remotes for c in r["cids"]}:
            clients.setdefault(cid, {**client_info(cid), "out": 0, "in": 0, "bytes": 0, "conns": 0})
        for r in remotes:
            r["org"] = self.geo.org(r["ip"])
            r["host"] = (ptr or {}).get(r["ip"])
            r["clients"] = [{"id": c, "name": self.display_name(c)} for c in r.pop("cids")]
        remotes.sort(key=lambda r: (r["bytes"], r["active"], r["conns"]), reverse=True)
        return {"cc": cc, "period": period, "clients": sorted(clients.values(), key=lambda c: c["bytes"], reverse=True),
                "remotes": remotes}

    def plex_summary(self) -> dict:
        servers = [dict(s.status) for s in self.plex_servers]
        sessions = [dict(x) for s in self.plex_servers for x in s.sessions]
        for x in sessions:
            x["country"] = None if x["local"] or not x.get("address") else self.geo.country(x["address"])
            x["address"] = None  # visas inte på sidan
        order = {"playing": 0, "buffering": 1, "paused": 2}
        sessions.sort(key=lambda x: (order.get(x["state"], 3), x["user"].lower()))
        return {
            "servers": servers,
            "sessions": sessions,
            "totals": {
                "streams": len(sessions),
                "direct": sum(1 for x in sessions if x["decision"] in ("direct", "stream")),
                "transcode": sum(1 for x in sessions if x["decision"] == "transcode"),
                "remote": sum(1 for x in sessions if not x["local"]),
                "bandwidth_kbps": sum(x["bandwidth_kbps"] or 0 for x in sessions),
            },
        }

    def security(self, since: int) -> dict:
        sec = self.history.security_summary(since)
        for item in sec["pfb_clients"]:
            item["name"] = self.display_name(item["cid"])
        sec["gateways"] = sorted(self.gateways.values(), key=lambda g: (not g["vpn"], g["name"]))
        sec["vpn_clients"] = list(self.vpn_clients.values())
        return sec

    # ── svar till webbsidan ──
    def set_alias(self, cid: str, name: str | None) -> None:
        name = (name or "").strip()[:64] or None
        self.history.set_alias(cid, name)
        if name:
            self.aliases[cid] = name
        else:
            self.aliases.pop(cid, None)

    def display_name(self, cid: str, known: dict | None = None, use_alias: bool = True) -> str:
        if use_alias and cid in self.aliases:
            return self.aliases[cid]
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
                "custom_name": cid in self.aliases,
                "default_name": self.display_name(cid, k, use_alias=False),
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
            "plex": self.plex_summary(),
            "unraids": [{"status": u.status, **u.data,
                         "watch": self.settings.get("watch").get(u.status.get("name") or "", {"containers": [], "vms": []})}
                        for u in self.unraids],
            "quiet": dict(zip(("active", "until"), self.settings.quiet_now())),
            "alerts": self.alerts.banner(now),
            "events": self.alerts.recent(25),
            "security": self.security(int(now - 86400)),
            "settings": self.settings.all(),
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
