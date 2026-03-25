"""
conv_1h/constants.py — All module-level constants for the 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
"""

import os
from datetime import timedelta, timezone

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))

# ─── Paths ───
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state_1h.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades_1h.jsonl")
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log_1h.jsonl")
_ANALYSIS_TAPE = os.path.join(_LOG_DIR, "analysis_1h.jsonl")
_SIGNAL_TAPE_1H = os.path.join(_LOG_DIR, "signal_tape_1h.jsonl")
_PAPER_PNL_LOG = os.path.join(_LOG_DIR, "paper_pnl_1h.jsonl")
_OBSERVE_LOG = os.path.join(_AXC, "polymarket", "logs", "observe_1h.jsonl")

# ─── Timezone ───
_HKT = timezone(timedelta(hours=8))
_ET = timezone(timedelta(hours=-4))

# ─── URLs ───
_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE = "https://api.binance.com/api/v3"
_DATA_API = "https://data-api.polymarket.com"

# ─── Loop timing ───
_CYCLE_S = 5
_HEAVY_INTERVAL_S = 20
_SCAN_INTERVAL_S = 300
_TOTAL_LOSS_FUSE_PCT = 0.22

# ─── Telegram (loaded from .env) ───
_ENV_PATH = os.path.join(_AXC, "secrets", ".env")
_TG_NEWS_TOKEN = ""
_TG_CHAT_ID = ""
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("TELEGRAM_NEWS_BOT_TOKEN="):
                _TG_NEWS_TOKEN = _line.split("=", 1)[1]
            elif _line.startswith("TELEGRAM_CHAT_ID="):
                _TG_CHAT_ID = _line.split("=", 1)[1]

# ─── Fill stats ───
_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}

# ─── Coin scope ───
_COIN_SLUGS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}
_COIN_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
_LIVE_COINS = {"BTC"}

# ─── Analysis collection ───
_ANALYSIS_INTERVAL_S = 60
_ANALYSIS_BURST_S = 15
_ANALYSIS_BURST_MIN = 57

# ─── Holder signal ───
_HOLDER_STRONG_IMBAL = 0.20
_HOLDER_MILD_IMBAL = 0.10
_HOLDER_CACHE_TTL = 30

# ─── Vol imbalance ───
_VOL_IMBAL_CACHE_TTL = 15

# ─── Time-of-Day gate ───
_TOD_SKIP_HOURS_HKT = {9, 19}

# ─── Paper trading ───
_PAPER_BUDGET = 8.40

# ─── 🔴 Exit thresholds (intentionally different from mm: 0.95 vs 0.96) ───
_BLACK_SWAN_MID = 0.95
_BLACK_SWAN_SELL_PCT = 0.95

# ─── Repricing ───
_1H_REPRICE_ENABLED = True
_1H_REPRICE_THRESHOLD = 0.02
_1H_REPRICE_MAX_PER_ORDER = 5
_1H_REPRICE_STOP_BEFORE_END_S = 600
_1H_REPRICE_MIN_AGE_S = 60
_1H_REPRICE_COOLDOWN_S = 20
