#!/usr/bin/env python3
"""
per_asset_optimizer.py — Per-asset Optuna optimization for new confidence architecture.

Architecture:
  Phase 1 (slow, once): Pre-compute all raw signals at every candle via BacktestEngine
  Phase 2 (fast, per trial): Replay signal stream with different filtering params

This achieves ~100x speedup: pre-computation ~90s, each trial ~0.05s.

Search space (13 dimensions per asset):
  - conf_gate_{range,trend,crash}     — confidence gate thresholds
  - mode_pen_{trend_in_range, ...}    — soft mode penalty strengths
  - persist_{range,trend,crash}       — persistence thresholds
  - cooldown                          — post-trade cooldown candles

Usage:
  python3 backtest/per_asset_optimizer.py --symbol BTCUSDT --trials 200
  python3 backtest/per_asset_optimizer.py --all --trials 200
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.optimizer import load_pair_data
from backtest.engine import (
    BacktestEngine, WARMUP_CANDLES, COMMISSION_RATE, SL_SLIPPAGE_PCT,
    MAX_RISK_PCT, MIN_RISK_FLOOR, _PROFILE_RISK, _get_size_tier,
    MAX_CRYPTO_POSITIONS,
)

log = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(AXC_HOME, "backtest", "data")

# ─── Defaults (v5 params — current best manual tuning) ───
DEFAULTS = {
    "conf_gate_range": 0.50,
    "conf_gate_trend": 0.50,
    "conf_gate_crash": 0.50,
    "mode_pen_trend_in_range": -0.30,
    "mode_pen_trend_in_crash": -0.20,
    "mode_pen_range_in_trend": -0.20,
    "mode_pen_range_in_crash": -0.30,
    "mode_pen_default_trend": -0.25,
    "mode_pen_default_range": -0.10,
    "persist_range": 3,
    "persist_trend": 4,
    "persist_crash": 1,
    "cooldown": 8,
}

# ─── Search Space ───
SEARCH_SPACE = {
    "conf_gate_range":          (0.30, 0.70),
    "conf_gate_trend":          (0.30, 0.70),
    "conf_gate_crash":          (0.30, 0.70),
    "mode_pen_trend_in_range":  (-0.50, 0.0),
    "mode_pen_trend_in_crash":  (-0.50, 0.0),
    "mode_pen_range_in_trend":  (-0.50, 0.0),
    "mode_pen_range_in_crash":  (-0.50, 0.0),
    "mode_pen_default_trend":   (-0.40, 0.0),
    "mode_pen_default_range":   (-0.30, 0.0),
    "persist_range":            (1, 6),       # int
    "persist_trend":            (1, 8),       # int
    "persist_crash":            (0, 3),       # int
    "cooldown":                 (4, 16),      # int
}

INT_PARAMS = {"persist_range", "persist_trend", "persist_crash", "cooldown"}

MIN_TRADES = 20


# ═══════════════════════════════════════════════════════
# Phase 1: Signal Capture
# ═══════════════════════════════════════════════════════

@dataclass
class RawSignal:
    """Pre-computed signal at a specific candle. Immutable across optimizer trials."""
    candle_idx: int     # index into df_1h
    strategy: str       # "range", "trend", "crash"
    direction: str      # "LONG" or "SHORT"
    confidence: float   # raw confidence (before mode penalty)
    score: float
    atr: float
    mode: str           # market mode at signal time
    risk_profile: str   # "aggressive"/"balanced"/"conservative"


@dataclass
class CandleData:
    """Minimal candle data for fast replay."""
    open: float
    high: float
    low: float
    close: float


def capture_signals(
    symbol: str,
    df_1h,
    df_4h,
) -> tuple[list[RawSignal], list[CandleData], list[dict]]:
    """Run engine in capture mode: compute all raw signals without filtering.

    Returns:
      signals: list of RawSignal (every signal from every strategy at every candle)
      candles: list of CandleData (OHLC for each 1H candle)
      strategy_params: dict mapping strategy name → PositionParams
    """
    from backtest.engine import calc_indicators, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES
    from trader_cycle.strategies.mode_detector import detect_mode_for_pair
    from trader_cycle.strategies.range_strategy import RangeStrategy
    from trader_cycle.strategies.trend_strategy import TrendStrategy
    from trader_cycle.strategies.crash_strategy import CrashStrategy
    from trader_cycle.strategies.regime_hmm import RegimeHMM
    from trader_cycle.strategies.regime_bocpd import RegimeBOCPD
    from trader_cycle.core.context import CycleContext
    from trader_cycle.config.settings import (
        HMM_ENABLED, HMM_N_STATES, HMM_WINDOW,
        HMM_REFIT_INTERVAL, HMM_MIN_SAMPLES, HMM_CRASH_THRESHOLD,
        REGIME_ENGINE,
        BOCPD_HAZARD_RATE, BOCPD_MAX_RUN_LENGTH,
        BOCPD_MIN_SAMPLES, BOCPD_CHANGEPOINT_THRESHOLD,
    )
    from backtest.engine import _VOL_PROFILE_MAP, _VOL_DOWNGRADE

    sym = symbol.upper()
    df_1h = df_1h.reset_index(drop=True)
    df_4h = df_4h.reset_index(drop=True)

    # Build indicator params
    params_1h = TIMEFRAME_PARAMS["1h"].copy()
    params_4h = TIMEFRAME_PARAMS["4h"].copy()
    if sym in PRODUCT_OVERRIDES:
        params_1h.update(PRODUCT_OVERRIDES[sym])
        params_4h.update(PRODUCT_OVERRIDES[sym])

    # Regime engine
    bocpd = None
    hmm = None
    if REGIME_ENGINE == "bocpd_cp":
        bocpd = RegimeBOCPD(
            hazard_rate=BOCPD_HAZARD_RATE,
            max_run_length=BOCPD_MAX_RUN_LENGTH,
            min_samples=BOCPD_MIN_SAMPLES,
            changepoint_threshold=BOCPD_CHANGEPOINT_THRESHOLD,
        )
    elif HMM_ENABLED:
        hmm = RegimeHMM(
            n_states=HMM_N_STATES,
            window=HMM_WINDOW,
            refit_interval=HMM_REFIT_INTERVAL,
            min_samples=HMM_MIN_SAMPLES,
        )

    # Strategies
    range_strat = RangeStrategy()
    trend_strat = TrendStrategy()
    crash_strat = CrashStrategy()
    strat_params = {
        "range": range_strat.get_position_params(),
        "trend": trend_strat.get_position_params(),
        "crash": crash_strat.get_position_params(),
    }

    # State
    current_mode = "UNKNOWN"
    mode_confirmed = False
    vol_regime = "NORMAL"
    vol_confidence = 0.0
    risk_profile = "balanced"
    ind_4h = {}
    last_4h_idx = -1

    close_times_4h = df_4h["close_time"].values if "close_time" in df_4h.columns else (
        df_4h["open_time"].values + 4 * 3600 * 1000
    )

    total_1h = len(df_1h)
    signals = []
    candles = []

    # Pre-extract candle data
    for i in range(total_1h):
        row = df_1h.iloc[i]
        candles.append(CandleData(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        ))

    for i in range(WARMUP_CANDLES, total_1h):
        candle = df_1h.iloc[i]
        ts_1h = int(candle["open_time"])

        # ── 4H update ──
        completed_4h_idx = int(np.searchsorted(close_times_4h, ts_1h, side="right")) - 1
        if completed_4h_idx >= 0 and completed_4h_idx != last_4h_idx:
            last_4h_idx = completed_4h_idx
            start = max(0, completed_4h_idx - WARMUP_CANDLES + 1)
            slice_4h = df_4h.iloc[start:completed_4h_idx + 1].reset_index(drop=True)
            if len(slice_4h) >= 50:
                ind_4h = calc_indicators(slice_4h, params_4h)
                # Volume ratio
                if len(slice_4h) >= 30:
                    avg_vol = float(slice_4h["volume"].tail(30).mean())
                    cur_vol = float(slice_4h["volume"].iloc[-1])
                    ind_4h["volume_ratio"] = cur_vol / avg_vol if avg_vol > 0 else 1.0
                else:
                    ind_4h["volume_ratio"] = 1.0

                # Regime engine
                hmm_regime = None
                hmm_confidence = 0.0
                hmm_crash_confirmed = False
                vr = "NORMAL"
                vc = 0.0

                if bocpd is not None:
                    try:
                        hmm_regime, hmm_confidence, hmm_crash_confirmed = bocpd.update(ind_4h)
                        vr, vc = bocpd.get_volatility_regime()
                    except Exception:
                        pass
                elif hmm is not None:
                    try:
                        hmm_regime, hmm_confidence, hmm_crash_confirmed = hmm.update(ind_4h)
                        vr, vc = hmm.get_volatility_regime()
                    except Exception:
                        pass

                # Voter brake check
                raw_mode, _votes = detect_mode_for_pair(
                    ind_4h, 0.0, hmm_regime, hmm_confidence, hmm_crash_confirmed
                )
                if _votes:
                    trend_voters = sum(1 for v in _votes.values() if v == "TREND")
                    if trend_voters >= 3 and vr != "HIGH":
                        vr = _VOL_DOWNGRADE[vr]

                # CRASH override
                if hmm_regime == "CRASH" and hmm_crash_confirmed and hmm_confidence >= HMM_CRASH_THRESHOLD:
                    vr = "HIGH"
                    vc = hmm_confidence

                vol_regime = vr
                vol_confidence = vc
                risk_profile = _VOL_PROFILE_MAP.get(vr, "balanced")
                current_mode = raw_mode if raw_mode != "UNKNOWN" else current_mode
                mode_confirmed = vc > 0

        # ── 1H indicators ──
        start_idx = max(0, i - WARMUP_CANDLES + 1)
        slice_1h = df_1h.iloc[start_idx:i + 1].reset_index(drop=True)
        if len(slice_1h) < 50:
            continue

        ind_1h = calc_indicators(slice_1h, params_1h)
        if len(slice_1h) >= 30:
            avg_vol = float(slice_1h["volume"].tail(30).mean())
            cur_vol = float(slice_1h["volume"].iloc[-1])
            ind_1h["volume_ratio"] = cur_vol / avg_vol if avg_vol > 0 else 1.0
        else:
            ind_1h["volume_ratio"] = 1.0

        if not mode_confirmed or not ind_4h:
            continue

        # ── Strategy evaluation (ALL strategies, no filtering) ──
        ts = candle["timestamp"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        ctx = CycleContext(
            timestamp=ts,
            market_mode=current_mode,
            mode_confirmed=mode_confirmed,
            volatility_regime=vol_regime,
            active_risk_profile=risk_profile,
        )

        indicators = {"4h": ind_4h, "1h": ind_1h}
        atr = ind_1h.get("atr")
        if not atr or atr <= 0:
            continue

        for strat in (range_strat, trend_strat, crash_strat):
            sig = strat.evaluate(sym, indicators, ctx)
            if sig and sig.confidence > 0:
                signals.append(RawSignal(
                    candle_idx=i,
                    strategy=sig.strategy,
                    direction=sig.direction,
                    confidence=sig.confidence,
                    score=sig.score,
                    atr=atr,
                    mode=current_mode,
                    risk_profile=risk_profile,
                ))

    return signals, candles, strat_params


# ═══════════════════════════════════════════════════════
# Phase 2: Fast Replay
# ═══════════════════════════════════════════════════════

def replay(
    signals: list[RawSignal],
    candles: list[CandleData],
    strat_params: dict,
    tuning: dict,
    initial_balance: float = 10000.0,
) -> dict:
    """Replay pre-computed signals with given tuning params. ~0.05s per call.

    Returns dict compatible with metrics analysis (return_pct, trades, etc.)
    """
    # ── Unpack tuning params ──
    conf_gate = {
        "range": tuning.get("conf_gate_range", 0.50),
        "trend": tuning.get("conf_gate_trend", 0.50),
        "crash": tuning.get("conf_gate_crash", 0.50),
    }
    mode_affinity = {
        "TREND": {"trend": 0.0, "range": tuning.get("mode_pen_range_in_trend", -0.20), "crash": 0.0},
        "RANGE": {"range": 0.0, "trend": tuning.get("mode_pen_trend_in_range", -0.30), "crash": 0.0},
        "CRASH": {"crash": 0.0, "trend": tuning.get("mode_pen_trend_in_crash", -0.20),
                  "range": tuning.get("mode_pen_range_in_crash", -0.30)},
    }
    mode_default = {
        "trend": tuning.get("mode_pen_default_trend", -0.25),
        "range": tuning.get("mode_pen_default_range", -0.10),
        "crash": 0.0,
    }
    persistence = {
        "range": tuning.get("persist_range", 3),
        "trend": tuning.get("persist_trend", 4),
        "crash": tuning.get("persist_crash", 1),
    }
    cooldown_candles = tuning.get("cooldown", 8)

    # ── Build signal index: candle_idx → list of signals ──
    from collections import defaultdict
    sig_by_candle = defaultdict(list)
    for sig in signals:
        sig_by_candle[sig.candle_idx].append(sig)

    # ── State ──
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown_pct = 0.0
    position = None  # (direction, entry_price, sl_price, tp_price, notional, strategy, entry_idx)
    pending = None    # (direction, strategy, atr, confidence, risk_profile, execute_at_idx)
    cooldown_remaining = 0
    persist_strategy = None
    persist_direction = None
    persist_count = 0

    trades = []  # list of dicts
    equity_curve = []  # (candle_idx, equity)
    pnl_series = []  # per-trade returns for Sharpe calc

    n_candles = len(candles)

    for i in range(WARMUP_CANDLES, n_candles):
        c = candles[i]

        # ── Execute pending signal ──
        if pending is not None and pending[5] == i:
            entry_price = c.open
            if entry_price > 0 and position is None:
                direction, strategy, atr, confidence, rp, _ = pending
                params = strat_params[strategy]

                sl_dist = atr * params.sl_atr_mult
                tp_dist = sl_dist * params.min_rr

                if direction == "LONG":
                    sl_price = entry_price - sl_dist
                    tp_price = entry_price + tp_dist
                else:
                    sl_price = entry_price + sl_dist
                    tp_price = entry_price - tp_dist

                # Position sizing
                sl_dist_pct = sl_dist / entry_price
                if sl_dist_pct > 0:
                    base_risk = _PROFILE_RISK.get(rp, 0.02)
                    size_tier = _get_size_tier(confidence)
                    risk_pct = max(base_risk * size_tier, MIN_RISK_FLOOR)
                    risk_pct = min(risk_pct, MAX_RISK_PCT)
                    notional = balance * risk_pct / sl_dist_pct
                    notional = min(notional, balance * 0.95)

                    if notional > 0:
                        position = (direction, entry_price, sl_price, tp_price, notional, strategy, i)

            pending = None

        # ── Check SL/TP ──
        if position is not None:
            direction, entry_price, sl_price, tp_price, notional, strategy, entry_idx = position

            if direction == "LONG":
                sl_hit = c.low <= sl_price
                tp_hit = c.high >= tp_price
            else:
                sl_hit = c.high >= sl_price
                tp_hit = c.low <= tp_price

            if sl_hit or tp_hit:
                if sl_hit and tp_hit:
                    # Both hit same candle → SL first (conservative)
                    exit_price = sl_price * (1 - SL_SLIPPAGE_PCT if direction == "LONG" else 1 + SL_SLIPPAGE_PCT)
                    reason = "SL"
                elif sl_hit:
                    exit_price = sl_price * ((1 - SL_SLIPPAGE_PCT) if direction == "LONG" else (1 + SL_SLIPPAGE_PCT))
                    reason = "SL"
                else:
                    exit_price = tp_price
                    reason = "TP"

                if direction == "LONG":
                    raw_pnl_pct = (exit_price - entry_price) / entry_price
                else:
                    raw_pnl_pct = (entry_price - exit_price) / entry_price

                commission = notional * COMMISSION_RATE * 2
                pnl = notional * raw_pnl_pct - commission
                balance += pnl

                trades.append({
                    "strategy": strategy,
                    "direction": direction,
                    "pnl": pnl,
                    "reason": reason,
                })
                pnl_series.append(pnl)

                position = None
                cooldown_remaining = cooldown_candles

        # ── Force close at last candle ──
        if i == n_candles - 1 and position is not None:
            direction, entry_price, sl_price, tp_price, notional, strategy, entry_idx = position
            exit_price = c.close
            if direction == "LONG":
                raw_pnl_pct = (exit_price - entry_price) / entry_price
            else:
                raw_pnl_pct = (entry_price - exit_price) / entry_price
            commission = notional * COMMISSION_RATE * 2
            pnl = notional * raw_pnl_pct - commission
            balance += pnl
            trades.append({"strategy": strategy, "direction": direction, "pnl": pnl, "reason": "END"})
            pnl_series.append(pnl)
            position = None

        # ── Track equity / drawdown ──
        equity = balance + (0 if position is None else 0)  # simplified: mark-to-market not needed
        peak_balance = max(peak_balance, equity)
        dd = (peak_balance - equity) / peak_balance * 100 if peak_balance > 0 else 0
        max_drawdown_pct = max(max_drawdown_pct, dd)

        # ── Signal evaluation ──
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
        elif position is None and pending is None:
            raw_sigs = sig_by_candle.get(i)
            if raw_sigs:
                # Apply mode penalty + confidence gate
                candidates = []
                for sig in raw_sigs:
                    penalties = mode_affinity.get(sig.mode, mode_default)
                    penalty = penalties.get(sig.strategy, 0.0)
                    adj_conf = sig.confidence + penalty
                    gate = conf_gate.get(sig.strategy, 0.50)
                    if adj_conf >= gate:
                        candidates.append((sig, max(adj_conf, 0.0)))

                if candidates:
                    # Pick best by adjusted confidence
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    best_sig, best_conf = candidates[0]

                    # Persistence check
                    threshold = persistence.get(best_sig.strategy, 0)
                    if threshold > 0:
                        if (best_sig.strategy == persist_strategy
                                and best_sig.direction == persist_direction):
                            persist_count += 1
                        else:
                            persist_strategy = best_sig.strategy
                            persist_direction = best_sig.direction
                            persist_count = 1

                        if persist_count < threshold:
                            continue  # not persistent enough — skip to next candle
                            # (continue in for loop is fine here)

                    # Signal passes → create pending
                    persist_count = 0
                    pending = (
                        best_sig.direction,
                        best_sig.strategy,
                        best_sig.atr,
                        best_conf,
                        best_sig.risk_profile,
                        i + 1,  # execute at next candle
                    )
                else:
                    persist_count = 0
            else:
                persist_count = 0

    # ── Compute summary metrics ──
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "return_pct": (balance / initial_balance - 1) * 100,
            "total_trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_drawdown_pct": max_drawdown_pct,
            "sharpe_ratio": 0,
            "calmar_ratio": 0,
        }

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gross_profit = sum(wins) if wins else 0
    gross_loss = sum(losses) if losses else 0

    return_pct = (balance / initial_balance - 1) * 100
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    pf = gross_profit / abs(gross_loss) if gross_loss != 0 else (10.0 if gross_profit > 0 else 0)

    # Sharpe (annualized from hourly returns)
    if len(pnl_series) >= 2:
        pnl_arr = np.array(pnl_series)
        mean_ret = np.mean(pnl_arr)
        std_ret = np.std(pnl_arr, ddof=1)
        sharpe = (mean_ret / std_ret) * math.sqrt(365 * 24 / max(len(pnl_arr), 1)) if std_ret > 0 else 0
    else:
        sharpe = 0

    # Calmar
    calmar = (return_pct / max(max_drawdown_pct, 0.1))

    return {
        "return_pct": round(return_pct, 4),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(min(pf, 10.0), 4),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_ratio": round(sharpe, 4),
        "calmar_ratio": round(min(calmar, 10.0), 4),
        "trades": trades,
    }


# ═══════════════════════════════════════════════════════
# Objective + Optimization
# ═══════════════════════════════════════════════════════

W_SHARPE = 0.40
W_PF = 0.30
W_CALMAR = 0.15
W_WR = 0.15


def compute_objective(result: dict) -> float:
    """Composite objective: higher = better."""
    total_trades = result.get("total_trades", 0)
    if total_trades < MIN_TRADES:
        return float("-inf")

    sharpe = result.get("sharpe_ratio", 0.0) or 0.0
    pf = result.get("profit_factor", 0.0)
    if isinstance(pf, str):
        pf = 10.0
    pf = min(pf, 10.0)
    calmar = result.get("calmar_ratio", 0.0) or 0.0
    calmar = min(calmar, 10.0)
    wr = result.get("win_rate", 0.0) / 100.0

    max_dd = result.get("max_drawdown_pct", 0.0)
    dd_penalty = max(0.0, (max_dd - 30.0) / 100.0)

    return (
        W_SHARPE * sharpe
        + W_PF * pf
        + W_CALMAR * calmar
        + W_WR * wr * 10.0
        - dd_penalty
    )


def _suggest_params(trial: optuna.Trial) -> dict:
    """Suggest parameters from Optuna search space."""
    params = {}
    for name, (lo, hi) in SEARCH_SPACE.items():
        if name in INT_PARAMS:
            params[name] = trial.suggest_int(name, int(lo), int(hi))
        else:
            params[name] = trial.suggest_float(name, lo, hi)
    return params


def apply_shrinkage(optimized: dict, factor: float = 0.70) -> dict:
    """Blend optimized params toward defaults."""
    blended = {}
    for key, default_val in DEFAULTS.items():
        opt_val = optimized.get(key, default_val)
        blended_val = factor * opt_val + (1.0 - factor) * default_val
        if key in INT_PARAMS:
            blended[key] = int(round(blended_val))
        else:
            blended[key] = round(blended_val, 4)
    return blended


def run_backtest_with_params(symbol, df_1h, df_4h, tuning_params, initial_balance=10000.0):
    """Full backtest (slow path) for final validation + walk-forward."""
    from backtest.metrics_ext import extend_summary
    engine = BacktestEngine(
        symbol=symbol,
        df_1h=df_1h.copy(),
        df_4h=df_4h.copy(),
        initial_balance=initial_balance,
        tuning_params=tuning_params,
        quiet=True,
    )
    result = engine.run()
    return extend_summary(result)


# ═══════════════════════════════════════════════════════
# Walk-Forward Validation (uses slow path for accuracy)
# ═══════════════════════════════════════════════════════

def walk_forward_validate(symbol, df_1h, df_4h, tuning_params, n_folds=3):
    """Walk-forward validation using full backtest engine."""
    n_1h = len(df_1h)
    warmup = WARMUP_CANDLES
    usable = n_1h - warmup
    fold_size = usable // n_folds

    if fold_size < 200:
        return {"passed": True, "reason": "insufficient_data", "folds": []}

    is_scores = []
    oos_scores = []
    folds = []

    for fold_idx in range(n_folds):
        test_start = warmup + fold_idx * fold_size
        test_end = min(test_start + fold_size, n_1h)

        # OOS: test fold with warmup prepended
        oos_start = max(0, test_start - warmup)
        oos_1h = df_1h.iloc[oos_start:test_end].copy()

        # IS: everything before test fold
        is_1h = df_1h.iloc[:test_start].copy()

        # 4H proportional
        n_4h = len(df_4h)
        split_4h = int(n_4h * (test_start / n_1h))
        end_4h = int(n_4h * (test_end / n_1h))
        oos_4h_start = max(0, int(n_4h * (oos_start / n_1h)))

        is_4h = df_4h.iloc[:split_4h].copy()
        oos_4h = df_4h.iloc[oos_4h_start:end_4h].copy()

        if len(is_1h) < warmup + 100 or len(oos_1h) < warmup + 50:
            continue

        is_result = run_backtest_with_params(symbol, is_1h, is_4h, tuning_params)
        oos_result = run_backtest_with_params(symbol, oos_1h, oos_4h, tuning_params)

        is_obj = compute_objective(is_result)
        oos_obj = compute_objective(oos_result)

        is_scores.append(is_obj)
        oos_scores.append(oos_obj)
        folds.append({
            "fold": fold_idx,
            "is_obj": round(is_obj, 4),
            "oos_obj": round(oos_obj, 4),
            "is_trades": is_result.get("total_trades", 0),
            "oos_trades": oos_result.get("total_trades", 0),
            "oos_return": round(oos_result.get("return_pct", 0.0), 2),
        })

    if not is_scores or not oos_scores:
        return {"passed": True, "reason": "no_valid_folds", "folds": folds}

    is_avg = sum(is_scores) / len(is_scores)
    oos_avg = sum(oos_scores) / len(oos_scores)

    if is_avg <= 0:
        passed = oos_avg >= 0
    else:
        passed = (1.0 - oos_avg / is_avg) < 0.50

    return {
        "passed": passed,
        "is_avg": round(is_avg, 4),
        "oos_avg": round(oos_avg, 4),
        "degradation_pct": round((1.0 - oos_avg / is_avg) * 100, 1) if is_avg > 0 else None,
        "folds": folds,
    }


# ═══════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════

def optimize_asset(
    symbol: str,
    days: int = 360,
    n_trials: int = 200,
    shrinkage: float = 0.70,
    validate: bool = True,
) -> dict:
    """Per-asset optimization: capture signals → fast Optuna → WF validate."""

    print(f"\n{'='*50}")
    print(f"  Optimizing {symbol} — {days}d — {n_trials} trials")
    print(f"{'='*50}")

    # ── Phase 1: Load + Capture ──
    print("\n[1/5] Loading data...")
    df_1h, df_4h = load_pair_data(symbol, days)
    print(f"  1H: {len(df_1h)} candles, 4H: {len(df_4h)} candles")

    print("\n[2/5] Capturing raw signals (one-time, ~90s)...")
    import time
    t0 = time.time()
    raw_signals, candle_data, strat_params = capture_signals(symbol, df_1h, df_4h)
    t1 = time.time()
    print(f"  Captured {len(raw_signals)} raw signals in {t1-t0:.1f}s")

    # Signal distribution
    from collections import Counter
    strat_counts = Counter(s.strategy for s in raw_signals)
    for s, n in sorted(strat_counts.items()):
        print(f"    {s}: {n} signals")

    # ── Baseline ──
    print("\n[3/5] Baseline (v5 defaults)...")
    baseline = replay(raw_signals, candle_data, strat_params, DEFAULTS)
    baseline_obj = compute_objective(baseline)
    print(f"  Return={baseline['return_pct']:+.2f}%  Sharpe={baseline['sharpe_ratio']:.2f}  "
          f"PF={baseline['profit_factor']:.2f}  Trades={baseline['total_trades']}  "
          f"Obj={baseline_obj:.4f}")

    # ── Phase 2: Fast Optuna ──
    print(f"\n[4/5] Optuna search ({n_trials} trials, fast replay)...")
    t0 = time.time()

    def objective(trial: optuna.Trial) -> float:
        params = _suggest_params(trial)
        result = replay(raw_signals, candle_data, strat_params, params)
        return compute_objective(result)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.enqueue_trial(DEFAULTS)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    t1 = time.time()
    best_params = study.best_params
    best_obj = study.best_value
    print(f"  Completed in {t1-t0:.1f}s ({(t1-t0)/n_trials:.2f}s/trial)")

    best_result = replay(raw_signals, candle_data, strat_params, best_params)
    print(f"\n  Best raw: Return={best_result['return_pct']:+.2f}%  "
          f"Sharpe={best_result['sharpe_ratio']:.2f}  "
          f"PF={best_result['profit_factor']:.2f}  "
          f"Trades={best_result['total_trades']}  Obj={best_obj:.4f}")
    if baseline_obj > 0:
        print(f"  Improvement: {(best_obj - baseline_obj):+.4f} ({(best_obj/baseline_obj - 1)*100:+.1f}%)")
    else:
        print(f"  Improvement: {(best_obj - baseline_obj):+.4f}")

    # Shrinkage
    shrunk_params = apply_shrinkage(best_params, shrinkage)
    shrunk_result = replay(raw_signals, candle_data, strat_params, shrunk_params)
    shrunk_obj = compute_objective(shrunk_result)
    print(f"\n  Shrunk ({shrinkage:.0%}): Return={shrunk_result['return_pct']:+.2f}%  "
          f"Sharpe={shrunk_result['sharpe_ratio']:.2f}  "
          f"PF={shrunk_result['profit_factor']:.2f}  "
          f"Trades={shrunk_result['total_trades']}  Obj={shrunk_obj:.4f}")

    # ── Walk-Forward (slow path for accuracy) ──
    wf_result = {"passed": None, "reason": "skipped"}
    if validate:
        print("\n[5/5] Walk-forward validation (3 folds, full engine)...")
        wf_result = walk_forward_validate(symbol, df_1h, df_4h, shrunk_params)
        status = "PASS" if wf_result["passed"] else "FAIL"
        print(f"  WF: {status}")
        if wf_result.get("degradation_pct") is not None:
            print(f"  IS avg={wf_result['is_avg']:.4f}  OOS avg={wf_result['oos_avg']:.4f}  "
                  f"Degradation={wf_result['degradation_pct']:.1f}%")
        for fold in wf_result.get("folds", []):
            print(f"    Fold {fold['fold']}: IS={fold['is_obj']:.4f} OOS={fold['oos_obj']:.4f} "
                  f"trades={fold['oos_trades']} ret={fold['oos_return']:+.2f}%")
    else:
        print("\n[5/5] Walk-forward validation skipped")

    # ── Final param selection ──
    if wf_result.get("passed"):
        final_params = shrunk_params
        final_source = "shrunk"
    else:
        mild_shrunk = apply_shrinkage(best_params, 0.50)
        mild_result = replay(raw_signals, candle_data, strat_params, mild_shrunk)
        mild_obj = compute_objective(mild_result)
        if mild_obj > baseline_obj:
            final_params = mild_shrunk
            final_source = "mild_shrunk"
        else:
            final_params = DEFAULTS
            final_source = "defaults"

    final_result = replay(raw_signals, candle_data, strat_params, final_params)
    final_obj = compute_objective(final_result)

    # ── Verify with full engine ──
    print(f"\n  Verifying final params with full engine...")
    full_result = run_backtest_with_params(symbol, df_1h, df_4h, final_params)
    print(f"  Full engine: Return={full_result.get('return_pct', 0):+.2f}%  "
          f"Sharpe={full_result.get('sharpe_ratio', 0):.2f}  "
          f"PF={full_result.get('profit_factor', 0):.2f}  "
          f"Trades={full_result.get('total_trades', 0)}")

    # ── Summary ──
    print(f"\n{'─'*50}")
    print(f"  FINAL ({final_source})")
    print(f"{'─'*50}")
    print(f"  Return:     {full_result.get('return_pct', 0):+.2f}%")
    print(f"  Trades:     {full_result.get('total_trades', 0)}")
    print(f"  Win Rate:   {full_result.get('win_rate', 0):.1f}%")
    print(f"  PF:         {full_result.get('profit_factor', 0):.2f}")
    print(f"  Sharpe:     {full_result.get('sharpe_ratio', 0):.2f}")
    print(f"  Max DD:     {full_result.get('max_drawdown_pct', 0):.1f}%")
    print(f"  Calmar:     {full_result.get('calmar_ratio', 0):.2f}")

    print(f"\n  Param changes from defaults:")
    for key in sorted(DEFAULTS):
        d = DEFAULTS[key]
        f = final_params[key]
        if abs(f - d) > 0.001:
            print(f"    {key}: {d} → {f}")

    # ── Per-strategy breakdown ──
    for strat in ("range", "trend", "crash"):
        st = [t for t in full_result.get("trades", []) if (t.strategy if hasattr(t, 'strategy') else t.get('strategy')) == strat]
        if st:
            wins = sum(1 for t in st if (t.pnl if hasattr(t, 'pnl') else t.get('pnl', 0)) > 0)
            total_pnl = sum(t.pnl if hasattr(t, 'pnl') else t.get('pnl', 0) for t in st)
            print(f"    {strat:6s}  {wins}W/{len(st)-wins}L  PnL=${total_pnl:+,.0f}")

    # ── Save ──
    output = {
        "symbol": symbol,
        "days": days,
        "n_trials": n_trials,
        "shrinkage": shrinkage,
        "final_source": final_source,
        "final_params": final_params,
        "best_raw_params": best_params,
        "baseline": {
            "return_pct": round(baseline["return_pct"], 2),
            "sharpe": round(baseline["sharpe_ratio"], 4),
            "pf": round(baseline["profit_factor"], 4),
            "trades": baseline["total_trades"],
            "objective": round(baseline_obj, 4),
        },
        "final": {
            "return_pct": round(full_result.get("return_pct", 0), 2),
            "sharpe": round(full_result.get("sharpe_ratio", 0) or 0, 4),
            "pf": round(full_result.get("profit_factor", 0) or 0, 4),
            "trades": full_result.get("total_trades", 0),
            "objective": round(final_obj, 4),
            "max_dd": round(full_result.get("max_drawdown_pct", 0), 2),
            "win_rate": round(full_result.get("win_rate", 0), 2),
        },
        "walk_forward": wf_result,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"opt_{symbol}_{days}d.json")
    tmp = tempfile.NamedTemporaryFile(
        mode='w', dir=os.path.dirname(path), delete=False, suffix='.tmp')
    json.dump(output, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    os.replace(tmp.name, path)
    print(f"\n  Saved → {path}")

    return output


def main():
    parser = argparse.ArgumentParser(description="Per-asset parameter optimizer (fast replay)")
    parser.add_argument("--symbol", help="Symbol to optimize (e.g. BTCUSDT)")
    parser.add_argument("--all", action="store_true", help="Optimize all 3 assets")
    parser.add_argument("--days", type=int, default=360, help="Backtest period (default 360)")
    parser.add_argument("--trials", type=int, default=200, help="Optuna trials (default 200)")
    parser.add_argument("--shrinkage", type=float, default=0.70, help="Shrinkage factor (default 0.70)")
    parser.add_argument("--no-validate", action="store_true", help="Skip walk-forward validation")
    args = parser.parse_args()

    symbols = []
    if args.all:
        symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    elif args.symbol:
        symbols = [args.symbol.upper()]
    else:
        parser.error("Specify --symbol or --all")

    results = {}
    for sym in symbols:
        result = optimize_asset(
            symbol=sym,
            days=args.days,
            n_trials=args.trials,
            shrinkage=args.shrinkage,
            validate=not args.no_validate,
        )
        results[sym] = result

    if len(results) > 1:
        print(f"\n{'='*60}")
        print(f"  CROSS-ASSET SUMMARY")
        print(f"{'='*60}")
        print(f"  {'Symbol':10s} {'Return':>8s} {'Sharpe':>7s} {'PF':>6s} {'WR':>6s} {'DD':>6s} {'Src':>10s}")
        for sym, r in results.items():
            f = r["final"]
            print(f"  {sym:10s} {f['return_pct']:>+7.2f}% {f['sharpe']:>7.2f} "
                  f"{f['pf']:>6.2f} {f['win_rate']:>5.1f}% {f['max_dd']:>5.1f}% "
                  f"{r['final_source']:>10s}")


if __name__ == "__main__":
    main()
