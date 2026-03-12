"""
mode_detector.py — 4H 6-indicator mode detection (RANGE/TREND/CRASH)

Original 5 indicators + HMM as 6th vote.
CRASH override: HMM state=CRASH + confidence ≥ 0.7 → skip voting, force CRASH.
Threshold: 4/6 majority (was 3/5).
"""

from __future__ import annotations

import logging

from ..config.settings import (
    MODE_RSI_TREND_LOW, MODE_RSI_TREND_HIGH,
    MODE_VOLUME_LOW, MODE_VOLUME_HIGH,
    MODE_FUNDING_THRESHOLD, MODE_CONFIRMATION_REQUIRED,
    PRIMARY_TIMEFRAME,
    HMM_ENABLED, HMM_N_STATES, HMM_WINDOW,
    HMM_REFIT_INTERVAL, HMM_MIN_CONFIDENCE,
    HMM_MIN_SAMPLES, HMM_CRASH_THRESHOLD,
)
from ..core.context import CycleContext

log = logging.getLogger(__name__)

# ─── Singleton HMM instance (lazy init) ───
_hmm_instance = None


def _get_hmm():
    """Lazy singleton for RegimeHMM — only created when HMM_ENABLED."""
    global _hmm_instance
    if _hmm_instance is None:
        from .regime_hmm import RegimeHMM
        _hmm_instance = RegimeHMM(
            n_states=HMM_N_STATES,
            window=HMM_WINDOW,
            refit_interval=HMM_REFIT_INTERVAL,
            min_samples=HMM_MIN_SAMPLES,
        )
    return _hmm_instance


def reset_hmm():
    """Reset HMM singleton (for testing)."""
    global _hmm_instance
    _hmm_instance = None


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


def _vote_hmm_from_result(regime: str, confidence: float) -> str:
    """Convert HMM result to a RANGE/TREND vote string.

    CRASH maps to RANGE vote (defensive), UNKNOWN = NEUTRAL.
    """
    if confidence < HMM_MIN_CONFIDENCE:
        return "NEUTRAL"
    if regime == "CRASH":
        return "RANGE"  # crash → defensive = range-like vote
    if regime in ("RANGE", "TREND"):
        return regime
    return "NEUTRAL"


def detect_mode_for_pair(
    indicators_4h: dict, funding_rate: float,
    hmm_regime: str | None = None, hmm_confidence: float = 0.0,
    hmm_crash_confirmed: bool = False,
) -> tuple[str, dict[str, str]]:
    """
    Run 6-indicator voting for one pair's 4H data.

    If HMM is available (hmm_regime not None), adds HMM as 6th vote.
    CRASH override: requires crash_confirmed (percentile gate) + high confidence.
    Returns (mode, votes_dict).
    """
    # CRASH override: HMM high-confidence + percentile-confirmed crash
    if (
        HMM_ENABLED
        and hmm_regime == "CRASH"
        and hmm_crash_confirmed
        and hmm_confidence >= HMM_CRASH_THRESHOLD
    ):
        votes = {"HMM": f"CRASH (conf={hmm_confidence:.0%})"}
        return "CRASH", votes

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

    # Add HMM as 6th vote if available
    if HMM_ENABLED and hmm_regime is not None:
        votes["HMM"] = _vote_hmm_from_result(hmm_regime, hmm_confidence)

    trend_count = sum(1 for v in votes.values() if v == "TREND")
    range_count = sum(1 for v in votes.values() if v == "RANGE")

    # Threshold: majority of actual votes (excluding NEUTRAL)
    # With 6 voters: 4/6 = majority. With 5 (HMM absent): 3/5 = majority.
    total_non_neutral = sum(1 for v in votes.values() if v in ("TREND", "RANGE"))
    threshold = max((total_non_neutral // 2) + 1, 3)  # at least 3

    if trend_count >= threshold:
        mode = "TREND"
    elif range_count >= threshold:
        mode = "RANGE"
    else:
        mode = "UNKNOWN"  # will maintain current mode

    return mode, votes


class DetectModeStep:
    """
    Step 6: Market mode detection.
    Aggregates votes across all pairs (BTC has most weight).
    Requires 2 consecutive same-mode for switch.
    CRASH mode: HMM override, skips confirmation requirement.
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

        # HMM update (if enabled)
        hmm_regime = None
        hmm_confidence = 0.0
        hmm_crash_confirmed = False
        if HMM_ENABLED:
            try:
                hmm = _get_hmm()
                hmm_regime, hmm_confidence, hmm_crash_confirmed = hmm.update(ind_4h)
            except Exception as e:
                log.warning("HMM update failed: %s", e)

        raw_mode, votes = detect_mode_for_pair(
            ind_4h, funding_rate, hmm_regime, hmm_confidence, hmm_crash_confirmed
        )
        ctx.mode_votes = votes

        # Store HMM confidence on context for position_sizer
        ctx.scan_config_updates["HMM_REGIME"] = hmm_regime or "UNKNOWN"
        ctx.scan_config_updates["HMM_CONFIDENCE"] = f"{hmm_confidence:.3f}"

        # CRASH mode: skip confirmation, immediately active
        if raw_mode == "CRASH":
            ctx.market_mode = "CRASH"
            ctx.mode_confirmed = True  # CRASH is always confirmed (emergency)
            ctx.scan_config_updates["MODE_CONFIRMED_CYCLES"] = ctx.prev_mode_cycles
            if ctx.verbose:
                print(f"    Mode: CRASH (HMM override, conf={hmm_confidence:.0%})")
            return ctx

        # Normal mode confirmation logic
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
            hmm_tag = f" HMM:{hmm_regime}({hmm_confidence:.0%})" if hmm_regime else ""
            print(f"    Mode: {ctx.market_mode} (confirmed={ctx.mode_confirmed}) [{vote_str}]{hmm_tag}")

        return ctx
