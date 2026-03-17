"""
bt_newarch_strategy.py — 5-Layer New Architecture Strategy for backtest validation.

v4 changes:
  - Z-Score now used as PULLBACK DETECTOR in trends (not mean reversion)
    - Trending + Z<-1.5 + EMA bull → LONG (buy the dip)
    - Trending + Z>+1.5 + EMA bear → SHORT (sell the rip)
  - True mean reversion only in genuine ranges (ADX<20 AND BB_pctl<25)
    with MA200 directional filter (don't fade the macro trend)
  - Removed regime warmup (unnecessary gating)
  - Use ctx.market_mode from engine when available
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(_AXC, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from indicator_calc import get_session_tag
from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal

log = logging.getLogger(__name__)

# ─── Constants ───
Z_PULLBACK_THRESHOLD = 1.5   # Z-Score for trend pullback detection
Z_REVERSION_THRESHOLD = 2.0  # Z-Score for true mean reversion (only in deep range)
BB_WIDTH_PCTL_DEAD = 10      # Below = skip (no volatility)
BB_WIDTH_PCTL_DEEP_RANGE = 25  # Below + ADX<20 = genuine range → allow mean reversion
ADX_DEEP_RANGE = 20          # ADX threshold for "genuinely flat"
ADX_TRENDING = 25            # ADX above = trending
MIN_ATR_FLOOR = 0.0001


class BTNewArchStrategy(StrategyBase):
    """5-Layer architecture v4: trend-aligned Z-Score + deep-range reversion.

    Accepts overrides dict for parameter sweep (keys match module constant names,
    lowercase). position_overrides: sl_atr_mult, min_rr, risk_pct, leverage.
    long_only: discard SHORT signals (test bull-market hypothesis).
    """
    name = "newarch"
    mode = ""
    required_timeframes = ["4h", "1h"]

    def __init__(
        self,
        overrides: dict | None = None,
        position_overrides: dict | None = None,
        long_only: bool = False,
    ):
        ov = overrides or {}
        self.z_pullback = ov.get("z_pullback", Z_PULLBACK_THRESHOLD)
        self.z_reversion = ov.get("z_reversion", Z_REVERSION_THRESHOLD)
        self.bb_pctl_dead = ov.get("bb_pctl_dead", BB_WIDTH_PCTL_DEAD)
        self.bb_pctl_range = ov.get("bb_pctl_range", BB_WIDTH_PCTL_DEEP_RANGE)
        self.adx_deep_range = ov.get("adx_deep_range", ADX_DEEP_RANGE)
        self.adx_trending = ov.get("adx_trending", ADX_TRENDING)
        self.long_only = long_only
        self._pos = position_overrides or {}

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        ind_1h = indicators.get("1h")
        ind_4h = indicators.get("4h")
        if not ind_1h or not ind_4h:
            return None

        price = ind_1h.get("price")
        atr = ind_1h.get("atr")
        if not price or not atr or atr < MIN_ATR_FLOOR:
            return None

        # ─── Layer 1: Filter ───
        bb_width_pctl = ind_1h.get("bb_width_pctl")
        z_robust = ind_1h.get("z_robust")
        if bb_width_pctl is None or z_robust is None:
            return None

        if bb_width_pctl < self.bb_pctl_dead:
            return None

        atr_pct = atr / price if price > 0 else 0
        if atr_pct < 0.001:
            return None

        # ─── Shared indicators ───
        adx = ind_1h.get("adx") or 0
        ema_fast = ind_1h.get("ema_fast") or 0
        ema_slow = ind_1h.get("ema_slow") or 0
        ma50 = ind_1h.get("ma50") or 0
        ma200 = ind_1h.get("ma200") or 0
        rsi = ind_1h.get("rsi")
        bb_basis = ind_1h.get("bb_basis") or 0

        # ─── Layer 2+3: Direction + Entry ───
        direction = None
        entry_type = None
        reasons = []

        # Determine macro trend
        macro_bull = ma50 > 0 and ma200 > 0 and ma50 > ma200
        macro_bear = ma50 > 0 and ma200 > 0 and ma50 < ma200
        ema_bull = ema_fast > 0 and ema_slow > 0 and ema_fast > ema_slow
        ema_bear = ema_fast > 0 and ema_slow > 0 and ema_fast < ema_slow

        # ─── Path A: Trending market (ADX > threshold or engine says TREND) ───
        is_trending = adx >= self.adx_trending or ctx.market_mode == "TREND"

        if is_trending:
            # Z-Score as pullback detector: Z below -threshold in uptrend = buy dip
            if z_robust < -self.z_pullback and (ema_bull or macro_bull):
                direction = "LONG"
                entry_type = "trend_pullback"
                reasons.append(f"Z={z_robust:.2f}<-{self.z_pullback}")
                reasons.append("TREND_DIP")
                if macro_bull:
                    reasons.append("MA50>200")
                if ema_bull:
                    reasons.append("EMA_BULL")

            elif z_robust > self.z_pullback and (ema_bear or macro_bear):
                direction = "SHORT"
                entry_type = "trend_pullback"
                reasons.append(f"Z={z_robust:.2f}>+{self.z_pullback}")
                reasons.append("TREND_RIP")
                if macro_bear:
                    reasons.append("MA50<200")
                if ema_bear:
                    reasons.append("EMA_BEAR")

            # Also: classic trend pullback (no Z-Score, just EMA + retrace to BB mid)
            elif bb_basis and ema_bull and price < bb_basis and adx >= self.adx_trending:
                direction = "LONG"
                entry_type = "ema_pullback"
                reasons.append("EMA_BULL")
                reasons.append("BELOW_BB_MID")
                reasons.append(f"ADX={adx:.0f}")

            elif bb_basis and ema_bear and price > bb_basis and adx >= self.adx_trending:
                direction = "SHORT"
                entry_type = "ema_pullback"
                reasons.append("EMA_BEAR")
                reasons.append("ABOVE_BB_MID")
                reasons.append(f"ADX={adx:.0f}")

        # ─── Path B: Deep range (ADX < threshold AND BB width pctl < threshold) ───
        is_deep_range = (adx < self.adx_deep_range and bb_width_pctl < self.bb_pctl_range
                         and ctx.market_mode in ("RANGE", "UNKNOWN"))

        if direction is None and is_deep_range:
            # True mean reversion: Z-Score entry WITH macro trend filter
            if z_robust > self.z_reversion and not macro_bull:
                # SHORT only if NOT in macro bull (don't fight the trend)
                direction = "SHORT"
                entry_type = "zscore_reversion"
                reasons.append(f"Z={z_robust:.2f}>+{self.z_reversion}")
                reasons.append("DEEP_RANGE")

            elif z_robust < -self.z_reversion and not macro_bear:
                # LONG only if NOT in macro bear
                direction = "LONG"
                entry_type = "zscore_reversion"
                reasons.append(f"Z={z_robust:.2f}<-{self.z_reversion}")
                reasons.append("DEEP_RANGE")

        if direction is None:
            return None

        # Long-only filter: discard SHORT signals (bull market hypothesis test)
        if self.long_only and direction == "SHORT":
            return None

        # ─── Layer 4: Confirmation ───
        confidence = 0.50

        # RSI alignment
        if rsi is not None:
            if direction == "LONG" and rsi < 30:
                confidence += 0.15
                reasons.append("RSI<30")
            elif direction == "SHORT" and rsi > 70:
                confidence += 0.15
                reasons.append("RSI>70")
            elif direction == "LONG" and rsi < 45:
                confidence += 0.05
            elif direction == "SHORT" and rsi > 55:
                confidence += 0.05

        # Volume
        volume_ratio = ind_1h.get("volume_ratio", 1.0) or 1.0
        if volume_ratio > 1.2:
            confidence += 0.10
            reasons.append(f"VOL={volume_ratio:.1f}")

        # Session
        ts = ctx.timestamp
        if ts is not None:
            session = get_session_tag(ts)
            if session in ("US_PRE", "US_OPEN"):
                confidence += 0.10
                reasons.append(f"SES={session}")
            elif session == "EU_OPEN":
                confidence += 0.05

        # ADX directional agreement
        di_plus = ind_1h.get("di_plus") or 0
        di_minus = ind_1h.get("di_minus") or 0
        if direction == "LONG" and di_plus > di_minus:
            confidence += 0.10
            reasons.append("ADX_AGREE")
        elif direction == "SHORT" and di_minus > di_plus:
            confidence += 0.10
            reasons.append("ADX_AGREE")

        # MACD histogram
        macd_hist = ind_1h.get("macd_hist")
        if macd_hist is not None:
            if direction == "LONG" and macd_hist > 0:
                confidence += 0.05
            elif direction == "SHORT" and macd_hist < 0:
                confidence += 0.05

        # Macro alignment bonus (strong signal)
        if entry_type == "trend_pullback":
            if (direction == "LONG" and macro_bull and ema_bull) or \
               (direction == "SHORT" and macro_bear and ema_bear):
                confidence += 0.10  # full alignment
                reasons.append("FULL_ALIGN")

        reasons.insert(0, f"entry={entry_type}")

        return Signal(
            pair=pair,
            direction=direction,
            strategy=self.name,
            strength="STRONG" if confidence >= 0.70 else "WEAK",
            entry_price=price,
            reasons=reasons,
            score=confidence * 10,
            confidence=confidence,
        )

    def evaluate_exit(self, pair, indicators, ctx):
        return None

    def get_position_params(self) -> PositionParams:
        return PositionParams(
            risk_pct=self._pos.get("risk_pct", 0.02),
            leverage=self._pos.get("leverage", 5),
            sl_atr_mult=self._pos.get("sl_atr_mult", 1.5),
            min_rr=self._pos.get("min_rr", 2.0),
        )
