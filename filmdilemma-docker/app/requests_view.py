"""Requests-sidan: önskningar med status steg för steg (Seerr -> nedladdning -> Plex).

Med Seerr konfigurerat hämtas önskningarna direkt från Seerr, så även sådant som önskats inne i Seerr syns.
Vanliga användare ser bara sina egna önskningar. Admin (Plex-serverns ägare) ser allas.
"""
import time

import httpx

from . import config, db, plex, tmdb_import
from .serializers import poster_url

_cache = {}          # (user_id, show_all) -> (tid, data)
_titles = {}         # (media, tmdb_id) -> {title, year, poster_path}

SEERR_REQ = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}


def _local_id(media, tmdb_id):
    return db.TV_OFFSET + tmdb_id if media == "tv" else tmdb_id


async def _title(media, tmdb_id):
    """Titel och affisch: från databasen, annars direkt från TMDB (cachas)."""
    row = db.connect().execute("SELECT title, year, poster_path FROM movies WHERE id=?",
                               (_local_id(media, tmdb_id),)).fetchone()
    if row:
        return {"title": row["title"], "year": row["year"], "poster_path": row["poster_path"], "in_db": True}
    key = (media, tmdb_id)
    if key in _titles:
        return _titles[key]
    out = {"title": f"TMDB {tmdb_id}", "year": None, "poster_path": None, "in_db": False}
    if tmdb_import.configured():
        t = tmdb_import.Tmdb()
        try:
            d = await t.get(f"/{media}/{tmdb_id}", language=tmdb_import.LANGUAGE)
            if d:
                date = d.get("release_date") or d.get("first_air_date") or ""
                out = {"title": d.get("title") or d.get("name") or out["title"],
                       "year": int(date[:4]) if date[:4].isdigit() else None,
                       "poster_path": d.get("poster_path"), "in_db": False}
        finally:
            await t.close()
    _titles[key] = out
    return out


def _stages(req_status, media_status, downloads, in_plex):
    """Bygger stegen. state: done / current / todo / failed."""
    st = []
    st.append({"key": "sent", "label": "Mottagen av Seerr" if config.REQUEST_MODE == "watchlist" else "Skickad till Seerr",
               "state": "done"})

    if req_status == "declined":
        st.append({"key": "approved", "label": "Nekad", "state": "failed"})
        return st, "Nekad i Seerr", None
    approved = req_status in ("approved", "completed") or media_status in (3, 4, 5)
    st.append({"key": "approved", "label": "Godkänd" if approved else "Väntar på godkännande",
               "state": "done" if approved else "current"})

    pct = None
    if downloads:
        size = sum(d.get("size") or 0 for d in downloads)
        left = sum(d.get("sizeLeft") or 0 for d in downloads)
        pct = round(100 * (size - left) / size) if size else None
    available = media_status == 5
    partly = media_status == 4
    if available or partly:
        dl = {"key": "download", "label": "Nedladdad", "state": "done"}
    elif req_status == "failed":
        dl = {"key": "download", "label": "Nedladdningen misslyckades", "state": "failed"}
    elif approved and downloads:
        dl = {"key": "download", "label": f"Laddas ner {pct}%" if pct is not None else "Laddas ner", "state": "current"}
    elif approved:
        dl = {"key": "download", "label": "Söker efter filmen" , "state": "current"}
    else:
        dl = {"key": "download", "label": "Laddas ner", "state": "todo"}
    st.append(dl)

    st.append({"key": "seerr", "label": "Delvis tillgänglig" if partly else "Tillgänglig i Seerr",
               "state": "done" if available else ("current" if partly else "todo")})
    st.append({"key": "plex", "label": "Uppe i Plex",
               "state": "done" if in_plex else ("current" if available or partly else "todo")})

    if in_plex:
        summary = "Uppe i Plex"
    elif available:
        summary = "Tillgänglig – väntar på Plex-synk"
    elif dl["state"] == "current":
        summary = dl["label"]
    elif not approved:
        summary = "Väntar på godkännande"
    else:
        summary = dl["label"]
    return st, summary, pct


async def _seerr_list(user_row, show_all):
    """Önskningar i Seerr: den inloggades egna, eller allas (bara admin)."""
    params = {"take": 100, "skip": 0, "sort": "added", "filter": "all"}
    async with plex._lan_client() as c:
        sid = await plex.seerr_user_id(c, user_row)
        if not show_all:
            if not sid:
                return None, "Ditt Plex-konto finns inte i Seerr."
            params["requestedBy"] = sid
        r = await c.get(f"{config.SEERR_URL}/api/v1/request", params=params, headers=plex._seerr_headers())
        r.raise_for_status()
        results = r.json().get("results", [])
    if not show_all:
        # Spärr ifall Seerr skulle ignorera filtret: visa aldrig någon annans önskningar
        results = [q for q in results if (q.get("requestedBy") or {}).get("id") == sid]
    for q in results:
        q["_mine"] = bool(sid) and (q.get("requestedBy") or {}).get("id") == sid
    return results, None


