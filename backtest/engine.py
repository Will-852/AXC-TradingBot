#!/usr/bin/env python3
"""
engine.py — Backtest 核心模擬器

設計決定：
  - 1H tick clock + 4H MTF sync（同 production 一致）
  - Reuse production calc_indicators / detect_mode / strategies
  - SL/TP 用 candle high/low，同根 candle 兩個都 hit → SL 先（保守）
  - Commission 0.05% × 2（entry + exit）+ SL slippage 0.02%
  - Mode confirmation 需要 2 consecutive same-mode
  - Signal 在 candle i 生成，entry 在 candle i+1 open 執行（無 look-ahead）

Scope boundary（唔做）：
  - Funding rate / Trailing SL / Partial TP / News / Multi-symbol / Re-entry
"""

import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ─── Path setup for production code reuse ───
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from indicator_calc import calc_indicators, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES
from trader_cycle.strategies.mode_detector import detect_mode_for_pair
from trader_cycle.strategies.range_strategy import RangeStrategy
from trader_cycle.strategies.trend_strategy import TrendStrategy
from trader_cycle.strategies.crash_strategy import CrashStrategy
from trader_cycle.strategies.regime_hmm import RegimeHMM
from trader_cycle.strategies.regime_bocpd import RegimeBOCPD
from backtest.strategies.bt_burst_strategy import BTBurstStrategy
from trader_cycle.risk.atr_conformal import ATRConformal
from trader_cycle.core.context import CycleContext
from config.params import get_regime_rule
from trader_cycle.config.settings import (
    MODE_CONFIRMATION_REQUIRED,
    MAX_CRYPTO_POSITIONS,
    HMM_ENABLED, HMM_N_STATES, HMM_WINDOW,
    HMM_REFIT_INTERVAL, HMM_MIN_SAMPLES, HMM_CRASH_THRESHOLD,
    REGIME_ENGINE,
    BOCPD_HAZARD_RATE, BOCPD_MAX_RUN_LENGTH,
    BOCPD_MIN_SAMPLES, BOCPD_CHANGEPOINT_THRESHOLD,
    CP_ENABLED, CP_ALPHA, CP_MIN_SCORES, CP_MAX_SCORES,
    CP_INFLATION_FACTOR, CP_FALLBACK_MULT,
    KELLY_WINDOW_N, KELLY_MIN_RISK, KELLY_MAX_RISK, KELLY_NO_EDGE,
    KELLY_MIN_TRADES_RANGE, KELLY_MIN_TRADES_TREND, KELLY_MIN_TRADES_CRASH,
)

log = logging.getLogger(__name__)

WARMUP_CANDLES = 200
COMMISSION_RATE = 0.0005   # 0.05% per side
SL_SLIPPAGE_PCT = 0.0002   # 0.02% adverse slippage on SL (market order)
CLUSTER_GAP_HOURS = 4      # trades < N hours apart = same cluster
MAX_RISK_PCT = 0.05        # hard cap: never risk >5% per trade (防止 optimizer 「全部加大注碼」)

# Persistence threshold: signal must appear N consecutive candles before acknowledged.
# Different from signal_delay (delays execution of an already-acknowledged signal).
# Per-strategy: crash=0 (immediate), range/trend=2 (need 2 consecutive same-direction signals).
PERSISTENCE_THRESHOLD = {"range": 3, "trend": 4, "crash": 1, "burst": 1, "newarch": 1}

# ─── New architecture: volatility regime → risk profile ───
_VOL_PROFILE_MAP = {"LOW": "balanced", "NORMAL": "balanced", "HIGH": "conservative"}
_VOL_DOWNGRADE = {"LOW": "NORMAL", "NORMAL": "HIGH", "HIGH": "HIGH"}
_PROFILE_RISK = {"aggressive": 0.03, "balanced": 0.02, "conservative": 0.01}
MIN_RISK_FLOOR = 0.005  # 0.5% absolute minimum risk
TRADE_COOLDOWN_CANDLES = 8  # wait 8 candles (8h) after closing a position before entering another

# ─── Regime SL/TP adjustment: recalibrate on vol regime change ───
REGIME_ADJUST_ENABLED = True
REGIME_ATR_EXPAND_THRESHOLD = 1.3   # ATR ratio > 1.3 = vol expanding >30%
REGIME_ATR_CONTRACT_THRESHOLD = 0.7  # ATR ratio < 0.7 = vol contracting >30%

# ─── Kelly: per-strategy min trade thresholds ───
_KELLY_MIN_TRADES = {
    "range": KELLY_MIN_TRADES_RANGE,
    "trend": KELLY_MIN_TRADES_TREND,
    "crash": KELLY_MIN_TRADES_CRASH,
}

# Per-strategy confidence gates
_STRATEGY_CONF_GATE = {"range": 0.50, "trend": 0.50, "crash": 0.50, "burst": 0.35, "newarch": 0.50}

# Soft mode penalty: when mode doesn't match strategy affinity, penalize confidence
# Strong penalty for trend (most false-positive prone) to approximate mode gate filtering
_MODE_AFFINITY = {
    "TREND": {"trend": 0.0, "range": -0.20, "crash": 0.0, "burst": -0.05, "newarch": 0.0},
    "RANGE": {"range": 0.0, "trend": -0.30, "crash": 0.0, "burst": -0.05, "newarch": 0.0},
    "CRASH": {"crash": 0.0, "trend": -0.20, "range": -0.30, "burst": -0.15, "newarch": 0.0},
}
_MODE_DEFAULT_PENALTY = {"trend": -0.25, "range": -0.10, "crash": 0.0, "burst": -0.05, "newarch": 0.0}


def _get_size_tier(confidence: float) -> float:
    """Map confidence to position size tier (matches production position_sizer.py)."""
    if confidence >= 0.7:
        return 1.0
    elif confidence >= 0.5:
        return 0.7
    else:
        return 0.5


@dataclass
class BTPosition:
    """Backtest position tracker."""
    direction: str      # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp_price: float
    notional: float     # position size in USDT
    entry_time: str
    strategy: str       # "range", "trend", "crash", "burst", or "newarch"
    vol_regime: str = "NORMAL"   # LOW / NORMAL / HIGH at entry
    market_mode: str = "UNKNOWN" # RANGE / TREND / CRASH at entry
    confidence: float = 0.0      # signal confidence at entry
    tp_source: str = "min_rr"    # TP calculation method: "bb_mid" / "atr_3.5" / "min_rr"
    atr_at_entry: float = 0.0   # ATR at entry time (for newarch trailing stop)
    regime_adjusted: bool = False  # one-shot flag: SL/TP recalibrated on regime change
    hfe: float = 0.0            # highest favorable excursion in price units


