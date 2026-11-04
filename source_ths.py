"""同花顺行业目录和指数收盘价。仅用 Python 标准库，结果保存在 data/ths。"""

import concurrent.futures
import html
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


CHINA = timezone(timedelta(hours=8))
CATALOG_URL = "https://q.10jqka.com.cn/thshy/"
LINE_URL = "https://d.10jqka.com.cn/v4/line/bk_{}/01/{}.js"
DEFAULT_DIR = Path(__file__).resolve().parent / "data" / "ths"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, payload):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as fout:
        temporary = Path(fout.name)
        try:
            json.dump(payload, fout, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            fout.flush()
        except BaseException:
            fout.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _get(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; LocalIndustryChart/1.0)",
        "Referer": CATALOG_URL,
    })
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("gb18030")
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt or isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            time.sleep(0.7)


def _catalog(text, prior_size):
    # The lower quotation table is paginated. Only the five directory groups are complete.
    groups = re.findall(r'<div\s+class=["\']cate_items["\']\s*>(.*?)</div>', text, re.S)
    if len(groups) != 5:
        raise ValueError("行业目录的五组分类未完整返回")
    links = []
    for group in groups:
        matches = re.findall(r'/thshy/detail/code/(\d{6})/[^>]*>([^<>]+)</a>', group)
        if not matches:
            raise ValueError("行业目录分组为空")
        links.extend((code, html.unescape(name).strip()) for code, name in matches)
    codes = {code for code, name in links}
    if len(codes) != len(links) or len(links) < max(75, prior_size * 0.85):
        raise ValueError(f"行业目录不完整或重复，解析到 {len(links)} 项")
    return [{"code": code, "name": name} for code, name in links]


def _quote(code, file):
    text = _get(LINE_URL.format(code, file)).strip()
    prefix = f"quotebridge_v4_line_bk_{code}_01_{file}"
    if not text.startswith(prefix + "(") or ")" not in text:
        raise ValueError(f"{file}.js 行情响应与行业代码不符")
    return json.loads(text[len(prefix) + 1:text.rindex(")")])


def _iso(value):
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("日线日期格式异常")
    return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()


