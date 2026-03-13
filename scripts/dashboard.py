#!/usr/bin/env python3
"""
dashboard.py — OpenClaw ICU Dashboard Backend
Serves canvas/index.html + /api/data JSON endpoint.

Usage:
  python3 dashboard.py          # start on :5555
  python3 dashboard.py --port 8080
"""

import copy
import fcntl
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import zipfile
import math
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
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

PORT = int(os.environ.get("DASHBOARD_PORT", 5566))
HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
SCRIPTS_DIR = os.path.join(HOME, "scripts")
if HOME not in sys.path:
    sys.path.insert(0, HOME)
HKT = timezone(timedelta(hours=8))
PNL_HISTORY_PATH = os.path.join(HOME, "shared", "pnl_history.json")
BALANCE_BASELINE_PATH = os.path.join(HOME, "shared", "balance_baseline.json")
CANVAS_HTML = os.path.join(HOME, "canvas", "index.html")
_profiles_cache = {"ts": 0, "data": {}, "active": ""}

# ── Service Management ────────────────────────────────────────────────
_PLIST_DIR = os.path.expanduser("~/Library/LaunchAgents")
_CORE_SERVICES = [
    ("ai.openclaw.scanner",      "Scanner"),
    ("ai.openclaw.tradercycle",   "Trader"),
    ("ai.openclaw.telegram",      "Telegram"),
    ("ai.openclaw.dashboard",     "Dashboard"),
    ("ai.openclaw.heartbeat",     "Heartbeat"),
    ("ai.openclaw.lightscan",     "LightScan"),
    ("ai.openclaw.newsbot",       "NewsBot"),
    ("ai.openclaw.report",        "Report"),
]

# ── AI Chat (Dashboard) ──────────────────────────────────────────────
PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
_CHAT_MODEL_CHAIN_FAST = ["claude-haiku-4-5-20251001", "gpt-5-mini"]
_CHAT_MODEL_CHAIN_DEEP = ["claude-sonnet-4-6", "gpt-5-mini"]
_CHAT_ANALYSIS_KW = {"分析", "點解", "策略", "比較", "評估", "建議"}
_CHAT_SONNET_DAILY_CAP = 15
_SONNET_USAGE_PATH = os.path.join(HOME, "shared", "sonnet_usage.json")
_chat_history = []  # list of {role, content, ts}
_chat_lock = threading.Lock()
_CHAT_MAX_PAIRS = 5
_CHAT_EXPIRY_SEC = 600  # 10 min

# Whitelist: profile-aware params with Chinese labels
# Keys starting with _ are resolved from active profile (config/profiles/)
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
        for rf in ["CLAUDE.md", "README.md", "requirements.txt"]:
            rpath = os.path.join(ROOT, rf)
            if os.path.exists(rpath):
                zf.write(rpath, rf)

        # 動態生成 .env.example（從現有 .env 取 key 名，清空值）
        env_example = [
            "# AXC Trading System .env.example",
            "# 複製為 secrets/.env 並填入你的 API Key",
            "# cp secrets/.env.example secrets/.env",
            "",
            "# ── AI 推理（選填，核心交易唔需要）────────────────",
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


CONNECT_TIMEOUT_SEC = 15


def _run_with_timeout(fn, timeout=CONNECT_TIMEOUT_SEC):
    """Run fn() in a daemon thread, raise TimeoutError if exceeds timeout."""
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"連接超時（>{timeout}s），請檢查網絡或交易所狀態")
    if error[0]:
        raise error[0]
    return result[0]


_aster_client_singleton = None
_aster_client_ts = 0
_EXCHANGE_RESYNC_INTERVAL = 300  # re-sync time offset every 5 min


def _get_aster_client():
    """Singleton AsterClient with periodic time re-sync."""
    global _aster_client_singleton, _aster_client_ts
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.aster_client import AsterClient

    now = time.time()
    if _aster_client_singleton is None:
        _aster_client_singleton = AsterClient()
        _aster_client_ts = now
    elif now - _aster_client_ts >= _EXCHANGE_RESYNC_INTERVAL:
        # Periodic re-sync to fix drifted time_offset
        try:
            _aster_client_singleton._sync_time()
        except Exception:
            pass  # keep using old offset — next cycle will retry
        _aster_client_ts = now
    return _aster_client_singleton


def _reset_aster_client():
    """Force rebuild on auth/connection failure."""
    global _aster_client_singleton, _aster_client_ts
    _aster_client_singleton = None
    _aster_client_ts = 0


_hl_client_singleton = None


def _get_hl_client():
    """Singleton HyperLiquidClient."""
    global _hl_client_singleton
    if _hl_client_singleton is None:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from trader_cycle.exchange.hyperliquid_client import HyperLiquidClient
        _hl_client_singleton = HyperLiquidClient()
    return _hl_client_singleton


_binance_client_singleton = None
_binance_client_ts = 0


def _get_binance_client():
    """Singleton BinanceClient with periodic time re-sync."""
    global _binance_client_singleton, _binance_client_ts
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.binance_client import BinanceClient

    now = time.time()
    if _binance_client_singleton is None:
        _binance_client_singleton = BinanceClient()
        _binance_client_ts = now
    elif now - _binance_client_ts >= _EXCHANGE_RESYNC_INTERVAL:
        try:
            _binance_client_singleton._sync_time()
        except Exception:
            pass
        _binance_client_ts = now
    return _binance_client_singleton


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
        except Exception as e:
            logging.warning("get_open_orders failed (%s): %s", name, e)
        return {
            "balance": client.get_usdt_balance(),
            "positions": _normalize_positions(client.get_positions(), orders, name),
            "orders": orders,
        }
    except Exception as e:
        logging.warning("exchange query %s error: %s", name, e)
        # Reset singleton on auth/connection failure so next cycle rebuilds
        if name == "aster":
            _reset_aster_client()
        elif name == "binance":
            global _binance_client_singleton, _binance_client_ts
            _binance_client_singleton = None
            _binance_client_ts = 0
        elif name == "hyperliquid":
            global _hl_client_singleton
            _hl_client_singleton = None
        return None


_exchange_cache = {"data": {}, "ts": 0}
_EXCHANGE_CACHE_TTL = 10  # 10s — positions update every 10s, not every 4s


def get_all_exchange_data():
    """Query all connected exchanges in parallel → per-exchange balance + positions.
    10s cache to avoid 429 rate limit (~18 calls/min instead of ~60)."""
    global _exchange_cache
    now = time.time()
    if _exchange_cache["data"] and now - _exchange_cache["ts"] < _EXCHANGE_CACHE_TTL:
        return _exchange_cache["data"]

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

    if result:
        _exchange_cache["data"] = result
        _exchange_cache["ts"] = now
    elif _exchange_cache["data"]:
        return _exchange_cache["data"]  # keep stale data on total failure
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
            except Exception as e:
                logging.warning("get_open_orders failed (%s): %s", symbol, e)
                # API failed → fallback to TRADE_STATE.md cached values
                ts = parse_md(os.path.join(HOME, "shared/TRADE_STATE.md"))
                try:
                    sl_price = float(ts.get("SL_PRICE", 0))
                except (ValueError, TypeError):
                    sl_price = 0
                try:
                    tp_price = float(ts.get("TP_PRICE", 0))
                except (ValueError, TypeError):
                    tp_price = 0

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


_funding_cache = {"data": {}, "ts": 0}
_FUNDING_CACHE_TTL = 120  # 2 min — funding rates update every 8h

_news_cache = {"data": None, "ts": 0}
_NEWS_CACHE_TTL = 120  # 2 min — news updates every 15 min
NEWS_SENTIMENT_PATH = os.path.join(HOME, "shared", "news_sentiment.json")
NEWS_STALE_MINUTES = 30


