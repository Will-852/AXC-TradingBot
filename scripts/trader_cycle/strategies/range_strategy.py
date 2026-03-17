"""
range_strategy.py — Range Trading (BB mean-reversion)

Phase 1 重構：
  - Binary conditions → weighted confidence scoring (0-1)
  - BB_width / ADX = soft penalties（唔係硬閘門）
  - 去 Stochastic（同 RSI 冗餘）
  - 直接讀 raw indicators 計 sub-scores，唔經 evaluate_range_signal()
  - 閾值 0.3：低過唔出信號

Sub-score weights:
  BB touch     0.25 — price 貼近 BB band
  RSI reversal 0.25 — RSI 極端 + 反轉跡象
  S/R prox     0.20 — 貼近 support/resistance
  Volume       0.20 — 成交量確認
  OBV          0.10 — 資金流方向
"""

from __future__ import annotations
import os
import sys

from ..config.settings import (
    RANGE_RISK_PCT, RANGE_LEVERAGE, RANGE_SL_ATR_MULT, RANGE_MIN_RR,
    SECONDARY_TIMEFRAME, PRIMARY_TIMEFRAME,
    ENTRY_VOLUME_MIN,
)
from ..config.pairs import get_pair
from ..core.context import CycleContext, Signal
from .base import StrategyBase, PositionParams

# Import params from indicator_calc for pair-level overrides
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from indicator_calc import TIMEFRAME_PARAMS, PRODUCT_OVERRIDES

# ─── Confidence weights ───
W_BB_TOUCH = 0.25
W_RSI_REV = 0.25
W_SR_PROX = 0.20
W_VOLUME = 0.20
W_OBV = 0.10

# ─── Sub-score parameters ───
CONFIDENCE_THRESHOLD = 0.30


def _score_bb_touch(price: float, bb_upper: float, bb_lower: float,
                    tol: float) -> tuple[float, str]:
    """BB touch sub-score: 0=far from band, 1=touching/beyond band.

    Returns (score, direction) where direction is 'LONG' or 'SHORT'.
    If price is in the middle, returns best side with low score.
    """
    if price is None or bb_upper is None or bb_lower is None or bb_upper <= bb_lower:
        return 0.0, ""

    bb_range = bb_upper - bb_lower
    mid = (bb_upper + bb_lower) / 2

    # Distance to lower band (for LONG)
    dist_lower = (price - bb_lower) / bb_range if bb_range > 0 else 0.5
    # Distance to upper band (for SHORT)
    dist_upper = (bb_upper - price) / bb_range if bb_range > 0 else 0.5

    # Score = 1 when touching, 0 when at mid
    score_long = max(0.0, 1.0 - dist_lower / (0.5 * (1 + tol)))
    score_short = max(0.0, 1.0 - dist_upper / (0.5 * (1 + tol)))

    # Beyond band → clamp to 1.0
    if price <= bb_lower * (1 + tol):
        score_long = 1.0
    if price >= bb_upper * (1 - tol):
        score_short = 1.0

    if price < mid:
        return min(score_long, 1.0), "LONG"
    else:
        return min(score_short, 1.0), "SHORT"


def _score_rsi_reversal(rsi: float, direction: str,
                        rsi_long: float = 35.0,
                        rsi_short: float = 65.0) -> float:
    """RSI reversal sub-score: extreme RSI in correct direction → high score.

    LONG: RSI < rsi_long threshold → score scales with how oversold
    SHORT: RSI > rsi_short threshold → score scales with how overbought
    """
    if rsi is None:
        return 0.0

    if direction == "LONG":
        if rsi >= rsi_long:
            return 0.0
        # RSI 30→1.0, rsi_long→0.0, linearly
        score = (rsi_long - rsi) / max(rsi_long - 20.0, 1.0)
        return min(max(score, 0.0), 1.0)
    elif direction == "SHORT":
        if rsi <= rsi_short:
            return 0.0
        score = (rsi - rsi_short) / max(80.0 - rsi_short, 1.0)
        return min(max(score, 0.0), 1.0)
    return 0.0


def _score_sr_proximity(price: float, support: float | None,
                        resistance: float | None,
                        direction: str) -> float:
    """S/R proximity sub-score: close to level → high score.

    LONG: distance to support (rolling_low) → closer = higher
    SHORT: distance to resistance (rolling_high) → closer = higher
    """
    if direction == "LONG" and support is not None and support > 0:
        dist_pct = abs(price - support) / price
        # Within 1% → score=1.0, 5%→0.0
        return min(max(1.0 - dist_pct / 0.05, 0.0), 1.0)
    elif direction == "SHORT" and resistance is not None and resistance > 0:
        dist_pct = abs(price - resistance) / price
        return min(max(1.0 - dist_pct / 0.05, 0.0), 1.0)
    return 0.0


def _score_volume(volume_ratio: float) -> float:
    """Volume sub-score: higher ratio → more confirmation.

    ratio 0.5→0.0, 1.0→0.25, 1.5→0.5, 2.0→0.75, 3.0→1.0
    """
    if volume_ratio is None or volume_ratio < 0.5:
        return 0.0
    score = (volume_ratio - 0.5) / 2.5  # 0.5→0, 3.0→1.0
    return min(max(score, 0.0), 1.0)


def _score_obv(obv: float | None, obv_ema: float | None,
               direction: str) -> float:
    """OBV confirmation sub-score: flow direction matches signal → high score."""
    if obv is None or obv_ema is None:
        return 0.5  # neutral when missing
    if direction == "LONG":
        return 1.0 if obv > obv_ema else 0.0
    elif direction == "SHORT":
        return 1.0 if obv < obv_ema else 0.0
    return 0.5


