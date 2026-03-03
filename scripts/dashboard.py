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
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 5555
HOME = os.path.expanduser("~/.openclaw")
HKT = timezone(timedelta(hours=8))
PRICE_HISTORY_PATH = os.path.join(HOME, "shared", "price_history.json")
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


def get_cost_tracker():
    path = os.path.join(HOME, "workspace/routing/COST_TRACKER.md")
    data = parse_md(path)
    return {
        "daily_total": data.get("DAILY_TOTAL", "$0.00"),
        "daily_calls": data.get("DAILY_CALLS", "0"),
        "tier1_calls": data.get("TIER1_CALLS", "0"),
        "tier2_calls": data.get("TIER2_CALLS", "0"),
        "tier3_calls": data.get("TIER3_CALLS", "0"),
    }


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
        history[sym] = history[sym][-10:]
    with open(PRICE_HISTORY_PATH, "w") as f:
        json.dump(history, f)
    return history


def collect_data():
    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    trade_state = parse_md(os.path.join(HOME, "workspace/agents/trader/TRADE_STATE.md"))
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    scan_config = parse_md(os.path.join(HOME, "workspace/agents/trader/config/SCAN_CONFIG.md"))

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
        "balance": trade_state.get("BALANCE_USDT", trade_state.get("ACCOUNT_BALANCE", "0")),
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
        "costs": get_cost_tracker(),
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
