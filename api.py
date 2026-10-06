"""員工端 / 簽核端 / 帳號相關 API。"""

import hashlib
import json
import secrets
from datetime import timedelta

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


def proof_code(conn, row):
    """簽核證明上的驗證碼。

    用本站專屬的祕密值加上這筆紀錄的關鍵欄位算出來，
    別人光看證明圖沒辦法自己編一組出來；管理員可以在後台核對。
    """
    secret = db.get_setting(conn, "proof_secret", "")
    if not secret:
        secret = secrets.token_hex(16)
        db.set_setting(conn, "proof_secret", secret)
    raw = "|".join([
        str(row["ticket_no"]), str(row["stamped_at"]), str(row["decided_at"] or ""),
        str(row["approver_name"] or ""), secret,
    ])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
    return "-".join([digest[0:4], digest[4:8], digest[8:12]])


def _load_segments(row):
    """計時器送出的登記會帶著每一段的明細；手動補登的沒有。"""
    try:
        raw = row["segments"]
    except (KeyError, IndexError):
        return []
    if not raw:
        return []
    try:
        return json.loads(raw)
    except ValueError:
        return []


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
        "dept_head_name": row["dept_head_name"],
        "dept_head_email": row["dept_head_email"],
        "decided_at": row["decided_at"],
        "decision_comment": row["decision_comment"],
        "applied_at": row["applied_at"],
        "apply_note": row["apply_note"],
        "voided_at": row["voided_at"],
        "void_reason": row["void_reason"],
        "proof_code": proof_code(conn, row),
        "segments": _load_segments(row),
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
        "apply_redirect_url": db.get_setting(ctx.conn, "apply_redirect_url", ""),
        "apply_redirect_label": db.get_setting(
            ctx.conn, "apply_redirect_label", "前往 HR 系統填正式申請"),
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

def selectable_people(ctx):
    """可以被選為主管的人：啟用中、有 Email、且不是自己。

    不能選自己 —— 否則就能自己核准自己的加班，簽核就失去意義了。
    """
    return [
        {"id": r["id"], "name": r["name"], "dept": r["dept"], "email": r["email"]}
        for r in ctx.conn.execute(
            "SELECT id,name,dept,email FROM users WHERE active=1 AND email<>'' "
            "AND id<>? ORDER BY dept, name", (ctx.user["id"],)
        )
    ]


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
        "people": selectable_people(ctx),
        "defaults": {
            "user_name": ctx.user["name"],
            "approver_id": ctx.user["approver_id"],
            "dept_head_id": ctx.user["dept_head_id"],
        },
        "holidays": holidays,
        "records": records,
        "timer": timer_state(ctx),
        "server_time": db.now_str(),
    }


def post_overtime(ctx):
    """登記加班：時間戳由伺服器蓋，寫入後不可修改。

    兩種來源：
      source=timer  → 把某一天「開始／暫停／繼續」累計的每一段加總成一筆（主要用法）
      其他          → 手動填開始、結束時間（忘了按開始時的補登）
    """
    if (ctx.body.get("source") or "") == "timer":
        return _post_overtime_from_timer(ctx)

    work_date = (ctx.body.get("work_date") or "").strip()
    start = (ctx.body.get("start_time") or "").strip()
    end = (ctx.body.get("end_time") or "").strip()

    try:
        dayutil.parse_date(work_date)
    except Exception:
        raise ApiError(400, "加班日期格式不正確")
    if not dayutil.validate_time(start) or not dayutil.validate_time(end):
        raise ApiError(400, "時間格式不正確")
    if start == end:
        raise ApiError(400, "開始與結束時間不能相同")
    content = _check_content(ctx)
    if work_date > dayutil.today_str():
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
    return _create_record(ctx, work_date, start, end, hours, content)


def _post_overtime_from_timer(ctx):
    """一天做完後，把當天累計的每一段確認送出成一筆登記。"""
    work_date = (ctx.body.get("work_date") or "").strip() or dayutil.today_str()
    try:
        dayutil.parse_date(work_date)
    except Exception:
        raise ApiError(400, "日期格式不正確")
    content = _check_content(ctx)

    # 還在計時就幫他按暫停：送出的那一刻就是今天的收工時間
    _normalize_running(ctx)
    now = db.now_str()
    ctx.conn.execute(
        "UPDATE work_segments SET ended_at=? WHERE user_id=? AND work_date=? "
        "AND ended_at IS NULL AND record_id IS NULL", (now, ctx.user["id"], work_date))

    segs = _open_segments(ctx, work_date)
    if not segs:
        raise ApiError(400, "{} 沒有尚未送出的計時紀錄".format(work_date))
    total = sum(_seg_seconds(s, now) for s in segs)
    if total < 60:
        raise ApiError(400, "累計不到 1 分鐘，不需要送出；可以把這段刪掉")

    hours = round(total / 3600.0, 2)
    start = segs[0]["started_at"][11:16]
    last_end = max(s["ended_at"] for s in segs)
    end = "24:00" if last_end[:10] > work_date else last_end[11:16]
    detail = [{
        "start": s["started_at"][11:19],
        "end": "24:00:00" if s["ended_at"][:10] > work_date else s["ended_at"][11:19],
        "seconds": _seg_seconds(s, now),
    } for s in segs]

    result = _create_record(ctx, work_date, start, end, hours, content,
                            segments=json.dumps(detail, ensure_ascii=False))
    ctx.conn.execute(
        "UPDATE work_segments SET record_id=? WHERE id IN ({})".format(
            ",".join("?" * len(segs))),
        [result["record"]["id"]] + [s["id"] for s in segs])
    result["timer"] = timer_state(ctx)
    return result


