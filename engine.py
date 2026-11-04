"""Build the browser snapshot from actual THS industry-index closing prices."""
import json
import math
import os
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from calendar_cn import close_date_limit, is_closed_period, is_trading_day, latest_closed_session, period_end, shanghai_now
from source_ths import load_cache


ROOT = Path(__file__).resolve().parent
KEEP = {"D": 510, "W": 300, "M": 240}
NAMES = {"D": "day", "W": "week", "M": "month"}


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            file.write(content.encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def bucket(d, p):
    return d if p == "D" else d.isocalendar()[:2] if p == "W" else (d.year, d.month)


def market_grid(days, p, cutoff):
    """Use one market-wide axis. Missing sector observations never shift other dates."""
    ends = []
    last_key = None
    for d in days:
        key = bucket(d, p)
        if key != last_key:
            ends.append(d)
        else:
            ends[-1] = d
        last_key = key
    closed = [p == "D" or is_closed_period(d, NAMES[p], cutoff) for d in ends]
    return ends, closed


def expected_end(p, cutoff, observed):
    if cutoff is None:
        return None
    if p == "D":
        return cutoff
    end = period_end(cutoff, NAMES[p])
    if end is None:
        return observed
    if end <= cutoff:
        return end
    before = (cutoff - timedelta(days=cutoff.weekday() + 1) if p == "W"
              else cutoff.replace(day=1) - timedelta(days=1))
    # A holiday week may have no trading sessions at all.
    for _ in range(4):
        end = period_end(before, NAMES[p])
        if end:
            return end
        before -= timedelta(days=7 if p == "W" else before.day)
    return observed


def qabs(values, fraction):
    ordered = sorted(abs(v) for v in values)
    return ordered[math.ceil(len(ordered) * fraction) - 1]


def points(value, ref, weight):
    return round(weight * min(1, max(0, value) / max(ref, 1e-8)))


def indicators(dates, close_by_date, keep):
    """Warm EMA on the full series before truncation. Resets after a missing market bar."""
    result = []
    start = max(0, len(dates) - keep)
    n = 0
    history = deque(maxlen=26)
    for i, day in enumerate(dates):
        close = close_by_date.get(day)
        if close is None:
            n = 0
            if i >= start:
                result.append(None)
            continue
        if n == 0:
            fast = slow = close
            dea = 0.0
            gold = dead = -100
            history.clear()
        n += 1
        fast += (close - fast) * (2 / 13)
        slow += (close - slow) * (2 / 27)
        dif = fast - slow
        dea += (dif - dea) * (2 / 10)
        hist = 2 * (dif - dea)
        d, h = dif / close, hist / close
        v = h - history[-3][1] if len(history) >= 3 else 0.0
        if history:
            prev = history[-1][1]
            if h > 0 >= prev:
                gold = n
            if h < 0 <= prev:
                dead = n
        if i >= start:
            parts = None
            if n >= 52:
                difs, bars, deltas = zip(*history)
                dq, hq, vq = (qabs(values, .75) for values in (difs, bars, deltas))
                # The first four contributions describe potential; the last five describe risk.
                parts = [points(d, dq, 30), points(h, hq, 25), points(v, vq, 25),
                         4 * max(0, 5 - (n - gold)) if h > 0 else 0,
                         points(-d, dq, 25), points(-h, hq, 20), points(-v, vq, 25),
                         3 * max(0, 5 - (n - dead)) if h < 0 else 0,
                         15 if abs(d) > 1e-8 and abs(d) >= qabs(difs, .80) and v < 0 else 0]
            result.append([close, round(dif, 5), round(dea, 5), round(hist, 5), parts]
                          if n >= 26 else [close, None, None, None, None])
        history.append((d, h, v))
    return result


def make_snapshot(catalog, histories, now=None):
    now = now or shanghai_now()
    expected = latest_closed_session(now)
    # If the exchange calendar has no confirmed expectation, still cap input by the wall clock.
    cutoff = expected or close_date_limit(now)
    parsed = {}
    for item in catalog:
        prices = {}
        for stamp, close in histories.get(item["code"], []):
            d = date.fromisoformat(stamp)
            if d <= cutoff and math.isfinite(close) and close > 0:
                prices[d] = close
        parsed[item["code"]] = prices
    actual = set().union(*(set(s) for s in parsed.values()))
    if not actual or not catalog:
        raise ValueError("无可验证的行业收盘数据，保留已有快照")
    invalid = {d for d in actual if is_trading_day(d) is False}
    if invalid:
        raise ValueError("本地行情存在非交易日收盘：" + str(min(invalid)))
    # Known market sessions are kept even when all vendors' observations are missing.
    first = min(actual)
    days = sorted(actual | {d for i in range((cutoff - first).days + 1)
                            if is_trading_day(d := first + timedelta(days=i)) is True})
    grids = {p: market_grid(days, p, cutoff) for p in KEEP}
    axes, goals = {}, {}
    for p, (ends, closed) in grids.items():
        last_confirmed = next((d for d, final in zip(reversed(ends), reversed(closed)) if final), None)
        goal = expected_end(p, expected, last_confirmed)
        goals[p] = goal.isoformat() if goal else None
        axes[p] = {"dates": [d.isoformat() for d in ends[-KEEP[p]:]],
                   "closed": closed[-KEEP[p]:]}
    rows = []
    for item in catalog:
        prices = parsed[item["code"]]
        series = {}
        for p, (ends, closed) in grids.items():
            # Incomplete historical groups are gaps (an unfinished trailing group is a preview).
            valid = {d: prices[d] for j, d in enumerate(ends)
                     if d in prices and (closed[j] or j == len(ends) - 1)}
            bars = indicators(ends, valid, KEEP[p])
            for bar, final in zip(bars, closed[-KEEP[p]:]):
                if bar is not None and not final:
                    bar[4] = None  # Developing weeks/months can be charted but never scored.
            series[p] = bars
        rows.append({"code": item["code"], "name": item["name"],
                     "first": min(prices).isoformat() if prices else None,
                     "last": max(prices).isoformat() if prices else None, "series": series})
    asof = max(actual).isoformat()
    expected_str = expected.isoformat() if expected else None
    summary = {"schema": 1, "asOf": asof, "expectedDay": expected_str,
               "count": len(rows), "fresh": sum(bool(expected_str) and r["last"] == expected_str for r in rows),
               "builtAt": now.isoformat(timespec="seconds")}
    return {**summary, "goals": goals, "axes": axes, "rows": rows}


def build_snapshot():
    payload = make_snapshot(*load_cache())
    packed = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    write_atomic(ROOT / "dist" / "market.js", "window.SECTOR_SNAPSHOT=" + packed + ";\n")
    summary = {k: payload[k] for k in ("schema", "asOf", "expectedDay", "count", "fresh", "builtAt")}
    write_atomic(ROOT / "data" / "snapshot-meta.json", json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), ensure_ascii=False))
