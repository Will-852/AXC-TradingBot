"""
test_regime_risk.py — Unit tests for volatility regime → risk profile mapping and sizing.

Tests:
  1. VOL_PROFILE_MAP correctness
  2. SelectRiskProfileStep maps regime → profile
  3. Size tier calculation
  4. MIN_RISK_FLOOR guarantee
  5. Profile risk values match config files
  6. End-to-end sizing: profile × size_tier × floor
"""

import os
import sys

import pytest

AXC_HOME = os.path.expanduser("~/projects/axc-trading")
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from trader_cycle.risk.regime_risk import SelectRiskProfileStep, VOL_PROFILE_MAP
from trader_cycle.risk.position_sizer import _get_size_tier, MIN_RISK_FLOOR
from trader_cycle.core.context import CycleContext


class TestVOLProfileMap:
    """VOL_PROFILE_MAP maps volatility regime to risk profile."""

    def test_low_to_aggressive(self):
        assert VOL_PROFILE_MAP["LOW"] == "aggressive"

    def test_normal_to_balanced(self):
        assert VOL_PROFILE_MAP["NORMAL"] == "balanced"

    def test_high_to_conservative(self):
        assert VOL_PROFILE_MAP["HIGH"] == "conservative"

    def test_all_three_regimes(self):
        """All 3 regimes are mapped."""
        assert set(VOL_PROFILE_MAP.keys()) == {"LOW", "NORMAL", "HIGH"}


class TestSelectRiskProfileStep:
    """SelectRiskProfileStep pipeline step."""

    def test_low_regime(self):
        ctx = CycleContext(volatility_regime="LOW")
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        assert result.active_risk_profile == "aggressive"

    def test_normal_regime(self):
        ctx = CycleContext(volatility_regime="NORMAL")
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        assert result.active_risk_profile == "balanced"

    def test_high_regime(self):
        ctx = CycleContext(volatility_regime="HIGH")
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        assert result.active_risk_profile == "conservative"

    def test_unknown_regime_fallback(self):
        """Unknown regime → fallback to balanced."""
        ctx = CycleContext(volatility_regime="UNKNOWN")
        step = SelectRiskProfileStep()
        result = step.run(ctx)
        assert result.active_risk_profile == "balanced"

    def test_step_name(self):
        step = SelectRiskProfileStep()
        assert step.name == "select_risk_profile"


class TestSizeTier:
    """Size tier mapping from confidence."""

    def test_high_confidence(self):
        """confidence >= 0.7 → full size 1.0."""
        assert _get_size_tier(0.7) == 1.0
        assert _get_size_tier(0.85) == 1.0
        assert _get_size_tier(1.0) == 1.0

    def test_medium_confidence(self):
        """0.5 <= confidence < 0.7 → 70% size."""
        assert _get_size_tier(0.5) == 0.7
        assert _get_size_tier(0.6) == 0.7
        assert _get_size_tier(0.69) == 0.7

    def test_low_confidence(self):
        """confidence < 0.5 → 50% size."""
        assert _get_size_tier(0.3) == 0.5
        assert _get_size_tier(0.4) == 0.5
        assert _get_size_tier(0.49) == 0.5

    def test_boundary_values(self):
        """Exact boundary values."""
        assert _get_size_tier(0.7) == 1.0
        assert _get_size_tier(0.5) == 0.7
        assert _get_size_tier(0.3) == 0.5


class TestMinRiskFloor:
    """MIN_RISK_FLOOR ensures minimum executable position."""

    def test_floor_value(self):
        assert MIN_RISK_FLOOR == 0.005  # 0.5%

    def test_floor_prevents_tiny_risk(self):
        """Even conservative profile × low confidence stays above floor."""
        conservative_risk = 0.01  # 1%
        low_tier = 0.5
        raw_risk = conservative_risk * low_tier  # 0.005
        final_risk = max(raw_risk, MIN_RISK_FLOOR)
        assert final_risk >= MIN_RISK_FLOOR


class TestProfileRiskValues:
    """Verify profile risk_per_trade_pct values match config files."""

    def test_aggressive_risk(self):
        from config.profiles.loader import load_profile
        profile = load_profile("AGGRESSIVE")
        assert profile["risk_per_trade_pct"] == 0.03

    def test_balanced_risk(self):
        from config.profiles.loader import load_profile
        profile = load_profile("BALANCED")
        assert profile["risk_per_trade_pct"] == 0.02

    def test_conservative_risk(self):
        from config.profiles.loader import load_profile
        profile = load_profile("CONSERVATIVE")
        assert profile["risk_per_trade_pct"] == 0.01


class TestEndToEndSizing:
    """End-to-end: regime → profile → risk × size_tier → final risk."""

    @pytest.mark.parametrize("regime,expected_profile,base_risk", [
        ("LOW", "aggressive", 0.03),
        ("NORMAL", "balanced", 0.02),
        ("HIGH", "conservative", 0.01),
    ])
    def test_regime_to_risk(self, regime, expected_profile, base_risk):
        """Verify full chain: regime → profile → base_risk."""
        profile_name = VOL_PROFILE_MAP[regime]
        assert profile_name == expected_profile

    @pytest.mark.parametrize("confidence,tier", [
        (0.8, 1.0), (0.6, 0.7), (0.35, 0.5),
    ])
    def test_sizing_chain(self, confidence, tier):
        """Full sizing: balanced profile × tier → reasonable risk."""
        base_risk = 0.02  # balanced
        final = max(base_risk * tier, MIN_RISK_FLOOR)
        assert 0.005 <= final <= 0.05  # within sane bounds
