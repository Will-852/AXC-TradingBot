"""constants.py — Module-level constants shared across the dashboard package.

設計決定：所有 path、port、service 定義集中一處，
避免 circular import 同重複定義。
"""

import os
import re
import sys
from datetime import timezone, timedelta

HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))

# Port: 從 config/params.py 讀 DASHBOARD_PORT（唯一定義點），env var 可 override
try:
    if HOME not in sys.path:
        sys.path.insert(0, HOME)
    from config.params import DASHBOARD_PORT as _DEFAULT_PORT
except ImportError:
    _DEFAULT_PORT = 5566
PORT = int(os.environ.get("DASHBOARD_PORT", _DEFAULT_PORT))

SCRIPTS_DIR = os.path.join(HOME, "scripts")
if HOME not in sys.path:
    sys.path.insert(0, HOME)

HKT = timezone(timedelta(hours=8))

# ── Paths ───────────────────────────────────────────────────────────
PNL_HISTORY_PATH = os.path.join(HOME, "shared", "pnl_history.json")
BALANCE_BASELINE_PATH = os.path.join(HOME, "shared", "balance_baseline.json")
CANVAS_HTML = os.path.join(HOME, "canvas", "index.html")
SECRETS_ENV_PATH = os.path.join(HOME, "secrets", ".env")
NEWS_SENTIMENT_PATH = os.path.join(HOME, "shared", "news_sentiment.json")
MACRO_STATE_PATH = os.path.join(HOME, "shared", "macro_state.json")
PRICES_CACHE_PATH = os.path.join(HOME, "shared", "prices_cache.json")
DOCS_ROOT = os.path.join(HOME, "docs")
BT_DATA_DIR = os.path.join(HOME, "backtest", "data")
DRYRUN_LOG_PATH = os.path.join(HOME, "logs", "dryrun.log")
LOAD_ENV_SH = os.path.join(SCRIPTS_DIR, "load_env.sh")
MAIN_PY = os.path.join(SCRIPTS_DIR, "trader_cycle", "main.py")
TRADE_LOG_PATH = os.path.join(HOME, "shared", "TRADE_LOG.md")

# ── Service Management ──────────────────────────────────────────────
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

# ── AI Chat ─────────────────────────────────────────────────────────
PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
PROXY2_API_KEY = os.environ.get("PROXY2_API_KEY", "")
PROXY2_BASE_URL = os.environ.get("PROXY2_BASE_URL", "")

# ── Params Display ──────────────────────────────────────────────────
PARAMS_DISPLAY = [
    ("RISK_PER_TRADE_PCT",      "風險/單",   "%"),
    ("MAX_OPEN_POSITIONS",      "最大倉位",  ""),
    ("_SL_ATR_MULT_RANGE",      "SL(R)",     "×ATR"),
    ("_SL_ATR_MULT_TREND",      "SL(T)",     "×ATR"),
    ("_TP_ATR_MULT",            "止盈",      "×ATR"),
    ("_TRIGGER_PCT",            "觸發門檻",  "%"),
    ("_ALLOW_TREND",            "趨勢",      "bool"),
    ("_ALLOW_RANGE",            "區間",      "bool"),
    ("SCAN_INTERVAL_SEC",       "掃描間隔",  "秒"),
]

# ── Kline API routing ──────────────────────────────────────────────
_KLINE_API = {
    "binance": "https://fapi.binance.com",
    "aster": "https://fapi.asterdex.com",
}
_ASTER_ONLY = {"XAGUSDT", "XAUUSDT"}

# ── Exchange ────────────────────────────────────────────────────────
CONNECT_TIMEOUT_SEC = 15

# ── Optional dependencies ──────────────────────────────────────────
try:
    import psutil  # noqa: F401 — re-exported
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── Utility ─────────────────────────────────────────────────────────

def parse_md(path):
    """Parse KEY: VALUE lines from a .md state file."""
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

# ── Demo Data ───────────────────────────────────────────────────────
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
        {"key": "_SL_ATR_MULT_RANGE", "label": "SL(R)", "value": "1.2", "unit": "×ATR"},
        {"key": "_SL_ATR_MULT_TREND", "label": "SL(T)", "value": "1.5", "unit": "×ATR"},
        {"key": "_TP_ATR_MULT", "label": "止盈", "value": "3.0", "unit": "×ATR"},
        {"key": "_TRIGGER_PCT", "label": "觸發門檻", "value": "2.0", "unit": "%"},
    ],
    "scan_log": [],
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
    "pnl_history": [],
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
