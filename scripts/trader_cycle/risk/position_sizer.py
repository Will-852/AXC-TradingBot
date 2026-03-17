"""
position_sizer.py — Position sizing, SL/TP calculation, funding cost adjustment

Phase 1 重構：
  - Gate + size_tier（唔再乘法堆疊）
  - base_risk = profile.risk_per_trade_pct（由 volatility regime 決定）
  - size_tier: confidence >= 0.7 → 1.0×, >= 0.5 → 0.7×, >= 0.3 → 0.5×
  - MIN_RISK_FLOOR = 0.5%（保證最低可執行倉位）
  - Kelly: 有數據時做上限，唔做 base
  - SL/TP 計算保持不變
"""

from __future__ import annotations

from ..config.settings import (
    PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME,
    REENTRY_SIZE_REDUCTION,
    CP_ENABLED,
    RANGE_TP_MID_FRACTION,
    KELLY_NO_EDGE,
)
from .kelly import compute_kelly_base_risk
from ..config.pairs import get_pair
from ..core.context import CycleContext, Signal
from ..strategies.base import PositionParams
from ..core.registry import StrategyRegistry


import logging

log = logging.getLogger(__name__)

# ─── Size tier based on signal confidence (replaces multiplicative stacking) ───
MIN_RISK_FLOOR = 0.005  # 0.5% absolute minimum risk

# Estimated holding periods (in 8h funding intervals)
FUNDING_PERIODS_RANGE = 3    # ~24h for range trades
FUNDING_PERIODS_TREND = 6    # ~48h for trend trades


def _get_size_tier(confidence: float) -> float:
    """Map confidence to position size tier.

    設計決定：用離散 tier 取代連續乘法，防止倉位消失。
      confidence >= 0.7 → 全倉 (1.0×)
      confidence >= 0.5 → 七成倉 (0.7×)
      confidence >= 0.3 → 半倉 (0.5×)
      confidence < 0.3  → 唔應該到呢度（evaluate 已過濾）
    """
    if confidence >= 0.7:
        return 1.0
    elif confidence >= 0.5:
        return 0.7
    else:
        return 0.5


def _load_profile_risk(profile_name: str) -> float:
    """Load risk_per_trade_pct from named profile.

    Fallback to 0.02 (2%) if profile load fails.
    """
    try:
        from config.profiles.loader import load_profile
        profile = load_profile(profile_name)
        return profile.get("risk_per_trade_pct", 0.02)
    except Exception as e:
        log.warning("Failed to load profile '%s': %s, using 2%%", profile_name, e)
        return 0.02


