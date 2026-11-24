"""Plex: inloggning (PIN-flöde), synk av bibliotek och sett-historik, samt filmönskningar."""
import asyncio
import logging
import os
import re
import time
import urllib.parse

import httpx

from . import config, db

log = logging.getLogger("plex")
PLEX_TV = os.environ.get("PLEX_TV_API", "https://plex.tv/api/v2")  # kan bytas ut vid test
PRODUCT = "Film Dilemma (lokal)"


class PlexError(Exception):
    pass


def _headers(token=None):
    h = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Client-Identifier": config.plex_client_id(),
        "X-Plex-Version": "1.0",
        "X-Plex-Platform": "Web",
        "X-Plex-Device-Name": "Film Dilemma",
    }
    if token:
        h["X-Plex-Token"] = token
    return h


def _client():
    """Klient mot plex.tv (certifikat kontrolleras)."""
    return httpx.AsyncClient(timeout=20)


def _lan_client():
    """Klient mot tjänster på ditt LAN (Plex-server, Overseerr, Radarr) som ofta har egensignerade certifikat."""
    return httpx.AsyncClient(timeout=20, verify=False)


# ---------- Inloggning ----------

async def create_pin():
    async with _client() as c:
        r = await c.post(f"{PLEX_TV}/pins", params={"strong": "true"}, headers=_headers())
        r.raise_for_status()
        pin = r.json()
    forward = f"{config.PUBLIC_URL}/plex-login?pin={pin['id']}"
    q = urllib.parse.urlencode({
        "clientID": config.plex_client_id(),
        "code": pin["code"],
        "context[device][product]": PRODUCT,
        "forwardUrl": forward,
    })
    return {"id": pin["id"], "code": pin["code"], "auth_url": f"https://app.plex.tv/auth#?{q}"}


async def check_pin(pin_id):
    async with _client() as c:
        r = await c.get(f"{PLEX_TV}/pins/{int(pin_id)}", headers=_headers())
        if r.status_code == 404:
            raise PlexError("Inloggningen har gått ut. Försök igen.")
        r.raise_for_status()
        return r.json().get("authToken")


async def server_identity():
    """Hämtar och sparar din Plex-servers machineIdentifier."""
    if not (config.PLEX_URL and config.PLEX_TOKEN):
        return None
    mid = config.plex_machine_id()
    if mid:
        return mid
    async with _lan_client() as c:
        r = await c.get(f"{config.PLEX_URL}/identity", headers=_headers(config.PLEX_TOKEN))
        r.raise_for_status()
        mid = r.json()["MediaContainer"]["machineIdentifier"]
    db.set_meta("plex_machine_id", mid)
    return mid


