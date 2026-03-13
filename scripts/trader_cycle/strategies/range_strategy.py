"""
range_strategy.py — Mode A: Range Trading
BB touch + RSI reversal + support/resistance + Stoch confirmation

Uses evaluate_range_signal() from indicator_calc.py (zero duplication).
Entry on 1H timeframe, with 4H mode detection as prerequisite.
"""

from __future__ import annotations
import os
import sys

from ..config.settings import (
    RANGE_RISK_PCT, RANGE_LEVERAGE, RANGE_SL_ATR_MULT, RANGE_MIN_RR,
    SECONDARY_TIMEFRAME, PRIMARY_TIMEFRAME,
    ENTRY_VOLUME_MIN,
    OBV_CONFIRM_BONUS, OBV_AGAINST_PENALTY,
)
from ..config.pairs import get_pair
from ..core.context import CycleContext, Signal
from .base import StrategyBase, PositionParams

# Import evaluate_range_signal from indicator_calc
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from indicator_calc import evaluate_range_signal, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES


class RangeStrategy(StrategyBase):
    """
    Mode A — Range Trading (BB mean-reversion)

    Prerequisites (handled by DetectModeStep):
      - Market mode = RANGE (5-indicator voting on 4H)

    Entry logic (this class):
      1. R0 + R1 preconditions on 1H (BB width < 0.05, ADX < threshold)
      2. C1: BB band touch
      3. C2: RSI oversold/overbought reversal
      4. C3: Support/resistance proximity
      5. C4 (optional): Stochastic crossover (STRONG signal)
      Requires C1 + C2 + C3 minimum.

    Adding new conditions:
      - Modify evaluate_range_signal() in indicator_calc.py
      - Or override evaluate() here for strategy-level adjustments
    """
    name = "range"
    mode = "RANGE"
    required_timeframes = ["4h", "1h"]

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> Signal | None:
        """Evaluate range entry for one pair using 1H indicators."""
        # ─── Volume gate (Yunis Collection) ───
        ind_4h = indicators.get(PRIMARY_TIMEFRAME, {})
        volume_ratio = ind_4h.get("volume_ratio", 1.0)
        if volume_ratio < ENTRY_VOLUME_MIN:
            return None  # volume too low — skip

        # Need 1H indicators for entry signals
        ind_1h = indicators.get(SECONDARY_TIMEFRAME)
        if not ind_1h:
            return None

        # Build params: start with timeframe defaults, apply overrides
        tf_params = TIMEFRAME_PARAMS.get(SECONDARY_TIMEFRAME, {}).copy()

        # Product-level overrides from indicator_calc
        if pair in PRODUCT_OVERRIDES:
            tf_params.update(PRODUCT_OVERRIDES[pair])

        # Pair-level overrides from pairs.py (takes precedence)
        try:
            pair_cfg = get_pair(pair)
            if pair_cfg.rsi_long is not None:
                tf_params["rsi_long"] = pair_cfg.rsi_long
            if pair_cfg.rsi_short is not None:
                tf_params["rsi_short"] = pair_cfg.rsi_short
            if pair_cfg.bb_touch_tol is not None:
                tf_params["bb_touch_tol"] = pair_cfg.bb_touch_tol
        except KeyError:
            pass

        # Call the existing evaluate_range_signal function
        result = evaluate_range_signal(ind_1h, tf_params)

        if not result["range_valid"]:
            return None  # R0/R1 failed on 1H

        # ─── Volume score bonus (Yunis Collection) ───
        vol_bonus = 0.0
        if volume_ratio >= 2.0:
            vol_bonus = 1.0
        elif volume_ratio >= 1.5:
            vol_bonus = 0.5

        # ─── OBV confirmation (Yunis Collection) ───
        obv = ind_4h.get("obv")
        obv_ema = ind_4h.get("obv_ema")

        # ─── LONG signal ───
        if result["signal_long"] == 1:
            strength = "STRONG" if any("STRONG" in r for r in result["reasons"]) else "WEAK"
            base_score = 4.0 if strength == "STRONG" else 3.0
            reasons = list(result["reasons"])
            if vol_bonus > 0:
                reasons.append(f"VOLUME_BONUS: +{vol_bonus} (ratio={volume_ratio:.2f})")

            obv_adj = 0.0
            if obv is not None and obv_ema is not None:
                if obv > obv_ema:
                    obv_adj = OBV_CONFIRM_BONUS
                elif obv < obv_ema:
                    obv_adj = OBV_AGAINST_PENALTY
                if obv_adj != 0.0:
                    obv_adj *= min(volume_ratio, 1.0)
                    label = "OBV_CONFIRM" if obv_adj > 0 else "OBV_AGAINST"
                    flow = "bullish" if obv > obv_ema else "bearish"
                    reasons.append(f"{label}: {obv_adj:+.2f} ({flow} flow, vol={volume_ratio:.2f})")

            return Signal(
                pair=pair,
                direction="LONG",
                strategy=self.name,
                strength=strength,
                entry_price=ind_1h.get("price", 0),
                reasons=reasons,
                score=base_score + vol_bonus + obv_adj,
            )

        # ─── SHORT signal ───
        if result["signal_short"] == -1:
            strength = "STRONG" if any("STRONG" in r for r in result["reasons"]) else "WEAK"
            base_score = 4.0 if strength == "STRONG" else 3.0
            reasons = list(result["reasons"])
            if vol_bonus > 0:
                reasons.append(f"VOLUME_BONUS: +{vol_bonus} (ratio={volume_ratio:.2f})")

            obv_adj = 0.0
            if obv is not None and obv_ema is not None:
                if obv < obv_ema:
                    obv_adj = OBV_CONFIRM_BONUS
                elif obv > obv_ema:
                    obv_adj = OBV_AGAINST_PENALTY
                if obv_adj != 0.0:
                    obv_adj *= min(volume_ratio, 1.0)
                    label = "OBV_CONFIRM" if obv_adj > 0 else "OBV_AGAINST"
                    flow = "bearish" if obv < obv_ema else "bullish"
                    reasons.append(f"{label}: {obv_adj:+.2f} ({flow} flow, vol={volume_ratio:.2f})")

            return Signal(
                pair=pair,
                direction="SHORT",
                strategy=self.name,
                strength=strength,
                entry_price=ind_1h.get("price", 0),
                reasons=reasons,
                score=base_score + vol_bonus + obv_adj,
            )

        return None  # Range valid but no entry trigger

    def get_position_params(self) -> PositionParams:
        """Range: 2% risk, 8x leverage, SL=1.2*ATR, min R:R 2.3."""
        return PositionParams(
            risk_pct=RANGE_RISK_PCT,
            leverage=RANGE_LEVERAGE,
            sl_atr_mult=RANGE_SL_ATR_MULT,
            min_rr=RANGE_MIN_RR,
        )

    def evaluate_exit(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> str | None:
        """
        Range exit conditions:
          - TP1: price reaches 50% toward BB basis → close 50%
          - TP2: price reaches BB basis (full mid) → close remaining
          - SL hit (handled by exchange order)
        Phase 3: implement with live position data.
        """
        return None