class SizePositionStep:
    """
    Step 11: Calculate position size, SL, and TP for the selected signal.
    Updates signal in-place with sl_price, tp1_price, tp2_price.
    Rejects signal if R:R is insufficient.
    """
    name = "size_position"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.selected_signal:
            return ctx

        signal = ctx.selected_signal

        # ─── Find strategy by signal name (not market mode) ───
        strategy = None
        for s in StrategyRegistry.all_strategies().values():
            if s.name == signal.strategy:
                strategy = s
                break
        if not strategy:
            ctx.warnings.append(f"No strategy '{signal.strategy}', cannot size")
            ctx.selected_signal = None
            return ctx

        params = strategy.get_position_params()

        # Get ATR from primary timeframe
        pair_ind = ctx.indicators.get(signal.pair, {})
        ind_4h = pair_ind.get(PRIMARY_TIMEFRAME, {})
        ind_1h = pair_ind.get(SECONDARY_TIMEFRAME, {})

        atr = ind_4h.get("atr")
        if not atr or atr <= 0:
            ctx.warnings.append(f"No ATR for {signal.pair}, cannot size position")
            ctx.selected_signal = None
            return ctx

        entry_price = signal.entry_price
        if entry_price <= 0:
            ctx.warnings.append(f"Invalid entry price for {signal.pair}")
            ctx.selected_signal = None
            return ctx

        # ─── SL Calculation ───
        sl_atr_mult = params.sl_atr_mult
        try:
            pair_cfg = get_pair(signal.pair)
            if pair_cfg.sl_mult_override is not None:
                sl_atr_mult = pair_cfg.sl_mult_override
        except KeyError:
            pair_cfg = None

        # Conformal Prediction: widen SL with uncertainty estimate
        atr_for_sl = atr
        if CP_ENABLED:
            try:
                from ..strategies.mode_detector import _get_cp
                cp = _get_cp()
                atr_for_sl = cp.get_atr_high(atr)
                if ctx.verbose:
                    q_hat = atr_for_sl - atr
                    print(f"      CP: atr={atr:.2f} + q_hat={q_hat:.2f} = atr_high={atr_for_sl:.2f}")
            except Exception as e:
                log.warning("CP get_atr_high failed, using raw ATR: %s", e)

        sl_distance = atr_for_sl * sl_atr_mult

        if signal.direction == "LONG":
            sl_price = entry_price - sl_distance
        else:
            sl_price = entry_price + sl_distance

        # ─── TP Calculation ───
        tp1_price, tp2_price = self._calc_tp(
            signal, params, ind_4h, ind_1h,
            entry_price, sl_distance, ctx
        )

        # ─── R:R Validation ───
        if tp1_price and tp1_price > 0:
            reward = abs(tp1_price - entry_price)
            risk = sl_distance
            rr_ratio = reward / risk if risk > 0 else 0
            if rr_ratio < params.min_rr:
                ctx.warnings.append(
                    f"R:R rejected: {signal.pair} {signal.direction} "
                    f"R:R={rr_ratio:.1f} < min {params.min_rr}"
                )
                ctx.selected_signal = None
                return ctx

        # ─── Position Size: Gate + Size Tier (Phase 1 refactor) ───
        balance = ctx.account_balance if ctx.account_balance > 0 else 100.0

        # 1. Base risk from active profile
        base_risk = _load_profile_risk(ctx.active_risk_profile)

        # 2. Size tier from signal confidence
        size_tier = _get_size_tier(signal.confidence)

        # 3. Loss reduction (re-entry after consecutive losses)
        consecutive_losses = _parse_int(ctx.trade_state.get("CONSECUTIVE_LOSSES", 0))
        loss_mult = (1 - REENTRY_SIZE_REDUCTION) ** consecutive_losses if consecutive_losses > 0 else 1.0

        # 4. Final risk = base × size_tier × loss_reduction
        final_risk = base_risk * size_tier * loss_mult

        # 5. MIN_RISK_FLOOR: guarantee minimum executable position
        final_risk = max(final_risk, MIN_RISK_FLOOR)

        # 6. Kelly cap: if Kelly has data, use as upper limit (not base)
        kelly_risk = compute_kelly_base_risk(ctx.market_mode)
        kelly_capped = False
        if kelly_risk == KELLY_NO_EDGE:
            # No statistical edge → block signal
            ctx.warnings.append(
                f"Kelly: no statistical edge in {ctx.market_mode} regime → signal blocked"
            )
            ctx.selected_signal = None
            return ctx
        if kelly_risk is not None and final_risk > kelly_risk:
            final_risk = kelly_risk
            kelly_capped = True

        risk_amount = balance * final_risk

        # Position size = risk_amount / (sl_distance / entry_price)
        sl_pct = sl_distance / entry_price
        position_notional = risk_amount / sl_pct if sl_pct > 0 else 0
        position_size = position_notional / entry_price if entry_price > 0 else 0
        margin_required = position_notional / params.leverage if params.leverage > 0 else 0

        # ─── Update signal with calculated values ───
        prec = pair_cfg.price_precision if pair_cfg else 2
        signal.sl_price = round(sl_price, prec)
        signal.tp1_price = round(tp1_price, prec) if tp1_price else 0.0
        signal.tp2_price = round(tp2_price, prec) if tp2_price else None

        try:
            qty_prec = pair_cfg.qty_precision if pair_cfg else 3
        except (AttributeError, TypeError):
            qty_prec = 3
        signal.position_size_qty = round(position_size, qty_prec)
        signal.position_notional = round(position_notional, 2)
        signal.margin_required = round(margin_required, 2)
        signal.leverage = params.leverage

        if ctx.verbose:
            print(f"    Position Sizing: {signal.pair} {signal.direction}")
            print(f"      Profile: {ctx.active_risk_profile} → base_risk={base_risk:.2%}")
            print(f"      Confidence: {signal.confidence:.2f} → size_tier={size_tier:.1f}×")
            print(f"      Final risk: {final_risk:.2%} (floor={MIN_RISK_FLOOR:.2%})")
            if kelly_capped:
                print(f"      Kelly cap: {kelly_risk:.2%}")
            print(f"      Entry: {entry_price} | SL: {signal.sl_price} | TP1: {signal.tp1_price}")
            if signal.tp2_price:
                print(f"      TP2: {signal.tp2_price}")
            print(f"      Size: {position_size:.4f} | Notional: ${position_notional:.2f}")
            print(f"      Margin: ${margin_required:.2f} | Leverage: {params.leverage}x")
            if tp1_price:
                rr = abs(tp1_price - entry_price) / sl_distance if sl_distance > 0 else 0
                print(f"      R:R = 1:{rr:.1f} (min 1:{params.min_rr})")
            if consecutive_losses > 0:
                print(f"      Loss reduction: ×{loss_mult:.2f} ({consecutive_losses} losses)")

        return ctx

    def _calc_tp(
        self, signal: Signal, params: PositionParams,
        ind_4h: dict, ind_1h: dict, entry_price: float,
        sl_distance: float, ctx: CycleContext,
    ) -> tuple[float | None, float | None]:
        """Route to strategy-specific TP calculation."""
        if signal.strategy == "range":
            return self._calc_range_tp(signal, ind_1h, entry_price, sl_distance, ctx)
        elif signal.strategy == "trend":
            return self._calc_trend_tp(signal, ind_4h, entry_price, sl_distance, ctx)
        elif signal.strategy == "crash":
            return self._calc_crash_tp(signal, ind_4h, entry_price, sl_distance, ctx)
        elif signal.strategy == "scalp":
            # Scalp: fixed ATR multiple
            atr = ind_4h.get("atr", 0)
            tp_mult = params.tp_atr_mult or 2.5
            if signal.direction == "LONG":
                return entry_price + atr * tp_mult, None
            else:
                return entry_price - atr * tp_mult, None

        return None, None

    def _calc_range_tp(
        self, signal: Signal, ind_1h: dict,
        entry_price: float, sl_distance: float, ctx: CycleContext,
    ) -> tuple[float | None, float | None]:
        """
        Range TP (180d backtest validated):
          TP1 = entry + RANGE_TP_MID_FRACTION × (BB basis − entry)
                Default 50%→mid (was 100%→mid, 0% WR → 59% WR)
          TP2 = BB basis (full mean reversion for remaining position)
        Plus funding cost adjustment.
        """
        bb_basis = ind_1h.get("bb_basis")
        bb_upper = ind_1h.get("bb_upper")
        bb_lower = ind_1h.get("bb_lower")

        if not bb_basis:
            # Fallback: min R:R × SL distance
            if signal.direction == "LONG":
                return entry_price + sl_distance * 2.3, None
            else:
                return entry_price - sl_distance * 2.3, None

        frac = RANGE_TP_MID_FRACTION
        if signal.direction == "LONG":
            tp1 = entry_price + (bb_basis - entry_price) * frac
            tp2 = bb_basis  # full mid for remaining position
        else:
            tp1 = entry_price - (entry_price - bb_basis) * frac
            tp2 = bb_basis  # full mid for remaining position

        # ─── Funding cost adjustment ───
        tp1 = self._adjust_tp_for_funding(
            signal.pair, signal.direction, signal.strategy,
            entry_price, tp1, ctx
        )

        return tp1, tp2

    def _calc_trend_tp(
        self, signal: Signal, ind_4h: dict,
        entry_price: float, sl_distance: float, ctx: CycleContext,
    ) -> tuple[float | None, float | None]:
        """
        Trend TP (from STRATEGY.md):
          TP = Next major S/R level (from SCAN_CONFIG)
          Must satisfy min R:R 1:3
          Fallback: 3× SL distance from entry
        """
        prefix = signal.pair.replace("USDT", "")

        if signal.direction == "LONG":
            resistance = _parse_config_float(ctx.scan_config.get(f"{prefix}_resistance"))
            if resistance and resistance > entry_price:
                tp1 = resistance
            else:
                tp1 = entry_price + sl_distance * 3.0
        else:
            support = _parse_config_float(ctx.scan_config.get(f"{prefix}_support"))
            if support and support < entry_price:
                tp1 = support
            else:
                tp1 = entry_price - sl_distance * 3.0

        # ─── Funding cost adjustment ───
        tp1 = self._adjust_tp_for_funding(
            signal.pair, signal.direction, signal.strategy,
            entry_price, tp1, ctx
        )

        return tp1, None

    def _calc_crash_tp(
        self, signal: Signal, ind_4h: dict,
        entry_price: float, sl_distance: float, ctx: CycleContext,
    ) -> tuple[float | None, float | None]:
        """Crash TP: ATR × 3.5 from entry (R:R = 3.5/2.0 = 1.75 > min 1.5)."""
        atr = ind_4h.get("atr", 0)
        tp_dist = atr * 3.5 if atr > 0 else sl_distance * 1.75
        # SHORT only in crash
        tp1 = entry_price - tp_dist

        tp1 = self._adjust_tp_for_funding(
            signal.pair, signal.direction, signal.strategy,
            entry_price, tp1, ctx
        )
        return tp1, None

    def _adjust_tp_for_funding(
        self, pair: str, direction: str, strategy: str,
        entry_price: float, tp_price: float | None,
        ctx: CycleContext,
    ) -> float | None:
        """
        Adjust TP to account for funding cost during hold period.

        If funding works AGAINST our direction:
          - LONG with positive funding → we PAY funding → need more profit
          - SHORT with negative funding → we PAY funding → need more profit

        Impact is particularly significant for XAG (+0.214%/8h = $0.64/8h).
        We shift TP further from entry to compensate for estimated funding cost.

        Estimated hold times:
          Range: ~24h = 3 funding periods (every 8h)
          Trend: ~48h = 6 funding periods
        """
        if tp_price is None:
            return None

        snap = ctx.market_data.get(pair)
        if not snap:
            return tp_price

        funding_rate = snap.funding_rate

        # Check if funding works against our direction
        funding_adverse = (
            (direction == "LONG" and funding_rate > 0) or
            (direction == "SHORT" and funding_rate < 0)
        )

        if not funding_adverse:
            return tp_price  # Funding is in our favor or zero, no adjustment

        # Estimate total funding cost over hold period
        # Crash holds are shorter (~12h) than range (~24h)
        FUNDING_PERIODS_CRASH = 2   # ~12h for crash trades (quick exit)
        estimated_periods = (
            FUNDING_PERIODS_RANGE if strategy == "range"
            else FUNDING_PERIODS_CRASH if strategy == "crash"
            else FUNDING_PERIODS_TREND
        )
        total_funding_pct = abs(funding_rate) * estimated_periods

        # Shift TP FURTHER from entry to cover funding cost
        funding_impact = entry_price * total_funding_pct

        if direction == "LONG":
            tp_price = tp_price + funding_impact  # Higher TP for more profit
        else:
            tp_price = tp_price - funding_impact  # Lower TP for more profit

        return tp_price


# ─── Helpers ───

def _parse_config_float(val) -> float | None:
    """Parse float from scan config value."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_int(val, default: int = 0) -> int:
    """Safely parse int."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
