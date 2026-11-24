"""Enkla JWT-tokens (HS256) för inloggning. Konton skapas bara via Plex."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config, db

ACCESS_TTL = 60 * 60            # 1 timme
REFRESH_TTL = 60 * 60 * 24 * 60  # 60 dagar


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_access(user_id):
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64(json.dumps({"sub": str(user_id), "iat": now, "exp": now + ACCESS_TTL}).encode())
    sig = hmac.new(config.secret_key().encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def verify_access(token):
    try:
        header, payload, sig = token.split(".")
        expected = hmac.new(config.secret_key().encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        data = json.loads(_unb64(payload))
        if int(data["exp"]) < time.time():
            return None
        return int(data["sub"])
    except (ValueError, KeyError, TypeError):
        return None


def issue_tokens(user_id):
    refresh = secrets.token_urlsafe(40)
    conn = db.connect()
    conn.execute("INSERT INTO refresh_tokens(token,user_id,expires) VALUES(?,?,?)",
                 (refresh, user_id, int(time.time()) + REFRESH_TTL))
    conn.execute("DELETE FROM refresh_tokens WHERE expires < ?", (int(time.time()),))
    conn.commit()
    return {"access_token": make_access(user_id), "refresh_token": refresh, "token_type": "bearer"}


def refresh(token):
    conn = db.connect()
    row = conn.execute("SELECT user_id, expires FROM refresh_tokens WHERE token=?", (token,)).fetchone()
    if not row or row["expires"] < time.time():
        return None
    conn.execute("DELETE FROM refresh_tokens WHERE token=?", (token,))
    conn.commit()
    return issue_tokens(row["user_id"])


def revoke(token):
    conn = db.connect()
    conn.execute("DELETE FROM refresh_tokens WHERE token=?", (token,))
    conn.commit()


def user_from_request(request):
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        uid = verify_access(h[7:].strip())
        if uid:
            row = db.connect().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            return row
    return None


def user_json(row):
    if not row:
        return None
    conn = db.connect()
    rated = conn.execute("SELECT COUNT(*) FROM ratings WHERE user_id=?", (row["id"],)).fetchone()[0]
    watched = conn.execute("SELECT COUNT(*) FROM watched WHERE user_id=?", (row["id"],)).fetchone()[0]
    return {
        "id": row["id"],
        "user_id": row["id"],
        "username": row["username"],
        "display_name": row["username"],
        "email": row["email"],
        "avatar_url": row["thumb"],
        "is_active": True,
        "is_verified": True,
        "email_verified": True,
        "is_superuser": bool(row["is_admin"]),
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
        "rated_count": rated,
        "watched_count": watched,
        "auth_provider": "plex",
        "has_password": False,
    }
