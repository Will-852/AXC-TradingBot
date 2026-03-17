"""
trend_strategy.py — Trend Trading (pullback/bounce entry)

Phase 1 重構：
  - Binary 4-KEY → weighted confidence scoring (0-1)
  - Day-of-week bias retained as small bonus (+0.05)
  - 閾值 0.3

Sub-score weights:
  MA alignment  0.25 — 4H MA50/MA200 趨勢結構
  MACD momentum 0.25 — 4H histogram 方向 + 擴張
  RSI pullback  0.20 — 1H RSI sweet spot（唔過熱）
  Price at MA   0.15 — 1H 價格貼近 MA50（pullback 入場區）
  Volume        0.10 — 4H 成交量
  OBV           0.05 — 資金流方向
"""

from __future__ import annotations
import os
from datetime import datetime

from ..config.settings import (
    TREND_RISK_PCT, TREND_LEVERAGE, TREND_SL_ATR_MULT, TREND_MIN_RR,
    BIAS_THRESHOLD, HKT, PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME,
    ENTRY_VOLUME_MIN, MACD_HIST_DECAY_THRESHOLD,
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
    del _ilu, _spec, _mod
except Exception:
    PULLBACK_TOLERANCE = 0.015
    TREND_RSI_LONG_LOW = 40
    TREND_RSI_LONG_HIGH = 55
    TREND_RSI_SHORT_LOW = 45
    TREND_RSI_SHORT_HIGH = 60

# ─── Confidence weights ───
W_MA = 0.25
W_MACD = 0.25
W_RSI = 0.20
W_PRICE_MA = 0.15
W_VOLUME = 0.10
W_OBV = 0.05

CONFIDENCE_THRESHOLD = 0.30
DAY_BIAS_BONUS = 0.05


def _check_day_bias(now: datetime) -> str | None:
    """Day-of-week bias: Thu night → SHORT, Fri night → LONG."""
    hkt = now.astimezone(HKT) if now.tzinfo else now
    weekday = hkt.weekday()
    hour = hkt.hour
    if (weekday == 3 and hour >= 21) or (weekday == 4 and hour < 1):
        return "SHORT"
    if (weekday == 4 and hour >= 21) or (weekday == 5 and hour < 3):
        return "LONG"
    return None


# ─── Sub-score functions ───

def _score_ma_alignment(price: float, ma50: float, ma200: float,
                        direction: str) -> float:
    """MA alignment: both MAs confirming direction → 1.0.

    LONG: price > both MAs → 1.0; price > one → 0.5; neither → 0.0
    SHORT: inverse
    """
    if direction == "LONG":
        above_50 = price > ma50
        above_200 = price > ma200
        if above_50 and above_200:
            return 1.0
        elif above_50 or above_200:
            return 0.5
        return 0.0
    else:  # SHORT
        below_50 = price < ma50
        below_200 = price < ma200
        if below_50 and below_200:
            return 1.0
        elif below_50 or below_200:
            return 0.5
        return 0.0


def _score_macd_momentum(macd_hist: float, macd_hist_prev: float,
                         direction: str) -> float:
    """MACD momentum: correct direction + expanding → 1.0.

    Components: direction match (0.6) + expanding (0.4)
    """
    if macd_hist is None or macd_hist_prev is None:
        return 0.0

    score = 0.0
    if direction == "LONG":
        if macd_hist > 0:
            score += 0.6
            if abs(macd_hist) > abs(macd_hist_prev):
                score += 0.4
    else:  # SHORT
        if macd_hist < 0:
            score += 0.6
            if abs(macd_hist) > abs(macd_hist_prev):
                score += 0.4
    return score


def _score_rsi_pullback(rsi: float, direction: str) -> float:
    """RSI in sweet spot (not overbought/oversold, room to move).

    LONG: RSI 40-55 → 1.0 at center (47.5), 0.0 outside
    SHORT: RSI 45-60 → 1.0 at center (52.5), 0.0 outside
    """
    if rsi is None:
        return 0.0

    if direction == "LONG":
        low, high = TREND_RSI_LONG_LOW, TREND_RSI_LONG_HIGH
    else:
        low, high = TREND_RSI_SHORT_LOW, TREND_RSI_SHORT_HIGH

    if rsi < low or rsi > high:
        # Partial score if close to window (within 5 pts)
        if rsi < low:
            return max(0.0, 1.0 - (low - rsi) / 5.0) * 0.3
        else:
            return max(0.0, 1.0 - (rsi - high) / 5.0) * 0.3

    # Inside window — peak at center
    center = (low + high) / 2
    half_width = (high - low) / 2
    dist = abs(rsi - center) / half_width
    return 1.0 - 0.3 * dist  # 1.0 at center, 0.7 at edges


def _score_price_at_ma(price: float, ma50: float,
                       tolerance: float) -> float:
    """Price proximity to 1H MA50 (pullback entry zone).

    Within tolerance → 1.0, 3× tolerance → 0.0
    """
    if ma50 is None or ma50 <= 0:
        return 0.0
    dist = abs(price - ma50) / ma50
    if dist <= tolerance:
        return 1.0
    score = 1.0 - (dist - tolerance) / (2 * tolerance)
    return max(0.0, score)


def _score_volume(volume_ratio: float) -> float:
    """Volume confirmation: higher = better. 0.5→0, 1.5→0.5, 3.0→1.0"""
    if volume_ratio is None or volume_ratio < 0.5:
        return 0.0
    return min((volume_ratio - 0.5) / 2.5, 1.0)


def _score_obv(obv: float | None, obv_ema: float | None,
               direction: str) -> float:
    """OBV flow direction match."""
    if obv is None or obv_ema is None:
        return 0.5
    if direction == "LONG":
        return 1.0 if obv > obv_ema else 0.0
    return 1.0 if obv < obv_ema else 0.0


class TrendStrategy(StrategyBase):
    """Trend Trading — pullback/bounce entry with confidence scoring."""

    name = "trend"
    mode = "TREND"
    required_timeframes = ["4h", "1h"]

    def evaluate(
        self, pair: str, indicators: dict[str, dict], ctx: CycleContext
    ) -> Signal | None:
        """Evaluate trend entry using weighted confidence scoring."""
        ind_4h = indicators.get(PRIMARY_TIMEFRAME)
        ind_1h = indicators.get(SECONDARY_TIMEFRAME)
        if not ind_4h or not ind_1h:
            return None

        volume_ratio = ind_4h.get("volume_ratio", 1.0)
        if volume_ratio < ENTRY_VOLUME_MIN:
            return None

        # Minimum price change gate
        if TREND_MIN_CHANGE_PCT is not None:
            high = ind_4h.get("high")
            low = ind_4h.get("low")
            if high and low and low > 0:
                change_pct = ((high - low) / low) * 100
                if change_pct < TREND_MIN_CHANGE_PCT:
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

        # ─── Evaluate both directions, pick the better one ───
        long_conf = self._calc_confidence(
            "LONG", price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
            rsi_1h, price_1h, ma50_1h, volume_ratio,
            ind_4h.get("obv"), ind_4h.get("obv_ema"),
        )
        short_conf = self._calc_confidence(
            "SHORT", price, ma50_4h, ma200_4h, macd_hist, macd_hist_prev,
            rsi_1h, price_1h, ma50_1h, volume_ratio,
            ind_4h.get("obv"), ind_4h.get("obv_ema"),
        )

        # Pick dominant direction
        if long_conf >= short_conf:
            direction, confidence, sub = "LONG", long_conf, self._sub_scores_cache
        else:
            direction, confidence, sub = "SHORT", short_conf, self._sub_scores_cache

        # Day-of-week bias bonus
        bias = _check_day_bias(ctx.timestamp) if ctx.timestamp else None
        bias_bonus = DAY_BIAS_BONUS if bias == direction else 0.0
        confidence += bias_bonus

        if confidence < CONFIDENCE_THRESHOLD:
            return None

        # ─── Build signal ───
        strength = "STRONG" if confidence >= 0.7 else "WEAK"
        reasons = [
            f"TREND_{direction}: conf={confidence:.2f}",
            f"  MA_align={sub['ma']:.2f}(w={W_MA})",
            f"  MACD={sub['macd']:.2f}(w={W_MACD}) hist={macd_hist:.4f}",
            f"  RSI_pb={sub['rsi']:.2f}(w={W_RSI}) rsi={rsi_1h:.1f}",
            f"  Price@MA={sub['price_ma']:.2f}(w={W_PRICE_MA})",
            f"  Volume={sub['vol']:.2f}(w={W_VOLUME}) ratio={volume_ratio:.2f}",
            f"  OBV={sub['obv']:.2f}(w={W_OBV})",
        ]
        if bias_bonus > 0:
            reasons.append(f"  DAY_BIAS: {bias} +{bias_bonus:.2f}")

        score = 3.0 + confidence * 2.0

        return Signal(
            pair=pair,
            direction=direction,
            strategy=self.name,
            strength=strength,
            entry_price=price_1h,
            reasons=reasons,
            score=score,
            confidence=confidence,
        )

    def _calc_confidence(
        self, direction: str,
        price: float, ma50_4h: float, ma200_4h: float,
        macd_hist: float, macd_hist_prev: float,
        rsi_1h: float, price_1h: float, ma50_1h: float,
        volume_ratio: float, obv: float | None, obv_ema: float | None,
    ) -> float:
        """Calculate weighted confidence for a direction."""
        sub = {
            "ma": _score_ma_alignment(price, ma50_4h, ma200_4h, direction),
            "macd": _score_macd_momentum(macd_hist, macd_hist_prev, direction),
            "rsi": _score_rsi_pullback(rsi_1h, direction),
            "price_ma": _score_price_at_ma(price_1h, ma50_1h, PULLBACK_TOLERANCE),
            "vol": _score_volume(volume_ratio),
            "obv": _score_obv(obv, obv_ema, direction),
        }
        self._sub_scores_cache = sub

        return (
            W_MA * sub["ma"]
            + W_MACD * sub["macd"]
            + W_RSI * sub["rsi"]
            + W_PRICE_MA * sub["price_ma"]
            + W_VOLUME * sub["vol"]
            + W_OBV * sub["obv"]
        )

    # Cache for last _calc_confidence call (avoid recomputing for reasons)
    _sub_scores_cache: dict = {}

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
        """Trend exit: MACD reversal, MA cross, or MACD weakening."""
        ind_4h = indicators.get(PRIMARY_TIMEFRAME)
        if not ind_4h:
            return None

        macd_hist = ind_4h.get("macd_hist")
        macd_hist_prev = ind_4h.get("macd_hist_prev")
        if macd_hist is not None and macd_hist_prev is not None:
            # Sign flip
            if macd_hist_prev > 0 and macd_hist < 0:
                return "MACD_REVERSAL: histogram turned negative on 4H"
            if macd_hist_prev < 0 and macd_hist > 0:
                return "MACD_REVERSAL: histogram turned positive on 4H"

            # MACD weakening early exit
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
        for pos in ctx.open_positions:
            if pos.pair == pair:
                return pos
        return None

    @staticmethod
    def _calc_current_rr(position) -> float:
        if not position.sl_price or position.entry_price == position.sl_price:
            return 0.0
        risk = abs(position.entry_price - position.sl_price)
        if risk <= 0:
            return 0.0
        return abs(position.mark_price - position.entry_price) / risk
