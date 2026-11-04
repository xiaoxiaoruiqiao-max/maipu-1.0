"""Local web server and unattended updater for the industry MACD dashboard."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, time as clock_time, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import calendar_cn
import engine
import source_ths

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DATA = ROOT / "data"
PORT = 18916
URL = f"http://127.0.0.1:{PORT}/"
REFRESH_TIME = clock_time(16, 15)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_status(state):
    path = DATA / "update-status.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def take_lock():
    """OS releases the lock on process exit, including abnormal exit."""
    DATA.mkdir(parents=True, exist_ok=True)
    lock = (DATA / "update.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if not lock.tell():
                lock.write(b"1")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        lock.close()
        return None
    return lock


def current_status(running=None):
    state = read_json(DATA / "update-status.json")
    meta = read_json(DATA / "snapshot-meta.json")
    if running is None:
        running = False
        if state.get("status") == "updating":
            lock = take_lock()
            running = lock is None
            if lock is not None:
                lock.close()
    calendar_cn.reload_calendar()
    expected = calendar_cn.latest_closed_session()
    try:
        version = str((DIST / "market.js").stat().st_mtime_ns)
    except FileNotFoundError:
        version = None
    status = state.get("status", "idle")
    message = state.get("message", "本地快照已就绪" if version else "等待首次更新")
    if running:
        status, message = "updating", "正在获取同花顺行业行情并计算指标"
    elif status == "updating":
        status, message = "error", "上次更新中断，可重新更新"
    return {
        "app": "industry-macd", "running": running, "status": status, "message": message,
        "lastAttempt": state.get("lastAttempt"), "lastSuccess": state.get("lastSuccess"),
        "targetDate": expected.isoformat() if expected else None,
        "dataThrough": meta.get("asOf"), "fresh": meta.get("fresh", 0), "count": meta.get("count", 0),
        "version": version, "schedule": "16:15 Asia/Shanghai",
    }


def due(now=None, *, throttle=False):
    now = calendar_cn.shanghai_now(now)
    calendar_cn.reload_calendar()
    # During the interval between exchange close and publication, catch up to
    # yesterday's close, leaving today's official fetch to the 16:15 run.
    due_instant = now
    if now.time() < REFRESH_TIME:
        due_instant = now.replace(hour=0, minute=0, second=0, microsecond=0)
    target = calendar_cn.latest_closed_session(due_instant)
    meta = read_json(DATA / "snapshot-meta.json")
    state = read_json(DATA / "update-status.json")
    complete = (DIST / "market.js").is_file() and meta.get("count", 0) > 0
    if complete and target and meta.get("asOf", "") >= target.isoformat() and meta.get("fresh") == meta["count"]:
        if state.get("status") not in ("partial", "error", "updating"):
            return False
    last = datetime.fromisoformat(state["lastAttempt"]) if state.get("lastAttempt") else None
    if last and not target and complete and state.get("status") == "ok":
        scheduled = now.replace(hour=16, minute=15, second=0, microsecond=0)
        if scheduled > now:
            scheduled -= timedelta(days=1)
        if last >= scheduled:
            return False
    return not (throttle and last and now - last < timedelta(minutes=30))


def run_update(lock):
    with lock:
        now = calendar_cn.shanghai_now()
        prior = read_json(DATA / "update-status.json")
        state = {"status": "updating", "lastAttempt": now.isoformat(timespec="seconds"),
                 "lastSuccess": prior.get("lastSuccess"), "message": "更新进行中"}
        save_status(state)
        try:
            calendar_warnings = calendar_cn.refresh_calendar()
            report = source_ths.refresh()
            if not report["updated"]:
                raise RuntimeError("没有行业成功取得行情，保留上次图表")
            meta = engine.build_snapshot()
            count, fresh, expected = meta["count"], meta["fresh"], meta["expectedDay"]
            issues = []
            if report["failed"]:
                issues.append(f'{len(report["failed"])} 个行业抓取失败')
            if report["catalog_error"]:
                issues.append("行业目录沿用本地副本")
            if expected and fresh < count:
                issues.append(f"{expected} 的收盘已覆盖 {fresh}/{count} 个行业")
            if not expected:
                issues.append("交易日历待确认")
            state["status"] = "partial" if issues else "ok"
            state["message"] = "；".join(issues) if issues else f"{fresh} 个行业的收盘均已更新至 {expected}"
            if calendar_warnings and not expected:
                state["message"] += "，交易所日历暂时不可用"
            if not issues:
                state["lastSuccess"] = calendar_cn.shanghai_now().isoformat(timespec="seconds")
        except Exception as exc:
            state.update(status="error", message=f"更新失败：{exc}")
        save_status(state)
        return state["status"] == "ok"


def check_and_update(*, only_if_due=False, throttle=False):
    if only_if_due and not due(throttle=throttle):
        return True
    lock = take_lock()
    if lock is None:
        return True
    if only_if_due and not due(throttle=throttle):
        lock.close()
        return True
    return run_update(lock)


def monitor():
    while True:
        check_and_update(only_if_due=True, throttle=True)
        time.sleep(60)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def local(self):
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send_error(403)
            return False
        return True

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def status_json(self, code=200, running=None):
        body = json.dumps(current_status(running), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.local():
            return
        if urlsplit(self.path).path == "/api/status":
            self.status_json()
        else:
            super().do_GET()

    def do_POST(self):
        if not self.local():
            return
        if urlsplit(self.path).path != "/api/refresh":
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        if ((origin is not None and origin != "http://" + self.headers["Host"])
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.send_error(403)
            return
        lock = take_lock()
        if lock is not None:
            threading.Thread(target=run_update, args=(lock,), daemon=True).start()
        self.status_json(202, running=True)

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *_):
        pass


def serve():
    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as server:
        print(URL, flush=True)
        threading.Thread(target=monitor, daemon=True).start()
        server.serve_forever()


def live():
    try:
        with urlopen(URL + "api/status", timeout=1) as response:
            return json.load(response).get("app") == "industry-macd"
    except (OSError, ValueError):
        return False


def open_dashboard():
    if not live():
        DATA.mkdir(exist_ok=True)
        with (DATA / "server.log").open("a", encoding="utf-8") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve"], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                             start_new_session=os.name != "nt")
        for _ in range(40):
            if live():
                break
            time.sleep(.15)
        else:
            print("Local service could not start. See data/server.log.", file=sys.stderr)
            return 1
    print(URL)
    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("command", choices=("open", "serve", "update", "due"), nargs="?", default="open")
    command = cli.parse_args().command
    if command == "serve":
        serve()
    elif command == "open":
        sys.exit(open_dashboard())
    else:
        ok = check_and_update(only_if_due=command == "due")
        if sys.stdout is not None:
            print(json.dumps(current_status(), ensure_ascii=True))
        sys.exit(0 if ok else 1)
