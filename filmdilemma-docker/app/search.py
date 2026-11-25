"""Filtrering och sortering av filmer (motsvarar /api/v1/movies/advanced-search/)."""
import math

from . import db
from .languages import lang_code
from .serializers import UserState, movie_card

SORTS = {
    "popularity": "m.popularity",
    "rating": "m.vote_average",
    "avg_rating": "m.vote_average",
    "imdb_rating": "m.vote_average",
    "year": "COALESCE(m.release_date, '')",
    "release_date": "COALESCE(m.release_date, '')",
    "name": "m.title COLLATE NOCASE",
    "title": "m.title COLLATE NOCASE",
    "votes": "m.vote_count",
    "vote_count": "m.vote_count",
    "runtime": "m.runtime",
    "duration": "m.runtime",
    "random": "RANDOM()",
    "added": "m.updated_at",
}


def _multi(params, *names):
    out = []
    for n in names:
        for v in params.getlist(n) if hasattr(params, "getlist") else ([params[n]] if n in params else []):
            if v is None or v == "":
                continue
            if isinstance(v, list):
                out.extend(str(x) for x in v if x not in (None, ""))
            else:
                out.extend(x.strip() for x in str(v).split(",") if x.strip())
    return out


def _num(params, name, cast=float):
    v = params.get(name)
    if v in (None, "", "null", "undefined"):
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def _truthy(params, name):
    return str(params.get(name, "")).lower() in ("1", "true", "yes", "on")


