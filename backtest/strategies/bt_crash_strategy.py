"""
bt_crash_strategy.py — Configurable Crash Strategy for backtest.

設計決定：同 production CrashStrategy 邏輯一致但閾值可配置。
SHORT-only, wider SL, conservative sizing.
"""

from __future__ import annotations

import os
import sys

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(_AXC, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal


class BTCrashStrategy(StrategyBase):
    """Configurable crash strategy for backtest.

    Configurable via entry_overrides:
      - rsi_entry: float (default 75)
      - volume_min: float (default 2.0)
    """

    name = "crash"
    mode = "CRASH"
    required_timeframes = ["4h", "1h"]

    def __init__(
        self,
        entry_overrides: dict | None = None,
        position_overrides: dict | None = None,
    ):
        self._entry = entry_overrides or {}
        self._pos = position_overrides or {}

    @property
    def rsi_entry(self) -> float:
        return self._entry.get("rsi_entry", 75)

    @property
    def volume_min(self) -> float:
        return self._entry.get("volume_min", 2.0)

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        ind_4h = indicators.get("4h")
        ind_1h = indicators.get("1h")
        if not ind_4h or not ind_1h:
            return None

        rsi = ind_1h.get("rsi")
        macd_hist = ind_1h.get("macd_hist")
        volume_ratio = ind_4h.get("volume_ratio", 1.0)
        price = ind_1h.get("price")

        if any(v is None for v in [rsi, macd_hist, price]):
            return None

        conditions = {
            "RSI_overbought": rsi > self.rsi_entry,
            "MACD_bearish": macd_hist < 0,
            "Volume_spike": volume_ratio > self.volume_min,
        }

        # 2-of-3 gate (mirrors production crash_strategy.py)
        if sum(conditions.values()) < 2:
            return None

        score = 3.0
        if rsi > 80:
            score += 0.5
        if volume_ratio > 3.0:
            score += 0.5
        if abs(macd_hist) > 0.01:
            score += 0.5

        return Signal(
            pair=pair,
            direction="SHORT",
            strategy=self.name,
            strength="STRONG" if score >= 4.0 else "WEAK",
            entry_price=price,
            reasons=[f"CRASH_SHORT: RSI={rsi:.1f} Vol={volume_ratio:.1f}x"],
            score=score,
        )

    def get_position_params(self) -> PositionParams:
        return PositionParams(
            risk_pct=self._pos.get("risk_pct", 0.01),
            leverage=self._pos.get("leverage", 5),
            sl_atr_mult=self._pos.get("sl_atr_mult", 2.0),
            min_rr=self._pos.get("min_rr", 1.5),
        )