def get_funding_rates():
    """Fetch current funding rates for watched symbols. Public API, 2-min cache."""
    now = time.time()
    if now - _funding_cache["ts"] < _FUNDING_CACHE_TTL:
        return _funding_cache["data"]
    try:
        client = _get_aster_client()
        # premiumIndex returns funding rate for all symbols
        raw = client._public_request("GET", "/fapi/v1/premiumIndex")
        rates = {}
        # Load watched symbols from params
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "params_fr", os.path.join(HOME, "config/params.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        watched = set(getattr(mod, "ASTER_SYMBOLS", []) + getattr(mod, "BINANCE_SYMBOLS", []))
        for item in raw:
            sym = item.get("symbol", "")
            if sym in watched:
                rate = float(item.get("lastFundingRate", 0))
                next_ts = int(item.get("nextFundingTime", 0))
                rates[sym] = {
                    "rate": round(rate * 100, 4),  # → percentage
                    "next_time": datetime.fromtimestamp(next_ts / 1000, tz=HKT).strftime("%H:%M") if next_ts else "",
                }
        _funding_cache["data"] = rates
        _funding_cache["ts"] = now
        return rates
    except Exception:
        logging.warning("Failed to fetch funding rates")
        return _funding_cache["data"]


def get_news_sentiment():
    """Read news sentiment from shared JSON. 2-min cache, staleness check."""
    now = time.time()
    if now - _news_cache["ts"] < _NEWS_CACHE_TTL:
        return _news_cache["data"]
    if not os.path.exists(NEWS_SENTIMENT_PATH):
        _news_cache["data"] = None
        _news_cache["ts"] = now
        return None
    try:
        with open(NEWS_SENTIMENT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        updated = raw.get("updated_at", "")
        stale = False
        if updated:
            try:
                ut = datetime.fromisoformat(updated)
                age_min = (datetime.now(timezone.utc) - ut).total_seconds() / 60
                stale = age_min > NEWS_STALE_MINUTES
            except (ValueError, TypeError):
                stale = True
        result = {
            "overall_sentiment": raw.get("overall_sentiment", "neutral"),
            "overall_impact": raw.get("overall_impact"),
            "confidence": raw.get("confidence", 0.0),
            "sentiment_by_symbol": raw.get("sentiment_by_symbol", {}),
            "key_narratives": raw.get("key_narratives", []),
            "risk_events": raw.get("risk_events", []),
            "summary": raw.get("summary", ""),
            "stale": stale or raw.get("stale", False),
            "updated_at": updated,
            "articles_analyzed": raw.get("articles_analyzed", 0),
        }
        _news_cache["data"] = result
        _news_cache["ts"] = now
        return result
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("Failed to read news sentiment: %s", e)
        return _news_cache["data"]


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
        services.append({
            "label": label,
            "name": name,
            "running": info.get("pid") is not None,
            "pid": info.get("pid"),
            "exit_code": info.get("exit"),
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
            params["_SL_ATR_MULT"] = profile.get("sl_atr_mult", 1.2)
            params["_TP_ATR_MULT"] = profile.get("tp_atr_mult", 2.0)
            params["_ALLOW_TREND"] = profile.get("allow_trend", True)
            params["_ALLOW_RANGE"] = profile.get("allow_range", True)
            params["_TRIGGER_PCT"] = profile.get("trigger_pct", 0.025)
            params["_TREND_MIN"] = profile.get("trend_min_change_pct")

        return params
    except Exception as e:
        return {"error": str(e)}


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

    # HMM regime data (written by trader_cycle to SCAN_CONFIG.md)
    scan_config = parse_md(os.path.join(HOME, "shared/SCAN_CONFIG.md"))
    hmm_regime = scan_config.get("HMM_REGIME", "")
    hmm_confidence = 0.0
    try:
        hmm_confidence = float(scan_config.get("HMM_CONFIDENCE", "0"))
    except (ValueError, TypeError):
        pass

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
        "hmm_regime": hmm_regime,
        "hmm_confidence": hmm_confidence,
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

# ── ATR fallback: compute from 4H klines when SCAN_CONFIG lacks data ──
_atr_fallback_cache = {}  # {symbol: {"atr": float, "ts": float}}
_ATR_FALLBACK_TTL = 300   # 5 min cache


def _compute_atr_from_klines(symbol):
    """Fetch 20x 4H klines and compute ATR(14) via Wilder's RMA."""
    base = _KLINE_API["aster"] if symbol in _ASTER_ONLY else _KLINE_API["binance"]
    url = f"{base}/fapi/v1/klines?symbol={symbol}&interval=4h&limit=20"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if len(data) < 15:
            return 0
        trs = []
        for i in range(1, len(data)):
            high, low = float(data[i][2]), float(data[i][3])
            prev_close = float(data[i - 1][4])
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        period = 14
        if len(trs) < period:
            return 0
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr
    except Exception as e:
        print(f"[ATR fallback] {symbol} fetch failed: {e}", file=sys.stderr)
        return 0


def _fetch_missing_atrs(symbols):
    """Concurrently compute ATR for symbols not in SCAN_CONFIG. Results cached 5 min."""
    now = time.time()
    to_fetch = [
        s for s in symbols
        if s not in _atr_fallback_cache
        or now - _atr_fallback_cache[s]["ts"] >= _ATR_FALLBACK_TTL
    ]
    if not to_fetch:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_compute_atr_from_klines, s): s for s in to_fetch}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                val = fut.result(timeout=10)
                _atr_fallback_cache[sym] = {"atr": val, "ts": time.time()}
            except Exception:
                _atr_fallback_cache[sym] = {"atr": 0, "ts": time.time()}


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
        try:
            from config.profiles.loader import load_profile as _lp
            profiles = {active_profile: _lp(active_profile)}
        except Exception:
            profiles = {}
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

    # Pre-compute which symbols lack ATR in SCAN_CONFIG → fetch once
    _atr_from_config = {}
    missing_atr = []
    for s in all_symbols:
        short = s.replace("USDT", "")
        val = float(scan_config.get(f"{short}_ATR", 0))
        _atr_from_config[s] = val
        if val <= 0:
            missing_atr.append(s)
    if missing_atr:
        _fetch_missing_atrs(missing_atr)

    plans = []
    for sym in all_symbols:
        short = sym.replace("USDT", "")
        data = cache.get(sym, {})
        price = float(data.get("price", 0))
        if price <= 0:
            continue

        # prices_cache.json stores change as percentage (e.g. 4.3 = 4.3%)
        change = abs(float(data.get("change", 0)))
        atr = _atr_from_config.get(sym, 0)
        if atr <= 0:
            fb = _atr_fallback_cache.get(sym)
            if fb:
                atr = fb["atr"]
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

        high_24h = float(data.get("high", 0))
        low_24h = float(data.get("low", 0))
        volume_24h = float(data.get("volume", 0))
        volume_ratio = float(scan_config.get(f"{short}_volume_ratio", 0))

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
            "high_24h": high_24h,
            "low_24h": low_24h,
            "volume_24h": volume_24h,
            "volume_ratio": volume_ratio,
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


_collect_cache = {"data": None, "ts": 0}
_COLLECT_CACHE_TTL = 4  # seconds — frontend polls every 5s

def _extract_hl_order_info(result: dict) -> dict:
    """Parse HyperLiquid SDK nested order response into flat dict.

    HL SDK returns: {status:"ok", response:{type:"order",
      data:{statuses:[{resting:{oid:N}} | {filled:{totalSz,avgPx,oid}}]}}}
    Returns: {orderId, avgPrice, executedQty, filled} or empty dict.
    """
    try:
        resp = result.get("response", {})
        if isinstance(resp, str):
            return {}
        data = resp.get("data", {})
        statuses = data.get("statuses", [])
        if not statuses:
            return {}
        s = statuses[0]
        if "filled" in s:
            f = s["filled"]
            return {
                "orderId": str(f.get("oid", "")),
                "avgPrice": float(f.get("avgPx", 0)),
                "executedQty": float(f.get("totalSz", 0)),
                "filled": True,
            }
        if "resting" in s:
            r = s["resting"]
            return {
                "orderId": str(r.get("oid", "")),
                "avgPrice": 0,
                "executedQty": 0,
                "filled": False,
            }
    except (KeyError, TypeError, ValueError, IndexError) as e:
        logging.warning("Failed to parse HL order response: %s → %s", result, e)
    return {}


# ── Pending SL/TP for limit orders (auto-set after fill) ────────────
_pending_sltp = {}  # {orderId: {symbol, platform, sl_price, tp_price, exit_side, qty, created_at}}
_PENDING_SLTP_FILE = os.path.join(HOME, "shared", "pending_sltp.json")
_pending_sltp_lock = threading.Lock()
_PENDING_SLTP_EXPIRY_SEC = 86400  # 24h


def _save_pending_sltp():
    """Atomic write pending SLTP state to JSON for crash recovery."""
    with _pending_sltp_lock:
        data = _pending_sltp.copy()
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.join(HOME, "shared"), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, _PENDING_SLTP_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logging.error("Failed to save pending SLTP: %s", e)


def _load_pending_sltp():
    """Load pending SLTP from JSON on startup, prune entries older than 24h."""
    global _pending_sltp
    if not os.path.exists(_PENDING_SLTP_FILE):
        return
    try:
        with open(_PENDING_SLTP_FILE, "r") as f:
            data = json.load(f)
        now = time.time()
        pruned = {
            oid: entry for oid, entry in data.items()
            if now - entry.get("created_at", 0) < _PENDING_SLTP_EXPIRY_SEC
        }
        with _pending_sltp_lock:
            _pending_sltp = pruned
        if len(pruned) < len(data):
            logging.info("Pending SLTP: pruned %d expired entries, %d remaining", len(data) - len(pruned), len(pruned))
            _save_pending_sltp()
        if pruned:
            logging.info("Pending SLTP: loaded %d entries from disk", len(pruned))
    except Exception as e:
        logging.warning("Failed to load pending SLTP: %s", e)


def _check_pending_sltp(exchange_data):
    """Check if pending limit orders have filled; auto-set SL/TP if so.

    Uses orders already fetched by get_all_exchange_data() — zero extra API calls
    for the detection phase. Only creates new API calls when placing SL/TP.
    """
    with _pending_sltp_lock:
        pending_copy = dict(_pending_sltp)
    if not pending_copy:
        return

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    # Classify each pending entry: still_pending, to_process, or to_remove
    to_process = {}   # order_id → (entry, pos_qty)
    to_remove = []    # order_ids to discard (cancelled / no position)

    for order_id, entry in pending_copy.items():
        platform = entry["platform"]
        symbol = entry["symbol"]
        plat_data = exchange_data.get(platform)
        if plat_data is None:
            continue  # exchange not connected this cycle

        # Check if orderId still in open orders
        open_order_ids = set()
        for o in plat_data.get("orders", []):
            oid = str(o.get("orderId", o.get("oid", "")))
            if oid:
                open_order_ids.add(oid)

        if order_id in open_order_ids:
            continue  # still pending — wait

        # Order disappeared from open orders → filled or cancelled
        has_position = False
        for pos in plat_data.get("positions", []):
            if pos.get("pair", "").upper() == symbol.upper():
                has_position = True
                break

        if not has_position:
            logging.info(
                "Pending SLTP: orderId=%s no position found for %s %s — removing (likely cancelled)",
                order_id, platform, symbol,
            )
            to_remove.append(order_id)
        else:
            to_process[order_id] = entry

    # Pop entries BEFORE processing to prevent concurrent duplicate placement
    if to_process or to_remove:
        with _pending_sltp_lock:
            for oid in to_remove:
                _pending_sltp.pop(oid, None)
            for oid in to_process:
                _pending_sltp.pop(oid, None)
        _save_pending_sltp()

    # Now place SL/TP (no lock held — safe for slow API calls)
    re_queue = []  # entries to put back on failure
    for order_id, entry in to_process.items():
        platform = entry["platform"]
        symbol = entry["symbol"]
        exit_side = entry["exit_side"]
        sl_price = entry.get("sl_price", 0)
        tp_price = entry.get("tp_price", 0)
        # Use stored order qty (not total position size — avoids conflict with existing SL/TP on add-to-position)
        order_qty = entry.get("qty", 0)
        if order_qty <= 0:
            logging.warning("Pending SLTP: orderId=%s has no stored qty, skipping", order_id)
            continue

        logging.info(
            "Pending SLTP: orderId=%s filled! Setting SL/TP for %s %s (qty=%s)",
            order_id, platform, symbol, order_qty,
        )

        try:
            client = client_fns[platform]()
        except Exception as e:
            logging.error("Pending SLTP: cannot connect to %s: %s", platform, e)
            re_queue.append((order_id, entry))
            continue

        sl_ok = True

        # Place SL (important but not emergency-close worthy — position already existed)
        if sl_price > 0:
            try:
                client.create_stop_market(symbol, exit_side, order_qty, sl_price)
                logging.info("Pending SLTP: SL set %s %s @ %s qty=%s", platform, symbol, sl_price, order_qty)
            except Exception as e:
                sl_ok = False
                logging.error("Pending SLTP: SL failed %s %s @ %s → %s", platform, symbol, sl_price, e)

        # Place TP (best-effort)
        if tp_price > 0:
            try:
                client.create_take_profit_market(symbol, exit_side, order_qty, tp_price)
                logging.info("Pending SLTP: TP set %s %s @ %s qty=%s", platform, symbol, tp_price, order_qty)
            except Exception as e:
                logging.warning("Pending SLTP: TP failed %s %s @ %s → %s", platform, symbol, tp_price, e)

        if not sl_ok:
            # SL failed — re-queue for retry next cycle
            logging.warning("Pending SLTP: SL failed, will retry next cycle for %s %s", platform, symbol)
            re_queue.append((order_id, entry))

    # Re-queue failed entries
    if re_queue:
        with _pending_sltp_lock:
            for oid, entry in re_queue:
                _pending_sltp[oid] = entry
        _save_pending_sltp()

    resolved_count = len(to_remove) + len(to_process) - len(re_queue)
    if resolved_count > 0 or to_remove:
        logging.info("Pending SLTP: resolved %d, re-queued %d, remaining %d",
                     resolved_count, len(re_queue), len(_pending_sltp))


def _extract_open_orders(exchange_data):
    """Extract pending limit orders from exchange data for dashboard display.

    Filters out SL/TP trigger orders — only shows entry orders (LIMIT type).
    Annotates with pending SL/TP status from _pending_sltp.
    """
    result = []
    # Order types that are SL/TP triggers (not entry orders)
    _TRIGGER_TYPES = {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT",
                      "TRAILING_STOP_MARKET"}
    for platform, pdata in exchange_data.items():
        if not pdata:
            continue
        for o in pdata.get("orders", []):
            otype = o.get("type", "")
            # HL format: orderType field instead of type
            hl_type = o.get("orderType", "").lower()
            # Skip SL/TP triggers
            if otype in _TRIGGER_TYPES:
                continue
            if hl_type and ("stop" in hl_type or "take profit" in hl_type):
                continue

            # Extract fields (Binance/Aster format vs HL format)
            oid = str(o.get("orderId", o.get("oid", "")))
            symbol = o.get("symbol", "")
            if not symbol and o.get("coin"):
                symbol = o["coin"] + "USDT"
            side = o.get("side", "").upper()
            if not side:
                # HL: side from order
                side = o.get("side", "Buy").upper()
                if side == "B":
                    side = "BUY"
                elif side == "A":
                    side = "SELL"
            price = float(o.get("price", o.get("limitPx", 0)) or 0)
            qty = float(o.get("origQty", o.get("sz", 0)) or 0)
            filled = float(o.get("executedQty", 0) or 0)
            order_time = o.get("time", o.get("timestamp", 0))

            if not symbol or price <= 0:
                continue

            # Check if SL/TP is queued for this order
            sltp_queued = False
            queued_sl = 0
            queued_tp = 0
            with _pending_sltp_lock:
                pentry = _pending_sltp.get(oid)
                if pentry:
                    sltp_queued = True
                    queued_sl = pentry.get("sl_price", 0)
                    queued_tp = pentry.get("tp_price", 0)

            result.append({
                "orderId": oid,
                "symbol": symbol.upper(),
                "side": side,
                "price": price,
                "qty": qty,
                "filled": filled,
                "platform": platform,
                "time": order_time,
                "sltp_queued": sltp_queued,
                "queued_sl": queued_sl,
                "queued_tp": queued_tp,
            })
    return result


# ── Macro State (for position scoring) ─────────────────────────────
MACRO_STATE_PATH = os.path.join(HOME, "shared", "macro_state.json")
_macro_cache = {"data": {}, "ts": 0}
_MACRO_CACHE_TTL = 120  # 2 min — macro updates ~30 min


def _get_macro_state():
    """Read macro_state.json with 2-min cache."""
    now = time.time()
    if now - _macro_cache["ts"] < _MACRO_CACHE_TTL and _macro_cache["data"]:
        return _macro_cache["data"]
    if not os.path.exists(MACRO_STATE_PATH):
        return _macro_cache["data"]
    try:
        with open(MACRO_STATE_PATH, encoding="utf-8") as f:
            _macro_cache["data"] = json.load(f)
        _macro_cache["ts"] = now
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("Failed to read macro_state: %s", e)
    return _macro_cache["data"]


# ── Position Hold Score ────────────────────────────────────────────
# 5 dimensions, weighted average → 0-10 score. Pure formula, zero API calls.
# Weights: PnL 25%, Tech 25%, Risk 20%, Sentiment 15%, Macro 15%

_SCORE_W_PNL = 0.25
_SCORE_W_TECH = 0.25
_SCORE_W_RISK = 0.20
_SCORE_W_SENT = 0.15
_SCORE_W_MACRO = 0.15


def _score_position(pos, plan_entry, news, risk_status, funding_rates, macro):
    """即時評估持倉健康度（0-10）。
    純公式計算，零 API call。用 dashboard 已 cache 嘅數據。
    點解用 5 維度：涵蓋交易最關鍵嘅方面（盈虧、技術面、風控、情緒、宏觀），
    缺任何一個維度 fallback 到中性分 5，唔影響整體。
    """
    factors = []
    is_long = pos.get("direction") == "LONG"
    entry = float(pos.get("entry_price", 0))
    mark = float(pos.get("mark_price", 0))
    symbol = pos.get("pair", "")

    # ── 1. PnL 趨勢 (0-10) ──────────────────────────────────────
    upnl_pct = float(pos.get("unrealized_pct", 0))
    # Map [-5%, +5%] → [0, 10], clamp
    pnl_base = max(0, min(10, upnl_pct + 5))

    # Momentum bonus: 1H/4H change aligned with direction → +1 (max)
    momentum_bonus = 0
    changes = {}
    if plan_entry:
        changes = plan_entry.get("changes", {})
    ch_1h = changes.get("1h", 0)
    ch_4h = changes.get("4h", 0)
    if is_long:
        if ch_1h > 0:
            momentum_bonus += 0.5
        if ch_4h > 0:
            momentum_bonus += 0.5
    else:
        if ch_1h < 0:
            momentum_bonus += 0.5
        if ch_4h < 0:
            momentum_bonus += 0.5

    pnl_score = max(0, min(10, pnl_base + momentum_bonus))
    detail_pnl = f"{'+' if upnl_pct >= 0 else ''}{upnl_pct:.1f}%"
    if momentum_bonus > 0:
        detail_pnl += " 動量↑"
    factors.append({"name": "PnL 趨勢", "score": round(pnl_score, 1), "detail": detail_pnl})

    # ── 2. 技術位置 (0-10) ───────────────────────────────────────
    tech_score = 5.0  # neutral fallback
    detail_tech = "無 S/R 數據"
    if plan_entry:
        support = float(plan_entry.get("support", 0))
        resistance = float(plan_entry.get("resistance", 0))
        if support > 0 and resistance > support and mark > 0:
            sr_range = resistance - support
            pos_in_range = (mark - support) / sr_range
            pos_in_range = max(0, min(1, pos_in_range))
            if is_long:
                # LONG: closer to resistance = closer to TP = higher score
                tech_score = pos_in_range * 10
                if pos_in_range > 0.7:
                    detail_tech = "近阻力 TP"
                elif pos_in_range < 0.3:
                    detail_tech = "近支撐 SL"
                else:
                    detail_tech = f"S/R 中段 ({pos_in_range:.0%})"
            else:
                # SHORT: closer to support = closer to TP = higher score
                tech_score = (1 - pos_in_range) * 10
                if pos_in_range < 0.3:
                    detail_tech = "近支撐 TP"
                elif pos_in_range > 0.7:
                    detail_tech = "近阻力 SL"
                else:
                    detail_tech = f"S/R 中段 ({1 - pos_in_range:.0%})"
    factors.append({"name": "技術位置", "score": round(tech_score, 1), "detail": detail_tech})

    # ── 3. 風險保護 (0-10) ───────────────────────────────────────
    risk_score = 0.0
    sl = float(pos.get("sl_price", 0))
    tp = float(pos.get("tp_price", 0))
    liq = float(pos.get("liq_price", 0))
    detail_parts = []

    if sl > 0:
        risk_score += 4.0
        detail_parts.append("SL✓")
    else:
        detail_parts.append("SL✗")
    if tp > 0:
        risk_score += 2.0
        detail_parts.append("TP✓")
    else:
        detail_parts.append("TP✗")

    # R:R ratio
    if sl > 0 and tp > 0 and entry > 0:
        if is_long:
            risk_dist = entry - sl
            reward_dist = tp - entry
        else:
            risk_dist = sl - entry
            reward_dist = entry - tp
        if risk_dist > 0:
            rr = reward_dist / risk_dist
            if rr >= 2:
                risk_score += 3.0
            elif rr >= 1.5:
                risk_score += 2.0
            elif rr >= 1:
                risk_score += 1.0
            detail_parts.append(f"R:R {rr:.1f}")

    # Liquidation distance
    if liq > 0 and mark > 0:
        liq_dist_pct = abs(mark - liq) / mark * 100
        if liq_dist_pct > 20:
            risk_score += 1.0
        detail_parts.append(f"強平{liq_dist_pct:.0f}%")

    risk_score = min(10, risk_score)
    factors.append({"name": "風險保護", "score": round(risk_score, 1), "detail": " ".join(detail_parts)})

    # ── 4. 市場情緒 (0-10) ───────────────────────────────────────
    sent_score = 5.0  # neutral fallback
    detail_sent = "無數據"
    if news and not news.get("stale", True):
        # Per-symbol sentiment overrides overall
        short = symbol.replace("USDT", "")
        sym_sent = (news.get("sentiment_by_symbol") or {}).get(short)
        if sym_sent and isinstance(sym_sent, dict):
            sentiment = sym_sent.get("sentiment") or news.get("overall_sentiment", "neutral")
            confidence = float(sym_sent.get("confidence") or news.get("confidence") or 0)
        else:
            sentiment = news.get("overall_sentiment", "neutral")
            confidence = float(news.get("confidence") or 0)
        confidence = max(0, min(1, confidence))

        # Aligned = good
        bullish = sentiment in ("bullish", "positive")
        bearish = sentiment in ("bearish", "negative")
        aligned = (is_long and bullish) or (not is_long and bearish)
        opposed = (is_long and bearish) or (not is_long and bullish)

        if aligned:
            sent_score = 7 + confidence * 3
            detail_sent = f"情緒利好 ({confidence:.0%})"
        elif opposed:
            sent_score = max(0, 3 - confidence * 3)
            detail_sent = f"情緒逆向 ({confidence:.0%})"
        else:
            sent_score = 5.0
            detail_sent = "中性"
    elif news and news.get("stale"):
        detail_sent = "數據過時"

    factors.append({"name": "市場情緒", "score": round(sent_score, 1), "detail": detail_sent})

    # ── 5. 宏觀環境 (0-10) ───────────────────────────────────────
    macro_score = 5.0  # neutral fallback
    detail_macro_parts = []

    # VIX
    vix = 0
    try:
        vix = float(macro.get("^VIX_price", 0))
    except (ValueError, TypeError):
        pass
    if vix > 0:
        if vix < 20:
            macro_score = 8.0
            detail_macro_parts.append(f"VIX {vix:.0f} 低")
        elif vix <= 30:
            macro_score = 5.0
            detail_macro_parts.append(f"VIX {vix:.0f}")
        else:
            macro_score = 2.0
            detail_macro_parts.append(f"VIX {vix:.0f} 高")
    else:
        detail_macro_parts.append("VIX N/A")

    # Funding rate alignment
    fr = (funding_rates or {}).get(symbol, {})
    fr_rate = fr.get("rate", 0)  # percentage
    if fr_rate != 0:
        # LONG + negative funding = being paid = good
        # SHORT + positive funding = being paid = good
        funding_aligned = (is_long and fr_rate < 0) or (not is_long and fr_rate > 0)
        if funding_aligned:
            macro_score = min(10, macro_score + 1)
            detail_macro_parts.append("FR利好")
        elif abs(fr_rate) > 0.03:  # > 0.03% = significant opposing
            macro_score = max(0, macro_score - 0.5)
            detail_macro_parts.append("FR逆向")

    # HMM regime confidence
    if risk_status:
        hmm_conf = float(risk_status.get("hmm_confidence", 0))
        if hmm_conf > 0.7:
            macro_score = min(10, macro_score + 1)

    factors.append({"name": "宏觀環境", "score": round(macro_score, 1),
                    "detail": " ".join(detail_macro_parts) if detail_macro_parts else "N/A"})

    # ── Weighted average ─────────────────────────────────────────
    weighted = (
        pnl_score * _SCORE_W_PNL +
        tech_score * _SCORE_W_TECH +
        risk_score * _SCORE_W_RISK +
        sent_score * _SCORE_W_SENT +
        macro_score * _SCORE_W_MACRO
    )
    weighted = max(0, min(10, weighted))

    return {
        "score": round(weighted, 1),
        "factors": factors,
    }


def collect_data():
    global _collect_cache
    now = time.time()
    if _collect_cache["data"] and now - _collect_cache["ts"] < _COLLECT_CACHE_TTL:
        return copy.copy(_collect_cache["data"])

    if _is_demo_mode():
        return _get_demo_data()

    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    # All dynamic sources
    agents = get_agent_info()
    params = get_trading_params()
    trade = get_trade_state()

    # Multi-exchange breakdown — single pass, reuse for balance/positions
    exchange_data = get_all_exchange_data()

    # Check pending SL/TP for filled limit orders (zero extra API calls)
    if _pending_sltp:
        _check_pending_sltp(exchange_data)

    # Extract open orders (limit orders only, not SL/TP triggers) for dashboard display
    open_orders = _extract_open_orders(exchange_data)

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
    signal_heatmap = []  # removed — scan_log rotation causes incomplete data
    funding_rates = get_funding_rates()

    # Pre-compute for position scoring (reused in result dict)
    action_plan = get_action_plan(scan_config, trade)
    news_sentiment = get_news_sentiment()
    risk_status = get_risk_status(live_bal)

    # Score each open position (pure formula, zero API calls)
    if live_positions:
        ap_by_sym = {p["symbol"]: p for p in action_plan} if action_plan else {}
        macro = _get_macro_state()
        for pos in live_positions:
            plan_entry = ap_by_sym.get(pos.get("pair"))
            pos["hold_score"] = _score_position(
                pos, plan_entry, news_sentiment, risk_status, funding_rates, macro
            )

    result = {
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
        "open_orders": open_orders,
        "consecutive_losses": int(trade["consecutive_losses"]),
        "agents": agents,
        "params": params,
        "params_display": params_display,
        "scan_log": get_scan_log(),
        "file_tree": get_file_tree(),
        "prices": prices,
        "action_plan": action_plan,
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
        "risk_status": risk_status,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pct": unrealized_pct,
        "fee_breakdown": fee_breakdown,
        "cumulative_fees": baseline.get("cumulative_fees", {}),
        "active_profile": params.get("ACTIVE_PROFILE", "CONSERVATIVE"),
        "active_regime_preset": params.get("ACTIVE_REGIME_PRESET", "classic"),
        "regime_engine": params.get("REGIME_ENGINE", "votes_hmm"),
        "cp_enabled": params.get("CP_ENABLED", False),
        "activity_log": get_activity_log(50),
        "trade_stats": trade_stats,
        "drawdown": drawdown,
        "signal_heatmap": signal_heatmap,
        "funding_rates": funding_rates,
        "news_sentiment": news_sentiment,
        "demo_mode": False,
        "exchanges": exchange_data,
    }
    _collect_cache["data"] = result
    _collect_cache["ts"] = time.time()
    return result


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
        _collect_cache["ts"] = 0
        return 200, {"ok": True, "mode": mode, "message": f"已切換至 {mode} 模式"}
    except Exception as e:
        return 500, {"error": str(e)}


def handle_set_regime(body):
    """POST /api/set_regime — switch regime preset."""
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}
    preset = data.get("preset", "").lower()
    valid = ["classic", "classic_cp", "bocpd", "full"]
    if preset not in valid:
        return 400, {"error": f"Invalid preset. Use: {valid}"}
    params_path = os.path.join(HOME, "config/params.py")
    try:
        with open(params_path) as f:
            content = f.read()
        content = re.sub(
            r'ACTIVE_REGIME_PRESET\s*=\s*"[^"]*"',
            f'ACTIVE_REGIME_PRESET = "{preset}"',
            content,
        )
        with open(params_path, "w") as f:
            f.write(content)
        _collect_cache["ts"] = 0
        return 200, {"ok": True, "preset": preset, "message": f"Regime → {preset}"}
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


def handle_cancel_order(body):
    """POST /api/cancel-order — cancel a pending order + clean up queued SL/TP."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()
    order_id = str(data.get("orderId") or "")

    if not symbol or not platform or not order_id:
        return 400, {"error": "symbol, platform, orderId required"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    try:
        client = client_fns[platform]()
        client.cancel_order(symbol, order_id)
        logging.info("Dashboard cancel-order: %s %s orderId=%s", platform, symbol, order_id)

        # Clean up pending SL/TP if queued
        with _pending_sltp_lock:
            removed = _pending_sltp.pop(order_id, None)
        if removed:
            _save_pending_sltp()
            logging.info("Pending SLTP removed for cancelled order: %s", order_id)

        _action_cache["ts"] = 0  # invalidate cache
        _collect_cache["ts"] = 0
        return 200, {"ok": True}

    except Exception as e:
        logging.error("Dashboard cancel-order failed: %s %s %s → %s", platform, symbol, order_id, e)
        return 500, {"error": str(e)}


def handle_place_order(body):
    """POST /api/place-order — open a new position from dashboard trade modal.
    Execution sequence:
      ① set_margin_mode ISOLATED
      ② set_leverage
      ③ market/limit entry
      ④ SL (critical — failure triggers emergency close; skipped for pending limit)
      ⑤ TP (best-effort; skipped for pending limit)
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return 400, {"error": "Invalid JSON"}

    symbol = (data.get("symbol") or "").upper().strip()
    platform = (data.get("platform") or "").lower().strip()
    side = (data.get("side") or "").upper().strip()       # BUY or SELL
    order_type = (data.get("order_type") or "MARKET").upper().strip()
    sl_price = data.get("sl_price")
    tp_price = data.get("tp_price")

    try:
        qty = float(data.get("qty", 0))
        leverage = int(data.get("leverage", 5))
        limit_price = float(data.get("limit_price") or 0)
    except (ValueError, TypeError):
        return 400, {"error": "qty/leverage/limit_price 必須為數字"}

    # Validation
    if not symbol or not symbol.endswith("USDT"):
        return 400, {"error": f"Invalid symbol: {symbol}"}
    if platform not in ("aster", "binance", "hyperliquid"):
        return 400, {"error": f"Invalid platform: {platform}"}
    if side not in ("BUY", "SELL"):
        return 400, {"error": f"Invalid side: {side}"}
    if qty <= 0:
        return 400, {"error": "數量必須大於 0"}
    if leverage < 1 or leverage > 125:
        return 400, {"error": f"Invalid leverage: {leverage}"}
    if order_type not in ("MARKET", "LIMIT"):
        return 400, {"error": f"Invalid order_type: {order_type}"}
    if order_type == "LIMIT" and limit_price <= 0:
        return 400, {"error": "限價單需要有效價格"}

    exit_side = "SELL" if side == "BUY" else "BUY"
    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }

    try:
        client = client_fns[platform]()
    except Exception as e:
        return 500, {"error": f"交易所未連接: {e}"}

    # Pre-validate qty against exchange precision rules (A1: use client methods)
    try:
        precision = client.validate_symbol_precision(symbol)
        step = precision.get("qty_precision", 0.001)
        min_qty_ex = precision.get("min_qty", 0.001)
        rounded_qty = client._round_to_precision(qty, step)

        if rounded_qty <= 0:
            return 400, {
                "error": f"數量太小：{qty:.8f} 經精度調整後為 0。"
                         f"最小下單量 {min_qty_ex}，請增加 USDT 金額"
            }
        if rounded_qty < min_qty_ex:
            return 400, {
                "error": f"數量 {rounded_qty} 低於最小下單量 {min_qty_ex}，"
                         f"請增加 USDT 金額"
            }
    except Exception as prec_err:
        # A2: only skip for network/timeout — symbol-not-found should fail early
        if "not found" in str(prec_err).lower():
            return 400, {"error": f"交易所不支援 {symbol}: {prec_err}"}
        logging.warning("Pre-validation skipped (non-fatal): %s", prec_err)

    t_start = time.time()

    try:
        # ① Margin mode
        try:
            client.set_margin_mode(symbol, "ISOLATED")
        except Exception:
            pass  # may already be set

        # ② Leverage
        client.set_leverage(symbol, leverage)

        # ③ Entry (market or limit)
        t_entry = time.time()
        if order_type == "LIMIT":
            entry_result = client.create_limit_order(symbol, side, qty, limit_price)
        else:
            entry_result = client.create_market_order(symbol, side, qty)
        t_fill = time.time()

        is_limit = order_type == "LIMIT"

        # HL SDK returns nested structure: {status:"ok", response:{type:"order",
        # data:{statuses:[{resting:{oid:N}} or {filled:{totalSz,avgPx,oid}}]}}}
        # Binance/Aster return flat: {orderId, avgPrice, executedQty, status}
        hl_info = _extract_hl_order_info(entry_result) if platform == "hyperliquid" else {}
        fill_qty = float(hl_info.get("executedQty") or entry_result.get("executedQty", 0)) or qty
        fill_price = float(hl_info.get("avgPrice") or entry_result.get("avgPrice", 0))
        raw_order_id = str(hl_info.get("orderId") or entry_result.get("orderId", ""))

        # Limit order may fill immediately if price matches market
        actually_pending = is_limit and fill_price == 0 and not hl_info.get("filled")
        resp = {
            "ok": True,
            "pending": actually_pending,
            "entry": {
                "orderId": raw_order_id,
                "avgPrice": fill_price if fill_price > 0 else limit_price,
                "executedQty": fill_qty,
            },
        }
        logging.info(
            "Dashboard place-order: %s %s %s %s qty=%s lev=%sx%s → %s",
            platform, symbol, side, order_type, qty, leverage,
            f" @{limit_price}" if is_limit else "", entry_result
        )

        # ④a Queue SL/TP for pending limit orders — auto-set after fill
        if actually_pending and (sl_price or tp_price):
            if raw_order_id:
                pending_entry = {
                    "symbol": symbol,
                    "platform": platform,
                    "sl_price": float(sl_price) if sl_price else 0,
                    "tp_price": float(tp_price) if tp_price else 0,
                    "exit_side": exit_side,
                    "qty": float(qty),  # store order qty for SL/TP placement
                    "created_at": time.time(),
                }
                with _pending_sltp_lock:
                    _pending_sltp[raw_order_id] = pending_entry
                _save_pending_sltp()
                resp["sltp_queued"] = True
                logging.info(
                    "Pending SLTP queued: %s %s orderId=%s sl=%s tp=%s qty=%s",
                    platform, symbol, raw_order_id,
                    sl_price or "none", tp_price or "none", qty,
                )
            else:
                logging.warning(
                    "Pending SLTP: cannot queue — empty orderId from %s %s response: %s",
                    platform, symbol, entry_result,
                )

        # ④b SL (critical) — skip for pending limit orders (not yet filled)
        if not actually_pending and sl_price and float(sl_price) > 0:
            try:
                sl_result = client.create_stop_market(
                    symbol, exit_side, fill_qty, float(sl_price)
                )
                resp["sl"] = {"orderId": str(sl_result.get("orderId", ""))}
            except Exception as sl_err:
                logging.error("Dashboard SL failed, emergency close: %s %s → %s", platform, symbol, sl_err)
                try:
                    client.close_position_market(symbol)
                    return 500, {"error": f"SL 落單失敗，已緊急平倉: {sl_err}"}
                except Exception as close_err:
                    logging.error("Emergency close also failed: %s", close_err)
                    return 500, {"error": f"SL 落單失敗，緊急平倉也失敗！倉位仍開放，請手動處理: {sl_err}"}

        # ⑤ TP (best-effort) — skip for pending limit orders
        if not actually_pending and tp_price and float(tp_price) > 0:
            try:
                tp_result = client.create_take_profit_market(
                    symbol, exit_side, fill_qty, float(tp_price)
                )
                resp["tp"] = {"orderId": str(tp_result.get("orderId", ""))}
            except Exception as tp_err:
                logging.warning("Dashboard TP failed (SL active): %s %s → %s", platform, symbol, tp_err)
                resp["warnings"] = [f"TP 設置失敗 (SL 保護中): {tp_err}"]

        # Timing: total + entry fill
        t_end = time.time()
        resp["timing"] = {
            "total_ms": round((t_end - t_start) * 1000),
            "fill_ms": round((t_fill - t_entry) * 1000),
        }

        # Invalidate caches so next fetchData() shows updated positions + orders
        _action_cache["ts"] = 0
        _collect_cache["ts"] = 0

        return 200, resp

    except Exception as e:
        err_msg = str(e).lower()
        if "insufficient" in err_msg or "balance" in err_msg:
            return 400, {"error": f"餘額不足: {e}"}
        logging.error("Dashboard place-order failed: %s %s → %s", platform, symbol, e)
        return 500, {"error": str(e)}


