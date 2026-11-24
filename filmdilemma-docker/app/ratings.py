"""Betyg från TMDB och IMDb för Plex-titlar.

TMDB: från den lokala databasen, annars direkt från TMDB:s API.
IMDb: IMDb har inget öppet API och tillåter inte skrapning av imdb.com. Betyget tas därför från
Plex (om biblioteket använder IMDb-betyg) och annars från OMDb (omdbapi.com) om OMDB_API_KEY är satt.
"""
import time

import httpx

from . import config, db, tmdb_import

_cache = {}
TTL = 6 * 3600


def _cached(key):
    v = _cache.get(key)
    if v and time.time() - v[0] < TTL:
        return v[1]
    return None


async def tmdb_details(kind, tmdb_id):
    """kind = 'movie' eller 'tv'. Returnerar {rating, votes, imdb_id, url} eller None."""
    if not tmdb_id or not tmdb_import.configured():
        return None
    key = ("tmdb", kind, tmdb_id)
    hit = _cached(key)
    if hit is not None:
        return hit
    headers = {"Accept": "application/json"}
    params = {"append_to_response": "external_ids"}
    if tmdb_import.TOKEN:
        headers["Authorization"] = f"Bearer {tmdb_import.TOKEN}"
    else:
        params["api_key"] = tmdb_import.API_KEY
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{tmdb_import.API}/{kind}/{tmdb_id}", params=params, headers=headers)
        if r.status_code != 200:
            return None
        d = r.json()
    except httpx.HTTPError:
        return None
    imdb_id = d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id")
    out = {"rating": round(d["vote_average"], 1) if d.get("vote_average") else None,
           "votes": d.get("vote_count"), "imdb_id": imdb_id,
           "url": f"https://www.themoviedb.org/{kind}/{tmdb_id}"}
    _cache[key] = (time.time(), out)
    return out


async def omdb_imdb(imdb_id):
    if not imdb_id or not config.OMDB_API_KEY:
        return None
    key = ("omdb", imdb_id)
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://www.omdbapi.com/", params={"i": imdb_id, "apikey": config.OMDB_API_KEY})
        d = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        d = {}
    rating = d.get("imdbRating")
    out = {"rating": float(rating) if rating and rating != "N/A" else None,
           "votes": d.get("imdbVotes") if d.get("imdbVotes") != "N/A" else None}
    _cache[key] = (time.time(), out)
    return out


def from_plex(meta):
    """Plock ut betyg som Plex själv har (Rating-listan i nya Plex-agenten)."""
    out = {}
    for r in meta.get("Rating", []) or []:
        img, val = r.get("image", ""), r.get("value")
        if val is None:
            continue
        if img.startswith("imdb://"):
            out["imdb"] = round(float(val), 1)
        elif img.startswith("themoviedb://"):
            out["tmdb"] = round(float(val), 1)
        elif img.startswith("rottentomatoes://") and r.get("type") == "critic":
            out["rt_critic"] = int(float(val) * 10)
        elif img.startswith("rottentomatoes://") and r.get("type") == "audience":
            out["rt_audience"] = int(float(val) * 10)
    if "imdb" not in out and str(meta.get("audienceRatingImage", "")).startswith("imdb://"):
        out["imdb"] = meta.get("audienceRating")
    if "imdb" not in out and str(meta.get("ratingImage", "")).startswith("imdb://"):
        out["imdb"] = meta.get("rating")
    return out


def imdb_id_from_guids(meta):
    for g in meta.get("Guid", []) or []:
        if str(g.get("id", "")).startswith("imdb://"):
            return g["id"][7:]
    return None


async def collect(meta, tmdb_id, movie_row):
    """Samlar TMDB- och IMDb-betyg för en Plex-titel."""
    kind = "tv" if meta.get("type") == "show" else "movie"
    plex_r = from_plex(meta)
    imdb_id = imdb_id_from_guids(meta) or (movie_row["imdb_id"] if movie_row else None)
    tmdb = None
    if movie_row and movie_row["vote_average"]:
        tmdb = {"rating": round(movie_row["vote_average"], 1), "votes": movie_row["vote_count"],
                "url": f"https://www.themoviedb.org/movie/{movie_row['id']}"}
    elif tmdb_id:
        tmdb = await tmdb_details(kind, tmdb_id)
    if tmdb and not imdb_id:
        imdb_id = tmdb.get("imdb_id")
    if not tmdb and plex_r.get("tmdb"):
        tmdb = {"rating": plex_r["tmdb"], "votes": None,
                "url": f"https://www.themoviedb.org/{kind}/{tmdb_id}" if tmdb_id else None}

    imdb = {"rating": plex_r.get("imdb"), "votes": None, "source": "plex"} if plex_r.get("imdb") else None
    if not imdb and imdb_id:
        o = await omdb_imdb(imdb_id)
        if o and o.get("rating"):
            imdb = {"rating": o["rating"], "votes": o.get("votes"), "source": "omdb"}
    if imdb_id:
        imdb = dict(imdb or {"rating": None, "votes": None, "source": None},
                    url=f"https://www.imdb.com/title/{imdb_id}/", id=imdb_id)
    return {
        "tmdb": {k: v for k, v in (tmdb or {}).items() if k != "imdb_id"} or None,
        "imdb": imdb,
        "rotten_tomatoes": {"critic": plex_r.get("rt_critic"), "audience": plex_r.get("rt_audience")}
        if ("rt_critic" in plex_r or "rt_audience" in plex_r) else None,
    }
