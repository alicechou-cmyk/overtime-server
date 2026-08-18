"""後台 API：員工與簽核路由管理、加班紀錄總覽、寄件匣、假日表、系統設定。

全部端點都要求 role='admin'。
"""

import csv
import io
import json
import re

import auth
import db
import dayutil
import mailer
from api import ApiError, RawResponse, record_json, user_public

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email):
    return bool(EMAIL_RE.match((email or "").strip()))


def _cc_rows(conn, user_id):
    return [
        {"label": r["label"], "email": r["email"]}
        for r in conn.execute(
            "SELECT label,email FROM user_cc WHERE user_id=? ORDER BY seq,id", (user_id,)
        )
    ]


def _user_admin_json(conn, row):
    out = user_public(row)
    out.update({
        "active": bool(row["active"]),
        "approver_id": row["approver_id"],
        "dept_head_id": row["dept_head_id"],
        "use_default_cc": bool(row["use_default_cc"]),
        "created_at": row["created_at"],
        "cc": _cc_rows(conn, row["id"]),
    })
    if row["approver_id"]:
        ap = conn.execute(
            "SELECT name,email,active FROM users WHERE id=?", (row["approver_id"],)
        ).fetchone()
        out["approver_name"] = ap["name"] if ap else ""
        out["approver_email"] = ap["email"] if ap else ""
        out["approver_active"] = bool(ap["active"]) if ap else False
    else:
        out["approver_name"] = ""
        out["approver_email"] = ""
        out["approver_active"] = False
    if row["dept_head_id"]:
        dh = conn.execute(
            "SELECT name,email FROM users WHERE id=?", (row["dept_head_id"],)).fetchone()
        out["dept_head_name"] = dh["name"] if dh else ""
        out["dept_head_email"] = dh["email"] if dh else ""
    else:
        out["dept_head_name"] = ""
        out["dept_head_email"] = ""
    out["stats"] = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(hours),0) AS hours FROM records "
        "WHERE user_id=? AND status<>'voided'",
        (row["id"],),
    ).fetchone()["total"]
    return out


# ---------- 員工 / 簽核路由 ----------

def get_users(ctx):
    rows = ctx.conn.execute("SELECT * FROM users ORDER BY active DESC, id")
    users = [_user_admin_json(ctx.conn, r) for r in rows]
    try:
        default_cc = json.loads(db.get_setting(ctx.conn, "default_cc", "[]") or "[]")
    except Exception:
        default_cc = []
    issues = []
    for u in users:
        if not u["active"]:
            continue
        if u["role"] != "admin" and not u["approver_id"]:
            issues.append({"user": u["name"], "issue": "尚未指定簽核主管"})
        elif u["approver_id"] and not u["approver_email"]:
            issues.append({"user": u["name"], "issue": "簽核主管沒有 Email"})
        elif u["approver_id"] and not u["approver_active"]:
            issues.append({"user": u["name"], "issue": "簽核主管帳號已停用"})
        if not u["email"]:
            issues.append({"user": u["name"], "issue": "本人沒有 Email，收不到通知"})
        if u["must_change_password"]:
            issues.append({"user": u["name"], "issue": "尚未變更初始密碼"})
    return {"users": users, "default_cc": default_cc, "issues": issues}


