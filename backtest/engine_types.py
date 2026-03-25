"""
engine_types.py — Dataclasses, constants, and helpers for the backtest engine.

Split from engine.py (2026-03-26) for cleaner imports.
All symbols re-exported from engine.py — 17 import sites unchanged.
"""

import json
import os
import sys
from dataclasses import dataclass

# ─── Path setup ───
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

# ─── Production imports (re-exported for per_asset_optimizer.py) ───
from indicator_calc import calc_indicators, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES
from trader_cycle.config.settings import MAX_CRYPTO_POSITIONS

# ─── Config sync: all trading params from params.py (2026-03-26) ───
from config.params import (
    REGIME_ADJUST_ENABLED as _PARAMS_REGIME_ADJUST,
    REGIME_ATR_EXPAND_THRESHOLD as _PARAMS_REGIME_EXPAND,
    REGIME_ATR_CONTRACT_THRESHOLD as _PARAMS_REGIME_CONTRACT,
    SIGNAL_CONF_GATE as _PARAMS_CONF_GATE,
    SIGNAL_MODE_AFFINITY as _PARAMS_MODE_AFFINITY,
    SIGNAL_MODE_DEFAULT_PENALTY as _PARAMS_MODE_DEFAULT_PENALTY,
    SIGNAL_PERSISTENCE as _PARAMS_PERSISTENCE,
)
from trader_cycle.config.settings import (
    KELLY_WINDOW_N, KELLY_MIN_RISK, KELLY_MAX_RISK, KELLY_NO_EDGE,
    KELLY_MIN_TRADES_RANGE, KELLY_MIN_TRADES_TREND, KELLY_MIN_TRADES_CRASH,
)  # noqa: F401 — re-exported for engine.py facade

# ─── Constants ───
WARMUP_CANDLES = 200
COMMISSION_RATE = 0.0005   # 0.05% per side
SL_SLIPPAGE_PCT = 0.0002   # 0.02% adverse slippage on SL
CLUSTER_GAP_HOURS = 4
MAX_RISK_PCT = 0.05        # hard cap: never risk >5% per trade

# Persistence: sourced from params.py SIGNAL_PERSISTENCE
PERSISTENCE_THRESHOLD = {
    "range": _PARAMS_PERSISTENCE.get("range", 3),
    "trend": _PARAMS_PERSISTENCE.get("trend", 1),
    "crash": _PARAMS_PERSISTENCE.get("crash", 2),
    "burst": 1, "newarch": 1,
}

# Volatility regime → risk profile
_VOL_PROFILE_MAP = {"LOW": "balanced", "NORMAL": "balanced", "HIGH": "conservative"}
_VOL_DOWNGRADE = {"LOW": "NORMAL", "NORMAL": "HIGH", "HIGH": "HIGH"}
_PROFILE_RISK = {"aggressive": 0.03, "balanced": 0.02, "conservative": 0.01}
MIN_RISK_FLOOR = 0.005
TRADE_COOLDOWN_CANDLES = 8

# Regime SL/TP adjustment: sourced from params.py
REGIME_ADJUST_ENABLED = _PARAMS_REGIME_ADJUST
REGIME_ATR_EXPAND_THRESHOLD = _PARAMS_REGIME_EXPAND
REGIME_ATR_CONTRACT_THRESHOLD = _PARAMS_REGIME_CONTRACT

# Kelly: per-strategy min trade thresholds
_KELLY_MIN_TRADES = {
    "range": KELLY_MIN_TRADES_RANGE,
    "trend": KELLY_MIN_TRADES_TREND,
    "crash": KELLY_MIN_TRADES_CRASH,
}

# Confidence gates: sourced from params.py SIGNAL_CONF_GATE
_STRATEGY_CONF_GATE = {
    "range": _PARAMS_CONF_GATE.get("range", 0.40),
    "trend": _PARAMS_CONF_GATE.get("trend", 0.48),
    "crash": _PARAMS_CONF_GATE.get("crash", 0.33),
    "burst": 0.35, "newarch": 0.50,
}