def handle_exchange_balance():
    """GET /api/exchange/balance — balances for all connected exchanges (parallel)."""
    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }

    def _query_balance(name, cfn):
        client = cfn()
        return name, client.get_usdt_balance()

    result = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_query_balance, name, cfn): name
            for name, cfn in client_fns.items()
        }
        for fut in as_completed(futures):
            try:
                name, bal = fut.result(timeout=CONNECT_TIMEOUT_SEC)
                result[name] = {"balance": bal}
            except Exception:
                pass  # exchange not connected
    return result


def handle_symbol_info(qs):
    """GET /api/exchange/symbol-info?symbol=BTCUSDT&platform=aster
    Returns precision rules + trading constraints for the trade modal UI."""
    symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
    platform = (qs.get("platform", [""])[0] or "").lower().strip()

    if not symbol or not platform:
        return 400, {"error": "symbol and platform required"}

    client_fns = {
        "aster": _get_aster_client,
        "binance": _get_binance_client,
        "hyperliquid": _get_hl_client,
    }
    if platform not in client_fns:
        return 400, {"error": f"Unknown platform: {platform}"}

    try:
        client = client_fns[platform]()
        precision = client.validate_symbol_precision(symbol)
        step = precision.get("qty_precision", 0.001)
        min_qty = precision.get("min_qty", 0.001)
        min_notional = precision.get("min_notional", 5.0)
        tick_size = precision.get("price_precision", 0.01)

        return 200, {
            "symbol": symbol,
            "platform": platform,
            "step_size": step,
            "min_qty": min_qty,
            "min_notional": min_notional,
            "tick_size": tick_size,
            "order_types": ["MARKET"],
        }
    except Exception as e:
        return 500, {"error": str(e)}