def _check_content(ctx):
    content = (ctx.body.get("content") or "").strip()
    if len(content) < 4:
        raise ApiError(400, "請簡述加班內容（至少 4 個字）")
    if len(content) > 2000:
        raise ApiError(400, "加班內容過長")
    return content


def _create_record(ctx, work_date, start, end, hours, content, segments=""):
    user_name = (ctx.body.get("user_name") or "").strip()[:40] or ctx.user["name"]
    approver = _pick_person(ctx, ctx.body.get("approver_id"), "直屬主管",
                            fallback=ctx.user["approver_id"])
    dept_head = _pick_person(ctx, ctx.body.get("dept_head_id"), "部門主管",
                             fallback=ctx.user["dept_head_id"])
    if not approver:
        raise ApiError(400, "請選擇你的直屬主管（要由他確認這次加班）")

    dt = dayutil.day_type(ctx.conn, work_date)
    recipients = mailer.resolve_recipients(ctx.conn, ctx.user)
    ticket_no = dayutil.make_ticket_no(ctx.conn, work_date)
    token = secrets.token_urlsafe(24)
    stamped_at = db.now_str()

    cur = ctx.conn.execute(
        "INSERT INTO records(ticket_no,user_id,user_name,user_dept,work_date,start_time,"
        "end_time,hours,content,day_type,day_type_text,stamped_at,status,approver_id,"
        "approver_name,approver_email,dept_head_id,dept_head_name,dept_head_email,"
        "approve_token,client_ip,segments) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticket_no, ctx.user["id"], user_name, ctx.user["dept"], work_date, start,
         end, hours, content, dt["code"], dt["text"], stamped_at, "logged",
         approver["id"], approver["name"], approver["email"],
         dept_head["id"] if dept_head else None,
         dept_head["name"] if dept_head else "",
         dept_head["email"] if dept_head else "",
         token, ctx.ip, segments),
    )
    record_id = cur.lastrowid
    record = ctx.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()

    mailer.queue_notify(ctx.conn, record, recipients["cc"])

    # 記住這次的選擇，下次登記自動帶入
    ctx.conn.execute(
        "UPDATE users SET approver_id=?, dept_head_id=? WHERE id=?",
        (approver["id"], dept_head["id"] if dept_head else None, ctx.user["id"]))
    has_mail = ctx.conn.execute(
        "SELECT COUNT(*) AS c FROM mails WHERE record_id=?", (record_id,)
    ).fetchone()["c"]
    if has_mail:
        ctx.conn.execute("UPDATE records SET status='notified' WHERE id=?", (record_id,))

    db.log_audit(ctx.conn, ctx.user["account"], "overtime_logged", ticket_no,
                 "{} {}-{}（{}h{}）".format(work_date, start, end, hours,
                                          "，計時器" if segments else ""), ctx.ip)

    record = ctx.conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    mailer.wake(ctx.conn)
    return {
        "ok": True,
        "record": record_json(ctx.conn, record, with_mails=True),
        "routing": recipients,
    }


# ---------- 計時器：開始 → 暫停 → 繼續，一天做完再一次送出 ----------

def _seg_seconds(seg, now):
    end = seg["ended_at"] or now
    return max(0, int((db.parse_ts(end) - db.parse_ts(seg["started_at"])).total_seconds()))


def _open_segments(ctx, work_date=None):
    """還沒送出的段落（依開始時間排序）。"""
    sql = "SELECT * FROM work_segments WHERE user_id=? AND record_id IS NULL"
    params = [ctx.user["id"]]
    if work_date:
        sql += " AND work_date=?"
        params.append(work_date)
    return list(ctx.conn.execute(sql + " ORDER BY started_at, id", params))


def _running_segment(ctx):
    return ctx.conn.execute(
        "SELECT * FROM work_segments WHERE user_id=? AND ended_at IS NULL "
        "AND record_id IS NULL ORDER BY id DESC LIMIT 1", (ctx.user["id"],)
    ).fetchone()


