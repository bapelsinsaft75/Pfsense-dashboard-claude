"""Filmförslag (originalets "Dilemma solver"): föreslår filmer utifrån känsla, era, språk m.m.

Cookien fd_dilemma_scope väljer var den letar:
  plex – bara titlar i ditt Plex-bibliotek (lokal databas)
  tmdb – bara TMDB, frågas direkt mot TMDB:s API (hela katalogen, titlar i Plex utesluts)
  all  – båda: förslag från Plex och från TMDB blandas
"""
import random

from . import db, tmdb_import
from .serializers import UserState, movie_card

# Känsla -> (genrer där minst en ska finnas, genrer som inte får finnas, nyckelord som ger extra poäng)
# (TMDB:s seriegenrer heter delvis annorlunda: "Action & Adventure", "Sci-Fi & Fantasy", "War & Politics", "Kids")
FEELINGS = {
    "light-funny": ({"Comedy"}, {"Horror", "War", "War & Politics"}, set()),
    "tense": ({"Thriller", "Action", "Crime", "Action & Adventure", "Mystery"}, {"Comedy", "Family", "Animation", "Kids"}, set()),
    "dark": ({"Crime", "Thriller", "War", "Drama", "War & Politics"}, {"Comedy", "Family", "Animation", "Romance", "Kids"},
             {"neo-noir", "violence", "revenge", "serial killer", "gangster", "dystopia", "corruption"}),
    "moving": ({"Drama", "Romance"}, {"Horror", "Comedy", "Action"},
               {"grief", "loss of loved one", "terminal illness", "tragedy", "friendship", "coming of age"}),
    "mind-bending": ({"Science Fiction", "Mystery", "Thriller", "Sci-Fi & Fantasy"}, {"Family", "Comedy", "Kids"},
                     {"time travel", "twist ending", "dream", "parallel world", "alternate reality",
                      "memory", "artificial intelligence (a.i.)", "simulated reality", "time loop", "nonlinear timeline"}),
    "scary": ({"Horror"}, {"Comedy", "Family", "Animation", "Kids"}, set()),
    "comforting": ({"Family", "Animation", "Comedy", "Romance", "Kids"},
                   {"Horror", "Thriller", "War", "Crime", "Action", "War & Politics"}, set()),
}
NEVER = ("News", "Talk", "Reality", "Soap")


def scope_from_request(request):
    v = (request.cookies.get("fd_dilemma_scope") or "").lower()
    if v in ("plex", "tmdb", "all"):
        return v
    # Standard: Plex om biblioteket är synkat, annars hela katalogen
    has_plex = db.connect().execute("SELECT 1 FROM plex_library LIMIT 1").fetchone()
    return "plex" if has_plex else "all"


