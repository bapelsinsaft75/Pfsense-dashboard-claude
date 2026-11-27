"""Inställningar som läses från miljövariabler."""
import os
import secrets

from . import db


def env(name, default=""):
    return os.environ.get(name, default).strip()


PUBLIC_URL = env("PUBLIC_URL", "http://192.168.2.50").rstrip("/")
WATCH_REGION = env("WATCH_REGION", "SE").upper()
REQUIRE_LOGIN = True   # sidan kräver alltid Plex-inloggning (första man ser är "Logga in via Plex")

PLEX_URL = env("PLEX_URL").rstrip("/")
PLEX_TOKEN = env("PLEX_TOKEN")
PLEX_SYNC_MINUTES = int(env("PLEX_SYNC_MINUTES", "30") or 30)              # sett-historik och Seerr-status
PLEX_LIBRARY_SYNC_HOURS = float(env("PLEX_LIBRARY_SYNC_HOURS", "12") or 12)  # biblioteken (filmer/serier i Plex)

# Seerr (seerr.dev) – efterföljaren till Overseerr/Jellyseerr, samma API. OVERSEERR_* fungerar som alias.
SEERR_URL = (env("SEERR_URL") or env("OVERSEERR_URL")).rstrip("/")
SEERR_API_KEY = env("SEERR_API_KEY") or env("OVERSEERR_API_KEY")
OVERSEERR_URL, OVERSEERR_API_KEY = SEERR_URL, SEERR_API_KEY
RADARR_URL = env("RADARR_URL").rstrip("/")
RADARR_API_KEY = env("RADARR_API_KEY")
RADARR_ROOT_FOLDER = env("RADARR_ROOT_FOLDER")
RADARR_QUALITY_PROFILE = env("RADARR_QUALITY_PROFILE")
OMDB_API_KEY = env("OMDB_API_KEY")
# Hur "Request movie/show" fungerar:
#   watchlist (standard) – lägg titeln på den inloggades Plex-watchlist; Seerr:s "Plex Watchlist Sync" önskar den
#   seerr               – skicka önskan direkt till Seerr (eller Radarr) via API
REQUEST_MODE = (env("REQUEST_MODE", "watchlist") or "watchlist").lower()
PLEX_DISCOVER_URL = env("PLEX_DISCOVER_URL", "https://discover.provider.plex.tv").rstrip("/")
PLEX_METADATA_URL = env("PLEX_METADATA_URL", "https://metadata.provider.plex.tv").rstrip("/")       # valfri, för IMDb-betyg när Plex saknar dem


def secret_key():
    key = env("SECRET_KEY")
    if key:
        return key
    key = db.get_meta("secret_key")
    if not key:
        key = secrets.token_urlsafe(48)
        db.set_meta("secret_key", key)
    return key


def plex_client_id():
    cid = db.get_meta("plex_client_id")
    if not cid:
        cid = "filmdilemma-local-" + secrets.token_hex(8)
        db.set_meta("plex_client_id", cid)
    return cid


def plex_machine_id():
    return db.get_meta("plex_machine_id")


def plex_web_url(rating_key):
    mid = plex_machine_id()
    if not mid or not rating_key:
        return None
    return (f"https://app.plex.tv/desktop/#!/server/{mid}/details?key=%2Flibrary%2Fmetadata%2F{rating_key}")


def request_backend():
    if SEERR_URL and SEERR_API_KEY:
        return "seerr"
    if RADARR_URL and RADARR_API_KEY:
        return "radarr"
    return "list"