async def login_with_token(user_token):
    """Kontrollerar att Plex-användaren har tillgång till din server och skapar/uppdaterar kontot."""
    async with _client() as c:
        r = await c.get(f"{PLEX_TV}/user", headers=_headers(user_token))
        if r.status_code == 401:
            raise PlexError("Plex godkände inte inloggningen.")
        r.raise_for_status()
        u = r.json()

        mid = await server_identity()
        if not mid:
            raise PlexError("Plex-servern är inte konfigurerad (PLEX_URL och PLEX_TOKEN saknas).")
        r = await c.get(f"{PLEX_TV}/resources", params={"includeHttps": "1", "includeRelay": "0"},
                        headers=_headers(user_token))
        r.raise_for_status()
        server = next((s for s in r.json() if s.get("clientIdentifier") == mid), None)
    if not server:
        raise PlexError("Ditt Plex-konto har inte tillgång till den här Plex-servern.")

    is_admin = 1 if server.get("owned") else 0
    server_token = server.get("accessToken") or user_token
    conn = db.connect()
    row = conn.execute("SELECT id FROM users WHERE plex_id=?", (u["id"],)).fetchone()
    name = u.get("username") or u.get("title") or u.get("email") or f"plex{u['id']}"
    if row:
        conn.execute("""UPDATE users SET username=?, email=?, thumb=?, is_admin=?, plex_token=?, server_token=?,
            last_login=CURRENT_TIMESTAMP WHERE id=?""",
                     (name, u.get("email"), u.get("thumb"), is_admin, user_token, server_token, row["id"]))
        uid = row["id"]
    else:
        cur = conn.execute("""INSERT INTO users(plex_id,username,email,thumb,is_admin,plex_token,server_token,last_login)
            VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                           (u["id"], name, u.get("email"), u.get("thumb"), is_admin, user_token, server_token))
        uid = cur.lastrowid
    conn.commit()
    return uid


# ---------- Bibliotek och sett-historik ----------

async def _sections(c, token):
    """Film- och seriebibliotek som token har tillgång till: [{key, title, type}]."""
    r = await c.get(f"{config.PLEX_URL}/library/sections", headers=_headers(token))
    r.raise_for_status()
    return [{"key": str(d["key"]), "title": d.get("title") or d["key"], "type": d.get("type")}
            for d in r.json()["MediaContainer"].get("Directory", []) if d.get("type") in ("movie", "show")]


_LEGACY = re.compile(r"com\.plexapp\.agents\.(themoviedb|imdb|thetvdb|tvdb)://([^?/]+)")


def _ext_ids(item):
    """Plockar ut TMDB/IMDb/TVDB-id från både nya Plex-agenten (Guid-lista) och äldre agenter (guid-sträng)."""
    ids = {}
    for g in item.get("Guid", []) or []:
        gid = str(g.get("id", ""))
        for prefix, key in (("tmdb://", "tmdb"), ("imdb://", "imdb"), ("tvdb://", "tvdb")):
            if gid.startswith(prefix):
                ids.setdefault(key, gid[len(prefix):])
    m = _LEGACY.search(str(item.get("guid", "")))
    if m:
        key = {"themoviedb": "tmdb", "imdb": "imdb", "thetvdb": "tvdb", "tvdb": "tvdb"}[m.group(1)]
        ids.setdefault(key, m.group(2))
    if "tmdb" in ids:
        try:
            ids["tmdb"] = int(ids["tmdb"])
        except ValueError:
            ids.pop("tmdb")
    return ids


def _tmdb_id(item):
    return _ext_ids(item).get("tmdb")


async def _section_items(c, token, section):
    plex_type = "1" if section["type"] == "movie" else "2"
    r = await c.get(f"{config.PLEX_URL}/library/sections/{section['key']}/all",
                    params={"type": plex_type, "includeGuids": "1"}, headers=_headers(token), timeout=180)
    r.raise_for_status()
    return r.json()["MediaContainer"].get("Metadata", []) or []


async def _resolve_ids(raw):
    """Fyller i TMDB-id för Plex-objekt som bara har IMDb- eller TVDB-id (slås upp via TMDB)."""
    from . import tmdb_import
    if not tmdb_import.configured():
        return
    todo = [r for r in raw if not r["tmdb"] and (r["imdb"] or r["tvdb"])]
    if not todo:
        return
    t = tmdb_import.Tmdb()
    try:
        async def one(r):
            src, ext = ("imdb", r["imdb"]) if r["imdb"] else ("tvdb", r["tvdb"])
            tid, media = await tmdb_import.find_by_external(t, src, ext)
            want = "movie" if r["type"] == "movie" else "tv"
            if tid and media == want:
                r["tmdb"] = tid
        for start in range(0, len(todo), 100):
            await asyncio.gather(*(one(r) for r in todo[start:start + 100]))
    finally:
        await t.close()


async def sync_library():
    """Hämtar alla film- och seriebibliotek med innehåll (med admin-token) och kopplar dem till TMDB."""
    if not (config.PLEX_URL and config.PLEX_TOKEN):
        return 0
    from . import tmdb_import
    await server_identity()
    sections, raw = [], []
    async with _lan_client() as c:
        for sec in await _sections(c, config.PLEX_TOKEN):
            its = await _section_items(c, config.PLEX_TOKEN, sec)
            sections.append((sec["key"], sec["title"], sec["type"], len(its)))
            for it in its:
                ids = _ext_ids(it)
                raw.append({"section": sec["key"], "rk": str(it["ratingKey"]), "type": sec["type"],
                            "title": it.get("title") or "?", "year": it.get("year"), "thumb": it.get("thumb"),
                            "art": it.get("art"), "tmdb": ids.get("tmdb"), "imdb": ids.get("imdb"),
                            "tvdb": ids.get("tvdb"), "added": it.get("addedAt"), "episodes": it.get("leafCount"),
                            "seasons": it.get("childCount"), "summary": (it.get("summary") or "")[:500]})
    await _resolve_ids(raw)

    # Lokalt id i movies-tabellen: filmer = TMDB-id, serier = TV_OFFSET + TMDB-id
    def local_id(r):
        if not r["tmdb"]:
            return None
        return r["tmdb"] if r["type"] == "movie" else db.TV_OFFSET + r["tmdb"]

    conn = db.connect()
    have = {row[0] for row in conn.execute("SELECT id FROM movies")}
    missing_movies = sorted({r["tmdb"] for r in raw if r["type"] == "movie" and r["tmdb"] and r["tmdb"] not in have})
    missing_tv = sorted({r["tmdb"] for r in raw if r["type"] == "show" and r["tmdb"]
                         and db.TV_OFFSET + r["tmdb"] not in have})
    if missing_movies or missing_tv:
        await tmdb_import.import_titles(missing_movies, missing_tv)

    library = {}
    for r in raw:
        lid = local_id(r)
        if lid and lid not in library:
            library[lid] = (lid, r["rk"], r["title"], r["year"], r["added"])
    with conn:
        conn.execute("DELETE FROM plex_sections")
        conn.executemany("INSERT INTO plex_sections(key,title,type,item_count) VALUES(?,?,?,?)", sections)
        conn.execute("DELETE FROM plex_items")
        conn.executemany("""INSERT OR REPLACE INTO plex_items(section_key,rating_key,type,title,year,thumb,art,tmdb_id,
            imdb_id,tvdb_id,added_at,episodes,seasons,summary) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(r["section"], r["rk"], r["type"], r["title"], r["year"], r["thumb"], r["art"], local_id(r),
                           r["imdb"], r["tvdb"], r["added"], r["episodes"], r["seasons"], r["summary"]) for r in raw])
        conn.execute("DELETE FROM plex_library")
        conn.executemany("INSERT INTO plex_library(tmdb_id,rating_key,title,year,added_at) VALUES(?,?,?,?,?)",
                         list(library.values()))
        # Önskningar som nu finns i Plex markeras som tillgängliga
        conn.execute("UPDATE requests SET status='available' WHERE movie_id IN (SELECT tmdb_id FROM plex_library)")
    db.set_meta("plex_last_sync", int(time.time()))
    unmatched = sum(1 for r in raw if not r["tmdb"])
    log.info("Plex synkat: %d bibliotek, %d titlar, %d kopplade till TMDB, %d utan TMDB-koppling",
             len(sections), len(raw), len(raw) - unmatched, unmatched)
    return len(library)


