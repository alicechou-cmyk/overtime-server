"""Vercel serverless 進入點。

Vercel 的 Python runtime 支援 WSGI 應用程式（變數名稱 `app`），
這裡直接沿用 wsgi.py，路由、權限、API 全部跟本機同一套程式碼。

靜態檔（HTML／CSS／JS）由 Vercel 直接從 public/ 提供，不會進到這裡。
真正的請求路徑由 vercel.json 的 rewrite 用 __path 參數傳進來（見 server.py）。
"""

import os
import sys

# 讓 Vercel 的執行環境找得到上一層的模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wsgi import application  # noqa: E402

app = application