_orderbook_cache: Dict[str, Any] = {}  # {symbol: {"data": ..., "ts": float}}
_ORDERBOOK_CACHE_TTL = 10  # seconds


def handle_orderbook(qs) -> tuple:
    """GET /api/orderbook?symbol=BTCUSDT — Order book depth with wall detection. 10s cache."""
    symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
    if not symbol:
        return 400, {"error": "symbol required"}

    now = time.time()
    cached = _orderbook_cache.get(symbol)
    if cached and now - cached["ts"] < _ORDERBOOK_CACHE_TTL:
        return 200, cached["data"]

    try:
        client = _get_aster_client()
        result = client.get_order_book(symbol, limit=20)
        _orderbook_cache[symbol] = {"data": result, "ts": now}
        return 200, result
    except Exception as e:
        logger.warning("Order book fetch failed for %s: %s", symbol, e)
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
    """Write or update Aster keys in secrets/.env + os.environ."""
    os.environ["ASTER_API_KEY"] = api_key
    os.environ["ASTER_API_SECRET"] = api_secret
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
        def verify():
            if SCRIPTS_DIR not in sys.path:
                sys.path.insert(0, SCRIPTS_DIR)
            from trader_cycle.exchange.aster_client import AsterClient
            client = AsterClient()
            return client.get_usdt_balance()
        bal = _run_with_timeout(verify)
        return 200, {"ok": True, "status": "connected", "key_preview": f"{api_key[:4]}...{api_key[-4:]}", "balance": round(bal, 2)}
    except TimeoutError as e:
        return 504, {"ok": False, "error": str(e)}
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
        "hold_score": {
            "score": 7.2,
            "factors": [
                {"name": "PnL 趨勢", "score": 6.0, "detail": "+0.98%"},
                {"name": "技術位置", "score": 7.7, "detail": "S/R 中段 (54%)"},
                {"name": "風險保護", "score": 9.0, "detail": "SL✓ TP✓ R:R 1.7"},
                {"name": "市場情緒", "score": 5.0, "detail": "中性"},
                {"name": "宏觀環境", "score": 5.5, "detail": "VIX 23"},
            ],
        },
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
    """Write or update Binance keys in secrets/.env + os.environ."""
    os.environ["BINANCE_API_KEY"] = api_key
    os.environ["BINANCE_API_SECRET"] = api_secret
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
        def verify():
            from binance.spot import Spot
            client = Spot(api_key=api_key, api_secret=api_secret)
            return client.account()
        account = _run_with_timeout(verify)
    except TimeoutError as e:
        return 504, {"ok": False, "error": str(e)}
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
    """Write or update HL keys in secrets/.env + os.environ."""
    os.environ["HL_PRIVATE_KEY"] = private_key
    os.environ["HL_ACCOUNT_ADDRESS"] = account_address
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
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except Exception:
        return 400, {"ok": False, "error": "Invalid JSON"}
    private_key = (data.get("private_key") or "").strip()
    account_address = (data.get("account_address") or "").strip()
    if not private_key or not account_address:
        return 400, {"ok": False, "error": "Missing private_key or account_address"}

    _save_hl_credentials(private_key, account_address)

    try:
        def verify():
            client = _get_hl_client()
            return client.get_usdt_balance()
        bal = _run_with_timeout(verify)
        addr_preview = f"{account_address[:6]}...{account_address[-4:]}"
        return 200, {"ok": True, "status": "connected", "addr_preview": addr_preview, "balance": round(bal, 2)}
    except TimeoutError as e:
        return 504, {"ok": False, "error": str(e)}
    except Exception as e:
        return 401, {"ok": False, "status": "error", "error": str(e)[:120]}