def _validate_user_payload(ctx, body, creating, user_id=None):
    account = (body.get("account") or "").strip()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    if creating:
        if not re.match(r"^[A-Za-z0-9._-]{3,32}$", account):
            raise ApiError(400, "帳號請用 3-32 個英數字、點、底線或減號")
        dup = ctx.conn.execute(
            "SELECT 1 FROM users WHERE account=?", (account,)
        ).fetchone()
        if dup:
            raise ApiError(409, "帳號「{}」已存在".format(account))
    if not name:
        raise ApiError(400, "請填姓名")
    if email and not _valid_email(email):
        raise ApiError(400, "Email 格式不正確：{}".format(email))
    role = body.get("role") or "user"
    if role not in ("user", "admin"):
        raise ApiError(400, "role 只能是 user 或 admin")

    approver_id = body.get("approver_id")
    if approver_id in ("", None):
        approver_id = None
    else:
        approver_id = int(approver_id)
        if user_id and approver_id == user_id:
            raise ApiError(400, "不能把自己設為自己的簽核主管")
        ap = ctx.conn.execute("SELECT id FROM users WHERE id=?", (approver_id,)).fetchone()
        if not ap:
            raise ApiError(400, "指定的簽核主管不存在")
        if user_id and _would_loop(ctx.conn, user_id, approver_id):
            raise ApiError(400, "簽核關係會形成循環，請重新指定")

    dept_head_id = body.get("dept_head_id")
    if dept_head_id in ("", None):
        dept_head_id = None
    else:
        dept_head_id = int(dept_head_id)
        if user_id and dept_head_id == user_id:
            raise ApiError(400, "不能把自己設為自己的部門主管")
        if not ctx.conn.execute(
                "SELECT 1 FROM users WHERE id=?", (dept_head_id,)).fetchone():
            raise ApiError(400, "指定的部門主管不存在")

    cc = body.get("cc") or []
    clean_cc = []
    for item in cc:
        email_v = (item.get("email") or "").strip()
        if not email_v:
            continue
        if not _valid_email(email_v):
            raise ApiError(400, "副本 Email 格式不正確：{}".format(email_v))
        clean_cc.append({"label": (item.get("label") or "").strip()[:40], "email": email_v})
    return {
        "account": account,
        "name": name,
        "email": email,
        "emp_no": (body.get("emp_no") or "").strip()[:40],
        "dept": (body.get("dept") or "").strip()[:60],
        "role": role,
        "approver_id": approver_id,
        "dept_head_id": dept_head_id,
        "use_default_cc": 1 if body.get("use_default_cc", True) else 0,
        "active": 1 if body.get("active", True) else 0,
        "cc": clean_cc,
    }


def _would_loop(conn, user_id, approver_id):
    """沿著簽核鏈往上找，看會不會繞回自己。"""
    seen = set()
    cur = approver_id
    while cur and cur not in seen:
        if cur == user_id:
            return True
        seen.add(cur)
        row = conn.execute("SELECT approver_id FROM users WHERE id=?", (cur,)).fetchone()
        cur = row["approver_id"] if row else None
    return False


def _save_cc(conn, user_id, cc):
    conn.execute("DELETE FROM user_cc WHERE user_id=?", (user_id,))
    for i, item in enumerate(cc):
        conn.execute(
            "INSERT INTO user_cc(user_id,label,email,seq) VALUES(?,?,?,?)",
            (user_id, item["label"], item["email"], i),
        )


def post_user(ctx):
    data = _validate_user_payload(ctx, ctx.body, creating=True)
    password = (ctx.body.get("password") or "").strip()
    generated = ""
    if password:
        err = auth.check_password_strength(password)
        if err:
            raise ApiError(400, err)
    else:
        password = auth.generate_password()
        generated = password
    ts = db.now_str()
    cur = ctx.conn.execute(
        "INSERT INTO users(account,name,email,emp_no,dept,role,approver_id,dept_head_id,"
        "use_default_cc,password_hash,must_change_password,active,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (data["account"], data["name"], data["email"], data["emp_no"], data["dept"],
         data["role"], data["approver_id"], data["dept_head_id"], data["use_default_cc"],
         auth.hash_password(password), 1, data["active"], ts, ts),
    )
    user_id = cur.lastrowid
    _save_cc(ctx.conn, user_id, data["cc"])
    db.log_audit(ctx.conn, ctx.user["account"], "user_created", data["account"],
                 ip=ctx.ip)
    row = ctx.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"ok": True, "user": _user_admin_json(ctx.conn, row),
            "generated_password": generated}


