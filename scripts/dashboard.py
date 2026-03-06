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
import urllib.parse
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
PNL_HISTORY_PATH = os.path.join(HOME, "shared", "pnl_history.json")
BALANCE_BASELINE_PATH = os.path.join(HOME, "shared", "balance_baseline.json")
CANVAS_HTML = os.path.join(HOME, "canvas", "index.html")

# Whitelist: profile-aware params with Chinese labels
# Keys starting with _ are resolved from TRADING_PROFILES[ACTIVE_PROFILE]
PARAMS_DISPLAY = [
    ("RISK_PER_TRADE_PCT",      "風險/單",   "%"),
    ("MAX_OPEN_POSITIONS",      "最大倉位",  ""),
    ("_SL_ATR_MULT",            "止損",      "×ATR"),
    ("_TP_ATR_MULT",            "止盈",      "×ATR"),
    ("_TRIGGER_PCT",            "觸發門檻",  "%"),
    ("_ALLOW_TREND",            "趨勢",      "bool"),
    ("_ALLOW_RANGE",            "區間",      "bool"),
    ("MAX_POSITION_SIZE_USDT",  "倉位上限",  "$"),
    ("SCAN_INTERVAL_SEC",       "掃描間隔",  "秒"),
]


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
    """Get open positions from Aster DEX with full details."""
    try:
        client = _get_aster_client()
        raw = client.get_positions()
        positions = []
        for p in raw:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            symbol = p.get("symbol", "")
            entry = float(p.get("entryPrice", 0))
            mark = float(p.get("markPrice", 0))
            leverage = int(p.get("leverage", 1))
            size = abs(amt)
            notional = size * mark
            upnl = float(p.get("unRealizedProfit", 0))
            upnl_pct = round(upnl / (notional / leverage) * 100, 2) if notional > 0 else 0

            # Fetch SL/TP from open orders
            sl_price = 0
            tp_price = 0
            try:
                orders = client.get_open_orders(symbol)
                for o in orders:
                    if o.get("type") == "STOP_MARKET":
                        sl_price = float(o.get("stopPrice", 0))
                    elif o.get("type") == "TAKE_PROFIT_MARKET":
                        tp_price = float(o.get("stopPrice", 0))
            except Exception:
                pass

            positions.append({
                "pair": symbol,
                "direction": "LONG" if amt > 0 else "SHORT",
                "entry_price": entry,
                "mark_price": mark,
                "size": size,
                "notional": round(notional, 2),
                "leverage": leverage,
                "margin_type": p.get("marginType", "isolated"),
                "margin": round(float(p.get("isolatedWallet", 0)), 2),
                "liq_price": float(p.get("liquidationPrice", 0)),
                "unrealized_pnl": upnl,
                "unrealized_pct": upnl_pct,
                "sl_price": sl_price,
                "tp_price": tp_price,
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
    """Dynamic agent info: model from SOUL.md → known map → openclaw.json fallback, status from launchctl."""
    agent_map = {
        "main": {"name": "主腦", "label": "ai.openclaw.gateway"},
        "aster_scanner": {"name": "掃描器", "label": "ai.openclaw.lightscan"},
        "aster_trader": {"name": "交易員", "label": "ai.openclaw.tradercycle"},
        "heartbeat": {"name": "心跳", "label": "ai.openclaw.heartbeat"},
        "haiku_filter": {"name": "過濾器", "label": None},
        "analyst": {"name": "分析師", "label": None},
        "decision": {"name": "決策者", "label": None},
    }
    # Known model assignments (from config/tier system)
    KNOWN_MODELS = {
        "main": "claude-haiku-4-5",
        "aster_scanner": "claude-haiku-4-5",
        "aster_trader": "claude-sonnet-4-6",
        "heartbeat": "claude-haiku-4-5",
        "haiku_filter": "claude-haiku-4-5",
        "analyst": "claude-sonnet-4-6",
        "decision": "claude-opus",
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
        if not os.path.exists(soul_path):
            soul_path = os.path.join(HOME, "agents", aid, "SOUL.md")
        if os.path.exists(soul_path):
            try:
                with open(soul_path) as f:
                    for line in f:
                        stripped = line.strip()
                        # Skip markdown table rows (greedy regex picks up wrong agent's model)
                        if stripped.startswith("|"):
                            continue
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
                                model = match.group(1).rstrip('.')
                                break
            except Exception:
                pass
        if not model:
            model = KNOWN_MODELS.get(aid, oc_models.get(aid, "未知模型"))
        # Status from launchagents
        label = meta.get("label")
        if label is None:
            # Pipeline agent — no launchd service yet
            status = "planned"
            pid = None
        else:
            la_info = la.get(label, {})
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


def get_scan_log(n=10):
    path = os.path.join(HOME, "workspace/agents/aster_trader/logs/SCAN_LOG.md")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines[-n:]


def get_activity_log(n: int = 50) -> list:
    """讀取最近 N 條活動記錄。前端顯示最新10條，傳50條供滾動。"""
    log_path = os.path.join(HOME, "shared/activity_log.jsonl")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, encoding="utf-8") as f:
            entries = [json.loads(l) for l in f if l.strip()]
        return entries[-n:][::-1]  # 最新在前
    except Exception:
        return []


def get_file_tree():
    dirs = [
        ("config/", "DNA", "params.py, modes/"),
        ("scripts/", "Muscle", "trader_cycle/, scanner_runner.py"),
        ("agents/main/workspace/", "Brain", "SOUL.md, MEMORY.md, skills/"),
        ("agents/aster_trader/workspace/", "Heart", "SOUL.md, trading-rules/"),
        ("agents/aster_scanner/workspace/", "Eye", "SOUL.md, scan-rules/"),
        ("agents/haiku_filter/", "Filter", "SOUL.md"),
        ("agents/analyst/", "Analyst", "SOUL.md"),
        ("agents/decision/", "Decision", "SOUL.md"),
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
    log_path = os.path.join(HOME, "workspace/agents/aster_trader/logs/SCAN_LOG.md")
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
        "aster_trader": {"calls": trader_calls, "cost": 0.0},
        "aster_scanner": {"calls": scanner_calls, "cost": 0.0},
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
    log_path = os.path.join(HOME, "workspace/agents/aster_trader/logs/SCAN_LOG.md")
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
    """Read ALL params dynamically from config/params.py. Zero hardcoded keys.
    Also resolves active profile into flat top-level keys for display."""
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
            elif isinstance(v, dict) and k == "TRADING_PROFILES":
                params[k] = v

        # Resolve active profile → override top-level display values
        profiles = getattr(mod, "TRADING_PROFILES", {})
        active = getattr(mod, "ACTIVE_PROFILE", "CONSERVATIVE")
        profile = profiles.get(active, {})
        if profile:
            params["_profile_name"] = active
            params["_profile_desc"] = profile.get("description", "")
            params["RISK_PER_TRADE_PCT"] = profile.get("risk_per_trade_pct", params.get("RISK_PER_TRADE_PCT", 0.02))
            params["MAX_OPEN_POSITIONS"] = profile.get("max_open_positions", params.get("MAX_OPEN_POSITIONS", 2))
            params["_SL_ATR_MULT"] = profile.get("sl_atr_mult", 1.2)
            params["_TP_ATR_MULT"] = profile.get("tp_atr_mult", 2.0)
            params["_ALLOW_TREND"] = profile.get("allow_trend", True)
            params["_ALLOW_RANGE"] = profile.get("allow_range", True)
            params["_TRIGGER_PCT"] = profile.get("trigger_pct", 0.38)
            params["_TREND_MIN"] = profile.get("trend_min_change_pct")

        return params
    except Exception as e:
        return {"error": str(e)}


def get_trade_state():
    """Read full trade state dynamically from TRADE_STATE.md.
    Parses ALL fields including position details inside code blocks."""
    path = os.path.join(HOME, "workspace/agents/aster_trader/TRADE_STATE.md")
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
    """Read trade history from trades.jsonl, merge entry + exit records.

    trades.jsonl has separate entry (exit=null) and exit (exit+pnl) records.
    We merge them: exit record overwrites the matching entry's exit/pnl fields.
    Matching key: same symbol + side + entry price.
    """
    jsonl_path = os.path.join(HOME, "memory/store/trades.jsonl")
    if not os.path.exists(jsonl_path):
        return []

    raw = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    # Separate entry (exit=null) and exit (exit!=null) records
    entries = []  # list of dicts
    exits = {}    # key → exit record (last one wins)

    for rec in raw:
        symbol = rec.get("symbol", "?")
        side = rec.get("side", "?")
        entry_val = float(rec.get("entry", 0))
        exit_price = rec.get("exit")

        if exit_price is not None:
            # Exit record — store by matching key
            key = f"{symbol}|{side}|{entry_val}"
            exits[key] = rec
        else:
            entries.append(rec)

    # Build merged trade list
    trades = []
    for rec in entries:
        symbol = rec.get("symbol", "?")
        side = rec.get("side", "?")
        entry_val = float(rec.get("entry", 0))
        key = f"{symbol}|{side}|{entry_val}"

        # Try to find matching exit
        exit_rec = exits.pop(key, None)
        if exit_rec:
            exit_price = float(exit_rec["exit"])
            pnl_val = float(exit_rec.get("pnl", 0))
            is_open = False
        else:
            exit_price = None
            pnl_val = 0.0
            is_open = True

        # Derive status
        if is_open:
            status = "open"
        elif pnl_val > 0:
            status = "win"
        elif pnl_val < 0:
            status = "loss"
        else:
            status = "closed"

        suspicious = entry_val < 10 and symbol.endswith("USDT")
        trades.append({
            "dir": side,
            "asset": symbol,
            "entry": entry_val,
            "exit": exit_price,
            "pnl": pnl_val,
            "time": rec.get("ts", ""),
            "open": is_open,
            "size": 0,
            "status": status,
            "suspicious": suspicious,
        })

    # Also include orphan exit records (exit without matching entry)
    for key, rec in exits.items():
        pnl_val = float(rec.get("pnl", 0))
        if pnl_val > 0:
            status = "win"
        elif pnl_val < 0:
            status = "loss"
        else:
            status = "closed"
        entry_val = float(rec.get("entry", 0))
        symbol = rec.get("symbol", "?")
        trades.append({
            "dir": rec.get("side", "?"),
            "asset": symbol,
            "entry": entry_val,
            "exit": float(rec["exit"]),
            "pnl": pnl_val,
            "time": rec.get("ts", ""),
            "open": False,
            "size": 0,
            "status": status,
            "suspicious": entry_val < 10 and symbol.endswith("USDT"),
        })

    return trades[-10:]


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
    trade_state = parse_md(os.path.join(HOME, "workspace/agents/aster_trader/TRADE_STATE.md"))
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
    data["history"] = hist[-500:]
    try:
        with open(PNL_HISTORY_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data["history"]


PRICES_CACHE_PATH = os.path.join(HOME, "shared/prices_cache.json")
_action_cache = {"data": [], "ts": 0}


def get_action_plan(scan_config, trade_state):
    """計算每個幣種嘅行動部署。零額外 API call。30 秒 cache 防並發讀寫。"""
    global _action_cache
    now = time.time()
    if now - _action_cache["ts"] < 30:
        return _action_cache["data"]

    # Dynamic load params (same pattern as get_trading_params)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "params_ap", os.path.join(HOME, "config/params.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        all_symbols = sorted(set(
            getattr(mod, "ASTER_SYMBOLS", []) + getattr(mod, "BINANCE_SYMBOLS", [])
        ))
        active_profile = getattr(mod, "ACTIVE_PROFILE", "BALANCED")
        profiles = getattr(mod, "TRADING_PROFILES", {})
    except Exception:
        return _action_cache["data"]

    profile = profiles.get(active_profile, {})
    threshold = profile.get("trigger_pct", 0.025) * 100  # → 2.5%
    sl_mult = profile.get("sl_atr_mult", 1.2)

    # Read prices_cache.json
    cache = {}
    try:
        with open(PRICES_CACHE_PATH) as f:
            cache = json.load(f)
    except Exception:
        return _action_cache["data"]

    consecutive = int(trade_state.get("consecutive_losses", 0))

    plans = []
    for sym in all_symbols:
        short = sym.replace("USDT", "")
        data = cache.get(sym, {})
        price = float(data.get("price", 0))
        if price <= 0:
            continue

        change = abs(float(data.get("change", 0)))
        atr = float(scan_config.get(f"{short}_ATR", 0))
        support = float(scan_config.get(f"{short}_support", 0))
        resistance = float(scan_config.get(f"{short}_resistance", 0))

        # Status: ready / near / far
        if change >= threshold:
            status = "ready"
        elif change >= threshold * 0.7:
            status = "near"
        else:
            status = "far"

        # SL/TP preview
        sl_dist = atr * sl_mult if atr > 0 else 0
        tp_dist = sl_dist * 2.0 if sl_dist > 0 else 0

        # Global blocker
        blocker = f"連虧 {consecutive}" if consecutive >= 2 else None

        plans.append({
            "symbol": sym, "price": price,
            "change_pct": round(change, 2),
            "threshold_pct": round(threshold, 2),
            "distance": round(max(0, threshold - change), 2),
            "status": status,
            "blocker": blocker,
            "atr": round(atr, 6),
            "support": support, "resistance": resistance,
            "sl_long": round(price - sl_dist, 6) if sl_dist else None,
            "sl_short": round(price + sl_dist, 6) if sl_dist else None,
            "tp_long": round(price + tp_dist, 6) if tp_dist else None,
            "tp_short": round(price - tp_dist, 6) if tp_dist else None,
            "sl_pct": round(sl_dist / price * 100, 2) if sl_dist else None,
        })
    _action_cache["data"] = plans
    _action_cache["ts"] = now
    return plans


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
                t["status"] = "closed"
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

    # Balance baseline for PnL (single source of truth: balance delta)
    baseline = get_balance_baseline(live_bal)
    today_pnl = baseline["today_pnl"]
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
    scan_config = parse_md(os.path.join(HOME, "workspace/agents/aster_trader/config/SCAN_CONFIG.md"))
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    prices = {
        "BTC": scan_config.get("BTC_price", "0"),
        "ETH": scan_config.get("ETH_price", "0"),
        "XRP": scan_config.get("XRP_price", "0"),
        "XAG": scan_config.get("XAG_price", "0"),
    }
    last_scan_ts = scan_config.get("last_updated", signal.get("TIMESTAMP", "?"))

    # Build params_display from whitelist (profile-aware)
    params_display = []
    for key, label, unit in PARAMS_DISPLAY:
        val = params.get(key)
        if val is not None:
            if unit == "bool":
                display = "開" if val else "關"
            elif unit == "%" and isinstance(val, (int, float)):
                display = f"{val*100:.0f}{unit}" if val < 1 else f"{val:.0f}{unit}"
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
        "action_plan": get_action_plan(scan_config, trade),
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
        "active_profile": params.get("ACTIVE_PROFILE", "CONSERVATIVE"),
        "activity_log": get_activity_log(50),
    }


def handle_set_mode(body):
    """POST /api/set_mode — switch trading profile."""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}
    mode = data.get("mode", "").upper()
    valid = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]
    if mode not in valid:
        return 400, {"error": f"Invalid mode. Use: {valid}"}
    params_path = os.path.join(HOME, "config/params.py")
    try:
        with open(params_path) as f:
            content = f.read()
        content = re.sub(r'ACTIVE_PROFILE\s*=\s*"[^"]*"', f'ACTIVE_PROFILE = "{mode}"', content)
        with open(params_path, "w") as f:
            f.write(content)
        return 200, {"ok": True, "mode": mode, "message": f"已切換至 {mode} 模式"}
    except Exception as e:
        return 500, {"error": str(e)}


