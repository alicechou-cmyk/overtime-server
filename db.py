"""資料庫層：同一份程式碼支援兩種後端。

  本機執行      → SQLite（data/overtime.db），不需要安裝任何東西
  雲端（Vercel）→ Postgres（Neon），由環境變數 DATABASE_URL 自動切換

切換方式：有設 DATABASE_URL 就走 Postgres，沒有就走 SQLite。
上層程式（api.py / auth.py …）不需要知道現在是哪一種。
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "overtime.db")

_DSN = os.environ.get("DATABASE_URL", "").strip()

# 這些資料表有 id 自動編號欄位；Postgres 需要靠 RETURNING id 才拿得到 lastrowid
_ID_TABLES = ("users", "user_cc", "records", "mails", "audit", "work_segments")


def use_postgres():
    return bool(_DSN)


def set_dsn(dsn):
    """給 `app.py --check-db` 用：手動指定連線字串。"""
    global _DSN
    _DSN = (dsn or "").strip()


def backend_name():
    return "Postgres" if use_postgres() else "SQLite"


# ---------- 資料表結構 ----------

_TABLES = """
CREATE TABLE IF NOT EXISTS users(
  id                   {pk},
  account              TEXT    NOT NULL UNIQUE,
  name                 TEXT    NOT NULL,
  email                TEXT    NOT NULL DEFAULT '',
  emp_no               TEXT    NOT NULL DEFAULT '',
  dept                 TEXT    NOT NULL DEFAULT '',
  role                 TEXT    NOT NULL DEFAULT 'user',
  approver_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
  dept_head_id         INTEGER,
  use_default_cc       INTEGER NOT NULL DEFAULT 1,
  password_hash        TEXT    NOT NULL,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  active               INTEGER NOT NULL DEFAULT 1,
  created_at           TEXT    NOT NULL,
  updated_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS user_cc(
  id       {pk},
  user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label    TEXT    NOT NULL DEFAULT '',
  email    TEXT    NOT NULL,
  seq      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_user_cc_user ON user_cc(user_id);

CREATE TABLE IF NOT EXISTS sessions(
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ip         TEXT NOT NULL DEFAULT '',
  ua         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS records(
  id               {pk},
  ticket_no        TEXT    NOT NULL UNIQUE,
  user_id          INTEGER NOT NULL REFERENCES users(id),
  user_name        TEXT    NOT NULL,
  user_dept        TEXT    NOT NULL DEFAULT '',
  work_date        TEXT    NOT NULL,
  start_time       TEXT    NOT NULL,
  end_time         TEXT    NOT NULL,
  hours            {real}  NOT NULL,
  content          TEXT    NOT NULL,
  day_type         TEXT    NOT NULL,
  day_type_text    TEXT    NOT NULL,
  stamped_at       TEXT    NOT NULL,
  status           TEXT    NOT NULL,
  approver_id      INTEGER REFERENCES users(id),
  approver_name    TEXT    NOT NULL DEFAULT '',
  approver_email   TEXT    NOT NULL DEFAULT '',
  dept_head_id     INTEGER,
  dept_head_name   TEXT    NOT NULL DEFAULT '',
  dept_head_email  TEXT    NOT NULL DEFAULT '',
  approve_token    TEXT    NOT NULL DEFAULT '',
  decided_at       TEXT,
  decision_comment TEXT    NOT NULL DEFAULT '',
  applied_at       TEXT,
  apply_note       TEXT    NOT NULL DEFAULT '',
  voided_at        TEXT,
  void_reason      TEXT    NOT NULL DEFAULT '',
  client_ip        TEXT    NOT NULL DEFAULT '',
  segments         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_user ON records(user_id, id);
CREATE INDEX IF NOT EXISTS idx_records_approver ON records(approver_id, status);
CREATE INDEX IF NOT EXISTS idx_records_token ON records(approve_token);

-- 計時器：「開始 → 暫停 → 繼續」的每一段。時間一律由伺服器蓋。
-- ended_at 是 NULL = 正在計時；record_id 是 NULL = 還沒送出成加班登記。
CREATE TABLE IF NOT EXISTS work_segments(
  id         {pk},
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  work_date  TEXT    NOT NULL,
  started_at TEXT    NOT NULL,
  ended_at   TEXT,
  record_id  INTEGER REFERENCES records(id),
  client_ip  TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_segments_user ON work_segments(user_id, record_id);

CREATE TABLE IF NOT EXISTS mails(
  id         {pk},
  record_id  INTEGER REFERENCES records(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  to_email   TEXT NOT NULL,
  to_label   TEXT NOT NULL DEFAULT '',
  subject    TEXT NOT NULL,
  body       TEXT NOT NULL,
  status     TEXT NOT NULL,
  error      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_mails_status ON mails(status, id);
CREATE INDEX IF NOT EXISTS idx_mails_record ON mails(record_id);

CREATE TABLE IF NOT EXISTS holidays(
  date TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit(
  id     {pk},
  at     TEXT NOT NULL,
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  ip     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(id);

CREATE TABLE IF NOT EXISTS login_attempts(
  account      TEXT PRIMARY KEY,
  fails        INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT
);
"""

SCHEMA_SQLITE = _TABLES.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT", real="REAL")
SCHEMA_PG = _TABLES.format(pk="SERIAL PRIMARY KEY", real="DOUBLE PRECISION")

DEFAULT_SETTINGS = {
    "company_name": os.environ.get("COMPANY_NAME", "玩美行動"),
    "base_url": "",                      # 空白 = 自動用當下請求的網址
    "mail_mode": "preview",              # preview | smtp | resend
    "default_cc": json.dumps(
        [{"label": "HR", "email": "hr@company.com"}], ensure_ascii=False
    ),
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_from": "",
    "smtp_from_name": "假日加班登記系統",
    "resend_api_key": "",
    "session_days": "7",
    # 送出正式申請後要導向的外部系統（例如公司的 HR 系統）；空白 = 不導向
    "apply_redirect_url": "",
    "apply_redirect_label": "前往 HR 系統填正式申請",
}

SEED_HOLIDAYS = {
    "2026-01-01": "元旦", "2026-02-17": "春節", "2026-02-18": "春節",
    "2026-02-19": "春節", "2026-02-28": "和平紀念日", "2026-04-04": "兒童節",
    "2026-04-05": "清明節", "2026-05-01": "勞動節", "2026-06-19": "端午節",
    "2026-09-25": "中秋節", "2026-09-28": "教師節", "2026-10-10": "國慶日",
    "2026-10-25": "台灣光復紀念日", "2026-12-25": "行憲紀念日",
}


# 時區：雲端主機通常是 UTC，但這個系統的時間戳必須是台灣時間才有意義。
# 一律用固定時區換算，本機（台灣）與雲端算出來的結果就會一致。
TZ_OFFSET_HOURS = float(os.environ.get("TZ_OFFSET_HOURS", "8"))
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))


def local_now():
    """當地時間（預設台灣 UTC+8），回傳不帶時區的 datetime 方便比較與存檔。"""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None, microsecond=0)


def now_str():
    """本地時間字串，例：2026-08-11 18:05:03"""
    return local_now().isoformat(sep=" ")


# ---------- Postgres 轉接層 ----------
# 讓 psycopg2 用起來跟 sqlite3 一樣：conn.execute(sql, params) 直接回結果。

class _PgResult(object):
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor.fetchall())


class PgConnection(object):
    """把 psycopg2 連線包成 sqlite3.Connection 的樣子。"""

    def __init__(self, raw):
        self._raw = raw

    @staticmethod
    def _translate(sql):
        # sqlite 用 ?，psycopg2 用 %s
        return sql.replace("?", "%s")

    @staticmethod
    def _needs_returning(sql):
        head = sql.strip().lower()
        if not head.startswith("insert into"):
            return False
        if "returning" in head:
            return False
        table = head[len("insert into"):].strip().split("(")[0].split()[0]
        return table in _ID_TABLES

    def execute(self, sql, params=()):
        from psycopg2.extras import RealDictCursor
        cursor = self._raw.cursor(cursor_factory=RealDictCursor)
        statement = self._translate(sql)
        lastrowid = None
        if self._needs_returning(sql):
            cursor.execute(statement.rstrip().rstrip(";") + " RETURNING id", params)
            row = cursor.fetchone()
            lastrowid = row["id"] if row else None
        else:
            cursor.execute(statement, params)
        return _PgResult(cursor, lastrowid)

    def executescript(self, sql):
        cursor = self._raw.cursor()
        cursor.execute(sql)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def connect():
    if use_postgres():
        import psycopg2
        raw = psycopg2.connect(_DSN, connect_timeout=10)
        return PgConnection(raw)

    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


@contextmanager
def db():
    """一個請求一條連線；離開時自動 commit / rollback。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()



# ---------- 欄位遷移 ----------
# CREATE TABLE IF NOT EXISTS 對已存在的表格沒有作用，
# 所以新增欄位要靠這裡逐欄檢查後 ALTER TABLE。可重複執行。

MIGRATIONS = [
    ("users", "dept_head_id", "INTEGER"),
    ("records", "dept_head_id", "INTEGER"),
    ("records", "dept_head_name", "TEXT NOT NULL DEFAULT ''"),
    ("records", "dept_head_email", "TEXT NOT NULL DEFAULT ''"),
    ("records", "segments", "TEXT NOT NULL DEFAULT ''"),
]


def existing_columns(conn, table):
    if use_postgres():
        rows = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name=?", (table,))
    else:
        rows = conn.execute("PRAGMA table_info({})".format(table))
    return set(r["name"] for r in rows)


def migrate(conn):
    """補上缺少的欄位，回傳這次實際新增了哪些。"""
    added = []
    for table, column, decl in MIGRATIONS:
        if column not in existing_columns(conn, table):
            conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, column, decl))
            added.append("{}.{}".format(table, column))
    return added


# ---------- 設定 ----------

def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def all_settings(conn):
    out = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key,value FROM settings"):
        out[row["key"]] = row["value"]
    # 環境變數優先（雲端上把密鑰放環境變數，不要存進資料庫）
    if os.environ.get("RESEND_API_KEY"):
        out["resend_api_key"] = os.environ["RESEND_API_KEY"]
    if os.environ.get("SMTP_PASS"):
        out["smtp_pass"] = os.environ["SMTP_PASS"]
    return out


def log_audit(conn, actor, action, target="", detail="", ip=""):
    conn.execute(
        "INSERT INTO audit(at,actor,action,target,detail,ip) VALUES(?,?,?,?,?,?)",
        (now_str(), actor, action, target, detail, ip),
    )


# ---------- 初始化 ----------

def init_db(seed_demo=None):
    """建立資料表、預設設定與假日；沒有任何使用者時建立管理員帳號。

    管理員初始密碼來源：
      本機 → 隨機產生並印出來
      雲端 → 環境變數 ADMIN_INITIAL_PASSWORD（Vercel 後台設定）
    """
    import auth

    if seed_demo is None:
        seed_demo = not use_postgres() or os.environ.get("SEED_DEMO") == "1"

    fresh = use_postgres() or not os.path.exists(DB_PATH)
    info = {"fresh": fresh, "accounts": [], "backend": backend_name()}

    with db() as conn:
        conn.executescript(SCHEMA_PG if use_postgres() else SCHEMA_SQLITE)
        added = migrate(conn)
        if added:
            info["migrated"] = added
            print("[db] 已新增欄位：{}".format(", ".join(added)))

        for key, value in DEFAULT_SETTINGS.items():
            if conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone() is None:
                set_setting(conn, key, value)

        for date, name in SEED_HOLIDAYS.items():
            conn.execute(
                "INSERT INTO holidays(date,name) VALUES(?,?) ON CONFLICT(date) DO NOTHING",
                (date, name),
            )

        has_user = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
        if has_user:
            return info

        ts = now_str()
        note = "管理員（第一次登入會要求換密碼）"
        admin_pw = os.environ.get("ADMIN_INITIAL_PASSWORD", "").strip()
        if not admin_pw:
            if use_postgres():
                # 雲端沒辦法把隨機密碼印給人看，一定要先設環境變數
                info["needs_admin_password"] = True
                return info
            admin_pw = auth.generate_password()

        # 雲端第一次部署可能同時有多個實例冷啟動，都想建立管理員。
        # 加上 ON CONFLICT DO NOTHING，搶輸的那個就安靜跳過。
        conn.execute(
            "INSERT INTO users(account,name,email,role,password_hash,"
            "must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account) DO NOTHING",
            ("admin", "系統管理員", "admin@localhost", "admin",
             auth.hash_password(admin_pw), 1, ts, ts),
        )
        info["accounts"].append(("admin", admin_pw, note))

        if seed_demo:
            demo_pw = "demo1234"
            demo_hash = auth.hash_password(demo_pw)
            conn.execute(
                "INSERT INTO users(account,name,email,emp_no,dept,role,password_hash,"
                "must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account) DO NOTHING",
                ("chen", "陳大明", "manager@company.com", "M001", "技術部", "user",
                 demo_hash, 0, ts, ts),
            )
            manager = conn.execute(
                "SELECT id FROM users WHERE account=?", ("chen",)).fetchone()
            manager_id = manager["id"] if manager else None
            for acct, name, email, emp_no in [
                ("ming", "王小明", "ming@company.com", "E101"),
                ("mei", "李小美", "mei@company.com", "E102"),
            ]:
                conn.execute(
                    "INSERT INTO users(account,name,email,emp_no,dept,role,approver_id,"
                    "password_hash,must_change_password,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account) DO NOTHING",
                    (acct, name, email, emp_no, "技術部", "user", manager_id,
                     demo_hash, 0, ts, ts),
                )
            info["accounts"].append(("ming / mei / chen", demo_pw, "示範帳號（可在後台刪除）"))

        log_audit(conn, "system", "init_db", detail="建立資料庫（{}）".format(backend_name()))

    return info


def parse_ts(s):
    """把 now_str() 存下來的字串轉回 datetime。"""
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
