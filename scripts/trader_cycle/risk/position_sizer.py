"""
position_sizer.py — Position sizing, SL/TP calculation, funding cost adjustment

2-Zone refactor (2026-03-23):
  - Zone A (1-10x) / Zone B (11-20x) — 由 regime_risk.py 決定
  - Margin per trade: 3% of account (from profile.margin_pct)
  - SL: profile.sl_pct_base × pair.vol_mult（per-coin adaptive SL）
  - TP: profile.tp_pct（1.5% target）
  - Leverage: from profile (range_leverage / trend_leverage), capped by pair.max_leverage
  - Legacy ATR-based SL path 保留為 fallback（profile 冇 sl_pct_base 時用）
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


def _load_zone_profile(profile_name: str) -> dict:
    """Load full zone profile dict.

    Fallback to DEFAULT_PROFILE if load fails.
    """
    try:
        from config.profiles.loader import load_profile
        return load_profile(profile_name)
    except Exception as e:
        log.warning("Failed to load profile '%s': %s, using defaults", profile_name, e)
        from config.profiles._base import DEFAULT_PROFILE
        return dict(DEFAULT_PROFILE)


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
        # ATR only required for legacy path; percentage SL path works without it
        zone_profile = _load_zone_profile(ctx.active_risk_profile)

        # ─── Signal-level zone override (Step 11) ───
        # Signal confidence can upgrade A→B or downgrade B→A independent of
        # regime_confidence (HMM posterior). Thresholds mirror ZONE_B_THRESHOLD
        # in regime_risk.py (0.70). ctx.active_risk_profile is updated so that
        # all downstream logging reflects the actual zone used.
        _SIGNAL_UPGRADE_THRESHOLD = 0.70
        _SIGNAL_DOWNGRADE_THRESHOLD = 0.50
        _sig_conf = signal.confidence
        if _sig_conf >= _SIGNAL_UPGRADE_THRESHOLD and ctx.active_risk_profile == "zone_a":
            log.info(
                "Signal zone override: A→B (signal.confidence=%.2f >= %.2f)",
                _sig_conf, _SIGNAL_UPGRADE_THRESHOLD,
            )
            ctx.active_risk_profile = "zone_b"
            zone_profile = _load_zone_profile("zone_b")
        elif _sig_conf < _SIGNAL_DOWNGRADE_THRESHOLD and ctx.active_risk_profile == "zone_b":
            log.info(
                "Signal zone override: B→A (signal.confidence=%.2f < %.2f)",
                _sig_conf, _SIGNAL_DOWNGRADE_THRESHOLD,
            )
            ctx.active_risk_profile = "zone_a"
            zone_profile = _load_zone_profile("zone_a")

        has_pct_sl = zone_profile.get("sl_pct_base") and zone_profile["sl_pct_base"] > 0
        if (not atr or atr <= 0) and not has_pct_sl:
            ctx.warnings.append(f"No ATR for {signal.pair}, cannot size position")
            ctx.selected_signal = None
            return ctx

        entry_price = signal.entry_price
        if entry_price <= 0:
            ctx.warnings.append(f"Invalid entry price for {signal.pair}")
            ctx.selected_signal = None
            return ctx

        # ─── SL Calculation (2-Zone: percentage-based, per-coin scaled) ───
        try:
            pair_cfg = get_pair(signal.pair)
        except KeyError:
            pair_cfg = None

        # zone_profile already loaded above (ATR guard)
        sl_pct_base = zone_profile.get("sl_pct_base")

        if sl_pct_base and sl_pct_base > 0:
            # 2-Zone path: SL = sl_pct_base × vol_mult
            vol_mult = pair_cfg.vol_mult if pair_cfg else 1.0
            sl_pct = sl_pct_base * vol_mult
            sl_distance = entry_price * sl_pct
        else:
            # Legacy ATR-based fallback
            sl_atr_mult = params.sl_atr_mult
            if pair_cfg and pair_cfg.sl_mult_override is not None:
                sl_atr_mult = pair_cfg.sl_mult_override

            atr_for_sl = atr
            if CP_ENABLED:
                try:
                    from ..strategies.mode_detector import _get_cp
                    cp = _get_cp()
                    atr_for_sl = cp.get_atr_high(atr)
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
            entry_price, sl_distance, ctx, zone_profile
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

        # ─── Position Size: 2-Zone Margin-Based (2026-03-23) ───
        balance = ctx.account_balance if ctx.account_balance > 0 else 100.0

        margin_pct = zone_profile.get("margin_pct", 0.03)  # 3% of account as margin

        # Leverage: from zone profile (not settings.py which is frozen at import time)
        strategy_type = signal.strategy  # "range" / "trend" / "crash" / "squeeze"
        if strategy_type == "range":
            leverage = zone_profile.get("range_leverage", params.leverage)
        elif strategy_type == "trend":
            leverage = zone_profile.get("trend_leverage", params.leverage)
        elif strategy_type == "squeeze":
            # Squeeze uses range leverage (breakout from compression = range-like entry)
            leverage = zone_profile.get("range_leverage", params.leverage)
        else:
            leverage = params.leverage  # crash uses strategy default
        # Cap by per-coin max leverage
        if pair_cfg and pair_cfg.max_leverage:
            leverage = min(leverage, pair_cfg.max_leverage)

        # Loss reduction (re-entry after consecutive losses — retained from Phase 1)
        consecutive_losses = _parse_int(ctx.trade_state.get("CONSECUTIVE_LOSSES", 0))
        loss_mult = (1 - REENTRY_SIZE_REDUCTION) ** consecutive_losses if consecutive_losses > 0 else 1.0

        # Margin = balance × margin_pct × loss_mult
        margin_required = balance * margin_pct * loss_mult

        # Position notional = margin × leverage
        position_notional = margin_required * leverage
        position_size = position_notional / entry_price if entry_price > 0 else 0

        # Account risk per trade = SL% × leverage × margin_pct
        # (for logging / circuit breaker reference)
        sl_pct_actual = sl_distance / entry_price if entry_price > 0 else 0
        account_risk_pct = sl_pct_actual * leverage * margin_pct

        # Kelly cap: if Kelly has data, scale down margin if over-betting
        kelly_risk = compute_kelly_base_risk(ctx.market_mode)
        kelly_capped = False
        if kelly_risk == KELLY_NO_EDGE:
            ctx.warnings.append(
                f"Kelly: no statistical edge in {ctx.market_mode} regime → signal blocked"
            )
            ctx.selected_signal = None
            return ctx
        if kelly_risk is not None and account_risk_pct > kelly_risk:
            # Scale down margin to respect Kelly
            scale = kelly_risk / account_risk_pct
            margin_required *= scale
            position_notional = margin_required * leverage
            position_size = position_notional / entry_price if entry_price > 0 else 0
            kelly_capped = True

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
        signal.leverage = leverage

        if ctx.verbose:
            vol_mult = pair_cfg.vol_mult if pair_cfg else 1.0
            print(f"    Position Sizing: {signal.pair} {signal.direction}")
            print(f"      Zone: {ctx.active_risk_profile.upper()} | Margin: {margin_pct:.0%} of ${balance:.0f}")
            print(f"      SL: {sl_pct_actual:.3%} (base {zone_profile.get('sl_pct_base', 'ATR')} × vol_mult {vol_mult})")
            print(f"      Account risk: {account_risk_pct:.3%} per trade")
            if kelly_capped:
                print(f"      Kelly cap applied: {kelly_risk:.2%}")
            print(f"      Entry: {entry_price} | SL: {signal.sl_price} | TP1: {signal.tp1_price}")
            if signal.tp2_price:
                print(f"      TP2: {signal.tp2_price}")
            print(f"      Size: {position_size:.4f} | Notional: ${position_notional:.2f}")
            print(f"      Margin: ${margin_required:.2f} | Leverage: {leverage}x (max {pair_cfg.max_leverage if pair_cfg else 'N/A'}x)")
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
        zone_profile: dict | None = None,
    ) -> tuple[float | None, float | None]:
        """Route to strategy-specific TP calculation.

        After computing strategy-specific TP, applies a TP floor derived from
        zone_profile["tp_pct_base"] × pair.vol_mult. If the strategy TP is
        worse than the floor (too close to entry), the floor is used instead.
        Skipped when zone_profile is absent or has no tp_pct_base (legacy fallback).
        """
        # Strategy-specific TP first
        if signal.strategy == "range":
            tp1, tp2 = self._calc_range_tp(signal, ind_1h, entry_price, sl_distance, ctx)
        elif signal.strategy == "trend":
            tp1, tp2 = self._calc_trend_tp(signal, ind_4h, entry_price, sl_distance, ctx)
        elif signal.strategy == "crash":
            tp1, tp2 = self._calc_crash_tp(signal, ind_4h, entry_price, sl_distance, ctx)
        elif signal.strategy in ("scalp", "squeeze"):
            # Scalp / Squeeze: fixed ATR multiple for TP
            atr = ind_1h.get("atr") or ind_4h.get("atr", 0)
            tp_mult = params.tp_atr_mult or 3.0
            if atr and atr > 0:
                if signal.direction == "LONG":
                    tp1, tp2 = entry_price + atr * tp_mult, None
                else:
                    tp1, tp2 = entry_price - atr * tp_mult, None
            else:
                tp1, tp2 = None, None
        else:
            return None, None

        # ─── TP floor from zone profile (tp_pct_base × vol_mult) ───
        tp_pct_base = (zone_profile or {}).get("tp_pct_base") if zone_profile else None
        if tp_pct_base and tp_pct_base > 0 and tp1 is not None:
            try:
                pair_cfg = get_pair(signal.pair)
                vol_mult = pair_cfg.vol_mult if pair_cfg else 1.0
            except KeyError:
                vol_mult = 1.0

            if signal.direction == "LONG":
                tp_floor = entry_price * (1 + tp_pct_base * vol_mult)
                if tp_floor > tp1:
                    log.debug(
                        "TP floor applied: %s LONG strategy_tp=%.4f < floor=%.4f",
                        signal.pair, tp1, tp_floor,
                    )
                    tp1 = tp_floor
            else:  # SHORT
                tp_floor = entry_price * (1 - tp_pct_base * vol_mult)
                if tp_floor < tp1:
                    log.debug(
                        "TP floor applied: %s SHORT strategy_tp=%.4f > floor=%.4f",
                        signal.pair, tp1, tp_floor,
                    )
                    tp1 = tp_floor

        return tp1, tp2

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
