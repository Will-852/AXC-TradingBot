"""squeeze.py — Bollinger Band squeeze detection and scoring.

Squeeze = BB width percentile 低 + ADX 低 + volume 靜。
呢度只做 detection/scoring，唔做 entry 決策（由 strategy 做）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SqueezeState:
    """Squeeze detection result."""
    is_squeeze: bool        # all gates passed
    bb_pctl_score: float    # 0-1 (lower pctl = higher score)
    adx_score: float        # 0-1 (lower ADX = higher score)
    vol_quiet_score: float  # 0-1 (lower volume = higher score)

    @property
    def composite_score(self) -> float:
        """Sum of sub-scores (for use as raw score, not confidence)."""
        return self.bb_pctl_score + self.adx_score + self.vol_quiet_score


def detect_squeeze(
    bb_width_pctl: float | None,
    adx: float | None,
    volume_ratio: float | None,
    *,
    bb_pctl_max: float = 30.0,
    adx_max: float = 25.0,
    vol_ratio_max: float = 0.80,
) -> SqueezeState:
    """Detect if market is in squeeze state.

    Gates (ALL must pass for is_squeeze=True):
      1. bb_width_pctl < bb_pctl_max
      2. adx < adx_max
      3. volume_ratio < vol_ratio_max (optional — None passes)

    Returns SqueezeState with per-component scores regardless of gate result.
    """
    # Score components (always computed for diagnostics)
    bb_score = _score_bb_pctl(bb_width_pctl, bb_pctl_max)
    adx_s = _score_adx_low(adx, adx_max)
    vol_s = _score_vol_quiet(volume_ratio, vol_ratio_max)

    # Gate logic
    # bb_width_pctl is None → pass gate (consistent with original strategy behaviour:
    # original only gated when pctl was not None AND >= threshold)
    bb_pass = bb_width_pctl is None or bb_width_pctl < bb_pctl_max
    adx_pass = adx is None or adx < adx_max
    vol_pass = volume_ratio is None or volume_ratio < vol_ratio_max

    return SqueezeState(
        is_squeeze=bb_pass and adx_pass and vol_pass,
        bb_pctl_score=bb_score,
        adx_score=adx_s,
        vol_quiet_score=vol_s,
    )


def score_squeeze(
    bb_width_pctl: float | None,
    adx: float | None,
    volume_ratio: float | None,
    *,
    w_bb_pctl: float = 0.30,
    w_adx: float = 0.25,
    w_vol: float = 0.25,
    bb_pctl_max: float = 30.0,
    adx_max: float = 25.0,
    vol_ratio_max: float = 0.80,
) -> float:
    """Weighted squeeze confidence score (0-1, before bonuses).

    只計 squeeze 三個 component 嘅 weighted sum。
    BB break score 由 strategy 自己加（squeeze vs burst 用法唔同）。
    """
    bb_s = _score_bb_pctl(bb_width_pctl, bb_pctl_max)
    adx_s = _score_adx_low(adx, adx_max)
    vol_s = _score_vol_quiet(volume_ratio, vol_ratio_max)
    return w_bb_pctl * bb_s + w_adx * adx_s + w_vol * vol_s


# ─── Internal scorers ───

def _score_bb_pctl(bb_width_pctl: float | None, threshold: float) -> float:
    """Linear: pctl=0 → 1.0, pctl=threshold → 0.0."""
    if bb_width_pctl is None or bb_width_pctl >= threshold or bb_width_pctl < 0:
        return 0.0
    return 1.0 - (bb_width_pctl / threshold)


def _score_adx_low(adx: float | None, threshold: float) -> float:
    """Linear: adx=0 → 1.0, adx=threshold → 0.0."""
    if adx is None or adx >= threshold or adx < 0:
        return 0.0
    return 1.0 - (adx / threshold)


def _score_vol_quiet(volume_ratio: float | None, threshold: float) -> float:
    """Linear: vol=0 → 1.0, vol=threshold → 0.0."""
    if volume_ratio is None or volume_ratio >= threshold or volume_ratio < 0:
        return 0.0
    return 1.0 - (volume_ratio / threshold)