def finalists(params, user_id, scope):
    """Förslag ur den lokala databasen (används för Plex-läget och för egna listor)."""
    conn = db.connect()
    where, args = [], []
    plex_only = scope == "plex"
    where.append("NOT EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value IN ('News','Talk','Reality','Soap'))")
    if plex_only:
        where.append("m.id IN (SELECT tmdb_id FROM plex_library)")
    else:
        where.append("""((COALESCE(m.media_type,'movie')='movie' AND m.vote_count >= 150 AND COALESCE(m.runtime, 90) >= 60)
                        OR m.id IN (SELECT tmdb_id FROM plex_library))""")

    source = params.get("source")
    if source == "watchlist" and user_id:
        where.append("m.id IN (SELECT movie_id FROM watchlist WHERE user_id=%d)" % user_id)
    elif source == "rewatch" and user_id:
        where.append("m.id IN (SELECT movie_id FROM ratings WHERE user_id=%d AND rating >= 8)" % user_id)

    anchor = params.get("anchor")
    anchor_ids = None
    if anchor and str(anchor).isdigit():
        from .main import similar_rows  # undvik cirkulär import vid start
        anchor_ids = [r["id"] for r in similar_rows(int(anchor), 200)]
        if not anchor_ids:
            return {"finalists": [], "total": 0}
        where.append("m.id IN (%s)" % ",".join(map(str, anchor_ids)))

    feeling = params.get("feeling") or "surprise"
    boost_kw = set()
    if feeling in FEELINGS and not source and not anchor_ids:
        want, avoid, boost_kw = FEELINGS[feeling]
        where.append("EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value IN (%s))" % ",".join("?" * len(want)))
        args += sorted(want)
        where.append("NOT EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value IN (%s))"
                     % ",".join("?" * len(avoid)))
        args += sorted(avoid)

    if params.get("year_min") and params.get("year_max"):
        where.append("m.year BETWEEN ? AND ?")
        args += [int(params["year_min"]), int(params["year_max"])]
    if str(params.get("not_english", "")).lower() in ("1", "true"):
        where.append("m.original_language != 'en'")
    max_rt = params.get("max_runtime")
    if max_rt and str(max_rt).isdigit():
        where.append("COALESCE(m.runtime, 0) <= ?")
        args.append(int(max_rt))
    w = (params.get("with") or "").lower()
    if w in ("kids", "family", "children"):
        where.append("EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value IN ('Family','Animation'))")

    providers = []   # tjänstväljaren är borttagen; filtrera aldrig på streamingtjänst
    if providers and not plex_only:
        region = (params.get("region") or "SE").upper().replace("'", "")
        where.append("""EXISTS (SELECT 1 FROM json_each(json_extract(m.providers, '$.%s.flatrate')) p
            WHERE p.value IN (%s))""" % (region, ",".join(providers)))

    exclude = [x for x in str(params.get("exclude") or "").split(",") if x.strip().isdigit()]
    if exclude:
        where.append("m.id NOT IN (%s)" % ",".join(exclude))

    base_where = " AND ".join(where)
    # Hoppa över filmer användaren redan sett (Plex-historik/markeringar) när det finns annat att välja
    unseen_where = base_where
    if user_id and source != "rewatch":
        unseen_where += " AND m.id NOT IN (SELECT movie_id FROM watched WHERE user_id=%d)" % user_id

    n = max(1, min(10, int(params.get("n") or 3)))
    for wsql in (unseen_where, base_where):
        total = conn.execute(f"SELECT COUNT(*) FROM movies m WHERE {wsql}", args).fetchone()[0]
        if total:
            break
    if not total:
        return {"finalists": [], "total": 0}

    # Slumpa bland de bättre filmerna: viktat på betyg och (för känslan) matchande nyckelord
    kw_sql, kw_args = "0", []
    if boost_kw:
        kw_sql = """(SELECT COUNT(*) FROM movie_keywords mk JOIN keywords k ON k.id=mk.keyword_id
                     WHERE mk.movie_id=m.id AND k.name IN (%s))""" % ",".join("?" * len(boost_kw))
        kw_args = sorted(boost_kw)
    rows = conn.execute(f"""SELECT m.* FROM movies m WHERE {wsql}
        ORDER BY (COALESCE(m.vote_average, 6) + {kw_sql} * 1.5) * (0.55 + (ABS(RANDOM()) % 1000) / 1000.0) DESC
        LIMIT ?""", args + kw_args + [n]).fetchall()
    st = UserState(user_id)
    out = []
    for r in rows:
        card = movie_card(r, st)
        card["providers"] = []
        out.append(card)
    return {"finalists": out, "total": total, "scope": scope}


# ---------------------------------------------------------------- direkt mot TMDB

TMDB_GENRES = {"Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35, "Crime": 80, "Documentary": 99,
               "Drama": 18, "Family": 10751, "Fantasy": 14, "History": 36, "Horror": 27, "Music": 10402,
               "Mystery": 9648, "Romance": 10749, "Science Fiction": 878, "TV Movie": 10770, "Thriller": 53,
               "War": 10752, "Western": 37}


def _ids(names):
    return sorted({TMDB_GENRES[n] for n in names if n in TMDB_GENRES})