async def sync_watched(user_id):
    """Sparar vilka bibliotek användaren ser och markerar filmer som setts i Plex som sedda."""
    if not config.PLEX_URL:
        return 0
    conn = db.connect()
    u = conn.execute("SELECT server_token FROM users WHERE id=?", (user_id,)).fetchone()
    if not u or not u["server_token"]:
        return 0
    seen, keys, progress = [], [], []
    # Plex-id -> lokalt film/serie-id (sätts av sync_library, som också löser IMDb/TVDB-id)
    local_ids = {r["rating_key"]: r["tmdb_id"] for r in
                 conn.execute("SELECT rating_key, tmdb_id FROM plex_items WHERE tmdb_id IS NOT NULL")}
    async with _lan_client() as c:
        for sec in await _sections(c, u["server_token"]):
            keys.append(sec["key"])
            for it in await _section_items(c, u["server_token"], sec):
                rk = str(it["ratingKey"])
                lid = local_ids.get(rk)
                if sec["type"] == "movie":
                    if (it.get("viewCount") or 0) > 0:
                        progress.append((user_id, rk, 1, 1))
                        if lid:
                            seen.append(lid)
                elif (it.get("viewedLeafCount") or 0) > 0:
                    progress.append((user_id, rk, it.get("viewedLeafCount"), it.get("leafCount")))
                    if lid and it.get("leafCount") and it["viewedLeafCount"] >= it["leafCount"]:
                        seen.append(lid)
    with conn:
        conn.execute("DELETE FROM user_plex_watched WHERE user_id=?", (user_id,))
        conn.executemany("INSERT OR REPLACE INTO user_plex_watched(user_id,rating_key,viewed,total) VALUES(?,?,?,?)",
                         progress)
        conn.execute("DELETE FROM user_sections WHERE user_id=?", (user_id,))
        conn.executemany("INSERT INTO user_sections(user_id,section_key) VALUES(?,?)", [(user_id, k) for k in keys])
        conn.execute("DELETE FROM watched WHERE user_id=? AND source='plex'", (user_id,))
        conn.executemany("INSERT OR IGNORE INTO watched(user_id,movie_id,source) VALUES(?,?,'plex')",
                         [(user_id, t) for t in set(seen)])
    log.info("Användare %s: %d bibliotek, %d sedda filmer", user_id, len(keys), len(set(seen)))
    return len(seen)


