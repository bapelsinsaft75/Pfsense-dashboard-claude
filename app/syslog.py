"""Tar emot syslog från pfSense och tolkar Suricata-larm och brandväggsloggen (filterlog).

pfSense: Status → System Logs → Settings → Remote Logging.
Suricata: Services → Suricata → interface → "Send Alerts to System Log".
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass
from typing import Callable

SURICATA_RE = re.compile(
    r"suricata\[?\d*\]?:?.*?\[(\d+):(\d+):(\d+)\]\s+(.*?)\s*(?:\[\*\*\]\s*)?"
    r"\[Classification:\s*([^\]]*)\]\s*\[Priority:\s*(\d+)\]\s*\{(\w+)\}\s*(\S+)\s*->\s*(\S+)",
    re.IGNORECASE,
)
FILTERLOG_RE = re.compile(r"filterlog(?:\[\d+\]:|\s+\d+\s+\S+\s+\S+|:)?\s*(\d+,.*)$")


@dataclass
class SuricataAlert:
    gid: int
    sid: int
    rev: int
    signature: str
    classification: str
    priority: int
    proto: str
    src: str
    sport: int | None
    dst: str
    dport: int | None


@dataclass
class FilterEvent:
    tracker: str
    interface: str
    action: str
    direction: str
    proto: str
    src: str
    dst: str
    sport: int | None
    dport: int | None


def _split_addr(s: str) -> tuple[str, int | None]:
    s = s.strip().rstrip(",")
    m = re.fullmatch(r"\[?([0-9a-fA-F:.]+?)\]?:(\d+)", s)
    if m and _ip(m.group(1)):
        return m.group(1), int(m.group(2))
    return s, None


def _ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def parse_suricata(line: str) -> SuricataAlert | None:
    if "suricata" not in line.lower():
        return None
    m = SURICATA_RE.search(line)
    if not m:
        return None
    gid, sid, rev, sig, cls, prio, proto, a, b = m.groups()
    src, sport = _split_addr(a)
    dst, dport = _split_addr(b)
    if not (_ip(src) and _ip(dst)):
        return None
    return SuricataAlert(int(gid), int(sid), int(rev), sig.strip(), cls.strip(), int(prio), proto.upper(), src, sport, dst, dport)


def parse_filterlog(line: str) -> FilterEvent | None:
    m = FILTERLOG_RE.search(line)
    if not m:
        return None
    f = m.group(1).strip().split(",")
    try:
        tracker, iface, action, direction, ipver = f[3], f[4], f[6], f[7], f[8]
        if ipver == "4":
            proto, src, dst, rest = f[16].lower(), f[18], f[19], f[20:]
        elif ipver == "6":
            proto, src, dst, rest = f[12].lower(), f[15], f[16], f[17:]
        else:
            return None
    except IndexError:
        return None
    sport = dport = None
    if proto in ("tcp", "udp") and len(rest) >= 2 and rest[0].isdigit() and rest[1].isdigit():
        sport, dport = int(rest[0]), int(rest[1])
    if not (_ip(src) and _ip(dst)):
        return None
    return FilterEvent(tracker, iface, action, direction, proto, src, dst, sport, dport)


class SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_line: Callable[[str], None]) -> None:
        self.on_line = on_line
        self.packets = 0

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        self.packets += 1
        for line in data.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                self.on_line(line)
