import socket
import struct

from app.config import Config
from app.netflow import V5_HEADER, V5_RECORD, FlowParser
from app.pfsense import parse_arp, parse_endpoints, parse_lease, parse_state
from app.unifi import parse_client, parse_device


def cfg(monkeypatch=None):
    import os
    os.environ["LAN_SUBNETS"] = "192.168.1.0/24"
    os.environ.pop("PFSENSE_URL", None)
    return Config()


def ip4(s):
    return socket.inet_aton(s)


def test_netflow_v5():
    hdr = V5_HEADER.pack(5, 2, 1000, 1700000000, 0, 1, 0, 0, 0)
    r1 = V5_RECORD.pack(ip4("192.168.1.21"), ip4("93.184.216.34"), ip4("0.0.0.0"), 1, 2, 10, 1500, 0, 0, 51000, 443, 0, 0x1B, 6, 0, 0, 0, 24, 0, 0)
    r2 = V5_RECORD.pack(ip4("93.184.216.34"), ip4("192.168.1.21"), ip4("0.0.0.0"), 2, 1, 20, 90000, 0, 0, 443, 51000, 0, 0x1B, 6, 0, 0, 0, 0, 24, 0)
    flows = FlowParser().parse(hdr + r1 + r2, "192.168.1.1")
    assert [(f.src, f.dst, f.bytes, f.sport, f.proto) for f in flows] == [
        ("192.168.1.21", "93.184.216.34", 1500, 51000, 6),
        ("93.184.216.34", "192.168.1.21", 90000, 443, 6),
    ]


def _v9_packet(template=True):
    fields = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (1, 4), (2, 4)]
    tpl_body = struct.pack("!HH", 256, len(fields)) + b"".join(struct.pack("!HH", t, l) for t, l in fields)
    tpl = struct.pack("!HH", 0, 4 + len(tpl_body)) + tpl_body
    rec = ip4("192.168.1.34") + ip4("17.0.0.1") + struct.pack("!HHBII", 60000, 443, 6, 4242, 7)
    data = struct.pack("!HH", 256, 4 + len(rec) + 3) + rec + b"\0\0\0"
    hdr = struct.pack("!HHIIII", 9, 2 if template else 1, 0, 0, 1, 7)
    return hdr + (tpl if template else b"") + data


def test_netflow_v9_template_then_data():
    p = FlowParser()
    assert p.parse(_v9_packet(template=False), "x") == []  # okänd mall ännu
    flows = p.parse(_v9_packet(), "x")
    assert len(flows) == 1 and flows[0].dst == "17.0.0.1" and flows[0].bytes == 4242
    flows = p.parse(_v9_packet(template=False), "x")
    assert len(flows) == 1


def test_ipfix_v10():
    fields = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (1, 8)]
    tpl_body = struct.pack("!HH", 300, len(fields)) + b"".join(struct.pack("!HH", t, l) for t, l in fields)
    tpl = struct.pack("!HH", 2, 4 + len(tpl_body)) + tpl_body
    rec = ip4("8.8.8.8") + ip4("192.168.1.10") + struct.pack("!HHBQ", 53, 40000, 17, 999)
    data = struct.pack("!HH", 300, 4 + len(rec)) + rec
    body = tpl + data
    pkt = struct.pack("!HHIII", 10, 16 + len(body), 0, 0, 0) + body
    flows = FlowParser().parse(pkt, "x")
    assert len(flows) == 1 and flows[0].src == "8.8.8.8" and flows[0].bytes == 999 and flows[0].proto == 17


def test_endpoints():
    assert parse_endpoints("192.168.1.5:443") == [("192.168.1.5", 443)]
    assert parse_endpoints("203.0.113.7:6000 (192.168.1.5:5000)") == [("203.0.113.7", 6000), ("192.168.1.5", 5000)]
    assert parse_endpoints("2a00:1450:400f:80d::200e[443]") == [("2a00:1450:400f:80d::200e", 443)]
    assert parse_endpoints("[2001:db8::1]:80") == [("2001:db8::1", 80)]
    assert parse_endpoints({"address": "1.1.1.1", "port": "53"}) == [("1.1.1.1", 53)]


