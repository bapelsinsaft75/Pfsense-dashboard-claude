"""Fristående backend för Film Dilemma-kopian.

Serverar frontenden (web/) och ett eget API som ersätter api.filmdilemma.com.
Data kommer från TMDB (se tmdb_import.py) och Plex (se plex.py).
"""
import asyncio
import contextlib
import json
import logging
import os
import random
import re
import shutil
import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import auth, config, db, plex, ratings, tmdb_import
from .search import search
from .serializers import (UserState, list_summary, movie_card, movie_detail, person, poster_url, profile_url)

log = logging.getLogger("app")
HERE = os.path.dirname(os.path.abspath(__file__))
WEB_SRC = os.environ.get("WEB_DIR", os.path.join(os.path.dirname(HERE), "web"))
WEB = os.environ.get("WEB_RUNTIME_DIR", "/tmp/fd-web")
LOCAL = os.path.join(HERE, "local")

ROUTES = []


def route(path, methods=("GET",)):
    def deco(fn):
        ROUTES.append(Route(path, fn, methods=list(methods)))
        return fn
    return deco


def ok(data=None, status=200):
    return JSONResponse({"ok": True} if data is None else data, status_code=status)


def err(msg, status=400, code=None):
    return JSONResponse({"detail": msg, "code": code}, status_code=status)


def current_user(request):
    return auth.user_from_request(request)


def uid(request):
    u = current_user(request)
    return u["id"] if u else None


def require_user(request):
    u = current_user(request)
    if not u:
        return None, err("Authentication required.", 401)
    return u, None


async def body_json(request):
    with contextlib.suppress(Exception):
        return await request.json()
    return {}


def merged_params(request, body=None):
    """Query-parametrar + JSON-kropp i ett objekt med getlist()."""
    class P(dict):
        def __init__(self, qp, extra):
            super().__init__()
            self._multi = {}
            for k in qp.keys():
                self._multi[k] = qp.getlist(k)
                self[k] = qp.get(k)
            for k, v in (extra or {}).items():
                self._multi[k] = v if isinstance(v, list) else [v]
                self[k] = v
        def getlist(self, k):
            return self._multi.get(k, [])
    return P(request.query_params, body if isinstance(body, dict) else {})


# ------------------------------------------------------------------ frontend

def prepare_web():
    """Kopierar frontenden och byter ut adresser så att inget går mot filmdilemma.com."""
    if os.path.isdir(WEB):
        shutil.rmtree(WEB)
    shutil.copytree(WEB_SRC, WEB)
    pub = config.PUBLIC_URL
    for root, _, files in os.walk(WEB):
        for f in files:
            if not f.endswith((".js", ".html", ".json", ".xml", ".txt")):
                continue
            p = os.path.join(root, f)
            with open(p, encoding="utf-8", errors="surrogateescape") as fh:
                s = fh.read()
            n = s.replace("https://api.filmdilemma.com", "").replace("https://filmdilemma.com", pub)
            if f == "index.html" and root == WEB:
                n = n.replace("</head>", '<link rel="stylesheet" href="/local/fd-local.css">'
                                         '<script defer src="/local/fd-local.js"></script></head>')
            if n != s:
                with open(p, "w", encoding="utf-8", errors="surrogateescape") as fh:
                    fh.write(n)


async def spa(request: Request):
    path = request.url.path
    if path.startswith("/api/"):
        return await api_fallback(request)
    f = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
    if f.startswith(WEB) and os.path.isfile(f):
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/static/" in path or "/fonts/" in path \
            else {"Cache-Control": "no-cache"}
        return FileResponse(f, headers=headers)
    if re.search(r"\.(js|css|png|jpg|svg|ico|woff2?|map|json)$", path):
        return Response(status_code=404)
    return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-cache"})


@route("/local/{name:path}")
async def local_static(request):
    name = request.path_params["name"]
    f = os.path.normpath(os.path.join(LOCAL, name))
    if f.startswith(LOCAL) and os.path.isfile(f):
        return FileResponse(f, headers={"Cache-Control": "no-cache"})
    return Response(status_code=404)


@route("/plex-login")
async def plex_login_page(request):
    return FileResponse(os.path.join(LOCAL, "plex-login.html"), headers={"Cache-Control": "no-cache"})


@route("/onskningar")
async def requests_page(request):
    return FileResponse(os.path.join(LOCAL, "requests.html"), headers={"Cache-Control": "no-cache"})


@route("/__fd/pass")
async def fd_pass(request):
    return ok({"pass": "local", "ttl": 3600})


@route("/api/v1/pass-trade", methods=("POST",))
async def pass_trade(request):
    return ok({"pass": "local", "ttl": 3600})


# ------------------------------------------------------------------ auth (bara Plex)

@route("/api/v1/auth/me", methods=("GET",))
async def auth_me(request):
    u = current_user(request)
    if not u:
        return err("Not authenticated", 401)
    return ok(auth.user_json(u))


@route("/api/v1/auth/me", methods=("PUT", "PATCH"))
async def auth_me_update(request):
    u, e = require_user(request)
    return e or ok(auth.user_json(u))


@route("/api/v1/auth/refresh", methods=("POST",))
async def auth_refresh(request):
    b = await body_json(request)
    t = auth.refresh(b.get("refresh_token", ""))
    if not t:
        return err("Invalid refresh token", 401)
    return ok(t)


@route("/api/v1/auth/logout", methods=("POST",))
async def auth_logout(request):
    b = await body_json(request)
    auth.revoke(b.get("refresh_token", ""))
    return ok()


@route("/api/v1/auth/login", methods=("POST",))
@route("/api/v1/auth/register", methods=("POST",))
async def auth_login_disabled(request):
    return err("Logga in med Plex på /plex-login", 400)


