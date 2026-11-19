"""Mottagare för NetFlow v5, v9 och IPFIX (v10), t.ex. från softflowd på pfSense."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("netflow")

V5_HEADER = struct.Struct("!HHIIIIBBH")
V5_RECORD = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")
V9_HEADER = struct.Struct("!HHIIII")
V10_HEADER = struct.Struct("!HHIII")

# Fält-ID enligt RFC 3954 / IANA IPFIX
F_BYTES = 1
F_PACKETS = 2
F_PROTO = 4
F_TCP_FLAGS = 6
F_SRC_PORT = 7
F_SRC_V4 = 8
F_DST_PORT = 11
F_DST_V4 = 12
F_SRC_V6 = 27
F_DST_V6 = 28
F_TOTAL_BYTES = 85  # octetTotalCount (IPFIX)


@dataclass
class Flow:
    src: str
    dst: str
    sport: int
    dport: int
    proto: int
    bytes: int
    packets: int


class FlowParser:
    """Tolkar UDP-paket. Mallar (templates) sparas per exportör."""

    def __init__(self) -> None:
        self.templates: dict[tuple, list[tuple[int, int]]] = {}
        self.packets = 0  # tolkade paket
        self.errors = 0
        self.received = 0  # alla UDP-paket, även de som inte gick att tolka
        self.versions: dict[int, int] = {}
        self.no_template = 0  # datamängder som kom innan mallen
        self.last_exporter: str | None = None
        self.last_received = 0.0
        self.flows = 0

    def parse(self, data: bytes, exporter: str = "") -> list[Flow]:
        import time as _t
        self.received += 1
        self.last_received = _t.time()
        self.last_exporter = exporter
        if len(data) < 4:
            self.errors += 1
            return []
        version = struct.unpack_from("!H", data)[0]
        self.versions[version] = self.versions.get(version, 0) + 1
        try:
            if version == 5:
                flows = self._v5(data)
            elif version == 9:
                flows = self._v9(data, exporter)
            elif version == 10:
                flows = self._v10(data, exporter)
            else:
                self.errors += 1
                return []
        except (struct.error, ValueError, IndexError) as exc:
            self.errors += 1
            log.debug("Felaktigt flödespaket: %s", exc)
            return []
        self.packets += 1
        self.flows += len(flows)
        return flows

    # ── v5 ──
    def _v5(self, data: bytes) -> list[Flow]:
        _, count, *_ = V5_HEADER.unpack_from(data)
        flows = []
        off = V5_HEADER.size
        for _ in range(count):
            if off + V5_RECORD.size > len(data):
                break
            r = V5_RECORD.unpack_from(data, off)
            off += V5_RECORD.size
            flows.append(
                Flow(
                    src=socket.inet_ntop(socket.AF_INET, r[0]),
                    dst=socket.inet_ntop(socket.AF_INET, r[1]),
                    sport=r[9],
                    dport=r[10],
                    proto=r[13],
                    bytes=r[6],
                    packets=r[5],
                )
            )
        return flows

    # ── v9 ──
    def _v9(self, data: bytes, exporter: str) -> list[Flow]:
        _, _, _, _, _, source_id = V9_HEADER.unpack_from(data)
        off = V9_HEADER.size
        flows: list[Flow] = []
        while off + 4 <= len(data):
            set_id, length = struct.unpack_from("!HH", data, off)
            if length < 4:
                break
            body = data[off + 4 : off + length]
            if set_id == 0:
                self._read_templates(body, (exporter, 9, source_id), ipfix=False)
            elif set_id >= 256:
                flows.extend(self._read_data(body, (exporter, 9, source_id, set_id), ipfix=False))
            off += length
        return flows

    # ── IPFIX ──
    def _v10(self, data: bytes, exporter: str) -> list[Flow]:
        _, total, _, _, domain = V10_HEADER.unpack_from(data)
        end = min(total, len(data))
        off = V10_HEADER.size
        flows: list[Flow] = []
        while off + 4 <= end:
            set_id, length = struct.unpack_from("!HH", data, off)
            if length < 4:
                break
            body = data[off + 4 : off + length]
            if set_id == 2:
                self._read_templates(body, (exporter, 10, domain), ipfix=True)
            elif set_id >= 256:
                flows.extend(self._read_data(body, (exporter, 10, domain, set_id), ipfix=True))
            off += length
        return flows

    def _read_templates(self, body: bytes, key: tuple, ipfix: bool) -> None:
        off = 0
        while off + 4 <= len(body):
            tid, count = struct.unpack_from("!HH", body, off)
            off += 4
            if tid < 256:
                break
            fields = []
            for _ in range(count):
                ftype, flen = struct.unpack_from("!HH", body, off)
                off += 4
                if ipfix and ftype & 0x8000:
                    ftype &= 0x7FFF
                    off += 4  # enterprise number
                    ftype = -1  # leverantörsspecifikt fält: hoppa över värdet
                fields.append((ftype, flen))
            self.templates[key + (tid,)] = fields

    def _read_data(self, body: bytes, key: tuple, ipfix: bool) -> list[Flow]:
        fields = self.templates.get(key)
        if not fields:
            self.no_template += 1
            return []
        flows = []
        off = 0
        fixed = all(flen != 0xFFFF for _, flen in fields)
        min_len = sum(flen for _, flen in fields) if fixed else 4
        while off + min_len <= len(body):
            rec: dict[int, bytes] = {}
            for ftype, flen in fields:
                if flen == 0xFFFF:
                    flen = body[off]
                    off += 1
                    if flen == 255:
                        flen = struct.unpack_from("!H", body, off)[0]
                        off += 2
                rec[ftype] = body[off : off + flen]
                off += flen
            flow = self._to_flow(rec)
            if flow:
                flows.append(flow)
        return flows

    @staticmethod
    def stats(self) -> dict:
        return {"received": self.received, "parsed": self.packets, "errors": self.errors, "flows": self.flows,
                "versions": {str(k): v for k, v in self.versions.items()}, "templates": len(self.templates),
                "waiting_for_template": self.no_template, "last_exporter": self.last_exporter,
                "last_received": self.last_received or None}

    @staticmethod
    def _to_flow(rec: dict[int, bytes]) -> Flow | None:
        def num(fid: int) -> int:
            raw = rec.get(fid)
            return int.from_bytes(raw, "big") if raw else 0

        if F_SRC_V4 in rec and F_DST_V4 in rec:
            src = socket.inet_ntop(socket.AF_INET, rec[F_SRC_V4])
            dst = socket.inet_ntop(socket.AF_INET, rec[F_DST_V4])
        elif F_SRC_V6 in rec and F_DST_V6 in rec:
            src = socket.inet_ntop(socket.AF_INET6, rec[F_SRC_V6])
            dst = socket.inet_ntop(socket.AF_INET6, rec[F_DST_V6])
        else:
            return None
        nbytes = num(F_BYTES) or num(F_TOTAL_BYTES)
        return Flow(src, dst, num(F_SRC_PORT), num(F_DST_PORT), num(F_PROTO), nbytes, num(F_PACKETS))


class NetflowProtocol(asyncio.DatagramProtocol):
    def __init__(self, parser: FlowParser, on_flows: Callable[[list[Flow]], None]) -> None:
        self.parser = parser
        self.on_flows = on_flows

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        flows = self.parser.parse(data, addr[0])
        if flows:
            self.on_flows(flows)
