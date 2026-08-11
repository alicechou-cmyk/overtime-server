#!/usr/bin/env python3
"""假日加班登記系統 — 本機啟動器（只用 Python 標準函式庫）

啟動：
    python3 app.py                 # 綁 0.0.0.0:8080，同網段的同事就能連進來
    python3 app.py --port 9000
    python3 app.py --https         # 用自簽憑證跑 HTTPS（瀏覽器會顯示警告需手動信任）
    python3 app.py --reset-admin   # 忘記管理員密碼時，重設並印出新密碼
    python3 app.py --check-db URL  # 檢查雲端 Postgres（Neon）連線與資料表是否正常

雲端（Vercel）走的是 api/index.py，共用同一份 server.py 與 API 程式碼。
"""

import argparse
import os
import socket
import ssl
import subprocess
import sys
from http.server import ThreadingHTTPServer

import auth
import db
import mailer
import server


def lan_ip():
    """取得本機在區網的 IP（不會真的送出封包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def ensure_cert():
    """需要 HTTPS 時，用 openssl 產一張自簽憑證（有效期一年）。"""
    cert = os.path.join(db.DATA_DIR, "server.crt")
    key = os.path.join(db.DATA_DIR, "server.key")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(db.DATA_DIR, exist_ok=True)
    ip = lan_ip()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
         "-keyout", key, "-out", cert, "-subj", "/CN={}".format(ip),
         "-addext", "subjectAltName=IP:{},IP:127.0.0.1,DNS:localhost".format(ip)],
        check=True, capture_output=True,
    )
    os.chmod(key, 0o600)
    print("已產生自簽憑證：{}".format(cert))
    return cert, key


def reset_admin():
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            print("資料庫裡沒有管理員帳號，請刪掉 data/overtime.db 後重新啟動。")
            return
        password = auth.generate_password()
        conn.execute(
            "UPDATE users SET password_hash=?, must_change_password=1, active=1, "
            "updated_at=? WHERE id=?",
            (auth.hash_password(password), db.now_str(), row["id"]),
        )
        auth.destroy_user_sessions(conn, row["id"])
        auth.clear_fails(conn, row["account"])
        db.log_audit(conn, "cli", "admin_password_reset", row["account"])
    print("\n管理員帳號：{}\n新密碼　　：{}\n（登入後會要求你設定新密碼）\n".format(
        row["account"], password))


def check_db(dsn):
    """把雲端資料庫從頭到尾測一遍：連線 → 建表 → 寫入 → 查詢 → 清理。

    部署到 Vercel 之前先用這個確認 Neon 連線字串是對的，
    比部署完看 500 錯誤好查太多。
    """
    print("\n檢查雲端資料庫連線…\n")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("❌ 缺少 psycopg2 套件，請先執行：")
        print("   /usr/bin/python3 -m pip install --user psycopg2-binary\n")
        return 1

    db.set_dsn(dsn)
    steps = []

    def ok(label, detail=""):
        steps.append(True)
        print("  ✅ {}{}".format(label, ("　" + detail) if detail else ""))

    def fail(label, exc):
        steps.append(False)
        print("  ❌ {}\n     {}: {}".format(label, type(exc).__name__, exc))

    try:
        with db.db() as conn:
            row = conn.execute("SELECT version() AS v").fetchone()
            ok("連得上資料庫", row["v"].split(",")[0][:60])
    except Exception as exc:
        fail("連不上資料庫", exc)
        print("\n請確認 DATABASE_URL 是 Neon 的 Pooled connection string，"
              "而且結尾有 ?sslmode=require\n")
        return 1

    try:
        info = db.init_db(seed_demo=False)
        ok("資料表建立完成", "後端 = " + info["backend"])
        if info.get("needs_admin_password"):
            print("     ⚠ 尚未建立管理員：請設定環境變數 ADMIN_INITIAL_PASSWORD")
    except Exception as exc:
        fail("建立資料表失敗", exc)
        return 1

    # 寫入 / 查詢 / 刪除 全流程實測，最後清乾淨
    try:
        with db.db() as conn:
            ts = db.now_str()
            cur = conn.execute(
                "INSERT INTO users(account,name,email,role,password_hash,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?)",
                ("__selftest__", "連線測試", "test@example.com", "user", "x", ts, ts),
            )
            uid = cur.lastrowid
            assert uid, "拿不到新增後的 id"
            ok("新增資料並取得自動編號", "id={}".format(uid))

            conn.execute(
                "INSERT INTO records(ticket_no,user_id,user_name,work_date,start_time,"
                "end_time,hours,content,day_type,day_type_text,stamped_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("__SELFTEST__", uid, "連線測試", "2026-01-01", "09:00", "12:00", 3.0,
                 "自我測試", "workday", "工作日", ts, "logged"),
            )
            agg = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(hours),0) AS h FROM records "
                "WHERE user_id=?", (uid,)).fetchone()
            ok("寫入加班紀錄並聚合查詢", "{} 筆 / {} 小時".format(agg["c"], agg["h"]))

            joined = conn.execute(
                "SELECT r.ticket_no, u.name FROM records r JOIN users u ON u.id=r.user_id "
                "WHERE r.user_id=?", (uid,)).fetchone()
            ok("關聯查詢", "{} / {}".format(joined["ticket_no"], joined["name"]))

            db.set_setting(conn, "__selftest__", "1")
            db.set_setting(conn, "__selftest__", "2")
            ok("設定寫入與覆寫（ON CONFLICT）",
               "值 = " + db.get_setting(conn, "__selftest__"))

            conn.execute("DELETE FROM records WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.execute("DELETE FROM settings WHERE key='__selftest__'")
            ok("測試資料已清除")
    except Exception as exc:
        fail("讀寫測試失敗", exc)
        return 1

    print("\n全部通過 ✅ 這組連線字串可以直接填進 Vercel 的 DATABASE_URL。\n")
    return 0 if all(steps) else 1


def print_banner(host, port, first_run, scheme):
    ip = lan_ip()
    bar = "─" * 56
    print("\n" + bar)
    print("  假日加班登記系統 已啟動（資料庫：{}）".format(db.backend_name()))
    print(bar)
    if host in ("0.0.0.0", "::"):
        print("  你自己用　：{}://localhost:{}".format(scheme, port))
        print("  同事連進來：{}://{}:{}   ← 把這個網址傳給同事".format(scheme, ip, port))
    else:
        print("  網址：{}://{}:{}".format(scheme, host, port))
    print("  後台管理　：{}://localhost:{}/admin".format(scheme, port))
    print(bar)
    if first_run and first_run["accounts"]:
        print("  第一次啟動，已建立以下帳號：\n")
        for account, password, note in first_run["accounts"]:
            print("    帳號：{:<20} 密碼：{}".format(account, password))
            print("    　　　{}\n".format(note))
        cred_file = os.path.join(db.DATA_DIR, "FIRST_RUN.txt")
        with open(cred_file, "w", encoding="utf-8") as fh:
            fh.write("假日加班登記系統 — 初始帳號（請登入後修改密碼，並刪除本檔案）\n\n")
            for account, password, note in first_run["accounts"]:
                fh.write("帳號：{}\n密碼：{}\n說明：{}\n\n".format(account, password, note))
        os.chmod(cred_file, 0o600)
        print("  （同樣的內容也存在 data/FIRST_RUN.txt）")
        print(bar)
    print("  每一筆連線都會顯示在下面，也會存進 data/server.log")
    print("  （同事連不上時，看這裡有沒有出現他的 IP，就知道請求有沒有到達）")
    print(bar)
    print("  按 Control + C 可以關掉伺服器\n")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="假日加班登記系統")
    parser.add_argument("--host", default="0.0.0.0", help="預設 0.0.0.0（同網段可連）")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--https", action="store_true", help="用自簽憑證跑 HTTPS")
    parser.add_argument("--no-demo", action="store_true", help="第一次啟動不要建示範帳號")
    parser.add_argument("--reset-admin", action="store_true", help="重設管理員密碼後結束")
    parser.add_argument("--check-db", metavar="URL",
                        help="檢查 Postgres（Neon）連線與資料表後結束")
    args = parser.parse_args()

    if args.check_db:
        return check_db(args.check_db)

    if args.reset_admin:
        db.init_db(seed_demo=False)
        reset_admin()
        return 0

    first_run = db.init_db(seed_demo=not args.no_demo)
    server.mark_initialized()

    scheme = "https" if args.https else "http"
    with db.db() as conn:
        auth.purge_expired(conn)
        if not db.get_setting(conn, "base_url", ""):
            db.set_setting(conn, "base_url", "{}://{}:{}".format(
                scheme, lan_ip(), args.port))

    mailer.start_worker()

    httpd = ThreadingHTTPServer((args.host, args.port), server.Handler)
    httpd.daemon_threads = True
    httpd.is_https = args.https
    if args.https:
        cert, key = ensure_cert()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

    print_banner(args.host, args.port, first_run, scheme)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已關閉。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
