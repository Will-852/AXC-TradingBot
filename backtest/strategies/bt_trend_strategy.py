"""
bt_trend_strategy.py — Configurable Trend Strategy for backtest optimization.

設計決定：
  - 同 production TrendStrategy 邏輯一致但所有閾值可配置
  - 支持 trend_min_keys (3 or 4) 控制入場嚴格度
  - 支持 pullback_tolerance, rsi 範圍配置
  - 評分用 WeightedScorer
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(_AXC, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal

from backtest.scoring import WeightedScorer, ScoringWeights

HKT = timezone(timedelta(hours=8))


def _check_day_bias(now: datetime) -> str | None:
    """Day-of-week bias windows (UTC+8). Same logic as production."""
    hkt = now.astimezone(HKT) if now.tzinfo else now
    weekday = hkt.weekday()
    hour = hkt.hour
    if (weekday == 3 and hour >= 21) or (weekday == 4 and hour < 1):
        return "SHORT"
    if (weekday == 4 and hour >= 21) or (weekday == 5 and hour < 3):
        return "LONG"
    return None


class BTTrendStrategy(StrategyBase):
    """
    Configurable trend strategy for backtest parameter search.

    Configurable via entry_overrides:
      - trend_min_keys: 3 or 4 (default 4)
      - pullback_tolerance: float (default 0.015)
      - rsi_long_low/high, rsi_short_low/high
      - entry_volume_min: float (default 0.8)
      - mode_confirmation: 1 or 2
    """
    name = "trend"
    mode = "TREND"
    required_timeframes = ["4h", "1h"]

    def __init__(
        self,
        entry_overrides: dict | None = None,
        scorer: WeightedScorer | None = None,
        position_overrides: dict | None = None,
    ):
        self._entry = entry_overrides or {}
        self._scorer = scorer or WeightedScorer()
        self._pos = position_overrides or {}

    @property
    def trend_min_keys(self) -> int:
        return self._entry.get("trend_min_keys", 4)

    @property
    def pullback_tolerance(self) -> float:
        return self._entry.get("pullback_tolerance", 0.015)

    @property
    def rsi_long_low(self) -> float:
        return self._entry.get("rsi_long_low", 40)

    @property
    def rsi_long_high(self) -> float:
        return self._entry.get("rsi_long_high", 55)

    @property
    def rsi_short_low(self) -> float:
        return self._entry.get("rsi_short_low", 45)

    @property
    def rsi_short_high(self) -> float:
        return self._entry.get("rsi_short_high", 60)

    @property
    def entry_volume_min(self) -> float:
        return self._entry.get("entry_volume_min", 0.8)

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext,
    ) -> Signal | None:
        """Evaluate trend entry with configurable thresholds."""
        ind_4h = indicators.get("4h")
        ind_1h = indicators.get("1h")
        if not ind_4h or not ind_1h:
            return None

        volume_ratio = ind_4h.get("volume_ratio", 1.0)
        if volume_ratio < self.entry_volume_min:
            return None

        price = ind_4h.get("price")
        ma50_4h = ind_4h.get("ma50")
        ma200_4h = ind_4h.get("ma200")
        macd_hist = ind_4h.get("macd_hist")
        macd_hist_prev = ind_4h.get("macd_hist_prev")
        rsi_1h = ind_1h.get("rsi")
        ma50_1h = ind_1h.get("ma50")
        price_1h = ind_1h.get("price")

        if any(v is None for v in [
            price, ma50_4h, ma200_4h, macd_hist,
            macd_hist_prev, rsi_1h, ma50_1h, price_1h
        ]):
            return None

        bias = _check_day_bias(ctx.timestamp) if ctx.timestamp else None

        long_keys = self._evaluate_long(
            price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
            rsi_1h, price_1h, ma50_1h,
        )
        short_keys = self._evaluate_short(
            price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
            rsi_1h, price_1h, ma50_1h,
        )

        long_count = sum(1 for v in long_keys.values() if v)
        short_count = sum(1 for v in short_keys.values() if v)

        # Configurable minimum keys (base from self.trend_min_keys, bias reduces by 1)
        min_long = max(self.trend_min_keys - (1 if bias == "LONG" else 0), 3)
        min_short = max(self.trend_min_keys - (1 if bias == "SHORT" else 0), 3)

        # OBV signal
        obv = ind_4h.get("obv")
        obv_ema = ind_4h.get("obv_ema")

        vol_spike = ind_1h.get("vol_spike", False)

        # ─── LONG ───
        if long_count >= min_long and long_count > short_count:
            obv_signal = 0
            if obv is not None and obv_ema is not None:
                obv_signal = 1 if obv > obv_ema else (-1 if obv < obv_ema else 0)

            score = self._scorer.score_trend(
                key_count=long_count,
                volume_ratio=volume_ratio,
                obv_signal=obv_signal,
                vol_spike=vol_spike,
            )
            strength = "STRONG" if long_count == 4 else "BIAS"
            reasons = [f"LONG_TREND: {long_count}/4 KEY confirmed"]
            for k, v in long_keys.items():
                reasons.append(f"  {k}: {'PASS' if v else 'FAIL'}")

            return Signal(
                pair=pair, direction="LONG", strategy=self.name,
                strength=strength, entry_price=price_1h,
                reasons=reasons, score=score,
            )

        # ─── SHORT ───
        if short_count >= min_short and short_count > long_count:
            obv_signal = 0
            if obv is not None and obv_ema is not None:
                obv_signal = 1 if obv < obv_ema else (-1 if obv > obv_ema else 0)

            score = self._scorer.score_trend(
                key_count=short_count,
                volume_ratio=volume_ratio,
                obv_signal=obv_signal,
                vol_spike=vol_spike,
            )
            strength = "STRONG" if short_count == 4 else "BIAS"
            reasons = [f"SHORT_TREND: {short_count}/4 KEY confirmed"]
            for k, v in short_keys.items():
                reasons.append(f"  {k}: {'PASS' if v else 'FAIL'}")

            return Signal(
                pair=pair, direction="SHORT", strategy=self.name,
                strength=strength, entry_price=price_1h,
                reasons=reasons, score=score,
            )

        return None

    def _evaluate_long(
        self, price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
        rsi_1h, price_1h, ma50_1h,
    ) -> dict[str, bool]:
        return {
            "MA_aligned": price > ma50_4h and price > ma200_4h,
            "MACD_bullish": macd_hist > 0 and abs(macd_hist) > abs(macd_hist_prev),
            "RSI_pullback": self.rsi_long_low <= rsi_1h <= self.rsi_long_high,
            "Price_at_MA": (
                abs(price_1h - ma50_1h) / ma50_1h < self.pullback_tolerance
                if ma50_1h > 0 else False
            ),
        }

    def _evaluate_short(
        self, price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
        rsi_1h, price_1h, ma50_1h,
    ) -> dict[str, bool]:
        return {
            "MA_aligned": price < ma50_4h and price < ma200_4h,
            "MACD_bearish": macd_hist < 0 and abs(macd_hist) > abs(macd_hist_prev),
            "RSI_bounce": self.rsi_short_low <= rsi_1h <= self.rsi_short_high,
            "Price_at_MA": (
                abs(price_1h - ma50_1h) / ma50_1h < self.pullback_tolerance
                if ma50_1h > 0 else False
            ),
        }

    def get_position_params(self) -> PositionParams:
        return PositionParams(
            risk_pct=self._pos.get("risk_pct", 0.02),
            leverage=self._pos.get("leverage", 7),
            sl_atr_mult=self._pos.get("sl_atr_mult", 1.5),
            min_rr=self._pos.get("min_rr", 3.0),
        )