def patch_user(ctx):
    user_id = ctx.path_params["id"]
    row = ctx.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ApiError(404, "找不到這個帳號")
    data = _validate_user_payload(ctx, ctx.body, creating=False, user_id=user_id)

    # 別把最後一個可用的管理員關掉或降級
    if row["role"] == "admin" and (data["role"] != "admin" or not data["active"]):
        others = ctx.conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND active=1 AND id<>?",
            (user_id,),
        ).fetchone()["c"]
        if others == 0:
            raise ApiError(400, "至少要保留一位啟用中的管理員")

    ctx.conn.execute(
        "UPDATE users SET name=?,email=?,emp_no=?,dept=?,role=?,approver_id=?,"
        "dept_head_id=?,use_default_cc=?,active=?,updated_at=? WHERE id=?",
        (data["name"], data["email"], data["emp_no"], data["dept"], data["role"],
         data["approver_id"], data["dept_head_id"], data["use_default_cc"],
         data["active"], db.now_str(), user_id),
    )
    _save_cc(ctx.conn, user_id, data["cc"])
    if not data["active"]:
        auth.destroy_user_sessions(ctx.conn, user_id)
    db.log_audit(ctx.conn, ctx.user["account"], "user_updated", row["account"], ip=ctx.ip)
    updated = ctx.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"ok": True, "user": _user_admin_json(ctx.conn, updated)}


def post_reset_password(ctx):
    user_id = ctx.path_params["id"]
    row = ctx.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ApiError(404, "找不到這個帳號")
    password = (ctx.body.get("password") or "").strip()
    if password:
        err = auth.check_password_strength(password)
        if err:
            raise ApiError(400, err)
    else:
        password = auth.generate_password()
    ctx.conn.execute(
        "UPDATE users SET password_hash=?, must_change_password=1, updated_at=? WHERE id=?",
        (auth.hash_password(password), db.now_str(), user_id),
    )
    auth.destroy_user_sessions(ctx.conn, user_id)
    auth.clear_fails(ctx.conn, row["account"])
    db.log_audit(ctx.conn, ctx.user["account"], "password_reset", row["account"], ip=ctx.ip)
    return {"ok": True, "password": password}


def delete_user(ctx):
    user_id = ctx.path_params["id"]
    row = ctx.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ApiError(404, "找不到這個帳號")
    if user_id == ctx.user["id"]:
        raise ApiError(400, "不能刪除自己")
    has_records = ctx.conn.execute(
        "SELECT 1 FROM records WHERE user_id=? LIMIT 1", (user_id,)
    ).fetchone()
    if has_records:
        raise ApiError(
            409, "這個帳號有加班紀錄，為了留存證據不能刪除；請改成「停用」。"
        )
    if row["role"] == "admin":
        others = ctx.conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND active=1 AND id<>?",
            (user_id,),
        ).fetchone()["c"]
        if others == 0:
            raise ApiError(400, "至少要保留一位啟用中的管理員")
    ctx.conn.execute("UPDATE users SET approver_id=NULL WHERE approver_id=?", (user_id,))
    ctx.conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.log_audit(ctx.conn, ctx.user["account"], "user_deleted", row["account"], ip=ctx.ip)
    return {"ok": True}


def get_routing_preview(ctx):
    """預覽某位員工的通知會寄給誰（後台設定完可以馬上確認）。"""
    user_id = ctx.path_params["id"]
    row = ctx.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ApiError(404, "找不到這個帳號")
    return {"routing": mailer.resolve_recipients(ctx.conn, row),
            "user": {"id": row["id"], "name": row["name"]}}


# ---------- 加班紀錄 ----------

def _record_filters(query):
    where = []
    params = []
    user_id = (query.get("user_id") or [""])[0]
    status = (query.get("status") or [""])[0]
    date_from = (query.get("from") or [""])[0]
    date_to = (query.get("to") or [""])[0]
    keyword = (query.get("q") or [""])[0].strip()
    if user_id:
        where.append("user_id=?")
        params.append(int(user_id))
    if status:
        where.append("status=?")
        params.append(status)
    if date_from:
        where.append("work_date>=?")
        params.append(date_from)
    if date_to:
        where.append("work_date<=?")
        params.append(date_to)
    if keyword:
        where.append("(user_name LIKE ? OR content LIKE ? OR ticket_no LIKE ?)")
        like = "%{}%".format(keyword)
        params += [like, like, like]
    sql = " WHERE " + " AND ".join(where) if where else ""
    return sql, params