def _soft_penalty_bb_width(bb_width: float) -> float:
    """BB_width > 0.065 → penalty up to -0.15 at width=0.10.

    設計決定：soft gate 取代硬閘門。寬 BB = 可能唔係 range，但唔完全排除。
    """
    if bb_width is None or bb_width <= 0.065:
        return 0.0
    return -0.15 * min((bb_width - 0.065) / 0.035, 1.0)


def _soft_penalty_adx(adx: float) -> float:
    """ADX > 25 → penalty up to -0.20 at ADX=40.

    設計決定：高 ADX = 趨勢市，唔係 range。但唔完全排除因為有時趨勢末端有回歸。
    """
    if adx is None or adx <= 25:
        return 0.0
    return -0.20 * min((adx - 25) / 15.0, 1.0)


class RangeStrategy(StrategyBase):
    """Range Trading — BB mean-reversion with confidence scoring."""

    name = "range"
    mode = "RANGE"
    required_timeframes = ["4h", "1h"]

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> Signal | None:
        """Evaluate range entry using weighted confidence scoring."""
        ind_4h = indicators.get(PRIMARY_TIMEFRAME, {})
        volume_ratio = ind_4h.get("volume_ratio", 1.0)

        # Volume gate still applies (too low = no liquidity)
        if volume_ratio < ENTRY_VOLUME_MIN:
            return None

        ind_1h = indicators.get(SECONDARY_TIMEFRAME)
        if not ind_1h:
            return None

        price = ind_1h.get("price")
        bb_upper = ind_1h.get("bb_upper")
        bb_lower = ind_1h.get("bb_lower")
        bb_width = ind_1h.get("bb_width")
        rsi = ind_1h.get("rsi")
        adx = ind_1h.get("adx")

        if price is None or bb_upper is None or bb_lower is None:
            return None

        # ─── Get pair-level RSI thresholds ───
        rsi_long, rsi_short, bb_touch_tol = 35.0, 65.0, 0.01
        tf_params = TIMEFRAME_PARAMS.get(SECONDARY_TIMEFRAME, {}).copy()
        if pair in PRODUCT_OVERRIDES:
            tf_params.update(PRODUCT_OVERRIDES[pair])
        try:
            pair_cfg = get_pair(pair)
            if pair_cfg.rsi_long is not None:
                rsi_long = pair_cfg.rsi_long
            if pair_cfg.rsi_short is not None:
                rsi_short = pair_cfg.rsi_short
            if pair_cfg.bb_touch_tol is not None:
                bb_touch_tol = pair_cfg.bb_touch_tol
        except KeyError:
            pass
        rsi_long = tf_params.get("rsi_long", rsi_long)
        rsi_short = tf_params.get("rsi_short", rsi_short)
        bb_touch_tol = tf_params.get("bb_touch_tol", bb_touch_tol)

        # ─── Sub-scores ───
        bb_score, direction = _score_bb_touch(price, bb_upper, bb_lower, bb_touch_tol)
        if not direction:
            return None  # can't determine direction

        rsi_score = _score_rsi_reversal(rsi, direction, rsi_long, rsi_short)
        sr_score = _score_sr_proximity(
            price,
            ind_1h.get("rolling_low"),
            ind_1h.get("rolling_high"),
            direction,
        )
        vol_score = _score_volume(volume_ratio)
        obv_score = _score_obv(ind_4h.get("obv"), ind_4h.get("obv_ema"), direction)

        # ─── Weighted confidence ───
        confidence = (
            W_BB_TOUCH * bb_score
            + W_RSI_REV * rsi_score
            + W_SR_PROX * sr_score
            + W_VOLUME * vol_score
            + W_OBV * obv_score
        )

        # ─── Soft penalties ───
        penalty_bb = _soft_penalty_bb_width(bb_width)
        penalty_adx = _soft_penalty_adx(adx)
        confidence += penalty_bb + penalty_adx
        confidence = max(confidence, 0.0)

        if confidence < CONFIDENCE_THRESHOLD:
            return None

        # ─── Build signal ───
        strength = "STRONG" if confidence >= 0.7 else "WEAK"
        reasons = [
            f"RANGE_{direction}: conf={confidence:.2f}",
            f"  BB_touch={bb_score:.2f}(w={W_BB_TOUCH})",
            f"  RSI_rev={rsi_score:.2f}(w={W_RSI_REV}) rsi={rsi:.1f}" if rsi else f"  RSI_rev={rsi_score:.2f}(w={W_RSI_REV})",
            f"  S/R_prox={sr_score:.2f}(w={W_SR_PROX})",
            f"  Volume={vol_score:.2f}(w={W_VOLUME}) ratio={volume_ratio:.2f}",
            f"  OBV={obv_score:.2f}(w={W_OBV})",
        ]
        if penalty_bb < 0:
            reasons.append(f"  PENALTY_BB_width={penalty_bb:.2f} (width={bb_width:.4f})")
        if penalty_adx < 0:
            reasons.append(f"  PENALTY_ADX={penalty_adx:.2f} (adx={adx:.1f})")

        # Backward-compat score (old system used 3-5 range)
        score = 3.0 + confidence * 2.0

        return Signal(
            pair=pair,
            direction=direction,
            strategy=self.name,
            strength=strength,
            entry_price=price,
            reasons=reasons,
            score=score,
            confidence=confidence,
        )

    def get_position_params(self) -> PositionParams:
        """Range: 2% risk, 8x leverage, SL=1.0*ATR, min R:R 2.3."""
        return PositionParams(
            risk_pct=RANGE_RISK_PCT,
            leverage=RANGE_LEVERAGE,
            sl_atr_mult=RANGE_SL_ATR_MULT,
            min_rr=RANGE_MIN_RR,
        )

    def evaluate_exit(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> str | None:
        """Range exit: TP at BB basis. SL handled by exchange order."""
        return None
