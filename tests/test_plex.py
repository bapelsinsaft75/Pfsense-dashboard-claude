import asyncio

import httpx

from app.plex import PlexServer, parse_session

EPISODE = {
    "type": "episode", "sessionKey": "12", "grandparentTitle": "Breaking Bad", "title": "One Minute", "parentIndex": 3,
    "index": 7, "year": 2010, "duration": 2822000, "viewOffset": 1394000, "grandparentThumb": "/library/metadata/1/thumb/2",
    "User": {"title": "Alex"}, "Player": {"title": "Vardagsrum-TV", "product": "Plex for LG", "state": "playing", "local": True},
    "Session": {"bandwidth": 12300, "location": "lan"},
    "Media": [{"videoResolution": "1080", "videoCodec": "h264", "audioCodec": "eac3", "audioChannels": 6}],
}
TRANSCODE = {
    "type": "movie", "sessionKey": "13", "title": "Dune: Part Two", "year": 2024, "duration": 9960000, "viewOffset": 60000,
    "thumb": "/library/metadata/2/thumb/3", "User": {"title": "Morfar"},
    "Player": {"title": "iPad", "product": "Plex for iOS", "state": "paused", "local": False, "remotePublicAddress": "2.249.115.243"},
    "Session": {"bandwidth": 4100, "location": "wan"},
    "Media": [{"videoResolution": "4k", "videoCodec": "hevc", "audioCodec": "truehd", "audioChannels": 8}],
    "TranscodeSession": {"videoDecision": "transcode", "audioDecision": "transcode", "height": 720,
                         "transcodeHwRequested": True, "progress": 42.5},
}


def test_parse_episode_and_transcode():
    e = parse_session(EPISODE, 0, "Peters Plex")
    assert e["title"] == "Breaking Bad" and e["label"] == "S03E07" and e["subtitle"] == "One Minute · 2010"
    assert e["decision"] == "direct" and e["video"] == "1080p · H264" and e["audio"] == "EAC3 5.1" and e["local"]
    assert e["thumb"] == "/library/metadata/1/thumb/2" and e["bandwidth_kbps"] == 12300
    t = parse_session(TRANSCODE, 1, "Alex Plex")
    assert t["decision"] == "transcode" and t["target"] == "720p" and t["hw"] and not t["local"]
    assert t["state"] == "paused" and t["subtitle"] == "2024 · 2 h 46 min" and t["address"] == "2.249.115.243"
    assert parse_session({"type": "photo"}, 0, "x") is None


def test_poll_with_mock_server():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.headers.get("X-Plex-Token") != "hemlig":
            return httpx.Response(401)
        if req.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"version": "1.41.3"}})
        if req.url.path == "/status/sessions":
            return httpx.Response(200, json={"MediaContainer": {"size": 2, "Metadata": [EPISODE, TRANSCODE]}})
        return httpx.Response(404)

    async def run():
        s = PlexServer(0, "Peters Plex", "http://plex.test:32400", "hemlig", False)
        s.client = httpx.AsyncClient(base_url=s.url, headers=s.client.headers, transport=httpx.MockTransport(handler))
        await s.poll()
        assert s.status["ok"] and s.status["streams"] == 2 and s.status["version"] == "1.41.3"
        bad = PlexServer(1, "Alex Plex", "http://plex.test:32400", "fel", False)
        bad.client = httpx.AsyncClient(base_url=bad.url, headers=bad.client.headers, transport=httpx.MockTransport(handler))
        await bad.poll()
        assert not bad.status["ok"] and "token" in bad.status["error"]

    asyncio.run(run())


def test_plex_down_alert(tmp_path, monkeypatch):
    import time
    from app.config import Config
    from app.engine import Engine
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PFSENSE_URL", "http://127.0.0.1:9")
    e = Engine(Config())
    assert e.settings.kind_enabled("plex_down")  # på från början
    e.settings.update({"confirm_count": 2, "confirm_interval": 10})
    p = PlexServer(0, "Alex Plex", "http://plex.test:32400", "t", False)
    p.status.update(ok=False, error="Kan inte nå Alex Plex (ConnectError).")
    clock = {"t": time.time()}
    orig = e.confirm

    def confirm(key, bad, now=None):  # varje kontroll räknas som en minut senare
        clock["t"] += 60
        return orig(key, bad, clock["t"])
    e.confirm = confirm
    e.check_plex(p)
    assert not e.alerts.is_active("plex:http://plex.test:32400")
    e.check_plex(p)
    banner = e.alerts.banner()
    assert banner and banner[0]["title"] == "Alex Plex svarar inte" and banner[0]["level"] == "critical"
    p.status.update(ok=True, error=None)
    e.check_plex(p)
    assert not e.alerts.is_active("plex:http://plex.test:32400")