def _price(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("日线收盘价缺失或无效")
    return value


def _check_rows(rows):
    if not rows or any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("日线日期顺序、唯一性或条数异常")
    return rows


def _recent(payload, day):
    rows = []
    for record in payload["data"].strip(";").split(";"):
        date_text, _, _, _, close, *_ = record.split(",")
        label = _iso(date_text)
        if label < day:
            rows.append([label, _price(close)])
    return _check_rows(rows)


def _full(payload, day):
    # all.js stores [low, open-low, high-low, close-low] at a fixed priceFactor.
    days = payload["dates"].split(",")
    prices = [int(v) for v in payload["price"].split(",")]
    years = [int(year) for year, count in payload["sortYear"] for _ in range(int(count))]
    factor = int(payload["priceFactor"])
    # The provider sometimes already counts today's intraday stub in the header
    # while both arrays still end at the previous close. Never invent that row.
    total = int(payload["total"])
    trailing_stub = (len(days) + 1 == total and years[-1] == int(day[:4]))
    if (len(years) != total or not (len(days) == total or trailing_stub)
            or len(prices) != 4 * len(days) or factor <= 0):
        raise ValueError("全历史行情编码长度或价格比例异常")
    rows = []
    for i, (year, month_day) in enumerate(zip(years, days)):
        label = _iso(str(year) + month_day)
        if label < day:
            rows.append([label, _price((prices[4*i] + prices[4*i+3]) / factor)])
    return _check_rows(rows)


def _history(code, plain, day):
    try:
        return _full(_quote(code, "all"), day), "all.js"
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        # Same provider, ordinary OHLC columns. Require every available year.
        rows = []
        for year, count in sorted(plain["year"].items()):
            if not int(count):
                continue
            payload = _quote(code, year)
            year_rows = _recent(payload, day)
            if any(not label.startswith(year + "-") for label, value in year_rows):
                raise ValueError(f"{year} 年行情含错误日期")
            if int(year) < int(day[:4]) and len(year_rows) != int(count):
                raise ValueError(f"{year} 年行情条数与源站目录不符")
            rows.extend(year_rows)
        return _check_rows(rows), "{year}.js"


def _closing_quote(code, day):
    # The history file can lag until the next morning. today.js supplies a quote date
    # and time. A cached morning/lunch response is never promoted to a closing bar.
    quote = _quote(code, "today")["bk_" + code]
    clock = str(quote["dt"]).zfill(4)
    if _iso(str(quote["1"])) != day or not re.fullmatch(r"(?:1[5-9]|2[0-3])[0-5]\d", clock):
        return None
    close = _price(quote["11"])
    if not _price(quote["9"]) <= close <= _price(quote["8"]):
        raise ValueError("收盘报价不在当日高低价之内")
    return [day, close], clock


def load_cache(data_dir=None):
    """Return (list of {code,name}, dict of code -> [[YYYY-MM-DD, close], ...])."""
    root = Path(data_dir) if data_dir is not None else DEFAULT_DIR
    if not (root / "catalog.json").exists():
        return [], {}
    catalog = _read_json(root / "catalog.json")["industries"]
    histories = {}
    for item in catalog:
        path = root / (item["code"] + ".json")
        if path.exists():
            histories[item["code"]] = _read_json(path)["bars"]
    return catalog, histories


def _collect(item, root, now):
    code = item["code"]
    day = now.date().isoformat()
    path = root / (code + ".json")
    old = _read_json(path) if path.exists() else {}
    old_rows = old.get("bars", [])
    plain = _quote(code, "last")
    recent = _recent(plain, day)
    old_map = dict(old_rows)
    common = [pair for pair in recent if pair[0] in old_map]
    # Missing overlap after long downtime or a historical revision calls for full rehydration.
    needs_full = not common or any(abs(old_map[k] - v) > 0.0011 for k, v in common)
    verification = old.get("full_verification", {})
    if needs_full:
        full, method = _history(code, plain, day)
        base = dict(full)
        overlap = [(k, v) for k, v in recent if k in base]
        if len(overlap) < min(10, len(recent)) or any(abs(base[k] - v) > 0.0011 for k, v in overlap):
            raise ValueError("压缩全历史与近期明文日线在重叠日期不一致")
        if old_rows and (full[0][0] > old_rows[0][0] or full[-1][0] < recent[0][0]):
            raise ValueError("全历史范围收缩，保留原缓存")
        verification = {"method": method, "first": overlap[0][0], "last": overlap[-1][0], "overlap": len(overlap),
                        "checked_at": now.isoformat(timespec="seconds")}
        # A previously accepted after-close bar may still be absent from the archive.
        base.update((k, v) for k, v in old_rows if k > full[-1][0])
    else:
        base = old_map
    base.update(recent)
    current = None
    warning = None
    if (now.hour, now.minute) >= (15, 35) and now.weekday() < 5:
        try:
            current = _closing_quote(code, day)
        except (OSError, ValueError, KeyError) as exc:
            warning = f"收盘时点未确认：{exc}"
        if current:
            base[current[0][0]] = current[0][1]
    bars = sorted(base.items())
    latest = bars[-1][0]
    record = {"code": code, "name": item["name"], "source_name": plain.get("name"),
              "captured_at": now.isoformat(timespec="seconds"), "full_verification": verification,
              "history_url": LINE_URL.format(code, "{year}" if verification.get("method") == "{year}.js" else "all"),
              "recent_url": LINE_URL.format(code, "last"),
              "quote_url": LINE_URL.format(code, "today"), "bars": bars}
    if current:
        record["quote_time"] = day + " " + current[1][:2] + ":" + current[1][2:]
    elif latest == old.get("quote_time", "")[:10]:
        record["quote_time"] = old["quote_time"]
    _write_json(path, record)
    return latest, warning


def refresh(data_dir=None):
    """Refresh directory and per-industry caches, retaining existing data on failure."""
    root = Path(data_dir) if data_dir is not None else DEFAULT_DIR
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(CHINA)
    catalog_path = root / "catalog.json"
    catalog = _read_json(catalog_path)["industries"] if catalog_path.exists() else []
    report = {"started_at": now.isoformat(timespec="seconds"), "catalog_error": None,
              "updated": [], "failed": {}, "warnings": {}, "latest": {}, "lagging": {}}
    try:
        catalog = _catalog(_get(CATALOG_URL), len(catalog))
        _write_json(catalog_path, {"captured_at": report["started_at"], "url": CATALOG_URL, "industries": catalog})
    except (ValueError, OSError) as exc:
        report["catalog_error"] = str(exc)
        if not catalog:
            raise RuntimeError(f"行业目录不可用，没有旧目录：{exc}") from exc
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(_collect, item, root, now): item["code"] for item in catalog}
        for future in concurrent.futures.as_completed(jobs):
            code = jobs[future]
            try:
                latest, warning = future.result()
                report["updated"].append(code)
                report["latest"][code] = latest
                if warning:
                    report["warnings"][code] = warning
            except Exception as exc:
                report["failed"][code] = f"{type(exc).__name__}: {exc}"
                path = root / (code + ".json")
                if path.exists():
                    report["latest"][code] = _read_json(path)["bars"][-1][0]
    if report["latest"]:
        end = max(report["latest"].values())
        report["lagging"] = {c: d for c, d in report["latest"].items() if d < end}
    report["updated"].sort()
    report["finished_at"] = datetime.now(CHINA).isoformat(timespec="seconds")
    _write_json(root / "refresh.json", report)
    return report


if __name__ == "__main__":
    result = refresh()
    print(json.dumps({k: len(v) if k in ("updated", "latest") else v for k, v in result.items()},
                     ensure_ascii=True, indent=2))
    sys.exit(int(bool(result["failed"] or result["catalog_error"])))
