"""services.py — LaunchAgent management, system info, trading params, trade state."""

import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone, timedelta

from scripts.dashboard.constants import (
    HOME, HKT, SCRIPTS_DIR,
    _PLIST_DIR, _CORE_SERVICES, PARAMS_DISPLAY,
    PRICES_CACHE_PATH, HAS_PSUTIL,
    parse_md,
)

if HAS_PSUTIL:
    import psutil

try:
    from openclaw_bridge import bridge
except ImportError:
    bridge = None

# ── Profiles cache ───────────────────────────────────────────────────
_profiles_cache = {"ts": 0, "data": {}, "active": ""}

# ── Heatmap cache ────────────────────────────────────────────────────
_heatmap_cache = {"data": None, "ts": 0}
_HEATMAP_CACHE_TTL = 120


# ── Agent Info ───────────────────────────────────────────────────────

def get_agent_info():
    """Dynamic agent info: model from SOUL.md → known map fallback, status from launchctl."""
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
    # Model fallback from openclaw.json (via bridge, empty if unavailable)
    oc_models = bridge.agent_models() if bridge else {}
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


def _auto_bootstrap():
    """Bootstrap stopped core services on dashboard startup."""
    la = get_launchagents()
    uid = os.getuid()
    for label, name in _CORE_SERVICES:
        if label == "ai.openclaw.dashboard":
            continue
        plist = os.path.join(_PLIST_DIR, f"{label}.plist")
        if not os.path.isfile(plist):
            continue
        if label not in la:
            logging.info("Auto-bootstrap: %s", label)
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", plist],
                capture_output=True, timeout=10,
            )


def handle_services():
    """GET /api/services — return all openclaw service statuses."""
    la = get_launchagents()
    services = []
    for label, name in _CORE_SERVICES:
        plist = os.path.join(_PLIST_DIR, f"{label}.plist")
        info = la.get(label, {})
        pid_val = info.get("pid")
        exit_val = info.get("exit")
        services.append({
            "label": label,
            "name": name,
            "running": pid_val is not None,
            "healthy": pid_val is not None or exit_val == 0,
            "pid": pid_val,
            "exit_code": exit_val,
            "plist_exists": os.path.isfile(plist),
        })
    return services


def handle_service_restart(body):
    """POST /api/service/restart — bootout + bootstrap a service."""
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}
    label = data.get("label", "")
    valid_labels = {l for l, _ in _CORE_SERVICES}
    if label not in valid_labels or label == "ai.openclaw.dashboard":
        return {"ok": False, "error": "Invalid service label"}

    plist = os.path.join(_PLIST_DIR, f"{label}.plist")
    if not os.path.isfile(plist):
        return {"ok": False, "error": "plist not found"}

    uid = os.getuid()
    # bootout first (ignore error if not running)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True, timeout=10,
    )
    time.sleep(1)
    # bootstrap
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", plist],
        capture_output=True, timeout=10, text=True,
    )
    ok = result.returncode == 0
    return {"ok": ok, "error": result.stderr.strip() if not ok else None}


# ── Logs & System Info ───────────────────────────────────────────────

def get_scan_log(n=10):
    path = os.path.join(HOME, "shared/SCAN_LOG.md")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines[-n:]


