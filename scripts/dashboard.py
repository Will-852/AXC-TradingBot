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
SCRIPTS_DIR = os.path.join(HOME, "scripts")
HKT = timezone(timedelta(hours=8))
PRICE_HISTORY_PATH = os.path.join(HOME, "shared", "price_history.json")
PNL_HISTORY_PATH = os.path.join(HOME, "shared", "pnl_history.json")
BALANCE_BASELINE_PATH = os.path.join(HOME, "shared", "balance_baseline.json")
CANVAS_HTML = os.path.join(HOME, "canvas", "index.html")

# Whitelist: only decision-relevant params with Chinese labels
PARAMS_DISPLAY = {
    "SCAN_INTERVAL_SEC":       ("掃描間隔", "秒"),
    "RISK_PER_TRADE_PCT":      ("風險/單", "%"),
    "MAX_OPEN_POSITIONS":      ("最大倉位", ""),
    "MAX_POSITION_SIZE_USDT":  ("倉位上限", "$"),
    "RANGE_SL_ATR_MULT":       ("R:SL", "×ATR"),
    "RANGE_TP_ATR_MULT":       ("R:TP", "×ATR"),
    "TREND_SL_ATR_MULT":       ("T:SL", "×ATR"),
    "TREND_TP_ATR_MULT":       ("T:TP", "×ATR"),
    "RANGE_ENTRY_CONFIRM":     ("入場確認", ""),
    "TREND_ENTRY_CONFIRM":     ("趨勢確認", ""),
}


def _get_aster_client():
    """Lazy-load AsterClient for live exchange queries."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.aster_client import AsterClient
    return AsterClient()


def get_live_balance():
    """Get USDT balance from Aster DEX. Falls back to TRADE_STATE.md."""
    try:
        return _get_aster_client().get_usdt_balance()
    except Exception:
        # Fallback to stale file
        ts = parse_md(os.path.join(HOME, "shared/TRADE_STATE.md"))
        try:
            return float(ts.get("BALANCE_USDT", 0))
        except (ValueError, TypeError):
            return 0.0


def get_live_positions():
    """Get open positions from Aster DEX."""
    try:
        raw = _get_aster_client().get_positions()
        positions = []
        for p in raw:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            positions.append({
                "pair": p.get("symbol", ""),
                "direction": "LONG" if amt > 0 else "SHORT",
                "entry_price": float(p.get("entryPrice", 0)),
                "mark_price": float(p.get("markPrice", 0)),
                "size": abs(amt),
                "unrealized_pnl": float(p.get("unRealizedProfit", 0)),
            })
        return positions
    except Exception:
        return []


def get_live_today_pnl():
    """Get today's realized PnL from exchange income history."""
    try:
        client = _get_aster_client()
        now = datetime.now(HKT)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(today_start.timestamp() * 1000)
        income = client.get_income(start_time=start_ms, limit=100)
        realized = sum(float(e["income"]) for e in income if e["incomeType"] == "REALIZED_PNL")
        funding = sum(float(e["income"]) for e in income if e["incomeType"] == "FUNDING_FEE")
        commission = sum(float(e["income"]) for e in income if e["incomeType"] == "COMMISSION")
        return {"realized": realized, "funding": funding, "commission": commission, "net": realized + funding + commission}
    except Exception:
        return None


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


