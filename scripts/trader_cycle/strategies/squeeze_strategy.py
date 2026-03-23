"""
squeeze_strategy.py — Squeeze-Explosion Strategy

360日分析：42.5% 嘅 BTC 波動集中在 16.1% 嘅 candles（range > 2%）。
SQUEEZE pattern (13-16%) + QUIET_THEN_BOOM (5-8%) = 最常見嘅爆發前兆。

Entry conditions (全部滿足):
  1. BB width percentile < 30%     → squeeze (adjusted: 20% too strict, only 7-9 trades/360d)
  2. Volume ratio < 0.8            → quiet accumulation
  3. ADX < 25                      → no directional energy
  4. Trigger: price breaks BB upper → LONG / BB lower → SHORT

Confidence scoring:
  W_BB_PCTL:  0.30 — squeeze 強度（percentile 越低越好）
  W_ADX_LOW:  0.25 — ADX 越低越好（energy building up）
  W_VOL_QUIET: 0.25 — volume 越靜越好（calm before storm）
  W_BB_BREAK:  0.20 — breakout 明確性（price 超出 BB band 幾多）

Bonuses:
  Per-coin session preference: +0.10
    BTC/ETH: Non-US (ASIA/EU) bonus — 360d backtest: Non-US PF 1.65-1.67 vs US PF 0.63-0.73
    SOL: US session bonus — 360d backtest: US PF 1.28 vs Non-US PF 0.91
  OBV divergence: +0.05 — OBV 已經偷步
  4H squeeze confirmation: +0.05 — multi-TF alignment

SL = 1.5 × ATR (adjusted: 1.0 too tight, 85% SL hit rate → 1.5 gives WR 35-37%)
TP = 3.0 × ATR → R:R = 2.0 → BE = 33%
"""

from __future__ import annotations

import logging

from ..config.settings import PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME
from ..core.context import CycleContext, Signal
from .base import StrategyBase, PositionParams

log = logging.getLogger(__name__)

# ─── Weights ───
W_BB_PCTL = 0.30
W_ADX_LOW = 0.25
W_VOL_QUIET = 0.25
W_BB_BREAK = 0.20

CONFIDENCE_THRESHOLD = 0.30

# ─── Thresholds (adjusted from 360d backtest) ───
BB_PCTL_SQUEEZE = 30.0        # percentile < 30% = squeeze (was 20, too strict → only 7-9 trades)
ADX_LOW_THRESHOLD = 25.0      # ADX < 25 (was 20, too strict)
VOL_QUIET_THRESHOLD = 0.80    # volume_ratio < 0.8 (was 0.7, too strict)

# ─── Session bonus/penalty (per-coin from config) ───
# 360d backtest finding: BTC/ETH profit in NON-US, SOL profit in US.
# Per-coin session_preference in coin config determines bonus direction.
# "non_us" = bonus for ASIA/EU, "us" = bonus for US_PRE/US_OPEN
SESSION_BONUS = 0.10
OBV_DIVERGENCE_BONUS = 0.05

_US_SESSIONS = {"US_PRE", "US_OPEN"}
_NON_US_SESSIONS = {"ASIA", "EU_OPEN"}


def _score_bb_squeeze(bb_width_pctl: float | None) -> float:
    """Score based on BB width percentile. Lower = tighter squeeze = higher score."""
    if bb_width_pctl is None:
        return 0.0
    if bb_width_pctl >= BB_PCTL_SQUEEZE:
        return 0.0  # Not in squeeze
    # Linear: pctl=0 → 1.0, pctl=20 → 0.0
    return 1.0 - (bb_width_pctl / BB_PCTL_SQUEEZE)


def _score_adx_low(adx: float | None) -> float:
    """Score based on ADX. Lower = less directional energy = higher score."""
    if adx is None:
        return 0.0
    if adx >= ADX_LOW_THRESHOLD:
        return 0.0
    # Linear: adx=0 → 1.0, adx=20 → 0.0
    return 1.0 - (adx / ADX_LOW_THRESHOLD)


def _score_vol_quiet(volume_ratio: float | None) -> float:
    """Score based on volume quietness. Lower ratio = quieter = higher score."""
    if volume_ratio is None:
        return 0.0
    if volume_ratio >= VOL_QUIET_THRESHOLD:
        return 0.0
    # Linear: vol=0 → 1.0, vol=0.7 → 0.0
    return 1.0 - (volume_ratio / VOL_QUIET_THRESHOLD)


def _score_bb_break(
    price: float, bb_upper: float, bb_lower: float, bb_width: float
) -> tuple[float, str | None]:
    """Score BB band breakout. Returns (score, direction).

    Direction = "LONG" if price > bb_upper, "SHORT" if price < bb_lower.
    Score = how far past the band (normalized by bb_width).
    """
    if not all((price, bb_upper, bb_lower, bb_width)) or bb_width <= 0:
        return 0.0, None

    if price > bb_upper:
        # Breakout above — LONG
        overshoot = (price - bb_upper) / price if price > 0 else 0
        return min(overshoot * 500.0, 1.0), "LONG"  # 0.2% overshoot → 1.0
    elif price < bb_lower:
        # Breakout below — SHORT
        overshoot = (bb_lower - price) / price if price > 0 else 0
        return min(overshoot * 500.0, 1.0), "SHORT"
    else:
        return 0.0, None


