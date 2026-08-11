#!/bin/bash
# 在 Finder 裡雙擊這個檔案就能啟動伺服器。
# 關閉伺服器：在這個視窗按 Control + C，或直接關掉視窗。

cd "$(dirname "$0")" || exit 1

PORT="${PORT:-8080}"

echo ""
echo "正在啟動假日加班登記系統…"
echo ""

# caffeinate -i：伺服器執行期間不讓 Mac 進入睡眠，避免同事突然連不進來
exec caffeinate -i /usr/bin/python3 app.py --port "$PORT"
