#!/usr/bin/env python3
"""
dashboard.py — OpenClaw ICU Dashboard Backend
Serves canvas/index.html + /api/data JSON endpoint.

Usage:
  python3 dashboard.py          # start on :5555
  python3 dashboard.py --port 8080
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

PORT = 5555
HOME = os.path.expanduser("~/.openclaw")
HKT = timezone(timedelta(hours=8))
PRICE_HISTORY_PATH = os.path.join(HOME, "shared", "price_history.json")
PNL_HISTORY_PATH = os.path.join(HOME, "shared", "pnl_history.json")
CANVAS_HTML = os.path.join(HOME, "canvas", "index.html")


def parse_md(path):
    data = {}
    if not os.path.exists(path):
        return data
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(\w+):\s*(.+)$', line)
            if m:
                data[m.group(1)] = m.group(2).strip()
    return data


def get_agents():
    agents = []
    try:
        with open(os.path.join(HOME, "openclaw.json")) as f:
            cfg = json.load(f)
        defaults = cfg.get("agents", {}).get("defaults", {})
        for a in cfg.get("agents", {}).get("list", []):
            aid = a.get("id", "?")
            ws = a.get("workspace", defaults.get("workspace", ""))
            model = a.get("model", defaults.get("model", {}).get("primary", "?"))
            agents.append({"id": aid, "workspace": ws, "model": model})
    except Exception:
        pass
    return agents


def get_launchagents():
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True, timeout=5)
        status = {}
        for line in out.strip().split("\n"):
            if "openclaw" in line:
                parts = line.split("\t")
                if len(parts) >= 3:
                    pid, exit_code, label = parts[0], parts[1], parts[2]
                    status[label] = {
                        "pid": pid if pid != "-" else None,
                        "exit": int(exit_code) if exit_code != "-" else None,
                    }
        return status
    except Exception:
        return {}


def get_scan_log(n=20):
    path = os.path.join(HOME, "workspace/agents/trader/logs/SCAN_LOG.md")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines[-n:]


def get_file_tree():
    dirs = [
        ("config/", "DNA", "params.py, modes/"),
        ("scripts/", "Muscle", "trader_cycle/, scanner_runner.py"),
        ("agents/main/workspace/", "Brain", "SOUL.md, MEMORY.md, skills/"),
        ("agents/trader/workspace/", "Heart", "SOUL.md, trading-rules/"),
        ("agents/scanner/workspace/", "Eye", "SOUL.md, scan-rules/"),
        ("agents/heartbeat/workspace/", "Nerve", "SOUL.md"),
        ("shared/", "Blood", "SIGNAL.md, TRADE_STATE.md"),
        ("logs/", "Logs", "lightscan.log, tradercycle.log"),
    ]
    tree = []
    for d, role, hint in dirs:
        full = os.path.join(HOME, d)
        if os.path.exists(full):
            count = sum(1 for _, _, files in os.walk(full) for _ in files)
            tree.append({"path": d, "role": role, "files": count, "hint": hint})
    return tree


def get_agent_activity():
    """Derive agent call counts from scan log + cost from tracker."""
    ct = parse_md(os.path.join(HOME, "workspace/routing/COST_TRACKER.md"))
    daily_total = ct.get("DAILY_TOTAL", "$0.00")

    today = datetime.now(HKT).strftime("%Y-%m-%d")
    log_path = os.path.join(HOME, "workspace/agents/trader/logs/SCAN_LOG.md")
    scanner_calls = 0
    trader_calls = 0
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                if today not in line:
                    continue
                if "LIGHT" in line:
                    scanner_calls += 1
                if "DEEP" in line:
                    trader_calls += 1

    now = datetime.now(HKT)
    hb_calls = (now.hour * 60 + now.minute) // 25

    total_calls = scanner_calls + trader_calls + hb_calls
    return {
        "main": {"calls": trader_calls, "cost": 0.0},
        "trader": {"calls": trader_calls, "cost": 0.0},
        "scanner": {"calls": scanner_calls, "cost": 0.0},
        "heartbeat": {"calls": hb_calls, "cost": 0.0},
        "total_cost": daily_total,
        "total_calls": total_calls,
        "projected_30d": "$0.00",
        "no_data": scanner_calls == 0 and trader_calls == 0,
    }


def get_uptime():
    """Get main agent uptime via psutil."""
    result = {"main_pid": None, "started": "—", "duration": "—"}
    try:
        la = get_launchagents()
        pid_str = la.get("ai.openclaw.gateway", {}).get("pid")
        if not pid_str or not HAS_PSUTIL:
            return result
        pid = int(pid_str)
        p = psutil.Process(pid)
        start = p.create_time()
        started_dt = datetime.fromtimestamp(start, tz=HKT)
        result["main_pid"] = pid
        result["started"] = started_dt.strftime("%Y-%m-%d %H:%M")

        elapsed = time.time() - start
        days = int(elapsed // 86400)
        hours = int((elapsed % 86400) // 3600)
        mins = int((elapsed % 3600) // 60)
        if days > 0:
            result["duration"] = f"{days}d {hours}h {mins}m"
        elif hours > 0:
            result["duration"] = f"{hours}h {mins}m"
        else:
            result["duration"] = f"{mins}m"
    except Exception:
        pass
    return result


def get_git_info():
    """Get last git commit info (fresh on every call)."""
    try:
        result = subprocess.run(
            ["git", "-C", HOME, "log", "--oneline", "-1"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(" ", 1)
            hash_short = parts[0]
            message = parts[1] if len(parts) > 1 else ""
            ago = ""
            ts_result = subprocess.run(
                ["git", "-C", HOME, "log", "-1", "--format=%ct"],
                capture_output=True, text=True, timeout=3,
            )
            if ts_result.returncode == 0:
                commit_time = int(ts_result.stdout.strip())
                diff = int(time.time()) - commit_time
                if diff < 3600:
                    ago = f"{diff // 60}分前"
                elif diff < 86400:
                    ago = f"{diff // 3600}小時前"
                else:
                    ago = f"{diff // 86400}日前"
            return {"hash": hash_short, "message": message, "ago": ago}
    except Exception:
        pass
    return {"hash": "unknown", "message": "", "ago": ""}


def get_telegram_status():
    """Check Telegram bot connectivity."""
    result = {"connected": False, "bot_name": "—", "last_sent": "—"}
    token = None
    env_path = os.path.join(HOME, "secrets/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.strip().split("=", 1)[1].strip().strip("'\"")
                    break
    if not token:
        return result
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        req = urllib.request.Request(url, headers={"User-Agent": "OpenClaw/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("ok"):
                result["connected"] = True
                result["bot_name"] = "@" + data["result"].get("username", "?")
    except Exception:
        pass
    for log_name in ["lightscan.log", "heartbeat.log"]:
        log_path = os.path.join(HOME, "logs", log_name)
        if not os.path.exists(log_path):
            continue
        try:
            with open(log_path) as f:
                for line in reversed(f.readlines()[-50:]):
                    if "telegram" in line.lower() or "sent" in line.lower():
                        m = re.search(r'(\d{2}:\d{2})', line)
                        if m:
                            result["last_sent"] = m.group(1)
                            break
        except Exception:
            pass
        if result["last_sent"] != "—":
            break
    return result


def get_trigger_summary():
    """Parse today's LIGHT TRIGGER entries from scan log."""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    log_path = os.path.join(HOME, "workspace/agents/trader/logs/SCAN_LOG.md")
    by_asset = {}
    by_reason = {}
    total = 0
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                if today not in line or "LIGHT TRIGGER" not in line:
                    continue
                total += 1
                m_asset = re.search(r'TRIGGER:(\w+)', line)
                if m_asset:
                    asset = m_asset.group(1)
                    by_asset[asset] = by_asset.get(asset, 0) + 1
                m_reason = re.search(r'REASON:(\w+?)_[+-]', line)
                if m_reason:
                    reason = m_reason.group(1)
                    by_reason[reason] = by_reason.get(reason, 0) + 1
                elif re.search(r'REASON:(\w+)', line):
                    reason = re.search(r'REASON:(\w+)', line).group(1)
                    by_reason[reason] = by_reason.get(reason, 0) + 1
    asset_list = sorted(
        [{"name": k, "count": v} for k, v in by_asset.items()],
        key=lambda x: -x["count"],
    )
    reason_list = sorted(
        [{"name": k, "count": v} for k, v in by_reason.items()],
        key=lambda x: -x["count"],
    )
    if total > 0:
        for item in asset_list:
            item["pct"] = round(item["count"] / total * 100)
        for item in reason_list:
            item["pct"] = round(item["count"] / total * 100)
    return {"total": total, "by_asset": asset_list, "by_reason": reason_list}