class SqueezeStrategy(StrategyBase):
    """Squeeze-Explosion: catch explosive moves after compression periods."""

    name = "squeeze"
    mode = "SQUEEZE"
    required_timeframes = [PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME]

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> Signal | None:
        # Use 1H for entry timing, 4H for squeeze confirmation
        ind_1h = indicators.get(SECONDARY_TIMEFRAME, {})
        ind_4h = indicators.get(PRIMARY_TIMEFRAME, {})

        price = ind_1h.get("price")
        bb_upper = ind_1h.get("bb_upper")
        bb_lower = ind_1h.get("bb_lower")
        bb_width = ind_1h.get("bb_width")
        bb_width_pctl = ind_1h.get("bb_width_pctl")
        adx = ind_1h.get("adx")
        volume_ratio = ind_1h.get("volume_ratio")
        obv = ind_1h.get("obv")
        obv_ema = ind_1h.get("obv_ema")

        if price is None or bb_upper is None or bb_lower is None:
            return None

        # ─── Gate: must be in squeeze (BB percentile < 30%) ───
        if bb_width_pctl is not None and bb_width_pctl >= BB_PCTL_SQUEEZE:
            return None  # Not in squeeze — skip

        # ─── Gate: ADX must be low (< 20) ───
        if adx is not None and adx >= ADX_LOW_THRESHOLD:
            return None  # Too much directional energy — not a squeeze

        # ─── Sub-scores ───
        bb_pctl_score = _score_bb_squeeze(bb_width_pctl)
        adx_score = _score_adx_low(adx)
        vol_score = _score_vol_quiet(volume_ratio)
        break_score, direction = _score_bb_break(price, bb_upper, bb_lower, bb_width)

        if not direction:
            return None  # No breakout — price still inside bands

        # ─── Weighted confidence ───
        confidence = (
            W_BB_PCTL * bb_pctl_score
            + W_ADX_LOW * adx_score
            + W_VOL_QUIET * vol_score
            + W_BB_BREAK * break_score
        )

        # ─── Bonuses ───
        reasons = []

        # Per-coin session preference (360d backtest: BTC/ETH → Non-US, SOL → US)
        try:
            from config.coins.loader import get_coin as _gc
            session_pref = _gc(pair).get("session_preference", "non_us")
        except (KeyError, ImportError):
            session_pref = "non_us"

        if session_pref == "non_us" and ctx.session_tag in _NON_US_SESSIONS:
            confidence += SESSION_BONUS
            reasons.append(f"NON_US_SESSION +{SESSION_BONUS}")
        elif session_pref == "us" and ctx.session_tag in _US_SESSIONS:
            confidence += SESSION_BONUS
            reasons.append(f"US_SESSION +{SESSION_BONUS}")

        # OBV divergence: OBV already moving while price is flat
        if obv is not None and obv_ema is not None:
            if direction == "LONG" and obv > obv_ema:
                confidence += OBV_DIVERGENCE_BONUS
                reasons.append(f"OBV_DIV +{OBV_DIVERGENCE_BONUS}")
            elif direction == "SHORT" and obv < obv_ema:
                confidence += OBV_DIVERGENCE_BONUS
                reasons.append(f"OBV_DIV +{OBV_DIVERGENCE_BONUS}")

        # ─── 4H confirmation: check 4H also shows squeeze ───
        bb_pctl_4h = ind_4h.get("bb_width_pctl")
        if bb_pctl_4h is not None and bb_pctl_4h < 30.0:
            confidence += 0.05  # Multi-TF squeeze confirmation
            reasons.append("4H_SQUEEZE +0.05")

        # ─── Threshold ───
        if confidence < CONFIDENCE_THRESHOLD:
            return None

        confidence = min(confidence, 1.0)

        # Build score from sub-components
        score = bb_pctl_score + adx_score + vol_score + break_score

        reasons.insert(0,
            f"SQZ: pctl={bb_width_pctl or 0:.0f}% adx={adx or 0:.1f} vol={volume_ratio or 0:.2f}"
        )

        return Signal(
            pair=pair,
            direction=direction,
            strategy="squeeze",
            strength="MEDIUM" if confidence < 0.60 else "STRONG",
            confidence=confidence,
            score=score,
            reasons=reasons,
        )

    def get_position_params(self) -> PositionParams:
        return PositionParams(
            risk_pct=0.03,        # 3% margin (same as all strategies)
            leverage=8,           # Uses zone range_leverage (overridden by position_sizer)
            sl_atr_mult=1.5,      # SL: 1.5 × ATR (backtest: 1.0 too tight, 85% SL hit rate)
            min_rr=2.0,           # R:R ≥ 2.0 → BE = 33% (backtest WR 35-37%)
            tp_atr_mult=3.0,      # Ride the explosion: 3.0 × ATR
        )