async def item_details(rating_key, token):
    """Detaljer om en film/serie från Plex-servern, med användarens egen token (sett-status per person)."""
    async with _lan_client() as c:
        r = await c.get(f"{config.PLEX_URL}/library/metadata/{rating_key}", headers=_headers(token))
        if r.status_code in (401, 403, 404):
            raise PlexError("Du har inte tillgång till den här titeln.")
        r.raise_for_status()
        meta = (r.json()["MediaContainer"].get("Metadata") or [None])[0]
        if not meta:
            raise PlexError("Titeln finns inte längre i Plex.")
        seasons = []
        if meta.get("type") == "show":
            r = await c.get(f"{config.PLEX_URL}/library/metadata/{rating_key}/children", headers=_headers(token))
            if r.status_code == 200:
                seasons = r.json()["MediaContainer"].get("Metadata", []) or []
    return meta, seasons


async def thumb(path, width=300, height=450):
    """Hämtar en (förminskad) bild från Plex-servern."""
    async with _lan_client() as c:
        r = await c.get(f"{config.PLEX_URL}/photo/:/transcode",
                        params={"width": width, "height": height, "minSize": 1, "upscale": 1, "url": path},
                        headers=_headers(config.PLEX_TOKEN))
        if r.status_code != 200:
            r = await c.get(f"{config.PLEX_URL}{path}", headers=_headers(config.PLEX_TOKEN))
        return r.status_code, r.headers.get("content-type", "image/jpeg"), r.content


async def sync_all():
    try:
        await sync_requests()
    except Exception as e:  # noqa: BLE001
        log.warning("Seerr-synk misslyckades: %s", e)
    try:
        await sync_library()
    except Exception as e:  # noqa: BLE001
        log.warning("Plex-synk av bibliotek misslyckades: %s", e)
    for (uid,) in db.connect().execute("SELECT id FROM users WHERE server_token IS NOT NULL").fetchall():
        try:
            await sync_watched(uid)
        except Exception as e:  # noqa: BLE001
            log.warning("Plex-synk av sett-historik för %s misslyckades: %s", uid, e)


async def sync_loop():
    await asyncio.sleep(5)
    while True:
        await sync_all()
        await asyncio.sleep(max(5, config.PLEX_SYNC_MINUTES) * 60)


# ---------- Önskningar ----------

# ---------- Seerr (seerr.dev, även Overseerr/Jellyseerr) ----------

SEERR_REQUEST_STATUS = {1: "pending", 2: "sent", 3: "declined", 4: "failed", 5: "available"}
SEERR_MEDIA_AVAILABLE = (4, 5)   # 4 = delvis tillgänglig, 5 = tillgänglig
_seerr_users = {"at": 0, "map": {}}


def _seerr_headers(seerr_user=None):
    h = {"X-Api-Key": config.SEERR_API_KEY, "Accept": "application/json"}
    if seerr_user:
        h["X-Api-User"] = str(seerr_user)   # önskan registreras på rätt person i Seerr
    return h


async def seerr_user_id(c, plex_id):
    """Seerr-användarens id för ett Plex-konto (matchas via plexId). Cachas i 10 minuter."""
    if not plex_id:
        return None
    if time.time() - _seerr_users["at"] > 600:
        mapping, skip = {}, 0
        while True:
            r = await c.get(f"{config.SEERR_URL}/api/v1/user", params={"take": 100, "skip": skip},
                            headers=_seerr_headers())
            if r.status_code != 200:
                break
            d = r.json()
            for u in d.get("results", []):
                if u.get("plexId"):
                    mapping[int(u["plexId"])] = u["id"]
            skip += 100
            if skip >= (d.get("pageInfo") or {}).get("results", 0):
                break
        _seerr_users.update(at=time.time(), map=mapping)
    return _seerr_users["map"].get(int(plex_id))


def _seerr_detail(status):
    return {"pending": "Väntar på godkännande i Seerr", "sent": "Godkänd i Seerr – laddas ner",
            "declined": "Nekad i Seerr", "available": "Tillgänglig", "failed": "Misslyckades i Seerr"}.get(status, "")


async def seerr_status(c, media_type, tmdb_id, seerr_id=None, plex_id=None):
    """Läser status för en titel i Seerr. Returnerar (status, seerr_request_id) eller (None, None)."""
    r = await c.get(f"{config.SEERR_URL}/api/v1/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}",
                    headers=_seerr_headers())
    if r.status_code != 200:
        return None, None
    info = r.json().get("mediaInfo") or {}
    if info.get("status") in SEERR_MEDIA_AVAILABLE:
        return "available", seerr_id
    reqs = info.get("requests") or []
    mine = next((q for q in reqs if seerr_id and q.get("id") == seerr_id), None) \
        or next((q for q in reqs if plex_id and (q.get("requestedBy") or {}).get("plexId") == plex_id), None) \
        or (reqs[0] if reqs else None)
    if not mine:
        return None, None
    return SEERR_REQUEST_STATUS.get(mine.get("status"), "pending"), mine.get("id")