async def tmdb_candidates(params, user_id, n):
    """Frågar TMDB direkt (discover/recommendations) och returnerar lokala film-id:n (importerar vid behov)."""
    if not tmdb_import.configured():
        return [], 0
    conn = db.connect()
    skip = {r[0] for r in conn.execute("SELECT tmdb_id FROM plex_library")}
    if user_id:
        skip |= {r[0] for r in conn.execute("SELECT movie_id FROM watched WHERE user_id=?", (user_id,))}
    skip |= {int(x) for x in str(params.get("exclude") or "").split(",") if x.strip().isdigit()}
    not_en = str(params.get("not_english", "")).lower() in ("1", "true")
    max_rt = params.get("max_runtime")

    t = tmdb_import.Tmdb()
    try:
        anchor = params.get("anchor")
        if anchor and str(anchor).isdigit() and int(anchor) < db.TV_OFFSET:
            d = await t.get(f"/movie/{int(anchor)}/recommendations", language=tmdb_import.LANGUAGE,
                            page=random.randint(1, 2))
            results, total = (d or {}).get("results", []), (d or {}).get("total_results", 0)
        else:
            q = {"language": tmdb_import.LANGUAGE, "include_adult": "false", "include_video": "false",
                 "vote_count.gte": 80, "sort_by": random.choice(["popularity.desc", "vote_average.desc",
                                                                 "vote_count.desc"]), "with_runtime.gte": 60}
            feeling = params.get("feeling") or "surprise"
            if feeling in FEELINGS:
                want, avoid, _ = FEELINGS[feeling]
                q["with_genres"] = "|".join(map(str, _ids(want)))
                q["without_genres"] = ",".join(map(str, _ids(avoid)))
            if params.get("year_min") and params.get("year_max"):
                q["primary_release_date.gte"] = f"{int(params['year_min'])}-01-01"
                q["primary_release_date.lte"] = f"{int(params['year_max'])}-12-31"
            if max_rt and str(max_rt).isdigit():
                q["with_runtime.lte"] = int(max_rt)
            if str(params.get("with") or "").lower() in ("kids", "family", "children"):
                q["with_genres"] = "10751|16"
            if not_en:
                q["vote_count.gte"] = 40
            first = await t.get("/discover/movie", page=1, **q)
            total = (first or {}).get("total_results", 0)
            pages = min(20, (first or {}).get("total_pages", 1))
            page = random.randint(1, max(1, pages))
            d = first if page == 1 else await t.get("/discover/movie", page=page, **q)
            results = (d or {}).get("results", [])
        results = [m for m in results if m["id"] not in skip and not m.get("adult")
                   and not (not_en and m.get("original_language") == "en")]
        random.shuffle(results)
        picked = results[:n]
        have = {r[0] for r in conn.execute("SELECT id FROM movies WHERE id IN (%s)"
                                           % ",".join(str(m["id"]) for m in picked))} if picked else set()
        for m in picked:
            if m["id"] not in have:
                full = await tmdb_import.fetch_movie(t, m["id"])
                if full:
                    tmdb_import.save_movie(conn, full)
        conn.commit()
    finally:
        await t.close()
    return [m["id"] for m in picked], total


async def finalists_any(params, user_id, scope):
    """Väljer källa: plex (lokalt), tmdb (direkt mot TMDB) eller all (blandat)."""
    n = max(1, min(10, int(params.get("n") or 3)))
    if params.get("source") or scope == "plex":
        return finalists(params, user_id, "plex" if scope == "plex" else "all")
    ids, total = await tmdb_candidates(params, user_id, n)
    if scope == "all":
        local = finalists(params, user_id, "plex")
        mix = local["finalists"] + []
        conn = db.connect()
        st = UserState(user_id)
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM movies WHERE id IN (%s)"
                                                 % ",".join(map(str, ids)))} if ids else {}
        tm = [dict(movie_card(rows[i], st), providers=[]) for i in ids if i in rows]
        out = []
        while (mix or tm) and len(out) < n:
            if mix and (not tm or random.random() < 0.5):
                out.append(mix.pop(0))
            else:
                out.append(tm.pop(0))
        return {"finalists": out, "total": local["total"] + total, "scope": scope}
    conn = db.connect()
    st = UserState(user_id)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM movies WHERE id IN (%s)"
                                             % ",".join(map(str, ids)))} if ids else {}
    return {"finalists": [dict(movie_card(rows[i], st), providers=[]) for i in ids if i in rows],
            "total": total, "scope": scope}
