"""Plex: inloggning (PIN-flöde), synk av bibliotek och sett-historik, samt filmönskningar."""
import asyncio
import logging
import os
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


def _tmdb_id(item):
    for g in item.get("Guid", []) or []:
        gid = g.get("id", "")
        if gid.startswith("tmdb://"):
            try:
                return int(gid[7:])
            except ValueError:
                return None
    return None


async def _section_items(c, token, section):
    plex_type = "1" if section["type"] == "movie" else "2"
    r = await c.get(f"{config.PLEX_URL}/library/sections/{section['key']}/all",
                    params={"type": plex_type, "includeGuids": "1"}, headers=_headers(token), timeout=180)
    r.raise_for_status()
    return r.json()["MediaContainer"].get("Metadata", []) or []


async def sync_library():
    """Hämtar alla film- och seriebibliotek med innehåll (med admin-token)."""
    if not (config.PLEX_URL and config.PLEX_TOKEN):
        return 0
    await server_identity()
    sections, items = [], []
    async with _lan_client() as c:
        for sec in await _sections(c, config.PLEX_TOKEN):
            its = await _section_items(c, config.PLEX_TOKEN, sec)
            sections.append((sec["key"], sec["title"], sec["type"], len(its)))
            for it in its:
                items.append((sec["key"], str(it["ratingKey"]), sec["type"], it.get("title") or "?",
                               it.get("year"), it.get("thumb"), it.get("art"), _tmdb_id(it), it.get("addedAt"),
                               it.get("leafCount"), it.get("childCount"), (it.get("summary") or "")[:500]))
    movie_rows = {}
    for (skey, rk, typ, title, year, thumb, art, tid, added, *_rest) in items:
        if typ == "movie" and tid and tid not in movie_rows:
            movie_rows[tid] = (tid, rk, title, year, added)
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM plex_sections")
        conn.executemany("INSERT INTO plex_sections(key,title,type,item_count) VALUES(?,?,?,?)", sections)
        conn.execute("DELETE FROM plex_items")
        conn.executemany("""INSERT OR REPLACE INTO plex_items(section_key,rating_key,type,title,year,thumb,art,tmdb_id,
            added_at,episodes,seasons,summary) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", items)
        conn.execute("DELETE FROM plex_library")
        conn.executemany("INSERT INTO plex_library(tmdb_id,rating_key,title,year,added_at) VALUES(?,?,?,?,?)",
                         list(movie_rows.values()))
        # Önskningar som nu finns i Plex markeras som tillgängliga
        conn.execute("UPDATE requests SET status='available' WHERE movie_id IN (SELECT tmdb_id FROM plex_library)")
    db.set_meta("plex_last_sync", int(time.time()))
    log.info("Plex synkat: %d bibliotek, %d objekt, %d filmer med TMDB-id",
             len(sections), len(items), len(movie_rows))
    return len(movie_rows)


async def sync_watched(user_id):
    """Sparar vilka bibliotek användaren ser och markerar filmer som setts i Plex som sedda."""
    if not config.PLEX_URL:
        return 0
    conn = db.connect()
    u = conn.execute("SELECT server_token FROM users WHERE id=?", (user_id,)).fetchone()
    if not u or not u["server_token"]:
        return 0
    seen, keys, progress = [], [], []
    async with _lan_client() as c:
        for sec in await _sections(c, u["server_token"]):
            keys.append(sec["key"])
            for it in await _section_items(c, u["server_token"], sec):
                rk = str(it["ratingKey"])
                if sec["type"] == "movie":
                    if (it.get("viewCount") or 0) > 0:
                        progress.append((user_id, rk, 1, 1))
                        tid = _tmdb_id(it)
                        if tid:
                            seen.append(tid)
                elif (it.get("viewedLeafCount") or 0) > 0:
                    progress.append((user_id, rk, it.get("viewedLeafCount"), it.get("leafCount")))
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

async def send_request(movie_row):
    """Skickar en önskan vidare till Overseerr/Jellyseerr eller Radarr. Returnerar (status, detalj)."""
    backend = config.request_backend()
    async with _lan_client() as c:
        if backend == "overseerr":
            r = await c.post(f"{config.OVERSEERR_URL}/api/v1/request",
                             json={"mediaType": "movie", "mediaId": movie_row["id"]},
                             headers={"X-Api-Key": config.OVERSEERR_API_KEY})
            if r.status_code in (200, 201):
                return "sent", "Skickad till Overseerr"
            if r.status_code == 409:
                return "sent", "Redan önskad i Overseerr"
            return "failed", f"Overseerr svarade {r.status_code}: {r.text[:200]}"
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
                return "sent", "Tillagd i Radarr"
            if r.status_code == 400 and "already" in r.text.lower():
                return "sent", "Finns redan i Radarr"
            return "failed", f"Radarr svarade {r.status_code}: {r.text[:200]}"
    return "pending", "Sparad i önskelistan"