def handle_hl_disconnect():
    """POST /api/hl/disconnect"""
    if os.path.exists(SECRETS_ENV_PATH):
        with open(SECRETS_ENV_PATH) as f:
            lines = [l for l in f.read().splitlines()
                     if not l.strip().startswith(("HL_PRIVATE_KEY=", "HL_ACCOUNT_ADDRESS="))]
        with open(SECRETS_ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    return 200, {"ok": True, "status": "disconnected"}


def _safe_docs_path(rel_path):
    """Resolve path and ensure it stays within HOME/docs/. Returns None if traversal detected."""
    docs_root = os.path.abspath(os.path.join(HOME, "docs"))
    resolved = os.path.abspath(os.path.join(HOME, rel_path))
    if not resolved.startswith(docs_root + os.sep) and resolved != docs_root:
        return None
    return resolved


def handle_file_read(rel_path):
    """GET /api/file?path=docs/..."""
    fp = _safe_docs_path(rel_path)
    if fp is None:
        return 403, "Forbidden"
    if not os.path.exists(fp):
        return 404, "Not found"
    with open(fp) as f:
        return 200, f.read()


def handle_open_folder(rel_path):
    """GET /api/open_folder?path=docs/..."""
    fp = _safe_docs_path(rel_path)
    if fp is None:
        return 403, {"error": "Forbidden"}
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


# ── Backtest infrastructure ─────────────────────────────────
BT_DATA_DIR = os.path.join(HOME, "backtest", "data")
_bt_pool: ProcessPoolExecutor | None = None  # lazy init to avoid child re-spawn
_bt_lock = threading.Lock()
_bt_jobs: dict = {}   # job_id → {"status", "result", "error", "symbol", "days"}
_BT_MAX_JOBS = 10     # evict oldest completed jobs beyond this


def _get_bt_pool() -> ProcessPoolExecutor:
    """Lazy-init ProcessPool to prevent child process re-creating it on import."""
    global _bt_pool
    if _bt_pool is None:
        _bt_pool = ProcessPoolExecutor(max_workers=2)
    return _bt_pool


def _evict_old_jobs():
    """Remove oldest completed/error jobs when over _BT_MAX_JOBS. Must hold _bt_lock."""
    finished = [(k, v) for k, v in _bt_jobs.items()
                if v["status"] in ("done", "error")]
    if len(finished) <= _BT_MAX_JOBS:
        return
    # job_id contains timestamp — sort by key (oldest first)
    finished.sort(key=lambda x: x[0])
    for k, _ in finished[:len(finished) - _BT_MAX_JOBS]:
        del _bt_jobs[k]


def _run_bt_worker(symbol: str, days: int, balance: float,
                   strategy_params: dict | None = None,
                   param_overrides: dict | None = None,
                   allowed_modes: list | None = None,
                   mode_confirmation: int | None = None,
                   platform: str = "binance") -> dict:
    """Module-level worker for ProcessPoolExecutor (must be picklable)."""
    import sys as _sys
    _home = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
    if _home not in _sys.path:
        _sys.path.insert(0, _home)
    _scripts = os.path.join(_home, "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)

    from backtest.fetch_historical import fetch_klines_range
    from backtest.engine import BacktestEngine, WARMUP_CANDLES
    from backtest.metrics_ext import extend_summary
    from datetime import datetime, timezone, timedelta

    def _calc_range(d, interval):
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)
        wh = WARMUP_CANDLES * (4 if interval == "4h" else 1)
        start_ms = int((now - timedelta(hours=d * 24 + wh)).timestamp() * 1000)
        return start_ms, end_ms

    s1, e1 = _calc_range(days, "1h")
    s4, e4 = _calc_range(days, "4h")
    df_1h = fetch_klines_range(symbol, "1h", s1, e1, platform)
    df_4h = fetch_klines_range(symbol, "4h", s4, e4, platform)

    # Monkey-patch strategy constants if overrides provided
    sp = strategy_params or {}
    _originals = {}
    _patched_modules = []
    if sp:
        import trader_cycle.strategies.range_strategy as _rs
        import trader_cycle.strategies.trend_strategy as _ts
        _STRATEGY_MAP = {
            "range_sl":       [(_rs, "RANGE_SL_ATR_MULT"), (_ts, None)],
            "range_rr":       [(_rs, "RANGE_MIN_RR"), (_ts, None)],
            "trend_sl":       [(_rs, None), (_ts, "TREND_SL_ATR_MULT")],
            "trend_rr":       [(_rs, None), (_ts, "TREND_MIN_RR")],
            "risk_pct":       [(_rs, "RANGE_RISK_PCT"), (_ts, "TREND_RISK_PCT")],
            "range_leverage": [(_rs, "RANGE_LEVERAGE"), (_ts, None)],
            "trend_leverage": [(_rs, None), (_ts, "TREND_LEVERAGE")],
        }
        for key, val in sp.items():
            targets = _STRATEGY_MAP.get(key, [])
            for mod, attr in targets:
                if attr and hasattr(mod, attr):
                    _originals[(mod, attr)] = getattr(mod, attr)
                    setattr(mod, attr, val)
                    _patched_modules.append((mod, attr))

    try:
        engine = BacktestEngine(
            symbol=symbol, df_1h=df_1h, df_4h=df_4h,
            initial_balance=balance, quiet=True,
            param_overrides=param_overrides or {},
            allowed_modes=allowed_modes,
            mode_confirmation=mode_confirmation,
        )
        result = engine.run()
        result = extend_summary(result)
    finally:
        # Restore monkey-patched constants
        for (mod, attr), orig_val in _originals.items():
            setattr(mod, attr, orig_val)

    # Serialize trades
    result["trades"] = [t.to_dict() for t in result["trades"]]
    return result


def _compute_stats_from_trades(trades: list, balance: float = 10000) -> dict:
    """Compute basic stats from trade dicts (for legacy JSONL without meta)."""
    if not trades:
        return {}
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    total_pnl = sum(t.get("pnl") or 0 for t in trades)
    n = len(trades)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t.get("pnl") or 0
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return {
        "return_pct": round(total_pnl / balance * 100, 2) if balance else 0,
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "total_trades": n,
        "max_drawdown_pct": round(max_dd / balance * 100, 2) if balance else 0,
        "expectancy": round(total_pnl / n, 2) if n else 0,
        "estimated": True,  # balance was assumed, not from original run
    }


def handle_bt_list():
    """Return metadata of existing backtest JSONL files.
    Fast path: if meta sidecar exists with stats, only count JSONL lines (no parse).
    Slow path (one-time): parse trades, compute stats, persist meta for next call."""
    results = []
    if not os.path.isdir(BT_DATA_DIR):
        return results
    for fname in sorted(os.listdir(BT_DATA_DIR)):
        if not fname.endswith("_trades.jsonl"):
            continue
        # Formats: bt_BTCUSDT_60d_trades.jsonl or bt_BTCUSDT_60d_v2_trades.jsonl
        stem = fname.replace("bt_", "", 1).replace("_trades.jsonl", "")
        stem_clean = re.sub(r'_v\d+$', '', stem)
        parts = stem_clean.rsplit("_", 1)
        if len(parts) != 2:
            continue
        symbol, days_str = parts
        try:
            days = int(days_str.replace("d", ""))
        except ValueError:
            continue
        fpath = os.path.join(BT_DATA_DIR, fname)
        is_imported = bool(re.search(r'_v\d+_trades\.jsonl$', fname))

        # Check meta sidecar first
        meta_fname = fname.replace("_trades.jsonl", "_meta.json")
        meta_path = os.path.join(BT_DATA_DIR, meta_fname)
        has_meta = False
        entry = {
            "symbol": symbol, "days": days,
            "file": fname, "is_imported": is_imported,
        }

        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as mf:
                    meta = json.load(mf)
                entry["balance"] = meta.get("balance")
                entry["strategy_params"] = meta.get("strategy_params", {})
                entry["param_overrides"] = meta.get("param_overrides", {})
                entry["allowed_modes"] = meta.get("allowed_modes")
                entry["mode_confirmation"] = meta.get("mode_confirmation")
                entry["stats"] = meta.get("stats", {})
                entry["created_at"] = meta.get("created_at", "")
                has_meta = bool(entry["stats"])
            except (json.JSONDecodeError, OSError):
                pass

        if has_meta:
            # Fast path: only count lines, skip JSON parsing
            try:
                with open(fpath, encoding="utf-8") as f:
                    entry["trade_count"] = sum(1 for line in f if line.strip())
            except OSError:
                entry["trade_count"] = 0
        else:
            # Slow path (one-time): parse trades → compute stats → persist meta
            trades = []
            try:
                with open(fpath, encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            trades.append(json.loads(line))
            except (OSError, json.JSONDecodeError):
                pass
            entry["trade_count"] = len(trades)

            if trades:
                stats = _compute_stats_from_trades(trades)
                entry["stats"] = stats
                entry["balance"] = 10000
                try:
                    backfill_meta = {
                        "symbol": symbol, "days": days, "balance": 10000,
                        "strategy_params": {}, "param_overrides": {},
                        "stats": stats,
                        "created_at": datetime.now(HKT).isoformat(),
                        "backfilled": True,
                    }
                    tmp_m = tempfile.NamedTemporaryFile(
                        mode='w', dir=BT_DATA_DIR, delete=False, suffix='.tmp')
                    json.dump(backfill_meta, tmp_m, ensure_ascii=False)
                    tmp_m.close()
                    os.replace(tmp_m.name, meta_path)
                    logging.info("Backfilled meta for %s", fname)
                except OSError:
                    pass

        results.append(entry)
    return results


def handle_bt_klines(qs: dict):
    """Return klines for chart display. Supports multiple intervals."""
    symbol = qs.get("symbol", [""])[0].upper()
    days_str = qs.get("days", ["60"])[0]
    interval = qs.get("interval", ["1h"])[0].lower()
    if not symbol:
        return 400, {"error": "symbol required"}
    try:
        days = int(days_str)
    except ValueError:
        return 400, {"error": "invalid days"}
    if interval not in _BT_VALID_INTERVALS:
        return 400, {"error": f"invalid interval: {interval}. Valid: {sorted(_BT_VALID_INTERVALS)}"}

    # Enforce max days per interval
    max_days = _BT_INTERVAL_MAX_DAYS[interval]
    if days > max_days:
        days = max_days

    if HOME not in sys.path:
        sys.path.insert(0, HOME)
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from backtest.fetch_historical import fetch_klines_range
    from backtest.engine import WARMUP_CANDLES

    try:
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)
        # Warmup buffer only meaningful for 1h (backtest engine timeframe)
        wh = WARMUP_CANDLES if interval == "1h" else 0
        start_ms = int((now - timedelta(hours=days * 24 + wh)).timestamp() * 1000)

        plat = "aster" if symbol in _BT_ASTER_SYMBOLS else "binance"
        df = fetch_klines_range(symbol, interval, start_ms, end_ms, plat)
    except Exception as e:
        return 500, {"error": f"Failed to fetch klines: {e}"}
    # KLineChart format
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "timestamp": int(row["open_time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })
    return 200, {"candles": candles, "interval": interval, "days": days}


def handle_bt_results(qs: dict):
    """Return trades for a specific backtest result file.
    Accepts either ?file=bt_BTCUSDT_30d_v2_trades.jsonl (exact)
    or ?symbol=BTCUSDT&days=30 (legacy, finds first match)."""
    # Prefer exact file parameter (used by loadExisting for _v{N} files)
    file_param = qs.get("file", [""])[0]
    if file_param:
        # Sanitize: only allow expected filename patterns
        if not file_param.endswith("_trades.jsonl") or "/" in file_param:
            return 400, {"error": "invalid file parameter"}
        fpath = os.path.join(BT_DATA_DIR, file_param)
        if not os.path.isfile(fpath):
            return 404, {"error": "file not found"}
        trades = []
        with open(fpath, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))
        return 200, {"file": file_param, "trades": trades}

    # Legacy: symbol + days lookup
    symbol = qs.get("symbol", [""])[0].upper()
    days_str = qs.get("days", [""])[0]
    if not symbol or not days_str:
        return 400, {"error": "symbol and days (or file) required"}
    try:
        days = int(days_str)
    except ValueError:
        return 400, {"error": "invalid days"}
    fname = f"bt_{symbol}_{days}d_trades.jsonl"
    fpath = os.path.join(BT_DATA_DIR, fname)
    if not os.path.isfile(fpath):
        return 404, {"error": "file not found"}
    trades = []
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))
    return 200, {"symbol": symbol, "days": days, "trades": trades}


