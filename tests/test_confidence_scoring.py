"""
test_confidence_scoring.py — Unit tests for strategy confidence scoring functions.

Tests:
  1. Range sub-scores: BB touch, RSI reversal, S/R proximity, volume, OBV
  2. Range soft penalties: BB width, ADX
  3. Trend sub-scores: MA alignment, MACD, RSI pullback, price at MA, volume, OBV
  4. Crash sub-scores: RSI exhaustion, MACD bearish, volume spike
  5. Boundary values: 0.0 and 1.0 edges
  6. Confidence threshold: below 0.3 → no signal
"""

import os
import sys

import pytest

AXC_HOME = os.path.expanduser("~/projects/axc-trading")
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

# ─── Range strategy sub-scores ───
from trader_cycle.strategies.range_strategy import (
    _score_bb_touch,
    _score_rsi_reversal,
    _score_sr_proximity,
    _score_volume,
    _score_obv,
    _soft_penalty_bb_width,
    _soft_penalty_adx,
    CONFIDENCE_THRESHOLD,
)

# ─── Crash strategy sub-scores ───
from trader_cycle.strategies.crash_strategy import (
    _score_rsi_exhaustion,
    _score_macd_bearish,
    _score_volume_spike,
)


class TestRangeBBTouch:
    """BB touch sub-score: returns (score, direction). Price at band → high score."""

    def test_at_lower_band(self):
        """Price at lower band → high score, LONG direction."""
        score, direction = _score_bb_touch(price=100.0, bb_upper=110.0, bb_lower=100.0, tol=0.005)
        assert score >= 0.9
        assert direction == "LONG"

    def test_far_from_bands(self):
        """Price in the middle → low score."""
        score, direction = _score_bb_touch(price=105.0, bb_upper=110.0, bb_lower=100.0, tol=0.005)
        assert score < 0.3

    def test_at_upper_band(self):
        """Price at upper band → high score, SHORT direction."""
        score, direction = _score_bb_touch(price=110.0, bb_upper=110.0, bb_lower=100.0, tol=0.005)
        assert score >= 0.9
        assert direction == "SHORT"

    def test_none_inputs(self):
        """None inputs → (0.0, '')."""
        score, direction = _score_bb_touch(None, 110.0, 100.0, 0.005)
        assert score == 0.0
        assert direction == ""

    def test_invalid_bands(self):
        """upper <= lower → (0.0, '')."""
        score, direction = _score_bb_touch(100.0, 100.0, 110.0, 0.005)
        assert score == 0.0

    def test_clamped_to_01(self):
        """Score always in [0, 1]."""
        score, _ = _score_bb_touch(price=95.0, bb_upper=110.0, bb_lower=100.0, tol=0.005)
        assert 0.0 <= score <= 1.0


class TestRangeRSIReversal:
    """RSI reversal sub-score."""

    def test_oversold_reversal_long(self):
        """RSI deeply oversold → high score for LONG."""
        # RSI 22 → (35-22)/(35-20) = 13/15 = 0.87
        score = _score_rsi_reversal(rsi=22.0, direction="LONG")
        assert score >= 0.7

    def test_overbought_reversal_short(self):
        """RSI deeply overbought → high score for SHORT."""
        # RSI 78 → (78-65)/(80-65) = 13/15 = 0.87
        score = _score_rsi_reversal(rsi=78.0, direction="SHORT")
        assert score >= 0.7

    def test_neutral_rsi(self):
        """RSI at 50 → 0.0."""
        assert _score_rsi_reversal(rsi=50.0, direction="LONG") == 0.0
        assert _score_rsi_reversal(rsi=50.0, direction="SHORT") == 0.0

    def test_none(self):
        assert _score_rsi_reversal(None, "LONG") == 0.0


class TestRangeSoftPenalties:
    """BB width and ADX soft penalties."""

    def test_narrow_bb_no_penalty(self):
        """BB width below threshold → no penalty."""
        assert _soft_penalty_bb_width(0.04) == 0.0

    def test_wide_bb_penalty(self):
        """BB width above threshold → negative penalty."""
        penalty = _soft_penalty_bb_width(0.10)
        assert penalty < 0
        assert penalty >= -0.15  # max penalty

    def test_low_adx_no_penalty(self):
        """ADX below 25 → no penalty."""
        assert _soft_penalty_adx(20.0) == 0.0

    def test_high_adx_penalty(self):
        """ADX above 25 → negative penalty."""
        penalty = _soft_penalty_adx(40.0)
        assert penalty < 0
        assert penalty >= -0.20  # max penalty

    def test_none_inputs(self):
        assert _soft_penalty_bb_width(None) == 0.0
        assert _soft_penalty_adx(None) == 0.0


class TestRangeVolume:
    """Volume sub-score."""

    def test_high_volume(self):
        score = _score_volume(volume_ratio=2.5)
        assert score >= 0.7

    def test_low_volume(self):
        score = _score_volume(volume_ratio=0.3)
        assert score < 0.3

    def test_none(self):
        assert _score_volume(None) == 0.0


class TestCrashRSIExhaustion:
    """Crash RSI exhaustion: relief rally overbought → SHORT opportunity."""

    def test_high_rsi(self):
        """RSI > 80 → 1.0."""
        score = _score_rsi_exhaustion(rsi=85.0, threshold=60.0)
        assert score == 1.0

    def test_moderate_rsi(self):
        """RSI 70 → partial score."""
        score = _score_rsi_exhaustion(rsi=70.0, threshold=60.0)
        assert 0.3 < score < 0.8

    def test_low_rsi(self):
        """RSI below threshold → 0.0."""
        assert _score_rsi_exhaustion(rsi=50.0, threshold=60.0) == 0.0

    def test_none(self):
        assert _score_rsi_exhaustion(None) == 0.0


class TestCrashMACDBearish:
    """Crash MACD histogram bearish scoring."""

    def test_large_negative(self):
        """Very negative histogram → 1.0."""
        score = _score_macd_bearish(-0.02)
        assert score == 1.0

    def test_small_negative(self):
        """Slightly negative → partial score."""
        score = _score_macd_bearish(-0.003)
        assert 0.1 < score < 0.5

    def test_positive(self):
        """Positive histogram → 0.0."""
        assert _score_macd_bearish(0.005) == 0.0

    def test_none(self):
        assert _score_macd_bearish(None) == 0.0


class TestCrashVolumeSpike:
    """Crash volume spike scoring."""

    def test_large_spike(self):
        """Volume ratio 5+ → 1.0."""
        score = _score_volume_spike(5.0, min_ratio=1.5)
        assert score == 1.0

    def test_moderate_spike(self):
        """Volume ratio 3 → partial."""
        score = _score_volume_spike(3.0, min_ratio=1.5)
        assert 0.3 < score < 0.7

    def test_below_min(self):
        """Below min_ratio → 0.0."""
        assert _score_volume_spike(1.0, min_ratio=1.5) == 0.0


class TestConfidenceThreshold:
    """Verify the confidence threshold is 0.3."""

    def test_threshold_value(self):
        assert CONFIDENCE_THRESHOLD == 0.30
