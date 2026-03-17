"""
crash_strategy.py — CRASH mode 策略（高波動防守型）

Phase 1 重構：
  - Binary 2-of-3 → weighted confidence scoring (0-1)
  - SHORT-only 保留
  - 閾值 0.3

Sub-score weights:
  RSI exhaustion  0.40 — relief rally overbought
  MACD bearish    0.30 — 下跌動量
  Volume spike    0.30 — 恐慌成交量
"""

from __future__ import annotations

from ..config.settings import (
    CRASH_RISK_PCT, CRASH_LEVERAGE, CRASH_SL_ATR_MULT,
    CRASH_MIN_RR, CRASH_RSI_ENTRY, CRASH_VOLUME_MIN,
)
from .base import StrategyBase, PositionParams
from ..core.context import CycleContext, Signal

# ─── Confidence weights ───
W_RSI = 0.40
W_MACD = 0.30
W_VOLUME = 0.30

CONFIDENCE_THRESHOLD = 0.30


def _score_rsi_exhaustion(rsi: float, threshold: float = 60.0) -> float:
    """RSI overbought in crash = relief rally exhaustion.

    RSI > threshold → starts scoring. RSI > 80 → 1.0.
    Low threshold (60) because in crash, even RSI 65 is stretched.
    """
    if rsi is None or rsi <= threshold:
        return 0.0
    return min((rsi - threshold) / (80.0 - threshold), 1.0)


def _score_macd_bearish(macd_hist: float) -> float:
    """MACD histogram < 0 and magnitude → bearish momentum score.

    hist < 0 → starts scoring. More negative → higher score.
    Scaled by typical crash histogram magnitude.
    """
    if macd_hist is None or macd_hist >= 0:
        return 0.0
    # Magnitude scoring: |hist| 0.001→0.2, 0.005→0.5, 0.01→1.0
    return min(abs(macd_hist) / 0.01, 1.0)


def _score_volume_spike(volume_ratio: float, min_ratio: float = 1.5) -> float:
    """Volume spike: high volume confirms panic selling.

    ratio < min_ratio → 0. ratio 1.5→0.0, 3.0→0.5, 5.0→1.0
    """
    if volume_ratio is None or volume_ratio < min_ratio:
        return 0.0
    return min((volume_ratio - min_ratio) / (5.0 - min_ratio), 1.0)


class CrashStrategy(StrategyBase):
    """SHORT-only strategy for CRASH regime with confidence scoring."""

    name = "crash"
    mode = "CRASH"
    required_timeframes = ["4h", "1h"]

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        ind_4h = indicators.get("4h")
        ind_1h = indicators.get("1h")
        if not ind_4h or not ind_1h:
            return None

        rsi = ind_1h.get("rsi")
        macd_hist = ind_1h.get("macd_hist")
        volume_ratio = ind_4h.get("volume_ratio", 1.0)
        price = ind_1h.get("price")

        if any(v is None for v in [rsi, macd_hist, price]):
            return None

        # ─── Sub-scores ───
        rsi_score = _score_rsi_exhaustion(rsi, CRASH_RSI_ENTRY)
        macd_score = _score_macd_bearish(macd_hist)
        vol_score = _score_volume_spike(volume_ratio, CRASH_VOLUME_MIN)

        # ─── Weighted confidence ───
        confidence = (
            W_RSI * rsi_score
            + W_MACD * macd_score
            + W_VOLUME * vol_score
        )

        if confidence < CONFIDENCE_THRESHOLD:
            return None

        # ─── Build signal (SHORT only) ───
        strength = "STRONG" if confidence >= 0.7 else "WEAK"
        reasons = [
            f"CRASH_SHORT: conf={confidence:.2f}",
            f"  RSI_exhaust={rsi_score:.2f}(w={W_RSI}) rsi={rsi:.1f}",
            f"  MACD_bear={macd_score:.2f}(w={W_MACD}) hist={macd_hist:.4f}",
            f"  Vol_spike={vol_score:.2f}(w={W_VOLUME}) ratio={volume_ratio:.1f}x",
        ]

        score = 3.0 + confidence * 2.0

        return Signal(
            pair=pair,
            direction="SHORT",
            strategy=self.name,
            strength=strength,
            entry_price=price,
            reasons=reasons,
            score=score,
            confidence=confidence,
        )

    def get_position_params(self) -> PositionParams:
        return PositionParams(
            risk_pct=CRASH_RISK_PCT,
            leverage=CRASH_LEVERAGE,
            sl_atr_mult=CRASH_SL_ATR_MULT,
            min_rr=CRASH_MIN_RR,
        )

    def evaluate_exit(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> str | None:
        """Exit when RSI drops below 40 or MACD turns positive."""
        ind_1h = indicators.get("1h")
        if not ind_1h:
            return None

        rsi = ind_1h.get("rsi")
        macd_hist = ind_1h.get("macd_hist")

        if rsi is not None and rsi < 40:
            return "CRASH_EXIT: RSI oversold recovery"
        if macd_hist is not None and macd_hist > 0:
            return "CRASH_EXIT: MACD bullish flip"

        return None