def test_state_outbound_and_inbound():
    c = cfg()
    out = parse_state({"interface": "lan", "protocol": "tcp", "direction": "in", "source": "192.168.1.21:51000",
                       "destination": "93.184.216.34:443", "bytes_in": 1000, "bytes_out": 50000}, c, set())
    assert out.initiated == "out" and out.lan_ip == "192.168.1.21" and out.up == 1000 and out.down == 50000
    # portforward: extern klient ansluter till Plex
    inb = parse_state({"interface": "wan", "protocol": "tcp", "direction": "in", "source": "73.1.2.3:55555",
                       "destination": "203.0.113.7:32400 (192.168.1.10:32400)", "bytes_in": 2000, "bytes_out": 900000},
                      c, {"203.0.113.7"})
    assert inb.initiated == "in" and inb.lan_ip == "192.168.1.10" and inb.remote_ip == "73.1.2.3"
    assert inb.up == 900000 and inb.down == 2000
    # WAN-tillståndet för en utgående NAT-anslutning ger samma nyckel som LAN-tillståndet
    nat = parse_state({"interface": "wan", "protocol": "tcp", "direction": "out",
                       "source": "192.168.1.21:51000 (203.0.113.7:61000)", "destination": "93.184.216.34:443",
                       "bytes": "1000:50000"}, c, {"203.0.113.7"})
    assert nat.key == out.key and nat.up == 1000
    # felaktigt omvänd källa/destination med väl känd extern port tolkas ändå som utgående
    rev = parse_state({"protocol": "tcp", "source": "93.184.216.34:443", "destination": "192.168.1.21:51000"}, c, set())
    assert rev.initiated == "out"
    # trafik mellan två LAN-klienter räknas inte
    assert parse_state({"protocol": "tcp", "source": "192.168.1.2:1", "destination": "192.168.1.3:2"}, c, set()) is None


def test_lease_arp():
    lease = parse_lease({"ip": "192.168.1.21", "mac": "F4-D4-88-5B-2E-17", "hostname": "macbook", "active_status": "active",
                         "online_status": "online"})
    assert lease["mac"] == "f4:d4:88:5b:2e:17" and lease["online"] is True
    arp = parse_arp({"ip_address": "192.168.1.21", "mac_address": "f4:d4:88:5b:2e:17", "expires": "Expires in 1100 seconds"})
    assert arp and not arp["expired"]


def test_unifi():
    w = parse_client({"mac": "AA:BB:CC:00:11:22", "is_wired": False, "ap_mac": "74:83:c2:00:00:04", "radio": "na",
                      "essid": "Hemma", "signal": -58, "ip": "192.168.1.34", "hostname": "iPhone"})
    assert w["kind"] == "wifi" and w["band"] == "5 GHz" and w["rssi"] == -58
    l = parse_client({"mac": "aa:bb:cc:00:11:23", "is_wired": True, "sw_mac": "74:83:C2:00:00:01", "sw_port": 3})
    assert l["kind"] == "wired" and l["dev"] == "74:83:c2:00:00:01" and l["port"] == 3
    d = parse_device({"mac": "74:83:c2:00:00:01", "type": "usw", "model": "USL16LP", "name": "Förråd", "state": 1,
                      "port_table": [{"port_idx": i, "up": i in (1, 3)} for i in range(1, 17)],
                      "uplink": {"uplink_mac": "00:11:22:33:44:55", "uplink_remote_port": None, "port_idx": 1}})
    assert d["model"] == "USW-Lite-16-PoE" and d["ports"] == 16 and d["ports_up"] == [1, 3] and d["own_uplink"] == 1
    assert parse_device({"mac": "00:00:00:00:00:01", "type": "ugw"}) is None


