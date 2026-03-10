"""
settings.py — 所有常數、閾值、路徑
唯一修改點：改任何交易參數只改呢個檔案
"""

import os
from datetime import timezone, timedelta

# ─── Paths ───
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
WORKSPACE = AXC_HOME  # alias for memory_keeper.py
_SHARED = os.path.join(AXC_HOME, "shared")
SCAN_CONFIG_PATH = os.path.join(_SHARED, "SCAN_CONFIG.md")
TRADE_STATE_PATH = os.path.join(_SHARED, "TRADE_STATE.md")
TRADE_LOG_PATH   = os.path.join(_SHARED, "TRADE_LOG.md")
SCAN_LOG_PATH    = os.path.join(_SHARED, "SCAN_LOG.md")
LOG_DIR = os.path.join(AXC_HOME, "logs")

# ─── Timezone ───
HKT = timezone(timedelta(hours=8))

# ─── Exchange APIs ───
ASTER_BASE = "https://fapi.asterdex.com"
ASTER_FAPI = f"{ASTER_BASE}/fapi/v1"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"
API_TIMEOUT = 10  # seconds

# ─── Pairs ───
PAIRS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "POLUSDT", "XAGUSDT", "XAUUSDT"]
PAIR_PREFIX = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "XRPUSDT": "XRP", "SOLUSDT": "SOL", "POLUSDT": "POL", "XAGUSDT": "XAG", "XAUUSDT": "XAU"}

# ─── Mode Detection (4H, 5 indicators) ───
MODE_RSI_TREND_LOW = 32          # RSI < 32 = trend signal
MODE_RSI_TREND_HIGH = 68         # RSI > 68 = trend signal
MODE_MACD_EXPANDING_THRESHOLD = 0.0  # histogram magnitude increasing
MODE_VOLUME_LOW = 0.50           # <50% of avg = trend signal
MODE_VOLUME_HIGH = 1.50          # >150% of avg = trend signal
MODE_FUNDING_THRESHOLD = 0.0007  # ±0.07%
MODE_CONFIRMATION_REQUIRED = 2   # consecutive same-mode before switch

# ─── Risk — Circuit Breakers (Non-negotiable) ───
CIRCUIT_BREAKER_SINGLE = 0.25    # 25% single position loss → immediate close
CIRCUIT_BREAKER_DAILY = 0.20     # 20% daily realized loss → stop all
COOLDOWN_2_LOSSES_MIN = 30       # 2 consecutive losses → 30min pause
COOLDOWN_3_LOSSES_MIN = 120      # 3 consecutive losses → 2hr pause
MAX_HOLD_HOURS = 72              # 3 days max hold
FUNDING_COST_FORCE_RATIO = 0.50  # funding > 50% unrealized → force close

# ─── Risk — No-Trade Conditions ───
NO_TRADE_VOLUME_MIN = 0.50       # volume < 50% of 30d avg = dead market
NO_TRADE_FUNDING_EXTREME = 0.002  # ±0.2% funding = extreme

# ─── Risk — Position Limits ───
MAX_CRYPTO_POSITIONS = 2
MAX_XAG_POSITIONS = 1
# BTC + ETH + SOL = same group (max 1 combined)
POSITION_GROUPS = {
    "crypto_correlated": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],  # max 1 total
    "crypto_independent": ["XRPUSDT", "POLUSDT"],             # max 1
    "commodity": ["XAGUSDT", "XAUUSDT"],                       # max 1
}

# ─── Range Strategy (Mode A) ───
RANGE_RISK_PCT = 0.02            # 2% capital per trade
RANGE_LEVERAGE = 8
RANGE_SL_ATR_MULT = 1.2          # SL = 1.2 × ATR
RANGE_MIN_RR = 2.3               # minimum reward:risk
RANGE_TRAILING_TRIGGER = 1.0     # +1R profit → move SL to breakeven

# ─── Trend Strategy (Mode B) ───
TREND_RISK_PCT = 0.02            # 2% capital per trade
TREND_LEVERAGE = 7
TREND_SL_ATR_MULT = 1.5          # SL = 1.5 × ATR
TREND_MIN_RR = 3.0               # minimum reward:risk

