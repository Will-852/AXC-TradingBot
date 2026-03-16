"""
risk_manager.py — Non-negotiable risk rules
Circuit breakers, cooldowns, position limits, no-trade conditions.

All rules are hard-coded from settings.py.
No LLM discretion — pure if-else enforcement.
"""

from __future__ import annotations
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_base = os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
if _base not in sys.path:
    sys.path.insert(0, _base)

from memory.writer import write_trade

from ..config.settings import (
    CIRCUIT_BREAKER_SINGLE, CIRCUIT_BREAKER_DAILY,
    COOLDOWN_2_LOSSES_MIN, COOLDOWN_3_LOSSES_MIN,
    MAX_HOLD_HOURS, FUNDING_COST_FORCE_RATIO,
    NO_TRADE_VOLUME_MIN, NO_TRADE_FUNDING_EXTREME,
    MAX_CRYPTO_POSITIONS, MAX_XAG_POSITIONS,
    POSITION_GROUPS, HKT,
    MAX_MARGIN_PCT, MARGIN_WARNING_PCT,
)
from ..config.pairs import get_pair
from ..core.context import CycleContext, ClosedPosition


class SafetyCheckStep:
    """
    Step 2: Circuit breakers + cooldown checks.
    Runs BEFORE market data fetch — blocks entire cycle if triggered.
    Sets ctx.risk_blocked = True to prevent any new trades.
    """
    name = "safety_check"

    def run(self, ctx: CycleContext) -> CycleContext:
        ts = ctx.trade_state

        # ─── Circuit Breaker: Daily Loss ───
        # DAILY_LOSS in state file is like "$12.50" — parse the number
        daily_loss_raw = str(ts.get("DAILY_LOSS", "0"))
        daily_loss_val = _parse_float(daily_loss_raw.replace("$", "").replace(",", ""), 0)
        # Convert absolute loss to percentage using balance
        balance = _parse_float(ts.get("BALANCE_USDT", 0)) or _parse_float(ts.get("ACCOUNT_BALANCE", 0))
        daily_pnl_pct = -(daily_loss_val / balance * 100) if balance > 0 and daily_loss_val > 0 else 0
        if daily_pnl_pct < -(CIRCUIT_BREAKER_DAILY * 100):
            ctx.risk_blocked = True
            ctx.risk_reasons.append(
                f"CIRCUIT_BREAKER_DAILY: {daily_pnl_pct:.1f}% "
                f"exceeds -{CIRCUIT_BREAKER_DAILY*100:.0f}% limit"
            )

        # ─── Cooldown Check ───
        consecutive_losses = _parse_int(ts.get("CONSECUTIVE_LOSSES", 0))
        cooldown_until = ts.get("COOLDOWN_UNTIL")

        if cooldown_until and str(cooldown_until).strip() not in ("", "—", "NONE", "N/A"):
            try:
                cooldown_dt = datetime.strptime(str(cooldown_until), "%Y-%m-%d %H:%M")
                cooldown_dt = cooldown_dt.replace(tzinfo=HKT)
                if ctx.timestamp and ctx.timestamp < cooldown_dt:
                    ctx.cooldown_active = True
                    ctx.cooldown_ends = cooldown_dt
                    ctx.risk_blocked = True
                    remaining = (cooldown_dt - ctx.timestamp).total_seconds() / 60
                    ctx.risk_reasons.append(
                        f"COOLDOWN: {consecutive_losses} consecutive losses, "
                        f"{remaining:.0f}min remaining until {cooldown_until}"
                    )
            except (ValueError, TypeError):
                pass

        # ─── Set new cooldown if losses accumulated ───
        if not ctx.cooldown_active:
            if consecutive_losses >= 3:
                self._set_cooldown(ctx, COOLDOWN_3_LOSSES_MIN, consecutive_losses)
            elif consecutive_losses >= 2:
                self._set_cooldown(ctx, COOLDOWN_2_LOSSES_MIN, consecutive_losses)

        if ctx.verbose:
            if ctx.risk_blocked:
                for r in ctx.risk_reasons:
                    print(f"    RISK: {r}")
            else:
                print(f"    Safety check: OK (losses={consecutive_losses})")

        return ctx

    def _set_cooldown(self, ctx: CycleContext, minutes: int, losses: int) -> None:
        """Set cooldown period in trade state."""
        if ctx.timestamp:
            cooldown_end = ctx.timestamp + timedelta(minutes=minutes)
            ctx.trade_state_updates["COOLDOWN_UNTIL"] = cooldown_end.strftime("%Y-%m-%d %H:%M")
            ctx.cooldown_active = True
            ctx.risk_blocked = True
            ctx.risk_reasons.append(
                f"COOLDOWN_SET: {minutes}min for {losses} consecutive losses"
            )