def test_base_url(monkeypatch):
    monkeypatch.setenv("UNIFI_URL", "https://192.168.2.9:8443/manage/site/default/dashboard")
    monkeypatch.setenv("PFSENSE_URL", "192.168.2.1/")
    c = Config()
    assert c.unifi_url == "https://192.168.2.9:8443" and c.pfsense_url == "https://192.168.2.1"


def test_guess_initiator():
    from app.pfsense import guess_initiator
    assert guess_initiator(51000, 443, "in") == "out"      # webb
    assert guess_initiator(50123, 3478, "in") == "out"     # STUN/videosamtal
    assert guess_initiator(62000, 5223, "in") == "out"     # Apple push
    assert guess_initiator(32400, 55555, "out") == "out"   # Plex: lämnas som den är
    assert guess_initiator(32400, 55555, "in") == "in"
    assert guess_initiator(51413, 60001, "in") == "in"     # torrent inkommande: båda höga, ingen ändring
    assert guess_initiator(22, 50000, "out") == "in"       # SSH till en LAN-server
    assert guess_initiator(123, 123, "out") == "out"


def test_state_high_remote_port_is_outbound():
    c = cfg()
    st = parse_state({"protocol": "udp", "source": "142.250.74.14:19302", "destination": "192.168.1.21:58000"}, c, set())
    assert st.initiated == "out"


def test_real_pfsense_29_state_format(monkeypatch):
    """Samma uppbyggnad som pfSense CE 2.9.0 med REST API v2 ger (adresserna är utbytta)."""
    monkeypatch.setenv("LAN_SUBNETS", "192.168.2.0/24")
    c = Config()
    wan = {"203.0.113.170"}
    rows = [
        {"interface": "igb3", "protocol": "tcp", "direction": "in", "source": "192.168.2.176:50642",
         "destination": "34.117.13.189:443", "bytes_in": 2681660, "bytes_out": 285963},
        {"interface": "igb0", "protocol": "tcp", "direction": "out", "source": "203.0.113.170:54388",
         "destination": "34.117.13.189:443", "bytes_in": 2681660, "bytes_out": 285963},
        {"interface": "all", "protocol": "tcp", "direction": "in", "source": "198.51.100.243:45262",
         "destination": "192.168.2.56:32400", "bytes_in": 172807, "bytes_out": 1158599},
        {"interface": "igb3", "protocol": "tcp", "direction": "out", "source": "198.51.100.243:45262",
         "destination": "192.168.2.56:32400", "bytes_in": 172807, "bytes_out": 1158599},
        {"interface": "igb3", "protocol": "tcp", "direction": "in", "source": "192.168.2.56:53801",
         "destination": "90.184.93.175:56555", "bytes_in": 26631, "bytes_out": 752009},
        {"interface": "igb0", "protocol": "udp", "direction": "out", "source": "203.0.113.170:18474",
         "destination": "176.10.148.59:1194", "bytes_in": 54667037255, "bytes_out": 92201119192},
        {"interface": "igb0", "protocol": "icmp", "direction": "out", "source": "203.0.113.170:54479",
         "destination": "203.0.113.1:8"},
    ]
    # 198.51.100.x är en dokumentationsadress och räknas inte som publik; byt till en publik för testet
    rows[2]["source"] = rows[3]["source"] = "2.249.115.243:45262"
    parsed = [parse_state(r, c, wan) for r in rows]
    out, out_wan, plex, plex_lan, p2p, vpn, icmp = parsed
    assert out.initiated == "out" and out.lan_ip == "192.168.2.176" and out.up == 2681660 and out.down == 285963
    assert out_wan is None and vpn is None and icmp is None  # routerns egna rader på WAN räknas inte
    assert plex.initiated == "in" and plex.lan_port == 32400 and plex.up == 1158599 and plex.down == 172807
    assert plex.key == plex_lan.key  # samma anslutning, räknas en gång
    assert p2p.initiated == "out"  # båda portarna höga: fälten avgör