@route("/api/local/plex/pin", methods=("POST",))
async def plex_pin(request):
    try:
        return ok(await plex.create_pin())
    except Exception as e:  # noqa: BLE001
        log.warning("Plex PIN: %s", e)
        return err("Kunde inte nå plex.tv.", 502)


@route("/api/local/plex/pin/{pin_id:int}", methods=("GET",))
async def plex_pin_check(request):
    try:
        token = await plex.check_pin(request.path_params["pin_id"])
        if not token:
            return ok({"status": "waiting"})
        user_id = await plex.login_with_token(token)
    except plex.PlexError as e:
        return err(str(e), 403)
    except Exception as e:  # noqa: BLE001
        log.warning("Plex-inloggning: %s", e)
        return err("Något gick fel vid Plex-inloggningen.", 502)
    asyncio.create_task(_safe(plex.sync_watched(user_id)))
    tokens = auth.issue_tokens(user_id)
    user = db.connect().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return ok({"status": "ok", **tokens, "user": auth.user_json(user)})


async def _safe(coro):
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        log.warning("Bakgrundsjobb misslyckades: %s", e)


# ------------------------------------------------------------------ filmer

def _movie_row(mid):
    return db.connect().execute("SELECT * FROM movies WHERE id=?", (mid,)).fetchone()


@route("/api/v1/movies/advanced-search/", methods=("GET", "POST"))
@route("/api/v1/movies/advanced-search", methods=("GET", "POST"))
@route("/api/v1/movies/", methods=("GET",))
@route("/api/v1/movies", methods=("GET",))
@route("/api/v1/movies/simple/", methods=("GET",))
async def movies_search(request):
    body = await body_json(request) if request.method == "POST" else {}
    return ok(search(merged_params(request, body), uid(request)))


@route("/api/v1/movies/advanced-search/count", methods=("GET", "POST"))
async def movies_count(request):
    body = await body_json(request) if request.method == "POST" else {}
    p = merged_params(request, body)
    p["page_size"] = 1
    p["light"] = "1"
    r = search(p, uid(request))
    return ok({"total": r["total"], "count": r["total"]})


@route("/api/v1/movies/{mid:int}", methods=("GET",))
async def movie_get(request):
    row = _movie_row(request.path_params["mid"])
    if not row:
        return err("The requested resource was not found.", 404)
    return ok(movie_detail(row, UserState(uid(request))))


def similar_rows(mid, limit=24):
    conn = db.connect()
    return conn.execute("""
        WITH kw AS (SELECT keyword_id FROM movie_keywords WHERE movie_id=:m),
             g AS (SELECT j.value v FROM movies, json_each(movies.genres) j WHERE movies.id=:m),
             ppl AS (SELECT person_id FROM movie_people WHERE movie_id=:m AND role IN ('director','writer'))
        SELECT m.*,
          (SELECT COUNT(*) FROM movie_keywords mk WHERE mk.movie_id=m.id AND mk.keyword_id IN (SELECT keyword_id FROM kw)) * 3
          + (SELECT COUNT(*) FROM json_each(m.genres) j WHERE j.value IN (SELECT v FROM g)) * 2
          + (SELECT COUNT(*) FROM movie_people mp WHERE mp.movie_id=m.id AND mp.person_id IN (SELECT person_id FROM ppl)) * 4
          + COALESCE(m.vote_average, 0) * 0.3 AS score
        FROM movies m
        WHERE m.id != :m AND m.id IN (SELECT movie_id FROM movie_keywords WHERE keyword_id IN (SELECT keyword_id FROM kw)
                                       UNION SELECT movie_id FROM movie_people WHERE person_id IN (SELECT person_id FROM ppl))
        ORDER BY score DESC LIMIT :lim""", {"m": mid, "lim": limit}).fetchall()


@route("/api/v1/movies/{mid:int}/similar", methods=("GET",))
@route("/api/v1/movies/{mid:int}/similar/", methods=("GET",))
@route("/api/v1/movies/{mid:int}/similar-movies", methods=("GET",))
async def movie_similar(request):
    mid = request.path_params["mid"]
    st = UserState(uid(request))
    limit = int(request.query_params.get("limit") or request.query_params.get("page_size") or 24)
    movies = [dict(movie_card(r, st), similarity=round(r["score"], 1)) for r in similar_rows(mid, limit)]
    return ok({"movies": movies, "items": movies, "total": len(movies), "is_fallback": False})


@route("/api/v1/movies/{mid:int}/watch-providers", methods=("GET",))
async def movie_providers(request):
    row = _movie_row(request.path_params["mid"])
    if not row:
        return err("Not found", 404)
    region = (request.query_params.get("region") or config.WATCH_REGION).upper()
    prov = db.jl(row["providers"], {}).get(region, {})
    conn = db.connect()
    pinfo = {r["id"]: r for r in conn.execute("SELECT * FROM providers")}

    def expand(ids):
        out = []
        for i in ids or []:
            p = pinfo.get(i)
            if p:
                out.append({"provider_id": i, "provider_name": p["name"], "logo_url": poster_url(p["logo_path"], "w92"),
                            "logo_path": p["logo_path"], "display_priority": p["priority"],
                            "affiliate_url": prov.get("link"), "url": prov.get("link")})
        return out

    data = {"region": region, "link": prov.get("link"), "flatrate": expand(prov.get("flatrate")),
            "free": expand(prov.get("free")), "ads": expand(prov.get("ads")), "rent": expand(prov.get("rent")),
            "buy": expand(prov.get("buy"))}
    rk = UserState().plex.get(row["id"])
    if rk:
        data["plex"] = {"available": True, "url": config.plex_web_url(rk)}
    data["providers"] = data["flatrate"] + data["free"] + data["ads"]
    return ok(data)


@route("/api/v1/movies/{mid:int}/trailer", methods=("GET",))
async def movie_trailer(request):
    row = _movie_row(request.path_params["mid"])
    key = row["trailer_key"] if row else None
    return ok({"youtube_key": key or "no_trailer", "trailer_youtube_key": key or "no_trailer"})