def search(params, user_id=None):
    where, args, joins = [], [], []

    # TV-shows-sidan visar bara serier; övriga sidor bara filmer (personliga listor visar båda)
    if params.get("media") == "tv":
        where.append("m.media_type = 'tv'")
    elif not params.get("personal_collection") and not _truthy(params, "include_tv"):
        where.append("COALESCE(m.media_type, 'movie') = 'movie'")

    q = (params.get("q") or params.get("query") or params.get("search") or "").strip()
    if q:
        where.append("(m.title LIKE ? OR m.original_title LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]

    genres = _multi(params, "genres", "genre")
    genres_or = _multi(params, "genres_or")
    for g in genres:
        where.append("EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value = ? COLLATE NOCASE)")
        args.append(g)
    if genres_or:
        where.append("EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value COLLATE NOCASE IN (%s))"
                     % ",".join("?" * len(genres_or)))
        args += genres_or
    for g in _multi(params, "exclude_genres"):
        where.append("NOT EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value = ? COLLATE NOCASE)")
        args.append(g)

    themes = _multi(params, "themes", "themes_or", "keywords", "tags")
    if themes:
        where.append("""EXISTS (SELECT 1 FROM movie_keywords mk JOIN keywords k ON k.id=mk.keyword_id
            WHERE mk.movie_id=m.id AND k.name COLLATE NOCASE IN (%s))""" % ",".join("?" * len(themes)))
        args += themes

    for lo, hi, col in (("year_min", "year_max", "m.year"), ("min_year", "max_year", "m.year"),
                        ("min_rating", "max_rating", "m.vote_average"),
                        ("runtime_min", "runtime_max", "m.runtime"), ("duration_min", "duration_max", "m.runtime")):
        a, b = _num(params, lo), _num(params, hi)
        if a is not None and not (col == "m.vote_average" and a <= 0):
            where.append(f"{col} >= ?")
            args.append(a)
        if b is not None and not (col == "m.vote_average" and b >= 10):
            where.append(f"{col} <= ?")
            args.append(b)

    langs = [lang_code(x) for x in _multi(params, "languages", "languages_or", "original_language", "language")]
    if langs:
        where.append("m.original_language IN (%s)" % ",".join("?" * len(langs)))
        args += langs

    for role, names in (("actor", _multi(params, "actors", "actor", "cast")),
                        ("director", _multi(params, "directors", "director")),
                        ("writer", _multi(params, "writers", "writer")),
                        ("composer", _multi(params, "composers", "composer")),
                        ("cinematographer", _multi(params, "cinematographers", "cinematographer"))):
        for n in names:
            where.append("""EXISTS (SELECT 1 FROM movie_people mp JOIN people p ON p.id=mp.person_id
                WHERE mp.movie_id=m.id AND mp.role=? AND p.name = ? COLLATE NOCASE)""")
            args += [role, n]
    for n in _multi(params, "exclude_actors"):
        where.append("""NOT EXISTS (SELECT 1 FROM movie_people mp JOIN people p ON p.id=mp.person_id
            WHERE mp.movie_id=m.id AND mp.role='actor' AND p.name = ? COLLATE NOCASE)""")
        args.append(n)

    person_name, person_role = params.get("person_name"), params.get("person_role")
    if person_name:
        where.append("""EXISTS (SELECT 1 FROM movie_people mp JOIN people p ON p.id=mp.person_id
            WHERE mp.movie_id=m.id AND p.name = ? COLLATE NOCASE %s)""" % ("AND mp.role=?" if person_role else ""))
        args.append(person_name)
        if person_role:
            args.append(person_role)

    list_id = _num(params, "list_id", int)
    if list_id:
        joins.append("JOIN list_movies lm ON lm.movie_id=m.id AND lm.list_id=%d" % list_id)

    # Bevakningslista, betygsatta och sedda filmer (sidorna /watchlist, /rated, /watched)
    pcol = {"watchlist": ("watchlist", "added_at"), "rated": ("ratings", "created_at"),
            "watched": ("watched", "watched_at")}.get(str(params.get("personal_collection") or ""))
    if pcol:
        if not user_id:
            where.append("0")
        else:
            joins.append("JOIN %s pc ON pc.movie_id=m.id AND pc.user_id=%d" % (pcol[0], user_id))

    providers = [p for p in _multi(params, "watch_provider_ids", "providers") if p.isdigit()]
    region = (params.get("watch_region") or "SE").upper()
    if providers:
        where.append("""EXISTS (SELECT 1 FROM json_each(json_extract(m.providers, '$.%s.flatrate')) p
            WHERE p.value IN (%s))""" % (region.replace("'", ""), ",".join("?" * len(providers))))
        args += [int(p) for p in providers]

    if _truthy(params, "cc"):   # "Cinephile Classic"
        where.append("m.vote_average >= 7.6 AND m.vote_count >= 1500")
    if _truthy(params, "exclude_documentaries"):
        where.append("NOT EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value='Documentary')")
    if _truthy(params, "exclude_animation"):
        where.append("NOT EXISTS (SELECT 1 FROM json_each(m.genres) j WHERE j.value='Animation')")
    if _truthy(params, "exclude_shorts"):
        where.append("COALESCE(m.runtime, 90) >= 40")
    if _truthy(params, "on_plex") or _truthy(params, "plex"):
        where.append("m.id IN (SELECT tmdb_id FROM plex_library)")
    if user_id:
        if _truthy(params, "exclude_watched"):
            where.append("m.id NOT IN (SELECT movie_id FROM watched WHERE user_id=%d)" % user_id)
        if _truthy(params, "exclude_watchlist"):
            where.append("m.id NOT IN (SELECT movie_id FROM watchlist WHERE user_id=%d)" % user_id)
        if _truthy(params, "exclude_rated"):
            where.append("m.id NOT IN (SELECT movie_id FROM ratings WHERE user_id=%d)" % user_id)
    if params.get("rating_mode") == "rated":
        where.append("m.vote_count >= 50")

    exclude_ids = [int(x) for x in _multi(params, "exclude_ids", "exclude_movie_ids") if x.isdigit()]
    if exclude_ids:
        where.append("m.id NOT IN (%s)" % ",".join(map(str, exclude_ids)))

    sort_by = params.get("sort_by") or ("rank" if list_id else "popularity")
    order = "ASC" if str(params.get("sort_order", "desc")).lower() == "asc" else "DESC"
    if sort_by in ("added_at", "rated_at", "watched_at", "date_added") and pcol and user_id:
        order_sql = f"pc.{pcol[1]} {order}"
    elif sort_by in ("my_rating",) and pcol and pcol[0] == "ratings" and user_id:
        order_sql = f"pc.rating {order}"
    elif sort_by in ("rank", "list_order") and list_id:
        order_sql = f"lm.rank {'DESC' if order == 'DESC' and sort_by != 'rank' else 'ASC'}"
    elif sort_by == "rating":
        # Viktat betyg så att filmer med få röster inte hamnar överst
        order_sql = f"(m.vote_average * m.vote_count + 6.5 * 300) / (m.vote_count + 300) {order}"
    else:
        order_sql = f"{SORTS.get(sort_by, 'm.popularity')} {order}"

    page = max(1, _num(params, "page", int) or 1)
    page_size = min(200, max(1, _num(params, "page_size", int) or _num(params, "limit", int) or 24))

    sql_from = "FROM movies m " + " ".join(joins)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    conn = db.connect()
    total = conn.execute(f"SELECT COUNT(*) {sql_from} {sql_where}", args).fetchone()[0]
    rows = conn.execute(f"SELECT m.* {sql_from} {sql_where} ORDER BY {order_sql}, m.id LIMIT ? OFFSET ?",
                        args + [page_size, (page - 1) * page_size]).fetchall()

    st = UserState(user_id)
    movies = [movie_card(r, st) for r in rows]
    breakdown = []
    if not _truthy(params, "light") and total:
        gb = conn.execute(f"""SELECT j.value g, COUNT(*) n {sql_from}, json_each(m.genres) j {sql_where}
            GROUP BY j.value ORDER BY n DESC""", args).fetchall()
        breakdown = [{"genre": r["g"], "count": r["n"], "pct": round(100 * r["n"] / total, 1)} for r in gb]
    return {
        "movies": movies,
        "items": movies,
        "results": movies,
        "total": total,
        "total_count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total / page_size)),
        "genre_breakdown": breakdown,
    }