def get_records(ctx):
    sql, params = _record_filters(ctx.query)
    rows = ctx.conn.execute(
        "SELECT * FROM records{} ORDER BY id DESC LIMIT 500".format(sql), params
    )
    records = [record_json(ctx.conn, r) for r in rows]
    summary = ctx.conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(hours),0) AS hours FROM records{}".format(sql),
        params,
    ).fetchone()
    by_status = {
        r["status"]: r["c"]
        for r in ctx.conn.execute(
            "SELECT status, COUNT(*) AS c FROM records{} GROUP BY status".format(sql), params
        )
    }
    return {
        "records": records,
        "summary": {"total": summary["total"], "hours": round(summary["hours"], 1),
                    "by_status": by_status},
    }


def get_records_csv(ctx):
    sql, params = _record_filters(ctx.query)
    rows = ctx.conn.execute(
        "SELECT * FROM records{} ORDER BY work_date, id".format(sql), params
    ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["單號", "姓名", "部門", "加班日期", "日別", "開始", "結束", "小時",
                     "加班內容", "系統時間戳", "狀態", "直屬主管", "部門主管", "簽核時間",
                     "主管備註", "正式申請時間", "補充說明"])
    for r in rows:
        writer.writerow([
            r["ticket_no"], r["user_name"], r["user_dept"], r["work_date"],
            r["day_type_text"], r["start_time"], r["end_time"], r["hours"],
            r["content"], r["stamped_at"], dayutil.status_text(r["status"]),
            r["approver_name"], r["dept_head_name"],
            r["decided_at"] or "", r["decision_comment"],
            r["applied_at"] or "", r["apply_note"],
        ])
    # 加 BOM，Excel 開啟中文才不會亂碼
    body = "﻿" + buf.getvalue()
    return RawResponse(
        body,
        "text/csv; charset=utf-8",
        {"Content-Disposition": 'attachment; filename="overtime_records.csv"'},
    )


def get_record_detail(ctx):
    row = ctx.conn.execute(
        "SELECT * FROM records WHERE id=?", (ctx.path_params["id"],)
    ).fetchone()
    if not row:
        raise ApiError(404, "找不到這筆紀錄")
    return {"record": record_json(ctx.conn, row, with_mails=True)}


# ---------- 寄件匣 ----------

def get_mails(ctx):
    status = (ctx.query.get("status") or [""])[0]
    where, params = "", []
    if status:
        where = " WHERE m.status=?"
        params.append(status)
    rows = ctx.conn.execute(
        "SELECT m.*, r.ticket_no FROM mails m LEFT JOIN records r ON r.id=m.record_id"
        "{} ORDER BY m.id DESC LIMIT 300".format(where),
        params,
    )
    mails = [{
        "id": r["id"],
        "ticket_no": r["ticket_no"] or "",
        "kind": r["kind"],
        "to_email": r["to_email"],
        "to_label": r["to_label"],
        "subject": r["subject"],
        "body": r["body"],
        "status": r["status"],
        "error": r["error"],
        "created_at": r["created_at"],
        "sent_at": r["sent_at"],
    } for r in rows]
    counts = {
        r["status"]: r["c"]
        for r in ctx.conn.execute("SELECT status, COUNT(*) AS c FROM mails GROUP BY status")
    }
    return {"mails": mails, "counts": counts,
            "mail_mode": db.get_setting(ctx.conn, "mail_mode", "preview")}


def post_mail_resend(ctx):
    mail_id = ctx.path_params["id"]
    row = ctx.conn.execute("SELECT * FROM mails WHERE id=?", (mail_id,)).fetchone()
    if not row:
        raise ApiError(404, "找不到這封信")
    if db.get_setting(ctx.conn, "mail_mode", "preview") != "smtp":
        raise ApiError(400, "目前是「模擬」模式，請先在系統設定切換為 SMTP 寄送")
    ctx.conn.execute(
        "UPDATE mails SET status='queued', error='' WHERE id=?", (mail_id,)
    )
    db.log_audit(ctx.conn, ctx.user["account"], "mail_resend", str(mail_id), ip=ctx.ip)
    mailer.wake(ctx.conn)
    return {"ok": True}


