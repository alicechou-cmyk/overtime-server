"""HTTP 處理層：路由、權限、Cookie、靜態檔。

本機（app.py）與雲端（api/index.py）共用這一份，行為完全一致。
"""

import json
import mimetypes
import os
import re
import sys
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote, parse_qs, urlparse

import api
import api_admin
import auth
import db
import mailer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
MAX_BODY = 1024 * 1024

# (method, 路徑 regex, handler, 權限)  權限：public | user | admin
ROUTES = [
    ("GET", r"^/api/setup-status$", api.get_setup_status, "public"),
    ("POST", r"^/api/setup$", api.post_setup, "public"),
    ("POST", r"^/api/login$", api.post_login, "public"),
    ("POST", r"^/api/logout$", api.post_logout, "public"),
    ("GET", r"^/api/me$", api.get_me, "user"),
    ("POST", r"^/api/change-password$", api.post_change_password, "user"),

    ("GET", r"^/api/bootstrap$", api.get_bootstrap, "user"),
    ("POST", r"^/api/overtime$", api.post_overtime, "user"),
    ("GET", r"^/api/records$", api.get_my_records, "user"),
    ("GET", r"^/api/records/(?P<id>\d+)$", api.get_record, "user"),
    ("POST", r"^/api/records/(?P<id>\d+)/apply$", api.post_apply, "user"),
    ("POST", r"^/api/records/(?P<id>\d+)/void$", api.post_void, "user"),

    ("GET", r"^/api/approvals$", api.get_approvals, "user"),
    ("POST", r"^/api/approvals/(?P<id>\d+)/decide$", api.post_decide, "user"),
    ("GET", r"^/api/approve-token$", api.get_approve_by_token, "public"),
    ("POST", r"^/api/approve-token$", api.post_approve_by_token, "public"),

    ("GET", r"^/api/admin/dashboard$", api_admin.get_dashboard, "admin"),
    ("GET", r"^/api/admin/users$", api_admin.get_users, "admin"),
    ("POST", r"^/api/admin/users$", api_admin.post_user, "admin"),
    ("PATCH", r"^/api/admin/users/(?P<id>\d+)$", api_admin.patch_user, "admin"),
    ("DELETE", r"^/api/admin/users/(?P<id>\d+)$", api_admin.delete_user, "admin"),
    ("POST", r"^/api/admin/users/(?P<id>\d+)/reset-password$",
     api_admin.post_reset_password, "admin"),
    ("GET", r"^/api/admin/users/(?P<id>\d+)/routing$",
     api_admin.get_routing_preview, "admin"),
    ("GET", r"^/api/admin/records$", api_admin.get_records, "admin"),
    ("GET", r"^/api/admin/records\.csv$", api_admin.get_records_csv, "admin"),
    ("GET", r"^/api/admin/records/(?P<id>\d+)$", api_admin.get_record_detail, "admin"),
    ("GET", r"^/api/admin/mails$", api_admin.get_mails, "admin"),
    ("POST", r"^/api/admin/mails/(?P<id>\d+)/resend$", api_admin.post_mail_resend, "admin"),
    ("GET", r"^/api/admin/holidays$", api_admin.get_holidays, "admin"),
    ("POST", r"^/api/admin/holidays$", api_admin.post_holiday, "admin"),
    ("POST", r"^/api/admin/holidays/import$", api_admin.post_holidays_import, "admin"),
    ("DELETE", r"^/api/admin/holidays/(?P<date>\d{4}-\d{2}-\d{2})$",
     api_admin.delete_holiday, "admin"),
    ("GET", r"^/api/admin/settings$", api_admin.get_settings, "admin"),
    ("PUT", r"^/api/admin/settings$", api_admin.put_settings, "admin"),
    ("POST", r"^/api/admin/smtp-test$", api_admin.post_smtp_test, "admin"),
    ("GET", r"^/api/admin/audit$", api_admin.get_audit, "admin"),
]

COMPILED = [(m, re.compile(p), h, a) for m, p, h, a in ROUTES]

