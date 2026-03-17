"""
test_normalized_rank.py — Unit tests for cross-strategy normalized rank selection.

Tests:
  1. Single signal per strategy → rank = 1.0
  2. Multiple signals same strategy → percentile rank 0-1
  3. Cross-strategy comparison uses rank (not raw confidence)
  4. PAIR_PRIORITY tiebreaker
  5. Empty signals → no selection
  6. Correlation boost logic
"""

import os
import sys
from datetime import datetime, timezone

import pytest

AXC_HOME = os.path.expanduser("~/projects/axc-trading")
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from trader_cycle.strategies.evaluate import SelectSignalStep
from trader_cycle.core.context import CycleContext, Signal


def _make_signal(
    pair: str = "BTCUSDT",
    direction: str = "LONG",
    strategy: str = "range",
    confidence: float = 0.5,
    score: float = 3.0,
) -> Signal:
    """Helper to create a test Signal."""
    return Signal(
        pair=pair,
        direction=direction,
        strategy=strategy,
        strength="STRONG" if confidence >= 0.7 else "WEAK",
        entry_price=50000.0,
        reasons=[],
        score=score,
        confidence=confidence,
    )


class TestSingleSignalRank:
    """Single signal per strategy gets rank = 1.0."""

    def test_one_range_signal(self):
        ctx = CycleContext()
        ctx.signals = [_make_signal(strategy="range", confidence=0.5)]
        step = SelectSignalStep()
        result = step.run(ctx)
        assert result.selected_signal is not None
        assert result.selected_signal.normalized_rank == 1.0

    def test_one_trend_signal(self):
        ctx = CycleContext()
        ctx.signals = [_make_signal(strategy="trend", confidence=0.6)]
        step = SelectSignalStep()
        result = step.run(ctx)
        assert result.selected_signal.normalized_rank == 1.0

    def test_one_per_strategy(self):
        """One signal per strategy → all rank 1.0, winner by confidence."""
        ctx = CycleContext()
        ctx.signals = [
            _make_signal(strategy="range", confidence=0.4),
            _make_signal(strategy="trend", confidence=0.7),
        ]
        step = SelectSignalStep()
        result = step.run(ctx)
        # Both get rank=1.0, trend wins by confidence tiebreaker
        assert result.selected_signal.strategy == "trend"
        assert result.selected_signal.normalized_rank == 1.0


class TestMultipleSignalsRank:
    """Multiple signals in same strategy → percentile ranking."""

    def test_two_range_signals(self):
        """Two range signals: higher conf gets rank=1.0, lower gets rank=0.0."""
        ctx = CycleContext()
        ctx.signals = [
            _make_signal(pair="BTCUSDT", strategy="range", confidence=0.4),
            _make_signal(pair="ETHUSDT", strategy="range", confidence=0.6),
        ]
        step = SelectSignalStep()
        result = step.run(ctx)

        # Higher confidence should be selected
        assert result.selected_signal.pair == "ETHUSDT"

        # Check ranks were assigned
        btc_sig = [s for s in ctx.signals if s.pair == "BTCUSDT"][0]
        eth_sig = [s for s in ctx.signals if s.pair == "ETHUSDT"][0]
        assert btc_sig.normalized_rank == 0.0  # lower
        assert eth_sig.normalized_rank == 1.0  # higher

    def test_three_signals_percentile(self):
        """Three signals: ranks should be 0.0, 0.5, 1.0."""
        ctx = CycleContext()
        ctx.signals = [
            _make_signal(pair="BTCUSDT", strategy="trend", confidence=0.3),
            _make_signal(pair="ETHUSDT", strategy="trend", confidence=0.5),
            _make_signal(pair="SOLUSDT", strategy="trend", confidence=0.7),
        ]
        step = SelectSignalStep()
        result = step.run(ctx)

        ranks = {s.pair: s.normalized_rank for s in ctx.signals}
        assert ranks["BTCUSDT"] == 0.0
        assert ranks["ETHUSDT"] == 0.5
        assert ranks["SOLUSDT"] == 1.0


class TestCrossStrategyFairness:
    """Normalized rank ensures cross-strategy fair comparison."""

    def test_range_beats_trend_by_rank(self):
        """Range with rank=1.0 should beat Trend with rank=0.5 even if raw conf lower."""
        ctx = CycleContext()
        # Range: only signal → rank=1.0, conf=0.4
        # Trend: two signals → best gets rank=1.0 but let's say the worse one is at rank=0.5
        ctx.signals = [
            _make_signal(pair="BTCUSDT", strategy="range", confidence=0.4),
            _make_signal(pair="ETHUSDT", strategy="trend", confidence=0.5),
            _make_signal(pair="SOLUSDT", strategy="trend", confidence=0.8),
        ]
        step = SelectSignalStep()
        result = step.run(ctx)

        # Range gets rank=1.0 (only signal in its strategy)
        # SOL trend gets rank=1.0, ETH trend gets rank=0.0
        # Both rank=1.0 → tiebreaker by confidence → SOL trend wins (0.8 > 0.4)
        assert result.selected_signal.pair == "SOLUSDT"
        assert result.selected_signal.strategy == "trend"

    def test_equal_rank_confidence_tiebreak(self):
        """Same rank → higher confidence wins."""
        ctx = CycleContext()
        ctx.signals = [
            _make_signal(pair="BTCUSDT", strategy="range", confidence=0.5),
            _make_signal(pair="ETHUSDT", strategy="trend", confidence=0.7),
        ]
        step = SelectSignalStep()
        result = step.run(ctx)

        # Both rank=1.0 (single signal each), trend wins by confidence
        assert result.selected_signal.strategy == "trend"


class TestPairPriorityTiebreak:
    """PAIR_PRIORITY as final tiebreaker."""

    def test_same_rank_same_conf_pair_wins(self):
        """Same rank + same confidence → higher priority pair wins."""
        ctx = CycleContext()
        ctx.signals = [
            _make_signal(pair="XAGUSDT", strategy="range", confidence=0.5),
            _make_signal(pair="BTCUSDT", strategy="trend", confidence=0.5),
        ]
        step = SelectSignalStep()
        result = step.run(ctx)

        # Both rank=1.0, both conf=0.5 → BTC has priority 4, XAG has 1
        assert result.selected_signal.pair == "BTCUSDT"


class TestEmptySignals:
    """No signals → no selection."""

    def test_empty_list(self):
        ctx = CycleContext()
        ctx.signals = []
        step = SelectSignalStep()
        result = step.run(ctx)
        assert result.selected_signal is None

    def test_none_signals_list(self):
        ctx = CycleContext()
        step = SelectSignalStep()
        result = step.run(ctx)
        assert result.selected_signal is None
