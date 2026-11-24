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


def build_lists(conn):
    """Skapar listor av TMDB-samlingar, nyckelord och genre+årtionde."""
    log.info("Bygger listor ...")
    conn.execute("DELETE FROM list_movies")
    conn.execute("DELETE FROM lists")
    next_id = [1]

    def add_list(name, description, kind, source_id, movie_rows):
        if len(movie_rows) < 5:
            return
        lid = next_id[0]
        next_id[0] += 1
        pop = sum(r["popularity"] or 0 for r in movie_rows[:20])
        conn.execute("INSERT INTO lists(id,name,description,kind,source_id,popularity) VALUES(?,?,?,?,?,?)",
                     (lid, name, description, kind, source_id, pop))
        conn.executemany("INSERT INTO list_movies(list_id,movie_id,rank) VALUES(?,?,?)",
                         [(lid, r["id"], i + 1) for i, r in enumerate(movie_rows)])

    # Nyckelord med många filmer -> "Movies about X"
    kws = conn.execute("""SELECT k.id, k.name, COUNT(*) n FROM keywords k JOIN movie_keywords mk ON mk.keyword_id=k.id
        GROUP BY k.id HAVING n >= 8 ORDER BY n DESC LIMIT 3000""").fetchall()
    skip = {"woman director", "duringcreditsstinger", "aftercreditsstinger", "independent film", "short film",
            "based on novel or book", "live action remake", "lgbt"}
    for k in kws:
        if k["name"] in skip:
            continue
        rows = conn.execute("""SELECT m.id, m.popularity FROM movies m JOIN movie_keywords mk ON mk.movie_id=m.id
            WHERE mk.keyword_id=? ORDER BY (COALESCE(m.vote_average,0)*LOG(COALESCE(m.vote_count,1)+1)) DESC LIMIT 200""",
                            (k["id"],)).fetchall()
        name = k["name"].strip()
        add_list(f"Best movies about {name}", f"Highly rated films tagged “{name}”.", "keyword", k["id"], rows)

    # Samlingar (franchiser)
    colls = conn.execute("""SELECT collection_id, collection_name, COUNT(*) n FROM movies WHERE collection_id IS NOT NULL
        GROUP BY collection_id HAVING n >= 3""").fetchall()
    for c in colls:
        rows = conn.execute("SELECT id, popularity FROM movies WHERE collection_id=? ORDER BY release_date",
                            (c["collection_id"],)).fetchall()
        if len(rows) >= 3:
            lid = next_id[0]
            next_id[0] += 1
            conn.execute("INSERT INTO lists(id,name,description,kind,source_id,popularity) VALUES(?,?,?,?,?,?)",
                         (lid, c["collection_name"], "Every film in the series, in release order.", "collection",
                          c["collection_id"], sum(r["popularity"] or 0 for r in rows)))
            conn.executemany("INSERT INTO list_movies(list_id,movie_id,rank) VALUES(?,?,?)",
                             [(lid, r["id"], i + 1) for i, r in enumerate(rows)])

    # Genre + årtionde
    genres = set()
    for (g,) in conn.execute("SELECT genres FROM movies"):
        genres.update(db.jl(g))
    for g in sorted(genres):
        for decade in range(1950, 2030, 10):
            rows = conn.execute("""SELECT m.id, m.popularity FROM movies m, json_each(m.genres) j
                WHERE j.value=? AND m.year BETWEEN ? AND ? AND m.vote_count >= 200
                ORDER BY m.vote_average DESC LIMIT 50""", (g, decade, decade + 9)).fetchall()
            add_list(f"Best {g.lower()} movies of the {decade}s", f"Top-rated {g.lower()} films from {decade}–{decade + 9}.",
                     "genre-decade", decade, rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    log.info("Klart: %d listor", n)


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