def _save_bt_result(symbol: str, days: int, trades: list):
    """Auto-save backtest trades to JSONL so 'Load old results' can find them."""
    try:
        os.makedirs(BT_DATA_DIR, exist_ok=True)
        fname = f"bt_{symbol}_{days}d_trades.jsonl"
        fpath = os.path.join(BT_DATA_DIR, fname)
        tmp = fpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        os.replace(tmp, fpath)
    except OSError as e:
        logging.warning("Failed to save backtest result: %s", e)


def _save_bt_metadata(symbol: str, days: int, balance: float,
                      strategy_params: dict | None = None,
                      param_overrides: dict | None = None,
                      allowed_modes: list | None = None,
                      mode_confirmation: int | None = None,
                      stats: dict | None = None):
    """Save backtest run metadata as JSON sidecar for later reference."""
    try:
        os.makedirs(BT_DATA_DIR, exist_ok=True)
        fname = f"bt_{symbol}_{days}d_meta.json"
        fpath = os.path.join(BT_DATA_DIR, fname)
        meta = {
            "symbol": symbol, "days": days, "balance": balance,
            "strategy_params": strategy_params or {},
            "param_overrides": param_overrides or {},
            "allowed_modes": allowed_modes,
            "mode_confirmation": mode_confirmation,
            "stats": stats or {},
            "created_at": datetime.now(HKT).isoformat(),
        }
        tmp = tempfile.NamedTemporaryFile(
            mode='w', dir=os.path.dirname(fpath),
            delete=False, suffix='.tmp')
        json.dump(meta, tmp, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, fpath)
    except OSError as e:
        logging.warning("Failed to save backtest metadata: %s", e)


_BT_ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT", "POLUSDT", "XAGUSDT", "XAUUSDT"}
_BT_ASTER_SYMBOLS = {"XAGUSDT", "XAUUSDT"}  # use Aster DEX for klines
_BT_MAX_DAYS = 365
_BT_JOB_TIMEOUT = 600  # 10 minutes
_BT_INTERVAL_MAX_DAYS = {
    "1m": 7, "5m": 30, "15m": 60,
    "1h": 365, "4h": 365, "1d": 365,
}
_BT_VALID_INTERVALS = set(_BT_INTERVAL_MAX_DAYS.keys())

_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

_aggtrades_lock = threading.Lock()


def handle_bt_aggtrades(qs: dict):
    """Return aggregated aggTrade data for footprint/delta/VP/large-trade features."""
    if not _aggtrades_lock.acquire(blocking=False):
        return 429, {"error": "aggTrades fetch already in progress, please wait"}
    try:
        return _handle_bt_aggtrades_inner(qs)
    finally:
        _aggtrades_lock.release()


def _handle_bt_aggtrades_inner(qs: dict):
    symbol = qs.get("symbol", [""])[0].upper()
    if not symbol:
        return 400, {"error": "symbol required"}
    if symbol not in _BT_ALLOWED_SYMBOLS:
        return 400, {"error": f"symbol not allowed: {symbol}. Valid: {sorted(_BT_ALLOWED_SYMBOLS)}"}

    try:
        days = min(int(qs.get("days", ["7"])[0]), 14)
    except ValueError:
        return 400, {"error": "invalid days"}
    if days < 1:
        return 400, {"error": "days must be >= 1"}

    interval = qs.get("interval", ["1h"])[0].lower()
    if interval not in _INTERVAL_MS:
        return 400, {"error": f"invalid interval: {interval}"}

    features_str = qs.get("features", ["delta,large,profile,heatmap"])[0]
    features = set(f.strip() for f in features_str.split(","))

    # Heatmap/delta on small intervals creates too many entries (20K+ at 1m)
    # Force minimum 15m for time-bucketed features
    _MIN_BUCKET_INTERVAL = 900_000  # 15m
    if _INTERVAL_MS[interval] < _MIN_BUCKET_INTERVAL:
        if "delta" in features or "heatmap" in features:
            interval = "15m"

    try:
        threshold = float(qs.get("threshold", ["100000"])[0])
    except ValueError:
        threshold = 100_000

    from backtest.fetch_agg_trades import (
        fetch_agg_trades_range,
        aggregate_delta_volume,
        aggregate_large_trades,
        aggregate_volume_profile,
        aggregate_footprint_heatmap,
        AGG_BUCKET_DEFAULTS,
    )

    try:
        bucket_str = qs.get("bucket_size", [""])[0]
        bucket_size = float(bucket_str) if bucket_str else AGG_BUCKET_DEFAULTS.get(symbol, 50)
    except ValueError:
        bucket_size = AGG_BUCKET_DEFAULTS.get(symbol, 50)

    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    interval_ms = _INTERVAL_MS[interval]

    # Align start_ms to interval boundary so candle timestamps match Binance klines
    start_ms = (start_ms // interval_ms) * interval_ms

    try:
        trades_df = fetch_agg_trades_range(symbol, start_ms, end_ms)
    except Exception as e:
        return 500, {"error": f"Failed to fetch aggTrades: {e}"}

    # Build candle timestamps aligned to interval boundaries
    candle_ts = list(range(start_ms, end_ms, interval_ms))

    result = {"symbol": symbol, "days": days, "interval": interval}

    if "delta" in features:
        result["delta_volume"] = aggregate_delta_volume(trades_df, candle_ts, interval_ms)
    if "large" in features:
        result["large_trades"] = aggregate_large_trades(trades_df, threshold)
    if "profile" in features:
        result["volume_profile"] = aggregate_volume_profile(trades_df, bucket_size)
    if "heatmap" in features:
        result["heatmap"] = aggregate_footprint_heatmap(
            trades_df, candle_ts, interval_ms, bucket_size
        )

    return 200, result


def handle_bt_run(body: str):
    """Start a backtest run in ProcessPool."""
    try:
        req = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}
    symbol = req.get("symbol", "BTCUSDT").upper()
    try:
        days = int(req.get("days", 60))
        balance = float(req.get("balance", 10000))
    except (ValueError, TypeError):
        return 400, {"error": "invalid days or balance"}

    # Phase 5C: Input validation
    if symbol not in _BT_ALLOWED_SYMBOLS:
        return 400, {"error": f"symbol not allowed: {symbol}. Valid: {sorted(_BT_ALLOWED_SYMBOLS)}"}
    if days < 1 or days > _BT_MAX_DAYS:
        return 400, {"error": f"days must be 1-{_BT_MAX_DAYS}"}
    if balance < 100 or balance > 10_000_000:
        return 400, {"error": "balance must be 100-10000000"}

    # Validate param_overrides numeric ranges
    param_overrides_raw = req.get("param_overrides") or {}
    if isinstance(param_overrides_raw, dict):
        for k, v in param_overrides_raw.items():
            if not isinstance(v, (int, float)):
                return 400, {"error": f"param_overrides.{k} must be numeric"}

    # Optional overrides from param panel
    strategy_params = req.get("strategy_params") or None
    param_overrides = req.get("param_overrides") or None
    allowed_modes = req.get("allowed_modes") or None
    mode_confirmation = req.get("mode_confirmation") or None
    if mode_confirmation is not None:
        mode_confirmation = int(mode_confirmation)

    job_id = f"{symbol}_{days}d_{int(time.time())}"
    with _bt_lock:
        _evict_old_jobs()
        _bt_jobs[job_id] = {"status": "running", "symbol": symbol, "days": days,
                            "result": None, "error": None}

    def _on_done(fut):
        with _bt_lock:
            try:
                result = fut.result()
                _bt_jobs[job_id]["result"] = result
                _bt_jobs[job_id]["status"] = "done"
                # Auto-save trades to JSONL for later loading
                _save_bt_result(symbol, days, result.get("trades", []))
            except Exception as e:
                _bt_jobs[job_id]["error"] = str(e)
                _bt_jobs[job_id]["status"] = "error"
                return
        # Save metadata sidecar outside lock (I/O bound)
        stats = {k: result.get(k) for k in (
            "return_pct", "win_rate", "profit_factor",
            "max_drawdown_pct", "total_trades", "sharpe_ratio",
            "sortino_ratio", "calmar_ratio", "var_95", "cvar_95",
            "recovery_factor", "payoff_ratio",
            "expectancy", "sqn", "sqn_grade", "alpha",
            "buyhold_return", "exposure_pct",
            "kelly_pct", "cagr_pct",
            "monthly_returns",
        ) if result.get(k) is not None}
        _save_bt_metadata(symbol, days, balance,
                          strategy_params=strategy_params,
                          param_overrides=param_overrides,
                          allowed_modes=allowed_modes,
                          mode_confirmation=mode_confirmation,
                          stats=stats)

    plat = "aster" if symbol in _BT_ASTER_SYMBOLS else "binance"
    fut = _get_bt_pool().submit(
        _run_bt_worker, symbol, days, balance,
        strategy_params=strategy_params,
        param_overrides=param_overrides,
        allowed_modes=allowed_modes,
        mode_confirmation=mode_confirmation,
        platform=plat,
    )
    fut.add_done_callback(_on_done)
    return 200, {"job_id": job_id, "status": "running"}