@dataclass
class BTTrade:
    """Completed trade record (compatible with metrics.py _load_trades())."""
    symbol: str
    side: str           # "LONG" or "SHORT"
    entry: float
    exit: float
    pnl: float
    sl_price: float
    tp_price: float
    entry_time: str
    exit_time: str
    exit_reason: str    # "SL", "TP", or "END"
    strategy: str
    vol_regime: str = "NORMAL"   # LOW / NORMAL / HIGH at entry
    market_mode: str = "UNKNOWN" # RANGE / TREND / CRASH at entry
    confidence: float = 0.0      # signal confidence at entry
    tp_source: str = "min_rr"    # TP calculation method: "bb_mid" / "atr_3.5" / "min_rr"

    def to_dict(self) -> dict:
        """Serialize all fields for dashboard API and analysis."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry": round(self.entry, 6),
            "exit": round(self.exit, 6),
            "pnl": round(self.pnl, 2),
            "sl_price": round(self.sl_price, 6),
            "tp_price": round(self.tp_price, 6),
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "exit_reason": self.exit_reason,
            "strategy": self.strategy,
            "vol_regime": self.vol_regime,
            "market_mode": self.market_mode,
            "confidence": round(self.confidence, 4),
            "tp_source": self.tp_source,
        }

    def to_jsonl(self) -> str:
        """Format for analysis: full trade record including regime context."""
        return json.dumps({
            "symbol": self.symbol,
            "side": self.side,
            "entry": round(self.entry, 6),
            "exit": round(self.exit, 6),
            "pnl": round(self.pnl, 2),
            "sl_price": round(self.sl_price, 6),
            "tp_price": round(self.tp_price, 6),
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "exit_reason": self.exit_reason,
            "strategy": self.strategy,
            "vol_regime": self.vol_regime,
            "market_mode": self.market_mode,
            "confidence": round(self.confidence, 4),
            "tp_source": self.tp_source,
            "ts": self.entry_time,
            "closed": True,
        }, ensure_ascii=False)


@dataclass
class _PendingSignal:
    """Signal generated at candle i, to be executed after signal_delay candles."""
    direction: str
    strategy: str
    atr: float
    signal_time: str
    score: float = 0.0        # signal score for confidence-based sizing + filtering
    confidence: float = 0.0   # signal confidence (0-1) for size_tier calculation
    remaining_delay: int = 1  # candles until execution (1 = next candle = default)
    bb_basis: float = 0.0     # 1H BB mid for Range TP (captured at signal time)
    atr_4h: float = 0.0       # 4H ATR for Crash TP (captured at signal time)


class BacktestEngine:
    """
    Candle-by-candle backtester using production strategies.

    Key design:
      - Signal at candle i close → entry at candle i+1 open (no look-ahead)
      - SL exit: slippage applied (market order). TP exit: exact price (limit order)
      - Cluster-adjusted stats alongside raw stats
      - param_overrides: override TIMEFRAME_PARAMS keys without monkey-patching
    """

    def __init__(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        initial_balance: float = 10000.0,
        commission_rate: float = COMMISSION_RATE,
        sl_slippage_pct: float = SL_SLIPPAGE_PCT,
        allowed_modes: list[str] | None = None,
        param_overrides: dict | None = None,
        strategy_overrides: dict | None = None,
        mode_confirmation: int | None = None,
        signal_delay: int = 1,
        min_score: float = 0.0,
        scorer=None,
        quiet: bool = False,
        tuning_params: dict | None = None,
    ):
        self.symbol = symbol.upper()
        self.df_1h = df_1h.reset_index(drop=True)
        self.df_4h = df_4h.reset_index(drop=True)
        self.allowed_modes = allowed_modes
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.commission_rate = commission_rate
        self.sl_slippage_pct = sl_slippage_pct
        self.signal_delay = max(1, signal_delay)  # minimum 1 (current behavior)
        self.min_score = min_score  # signal score below this → discard
        self._scorer = scorer       # WeightedScorer for confidence-based sizing (optional)
        self.quiet = quiet

        # ── Per-asset tuning params (optimizer injects these) ──
        tp = tuning_params or {}
        self._conf_gate = {
            "range": tp.get("conf_gate_range", _STRATEGY_CONF_GATE["range"]),
            "trend": tp.get("conf_gate_trend", _STRATEGY_CONF_GATE["trend"]),
            "crash": tp.get("conf_gate_crash", _STRATEGY_CONF_GATE["crash"]),
            "burst": tp.get("conf_gate_burst", _STRATEGY_CONF_GATE["burst"]),
            "newarch": tp.get("conf_gate_newarch", _STRATEGY_CONF_GATE["newarch"]),
        }
        self._mode_affinity = {
            "TREND": {
                "trend": 0.0,
                "range": tp.get("mode_pen_range_in_trend", _MODE_AFFINITY["TREND"]["range"]),
                "crash": 0.0,
                "burst": tp.get("mode_pen_burst_in_trend", _MODE_AFFINITY["TREND"]["burst"]),
            },
            "RANGE": {
                "range": 0.0,
                "trend": tp.get("mode_pen_trend_in_range", _MODE_AFFINITY["RANGE"]["trend"]),
                "crash": 0.0,
                "burst": tp.get("mode_pen_burst_in_range", _MODE_AFFINITY["RANGE"]["burst"]),
            },
            "CRASH": {
                "crash": 0.0,
                "trend": tp.get("mode_pen_trend_in_crash", _MODE_AFFINITY["CRASH"]["trend"]),
                "range": tp.get("mode_pen_range_in_crash", _MODE_AFFINITY["CRASH"]["range"]),
                "burst": tp.get("mode_pen_burst_in_crash", _MODE_AFFINITY["CRASH"]["burst"]),
            },
        }
        self._mode_default_penalty = {
            "trend": tp.get("mode_pen_default_trend", _MODE_DEFAULT_PENALTY["trend"]),
            "range": tp.get("mode_pen_default_range", _MODE_DEFAULT_PENALTY["range"]),
            "crash": 0.0,
            "burst": tp.get("mode_pen_default_burst", _MODE_DEFAULT_PENALTY["burst"]),
        }
        self._persistence = {
            "range": tp.get("persist_range", PERSISTENCE_THRESHOLD["range"]),
            "trend": tp.get("persist_trend", PERSISTENCE_THRESHOLD["trend"]),
            "crash": tp.get("persist_crash", PERSISTENCE_THRESHOLD["crash"]),
            "burst": tp.get("persist_burst", PERSISTENCE_THRESHOLD["burst"]),
            "newarch": tp.get("persist_newarch", PERSISTENCE_THRESHOLD["newarch"]),
        }
        self._cooldown = tp.get("cooldown", TRADE_COOLDOWN_CANDLES)

        # Indicator params with product overrides + backtest overrides
        self.param_overrides = param_overrides or {}
        self.params_1h = self._build_params("1h")
        self.params_4h = self._build_params("4h")

        # Mode confirmation override (for optimizer: 1 or 2)
        self._mode_confirmation = mode_confirmation or MODE_CONFIRMATION_REQUIRED

        # Strategies — injectable for optimizer
        strats = strategy_overrides or {}
        self.range_strategy = strats.get("range", RangeStrategy())
        self.trend_strategy = strats.get("trend", TrendStrategy())
        self.crash_strategy = strats.get("crash", CrashStrategy())
        # Burst strategy: disabled by default. Enable via strategy_overrides.
        # Tested on XRP 360d: neither continuation nor fade improved results.
        # Volume spikes on XRP don't carry reliable directional signal.
        self.burst_strategy = strats.get("burst", None)
        # NewArch strategy: 5-layer architecture validation. Disabled by default.
        self.newarch_strategy = strats.get("newarch", None)

        # Regime engine (per-run independent instances, no persistence across backtests)
        self.regime_engine = REGIME_ENGINE
        self.hmm_enabled = HMM_ENABLED
        self._hmm: RegimeHMM | None = None
        self._bocpd: RegimeBOCPD | None = None

        if self.regime_engine == "bocpd_cp":
            self._bocpd = RegimeBOCPD(
                hazard_rate=BOCPD_HAZARD_RATE,
                max_run_length=BOCPD_MAX_RUN_LENGTH,
                min_samples=BOCPD_MIN_SAMPLES,
                changepoint_threshold=BOCPD_CHANGEPOINT_THRESHOLD,
            )
        elif self.hmm_enabled:
            self._hmm = RegimeHMM(
                n_states=HMM_N_STATES,
                window=HMM_WINDOW,
                refit_interval=HMM_REFIT_INTERVAL,
                min_samples=HMM_MIN_SAMPLES,
            )

        # Conformal Prediction (optional, independent of engine)
        self.cp_enabled = CP_ENABLED
        self._cp: ATRConformal | None = None
        if self.cp_enabled:
            self._cp = ATRConformal(
                alpha=CP_ALPHA,
                min_scores=CP_MIN_SCORES,
                max_scores=CP_MAX_SCORES,
                inflation_factor=CP_INFLATION_FACTOR,
                fallback_mult=CP_FALLBACK_MULT,
            )

        # State
        self.positions: list[BTPosition] = []
        self.trades: list[BTTrade] = []
        self.equity_curve: list[dict] = []
        self.indicator_series: list[dict] = []
        self._pending_signal: _PendingSignal | None = None

        # Persistence threshold tracking
        self._persist_strategy: str | None = None  # last signal's strategy
        self._persist_direction: str | None = None  # last signal's direction
        self._persist_count: int = 0                 # consecutive candles with same signal

        # Trade cooldown: skip N candles after closing a position
        self._cooldown_remaining: int = 0

        # Diagnostic: track confidence of signals that led to trades
        self._trade_confidences: list[tuple[str, float]] = []  # (strategy, confidence)

        # Mode tracking
        self.current_mode = "UNKNOWN"
        self.mode_confirmed = False
        self.prev_mode = "UNKNOWN"
        self.prev_mode_cycles = 0

        # Volatility regime tracking (new architecture)
        self.volatility_regime = "NORMAL"
        self.vol_confidence = 0.0
        self.active_risk_profile = "balanced"

        # 4H tracking
        self._last_4h_idx = -1
        self._ind_4h: dict = {}

    def _build_params(self, timeframe: str) -> dict:
        """Build indicator params: TIMEFRAME_PARAMS + PRODUCT_OVERRIDES + backtest overrides."""
        params = TIMEFRAME_PARAMS[timeframe].copy()
        if self.symbol in PRODUCT_OVERRIDES:
            params.update(PRODUCT_OVERRIDES[self.symbol])
        # Apply backtest-specific overrides (e.g. bb_touch_tol, adx_range_max)
        params.update(self.param_overrides)
        return params

    def run(self) -> dict:
        """
        Run backtest. Returns summary dict.

        Main loop per 1H candle:
          1. Check 4H boundary → update 4H indicators + mode
          2. Execute pending signal from previous candle (entry at this candle's open)
          3. Check SL/TP on open positions (candle high/low)
          4. Calc 1H indicators (rolling 200-candle window)
          5. If mode confirmed + slots available → strategy.evaluate()
          6. If signal → store as pending (will execute next candle)
          7. Record equity (mark-to-market)
        """
        total_1h = len(self.df_1h)
        min_candles = WARMUP_CANDLES + 10
        if total_1h < min_candles:
            raise ValueError(f"Not enough 1H data: {total_1h} (need {min_candles}+)")

        close_times_4h = self.df_4h["close_time"].astype(int).values

        test_candles = total_1h - WARMUP_CANDLES
        if not self.quiet:
            print(f"\n  Running: {self.symbol}")
            print(f"  1H: {total_1h} candles (warmup={WARMUP_CANDLES}, test={test_candles})")
            print(f"  4H: {len(self.df_4h)} candles")
            print(f"  Balance: ${self.initial_balance:,.0f}")

        # Apply param_overrides to indicator_calc module for RangeStrategy
        # (it reads TIMEFRAME_PARAMS directly inside evaluate())
        _patched_keys = {}     # key → original value (SENTINEL if key didn't exist)
        _SENTINEL = object()
        if self.param_overrides:
            import indicator_calc as _ic
            for key in ["bb_touch_tol", "adx_range_max", "bb_width_squeeze"]:
                if key in self.param_overrides:
                    _patched_keys[key] = TIMEFRAME_PARAMS["1h"].get(key, _SENTINEL)
                    TIMEFRAME_PARAMS["1h"][key] = self.param_overrides[key]
            if "bb_width_min" in self.param_overrides:
                _patched_keys["_BB_WIDTH_MIN"] = _ic.BB_WIDTH_MIN
                _ic.BB_WIDTH_MIN = self.param_overrides["bb_width_min"]

            # Patch mode_detector RSI thresholds (imported as module attrs)
            import trader_cycle.strategies.mode_detector as _md
            for md_key in ("mode_rsi_trend_low", "mode_rsi_trend_high"):
                if md_key in self.param_overrides:
                    attr = md_key.upper()  # MODE_RSI_TREND_LOW / HIGH
                    _patched_keys[f"_md_{attr}"] = getattr(_md, attr)
                    setattr(_md, attr, self.param_overrides[md_key])

        try:
            self._run_loop(total_1h, close_times_4h)
        finally:
            if _patched_keys:
                import indicator_calc as _ic
                import trader_cycle.strategies.mode_detector as _md
                for key, orig in _patched_keys.items():
                    if key == "_BB_WIDTH_MIN":
                        _ic.BB_WIDTH_MIN = orig
                    elif key.startswith("_md_"):
                        setattr(_md, key[4:], orig)  # strip "_md_" prefix
                    elif orig is _SENTINEL:
                        TIMEFRAME_PARAMS["1h"].pop(key, None)
                    else:
                        TIMEFRAME_PARAMS["1h"][key] = orig

        return self._summary()

    def _run_loop(self, total_1h: int, close_times_4h):
        """Core backtest loop (separated for clean try/finally)."""
        for i in range(WARMUP_CANDLES, total_1h):
            candle = self.df_1h.iloc[i]
            ts_1h = int(candle["open_time"])
            candle_time = str(candle["timestamp"])

            # ── Step 1: 4H boundary ──
            completed_4h_idx = int(
                np.searchsorted(close_times_4h, ts_1h, side="right")
            ) - 1

            if completed_4h_idx >= 0 and completed_4h_idx != self._last_4h_idx:
                self._update_4h(completed_4h_idx)
                self._last_4h_idx = completed_4h_idx

            # ── Step 2: Execute pending signal (after delay countdown) ──
            if self._pending_signal is not None:
                self._pending_signal.remaining_delay -= 1
                if self._pending_signal.remaining_delay <= 0:
                    self._execute_pending(candle, candle_time)

            # ── Step 3: Check SL/TP ──
            self._check_sl_tp(candle, candle_time)

            # ── Step 3b: Trailing stop for newarch positions ──
            # DISABLED v3: breakeven at 1R kills mean reversion trades.
            # Price oscillates in ranging regime → SL at entry = instant stop out.
            # TODO: re-enable only for trend_pullback entries after adding entry_type to BTPosition.
            # if self.newarch_strategy is not None:
            #     self._check_trailing(candle)

            # ── Step 4: Calc 1H indicators ──
            start_idx = max(0, i - WARMUP_CANDLES + 1)
            slice_1h = self.df_1h.iloc[start_idx:i + 1].reset_index(drop=True)

            if len(slice_1h) < 50:
                self._record_equity(candle)
                continue

            ind_1h = calc_indicators(slice_1h, self.params_1h)

            # Volume ratio
            if len(slice_1h) >= 30:
                avg_vol = float(slice_1h["volume"].tail(30).mean())
                cur_vol = float(slice_1h["volume"].iloc[-1])
                ind_1h["volume_ratio"] = cur_vol / avg_vol if avg_vol > 0 else 1.0
            else:
                ind_1h["volume_ratio"] = 1.0

            # prev_close: previous candle's close (for burst strategy price_change calc)
            if len(slice_1h) >= 2:
                ind_1h["prev_close"] = float(slice_1h["close"].iloc[-2])
            else:
                ind_1h["prev_close"] = None

            # ── Collect indicator snapshot for frontend ──
            self.indicator_series.append({
                "time": candle_time,
                "bb_upper": ind_1h.get("bb_upper"),
                "bb_lower": ind_1h.get("bb_lower"),
                "bb_basis": ind_1h.get("bb_basis"),
                "rsi": ind_1h.get("rsi"),
                "adx": ind_1h.get("adx"),
                "atr": ind_1h.get("atr"),
                "ema_fast": ind_1h.get("ema_fast"),
                "ema_slow": ind_1h.get("ema_slow"),
                "ma50": ind_1h.get("ma50"),
                "ma200": ind_1h.get("ma200"),
                "macd_line": ind_1h.get("macd_line"),
                "macd_signal": ind_1h.get("macd_signal"),
                "macd_hist": ind_1h.get("macd_hist"),
                "stoch_k": ind_1h.get("stoch_k"),
                "stoch_d": ind_1h.get("stoch_d"),
                "volume_ratio": ind_1h.get("volume_ratio"),
                "vwap": ind_1h.get("vwap"),
                "vwap_upper": ind_1h.get("vwap_upper"),
                "vwap_lower": ind_1h.get("vwap_lower"),
                "mode": self.current_mode,
                "vol_regime": self.volatility_regime,
            })

            # ── Burst strategy internal cooldown (separate from trade cooldown) ──
            if self.burst_strategy is not None:
                self.burst_strategy.tick_cooldown()

            # ── Step 5-6: Strategy evaluation → pending signal ──
            # Cooldown: decrement and skip if still cooling down
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
            else:
                mode_allowed = (
                    self.allowed_modes is None
                    or self.current_mode in self.allowed_modes
                )
                if (
                    self.mode_confirmed
                    and mode_allowed
                    and len(self.positions) < MAX_CRYPTO_POSITIONS
                    and self._ind_4h
                    and self._pending_signal is None  # don't overwrite pending
                ):
                    self._try_signal(ind_1h, candle, candle_time)

            # ── Step 7: Record equity ──
            self._record_equity(candle)

        # Force-close remaining positions at last candle's close
        if self.positions:
            last = self.df_1h.iloc[-1]
            last_time = str(last["timestamp"])
            for pos in list(self.positions):
                self._close_position(pos, float(last["close"]), last_time, "END")

    def _update_4h(self, idx_4h: int):
        """Recalculate 4H indicators and run mode detection."""
        start = max(0, idx_4h - WARMUP_CANDLES + 1)
        slice_4h = self.df_4h.iloc[start:idx_4h + 1].reset_index(drop=True)

        if len(slice_4h) < 50:
            return

        self._ind_4h = calc_indicators(slice_4h, self.params_4h)

        # Volume ratio
        if len(slice_4h) >= 30:
            avg_vol = float(slice_4h["volume"].tail(30).mean())
            cur_vol = float(slice_4h["volume"].iloc[-1])
            self._ind_4h["volume_ratio"] = cur_vol / avg_vol if avg_vol > 0 else 1.0
        else:
            self._ind_4h["volume_ratio"] = 1.0

        # ── Regime engine update + volatility regime ──
        hmm_regime = None
        hmm_confidence = 0.0
        hmm_crash_confirmed = False
        vol_regime = "NORMAL"
        vol_confidence = 0.0

        if self._bocpd is not None:
            try:
                hmm_regime, hmm_confidence, hmm_crash_confirmed = self._bocpd.update(self._ind_4h)
                vol_regime, vol_confidence = self._bocpd.get_volatility_regime()
            except Exception as e:
                log.warning("BOCPD update failed in backtest: %s", e)
        elif self._hmm is not None:
            try:
                hmm_regime, hmm_confidence, hmm_crash_confirmed = self._hmm.update(self._ind_4h)
                vol_regime, vol_confidence = self._hmm.get_volatility_regime()
            except Exception as e:
                log.warning("HMM update failed in backtest: %s", e)

        # Voter brake check: run old voters, ≥3 TREND votes → downgrade one level
        raw_mode, _votes = detect_mode_for_pair(
            self._ind_4h, 0.0, hmm_regime, hmm_confidence, hmm_crash_confirmed
        )
        if _votes:
            trend_voters = sum(1 for v in _votes.values() if v == "TREND")
            if trend_voters >= 3 and vol_regime != "HIGH":
                vol_regime = _VOL_DOWNGRADE[vol_regime]

        # CRASH override: force HIGH regime
        if (
            hmm_regime == "CRASH"
            and hmm_crash_confirmed
            and hmm_confidence >= HMM_CRASH_THRESHOLD
        ):
            vol_regime = "HIGH"
            vol_confidence = hmm_confidence

        # Update volatility regime state (direct assignment, no hysteresis)
        prev_regime = self.volatility_regime
        self.volatility_regime = vol_regime
        self.vol_confidence = vol_confidence
        self.active_risk_profile = _VOL_PROFILE_MAP.get(vol_regime, "balanced")

        # Regime SL/TP adjustment: recalibrate open positions on regime change
        self._adjust_regime_sl_tp(prev_regime, hmm_crash_confirmed)

        # Mode tracking (for indicator_series / audit trail)
        self.current_mode = raw_mode if raw_mode != "UNKNOWN" else self.current_mode
        # New architecture: mode_confirmed = always True except cold start
        self.mode_confirmed = vol_confidence > 0

        # CP update (every 4H candle, not just on trade execution)
        self._update_cp_backtest()

    def _adjust_regime_sl_tp(self, prev_regime: str, crash_confirmed: bool):
        """One-shot SL/TP recalibration when vol regime changes mid-trade.

        Why: LOW vol entries use tight SL (low ATR). When vol expands, the tight SL
        gets hit → big notional × small SL = outsized dollar loss. Recalibrate to
        match the new vol environment.

        Rules:
          - Expanding (ATR ratio > 1.3): SL→breakeven if profitable, tighten TP
          - Contracting (ATR ratio < 0.7): tighten both SL and TP
          - SL only moves in protective direction (forward), never backward
          - One-shot per position (regime_adjusted flag)
          - CRASH override: skip — crash has its own handling
        """
        if not REGIME_ADJUST_ENABLED:
            return
        if self.volatility_regime == prev_regime:
            return
        if not self.positions:
            return
        # Skip CRASH — handled separately
        if self.volatility_regime == "HIGH" and crash_confirmed:
            return

        current_atr = self._ind_4h.get("atr", 0.0)
        if current_atr <= 0:
            return

        for pos in self.positions:
            if pos.regime_adjusted:
                continue
            if pos.atr_at_entry <= 0:
                continue

            atr_ratio = current_atr / pos.atr_at_entry
            entry = pos.entry_price

            if atr_ratio > REGIME_ATR_EXPAND_THRESHOLD:
                # EXPANDING: protect profits, tighten TP
                if pos.direction == "LONG":
                    current_price = float(self.df_1h.iloc[-1]["close"])
                    if current_price > entry:
                        pos.sl_price = max(pos.sl_price, entry)  # breakeven
                    original_tp_dist = abs(pos.tp_price - entry)
                    new_tp_dist = original_tp_dist / (atr_ratio ** 0.5)
                    pos.tp_price = entry + new_tp_dist
                else:  # SHORT
                    current_price = float(self.df_1h.iloc[-1]["close"])
                    if current_price < entry:
                        pos.sl_price = min(pos.sl_price, entry)  # breakeven
                    original_tp_dist = abs(entry - pos.tp_price)
                    new_tp_dist = original_tp_dist / (atr_ratio ** 0.5)
                    pos.tp_price = entry - new_tp_dist

            elif atr_ratio < REGIME_ATR_CONTRACT_THRESHOLD:
                # CONTRACTING: tighten both SL and TP
                original_sl_dist = abs(entry - pos.sl_price)
                new_sl_dist = original_sl_dist * atr_ratio
                original_tp_dist = abs(pos.tp_price - entry)
                new_tp_dist = original_tp_dist * atr_ratio
                if pos.direction == "LONG":
                    candidate_sl = entry - new_sl_dist
                    pos.sl_price = max(pos.sl_price, candidate_sl)  # forward only
                    pos.tp_price = entry + new_tp_dist
                else:
                    candidate_sl = entry + new_sl_dist
                    pos.sl_price = min(pos.sl_price, candidate_sl)  # forward only
                    pos.tp_price = entry - new_tp_dist

            pos.regime_adjusted = True
            log.info(
                "Regime SL/TP adjust [%s %s]: %s→%s atr_ratio=%.2f "
                "SL=%.4f TP=%.4f",
                pos.direction, pos.strategy, prev_regime,
                self.volatility_regime, atr_ratio,
                pos.sl_price, pos.tp_price,
            )

    def _update_cp_backtest(self):
        """Update CP calibration every 4H candle (not just on trade execution)."""
        if self._cp is None or not self._ind_4h:
            return
        try:
            atr = self._ind_4h.get("atr")
            if not atr or atr <= 0:
                return
            true_range = self._ind_4h.get("high", 0) - self._ind_4h.get("low", 0)
            if true_range <= 0:
                true_range = atr
            self._cp.update(regime=self.current_mode, atr=atr, true_range=true_range)
        except Exception as e:
            log.warning("CP update failed in backtest: %s", e)

    def _check_sl_tp(self, candle, candle_time: str):
        """Check SL/TP. SL = market order (slippage applied). TP = limit (exact)."""
        high = float(candle["high"])
        low = float(candle["low"])
        to_close = []

        for pos in self.positions:
            if pos.direction == "LONG":
                sl_hit = low <= pos.sl_price
                tp_hit = high >= pos.tp_price
            else:
                sl_hit = high >= pos.sl_price
                tp_hit = low <= pos.tp_price

            if sl_hit and tp_hit:
                to_close.append((pos, self._sl_fill_price(pos), "SL"))
            elif sl_hit:
                to_close.append((pos, self._sl_fill_price(pos), "SL"))
            elif tp_hit:
                to_close.append((pos, pos.tp_price, "TP"))

        for pos, exit_price, reason in to_close:
            self._close_position(pos, exit_price, candle_time, reason)

    def _sl_fill_price(self, pos: BTPosition) -> float:
        """SL fill with adverse slippage (market order)."""
        if pos.direction == "LONG":
            return pos.sl_price * (1 - self.sl_slippage_pct)
        else:
            return pos.sl_price * (1 + self.sl_slippage_pct)

    def _check_trailing(self, candle):
        """Trailing stop for newarch positions: breakeven at 1R, trail at 2R.

        Called per-candle AFTER _check_sl_tp (so only surviving positions are trailed).
        Modifies pos.sl_price in-place — next candle's _check_sl_tp will catch it.
        """
        high = float(candle["high"])
        low = float(candle["low"])

        for pos in self.positions:
            if pos.strategy != "newarch" or pos.atr_at_entry <= 0:
                continue

            atr = pos.atr_at_entry
            sl_dist = atr * 1.5  # matches sl_atr_mult=1.5

            # Update HFE (highest favorable excursion)
            if pos.direction == "LONG":
                excursion = high - pos.entry_price
            else:
                excursion = pos.entry_price - low
            pos.hfe = max(pos.hfe, excursion)

            r_multiple = pos.hfe / sl_dist if sl_dist > 0 else 0

            if r_multiple >= 2.0:
                # Trail: SL = entry + (HFE - 1×ATR)
                trail_offset = pos.hfe - atr
                if pos.direction == "LONG":
                    new_sl = pos.entry_price + trail_offset
                else:
                    new_sl = pos.entry_price - trail_offset
                # Only move SL forward, never backward
                if pos.direction == "LONG" and new_sl > pos.sl_price:
                    pos.sl_price = new_sl
                elif pos.direction == "SHORT" and new_sl < pos.sl_price:
                    pos.sl_price = new_sl
            elif r_multiple >= 1.0:
                # Breakeven: move SL to entry
                if pos.direction == "LONG" and pos.sl_price < pos.entry_price:
                    pos.sl_price = pos.entry_price
                elif pos.direction == "SHORT" and pos.sl_price > pos.entry_price:
                    pos.sl_price = pos.entry_price

    def _close_position(self, pos: BTPosition, exit_price: float, exit_time: str, reason: str):
        """Close position, calculate PnL, record trade."""
        if pos.direction == "LONG":
            raw_pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:
            raw_pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

        # Fee: truncate toward zero (int × 1e8 / 1e8), not round
        commission = int(pos.notional * self.commission_rate * 2 * 1e8) / 1e8
        pnl = pos.notional * raw_pnl_pct - commission
        self.balance += pnl

        if pos in self.positions:
            self.positions.remove(pos)

        # Trade cooldown: wait before next entry (prevent revenge trading)
        self._cooldown_remaining = self._cooldown

        self.trades.append(BTTrade(
            symbol=self.symbol,
            side=pos.direction,
            entry=pos.entry_price,
            exit=exit_price,
            pnl=round(pnl, 2),
            sl_price=pos.sl_price,
            tp_price=pos.tp_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            exit_reason=reason,
            strategy=pos.strategy,
            vol_regime=pos.vol_regime,
            market_mode=pos.market_mode,
            confidence=pos.confidence,
            tp_source=pos.tp_source,
        ))

    def _try_signal(self, ind_1h: dict, candle, candle_time: str):
        """Evaluate ALL strategies, pick best by confidence (no mode gate)."""
        indicators = {"4h": self._ind_4h, "1h": ind_1h}

        ts = candle["timestamp"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        ctx = CycleContext(
            timestamp=ts,
            market_mode=self.current_mode,
            mode_confirmed=self.mode_confirmed,
            volatility_regime=self.volatility_regime,
            active_risk_profile=self.active_risk_profile,
        )

        # ── Run ALL strategies (no mode gate) ──
        candidates = []
        all_strategies = [self.range_strategy, self.trend_strategy, self.crash_strategy]
        if self.burst_strategy is not None:
            all_strategies.append(self.burst_strategy)
        if self.newarch_strategy is not None:
            all_strategies.append(self.newarch_strategy)
        for strategy in all_strategies:
            sig = strategy.evaluate(self.symbol, indicators, ctx)
            if sig:
                # New architecture: soft mode penalty + per-strategy confidence gate
                # Old architecture: BT strategies leave confidence=0.0, accept all
                if sig.confidence > 0:
                    # Soft mode penalty: penalize strategies that don't match current mode
                    mode_penalties = self._mode_affinity.get(
                        self.current_mode, self._mode_default_penalty
                    )
                    penalty = mode_penalties.get(sig.strategy, 0.0) if isinstance(mode_penalties, dict) else mode_penalties
                    adjusted_conf = sig.confidence + penalty
                    gate = self._conf_gate.get(sig.strategy, 0.50)
                    if adjusted_conf < gate:
                        continue
                    sig.confidence = max(adjusted_conf, 0.0)
                candidates.append(sig)

        if not candidates:
            self._persist_count = 0
            return

        # Pick best: prefer confidence if set, fallback to score
        candidates.sort(
            key=lambda s: (s.confidence, s.score) if s.confidence > 0 else (0, s.score),
            reverse=True,
        )
        signal = candidates[0]

        # ── Regime-conditional filter (same rules as live signal_filter.py) ──
        rule = get_regime_rule(self.symbol, self.volatility_regime,
                               self.current_mode, signal.strategy)
        if rule == "BLOCK":
            self._persist_count = 0
            return
        if isinstance(rule, dict) and "conf_gate" in rule and signal.confidence > 0:
            if signal.confidence < rule["conf_gate"]:
                self._persist_count = 0
                return

        # Score-based filtering (relevant for old BT strategies with scorer)
        if signal.score < self.min_score:
            self._persist_count = 0
            return

        # Persistence threshold: same strategy+direction must persist N candles
        threshold = self._persistence.get(signal.strategy, 0)
        if threshold > 0:
            if (signal.strategy == self._persist_strategy
                    and signal.direction == self._persist_direction):
                self._persist_count += 1
            else:
                self._persist_strategy = signal.strategy
                self._persist_direction = signal.direction
                self._persist_count = 1

            if self._persist_count < threshold:
                return  # not yet persistent enough
        # Reset persistence on successful pass
        self._persist_count = 0

        atr = ind_1h.get("atr")
        if atr and atr > 0:
            self._pending_signal = _PendingSignal(
                direction=signal.direction,
                strategy=signal.strategy,
                atr=atr,
                signal_time=candle_time,
                score=signal.score,
                confidence=signal.confidence,
                remaining_delay=self.signal_delay,
                bb_basis=ind_1h.get("bb_basis", 0.0) or 0.0,
                atr_4h=self._ind_4h.get("atr", 0.0) if self._ind_4h else 0.0,
            )
            self._trade_confidences.append((signal.strategy, signal.confidence))

    def _calc_bt_kelly(self, strategy: str) -> float | None:
        """Per-strategy Kelly criterion using backtest closed trades.

        Replicates live kelly.py formula: win rate × payoff ratio → raw Kelly,
        CV correction, half-Kelly, clamped to [KELLY_MIN_RISK, KELLY_MAX_RISK].

        Returns:
          float > 0     → Kelly-derived risk cap
          KELLY_NO_EDGE → sufficient data but f*≤0 → block signal
          None          → insufficient data → use default risk
        """
        min_trades = _KELLY_MIN_TRADES.get(strategy, KELLY_MIN_TRADES_TREND)

        # Filter closed trades by strategy, take last KELLY_WINDOW_N
        matched = [t for t in self.trades if t.strategy == strategy]
        recent = matched[-KELLY_WINDOW_N:]

        if len(recent) < min_trades:
            return None

        wins = [t for t in recent if t.pnl > 0]
        losses = [t for t in recent if t.pnl <= 0]

        if not wins or not losses:
            return KELLY_NO_EDGE

        wr = len(wins) / len(recent)
        avg_win = sum(t.pnl for t in wins) / len(wins)
        avg_loss = abs(sum(t.pnl for t in losses) / len(losses))

        if avg_loss == 0:
            return KELLY_NO_EDGE

        b = avg_win / avg_loss  # payoff ratio
        f_star = (wr * b - (1 - wr)) / b  # raw Kelly

        if f_star <= 0:
            log.info(
                "BT Kelly[%s]: f*=%.4f ≤ 0 → no edge (wr=%.1f%%, b=%.2f) → block",
                strategy, f_star, wr * 100, b,
            )
            return KELLY_NO_EDGE

        # CV correction: SE / mean of edge estimate
        n = len(recent)
        edges = [t.pnl / avg_loss for t in recent]
        edge_mean = sum(edges) / n

        if edge_mean > 0:
            edge_var = sum((e - edge_mean) ** 2 for e in edges) / n
            edge_std = math.sqrt(edge_var)
            cv = edge_std / (edge_mean * math.sqrt(n))
        else:
            cv = float("inf")

        cv_factor = max(0.0, 1.0 - cv)
        f_adjusted = f_star * cv_factor

        # Half-Kelly + clamp
        base_risk = f_adjusted * 0.5
        base_risk = max(KELLY_MIN_RISK, min(KELLY_MAX_RISK, base_risk))

        log.debug(
            "BT Kelly[%s]: wr=%.1f%% b=%.2f f*=%.4f CV=%.2f → risk=%.2f%%",
            strategy, wr * 100, b, f_star, cv, base_risk * 100,
        )
        return base_risk

    def _execute_pending(self, candle, candle_time: str):
        """Execute pending signal at this candle's open price."""
        sig = self._pending_signal
        self._pending_signal = None

        entry_price = float(candle["open"])
        if entry_price <= 0:
            return

        # Strategy params
        if sig.strategy == "range":
            params = self.range_strategy.get_position_params()
        elif sig.strategy == "crash":
            params = self.crash_strategy.get_position_params()
        elif sig.strategy == "burst" and self.burst_strategy is not None:
            params = self.burst_strategy.get_position_params()
        elif sig.strategy == "newarch" and self.newarch_strategy is not None:
            params = self.newarch_strategy.get_position_params()
        else:
            params = self.trend_strategy.get_position_params()

        # SL/TP from ATR (captured at signal time)
        # CP: widen ATR with uncertainty estimate (calibration in _update_4h)
        atr_for_sl = sig.atr
        if self._cp is not None:
            try:
                atr_for_sl = self._cp.get_atr_high(sig.atr)
            except Exception as e:
                log.warning("CP get_atr_high failed in backtest: %s", e)

        sl_dist = atr_for_sl * params.sl_atr_mult

        # Minimum SL floor: 0.3% of entry price (prevents dust SL from ATR noise)
        min_sl = entry_price * 0.003
        if sl_dist < min_sl:
            sl_dist = min_sl

        # ── Strategy-specific TP (matches live position_sizer.py) ──
        tp_source = "min_rr"  # default fallback
        if sig.strategy == "range" and sig.bb_basis > 0:
            # Range: TP = 50% of distance to BB mid (mean reversion target)
            if sig.direction == "LONG":
                tp_dist = (sig.bb_basis - entry_price) * 0.50
            else:
                tp_dist = (entry_price - sig.bb_basis) * 0.50
            tp_dist = max(tp_dist, sl_dist * 1.0)  # floor: at least 1:1
            tp_source = "bb_mid"
        elif sig.strategy == "crash":
            # Crash: 3.5× 4H ATR (live uses 4H ATR, not 1H)
            atr_4h = sig.atr_4h if sig.atr_4h > 0 else sig.atr
            tp_dist = atr_4h * 3.5
            tp_source = "atr_3.5"
        elif sig.strategy == "newarch":
            # NewArch: regime-dependent TP
            # BB mid for ranging Z-Score entries, 2.5× ATR for trending NFS entries
            if sig.bb_basis > 0:
                if sig.direction == "LONG":
                    bb_tp = (sig.bb_basis - entry_price) * 0.60
                else:
                    bb_tp = (entry_price - sig.bb_basis) * 0.60
                atr_tp = sig.atr * 2.5
                tp_dist = max(bb_tp, atr_tp, sl_dist * params.min_rr)
            else:
                tp_dist = sig.atr * 2.5
            tp_dist = max(tp_dist, sl_dist * params.min_rr)
            tp_source = "newarch"
        else:
            # Trend / Burst: generic min_rr (same as live fallback without S/R)
            tp_dist = sl_dist * params.min_rr

        # Min R:R validation — reject if R:R < half of target
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr < params.min_rr * 0.5:
            return

        if sig.direction == "LONG":
            sl_price = entry_price - sl_dist
            tp_price = entry_price + tp_dist
        else:
            sl_price = entry_price + sl_dist
            tp_price = entry_price - tp_dist

        # Position sizing
        sl_dist_pct = sl_dist / entry_price
        if sl_dist_pct <= 0:
            return

        if sig.confidence > 0:
            # New architecture: profile risk × size_tier (production strategies)
            base_risk = _PROFILE_RISK.get(self.active_risk_profile, 0.02)
            size_tier = _get_size_tier(sig.confidence)
            risk_pct = base_risk * size_tier
            risk_pct = max(risk_pct, MIN_RISK_FLOOR)
        else:
            # Old architecture: scorer-based (BT strategies for optimizer)
            risk_pct = params.risk_pct
            if self._scorer is not None:
                risk_pct *= self._scorer.risk_multiplier(sig.score)
        risk_pct = min(risk_pct, MAX_RISK_PCT)

        # ── Kelly blocking: block no-edge trades, cap risk if edge is thin ──
        kelly_risk = self._calc_bt_kelly(sig.strategy)
        if kelly_risk == KELLY_NO_EDGE:
            return  # no statistical edge → block trade
        if kelly_risk is not None and kelly_risk < risk_pct:
            risk_pct = kelly_risk  # Kelly caps risk

        risk_amount = self.balance * risk_pct
        notional = risk_amount / sl_dist_pct
        max_notional = self.balance * params.leverage
        notional = min(notional, max_notional)

        if notional < 10:
            return

        self.positions.append(BTPosition(
            direction=sig.direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            notional=notional,
            entry_time=candle_time,
            strategy=sig.strategy,
            vol_regime=self.volatility_regime,
            market_mode=self.current_mode,
            confidence=sig.confidence,
            tp_source=tp_source,
            atr_at_entry=sig.atr if sig.atr else self._ind_4h.get("atr", 0.0),
        ))

    def _record_equity(self, candle):
        """Record equity point with mark-to-market unrealized PnL."""
        price = float(candle["close"])
        unrealized = 0.0
        for pos in self.positions:
            if pos.direction == "LONG":
                unrealized += pos.notional * ((price - pos.entry_price) / pos.entry_price)
            else:
                unrealized += pos.notional * ((pos.entry_price - price) / pos.entry_price)

        self.equity_curve.append({
            "time": str(candle["timestamp"]),
            "equity": round(self.balance + unrealized, 2),
            "balance": round(self.balance, 2),
            "positions": len(self.positions),
            "mode": self.current_mode,
            "vol_regime": self.volatility_regime,
        })

    def _summary(self) -> dict:
        """Calculate backtest summary with raw + cluster-adjusted stats."""
        base = {
            "symbol": self.symbol,
            "total_trades": 0, "winners": 0, "losers": 0,
            "final_balance": round(self.balance, 2),
            "return_pct": 0.0, "win_rate": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0,
            "trades": [], "equity_curve": self.equity_curve,
            "indicator_series": self.indicator_series,
            "clusters": 0, "independent_decisions": 0,
            "cluster_adj_wr": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0, "calmar_ratio": 0.0,
            "var_95": 0.0, "cvar_95": 0.0,
            "recovery_factor": 0.0, "payoff_ratio": 0.0,
            "drawdown_periods": [], "monthly_returns": {},
            "max_win_streak": 0, "max_loss_streak": 0,
            "by_strategy": {},
            "vol_regime_dist": {},
        }

        if not self.trades:
            return base

        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        wr = len(wins) / len(self.trades) * 100

        # Max drawdown
        max_dd = 0.0
        max_dd_abs = 0.0
        peak = self.initial_balance
        for pt in self.equity_curve:
            eq = pt["equity"]
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd
                    max_dd_abs = peak - eq

        # Drawdown periods (episodes with depth > 1%)
        drawdown_periods: list[dict] = []
        if self.equity_curve:
            peak_val = self.equity_curve[0]["equity"]
            peak_idx = 0
            in_dd = False
            dd_start = 0
            dd_trough_idx = 0
            dd_trough_val = peak_val

            for idx, pt in enumerate(self.equity_curve):
                eq = pt["equity"]
                if eq >= peak_val:
                    if in_dd:
                        depth = (peak_val - dd_trough_val) / peak_val * 100
                        if depth > 1.0:
                            drawdown_periods.append({
                                "start": self.equity_curve[dd_start]["time"],
                                "trough": self.equity_curve[dd_trough_idx]["time"],
                                "end": pt["time"],
                                "depth_pct": round(depth, 1),
                                "duration_candles": idx - dd_start,
                                "recovery_candles": idx - dd_trough_idx,
                            })
                        in_dd = False
                    peak_val = eq
                    peak_idx = idx
                else:
                    if not in_dd:
                        in_dd = True
                        dd_start = peak_idx
                        dd_trough_idx = idx
                        dd_trough_val = eq
                    elif eq < dd_trough_val:
                        dd_trough_idx = idx
                        dd_trough_val = eq

            # Ongoing drawdown at end
            if in_dd:
                depth = (peak_val - dd_trough_val) / peak_val * 100
                if depth > 1.0:
                    drawdown_periods.append({
                        "start": self.equity_curve[dd_start]["time"],
                        "trough": self.equity_curve[dd_trough_idx]["time"],
                        "end": None,
                        "depth_pct": round(depth, 1),
                        "duration_candles": len(self.equity_curve) - 1 - dd_start,
                        "recovery_candles": None,
                    })

            drawdown_periods.sort(key=lambda d: d["depth_pct"], reverse=True)
            drawdown_periods = drawdown_periods[:3]

        # Cluster analysis
        clusters = self._detect_clusters()
        n_clusters = len(clusters)
        # Independent decisions = non-clustered trades + 1 per cluster
        clustered_count = sum(len(c) for c in clusters)
        independent = (len(self.trades) - clustered_count) + n_clusters

        # Cluster-adjusted win rate: each cluster counts as 1 trade (net PnL)
        adj_wins = sum(1 for t in self.trades if t.pnl > 0
                       and not any(t in c for c in clusters))
        adj_losses = sum(1 for t in self.trades if t.pnl <= 0
                         and not any(t in c for c in clusters))
        for c in clusters:
            net = sum(t.pnl for t in c)
            if net > 0:
                adj_wins += 1
            else:
                adj_losses += 1
        adj_wr = adj_wins / independent * 100 if independent > 0 else 0.0

        # Hourly equity returns (shared by Sharpe, Sortino, VaR/CVaR)
        hourly_returns: list[float] = []
        if len(self.equity_curve) > 1:
            eqs = [pt["equity"] for pt in self.equity_curve]
            hourly_returns = [(eqs[j] - eqs[j-1]) / eqs[j-1]
                              for j in range(1, len(eqs)) if eqs[j-1] > 0]

        # Sharpe ratio (annualized from 1H equity returns — sqrt(8760) for hourly)
        sharpe = 0.0
        if hourly_returns:
            r_mean = sum(hourly_returns) / len(hourly_returns)
            r_std = (sum((r - r_mean) ** 2 for r in hourly_returns) / len(hourly_returns)) ** 0.5
            if r_std > 0:
                sharpe = round((r_mean / r_std) * (8760 ** 0.5), 2)

        # Sortino ratio (downside deviation: denominator = total N, not just negatives)
        sortino = 0.0
        if hourly_returns:
            r_mean = sum(hourly_returns) / len(hourly_returns)
            downside = [r for r in hourly_returns if r < 0]
            if downside:
                down_std = (sum(r ** 2 for r in downside) / len(hourly_returns)) ** 0.5
                if down_std > 0:
                    sortino = round((r_mean / down_std) * (8760 ** 0.5), 2)

        # Calmar ratio (annualized return / max drawdown)
        calmar = 0.0
        if max_dd > 0 and len(self.equity_curve) >= 2:
            days_span = len(self.equity_curve) / 24
            if days_span > 0:
                ann_return = (self.balance / self.initial_balance) ** (365 / days_span) - 1
                calmar = round(ann_return / max_dd, 2)

        # VaR 95% + CVaR 95% (hourly returns)
        var_95 = 0.0
        cvar_95 = 0.0
        if hourly_returns:
            var_95 = round(float(np.percentile(hourly_returns, 5)), 6)
            tail = [r for r in hourly_returns if r <= var_95]
            if tail:
                cvar_95 = round(sum(tail) / len(tail), 6)

        # Win/loss streaks
        max_win_streak = max_loss_streak = cur_win = cur_loss = 0
        for t in self.trades:
            if t.pnl > 0:
                cur_win += 1
                cur_loss = 0
            else:
                cur_loss += 1
                cur_win = 0
            max_win_streak = max(max_win_streak, cur_win)
            max_loss_streak = max(max_loss_streak, cur_loss)

        # Strategy breakdown
        by_strategy = {}
        for strat in ("range", "trend", "crash"):
            strat_trades = [t for t in self.trades if t.strategy == strat]
            if strat_trades:
                strat_wins = sum(1 for t in strat_trades if t.pnl > 0)
                by_strategy[strat] = {
                    "count": len(strat_trades),
                    "wins": strat_wins,
                    "win_rate": round(strat_wins / len(strat_trades) * 100, 1),
                    "avg_pnl": round(sum(t.pnl for t in strat_trades) / len(strat_trades), 2),
                }

        # Volatility regime distribution
        vol_regime_dist: dict[str, int] = {}
        if self.equity_curve:
            for pt in self.equity_curve:
                vr = pt.get("vol_regime", "NORMAL")
                vol_regime_dist[vr] = vol_regime_dist.get(vr, 0) + 1

        # SQN = sqrt(N) × mean(R-multiples) / std(R-multiples)
        # R-multiple = PnL / risk_amount (approximated as avg_loss)
        sqn = 0.0
        sqn_grade = "N/A"
        if len(self.trades) >= 5 and losses:
            avg_loss_abs = gross_loss / len(losses)
            if avg_loss_abs > 0:
                r_multiples = [t.pnl / avg_loss_abs for t in self.trades]
                r_mean = sum(r_multiples) / len(r_multiples)
                r_std = (sum((r - r_mean) ** 2 for r in r_multiples) / len(r_multiples)) ** 0.5
                if r_std > 0:
                    sqn = round(len(self.trades) ** 0.5 * r_mean / r_std, 2)
                    if sqn >= 7.0: sqn_grade = "Holy Grail"
                    elif sqn >= 5.0: sqn_grade = "Superb"
                    elif sqn >= 3.0: sqn_grade = "Excellent"
                    elif sqn >= 2.5: sqn_grade = "Good"
                    elif sqn >= 2.0: sqn_grade = "Average"
                    elif sqn >= 1.6: sqn_grade = "Below Avg"
                    else: sqn_grade = "Poor"

        # Alpha vs buy-and-hold
        alpha = 0.0
        buyhold_return = 0.0
        if len(self.equity_curve) >= 2:
            first_eq = self.equity_curve[0]
            last_eq = self.equity_curve[-1]
            # Buy-and-hold: first candle close → last candle close
            first_price = float(self.df_1h.iloc[WARMUP_CANDLES]["close"])
            last_price = float(self.df_1h.iloc[-1]["close"])
            if first_price > 0:
                buyhold_return = round((last_price - first_price) / first_price * 100, 2)
            strategy_return = round(
                (self.balance - self.initial_balance) / self.initial_balance * 100, 2
            )
            alpha = round(strategy_return - buyhold_return, 2)

        # Exposure%: fraction of candles with open positions
        exposure_pct = 0.0
        if self.equity_curve:
            candles_with_pos = sum(1 for pt in self.equity_curve if pt.get("positions", 0) > 0)
            exposure_pct = round(candles_with_pos / len(self.equity_curve) * 100, 1)

        # Recovery factor (net profit / max drawdown absolute)
        recovery_factor = 0.0
        if max_dd_abs > 0:
            recovery_factor = round((self.balance - self.initial_balance) / max_dd_abs, 2)

        # Payoff ratio (avg win / |avg loss|)
        payoff_ratio = 0.0
        if wins and losses:
            avg_w = gross_profit / len(wins)
            avg_l = gross_loss / len(losses)
            if avg_l > 0:
                payoff_ratio = round(avg_w / avg_l, 2)

        # Monthly return breakdown
        monthly_returns: dict[str, float] = {}
        if len(self.equity_curve) >= 2:
            cur_month: str | None = None
            month_start_eq = 0.0
            prev_eq = 0.0
            for pt in self.equity_curve:
                month_key = pt["time"][:7]
                if month_key != cur_month:
                    if cur_month is not None and month_start_eq > 0:
                        monthly_returns[cur_month] = round(
                            (prev_eq - month_start_eq) / month_start_eq * 100, 2
                        )
                    cur_month = month_key
                    month_start_eq = pt["equity"]
                prev_eq = pt["equity"]
            if cur_month is not None and month_start_eq > 0:
                monthly_returns[cur_month] = round(
                    (prev_eq - month_start_eq) / month_start_eq * 100, 2
                )

        base.update({
            "total_trades": len(self.trades),
            "winners": len(wins),
            "losers": len(losses),
            "return_pct": round(
                (self.balance - self.initial_balance) / self.initial_balance * 100, 2
            ),
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
            "expectancy": round(
                sum(t.pnl for t in self.trades) / len(self.trades), 2
            ),
            "max_drawdown_pct": round(max_dd * 100, 1),
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "sharpe_ratio": sharpe,
            "sqn": sqn,
            "sqn_grade": sqn_grade,
            "alpha": alpha,
            "buyhold_return": buyhold_return,
            "exposure_pct": exposure_pct,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "by_strategy": by_strategy,
            "trades": self.trades,
            "clusters": n_clusters,
            "independent_decisions": independent,
            "cluster_adj_wr": round(adj_wr, 1),
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "var_95": var_95,
            "cvar_95": cvar_95,
            "recovery_factor": recovery_factor,
            "payoff_ratio": payoff_ratio,
            "drawdown_periods": drawdown_periods,
            "monthly_returns": monthly_returns,
            "vol_regime_dist": vol_regime_dist,
            "confidence_dist": self._summarize_confidences(),
        })
        return base

    def _summarize_confidences(self) -> dict:
        """Summarize confidence distribution of signals that led to trades."""
        if not self._trade_confidences:
            return {}
        from collections import defaultdict
        by_strat = defaultdict(list)
        for strat, conf in self._trade_confidences:
            by_strat[strat].append(conf)
        result = {}
        for strat, confs in by_strat.items():
            confs.sort()
            n = len(confs)
            result[strat] = {
                "count": n,
                "min": round(confs[0], 3),
                "p25": round(confs[n // 4], 3),
                "median": round(confs[n // 2], 3),
                "p75": round(confs[3 * n // 4], 3),
                "max": round(confs[-1], 3),
                "mean": round(sum(confs) / n, 3),
            }
        return result

    def _detect_clusters(self) -> list[list[BTTrade]]:
        """Find trade clusters: same pair+direction, < CLUSTER_GAP_HOURS apart."""
        if len(self.trades) < 2:
            return []

        sorted_trades = sorted(self.trades, key=lambda t: t.entry_time)
        clusters = []
        current = [sorted_trades[0]]

        for t in sorted_trades[1:]:
            prev = current[-1]
            try:
                t_ts = datetime.fromisoformat(t.entry_time)
                p_ts = datetime.fromisoformat(prev.entry_time)
                gap = (t_ts - p_ts).total_seconds() / 3600
            except (ValueError, TypeError):
                gap = float("inf")

            if (
                gap < CLUSTER_GAP_HOURS
                and t.side == prev.side
                and t.symbol == prev.symbol
            ):
                current.append(t)
            else:
                if len(current) > 1:
                    clusters.append(current)
                current = [t]

        if len(current) > 1:
            clusters.append(current)

        return clusters
