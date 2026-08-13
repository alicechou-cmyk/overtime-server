"""WSGI 進入點：給只支援 WSGI 的雲端主機用（例如 PythonAnywhere）。

它把 WSGI 的請求轉成 server.Handler 認得的形式，
所以路由、權限、API 全部沿用同一份程式碼，行為跟本機一致。

主機端只要指向這個檔案裡的 `application` 就會動。
"""

import io
import os
import sys
import traceback
from http.client import responses as HTTP_REASONS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402


class _Headers(object):
    """把 WSGI environ 裡的 HTTP_* 還原成不分大小寫的標頭查詢。"""

    def __init__(self, environ):
        self._items = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self._items[key[5:].replace("_", "-").lower()] = value
        if environ.get("CONTENT_TYPE"):
            self._items["content-type"] = environ["CONTENT_TYPE"]
        if environ.get("CONTENT_LENGTH"):
            self._items["content-length"] = environ["CONTENT_LENGTH"]

    def get(self, name, default=None):
        return self._items.get(name.lower(), default)

    def __getitem__(self, name):
        return self._items[name.lower()]

    def __contains__(self, name):
        return name.lower() in self._items


class WsgiRequest(server.Handler):
    """不經過 socket 的 Handler：把回應收集到記憶體裡交給 WSGI。"""

    def __init__(self, environ):
        # 故意不呼叫父類別的 __init__（那會去讀寫 socket）
        self.environ = environ
        self.command = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/") or "/"
        query = environ.get("QUERY_STRING", "")
        self.path = path + ("?" + query if query else "")
        self.requestline = "{} {} HTTP/1.1".format(self.command, self.path)
        self.request_version = "HTTP/1.1"
        self.headers = _Headers(environ)
        self.rfile = environ.get("wsgi.input") or io.BytesIO()
        self.wfile = io.BytesIO()
        self.client_address = (environ.get("REMOTE_ADDR", ""), 0)
        self.url_scheme = environ.get("wsgi.url_scheme", "http")
        self.server = None
        self.pending_cookies = []
        self.status_code = 200
        self.out_headers = []

    # ---------- 改寫傳輸層 ----------

    def send_response(self, code, message=None):
        self.status_code = code
        self.log_request(code)

    def send_header(self, key, value):
        self.out_headers.append((key, str(value)))

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self.status_code = code
        self.out_headers = [("Content-Type", "text/plain; charset=utf-8")]
        self.wfile = io.BytesIO((message or "error").encode("utf-8"))


def application(environ, start_response):
    request = WsgiRequest(environ)
    try:
        request._dispatch(request.command)
    except Exception:
        traceback.print_exc()
        request.status_code = 500
        request.out_headers = [("Content-Type", "application/json; charset=utf-8")]
        request.wfile = io.BytesIO(b'{"error": "\xe4\xbc\xba\xe6\x9c\x8d\xe5\x99\xa8'
                                  b'\xe5\x85\xa7\xe9\x83\xa8\xe9\x8c\xaf\xe8\xaa\xa4"}')

    body = request.wfile.getvalue()
    headers = [(k, v) for k, v in request.out_headers
               if k.lower() != "content-length"]
    headers.append(("Content-Length", str(len(body))))

    status = "{} {}".format(request.status_code,
                            HTTP_REASONS.get(request.status_code, ""))
    start_response(status, headers)
    return [body]
