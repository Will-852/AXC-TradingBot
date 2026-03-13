"""
test_bocpd.py — Unit tests for RegimeBOCPD.

Tests:
  1. Stable sequence → run length grows, low P(changepoint)
  2. Mean shift → P(changepoint) spike, regime switch
  3. Variance shift → P(changepoint) spike
  4. Cold start → UNKNOWN until min_samples
  5. Persistence → save + load + replay == same result
  6. Interface → same output format as RegimeHMM.update()
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

from trader_cycle.strategies.regime_bocpd import RegimeBOCPD, _student_t_logpdf


# ─── Helpers ───

def _make_indicators(close: float, atr: float) -> dict:
    """Minimal 4H indicator dict for BOCPD (only needs price + atr)."""
    return {"price": close, "atr": atr}


def _feed_stable(bocpd: RegimeBOCPD, n: int, close: float = 50000.0, atr: float = 500.0):
    """Feed n stable candles."""
    results = []
    for _ in range(n):
        result = bocpd.update(_make_indicators(close, atr))
        results.append(result)
    return results


# ─── Tests ───

class TestStudentT:
    """Test the Student-t logpdf helper."""

    def test_known_value(self):
        """Standard normal (df→∞) at x=0 should be ≈ -0.919."""
        # df=1000 ≈ normal, var=1, mu=0
        logp = _student_t_logpdf(0.0, df=1000, mu=0.0, var=1.0)
        assert -1.0 < logp < -0.8

    def test_tails_lower(self):
        """Points further from mean should have lower density."""
        logp_center = _student_t_logpdf(0.0, df=5, mu=0.0, var=1.0)
        logp_tail = _student_t_logpdf(3.0, df=5, mu=0.0, var=1.0)
        assert logp_center > logp_tail

    def test_zero_var_safe(self):
        """Zero variance should return -inf-like value, not crash."""
        logp = _student_t_logpdf(0.0, df=5, mu=0.0, var=0.0)
        assert logp < -1e100


class TestColdStart:
    """BOCPD should return UNKNOWN before min_samples."""

    def test_unknown_before_threshold(self):
        bocpd = RegimeBOCPD(min_samples=30)
        for i in range(29):
            regime, conf, crash = bocpd.update(_make_indicators(50000.0, 500.0))
            assert regime == "UNKNOWN"
            assert conf == 0.0
            assert crash is False

    def test_active_after_threshold(self):
        bocpd = RegimeBOCPD(min_samples=30)
        results = _feed_stable(bocpd, 35)
        # Last few should not be UNKNOWN
        last = results[-1]
        assert last[0] in ("RANGE", "TREND", "CRASH")
        assert last[1] > 0.0


class TestStableSequence:
    """Stable data → run length grows, confidence high."""

    def test_confidence_increases(self):
        bocpd = RegimeBOCPD(min_samples=20, hazard_rate=0.02)
        results = _feed_stable(bocpd, 60, close=50000.0, atr=500.0)

        # After warmup, confidence should be high (run length growing)
        confs = [r[1] for r in results if r[0] != "UNKNOWN"]
        assert len(confs) > 10
        # Last 10 should have high confidence (> 0.5)
        for c in confs[-10:]:
            assert c > 0.5

    def test_no_regime_flipping(self):
        """Stable data should not flip regimes randomly."""
        bocpd = RegimeBOCPD(min_samples=20)
        results = _feed_stable(bocpd, 80, close=50000.0, atr=500.0)
        regimes = [r[0] for r in results if r[0] != "UNKNOWN"]
        # Should be mostly one regime
        from collections import Counter
        counts = Counter(regimes)
        dominant = counts.most_common(1)[0][1]
        assert dominant / len(regimes) > 0.7


class TestMeanShift:
    """Sudden volatility increase should trigger changepoint."""

    def test_changepoint_detected(self):
        bocpd = RegimeBOCPD(min_samples=20, hazard_rate=0.05)  # higher hazard = faster detection

        # Phase 1: low volatility
        _feed_stable(bocpd, 50, close=50000.0, atr=300.0)

        # Record pre-shift P(r=0)
        p_changepoint_before = float(bocpd._run_length_dist[0])

        # Phase 2: high volatility (3× increase)
        max_p_changepoint = 0.0
        for _ in range(30):
            bocpd.update(_make_indicators(50000.0, 900.0))
            max_p_changepoint = max(max_p_changepoint, float(bocpd._run_length_dist[0]))

        # P(changepoint) should increase after mean shift
        assert max_p_changepoint > p_changepoint_before


class TestVarianceShift:
    """Gradually increasing volatility should eventually trigger detection."""

    def test_gradual_shift(self):
        bocpd = RegimeBOCPD(min_samples=20)

        # Phase 1: stable
        _feed_stable(bocpd, 50, close=50000.0, atr=300.0)

        # Phase 2: gradually increasing volatility
        last_regime = None
        regime_changed = False
        for i in range(50):
            atr = 300.0 + i * 20  # 300 → 1300
            result = bocpd.update(_make_indicators(50000.0, atr))
            if result[0] != "UNKNOWN":
                if last_regime is not None and result[0] != last_regime:
                    regime_changed = True
                last_regime = result[0]

        # Should detect regime change at some point
        assert regime_changed


class TestCrashConfirmed:
    """crash_confirmed should require 85th percentile gate."""

    def test_extreme_vol_triggers_crash(self):
        bocpd = RegimeBOCPD(min_samples=20)

        # Feed moderate volatility
        _feed_stable(bocpd, 60, close=50000.0, atr=500.0)

        # Now feed extreme volatility
        results = []
        for _ in range(20):
            result = bocpd.update(_make_indicators(50000.0, 3000.0))
            results.append(result)

        # At some point should get CRASH + crash_confirmed
        crash_results = [r for r in results if r[0] == "CRASH" and r[2] is True]
        # Extreme vol (6× normal) should trigger crash_confirmed
        assert len(crash_results) > 0


class TestPersistence:
    """Save + load should reproduce same results."""

    def test_save_load_replay(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bocpd_state.json")

            bocpd1 = RegimeBOCPD(min_samples=20)
            _feed_stable(bocpd1, 50, close=50000.0, atr=500.0)
            result1 = bocpd1.update(_make_indicators(50000.0, 500.0))
            bocpd1.save_state(path)

            # Load into new instance
            bocpd2 = RegimeBOCPD(min_samples=20)
            loaded = bocpd2.load_state(path)
            assert loaded is True

            # Feed same candle → should get similar result
            result2 = bocpd2.update(_make_indicators(50000.0, 500.0))
            assert result2[0] == result1[0]  # same regime

    def test_empty_state_file(self):
        """Non-existent state file should return False."""
        bocpd = RegimeBOCPD()
        assert bocpd.load_state("/nonexistent/path.json") is False


class TestInterface:
    """Output format should match RegimeHMM.update()."""

    def test_return_types(self):
        bocpd = RegimeBOCPD(min_samples=5)
        _feed_stable(bocpd, 10, close=50000.0, atr=500.0)
        result = bocpd.update(_make_indicators(50000.0, 500.0))

        assert isinstance(result, tuple)
        assert len(result) == 3
        assert isinstance(result[0], str)  # regime label
        assert isinstance(result[1], float)  # confidence
        assert isinstance(result[2], bool)  # crash_confirmed

    def test_regime_labels(self):
        """Should only return valid labels."""
        bocpd = RegimeBOCPD(min_samples=5)
        for _ in range(20):
            regime, _, _ = bocpd.update(_make_indicators(50000.0, 500.0))
            assert regime in ("RANGE", "TREND", "CRASH", "UNKNOWN")

    def test_confidence_range(self):
        """Confidence should be in [0, 1]."""
        bocpd = RegimeBOCPD(min_samples=5)
        for _ in range(20):
            _, conf, _ = bocpd.update(_make_indicators(50000.0, 500.0))
            assert 0.0 <= conf <= 1.0

    def test_missing_indicators(self):
        """Missing price/atr should return UNKNOWN."""
        bocpd = RegimeBOCPD()
        result = bocpd.update({"price": None, "atr": 500.0})
        assert result == ("UNKNOWN", 0.0, False)

        result = bocpd.update({"price": 50000.0, "atr": None})
        assert result == ("UNKNOWN", 0.0, False)


class TestTruncation:
    """Run length distribution should not grow beyond max_run_length."""

    def test_bounded_size(self):
        max_rl = 50
        bocpd = RegimeBOCPD(min_samples=10, max_run_length=max_rl)
        _feed_stable(bocpd, 100, close=50000.0, atr=500.0)
        assert len(bocpd._run_length_dist) <= max_rl + 1
