"""通知信：組信、進寄件匣、送出。

三種模式（後台可切換）：
  preview — 只寫進寄件匣，不真的寄出（預設，適合示範）
  smtp    — 透過設定好的 SMTP 主機寄出（本機／VPS 適用）
  resend  — 透過 Resend 的 HTTP API 寄出（雲端 serverless 適用，不需要 SMTP 連外）

本機執行時有背景執行緒負責送信；雲端 serverless 沒有背景執行緒，
改成在請求內直接送出（見 deliver_pending）。
"""

import json
import smtplib
import threading
import urllib.error
import urllib.request
import traceback
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

import db
import dayutil

_worker_started = False
_wake = threading.Event()


# ---------- 收件人解析 ----------

def resolve_recipients(conn, user_row):
    """算出這位員工的加班通知要寄給誰。

    回傳 {"approver": {...} 或 None, "cc": [{label,email}, ...]}
    """
    approver = None
    if user_row["approver_id"]:
        row = conn.execute(
            "SELECT id,name,email,active FROM users WHERE id=?", (user_row["approver_id"],)
        ).fetchone()
        if row and row["active"] and row["email"]:
            approver = {"id": row["id"], "name": row["name"], "email": row["email"]}
        elif row:
            approver = {"id": row["id"], "name": row["name"], "email": row["email"] or "",
                        "warning": "主管未設定 Email" if not row["email"] else "主管帳號已停用"}

    cc = []
    seen = set()
    for row in conn.execute(
        "SELECT label,email FROM user_cc WHERE user_id=? ORDER BY seq,id", (user_row["id"],)
    ):
        key = row["email"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            cc.append({"label": row["label"] or "副本", "email": row["email"].strip()})

    if user_row["use_default_cc"]:
        try:
            defaults = json.loads(db.get_setting(conn, "default_cc", "[]") or "[]")
        except Exception:
            defaults = []
        for item in defaults:
            email = (item.get("email") or "").strip()
            key = email.lower()
            if email and key not in seen:
                seen.add(key)
                cc.append({"label": item.get("label") or "副本", "email": email,
                           "from_default": True})

    return {"approver": approver, "cc": cc}


# ---------- 信件內容 ----------

def _approve_link(conn, record):
    base = (db.get_setting(conn, "base_url", "") or "").rstrip("/")
    if not base or not record["approve_token"]:
        return ""
    return "{}/approve?t={}".format(base, record["approve_token"])


def _fmt_seconds(seconds):
    h, m = divmod(int(seconds) // 60, 60)
    return "{} 小時 {} 分".format(h, m) if h else "{} 分".format(m)


def segment_lines(record):
    """計時器送出的登記：列出每一段開始／暫停的時間。"""
    try:
        segs = json.loads(record["segments"] or "[]")
    except (KeyError, IndexError, ValueError):
        return []
    if not segs:
        return []
    lines = ["計時明細（開始 → 暫停，伺服器時間）："]
    for i, seg in enumerate(segs, 1):
        lines.append("  第 {} 段　{} → {}（{}）".format(
            i, seg["start"], seg["end"], _fmt_seconds(seg["seconds"])))
    return lines


def build_notify_mail(conn, record, for_approver):
    company = db.get_setting(conn, "company_name", "")
    subject = "[加班通知] {}・{}（{}）".format(
        record["user_name"], record["work_date"], record["day_type_text"]
    )
    lines = [
        "姓名：{}{}".format(record["user_name"],
                          "（{}）".format(record["user_dept"]) if record["user_dept"] else ""),
        "日期：{}（{}）".format(record["work_date"], record["day_type_text"]),
        "時段：{} - {}（共 {} 小時）".format(
            record["start_time"], record["end_time"], record["hours"]),
    ] + segment_lines(record) + [
        "內容：{}".format(record["content"]),
        "",
        "系統時間戳：{}（不可修改）".format(record["stamped_at"]),
        "登記單號：{}".format(record["ticket_no"]),
        "",
        "直屬主管：{}".format(record["approver_name"] or "（未指定）"),
        "部門主管：{}".format(record["dept_head_name"] or "（未指定）"),
    ]
    if for_approver:
        link = _approve_link(conn, record)
        lines += ["", "─" * 28, "請確認這次加班是否屬實："]
        if link:
            lines += ["  {}".format(link),
                      "（此連結可直接簽核，請勿轉寄）"]
        else:
            lines += ["  請登入系統的「待我簽核」頁面處理。"]
    else:
        lines += ["", "本信為副本通知，供存查用。"]
    if company:
        lines += ["", "— {} 假日加班登記系統".format(company)]
    return subject, "\n".join(lines)


def build_decision_mail(conn, record, decision):
    verb = "已確認" if decision == "approve" else "已退回"
    subject = "[加班{}] {}・{}".format(verb, record["user_name"], record["work_date"])
    lines = [
        "{} 的加班登記，主管 {} {}。".format(
            record["user_name"], record["approver_name"], verb),
        "",
        "日期：{}（{}）".format(record["work_date"], record["day_type_text"]),
        "時段：{} - {}（共 {} 小時）".format(
            record["start_time"], record["end_time"], record["hours"]),
        "單號：{}".format(record["ticket_no"]),
        "時間戳：{}".format(record["stamped_at"]),
    ]
    if record["decision_comment"]:
        lines += ["", "主管備註：{}".format(record["decision_comment"])]
    if decision == "approve":
        lines += ["", "員工可接著送出正式加班申請。"]
    return subject, "\n".join(lines)


def build_applied_mail(conn, record):
    subject = "[正式加班申請] {}・{}（{} 小時）".format(
        record["user_name"], record["work_date"], record["hours"]
    )
    lines = [
        "正式加班申請已送出，請 HR 存查並計入加班紀錄。",
        "",
        "姓名：{}{}".format(record["user_name"],
                          "（{}）".format(record["user_dept"]) if record["user_dept"] else ""),
        "日期：{}（{}）".format(record["work_date"], record["day_type_text"]),
        "時段：{} - {}（共 {} 小時）".format(
            record["start_time"], record["end_time"], record["hours"]),
    ] + segment_lines(record) + [
        "內容：{}".format(record["content"]),
        "",
        "登記時間戳：{}（不可修改）".format(record["stamped_at"]),
        "直屬主管確認：{} @ {}".format(record["approver_name"], record["decided_at"] or "-"),
        "部門主管（副本）：{}".format(record["dept_head_name"] or "（未指定）"),
        "申請時間：{}".format(record["applied_at"] or "-"),
        "單號：{}".format(record["ticket_no"]),
    ]
    if record["apply_note"]:
        lines += ["", "補充說明：{}".format(record["apply_note"])]
    return subject, "\n".join(lines)


# ---------- 進寄件匣 ----------

LIVE_MODES = ("smtp", "resend")


def queue_mail(conn, record_id, kind, to_email, to_label, subject, body):
    mode = db.get_setting(conn, "mail_mode", "preview")
    status = "queued" if mode in LIVE_MODES else "preview"
    conn.execute(
        "INSERT INTO mails(record_id,kind,to_email,to_label,subject,body,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (record_id, kind, to_email, to_label, subject, body, status, db.now_str()),
    )
    if status == "queued":
        _wake.set()


def queue_notify(conn, record, cc_list):
    """登記完成 → 直屬主管（要簽核）＋ 部門主管與副本收件人（只收信）。

    同一個 Email 只寄一封，避免部門主管兼任直屬主管時收到兩封。
    """
    seen = set()

    if record["approver_email"]:
        subject, body = build_notify_mail(conn, record, for_approver=True)
        queue_mail(conn, record["id"], "notify_approver", record["approver_email"],
                   "直屬主管・{}".format(record["approver_name"]), subject, body)
        seen.add(record["approver_email"].strip().lower())

    subject, body = build_notify_mail(conn, record, for_approver=False)

    targets = []
    if record["dept_head_email"]:
        targets.append({"label": "部門主管・{}".format(record["dept_head_name"]),
                        "email": record["dept_head_email"]})
    targets.extend(cc_list)

    for item in targets:
        key = (item["email"] or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        queue_mail(conn, record["id"], "notify_cc", item["email"], item["label"],
                   subject, body)


def queue_decision(conn, record, decision, employee_email, cc_list):
    subject, body = build_decision_mail(conn, record, decision)
    if employee_email:
        queue_mail(conn, record["id"], "decision", employee_email,
                   "員工・{}".format(record["user_name"]), subject, body)
    for item in cc_list:
        queue_mail(conn, record["id"], "decision", item["email"], item["label"],
                   subject, body)


def queue_applied(conn, record, cc_list, approver_email):
    subject, body = build_applied_mail(conn, record)
    targets = list(cc_list)
    if approver_email:
        targets.append({"label": "直屬主管・{}".format(record["approver_name"]),
                        "email": approver_email})
    if record["dept_head_email"]:
        targets.append({"label": "部門主管・{}".format(record["dept_head_name"]),
                        "email": record["dept_head_email"]})
    seen = set()
    for item in targets:
        key = (item["email"] or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        queue_mail(conn, record["id"], "applied", item["email"], item["label"],
                   subject, body)


# ---------- 真的寄出 ----------

def send_now(settings, to_email, subject, body):
    """依目前模式寄出一封信；失敗就拋例外。"""
    mode = settings.get("mail_mode", "preview")
    if mode == "resend":
        return _send_via_resend(settings, to_email, subject, body)
    return _send_via_smtp(settings, to_email, subject, body)


def _send_via_resend(settings, to_email, subject, body):
    """Resend 的 HTTP API。雲端環境用這個，不需要對外開 SMTP 連線。"""
    api_key = (settings.get("resend_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("尚未設定 Resend API Key（環境變數 RESEND_API_KEY）")
    sender = (settings.get("smtp_from") or "onboarding@resend.dev").strip()
    from_name = settings.get("smtp_from_name") or ""
    payload = json.dumps({
        "from": "{} <{}>".format(from_name, sender) if from_name else sender,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as res:
            res.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("Resend 回應 {}：{}".format(exc.code, detail))


def _send_via_smtp(settings, to_email, subject, body):
    """用設定好的 SMTP 寄一封信；失敗就拋例外。"""
    host = settings.get("smtp_host", "").strip()
    if not host:
        raise RuntimeError("尚未設定 SMTP 主機")
    port = int(settings.get("smtp_port") or 587)
    security = settings.get("smtp_security", "starttls")
    user = settings.get("smtp_user", "").strip()
    password = settings.get("smtp_pass", "")
    sender = (settings.get("smtp_from") or user or "no-reply@localhost").strip()
    from_name = settings.get("smtp_from_name") or ""

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), sender)) if from_name else sender
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=8)
    else:
        server = smtplib.SMTP(host, port, timeout=8)
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls()
            server.ehlo()
        if user:
            server.login(user, password)
        server.sendmail(sender, [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def deliver_pending(conn, limit=6):
    """把佇列裡的信直接送出（用呼叫端既有的連線）。

    雲端 serverless 沒有背景執行緒，就靠這個在請求內把信送掉。
    單次有上限，避免一個請求卡太久被平台判定逾時。
    """
    settings = db.all_settings(conn)
    if settings.get("mail_mode") not in LIVE_MODES:
        return 0

    rows = conn.execute(
        "SELECT id,to_email,subject,body FROM mails WHERE status='queued' "
        "ORDER BY id LIMIT ?", (limit,)
    ).fetchall()

    sent = 0
    for row in rows:
        try:
            send_now(settings, row["to_email"], row["subject"], row["body"])
            conn.execute(
                "UPDATE mails SET status='sent', sent_at=?, error='' WHERE id=?",
                (db.now_str(), row["id"]),
            )
            sent += 1
        except Exception as exc:
            detail = "{}: {}".format(type(exc).__name__, exc)[:500]
            conn.execute(
                "UPDATE mails SET status='failed', error=? WHERE id=?",
                (detail, row["id"]),
            )
            print("[mailer] 寄送失敗 → {}：{}".format(row["to_email"], detail))
    return sent


def _process_queue():
    with db.db() as conn:
        deliver_pending(conn, limit=20)


def _loop():
    while True:
        try:
            _process_queue()
        except Exception:
            traceback.print_exc()
        _wake.wait(timeout=15)
        _wake.clear()


def start_worker():
    """只有本機常駐執行時才啟動背景執行緒。"""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_loop, name="mailer", daemon=True).start()


def wake(conn=None):
    """通知有新信要寄。

    本機：叫醒背景執行緒。
    雲端：不在這裡寄 —— 這裡還在請求的資料庫交易中，
          萬一寄信卡住被平台中斷，連加班紀錄都會一起回滾掉。
          改由 server.py 在交易 commit 之後呼叫 flush_after_commit()。
    """
    if _worker_started:
        _wake.set()


def flush_after_commit(limit=3):
    """交易已經 commit 之後才寄信，用獨立連線，一封一個交易。

    寄信失敗不影響已經存好的加班紀錄；失敗的信留在寄件匣可以重寄。
    """
    if _worker_started:
        return
    try:
        with db.db() as conn:
            if db.get_setting(conn, "mail_mode", "preview") not in LIVE_MODES:
                return
            rows = conn.execute(
                "SELECT id FROM mails WHERE status='queued' ORDER BY id LIMIT ?",
                (limit,)
            ).fetchall()
            ids = [r["id"] for r in rows]
        for mail_id in ids:
            _send_one_committed(mail_id)
    except Exception:
        traceback.print_exc()


def _send_one_committed(mail_id):
    """寄一封信，並立刻把結果寫回去（獨立交易）。"""
    try:
        with db.db() as conn:
            settings = db.all_settings(conn)
            row = conn.execute(
                "SELECT id,to_email,subject,body,status FROM mails WHERE id=?", (mail_id,)
            ).fetchone()
            if not row or row["status"] != "queued":
                return
            payload = (row["to_email"], row["subject"], row["body"])
        try:
            send_now(settings, *payload)
            status, error = "sent", ""
        except Exception as exc:
            status = "failed"
            error = "{}: {}".format(type(exc).__name__, exc)[:500]
            print("[mailer] 寄送失敗 → {}：{}".format(payload[0], error))
        with db.db() as conn:
            if status == "sent":
                conn.execute(
                    "UPDATE mails SET status='sent', sent_at=?, error='' WHERE id=?",
                    (db.now_str(), mail_id))
            else:
                conn.execute("UPDATE mails SET status='failed', error=? WHERE id=?",
                             (error, mail_id))
    except Exception:
        traceback.print_exc()