def get_agent_info():
    """Dynamic agent info: model from SOUL.md → openclaw.json fallback, status from launchctl."""
    agent_map = {
        "main": {"name": "主腦", "label": "ai.openclaw.gateway"},
        "scanner": {"name": "掃描器", "label": "ai.openclaw.lightscan"},
        "trader": {"name": "交易員", "label": "ai.openclaw.tradercycle"},
        "heartbeat": {"name": "心跳", "label": "ai.openclaw.heartbeat"},
    }
    # Models from openclaw.json as fallback
    oc_models = {}
    try:
        with open(os.path.join(HOME, "openclaw.json")) as f:
            cfg = json.load(f)
        defaults = cfg.get("agents", {}).get("defaults", {})
        default_model = defaults.get("model", {}).get("primary", "unknown")
        for a in cfg.get("agents", {}).get("list", []):
            aid = a.get("id", "?")
            oc_models[aid] = a.get("model", default_model).split("/")[-1]
    except Exception:
        pass
    la = get_launchagents()
    agents = []
    for aid, meta in agent_map.items():
        # Try SOUL.md for model string
        model = None
        soul_path = os.path.join(HOME, "agents", aid, "workspace", "SOUL.md")
        if os.path.exists(soul_path):
            try:
                with open(soul_path) as f:
                    for line in f:
                        ll = line.lower()
                        if 'model:' in ll or 'model =' in ll:
                            model = line.split(':', 1)[-1].strip()
                            model = model.split('=', 1)[-1].strip().strip('"\'')
                            break
                        if any(m in line for m in ['claude-', 'gpt-', 'gemini-', 'llama']):
                            match = re.search(
                                r'(claude-[\w.-]+|gpt-[\w.-]+|gemini-[\w.-]+|llama-[\w.-]+)',
                                line,
                            )
                            if match:
                                model = match.group(1)
                                break
            except Exception:
                pass
        if not model:
            model = oc_models.get(aid, "未知模型")
        # Status from launchagents
        la_info = la.get(meta["label"], {})
        if la_info.get("pid"):
            status = "online"
            pid = la_info["pid"]
        elif la_info.get("exit") == 0:
            status = "idle"
            pid = None
        elif la_info.get("exit") is not None:
            status = "error"
            pid = None
        else:
            status = "error"
            pid = None
        agents.append({
            "id": aid, "name": meta["name"], "model": model,
            "pid": pid, "status": status,
        })
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
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    ct_date = ct.get("DATE", "")
    daily_total = ct.get("DAILY_TOTAL", "$0.00") if ct_date == today else "—"

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


