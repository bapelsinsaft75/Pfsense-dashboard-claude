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

async def _movie_sections(c, token):
    r = await c.get(f"{config.PLEX_URL}/library/sections", headers=_headers(token))
    r.raise_for_status()
    return [d["key"] for d in r.json()["MediaContainer"].get("Directory", []) if d.get("type") == "movie"]


def _tmdb_id(item):
    for g in item.get("Guid", []) or []:
        gid = g.get("id", "")
        if gid.startswith("tmdb://"):
            try:
                return int(gid[7:])
            except ValueError:
                return None
    return None


async def _library_items(c, token):
    items = []
    for key in await _movie_sections(c, token):
        r = await c.get(f"{config.PLEX_URL}/library/sections/{key}/all",
                        params={"type": "1", "includeGuids": "1"}, headers=_headers(token), timeout=120)
        r.raise_for_status()
        items += r.json()["MediaContainer"].get("Metadata", []) or []
    return items


async def sync_library():
    if not (config.PLEX_URL and config.PLEX_TOKEN):
        return 0
    await server_identity()
    async with _lan_client() as c:
        items = await _library_items(c, config.PLEX_TOKEN)
    rows = []
    for it in items:
        tid = _tmdb_id(it)
        if tid:
            rows.append((tid, str(it["ratingKey"]), it.get("title"), it.get("year"), it.get("addedAt")))
    conn = db.connect()
    conn.execute("DELETE FROM plex_library")
    conn.executemany("INSERT OR REPLACE INTO plex_library(tmdb_id,rating_key,title,year,added_at) VALUES(?,?,?,?,?)",
                     rows)
    # Önskningar som nu finns i Plex markeras som tillgängliga
    conn.execute("UPDATE requests SET status='available' WHERE movie_id IN (SELECT tmdb_id FROM plex_library)")
    conn.commit()
    db.set_meta("plex_last_sync", int(time.time()))
    log.info("Plex-bibliotek synkat: %d filmer med TMDB-id", len(rows))
    return len(rows)


async def sync_watched(user_id):
    """Markerar filmer som användaren sett i Plex som sedda."""
    if not config.PLEX_URL:
        return 0
    conn = db.connect()
    u = conn.execute("SELECT server_token FROM users WHERE id=?", (user_id,)).fetchone()
    if not u or not u["server_token"]:
        return 0
    async with _lan_client() as c:
        items = await _library_items(c, u["server_token"])
    seen = [tid for tid in (_tmdb_id(i) for i in items if (i.get("viewCount") or 0) > 0) if tid]
    conn.execute("DELETE FROM watched WHERE user_id=? AND source='plex'", (user_id,))
    conn.executemany("INSERT OR IGNORE INTO watched(user_id,movie_id,source) VALUES(?,?,'plex')",
                     [(user_id, t) for t in seen])
    conn.commit()
    log.info("Sett-historik för användare %s: %d filmer", user_id, len(seen))
    return len(seen)


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