@route("/api/v1/movies/{mid:int}/crew-photos", methods=("GET",))
async def movie_crew_photos(request):
    from .serializers import crew_photos
    row = _movie_row(request.path_params["mid"])
    return ok(crew_photos(row) if row else {})


@route("/api/v1/movies/{mid:int}/cast", methods=("GET",))
async def movie_cast(request):
    row = _movie_row(request.path_params["mid"])
    if not row:
        return ok([])
    return ok(movie_detail(row, UserState())["cast"])


def _unified(q, limit):
    conn = db.connect()
    like = f"%{q}%"
    out = []
    st = UserState()
    for r in conn.execute("""SELECT * FROM movies WHERE title LIKE ? OR original_title LIKE ?
            ORDER BY (title LIKE ?) DESC, popularity DESC LIMIT ?""", (like, like, f"{q}%", limit)):
        out.append(dict(movie_card(r, st), type="movie"))
    for r in conn.execute("""SELECT p.id, p.name, p.profile_path, mp.role, COUNT(*) movie_count
            FROM people p JOIN movie_people mp ON mp.person_id=p.id WHERE p.name LIKE ?
            GROUP BY p.id, mp.role ORDER BY movie_count DESC LIMIT ?""", (like, max(5, limit // 3))):
        out.append(dict(person(r, r["role"]), type=r["role"]))
    for r in conn.execute("""SELECT l.*, (SELECT COUNT(*) FROM list_movies WHERE list_id=l.id) movie_count
            FROM lists l WHERE l.name LIKE ? ORDER BY popularity DESC LIMIT 5""", (like,)):
        out.append(dict(list_summary(r), type="list"))
    return out


@route("/api/v1/movies/search-unified/", methods=("GET",))
@route("/api/v1/movies/unified-autocomplete/", methods=("GET",))
@route("/api/v1/movies/autocomplete/", methods=("GET",))
async def movies_unified(request):
    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return ok([])
    return ok(_unified(q, int(request.query_params.get("limit") or 20)))


@route("/api/v1/movies/search/", methods=("GET",))
@route("/api/v1/movies/search", methods=("GET",))
async def movies_title_search(request):
    p = merged_params(request)
    return ok(search(p, uid(request)))


@route("/api/v1/movies/random", methods=("GET",))
@route("/api/v1/movies/random/", methods=("GET",))
async def movies_random(request):
    row = db.connect().execute(
        "SELECT * FROM movies WHERE vote_count >= 300 ORDER BY RANDOM() LIMIT 1").fetchone()
    return ok(movie_card(row, UserState(uid(request))) if row else {})


@route("/api/v1/trending/widget-movies", methods=("GET",))
async def trending(request):
    rows = db.connect().execute("SELECT * FROM movies WHERE year >= strftime('%Y','now') - 1 "
                                "ORDER BY popularity DESC LIMIT 20").fetchall()
    st = UserState(uid(request))
    movies = [movie_card(r, st) for r in rows]
    return ok({"movies": movies, "items": movies})


# ------------------------------------------------------------------ listor

def _list_previews(list_ids):
    conn = db.connect()
    out = {}
    for lid in list_ids:
        rows = conn.execute("""SELECT m.id, m.title, m.poster_path FROM list_movies lm JOIN movies m ON m.id=lm.movie_id
            WHERE lm.list_id=? AND m.poster_path IS NOT NULL ORDER BY lm.rank LIMIT 4""", (lid,)).fetchall()
        out[lid] = [{"id": r["id"], "movie_id": r["id"], "name": r["title"], "poster_url": poster_url(r["poster_path"], "w342")}
                    for r in rows]
    return out


def _lists_query(where="", args=(), order="l.popularity DESC", limit=30, offset=0):
    conn = db.connect()
    rows = conn.execute(f"""SELECT l.*, (SELECT COUNT(*) FROM list_movies WHERE list_id=l.id) movie_count
        FROM lists l {where} ORDER BY {order} LIMIT ? OFFSET ?""", (*args, limit, offset)).fetchall()
    prev = _list_previews([r["id"] for r in rows])
    return [list_summary(r, prev[r["id"]]) for r in rows]


@route("/api/v1/lists/", methods=("GET",))
@route("/api/v1/lists", methods=("GET",))
@route("/api/v1/lists/search/", methods=("GET",))
async def lists_index(request):
    qp = request.query_params
    page = int(qp.get("page") or 1)
    size = min(100, int(qp.get("page_size") or qp.get("limit") or 30))
    where, args = "", []
    q = (qp.get("q") or qp.get("search") or "").strip()
    if q:
        where, args = "WHERE l.name LIKE ?", [f"%{q}%"]
    sort = qp.get("sort_by") or "popularity"
    order = {"name": "l.name COLLATE NOCASE", "movie_count": "movie_count", "popularity": "l.popularity",
             "random": "RANDOM()"}.get(sort, "l.popularity")
    order += " ASC" if (qp.get("sort_order") == "asc" or (sort == "name" and not qp.get("sort_order"))) else " DESC"
    total = db.connect().execute(f"SELECT COUNT(*) FROM lists l {where}", args).fetchone()[0]
    lists = _lists_query(where, args, order, size, (page - 1) * size)
    return ok({"lists": lists, "items": lists, "total": total, "page": page, "page_size": size,
               "total_pages": max(1, -(-total // size))})


@route("/api/v1/lists/random/", methods=("GET",))
@route("/api/v1/lists/random", methods=("GET",))
async def lists_random(request):
    qp = request.query_params
    limit = min(50, int(qp.get("limit") or 10))
    excl = [int(x) for x in (qp.get("exclude_ids") or "").split(",") if x.strip().isdigit()]
    where = "WHERE l.kind != 'collection'"
    if excl:
        where += " AND l.id NOT IN (%s)" % ",".join(map(str, excl))
    return ok(_lists_query(where, (), "RANDOM()", limit))


@route("/api/v1/lists/by-movie-id/{mid:int}", methods=("GET",))
async def lists_by_movie(request):
    mid = request.path_params["mid"]
    return ok(_lists_query("WHERE l.id IN (SELECT list_id FROM list_movies WHERE movie_id=?)", (mid,),
                           "l.popularity DESC", 30))


@route("/api/v1/lists/{lid:int}", methods=("GET",))
async def list_get(request):
    lid = request.path_params["lid"]
    conn = db.connect()
    row = conn.execute("SELECT l.*, (SELECT COUNT(*) FROM list_movies WHERE list_id=l.id) movie_count FROM lists l "
                       "WHERE id=?", (lid,)).fetchone()
    if not row:
        return err("The requested resource was not found.", 404)
    qp = request.query_params
    rows = conn.execute("""SELECT m.*, lm.rank FROM list_movies lm JOIN movies m ON m.id=lm.movie_id
        WHERE lm.list_id=? ORDER BY lm.rank""", (lid,)).fetchall()
    max_rank = qp.get("max_rank")
    if max_rank and max_rank.isdigit():
        rows = [r for r in rows if r["rank"] <= int(max_rank)]
    sample = qp.get("sample")
    if sample and sample.isdigit():
        rnd = random.Random(qp.get("seed") or 0)
        rows = rnd.sample(rows, min(int(sample), len(rows)))
    st = UserState(uid(request))
    movies = [dict(movie_card(r, st), rank=r["rank"], list_order=r["rank"]) for r in rows]
    d = list_summary(row, _list_previews([lid])[lid])
    d.update({"movies": movies, "items": movies})
    return ok(d)


@route("/api/v1/lists/{lid:int}/similar", methods=("GET",))
async def list_similar(request):
    lid = request.path_params["lid"]
    return ok(_lists_query("""WHERE l.id != ? AND l.id IN (SELECT lm2.list_id FROM list_movies lm1
        JOIN list_movies lm2 ON lm2.movie_id=lm1.movie_id WHERE lm1.list_id=? GROUP BY lm2.list_id
        ORDER BY COUNT(*) DESC LIMIT 12)""", (lid, lid), "l.popularity DESC", 12))


@route("/api/v1/utils/curated-list-ids", methods=("GET",))
async def curated_ids(request):
    return ok([r[0] for r in db.connect().execute("SELECT id FROM lists WHERE kind='collection' LIMIT 500")])


# ------------------------------------------------------------------ personer

def _person_movies(role, name, request):
    conn = db.connect()
    p = conn.execute("SELECT * FROM people WHERE name=? COLLATE NOCASE ORDER BY "
                     "(SELECT COUNT(*) FROM movie_people WHERE person_id=people.id) DESC LIMIT 1", (name,)).fetchone()
    if not p:
        return ok([])
    rows = conn.execute("""SELECT m.*, mp.character FROM movie_people mp JOIN movies m ON m.id=mp.movie_id
        WHERE mp.person_id=? AND mp.role=? ORDER BY m.release_date DESC""", (p["id"], role)).fetchall()
    st = UserState(uid(request))
    # Frontenden förväntar sig en ren lista med filmer
    return ok([dict(movie_card(r, st), character_name=r["character"]) for r in rows])


for _role, _seg in (("actor", "actors"), ("director", "directors"), ("writer", "writers"),
                    ("composer", "composers"), ("cinematographer", "cinematographers")):
    def _mk(role):
        async def handler(request):
            return _person_movies(role, request.path_params["name"], request)
        return handler
    ROUTES.append(Route(f"/api/v1/{_seg}/{{name}}/movies", _mk(_role), methods=["GET"]))


@route("/api/v1/actors/{name}", methods=("GET",))
async def actor_get(request):
    name = request.path_params["name"]
    if name in ("random", "search", "crush-names", "cinematic-crushes"):
        return ok([])
    p = db.connect().execute("SELECT * FROM people WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    return ok(person(p, "actor")) if p else err("Not found", 404)


def _people_list(role, limit, order="movie_count DESC"):
    rows = db.connect().execute(f"""SELECT p.id, p.name, p.profile_path, COUNT(*) movie_count FROM movie_people mp
        JOIN people p ON p.id=mp.person_id WHERE mp.role=? GROUP BY p.id ORDER BY {order} LIMIT ?""",
                                (role, limit)).fetchall()
    return [person(r, role) for r in rows]


@route("/api/v1/actors/random/", methods=("GET",))
async def actors_random(request):
    return ok(_people_list("actor", int(request.query_params.get("limit") or 10), "RANDOM()"))


@route("/api/v1/directors/", methods=("GET",))
@route("/api/v1/directors/most-listed", methods=("GET",))
async def directors_list(request):
    return ok(_people_list("director", int(request.query_params.get("limit") or 30)))


@route("/api/v1/actors/", methods=("GET",))
async def actors_list(request):
    qp = request.query_params
    size = int(qp.get("page_size") or qp.get("limit") or 60)
    items = _people_list("actor", size)
    return ok({"actors": items, "items": items, "total": len(items)})


# ------------------------------------------------------------------ referensdata

@route("/api/v1/genres-themes/master-genres", methods=("GET",))
async def master_genres(request):
    gs = set()
    for (g,) in db.connect().execute("SELECT genres FROM movies"):
        gs.update(db.jl(g))
    return ok(sorted(gs))


_provider_cache = {}


@route("/api/v1/utils/watch-providers", methods=("GET",))
async def watch_providers(request):
    region = (request.query_params.get("region") or config.WATCH_REGION).upper()
    conn = db.connect()
    key = (region, conn.execute("SELECT COUNT(*), MAX(updated_at) FROM movies").fetchone()[:])
    if key in _provider_cache:
        return ok(_provider_cache[key])
    counts = {}
    for (p,) in conn.execute("SELECT providers FROM movies"):
        for i in db.jl(p, {}).get(region, {}).get("flatrate", []):
            counts[i] = counts.get(i, 0) + 1
    rows = conn.execute("SELECT * FROM providers").fetchall()
    out = [{"provider_id": r["id"], "id": r["id"], "provider_name": r["name"], "name": r["name"],
            "logo_url": poster_url(r["logo_path"], "w92"), "logo_path": r["logo_path"],
            "display_priority": r["priority"], "movie_count": counts.get(r["id"], 0)}
           for r in rows if counts.get(r["id"])]
    out.sort(key=lambda x: -x["movie_count"])
    _provider_cache.clear()
    _provider_cache[key] = out
    return ok(out)


@route("/api/v1/utils/watch-provider-regions", methods=("GET",))
async def provider_regions(request):
    regions = set()
    for (p,) in db.connect().execute("SELECT providers FROM movies LIMIT 2000"):
        regions.update(db.jl(p, {}).keys())
    return ok([{"iso_3166_1": r, "code": r, "english_name": r, "name": r} for r in sorted(regions)])


@route("/api/v1/feature-flags", methods=("GET",))
async def feature_flags(request):
    return ok({})


@route("/api/v1/app/config", methods=("GET",))
async def app_config(request):
    return ok({})


# ------------------------------------------------------------------ användarens data

@route("/api/v1/me/watched/{mid:int}", methods=("POST", "PUT"))
async def watched_add(request):
    u, e = require_user(request)
    if e:
        return e
    conn = db.connect()
    conn.execute("INSERT OR IGNORE INTO watched(user_id,movie_id) VALUES(?,?)", (u["id"], request.path_params["mid"]))
    conn.commit()
    return ok({"watched": True})


@route("/api/v1/me/watched/{mid:int}", methods=("DELETE",))
async def watched_del(request):
    u, e = require_user(request)
    if e:
        return e
    conn = db.connect()
    conn.execute("DELETE FROM watched WHERE user_id=? AND movie_id=?", (u["id"], request.path_params["mid"]))
    conn.commit()
    return ok({"watched": False})


@route("/api/v1/me/ratings", methods=("PUT", "POST"))
async def rating_set(request):
    u, e = require_user(request)
    if e:
        return e
    b = await body_json(request)
    mid = int(b.get("movie_id") or 0)
    rating = b.get("rating")
    if not mid or rating is None:
        return err("movie_id och rating krävs")
    conn = db.connect()
    conn.execute("INSERT INTO ratings(user_id,movie_id,rating) VALUES(?,?,?) "
                 "ON CONFLICT(user_id,movie_id) DO UPDATE SET rating=excluded.rating", (u["id"], mid, float(rating)))
    conn.execute("INSERT OR IGNORE INTO watched(user_id,movie_id) VALUES(?,?)", (u["id"], mid))
    conn.commit()
    return ok({"movie_id": mid, "rating": float(rating)})


@route("/api/v1/me/ratings", methods=("GET",))
async def ratings_get(request):
    u = current_user(request)
    if not u:
        return ok([])
    conn = db.connect()
    mid = request.query_params.get("movie_id")
    if mid:
        r = conn.execute("SELECT rating FROM ratings WHERE user_id=? AND movie_id=?", (u["id"], int(mid))).fetchone()
        return ok({"movie_id": int(mid), "rating": r["rating"] if r else None})
    rows = conn.execute("""SELECT m.*, r.rating my_r FROM ratings r JOIN movies m ON m.id=r.movie_id
        WHERE r.user_id=? ORDER BY r.created_at DESC""", (u["id"],)).fetchall()
    st = UserState(u["id"])
    movies = [movie_card(r, st) for r in rows]
    return ok({"movies": movies, "items": movies, "ratings": movies, "total": len(movies)})


@route("/api/v1/me/ratings/{mid:int}", methods=("DELETE",))
async def rating_del(request):
    u, e = require_user(request)
    if e:
        return e
    conn = db.connect()
    conn.execute("DELETE FROM ratings WHERE user_id=? AND movie_id=?", (u["id"], request.path_params["mid"]))
    conn.commit()
    return ok()


def _ids_param(request):
    ids = []
    for v in request.query_params.getlist("movie_ids"):
        ids += [int(x) for x in v.split(",") if x.strip().isdigit()]
    return ids[:500]


@route("/api/v1/me/watched/map", methods=("GET",))
async def watched_map(request):
    u = current_user(request)
    ids = _ids_param(request)
    if not u or not ids:
        return ok({"watched": {}, "available": bool(u)})
    rows = db.connect().execute("""SELECT w.movie_id, w.source, w.watched_at,
        EXISTS(SELECT 1 FROM ratings r WHERE r.user_id=w.user_id AND r.movie_id=w.movie_id) rated
        FROM watched w WHERE w.user_id=? AND w.movie_id IN (%s)""" % ",".join("?" * len(ids)),
                                (u["id"], *ids)).fetchall()
    return ok({"watched": {str(r["movie_id"]): {"watched": True, "locked": bool(r["rated"]), "source": r["source"],
                                                "watched_at": r["watched_at"]} for r in rows},
               "available": True})


@route("/api/v1/me/ratings/map", methods=("GET",))
async def ratings_map(request):
    u = current_user(request)
    ids = _ids_param(request)
    if not u or not ids:
        return ok({"ratings": {}})
    rows = db.connect().execute("SELECT movie_id, rating FROM ratings WHERE user_id=? AND movie_id IN (%s)"
                                % ",".join("?" * len(ids)), (u["id"], *ids)).fetchall()
    return ok({"ratings": {str(r["movie_id"]): r["rating"] for r in rows}})


@route("/api/v1/me/rated-movies", methods=("GET",))
async def rated_movies(request):
    u, e = require_user(request)
    if e:
        return e
    rows = db.connect().execute("""SELECT m.*, r.rating my_r, r.created_at rated_at FROM ratings r
        JOIN movies m ON m.id=r.movie_id WHERE r.user_id=? ORDER BY r.created_at DESC""", (u["id"],)).fetchall()
    st = UserState(u["id"])
    movies = [dict(movie_card(r, st), rated_at=r["rated_at"]) for r in rows]
    return ok({"movies": movies, "items": movies, "total": len(movies), "total_pages": 1, "page": 1})


def _user_movies(table, user_id, order_col):
    rows = db.connect().execute(f"""SELECT m.* FROM {table} t JOIN movies m ON m.id=t.movie_id
        WHERE t.user_id=? ORDER BY t.{order_col} DESC""", (user_id,)).fetchall()
    st = UserState(user_id)
    movies = [movie_card(r, st) for r in rows]
    return {"movies": movies, "items": movies, "total": len(movies), "movie_count": len(movies)}


@route("/api/v1/user-lists/me/watchlist/movies", methods=("POST",))
async def watchlist_add(request):
    u, e = require_user(request)
    if e:
        return e
    b = await body_json(request)
    mid = int(b.get("movie_id") or 0)
    conn = db.connect()
    conn.execute("INSERT OR IGNORE INTO watchlist(user_id,movie_id) VALUES(?,?)", (u["id"], mid))
    conn.commit()
    return ok({"in_watchlist": True, "movie_id": mid})


@route("/api/v1/user-lists/me/watchlist/movies/{mid:int}", methods=("DELETE",))
async def watchlist_del(request):
    u, e = require_user(request)
    if e:
        return e
    conn = db.connect()
    conn.execute("DELETE FROM watchlist WHERE user_id=? AND movie_id=?", (u["id"], request.path_params["mid"]))
    conn.commit()
    return ok({"in_watchlist": False})


@route("/api/v1/user-lists/me/watchlist", methods=("GET",))
@route("/api/v1/user-lists/me/watchlist/", methods=("GET",))
@route("/api/v1/me/watchlist", methods=("GET",))
async def watchlist_get(request):
    u, e = require_user(request)
    if e:
        return e
    d = _user_movies("watchlist", u["id"], "added_at")
    d.update({"id": "watchlist", "name": "Watchlist"})
    return ok(d)


@route("/api/v1/me/watched", methods=("GET",))
@route("/api/v1/me/watched/", methods=("GET",))
async def watched_get(request):
    u, e = require_user(request)
    if e:
        return e
    return ok(_user_movies("watched", u["id"], "watched_at"))


# ------------------------------------------------------------------ Plex-status och önskningar

@route("/api/local/config", methods=("GET",))
async def local_config(request):
    u = current_user(request)
    return ok({
        "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
        "request_backend": config.request_backend(),
        "require_login": config.REQUIRE_LOGIN,
        "user": auth.user_json(u) if u else None,
        "import_status": db.get_meta("import_status"),
        "import_progress": db.get_meta("import_progress"),
        "movie_count": db.connect().execute("SELECT COUNT(*) FROM movies").fetchone()[0],
        "plex_library_count": db.connect().execute("SELECT COUNT(*) FROM plex_library").fetchone()[0],
    })


@route("/api/local/movies/{mid:int}/status", methods=("GET",))
async def local_movie_status(request):
    mid = request.path_params["mid"]
    u = current_user(request)
    st = UserState(u["id"] if u else None)
    rk = st.plex.get(mid)
    conn = db.connect()
    req = conn.execute("SELECT status, detail FROM requests WHERE movie_id=? ORDER BY "
                       "(user_id=?) DESC, created_at DESC LIMIT 1", (mid, u["id"] if u else 0)).fetchone()
    return ok({
        "movie_id": mid,
        "on_plex": rk is not None,
        "plex_url": config.plex_web_url(rk) if rk else None,
        "watched": mid in st.watched,
        "request_status": req["status"] if req else None,
        "request_detail": req["detail"] if req else None,
        "logged_in": bool(u),
        "plex_configured": bool(config.PLEX_URL and config.PLEX_TOKEN),
    })


@route("/api/local/requests", methods=("POST",))
async def local_request_create(request):
    u, e = require_user(request)
    if e:
        return e
    b = await body_json(request)
    mid = int(b.get("movie_id") or 0)
    row = _movie_row(mid)
    if not row:
        return err("Filmen finns inte.", 404)
    conn = db.connect()
    if conn.execute("SELECT 1 FROM plex_library WHERE tmdb_id=?", (mid,)).fetchone():
        return err("Filmen finns redan i Plex.", 409)
    try:
        status, detail = await plex.send_request(row)
    except Exception as ex:  # noqa: BLE001
        status, detail = "failed", f"Kunde inte skicka önskan: {ex}"
    conn.execute("""INSERT INTO requests(user_id,movie_id,status,detail) VALUES(?,?,?,?)
        ON CONFLICT(user_id,movie_id) DO UPDATE SET status=excluded.status, detail=excluded.detail""",
                 (u["id"], mid, status, detail))
    conn.commit()
    return ok({"status": status, "detail": detail}, 200 if status != "failed" else 502)


@route("/api/local/requests", methods=("GET",))
async def local_requests_list(request):
    u, e = require_user(request)
    if e:
        return e
    conn = db.connect()
    where = "" if u["is_admin"] else "WHERE r.user_id=%d" % u["id"]
    rows = conn.execute(f"""SELECT r.*, u.username, m.title, m.year, m.poster_path FROM requests r
        JOIN users u ON u.id=r.user_id JOIN movies m ON m.id=r.movie_id {where} ORDER BY r.created_at DESC""").fetchall()
    return ok([{"id": r["id"], "movie_id": r["movie_id"], "title": r["title"], "year": r["year"],
                "poster_url": poster_url(r["poster_path"], "w185"), "status": r["status"], "detail": r["detail"],
                "username": r["username"], "created_at": r["created_at"], "mine": r["user_id"] == u["id"]}
               for r in rows])


@route("/api/local/requests/{rid:int}", methods=("DELETE",))
async def local_request_delete(request):
    u, e = require_user(request)
    if e:
        return e
    conn = db.connect()
    if u["is_admin"]:
        conn.execute("DELETE FROM requests WHERE id=?", (request.path_params["rid"],))
    else:
        conn.execute("DELETE FROM requests WHERE id=? AND user_id=?", (request.path_params["rid"], u["id"]))
    conn.commit()
    return ok()


def _user_section_keys(u):
    conn = db.connect()
    if u["is_admin"]:
        return [r[0] for r in conn.execute("SELECT key FROM plex_sections")]
    return [r[0] for r in conn.execute("SELECT section_key FROM user_sections WHERE user_id=?", (u["id"],))]


@route("/api/local/plex/sections", methods=("GET",))
async def local_plex_sections(request):
    u, e = require_user(request)
    if e:
        return e
    keys = _user_section_keys(u)
    if not keys and not u["is_admin"]:
        # Biblioteksbehörighet har inte synkats än för den här användaren
        asyncio.create_task(_safe(plex.sync_watched(u["id"])))
    rows = db.connect().execute("SELECT * FROM plex_sections ORDER BY rowid").fetchall()
    return ok([{"key": r["key"], "title": r["title"], "type": r["type"], "count": r["item_count"]}
               for r in rows if r["key"] in keys])


@route("/api/local/plex/sections/{key}/items", methods=("GET",))
async def local_plex_items(request):
    u, e = require_user(request)
    if e:
        return e
    key = request.path_params["key"]
    if key not in _user_section_keys(u):
        return err("Du har inte tillgång till det här biblioteket.", 403)
    conn = db.connect()
    sec = conn.execute("SELECT * FROM plex_sections WHERE key=?", (key,)).fetchone()
    if not sec:
        return err("Biblioteket finns inte.", 404)
    rows = conn.execute("""SELECT i.*, m.poster_path, m.vote_average, m.id AS movie_id FROM plex_items i
        LEFT JOIN movies m ON m.id=i.tmdb_id AND i.type='movie'
        WHERE i.section_key=? ORDER BY i.title COLLATE NOCASE""", (key,)).fetchall()
    watched = {r[0] for r in conn.execute("SELECT movie_id FROM watched WHERE user_id=?", (u["id"],))}
    progress = {r["rating_key"]: (r["viewed"], r["total"]) for r in conn.execute(
        "SELECT rating_key, viewed, total FROM user_plex_watched WHERE user_id=?", (u["id"],))}
    items = []
    for r in rows:
        in_db = r["movie_id"] is not None
        items.append({
            "rating_key": r["rating_key"],
            "title": r["title"],
            "year": r["year"],
            "type": r["type"],
            "added_at": r["added_at"],
            "seasons": r["seasons"],
            "episodes": r["episodes"],
            "rating": round(r["vote_average"], 1) if r["vote_average"] else None,
            "poster_url": poster_url(r["poster_path"], "w342") if r["poster_path"]
            else (f"/api/local/plex/thumb/{r['rating_key']}" if r["thumb"] else None),
            "url": f"/movies/{r['movie_id']}" if in_db else config.plex_web_url(r["rating_key"]),
            "external": not in_db,
            "plex_url": config.plex_web_url(r["rating_key"]),
            "watched": bool((in_db and r["movie_id"] in watched)
                            or (r["rating_key"] in progress and progress[r["rating_key"]][0] >= (progress[r["rating_key"]][1] or 1))),
            "viewed_episodes": progress.get(r["rating_key"], (None, None))[0] if r["type"] == "show" else None,
        })
    return ok({"key": sec["key"], "title": sec["title"], "type": sec["type"], "items": items})


def _fmt_ms(ms):
    if not ms:
        return None
    m = int(ms) // 60000
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m"


@route("/api/local/plex/items/{rk}", methods=("GET",))
async def local_plex_item(request):
    u, e = require_user(request)
    if e:
        return e
    rk = request.path_params["rk"]
    conn = db.connect()
    row = conn.execute("SELECT * FROM plex_items WHERE rating_key=? AND section_key IN (%s) LIMIT 1"
                       % ",".join("?" * max(1, len(_user_section_keys(u)))),
                       (rk, *(_user_section_keys(u) or [""]))).fetchone()
    if not row:
        return err("Du har inte tillgång till den här titeln.", 403)
    token = (conn.execute("SELECT server_token FROM users WHERE id=?", (u["id"],)).fetchone() or {})["server_token"] \
        or config.PLEX_TOKEN
    try:
        meta, seasons = await plex.item_details(rk, token)
    except plex.PlexError as ex:
        return err(str(ex), 404)
    except Exception as ex:  # noqa: BLE001
        log.warning("Plex-detaljer %s: %s", rk, ex)
        return err("Kunde inte hämta uppgifter från Plex.", 502)

    movie = conn.execute("SELECT * FROM movies WHERE id=?", (row["tmdb_id"],)).fetchone() \
        if row["type"] == "movie" and row["tmdb_id"] else None
    rating_info = await ratings.collect(meta, row["tmdb_id"], movie)

    def tags(key, limit=None):
        return [t.get("tag") for t in (meta.get(key) or [])[:limit] if t.get("tag")]

    media = (meta.get("Media") or [{}])[0]
    is_show = meta.get("type") == "show"
    viewed = meta.get("viewedLeafCount") if is_show else None
    total = meta.get("leafCount") if is_show else None
    data = {
        "rating_key": rk,
        "type": meta.get("type"),
        "title": meta.get("title"),
        "original_title": meta.get("originalTitle"),
        "year": meta.get("year"),
        "tagline": meta.get("tagline"),
        "summary": meta.get("summary") or (movie["overview"] if movie else None),
        "genres": tags("Genre") or (db.jl(movie["genres"]) if movie else []),
        "directors": tags("Director"),
        "writers": tags("Writer", 4),
        "studio": meta.get("studio"),
        "content_rating": meta.get("contentRating"),
        "released": meta.get("originallyAvailableAt"),
        "duration": _fmt_ms(meta.get("duration")) if not is_show else None,
        "cast": [{"name": r.get("tag"), "role": r.get("role"), "photo": r.get("thumb")}
                 for r in (meta.get("Role") or [])[:12]],
        "ratings": rating_info,
        "poster_url": poster_url(movie["poster_path"], "w500") if movie and movie["poster_path"]
        else (f"/api/local/plex/thumb/{rk}" if row["thumb"] else None),
        "backdrop_url": poster_url(movie["backdrop_path"], "w1280") if movie and movie["backdrop_path"]
        else (f"/api/local/plex/thumb/{rk}?kind=art" if row["art"] else None),
        "plex_url": config.plex_web_url(rk),
        "film_url": f"/movies/{movie['id']}" if movie else None,
        "watched": (viewed is not None and total and viewed >= total) or (not is_show and (meta.get("viewCount") or 0) > 0),
        "in_progress": bool(meta.get("viewOffset")) and not is_show,
        "progress_pct": round(100 * meta["viewOffset"] / meta["duration"]) if meta.get("viewOffset") and meta.get("duration") else None,
        "view_count": meta.get("viewCount"),
        "last_viewed": meta.get("lastViewedAt"),
        "episodes": total,
        "viewed_episodes": viewed,
        "seasons": [{"title": sn.get("title"), "episodes": sn.get("leafCount"), "viewed": sn.get("viewedLeafCount") or 0}
                    for sn in seasons if sn.get("type") == "season"],
        "video": " · ".join(x for x in [
            (str(media.get("videoResolution")).upper() + ("p" if str(media.get("videoResolution", "")).isdigit() else ""))
            if media.get("videoResolution") else None,
            str(media.get("videoCodec") or "").upper() or None,
            (str(media.get("audioCodec") or "").upper() + (f" {media.get('audioChannels')}ch" if media.get("audioChannels") else ""))
            if media.get("audioCodec") else None,
        ] if x) or None,
    }
    return ok(data)


@route("/api/local/plex/thumb/{rk}", methods=("GET",))
async def local_plex_thumb(request):
    rk = request.path_params["rk"]
    art = request.query_params.get("kind") == "art"
    row = db.connect().execute("SELECT thumb, art FROM plex_items WHERE rating_key=? LIMIT 1", (rk,)).fetchone()
    path = row and (row["art"] if art else row["thumb"])
    if not path or not (config.PLEX_URL and config.PLEX_TOKEN):
        return Response(status_code=404)
    try:
        status, ctype, content = await plex.thumb(path, *((1280, 720) if art else (300, 450)))
    except Exception:  # noqa: BLE001
        return Response(status_code=502)
    if status != 200:
        return Response(status_code=404)
    return Response(content, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


@route("/plex")
@route("/plex/{key}")
async def plex_page(request):
    return FileResponse(os.path.join(LOCAL, "plex.html"), headers={"Cache-Control": "no-cache"})


@route("/api/local/plex/sync", methods=("POST",))
async def local_plex_sync(request):
    u, e = require_user(request)
    if e:
        return e
    if u["is_admin"]:
        asyncio.create_task(_safe(plex.sync_all()))
    else:
        asyncio.create_task(_safe(plex.sync_watched(u["id"])))
    return ok({"started": True})


# ------------------------------------------------------------------ allt annat

async def api_fallback(request):
    """Funktioner från originalet som inte finns här (spel, chatt, partner m.m.) får tomma svar."""
    if request.method == "GET":
        return ok([])
    if request.url.path.startswith(("/api/v1/me/", "/api/v1/user-lists/")) and not current_user(request):
        return err("Authentication required.", 401)
    return ok({"ok": True})


# ------------------------------------------------------------------ start

def _run_import_thread(limit):
    # Egen tråd med egen event-loop och egen databasanslutning, så att webbservern
    # svarar som vanligt medan importen och listbygget pågår.
    asyncio.run(tmdb_import.run(limit))


async def import_if_empty():
    conn = db.connect()
    count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    unfinished = db.get_meta("import_status") == "running"
    if (count == 0 or unfinished) and tmdb_import.configured():
        log.info("Startar import från TMDB i bakgrunden (max %d filmer, %d finns redan).",
                 tmdb_import.MAX_MOVIES, count)
        await asyncio.to_thread(_run_import_thread, tmdb_import.MAX_MOVIES)
    elif count == 0:
        log.warning("Databasen är tom och TMDB_TOKEN saknas – sidan kommer att vara tom.")
    elif conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == 0:
        log.info("Inga listor finns – bygger listor.")
        await asyncio.to_thread(tmdb_import.build_lists_standalone)


@contextlib.asynccontextmanager
async def lifespan(app):
    db.init()
    prepare_web()
    tasks = [asyncio.create_task(_safe(import_if_empty()))]
    if config.PLEX_URL and config.PLEX_TOKEN:
        tasks.append(asyncio.create_task(plex.sync_loop()))
    yield
    for t in tasks:
        t.cancel()


ROUTES.append(Route("/{path:path}", spa, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]))
app = Starlette(routes=ROUTES, lifespan=lifespan)


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "80")), proxy_headers=True,
                forwarded_allow_ips="*", log_level="warning")


if __name__ == "__main__":
    main()
