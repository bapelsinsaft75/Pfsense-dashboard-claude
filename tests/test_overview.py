import time
from collections import deque

from app.config import Config
from app.engine import Engine
from app.overview import build
from app.unifi import parse_device


def _engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PFSENSE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("WAN_DOWN_MBIT", "1000")
    monkeypatch.setenv("WAN_UP_MBIT", "20")
    return Engine(Config())


def test_full_upload_explains_latency(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    now = time.time()
    e.gateways = {"WAN": {"name": "WAN", "state": "up", "vpn": False}}
    h = e.gw_hist["WAN"] = deque()
    for i in range(100):
        h.append((now - 3000 + i * 30, 10.0, 1.0, 0.0, None, None))
    h.append((now, 184.0, 38.0, 0.0, 200e6, 19.6e6))
    e.wan_rate = (200e6, 19.6e6)
    e.rates = {"aa": (1e6, 17e6)}
    e.aliases["aa"] = "Tower"
    o = build(e)
    lat = [p for p in o["problems"] if "fördröjning" in p["title"]]
    assert lat and lat[0]["level"] == "err" and "Tower" in lat[0]["why"] and "Uppladdningen är full" in lat[0]["why"]
    assert o["verdict"]["level"] == "err"
    up = next(t for t in o["tiles"] if t["label"] == "WAN upp")
    assert up["level"] == "err" and up["unit"] == "/20"


def test_all_ok_and_links(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.wan_rate = (100e6, 2e6)
    d = parse_device({"mac": "aa:bb:cc:00:00:01", "type": "usw", "name": "Switch", "state": 1,
                      "port_table": [{"port_idx": 1, "up": True, "speed": 1000, "is_uplink": True}],
                      "uplink": {"port_idx": 1, "speed": 1000, "rx_bytes-r": 112_500_000, "tx_bytes-r": 1_000_000}})
    ap = parse_device({"mac": "aa:bb:cc:00:00:02", "type": "uap", "name": "AP", "state": 1,
                       "uplink": {"uplink_mac": "aa:bb:cc:00:00:01", "speed": 1000, "rx_bytes-r": 1e6, "tx_bytes-r": 1e6},
                       "radio_table_stats": [{"radio": "na", "cu_total": 30, "num_sta": 4}]})
    e.devices = {d["id"]: d, ap["id"]: ap}
    o = build(e)
    # en upplänk som är 90 % full är en flaskhals
    assert [p["title"] for p in o["problems"]] == ["Länken pfSense ↔ Switch är nästan full"]
    sw = next(x for x in o["map"]["edges"] if x["to"] == d["id"])
    assert sw["from"] == "pfsense" and round(sw["pct"]) == 90
    assert next(x for x in o["map"]["edges"] if x["to"] == ap["id"])["from"] == d["id"]
    assert o["links"][0]["name"] == "pfSense ↔ Switch"


def test_nothing_wrong(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch)
    e.wan_rate = (100e6, 2e6)
    o = build(e)
    assert o["verdict"] == {"level": "ok", "text": "Allt fungerar", "sub": "Inga problem hittades."}