def handle_suggest_mode():
    """GET /api/suggest_mode — suggest profile based on BTC 24h change."""
    # Read BTC change% from prices_cache.json (populated by scanner)
    change = 0.0
    try:
        with open(PRICES_CACHE_PATH) as f:
            cache = json.load(f)
        change = abs(float(cache.get("BTCUSDT", {}).get("change", 0)))
    except Exception:
        pass
    if change > 5.0:
        suggested = "AGGRESSIVE"
        reason = f"BTC 24H 變化 {change:.1f}% > 5%，市場波動大"
    elif change > 2.0:
        suggested = "BALANCED"
        reason = f"BTC 24H 變化 {change:.1f}%，中等波動"
    else:
        suggested = "CONSERVATIVE"
        reason = f"BTC 24H 變化 {change:.1f}%，市場平靜"
    return {"suggested": suggested, "reason": reason, "btc_change_24h": round(change, 2)}


# ── Binance Connection API ────────────────────────────

SECRETS_ENV_PATH = os.path.join(HOME, "secrets", ".env")


def _get_binance_credentials():
    """Read Binance keys from secrets/.env"""
    api_key = api_secret = ""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("BINANCE_API_SECRET="):
                    api_secret = line.split("=", 1)[1].strip()
    return api_key, api_secret


