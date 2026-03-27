"""
test_regime_risk.py — Unit tests for 2-Zone system (2026-03-23 refactor).

Tests:
  1. SelectRiskProfileStep: confidence → zone_a / zone_b
  2. Hysteresis: Zone B needs consecutive confirmations
  3. Zone profile loading and key values
  4. Signal-level zone override thresholds
"""

import os
import sys

import pytest

AXC_HOME = os.path.expanduser("~/projects/axc-trading")
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

from trader_cycle.risk.regime_risk import (
    SelectRiskProfileStep,
    ZONE_B_THRESHOLD,
    ZONE_B_CONFIRM_CYCLES,
    _zone_b_consecutive,
)
from trader_cycle.core.context import CycleContext


class TestSelectRiskProfileStep:
    """Zone selection based on regime confidence + hysteresis."""

    def setup_method(self):
        """Reset module-level counter before each test."""
        import trader_cycle.risk.regime_risk as rr
        rr._zone_b_consecutive = 0

    def test_low_confidence_zone_a(self):
        ctx = CycleContext(regime_confidence=0.3)
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        assert result.active_risk_profile == "zone_a"

    def test_high_confidence_single_cycle_still_zone_a(self):
        """Single high-confidence cycle should NOT upgrade to Zone B (hysteresis)."""
        ctx = CycleContext(regime_confidence=0.85)
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        # First cycle: consecutive=1, needs 2 → still zone_a
        assert result.active_risk_profile == "zone_a"

    def test_high_confidence_two_cycles_zone_b(self):
        """Two consecutive high-confidence cycles → Zone B."""
        step = SelectRiskProfileStep()
        # Cycle 1
        ctx1 = CycleContext(regime_confidence=0.85)
        step.run(ctx1)
        # Cycle 2
        ctx2 = CycleContext(regime_confidence=0.80)
        result = step.run(ctx2)
        assert result.active_risk_profile == "zone_b"

    def test_interruption_resets_counter(self):
        """A low-confidence cycle resets the counter."""
        step = SelectRiskProfileStep()
        # Cycle 1: high
        step.run(CycleContext(regime_confidence=0.85))
        # Cycle 2: low → resets
        step.run(CycleContext(regime_confidence=0.40))
        # Cycle 3: high again → consecutive=1, not enough
        ctx3 = CycleContext(regime_confidence=0.90)
        result = step.run(ctx3)
        assert result.active_risk_profile == "zone_a"

    def test_downgrade_immediate(self):
        """Drop below threshold → immediate downgrade to Zone A."""
        step = SelectRiskProfileStep()
        # Build up to Zone B
        step.run(CycleContext(regime_confidence=0.85))
        step.run(CycleContext(regime_confidence=0.80))  # now zone_b
        # Drop
        ctx = CycleContext(regime_confidence=0.50)
        result = step.run(ctx)
        assert result.active_risk_profile == "zone_a"

    def test_cold_start_zone_a(self):
        """Cold start (confidence=0) → Zone A."""
        ctx = CycleContext(regime_confidence=0.0)
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        assert result.active_risk_profile == "zone_a"

    def test_step_name(self):
        step = SelectRiskProfileStep()
        assert step.name == "select_risk_profile"


class TestZoneConstants:
    """Zone thresholds and config."""

    def test_zone_b_threshold(self):
        assert ZONE_B_THRESHOLD == 0.70

    def test_zone_b_confirm_cycles(self):
        assert ZONE_B_CONFIRM_CYCLES == 2


class TestZoneProfileValues:
    """Verify zone profile configs load correctly."""

    def test_zone_a_loads(self):
        from config.profiles.loader import load_profile
        za = load_profile("ZONE_A")
        assert za["zone"] == "A"
        assert za["margin_pct"] == 0.25   # updated 2026-03-28: was 0.03
        assert za["sl_pct_base"] == 0.010
        assert za["range_leverage"] == 20  # updated 2026-03-28: was 8
        assert za["max_open_positions"] == 2

    def test_zone_b_loads(self):
        from config.profiles.loader import load_profile
        zb = load_profile("ZONE_B")
        assert zb["zone"] == "B"
        assert zb["margin_pct"] == 0.25   # updated 2026-03-28: was 0.03
        assert zb["sl_pct_base"] == 0.006
        assert zb["range_leverage"] == 18
        assert zb["max_open_positions"] == 1

    def test_only_zones_discovered(self):
        from config.profiles.loader import list_profiles
        profiles = list_profiles()
        assert "ZONE_A" in profiles
        assert "ZONE_B" in profiles
        assert "AGGRESSIVE" not in profiles
        assert "BALANCED" not in profiles
        assert "CONSERVATIVE" not in profiles

    def test_context_default_is_zone_a(self):
        ctx = CycleContext()
        assert ctx.active_risk_profile == "zone_a"