# ─── Scalp Strategy (Mode C) — Future ───
SCALP_RISK_PCT = 0.01            # 1% capital per trade
SCALP_LEVERAGE = 5
SCALP_SL_ATR_MULT = 1.0
SCALP_TP_ATR_MULT = 2.5

# ─── Yunis Collection: Volume Gate ───
ENTRY_VOLUME_MIN = 0.8              # volume_ratio < 0.8 → skip entry (low conviction)

# ─── Yunis Collection: MACD Weakening Exit ───
MACD_HIST_DECAY_THRESHOLD = 0.6     # histogram shrinks to <60% of prev → weakening

# ─── Yunis Collection: OBV Confirmation ───
OBV_CONFIRM_BONUS = 0.5              # OBV 方向同信號一致 → +0.5
OBV_AGAINST_PENALTY = -0.5           # OBV 方向同信號相反 → -0.5

# ─── Yunis Collection: Signal Confidence → Position Size ───
CONFIDENCE_RISK_HIGH = 1.25         # score >= 4.5 → risk × 1.25
CONFIDENCE_RISK_NORMAL = 1.0        # score 3.0-4.4 → risk × 1.0
CONFIDENCE_RISK_LOW = 0.6           # score < 3.0 → risk × 0.6
CONFIDENCE_RISK_CAP = 0.03          # absolute cap: never exceed 3% risk

# ─── Day-of-Week Bias (UTC+8) ───
# Thursday 21:00-01:00 → SHORT bias (3.5/5 sufficient)
# Friday  21:00-03:00 → LONG bias  (3.5/5 sufficient)
BIAS_THRESHOLD = 3.5              # reduced from 4/5 to 3.5/5

# ─── Re-entry Rules ───
REENTRY_MIN_WAIT_MIN = 10        # minimum 10min between trades
REENTRY_INDICATORS_REQUIRED = 5  # 5/5 indicators (stricter)
REENTRY_SIZE_REDUCTION = 0.30    # 30% smaller position

# ─── Trailing SL/TP + Early Exit (AdjustPositionsStep) ───
TRAILING_SL_BREAKEVEN_ATR = 1.0    # profit > 1×ATR → SL to entry
TRAILING_SL_LOCK_PROFIT_ATR = 2.0  # profit > 2×ATR → SL to entry+1×ATR
EARLY_EXIT_RSI_OVERBOUGHT = 70     # LONG exit threshold
EARLY_EXIT_RSI_OVERSOLD = 30       # SHORT exit threshold
EARLY_EXIT_VOLUME_SPIKE = 2.0      # opposite-direction volume threshold
EARLY_EXIT_MIN_ADVERSE_PCT = 0.002 # 0.2% minimum adverse move for volume exit
TP_EXTEND_ADX_MIN = 25             # trend confirmed for TP extension
TP_EXTEND_RSI_LONG_MAX = 75        # RSI still room for LONG
TP_EXTEND_RSI_SHORT_MIN = 25       # RSI still room for SHORT
TP_EXTEND_ATR_MULT = 1.0           # extend TP by 1×ATR
TP_PROXIMITY_PCT = 0.003           # 0.3% = near TP
REENTRY_COOLDOWN_CYCLES = 3        # 3 cycles ≈ 1.5h

# ─── Telegram ───
TG_BOT_TOKEN = "8373819624:AAFH-SVTqqYlU22JnuiiBpB2uZytvw_pN30"
TG_CHAT_ID = "2060972655"

# ─── Silent Mode ───
SILENT_MODE_THRESHOLD_CYCLES = 2  # 2 consecutive NO SIGNAL → silent

# ─── SCAN_CONFIG Writer Fields (trader-cycle owns these) ───
TRADER_OWNED_FIELDS = [
    "CONFIG_VALID", "SILENT_MODE", "SILENT_MODE_CYCLES",
    "last_updated", "update_count",
    # ATR fields
    "BTC_ATR", "ETH_ATR", "XRP_ATR", "SOL_ATR", "POL_ATR", "XAG_ATR", "XAU_ATR",
    # S/R fields
    "BTC_support", "BTC_resistance", "ETH_support", "ETH_resistance",
    "XRP_support", "XRP_resistance", "SOL_support", "SOL_resistance",
    "POL_support", "POL_resistance",
    "XAG_support", "XAG_resistance", "XAU_support", "XAU_resistance",
    # S/R zones
    "BTC_support_zone", "BTC_resistance_zone",
    "ETH_support_zone", "ETH_resistance_zone",
    "XRP_support_zone", "XRP_resistance_zone",
    "SOL_support_zone", "SOL_resistance_zone",
    "POL_support_zone", "POL_resistance_zone",
    "XAG_support_zone", "XAG_resistance_zone",
    "XAU_support_zone", "XAU_resistance_zone",
]

