"""
bt_burst_strategy.py — XRP Burst Strategy for backtest.

設計決定：
  XRP 嘅特性係長期 range-bound 但間中有 volume spike burst。
  傳統 range/trend 策略喺 XRP 上表現差，因為：
  - Range：XRP 嘅 range 太闊、breakout 太突然
  - Trend：XRP trend 持續時間短

  Burst strategy 專門捕捉 volume spike 帶動嘅短期 momentum：
  - LONG：volume burst + price up + OBV 確認 → ride the spike
  - SHORT：volume burst + price down + 做空回調

觸發條件（全部要 pass）：
  1. volume_ratio > 2.5（30-candle avg 嘅 2.5 倍）
  2. |price_change_1h| > 2%
  3. OBV 方向一致（OBV > OBV_EMA 做 LONG，OBV < OBV_EMA 做 SHORT）
  4. volatility_regime ≠ HIGH（crash 時唔啟動）

安全網：
  - 4H cooldown（4 個 1H candle）after each burst signal
  - Confidence cap at 0.80
  - SHORT only when price_change_1h < -2%（唔亂做空）
"""

from __future__ import annotations

import os
import sys

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(_AXC, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal

# ── Thresholds ──
VOLUME_RATIO_MIN = 2.5        # minimum volume spike (× 30-candle avg)
PRICE_CHANGE_MIN = 0.02       # minimum |price change| (2%)
CONFIDENCE_CAP = 0.80         # max confidence output
CONFIDENCE_THRESHOLD = 0.30   # min confidence to emit signal
BURST_COOLDOWN_CANDLES = 4    # internal cooldown (4H on 1H clock)

# ── Sub-score weights (sum = 1.0) ──
W_VOLUME = 0.40    # volume spike strength
W_MOMENTUM = 0.35  # price change magnitude
W_OBV = 0.25       # OBV confirmation strength


def _score_volume(volume_ratio: float) -> float:
    """Score volume spike: 0 at 2.5, 1.0 at 5.0+."""
    if volume_ratio <= VOLUME_RATIO_MIN:
        return 0.0
    return min((volume_ratio - VOLUME_RATIO_MIN) / 2.5, 1.0)


def _score_momentum(price_change_pct: float) -> float:
    """Score price change magnitude: 0 at 2%, 1.0 at 6%+."""
    abs_change = abs(price_change_pct)
    if abs_change <= PRICE_CHANGE_MIN:
        return 0.0
    return min((abs_change - PRICE_CHANGE_MIN) / 0.04, 1.0)


def _score_obv(obv: float, obv_ema: float, direction: str) -> float:
    """Score OBV confirmation: 0.0 (against) to 1.0 (strong with).
    direction="LONG" → OBV > OBV_EMA is positive.
    direction="SHORT" → OBV < OBV_EMA is positive."""
    if obv is None or obv_ema is None or obv_ema == 0:
        return 0.0
    obv_diff_pct = (obv - obv_ema) / abs(obv_ema) if obv_ema != 0 else 0.0
    if direction == "LONG":
        # OBV above EMA → bullish confirmation
        if obv_diff_pct <= 0:
            return 0.0
        return min(obv_diff_pct / 0.10, 1.0)  # 10% above EMA = full score
    else:
        # OBV below EMA → bearish confirmation
        if obv_diff_pct >= 0:
            return 0.0
        return min(abs(obv_diff_pct) / 0.10, 1.0)


class BTBurstStrategy(StrategyBase):
    """XRP volume-burst strategy for backtest.

    Detects volume spikes with price momentum and OBV confirmation.
    Emits LONG on upward bursts, SHORT on downward bursts.
    Internal 4H cooldown prevents rapid-fire signals.
    """

    name = "burst"
    mode = "BURST"  # doesn't map to any real mode — always penalized by mode affinity
    required_timeframes = ["1h"]

    def __init__(
        self,
        entry_overrides: dict | None = None,
        position_overrides: dict | None = None,
    ):
        self._entry = entry_overrides or {}
        self._pos = position_overrides or {}
        # Internal cooldown counter (decremented each candle by engine caller)
        self._cooldown_remaining = 0

    @property
    def vol_min(self) -> float:
        return self._entry.get("volume_min", VOLUME_RATIO_MIN)

    @property
    def price_change_min(self) -> float:
        return self._entry.get("price_change_min", PRICE_CHANGE_MIN)

    @property
    def cooldown(self) -> int:
        return self._entry.get("cooldown", BURST_COOLDOWN_CANDLES)

    def tick_cooldown(self):
        """Called each candle by engine to decrement internal burst cooldown."""
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        # ── Internal cooldown check ──
        if self._cooldown_remaining > 0:
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

        # ── Compute price change from close vs previous close ──
        # Use rolling_high/low and price to approximate 1H change
        # Engine provides prev_close if available, otherwise use BB basis as proxy
        prev_close = ind_1h.get("prev_close")
        if prev_close is None or prev_close <= 0:
            # Fallback: can't compute price change without prev_close
            return None

        price_change_pct = (price - prev_close) / prev_close

        # ── Hard gates (ALL must pass) ──
        if volume_ratio < self.vol_min:
            return None
        if abs(price_change_pct) < self.price_change_min:
            return None

        # ── Direction from price change ──
        direction = "LONG" if price_change_pct > 0 else "SHORT"

        # ── OBV confirmation gate ──
        obv_score = _score_obv(obv, obv_ema, direction)
        if obv_score <= 0:
            return None  # OBV must confirm direction

        # ── Confidence scoring ──
        vol_score = _score_volume(volume_ratio)
        mom_score = _score_momentum(price_change_pct)

        confidence = W_VOLUME * vol_score + W_MOMENTUM * mom_score + W_OBV * obv_score
        confidence = min(confidence, CONFIDENCE_CAP)

        if confidence < CONFIDENCE_THRESHOLD:
            return None

        # ── Activate burst cooldown ──
        self._cooldown_remaining = self.cooldown

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
        """Burst trades: tight SL (1.5 ATR), moderate leverage, quick TP."""
        return PositionParams(
            risk_pct=self._pos.get("risk_pct", 0.02),
            leverage=self._pos.get("leverage", 5),
            sl_atr_mult=self._pos.get("sl_atr_mult", 1.5),
            min_rr=self._pos.get("min_rr", 2.0),
        )
