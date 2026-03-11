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
from trader_cycle.core.context import CycleContext
from trader_cycle.config.settings import (
    MODE_CONFIRMATION_REQUIRED,
    MAX_CRYPTO_POSITIONS,
)

log = logging.getLogger(__name__)

WARMUP_CANDLES = 200
COMMISSION_RATE = 0.0005   # 0.05% per side
SL_SLIPPAGE_PCT = 0.0002   # 0.02% adverse slippage on SL (market order)
CLUSTER_GAP_HOURS = 4      # trades < N hours apart = same cluster


@dataclass
class BTPosition:
    """Backtest position tracker."""
    direction: str      # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp_price: float
    notional: float     # position size in USDT
    entry_time: str
    strategy: str       # "range" or "trend"


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

    def to_jsonl(self) -> str:
        """Format matching metrics.py _load_trades() schema."""
        return json.dumps({
            "symbol": self.symbol,
            "side": self.side,
            "entry": round(self.entry, 6),
            "exit": round(self.exit, 6),
            "pnl": round(self.pnl, 2),
            "sl_price": round(self.sl_price, 6),
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
    remaining_delay: int = 1  # candles until execution (1 = next candle = default)


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

        # State
        self.positions: list[BTPosition] = []
        self.trades: list[BTTrade] = []
        self.equity_curve: list[dict] = []
        self._pending_signal: _PendingSignal | None = None

        # Mode tracking
        self.current_mode = "UNKNOWN"
        self.mode_confirmed = False
        self.prev_mode = "UNKNOWN"
        self.prev_mode_cycles = 0

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

        try:
            self._run_loop(total_1h, close_times_4h)
        finally:
            if _patched_keys:
                import indicator_calc as _ic
                for key, orig in _patched_keys.items():
                    if key == "_BB_WIDTH_MIN":
                        _ic.BB_WIDTH_MIN = orig
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

            # ── Step 5-6: Strategy evaluation → pending signal ──
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

        raw_mode, _votes = detect_mode_for_pair(self._ind_4h, 0.0)

        # Mode confirmation (mirrors DetectModeStep)
        if raw_mode == "UNKNOWN":
            self.current_mode = self.prev_mode
            self.mode_confirmed = self.prev_mode_cycles >= self._mode_confirmation
        elif raw_mode == self.prev_mode:
            self.current_mode = raw_mode
            self.prev_mode_cycles += 1
            self.mode_confirmed = self.prev_mode_cycles >= self._mode_confirmation
        else:
            self.current_mode = raw_mode
            self.mode_confirmed = False
            self.prev_mode = raw_mode
            self.prev_mode_cycles = 1

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

    def _close_position(self, pos: BTPosition, exit_price: float, exit_time: str, reason: str):
        """Close position, calculate PnL, record trade."""
        if pos.direction == "LONG":
            raw_pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:
            raw_pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

        pnl = pos.notional * (raw_pnl_pct - self.commission_rate * 2)
        self.balance += pnl

        if pos in self.positions:
            self.positions.remove(pos)

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
        ))

    def _try_signal(self, ind_1h: dict, candle, candle_time: str):
        """Evaluate strategy. If signal, store as pending (execute next candle)."""
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
        )

        signal = None
        if self.current_mode == "RANGE":
            signal = self.range_strategy.evaluate(self.symbol, indicators, ctx)
        elif self.current_mode == "TREND":
            signal = self.trend_strategy.evaluate(self.symbol, indicators, ctx)

        if signal:
            # Score-based filtering: discard low-quality signals
            if signal.score < self.min_score:
                return

            atr = ind_1h.get("atr")
            if atr and atr > 0:
                self._pending_signal = _PendingSignal(
                    direction=signal.direction,
                    strategy=signal.strategy,
                    atr=atr,
                    signal_time=candle_time,
                    score=signal.score,
                    remaining_delay=self.signal_delay,
                )

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
        else:
            params = self.trend_strategy.get_position_params()

        # SL/TP from ATR (captured at signal time)
        sl_dist = sig.atr * params.sl_atr_mult
        tp_dist = sl_dist * params.min_rr

        if sig.direction == "LONG":
            sl_price = entry_price - sl_dist
            tp_price = entry_price + tp_dist
        else:
            sl_price = entry_price + sl_dist
            tp_price = entry_price - tp_dist

        # Position sizing (score-based confidence multiplier)
        sl_dist_pct = sl_dist / entry_price
        if sl_dist_pct <= 0:
            return

        risk_pct = params.risk_pct
        if self._scorer is not None:
            risk_pct *= self._scorer.risk_multiplier(sig.score)

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
            "clusters": 0, "independent_decisions": 0,
            "cluster_adj_wr": 0.0,
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
        peak = self.initial_balance
        for pt in self.equity_curve:
            eq = pt["equity"]
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd

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
            "trades": self.trades,
            "clusters": n_clusters,
            "independent_decisions": independent,
            "cluster_adj_wr": round(adj_wr, 1),
        })
        return base

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
