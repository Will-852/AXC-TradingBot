"""
mom_5m/config.py — Constants, per-coin config, and utilities for the 5M Momentum bot.

Split from run_5m_live.py (2026-03-26).
🟢 SAFE: Pure constants and config. No trading impact.
"""

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from datetime import timedelta, timezone

logger = logging.getLogger(__name__)

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))

# ─── Timezone ───
_HKT = timezone(timedelta(hours=8))

# ─── Paths ───
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state_5m.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades_5m.jsonl")
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log_5m.jsonl")
_W4_LOG = os.path.join(_LOG_DIR, "mm_w4_5m.jsonl")

# ─── URLs ───
_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE = "https://api.binance.com/api/v3"
_BINANCE_FUTURES = "https://fapi.binance.com"

# ─── Timing ───
_WINDOW_S = 300           # 5M = 300 seconds
_CYCLE_S = 5              # 5s main loop
_HEAVY_INTERVAL_S = 5     # heavy ops every 5s (v4 port, was 10s)
_SCAN_S = 60              # discover every 60s (5M windows every 300s)
_CANCEL_BEFORE_END_S = 30 # cancel 30s before window end (not 120s like 15M)
_RESOLUTION_DELAY_MS = 60_000  # wait 60s after window end before resolving

# ─── Risk ───
_TOTAL_LOSS_FUSE_PCT = 0.20  # 20% of initial bankroll
_BANKROLL_FRACTION = 0.30    # 5M uses 30% of wallet (15M=60%, 1H=10%)

# ─── Fill stats ───
_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}


# ─── Per-Coin Config ───

@dataclass
class CoinConfig:
    """Per-coin parameters. Each coin can be tuned independently."""
    live: bool = False          # True = execute orders, False = paper only
    delay_s: int = 15           # seconds after window open before entry
    threshold_bps: int = 5      # minimum |log_return| to trigger signal
    contrarian: bool = False    # True = bet AGAINST momentum (SOL pattern)
    symbol: str = "BTCUSDT"     # Binance symbol
    slug_prefix: str = "btc"    # Polymarket slug prefix
    min_order_size: float = 5.0 # CLOB minimum shares per order


COIN_CONFIG = {
    "btc": CoinConfig(live=True, symbol="BTCUSDT", slug_prefix="btc",
                      delay_s=60, threshold_bps=8),
    "eth": CoinConfig(live=False, symbol="ETHUSDT", slug_prefix="eth",
                      delay_s=60, threshold_bps=5),
    "sol": CoinConfig(live=False, symbol="SOLUSDT", slug_prefix="sol",
                      delay_s=60, threshold_bps=5),
    "xrp": CoinConfig(live=False, symbol="XRPUSDT", slug_prefix="xrp",
                      delay_s=60, threshold_bps=5),
}

_COIN_SYMBOLS = {c: cfg.symbol for c, cfg in COIN_CONFIG.items()}


# ─── Confidence-Tiered Lean Ratios ───
# Data-validated: 153 trades, corrected WR = 81.0%
# T1(5-10bps)=77.3% WR, T2(10-20bps)=87.9%, T3(>20bps)=100%

_LEAN_TIERS = [
    # (min_bps, lean_ratio, tier_name)
    (20, 8.0, "T3"),   # >20bps: 100% WR, aggressive lean
    (10, 5.0, "T2"),   # 10-20bps: 87.9% WR, strong lean
    (5,  2.0, "T1"),   # 5-10bps: 77.3% WR, conservative lean
]


def _tier_lean_ratio(mag_bps: float) -> tuple[float, str]:
    """Select lean ratio by signal magnitude. Returns (ratio, tier_name)."""
    for min_bps, ratio, name in _LEAN_TIERS:
        if mag_bps >= min_bps:
            return ratio, name
    return 1.0, "T0"  # below threshold — should not reach here (filtered earlier)


# ─── 🔴 Exit thresholds (intentionally different: 5M=0.99, mm=0.96, 1H=0.95) ───
_PROFIT_LOCK_MID = 0.99    # sell when winning side mid >= 99¢
_PROFIT_LOCK_BID_DISCOUNT = 0.01  # sell at mid - 1¢


# ─── Telegram ───
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


def tg_alert(msg: str):
    """Send alert via Telegram. Non-blocking, errors silenced."""
    if not _TG_NEWS_TOKEN or not _TG_CHAT_ID:
        return
    try:
        data = json.dumps({"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_TG_NEWS_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.debug("TG alert failed: %s", e)