def get_signal_heatmap():
    """Parse SCAN_LOG.md to build 7×24 signal frequency grid (weekday × hour).

    Supports two log formats:
    - New: [2026-03-10 16:38 UTC+8] LIGHT TRIGGER:...
    - Old: 觸發  16:26  STRONG BTCUSDT@okx ...
    Returns list of {day, hour, count} for non-zero cells.
    """
    global _heatmap_cache
    now = time.time()
    if now - _heatmap_cache["ts"] < _HEATMAP_CACHE_TTL and _heatmap_cache["data"] is not None:
        return _heatmap_cache["data"]

    path = os.path.join(HOME, "shared/SCAN_LOG.md")
    if not os.path.exists(path):
        return []

    # 7 days × 24 hours grid
    grid = [[0] * 24 for _ in range(7)]

    # Only new format with full date is trustworthy for weekday×hour grid
    re_new = re.compile(r'^\[(\d{4}-\d{2}-\d{2})\s+(\d{2}):\d{2}.*\]\s+\w+\s+TRIGGER:')

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                m = re_new.match(line)
                if m:
                    try:
                        dt = datetime.strptime(m.group(1), "%Y-%m-%d")
                        weekday = dt.weekday()  # 0=Mon
                        hour = int(m.group(2))
                        grid[weekday][hour] += 1
                    except ValueError:
                        pass
    except Exception:
        pass

    result = []
    for day in range(7):
        for hour in range(24):
            result.append({"day": day, "hour": hour, "count": grid[day][hour]})

    _heatmap_cache["data"] = result
    _heatmap_cache["ts"] = now
    return result


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
    ct = {}
    try:
        ct = parse_md(os.path.join(os.path.expanduser("~/.openclaw/workspace"), "routing/COST_TRACKER.md"))
    except Exception:
        pass
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    ct_date = ct.get("DATE", "")
    daily_total = ct.get("DAILY_TOTAL", "$0.00") if ct_date == today else "—"

    today = datetime.now(HKT).strftime("%Y-%m-%d")
    log_path = os.path.join(HOME, "shared/SCAN_LOG.md")
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
    log_path = os.path.join(HOME, "shared/SCAN_LOG.md")
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
                m_reason = re.search(r'REASON:(\S+)', line)
                if m_reason:
                    raw_reason = m_reason.group(1)
                    # Strip numeric suffix (e.g. _5.0pct, _3, _+2.1) and sign suffixes
                    reason = re.sub(r'_[+-]?[\d.]+\w*$', '', raw_reason)
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


# ── Trading Params ───────────────────────────────────────────────────

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
            elif isinstance(v, dict) and k == "TIMEFRAME_PARAMS":
                params[k] = v

        # Resolve active profile via loader (cached 30s)
        try:
            from config.profiles.loader import load_profile, get_all_profiles
            active = getattr(mod, "ACTIVE_PROFILE", "BALANCED")
            profile = load_profile(active)
            now_t = time.time()
            if now_t - _profiles_cache["ts"] > 30 or _profiles_cache["active"] != active:
                _profiles_cache.update(ts=now_t, data=get_all_profiles(), active=active)
            params["TRADING_PROFILES"] = _profiles_cache["data"]
        except Exception:
            active = getattr(mod, "ACTIVE_PROFILE", "BALANCED")
            profile = {}

        if profile:
            params["_profile_name"] = active
            params["_profile_desc"] = profile.get("description", "")
            params["RISK_PER_TRADE_PCT"] = profile.get("risk_per_trade_pct", 0.02)
            params["MAX_OPEN_POSITIONS"] = profile.get("max_open_positions", 2)
            params["_SL_ATR_MULT_RANGE"] = profile.get("sl_atr_mult_range", 1.2)
            params["_SL_ATR_MULT_TREND"] = profile.get("sl_atr_mult_trend", 1.5)
            params["_TP_ATR_MULT"] = profile.get("tp_atr_mult", 2.0)
            params["_ALLOW_TREND"] = profile.get("allow_trend", True)
            params["_ALLOW_RANGE"] = profile.get("allow_range", True)
            params["_TRIGGER_PCT"] = profile.get("trigger_pct", 0.025)
            params["_TREND_MIN"] = profile.get("trend_min_change_pct")

        return params
    except Exception as e:
        return {"error": str(e)}


# ── Trade State ──────────────────────────────────────────────────────

