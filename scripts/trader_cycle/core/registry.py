"""
registry.py — Strategy 註冊表
加新策略只需 register()，pipeline 自動 pick up
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..strategies.base import StrategyBase


class StrategyRegistry:
    """
    Global strategy registry.
    Pipeline queries this for the active strategy based on market mode.
    """
    _strategies: dict[str, "StrategyBase"] = {}

    @classmethod
    def register(cls, strategy: "StrategyBase") -> None:
        """Register a strategy. Key = strategy.mode (e.g. 'RANGE', 'TREND')."""
        cls._strategies[strategy.mode] = strategy

    @classmethod
    def get(cls, mode: str) -> "StrategyBase | None":
        """Get strategy for a market mode. Returns None if not registered."""
        return cls._strategies.get(mode)

    @classmethod
    def all_strategies(cls) -> dict[str, "StrategyBase"]:
        """Return all registered strategies."""
        return dict(cls._strategies)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered strategies (for testing)."""
        cls._strategies.clear()
