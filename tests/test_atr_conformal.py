"""
test_atr_conformal.py — Unit tests for ATRConformal.

Tests:
  1. Stable ATR → q_hat small, atr_high ≈ atr
  2. Volatile ATR → q_hat large, atr_high >> atr
  3. Regime switch (有歷史) → warm start from target bank
  4. Regime switch (冇歷史) → inflated scores from old bank
  5. Bank overflow → FIFO trimming
  6. Persistence → save + load preserves all banks
"""

import json
import os
import tempfile

import numpy as np
import pytest

# ─── Path setup ───
import sys
AXC_HOME = os.path.expanduser("~/projects/axc-trading")
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from trader_cycle.risk.atr_conformal import ATRConformal


# ─── Tests ───

class TestBasicOperation:
    """Basic update/get_atr_high flow."""

    def test_initial_no_q_hat(self):
        """First call should return None (no previous ATR to compute score)."""
        cp = ATRConformal(min_scores=5)
        q = cp.update("RANGE", atr=500.0, true_range=480.0)
        assert q is None  # first call, no prev_atr

    def test_fallback_when_no_data(self):
        """get_atr_high should use fallback_mult when q_hat is None."""
        cp = ATRConformal(fallback_mult=1.5)
        atr_high = cp.get_atr_high(500.0)
        assert atr_high == 750.0  # 500 × 1.5


class TestStableATR:
    """Stable ATR → small q_hat."""

    def test_small_q_hat(self):
        cp = ATRConformal(alpha=0.10, min_scores=10)

        # Feed stable data: ATR ≈ true_range (small residuals)
        for _ in range(30):
            cp.update("RANGE", atr=500.0, true_range=505.0)

        atr_high = cp.get_atr_high(500.0)
        # q_hat should be small (scores ≈ 5 each time)
        assert atr_high < 520.0  # 500 + small q_hat
        assert atr_high > 500.0  # but > raw ATR


class TestVolatileATR:
    """Volatile ATR → large q_hat."""

    def test_large_q_hat(self):
        cp = ATRConformal(alpha=0.10, min_scores=10)

        # Feed volatile data: big residuals
        np.random.seed(42)
        for _ in range(30):
            true_range = 500.0 + np.random.normal(0, 200)
            cp.update("RANGE", atr=500.0, true_range=max(true_range, 100))

        atr_high = cp.get_atr_high(500.0)
        # q_hat should be large
        assert atr_high > 600.0  # significant uncertainty


class TestRegimeSwitchWarmStart:
    """Switching to a regime with history → warm start from target bank."""

    def test_warm_start(self):
        cp = ATRConformal(alpha=0.10, min_scores=10)

        # Build RANGE bank
        for _ in range(25):
            cp.update("RANGE", atr=500.0, true_range=510.0)

        # Build TREND bank
        for _ in range(25):
            cp.update("TREND", atr=800.0, true_range=850.0)

        # Switch back to RANGE → should use RANGE bank (warm start)
        q = cp.update("RANGE", atr=500.0, true_range=510.0)
        assert q is not None

        atr_high = cp.get_atr_high(500.0)
        # Should be based on RANGE bank scores (≈10 each), not TREND (≈50)
        assert atr_high < 520.0


class TestRegimeSwitchColdStart:
    """Switching to a regime with no history → inflated from old bank."""

    def test_inflated_scores(self):
        cp = ATRConformal(
            alpha=0.10, min_scores=10,
            inflation_factor=1.5,
        )

        # Build only RANGE bank
        for _ in range(25):
            cp.update("RANGE", atr=500.0, true_range=510.0)

        # Get RANGE q_hat as baseline
        q_range = cp._compute_q_hat("RANGE")
        assert q_range is not None

        # Switch to CRASH (empty bank) → should use inflated RANGE scores
        q_crash = cp._compute_q_hat("CRASH")
        assert q_crash is not None
        assert q_crash > q_range  # inflated should be larger


class TestFIFOTrimming:
    """Bank should not exceed max_scores."""

    def test_max_scores_respected(self):
        cp = ATRConformal(max_scores=50)

        for _ in range(100):
            cp.update("RANGE", atr=500.0, true_range=510.0)

        assert len(cp._banks["RANGE"]) <= 50


class TestPersistence:
    """Save + load should preserve all banks."""

    def test_save_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "cp_state.json")

            cp1 = ATRConformal(min_scores=5)
            # Build some data in RANGE and TREND banks
            for _ in range(15):
                cp1.update("RANGE", atr=500.0, true_range=510.0)
            for _ in range(10):
                cp1.update("TREND", atr=800.0, true_range=830.0)
            cp1.save_state(path)

            # Verify file exists and is valid JSON
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert "banks" in data
            assert "RANGE" in data["banks"]

            # Load into new instance
            cp2 = ATRConformal(min_scores=5)
            loaded = cp2.load_state(path)
            assert loaded is True

            # Banks should match
            assert len(cp2._banks["RANGE"]) == len(cp1._banks["RANGE"])
            assert len(cp2._banks["TREND"]) == len(cp1._banks["TREND"])
            assert cp2._active_regime == cp1._active_regime

    def test_nonexistent_file(self):
        cp = ATRConformal()
        assert cp.load_state("/nonexistent/cp.json") is False


class TestEdgeCases:
    """Edge cases and safety."""

    def test_unknown_regime_handled(self):
        """UNKNOWN regime should not crash."""
        cp = ATRConformal(min_scores=5)
        result = cp.update("UNKNOWN", atr=500.0, true_range=510.0)
        # Should not crash, returns None (no scores yet)
        assert result is None or isinstance(result, float)

    def test_zero_atr(self):
        """Zero ATR should not crash."""
        cp = ATRConformal()
        result = cp.update("RANGE", atr=0.0, true_range=0.0)
        assert result is None or isinstance(result, float)

    def test_get_atr_high_with_q_hat(self):
        """After enough data, get_atr_high should return atr + q_hat."""
        cp = ATRConformal(min_scores=5)
        for _ in range(20):
            cp.update("RANGE", atr=500.0, true_range=520.0)

        atr_high = cp.get_atr_high(500.0)
        assert atr_high > 500.0
        assert atr_high < 600.0  # q_hat ≈ 20 (score = |500-520| = 20)

    def test_multiple_regimes_independent(self):
        """Each regime bank should accumulate independently."""
        cp = ATRConformal(min_scores=5, max_scores=100)

        # RANGE: small residuals
        for _ in range(10):
            cp.update("RANGE", atr=500.0, true_range=505.0)

        # CRASH: large residuals
        for _ in range(10):
            cp.update("CRASH", atr=500.0, true_range=700.0)

        # RANGE bank should have small scores, CRASH bank large scores
        range_scores = cp._banks["RANGE"]
        crash_scores = cp._banks["CRASH"]

        assert len(range_scores) > 0
        assert len(crash_scores) > 0

        # Average score in CRASH should be much larger
        avg_range = sum(range_scores) / len(range_scores)
        avg_crash = sum(crash_scores) / len(crash_scores)
        assert avg_crash > avg_range * 5
