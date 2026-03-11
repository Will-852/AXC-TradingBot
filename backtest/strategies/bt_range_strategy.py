"""
bt_range_strategy.py — Configurable Range Strategy for backtest optimization.

設計決定：
  - 唔繼承 production RangeStrategy（因為要 override 太多硬編碼值）
  - 直接 reuse evaluate_range_signal() + WeightedScorer
  - 所有入場 gate 可透過 param_overrides 調整
  - 評分公式用 WeightedScorer（可注入唔同權重）
"""

from __future__ import annotations

import os
import sys

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(_AXC, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from indicator_calc import evaluate_range_signal, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES
from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal

from backtest.scoring import WeightedScorer, ScoringWeights


class BTRangeStrategy(StrategyBase):
    """
    Configurable range strategy for backtest parameter search.

    Accepts:
      - entry_overrides: gate thresholds (entry_volume_min, etc.)
      - scorer: WeightedScorer with tunable weights
    """
    name = "range"
    mode = "RANGE"
    required_timeframes = ["4h", "1h"]

    def __init__(
        self,
        entry_overrides: dict | None = None,
        scorer: WeightedScorer | None = None,
        position_overrides: dict | None = None,
    ):
        self._entry = entry_overrides or {}
        self._scorer = scorer or WeightedScorer()
        self._pos = position_overrides or {}

    @property
    def entry_volume_min(self) -> float:
        return self._entry.get("entry_volume_min", 0.8)

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        """Evaluate range entry with configurable gates and scoring."""
        ind_4h = indicators.get("4h", {})
        volume_ratio = ind_4h.get("volume_ratio", 1.0)

        # Volume gate (configurable)
        if volume_ratio < self.entry_volume_min:
            return None

        ind_1h = indicators.get("1h")
        if not ind_1h:
            return None

        # Build params (reuse production logic)
        tf_params = TIMEFRAME_PARAMS.get("1h", {}).copy()
        if pair in PRODUCT_OVERRIDES:
            tf_params.update(PRODUCT_OVERRIDES[pair])

        # Apply entry overrides (rsi_long, rsi_short, bb_touch_tol, adx_range_max)
        for key in ("rsi_long", "rsi_short", "bb_touch_tol", "adx_range_max"):
            if key in self._entry:
                tf_params[key] = self._entry[key]

        # evaluate_range_signal uses module-level BB_WIDTH_MIN — already patched by engine
        result = evaluate_range_signal(ind_1h, tf_params)

        if not result["range_valid"]:
            return None

        # OBV signal
        obv = ind_4h.get("obv")
        obv_ema = ind_4h.get("obv_ema")

        # ─── LONG ───
        if result["signal_long"] == 1:
            strength = "STRONG" if any("STRONG" in r for r in result["reasons"]) else "WEAK"
            obv_signal = self._obv_direction(obv, obv_ema, "LONG")
            score = self._scorer.score_range(
                strength=strength,
                volume_ratio=volume_ratio,
                obv_signal=obv_signal,
            )
            return Signal(
                pair=pair, direction="LONG", strategy=self.name,
                strength=strength, entry_price=ind_1h.get("price", 0),
                reasons=list(result["reasons"]), score=score,
            )

        # ─── SHORT ───
        if result["signal_short"] == -1:
            strength = "STRONG" if any("STRONG" in r for r in result["reasons"]) else "WEAK"
            obv_signal = self._obv_direction(obv, obv_ema, "SHORT")
            score = self._scorer.score_range(
                strength=strength,
                volume_ratio=volume_ratio,
                obv_signal=obv_signal,
            )
            return Signal(
                pair=pair, direction="SHORT", strategy=self.name,
                strength=strength, entry_price=ind_1h.get("price", 0),
                reasons=list(result["reasons"]), score=score,
            )

        return None

    @staticmethod
    def _obv_direction(obv, obv_ema, direction: str) -> int:
        """Convert OBV state to +1 (confirm), -1 (against), 0 (neutral)."""
        if obv is None or obv_ema is None:
            return 0
        if direction == "LONG":
            return 1 if obv > obv_ema else (-1 if obv < obv_ema else 0)
        else:
            return 1 if obv < obv_ema else (-1 if obv > obv_ema else 0)

    def get_position_params(self) -> PositionParams:
        return PositionParams(
            risk_pct=self._pos.get("risk_pct", 0.02),
            leverage=self._pos.get("leverage", 8),
            sl_atr_mult=self._pos.get("sl_atr_mult", 1.2),
            min_rr=self._pos.get("min_rr", 2.3),
        )
