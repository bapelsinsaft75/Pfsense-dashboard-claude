"""Status och aktiva strömmar från en eller flera Plex Media Server."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("plex")

RES = {"4k": "4K", "2160": "4K", "1080": "1080p", "720": "720p", "576": "576p", "480": "480p", "sd": "SD"}


def _res_label(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return RES.get(str(value).lower(), f"{value}p" if str(value).isdigit() else str(value))


def _height_label(h: Any) -> str | None:
    try:
        h = int(h)
    except (TypeError, ValueError):
        return None
    if h >= 1600:
        return "4K"
    for limit, label in ((1000, "1080p"), (700, "720p"), (560, "576p"), (400, "480p")):
        if h >= limit:
            return label
    return f"{h}p"


def _channels(n: Any) -> str:
    return {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(n if isinstance(n, int) else -1, f"{n} kanaler" if n else "")


class PlexServer:
    def __init__(self, index: int, name: str, url: str, token: str, verify: bool) -> None:
        self.index = index
        self.name = name
        self.url = url
        self.token = token
        self.client = httpx.AsyncClient(
            base_url=url, verify=verify, timeout=10,
            headers={"X-Plex-Token": token, "Accept": "application/json",
                     "X-Plex-Product": "pfSense Natverkskarta", "X-Plex-Client-Identifier": "pfsense-dashboard"},
        )
        self.status: dict[str, Any] = {"name": name, "ok": False, "error": None, "last": None, "version": None,
                                       "streams": 0}
        self.sessions: list[dict] = []
        self.libraries: list[dict] = []
        self._lib_at = 0.0
        self._counts_at = 0.0

    async def poll(self) -> None:
        try:
            ident = await self.client.get("/identity")
            if ident.status_code < 400:
                self.status["version"] = (ident.json().get("MediaContainer") or {}).get("version")
            resp = await self.client.get("/status/sessions")
        except httpx.HTTPError as exc:
            self._fail(f"Kan inte nå {self.name} ({exc.__class__.__name__}). Kontrollera adressen {self.url}.")
            return
        if resp.status_code == 401:
            self._fail(f"{self.name} nekade åtkomst. Kontrollera Plex-token.")
            return
        if resp.status_code >= 400:
            self._fail(f"{self.name} svarade {resp.status_code}.")
            return
        try:
            items = (resp.json().get("MediaContainer") or {}).get("Metadata") or []
        except ValueError:
            self._fail(f"{self.name} gav inget JSON-svar.")
            return
        self.sessions = [s for s in (parse_session(m, self.index, self.name) for m in items) if s]
        self.status.update(ok=True, error=None, last=time.time(), streams=len(self.sessions))
        try:
            await self.poll_libraries()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("%s: kunde inte läsa biblioteken (%s)", self.name, exc)

    async def _json(self, path: str, params: dict | None = None) -> dict:
        r = await self.client.get(path, params=params)
        if r.status_code in (401, 403):
            raise PermissionError(r.status_code)
        r.raise_for_status()
        return r.json().get("MediaContainer") or {}

    async def _count(self, key: str, ptype: int | None) -> int | None:
        params = {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 0}
        if ptype:
            params["type"] = ptype
        try:
            mc = await self._json(f"/library/sections/{key}/all", params)
        except (httpx.HTTPError, ValueError, PermissionError):
            return None
        n = mc.get("totalSize", mc.get("size"))
        return int(n) if n is not None else None

    async def poll_libraries(self) -> None:
        """Biblioteken med antal titlar. Antalen läses var 5:e minut, och oftare medan Plex skannar."""
        now = time.time()
        scanning = any(lib.get("refreshing") for lib in self.libraries)
        if not scanning and now - self._lib_at < 60:
            return
        self._lib_at = now
        try:
            mc = await self._json("/library/sections")
        except PermissionError:
            return
        libs = []
        old = {x["key"]: x for x in self.libraries}
        for d in mc.get("Directory") or []:
            key = str(d.get("key"))
            prev = old.get(key, {})
            libs.append({"key": key, "title": d.get("title") or key, "type": d.get("type"),
                         "scanned": d.get("scannedAt") or d.get("updatedAt"),
                         "refreshing": str(d.get("refreshing")).lower() in ("1", "true"),
                         "progress": None, "count": prev.get("count"), "count2": prev.get("count2")})
        if scanning or any(x["refreshing"] for x in libs):
            try:
                acts = (await self._json("/activities")).get("Activity") or []
            except (httpx.HTTPError, ValueError, PermissionError):
                acts = []
            for a in acts:
                sec = str(((a.get("Context") or {}).get("librarySectionID")) or "")
                for lib in libs:
                    if lib["key"] == sec and a.get("progress") is not None:
                        lib["progress"] = a.get("progress")
                        lib["refreshing"] = True
        if now - self._counts_at > 300 or any(prev.get("refreshing") and not x["refreshing"]
                                              for x in libs for prev in [old.get(x["key"], {})]):
            self._counts_at = now
            for lib in libs:
                t1, t2 = {"movie": (1, None), "show": (2, 4), "artist": (8, 10), "photo": (None, None)}.get(lib["type"], (None, None))
                lib["count"] = await self._count(lib["key"], t1)
                lib["count2"] = await self._count(lib["key"], t2) if t2 else None
        self.libraries = libs

    async def refresh(self, key: str | None = None) -> None:
        """Be Plex skanna ett bibliotek (eller alla) efter nya och borttagna filer."""
        path = f"/library/sections/{key}/refresh" if key else "/library/sections/all/refresh"
        try:
            r = await self.client.get(path)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Kan inte nå {self.name} ({exc.__class__.__name__}).") from exc
        if r.status_code in (401, 403):
            raise RuntimeError(f"{self.name} tillåter inte uppdatering med den här token. Den måste tillhöra serverns ägare.")
        if r.status_code >= 400:
            raise RuntimeError(f"{self.name} svarade {r.status_code}.")
        for lib in self.libraries:
            if key is None or lib["key"] == key:
                lib["refreshing"] = True
        self._lib_at = 0

    def _fail(self, msg: str) -> None:
        self.sessions = []
        self.status.update(ok=False, error=msg, streams=0)
        log.warning("%s", msg)

    async def thumb(self, path: str) -> tuple[bytes, str] | None:
        params = {"width": 160, "height": 240, "minSize": 1, "upscale": 1, "url": path}
        try:
            resp = await self.client.get("/photo/:/transcode", params=params)
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        return resp.content, resp.headers.get("content-type", "image/jpeg")

    async def close(self) -> None:
        await self.client.aclose()


def parse_session(m: dict, index: int, server: str) -> dict | None:
    kind = m.get("type")
    if kind not in ("movie", "episode", "track", "clip"):
        return None
    player = m.get("Player") or {}
    session = m.get("Session") or {}
    user = (m.get("User") or {}).get("title") or "Okänd"
    media = (m.get("Media") or [{}])[0] or {}
    tc = m.get("TranscodeSession") or {}

    if kind == "episode":
        title = m.get("grandparentTitle") or m.get("title")
        s, e = m.get("parentIndex"), m.get("index")
        label = f"S{int(s):02d}E{int(e):02d}" if isinstance(s, int) and isinstance(e, int) else "Avsnitt"
        subtitle = " · ".join(str(x) for x in (m.get("title"), m.get("year")) if x)
        thumb = m.get("grandparentThumb") or m.get("thumb")
    elif kind == "track":
        title = " – ".join(x for x in (m.get("grandparentTitle"), m.get("parentTitle")) if x) or m.get("title")
        label = "Musik"
        subtitle = " · ".join(str(x) for x in (m.get("title"), m.get("parentYear") or m.get("year")) if x)
        thumb = m.get("parentThumb") or m.get("thumb")
    else:
        title = m.get("title")
        label = "Film" if kind == "movie" else "Klipp"
        dur = m.get("duration")
        dur_s = f"{dur // 3600000} h {dur // 60000 % 60} min" if isinstance(dur, int) and dur >= 3600000 else (
            f"{dur // 60000} min" if isinstance(dur, int) else None)
        subtitle = " · ".join(str(x) for x in (m.get("year"), dur_s) if x)
        thumb = m.get("thumb")

    video_dec = str(tc.get("videoDecision") or "").lower()
    audio_dec = str(tc.get("audioDecision") or "").lower()
    if not tc:
        decision = "direct"
    elif video_dec == "transcode" or (kind == "track" and audio_dec == "transcode"):
        decision = "transcode"
    elif video_dec == "copy" or audio_dec in ("copy", "transcode"):
        decision = "stream"
    else:
        decision = "direct"

    source_res = _res_label(media.get("videoResolution"))
    target_res = _height_label(tc.get("height")) if decision == "transcode" else None
    vcodec = str(media.get("videoCodec") or "").upper().replace("HEVC", "HEVC") or None
    acodec = str(media.get("audioCodec") or "").upper() or None
    video = " · ".join(x for x in (source_res, vcodec) if x) or None
    audio = " ".join(x for x in (acodec, _channels(media.get("audioChannels"))) if x) or None
    if kind == "track" and media.get("bitrate"):
        audio = f"{acodec or 'Ljud'} {media.get('bitrate')} kbit/s"

    local = player.get("local")
    if local is None:
        local = session.get("location") == "lan"
    bw = session.get("bandwidth")
    return {
        "server": index,
        "server_name": server,
        "id": f"{index}:{m.get('sessionKey') or session.get('id') or m.get('ratingKey')}",
        "type": kind,
        "title": title or "Okänd titel",
        "label": label,
        "subtitle": subtitle,
        "user": user,
        "device": player.get("title") or player.get("device") or player.get("platform"),
        "product": player.get("product"),
        "state": str(player.get("state") or "playing").lower(),
        "local": bool(local),
        "address": player.get("remotePublicAddress") or player.get("address"),
        "duration": m.get("duration"),
        "offset": m.get("viewOffset"),
        "buffer": tc.get("progress"),
        "decision": decision,
        "video": video,
        "target": target_res,
        "hw": bool(tc.get("transcodeHwRequested") or tc.get("transcodeHwFullPipeline")),
        "audio": audio,
        "bandwidth_kbps": bw if isinstance(bw, (int, float)) else None,
        "thumb": thumb,
    }
