"""
trend_strategy.py — Mode B: Trend Trading
4 KEY indicators must all confirm (or 3/4 with day-of-week bias)

Entry: buying pullbacks in uptrend, selling bounces in downtrend.
All 4 conditions on 4H + 1H must align before entry.
"""

from __future__ import annotations
import os
from datetime import datetime

from ..config.settings import (
    TREND_RISK_PCT, TREND_LEVERAGE, TREND_SL_ATR_MULT, TREND_MIN_RR,
    BIAS_THRESHOLD, HKT, PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME,
    ENTRY_VOLUME_MIN, MACD_HIST_DECAY_THRESHOLD,
    OBV_CONFIRM_BONUS, OBV_AGAINST_PENALTY,
    TREND_MIN_CHANGE_PCT,
)
from ..core.context import CycleContext, Signal
from .base import StrategyBase, PositionParams


# ─── Trend-specific thresholds (from config/params.py) ───
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_params", os.path.join(os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading")), "config", "params.py")
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    PULLBACK_TOLERANCE = getattr(_mod, "PULLBACK_TOLERANCE", 0.015)
    TREND_RSI_LONG_LOW = getattr(_mod, "TREND_RSI_LONG_LOW", 40)
    TREND_RSI_LONG_HIGH = getattr(_mod, "TREND_RSI_LONG_HIGH", 55)
    TREND_RSI_SHORT_LOW = getattr(_mod, "TREND_RSI_SHORT_LOW", 45)
    TREND_RSI_SHORT_HIGH = getattr(_mod, "TREND_RSI_SHORT_HIGH", 60)
    TREND_MIN_KEYS = getattr(_mod, "TREND_MIN_KEYS", 4)
    del _ilu, _spec, _mod
except Exception:
    PULLBACK_TOLERANCE = 0.015
    TREND_RSI_LONG_LOW = 40
    TREND_RSI_LONG_HIGH = 55
    TREND_RSI_SHORT_LOW = 45
    TREND_RSI_SHORT_HIGH = 60
    TREND_MIN_KEYS = 4


def _check_day_bias(now: datetime) -> str | None:
    """
    Check day-of-week bias windows (UTC+8).
    Based on historical crypto weekend patterns.

    Thursday 21:00 – Friday 01:00 UTC+8 → SHORT bias
    Friday 21:00 – Saturday 03:00 UTC+8 → LONG bias

    When bias is active, only 3/4 KEY indicators needed.
    Returns "LONG", "SHORT", or None.
    """
    hkt = now.astimezone(HKT) if now.tzinfo else now
    weekday = hkt.weekday()  # Mon=0 ... Sun=6
    hour = hkt.hour

    # Thursday (3) 21:00 → Friday (4) 01:00 → SHORT bias
    if (weekday == 3 and hour >= 21) or (weekday == 4 and hour < 1):
        return "SHORT"

    # Friday (4) 21:00 → Saturday (5) 03:00 → LONG bias
    if (weekday == 4 and hour >= 21) or (weekday == 5 and hour < 3):
        return "LONG"

    return None


