"""Vercel serverless 進入點。

Vercel 的 Python runtime 會找名為 `handler` 的 BaseHTTPRequestHandler 子類別，
所以這裡直接沿用本機那一份 server.Handler——路由、權限、API 全部同一套程式碼。

靜態檔（HTML／CSS／JS）由 Vercel 直接從 public/ 提供，不會進到這裡。
"""

import os
import sys

# 讓 Vercel 的執行環境找得到上一層的模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import Handler  # noqa: E402

handler = Handler
