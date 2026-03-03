"""
base.py — StrategyBase ABC
所有策略繼承呢個 class，實現 evaluate() 同 get_position_params()
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.context import CycleContext, Signal


@dataclass
class PositionParams:
    """Position sizing parameters for a strategy."""
    risk_pct: float          # e.g. 0.02 = 2%
    leverage: int            # e.g. 8
    sl_atr_mult: float       # e.g. 1.2 = 1.2×ATR for stop loss
    min_rr: float            # minimum reward:risk ratio
    tp_atr_mult: float | None = None  # for scalp: 2.5×ATR


class StrategyBase(ABC):
    """
    Abstract base for all trading strategies.
    加新策略：
      1. 建新 .py 繼承 StrategyBase
      2. 實現 evaluate() + get_position_params()
      3. 在 main.py StrategyRegistry.register(MyStrategy())
    """

    name: str = ""           # "range", "trend", "scalp"
    mode: str = ""           # "RANGE", "TREND" — matches market mode
    required_timeframes: list[str] = []  # ["4h", "1h"]

    @abstractmethod
    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> Signal | None:
        """
        Evaluate entry conditions for one pair.
        indicators = {"4h": {...}, "1h": {...}}
        Returns Signal if entry triggered, None otherwise.
        """
        ...

    @abstractmethod
    def get_position_params(self) -> PositionParams:
        """Return position sizing params for this strategy."""
        ...

    def evaluate_exit(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> str | None:
        """
        Check exit conditions for an open position.
        Returns exit reason string, or None if no exit.
        Override in subclass for strategy-specific exits.
        """
        return None
