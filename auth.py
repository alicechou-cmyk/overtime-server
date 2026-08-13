"""登入驗證：密碼雜湊、Session、CSRF、登入失敗鎖定。"""

import base64
import hashlib
import hmac
import secrets
import string
from datetime import datetime, timedelta

import db

PBKDF2_ROUNDS = 240_000
SESSION_COOKIE = "ot_session"
MAX_FAILS = 8               # 連續失敗次數上限
LOCK_MINUTES = 10           # 超過就鎖這麼久


# ---------- 密碼 ----------

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password, stored):
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(rounds), len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def generate_password(length=12):
    """好唸好抄的隨機密碼（避開 0/O、1/l 這類容易看錯的字元）。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(length))
    return "{}-{}-{}".format(raw[:4], raw[4:8], raw[8:])


def check_password_strength(password):
    """回傳錯誤訊息字串，通過則回傳 None。"""
    if len(password) < 8:
        return "密碼至少要 8 個字元"
    kinds = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(c in string.punctuation for c in password),
    ])
    if kinds < 2:
        return "密碼請至少混用兩種以上：小寫字母、大寫字母、數字、符號"
    return None


# ---------- 登入失敗鎖定 ----------

def is_locked(conn, account):
    row = conn.execute(
        "SELECT locked_until FROM login_attempts WHERE account=?", (account,)
    ).fetchone()
    if not row or not row["locked_until"]:
        return None
    until = datetime.fromisoformat(row["locked_until"])
    if until > db.local_now():
        return until
    conn.execute(
        "UPDATE login_attempts SET fails=0, locked_until=NULL WHERE account=?", (account,)
    )
    return None


def record_fail(conn, account):
    conn.execute(
        "INSERT INTO login_attempts(account,fails) VALUES(?,1) "
        "ON CONFLICT(account) DO UPDATE SET fails=fails+1",
        (account,),
    )
    fails = conn.execute(
        "SELECT fails FROM login_attempts WHERE account=?", (account,)
    ).fetchone()["fails"]
    if fails >= MAX_FAILS:
        until = (db.local_now() + timedelta(minutes=LOCK_MINUTES)).replace(microsecond=0)
        conn.execute(
            "UPDATE login_attempts SET locked_until=? WHERE account=?",
            (until.isoformat(sep=" "), account),
        )
        return until
    return None


def clear_fails(conn, account):
    conn.execute("DELETE FROM login_attempts WHERE account=?", (account,))


# ---------- Session ----------

def create_session(conn, user_id, ip="", ua=""):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    days = int(db.get_setting(conn, "session_days", "7") or 7)
    created = db.local_now().replace(microsecond=0)
    expires = created + timedelta(days=days)
    conn.execute(
        "INSERT INTO sessions(token,user_id,csrf,created_at,expires_at,ip,ua) "
        "VALUES(?,?,?,?,?,?,?)",
        (token, user_id, csrf, created.isoformat(sep=" "), expires.isoformat(sep=" "),
         ip, ua[:300]),
    )
    return token, csrf


def get_session_user(conn, token):
    """回傳 (user_row, session_row)，失效則 (None, None)。"""
    if not token:
        return None, None
    row = conn.execute(
        "SELECT s.token, s.csrf, s.expires_at, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,),
    ).fetchone()
    if not row:
        return None, None
    if datetime.fromisoformat(row["expires_at"]) < db.local_now():
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None, None
    if not row["active"]:
        return None, None
    return row, {"token": row["token"], "csrf": row["csrf"]}


def destroy_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def destroy_user_sessions(conn, user_id, keep_token=None):
    if keep_token:
        conn.execute(
            "DELETE FROM sessions WHERE user_id=? AND token<>?", (user_id, keep_token)
        )
    else:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def purge_expired(conn):
    conn.execute(
        "DELETE FROM sessions WHERE expires_at < ?",
        (db.local_now().isoformat(sep=" "),),
    )