# ---------- 假日表 ----------

def get_holidays(ctx):
    year = (ctx.query.get("year") or [""])[0]
    if year:
        rows = ctx.conn.execute(
            "SELECT date,name FROM holidays WHERE date LIKE ? ORDER BY date",
            ("{}-%".format(year),),
        )
    else:
        rows = ctx.conn.execute("SELECT date,name FROM holidays ORDER BY date")
    return {"holidays": [{"date": r["date"], "name": r["name"]} for r in rows]}


def post_holiday(ctx):
    date_str = (ctx.body.get("date") or "").strip()
    name = (ctx.body.get("name") or "").strip()
    try:
        dayutil.parse_date(date_str)
    except Exception:
        raise ApiError(400, "日期格式要像 2026-10-10")
    if not name:
        raise ApiError(400, "請填假日名稱")
    ctx.conn.execute(
        "INSERT INTO holidays(date,name) VALUES(?,?) "
        "ON CONFLICT(date) DO UPDATE SET name=excluded.name",
        (date_str, name[:60]),
    )
    db.log_audit(ctx.conn, ctx.user["account"], "holiday_saved", date_str, name, ctx.ip)
    return {"ok": True}


def delete_holiday(ctx):
    date_str = ctx.path_params["date"]
    ctx.conn.execute("DELETE FROM holidays WHERE date=?", (date_str,))
    db.log_audit(ctx.conn, ctx.user["account"], "holiday_deleted", date_str, ip=ctx.ip)
    return {"ok": True}


def post_holidays_import(ctx):
    """貼上多行「2026-10-10,國慶日」批次匯入（也吃 tab 或全形逗號）。"""
    text = ctx.body.get("text") or ""
    added, failed = 0, []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[,\t，]", line, maxsplit=1)
        date_str = parts[0].strip().replace("/", "-")
        name = parts[1].strip() if len(parts) > 1 else "國定假日"
        try:
            dayutil.parse_date(date_str)
        except Exception:
            failed.append(line[:40])
            continue
        ctx.conn.execute(
            "INSERT INTO holidays(date,name) VALUES(?,?) "
            "ON CONFLICT(date) DO UPDATE SET name=excluded.name",
            (date_str, name[:60]),
        )
        added += 1
    db.log_audit(ctx.conn, ctx.user["account"], "holidays_imported",
                 detail="{} 筆".format(added), ip=ctx.ip)
    return {"ok": True, "added": added, "failed": failed}


# ---------- 系統設定 ----------

PUBLIC_SETTING_KEYS = [
    "company_name", "base_url", "mail_mode", "default_cc", "smtp_host", "smtp_port",
    "smtp_security", "smtp_user", "smtp_from", "smtp_from_name", "session_days",
    "apply_redirect_url", "apply_redirect_label",
]


def get_settings(ctx):
    settings = db.all_settings(ctx.conn)
    out = {k: settings.get(k, "") for k in PUBLIC_SETTING_KEYS}
    try:
        out["default_cc"] = json.loads(out.get("default_cc") or "[]")
    except Exception:
        out["default_cc"] = []
    out["smtp_pass_set"] = bool(settings.get("smtp_pass"))
    return {"settings": out}


