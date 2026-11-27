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
    if not row or row["expires"] < time.time() or not _active_user(row["user_id"]):
        return None
    conn.execute("DELETE FROM refresh_tokens WHERE token=?", (token,))
    conn.commit()
    return issue_tokens(row["user_id"])


def revoke(token):
    conn = db.connect()
    conn.execute("DELETE FROM refresh_tokens WHERE token=?", (token,))
    conn.commit()


# ---------- Sessionscookie (läses av servern, även för bilder och sidladdningar) ----------
SESSION_COOKIE = "fd_sess"
SESSION_TTL = REFRESH_TTL


def make_session(user_id):
    exp = int(time.time()) + SESSION_TTL
    msg = f"{user_id}.{exp}"
    sig = hmac.new(config.secret_key().encode(), ("sess:" + msg).encode(), hashlib.sha256).digest()
    return f"{msg}.{_b64(sig)}"


def verify_session(value):
    try:
        uid, exp, sig = (value or "").split(".")
        msg = f"{uid}.{exp}"
        expected = hmac.new(config.secret_key().encode(), ("sess:" + msg).encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)) or int(exp) < time.time():
            return None
        return int(uid)
    except (ValueError, TypeError):
        return None


def set_session_cookie(response, request, user_id):
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    response.set_cookie(SESSION_COOKIE, make_session(user_id), max_age=SESSION_TTL, httponly=True,
                        samesite="lax", secure=secure, path="/")


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE, path="/")


def _active_user(uid):
    if not uid:
        return None
    row = db.connect().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row or ("disabled" in row.keys() and row["disabled"]):
        return None
    return row


def user_from_request(request):
    """Inloggad användare via Bearer-token eller sessionscookie. Spärrade användare räknas som utloggade."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        user = _active_user(verify_access(h[7:].strip()))
        if user:
            return user
    return _active_user(verify_session(request.cookies.get(SESSION_COOKIE)))


def revoke_user(user_id):
    """Spärrar en användare (t.ex. när Plex-åtkomsten till servern tagits bort)."""
    conn = db.connect()
    conn.execute("UPDATE users SET disabled=1 WHERE id=?", (user_id,))
    conn.execute("DELETE FROM refresh_tokens WHERE user_id=?", (user_id,))
    conn.commit()


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
