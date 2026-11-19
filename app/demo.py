"""Demoläge: fyller motorn med exempeldata så att dashboarden går att titta på innan något är konfigurerat."""
from __future__ import annotations

import math
import random
import time

from .engine import Engine
from .history import BUCKET
from .pfsense import Conn

# id, namn, ip, typ, ned Mbit/s, upp Mbit/s, GB per dygn, anslutning
CLIENTS = [
    ("bc:33:29:7a:01:f2", "PS5", "192.168.1.41", 182, 3.2, 48.2, ("wired", "sw3", 3)),
    ("a8:23:fe:10:9c:44", "Vardagsrum-TV", "192.168.1.40", 24.5, .6, 31.7, ("wired", "sw3", 2)),
    ("d0:50:99:3a:11:0c", "Unraid", "192.168.1.10", 6.1, 41.8, 22.4, ("wired", "sw1", 3)),
    ("04:42:1a:c3:7e:90", "Kontor-PC", "192.168.1.22", 14.2, 8.9, 12.9, ("wired", "sw2", 2)),
    ("f4:d4:88:5b:2e:17", "MacBook-Pro", "192.168.1.21", 9.7, 2.4, 9.8, ("wifi", "5 GHz", -54)),
    ("90:dd:5d:21:b8:03", "Apple-TV", "192.168.1.42", 0, 0, 7.1, ("wired", "sw3", 4)),
    ("6c:4a:85:e0:33:9b", "iPad", "192.168.1.35", 3.8, .4, 4.3, ("wifi", "5 GHz", -61)),
    ("3a:91:0f:6e:d2:55", "iPhone-15", "192.168.1.34", 2.1, .9, 3.9, ("wifi", "5 GHz", -58)),
    ("8e:12:c7:40:aa:61", "Galaxy-S24", "192.168.1.36", 1.3, .3, 2.7, ("wifi", "5 GHz", -67)),
    ("48:a6:b8:0d:5f:2e", "Sonos-Kök", "192.168.1.50", .32, .02, 1.2, ("wifi", "2,4 GHz", -63)),
    ("dc:a6:32:9f:14:b7", "Home-Assistant", "192.168.1.60", .12, .21, .62, ("wired", "sw1", 5)),
    ("64:90:c1:2b:7d:08", "Roborock", "192.168.1.62", .01, .02, .15, ("wifi", "2,4 GHz", -71)),
    ("00:17:88:6c:41:d9", "Hue-Bridge", "192.168.1.61", 0, .01, .04, ("wired", "sw1", 7)),
    ("30:24:a9:8e:12:7c", "Skrivare-HP", "192.168.1.70", 0, 0, .01, ("wired", "sw2", 4)),
]
IDLE = {"Apple-TV", "Skrivare-HP"}

# land, riktning, andel av klientens data, exempeladress
FLOWS = {
    "PS5": [("DE", "out", .7, "18.192.0.10"), ("JP", "out", .15, "133.11.0.1"), ("US", "out", .15, "3.80.0.1")],
    "Vardagsrum-TV": [("NL", "out", .6, "45.57.0.1"), ("SE", "out", .3, "194.68.0.1"), ("GB", "out", .1, "81.2.69.1")],
    "Unraid": [("US", "in", .3, "73.0.0.1"), ("DE", "out", .4, "88.198.0.1"), ("NL", "in", .05, "185.220.0.1"),
               ("CN", "in", .01, "1.180.0.1"), ("BR", "in", .01, "177.0.0.1"), ("IN", "in", .01, "49.36.0.1"),
               ("FI", "out", .22, "95.216.0.1")],
    "Kontor-PC": [("IE", "out", .4, "52.16.0.1"), ("US", "out", .3, "13.107.0.1"), ("SE", "out", .2, "194.68.0.2"),
                  ("FR", "out", .1, "51.15.0.1")],
    "MacBook-Pro": [("IE", "out", .5, "17.253.0.1"), ("US", "out", .3, "17.0.0.1"), ("SE", "out", .2, "194.68.0.3")],
    "Apple-TV": [("NL", "out", .7, "45.57.0.2"), ("US", "out", .3, "17.0.0.2")],
    "iPad": [("SE", "out", .6, "194.68.0.4"), ("GB", "out", .4, "81.2.69.2")],
    "iPhone-15": [("SE", "out", .5, "194.68.0.5"), ("US", "out", .3, "17.0.0.3"), ("NO", "out", .2, "84.208.0.1")],
    "Galaxy-S24": [("IE", "out", .6, "34.240.0.1"), ("FI", "out", .4, "95.216.0.2")],
    "Sonos-Kök": [("SE", "out", .7, "194.68.0.6"), ("DK", "out", .3, "80.62.0.1")],
    "Home-Assistant": [("DK", "out", .6, "80.62.0.2"), ("RU", "in", .02, "5.8.0.1"), ("DE", "out", .38, "88.198.0.2")],
    "Roborock": [("SG", "out", .6, "13.228.0.1"), ("DE", "out", .4, "18.192.0.2")],
    "Hue-Bridge": [("US", "out", 1, "3.80.0.2")],
    "Skrivare-HP": [("US", "out", 1, "15.73.0.1")],
}

