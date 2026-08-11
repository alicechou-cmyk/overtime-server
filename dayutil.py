"""日期判定與工時計算（以伺服器為準，前端只負責顯示）。"""

from datetime import date, datetime

WD = ["一", "二", "三", "四", "五", "六", "日"]  # datetime.weekday(): 0=一


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def day_type(conn, date_str):
    """依國定假日表 + 星期判斷日別。

    國定假日 > 例假（週日） > 休息日（週六） > 工作日
    """
    row = conn.execute("SELECT name FROM holidays WHERE date=?", (date_str,)).fetchone()
    if row:
        return {"code": "holiday", "text": "國定假日・{}".format(row["name"]),
                "cls": "badge-holiday"}
    wd = parse_date(date_str).weekday()
    if wd == 6:
        return {"code": "weeklyrest", "text": "例假（週日）", "cls": "badge-weeklyrest"}
    if wd == 5:
        return {"code": "restday", "text": "休息日（週六）", "cls": "badge-restday"}
    return {"code": "workday", "text": "工作日（週{}）".format(WD[wd]),
            "cls": "badge-workday"}


MAX_HOURS = 16  # 單筆加班時數上限，用來擋「結束時間填得比開始時間早」這類明顯的誤填


def calc_hours(start, end):
    """跨午夜也算得出來；回傳小時數（一位小數）。"""
    sh, sm = [int(x) for x in start.split(":")[:2]]
    eh, em = [int(x) for x in end.split(":")[:2]]
    diff = (eh * 60 + em) - (sh * 60 + sm)
    if diff <= 0:
        diff += 24 * 60
    return round(diff / 60.0, 1)


def validate_time(s):
    try:
        h, m = [int(x) for x in s.split(":")[:2]]
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False


def make_ticket_no(conn, work_date):
    """單號格式 OT-YYYYMMDD-001（依當日登記序號遞增）。"""
    prefix = "OT-{}".format(work_date.replace("-", ""))
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM records WHERE ticket_no LIKE ?", (prefix + "-%",)
    ).fetchone()
    seq = row["c"] + 1
    while True:
        candidate = "{}-{:03d}".format(prefix, seq)
        exists = conn.execute(
            "SELECT 1 FROM records WHERE ticket_no=?", (candidate,)
        ).fetchone()
        if not exists:
            return candidate
        seq += 1


STATUS_TEXT = {
    "logged": "已登記",
    "notified": "已通知主管",
    "confirmed": "主管已確認",
    "rejected": "主管已退回",
    "applied": "正式申請已送出",
    "voided": "已作廢",
}


def status_text(code):
    return STATUS_TEXT.get(code, code)


def today_str():
    return date.today().isoformat()