def _normalize_running(ctx):
    """忘了按暫停就過了午夜：在 00:00 把那一段切開，前一天的工時歸前一天。"""
    seg = _running_segment(ctx)
    today = dayutil.today_str()
    while seg is not None and seg["work_date"] < today:
        next_day = (dayutil.parse_date(seg["work_date"]) + timedelta(days=1)).isoformat()
        midnight = next_day + " 00:00:00"
        ctx.conn.execute("UPDATE work_segments SET ended_at=? WHERE id=?",
                         (midnight, seg["id"]))
        cur = ctx.conn.execute(
            "INSERT INTO work_segments(user_id,work_date,started_at,client_ip) "
            "VALUES(?,?,?,?)", (ctx.user["id"], next_day, midnight, seg["client_ip"]))
        seg = ctx.conn.execute("SELECT * FROM work_segments WHERE id=?",
                               (cur.lastrowid,)).fetchone()
    return seg


def timer_state(ctx):
    running = _normalize_running(ctx)
    now = db.now_str()
    days = {}
    for s in _open_segments(ctx):
        day = days.setdefault(s["work_date"], {
            "work_date": s["work_date"],
            "day_type_text": dayutil.day_type(ctx.conn, s["work_date"])["text"],
            "segments": [], "closed_seconds": 0, "running": False,
        })
        seconds = _seg_seconds(s, now)
        day["segments"].append({
            "id": s["id"],
            "started_at": s["started_at"],
            "ended_at": s["ended_at"],
            "seconds": seconds,
        })
        if s["ended_at"]:
            day["closed_seconds"] += seconds
        else:
            day["running"] = True
    return {
        "today": dayutil.today_str(),
        "server_time": now,
        "running": running is not None,
        "running_since": running["started_at"] if running else None,
        "days": [days[k] for k in sorted(days)],
    }


def get_timer(ctx):
    return {"timer": timer_state(ctx)}


def post_timer_start(ctx):
    """開始／繼續：開一段新的計時。"""
    if _normalize_running(ctx) is not None:
        raise ApiError(409, "已經在計時中了")
    today = dayutil.today_str()
    resumed = bool(_open_segments(ctx, today))
    now = db.now_str()
    ctx.conn.execute(
        "INSERT INTO work_segments(user_id,work_date,started_at,client_ip) VALUES(?,?,?,?)",
        (ctx.user["id"], today, now, ctx.ip))
    db.log_audit(ctx.conn, ctx.user["account"], "timer_resume" if resumed else "timer_start",
                 today, now[11:], ctx.ip)
    return {"ok": True, "timer": timer_state(ctx)}


def post_timer_pause(ctx):
    seg = _normalize_running(ctx)
    if seg is None:
        raise ApiError(409, "目前沒有在計時")
    now = db.now_str()
    ctx.conn.execute("UPDATE work_segments SET ended_at=? WHERE id=?", (now, seg["id"]))
    db.log_audit(ctx.conn, ctx.user["account"], "timer_pause", seg["work_date"],
                 "{} - {}".format(seg["started_at"][11:], now[11:]), ctx.ip)
    return {"ok": True, "timer": timer_state(ctx)}


def delete_timer_segment(ctx):
    """刪掉一段還沒送出的計時（例如忘了按暫停、按錯）。操作紀錄會留下來。"""
    seg = ctx.conn.execute(
        "SELECT * FROM work_segments WHERE id=? AND user_id=?",
        (ctx.path_params["id"], ctx.user["id"])).fetchone()
    if not seg:
        raise ApiError(404, "找不到這段計時")
    if seg["record_id"] is not None:
        raise ApiError(409, "這段已經送出，不能刪除")
    if seg["ended_at"] is None:
        raise ApiError(409, "正在計時中，請先暫停再刪除")
    ctx.conn.execute("DELETE FROM work_segments WHERE id=?", (seg["id"],))
    db.log_audit(ctx.conn, ctx.user["account"], "timer_segment_deleted", seg["work_date"],
                 "{} - {}".format(seg["started_at"][11:], seg["ended_at"][11:]), ctx.ip)
    return {"ok": True, "timer": timer_state(ctx)}


def _pick_person(ctx, raw_id, label, fallback=None):
    """把前端傳來的人員 id 換成 {id,name,email}，順便驗證。"""
    person_id = raw_id if raw_id not in ("", None) else fallback
    if person_id in ("", None):
        return None
    try:
        person_id = int(person_id)
    except (TypeError, ValueError):
        raise ApiError(400, "{}的選擇不正確".format(label))
    if person_id == ctx.user["id"]:
        raise ApiError(400, "{}不能選自己".format(label))
    row = ctx.conn.execute(
        "SELECT id,name,email,active FROM users WHERE id=?", (person_id,)).fetchone()
    if not row or not row["active"]:
        raise ApiError(400, "{}的帳號不存在或已停用，請重新選擇".format(label))
    if not row["email"]:
        raise ApiError(400, "{}（{}）沒有填 Email，收不到通知信，請聯絡管理員".format(
            label, row["name"]))
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


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
    # 計時器送出的登記作廢後，那幾段計時回到「未送出」，修正後可以重新送出
    ctx.conn.execute("UPDATE work_segments SET record_id=NULL WHERE record_id=?",
                     (row["id"],))
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