def update_pnl_history(balance):
    """Track PnL history, persist to shared/pnl_history.json."""
    data = {"baseline": None, "history": []}
    if os.path.exists(PNL_HISTORY_PATH):
        try:
            with open(PNL_HISTORY_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {"baseline": None, "history": []}
    try:
        bal = float(balance)
    except (ValueError, TypeError):
        return data.get("history", [])
    if data.get("baseline") is None:
        data["baseline"] = bal
    now = int(time.time())
    pnl = round(bal - data["baseline"], 2)
    hist = data.get("history", [])
    if hist and now - hist[-1]["t"] < 30:
        hist[-1] = {"t": now, "v": pnl}
    else:
        hist.append({"t": now, "v": pnl})
    data["history"] = hist[-200:]
    try:
        with open(PNL_HISTORY_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data["history"]


def update_price_history(prices):
    history = {}
    if os.path.exists(PRICE_HISTORY_PATH):
        try:
            with open(PRICE_HISTORY_PATH) as f:
                history = json.load(f)
        except Exception:
            history = {}
    for sym, price in prices.items():
        try:
            p = float(price)
        except (ValueError, TypeError):
            continue
        if sym not in history:
            history[sym] = []
        history[sym].append(p)
        history[sym] = history[sym][-20:]
    with open(PRICE_HISTORY_PATH, "w") as f:
        json.dump(history, f)
    return history


def collect_data():
    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    trade_state = parse_md(os.path.join(HOME, "workspace/agents/trader/TRADE_STATE.md"))
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    scan_config = parse_md(os.path.join(HOME, "workspace/agents/trader/config/SCAN_CONFIG.md"))

    balance = trade_state.get("BALANCE_USDT", trade_state.get("ACCOUNT_BALANCE", "0"))
    pnl_history = update_pnl_history(balance)

    prices = {
        "BTC": scan_config.get("BTC_price", "0"),
        "ETH": scan_config.get("ETH_price", "0"),
        "XRP": scan_config.get("XRP_price", "0"),
        "XAG": scan_config.get("XAG_price", "0"),
    }
    price_history = update_price_history(prices)

    la = get_launchagents()
    agent_labels = {
        "main": "ai.openclaw.gateway",
        "scanner": "ai.openclaw.lightscan",
        "trader": "ai.openclaw.tradercycle",
        "heartbeat": "ai.openclaw.heartbeat",
    }
    agents = []
    for a in get_agents():
        aid = a["id"]
        label = agent_labels.get(aid, "")
        la_info = la.get(label, {})
        if la_info.get("pid"):
            status = "live"
            pid = la_info["pid"]
        elif la_info.get("exit") == 0:
            status = "ok"
            pid = None
        elif la_info.get("exit") is not None:
            status = "warn"
            pid = None
        else:
            status = "off"
            pid = None
        agents.append({
            "id": aid,
            "model": a["model"].split("/")[-1],
            "workspace": a["workspace"],
            "status": status,
            "pid": pid,
            "exit": la_info.get("exit"),
        })

    last_scan_ts = scan_config.get("last_updated", signal.get("TIMESTAMP", "?"))

    return {
        "timestamp": ts,
        "balance": balance,
        "mode": scan_config.get("MARKET_MODE", trade_state.get("MARKET_MODE", "?")),
        "signal_active": signal.get("SIGNAL_ACTIVE", "NO"),
        "signal_pair": signal.get("PAIR", "---"),
        "position": trade_state.get("POSITION_STATUS", trade_state.get("PAIR", "NONE")),
        "direction": trade_state.get("DIRECTION", "---"),
        "consecutive_losses": trade_state.get("CONSECUTIVE_LOSSES", "0"),
        "agents": agents,
        "scan_log": get_scan_log(),
        "file_tree": get_file_tree(),
        "prices": prices,
        "price_history": price_history,
        "trigger": scan_config.get("TRIGGER_PENDING", "OFF"),
        "scan_count": scan_config.get("LIGHT_SCAN_COUNT", "0"),
        "last_scan": last_scan_ts,
        "agent_activity": get_agent_activity(),
        "uptime": get_uptime(),
        "git": get_git_info(),
        "telegram": get_telegram_status(),
        "trigger_summary": get_trigger_summary(),
        "pnl_history": pnl_history,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            data = collect_data()
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            try:
                with open(CANVAS_HTML, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/index.html not found")

    def log_message(self, format, *args):
        pass


def main():
    port = PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"OpenClaw ICU Dashboard: http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
