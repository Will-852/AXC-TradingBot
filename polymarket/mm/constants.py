"""
mm/constants.py — All module-level constants for the MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🔴 Trading-critical values — any change here directly affects real money.
"""

import os
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

# ─── Paths ───
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades.jsonl")  # base, see trade_path()
_SIGNAL_LOG = os.path.join(_LOG_DIR, "mm_signals.jsonl")  # base, see signal_path()
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log.jsonl")  # base, see order_path()
_BOTHSIDES_LOG = os.path.join(_LOG_DIR, "mm_bothsides.jsonl")
_POS_LOG = os.path.join(_LOG_DIR, "mm_positions.jsonl")
_REVERSAL_LOG = os.path.join(_LOG_DIR, "reversal_research.jsonl")


def signal_path(coin: str) -> str:
    return os.path.join(_LOG_DIR, f"mm_signals_{coin.upper()}.jsonl")


def order_path(coin: str) -> str:
    return os.path.join(_LOG_DIR, f"mm_order_log_{coin.upper()}.jsonl")


def trade_path(coin: str) -> str:
    return os.path.join(_LOG_DIR, f"mm_trades_{coin.upper()}.jsonl")


def coin_from_title(title: str) -> str:
    t = title.lower()
    if "ethereum" in t: return "ETH"
    if "solana" in t: return "SOL"
    if "xrp" in t: return "XRP"
    return "BTC"

# ─── Timezone ───
_HKT = ZoneInfo("Asia/Hong_Kong")
_ET = ZoneInfo("America/New_York")

# ─── Loop timing ───
_CYCLE_S = 2           # 2s main loop — fast reaction
_SCAN_S = 120          # discovery every 2 min
_HEAVY_INTERVAL_S = 3  # heavy ops every 3s

# ─── Newbie protection ───
_PROTECTION_HOURS = 3
_PROTECTION_BET_PCT = 0.01   # 1% per market during protection
_PROTECTION_MAX_MARKETS = 1  # 1 market per cycle

# ─── 🔴 Sizing / Execution gate ───
_MAX_ROUNDS = 3          # max scalp rounds per market window
_REENTRY_COOLDOWN_S = 30 # seconds after sell before re-entry
_LIVE_TRADE_COINS = {"btc", "sol"}  # BTC + SOL live; ETH + XRP = observe only
_BET_PCT_BY_COIN = {"btc": 0.03, "sol": 0.01}
_MIN_VIABLE_BUDGET = 3.50  # 2 sides × 5 shares × ~$0.20 = $2 min; need ≥$3.33 for T1 60% split

# ─── 🔴 Endgame (1-share data collection, last 2 min) ───
_ENDGAME_ENABLED = True
_ENDGAME_TTE_START = 120     # activate at T-120s
_ENDGAME_TTE_STOP = 30       # stop placing at T-30s
_ENDGAME_SHARES = 1          # 1 share per bet
_ENDGAME_MID_RANGE = (0.17, 0.83)   # only undecided markets
_ENDGAME_FLIP_RANGE = (0.38, 0.62)  # case 1: coin flip zone
_ENDGAME_REVERSAL = 0.20     # case 2: ≥20pt mid move toward center
_ENDGAME_DAILY_CAP = 10      # max 10 bets/day

# ─── Reversal research ───
_REVERSAL_TTE_START = 300    # start logging at T-5min
_REVERSAL_EXTREME_THRESH = 0.10  # log when cheap side ≤ 10¢

# ─── URLs ───
_BINANCE = "https://fapi.binance.com"
_BINANCE_SPOT = "https://api.binance.com"
_DATA_API = "https://data-api.polymarket.com"

# ─── Rate limiting ───
_API_LIMIT_PER_MIN = 200  # conservative: 200/min out of 2400 limit

# ─── Holder imbalance ───
_HOLDER_CACHE_TTL = 30  # seconds

# ─── Cross-exchange price validation ───
_CROSS_EXCHANGES = {
    "binance": "https://api.binance.com/api/v3/ticker/price?symbol={sym}",
    "okx":     "https://www.okx.com/api/v5/market/ticker?instId={sym_okx}",
    "bybit":   "https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}",
}
_SYM_MAP_OKX = {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"}

# ─── 🔴 W4 Signal parameters ───
_W4_DELAY_S = 300       # 5 min after window open (15M sweet spot)
_W4_THRESHOLD_BPS = 5   # 5 basis points minimum
_W4_LEAN_RATIO = 1.0    # v4: PURE ARB. Lean killed.
# v3 had dynamic 1.2/1.0 by tier. BMD proved: (1) lean = -$14.55 drag on 26 trades,
# (2) T1+T2 amplifies to R=2.7-4.2x, (3) one bad trade wipes session.
# Keep _w4_dynamic_ratio() for logging but FORCE R=1.0.
_W4_RATIO_BY_TIER = {
    "5-10": 1.0,   # pure arb — lean killed pending 50-trade validation
    "10+":  1.0,   # pure arb
}
_W4_EFFECTIVE_R_CAP = 1.1  # runtime cap: cancel excess if actual ratio > this

# ─── 🔴 W4 Staged Entry: T1 at 5min, T2 confirmation at 8min ───
_W4_T2_DELAY_S = 480       # T2 confirmation at 8 min into window
_W4_T1_PCT = 0.60          # T1 gets 60% of budget
_W4_T2_PCT = 0.40          # T2 gets 40% (if direction confirmed)

# ─── 🔴 W4 Order Repricing ───
_REPRICE_COOLDOWN_S = 5     # max 1 reprice per 5s per market
_REPRICE_THRESHOLD = 0.02   # 2¢ drift triggers reprice
_REPRICE_MAX_PER_ORDER = 3  # max 3 reprices per order lifetime

# ─── 🔴 W4 Lean-Unfilled Protection (Door B) ───
_LEAN_UNFILLED_TIMEOUT_S = 90  # seconds after entry before declaring lean unfilled
_LEAN_PREEMPTIVE_S = 20        # seconds for Stage 1 preemptive hedge cancel

# ─── 🔴 W4 Taker Conversion ───
_TAKER_CONVERT_ENABLED = True
_TAKER_CONVERT_DELAY_S = 3    # wait N seconds after first fill before converting
_TAKER_CONVERT_MAX_SPREAD = 0.04  # abort if taker price > maker price + 4¢

# ─── 🔴 Hedge parameters ───
_HEDGE_BTC_THRESHOLD = 50   # $50 BTC move in 30s = hedge trigger
_HEDGE_PCT = 0.30           # hedge 30% of position

# ─── 🔴 Exit thresholds (previously buried in run_cycle function body) ───
_EXIT_STOP_PCT = 0.25       # -25% → stop loss (pre-recovery only)
_BLACK_SWAN_MID = 0.96      # sell 96% at 96¢+ → lock profit, keep 4% free roll
_BLACK_SWAN_SELL_PCT = 0.96 # sell 96%, keep 4% as free upside
_COST_RECOVERY_MID = 0.64   # recover cost when mid ≥ 64¢

# ─── State defaults ───
_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}


# ─── Bug fix: _ts_hkt() was called but never defined (lines 2617, 2641) ───
def ts_hkt() -> str:
    """HKT timestamp string for log entries."""
    return datetime.now(tz=_HKT).isoformat(timespec="seconds")
