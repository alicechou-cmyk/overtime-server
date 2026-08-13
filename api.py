"""員工端 / 簽核端 / 帳號相關 API。"""

import json
import secrets

import auth
import db
import dayutil
import mailer


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class RawResponse(object):
    """非 JSON 的回應（例如 CSV 下載）。"""

    def __init__(self, body, content_type="text/plain; charset=utf-8", headers=None):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.content_type = content_type
        self.headers = headers or {}


# ---------- 序列化 ----------

def user_public(row):
    return {
        "id": row["id"],
        "account": row["account"],
        "name": row["name"],
        "email": row["email"],
        "emp_no": row["emp_no"],
        "dept": row["dept"],
        "role": row["role"],
        "must_change_password": bool(row["must_change_password"]),
    }


def record_json(conn, row, with_mails=False):
    out = {
        "id": row["id"],
        "ticket_no": row["ticket_no"],
        "user_id": row["user_id"],
        "user_name": row["user_name"],
        "user_dept": row["user_dept"],
        "work_date": row["work_date"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "hours": row["hours"],
        "content": row["content"],
        "day_type": row["day_type"],
        "day_type_text": row["day_type_text"],
        "stamped_at": row["stamped_at"],
        "status": row["status"],
        "status_text": dayutil.status_text(row["status"]),
        "approver_name": row["approver_name"],
        "approver_email": row["approver_email"],
        "decided_at": row["decided_at"],
        "decision_comment": row["decision_comment"],
        "applied_at": row["applied_at"],
        "apply_note": row["apply_note"],
        "voided_at": row["voided_at"],
        "void_reason": row["void_reason"],
    }
    if with_mails:
        out["mails"] = [
            {
                "id": m["id"],
                "kind": m["kind"],
                "to_email": m["to_email"],
                "to_label": m["to_label"],
                "subject": m["subject"],
                "body": m["body"],
                "status": m["status"],
                "error": m["error"],
                "created_at": m["created_at"],
                "sent_at": m["sent_at"],
            }
            for m in conn.execute(
                "SELECT * FROM mails WHERE record_id=? ORDER BY id", (row["id"],)
            )
        ]
    return out


# ---------- 帳號 ----------

def post_login(ctx):
    account = (ctx.body.get("account") or "").strip()
    password = ctx.body.get("password") or ""
    if not account or not password:
        raise ApiError(400, "請輸入帳號與密碼")

    locked_until = auth.is_locked(ctx.conn, account)
    if locked_until:
        raise ApiError(429, "嘗試次數過多，請於 {} 後再試".format(
            locked_until.strftime("%H:%M")))

    row = ctx.conn.execute(
        "SELECT * FROM users WHERE account=? OR email=?", (account, account)
    ).fetchone()
    if row is None and ctx.conn.execute(
            "SELECT 1 FROM users LIMIT 1").fetchone() is None:
        raise ApiError(503, "系統還沒完成初始設定，請重新整理這一頁。")
    ok = row is not None and row["active"] and auth.verify_password(
        password, row["password_hash"])
    if not ok:
        until = auth.record_fail(ctx.conn, account)
        db.log_audit(ctx.conn, account, "login_failed", ip=ctx.ip)
        if until:
            raise ApiError(429, "連續登入失敗，帳號暫時鎖定至 {}".format(
                until.strftime("%H:%M")))
        raise ApiError(401, "帳號或密碼錯誤")

    auth.clear_fails(ctx.conn, account)
    token, csrf = auth.create_session(ctx.conn, row["id"], ctx.ip, ctx.ua)
    db.log_audit(ctx.conn, row["account"], "login", ip=ctx.ip)
    ctx.set_session_cookie(token)
    return {"ok": True, "user": user_public(row), "csrf": csrf}


def get_setup_status(ctx):
    """還沒有任何帳號時，登入頁會改成「建立管理員」的初始設定畫面。"""
    has_user = ctx.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    return {
        "needs_setup": not has_user,
        "company_name": db.get_setting(ctx.conn, "company_name", ""),
    }


def post_setup(ctx):
    """第一次啟用：由使用者自己在畫面上設定管理員密碼。

    只有在資料庫完全沒有使用者時才能呼叫，之後永久關閉。
    """
    if ctx.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        raise ApiError(409, "系統已經設定過了，請直接登入")

    password = ctx.body.get("password") or ""
    err = auth.check_password_strength(password)
    if err:
        raise ApiError(400, err)

    name = (ctx.body.get("name") or "").strip()[:40] or "系統管理員"
    email = (ctx.body.get("email") or "").strip()[:120]
    company = (ctx.body.get("company_name") or "").strip()[:60]
    ts = db.now_str()

    cur = ctx.conn.execute(
        "INSERT INTO users(account,name,email,role,password_hash,"
        "must_change_password,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ("admin", name, email, "admin", auth.hash_password(password), 0, ts, ts),
    )
    user_id = cur.lastrowid
    if company:
        db.set_setting(ctx.conn, "company_name", company)

    token, csrf = auth.create_session(ctx.conn, user_id, ctx.ip, ctx.ua)
    ctx.set_session_cookie(token)
    db.log_audit(ctx.conn, "admin", "initial_setup", detail="建立第一個管理員帳號",
                 ip=ctx.ip)
    return {"ok": True, "account": "admin", "csrf": csrf}


def post_logout(ctx):
    if ctx.session:
        auth.destroy_session(ctx.conn, ctx.session["token"])
    ctx.clear_session_cookie()
    return {"ok": True}


def get_me(ctx):
    pending = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM records WHERE approver_id=? AND status IN "
        "('logged','notified')",
        (ctx.user["id"],),
    ).fetchone()["c"]
    is_approver = ctx.conn.execute(
        "SELECT 1 FROM users WHERE approver_id=? LIMIT 1", (ctx.user["id"],)
    ).fetchone() is not None
    return {
        "user": user_public(ctx.user),
        "csrf": ctx.session["csrf"],
        "is_approver": is_approver,
        "pending_approvals": pending,
        "company_name": db.get_setting(ctx.conn, "company_name", ""),
        "mail_mode": db.get_setting(ctx.conn, "mail_mode", "preview"),
    }