def _save_binance_credentials(api_key, api_secret):
    """Write or update Binance keys in secrets/.env"""
    os.makedirs(os.path.dirname(SECRETS_ENV_PATH), exist_ok=True)
    lines = []
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                if not line.strip().startswith(("BINANCE_API_KEY=", "BINANCE_API_SECRET=")):
                    lines.append(line.rstrip("\n"))
    lines.append(f"BINANCE_API_KEY={api_key}")
    lines.append(f"BINANCE_API_SECRET={api_secret}")
    with open(SECRETS_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def handle_binance_status():
    """GET /api/binance/status"""
    api_key, api_secret = _get_binance_credentials()
    if not api_key or not api_secret:
        return 200, {"status": "disconnected", "label": "未連接", "balance": None}
    try:
        from binance.spot import Spot
        client = Spot(api_key=api_key, api_secret=api_secret)
        account = client.account()
        balances = {
            b["asset"]: float(b["free"]) + float(b["locked"])
            for b in account["balances"]
            if float(b["free"]) + float(b["locked"]) > 0
        }
        usdt_bal = balances.get("USDT", 0)
        return 200, {
            "status": "connected", "label": "已連接",
            "balance": round(usdt_bal, 2),
            "account_type": account.get("accountType", "SPOT"),
            "key_preview": f"{api_key[:4]}...{api_key[-4:]}",
        }
    except Exception as e:
        return 200, {"status": "error", "label": "驗證失敗", "balance": None, "error": str(e)[:80]}


def handle_binance_connect(body):
    """POST /api/binance/connect"""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"ok": False, "error": "Invalid JSON"}
    api_key = (data.get("api_key") or "").strip()
    api_secret = (data.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return 400, {"ok": False, "error": "API Key 和 Secret 不能為空"}
    try:
        from binance.spot import Spot
        client = Spot(api_key=api_key, api_secret=api_secret)
        account = client.account()
    except Exception as e:
        return 401, {"ok": False, "error": f"驗證失敗：{str(e)[:120]}"}
    _save_binance_credentials(api_key, api_secret)
    usdt = next((float(b["free"]) for b in account["balances"] if b["asset"] == "USDT"), 0)
    return 200, {
        "ok": True, "status": "connected",
        "key_preview": f"{api_key[:4]}...{api_key[-4:]}",
        "balance": round(usdt, 2),
    }


def handle_binance_disconnect():
    """POST /api/binance/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.startswith(("BINANCE_API_KEY=", "BINANCE_API_SECRET="))]
        with open(SECRETS_ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    return 200, {"ok": True, "status": "disconnected"}


def handle_file_read(rel_path):
    """GET /api/file?path=docs/..."""
    if not rel_path.startswith("docs/"):
        return 403, "Forbidden"
    fp = os.path.join(HOME, rel_path)
    if not os.path.exists(fp):
        return 404, "Not found"
    with open(fp) as f:
        return 200, f.read()


def handle_open_folder(rel_path):
    """GET /api/open_folder?path=docs/..."""
    if not rel_path.startswith("docs/"):
        return 403, {"error": "Forbidden"}
    fp = os.path.join(HOME, rel_path)
    if os.path.exists(fp):
        subprocess.Popen(["open", fp])
    return 200, {"ok": True}


def collect_debug():
    """Debug endpoint: raw file contents, existence checks, processes."""
    results = {}
    files_to_check = [
        "workspace/agents/aster_trader/TRADE_STATE.md",
        "shared/SIGNAL.md",
        "workspace/routing/COST_TRACKER.md",
        "workspace/agents/aster_trader/config/SCAN_CONFIG.md",
        "workspace/agents/aster_trader/TRADE_LOG.md",
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
    ts_path = os.path.join(HOME, "workspace/agents/aster_trader/TRADE_STATE.md")
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
    sc_path = os.path.join(HOME, "workspace/agents/aster_trader/config/SCAN_CONFIG.md")
    try:
        with open(sc_path) as f:
            results["scan_config_raw"] = f.read()
    except Exception as e:
        results["scan_config_raw"] = f"ERROR: {e}"
    # Raw TRADE_LOG.md
    tl_path = os.path.join(HOME, "workspace/agents/aster_trader/TRADE_LOG.md")
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


DOCS_ROOT = os.path.join(HOME, "docs")


def get_docs_list() -> list:
    """返回 docs/ 下所有 .md 文件嘅相對路徑。"""
    result = []
    if not os.path.isdir(DOCS_ROOT):
        return result
    for root, dirs, files in os.walk(DOCS_ROOT):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for f in sorted(files):
            if f.endswith(".md"):
                rel = os.path.relpath(os.path.join(root, f), DOCS_ROOT)
                result.append(rel.replace("\\", "/"))
    return result


def serve_doc(filename: str):
    """
    提供 docs/ 文件內容。
    雙重安全：只允許 .md + abspath 確認在 docs/ 範圍內。
    Returns: (status_code, content, content_type)
    """
    if not filename.endswith(".md"):
        return 403, "Not allowed", "text/plain; charset=utf-8"

    safe_path = os.path.abspath(os.path.join(DOCS_ROOT, filename))
    if not safe_path.startswith(os.path.abspath(DOCS_ROOT)):
        return 403, "Forbidden", "text/plain; charset=utf-8"

    if not os.path.exists(safe_path):
        return 404, "Not found", "text/plain; charset=utf-8"

    with open(safe_path, encoding="utf-8") as f:
        content = f.read()
    return 200, content, "text/plain; charset=utf-8"


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/api/data":
            self._json_response(200, collect_data())
        elif path == "/api/debug":
            self._json_response(200, collect_debug())
        elif path == "/api/suggest_mode":
            self._json_response(200, handle_suggest_mode())
        elif path == "/api/binance/status":
            code, data = handle_binance_status()
            self._json_response(code, data)
        elif path == "/api/file":
            rel = qs.get("path", [""])[0]
            code, content = handle_file_read(rel)
            if isinstance(content, str):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self._json_response(code, {"error": content})
        elif path == "/api/open_folder":
            rel = qs.get("path", [""])[0]
            code, data = handle_open_folder(rel)
            self._json_response(code, data)
        elif path == "/details":
            details_path = os.path.join(HOME, "canvas/details.html")
            try:
                with open(details_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"details.html not found")
        elif path == "/api/docs-list":
            self._json_response(200, get_docs_list())
        elif path.startswith("/api/doc/"):
            filename = urllib.parse.unquote(path[9:])
            code, content, ctype = serve_doc(filename)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content.encode() if isinstance(content, str) else content)
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

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length > 0 else ""
        if self.path == "/api/set_mode":
            code, data = handle_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/binance/connect":
            code, data = handle_binance_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/binance/disconnect":
            code, data = handle_binance_disconnect()
            self._json_response(code, data)
        else:
            self._json_response(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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