def handle_bt_status(qs: dict):
    """Check backtest job status."""
    job_id = qs.get("job_id", [""])[0]
    if not job_id:
        with _bt_lock:
            return 200, {k: {"status": v["status"], "symbol": v["symbol"],
                              "days": v["days"]}
                         for k, v in _bt_jobs.items()}
    with _bt_lock:
        job = _bt_jobs.get(job_id)
    if not job:
        return 404, {"error": "job not found"}
    resp = {"job_id": job_id, "status": job["status"],
            "symbol": job["symbol"], "days": job["days"]}
    if job["status"] == "done":
        resp["result"] = job["result"]
    elif job["status"] == "error":
        resp["error"] = job["error"]
    return 200, resp


_REPORT_FORMAT_VERSION = "1.0"


def handle_bt_export(qs: dict):
    """Export a complete backtest report as a single JSON file.
    Accepts ?file=exact_filename or ?symbol=X&days=N (legacy)."""
    file_param = qs.get("file", [""])[0]
    if file_param:
        if not file_param.endswith("_trades.jsonl") or "/" in file_param:
            return 400, {"error": "invalid file parameter"}
        fname = file_param
        # Extract symbol/days from filename for report metadata
        m = re.match(r'bt_([A-Z]+)_(\d+)d(?:_v\d+)?_trades\.jsonl', fname)
        if not m:
            return 400, {"error": "cannot parse filename"}
        symbol, days = m.group(1), int(m.group(2))
    else:
        symbol = qs.get("symbol", [""])[0].upper()
        days_str = qs.get("days", [""])[0]
        if not symbol or not days_str:
            return 400, {"error": "symbol and days (or file) required"}
        try:
            days = int(days_str)
        except ValueError:
            return 400, {"error": "invalid days"}
        fname = f"bt_{symbol}_{days}d_trades.jsonl"

    # Read trades from JSONL
    fpath = os.path.join(BT_DATA_DIR, fname)
    if not os.path.isfile(fpath):
        return 404, {"error": f"No saved result for {fname}"}
    trades = []
    with open(fpath, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))

    # Read meta sidecar if available (matches trades filename base)
    meta_path = os.path.join(BT_DATA_DIR, fname.replace("_trades.jsonl", "_meta.json"))
    meta = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    report = {
        "format_version": _REPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": "AXC BacktestEngine",
        "config": {
            "symbol": symbol,
            "days": days,
            "balance": meta.get("balance", 10000),
            "interval": "1h",
            "strategy_params": meta.get("strategy_params", {}),
            "param_overrides": meta.get("param_overrides", {}),
        },
        "stats": meta.get("stats", {}),
        "trades": trades,
    }

    # Also save a copy to exports folder for local reference
    export_dir = os.path.join(BT_DATA_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
    export_path = os.path.join(export_dir, f"{symbol}_{days}d_{ts}.json")
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode='w', dir=export_dir, delete=False, suffix='.tmp')
        json.dump(report, tmp, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, export_path)
        logging.info("Exported report → %s", export_path)
    except OSError as e:
        logging.warning("Failed to save export copy: %s", e)

    return 200, report


def handle_bt_import(body: str):
    """Import a backtest report JSON and save as JSONL + meta for dashboard viewing."""
    try:
        report = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON"}

    # Validate minimum required fields
    trades = report.get("trades")
    if not trades or not isinstance(trades, list):
        return 400, {"error": "trades array is required and must not be empty"}

    config = report.get("config", {})
    symbol = config.get("symbol", "").upper()
    if not symbol:
        return 400, {"error": "config.symbol is required"}

    # Validate each trade has minimum fields
    required_trade_fields = {"side", "entry", "exit"}
    for i, t in enumerate(trades):
        missing = required_trade_fields - set(t.keys())
        if missing:
            return 400, {"error": f"trade[{i}] missing fields: {missing}"}

    # Normalize trades: ensure pnl exists.
    # NOTE: fallback PnL = price diff only (ignores qty/position size).
    # External engines should include their own pnl field for accuracy.
    for t in trades:
        if "pnl" not in t:
            entry = float(t["entry"])
            exit_p = float(t["exit"])
            mult = -1 if t["side"].upper() == "SHORT" else 1
            t["pnl"] = round((exit_p - entry) * mult, 2)
        # Normalize time field
        if "entry_time" not in t and "ts" in t:
            t["entry_time"] = t["ts"]

    # Determine days from config or trade time range
    days = config.get("days", 0)
    if not days and len(trades) >= 2:
        first_ts = trades[0].get("entry_time") or trades[0].get("ts", "")
        last_ts = trades[-1].get("entry_time") or trades[-1].get("ts", "")
        if first_ts and last_ts:
            try:
                # stdlib fromisoformat handles "2026-03-01T08:00:00" and "2026-03-01 08:00:00"
                d0 = datetime.fromisoformat(first_ts)
                d1 = datetime.fromisoformat(last_ts)
                days = max(1, (d1 - d0).days)
            except (ValueError, TypeError) as e:
                logging.warning("Could not parse trade timestamps for days estimate: %s", e)
                days = len(trades)
    if not days:
        days = len(trades)

    # Use standard {days}d naming so loadExisting + export can find it.
    # To avoid overwriting native results, check if file exists and append
    # a numeric suffix: bt_BTCUSDT_30d_trades.jsonl → bt_BTCUSDT_30d_v2_trades.jsonl
    base = f"bt_{symbol}_{days}d"
    fname = f"{base}_trades.jsonl"
    fpath = os.path.join(BT_DATA_DIR, fname)
    suffix = 1
    while os.path.isfile(fpath) and suffix < 100:
        suffix += 1
        fname = f"{base}_v{suffix}_trades.jsonl"
        fpath = os.path.join(BT_DATA_DIR, fname)
    if suffix >= 100:
        return 400, {"error": f"Too many imports for {symbol} {days}d (max 100)"}

    os.makedirs(BT_DATA_DIR, exist_ok=True)
    tmp = fpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    os.replace(tmp, fpath)

    # Save meta sidecar (same base name)
    stats = report.get("stats", {})
    meta = {
        "symbol": symbol,
        "days": days,
        "balance": config.get("balance", 10000),
        "strategy_params": config.get("strategy_params", {}),
        "param_overrides": config.get("param_overrides", {}),
        "stats": stats,
        "source": report.get("source", "external"),
        "created_at": datetime.now(HKT).isoformat(),
    }
    meta_fname = fname.replace("_trades.jsonl", "_meta.json")
    meta_path = os.path.join(BT_DATA_DIR, meta_fname)
    tmp_m = tempfile.NamedTemporaryFile(
        mode='w', dir=BT_DATA_DIR, delete=False, suffix='.tmp')
    json.dump(meta, tmp_m, ensure_ascii=False)
    tmp_m.close()
    os.replace(tmp_m.name, meta_path)

    # Save original imported JSON to exports folder
    export_dir = os.path.join(BT_DATA_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)
    ts_tag = datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
    import_copy = os.path.join(export_dir, f"imported_{symbol}_{days}d_{ts_tag}.json")
    try:
        with open(import_copy, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
        logging.info("Imported report saved → %s", import_copy)
    except OSError as e:
        logging.warning("Failed to save import copy: %s", e)

    return 200, {
        "status": "imported",
        "file": fname,
        "days": days,
        "trades": len(trades),
        "symbol": symbol,
    }


_ALLOWED_ORIGINS = {
    f"http://127.0.0.1:{PORT}",
    f"http://localhost:{PORT}",
}


# ── AI Chat handlers ─────────────────────────────────────────────────

_CHAT_SYSTEM_PROMPT = """你係 AXC Dashboard 嘅 AI 交易搭檔。
格式：Markdown OK（dashboard 支援 bold、list、code）。回覆上限 15 行。
語氣：香港交易員廣東話口語，直接有態度。
收到數據問數據答，有觀點要講。唔好客套。
成交量解讀：volume_ratio >1.5 = 成交活躍，breakout 可信度高；<0.5 = 成交低迷，小心假突破。"""


def _sonnet_usage_ok() -> bool:
    """Check + increment shared Sonnet daily cap. Thread-safe via file lock."""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    try:
        os.makedirs(os.path.dirname(_SONNET_USAGE_PATH), exist_ok=True)
        with open(_SONNET_USAGE_PATH, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read().strip()
            data = json.loads(raw) if raw else {"date": today, "count": 0}
            if data.get("date") != today:
                data = {"date": today, "count": 0}
            if data["count"] >= _CHAT_SONNET_DAILY_CAP:
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            data["count"] += 1
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
            fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception:
        return False


def _build_chat_context() -> str:
    """Build compact context from collect_data() for AI chat (~1500 chars)."""
    try:
        d = collect_data()
    except Exception as e:
        return f"(context error: {e})"
    parts = []

    # Mode + risk
    parts.append(f"模式: {d.get('mode', '?')} | 連虧: {d.get('consecutive_losses', 0)}")
    risk = d.get("risk_status", {})
    if risk:
        parts.append(f"風險: DD {risk.get('current_dd_pct', 0)}% | 日限 {risk.get('daily_limit_pct', 0)}% used")

    # Balance + PnL
    parts.append(f"餘額: ${d.get('balance', 0):.2f} | 今日: {d.get('today_pnl', 0):+.2f} | 總計: {d.get('total_pnl', 0):+.2f}")

    # Positions
    positions = d.get("live_positions", [])
    if positions:
        pos_lines = []
        for p in positions[:5]:
            hs = p.get("hold_score", {})
            score_str = f" 評分={hs.get('score')}" if hs.get("score") is not None else ""
            pos_lines.append(
                f"  {p.get('pair','?')} {p.get('direction','?')} "
                f"entry={p.get('entry_price', 0)} "
                f"SL={p['sl_price'] if p.get('sl_price') else '-'} "
                f"TP={p['tp_price'] if p.get('tp_price') else '-'} "
                f"uPnL={p.get('unrealized_pnl', 0):+.2f}{score_str}"
            )
        parts.append("持倉:\n" + "\n".join(pos_lines))
    else:
        parts.append("持倉: 無")

    # Prices + changes
    ap = d.get("action_plan", [])
    if ap:
        price_parts = []
        for a in ap:
            chg = a.get("change_24h", "")
            price_parts.append(f"{a.get('symbol','?')} {a.get('price', '?')} ({chg})")
        parts.append("價格: " + " | ".join(price_parts))

    # Volume ratios (current vs 30-candle avg)
    if ap:
        vr_parts = [
            f"{a.get('symbol','?').replace('USDT','')} {a.get('volume_ratio', 0):.1f}x"
            for a in ap if a.get("volume_ratio", 0) > 0
        ]
        if vr_parts:
            parts.append("成交量: " + " | ".join(vr_parts))

    # Funding rates
    fr = d.get("funding_rates", {})
    if fr:
        fr_parts = []
        for sym, data in fr.items():
            if isinstance(data, dict):
                rate = data.get("rate", "?")
                fr_parts.append(f"{sym}={rate}")
        if fr_parts:
            parts.append("資金費率: " + " | ".join(fr_parts))

    # Latest signal
    sig = d.get("signal_active", "NO")
    if sig == "YES":
        parts.append(f"信號: {d.get('signal_pair', '?')} ACTIVE")

    return "\n".join(parts)


def _call_dashboard_llm(user_msg: str, context: str,
                        history: list[dict] | None = None,
                        model_chain: list[str] | None = None) -> str:
    """Call LLM via proxy with model fallback chain."""
    chain = model_chain or _CHAT_MODEL_CHAIN_FAST

    msgs = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]
    if history:
        for m in history:
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user",
                 "content": f"{context}\n\n---\n\n用戶：{user_msg}"})

    for model in chain:
        is_anthropic = model.startswith("claude-")
        endpoint = "messages" if is_anthropic else "chat/completions"
        url = f"{PROXY_BASE_URL}/{endpoint}"

        if is_anthropic:
            body_dict = {"model": model, "max_tokens": 1200,
                         "system": _CHAT_SYSTEM_PROMPT,
                         "messages": msgs[1:]}  # exclude system msg
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {PROXY_API_KEY}",
                       "anthropic-version": "2023-06-01"}
        else:
            body_dict = {"model": model, "max_tokens": 1200,
                         "messages": msgs}
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {PROXY_API_KEY}"}

        req = urllib.request.Request(url, json.dumps(body_dict).encode(),
                                     method="POST", headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            if is_anthropic:
                return data["content"][0]["text"]
            else:
                return data["choices"][0]["message"]["content"]
        except Exception:
            continue

    raise RuntimeError("All models in chain failed")


def handle_chat(body: str):
    """POST /api/chat — AI chat from dashboard."""
    if not PROXY_API_KEY:
        return 500, {"error": "API key not configured"}

    try:
        payload = json.loads(body)
    except Exception:
        return 400, {"error": "Invalid JSON"}

    msg = (payload.get("message") or "").strip()
    if not msg:
        return 400, {"error": "Empty message"}

    # Model routing: analysis keywords → Sonnet
    use_sonnet = any(kw in msg for kw in _CHAT_ANALYSIS_KW)
    if use_sonnet and not _sonnet_usage_ok():
        use_sonnet = False
    chain = _CHAT_MODEL_CHAIN_DEEP if use_sonnet else _CHAT_MODEL_CHAIN_FAST

    # Build context
    context = _build_chat_context()

    # Manage history (thread-safe)
    now = time.time()
    with _chat_lock:
        # Expire old entries
        _chat_history[:] = [m for m in _chat_history if now - m.get("ts", 0) < _CHAT_EXPIRY_SEC]
        # Trim to max pairs
        while len(_chat_history) > _CHAT_MAX_PAIRS * 2:
            _chat_history.pop(0)
        history_for_api = [{"role": m["role"], "content": m["content"]} for m in _chat_history]

    try:
        reply = _call_dashboard_llm(msg, context, history_for_api, chain)
    except urllib.error.URLError as e:
        logging.error(f"Chat API error: {e}")
        return 502, {"error": "AI 暫時冇回應，稍後再試"}
    except Exception as e:
        logging.error(f"Chat error: {e}")
        return 500, {"error": str(e)}

    # Update history
    with _chat_lock:
        _chat_history.append({"role": "user", "content": msg, "ts": now})
        _chat_history.append({"role": "assistant", "content": reply, "ts": now})

    model_label = "sonnet" if use_sonnet else "haiku"
    return 200, {"reply": reply, "model": model_label}


# ── Paper Trading (dry-run) ──────────────────────────────────────
TRADE_LOG_PATH = os.path.join(HOME, "shared", "TRADE_LOG.md")
DRYRUN_LOG_PATH = os.path.join(HOME, "logs", "dryrun.log")
LOAD_ENV_SH = os.path.join(SCRIPTS_DIR, "load_env.sh")
MAIN_PY = os.path.join(SCRIPTS_DIR, "trader_cycle", "main.py")
_dryrun_proc = None  # subprocess.Popen handle
_dryrun_lock = threading.Lock()


def _detect_tradercycle_mode():
    """Detect running tradercycle mode via psutil process scan.
    Returns (mode, pid): mode = 'live' | 'dry_run' | 'stopped'
    """
    if not HAS_PSUTIL:
        return "stopped", None
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline)
            if "trader_cycle/main.py" not in joined and "trader_cycle\\main.py" not in joined:
                continue
            if "--live" in cmdline:
                return "live", proc.info["pid"]
            return "dry_run", proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return "stopped", None