async def sync_requests(rows=None):
    """Uppdaterar önskningarnas status från Seerr."""
    if config.request_backend() != "seerr":
        return 0
    conn = db.connect()
    if rows is None:
        rows = conn.execute("""SELECT r.id, r.movie_id, r.status, r.seerr_id, r.media_type, u.plex_id
            FROM requests r JOIN users u ON u.id=r.user_id WHERE r.status NOT IN ('available')""").fetchall()
    changed = 0
    async with _lan_client() as c:
        for r in rows:
            is_tv = (r["media_type"] or "movie") == "tv" or r["movie_id"] > db.TV_OFFSET
            tmdb_id = r["movie_id"] - db.TV_OFFSET if r["movie_id"] > db.TV_OFFSET else r["movie_id"]
            try:
                status, sid = await seerr_status(c, "tv" if is_tv else "movie", tmdb_id, r["seerr_id"], r["plex_id"])
            except httpx.HTTPError:
                continue
            if status and (status != r["status"] or sid != r["seerr_id"]):
                conn.execute("UPDATE requests SET status=?, detail=?, seerr_id=?, updated_at=CURRENT_TIMESTAMP "
                             "WHERE id=?", (status, _seerr_detail(status), sid, r["id"]))
                changed += 1
    conn.commit()
    if changed:
        log.info("Seerr: %d önskningar fick ny status", changed)
    return changed


async def send_request(movie_row, user_row=None):
    """Skickar en önskan till Seerr (eller Radarr). Returnerar (status, detalj, seerr_id)."""
    backend = config.request_backend()
    is_tv = (movie_row["media_type"] or "movie") == "tv"
    tmdb_id = movie_row["id"] - db.TV_OFFSET if is_tv else movie_row["id"]
    async with _lan_client() as c:
        if backend == "seerr":
            plex_id = user_row["plex_id"] if user_row else None
            try:
                su = await seerr_user_id(c, plex_id)
            except httpx.HTTPError:
                su = None
            body = {"mediaType": "tv", "mediaId": tmdb_id, "seasons": "all"} if is_tv \
                else {"mediaType": "movie", "mediaId": tmdb_id}
            r = await c.post(f"{config.SEERR_URL}/api/v1/request", json=body, headers=_seerr_headers(su))
            if r.status_code in (200, 201):
                d = r.json()
                st = SEERR_REQUEST_STATUS.get(d.get("status"), "pending")
                who = "" if su else " (som admin – Plex-kontot finns inte i Seerr)"
                return st, _seerr_detail(st) + who, d.get("id")
            if r.status_code == 409:
                st, sid = await seerr_status(c, "tv" if is_tv else "movie", tmdb_id, None, plex_id)
                st = st or "pending"
                d = _seerr_detail(st)
                return st, "Redan önskad i Seerr – " + (d[:1].lower() + d[1:]), sid
            if r.status_code == 403 and su:
                return "failed", "Seerr nekade: ditt Seerr-konto saknar rätt att önska " + ("serier" if is_tv else "filmer"), None
            return "failed", f"Seerr svarade {r.status_code}: {r.text[:200]}", None
        if backend == "radarr" and is_tv:
            return "pending", "Radarr hanterar inte serier – sparad i önskelistan", None
        if backend == "radarr":
            h = {"X-Api-Key": config.RADARR_API_KEY}
            root = config.RADARR_ROOT_FOLDER
            if not root:
                rf = (await c.get(f"{config.RADARR_URL}/api/v3/rootfolder", headers=h)).json()
                root = rf[0]["path"] if rf else None
            qp = config.RADARR_QUALITY_PROFILE
            profiles = (await c.get(f"{config.RADARR_URL}/api/v3/qualityprofile", headers=h)).json()
            qid = next((p["id"] for p in profiles if str(p["id"]) == qp or p["name"] == qp), None) \
                or (profiles[0]["id"] if profiles else 1)
            body = {"tmdbId": movie_row["id"], "title": movie_row["title"], "year": movie_row["year"] or 0,
                    "qualityProfileId": qid, "rootFolderPath": root, "monitored": True,
                    "minimumAvailability": "released", "addOptions": {"searchForMovie": True}}
            r = await c.post(f"{config.RADARR_URL}/api/v3/movie", json=body, headers=h)
            if r.status_code in (200, 201):
                return "sent", "Tillagd i Radarr", None
            if r.status_code == 400 and "already" in r.text.lower():
                return "sent", "Finns redan i Radarr", None
            return "failed", f"Radarr svarade {r.status_code}: {r.text[:200]}", None
    return "pending", "Sparad i önskelistan", None
