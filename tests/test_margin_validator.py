"""
test_margin_validator.py — MarginUtilizationValidator + aggregate margin monitoring.

Tests three scenarios: below threshold, warning zone, hard block.
Also tests the post-trade aggregate margin alert in ManagePositionsStep.
"""

import os
import sys

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import pytest
from scripts.trader_cycle.core.context import CycleContext, Position, Signal
from scripts.trader_cycle.risk.validators import MarginUtilizationValidator


@pytest.fixture
def validator():
    return MarginUtilizationValidator()


def _make_ctx(balance, positions, signal_margin):
    """Helper: build CycleContext with positions + a signal requiring margin."""
    ctx = CycleContext(account_balance=balance)
    ctx.open_positions = positions
    ctx.selected_signal = Signal(
        pair="ETHUSDT", direction="LONG", strategy="range", strength="STRONG",
        margin_required=signal_margin, leverage=8,
    )
    return ctx


class TestMarginUtilizationValidator:
    """Three cases: below threshold, warning zone, hard block."""

    def test_below_threshold(self, validator):
        """20% total utilization → pass, no message."""
        ctx = _make_ctx(
            balance=1000.0,
            positions=[Position(pair="BTCUSDT", direction="LONG", isolated_wallet=100.0)],
            signal_margin=100.0,  # 100+100=200 / 1000 = 20%
        )
        result = validator.validate(ctx)
        assert result.passed is True
        assert result.hard_block is False
        assert result.message == ""

    def test_warning_zone(self, validator):
        """45% total utilization → pass with warning message."""
        ctx = _make_ctx(
            balance=1000.0,
            positions=[Position(pair="BTCUSDT", direction="LONG", isolated_wallet=300.0)],
            signal_margin=150.0,  # 300+150=450 / 1000 = 45%
        )
        result = validator.validate(ctx)
        assert result.passed is True
        assert result.hard_block is False
        assert "approaching" in result.message

    def test_hard_block(self, validator):
        """55% total utilization → blocked."""
        ctx = _make_ctx(
            balance=1000.0,
            positions=[Position(pair="BTCUSDT", direction="LONG", isolated_wallet=350.0)],
            signal_margin=200.0,  # 350+200=550 / 1000 = 55%
        )
        result = validator.validate(ctx)
        assert result.passed is False
        assert result.hard_block is True
        assert "exceed" in result.message

    def test_no_signal_passes(self, validator):
        """No selected signal → skip (pass)."""
        ctx = CycleContext(account_balance=1000.0)
        ctx.selected_signal = None
        result = validator.validate(ctx)
        assert result.passed is True

    def test_zero_balance_skips(self, validator):
        """Zero balance → fail-open (pass)."""
        ctx = _make_ctx(balance=0.0, positions=[], signal_margin=100.0)
        result = validator.validate(ctx)
        assert result.passed is True

    def test_fallback_margin_estimate(self, validator):
        """isolated_wallet=0 → estimate from size*entry/leverage."""
        pos = Position(
            pair="BTCUSDT", direction="LONG",
            isolated_wallet=0.0,  # no wallet data
            size=0.01, entry_price=50000.0,  # notional = 500
        )
        ctx = _make_ctx(
            balance=1000.0,
            positions=[pos],
            signal_margin=100.0,
            # pos margin estimate: 500 / 8 (from signal.leverage) = 62.5
            # total: 62.5 + 100 = 162.5 / 1000 = 16.25% → pass
        )
        result = validator.validate(ctx)
        assert result.passed is True

    def test_fallback_margin_blocks(self, validator):
        """Fallback estimate can also trigger block."""
        pos = Position(
            pair="BTCUSDT", direction="LONG",
            isolated_wallet=0.0,
            size=0.1, entry_price=50000.0,  # notional = 5000
        )
        ctx = _make_ctx(
            balance=1000.0,
            positions=[pos],
            signal_margin=100.0,
            # pos margin estimate: 5000 / 8 = 625
            # total: 625 + 100 = 725 / 1000 = 72.5% → block
        )
        result = validator.validate(ctx)
        assert result.passed is False
        assert result.hard_block is True


class TestAggregateMarginMonitoring:
    """ManagePositionsStep._check_aggregate_margin: post-trade alerts."""

    def test_alert_above_max(self):
        from scripts.trader_cycle.risk.risk_manager import ManagePositionsStep
        step = ManagePositionsStep()
        ctx = CycleContext(account_balance=1000.0)
        ctx.open_positions = [
            Position(pair="BTCUSDT", direction="LONG", isolated_wallet=300.0),
            Position(pair="ETHUSDT", direction="LONG", isolated_wallet=250.0),
        ]
        step._check_aggregate_margin(ctx)
        # 550/1000 = 55% > MAX_MARGIN_PCT (50%)
        assert any("Margin Utilization Alert" in m for m in ctx.telegram_messages)

    def test_warning_above_threshold(self):
        from scripts.trader_cycle.risk.risk_manager import ManagePositionsStep
        step = ManagePositionsStep()
        ctx = CycleContext(account_balance=1000.0)
        ctx.open_positions = [
            Position(pair="BTCUSDT", direction="LONG", isolated_wallet=450.0),
        ]
        step._check_aggregate_margin(ctx)
        # 450/1000 = 45% > MARGIN_WARNING_PCT (40%) but < MAX (50%)
        assert any("approaching" in w for w in ctx.warnings)
        assert not ctx.telegram_messages  # no TG alert for warning

    def test_no_alert_below_threshold(self):
        from scripts.trader_cycle.risk.risk_manager import ManagePositionsStep
        step = ManagePositionsStep()
        ctx = CycleContext(account_balance=1000.0)
        ctx.open_positions = [
            Position(pair="BTCUSDT", direction="LONG", isolated_wallet=200.0),
        ]
        step._check_aggregate_margin(ctx)
        # 200/1000 = 20% → no alert
        assert not ctx.telegram_messages
        assert not ctx.warnings