async def build(user_row, scope="mine"):
    # Bara admin får se allas önskningar
    show_all = bool(user_row["is_admin"]) and scope == "all"
    key = (user_row["id"], show_all)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 15:
        return hit[1]

    conn = db.connect()
    plex_ids = {r[0] for r in conn.execute("SELECT tmdb_id FROM plex_library")}
    items, note = [], None

    if config.request_backend() == "seerr":
        try:
            results, note = await _seerr_list(user_row, show_all)
        except (httpx.HTTPError, ValueError) as ex:
            results, note = None, f"Kunde inte nå Seerr: {ex}"
        for q in results or []:
            media = q.get("media") or {}
            mtype = "tv" if (q.get("type") or media.get("mediaType")) == "tv" else "movie"
            tmdb_id = media.get("tmdbId")
            if not tmdb_id:
                continue
            info = await _title(mtype, tmdb_id)
            lid = _local_id(mtype, tmdb_id)
            stages, summary, pct = _stages(SEERR_REQ.get(q.get("status"), "pending"), media.get("status"),
                                           media.get("downloadStatus") or [], lid in plex_ids)
            who = q.get("requestedBy") or {}
            items.append({
                "id": f"seerr-{q.get('id')}", "source": "seerr", "media_type": mtype,
                "title": info["title"], "year": info["year"],
                "poster_url": poster_url(info["poster_path"], "w185"),
                "url": f"/movies/{lid}" if info["in_db"] else None,
                "seerr_url": f"{config.SEERR_URL}/{mtype}/{tmdb_id}",
                "requested_by": who.get("displayName") or who.get("plexUsername") or who.get("email"),
                "created_at": q.get("createdAt"), "stages": stages, "summary": summary, "progress": pct,
                "seasons": len(q.get("seasons") or []) or None, "mine": q.get("_mine", False),
            })
        # Lokala önskningar som Seerr inte (ännu) har: misslyckade och sådana som ligger på Plex-watchlist
        local_where = "r.seerr_id IS NULL AND r.status IN ('failed','watchlist')"
    else:
        local_where = "1=1"
    seerr_keys = {(i["media_type"], i["title"]) for i in items}
    seerr_lids = {int(i["url"].rsplit("/", 1)[1]) for i in items if i.get("url")}

    where = local_where + ("" if show_all else f" AND r.user_id={int(user_row['id'])}")
    for r in conn.execute(f"""SELECT r.*, u.username, m.title, m.year, m.poster_path, m.media_type mt FROM requests r
            JOIN users u ON u.id=r.user_id JOIN movies m ON m.id=r.movie_id WHERE {where}
            ORDER BY r.created_at DESC""").fetchall():
        in_plex = r["movie_id"] in plex_ids
        if r["movie_id"] in seerr_lids or ((r["mt"] or "movie"), r["title"]) in seerr_keys:
            continue   # Seerr har redan önskan – visas via Seerr ovan
        if r["status"] == "watchlist":
            has_seerr = config.request_backend() == "seerr"
            stages = [{"key": "watchlist", "label": "På din Plex-watchlist", "state": "done"}]
            if has_seerr:
                stages += [{"key": "seerr", "label": "Väntar på Seerr", "state": "current"},
                           {"key": "download", "label": "Laddas ner", "state": "todo"}]
            stages.append({"key": "plex", "label": "Uppe i Plex", "state": "done" if in_plex else "todo"})
            summary = "Uppe i Plex" if in_plex else ("Lagd på Plex-watchlist – Seerr önskar den inom några minuter"
                                                     if has_seerr else "Lagd på din Plex-watchlist")
        elif r["status"] == "failed":
            stages = [{"key": "sent", "label": "Kunde inte skickas", "state": "failed"}]
            summary = r["detail"] or "Misslyckades"
        else:
            stages = [{"key": "sent", "label": "Sparad", "state": "done"},
                      {"key": "plex", "label": "Uppe i Plex", "state": "done" if in_plex else "todo"}]
            summary = "Uppe i Plex" if in_plex else (r["detail"] or "Väntar")
        items.append({
            "id": f"local-{r['id']}", "local_id": r["id"], "source": "local", "media_type": r["mt"] or "movie",
            "title": r["title"], "year": r["year"], "poster_url": poster_url(r["poster_path"], "w185"),
            "url": f"/movies/{r['movie_id']}", "seerr_url": None, "requested_by": r["username"],
            "created_at": r["created_at"], "stages": stages, "summary": summary, "progress": None,
            "mine": r["user_id"] == user_row["id"],
        })
    items.sort(key=lambda i: str(i.get("created_at") or ""), reverse=True)
    data = {"items": items, "note": note, "backend": config.request_backend(),
            "is_admin": bool(user_row["is_admin"]), "scope": "all" if show_all else "mine"}
    _cache[key] = (time.time(), data)
    return data


def invalidate():
    _cache.clear()
