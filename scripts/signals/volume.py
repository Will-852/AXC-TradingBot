"""volume.py — Volume signal scoring and detection.

兩種相反嘅 volume signal：
  - Quiet（squeeze 用）：volume_ratio < threshold → 暴風雨前嘅寧靜
  - Spike（burst 用）：volume_ratio > threshold → 爆發
  - Projection（event-driven 用）：用 open kline volume 推算完整 candle volume
"""

from __future__ import annotations

import math


def _is_valid(v: float | None) -> bool:
    """Check value is not None, NaN, or inf."""
    return v is not None and math.isfinite(v)


# ─── Quiet volume (squeeze strategy) ───

def score_volume_quiet(
    volume_ratio: float | None,
    threshold: float = 0.80,
) -> float:
    """Score volume quietness. Lower ratio = quieter = higher score.

    Linear: ratio=0 → 1.0, ratio=threshold → 0.0, ratio>threshold → 0.0.
    """
    if not _is_valid(volume_ratio) or volume_ratio >= threshold or volume_ratio < 0:
        return 0.0
    return 1.0 - (volume_ratio / threshold)


# ─── Volume spike (burst strategy) ───

def score_volume_spike(
    volume_ratio: float,
    floor: float = 2.5,
    ceiling: float = 5.0,
) -> float:
    """Score volume spike strength. 0 at floor, 1.0 at ceiling+.

    Linear ramp from floor to ceiling.
    """
    if not _is_valid(volume_ratio) or volume_ratio <= floor:
        return 0.0
    return min((volume_ratio - floor) / (ceiling - floor), 1.0)


def detect_volume_spike(
    volume_ratio: float,
    threshold: float = 2.5,
) -> bool:
    """Simple bool: is volume spiking above threshold?"""
    return _is_valid(volume_ratio) and volume_ratio >= threshold


# ─── Volume projection (event-driven trigger) ───

def project_volume(
    current_volume: float,
    elapsed_pct: float,
    avg_volume: float,
) -> float:
    """Project full-candle volume from partial candle data.

    Args:
        current_volume: accumulated volume so far in this candle.
        elapsed_pct: fraction of candle elapsed (0.0-1.0).
        avg_volume: historical average volume per candle (e.g. 30-bar avg).

    Returns:
        Projected volume ratio (projected_volume / avg_volume).
        Returns 0.0 if inputs invalid or elapsed too early (<5%).

    設計決定：elapsed_pct < 5% 唔計算 — 太早嘅 projection 全係 noise。
    """
    if elapsed_pct < 0.05 or avg_volume <= 0 or current_volume < 0:
        return 0.0
    projected = current_volume / elapsed_pct
    return projected / avg_volume