# 網址 → public/ 底下的檔案。權限一律由頁面本身的 JS 檢查（雲端靜態託管也是這樣）
PAGES = {
    "/": "index.html",
    "/login": "login.html",
    "/approvals": "approvals.html",
    "/admin": "admin.html",
    "/approve": "approve.html",
    "/password": "password.html",
}

# 每次部署時更新，用來確認線上跑的是哪一版
APP_VERSION = "2026-08-19-git"

ACCESS_LOG = os.path.join(db.DATA_DIR, "server.log")
_log_lock = threading.Lock()
_init_lock = threading.Lock()
_initialized = False


def ensure_initialized():
    """雲端 serverless 沒有啟動流程，第一個請求進來時才建表。"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        try:
            db.init_db()
        except Exception:
            # 多個實例同時冷啟動時可能撞在一起建表。
            # 只要資料表已經在了就當作成功，否則往外拋。
            traceback.print_exc()
            with db.db() as conn:
                conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        _initialized = True


def mark_initialized():
    """本機啟動時已經跑過 init_db，就不用再跑一次。"""
    global _initialized
    _initialized = True


def write_access_log(entry):
    """把請求寫進 data/server.log（只在本機 SQLite 模式；雲端檔案系統唯讀）。"""
    if db.use_postgres():
        return
    try:
        with _log_lock:
            with open(ACCESS_LOG, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
    except OSError:
        pass


class Ctx(object):
    def __init__(self, handler, conn, user, session, body, query, path_params):
        self.handler = handler
        self.conn = conn
        self.user = user
        self.session = session
        self.body = body or {}
        self.query = query or {}
        self.path_params = path_params or {}
        self.ip = handler.client_ip()
        self.ua = handler.headers.get("User-Agent", "")

    def set_session_cookie(self, token):
        days = int(db.get_setting(self.conn, "session_days", "7") or 7)
        parts = [
            "{}={}".format(auth.SESSION_COOKIE, token),
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age={}".format(days * 86400),
        ]
        if self.handler.scheme() == "https":
            parts.append("Secure")
        self.handler.pending_cookies.append("; ".join(parts))

    def clear_session_cookie(self):
        self.handler.pending_cookies.append(
            "{}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0".format(auth.SESSION_COOKIE)
        )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OvertimeLog/1.0"

    # ---------- 請求資訊 ----------

    def client_ip(self):
        """雲端在反向代理後面，真正的來源 IP 在 X-Forwarded-For。"""
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        try:
            return self.client_address[0]
        except Exception:
            return ""

    def scheme(self):
        proto = self.headers.get("X-Forwarded-Proto", "")
        if proto:
            return proto.split(",")[0].strip()
        # WSGI 模式沒有 socket 伺服器物件，改看 environ 帶進來的 url_scheme
        explicit = getattr(self, "url_scheme", None)
        if explicit:
            return explicit
        return "https" if getattr(getattr(self, "server", None),
                                  "is_https", False) else "http"

    def request_origin(self):
        host = self.headers.get("Host", "")
        return "{}://{}".format(self.scheme(), host) if host else ""

    # ---------- 回應 ----------

    def log_message(self, fmt, *args):
        line = fmt % args
        if "/static/" in line:
            return
        entry = "[{}] {} {}".format(
            self.log_date_time_string(), self.client_ip(), line)
        sys.stderr.write(entry + "\n")
        write_access_log(entry)

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            for cookie in getattr(self, "pending_cookies", []):
                self.send_header("Set-Cookie", cookie)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status, obj, headers=None):
        self._send(status, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8", headers)

    def _redirect(self, location):
        self._send(302, b"", "text/plain; charset=utf-8", {"Location": location})

    def _cookie_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            cookie = SimpleCookie()
            cookie.load(raw)
            morsel = cookie.get(auth.SESSION_COOKIE)
            return morsel.value if morsel else None
        except Exception:
            return None

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise api.ApiError(413, "資料量過大")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise api.ApiError(400, "請求格式錯誤（需要 JSON）")
        if not isinstance(data, dict):
            raise api.ApiError(400, "請求格式錯誤")
        return data

    def _same_origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            return urlparse(origin).netloc == host
        except Exception:
            return False

    # ---------- 分派 ----------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        self.pending_cookies = []
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)

            # Vercel 的 rewrite 會把路徑改寫成 /api/index，真正的路徑放在 __path。
            # 這裡還原回來，路由才認得出是哪一支 API。
            if "__path" in query:
                real = query.pop("__path")[0] or "/"
                if not real.startswith("/"):
                    real = "/" + real
                path = urlparse(real).path.rstrip("/") or "/"

            if path == "/health":
                return self._json(200, {"ok": True, "time": db.now_str(),
                                        "backend": db.backend_name(),
                                        "version": APP_VERSION})
            if path == "/favicon.ico":
                return self._send(204)
            if path.startswith("/static/"):
                return self._serve_static(path)
            if path.startswith("/api/"):
                return self._handle_api(method, path, query)
            if method == "GET":
                return self._serve_page(path)
            return self._json(405, {"error": "不支援的方法"})
        except api.ApiError as err:
            self._json(err.status, {"error": err.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "伺服器內部錯誤"})

    def _handle_api(self, method, path, query):
        route = None
        path_params = {}
        path_matched = False
        for r_method, pattern, handler, level in COMPILED:
            match = pattern.match(path)
            if match:
                path_matched = True
                if r_method == method:
                    route = (handler, level)
                    path_params = match.groupdict()
                    break
        if not route:
            if path_matched:
                return self._json(405, {"error": "不支援的方法"})
            return self._json(404, {"error": "找不到這個 API"})

        handler, level = route
        if method != "GET" and not self._same_origin_ok():
            return self._json(403, {"error": "來源不正確，請重新整理頁面"})

        for key in path_params:
            if path_params[key] and path_params[key].isdigit():
                path_params[key] = int(path_params[key])

        body = self._read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}

        ensure_initialized()

        with db.db() as conn:
            self._autoconfig_base_url(conn)
            user, session = auth.get_session_user(conn, self._cookie_token())

            if level != "public":
                if not user:
                    return self._json(401, {"error": "請先登入"})
                if level == "admin" and user["role"] != "admin":
                    return self._json(403, {"error": "需要管理員權限"})
                if method != "GET":
                    sent = self.headers.get("X-CSRF-Token", "")
                    if not sent or sent != session["csrf"]:
                        return self._json(403, {"error": "CSRF 檢查失敗，請重新整理頁面"})
                if user["must_change_password"] and path not in (
                        "/api/change-password", "/api/logout", "/api/me"):
                    return self._json(423, {"error": "請先變更初始密碼",
                                            "must_change_password": True})

            ctx = Ctx(self, conn, user, session, body, query, path_params)
            result = handler(ctx)

        # 交易已經 commit（紀錄安全存好了）之後才寄信。
        # 這樣寄信慢或失敗都不會影響已登記的資料。
        if method != "GET":
            mailer.flush_after_commit()

        if isinstance(result, api.RawResponse):
            return self._send(200, result.body, result.content_type, result.headers)
        return self._json(200, result if result is not None else {"ok": True})

    def _autoconfig_base_url(self, conn):
        """第一次有人用正式網址連進來時，自動記下來（信裡的簽核連結要用）。"""
        try:
            if db.get_setting(conn, "base_url", ""):
                return
            origin = self.request_origin()
            if origin and "localhost" not in origin and "127.0.0.1" not in origin:
                db.set_setting(conn, "base_url", origin)
        except Exception:
            pass

    def _serve_page(self, path):
        filename = PAGES.get(path)
        if not filename:
            return self._send(404, "找不到頁面", "text/plain; charset=utf-8")
        full = os.path.join(PUBLIC_DIR, filename)
        try:
            with open(full, "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        except FileNotFoundError:
            return self._send(500, "缺少頁面檔案：{}".format(filename))

    def _serve_static(self, path):
        rel = path[len("/static/"):]
        root = os.path.join(PUBLIC_DIR, "static")
        full = os.path.normpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return self._send(404, "not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        try:
            with open(full, "rb") as fh:
                return self._send(200, fh.read(), ctype)
        except OSError:
            return self._send(404, "not found")