# ─── Indicator Timeframes ───
PRIMARY_TIMEFRAME = "4h"
SECONDARY_TIMEFRAME = "1h"
KLINE_LIMIT = 200                # candles to fetch

# ─── Scan Log ───
SCAN_LOG_MAX_LINES = 200

# ─── Phase 3: Live Trading ───
SECRETS_PATH = os.path.join(AXC_HOME, "secrets", ".env")
ORDER_TIMEOUT_SEC = 300              # 5 min unfilled → cancel
PAPER_GATE_HOURS = 48                # minimum DRY_RUN hours before --live
PAPER_GATE_FILE = os.path.join(LOG_DIR, "paper_gate_start.txt")
CYCLE_LOG_DIR = os.path.join(LOG_DIR, "cycles")

# ─── Platform Symbol Lists (fallback; overridden by params.py below) ───
ASTER_SYMBOLS: set[str] = {"BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT", "XAUUSDT"}
BINANCE_SYMBOLS: set[str] = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "POLUSDT"}

# ─── Profile Override ───
# Read ACTIVE_PROFILE from config/params.py → override strategy constants
# This makes 打法 (穩/平/攻) actually control trading behavior
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_params", os.path.join(AXC_HOME, "config", "params.py")
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _profiles = getattr(_mod, "TRADING_PROFILES", {})
    _active = getattr(_mod, "ACTIVE_PROFILE", None)
    # ─── Platform symbol lists from params.py ───
    ASTER_SYMBOLS = set(getattr(_mod, "ASTER_SYMBOLS", []))
    BINANCE_SYMBOLS = set(getattr(_mod, "BINANCE_SYMBOLS", []))
    # ─── Mode Detection overrides from params.py ───
    MODE_RSI_TREND_LOW = getattr(_mod, "MODE_RSI_TREND_LOW", MODE_RSI_TREND_LOW)
    MODE_RSI_TREND_HIGH = getattr(_mod, "MODE_RSI_TREND_HIGH", MODE_RSI_TREND_HIGH)
    MODE_VOLUME_LOW = getattr(_mod, "MODE_VOLUME_LOW", MODE_VOLUME_LOW)
    MODE_VOLUME_HIGH = getattr(_mod, "MODE_VOLUME_HIGH", MODE_VOLUME_HIGH)
    MODE_FUNDING_THRESHOLD = getattr(_mod, "MODE_FUNDING_THRESHOLD", MODE_FUNDING_THRESHOLD)
    MODE_CONFIRMATION_REQUIRED = getattr(_mod, "MODE_CONFIRMATION_REQUIRED", MODE_CONFIRMATION_REQUIRED)

    _p = _profiles.get(_active, {}) if _active else {}
    if _p:
        RANGE_RISK_PCT = _p.get("risk_per_trade_pct", RANGE_RISK_PCT)
        TREND_RISK_PCT = _p.get("risk_per_trade_pct", TREND_RISK_PCT)
        RANGE_SL_ATR_MULT = _p.get("sl_atr_mult", RANGE_SL_ATR_MULT)
        TREND_SL_ATR_MULT = _p.get("sl_atr_mult", TREND_SL_ATR_MULT)
        RANGE_MIN_RR = _p.get("range_min_rr", RANGE_MIN_RR)
        TREND_MIN_RR = _p.get("trend_min_rr", TREND_MIN_RR)
        MAX_CRYPTO_POSITIONS = _p.get("max_open_positions", MAX_CRYPTO_POSITIONS)
    del _ilu, _spec, _mod, _profiles, _active, _p
except Exception:
    pass  # fallback to hardcoded defaults above