# Mode affinity: sourced from params.py SIGNAL_MODE_AFFINITY
_MODE_AFFINITY = {}
for _mode, _strats in _PARAMS_MODE_AFFINITY.items():
    _MODE_AFFINITY[_mode] = dict(_strats)
    _MODE_AFFINITY[_mode].setdefault("burst", -0.05)
    _MODE_AFFINITY[_mode].setdefault("newarch", 0.0)
    if "squeeze" in _MODE_AFFINITY[_mode] and "burst" not in _strats:
        _MODE_AFFINITY[_mode]["burst"] = _MODE_AFFINITY[_mode].pop("squeeze")

_MODE_DEFAULT_PENALTY = dict(_PARAMS_MODE_DEFAULT_PENALTY)
_MODE_DEFAULT_PENALTY.setdefault("burst", -0.05)
_MODE_DEFAULT_PENALTY.setdefault("newarch", 0.0)
if "squeeze" in _MODE_DEFAULT_PENALTY and "burst" not in _PARAMS_MODE_DEFAULT_PENALTY:
    _MODE_DEFAULT_PENALTY["burst"] = _MODE_DEFAULT_PENALTY.pop("squeeze")


# ─── Helper ───
def _get_size_tier(confidence: float) -> float:
    """Map confidence to position size tier (matches production position_sizer.py)."""
    if confidence >= 0.7:
        return 1.0
    elif confidence >= 0.5:
        return 0.7
    else:
        return 0.5


# ─── Dataclasses ───
@dataclass
class BTPosition:
    """Backtest position tracker."""
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    notional: float
    entry_time: str
    strategy: str
    vol_regime: str = "NORMAL"
    market_mode: str = "UNKNOWN"
    confidence: float = 0.0
    tp_source: str = "min_rr"
    atr_at_entry: float = 0.0
    regime_adjusted: bool = False
    hfe: float = 0.0


@dataclass
class BTTrade:
    """Completed trade record (compatible with metrics.py _load_trades())."""
    symbol: str
    side: str
    entry: float
    exit: float
    pnl: float
    sl_price: float
    tp_price: float
    entry_time: str
    exit_time: str
    exit_reason: str
    strategy: str
    vol_regime: str = "NORMAL"
    market_mode: str = "UNKNOWN"
    confidence: float = 0.0
    tp_source: str = "min_rr"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "side": self.side,
            "entry": round(self.entry, 6), "exit": round(self.exit, 6),
            "pnl": round(self.pnl, 2),
            "sl_price": round(self.sl_price, 6), "tp_price": round(self.tp_price, 6),
            "entry_time": self.entry_time, "exit_time": self.exit_time,
            "exit_reason": self.exit_reason, "strategy": self.strategy,
            "vol_regime": self.vol_regime, "market_mode": self.market_mode,
            "confidence": round(self.confidence, 4), "tp_source": self.tp_source,
        }

    def to_jsonl(self) -> str:
        return json.dumps({
            "symbol": self.symbol, "side": self.side,
            "entry": round(self.entry, 6), "exit": round(self.exit, 6),
            "pnl": round(self.pnl, 2),
            "sl_price": round(self.sl_price, 6), "tp_price": round(self.tp_price, 6),
            "entry_time": self.entry_time, "exit_time": self.exit_time,
            "exit_reason": self.exit_reason, "strategy": self.strategy,
            "vol_regime": self.vol_regime, "market_mode": self.market_mode,
            "confidence": round(self.confidence, 4), "tp_source": self.tp_source,
            "ts": self.entry_time, "closed": True,
        }, ensure_ascii=False)


@dataclass
class _PendingSignal:
    """Signal generated at candle i, to be executed after signal_delay candles."""
    direction: str
    strategy: str
    atr: float
    signal_time: str
    score: float = 0.0
    confidence: float = 0.0
    remaining_delay: int = 1
    bb_basis: float = 0.0
    atr_4h: float = 0.0
