import asyncio

from app.pihole import (PiHole, PiHoleError, _version_text, base_domain, classify, clean_domain, parse_query,
                        rule_label, subdomain_regex)


def test_base_domain_and_regex():
    assert base_domain("telemetry.roborock.com") == "roborock.com"
    assert base_domain("news.bbc.co.uk") == "bbc.co.uk"
    rx = subdomain_regex("a.b.example.com")
    assert rx == r"(\.|^)example\.com$"
    assert rule_label(rx) == "*.example.com"
    assert rule_label("plain.com") == "plain.com"


def test_clean_domain():
    assert clean_domain(" HTTPS://Ads.Example.com/path ") == "ads.example.com"
    for bad in ("", "nodot", "a b.com", "-bad.com"):
        try:
            clean_domain(bad)
        except PiHoleError:
            continue
        raise AssertionError(bad)


def test_classify_and_parse():
    assert classify({"status": "GRAVITY"}) == (True, "blocklista")
    assert classify({"status": "CACHE"}) == (False, "cache")
    assert classify({"status": "FORWARDED", "list_id": 4}) == (False, "egen tillåtlista")
    q = parse_query({"id": 1, "time": 10.5, "domain": "x.com", "type": "A", "status": "FORWARDED",
                     "client": {"ip": "192.168.2.5", "name": "pc"}, "reply": {"time": 12.3}})
    assert q["blocked"] is False and q["reply_ms"] == 12.3 and q["ip"] == "192.168.2.5"
    assert parse_query({"status": "GRAVITY", "reply": {"time": -1}})["reply_ms"] is None


def test_version_update():
    local, update = _version_text({"version": {"core": {"local": {"version": "v6.1"}, "remote": {"version": "v6.2"}},
                                               "ftl": {"local": {"version": "v6.1"}, "remote": {"version": "v6.1"}}}})
    assert local == "v6.1" and update == "v6.2"
    assert _version_text({"version": {"core": {"local": {"version": "v6.2"}, "remote": {"version": "v6.2"}}}})[1] is None


def test_compose_merges_clients_and_buckets():
    hist = {"history": [{"timestamp": 0, "total": 10, "blocked": 2, "cached": 5},
                        {"timestamp": 600, "total": 20, "blocked": 4, "cached": 8},
                        {"timestamp": 3600, "total": 5, "blocked": 1, "cached": 1}]}
    d = PiHole._compose(hist, {"domains": [{"domain": "a.com", "count": 3}]}, {"domains": []},
                        {"clients": [{"ip": "1.1.1.1", "name": "x", "count": 30}]},
                        {"clients": [{"ip": "1.1.1.1", "count": 6}, {"ip": "2.2.2.2", "count": 1}]}, 3600)
    assert (d["total"], d["blocked"], d["cached"]) == (35, 7, 14)
    assert d["history"] == [{"t": 0, "allowed": 24, "blocked": 6}, {"t": 3600, "allowed": 4, "blocked": 1}]
    by = {c["ip"]: c for c in d["clients"]}
    assert by["1.1.1.1"]["blocked"] == 6 and by["2.2.2.2"]["total"] == 0


class FakePiHole(PiHole):
    def __init__(self):
        super().__init__("Pi-hole", "http://x", "pw", False)
        self.calls = []
        self.store = []
        self.logins = 0

    async def _api(self, method, path, params=None, json=None):
        self.calls.append((method, path, json))
        if path == "/auth":
            self.logins += 1
            return 200, {"session": {"valid": True, "sid": "abc"}}
        if not self.sid:
            return 401, {"error": {"message": "unauthorized"}}
        if path == "/domains" and method == "GET":
            return 200, {"domains": [dict(d) for d in self.store]}
        if path.startswith("/domains/") and method == "POST":
            _, _, t, k = path.split("/")
            self.store.append({"domain": json["domain"], "type": t, "kind": k})
            return 201, {}
        if path.startswith("/domains/") and method == "DELETE":
            _, _, t, k, d = path.split("/")
            self.store = [r for r in self.store if not (r["type"] == t and r["kind"] == k and r["domain"] == d)]
            return 204, None
        if path == "/dns/blocking":
            return 200, {"blocking": "disabled" if json and not json["blocking"] else "enabled", "timer": json and json["timer"]}
        return 404, None


def test_login_and_domain_changes():
    async def run():
        p = FakePiHole()
        # första anropet ger 401 → inloggning → nytt försök
        await p.change_domain("block", "ads.example.com")
        assert p.logins == 1 and p.sid == "abc"
        assert {"domain": "ads.example.com", "type": "deny", "kind": "exact"} in p.store
        # avblockera en egen blockering: tar bort den i stället för att lägga till i tillåtlistan
        what = await p.change_domain("unblock", "ads.example.com")
        assert p.store == [] and "tog bort" in what
        # avblockera något från blocklistan: hamnar i tillåtlistan
        await p.change_domain("unblock", "track.example.com", scope="sub")
        assert p.store == [{"domain": r"(\.|^)example\.com$", "type": "allow", "kind": "regex"}]
        b = await p.set_blocking(False, 300)
        assert b["state"] == "disabled" and b["timer"] == 300
        assert p.brief()["paused_left"] in (299, 300)
    asyncio.run(run())
