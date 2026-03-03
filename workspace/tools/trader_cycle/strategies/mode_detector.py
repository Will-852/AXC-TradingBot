"""
mode_detector.py — 4H 5-indicator mode detection (RANGE/TREND)
Based on STRATEGY.md 模式偵測（4H，5 指標）
"""

from __future__ import annotations

from ..config.settings import (
    MODE_RSI_TREND_LOW, MODE_RSI_TREND_HIGH,
    MODE_VOLUME_LOW, MODE_VOLUME_HIGH,
    MODE_FUNDING_THRESHOLD, MODE_CONFIRMATION_REQUIRED,
    PRIMARY_TIMEFRAME,
)
from ..core.context import CycleContext


def _vote_rsi(rsi: float | None) -> str:
    """RSI < 32 or > 68 → TREND, 32-68 → RANGE."""
    if rsi is None:
        return "NEUTRAL"
    if rsi < MODE_RSI_TREND_LOW or rsi > MODE_RSI_TREND_HIGH:
        return "TREND"
    return "RANGE"


def _vote_macd(hist: float | None, hist_prev: float | None) -> str:
    """Histogram expanding → TREND, narrowing/near-zero → RANGE."""
    if hist is None or hist_prev is None:
        return "NEUTRAL"
    # Expanding = magnitude increasing
    if abs(hist) > abs(hist_prev) and abs(hist) > 0.001:
        return "TREND"
    return "RANGE"


def _vote_volume(volume_ratio: float | None) -> str:
    """<50% or >150% of avg → TREND, 50-150% → RANGE."""
    if volume_ratio is None:
        return "NEUTRAL"
    if volume_ratio < MODE_VOLUME_LOW or volume_ratio > MODE_VOLUME_HIGH:
        return "TREND"
    return "RANGE"


def _vote_ma(price: float | None, ma50: float | None, ma200: float | None) -> str:
    """Price clearly above/below both MAs → TREND, between → RANGE."""
    if price is None or ma50 is None or ma200 is None:
        return "NEUTRAL"
    upper = max(ma50, ma200)
    lower = min(ma50, ma200)
    if price > upper or price < lower:
        return "TREND"
    return "RANGE"


def _vote_funding(funding_rate: float | None) -> str:
    """>±0.07% → TREND, -0.07%~+0.07% → RANGE."""
    if funding_rate is None:
        return "NEUTRAL"
    if abs(funding_rate) > MODE_FUNDING_THRESHOLD:
        return "TREND"
    return "RANGE"


def detect_mode_for_pair(indicators_4h: dict, funding_rate: float) -> tuple[str, dict[str, str]]:
    """
    Run 5-indicator voting for one pair's 4H data.
    Returns (mode, votes_dict).
    """
    votes = {
        "RSI": _vote_rsi(indicators_4h.get("rsi")),
        "MACD": _vote_macd(
            indicators_4h.get("macd_hist"),
            indicators_4h.get("macd_hist_prev")
        ),
        "Volume": _vote_volume(indicators_4h.get("volume_ratio")),
        "MA": _vote_ma(
            indicators_4h.get("price"),
            indicators_4h.get("ma50"),
            indicators_4h.get("ma200")
        ),
        "Funding": _vote_funding(funding_rate),
    }

    trend_count = sum(1 for v in votes.values() if v == "TREND")
    range_count = sum(1 for v in votes.values() if v == "RANGE")

    if trend_count >= 3:
        mode = "TREND"
    elif range_count >= 3:
        mode = "RANGE"
    else:
        mode = "UNKNOWN"  # will maintain current mode

    return mode, votes


class DetectModeStep:
    """
    Step 6: Market mode detection.
    Aggregates votes across all pairs (BTC has most weight).
    Requires 2 consecutive same-mode for switch.
    """
    name = "detect_mode"

    def run(self, ctx: CycleContext) -> CycleContext:
        # Use BTC as primary indicator (most reliable)
        primary = "BTCUSDT"
        if primary not in ctx.indicators or PRIMARY_TIMEFRAME not in ctx.indicators[primary]:
            # Fallback: use first available
            for sym in ctx.indicators:
                if PRIMARY_TIMEFRAME in ctx.indicators[sym]:
                    primary = sym
                    break
            else:
                ctx.warnings.append("No 4H indicators available for mode detection")
                ctx.market_mode = ctx.prev_mode  # keep previous
                return ctx

        ind_4h = ctx.indicators[primary][PRIMARY_TIMEFRAME]
        funding = ctx.market_data.get(primary, None)
        funding_rate = funding.funding_rate if funding else 0.0

        raw_mode, votes = detect_mode_for_pair(ind_4h, funding_rate)
        ctx.mode_votes = votes

        # Mode confirmation logic
        if raw_mode == "UNKNOWN":
            # Tie → maintain current mode
            ctx.market_mode = ctx.prev_mode
            ctx.mode_confirmed = ctx.prev_mode_cycles >= MODE_CONFIRMATION_REQUIRED
        elif raw_mode == ctx.prev_mode:
            # Same as before → increment confirmation
            ctx.market_mode = raw_mode
            new_cycles = ctx.prev_mode_cycles + 1
            ctx.mode_confirmed = new_cycles >= MODE_CONFIRMATION_REQUIRED
            ctx.scan_config_updates["MODE_CONFIRMED_CYCLES"] = new_cycles
        else:
            # Mode change detected → need 2 consecutive
            if ctx.prev_mode_cycles == 0 or ctx.prev_mode == "UNKNOWN":
                # First detection or from unknown → accept immediately
                ctx.market_mode = raw_mode
                ctx.mode_confirmed = False
                ctx.scan_config_updates["MODE_CONFIRMED_CYCLES"] = 1
            else:
                # Was in a confirmed mode → need to see this new mode again
                ctx.market_mode = raw_mode
                ctx.mode_confirmed = False
                ctx.scan_config_updates["MODE_CONFIRMED_CYCLES"] = 1

        if ctx.verbose:
            vote_str = " | ".join(f"{k}:{v}" for k, v in votes.items())
            print(f"    Mode: {ctx.market_mode} (confirmed={ctx.mode_confirmed}) [{vote_str}]")

        return ctx