def post_change_password(ctx):
    old = ctx.body.get("old_password") or ""
    new = ctx.body.get("new_password") or ""
    if not ctx.user["must_change_password"]:
        if not auth.verify_password(old, ctx.user["password_hash"]):
            raise ApiError(400, "目前密碼不正確")
    err = auth.check_password_strength(new)
    if err:
        raise ApiError(400, err)
    if auth.verify_password(new, ctx.user["password_hash"]):
        raise ApiError(400, "新密碼不能與舊密碼相同")
    ctx.conn.execute(
        "UPDATE users SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?",
        (auth.hash_password(new), db.now_str(), ctx.user["id"]),
    )
    # 其他裝置上的舊登入一併失效
    auth.destroy_user_sessions(ctx.conn, ctx.user["id"], keep_token=ctx.session["token"])
    db.log_audit(ctx.conn, ctx.user["account"], "change_password", ip=ctx.ip)
    return {"ok": True}


# ---------- 員工端 ----------

def get_bootstrap(ctx):
    """員工登記頁一次抓齊需要的資料。"""
    recipients = mailer.resolve_recipients(ctx.conn, ctx.user)
    holidays = {
        row["date"]: row["name"]
        for row in ctx.conn.execute("SELECT date,name FROM holidays")
    }
    records = [
        record_json(ctx.conn, row)
        for row in ctx.conn.execute(
            "SELECT * FROM records WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (ctx.user["id"],),
        )
    ]
    return {
        "me": get_me(ctx),
        "routing": recipients,
        "holidays": holidays,
        "records": records,
        "server_time": db.now_str(),
    }