def get_trading_params():
    """Read ALL params dynamically from config/params.py. Zero hardcoded keys."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "params", os.path.join(HOME, "config/params.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        params = {}
        for k in dir(mod):
            if k.startswith('_'):
                continue
            v = getattr(mod, k)
            if isinstance(v, (int, float, str, bool, list)):
                params[k] = v
        return params
    except Exception as e:
        return {"error": str(e)}


def get_trade_state():
    """Read full trade state dynamically from TRADE_STATE.md.
    Parses ALL fields including position details inside code blocks."""
    path = os.path.join(HOME, "workspace/agents/trader/TRADE_STATE.md")
    state = {
        "balance": 0, "pnl_today": 0, "pnl_total": 0,
        "position": "無", "direction": "—",
        "consecutive_losses": 0, "daily_loss": 0,
        "in_position": False, "market_mode": "RANGE",
        "system_status": "UNKNOWN", "cooldown_active": False,
        "entry_price": 0, "mark_price": 0, "size": 0,
        "sl_price": 0, "tp_price": 0, "unrealized_pnl": 0,
    }
    if not os.path.exists(path):
        return state
    try:
        with open(path) as f:
            content = f.read()
    except Exception:
        return state
    # Float fields
    float_patterns = {
        "balance": r'BALANCE_USDT:\s*\$?([\d.]+)',
        "daily_loss": r'DAILY_LOSS:\s*\$?([\d.]+)',
        "consecutive_losses": r'CONSECUTIVE_LOSSES:\s*(\d+)',
        "entry_price": r'ENTRY_PRICE:\s*\$?([\d.]+)',
        "mark_price": r'MARK_PRICE:\s*\$?([\d.]+)',
        "size": r'(?:^|\n)\s*SIZE:\s*([\d.]+)',
        "sl_price": r'SL_PRICE:\s*\$?([\d.]+)',
        "tp_price": r'TP_PRICE:\s*\$?([\d.]+)',
        "unrealized_pnl": r'UNREALIZED_PNL:\s*\$?(-?[\d.]+)',
    }
    for key, pattern in float_patterns.items():
        m = re.search(pattern, content)
        if m:
            state[key] = float(m.group(1))
    # String fields
    for key, pattern in [
        ("market_mode", r'MARKET_MODE:\s*(\w+)'),
        ("system_status", r'SYSTEM_STATUS:\s*(\w+)'),
    ]:
        m = re.search(pattern, content)
        if m:
            state[key] = m.group(1)
    # Boolean fields
    m = re.search(r'COOLDOWN_ACTIVE:\s*(\w+)', content)
    if m:
        state["cooldown_active"] = m.group(1) == "YES"
    m = re.search(r'POSITION_OPEN:\s*(\w+)', content)
    if m:
        state["in_position"] = m.group(1) == "YES"
    # Position details
    if not state["in_position"]:
        state["position"] = "無"
        state["direction"] = "—"
    else:
        m = re.search(r'(?:^|\n)\s*PAIR:\s*(\S+)', content)
        if m and m.group(1) != '—':
            state["position"] = m.group(1)
        m = re.search(r'DIRECTION:\s*(\S+)', content)
        if m and m.group(1) != '—':
            state["direction"] = m.group(1)
    return state


def get_trade_history():
    """Parse trade history from TRADE_LOG.md markdown table."""
    path = os.path.join(HOME, "workspace/agents/trader/TRADE_LOG.md")
    trades = []
    if not os.path.exists(path):
        return trades
    with open(path) as f:
        content = f.read()
    for line in content.split('\n'):
        line = line.strip()
        if not line.startswith("|") or line.startswith("| #") or line.startswith("|---"):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) < 7:
            continue
        try:
            date = parts[1].strip()
            asset = parts[2].strip().replace("/", "")
            direction = parts[3].strip()
            entry_str = parts[4].replace("$", "").strip()
            exit_str = parts[5].strip()
            pnl_str = parts[6].strip()
            entry = float(entry_str) if entry_str else 0.0
            is_open = "OPEN" in exit_str
            exit_price = 0.0
            if not is_open:
                m = re.search(r'[\$]?([\d.]+)', exit_str)
                if m:
                    exit_price = float(m.group(1))
            pnl = 0.0
            m = re.search(r'[\$]?([\d.]+)', pnl_str)
            if m:
                pnl = float(m.group(1))
                if '-' in pnl_str:
                    pnl = -pnl
            trade = {
                "dir": direction,
                "asset": asset,
                "entry": entry,
                "exit": exit_price if not is_open else None,
                "pnl": pnl,
                "time": date,
                "open": is_open,
                "size": 0,
            }
            # For open trades, try to parse size from detail sections
            if is_open:
                m_size = re.search(r'大小[:：]\s*([\d.]+)', content)
                if m_size:
                    trade["size"] = float(m_size.group(1))
            trades.append(trade)
        except Exception:
            continue
    return trades[-5:]


def get_risk_status():
    """Read risk parameters dynamically from settings.py + TRADE_STATE.md. Zero hardcoded values."""
    import importlib.util
    # Load settings.py via importlib
    circuit_daily = 0
    circuit_single = 0
    cooldown_2 = 0
    cooldown_3 = 0
    max_hold = 0
    try:
        spec = importlib.util.spec_from_file_location(
            "settings", os.path.join(HOME, "scripts/trader_cycle/config/settings.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        circuit_daily = getattr(mod, "CIRCUIT_BREAKER_DAILY", 0)
        circuit_single = getattr(mod, "CIRCUIT_BREAKER_SINGLE", 0)
        cooldown_2 = getattr(mod, "COOLDOWN_2_LOSSES_MIN", 0)
        cooldown_3 = getattr(mod, "COOLDOWN_3_LOSSES_MIN", 0)
        max_hold = getattr(mod, "MAX_HOLD_HOURS", 0)
    except Exception:
        pass
    # Trade state
    trade_state = parse_md(os.path.join(HOME, "workspace/agents/trader/TRADE_STATE.md"))
    cons_losses = 0
    try:
        cons_losses = int(trade_state.get("CONSECUTIVE_LOSSES", "0"))
    except (ValueError, TypeError):
        pass
    balance = 0.0
    try:
        balance = float(trade_state.get("BALANCE_USDT",
                        trade_state.get("ACCOUNT_BALANCE", "0")))
    except (ValueError, TypeError):
        pass
    max_daily_loss = round(balance * circuit_daily, 2) if circuit_daily else 0
    daily_loss = 0.0
    dl_str = trade_state.get("DAILY_LOSS", "0")
    m = re.search(r'[\$]?([\d.]+)', str(dl_str))
    if m:
        daily_loss = float(m.group(1))
    market_mode = trade_state.get("MARKET_MODE", "RANGE")
    cooldown_active = trade_state.get("COOLDOWN_ACTIVE", "NO") == "YES"
    # max consecutive derived from highest cooldown tier
    max_cons = 3 if cooldown_3 > 0 else (2 if cooldown_2 > 0 else 1)
    return {
        "consecutive_losses": cons_losses,
        "max_consecutive_losses": max_cons,
        "daily_loss": daily_loss,
        "max_daily_loss": max_daily_loss,
        "circuit_daily_pct": round(circuit_daily * 100),
        "circuit_single_pct": round(circuit_single * 100),
        "cooldown_2_min": cooldown_2,
        "cooldown_3_min": cooldown_3,
        "max_hold_hours": max_hold,
        "market_mode": market_mode,
        "trigger_cooldown": cooldown_active or cons_losses >= 2,
    }


def get_balance_baseline(current_balance):
    """Get or create balance baseline. Resets start_of_day on new day.
    Returns {"today_pnl": float, "total_pnl": float, "start_of_day": float, "all_time_start": float}."""
    try:
        bal = float(current_balance)
    except (ValueError, TypeError):
        return {"today_pnl": 0, "total_pnl": 0, "start_of_day": 0, "all_time_start": 0}

    today = datetime.now(HKT).strftime("%Y-%m-%d")
    data = None
    if os.path.exists(BALANCE_BASELINE_PATH):
        try:
            with open(BALANCE_BASELINE_PATH) as f:
                data = json.load(f)
        except Exception:
            data = None

    if data is None:
        # First ever call — create baseline
        data = {"start_of_day": bal, "date": today, "all_time_start": bal}
        with open(BALANCE_BASELINE_PATH, "w") as f:
            json.dump(data, f)
    elif data.get("date") != today:
        # New day — roll start_of_day to current balance
        data["start_of_day"] = bal
        data["date"] = today
        with open(BALANCE_BASELINE_PATH, "w") as f:
            json.dump(data, f)

    today_pnl = round(bal - data["start_of_day"], 2)
    total_pnl = round(bal - data["all_time_start"], 2)
    return {
        "today_pnl": today_pnl,
        "total_pnl": total_pnl,
        "start_of_day": data["start_of_day"],
        "all_time_start": data["all_time_start"],
    }


def update_pnl_history(balance):
    """Track PnL history for sparkline chart."""
    data = {"history": []}
    if os.path.exists(PNL_HISTORY_PATH):
        try:
            with open(PNL_HISTORY_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {"history": []}
    try:
        bal = float(balance)
    except (ValueError, TypeError):
        return data.get("history", [])
    baseline = get_balance_baseline(bal)
    now = int(time.time())
    pnl = baseline["today_pnl"]
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


def _enrich_trades(trades, prices, trade_state):
    """Enrich trades: cross-reference TRADE_STATE for open/closed truth,
    add current_price for unrealized PnL on genuinely open trades."""
    position_open = trade_state.get("in_position", False)
    for t in trades:
        if t.get("open"):
            if not position_open:
                # TRADE_STATE says no position — this trade was closed
                # (TRADE_LOG not yet updated by trader agent)
                t["open"] = False
                t["exit"] = "SL/TP"
                t["stale_open"] = True  # flag for frontend
            else:
                sym = t["asset"].replace("USDT", "")
                try:
                    t["current_price"] = float(prices.get(sym, 0))
                except (ValueError, TypeError):
                    t["current_price"] = 0
    return trades


def collect_data():
    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    # All dynamic sources
    agents = get_agent_info()
    params = get_trading_params()
    trade = get_trade_state()

    # LIVE balance from exchange (source of truth)
    live_bal = get_live_balance()

    # LIVE positions from exchange
    live_positions = get_live_positions()
    has_position = len(live_positions) > 0

    # LIVE today's PnL from exchange income
    live_pnl = get_live_today_pnl()
    today_pnl = round(live_pnl["net"], 2) if live_pnl else 0.0

    # Balance baseline for total PnL tracking
    baseline = get_balance_baseline(live_bal)
    pnl_history = update_pnl_history(live_bal)

    # Unrealized PnL from live positions
    unrealized_pnl = round(sum(p["unrealized_pnl"] for p in live_positions), 2)
    unrealized_pct = round(unrealized_pnl / live_bal * 100, 2) if live_bal > 0 else 0.0

    # Position display from live exchange
    if has_position:
        pos = live_positions[0]
        position_str = pos["pair"]
        direction_str = pos["direction"]
    else:
        position_str = trade["position"]  # fallback "無"
        direction_str = trade["direction"]

    # Prices from scan config
    scan_config = parse_md(os.path.join(HOME, "workspace/agents/trader/config/SCAN_CONFIG.md"))
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    prices = {
        "BTC": scan_config.get("BTC_price", "0"),
        "ETH": scan_config.get("ETH_price", "0"),
        "XRP": scan_config.get("XRP_price", "0"),
        "XAG": scan_config.get("XAG_price", "0"),
    }
    price_history = update_price_history(prices)
    last_scan_ts = scan_config.get("last_updated", signal.get("TIMESTAMP", "?"))

    # Build params_display from whitelist
    params_display = []
    for key, (label, unit) in PARAMS_DISPLAY.items():
        val = params.get(key)
        if val is not None:
            if unit == "%" and isinstance(val, float) and val < 1:
                display = f"{val*100:.0f}{unit}"
            elif unit == "$":
                display = f"{unit}{val}"
            else:
                display = f"{val}{unit}"
            params_display.append({"label": label, "value": display})

    # Trade history (for log display only)
    trades = _enrich_trades(get_trade_history(), prices, trade)

    return {
        "timestamp": ts,
        "balance": live_bal,
        "today_pnl": today_pnl,
        "total_pnl": baseline["total_pnl"],
        "mode": trade["market_mode"],
        "signal_active": signal.get("SIGNAL_ACTIVE", "NO"),
        "signal_pair": signal.get("PAIR", "---"),
        "position": position_str,
        "direction": direction_str,
        "in_position": has_position,
        "live_positions": live_positions,
        "consecutive_losses": int(trade["consecutive_losses"]),
        "agents": agents,
        "params": params,
        "params_display": params_display,
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
        "trade_history": trades,
        "risk_status": get_risk_status(),
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pct": unrealized_pct,
    }


def collect_debug():
    """Debug endpoint: raw file contents, existence checks, processes."""
    results = {}
    files_to_check = [
        "workspace/agents/trader/TRADE_STATE.md",
        "shared/SIGNAL.md",
        "workspace/routing/COST_TRACKER.md",
        "workspace/agents/trader/config/SCAN_CONFIG.md",
        "workspace/agents/trader/TRADE_LOG.md",
        "config/params.py",
        "scripts/trader_cycle/config/settings.py",
        "secrets/.env",
    ]
    results["files"] = {}
    for f in files_to_check:
        p = os.path.join(HOME, f)
        exists = os.path.exists(p)
        results["files"][f] = {
            "exists": exists,
            "size": os.path.getsize(p) if exists else 0,
            "modified": os.path.getmtime(p) if exists else 0,
        }
    # Raw TRADE_STATE.md
    ts_path = os.path.join(HOME, "workspace/agents/trader/TRADE_STATE.md")
    try:
        with open(ts_path) as f:
            results["trade_state_raw"] = f.read()
    except Exception as e:
        results["trade_state_raw"] = f"ERROR: {e}"
    # Raw SIGNAL.md
    sig_path = os.path.join(HOME, "shared/SIGNAL.md")
    try:
        with open(sig_path) as f:
            results["signal_raw"] = f.read()
    except Exception as e:
        results["signal_raw"] = f"ERROR: {e}"
    # Raw SCAN_CONFIG.md
    sc_path = os.path.join(HOME, "workspace/agents/trader/config/SCAN_CONFIG.md")
    try:
        with open(sc_path) as f:
            results["scan_config_raw"] = f.read()
    except Exception as e:
        results["scan_config_raw"] = f"ERROR: {e}"
    # Raw TRADE_LOG.md
    tl_path = os.path.join(HOME, "workspace/agents/trader/TRADE_LOG.md")
    try:
        with open(tl_path) as f:
            results["trade_log_raw"] = f.read()
    except Exception as e:
        results["trade_log_raw"] = f"ERROR: {e}"
    # Latest scan log
    results["latest_scan"] = get_scan_log(3)
    # Parsed results
    results["parsed_trade_state"] = get_trade_state()
    results["parsed_trade_history"] = get_trade_history()
    # Launchctl agents
    results["launchctl"] = get_launchagents()
    # Python/claude processes
    procs = []
    if HAS_PSUTIL:
        import psutil as _ps
        for p in _ps.process_iter(['pid', 'name', 'cmdline']):
            try:
                nm = p.info['name'].lower()
                if 'python' in nm or 'claude' in nm or 'openclaw' in nm:
                    cmd = p.info.get('cmdline') or []
                    procs.append({"pid": p.info['pid'], "name": p.info['name'], "cmd": ' '.join(cmd[:4])})
            except Exception:
                pass
    results["processes"] = procs
    return results


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
        elif self.path == "/api/debug":
            data = collect_debug()
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
