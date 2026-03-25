"""
Mode C: Skip.

No trade. Signal too weak or conditions unfavorable.
Log the skip reason for analysis.
"""

from dataclasses import dataclass


@dataclass
class SkipDecision:
    reason: str            # Why we skipped
    momentum_bps: float    # What the momentum was
    threshold_bps: float   # What threshold we needed


def should_skip(
    abs_momentum_bps: float,
    threshold_bps: float = 8.0,
    vol_regime: str = "mid",
    session_drawdown: float = 0.0,
    session_stop_all: float = 30.0,
) -> SkipDecision | None:
    """
    Returns SkipDecision if we should skip, None if OK to trade.
    """
    if session_drawdown >= session_stop_all:
        return SkipDecision(
            reason=f"session_drawdown ${session_drawdown:.0f} >= ${session_stop_all:.0f}",
            momentum_bps=abs_momentum_bps,
            threshold_bps=threshold_bps,
        )

    if vol_regime == "low":
        # Downgrade: raise threshold by 50%
        effective_threshold = threshold_bps * 1.5
    else:
        effective_threshold = threshold_bps

    if abs_momentum_bps < effective_threshold:
        return SkipDecision(
            reason=f"|momentum| {abs_momentum_bps:.1f}bps < threshold {effective_threshold:.1f}bps",
            momentum_bps=abs_momentum_bps,
            threshold_bps=effective_threshold,
        )

    return None