def post_overtime(ctx):
    """登記加班：時間戳由伺服器蓋，寫入後不可修改。"""
    work_date = (ctx.body.get("work_date") or "").strip()
    start = (ctx.body.get("start_time") or "").strip()
    end = (ctx.body.get("end_time") or "").strip()
    content = (ctx.body.get("content") or "").strip()

    try:
        dayutil.parse_date(work_date)
    except Exception:
        raise ApiError(400, "加班日期格式不正確")
    if not dayutil.validate_time(start) or not dayutil.validate_time(end):
        raise ApiError(400, "時間格式不正確")
    if start == end:
        raise ApiError(400, "開始與結束時間不能相同")
    if len(content) < 4:
        raise ApiError(400, "請簡述加班內容（至少 4 個字）")
    if len(content) > 2000:
        raise ApiError(400, "加班內容過長")

    today = dayutil.today_str()
    if work_date > today:
        raise ApiError(400, "不能登記未來日期的加班")

    dup = ctx.conn.execute(
        "SELECT ticket_no FROM records WHERE user_id=? AND work_date=? AND start_time=? "
        "AND end_time=? AND status<>'voided'",
        (ctx.user["id"], work_date, start, end),
    ).fetchone()
    if dup:
        raise ApiError(409, "同一天同時段已經登記過了（單號 {}）".format(dup["ticket_no"]))

    hours = dayutil.calc_hours(start, end)
    if hours > dayutil.MAX_HOURS:
        raise ApiError(400, "算出來是 {} 小時，看起來時間填錯了（結束時間比開始時間早，"
                            "系統會當成跨夜）。單筆上限 {} 小時，跨夜請分兩筆登記。".format(
                                hours, dayutil.MAX_HOURS))
    dt = dayutil.day_type(ctx.conn, work_date)
    recipients = mailer.resolve_recipients(ctx.conn, ctx.user)
    approver = recipients["approver"]
    ticket_no = dayutil.make_ticket_no(ctx.conn, work_date)
    token = secrets.token_urlsafe(24)
    stamped_at = db.now_str()

    cur = ctx.conn.execute(
        "INSERT INTO records(ticket_no,user_id,user_name,user_dept,work_date,start_time,"
        "end_time,hours,content,day_type,day_type_text,stamped_at,status,approver_id,"
        "approver_name,approver_email,approve_token,client_ip) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticket_no, ctx.user["id"], ctx.user["name"], ctx.user["dept"], work_date, start,
         end, hours, content, dt["code"], dt["text"], stamped_at, "logged",
         approver["id"] if approver else None,
         approver["name"] if approver else "",
         (approver.get("email") or "") if approver else "",
         token, ctx.ip),
    )
    record_id = cur.lastrowid
    record = ctx.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()

    mailer.queue_notify(ctx.conn, record, recipients)
    has_mail = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM mails WHERE record_id=?", (record_id,)
    ).fetchone()["c"]
    if has_mail:
        ctx.conn.execute("UPDATE records SET status='notified' WHERE id=?", (record_id,))

    db.log_audit(ctx.conn, ctx.user["account"], "overtime_logged", ticket_no,
                 "{} {}-{}（{}h）".format(work_date, start, end, hours), ctx.ip)

    record = ctx.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    mailer.wake(ctx.conn)
    return {
        "ok": True,
        "record": record_json(ctx.conn, record, with_mails=True),
        "routing": recipients,
    }


def _own_record(ctx, record_id):
    row = ctx.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if not row:
        raise ApiError(404, "找不到這筆登記")
    if row["user_id"] != ctx.user["id"] and ctx.user["role"] != "admin":
        raise ApiError(403, "沒有權限查看這筆登記")
    return row


def get_record(ctx):
    row = _own_record(ctx, ctx.path_params["id"])
    return {"record": record_json(ctx.conn, row, with_mails=True)}


def post_apply(ctx):
    """送出正式加班申請（需主管已確認）。"""
    row = _own_record(ctx, ctx.path_params["id"])
    if row["user_id"] != ctx.user["id"]:
        raise ApiError(403, "只能送出自己的申請")
    if row["status"] == "applied":
        raise ApiError(409, "這筆已經送出正式申請了")
    if row["status"] != "confirmed":
        raise ApiError(409, "主管確認後才能送出正式申請（目前狀態：{}）".format(
            dayutil.status_text(row["status"])))

    note = (ctx.body.get("note") or "").strip()[:2000]
    applied_at = db.now_str()
    ctx.conn.execute(
        "UPDATE records SET status='applied', applied_at=?, apply_note=? WHERE id=?",
        (applied_at, note, row["id"]),
    )
    record = ctx.conn.execute("SELECT * FROM records WHERE id=?", (row["id"],)).fetchone()
    recipients = mailer.resolve_recipients(ctx.conn, ctx.user)
    mailer.queue_applied(ctx.conn, record, recipients["cc"], record["approver_email"])
    db.log_audit(ctx.conn, ctx.user["account"], "overtime_applied", record["ticket_no"],
                 ip=ctx.ip)
    mailer.wake(ctx.conn)
    return {"ok": True, "record": record_json(ctx.conn, record, with_mails=True)}


def post_void(ctx):
    """作廢一筆登記。時間戳與紀錄都保留，只改狀態，維持不可竄改。"""
    row = _own_record(ctx, ctx.path_params["id"])
    if row["user_id"] != ctx.user["id"] and ctx.user["role"] != "admin":
        raise ApiError(403, "沒有權限")
    if row["status"] == "applied":
        raise ApiError(409, "已送出正式申請，請洽管理員處理")
    if row["status"] == "voided":
        return {"ok": True}
    reason = (ctx.body.get("reason") or "").strip()[:500]
    ctx.conn.execute(
        "UPDATE records SET status='voided', voided_at=?, void_reason=? WHERE id=?",
        (db.now_str(), reason, row["id"]),
    )
    db.log_audit(ctx.conn, ctx.user["account"], "overtime_voided", row["ticket_no"],
                 reason, ctx.ip)
    return {"ok": True}


def get_my_records(ctx):
    rows = ctx.conn.execute(
        "SELECT * FROM records WHERE user_id=? ORDER BY id DESC LIMIT 100",
        (ctx.user["id"],),
    )
    return {"records": [record_json(ctx.conn, r) for r in rows]}


