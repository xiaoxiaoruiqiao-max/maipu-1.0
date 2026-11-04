"""沪深交易日和已收盘周期。运行时只读本地日历；更新时再查询深交所。"""

import calendar
import json
import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen


CN = timezone(timedelta(hours=8), "Asia/Shanghai")
CLOSE = time(15)
ONE_DAY = timedelta(days=1)
CALENDAR_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month="
HOLIDAY_NOTICE = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
CALENDAR_FILE = Path(__file__).resolve().parent / "data" / "calendar-cn.json"


def shanghai_now(now=None):
    """Convert an instant to Shanghai time. A naive input denotes Shanghai local time."""
    if now is None:
        return datetime.now(CN)
    return now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)


def close_date_limit(now=None):
    """Upper date limit for an *observed* daily close, even without a calendar."""
    local = shanghai_now(now)
    return local.date() if local.time() >= CLOSE else local.date() - ONE_DAY


@lru_cache(maxsize=1)
def _months():
    if not CALENDAR_FILE.exists():
        return {}
    return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))["months"]


def reload_calendar():
    """Pick up another updater's atomic calendar write before building a snapshot."""
    _months.cache_clear()


def is_trading_day(day):
    """True/False for covered months. Unknown weekdays return None."""
    mask = _months().get(day.isoformat()[:7])
    if mask is not None:
        return mask[day.day - 1] == "1"
    return False if day.weekday() >= 5 else None


def latest_closed_session(now=None):
    """Most recent scheduled Shanghai/Shenzhen close, or None if calendar is unknown."""
    day = close_date_limit(now)
    while is_trading_day(day) is False:
        day -= ONE_DAY
    return day if is_trading_day(day) is True else None


def _bounds(day, period):
    if period == "day":
        return day, day
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return monday, monday + timedelta(days=4)
    if period == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    raise ValueError("period must be day, week or month")


def period_end(day, period):
    """Last scheduled session in the containing period, None if closed throughout or unknown."""
    first, cursor = _bounds(day, period)
    while cursor >= first:
        trading = is_trading_day(cursor)
        if trading is None:
            return None
        if trading:
            return cursor
        cursor -= ONE_DAY
    return None


def is_closed_period(last_bar, period, cutoff, *, next_bar=None):
    """Does the real close on last_bar finish this period on/before cutoff?

    For uncovered months, an optional next_bar must be the verified next market
    session (e.g. from a benchmark), not the next point in a sparse industry series.
    """
    if last_bar > cutoff or is_trading_day(last_bar) is False:
        return False
    if period == "day":
        return True
    expected = period_end(last_bar, period)
    if expected is not None:
        return last_bar == expected
    _, last_possible = _bounds(last_bar, period)
    while last_possible.weekday() >= 5:
        last_possible -= ONE_DAY
    return last_bar == last_possible or (next_bar is not None and last_possible < next_bar <= cutoff)


def _fetch_month(month):
    start = date.fromisoformat(month + "-01")
    days = calendar.monthrange(start.year, start.month)[1]
    request = Request(CALENDAR_URL + month, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.szse.cn/"})
    with urlopen(request, timeout=9) as response:
        rows = json.load(response)["data"]
    if not rows:  # Not yet published, or outside recorded history.
        return None
    if month == "2017-01" and not any(row["jyrq"] == "2017-01-01" for row in rows):
        # The SZSE payload omits this Sunday; the SSE's annual notice confirms closure.
        # https://www.sse.com.cn/disclosure/announcement/general/c/c_20161222_4218613.shtml
        rows = [*rows, {"jyrq": "2017-01-01", "jybz": "0"}]
    expected = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    ordered = sorted(rows, key=lambda row: row["jyrq"])
    flags = [str(row["jybz"]) for row in ordered]
    if [row["jyrq"] for row in ordered] != expected or not set(flags) <= {"0", "1"} or "1" not in flags:
        raise ValueError(f"Invalid Shenzhen exchange calendar: {month}")
    return "".join(flags)


def _save(months):
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CALENDAR_FILE.parent,
                                     delete=False, suffix=".tmp") as writer:
        tmp = Path(writer.name)
        try:
            json.dump({"source": CALENDAR_URL, "annual_notice_2026": HOLIDAY_NOTICE,
                       "2017-01-01": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20161222_4218613.shtml",
                       "months": dict(sorted(months.items()))},
                      writer, ensure_ascii=False, indent=1)
            writer.write("\n")
        except BaseException:
            writer.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, CALENDAR_FILE)
    finally:
        tmp.unlink(missing_ok=True)
    reload_calendar()


def refresh_calendar(now=None):
    """Refresh this month and next; fill a gap since the last bundled/cached month."""
    today = shanghai_now(now).date()
    first = today.replace(day=1)
    this_month = first.isoformat()[:7]
    following = (first + timedelta(days=32)).replace(day=1)
    reload_calendar()
    known = _months()
    first_cached = min(known, default=this_month)
    cursor = min(first, date.fromisoformat(first_cached + "-01"))
    updates = {}
    warnings = []
    while cursor <= following:
        month = cursor.isoformat()[:7]
        cursor = (cursor + timedelta(days=32)).replace(day=1)
        if month < this_month and month in known:
            continue
        try:
            new_mask = _fetch_month(month)
            if new_mask is not None and known.get(month) != new_mask:
                updates[month] = new_mask
            if new_mask is None and month not in known and month <= this_month:
                warnings.append(f"{month} 交易所日历待确认")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append(f"{month}: {exc}")
    if updates:
        _save({**known, **updates})
    return warnings
