"""Importerar filmer från TMDB till den lokala databasen.

Körs automatiskt vid start om databasen är tom, och kan köras manuellt:
    docker exec -it filmdilemma python -m app.tmdb_import [--max 20000] [--refresh]

Kräver TMDB_TOKEN (API Read Access Token, "eyJ...") eller TMDB_API_KEY (v3-nyckel).
"""
import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sys
import time

import httpx

from . import db

log = logging.getLogger("tmdb_import")

API = "https://api.themoviedb.org/3"
TOKEN = os.environ.get("TMDB_TOKEN", "").strip()
API_KEY = os.environ.get("TMDB_API_KEY", "").strip()
LANGUAGE = os.environ.get("TMDB_LANGUAGE", "en-US")
MAX_MOVIES = int(os.environ.get("MAX_MOVIES", "20000"))
MIN_VOTES = int(os.environ.get("MIN_VOTES", "40"))
CONCURRENCY = int(os.environ.get("TMDB_CONCURRENCY", "20"))
REQ_PER_SEC = float(os.environ.get("TMDB_RATE", "35"))


def configured():
    return bool(TOKEN or API_KEY)


class Tmdb:
    def __init__(self):
        headers = {"Accept": "application/json"}
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
        self.client = httpx.AsyncClient(headers=headers, timeout=30)
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def _throttle(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + 1.0 / REQ_PER_SEC
        if wait > 0:
            await asyncio.sleep(wait)

    async def get(self, path, **params):
        if API_KEY and not TOKEN:
            params["api_key"] = API_KEY
        for attempt in range(6):
            async with self.sem:
                await self._throttle()
                try:
                    r = await self.client.get(API + path, params=params)
                except httpx.HTTPError as e:
                    log.warning("TMDB %s: %s", path, e)
                    await asyncio.sleep(2 ** attempt)
                    continue
            if r.status_code == 429:
                await asyncio.sleep(float(r.headers.get("Retry-After", "2")))
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code in (401, 403):
                raise SystemExit("TMDB nekade åtkomst (401/403). Kontrollera TMDB_TOKEN / TMDB_API_KEY.")
            r.raise_for_status()
            return r.json()
        return None

    async def close(self):
        await self.client.aclose()


def _year_ranges():
    """Delar upp discover i årsintervall så att vi kommer förbi TMDB:s gräns på 500 sidor."""
    this_year = dt.date.today().year
    ranges = [(y, y) for y in range(this_year + 1, 1999, -1)]
    ranges += [(y, y + 1) for y in range(1998, 1979, -2)]
    ranges += [(y, y + 4) for y in range(1975, 1939, -5)]
    ranges += [(1874, 1939)]
    return ranges


async def discover_ids(t, limit):
    """Samlar TMDB-id:n för de filmer som har flest röster, uppdelat på årsintervall."""
    found = {}
    ranges = _year_ranges()
    # Ungefärlig kvot per intervall; de med flest röster väljs sedan ut totalt
    per_range = max(200, int(limit / len(ranges) * 1.6))
    pages_needed = min(500, -(-per_range // 20))

    def params(y0, y1, page):
        return {"sort_by": "vote_count.desc", "primary_release_date.gte": f"{y0}-01-01",
                "primary_release_date.lte": f"{y1}-12-31", "vote_count.gte": MIN_VOTES,
                "include_adult": "false", "include_video": "false", "language": LANGUAGE, "page": page}

    for (y0, y1) in ranges:
        first = await t.get("/discover/movie", **params(y0, y1, 1))
        if not first or not first.get("results"):
            continue
        pages = [first]
        last = min(pages_needed, first.get("total_pages", 1))
        if last > 1:
            pages += await asyncio.gather(*(t.get("/discover/movie", **params(y0, y1, p))
                                            for p in range(2, last + 1)))
        got = 0
        for data in pages:
            for m in (data or {}).get("results", []):
                if m["id"] not in found:
                    found[m["id"]] = m.get("vote_count", 0)
                    got += 1
        log.info("Upptäckt %s-%s: %d filmer (totalt %d)", y0, y1, got, len(found))
    ids = sorted(found, key=lambda k: -found[k])
    return ids[:limit]


def _people(credits, job_filter):
    return [c for c in credits.get("crew", []) if job_filter(c)]


def save_movie(conn, m):
    credits = m.get("credits") or {}
    crew = credits.get("crew", [])
    directors = [c for c in crew if c.get("job") == "Director"]
    writers = [c for c in crew if c.get("department") == "Writing"]
    composers = [c for c in crew if c.get("job") in ("Original Music Composer", "Music")]
    dops = [c for c in crew if c.get("job") == "Director of Photography"]
    cast = sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))[:30]

    def uniq(people):
        seen, out = set(), []
        for p in people:
            if p["id"] not in seen:
                seen.add(p["id"])
                out.append(p)
        return out

    directors, writers, composers = uniq(directors), uniq(writers)[:6], uniq(composers)[:3]

    videos = (m.get("videos") or {}).get("results", [])
    trailer = next((v["key"] for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"
                    and v.get("official")), None) or next(
        (v["key"] for v in videos if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")), None)

    kws = (m.get("keywords") or {}).get("keywords", [])
    providers = (m.get("watch/providers") or {}).get("results", {})
    slim_prov = {}
    for region, p in providers.items():
        slim_prov[region] = {"link": p.get("link")}
        for kind in ("flatrate", "free", "ads", "rent", "buy"):
            if p.get(kind):
                slim_prov[region][kind] = [x["provider_id"] for x in p[kind]]
                for x in p[kind]:
                    conn.execute("INSERT OR IGNORE INTO providers(id,name,logo_path,priority) VALUES(?,?,?,?)",
                                 (x["provider_id"], x.get("provider_name"), x.get("logo_path"),
                                  x.get("display_priority", 99)))

    rd = m.get("release_date") or ""
    coll = m.get("belongs_to_collection") or {}
    conn.execute("""INSERT OR REPLACE INTO movies(id,title,original_title,original_language,year,release_date,runtime,
        overview,tagline,poster_path,backdrop_path,vote_average,vote_count,popularity,imdb_id,genres,keywords,languages,
        countries,directors,writers,composers,cinematographer,cast_json,trailer_key,collection_id,collection_name,
        providers,budget,revenue,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        m["id"], m.get("title") or m.get("original_title") or "?", m.get("original_title"), m.get("original_language"),
        int(rd[:4]) if rd[:4].isdigit() else None, rd or None, m.get("runtime"), m.get("overview"), m.get("tagline"),
        m.get("poster_path"), m.get("backdrop_path"), m.get("vote_average"), m.get("vote_count"), m.get("popularity"),
        m.get("imdb_id"),
        json.dumps([g["name"] for g in m.get("genres", [])]),
        json.dumps([{"id": k["id"], "name": k["name"]} for k in kws]),
        json.dumps([l["iso_639_1"] for l in m.get("spoken_languages", [])]),
        json.dumps([c["iso_3166_1"] for c in m.get("production_countries", [])]),
        json.dumps([p["name"] for p in directors]),
        json.dumps([p["name"] for p in writers]),
        json.dumps([p["name"] for p in composers]),
        dops[0]["name"] if dops else None,
        json.dumps([{"id": c["id"], "name": c["name"], "character": c.get("character"),
                     "profile_path": c.get("profile_path"), "order": c.get("order")} for c in cast]),
        trailer, coll.get("id"), coll.get("name"), json.dumps(slim_prov),
        m.get("budget"), m.get("revenue"), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
    ))
    conn.execute("DELETE FROM movie_people WHERE movie_id=?", (m["id"],))
    conn.execute("DELETE FROM movie_keywords WHERE movie_id=?", (m["id"],))

    def link(person, role, ord_, character=None):
        conn.execute("INSERT OR IGNORE INTO people(id,name,profile_path) VALUES(?,?,?)",
                     (person["id"], person["name"], person.get("profile_path")))
        if person.get("profile_path"):
            conn.execute("UPDATE people SET profile_path=? WHERE id=? AND profile_path IS NULL",
                         (person["profile_path"], person["id"]))
        conn.execute("INSERT OR IGNORE INTO movie_people(movie_id,person_id,role,ord,character) VALUES(?,?,?,?,?)",
                     (m["id"], person["id"], role, ord_, character))

    for i, c in enumerate(cast):
        link(c, "actor", i, c.get("character"))
    for role, people in (("director", directors), ("writer", writers), ("composer", composers),
                         ("cinematographer", dops[:1])):
        for i, p in enumerate(people):
            link(p, role, i)
    for k in kws:
        conn.execute("INSERT OR IGNORE INTO keywords(id,name) VALUES(?,?)", (k["id"], k["name"]))
        conn.execute("INSERT OR IGNORE INTO movie_keywords(movie_id,keyword_id) VALUES(?,?)", (m["id"], k["id"]))


async def fetch_movie(t, mid):
    return await t.get(f"/movie/{mid}", language=LANGUAGE,
                       append_to_response="credits,keywords,videos,watch/providers")


async def fetch_tv(t, tv_id):
    return await t.get(f"/tv/{tv_id}", language=LANGUAGE,
                       append_to_response="aggregate_credits,keywords,videos,watch/providers,external_ids")


def save_tv(conn, d):
    """Sparar en serie i movies-tabellen (id = TV_OFFSET + serie-id) i samma format som en film."""
    agg = d.get("aggregate_credits") or {}
    cast = []
    for c in sorted(agg.get("cast", []), key=lambda c: c.get("order", 999))[:30]:
        roles = c.get("roles") or [{}]
        cast.append({"id": c["id"], "name": c["name"], "character": roles[0].get("character"),
                     "profile_path": c.get("profile_path"), "order": c.get("order")})
    crew = [{"id": p["id"], "name": p["name"], "profile_path": p.get("profile_path"), "job": "Director",
             "department": "Directing"} for p in d.get("created_by", [])]
    for c in agg.get("crew", []):
        jobs = {j.get("job") for j in c.get("jobs", [])}
        if jobs & {"Original Music Composer", "Music"}:
            crew.append({"id": c["id"], "name": c["name"], "profile_path": c.get("profile_path"),
                         "job": "Original Music Composer", "department": "Sound"})
    runtimes = d.get("episode_run_time") or []
    m = {
        "id": db.TV_OFFSET + d["id"],
        "title": d.get("name"), "original_title": d.get("original_name"),
        "original_language": d.get("original_language"), "release_date": d.get("first_air_date"),
        "runtime": runtimes[0] if runtimes else ((d.get("last_episode_to_air") or {}).get("runtime")),
        "overview": d.get("overview"), "tagline": d.get("tagline"),
        "poster_path": d.get("poster_path"), "backdrop_path": d.get("backdrop_path"),
        "vote_average": d.get("vote_average"), "vote_count": d.get("vote_count"), "popularity": d.get("popularity"),
        "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
        "genres": d.get("genres", []),
        "keywords": {"keywords": (d.get("keywords") or {}).get("results", [])},
        "spoken_languages": d.get("spoken_languages", []),
        "production_countries": d.get("production_countries", []),
        "credits": {"cast": cast, "crew": crew},
        "videos": d.get("videos"), "watch/providers": d.get("watch/providers"),
    }
    save_movie(conn, m)
    conn.execute("UPDATE movies SET media_type='tv' WHERE id=?", (m["id"],))


async def find_by_external(t, source, ext_id):
    """Slår upp TMDB-id från IMDb- eller TVDB-id. source = 'imdb' eller 'tvdb'. Returnerar (tmdb_id, media)."""
    conn = db.connect()
    key = f"{source}:{ext_id}"
    row = conn.execute("SELECT tmdb_id, media FROM tmdb_find WHERE ext_key=?", (key,)).fetchone()
    if row:
        return row["tmdb_id"], row["media"]
    d = await t.get(f"/find/{ext_id}", external_source=f"{source}_id")
    tid, media = None, None
    if d:
        if d.get("movie_results"):
            tid, media = d["movie_results"][0]["id"], "movie"
        elif d.get("tv_results"):
            tid, media = d["tv_results"][0]["id"], "tv"
    conn.execute("INSERT OR REPLACE INTO tmdb_find(ext_key,tmdb_id,media) VALUES(?,?,?)", (key, tid, media))
    conn.commit()
    return tid, media


async def import_titles(movie_ids, tv_ids):
    """Hämtar filmer och serier som finns i Plex men saknas i databasen."""
    if not configured() or not (movie_ids or tv_ids):
        return 0
    conn = db.connect()
    t = Tmdb()
    done = 0
    try:
        for start in range(0, len(movie_ids), 100):
            chunk = movie_ids[start:start + 100]
            for m in await asyncio.gather(*(fetch_movie(t, i) for i in chunk)):
                if m and not m.get("adult"):
                    save_movie(conn, m)
                    done += 1
            conn.commit()
        for start in range(0, len(tv_ids), 100):
            chunk = tv_ids[start:start + 100]
            for d in await asyncio.gather(*(fetch_tv(t, i) for i in chunk)):
                if d:
                    save_tv(conn, d)
                    done += 1
            conn.commit()
    finally:
        await t.close()
    log.info("Hämtade %d titlar från Plex-biblioteket som saknades (%d filmer, %d serier efterfrågade)",
             done, len(movie_ids), len(tv_ids))
    return done


_search_cache = {}


async def import_ids(t, ids, conn):
    """Hämtar filmer som saknas lokalt. Returnerar antal nya."""
    have = {r[0] for r in conn.execute("SELECT id FROM movies WHERE id IN (%s)" % ",".join(map(str, ids)))} if ids else set()
    todo = [i for i in ids if i not in have]
    for start in range(0, len(todo), 50):
        for m in await asyncio.gather(*(fetch_movie(t, i) for i in todo[start:start + 50])):
            if m and not m.get("adult"):
                save_movie(conn, m)
        conn.commit()
    return len(todo)


async def search_and_import(query, limit=10):
    """Söker direkt i TMDB och hämtar in träffar som saknas lokalt (t.ex. årets nya filmer)."""
    q = (query or "").strip().lower()
    if len(q) < 2 or not configured():
        return 0
    hit = _search_cache.get(q)
    if hit and time.time() - hit < 1800:
        return 0
    _search_cache[q] = time.time()
    conn = db.connect()
    t = Tmdb()
    try:
        d = await t.get("/search/movie", query=query, language=LANGUAGE, include_adult="false", page=1)
        ids = [m["id"] for m in (d or {}).get("results", [])
               if not m.get("adult") and (m.get("poster_path") or (m.get("vote_count") or 0) > 0)][:limit]
        return await import_ids(t, ids, conn)
    finally:
        await t.close()


async def refresh_recent():
    """Hämtar nya och aktuella filmer: på bio, kommande, trendande och årets populäraste."""
    if not configured():
        return 0
    conn = db.connect()
    t = Tmdb()
    try:
        ids = []
        year = dt.date.today().year
        for path, pages in (("/movie/now_playing", 3), ("/movie/upcoming", 3), ("/trending/movie/week", 3),
                            ("/movie/popular", 5)):
            for page in range(1, pages + 1):
                d = await t.get(path, language=LANGUAGE, page=page)
                ids += [m["id"] for m in (d or {}).get("results", []) if not m.get("adult")]
        for y in (year, year - 1):
            for page in range(1, 6):
                d = await t.get("/discover/movie", language=LANGUAGE, sort_by="popularity.desc", page=page,
                                include_adult="false", **{"primary_release_year": y, "vote_count.gte": 5})
                ids += [m["id"] for m in (d or {}).get("results", []) if not m.get("adult")]
        ids = list(dict.fromkeys(ids))
        n = await import_ids(t, ids, conn)
        db.set_meta("recent_refreshed", int(time.time()))
        log.info("Nya filmer från TMDB: %d kontrollerade, %d hämtade", len(ids), n)
        if n:
            build_lists(conn)
        return n
    finally:
        await t.close()


async def refresh_loop():
    """Kör refresh_recent en gång per dygn (första gången när huvudimporten är klar)."""
    while db.get_meta("import_status") == "running":
        await asyncio.sleep(60)
    while True:
        last = int(db.get_meta("recent_refreshed") or 0)
        if time.time() - last > 20 * 3600:
            try:
                await refresh_recent()
            except Exception as e:  # noqa: BLE001
                log.warning("Hämtning av nya filmer misslyckades: %s", e)
        await asyncio.sleep(3600)


def build_lists(conn):
    """Skapar listor av TMDB-nyckelord, samlingar och genre+årtionde (beräknas i minnet)."""
    import math
    log.info("Bygger listor ...")
    movies = {}
    for r in conn.execute("SELECT id, title, year, release_date, genres, vote_average, vote_count, popularity, "
                          "collection_id, collection_name FROM movies WHERE COALESCE(media_type,'movie')='movie'"):
        movies[r["id"]] = r
    score = {mid: (r["vote_average"] or 0) * math.log((r["vote_count"] or 0) + 2) for mid, r in movies.items()}

    lists = []   # (name, description, kind, source_id, [movie ids])

    # Nyckelord med många filmer -> "Best movies about X"
    kw_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM keywords")}
    by_kw = {}
    for mk in conn.execute("SELECT movie_id, keyword_id FROM movie_keywords"):
        if mk["movie_id"] in movies:
            by_kw.setdefault(mk["keyword_id"], []).append(mk["movie_id"])
    skip = {"woman director", "duringcreditsstinger", "aftercreditsstinger", "independent film", "short film",
            "based on novel or book", "live action remake", "lgbt"}
    for kid, mids in sorted(by_kw.items(), key=lambda kv: -len(kv[1]))[:3000]:
        name = (kw_names.get(kid) or "").strip()
        if len(mids) < 8 or not name or name in skip:
            continue
        mids = sorted((m for m in mids if m in movies), key=lambda m: -score[m])[:200]
        lists.append((f"Best movies about {name}", f"Highly rated films tagged “{name}”.", "keyword", kid, mids))

    # Samlingar (filmserier) i utgivningsordning
    colls = {}
    for mid, r in movies.items():
        if r["collection_id"]:
            colls.setdefault((r["collection_id"], r["collection_name"]), []).append(mid)
    for (cid, cname), mids in colls.items():
        if len(mids) >= 3:
            mids.sort(key=lambda m: movies[m]["release_date"] or "")
            lists.append((cname, "Every film in the series, in release order.", "collection", cid, mids))

    # Genre + årtionde
    by_gd = {}
    for mid, r in movies.items():
        if not r["year"] or (r["vote_count"] or 0) < 200:
            continue
        decade = r["year"] // 10 * 10
        for g in db.jl(r["genres"]):
            by_gd.setdefault((g, decade), []).append(mid)
    for (g, decade), mids in sorted(by_gd.items()):
        if decade < 1950 or len(mids) < 5:
            continue
        mids = sorted(mids, key=lambda m: -(movies[m]["vote_average"] or 0))[:50]
        lists.append((f"Best {g.lower()} movies of the {decade}s",
                      f"Top-rated {g.lower()} films from {decade}–{decade + 9}.", "genre-decade", decade, mids))

    # Skriv allt i en transaktion så att sidan ser gamla listor tills de nya är klara
    rows, links = [], []
    for i, (name, desc, kind, src, mids) in enumerate(lists, start=1):
        rows.append((i, name, desc, kind, src, sum(movies[m]["popularity"] or 0 for m in mids[:20])))
        links += [(i, m, rank) for rank, m in enumerate(mids, start=1)]
    with conn:
        conn.execute("DELETE FROM list_movies")
        conn.execute("DELETE FROM lists")
        conn.executemany("INSERT INTO lists(id,name,description,kind,source_id,popularity) VALUES(?,?,?,?,?,?)", rows)
        conn.executemany("INSERT INTO list_movies(list_id,movie_id,rank) VALUES(?,?,?)", links)
    log.info("Klart: %d listor", len(rows))


def build_lists_standalone():
    db.init()
    build_lists(db.connect())


async def run(limit, refresh=False):
    if not configured():
        log.error("TMDB_TOKEN eller TMDB_API_KEY saknas – ingen import görs.")
        return
    db.init()
    conn = db.connect()
    t = Tmdb()
    db.set_meta("import_status", "running")
    try:
        log.info("Hämtar lista över filmer från TMDB (max %d) ...", limit)
        ids = await discover_ids(t, limit)
        existing = {r[0] for r in conn.execute("SELECT id FROM movies")}
        todo = ids if refresh else [i for i in ids if i not in existing]
        log.info("%d filmer att hämta (%d finns redan)", len(todo), len(ids) - len(todo))
        done = 0
        batch = 200
        for start in range(0, len(todo), batch):
            chunk = todo[start:start + batch]
            results = await asyncio.gather(*(fetch_movie(t, i) for i in chunk))
            for m in results:
                if m and not m.get("adult"):
                    save_movie(conn, m)
            conn.commit()
            done += len(chunk)
            db.set_meta("import_progress", f"{done}/{len(todo)}")
            log.info("Importerat %d/%d", done, len(todo))
        build_lists(conn)
        db.set_meta("import_status", "done")
        db.set_meta("import_finished", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"))
    finally:
        await t.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=MAX_MOVIES)
    ap.add_argument("--refresh", action="store_true", help="hämta om filmer som redan finns")
    ap.add_argument("--lists-only", action="store_true", help="bygg bara om listorna")
    a = ap.parse_args()
    if a.lists_only:
        db.init()
        build_lists(db.connect())
        return
    asyncio.run(run(a.max, a.refresh))


if __name__ == "__main__":
    main()
