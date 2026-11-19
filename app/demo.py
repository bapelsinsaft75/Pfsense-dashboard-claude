"""Demoläge: fyller motorn med exempeldata så att dashboarden går att titta på innan något är konfigurerat."""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo
import random
import time

from .engine import Engine

STOCKHOLM = ZoneInfo("Europe/Stockholm")
from .history import BUCKET
from .pfsense import Conn

# id, namn, ip, typ, ned Mbit/s, upp Mbit/s, GB per dygn, anslutning
CLIENTS = [
    ("bc:33:29:7a:01:f2", "PS5", "192.168.2.41", 182, 3.2, 48.2, ("wired", "sw3", 3)),
    ("a8:23:fe:10:9c:44", "Vardagsrum-TV", "192.168.2.40", 24.5, .6, 31.7, ("wired", "sw3", 2)),
    ("d0:50:99:3a:11:0c", "Unraid", "192.168.2.10", 6.1, 41.8, 22.4, ("wired", "sw1", 3)),
    ("04:42:1a:c3:7e:90", "Kontor-PC", "192.168.2.22", 14.2, 8.9, 12.9, ("wired", "sw2", 2)),
    ("f4:d4:88:5b:2e:17", "MacBook-Pro", "192.168.2.21", 9.7, 2.4, 9.8, ("wifi", "5 GHz", -54)),
    ("90:dd:5d:21:b8:03", "Apple-TV", "192.168.2.42", 0, 0, 7.1, ("wired", "sw3", 4)),
    ("6c:4a:85:e0:33:9b", "iPad", "192.168.2.35", 3.8, .4, 4.3, ("wifi", "5 GHz", -61)),
    ("3a:91:0f:6e:d2:55", "iPhone-15", "192.168.2.34", 2.1, .9, 3.9, ("wifi", "5 GHz", -58)),
    ("8e:12:c7:40:aa:61", "Galaxy-S24", "192.168.2.36", 1.3, .3, 2.7, ("wifi", "5 GHz", -67)),
    ("48:a6:b8:0d:5f:2e", "Sonos-Kök", "192.168.2.50", .32, .02, 1.2, ("wifi", "2,4 GHz", -63)),
    ("dc:a6:32:9f:14:b7", "Home-Assistant", "192.168.2.60", .12, .21, .62, ("wired", "sw1", 5)),
    ("64:90:c1:2b:7d:08", "Roborock", "192.168.2.62", .01, .02, .15, ("wifi", "2,4 GHz", -71)),
    ("00:17:88:6c:41:d9", "Hue-Bridge", "192.168.2.61", 0, .01, .04, ("wired", "sw1", 7)),
    ("30:24:a9:8e:12:7c", "Skrivare-HP", "192.168.2.70", 0, 0, .01, ("wired", "sw2", 4)),
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
            lt = datetime.fromtimestamp(t, STOCKHOLM)
            hour = lt.hour + lt.minute / 60
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
    # total trafik enligt UniFi: internettrafiken plus intern trafik (Plex hemma, filkopiering, backup)
    tot: dict[tuple, list[int]] = {}
    for t, res, mac, _cc, _d, up, down, _n in rows:
        c = tot.setdefault((t, res, mac), [0, 0])
        c[0] += up
        c[1] += down
    for (t, res, mac), c in tot.items():
        hour = datetime.fromtimestamp(t, STOCKHOLM).hour
        iu, idn = _internal_bytes(mac, hour, res)
        c[0] += iu
        c[1] += idn
    with engine.history.lock:
        engine.history.db.executemany("INSERT INTO totals VALUES(?,?,?,?,?)",
                                      [(t, res, mac, c[0], c[1]) for (t, res, mac), c in tot.items()])
        engine.history.db.commit()
    # externa adresser per klient (en rad per timme senaste dygnet)
    rem_rows = []
    for t in range(int((now - 86400) // 3600 * 3600), int(now), 3600):
        for mac, name, _ip, down, up, gb, _link in CLIENTS:
            for cc, direction, share, remote in FLOWS[name]:
                total = gb * 1e9 / 24 * share * rnd.uniform(.5, 1.5)
                ratio = up / (up + down) if (up + down) else .1
                port = 32400 if direction == "in" else 443
                rem_rows.append((t, mac, remote, cc, direction, port, int(total * ratio), int(total * (1 - ratio)),
                                 rnd.randint(1, 20)))
    with engine.history.lock:
        engine.history.db.executemany("INSERT INTO remotes VALUES(?,?,?,?,?,?,?,?,?)", rem_rows)
        engine.history.db.commit()

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
    engine.geo.org = lambda ip: DEMO_ORG.get(ip.rsplit(".", 2)[0])  # type: ignore[method-assign]
    for name in ("pfsense", "unifi", "netflow", "syslog"):
        engine.status[name].update(enabled=True, ok=True, error=None, last=now)
    _seed_alerts(engine, now, rnd)
    _seed_plex(engine, now)
    _seed_unraid(engine, now)
    tick(engine)


def _seed_plex(engine: Engine, now: float) -> None:
    from .plex import PlexServer
    peter = PlexServer(0, "Peters Plex", "http://192.168.2.56:32400", "demo", False)
    alex = PlexServer(1, "Alex Plex", "http://192.168.100.20:32400", "demo", False)
    base = {"buffer": None, "address": None, "thumb": None, "hw": False, "target": None}
    peter.sessions = [
        {**base, "server": 0, "server_name": "Peters Plex", "id": "0:1", "type": "episode", "title": "Breaking Bad",
         "label": "S03E07", "subtitle": "One Minute · 2010", "user": "Alex", "device": "Vardagsrum-TV",
         "product": "Plex för LG", "state": "playing", "local": True, "duration": 2822000, "offset": 1394000,
         "decision": "direct", "video": "1080p · H264", "audio": "EAC3 5.1", "bandwidth_kbps": 12300},
        {**base, "server": 0, "server_name": "Peters Plex", "id": "0:2", "type": "movie", "title": "Dune: Part Two",
         "label": "Film", "subtitle": "2024 · 2 h 46 min", "user": "Peter", "device": "Apple-TV", "product": "Plex för tvOS",
         "state": "playing", "local": True, "duration": 9960000, "offset": 2800000, "decision": "direct",
         "video": "4K · HEVC", "audio": "TRUEHD 7.1", "bandwidth_kbps": 21800},
        {**base, "server": 0, "server_name": "Peters Plex", "id": "0:3", "type": "track", "title": "Lorde – Melodrama",
         "label": "Musik", "subtitle": "Green Light · 2017", "user": "Peter", "device": "Sonos-Kök", "product": "Plexamp",
         "state": "paused", "local": True, "duration": 234000, "offset": 91000, "decision": "direct",
         "video": None, "audio": "FLAC 1411 kbit/s", "bandwidth_kbps": 1400},
    ]
    alex.sessions = [
        {**base, "server": 1, "server_name": "Alex Plex", "id": "1:1", "type": "episode", "title": "The Bear",
         "label": "S02E06", "subtitle": "Fishes · 2023", "user": "Morfar", "device": "iPad", "product": "Plex för iOS",
         "state": "playing", "local": False, "address": "2.249.115.243", "duration": 3960000, "offset": 2875000,
         "buffer": 80, "decision": "transcode", "video": "4K · HEVC", "target": "720p", "hw": True, "audio": "AAC stereo",
         "bandwidth_kbps": 4100},
    ]
    for srv in (peter, alex):
        srv.status.update(ok=True, error=None, last=now, version="1.41.3.9314", streams=len(srv.sessions))
    engine.plex_servers = [peter, alex]
    _REMOTE_CC["2.249.115.243"] = "SE"


def _seed_unraid(engine: Engine, now: float) -> None:
    from .unraid import Unraid, build, health
    u = Unraid("LillaNasen", "http://192.168.2.10", "demo", False)
    TB = 11_718_885_324  # 12 TB-disk i KiB, som API:et
    disk = lambda name, dev, used, temp, spin=True: {"name": name, "device": dev, "size": TB, "status": "DISK_OK",  # noqa: E731
                                                    "temp": temp, "fsSize": TB, "fsUsed": int(TB * used), "fsFree": int(TB * (1 - used)),
                                                    "isSpinning": spin}
    sections = {
        "system": {"info": {"os": {"hostname": "Tower", "uptime": 18 * 86400 + 4 * 3600},
                            "cpu": {"brand": "Intel Core i5-12400", "threads": 12},
                            "versions": {"core": {"unraid": "7.2.4"}}},
                   "metrics": {"cpu": {"percentTotal": 23.4}, "memory": {"total": 34359738368, "used": 19541180416}}},
        "array": {"array": {"state": "STARTED",
                            "capacity": {"kilobytes": {"total": str(4 * TB), "used": str(int(2.62 * TB)), "free": str(int(1.38 * TB))}},
                            "parities": [{"name": "parity", "device": "WDC WD120EFBX", "size": TB, "status": "DISK_OK", "temp": 38,
                                          "isSpinning": True}],
                            "disks": [disk("disk1", "WDC WD120EFBX", .82, 39), disk("disk2", "ST12000VN0008", .71, None, False),
                                      disk("disk3", "ST12000VN0008", .94, 52), disk("disk4", "WDC WD120EFBX", .15, 36)],
                            "caches": [{"name": "cache", "device": "Samsung 990 Pro", "size": 1_953_514_584, "status": "DISK_OK",
                                        "temp": 44, "fsSize": 1_953_514_584, "fsUsed": 898_616_708, "isSpinning": True}]}},
        "parity": {"parityHistory": [{"date": "2026-11-03T03:30:00", "duration": 61200, "errors": 0, "status": "OK"}]},
        "docker": {"docker": {"containers": [
            {"names": [f"/{n}"], "state": st, "status": stat, "autoStart": auto} for n, st, stat, auto in (
                ("plex", "RUNNING", "Up 6 days", True), ("unifi-network-application", "RUNNING", "Up 18 days", True),
                ("pfsense-dashboard", "RUNNING", "Up 2 hours", True), ("home-assistant", "RUNNING", "Up 18 days", True),
                ("sonarr", "RUNNING", "Up 6 days", True), ("radarr", "RUNNING", "Up 6 days", True),
                ("prowlarr", "RUNNING", "Up 6 days", True), ("tautulli", "RUNNING", "Up 6 days", True),
                ("qbittorrent", "EXITED", "Exited (137) 12 minutes ago", True), ("handbrake", "EXITED", "Exited (0) 3 days ago", False))]}},
        "vms": {"vms": {"domains": [{"name": "Windows 11", "state": "RUNNING"}, {"name": "Ubuntu test", "state": "SHUTOFF"}]}},
    }
    u.data = build(sections)
    level, reasons = health(u.data)
    u.status.update(ok=True, last=now, level=level, name="LillaNasen", detail="; ".join(reasons))
    engine.unraids = [u, _second_unraid(now)]


def _second_unraid(now: float):
    from .unraid import Unraid, build, health
    u = Unraid("Tower", "http://192.168.2.11", "demo", False)
    TB = 15_625_000_000  # 16 TB-disk i KiB
    disk = lambda n, used, temp: {"name": n, "device": "TOSHIBA MG08ACA16TE", "size": TB, "status": "DISK_OK", "temp": temp,  # noqa: E731
                                  "fsSize": TB, "fsUsed": int(TB * used), "isSpinning": True}
    u.data = build({
        "system": {"info": {"os": {"hostname": "Tower", "uptime": 41 * 86400}, "cpu": {"brand": "AMD Ryzen 7 5700G", "threads": 16},
                            "versions": {"core": {"unraid": "7.2.4"}}},
                   "metrics": {"cpu": {"percentTotal": 7.9}, "memory": {"total": 68719476736, "used": 24051816858}}},
        "array": {"array": {"state": "STARTED",
                            "capacity": {"kilobytes": {"total": str(3 * TB), "used": str(int(1.3 * TB)), "free": str(int(1.7 * TB))}},
                            "parities": [{"name": "parity", "device": "TOSHIBA MG08ACA16TE", "size": TB, "status": "DISK_OK", "temp": 34,
                                          "isSpinning": True}],
                            "disks": [disk("disk1", .61, 35), disk("disk2", .43, 36), disk("disk3", .26, 34)],
                            "caches": []}},
        "docker": {"docker": {"containers": [{"names": [f"/{n}"], "state": "RUNNING", "status": "Up 41 days", "autoStart": True}
                                             for n in ("plex", "tautulli", "immich", "nextcloud", "mariadb")]}},
        "vms": {"vms": {"domains": []}},
    })
    level, reasons = health(u.data)
    u.status.update(ok=True, last=now, level=level, name="Tower", detail="; ".join(reasons) or "Allt ser bra ut")
    return u


def _seed_alerts(engine: Engine, now: float, rnd: random.Random) -> None:
    engine.gateways = {
        "WAN_DHCP": {"name": "WAN_DHCP", "state": "up", "status": "online", "loss": "0.0%", "delay": "4.1ms",
                     "monitor": "1.1.1.1", "vpn": False},
    }
    engine.vpn_clients = {"Littorin VPN": {"name": "Littorin VPN", "up": True, "status": "up, connected",
                                           "remote": "185.159.157.1"}}
    engine._update_vpn_status()
    a = engine.alerts
    vpn = a.raise_("ovpn:Littorin VPN", "critical", "vpn_down", "VPN-anslutningen Littorin VPN är nere",
                   'OpenVPN-klienten Littorin VPN har status "down, reconnecting".', now=now - 47 * 60)
    a.resolve("ovpn:Littorin VPN", "Littorin VPN är ansluten igen.", now=now - 39 * 60)
    a.raise_("data:bc:33:29:7a:01:f2", "warning", "data_anomaly", "PS5 använder ovanligt mycket data",
             "PS5 har använt 48,2 GB de senaste 24 timmarna. Normalt är 11,6 GB per dygn (4 gånger mer än vanligt).",
             now=now - 25 * 60)
    a.raise_("pfb:64:90:c1:2b:7d:08", "warning", "pfblocker", "Roborock försöker nå blockerade adresser",
             "pfBlockerNG har stoppat 31 försök från Roborock (192.168.2.62) de senaste 10 minuterna, "
             "till bland annat 47.88.0.12, 8.209.0.4. Det kan tyda på skadlig kod eller reklam/spårning.", now=now - 8 * 60)
    h = engine.history
    sigs = [("ET SCAN Suspicious inbound to mySQL port 3306", 2, 41), ("ET DROP Dshield Block Listed Source", 2, 128),
            ("ET POLICY Possible Plex Media Server Login from external", 3, 6), ("ET SCAN NMAP OS Detection Probe", 2, 9)]
    for label, prio, n in sigs:
        for _ in range(n):
            h.add_security("suricata", label, prio, "d0:50:99:3a:11:0c", "45.148.10.5", rnd.choice(["CN", "RU", "NL", "US"]),
                           "in", ts=now - rnd.uniform(0, 86000))
    for _ in range(214):
        h.add_security("pfblockerng", "igb1", 0, "64:90:c1:2b:7d:08", "47.88.0.12", "CN", "out", ts=now - rnd.uniform(0, 86000))
    for _ in range(38):
        h.add_security("pfblockerng", "igb1", 0, "a8:23:fe:10:9c:44", "8.8.4.4", "US", "out", ts=now - rnd.uniform(0, 86000))
    for cc, n in (("CN", 1830), ("RU", 1204), ("US", 760), ("NL", 512), ("BR", 330), ("IN", 290)):
        for _ in range(n):
            h.add_security("pfblockerng", "igb0", 0, "", "", cc, "in", ts=now - rnd.uniform(0, 86000))
    h.flush()
    del vpn


DEMO_ORG = {
    "18.192": "Amazon.com, Inc. (AS16509)", "3.80": "Amazon.com, Inc. (AS14618)", "133.11": "Sony Interactive Entertainment (AS2914)",
    "45.57": "Netflix Streaming Services (AS2906)", "194.68": "SVT / Akamai (AS20940)", "81.2": "BBC (AS2818)",
    "73.0": "Comcast Cable (AS7922)", "88.198": "Hetzner Online GmbH (AS24940)", "185.220": "Tor exit / Stiftung Erneuerbare Freiheit (AS60729)",
    "1.180": "China Telecom (AS4134)", "177.0": "Telefônica Brasil (AS27699)", "49.36": "Reliance Jio (AS55836)",
    "95.216": "Hetzner Online GmbH (AS24940)", "52.16": "Microsoft Corporation (AS8075)", "13.107": "Microsoft Corporation (AS8068)",
    "51.15": "Scaleway (AS12876)", "17.253": "Apple Inc. (AS714)", "17.0": "Apple Inc. (AS714)", "84.208": "Telenor Norge (AS2119)",
    "34.240": "Google LLC (AS15169)", "80.62": "TDC Holding (AS3292)", "5.8": "PJSC Rostelecom (AS12389)",
    "13.228": "Amazon.com, Inc. (AS16509)", "15.73": "HP Inc. (AS71)",
}

_REMOTE_CC = {remote: cc for flows in FLOWS.values() for cc, _d, _s, remote in flows}


def _demo_country(ip: str) -> str | None:
    return _REMOTE_CC.get(ip)


# intern trafik per klient i byte per sekund: (upp, ned)
INTERNAL = {
    "d0:50:99:3a:11:0c": (2.6e6, 0.2e6),   # Unraid: Plex till TV:n och Apple-TV
    "a8:23:fe:10:9c:44": (0.02e6, 1.6e6),  # Vardagsrum-TV
    "90:dd:5d:21:b8:03": (0.01e6, 0.9e6),  # Apple-TV
    "04:42:1a:c3:7e:90": (0.8e6, 0.6e6),   # Kontor-PC: filkopiering till LillaNasen
    "f4:d4:88:5b:2e:17": (0.15e6, 0.2e6),  # MacBook: Time Machine
}


def _internal_bytes(mac: str, hour: int, seconds: float) -> tuple[int, int]:
    up, down = INTERNAL.get(mac, (0, 0))
    evening = 1.6 if 18 <= hour <= 23 else 0.5 if 7 <= hour < 18 else 0.15
    if mac == "d0:50:99:3a:11:0c" and 1 <= hour < 4:  # nattlig backup till Unraid
        return int(up * 0.2 * seconds), int(9e6 * seconds)
    return int(up * evening * seconds), int(down * evening * seconds)


def _demo_recent(engine: Engine, now: float, seconds: float, at: float) -> None:
    for mac, name, _ip, down, up, *_ in CLIENTS:
        if name in IDLE:
            continue
        burst = 2.2 if name == "Kontor-PC" and int(at // 300) % 3 == 0 else 1
        j = random.uniform(.7, 1.3) * burst
        cc = FLOWS[name][0][0]
        iu, idn = int(up * 1e6 * j * seconds / 8), int(down * 1e6 * j * seconds / 8)
        engine.history.add_recent(mac, cc, iu, idn, at)
        hour = datetime.fromtimestamp(at, STOCKHOLM).hour
        lu, ld = _internal_bytes(mac, hour, seconds)
        r = engine.history.recent_all[(int(at // 30 * 30), mac)]
        r[0] += iu + int(lu * random.uniform(.7, 1.3))
        r[1] += idn + int(ld * random.uniform(.7, 1.3))


def tick(engine: Engine) -> None:
    now = time.time()
    if not getattr(engine, "_demo_recent_seeded", False):
        for t in range(int(now - 3600), int(now), 30):
            _demo_recent(engine, now, 30, t)
        engine._demo_recent_seeded = True
    else:
        _demo_recent(engine, now, 2, now)
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