# ---------- 簽核端 ----------

def get_approvals(ctx):
    status = (ctx.query.get("status") or ["pending"])[0]
    if status == "pending":
        where = "approver_id=? AND status IN ('logged','notified')"
        params = (ctx.user["id"],)
    elif status == "all":
        where = "approver_id=?"
        params = (ctx.user["id"],)
    else:
        where = "approver_id=? AND status=?"
        params = (ctx.user["id"], status)
    rows = ctx.conn.execute(
        "SELECT * FROM records WHERE {} ORDER BY id DESC LIMIT 200".format(where), params
    )
    return {"records": [record_json(ctx.conn, r) for r in rows]}


def _apply_decision(ctx, row, decision, comment, actor_label):
    if row["status"] in ("confirmed", "rejected"):
        raise ApiError(409, "這筆已經處理過了（{}）".format(
            dayutil.status_text(row["status"])))
    if row["status"] == "voided":
        raise ApiError(409, "這筆已作廢")
    if row["status"] == "applied":
        raise ApiError(409, "這筆已送出正式申請")

    new_status = "confirmed" if decision == "approve" else "rejected"
    ctx.conn.execute(
        "UPDATE records SET status=?, decided_at=?, decision_comment=? WHERE id=?",
        (new_status, db.now_str(), comment[:1000], row["id"]),
    )
    record = ctx.conn.execute("SELECT * FROM records WHERE id=?", (row["id"],)).fetchone()

    employee = ctx.conn.execute(
        "SELECT * FROM users WHERE id=?", (record["user_id"],)
    ).fetchone()
    cc = []
    if employee:
        cc = mailer.resolve_recipients(ctx.conn, employee)["cc"]
    mailer.queue_decision(ctx.conn, record, decision,
                          employee["email"] if employee else "", cc)
    db.log_audit(ctx.conn, actor_label,
                 "overtime_" + ("approved" if decision == "approve" else "rejected"),
                 record["ticket_no"], comment[:200], ctx.ip)
    mailer.wake(ctx.conn)
    return record


def post_decide(ctx):
    decision = (ctx.body.get("decision") or "").strip()
    if decision not in ("approve", "reject"):
        raise ApiError(400, "decision 必須是 approve 或 reject")
    comment = (ctx.body.get("comment") or "").strip()
    row = ctx.conn.execute(
        "SELECT * FROM records WHERE id=?", (ctx.path_params["id"],)
    ).fetchone()
    if not row:
        raise ApiError(404, "找不到這筆登記")
    if row["approver_id"] != ctx.user["id"] and ctx.user["role"] != "admin":
        raise ApiError(403, "你不是這筆登記的簽核主管")
    record = _apply_decision(ctx, row, decision, comment, ctx.user["account"])
    return {"ok": True, "record": record_json(ctx.conn, record)}


# ---------- 免登入簽核連結（信件按鈕） ----------

def get_approve_by_token(ctx):
    token = (ctx.query.get("t") or [""])[0]
    if not token or len(token) < 16:
        raise ApiError(400, "連結不正確")
    row = ctx.conn.execute(
        "SELECT * FROM records WHERE approve_token=?", (token,)
    ).fetchone()
    if not row:
        raise ApiError(404, "連結已失效或不存在")
    return {
        "record": {
            "ticket_no": row["ticket_no"],
            "user_name": row["user_name"],
            "user_dept": row["user_dept"],
            "work_date": row["work_date"],
            "day_type_text": row["day_type_text"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "hours": row["hours"],
            "content": row["content"],
            "stamped_at": row["stamped_at"],
            "status": row["status"],
            "status_text": dayutil.status_text(row["status"]),
            "approver_name": row["approver_name"],
            "decided_at": row["decided_at"],
            "decision_comment": row["decision_comment"],
        }
    }


def post_approve_by_token(ctx):
    token = (ctx.body.get("token") or "").strip()
    decision = (ctx.body.get("decision") or "").strip()
    comment = (ctx.body.get("comment") or "").strip()
    if decision not in ("approve", "reject"):
        raise ApiError(400, "decision 必須是 approve 或 reject")
    row = ctx.conn.execute(
        "SELECT * FROM records WHERE approve_token=?", (token,)
    ).fetchone()
    if not row:
        raise ApiError(404, "連結已失效或不存在")
    actor = "簽核連結・{}".format(row["approver_name"] or "未知主管")
    record = _apply_decision(ctx, row, decision, comment, actor)
    return {"ok": True, "record": {
        "status": record["status"],
        "status_text": dayutil.status_text(record["status"]),
        "decided_at": record["decided_at"],
    }}
