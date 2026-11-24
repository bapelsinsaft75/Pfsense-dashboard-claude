"""SQLite-databas för den fristående Film Dilemma-kopian."""
import json
import os
import sqlite3
import threading

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "filmdilemma.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY,            -- TMDB-id
    title TEXT NOT NULL,
    original_title TEXT,
    original_language TEXT,
    year INTEGER,
    release_date TEXT,
    runtime INTEGER,
    overview TEXT,
    tagline TEXT,
    poster_path TEXT,
    backdrop_path TEXT,
    vote_average REAL,
    vote_count INTEGER,
    popularity REAL,
    imdb_id TEXT,
    genres TEXT,          -- JSON-lista med namn
    keywords TEXT,        -- JSON-lista med {id,name}
    languages TEXT,       -- JSON-lista med iso-koder
    countries TEXT,       -- JSON-lista med iso-koder
    directors TEXT,       -- JSON-lista med namn
    writers TEXT,
    composers TEXT,
    cinematographer TEXT,
    cast_json TEXT,       -- JSON-lista med {id,name,character,profile_path,order}
    trailer_key TEXT,
    collection_id INTEGER,
    collection_name TEXT,
    providers TEXT,       -- JSON {region: {link, flatrate:[..], rent:[..], buy:[..]}}
    budget INTEGER,
    revenue INTEGER,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_movies_pop ON movies(popularity DESC);
CREATE INDEX IF NOT EXISTS ix_movies_year ON movies(year);
CREATE INDEX IF NOT EXISTS ix_movies_vote ON movies(vote_average);
CREATE INDEX IF NOT EXISTS ix_movies_coll ON movies(collection_id);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    profile_path TEXT
);
CREATE INDEX IF NOT EXISTS ix_people_name ON people(name);

CREATE TABLE IF NOT EXISTS movie_people (
    movie_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    role TEXT NOT NULL,          -- actor/director/writer/composer/cinematographer
    ord INTEGER,
    character TEXT,
    PRIMARY KEY (movie_id, person_id, role)
);
CREATE INDEX IF NOT EXISTS ix_mp_person ON movie_people(person_id, role);
CREATE INDEX IF NOT EXISTS ix_mp_role ON movie_people(role, person_id);

CREATE TABLE IF NOT EXISTS movie_keywords (
    movie_id INTEGER NOT NULL,
    keyword_id INTEGER NOT NULL,
    PRIMARY KEY (movie_id, keyword_id)
);
CREATE INDEX IF NOT EXISTS ix_mk_kw ON movie_keywords(keyword_id);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    kind TEXT,                 -- keyword / collection / genre-decade
    source_id INTEGER,
    popularity REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS list_movies (
    list_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rank INTEGER,
    PRIMARY KEY (list_id, movie_id)
);
CREATE INDEX IF NOT EXISTS ix_lm_movie ON list_movies(movie_id);

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    logo_path TEXT,
    priority INTEGER
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plex_id INTEGER UNIQUE,
    username TEXT NOT NULL,
    email TEXT,
    thumb TEXT,
    is_admin INTEGER DEFAULT 0,
    plex_token TEXT,           -- användarens plex.tv-token (för sett-historik)
    server_token TEXT,         -- användarens token mot din Plex-server
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rating REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS watched (
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    source TEXT DEFAULT 'manual',   -- manual / plex
    watched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS watchlist (
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS plex_library (
    tmdb_id INTEGER PRIMARY KEY,
    rating_key TEXT NOT NULL,
    title TEXT,
    year INTEGER,
    added_at INTEGER
);
CREATE TABLE IF NOT EXISTS plex_sections (
    key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL,          -- movie / show
    item_count INTEGER
);
CREATE TABLE IF NOT EXISTS plex_items (
    section_key TEXT NOT NULL,
    rating_key TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    thumb TEXT,
    art TEXT,
    tmdb_id INTEGER,
    added_at INTEGER,
    episodes INTEGER,
    seasons INTEGER,
    summary TEXT,
    PRIMARY KEY (section_key, rating_key)
);
CREATE TABLE IF NOT EXISTS user_sections (
    user_id INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    PRIMARY KEY (user_id, section_key)
);
CREATE TABLE IF NOT EXISTS user_plex_watched (
    user_id INTEGER NOT NULL,
    rating_key TEXT NOT NULL,
    viewed INTEGER,             -- sedda avsnitt (serier) eller 1 (film)
    total INTEGER,              -- antal avsnitt (serier) eller 1 (film)
    PRIMARY KEY (user_id, rating_key)
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',   -- pending / sent / available / failed
    detail TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, movie_id)
);
CREATE TABLE IF NOT EXISTS tmdb_find (
    ext_key TEXT PRIMARY KEY,     -- t.ex. imdb:tt0133093 eller tvdb:81189
    tmdb_id INTEGER,
    media TEXT                    -- movie / tv / NULL (hittades inte)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_local = threading.local()


def connect():
    """En anslutning per tråd."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=OFF")
        _local.conn = conn
    return conn


MIGRATIONS = [("plex_items", "art", "TEXT"), ("plex_items", "imdb_id", "TEXT"), ("plex_items", "tvdb_id", "TEXT"),
              ("movies", "media_type", "TEXT DEFAULT 'movie'"), ("requests", "media_type", "TEXT DEFAULT 'movie'")]

# Serier lagras i samma tabell som filmer med id = TV_OFFSET + TMDB:s serie-id (så att id:n inte krockar)
TV_OFFSET = 1_000_000_000


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    for table, column, ctype in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
    conn.commit()


def jl(value, default=None):
    """Läs ett JSON-fält."""
    if value is None or value == "":
        return [] if default is None else default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return [] if default is None else default


def get_meta(key, default=None):
    row = connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key, value):
    conn = connect()
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
    conn.commit()