def put_settings(ctx):
    body = ctx.body or {}
    if "default_cc" in body:
        items = body.get("default_cc") or []
        clean = []
        for item in items:
            email = (item.get("email") or "").strip()
            if not email:
                continue
            if not _valid_email(email):
                raise ApiError(400, "預設副本 Email 格式不正確：{}".format(email))
            clean.append({"label": (item.get("label") or "").strip()[:40], "email": email})
        db.set_setting(ctx.conn, "default_cc", json.dumps(clean, ensure_ascii=False))

    if "mail_mode" in body:
        mode = body["mail_mode"]
        if mode not in ("preview", "smtp"):
            raise ApiError(400, "mail_mode 只能是 preview 或 smtp")
        if mode == "smtp" and not (body.get("smtp_host") or
                                   db.get_setting(ctx.conn, "smtp_host")):
            raise ApiError(400, "要切換成真的寄信，請先填 SMTP 主機")
        db.set_setting(ctx.conn, "mail_mode", mode)

    if "smtp_security" in body and body["smtp_security"] not in ("none", "starttls", "ssl"):
        raise ApiError(400, "smtp_security 只能是 none / starttls / ssl")

    if "apply_redirect_url" in body:
        url = str(body["apply_redirect_url"]).strip()
        if url and not url.startswith(("http://", "https://")):
            raise ApiError(400, "導向網址必須以 http:// 或 https:// 開頭")
        if len(url) > 500:
            raise ApiError(400, "導向網址過長")
        db.set_setting(ctx.conn, "apply_redirect_url", url)

    for key in ["company_name", "base_url", "smtp_host", "smtp_port", "smtp_security",
                "smtp_user", "smtp_from", "smtp_from_name", "session_days",
                "apply_redirect_label"]:
        if key in body:
            db.set_setting(ctx.conn, key, str(body[key]).strip())

    # 密碼欄留空 = 不變更；填 "__clear__" = 清空
    if "smtp_pass" in body:
        value = body["smtp_pass"]
        if value == "__clear__":
            db.set_setting(ctx.conn, "smtp_pass", "")
        elif value:
            db.set_setting(ctx.conn, "smtp_pass", value)

    db.log_audit(ctx.conn, ctx.user["account"], "settings_updated", ip=ctx.ip)
    mailer.wake(ctx.conn)
    return get_settings(ctx)


def post_smtp_test(ctx):
    to_email = (ctx.body.get("to") or "").strip()
    if not _valid_email(to_email):
        raise ApiError(400, "請填一個正確的測試收件 Email")
    settings = db.all_settings(ctx.conn)
    subject = "[測試信] 假日加班登記系統"
    body = ("這是一封測試信，代表 SMTP 設定正確。\n\n"
            "系統時間：{}\n公司：{}\n".format(db.now_str(),
                                          settings.get("company_name", "")))
    try:
        mailer.send_now(settings, to_email, subject, body)
    except Exception as exc:
        detail = "{}: {}".format(type(exc).__name__, exc)
        ctx.conn.execute(
            "INSERT INTO mails(record_id,kind,to_email,to_label,subject,body,status,error,"
            "created_at) VALUES(NULL,'test',?,?,?,?,'failed',?,?)",
            (to_email, "測試", subject, body, detail[:500], db.now_str()),
        )
        raise ApiError(400, "寄送失敗：{}".format(detail[:300]))
    ctx.conn.execute(
        "INSERT INTO mails(record_id,kind,to_email,to_label,subject,body,status,created_at,"
        "sent_at) VALUES(NULL,'test',?,?,?,?,'sent',?,?)",
        (to_email, "測試", subject, body, db.now_str(), db.now_str()),
    )
    return {"ok": True}


# ---------- 操作紀錄 ----------

def get_audit(ctx):
    rows = ctx.conn.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT 300"
    )
    return {"audit": [{
        "at": r["at"], "actor": r["actor"], "action": r["action"],
        "target": r["target"], "detail": r["detail"], "ip": r["ip"],
    } for r in rows]}


def get_dashboard(ctx):
    conn = ctx.conn
    users_total = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE active=1").fetchone()["c"]
    no_approver = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE active=1 AND role<>'admin' "
        "AND approver_id IS NULL").fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM records WHERE status IN ('logged','notified')"
    ).fetchone()["c"]
    month = db.now_str()[:7]
    month_stat = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(hours),0) AS h FROM records "
        "WHERE work_date LIKE ? AND status<>'voided'", ("{}-%".format(month),)
    ).fetchone()
    failed_mails = conn.execute(
        "SELECT COUNT(*) AS c FROM mails WHERE status='failed'").fetchone()["c"]
    return {"dashboard": {
        "users_total": users_total,
        "no_approver": no_approver,
        "pending": pending,
        "month": month,
        "month_records": month_stat["c"],
        "month_hours": round(month_stat["h"], 1),
        "failed_mails": failed_mails,
        "mail_mode": db.get_setting(conn, "mail_mode", "preview"),
    }}
