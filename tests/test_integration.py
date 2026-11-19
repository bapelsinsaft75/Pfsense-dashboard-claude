"""Kör hela appen mot låtsasservrar för pfSense och UniFi."""
import asyncio
import os
import socket
import struct
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

KEY = "testnyckel"
STATE_BYTES = {"n": 1000}


def make_pfsense() -> FastAPI:
    app = FastAPI()

    def wrap(data):
        return {"code": 200, "status": "ok", "response_id": "SUCCESS", "message": "", "data": data}

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.headers.get("x-api-key") != KEY:
            return JSONResponse({"code": 401, "status": "unauthorized", "data": []}, status_code=401)
        return await call_next(request)

    @app.get("/api/v2/status/dhcp_server/leases")
    def leases():
        return wrap([{"ip": "192.168.1.21", "mac": "f4:d4:88:5b:2e:17", "hostname": "macbook", "active_status": "active",
                      "online_status": "online"},
                     {"ip": "192.168.1.10", "mac": "d0:50:99:3a:11:0c", "hostname": "Tower", "descr": "Unraid",
                      "active_status": "static", "online_status": "online"}])

    @app.get("/api/v2/diagnostics/arp_table")
    def arp():
        return wrap([{"ip_address": "192.168.1.21", "mac_address": "f4:d4:88:5b:2e:17", "interface": "igb1",
                      "expires": "Expires in 1200 seconds"},
                     {"ip_address": "192.168.1.10", "mac_address": "d0:50:99:3a:11:0c", "interface": "igb1",
                      "expires": "Expires in 900 seconds"}])

    @app.get("/api/v2/status/interfaces")
    def interfaces():
        STATE_BYTES["n"] += 5_000_000
        return wrap([{"name": "wan", "descr": "WAN", "ipaddr": "203.0.113.7", "inbytes": STATE_BYTES["n"] * 10,
                      "outbytes": STATE_BYTES["n"]}])

    @app.get("/api/v2/firewall/states")
    def states():
        n = STATE_BYTES["n"]
        return wrap([
            {"interface": "lan", "protocol": "tcp", "direction": "in", "source": "192.168.1.21:51000",
             "destination": "8.8.8.8:443", "bytes_in": n // 10, "bytes_out": n},
            {"interface": "wan", "protocol": "tcp", "direction": "in", "source": "9.9.9.9:55555",
             "destination": "203.0.113.7:32400 (192.168.1.10:32400)", "bytes_in": n // 100, "bytes_out": n // 2},
        ])

    return app


def make_unifi() -> FastAPI:
    app = FastAPI()

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        if body.get("username") != "dashboard":
            return JSONResponse({"meta": {"rc": "error"}}, status_code=400)
        resp = JSONResponse({"meta": {"rc": "ok"}, "data": []})
        resp.set_cookie("unifises", "abc")
        return resp

    @app.get("/api/s/default/stat/sta")
    def sta(request: Request):
        if request.cookies.get("unifises") != "abc":
            return JSONResponse({"meta": {"rc": "error", "msg": "api.err.LoginRequired"}}, status_code=401)
        return {"meta": {"rc": "ok"}, "data": [
            {"mac": "f4:d4:88:5b:2e:17", "ip": "192.168.1.21", "is_wired": False, "ap_mac": "74:83:c2:00:00:04",
             "radio": "na", "essid": "Hemma", "signal": -54, "name": "MacBook-Pro"},
            {"mac": "d0:50:99:3a:11:0c", "ip": "192.168.1.10", "is_wired": True, "sw_mac": "74:83:c2:00:00:01", "sw_port": 3},
        ]}

    @app.get("/api/s/default/stat/device")
    def dev():
        return {"meta": {"rc": "ok"}, "data": [
            {"mac": "74:83:c2:00:00:01", "type": "usw", "model": "USL8LP", "name": "Switch Kontor", "state": 1,
             "port_table": [{"port_idx": i, "up": i in (1, 3, 8)} for i in range(1, 9)], "uplink": {"port_idx": 1}},
            {"mac": "74:83:c2:00:00:04", "type": "uap", "model": "UAP6MP", "name": "AP Hall", "state": 1,
             "uplink": {"uplink_mac": "74:83:c2:00:00:01", "uplink_remote_port": 8}},
        ]}

    return app


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server startade inte")


@pytest.fixture()
def running(tmp_path):
    pf_port, uf_port, nf_port = free_port(), free_port(), free_port(socket.SOCK_DGRAM)
    servers = [serve(make_pfsense(), pf_port), serve(make_unifi(), uf_port)]
    os.environ.update({
        "PFSENSE_URL": f"http://127.0.0.1:{pf_port}", "PFSENSE_API_KEY": KEY, "PFSENSE_POLL_SECONDS": "3",
        "UNIFI_URL": f"http://127.0.0.1:{uf_port}", "UNIFI_USER": "dashboard", "UNIFI_PASS": "x",
        "LAN_SUBNETS": "192.168.1.0/24", "DATA_DIR": str(tmp_path), "NETFLOW_PORT": str(nf_port),
        "GEOIP_AUTO_DOWNLOAD": "false", "DEMO": "false",
    })
    yield nf_port
    for s in servers:
        s.should_exit = True


def test_end_to_end(running):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        engine = app.state.engine
        engine.geo.country = lambda ip: {"8.8.8.8": "US", "9.9.9.9": "DE", "1.1.1.1": "AU"}.get(ip)
        deadline = time.time() + 15
        while time.time() < deadline:
            d = client.get("/api/summary").json()
            if d["status"]["pfsense"]["ok"] and d["status"]["unifi"]["ok"] and engine._prev_states_ts and \
                    any(c["down_bps"] > 0 for c in d["clients"]):
                break
            time.sleep(0.5)
        assert d["mode"] == "live"
        assert d["status"]["pfsense"]["ok"], d["status"]
        assert d["status"]["unifi"]["ok"], d["status"]
        names = {c["name"]: c for c in d["clients"]}
        assert set(names) == {"MacBook-Pro", "Unraid"}
        assert names["MacBook-Pro"]["conn"]["kind"] == "wifi" and names["MacBook-Pro"]["conn"]["band"] == "5 GHz"
        assert names["Unraid"]["conn"] == {"kind": "wired", "dev": "74:83:c2:00:00:01", "port": 3, "dev_name": "Switch Kontor"}
        assert names["MacBook-Pro"]["down_bps"] > 0
        assert d["kpi"]["wifi"] == 1 and d["kpi"]["wired"] == 1
        flows = {(f["cc"], f["dir"]) for f in d["flows"]}
        assert ("US", "out") in flows and ("DE", "in") in flows
        sw = next(x for x in d["devices"] if x["kind"] == "switch")
        assert sw["used"] == {"3": "Unraid"} and sw["infra"]["8"] == "AP Hall" and sw["infra"]["1"] == "Upplänk"
        ap = next(x for x in d["devices"] if x["kind"] == "ap")
        assert ap["bands"] == {"5 GHz": 1} and ap["uplink"] == "Switch Kontor, port 8"

        # NetFlow: ett v5-paket ska tas emot och växla historikkällan till netflow
        from app.netflow import V5_HEADER, V5_RECORD
        rec = V5_RECORD.pack(socket.inet_aton("1.1.1.1"), socket.inet_aton("192.168.1.21"), b"\0" * 4, 0, 0, 5, 777000,
                             0, 0, 443, 52000, 0, 0, 6, 0, 0, 0, 0, 0, 0)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(V5_HEADER.pack(5, 1, 0, 0, 0, 0, 0, 0, 0) + rec, ("127.0.0.1", running))
        s.close()
        for _ in range(40):
            if engine.nf_flows:
                break
            time.sleep(0.1)
        assert engine.nf_flows == 1 and engine.history_source() == "netflow"
        engine.history.flush()
        d = client.get("/api/summary?period=7d").json()
        assert ("AU", "out") in {(f["cc"], f["dir"]) for f in d["flows"]}
        assert client.get("/api/debug/raw/states").json()["sample"]
        assert client.get("/").status_code == 200
