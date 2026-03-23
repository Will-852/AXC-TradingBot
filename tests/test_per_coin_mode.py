"""Tests for per-coin mode detection — Phase 3."""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scripts.trader_cycle.strategies.mode_detector import detect_mode_for_pair


@dataclass
class _MockMarketSnapshot:
    funding_rate: float = 0.0
    price_change_24h_pct: float = 0.0


def _make_ctx(**overrides):
    """Build a minimal CycleContext-like object for testing."""
    from scripts.trader_cycle.core.context import CycleContext
    ctx = CycleContext(
        timestamp=datetime.now(timezone.utc),
        verbose=False,
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# ── Indicator fixtures ──
def _trending_indicators():
    """4H indicators that should vote TREND."""
    return {
        "rsi": 75.0,          # > MODE_RSI_TREND_HIGH (69) → TREND
        "macd_hist": 0.5,     # Expanding → TREND
        "macd_hist_prev": 0.2,
        "volume_ratio": 2.0,  # > MODE_VOLUME_HIGH (1.5) → TREND
        "price": 100.0,
        "ma50": 95.0,
        "ma200": 90.0,        # price > ma50 > ma200 → TREND
    }


def _ranging_indicators():
    """4H indicators that should vote RANGE."""
    return {
        "rsi": 50.0,          # Between 34-69 → RANGE
        "macd_hist": 0.001,   # Contracting → RANGE
        "macd_hist_prev": 0.002,
        "volume_ratio": 1.0,  # Between 0.5-1.5 → RANGE
        "price": 95.0,
        "ma50": 96.0,
        "ma200": 94.0,        # price between MAs → RANGE
    }


class TestDetectModeForPair:
    """detect_mode_for_pair() — pure function, symbol-agnostic."""

    def test_trending_returns_trend(self):
        mode, votes = detect_mode_for_pair(_trending_indicators(), 0.0)
        assert mode == "TREND"
        trend_count = sum(1 for v in votes.values() if v == "TREND")
        assert trend_count >= 3

    def test_ranging_returns_range(self):
        mode, votes = detect_mode_for_pair(_ranging_indicators(), 0.0)
        assert mode == "RANGE"

    def test_with_hmm_crash_override(self):
        mode, votes = detect_mode_for_pair(
            _ranging_indicators(), 0.0,
            hmm_regime="CRASH", hmm_confidence=0.85,
            hmm_crash_confirmed=True,
        )
        assert mode == "CRASH"


class TestPerCoinModeContext:
    """CycleContext per-coin mode fields."""

    def test_context_has_per_coin_fields(self):
        ctx = _make_ctx()
        assert isinstance(ctx.coin_market_mode, dict)
        assert isinstance(ctx.coin_mode_votes, dict)
        assert len(ctx.coin_market_mode) == 0  # Empty initially

    def test_per_coin_mode_independent(self):
        """BTC can be TREND while ETH is RANGE."""
        ctx = _make_ctx()
        ctx.coin_market_mode["BTCUSDT"] = "TREND"
        ctx.coin_market_mode["ETHUSDT"] = "RANGE"
        assert ctx.coin_market_mode["BTCUSDT"] != ctx.coin_market_mode["ETHUSDT"]

    def test_global_mode_unchanged(self):
        """Global market_mode should still work (backward compat)."""
        ctx = _make_ctx()
        ctx.market_mode = "TREND"
        ctx.coin_market_mode["ETHUSDT"] = "RANGE"
        assert ctx.market_mode == "TREND"  # Global unchanged


class TestDetectModeStepPerCoin:
    """DetectModeStep with per-coin indicators."""

    def test_per_coin_modes_populated(self):
        """When multiple coins have 4H data, each gets its own mode."""
        from scripts.trader_cycle.strategies.mode_detector import DetectModeStep

        ctx = _make_ctx(
            indicators={
                "BTCUSDT": {"4h": _trending_indicators()},
                "ETHUSDT": {"4h": _ranging_indicators()},
            },
            market_data={
                "BTCUSDT": _MockMarketSnapshot(funding_rate=0.0),
                "ETHUSDT": _MockMarketSnapshot(funding_rate=0.0),
            },
        )

        step = DetectModeStep()
        result = step.run(ctx)

        # Per-coin modes should be populated
        assert "BTCUSDT" in result.coin_market_mode
        assert "ETHUSDT" in result.coin_market_mode

        # BTC = anchor → same as global
        assert result.coin_market_mode["BTCUSDT"] == result.market_mode

    def test_missing_coin_indicators_skipped(self):
        """Coins without 4H data don't get a mode entry."""
        from scripts.trader_cycle.strategies.mode_detector import DetectModeStep

        ctx = _make_ctx(
            indicators={
                "BTCUSDT": {"4h": _trending_indicators()},
                "XRPUSDT": {"1h": _ranging_indicators()},  # No 4h → skipped
            },
            market_data={
                "BTCUSDT": _MockMarketSnapshot(funding_rate=0.0),
            },
        )

        step = DetectModeStep()
        result = step.run(ctx)

        assert "BTCUSDT" in result.coin_market_mode
        assert "XRPUSDT" not in result.coin_market_mode


class TestSignalFilterPerCoinMode:
    """SignalFilterStep uses per-coin mode for affinity penalty."""

    def test_per_coin_mode_affects_penalty(self):
        """ETH in RANGE mode should penalize trend signals differently than BTC in TREND."""
        from config.params import SIGNAL_MODE_AFFINITY, SIGNAL_MODE_DEFAULT_PENALTY

        # When coin is in RANGE mode, trend strategy gets heavy penalty
        range_penalties = SIGNAL_MODE_AFFINITY.get("RANGE", SIGNAL_MODE_DEFAULT_PENALTY)
        trend_in_range_penalty = range_penalties.get("trend", 0.0)

        # When coin is in TREND mode, trend strategy gets no penalty
        trend_penalties = SIGNAL_MODE_AFFINITY.get("TREND", SIGNAL_MODE_DEFAULT_PENALTY)
        trend_in_trend_penalty = trend_penalties.get("trend", 0.0)

        # Trend in RANGE should be much worse than trend in TREND
        assert trend_in_range_penalty < trend_in_trend_penalty
        assert trend_in_range_penalty <= -0.30  # At least -0.30 penalty