class NoTradeCheckStep:
    """
    Step 3b: No-trade conditions that need market data.
    Runs AFTER FetchMarketDataStep + CalcIndicatorsStep.
    Marks individual pairs as no-trade (doesn't block entire cycle).
    """
    name = "no_trade_check"

    def run(self, ctx: CycleContext) -> CycleContext:
        no_trade_pairs: set[str] = set()

        for symbol, snap in ctx.market_data.items():
            reasons = []

            # ─── Volume too low ───
            if symbol in ctx.indicators:
                for tf in ctx.indicators[symbol]:
                    vol_ratio = ctx.indicators[symbol][tf].get("volume_ratio")
                    if vol_ratio is not None and vol_ratio < NO_TRADE_VOLUME_MIN:
                        reasons.append(
                            f"LOW_VOLUME: {symbol} {tf} "
                            f"volume={vol_ratio:.0%} of avg (min {NO_TRADE_VOLUME_MIN:.0%})"
                        )

            # ─── Extreme funding ───
            if abs(snap.funding_rate) > NO_TRADE_FUNDING_EXTREME:
                reasons.append(
                    f"EXTREME_FUNDING: {symbol} "
                    f"funding={snap.funding_rate:.4%} (limit +-{NO_TRADE_FUNDING_EXTREME:.2%})"
                )

            if reasons:
                no_trade_pairs.add(symbol)
                ctx.no_trade_reasons.extend(reasons)

        # ─── Position group limits ───
        self._check_position_limits(ctx, no_trade_pairs)

        if ctx.verbose:
            if ctx.no_trade_reasons:
                for r in ctx.no_trade_reasons:
                    print(f"    NO_TRADE: {r}")
            else:
                print("    No-trade check: all pairs clear")

        return ctx

    def _check_position_limits(self, ctx: CycleContext, no_trade_pairs: set[str]) -> None:
        """Check if position group limits are exceeded."""
        # Count existing positions per group
        group_counts: dict[str, int] = {}
        for pos in ctx.open_positions:
            try:
                pair_cfg = get_pair(pos.pair)
                group = pair_cfg.group
                group_counts[group] = group_counts.get(group, 0) + 1
            except KeyError:
                pass

        # Check each group
        for group_name, symbols in POSITION_GROUPS.items():
            count = group_counts.get(group_name, 0)
            max_allowed = 1  # Each group: max 1 position
            if count >= max_allowed:
                # Mark all symbols in this group as no-trade
                for sym in symbols:
                    no_trade_pairs.add(sym)
                ctx.no_trade_reasons.append(
                    f"GROUP_FULL: {group_name} has {count}/{max_allowed} "
                    f"positions ({', '.join(symbols)})"
                )


