"""Klient för pfSense REST API (pfSense-pkg-RESTAPI v2) och tolkning av svaren.

Tolkningen är medvetet tålig: fältnamn skiljer sig mellan versioner av paketet,
så varje värde letas upp bland flera tänkbara nycklar.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config, is_public


class PfSenseError(Exception):
    pass


class PfSense:
    LEASES = "/api/v2/status/dhcp_server/leases"
    ARP = "/api/v2/diagnostics/arp_table"
    STATES = "/api/v2/firewall/states"
    INTERFACES = "/api/v2/status/interfaces"
    GATEWAYS = "/api/v2/status/gateways"
    OPENVPN_CLIENTS = "/api/v2/status/openvpn/clients"
    SYSTEM = "/api/v2/status/system"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = httpx.AsyncClient(
            base_url=cfg.pfsense_url,
            verify=cfg.pfsense_verify_ssl,
            timeout=30,
            headers={"X-API-Key": cfg.pfsense_api_key, "Accept": "application/json"},
        )

    async def get(self, path: str, params: dict | None = None) -> list[dict]:
        try:
            resp = await self.client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise PfSenseError(f"Kan inte nå pfSense ({exc.__class__.__name__}): {exc}") from exc
        if resp.status_code in (401, 403):
            raise PfSenseError(f"pfSense nekade åtkomst ({resp.status_code}). Kontrollera API-nyckeln och dess behörigheter.")
        if resp.status_code == 404:
            raise PfSenseError(f"pfSense svarade 404 på {path}. Är REST API v2 installerat?")
        if resp.status_code >= 400:
            raise PfSenseError(f"pfSense svarade {resp.status_code} på {path}: {resp.text[:200]}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise PfSenseError(f"pfSense gav inget JSON-svar på {path}") from exc
        data = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(data, dict):
            data = list(data.values()) if all(isinstance(v, dict) for v in data.values()) else [data]
        return data if isinstance(data, list) else []

    async def leases(self) -> list[dict]:
        return await self.get(self.LEASES, {"limit": 0})

    async def arp(self) -> list[dict]:
        return await self.get(self.ARP, {"limit": 0})

    async def states(self) -> list[dict]:
        return await self.get(self.STATES, {"limit": 0})

    async def interfaces(self) -> list[dict]:
        return await self.get(self.INTERFACES)

    async def gateways(self) -> list[dict]:
        return await self.get(self.GATEWAYS)

    async def openvpn_clients(self) -> list[dict]:
        return await self.get(self.OPENVPN_CLIENTS)

    async def system(self) -> dict:
        rows = await self.get(self.SYSTEM)
        return rows[0] if rows else {}

    async def close(self) -> None:
        await self.client.aclose()


# ── hjälpfunktioner ──
def pick(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def norm_mac(mac: Any) -> str | None:
    if not mac or not isinstance(mac, str):
        return None
    hexdigits = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(hexdigits) != 12:
        return None
    return ":".join(hexdigits[i : i + 2] for i in range(0, 12, 2)).lower()


def to_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"\d+", str(v).replace(" ", ""))
    return int(m.group()) if m else None


def truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "active", "online", "online/active")


def _valid_ip(s: str) -> str | None:
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return None


def parse_endpoints(value: Any) -> list[tuple[str, int | None]]:
    """'192.168.1.5:443 (1.2.3.4:6000)' eller '2001:db8::1[443]' → [(ip, port), ...]."""
    if value is None:
        return []
    if isinstance(value, dict):
        ip = pick(value, "address", "ip", "addr")
        port = to_int(pick(value, "port"))
        ip = _valid_ip(str(ip)) if ip else None
        return [(ip, port)] if ip else []
    out = []
    for part in re.split(r"[()]", str(value)):
        part = part.strip().strip(",")
        if not part:
            continue
        ip = port = None
        m = re.fullmatch(r"\[([0-9a-fA-F:.]+)\]:(\d+)", part) or re.fullmatch(r"([0-9a-fA-F:.]+)\[(\d+)\]", part)
        if m:
            ip, port = m.group(1), int(m.group(2))
        elif part.count(":") == 1:
            host, _, p = part.partition(":")
            ip, port = host, to_int(p)
        else:
            ip = part
        ip = _valid_ip(ip) if ip else None
        if ip:
            out.append((ip, port))
    return out


@dataclass
class Conn:
    key: tuple
    lan_ip: str
    lan_port: int | None
    remote_ip: str
    remote_port: int | None
    proto: str
    initiated: str  # "out" = startad inifrån, "in" = startad utifrån
    up: int | None  # klient → internet
    down: int | None  # internet → klient


WELL_KNOWN = 1024
EPHEMERAL = 32768  # Linux 32768–60999, Windows/macOS 49152–65535


def guess_initiator(lan_port: int | None, remote_port: int | None, default: str) -> str:
    """Den som startar en anslutning använder nästan alltid en tillfällig (hög) port mot en tjänsteport.

    Används för att rätta en riktning som ser fel ut. Lika höga eller lika låga portar ändrar inget.
    """
    if lan_port is None or remote_port is None:
        return default
    if remote_port < WELL_KNOWN <= lan_port or (lan_port >= EPHEMERAL and remote_port < EPHEMERAL):
        return "out"
    if lan_port < WELL_KNOWN <= remote_port or (remote_port >= EPHEMERAL and lan_port < EPHEMERAL and lan_port < 1024):
        return "in"
    return default


def parse_state(st: dict, cfg: Config, wan_ips: set[str]) -> Conn | None:
    proto = str(pick(st, "protocol", "proto", default="")).lower()
    src_raw = pick(st, "source", "src", "source_address")
    dst_raw = pick(st, "destination", "dst", "destination_address")
    direction = str(pick(st, "direction", "dir", default="")).lower()

    # Rå pfctl-rad, t.ex. "all tcp 1.2.3.4:443 <- 192.168.1.5:5000"
    raw_line = pick(st, "raw", "line")
    if (src_raw is None or dst_raw is None) and raw_line:
        m = re.search(r"(\S+(?: \([^)]*\))?)\s+(<-|->)\s+(\S+(?: \([^)]*\))?)", str(raw_line))
        if m:
            a, arrow, b = m.groups()
            src_raw, dst_raw = (b, a) if arrow == "<-" else (a, b)
    src = parse_endpoints(src_raw)
    dst = parse_endpoints(dst_raw)
    if not src or not dst:
        return None
    del direction  # källa/destination anges redan ur initiativtagarens perspektiv

    def first(eps, cond):
        return next(((ip, p) for ip, p in eps if cond(ip)), None)

    is_lan = cfg.is_lan
    is_remote = lambda ip: is_public(ip) and ip not in wan_ips and not is_lan(ip)  # noqa: E731
    src_lan, dst_lan = first(src, is_lan), first(dst, is_lan)
    src_rem, dst_rem = first(src, is_remote), first(dst, is_remote)

    if src_lan and dst_rem and not dst_lan:
        lan, remote, initiated = src_lan, dst_rem, "out"
    elif dst_lan and src_rem and not src_lan:
        lan, remote, initiated = dst_lan, src_rem, "in"
    else:
        return None

    # Rimlighetskontroll med portarna: tillfällig port lokalt mot en tjänsteport externt
    # betyder att klienten startade anslutningen, och tvärtom.
    initiated = guess_initiator(lan[1], remote[1], initiated)

    b_in = to_int(pick(st, "bytes_in", "bytesin", "in_bytes"))
    b_out = to_int(pick(st, "bytes_out", "bytesout", "out_bytes"))
    if b_in is None and b_out is None:
        pair = pick(st, "bytes")
        if isinstance(pair, str) and re.search(r"\d+\D+\d+", pair):
            nums = [int(x) for x in re.findall(r"\d+", pair)]
            b_in, b_out = nums[0], nums[1]
        elif pair is not None or pick(st, "bytes_total") is not None:
            b_in, b_out = None, to_int(pick(st, "bytes_total", "bytes"))
    # Första räknaren = paket från den som startade anslutningen.
    if initiated == "out":
        up, down = b_in, b_out
    else:
        up, down = b_out, b_in

    key = (proto, lan[0], lan[1], remote[0], remote[1])
    return Conn(key, lan[0], lan[1], remote[0], remote[1], proto, initiated, up, down)


def parse_lease(row: dict) -> dict | None:
    ip = pick(row, "ip", "ip_address", "address")
    mac = norm_mac(pick(row, "mac", "mac_address", "hwaddr"))
    if not ip or not mac:
        return None
    status = str(pick(row, "active_status", "status", "act", default="")).lower()
    online = pick(row, "online_status", "online")
    return {
        "ip": str(ip),
        "mac": mac,
        "hostname": pick(row, "hostname", "client_hostname", "host"),
        "descr": pick(row, "descr", "description"),
        "active": status in ("active", "static", "") or truthy(status),
        "online": truthy(online) if online is not None else None,
    }


def parse_arp(row: dict) -> dict | None:
    ip = pick(row, "ip_address", "ip", "address")
    mac = norm_mac(pick(row, "mac_address", "mac"))
    if not ip or not mac:
        return None
    expires = str(pick(row, "expires", default="")).lower()
    return {
        "ip": str(ip),
        "mac": mac,
        "hostname": pick(row, "hostname"),
        "interface": pick(row, "interface", "if"),
        "expired": "expired" in expires,
    }


def parse_interfaces(rows: list[dict], wan_name: str) -> tuple[dict | None, set[str]]:
    """Returnerar (WAN-raden, alla WAN-adresser)."""
    wan = None
    for row in rows:
        names = {str(pick(row, k, default="")).lower() for k in ("name", "descr", "description", "id", "if")}
        if wan_name in names:
            wan = row
            break
    ips = set()
    if wan:
        for k in ("ipaddr", "ipaddrv6", "ip", "ipv4", "ipv6"):
            v = wan.get(k)
            if isinstance(v, str) and _valid_ip(v.split("/")[0]):
                ips.add(_valid_ip(v.split("/")[0]))
    return wan, ips


def wan_counters(wan: dict) -> tuple[int, int] | None:
    rx = to_int(pick(wan, "inbytes", "inbytes_pass", "bytes_in", "rx_bytes"))
    tx = to_int(pick(wan, "outbytes", "outbytes_pass", "bytes_out", "tx_bytes"))
    if rx is None or tx is None:
        return None
    return rx, tx


VPN_NAME = re.compile(r"vpn|wg|wireguard|ovpn|tun|proton|mullvad|nord|ipsec|tailscale", re.IGNORECASE)


def parse_gateway(row: dict) -> dict | None:
    name = pick(row, "name", "gateway", "descr")
    if not name:
        return None
    status = str(pick(row, "status", default="")).lower()
    sub = str(pick(row, "substatus", default="")).lower()
    loss = pick(row, "loss")
    if status in ("", "none", "unknown") and sub in ("", "none"):
        state = "unknown"
    elif status in ("down", "offline", "force_down") or "down" in status:
        state = "down"
    elif status in ("loss", "highloss", "delay", "highdelay") or sub in ("loss", "highloss", "delay", "highdelay"):
        state = "degraded"
    else:
        state = "up"
    return {
        "name": str(name),
        "state": state,
        "status": status or sub,
        "loss": str(loss) if loss is not None else None,
        "delay": str(pick(row, "delay", default="")) or None,
        "stddev": str(pick(row, "stddev", default="")) or None,
        "monitor": pick(row, "monitorip", "monitor"),
        "vpn": bool(VPN_NAME.search(str(name)) or VPN_NAME.search(str(pick(row, "descr", default="")))),
    }


def parse_openvpn_client(row: dict) -> dict | None:
    name = pick(row, "name", "description", "descr")
    if not name:
        vpnid = pick(row, "vpnid", "id")
        name = f"OpenVPN-klient {vpnid}" if vpnid is not None else None
    if not name:
        return None
    status = str(pick(row, "status", default="")).lower()
    state = str(pick(row, "state", "state_detail", default="")).lower()
    if not status and not state:
        return None
    if state:
        # OpenVPN-tillstånd: CONNECTED, RECONNECTING, WAIT, AUTH, GET_CONFIG, ASSIGN_IP, EXITING ...
        up = state.startswith("connected") or (state in ("", "none") and status in ("up", "connected"))
    else:
        up = status in ("up", "connected", "established") or status.startswith("connected")
    status = ", ".join(x for x in (status, state) if x)
    return {"name": str(name), "up": up, "status": status, "remote": pick(row, "remote_host", "remote")}
