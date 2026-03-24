"""obv.py — OBV (On-Balance Volume) confirmation scoring.

Squeeze 用做 bonus（+0.05），Burst 用做 hard gate + weighted score（W=0.25）。
兩邊都用同一套 OBV vs OBV_EMA 邏輯，但 strategy 決定 gate vs bonus。
"""

from __future__ import annotations

import math


def score_obv_confirmation(
    obv: float | None,
    obv_ema: float | None,
    direction: str,
    *,
    full_score_pct: float = 0.10,
) -> float:
    """Score OBV confirmation strength (0.0-1.0).

    direction="LONG" → OBV > OBV_EMA is positive.
    direction="SHORT" → OBV < OBV_EMA is positive.

    Args:
        obv: Current OBV value.
        obv_ema: OBV exponential moving average.
        direction: "LONG" or "SHORT".
        full_score_pct: OBV deviation % for full score (default 10%).

    Returns:
        0.0 (against or no data) to 1.0 (strong confirmation).
    """
    if (obv is None or obv_ema is None or obv_ema == 0
            or not math.isfinite(obv) or not math.isfinite(obv_ema)):
        return 0.0

    obv_diff_pct = (obv - obv_ema) / abs(obv_ema)

    if direction == "LONG":
        if obv_diff_pct <= 0:
            return 0.0
        return min(obv_diff_pct / full_score_pct, 1.0)
    else:
        # SHORT: OBV below EMA = bearish confirmation
        if obv_diff_pct >= 0:
            return 0.0
        return min(abs(obv_diff_pct) / full_score_pct, 1.0)


def detect_obv_divergence(
    obv: float | None,
    obv_ema: float | None,
    direction: str,
) -> bool:
    """Simple bool: does OBV confirm the intended direction?

    Squeeze 用呢個做 bonus gate（True → +0.05）。
    Burst 用 score_obv_confirmation > 0 做 hard gate。
    """
    if (obv is None or obv_ema is None
            or not math.isfinite(obv) or not math.isfinite(obv_ema)):
        return False
    if direction == "LONG":
        return obv > obv_ema
    return obv < obv_ema
