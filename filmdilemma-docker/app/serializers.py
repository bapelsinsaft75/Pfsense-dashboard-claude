"""Gör om databasrader till de JSON-format som Film Dilemma-frontenden förväntar sig."""
from . import db
from . import config
from .languages import lang_name

IMG = "https://image.tmdb.org/t/p"


def poster_url(path, size="w500"):
    return f"{IMG}/{size}{path}" if path else None


def profile_url(path, size="w185"):
    return f"{IMG}/{size}{path}" if path else None


def duration(minutes):
    if not minutes:
        return None
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if h else f"{m}m"


class UserState:
    """Användarens egna markeringar (betyg, sett, bevakning, Plex) – hämtas en gång per anrop."""

    def __init__(self, user_id=None):
        self.user_id = user_id
        conn = db.connect()
        self.plex = {r["tmdb_id"]: r["rating_key"] for r in conn.execute("SELECT tmdb_id, rating_key FROM plex_library")}
        if user_id:
            self.ratings = {r["movie_id"]: r["rating"] for r in
                            conn.execute("SELECT movie_id, rating FROM ratings WHERE user_id=?", (user_id,))}
            self.watched = {r["movie_id"] for r in conn.execute("SELECT movie_id FROM watched WHERE user_id=?", (user_id,))}
            self.watchlist = {r["movie_id"] for r in
                              conn.execute("SELECT movie_id FROM watchlist WHERE user_id=?", (user_id,))}
            self.requests = {r["movie_id"]: r["status"] for r in
                             conn.execute("SELECT movie_id, status FROM requests WHERE user_id=?", (user_id,))}
        else:
            self.ratings, self.watched, self.watchlist, self.requests = {}, set(), set(), {}


def rating_of(row):
    v = row["vote_average"]
    return round(float(v), 1) if v is not None else None


def movie_card(row, st: UserState = None):
    mid = row["id"]
    d = {
        "id": mid,
        "movie_id": mid,
        "tmdb_id": mid,
        "name": row["title"],
        "title": row["title"],
        "original_title": row["original_title"],
        "original_language": lang_name(row["original_language"]),
        "year": row["year"],
        "release_date": row["release_date"],
        "poster_url": poster_url(row["poster_path"]),
        "poster_path": row["poster_path"],
        "backdrop_url": poster_url(row["backdrop_path"], "w1280"),
        "rating": rating_of(row),
        "avg_rating": rating_of(row),
        "vote_count": row["vote_count"],
        "popularity": row["popularity"],
        "duration": duration(row["runtime"]),
        "runtime": row["runtime"],
        "genres": db.jl(row["genres"]),
        "directors": db.jl(row["directors"]) if "directors" in row.keys() else [],
        "imdb_id": row["imdb_id"],
        "media_type": (row["media_type"] if "media_type" in row.keys() else None) or "movie",
    }
    if st is not None:
        d["my_rating"] = st.ratings.get(mid)
        d["in_watchlist"] = mid in st.watchlist
        d["watched"] = mid in st.watched
        d["is_watched"] = mid in st.watched
        d["on_plex"] = mid in st.plex
    return d


def movie_detail(row, st: UserState):
    d = movie_card(row, st)
    cast = db.jl(row["cast_json"])
    kws = db.jl(row["keywords"])
    conn = db.connect()
    franchise = []
    if row["collection_id"]:
        franchise = [movie_card(r, st) for r in conn.execute(
            "SELECT * FROM movies WHERE collection_id=? ORDER BY release_date", (row["collection_id"],))]
    d.update({
        "plot": row["overview"],
        "premise": row["overview"],
        "description": row["overview"],
        "tagline": row["tagline"],
        "sub_genres": [],
        "moods": [],
        "styles": [],
        "themes": [k["name"] for k in kws[:12]],
        "keywords": [k["name"] for k in kws],
        "keywords_total": len(kws),
        "languages": [lang_name(x) for x in db.jl(row["languages"])],
        "countries": db.jl(row["countries"]),
        "directors": db.jl(row["directors"]),
        "writers": db.jl(row["writers"]),
        "composers": db.jl(row["composers"]),
        "cinematographer": row["cinematographer"],
        "actors": [c["name"] for c in cast],
        "cast": [{"name": c["name"], "actor_name": c["name"], "character_name": c.get("character"),
                  "character": c.get("character"), "photo_url": profile_url(c.get("profile_path")),
                  "image_url": profile_url(c.get("profile_path")), "actor_billing": c.get("order"), "id": c["id"]}
                 for c in cast],
        "trailer_youtube_key": row["trailer_key"] or "no_trailer",
        "franchise_id": row["collection_id"],
        "franchise_name": row["collection_name"],
        "franchise_films": franchise if len(franchise) > 1 else [],
        "franchise_position": next((i + 1 for i, f in enumerate(franchise) if f["id"] == row["id"]), None),
        "safe_backdrops": [poster_url(row["backdrop_path"], "w1280")] if row["backdrop_path"] else [],
        "box_office": row["revenue"] or None,
        "budget": row["budget"] or None,
        "spoiler_mode": "hide",
        "rating_thin": (row["vote_count"] or 0) < 50,
        "reviews_available": False,
        "has_partner_reviews": False,
        "similar_section_disabled": False,
        "similar_data_insufficient": False,
        "awards": [],
        "book_links": [],
        "music_affiliate": None,
        "founder_note": None,
        "founder_score": None,
        "crew_photos": crew_photos(row),
        "plex": plex_info(row["id"], st),
    })
    return d


def crew_photos(row):
    conn = db.connect()
    out = {}
    for role, key in (("director", "directors"), ("writer", "writers"), ("composer", "composers"),
                      ("cinematographer", "cinematographer")):
        rows = conn.execute("""SELECT p.name, p.profile_path FROM movie_people mp JOIN people p ON p.id=mp.person_id
            WHERE mp.movie_id=? AND mp.role=? ORDER BY mp.ord""", (row["id"], role)).fetchall()
        out[key] = {r["name"]: profile_url(r["profile_path"]) for r in rows if r["profile_path"]}
    return out


def plex_info(movie_id, st: UserState):
    rk = st.plex.get(movie_id)
    return {
        "available": rk is not None,
        "url": config.plex_web_url(rk) if rk else None,
        "requested": st.requests.get(movie_id),
    }


def list_summary(row, previews=None):
    return {
        "id": row["id"],
        "list_id": row["id"],
        "name": row["name"],
        "final_name": row["name"],
        "description": row["description"],
        "movie_count": row["movie_count"] if "movie_count" in row.keys() else None,
        "list_kind": "curated",
        "kind": row["kind"],
        "views_count": int(row["popularity"] or 0),
        "for_poster_previews": previews or [],
        "is_public": True,
    }


def person(row, role):
    return {
        "id": row["id"],
        "name": row["name"],
        "person_name": row["name"],
        "person_role": role,
        "photo_url": profile_url(row["profile_path"]),
        "image_url": profile_url(row["profile_path"]),
        "movie_count": row["movie_count"] if "movie_count" in row.keys() else None,
    }