def _parse_dryrun_entries(limit=20):
    """Parse [DRY_RUN] entries from TRADE_LOG.md, return most recent `limit`."""
    entries = []
    try:
        with open(TRADE_LOG_PATH, "r") as f:
            for line in f:
                if "[DRY_RUN]" not in line:
                    continue
                # format: [2026-03-11 22:20] [DRY_RUN] ENTRY LONG SOLUSDT qty=1.0 @ 86.43 SL=84.42 TP=92.45 leverage=8x margin=$13.02
                m = re.match(
                    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] \[DRY_RUN\] "
                    r"(ENTRY|EXIT)\s+(LONG|SHORT)\s+(\S+)\s+"
                    r"(?:qty=(\S+)\s+@\s+(\S+))?"
                    r"(?:\s+SL=(\S+))?"
                    r"(?:\s+TP\d?=(\S+))?"
                    r"(?:\s+leverage=(\S+))?"
                    r"(?:\s+margin=\$?(\S+))?",
                    line.strip()
                )
                if m:
                    entries.append({
                        "time": m.group(1),
                        "action": m.group(2),
                        "direction": m.group(3),
                        "pair": m.group(4),
                        "qty": m.group(5) or "",
                        "price": m.group(6) or "",
                        "sl": m.group(7) or "",
                        "tp": m.group(8) or "",
                        "leverage": m.group(9) or "",
                        "margin": m.group(10) or "",
                    })
    except FileNotFoundError:
        pass
    return entries[-limit:]


def handle_paper_trading_status():
    """GET /api/paper-trading — status + recent dry-run entries."""
    mode, pid = _detect_tradercycle_mode()
    entries = _parse_dryrun_entries(20)
    return 200, {
        "mode": mode,
        "pid": pid,
        "entries": entries,
    }


def handle_paper_trading_start():
    """POST /api/paper-trading/start — launch one dry-run cycle."""
    global _dryrun_proc
    with _dryrun_lock:
        # Check if already running
        if _dryrun_proc and _dryrun_proc.poll() is None:
            return 409, {"error": "Dry-run 已在執行中", "pid": _dryrun_proc.pid}

        # Check live cycle
        mode, pid = _detect_tradercycle_mode()
        if mode == "live":
            return 409, {"error": "Live tradercycle 正在運行，無法同時執行 dry-run", "pid": pid}
        if mode == "dry_run":
            return 409, {"error": "Dry-run 已在執行中", "pid": pid}

        # Launch: bash load_env.sh python3 main.py --dry-run --verbose
        try:
            log_f = open(DRYRUN_LOG_PATH, "a")
            _dryrun_proc = subprocess.Popen(
                ["/bin/bash", LOAD_ENV_SH, sys.executable, MAIN_PY, "--dry-run", "--verbose"],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=HOME,
            )
            logging.info("Paper trading started: PID %d", _dryrun_proc.pid)
            return 200, {"ok": True, "pid": _dryrun_proc.pid}
        except Exception as e:
            logging.exception("Failed to start paper trading")
            return 500, {"error": str(e)}


def handle_paper_trading_stop():
    """POST /api/paper-trading/stop — terminate running dry-run."""
    global _dryrun_proc
    with _dryrun_lock:
        # First try our tracked subprocess
        if _dryrun_proc and _dryrun_proc.poll() is None:
            _dryrun_proc.terminate()
            try:
                _dryrun_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _dryrun_proc.kill()
            pid = _dryrun_proc.pid
            _dryrun_proc = None
            logging.info("Paper trading stopped: PID %d", pid)
            return 200, {"ok": True, "stopped_pid": pid}

        # Fallback: find via psutil
        mode, pid = _detect_tradercycle_mode()
        if mode == "dry_run" and pid and HAS_PSUTIL:
            try:
                p = psutil.Process(pid)
                p.terminate()
                p.wait(timeout=5)
                logging.info("Paper trading stopped (psutil): PID %d", pid)
                return 200, {"ok": True, "stopped_pid": pid}
            except Exception as e:
                return 500, {"error": f"無法停止 PID {pid}: {e}"}

        _dryrun_proc = None
        return 404, {"error": "沒有運行中嘅 dry-run process"}


class Handler(BaseHTTPRequestHandler):

    def _check_origin(self):
        """Block cross-origin POST requests (CSRF protection)."""
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        if origin:
            if origin not in _ALLOWED_ORIGINS:
                self._json_response(403, {"error": "Forbidden origin"})
                return False
        elif referer:
            if not any(referer == o or referer.startswith(o + "/") for o in _ALLOWED_ORIGINS):
                self._json_response(403, {"error": "Forbidden referer"})
                return False
        ct = self.headers.get("Content-Type", "")
        if not ct.startswith("application/json"):
            self._json_response(400, {"error": "Content-Type must be application/json"})
            return False
        return True

    def _json_response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "") if hasattr(self, 'headers') and self.headers else ""
        allowed = origin if origin in _ALLOWED_ORIGINS else f"http://127.0.0.1:{PORT}"
        self.send_header("Access-Control-Allow-Origin", allowed)
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
            if qs.get("token", [""])[0] != "axc-debug":
                self._json_response(403, {"error": "Forbidden"})
            else:
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
        elif path == "/api/exchange/balance":
            self._json_response(200, handle_exchange_balance())
        elif path == "/api/exchange/symbol-info":
            code, data = handle_symbol_info(qs)
            self._json_response(code, data)
        elif path == "/api/orderbook":
            code, data = handle_orderbook(qs)
            self._json_response(code, data)
        elif path == "/api/file":
            rel = qs.get("path", [""])[0]
            code, content = handle_file_read(rel)
            if isinstance(content, str):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{PORT}")
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                self._json_response(code, {"error": content})
        elif path == "/api/open_folder":
            rel = qs.get("path", [""])[0]
            code, data = handle_open_folder(rel)
            self._json_response(code, data)
        # ── Backtest API ──
        elif path == "/api/backtest/list":
            self._json_response(200, handle_bt_list())
        elif path == "/api/backtest/klines":
            code, data = handle_bt_klines(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/results":
            code, data = handle_bt_results(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/status":
            code, data = handle_bt_status(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/aggtrades":
            code, data = handle_bt_aggtrades(qs)
            self._json_response(code, data)
        elif path == "/api/backtest/export":
            code, data = handle_bt_export(qs)
            self._json_response(code, data)
        elif path == "/backtest":
            bt_path = os.path.join(HOME, "canvas/backtest.html")
            try:
                with open(bt_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/backtest.html not found")
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
        elif path == "/paper":
            paper_path = os.path.join(HOME, "canvas/paper.html")
            try:
                with open(paper_path, "rb") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/paper.html not found")
        elif path == "/api/docs-list":
            self._json_response(200, get_docs_list())
        elif path.startswith("/api/doc/"):
            filename = urllib.parse.unquote(path[9:])
            code, content, ctype = serve_doc(filename)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{PORT}")
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
        elif path == "/api/paper-trading":
            code, data = handle_paper_trading_status()
            self._json_response(code, data)
        elif path == "/api/services":
            self._json_response(200, handle_services())
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
        elif path.startswith("/svg/") or path.endswith((".css", ".js")):
            _mime = {".svg": "image/svg+xml", ".png": "image/png",
                     ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".css": "text/css; charset=utf-8",
                     ".js": "application/javascript; charset=utf-8"}
            ext = os.path.splitext(path)[1].lower()
            ctype = _mime.get(ext)
            img_path = os.path.join(HOME, "canvas", path.lstrip("/"))
            if ctype and os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-cache, must-revalidate")
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
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"canvas/index.html not found")

    def do_POST(self):
        if not self._check_origin():
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length > 0 else ""
        if self.path == "/api/set_mode":
            code, data = handle_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/config/mode":
            code, data = handle_set_mode(body)
            self._json_response(code, data)
        elif self.path == "/api/set_regime":
            code, data = handle_set_regime(body)
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
        elif self.path == "/api/place-order":
            code, data = handle_place_order(body)
            self._json_response(code, data)
        elif self.path == "/api/cancel-order":
            code, data = handle_cancel_order(body)
            self._json_response(code, data)
        elif self.path == "/api/backtest/run":
            code, data = handle_bt_run(body)
            self._json_response(code, data)
        elif self.path == "/api/backtest/import":
            if len(body) > 50 * 1024 * 1024:  # 50 MB limit
                self._json_response(413, {"error": "File too large (max 50 MB)"})
            else:
                code, data = handle_bt_import(body)
                self._json_response(code, data)
        elif self.path == "/api/chat":
            code, data = handle_chat(body)
            self._json_response(code, data)
        elif self.path == "/api/paper-trading/start":
            code, data = handle_paper_trading_start()
            self._json_response(code, data)
        elif self.path == "/api/paper-trading/stop":
            code, data = handle_paper_trading_stop()
            self._json_response(code, data)
        elif self.path == "/api/service/restart":
            data = handle_service_restart(body)
            self._json_response(200 if data["ok"] else 400, data)
        else:
            self._json_response(404, {"error": "Not found"})

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        allowed = origin if origin in _ALLOWED_ORIGINS else f"http://127.0.0.1:{PORT}"
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", allowed)
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

    # Restore pending SL/TP state from disk (crash recovery)
    _load_pending_sltp()

    # Auto-bootstrap stopped services
    _auto_bootstrap()

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    bind = "127.0.0.1"
    server = ThreadedHTTPServer((bind, port), Handler)
    print(f"AXC Dashboard: http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
