"""
burst_strategy.py — Volume Burst Strategy (Production)

捕捉 volume spike 帶動嘅短期 momentum。同 squeeze strategy 共享 signal module
但觸發條件完全唔同：squeeze 等壓縮後爆發，burst 捉即時 volume spike。

觸發條件（全部要 pass）：
  1. volume_ratio > 2.5（30-candle avg 嘅 2.5 倍）
  2. |price_change_1h| > 2%
  3. OBV 方向一致（OBV > OBV_EMA 做 LONG，OBV < OBV_EMA 做 SHORT）
  4. volatility_regime ≠ HIGH（crash 時唔啟動）

安全網：
  - 4H cooldown（4 個 1H candle）after each burst signal
  - Confidence cap at 0.80

SL = 1.5 × ATR, R:R = 2.0 → BE = 33%

Origin: backtest/strategies/bt_burst_strategy.py — 搬入 production，
保持 signal parity。
"""

from __future__ import annotations

import logging
import time

from ..core.context import CycleContext, Signal
from .base import StrategyBase, PositionParams

from signals.volume import score_volume_spike
from signals.obv import score_obv_confirmation

log = logging.getLogger(__name__)

# ── Thresholds ──
VOLUME_RATIO_MIN = 2.5        # minimum volume spike (× 30-candle avg)
VOLUME_SPIKE_CEILING = 5.0    # full score at 5x (decoupled from VOLUME_RATIO_MIN)
PRICE_CHANGE_MIN = 0.02       # minimum |price change| (2%)
CONFIDENCE_CAP = 0.80         # max confidence output
CONFIDENCE_THRESHOLD = 0.30   # min confidence to emit signal
BURST_COOLDOWN_SECONDS = 4 * 3600  # 4 hours (= 4 × 1H candles, time-based for production)

# ── Sub-score weights (sum = 1.0) ──
W_VOLUME = 0.40    # volume spike strength
W_MOMENTUM = 0.35  # price change magnitude
W_OBV = 0.25       # OBV confirmation strength


def _score_momentum(price_change_pct: float) -> float:
    """Score price change magnitude: 0 at 2%, 1.0 at 6%+."""
    abs_change = abs(price_change_pct)
    if abs_change <= PRICE_CHANGE_MIN:
        return 0.0
    return min((abs_change - PRICE_CHANGE_MIN) / 0.04, 1.0)


class BurstStrategy(StrategyBase):
    """Volume-burst strategy for production.

    Detects volume spikes with price momentum and OBV confirmation.
    Emits LONG on upward bursts, SHORT on downward bursts.
    Internal 4H cooldown prevents rapid-fire signals.
    """

    name = "burst"
    mode = "BURST"
    required_timeframes = ["1h"]

    def __init__(self):
        # Per-symbol cooldown: {symbol: cooldown_expiry_timestamp}
        self._cooldowns: dict[str, float] = {}

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        # ── Time-based cooldown check (per-symbol) ──
        now = time.time()
        if self._cooldowns.get(pair, 0) > now:
            return None

        # ── Safety: skip during HIGH volatility regime (crash conditions) ──
        if ctx is not None and ctx.volatility_regime == "HIGH":
            return None

        ind_1h = indicators.get("1h")
        if not ind_1h:
            return None

        price = ind_1h.get("price")
        volume_ratio = ind_1h.get("volume_ratio", 1.0)
        obv = ind_1h.get("obv")
        obv_ema = ind_1h.get("obv_ema")

        if price is None or price <= 0:
            return None

        # ── Price change from prev_close ──
        prev_close = ind_1h.get("prev_close")
        if prev_close is None or prev_close <= 0:
            return None

        price_change_pct = (price - prev_close) / prev_close

        # ── Hard gates (ALL must pass) ──
        if volume_ratio < VOLUME_RATIO_MIN:
            return None
        if abs(price_change_pct) < PRICE_CHANGE_MIN:
            return None

        # ── Direction from price change ──
        direction = "LONG" if price_change_pct > 0 else "SHORT"

        # ── OBV confirmation gate (shared signal module) ──
        obv_score = score_obv_confirmation(obv, obv_ema, direction)
        if obv_score <= 0:
            return None

        # ── Confidence scoring ──
        vol_score = score_volume_spike(
            volume_ratio, floor=VOLUME_RATIO_MIN, ceiling=VOLUME_SPIKE_CEILING,
        )
        mom_score = _score_momentum(price_change_pct)

        confidence = W_VOLUME * vol_score + W_MOMENTUM * mom_score + W_OBV * obv_score
        confidence = min(confidence, CONFIDENCE_CAP)

        if confidence < CONFIDENCE_THRESHOLD:
            return None

        # ── Activate burst cooldown (time-based, per-symbol) ──
        self._cooldowns[pair] = time.time() + BURST_COOLDOWN_SECONDS

        strength = "STRONG" if confidence >= 0.6 else "WEAK"
        score = 3.0 + confidence * 2.0  # 3.0-5.0 range

        reasons = [
            f"BURST_{direction}: vol={volume_ratio:.1f}x "
            f"Δp={price_change_pct*100:+.1f}% "
            f"OBV={'↑' if direction == 'LONG' else '↓'}"
        ]

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
        return PositionParams(
            risk_pct=0.02,
            leverage=5,
            sl_atr_mult=1.5,
            min_rr=2.0,
        )