class TrendStrategy(StrategyBase):
    """
    Mode B — Trend Trading (pullback/bounce entry)

    Entry requires ALL 4 KEY conditions:
      KEY1: MA(4H) — Price above/below both 50MA and 200MA
      KEY2: MACD(4H) — Positive/negative with expanding histogram
      KEY3: RSI(1H) — 40-55 (LONG) or 45-60 (SHORT)
      KEY4: Price — Pulling back to 1H MA50

    Day-of-week bias reduces requirement to 3/4 KEY:
      Thu 21:00-01:00 UTC+8 → SHORT bias
      Fri 21:00-03:00 UTC+8 → LONG bias

    Adding new conditions:
      - Add to _evaluate_long() / _evaluate_short()
      - Adjust PULLBACK_TOLERANCE for sensitivity
    """
    name = "trend"
    mode = "TREND"
    required_timeframes = ["4h", "1h"]

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> Signal | None:
        """Evaluate trend entry for one pair."""
        ind_4h = indicators.get(PRIMARY_TIMEFRAME)
        ind_1h = indicators.get(SECONDARY_TIMEFRAME)

        if not ind_4h or not ind_1h:
            return None

        # ─── Volume gate (Yunis Collection) ───
        volume_ratio = ind_4h.get("volume_ratio", 1.0)
        if volume_ratio < ENTRY_VOLUME_MIN:
            return None  # volume too low — skip

        # ─── Minimum price change gate ───
        # Profile 控制趨勢入場最低變動：AGGRESSIVE=2%, BALANCED=5%
        if TREND_MIN_CHANGE_PCT is not None:
            high = ind_4h.get("high")
            low = ind_4h.get("low")
            if high and low and low > 0:
                change_pct = ((high - low) / low) * 100
                if change_pct < TREND_MIN_CHANGE_PCT:
                    return None  # 4H range too narrow for trend entry

        # Extract required values
        price = ind_4h.get("price")
        ma50_4h = ind_4h.get("ma50")
        ma200_4h = ind_4h.get("ma200")
        macd_hist = ind_4h.get("macd_hist")
        macd_hist_prev = ind_4h.get("macd_hist_prev")
        rsi_1h = ind_1h.get("rsi")
        ma50_1h = ind_1h.get("ma50")
        price_1h = ind_1h.get("price")

        # All data must be available
        if any(v is None for v in [
            price, ma50_4h, ma200_4h, macd_hist,
            macd_hist_prev, rsi_1h, ma50_1h, price_1h
        ]):
            return None

        # Check day-of-week bias
        bias = _check_day_bias(ctx.timestamp) if ctx.timestamp else None

        # ─── Evaluate conditions ───
        long_keys = self._evaluate_long(
            price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
            rsi_1h, price_1h, ma50_1h
        )
        short_keys = self._evaluate_short(
            price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
            rsi_1h, price_1h, ma50_1h
        )

        long_count = sum(1 for v in long_keys.values() if v)
        short_count = sum(1 for v in short_keys.values() if v)

        # Minimum required (TREND_MIN_KEYS normally, -1 with matching bias)
        min_long = max(TREND_MIN_KEYS - (1 if bias == "LONG" else 0), 3)
        min_short = max(TREND_MIN_KEYS - (1 if bias == "SHORT" else 0), 3)

        # ─── Volume score bonus (Yunis Collection) ───
        vol_bonus = 0.0
        if volume_ratio >= 2.0:
            vol_bonus = 1.0
        elif volume_ratio >= 1.5:
            vol_bonus = 0.5

        # ─── OBV confirmation (Yunis Collection) ───
        obv = ind_4h.get("obv")
        obv_ema = ind_4h.get("obv_ema")

        vol_spike = ind_1h.get("vol_spike", False) if ind_1h else False

        # ─── Check LONG ───
        if long_count >= min_long and long_count > short_count:
            reasons = [f"LONG_TREND: {long_count}/4 KEY confirmed"]
            for k, v in long_keys.items():
                reasons.append(f"  {k}: {'PASS' if v else 'FAIL'}")
            if bias == "LONG":
                reasons.append("  DAY_BIAS: LONG active (3/4 sufficient)")
            if vol_bonus > 0:
                reasons.append(f"  VOLUME_BONUS: +{vol_bonus} (ratio={volume_ratio:.2f})")
            if vol_spike:
                reasons.append("  VOL_SPIKE: detected (SMA-based)")

            obv_adj = 0.0
            if obv is not None and obv_ema is not None:
                if obv > obv_ema:
                    obv_adj = OBV_CONFIRM_BONUS
                elif obv < obv_ema:
                    obv_adj = OBV_AGAINST_PENALTY
                if obv_adj != 0.0:
                    obv_adj *= min(volume_ratio, 1.0)
                    label = "OBV_CONFIRM" if obv_adj > 0 else "OBV_AGAINST"
                    flow = "bullish" if obv > obv_ema else "bearish"
                    reasons.append(f"  {label}: {obv_adj:+.2f} ({flow} flow, vol={volume_ratio:.2f})")

            base_score = 5.0 if long_count == 4 else 3.5
            return Signal(
                pair=pair,
                direction="LONG",
                strategy=self.name,
                strength="STRONG" if long_count == 4 else "BIAS",
                entry_price=price_1h,
                reasons=reasons,
                score=base_score + vol_bonus + obv_adj,
            )

        # ─── Check SHORT ───
        if short_count >= min_short and short_count > long_count:
            reasons = [f"SHORT_TREND: {short_count}/4 KEY confirmed"]
            for k, v in short_keys.items():
                reasons.append(f"  {k}: {'PASS' if v else 'FAIL'}")
            if bias == "SHORT":
                reasons.append("  DAY_BIAS: SHORT active (3/4 sufficient)")
            if vol_bonus > 0:
                reasons.append(f"  VOLUME_BONUS: +{vol_bonus} (ratio={volume_ratio:.2f})")
            if vol_spike:
                reasons.append("  VOL_SPIKE: detected (SMA-based)")

            obv_adj = 0.0
            if obv is not None and obv_ema is not None:
                if obv < obv_ema:
                    obv_adj = OBV_CONFIRM_BONUS
                elif obv > obv_ema:
                    obv_adj = OBV_AGAINST_PENALTY
                if obv_adj != 0.0:
                    obv_adj *= min(volume_ratio, 1.0)
                    label = "OBV_CONFIRM" if obv_adj > 0 else "OBV_AGAINST"
                    flow = "bearish" if obv < obv_ema else "bullish"
                    reasons.append(f"  {label}: {obv_adj:+.2f} ({flow} flow, vol={volume_ratio:.2f})")

            base_score = 5.0 if short_count == 4 else 3.5
            return Signal(
                pair=pair,
                direction="SHORT",
                strategy=self.name,
                strength="STRONG" if short_count == 4 else "BIAS",
                entry_price=price_1h,
                reasons=reasons,
                score=base_score + vol_bonus + obv_adj,
            )

        return None  # Not enough KEY conditions met

    def _evaluate_long(
        self, price: float, ma50_4h: float, ma200_4h: float,
        macd_hist: float, macd_hist_prev: float,
        rsi_1h: float, price_1h: float, ma50_1h: float,
    ) -> dict[str, bool]:
        """
        Evaluate 4 KEY conditions for LONG (buying pullbacks).
          KEY1: Price above both 4H MAs (uptrend structure)
          KEY2: MACD histogram positive and expanding (momentum)
          KEY3: 1H RSI 40-55 (not overbought, room to run)
          KEY4: Price near 1H MA50 (pullback entry zone)
        """
        return {
            "MA_aligned": price > ma50_4h and price > ma200_4h,
            "MACD_bullish": (
                macd_hist > 0 and
                abs(macd_hist) > abs(macd_hist_prev)
            ),
            "RSI_pullback": TREND_RSI_LONG_LOW <= rsi_1h <= TREND_RSI_LONG_HIGH,
            "Price_at_MA": (
                abs(price_1h - ma50_1h) / ma50_1h < PULLBACK_TOLERANCE
                if ma50_1h > 0 else False
            ),
        }

    def _evaluate_short(
        self, price: float, ma50_4h: float, ma200_4h: float,
        macd_hist: float, macd_hist_prev: float,
        rsi_1h: float, price_1h: float, ma50_1h: float,
    ) -> dict[str, bool]:
        """
        Evaluate 4 KEY conditions for SHORT (selling bounces).
          KEY1: Price below both 4H MAs (downtrend structure)
          KEY2: MACD histogram negative and expanding (momentum)
          KEY3: 1H RSI 45-60 (not oversold, room to fall)
          KEY4: Price near 1H MA50 (bounce entry zone)
        """
        return {
            "MA_aligned": price < ma50_4h and price < ma200_4h,
            "MACD_bearish": (
                macd_hist < 0 and
                abs(macd_hist) > abs(macd_hist_prev)
            ),
            "RSI_bounce": TREND_RSI_SHORT_LOW <= rsi_1h <= TREND_RSI_SHORT_HIGH,
            "Price_at_MA": (
                abs(price_1h - ma50_1h) / ma50_1h < PULLBACK_TOLERANCE
                if ma50_1h > 0 else False
            ),
        }

    def get_position_params(self) -> PositionParams:
        """Trend: 2% risk, 7x leverage, SL=1.5*ATR, min R:R 3.0."""
        return PositionParams(
            risk_pct=TREND_RISK_PCT,
            leverage=TREND_LEVERAGE,
            sl_atr_mult=TREND_SL_ATR_MULT,
            min_rr=TREND_MIN_RR,
        )

    def evaluate_exit(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> str | None:
        """
        Trend exit conditions (checked every cycle for open positions):
          1. MACD 4H reverse crossover (histogram sign flip)
          1b. MACD 4H histogram weakening >40% + RR ≥ 1.0 (Yunis Collection)
          2. Price back between 50MA and 200MA (trend structure broken)
          3. 3+ mode votes flip to RANGE (market regime change)
        """
        ind_4h = indicators.get(PRIMARY_TIMEFRAME)
        if not ind_4h:
            return None

        # ─── MACD reversal on 4H ───
        macd_hist = ind_4h.get("macd_hist")
        macd_hist_prev = ind_4h.get("macd_hist_prev")
        if macd_hist is not None and macd_hist_prev is not None:
            # Sign flip = momentum reversal
            if macd_hist_prev > 0 and macd_hist < 0:
                return "MACD_REVERSAL: histogram turned negative on 4H"
            if macd_hist_prev < 0 and macd_hist > 0:
                return "MACD_REVERSAL: histogram turned positive on 4H"

            # ─── MACD weakening early exit (Yunis Collection) ───
            # Histogram same sign but decaying >40% → trend losing steam
            # Only exit if position already has R:R ≥ 1.0
            position = self._find_position(pair, ctx)
            if position and macd_hist_prev != 0:
                current_rr = self._calc_current_rr(position)
                if current_rr >= 1.0:
                    decay = abs(macd_hist) / abs(macd_hist_prev)
                    if (position.direction == "LONG" and macd_hist > 0
                            and decay < MACD_HIST_DECAY_THRESHOLD):
                        return (
                            f"MACD_WEAKENING_EXIT: histogram decayed to "
                            f"{decay:.0%} (R:R={current_rr:.1f})"
                        )
                    if (position.direction == "SHORT" and macd_hist < 0
                            and decay < MACD_HIST_DECAY_THRESHOLD):
                        return (
                            f"MACD_WEAKENING_EXIT: histogram decayed to "
                            f"{decay:.0%} (R:R={current_rr:.1f})"
                        )

        # ─── Price back between MAs ───
        price = ind_4h.get("price")
        ma50 = ind_4h.get("ma50")
        ma200 = ind_4h.get("ma200")
        if price is not None and ma50 is not None and ma200 is not None:
            lower_ma = min(ma50, ma200)
            upper_ma = max(ma50, ma200)
            if lower_ma < price < upper_ma:
                return "MA_CROSS: price between 50MA and 200MA (trend weakening)"

        return None

    @staticmethod
    def _find_position(pair: str, ctx: CycleContext):
        """Find open position for this pair."""
        for pos in ctx.open_positions:
            if pos.pair == pair:
                return pos
        return None

    @staticmethod
    def _calc_current_rr(position) -> float:
        """Calculate current reward:risk ratio for an open position."""
        if not position.sl_price or position.entry_price == position.sl_price:
            return 0.0
        risk = abs(position.entry_price - position.sl_price)
        if risk <= 0:
            return 0.0
        reward = abs(position.mark_price - position.entry_price)
        return reward / risk