DEVICES = {
    "sw1": {"id": "74:83:c2:00:00:01", "kind": "switch", "name": "Switch Förråd", "model": "USW-Lite-16-PoE", "ports": 16,
            "own_uplink": 1, "uplink_mac": None, "uplink_port": None, "online": True},
    "sw2": {"id": "74:83:c2:00:00:02", "kind": "switch", "name": "Switch Kontor", "model": "USW-Lite-8-PoE", "ports": 8,
            "own_uplink": 1, "uplink_mac": "74:83:c2:00:00:01", "uplink_port": 2, "online": True},
    "sw3": {"id": "74:83:c2:00:00:03", "kind": "switch", "name": "Switch Vardagsrum", "model": "USW-Flex-Mini", "ports": 5,
            "own_uplink": 1, "uplink_mac": "74:83:c2:00:00:01", "uplink_port": 4, "online": True},
    "ap": {"id": "74:83:c2:00:00:04", "kind": "ap", "name": "AP Hall", "model": "U6 Pro", "ports": 1,
           "own_uplink": None, "uplink_mac": "74:83:c2:00:00:01", "uplink_port": 8, "online": True},
}


def _daily_shape(hour: float) -> float:
    evening = math.exp(-((hour - 21) / 2.4) ** 2)
    day = math.exp(-((hour - 13) / 4) ** 2)
    return .08 + evening * .9 + day * .35


def setup(engine: Engine) -> None:
    rnd = random.Random(42)
    now = time.time()
    engine.wan_ips = {"203.0.113.7"}
    for key, d in DEVICES.items():
        dev = dict(d)
        engine.devices[dev["id"]] = dev
    for mac, name, ip, *_rest, link in CLIENTS:
        engine.clients[mac] = {"mac": mac, "ip": ip, "descr": name, "online": name not in IDLE, "last_seen": now}
        if link[0] == "wired":
            u = {"mac": mac, "ip": ip, "kind": "wired", "dev": DEVICES[link[1]]["id"], "port": link[2], "alias": name}
        else:
            u = {"mac": mac, "ip": ip, "kind": "wifi", "dev": DEVICES["ap"]["id"], "band": link[1], "ssid": "Hemma",
                 "rssi": link[2], "alias": name}
        if name not in IDLE:
            engine.unifi_clients[mac] = u
    for d in engine.devices.values():
        d["ports_up"] = []
    engine._rebuild_ip_map()
    engine.history.remember_clients([(mac, mac, ip, name, int(now)) for mac, name, ip, *_ in CLIENTS])

    # historik: 5-minutershinkar senaste 48 h, timhinkar bakåt till 30 dygn
    rows = []
    start_5m = int((now - 48 * 3600) // BUCKET * BUCKET)
    start_h = int((now - 30 * 86400) // 3600 * 3600)
    for res, start, end in ((3600, start_h, start_5m), (BUCKET, start_5m, int(now))):
        for t in range(start, end, res):
            hour = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
            shape = _daily_shape(hour)
            for mac, name, _ip, down, up, gb, _link in CLIENTS:
                per_slot = gb * 1e9 * res / 86400 * shape / .42
                for cc, direction, share, _ in FLOWS[name]:
                    total = per_slot * share * rnd.uniform(.6, 1.4)
                    ratio = up / (up + down) if (up + down) else .1
                    if name == "PS5" and 2.5 <= hour < 3.5:
                        total *= 6
                    rows.append((t, res, mac, cc, direction, int(total * ratio), int(total * (1 - ratio)),
                                 max(1, int(rnd.uniform(0, 4) * res / 300))))
    engine.history.insert_raw(rows)

    # aktiva anslutningar (för de linjer som syns "just nu")
    for mac, name, ip, *_ in CLIENTS:
        if name in IDLE:
            continue
        for cc, direction, _share, remote in FLOWS[name]:
            port = 32400 if direction == "in" else 50000 + rnd.randint(0, 9999)
            rport = 50000 + rnd.randint(0, 9999) if direction == "in" else 443
            key = ("tcp", ip, port, remote, rport)
            engine.conns[key] = Conn(key, ip, port, remote, rport, "tcp", direction, 0, 0)
    engine.geo.status.update(ok=True, error=None, source="Exempeldata")
    engine.geo.country = _demo_country  # type: ignore[method-assign]
    for name in ("pfsense", "unifi", "netflow"):
        engine.status[name].update(enabled=True, ok=True, error=None, last=now)
    tick(engine)


_REMOTE_CC = {remote: cc for flows in FLOWS.values() for cc, _d, _s, remote in flows}


def _demo_country(ip: str) -> str | None:
    return _REMOTE_CC.get(ip)


def tick(engine: Engine) -> None:
    now = time.time()
    rates = {}
    for mac, name, _ip, down, up, *_ in CLIENTS:
        if name in IDLE:
            continue
        j = lambda: random.uniform(.78, 1.22)  # noqa: E731
        rates[mac] = (down * 1e6 * j(), up * 1e6 * j())
    engine.rates = rates
    engine.wan_rate = (sum(r[0] for r in rates.values()) * 1.02, sum(r[1] for r in rates.values()) * 1.02)
    for name in ("pfsense", "unifi", "netflow"):
        engine.status[name]["last"] = now
