import asyncio
import json

import httpx

from app.unraid import Unraid, build, health


def test_build_and_health():
    d = build({
        "system": {"info": {"os": {"hostname": "Tower", "uptime": 3600}, "cpu": {"brand": "i5", "threads": 12},
                            "versions": {"core": {"unraid": "7.2.4"}}},
                   "metrics": {"cpu": {"percentTotal": 12.5}, "memory": {"total": 100, "used": 40}}},
        "array": {"array": {"state": "STARTED", "capacity": {"kilobytes": {"total": "1000", "used": "950", "free": "50"}},
                            "parities": [{"name": "parity", "size": 100, "status": "DISK_OK", "temp": 35}],
                            "disks": [{"name": "disk1", "size": 100, "status": "DISK_OK", "temp": 55, "fsSize": 100, "fsUsed": 50},
                                      {"name": "disk2", "size": 100, "status": "DISK_OK", "temp": None, "isSpinning": False},
                                      {"name": "disk3", "size": 100, "status": "DISK_DSBL", "temp": 30}],
                            "caches": []}},
        "docker": {"docker": {"containers": [{"names": ["/plex"], "state": "RUNNING", "status": "Up 2 days", "autoStart": True},
                                             {"names": ["/qbit"], "state": "EXITED", "status": "Exited (137)", "autoStart": True},
                                             {"names": ["/hb"], "state": "EXITED", "status": "Exited (0)", "autoStart": False}]}},
        "vms": {"vms": {"domains": [{"name": "Win", "state": "RUNNING"}, {"name": "Ubu", "state": "SHUTOFF"}]}},
        "parity": None,
    })
    assert d["hostname"] == "Tower" and d["version"] == "7.2.4" and d["memory"]["pct"] == 40
    assert d["capacity"]["pct"] == 95
    disks = {x["name"]: x for x in d["disks"]}
    assert disks["disk1"]["label"] == "Varm" and disks["disk1"]["used_pct"] == 50
    assert disks["disk2"]["label"] == "Vilar" and disks["disk2"]["temp"] is None
    assert disks["disk3"]["status"] == "err"
    assert [c["state"] for c in d["containers"]] == ["bad", "running", "stopped"]
    assert [v["running"] for v in d["vms"]] == [True, False]
    level, reasons = health(d)
    assert level == "err" and any("disk3" in r for r in reasons) and any("qbit" in r for r in reasons)


def test_query_fallback():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.headers.get("x-api-key") != "nyckel":
            return httpx.Response(401)
        q = json.loads(req.content)["query"]
        seen.append(q)
        if "metrics" in q or "isSpinning" in q or "parityHistory" in q:
            return httpx.Response(200, json={"errors": [{"message": 'Cannot query field "x"'}]})
        if q.startswith("{ info"):
            return httpx.Response(200, json={"data": {"info": {"os": {"hostname": "Tower", "uptime": 10}, "cpu": {},
                                                               "versions": {"unraid": "7.2.4"}, "memory": {"total": 8, "used": 2}}}})
        if q.startswith("{ array"):
            return httpx.Response(200, json={"data": {"array": {"state": "STARTED", "capacity": {"kilobytes": {"total": "10", "used": "5", "free": "5"}},
                                                                "parities": [], "disks": [], "caches": []}}})
        if q.startswith("{ docker"):
            return httpx.Response(200, json={"data": {"docker": {"containers": []}}})
        return httpx.Response(200, json={"errors": [{"message": "okänd"}]})

    async def run():
        u = Unraid(None, "http://unraid.test", "nyckel", False)
        u.client = httpx.AsyncClient(base_url=u.url, headers=u.client.headers, transport=httpx.MockTransport(handler))
        await u.poll()
        assert u.status["ok"] and u.status["name"] == "Tower" and u.status["level"] == "ok"
        assert u.chosen["system"] == 1 and u.chosen["array"] == 1 and "parity" in u.errors
        assert u.data["version"] == "7.2.4" and u.data["memory"]["pct"] == 25
        bad = Unraid(None, "http://unraid.test", "fel", False)
        bad.client = httpx.AsyncClient(base_url=bad.url, headers=bad.client.headers, transport=httpx.MockTransport(handler))
        await bad.poll()
        assert not bad.status["ok"] and "API-nyckeln" in bad.status["error"]

    asyncio.run(run())


def test_config_two_servers(monkeypatch):
    from app.config import Config
    for k in ("UNRAID_URL", "UNRAID_API_KEY", "UNRAID_NAME", "UNRAID1_URL", "UNRAID2_URL", "UNRAID2_API_KEY", "UNRAID2_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("UNRAID_URL", "192.168.2.10")
    monkeypatch.setenv("UNRAID_API_KEY", "a")
    monkeypatch.setenv("UNRAID_NAME", "LillaNasen")
    monkeypatch.setenv("UNRAID2_URL", "http://192.168.2.11/")
    monkeypatch.setenv("UNRAID2_API_KEY", "b")
    monkeypatch.setenv("UNRAID2_NAME", "Tower")
    servers = Config().unraid_servers
    assert [(s["name"], s["url"], s["key"]) for s in servers] == [
        ("LillaNasen", "http://192.168.2.10", "a"), ("Tower", "http://192.168.2.11", "b")]
