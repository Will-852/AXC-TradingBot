"""
crash_strategy.py — CRASH mode 策略（高波動防守型）

設計決定：
  - 只做 SHORT（crash = 跌市，唔追反彈）
  - 更闊 SL（ATR × 2.0，因為波動大）
  - 更保守 risk（1% instead of 2%）
  - 更低槓桿（5x instead of 7-8x）
  - 需要更強信號（RSI > 75 + MACD bearish + volume spike）
  - R:R ≥ 1.5（較低門檻，因為 crash 環境利潤空間大）
"""

from __future__ import annotations

from ..config.settings import (
    CRASH_RISK_PCT, CRASH_LEVERAGE, CRASH_SL_ATR_MULT,
    CRASH_MIN_RR, CRASH_RSI_ENTRY, CRASH_VOLUME_MIN,
)
from .base import StrategyBase, PositionParams
from ..core.context import CycleContext, Signal


class CrashStrategy(StrategyBase):
    """SHORT-only strategy for CRASH regime (HMM state=2).

    Entry requires all 3 conditions:
      1. RSI > CRASH_RSI_ENTRY (75) — overbought / relief rally exhaustion
      2. MACD histogram < 0 — bearish momentum
      3. volume_ratio > CRASH_VOLUME_MIN (2.0) — volume spike confirming panic
    """

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

        # ─── Entry conditions: all 3 must pass ───
        conditions = {
            "RSI_overbought": rsi > CRASH_RSI_ENTRY,
            "MACD_bearish": macd_hist < 0,
            "Volume_spike": volume_ratio > CRASH_VOLUME_MIN,
        }

        if not all(conditions.values()):
            return None

        # ─── Only SHORT in crash ───
        reasons = [f"CRASH_SHORT: RSI={rsi:.1f} MACD_h={macd_hist:.4f} Vol={volume_ratio:.1f}x"]
        for k, v in conditions.items():
            reasons.append(f"  {k}: {'PASS' if v else 'FAIL'}")

        # Score based on how extreme conditions are
        score = 3.0
        if rsi > 80:
            score += 0.5
        if volume_ratio > 3.0:
            score += 0.5
        if abs(macd_hist) > 0.01:
            score += 0.5

        return Signal(
            pair=pair,
            direction="SHORT",
            strategy=self.name,
            strength="STRONG" if score >= 4.0 else "WEAK",
            entry_price=price,
            reasons=reasons,
            score=score,
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