def get_trade_state():
    """Read trade state — JSON first (v2), MD fallback (v1).
    Returns dashboard-friendly dict with normalized key names.
    """
    state = {
        "balance": 0, "pnl_today": 0, "pnl_total": 0,
        "position": "無", "direction": "—",
        "consecutive_losses": 0, "daily_loss": 0,
        "in_position": False, "market_mode": "RANGE",
        "system_status": "UNKNOWN", "cooldown_active": False,
        "entry_price": 0, "mark_price": 0, "size": 0,
        "sl_price": 0, "tp_price": 0, "unrealized_pnl": 0,
    }

    # Try JSON first (Sprint 2A)
    json_path = os.path.join(HOME, "shared/TRADE_STATE.json")
    if os.path.exists(json_path):
        try:
            with open(json_path) as f:
                data = json.load(f)
            return _json_state_to_dashboard(data, state)
        except Exception:
            pass  # fallback to MD

    # Fallback: parse MD
    path = os.path.join(HOME, "shared/TRADE_STATE.md")
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


def _json_state_to_dashboard(data: dict, defaults: dict) -> dict:
    """Convert TRADE_STATE.json → dashboard-friendly dict."""
    state = dict(defaults)
    sys_d = data.get("system", {})
    state["market_mode"] = sys_d.get("market_mode", "RANGE")
    state["system_status"] = sys_d.get("status", "UNKNOWN")

    acct = data.get("account", {})
    state["balance"] = acct.get("balance_usdt", 0)

    risk = data.get("risk", {})
    state["daily_loss"] = risk.get("daily_loss", 0)
    state["consecutive_losses"] = risk.get("consecutive_losses", 0)
    state["cooldown_active"] = risk.get("cooldown_active", False)

    positions = data.get("positions", [])
    if positions:
        pos = positions[0]
        state["in_position"] = True
        state["position"] = pos.get("pair", "無")
        state["direction"] = pos.get("direction", "—")
        state["entry_price"] = pos.get("entry_price", 0)
        state["mark_price"] = pos.get("mark_price", 0)
        state["size"] = pos.get("size", 0)
        state["sl_price"] = pos.get("sl_price", 0)
        state["tp_price"] = pos.get("tp_price", 0)
        state["unrealized_pnl"] = pos.get("unrealized_pnl", 0)

    return state


# ── Debug ────────────────────────────────────────────────────────────

def collect_debug():
    """Debug endpoint: raw file contents, existence checks, processes."""
    results = {}
    # Files in AXC_HOME
    home_files = [
        "shared/TRADE_STATE.md",
        "shared/SIGNAL.md",
        "shared/SCAN_LOG.md",
        "config/params.py",
        "scripts/trader_cycle/config/settings.py",
        "secrets/.env",
    ]
    home_files += ["shared/SCAN_CONFIG.md", "shared/TRADE_LOG.md"]
    results["files"] = {}
    for f in home_files:
        p = os.path.join(HOME, f)
        exists = os.path.exists(p)
        results["files"][f] = {
            "exists": exists,
            "size": os.path.getsize(p) if exists else 0,
            "modified": os.path.getmtime(p) if exists else 0,
        }
    # Raw TRADE_STATE.md
    ts_path = os.path.join(HOME, "shared/TRADE_STATE.md")
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
    sc_path = os.path.join(HOME, "shared/SCAN_CONFIG.md")
    try:
        with open(sc_path) as f:
            results["scan_config_raw"] = f.read()
    except Exception as e:
        results["scan_config_raw"] = f"ERROR: {e}"
    # Raw TRADE_LOG.md
    tl_path = os.path.join(HOME, "shared/TRADE_LOG.md")
    try:
        with open(tl_path) as f:
            results["trade_log_raw"] = f.read()
    except Exception as e:
        results["trade_log_raw"] = f"ERROR: {e}"
    # Latest scan log
    results["latest_scan"] = get_scan_log(3)
    # Parsed results
    results["parsed_trade_state"] = get_trade_state()
    from scripts.dashboard.analytics import get_trade_history
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
