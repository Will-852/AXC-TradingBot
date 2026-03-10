#!/usr/bin/env python3
"""
dashboard.py — OpenClaw ICU Dashboard Backend
Serves canvas/index.html + /api/data JSON endpoint.

Usage:
  python3 dashboard.py          # start on :5555
  python3 dashboard.py --port 8080
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import zipfile
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
try:
    from openclaw_bridge import bridge
except ImportError:
    bridge = None

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

PORT = 5555
HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
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


def generate_share_package() -> bytes:
    """
    生成 AXC setup zip（io.BytesIO，記憶體操作）。
    包含：scripts/, config/, canvas/, docs/, backtest/（源碼）,
          agents/*/SOUL.md, CLAUDE.md, requirements.txt, openclaw.json
    排除：secrets/, logs/, memory/, shared/, backups/,
          mlx_model/, __pycache__/, .git/,
          agents/*/workspace/, agents/*/agent/,
          agents/main/sessions/, backtest/data/
    自動生成 secrets/.env.example（變數名，值清空）
    """
    ROOT = HOME
    EXCLUDE_TOP = {
        "secrets", "logs", "memory", "shared",
        "backups", "mlx_model", ".git", ".github",
    }

    def should_exclude(rel: str) -> bool:
        parts = rel.replace("\\", "/").split("/")
        if parts[0] in EXCLUDE_TOP:
            return True
        if (len(parts) >= 3 and parts[0] == "agents"
                and parts[2] in ("workspace", "agent")):
            return True
        if "__pycache__" in parts:
            return True
        if rel.startswith(os.path.join("agents", "main", "sessions")):
            return True
        # backtest/data/ 排除（CSV cache + 生成嘅 PNG/JSONL）
        if rel.startswith(os.path.join("backtest", "data")):
            return True
        return False

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 包含主要目錄
        for inc in ["scripts", "config", "canvas", "docs", "backtest"]:
            inc_path = os.path.join(ROOT, inc)
            if not os.path.exists(inc_path):
                continue
            for dirpath, dirs, files in os.walk(inc_path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for fname in files:
                    fpath = os.path.join(dirpath, fname)
                    rel = os.path.relpath(fpath, ROOT)
                    if not should_exclude(rel):
                        zf.write(fpath, rel)

        # agents/ 只包含 SOUL.md
        agents_path = os.path.join(ROOT, "agents")
        if os.path.exists(agents_path):
            for dirpath, dirs, files in os.walk(agents_path):
                dirs[:] = [d for d in dirs
                           if d not in ("workspace", "agent", "__pycache__")]
                for fname in files:
                    if fname == "SOUL.md":
                        fpath = os.path.join(dirpath, fname)
                        rel = os.path.relpath(fpath, ROOT)
                        zf.write(fpath, rel)

        # 根目錄文件（存在先加）
        for rf in ["CLAUDE.md", "requirements.txt", "openclaw.json"]:
            rpath = os.path.join(ROOT, rf)
            if os.path.exists(rpath):
                zf.write(rpath, rf)

        # 動態生成 .env.example（從現有 .env 取 key 名，清空值）
        env_example = [
            "# AXC Trading System .env.example",
            "# 複製為 secrets/.env 並填入你的 API Key",
            "# cp secrets/.env.example secrets/.env",
            "",
            "# ── AI 推理（必填）────────────────",
            "PROXY_API_KEY=",
            "PROXY_BASE_URL=https://tao.plus7.plus/v1",
            "",
        ]
        env_path = os.path.join(ROOT, "secrets", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key = line.split("=")[0]
                        if key not in ("PROXY_API_KEY", "PROXY_BASE_URL"):
                            env_example.append(f"{key}=")
        else:
            env_example += [
                "ASTER_API_KEY=", "ASTER_API_SECRET=",
                "BINANCE_API_KEY=", "BINANCE_API_SECRET=",
                "TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=",
                "VOYAGE_API_KEY=",
            ]
        zf.writestr("secrets/.env.example", "\n".join(env_example))

        # INSTALL.md（從 guides 搬）
        install_path = os.path.join(ROOT, "docs", "guides", "00-install.md")
        if os.path.exists(install_path):
            zf.write(install_path, "INSTALL.md")

    return buf.getvalue()


def _get_aster_client():
    """Lazy-load AsterClient for live exchange queries."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.aster_client import AsterClient
    return AsterClient()


def _get_hl_client():
    """Lazy-load HyperLiquidClient for live exchange queries."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.hyperliquid_client import HyperLiquidClient
    return HyperLiquidClient()


def _get_binance_client():
    """Lazy-load BinanceClient for live exchange queries."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.binance_client import BinanceClient
    return BinanceClient()


def _normalize_positions(raw, orders, platform):
    """Normalize raw positions + open orders → dashboard format with platform tag.
    Works for Aster/Binance (native format) and HL (pre-normalized by client).
    """
    positions = []
    for p in raw:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        symbol = p.get("symbol", "")
        entry = float(p.get("entryPrice", 0))
        mark = float(p.get("markPrice", 0))
        leverage = int(float(p.get("leverage", 1)))
        size = abs(amt)
        notional = size * mark
        upnl = float(p.get("unRealizedProfit", 0))
        upnl_pct = round(upnl / (notional / leverage) * 100, 2) if notional > 0 else 0

        # SL/TP from open orders — format differs by exchange
        sl_price = 0
        tp_price = 0
        for o in orders:
            # Aster/Binance format: type + stopPrice
            otype = o.get("type", "")
            if otype == "STOP_MARKET":
                if not symbol or o.get("symbol") == symbol:
                    sl_price = float(o.get("stopPrice", 0))
            elif otype == "TAKE_PROFIT_MARKET":
                if not symbol or o.get("symbol") == symbol:
                    tp_price = float(o.get("stopPrice", 0))
            # HL format: coin + orderType contains "Stop" / "Take"
            if not otype and o.get("coin"):
                hl_type = o.get("orderType", "")
                hl_sym = o.get("coin", "") + "USDT"
                if hl_sym == symbol or not symbol:
                    if "stop" in hl_type.lower():
                        sl_price = float(o.get("triggerPx", o.get("limitPx", 0)))
                    elif "take" in hl_type.lower():
                        tp_price = float(o.get("triggerPx", o.get("limitPx", 0)))

        positions.append({
            "pair": symbol,
            "direction": "LONG" if amt > 0 else "SHORT",
            "entry_price": entry,
            "mark_price": mark,
            "size": size,
            "notional": round(notional, 2),
            "leverage": leverage,
            "margin_type": p.get("marginType", "cross"),
            "margin": round(float(p.get("isolatedWallet", p.get("marginUsed", 0))), 2),
            "liq_price": float(p.get("liquidationPrice", p.get("liquidationPx", 0)) or 0),
            "unrealized_pnl": upnl,
            "unrealized_pct": upnl_pct,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "platform": platform,
        })
    return positions


def _query_single_exchange(name, client_fn, cred_check):
    """Query one exchange — called inside thread pool."""
    c1, c2 = cred_check()
    if not c1 or not c2:
        return None
    try:
        client = client_fn()
        orders = []
        try:
            orders = client.get_open_orders()
        except Exception:
            pass
        return {
            "balance": client.get_usdt_balance(),
            "positions": _normalize_positions(client.get_positions(), orders, name),
        }
    except Exception as e:
        logging.warning("exchange query %s error: %s", name, e)
        return None


def get_all_exchange_data():
    """Query all connected exchanges in parallel → per-exchange balance + positions."""
    exchanges = [
        ("aster", _get_aster_client, _get_aster_credentials),
        ("binance", _get_binance_client, _get_binance_credentials),
        ("hyperliquid", _get_hl_client, _get_hl_credentials),
    ]
    result = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_query_single_exchange, name, cfn, cred): name
            for name, cfn, cred in exchanges
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                data = fut.result(timeout=10)
                if data is not None:
                    result[name] = data
            except Exception as e:
                logging.warning("exchange query %s timeout/error: %s", name, e)

    return result


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


_trade_history_cache = {"data": [], "ts": 0}
TRADE_HISTORY_CACHE_TTL = 60  # seconds


def get_live_trade_history():
    """Get last 30 trades from Aster DEX. 60s cache to avoid 429."""
    now = time.time()
    if now - _trade_history_cache["ts"] < TRADE_HISTORY_CACHE_TTL:
        return _trade_history_cache["data"]
    try:
        client = _get_aster_client()
        raw = client._private_request("GET", "/fapi/v1/userTrades", {"limit": 30})
        trades = []
        for t in raw:
            ts_ms = int(t.get("time", 0))
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=HKT)
            trades.append({
                "time": dt.strftime("%m-%d %H:%M"),
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "price": float(t.get("price", 0)),
                "qty": float(t.get("qty", 0)),
                "realizedPnl": float(t.get("realizedPnl", 0)),
                "commission": float(t.get("commission", 0)),
            })
        trades.reverse()  # 最新在前
        _trade_history_cache["data"] = trades
        _trade_history_cache["ts"] = now
        return trades
    except Exception:
        return _trade_history_cache["data"] or []


def _get_exchange_income(start_time=None, end_time=None, limit=100):
    """Query income from all connected exchanges. Returns summed totals.
    Any single exchange failure logs warning but doesn't block others."""
    exchanges = []
    ak, asec = _get_aster_credentials()
    if ak and asec:
        exchanges.append(("Aster", _get_aster_client))
    bk, bsec = _get_binance_credentials()
    if bk and bsec:
        exchanges.append(("Binance", _get_binance_client))
    hpk, haddr = _get_hl_credentials()
    if hpk and haddr:
        exchanges.append(("HL", _get_hl_client))

    if not exchanges:
        return None

    total = {"realized": 0.0, "funding": 0.0, "commission": 0.0, "insurance": 0.0}
    any_success = False
    for name, get_client in exchanges:
        try:
            client = get_client()
            kwargs = {"limit": limit}
            if start_time is not None:
                kwargs["start_time"] = start_time
            if end_time is not None:
                kwargs["end_time"] = end_time
            income = client.get_income(**kwargs)
            total["realized"] += sum(float(e["income"]) for e in income if e["incomeType"] == "REALIZED_PNL")
            total["funding"] += sum(float(e["income"]) for e in income if e["incomeType"] == "FUNDING_FEE")
            total["commission"] += sum(float(e["income"]) for e in income if e["incomeType"] == "COMMISSION")
            total["insurance"] += sum(float(e["income"]) for e in income if e["incomeType"] == "INSURANCE_CLEAR")
            any_success = True
        except Exception:
            logging.warning("Failed to get income from %s", name)

    if not any_success:
        return None
    total["net"] = total["realized"] + total["funding"] + total["commission"] + total["insurance"]
    return total


def get_live_today_pnl():
    """Get today's realized PnL from all connected exchanges."""
    now = datetime.now(HKT)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(today_start.timestamp() * 1000)
    return _get_exchange_income(start_time=start_ms, limit=100)


def _bootstrap_all_time_pnl():
    """One-time pull of all historical income BEFORE today from all connected exchanges.
    Used to seed all_time_realized when baseline has no such field.
    Excludes today — today's PnL is added separately via today_net."""
    today_start = datetime.now(HKT).replace(hour=0, minute=0, second=0, microsecond=0)
    end_ms = int(today_start.timestamp() * 1000)
    result = _get_exchange_income(start_time=None, end_time=end_ms, limit=1000)
    if result is None:
        return 0.0
    logging.info("Bootstrapped all-time realized PnL (excl today): %.4f (r=%.4f f=%.4f c=%.4f)",
                 result["net"], result["realized"], result["funding"], result["commission"])
    return result["net"]


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


def get_scan_log(n=10):
    path = os.path.join(HOME, "shared/SCAN_LOG.md")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines[-n:]


_heatmap_cache = {"data": None, "ts": 0}
_HEATMAP_CACHE_TTL = 120


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
    # Old format (觸發 HH:MM ...) has no date — skipped to avoid false distribution
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
            elif isinstance(v, dict) and k in ("TRADING_PROFILES", "TIMEFRAME_PARAMS"):
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
    path = os.path.join(HOME, "shared/TRADE_STATE.md")
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


def get_trade_stats(exchange_trades=None):
    """Aggregate win/loss stats from REAL exchange fills (API data).

    Uses exchange_trades (from get_live_trade_history) — fills with non-zero
    realizedPnl represent closing trades. This is the only trustworthy source.
    Returns: {total, wins, losses, win_rate, avg_win, avg_loss, profit_factor, source}
    """
    empty = {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
             "avg_win": 0, "avg_loss": 0, "profit_factor": 0, "source": "exchange_api"}

    if not exchange_trades:
        return empty

    # Non-zero realizedPnl = closing fill (actual profit/loss event)
    closed_pnls = []
    for t in exchange_trades:
        rpnl = float(t.get("realizedPnl", 0))
        if rpnl != 0:
            closed_pnls.append(rpnl)

    if not closed_pnls:
        return empty

    wins = [p for p in closed_pnls if p > 0]
    losses = [p for p in closed_pnls if p < 0]
    total = len(closed_pnls)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round(win_count / total * 100, 1) if total > 0 else 0
    avg_win = round(sum(wins) / win_count, 2) if wins else 0
    avg_loss = round(sum(losses) / loss_count, 2) if losses else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0

    return {
        "total": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "source": "exchange_api",
    }


def get_risk_status(live_balance=None):
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
    trade_state = parse_md(os.path.join(HOME, "shared/TRADE_STATE.md"))
    cons_losses = 0
    try:
        cons_losses = int(trade_state.get("CONSECUTIVE_LOSSES", "0"))
    except (ValueError, TypeError):
        pass
    # Prefer live API balance over TRADE_STATE for max_daily_loss calculation
    if live_balance and live_balance > 0:
        balance = live_balance
    else:
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


def get_balance_baseline(current_balance, fee_breakdown=None):
    """Get or create balance baseline. Resets start_of_day on new day.
    Tracks cumulative fees + all_time_realized (from exchange income API).
    total_pnl is realized-based — immune to deposits/withdrawals."""
    try:
        bal = float(current_balance)
    except (ValueError, TypeError):
        return {"today_pnl": 0, "total_pnl": 0, "start_of_day": 0, "all_time_start": 0,
                "cumulative_fees": {"realized": 0, "funding": 0, "commission": 0}}

    today = datetime.now(HKT).strftime("%Y-%m-%d")
    data = None
    if os.path.exists(BALANCE_BASELINE_PATH):
        try:
            with open(BALANCE_BASELINE_PATH) as f:
                data = json.load(f)
        except Exception:
            data = None

    dirty = False
    if data is None:
        # First ever call — create baseline + bootstrap realized PnL from API
        bootstrapped = _bootstrap_all_time_pnl()
        data = {"start_of_day": bal, "date": today, "all_time_start": bal,
                "all_time_realized": bootstrapped,
                "cumulative_fees": {"realized": 0, "funding": 0, "commission": 0, "insurance": 0},
                "yesterday_fees": {"realized": 0, "funding": 0, "commission": 0, "insurance": 0}}
        dirty = True
    else:
        # Migration: seed all_time_realized if missing from existing baseline
        if "all_time_realized" not in data:
            data["all_time_realized"] = _bootstrap_all_time_pnl()
            dirty = True

        if data.get("date") != today:
            # New day — roll. Accumulate yesterday's fees into cumulative totals.
            cum = data.get("cumulative_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
            yest = data.get("yesterday_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
            yesterday_net = 0.0
            for k in ("realized", "funding", "commission", "insurance"):
                val = round(yest.get(k, 0), 4)
                cum[k] = round(cum.get(k, 0) + val, 4)
                yesterday_net += val
            data["cumulative_fees"] = cum
            data["all_time_realized"] = round(data.get("all_time_realized", 0) + yesterday_net, 4)
            data["yesterday_fees"] = {"realized": 0, "funding": 0, "commission": 0, "insurance": 0}
            data["start_of_day"] = bal
            data["date"] = today
            dirty = True

    # Update yesterday_fees with current day's fee breakdown (overwrite, not accumulate)
    if fee_breakdown:
        data["yesterday_fees"] = {
            "realized": fee_breakdown.get("realized", 0),
            "funding": fee_breakdown.get("funding", 0),
            "commission": fee_breakdown.get("commission", 0),
            "insurance": fee_breakdown.get("insurance", 0),
        }
        dirty = True

    if dirty:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(BALANCE_BASELINE_PATH),
                                          delete=False, suffix='.tmp')
        json.dump(data, tmp)
        tmp.close()
        os.replace(tmp.name, BALANCE_BASELINE_PATH)

    today_pnl = round(bal - data["start_of_day"], 2)

    # total_pnl = all_time_realized + today's running net (realized-based, not balance delta)
    today_net = 0.0
    if fee_breakdown:
        for k in ("realized", "funding", "commission", "insurance"):
            today_net += fee_breakdown.get(k, 0)
    total_pnl = round(data.get("all_time_realized", 0) + today_net, 2)

    # Total cumulative = stored cumulative + today's running fees
    cum = data.get("cumulative_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
    today_fees = data.get("yesterday_fees", {"realized": 0, "funding": 0, "commission": 0, "insurance": 0})
    total_fees = {}
    for k in ("realized", "funding", "commission", "insurance"):
        total_fees[k] = round(cum.get(k, 0) + today_fees.get(k, 0), 4)

    return {
        "today_pnl": today_pnl,
        "total_pnl": total_pnl,
        "start_of_day": data["start_of_day"],
        "all_time_start": data["all_time_start"],
        "cumulative_fees": total_fees,
    }



def update_pnl_history_verified(today_pnl):
    """Track PnL history using verified today_pnl value (from fee_breakdown.net)."""
    data = {"history": []}
    if os.path.exists(PNL_HISTORY_PATH):
        try:
            with open(PNL_HISTORY_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {"history": []}
    now = int(time.time())
    pnl = round(today_pnl, 2)
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


def calc_drawdown(pnl_history, all_time_start, current_balance):
    """Calculate current and max drawdown.

    Uses current_balance (real account value) for current drawdown instead of
    pnl_history last entry, because pnl_history tracks today_pnl which resets
    daily — causing cross-day drawdown to be invisible.

    peak_value is still derived from pnl_history max + all_time_start (best
    available estimate of historical peak account value).
    """
    empty = {"current_dd": 0, "current_dd_pct": 0, "max_dd": 0, "max_dd_pct": 0, "peak_value": 0}
    if all_time_start <= 0:
        return empty

    # Peak account value = initial balance + highest recorded PnL snapshot
    peak_pnl = 0.0
    if pnl_history:
        for point in pnl_history:
            v = point.get("v", 0)
            if v > peak_pnl:
                peak_pnl = v

    peak_value = round(all_time_start + peak_pnl, 2)

    # Current drawdown: use REAL balance, not pnl_history last entry
    current_dd = max(peak_value - current_balance, 0)
    # Max drawdown: larger of historical intra-day max or current real drawdown
    # (intra-day max from pnl_history may undercount cross-day drops)
    intraday_max_dd = 0.0
    if pnl_history:
        running_peak = 0.0
        for point in pnl_history:
            v = point.get("v", 0)
            if v > running_peak:
                running_peak = v
            dd = running_peak - v
            if dd > intraday_max_dd:
                intraday_max_dd = dd
    max_dd = max(current_dd, intraday_max_dd)

    return {
        "current_dd": round(current_dd, 2),
        "current_dd_pct": round(current_dd / peak_value * 100, 2) if peak_value > 0 else 0,
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / peak_value * 100, 2) if peak_value > 0 else 0,
        "peak_value": peak_value,
    }


PRICES_CACHE_PATH = os.path.join(HOME, "shared/prices_cache.json")
_4h_cache = {"data": {}, "ts": 0}
_4H_CACHE_TTL = 120  # seconds


# API bases for kline fetching
_KLINE_API = {
    "binance": "https://fapi.binance.com",
    "aster": "https://fapi.asterdex.com",
}
# Symbols only on Aster (not Binance Futures)
_ASTER_ONLY = {"XAGUSDT", "XAUUSDT"}


def _fetch_kline_change(symbol, interval="4h"):
    """Fetch kline from appropriate exchange for a single symbol.
    Returns (symbol, interval, pct_change) or None."""
    base = _KLINE_API["aster"] if symbol in _ASTER_ONLY else _KLINE_API["binance"]
    url = f"{base}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=2"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if len(data) >= 1:
                candle = data[-1]  # current (incomplete) candle for real-time feel
                open_price = float(candle[1])
                close_price = float(candle[4])
                if open_price > 0:
                    return round((close_price - open_price) / open_price * 100, 2)
    except Exception:
        pass
    return None


def get_multi_interval_changes(symbols):
    """Get 4H + 1H + 24H change for all symbols. 120s cache, concurrent fetching.
    Returns {symbol: {"4h": pct, "1h": pct, "24h": pct}}."""
    global _4h_cache
    now = time.time()
    if now - _4h_cache["ts"] < _4H_CACHE_TTL and _4h_cache["data"]:
        return _4h_cache["data"]

    intervals = ["1h", "4h"]
    result = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for sym in symbols:
            for iv in intervals:
                futures[pool.submit(_fetch_kline_change, sym, iv)] = (sym, iv)
        for fut in as_completed(futures):
            sym, iv = futures[fut]
            try:
                val = fut.result(timeout=8)
                if val is not None:
                    if sym not in result:
                        result[sym] = {}
                    result[sym][iv] = val
            except Exception:
                pass

    _4h_cache["data"] = result
    _4h_cache["ts"] = now
    return result


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
        # Trader PAIRS = actually tradeable symbols (from trader_cycle settings)
        try:
            spec_tc = importlib.util.spec_from_file_location(
                "settings_ap", os.path.join(HOME, "scripts/trader_cycle/config/settings.py")
            )
            mod_tc = importlib.util.module_from_spec(spec_tc)
            spec_tc.loader.exec_module(mod_tc)
            trader_pairs = set(getattr(mod_tc, "PAIRS", []))
        except Exception:
            trader_pairs = set()
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

    # Fetch 4H changes (cached, concurrent)
    # Fetch multi-interval changes (1H + 4H) for all symbols
    interval_changes = get_multi_interval_changes(all_symbols)

    plans = []
    for sym in all_symbols:
        short = sym.replace("USDT", "")
        data = cache.get(sym, {})
        price = float(data.get("price", 0))
        if price <= 0:
            continue

        # prices_cache.json stores change as percentage (e.g. 4.3 = 4.3%)
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

        is_tradeable = sym in trader_pairs

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
            "tp_pct": round(tp_dist / price * 100, 2) if tp_dist else None,
            "changes": interval_changes.get(sym, {}),
            "tradeable": is_tradeable,
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
    if _is_demo_mode():
        return _get_demo_data()

    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    # All dynamic sources
    agents = get_agent_info()
    params = get_trading_params()
    trade = get_trade_state()

    # Multi-exchange breakdown — single pass, reuse for balance/positions
    exchange_data = get_all_exchange_data()

    # Extract Aster data from exchange_data (avoid double API call)
    _aster_data = exchange_data.get("aster", {})
    live_bal = _aster_data.get("balance", 0.0) if _aster_data else get_live_balance()
    live_positions = _aster_data.get("positions", []) if _aster_data else get_live_positions()
    has_position = len(live_positions) > 0

    # Balance baseline for PnL
    # Today fee breakdown from exchange income API (verified source)
    fee_breakdown_raw = get_live_today_pnl()
    fee_breakdown = fee_breakdown_raw or {"realized": 0, "funding": 0, "commission": 0, "net": 0}

    # Balance baseline + cumulative fee tracking
    baseline = get_balance_baseline(live_bal, fee_breakdown if fee_breakdown_raw else None)

    # today_pnl: use verified source when API succeeded, fallback to balance delta when API failed
    today_pnl = fee_breakdown["net"] if fee_breakdown_raw is not None else baseline["today_pnl"]
    pnl_history = update_pnl_history_verified(today_pnl)

    # Unrealized PnL from live positions
    unrealized_pnl = round(sum(p["unrealized_pnl"] for p in live_positions), 4)
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
    scan_config = parse_md(os.path.join(HOME, "shared/SCAN_CONFIG.md"))
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

    # Exchange trade history (real fills from API)
    exchange_trades = get_live_trade_history()

    # New: trade stats (from real exchange fills), drawdown, signal heatmap
    trade_stats = get_trade_stats(exchange_trades)
    drawdown = calc_drawdown(pnl_history, baseline.get("all_time_start", 0), live_bal)
    signal_heatmap = get_signal_heatmap()

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
        "exchange_trades": exchange_trades,
        "risk_status": get_risk_status(live_bal),
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pct": unrealized_pct,
        "fee_breakdown": fee_breakdown,
        "cumulative_fees": baseline.get("cumulative_fees", {}),
        "active_profile": params.get("ACTIVE_PROFILE", "CONSERVATIVE"),
        "activity_log": get_activity_log(50),
        "trade_stats": trade_stats,
        "drawdown": drawdown,
        "signal_heatmap": signal_heatmap,
        "demo_mode": False,
        "exchanges": exchange_data,
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


def handle_api_state():
    """GET /api/state — AXC state endpoint. Returns trade state + signal + key params."""
    trade = get_trade_state()
    signal = parse_md(os.path.join(HOME, "shared/SIGNAL.md"))
    params = get_trading_params()
    return {
        "trade_state": trade,
        "signal": {
            "active": signal.get("SIGNAL_ACTIVE", "NO"),
            "pair": signal.get("PAIR", "—"),
            "direction": signal.get("DIRECTION", "—"),
            "strategy": signal.get("STRATEGY", "—"),
            "strength": signal.get("STRENGTH", "—"),
            "score": signal.get("SCORE", "0"),
            "entry_price": signal.get("ENTRY_PRICE", "0"),
            "timestamp": signal.get("TIMESTAMP", "—"),
            "reasons": signal.get("REASONS", "—"),
            "trigger_count": signal.get("TRIGGER_COUNT", "0"),
            "scan_status": signal.get("SCAN_STATUS", "—"),
        },
        "active_profile": params.get("ACTIVE_PROFILE", "CONSERVATIVE"),
        "trading_enabled": params.get("TRADING_ENABLED", True),
    }


def handle_api_config():
    """GET /api/config — AXC config endpoint. Returns all trading params."""
    return get_trading_params()


def handle_set_trading(body):
    """POST /api/config/trading — toggle TRADING_ENABLED in params.py."""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return 400, {"error": "Field 'enabled' must be boolean"}
    params_path = os.path.join(HOME, "config/params.py")
    try:
        with open(params_path) as f:
            content = f.read()
        if "TRADING_ENABLED" in content:
            content = re.sub(
                r'TRADING_ENABLED\s*=\s*\w+',
                f'TRADING_ENABLED = {enabled}',
                content,
            )
        else:
            content += f'\nTRADING_ENABLED = {enabled}\n'
        with open(params_path, "w") as f:
            f.write(content)
        return 200, {"ok": True, "enabled": enabled}
    except Exception as e:
        return 500, {"error": str(e)}


def handle_close_position(body):
    """POST /api/close-position — market close a position via dashboard.
    Reuses _get_*_client() + client.close_position_market(symbol).
    Server binds 127.0.0.1 only; frontend enforces confirmation modal.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()

    if not symbol or not symbol.endswith("USDT"):
        return 400, {"error": f"Invalid symbol: {symbol}"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    try:
        client = client_fns[platform]()
        result = client.close_position_market(symbol)
        logging.info("Dashboard close-position: %s %s → %s", platform, symbol, result)
        return 200, {"ok": True, "result": result}
    except Exception as e:
        logging.error("Dashboard close-position failed: %s %s → %s", platform, symbol, e)
        return 500, {"error": str(e)}


def handle_modify_sltp(body):
    """POST /api/modify-sltp — cancel+recreate SL/TP orders for a position.
    Same cancel+recreate pattern as tg_bot move_sl_to_entry and adjust_positions trailing SL.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()
    try:
        sl_price = float(data.get("sl_price") or 0)
        tp_price = float(data.get("tp_price") or 0)
    except (ValueError, TypeError):
        return 400, {"error": "SL/TP 價格必須為數字"}

    if not symbol or not symbol.endswith("USDT"):
        return 400, {"error": f"Invalid symbol: {symbol}"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}
    if sl_price <= 0 and tp_price <= 0:
        return 400, {"error": "至少提供一個 SL 或 TP 價格"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    try:
        client = client_fns[platform]()

        # 1. Get position to determine side + qty
        positions = client.get_positions(symbol)
        pos = next((p for p in positions if float(p.get("positionAmt", 0)) != 0), None)
        if not pos:
            return 400, {"error": f"{symbol} 無持倉"}

        amt = float(pos["positionAmt"])
        direction = "LONG" if amt > 0 else "SHORT"
        close_side = "SELL" if direction == "LONG" else "BUY"
        qty = abs(amt)

        results = {}
        warnings = []

        # Helper: classify order as "sl"/"tp"/None across Aster/Binance + HL formats
        def _order_kind(o):
            otype = o.get("type", "")
            if otype == "STOP_MARKET":
                return "sl"
            if otype == "TAKE_PROFIT_MARKET":
                return "tp"
            # HL format: orderType contains "Stop Market" / "Take Profit"
            if not otype and o.get("coin"):
                hl_type = o.get("orderType", "").lower()
                if "stop" in hl_type:
                    return "sl"
                if "take" in hl_type:
                    return "tp"
            return None

        def _order_id(o):
            return str(o.get("orderId", o.get("oid", "")))

        # Fetch orders once if either SL or TP needs modification
        orders = client.get_open_orders(symbol) if (sl_price > 0 or tp_price > 0) else []

        # 2. Modify SL if provided
        if sl_price > 0:
            for o in orders:
                if _order_kind(o) == "sl":
                    try:
                        client.cancel_order(symbol, _order_id(o))
                    except Exception as e:
                        logging.warning("Cancel SL order %s failed: %s", _order_id(o), e)
            try:
                client.create_stop_market(symbol, close_side, qty, sl_price)
                results["sl"] = sl_price
                logging.info("Dashboard modify SL: %s %s → %s", platform, symbol, sl_price)
            except Exception as e:
                warnings.append(f"SL 設置失敗: {e}")
                logging.error("Dashboard create SL failed: %s %s → %s", platform, symbol, e)

        # 3. Modify TP if provided
        if tp_price > 0:
            for o in orders:
                if _order_kind(o) == "tp":
                    try:
                        client.cancel_order(symbol, _order_id(o))
                    except Exception as e:
                        logging.warning("Cancel TP order %s failed: %s", _order_id(o), e)
            try:
                client.create_take_profit_market(symbol, close_side, qty, tp_price)
                results["tp"] = tp_price
                logging.info("Dashboard modify TP: %s %s → %s", platform, symbol, tp_price)
            except Exception as e:
                warnings.append(f"TP 設置失敗: {e}")
                logging.error("Dashboard create TP failed: %s %s → %s", platform, symbol, e)

        if not results and warnings:
            return 500, {"error": "; ".join(warnings)}

        resp = {"ok": True, "results": results}
        if warnings:
            resp["warnings"] = warnings
        return 200, resp

    except Exception as e:
        logging.error("Dashboard modify-sltp failed: %s %s → %s", platform, symbol, e)
        return 500, {"error": str(e)}


def handle_api_scan_log():
    """GET /api/scan-log — AXC scan log endpoint."""
    return {"lines": get_scan_log(n=20)}


def handle_api_health():
    """GET /api/health — AXC health endpoint. Agent status + timestamps + heartbeat."""
    agents = get_agent_info()

    # File mtime checks (same as tg_bot cmd_health)
    mtime_checks = {
        "main": os.path.join(HOME, "agents/main/sessions/sessions.json"),
        "heartbeat": os.path.join(HOME, "logs/heartbeat.log"),
        "signal": os.path.join(HOME, "shared/SIGNAL.md"),
    }
    timestamps = {}
    now = time.time()
    for key, path in mtime_checks.items():
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            age_min = int((now - mtime) / 60)
            timestamps[key] = {"age_min": age_min, "status": "ok" if age_min < 10 else ("warn" if age_min < 30 else "stale")}
        else:
            timestamps[key] = {"age_min": -1, "status": "missing"}

    # Scanner heartbeat
    scanner = {"status": "missing", "detail": "", "age_min": -1}
    hb_path = os.path.join(HOME, "logs/scanner_heartbeat.txt")
    if os.path.exists(hb_path):
        try:
            with open(hb_path) as f:
                hb = f.read().strip()
            parts = hb.split(" ", 2)
            ts = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            age_min = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
            scanner = {
                "status": parts[1] if len(parts) > 1 else "unknown",
                "detail": parts[2] if len(parts) > 2 else "",
                "age_min": age_min,
            }
        except Exception:
            scanner = {"status": "error", "detail": "parse failed", "age_min": -1}

    # Memory count
    memory_count = 0
    emb_path = os.path.join(HOME, "memory/index/embeddings.npy")
    if os.path.exists(emb_path):
        try:
            import numpy as np
            embs = np.load(emb_path)
            memory_count = embs.shape[0]
        except Exception:
            pass

    # SCAN_LOG.md mtime (used by check_and_push_alerts for stall detection)
    scan_log_age_min = -1
    scan_log_path = os.path.join(HOME, "shared/SCAN_LOG.md")
    if os.path.exists(scan_log_path):
        scan_log_age_min = int((time.time() - os.path.getmtime(scan_log_path)) / 60)

    return {
        "agents": agents,
        "timestamps": timestamps,
        "scanner": scanner,
        "scan_log_age_min": scan_log_age_min,
        "memory_count": memory_count,
        "uptime": get_uptime(),
    }


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


def _get_aster_credentials():
    """Read Aster keys from secrets/.env"""
    api_key = api_secret = ""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ASTER_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("ASTER_API_SECRET="):
                    api_secret = line.split("=", 1)[1].strip()
    return api_key, api_secret


def _save_aster_credentials(api_key, api_secret):
    """Write or update Aster keys in secrets/.env"""
    os.makedirs(os.path.dirname(SECRETS_ENV_PATH), exist_ok=True)
    lines = []
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                if not line.strip().startswith(("ASTER_API_KEY=", "ASTER_API_SECRET=")):
                    lines.append(line.rstrip("\n"))
    lines.append(f"ASTER_API_KEY={api_key}")
    lines.append(f"ASTER_API_SECRET={api_secret}")
    with open(SECRETS_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def handle_aster_status():
    """GET /api/aster/status"""
    api_key, api_secret = _get_aster_credentials()
    if not api_key or not api_secret:
        return 200, {"status": "disconnected", "label": "未連接", "balance": None}
    try:
        client = _get_aster_client()
        bal = client.get_usdt_balance()
        return 200, {
            "status": "connected", "label": "已連接",
            "balance": round(bal, 2),
            "key_preview": f"{api_key[:4]}...{api_key[-4:]}",
        }
    except Exception as e:
        return 200, {"status": "error", "label": "驗證失敗", "balance": None, "error": str(e)[:80]}


def handle_aster_connect(body):
    """POST /api/aster/connect"""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"ok": False, "error": "Invalid JSON"}
    api_key = (data.get("api_key") or "").strip()
    api_secret = (data.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return 400, {"ok": False, "error": "API Key 和 Secret 不能為空"}
    _save_aster_credentials(api_key, api_secret)
    try:
        # Reimport with new creds
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from trader_cycle.exchange.aster_client import AsterClient
        client = AsterClient()
        bal = client.get_usdt_balance()
        return 200, {"ok": True, "status": "connected", "key_preview": f"{api_key[:4]}...{api_key[-4:]}", "balance": round(bal, 2)}
    except Exception as e:
        return 401, {"ok": False, "error": f"驗證失敗：{str(e)[:120]}"}


def handle_aster_disconnect():
    """POST /api/aster/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.strip().startswith(("ASTER_API_KEY=", "ASTER_API_SECRET="))]
        with open(SECRETS_ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    return 200, {"ok": True, "status": "disconnected"}


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


def _is_demo_mode() -> bool:
    """True when no exchange credentials configured — triggers demo data."""
    ak, asec = _get_aster_credentials()
    if ak and asec:
        return False
    bk, bsec = _get_binance_credentials()
    if bk and bsec:
        return False
    hpk, haddr = _get_hl_credentials()
    if hpk and haddr:
        return False
    return True


# ── Demo data (shown when no exchange credentials configured) ──
# Field names must match what canvas/index.html render functions expect.
DEMO_DATA = {
    "balance": 1083.42,
    "today_pnl": 12.56,
    "total_pnl": 83.42,
    "mode": "RANGE",
    "signal_active": "YES",
    "signal_pair": "BTCUSDT",
    "position": "BTCUSDT LONG 0.001 BTC @ $67,234",
    "direction": "LONG",
    "in_position": True,
    "live_positions": [{
        "pair": "BTCUSDT", "direction": "LONG", "size": "0.001",
        "leverage": "5x", "entry_price": 67234.0, "mark_price": 67890.0,
        "sl_price": 66500.0, "tp_price": 68500.0,
        "unrealized_pnl": "0.66", "unrealized_pct": "0.98",
        "liq_price": 63200.0, "margin": "13.45",
    }],
    "consecutive_losses": 0,
    "agents": [
        {"name": "scanner", "status": "running", "last_seen": ""},
        {"name": "analyst", "status": "idle", "last_seen": ""},
        {"name": "risk_mgr", "status": "idle", "last_seen": ""},
        {"name": "executor", "status": "idle", "last_seen": ""},
    ],
    "params": {
        "RISK_PER_TRADE_PCT": 1.5, "MAX_OPEN_POSITIONS": 3,
        "ACTIVE_PROFILE": "BALANCED",
    },
    "params_display": [
        {"key": "RISK_PER_TRADE_PCT", "label": "風險/單", "value": "1.5", "unit": "%"},
        {"key": "MAX_OPEN_POSITIONS", "label": "最大倉位", "value": "3", "unit": ""},
        {"key": "_SL_ATR_MULT", "label": "止損", "value": "1.5", "unit": "×ATR"},
        {"key": "_TP_ATR_MULT", "label": "止盈", "value": "3.0", "unit": "×ATR"},
        {"key": "_TRIGGER_PCT", "label": "觸發門檻", "value": "2.0", "unit": "%"},
    ],
    "scan_log": [],  # filled dynamically with today's date
    "file_tree": [],
    "prices": {"BTCUSDT": 67890.0, "ETHUSDT": 3456.78, "SOLUSDT": 142.35},
    "action_plan": [
        {"symbol": "BTCUSDT", "price": 67890.0, "change_pct": 2.3,
         "threshold_pct": 2.0, "atr": 1250.0,
         "sl_long": 66500.0, "tp_long": 68500.0,
         "sl_short": 69200.0, "tp_short": 67000.0,
         "support": 66000.0, "resistance": 69500.0, "status": "ready"},
        {"symbol": "ETHUSDT", "price": 3456.78, "change_pct": 1.1,
         "threshold_pct": 2.0, "atr": 85.0,
         "sl_long": 3370.0, "tp_long": 3540.0,
         "sl_short": 3540.0, "tp_short": 3370.0,
         "support": 3350.0, "resistance": 3550.0, "status": "ready"},
        {"symbol": "SOLUSDT", "price": 142.35, "change_pct": 0.5,
         "threshold_pct": 2.0, "atr": 4.2,
         "sl_long": 138.0, "tp_long": 150.0,
         "sl_short": 146.0, "tp_short": 138.0,
         "support": 136.0, "resistance": 148.0, "status": "ready"},
    ],
    "trigger": "OFF",
    "scan_count": "42",
    "last_scan": "",
    "agent_activity": [],
    "uptime": "2d 14h 32m",
    "git": {"branch": "main", "commit": "demo"},
    "telegram": {"status": "demo", "label": "Demo"},
    "trigger_summary": {
        "by_asset": [
            {"name": "BTCUSDT", "pct": 60, "count": 3},
            {"name": "ETHUSDT", "pct": 40, "count": 2},
        ],
        "by_reason": [
            {"name": "breakout_long", "pct": 40, "count": 2},
            {"name": "range_reversal", "pct": 40, "count": 2},
            {"name": "trend_continuation", "pct": 20, "count": 1},
        ],
        "total": 5,
    },
    "pnl_history": [],  # filled dynamically
    "trade_history": [],
    "exchange_trades": [
        {"side": "BUY", "symbol": "BTCUSDT", "time": "", "price": "67234.00",
         "qty": "0.001", "realizedPnl": "0.00", "commission": "0.034"},
        {"side": "SELL", "symbol": "ETHUSDT", "time": "", "price": "3456.00",
         "qty": "0.05", "realizedPnl": "3.80", "commission": "0.086"},
        {"side": "BUY", "symbol": "ETHUSDT", "time": "", "price": "3380.00",
         "qty": "0.05", "realizedPnl": "0.00", "commission": "0.085"},
        {"side": "SELL", "symbol": "SOLUSDT", "time": "", "price": "148.50",
         "qty": "1.2", "realizedPnl": "7.44", "commission": "0.089"},
    ],
    "risk_status": {
        "consecutive_losses": 1, "max_consecutive_losses": 3,
        "daily_loss": "0.00", "max_daily_loss": "14.00",
        "circuit_single_pct": 25, "circuit_daily_pct": 15,
        "market_mode": "NORMAL",
    },
    "unrealized_pnl": 0.656,
    "unrealized_pct": 0.98,
    "fee_breakdown": {
        "realized": "32.00", "funding": "-0.45",
        "commission": "1.29", "net": "30.26",
    },
    "active_profile": "BALANCED",
    "activity_log": [
        {"time": "", "msg": "BTCUSDT LONG opened @ $67,234", "type": "trade_entry"},
        {"time": "", "msg": "DEEP scan triggered BTCUSDT (score 78)", "type": "signal"},
        {"time": "", "msg": "ETHUSDT LONG closed +$22.40 (+2.25%)", "type": "trade_exit"},
        {"time": "", "msg": "Risk check passed — drawdown 1.2%", "type": "system"},
        {"time": "", "msg": "Mode switched to RANGE", "type": "mode_change"},
    ],
}


def _get_demo_data() -> dict:
    """Return demo data with dynamic timestamps and sine-wave PnL history."""
    now = datetime.now(HKT)
    ts = now.strftime("%Y-%m-%d %H:%M:%S UTC+8")
    today = now.strftime("%Y-%m-%d")
    data = {k: v for k, v in DEMO_DATA.items()}  # shallow copy
    data["timestamp"] = ts
    data["last_scan"] = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    data["demo_mode"] = True

    # Scan log with today's date so frontend filter picks them up
    data["scan_log"] = [
        f"[{today} 10:30:00] LIGHT scan #42 — 6 pairs, 0 triggers",
        f"[{today} 10:25:00] LIGHT scan #41 — 6 pairs, 0 triggers",
        f"[{today} 10:15:00] DEEP scan #8 — TRIGGER:BTCUSDT score=78",
        f"[{today} 10:00:00] LIGHT scan #40 — 6 pairs, 1 triggers",
        f"[{today} 09:45:00] LIGHT scan #39 — 6 pairs, 0 triggers",
    ]

    # PnL history: {t: unix_seconds, v: pnl_value}
    pnl_history = []
    base_pnl = 0.0
    for i in range(24):
        t = now - timedelta(hours=(24 - i) * 7)
        val = base_pnl + 40 * math.sin(i * 0.5) + i * 3.5
        pnl_history.append({
            "t": int(t.timestamp()),
            "v": round(val, 2),
        })
    data["pnl_history"] = pnl_history

    # Activity log with dynamic timestamps, field name = "time"
    activity = []
    for j, entry in enumerate(DEMO_DATA["activity_log"]):
        e = dict(entry)
        e["time"] = (now - timedelta(minutes=30 * (j + 1))).strftime("%Y-%m-%d %H:%M")
        activity.append(e)
    data["activity_log"] = activity

    # Exchange trades with dynamic timestamps
    trades = []
    for k, tr in enumerate(DEMO_DATA["exchange_trades"]):
        t2 = dict(tr)
        t2["time"] = (now - timedelta(hours=k * 6 + 1)).strftime("%Y-%m-%d %H:%M:%S")
        trades.append(t2)
    data["exchange_trades"] = trades

    # Agent last_seen
    agents = []
    for a in DEMO_DATA["agents"]:
        a2 = dict(a)
        a2["last_seen"] = (now - timedelta(minutes=2)).strftime("%H:%M:%S")
        agents.append(a2)
    data["agents"] = agents

    return data


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


# ── HyperLiquid Connection API ────────────────────────────

def _get_hl_credentials():
    """Read HL keys from secrets/.env"""
    pk = addr = ""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("HL_PRIVATE_KEY="):
                    pk = line.split("=", 1)[1].strip()
                elif line.startswith("HL_ACCOUNT_ADDRESS="):
                    addr = line.split("=", 1)[1].strip()
    return pk, addr


def _save_hl_credentials(private_key, account_address):
    """Write or update HL keys in secrets/.env"""
    lines = []
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            for line in f.read().splitlines():
                if not line.strip().startswith(("HL_PRIVATE_KEY=", "HL_ACCOUNT_ADDRESS=")):
                    lines.append(line)
    lines.append(f"HL_PRIVATE_KEY={private_key}")
    lines.append(f"HL_ACCOUNT_ADDRESS={account_address}")
    with open(SECRETS_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def handle_hl_status():
    """GET /api/hl/status"""
    pk, addr = _get_hl_credentials()
    if not pk or not addr:
        return 200, {"status": "disconnected", "label": "未連接", "balance": None}
    try:
        client = _get_hl_client()
        bal = client.get_usdt_balance()
        return 200, {
            "status": "connected", "label": "已連接",
            "balance": round(bal, 2),
        }
    except Exception as e:
        return 200, {"status": "error", "label": "驗證失敗", "balance": None, "error": str(e)[:80]}


def handle_hl_connect(body):
    """POST /api/hl/connect"""
    private_key = body.get("private_key", "").strip()
    account_address = body.get("account_address", "").strip()
    if not private_key or not account_address:
        return 400, {"error": "Missing private_key or account_address"}

    _save_hl_credentials(private_key, account_address)

    try:
        client = _get_hl_client()
        bal = client.get_usdt_balance()
        addr_preview = f"{account_address[:6]}...{account_address[-4:]}"
        return 200, {"ok": True, "status": "connected", "addr_preview": addr_preview, "balance": round(bal, 2)}
    except Exception as e:
        return 200, {"ok": False, "status": "error", "error": str(e)[:120]}


def handle_hl_disconnect():
    """POST /api/hl/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.strip().startswith(("HL_PRIVATE_KEY=", "HL_ACCOUNT_ADDRESS="))]
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
        elif path == "/api/state":
            self._json_response(200, handle_api_state())
        elif path == "/api/config":
            self._json_response(200, handle_api_config())
        elif path == "/api/scan-log":
            self._json_response(200, handle_api_scan_log())
        elif path == "/api/health":
            self._json_response(200, handle_api_health())
        elif path == "/api/debug":
            self._json_response(200, collect_debug())
        elif path == "/api/suggest_mode":
            self._json_response(200, handle_suggest_mode())
        elif path == "/api/binance/status":
            code, data = handle_binance_status()
            self._json_response(code, data)
        elif path == "/api/aster/status":
            code, data = handle_aster_status()
            self._json_response(code, data)
        elif path == "/api/hl/status":
            code, data = handle_hl_status()
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
        elif path in ("/share", "/share/windows"):
            fname = "share-windows.html" if path == "/share/windows" else "share.html"
            share_path = os.path.join(HOME, "canvas", fname)
            if os.path.exists(share_path):
                with open(share_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
        elif path == "/api/share/package":
            try:
                zip_bytes = generate_share_package()
                date_str = datetime.now().strftime("%Y%m%d")
                filename = f"axc-setup-{date_str}.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"'
                )
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.end_headers()
                self.wfile.write(zip_bytes)
            except Exception as e:
                err = f"Error: {e}".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
        elif path.startswith("/svg/"):
            _mime = {".svg": "image/svg+xml", ".png": "image/png",
                     ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
            ext = os.path.splitext(path)[1].lower()
            ctype = _mime.get(ext)
            img_path = os.path.join(HOME, "canvas", path.lstrip("/"))
            if ctype and os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
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
        elif self.path == "/api/config/mode":
            code, data = handle_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/config/trading":
            code, data = handle_set_trading(body)
            self._json_response(code, data)
        elif self.path == "/api/binance/connect":
            code, data = handle_binance_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/binance/disconnect":
            code, data = handle_binance_disconnect()
            self._json_response(code, data)
        elif self.path == "/api/aster/connect":
            code, data = handle_aster_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/aster/disconnect":
            code, data = handle_aster_disconnect()
            self._json_response(code, data)
        elif self.path == "/api/hl/connect":
            code, data = handle_hl_connect(body)
            self._json_response(code, data)
        elif self.path == "/api/hl/disconnect":
            code, data = handle_hl_disconnect()
            self._json_response(code, data)
        elif self.path == "/api/close-position":
            code, data = handle_close_position(body)
            self._json_response(code, data)
        elif self.path == "/api/modify-sltp":
            code, data = handle_modify_sltp(body)
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
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    bind = "0.0.0.0"
    server = ThreadedHTTPServer((bind, port), Handler)
    print(f"AXC Dashboard: http://localhost:{port}")
    print(f"⚠️  局域網可連：http://<你的IP>:{port} — 同一 WiFi 嘅設備都可以存取（包括平倉等操作）")
    print(f"   如果喺公共網絡，建議改回 127.0.0.1（編輯 dashboard.py 最尾 bind 變數）")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