class ManagePositionsStep:
    """
    Step 8: Manage existing positions.
    Checks: circuit breaker, max hold time, funding cost ratio.
    DRY_RUN: report what would happen, don't close.
    LIVE: actually submit close orders via exchange client.
    """
    name = "manage_positions"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.open_positions:
            if ctx.verbose:
                print("    No open positions to manage")
            return ctx

        for pos in ctx.open_positions:
            exit_reasons = []

            # ─── Circuit Breaker: Single Position Loss ───
            if pos.entry_price > 0 and pos.mark_price > 0:
                if pos.direction == "LONG":
                    pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price
                else:
                    pnl_pct = (pos.entry_price - pos.mark_price) / pos.entry_price

                if pnl_pct < -CIRCUIT_BREAKER_SINGLE:
                    exit_reasons.append(
                        f"CIRCUIT_BREAKER: {pnl_pct:.1%} loss > "
                        f"-{CIRCUIT_BREAKER_SINGLE:.0%} limit"
                    )

            # ─── Max Hold Time ───
            if pos.entry_time and ctx.timestamp:
                hold_hours = (ctx.timestamp - pos.entry_time).total_seconds() / 3600
                if hold_hours > MAX_HOLD_HOURS:
                    exit_reasons.append(
                        f"MAX_HOLD: {hold_hours:.0f}h > {MAX_HOLD_HOURS}h limit"
                    )

            # ─── Funding Cost vs Unrealized PnL ───
            if pos.unrealized_pnl != 0 and pos.funding_cost != 0:
                funding_ratio = abs(pos.funding_cost) / max(abs(pos.unrealized_pnl), 0.01)
                if funding_ratio > FUNDING_COST_FORCE_RATIO:
                    exit_reasons.append(
                        f"FUNDING_EATING_PROFIT: funding ${pos.funding_cost:.2f} is "
                        f"{funding_ratio:.0%} of PnL ${pos.unrealized_pnl:.2f}"
                    )

            # ─── TP Health Check: price passed TP but position still open ───
            tp_price = _parse_float(ctx.trade_state.get("TP_PRICE", 0))
            if tp_price > 0 and pos.mark_price > 0:
                tp_passed = False
                if pos.direction == "LONG" and pos.mark_price >= tp_price:
                    tp_passed = True
                elif pos.direction == "SHORT" and pos.mark_price <= tp_price:
                    tp_passed = True
                if tp_passed:
                    exit_reasons.append(
                        f"TP_MISSED: price {pos.mark_price} passed "
                        f"TP {tp_price} but position still open"
                    )
                    logger.warning(
                        f"[{pos.pair}] TP health check: mark={pos.mark_price} "
                        f"passed TP={tp_price}, forcing close"
                    )

            # ─── Margin Health Alert (Sprint 2B — alert only, no auto-close) ───
            self._check_margin_health(pos, ctx)

            # ─── Report or execute ───
            if exit_reasons:
                if ctx.dry_run or (not ctx.exchange_client and not ctx.exchange_clients):
                    # DRY_RUN: report only
                    for r in exit_reasons:
                        ctx.warnings.append(f"[DRY_RUN] Would close {pos.pair} {pos.direction}: {r}")
                else:
                    # LIVE: actually close position + cancel SL/TP orders
                    for r in exit_reasons:
                        ctx.warnings.append(f"EXIT {pos.pair} {pos.direction}: {r}")
                    self._execute_close(pos, ctx, exit_reasons)

            if ctx.verbose and exit_reasons:
                prefix = "[DRY_RUN]" if ctx.dry_run else "[LIVE]"
                for r in exit_reasons:
                    print(f"    {prefix} EXIT {pos.pair}: {r}")

        # ─── Aggregate Margin Monitoring (post-trade, alert-only) ───
        self._check_aggregate_margin(ctx)

        return ctx

    def _execute_close(self, pos, ctx: CycleContext,
                       exit_reasons: list[str] | None = None) -> None:
        """Close position on exchange and cancel associated orders.
        Routes to correct exchange based on pos.platform.
        """
        client = ctx.exchange_clients.get(pos.platform, ctx.exchange_client)

        # Cancel existing SL/TP orders first
        try:
            open_orders = client.get_open_orders(pos.pair)
            for order in open_orders:
                oid = order.get("orderId")
                if oid:
                    try:
                        client.cancel_order(pos.pair, str(oid))
                    except Exception:
                        pass  # Best effort
        except Exception as e:
            ctx.warnings.append(f"Failed to cancel orders for {pos.pair}: {e}")

        # Market close
        close_intent_id = ""
        if ctx.wal:
            close_intent_id = ctx.wal.log_intent(
                "close", pos.pair, pos.direction, pos.size,
                pos.mark_price, pos.sl_price, pos.platform,
            )
        try:
            result = client.close_position_market(pos.pair)
            if ctx.wal and close_intent_id:
                ctx.wal.log_done(close_intent_id)
            reason_str = "; ".join(exit_reasons) if exit_reasons else "risk"
            ctx.warnings.append(f"Position closed: {pos.pair} {pos.direction}")
            ctx.trade_log_entries.append(
                f"[{ctx.timestamp_str}] EXIT {pos.direction} {pos.pair} "
                f"size={pos.size} entry={pos.entry_price} "
                f"mark={pos.mark_price} pnl={pos.unrealized_pnl:.2f} "
                f"reason={reason_str}"
            )

            # Persist to trades.jsonl
            try:
                write_trade(pos.pair, pos.direction, pos.entry_price,
                            exit_price=pos.mark_price,
                            pnl=pos.unrealized_pnl,
                            strategy=ctx.market_mode.lower(),
                            notes=f"risk close: {reason_str}")
            except Exception as e:
                logger.warning(f"write_trade for risk close failed: {e}")

            ctx.closed_positions.append(ClosedPosition(
                pair=pos.pair, direction=pos.direction,
                entry_price=pos.entry_price, exit_price=pos.mark_price,
                size=pos.size, pnl=pos.unrealized_pnl,
                reason=reason_str, timestamp=ctx.timestamp_str,
            ))

            # Clear trade state (keys must match TRADE_STATE.md format)
            ctx.trade_state_updates.update({
                "POSITION_OPEN": "NO",
                "PAIR": "—",
                "DIRECTION": "—",
                "ENTRY_PRICE": "0",
                "SIZE": "0",
                "SL_PRICE": "0",
                "TP_PRICE": "0",
                "TP2_PRICE": "0",
            })

        except Exception as e:
            if ctx.wal and close_intent_id:
                ctx.wal.log_failed(close_intent_id, str(e))
            ctx.errors.append(f"FAILED to close {pos.pair}: {e}")

    def _check_margin_health(self, pos, ctx: CycleContext) -> None:
        """Sprint 2B Phase A: alert-only margin monitoring.

        No auto-close — just Telegram alerts when margin is unhealthy.
        Phase B (auto-close) deferred for threshold verification.
        """
        # Margin ratio: margin_balance / maint_margin — safe when > 1
        if pos.maint_margin > 0 and pos.isolated_wallet > 0:
            ratio = pos.isolated_wallet / pos.maint_margin
            pos.margin_ratio = ratio  # store for diagnostics
            if ratio < 1.5:
                alert = (
                    f"<b>Margin Warning</b> [{pos.pair}]\n"
                    f"Margin ratio: {ratio:.2f} (threshold: 1.5)\n"
                    f"Wallet: ${pos.isolated_wallet:.2f} | Maint: ${pos.maint_margin:.2f}"
                )
                ctx.telegram_messages.append(alert)
                logger.warning(f"[{pos.pair}] Low margin ratio: {ratio:.2f}")

        # Distance to liquidation
        dist_pct = pos.distance_to_liquidation_pct
        if pos.liquidation_price > 0 and dist_pct < 2.0:
            alert = (
                f"<b>LIQUIDATION RISK</b> [{pos.pair}]\n"
                f"Mark: {pos.mark_price} | Liq: {pos.liquidation_price}\n"
                f"Distance: {dist_pct:.1f}% (critical < 2%)"
            )
            ctx.telegram_messages.append(alert)
            logger.error(f"[{pos.pair}] Near liquidation: {dist_pct:.1f}%")

    def _check_aggregate_margin(self, ctx: CycleContext) -> None:
        """Post-trade aggregate margin monitoring (alert-only, no auto-close).

        Why separate from MarginUtilizationValidator: validator blocks new entries,
        this monitors after position changes within the cycle (e.g. after partial close).
        """
        if ctx.account_balance <= 0 or not ctx.open_positions:
            return

        total_margin = sum(
            pos.isolated_wallet for pos in ctx.open_positions
            if pos.isolated_wallet > 0
        )
        utilization = total_margin / ctx.account_balance

        if utilization > MAX_MARGIN_PCT:
            alert = (
                f"<b>Margin Utilization Alert</b>\n"
                f"Usage: {utilization:.0%} (limit {MAX_MARGIN_PCT:.0%})\n"
                f"Total margin: ${total_margin:.2f} / Balance: ${ctx.account_balance:.2f}"
            )
            ctx.telegram_messages.append(alert)
            logger.warning(f"Aggregate margin {utilization:.0%} exceeds {MAX_MARGIN_PCT:.0%}")
        elif utilization > MARGIN_WARNING_PCT:
            logger.info(f"Aggregate margin {utilization:.0%} approaching limit")
            ctx.warnings.append(
                f"Margin utilization {utilization:.0%} approaching {MAX_MARGIN_PCT:.0%} limit"
            )


# ─── Helpers ───

def _parse_float(val, default: float = 0.0) -> float:
    """Safely parse a float from trade state values."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_int(val, default: int = 0) -> int:
    """Safely parse an int from trade state values."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
