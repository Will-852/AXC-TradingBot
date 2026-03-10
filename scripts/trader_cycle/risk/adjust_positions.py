"""
adjust_positions.py — Step 8.5: AdjustPositionsStep
Trailing SL, TP Extension, Early Exit, Re-Entry Eligibility.

All pure math — zero LLM token consumption.
Each operation is independent try/except — one failure won't block others.

Operation order matters:
  1. Trailing SL  (modify SL order)
  2. TP Extension (modify TP order)
  3. Early Exit   (close position + set re-entry)
If Early Exit runs first, subsequent ops would fail (position gone).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_base = os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
if _base not in sys.path:
    sys.path.insert(0, _base)

from memory.writer import write_trade

from ..config.settings import (
    PRIMARY_TIMEFRAME,
    TRAILING_SL_BREAKEVEN_ATR,
    TRAILING_SL_LOCK_PROFIT_ATR,
    EARLY_EXIT_RSI_OVERBOUGHT,
    EARLY_EXIT_RSI_OVERSOLD,
    EARLY_EXIT_VOLUME_SPIKE,
    EARLY_EXIT_MIN_ADVERSE_PCT,
    TP_EXTEND_ADX_MIN,
    TP_EXTEND_RSI_LONG_MAX,
    TP_EXTEND_RSI_SHORT_MIN,
    TP_EXTEND_ATR_MULT,
    TP_PROXIMITY_PCT,
    REENTRY_COOLDOWN_CYCLES,
)
from ..core.context import CycleContext, ClosedPosition


class AdjustPositionsStep:
    """
    Step 8.5: Adjust open positions — trailing SL, TP extension, early exit.
    Runs after ManagePositionsStep (hard exits) and before EvaluateSignalsStep.
    """
    name = "adjust_positions"

    def run(self, ctx: CycleContext) -> CycleContext:
        # ─── Load re-entry state from TRADE_STATE (cross-cycle persistence) ───
        self._load_reentry_state(ctx)

        if not ctx.open_positions:
            if ctx.verbose:
                print("    AdjustPositions: no open positions")
            return ctx

        for pos in ctx.open_positions:
            # Need ATR from indicators for this pair
            atr = self._get_atr(pos.pair, ctx)
            if atr is None or atr <= 0:
                if ctx.verbose:
                    print(f"    AdjustPositions [{pos.pair}]: skipped (no ATR)")
                continue

            indicators = self._get_indicators(pos.pair, ctx)

            # Operation 1: Trailing SL
            try:
                self._trailing_sl(pos, atr, ctx)
            except Exception as e:
                ctx.warnings.append(f"TrailingSL error [{pos.pair}]: {e}")
                logger.error(f"TrailingSL [{pos.pair}]: {e}")

            # Operation 2: TP Extension
            try:
                self._tp_extension(pos, atr, indicators, ctx)
            except Exception as e:
                ctx.warnings.append(f"TPExtension error [{pos.pair}]: {e}")
                logger.error(f"TPExtension [{pos.pair}]: {e}")

            # Operation 3: Early Exit (may close position)
            try:
                self._early_exit(pos, atr, indicators, ctx)
            except Exception as e:
                ctx.warnings.append(f"EarlyExit error [{pos.pair}]: {e}")
                logger.error(f"EarlyExit [{pos.pair}]: {e}")

        return ctx

    # ──────────────────────────────────────────────
    # Operation 1: Trailing Stop Loss
    # ──────────────────────────────────────────────

    def _trailing_sl(self, pos, atr: float, ctx: CycleContext) -> None:
        """
        Move SL towards profit direction only:
        - profit > 1×ATR → SL to break-even (entry price)
        - profit > 2×ATR → SL to entry + 1×ATR
        Safety: SL only moves in favourable direction, never pulled away.
        """
        if pos.entry_price <= 0 or pos.mark_price <= 0:
            return

        # Calculate profit in price terms
        if pos.direction == "LONG":
            profit = pos.mark_price - pos.entry_price
        else:
            profit = pos.entry_price - pos.mark_price

        if profit <= 0:
            return  # Not in profit, no trailing

        # Determine new SL level
        new_sl = None
        move_label = ""

        if profit >= TRAILING_SL_LOCK_PROFIT_ATR * atr:
            # Lock profit: SL at entry + 1×ATR
            if pos.direction == "LONG":
                new_sl = pos.entry_price + atr
            else:
                new_sl = pos.entry_price - atr
            move_label = "lock_profit"
        elif profit >= TRAILING_SL_BREAKEVEN_ATR * atr:
            # Break-even: SL at entry price
            new_sl = pos.entry_price
            move_label = "breakeven"

        if new_sl is None:
            return

        # Safety: SL must only move in favourable direction
        current_sl = pos.sl_price
        if current_sl > 0:
            if pos.direction == "LONG" and new_sl <= current_sl:
                return  # Would move SL further from price — skip
            if pos.direction == "SHORT" and new_sl >= current_sl:
                return  # Would move SL further from price — skip

        if ctx.verbose:
            print(
                f"    TrailingSL [{pos.pair}]: {move_label} "
                f"SL {current_sl:.4f} → {new_sl:.4f} "
                f"(profit={profit:.4f}, ATR={atr:.4f})"
            )

        # DRY_RUN: log only
        if ctx.dry_run or not ctx.exchange_clients:
            ctx.warnings.append(
                f"[DRY_RUN] TrailingSL [{pos.pair}]: "
                f"would move SL {current_sl} → {new_sl} ({move_label})"
            )
            return

        # LIVE: cancel old SL → place new SL
        client = ctx.exchange_clients.get(pos.platform, ctx.exchange_client)
        old_sl_orders = self._find_orders_by_type(client, pos.pair, "STOP_MARKET")

        for order in old_sl_orders:
            oid = str(order.get("orderId", ""))
            try:
                client.cancel_order(pos.pair, oid)
                logger.info(f"[{pos.pair}] Cancelled old SL order {oid}")
            except Exception as e:
                logger.warning(f"[{pos.pair}] Cancel old SL {oid} failed: {e}")

        # Place new SL
        exit_side = "SELL" if pos.direction == "LONG" else "BUY"
        try:
            result = client.create_stop_market(
                pos.pair, exit_side, abs(pos.size), new_sl, reduce_only=True,
            )
            new_oid = str(result.get("orderId", ""))
            logger.info(
                f"[{pos.pair}] New trailing SL placed: {new_sl} ({move_label}) id={new_oid}"
            )
            ctx.trade_state_updates["TRAILING_SL_ACTIVE"] = "YES"
            ctx.trade_state_updates["TRAILING_SL_LAST_MOVE"] = (
                f"{move_label} {new_sl:.4f}"
            )
            ctx.trade_state_updates["SL_PRICE"] = str(new_sl)
            ctx.telegram_messages.append(
                f"<b>Trailing SL</b> [{pos.pair}]\n"
                f"SL moved: {current_sl:.4f} → {new_sl:.4f} ({move_label})"
            )
        except Exception as e:
            # Failed to place new SL — try to restore old one
            logger.error(f"[{pos.pair}] New SL failed: {e} — restoring old SL")
            ctx.errors.append(
                f"TrailingSL [{pos.pair}]: new SL failed ({e}), restoring old"
            )
            if current_sl > 0:
                try:
                    client.create_stop_market(
                        pos.pair, exit_side, abs(pos.size),
                        current_sl, reduce_only=True,
                    )
                    logger.info(f"[{pos.pair}] Old SL {current_sl} restored")
                except Exception as restore_err:
                    ctx.errors.append(
                        f"CRITICAL [{pos.pair}]: SL restore also failed: {restore_err}"
                    )

    # ──────────────────────────────────────────────
    # Operation 2: TP Extension
    # ──────────────────────────────────────────────

    def _tp_extension(self, pos, atr: float, indicators: dict,
                      ctx: CycleContext) -> None:
        """
        Extend TP when price approaches it + trend is strong:
        - Price within 0.3% of TP
        - RSI not extreme
        - ADX > 25 (trend confirmed)
        - Volume increasing
        Max 2 extensions to prevent infinite chase.
        """
        # Prefer live TP from exchange position, fallback to TRADE_STATE
        tp_price = pos.tp_price if pos.tp_price > 0 else _parse_float(ctx.trade_state.get("TP_PRICE", 0))
        if tp_price <= 0 or pos.mark_price <= 0:
            return

        # Check extension count limit
        extend_count = _parse_int(ctx.trade_state.get("TP_EXTEND_COUNT", 0))
        if extend_count >= 2:
            return

        # Check proximity to TP
        distance_pct = abs(pos.mark_price - tp_price) / tp_price
        if distance_pct > TP_PROXIMITY_PCT:
            return  # Not close enough to TP

        # Check indicators
        rsi = indicators.get("rsi")
        adx = indicators.get("adx")
        volume_ratio = indicators.get("volume_ratio")

        if adx is None or adx < TP_EXTEND_ADX_MIN:
            return  # Trend not strong enough

        if volume_ratio is not None and volume_ratio < 1.0:
            return  # Volume not increasing

        # RSI check — still room to run
        if pos.direction == "LONG" and rsi is not None:
            if rsi > TP_EXTEND_RSI_LONG_MAX:
                return  # RSI too high, TP extension risky
        elif pos.direction == "SHORT" and rsi is not None:
            if rsi < TP_EXTEND_RSI_SHORT_MIN:
                return  # RSI too low, TP extension risky

        # Calculate new TP
        if pos.direction == "LONG":
            new_tp = tp_price + TP_EXTEND_ATR_MULT * atr
        else:
            new_tp = tp_price - TP_EXTEND_ATR_MULT * atr

        if ctx.verbose:
            print(
                f"    TPExtension [{pos.pair}]: TP {tp_price:.4f} → {new_tp:.4f} "
                f"(ext #{extend_count + 1}, ADX={adx:.1f}, RSI={rsi})"
            )

        # DRY_RUN: log only
        if ctx.dry_run or not ctx.exchange_clients:
            ctx.warnings.append(
                f"[DRY_RUN] TPExtension [{pos.pair}]: "
                f"would move TP {tp_price} → {new_tp}"
            )
            return

        # LIVE: find the farthest TP order and replace it
        client = ctx.exchange_clients.get(pos.platform, ctx.exchange_client)
        tp_orders = self._find_orders_by_type(
            client, pos.pair, "TAKE_PROFIT_MARKET"
        )

        if not tp_orders:
            return  # No TP orders to extend

        # For Range strategy (50/50 split), only extend the farthest TP
        farthest = max(
            tp_orders,
            key=lambda o: abs(float(o.get("stopPrice", 0)) - pos.entry_price),
        )
        farthest_oid = str(farthest.get("orderId", ""))
        farthest_qty = float(farthest.get("origQty", 0))

        if farthest_qty <= 0:
            return

        try:
            client.cancel_order(pos.pair, farthest_oid)
            logger.info(f"[{pos.pair}] Cancelled TP order {farthest_oid}")
        except Exception as e:
            logger.warning(f"[{pos.pair}] Cancel TP {farthest_oid} failed: {e}")
            return

        exit_side = "SELL" if pos.direction == "LONG" else "BUY"
        try:
            result = client.create_take_profit_market(
                pos.pair, exit_side, farthest_qty, new_tp, reduce_only=True,
            )
            new_oid = str(result.get("orderId", ""))
            logger.info(
                f"[{pos.pair}] TP extended: {tp_price} → {new_tp} "
                f"(ext #{extend_count + 1}) id={new_oid}"
            )
            ctx.trade_state_updates["TP_EXTENDED"] = "YES"
            ctx.trade_state_updates["TP_EXTEND_COUNT"] = str(extend_count + 1)
            ctx.trade_state_updates["TP_PRICE"] = str(new_tp)
            ctx.telegram_messages.append(
                f"<b>TP Extended</b> [{pos.pair}]\n"
                f"TP moved: {tp_price:.4f} → {new_tp:.4f} (#{extend_count + 1})"
            )
        except Exception as e:
            # TP extension failed — restore original TP
            logger.error(f"[{pos.pair}] New TP failed: {e} — restoring old TP")
            ctx.warnings.append(
                f"TPExtension [{pos.pair}]: new TP failed ({e}), restoring old"
            )
            try:
                client.create_take_profit_market(
                    pos.pair, exit_side, farthest_qty, tp_price, reduce_only=True,
                )
                logger.info(f"[{pos.pair}] Old TP {tp_price} restored")
            except Exception as restore_err:
                ctx.warnings.append(
                    f"TP restore failed [{pos.pair}]: {restore_err} (SL still active)"
                )

    # ──────────────────────────────────────────────
    # Operation 3: Early Exit
    # ──────────────────────────────────────────────

    def _early_exit(self, pos, atr: float, indicators: dict,
                    ctx: CycleContext) -> None:
        """
        Exit early when momentum reverses:
        - LONG: RSI > 70 + MACD histogram < 0
        - SHORT: RSI < 30 + MACD histogram > 0
        - Volume spike (>2×) + price moving against position
        Sets re-entry eligibility after early exit.
        """
        rsi = indicators.get("rsi")
        macd_hist = indicators.get("macd_histogram")
        volume_ratio = indicators.get("volume_ratio")

        if rsi is None or macd_hist is None:
            return

        exit_reason = ""

        # Momentum reversal exit
        if pos.direction == "LONG":
            if rsi > EARLY_EXIT_RSI_OVERBOUGHT and macd_hist < 0:
                exit_reason = (
                    f"EARLY_EXIT: LONG momentum reversal "
                    f"(RSI={rsi:.1f}>{EARLY_EXIT_RSI_OVERBOUGHT}, MACD_hist={macd_hist:.4f}<0)"
                )
        else:  # SHORT
            if rsi < EARLY_EXIT_RSI_OVERSOLD and macd_hist > 0:
                exit_reason = (
                    f"EARLY_EXIT: SHORT momentum reversal "
                    f"(RSI={rsi:.1f}<{EARLY_EXIT_RSI_OVERSOLD}, MACD_hist={macd_hist:.4f}>0)"
                )

        # Volume spike + significant adverse price move
        if not exit_reason and volume_ratio is not None and pos.entry_price > 0:
            adverse_pct = abs(pos.mark_price - pos.entry_price) / pos.entry_price
            if (volume_ratio > EARLY_EXIT_VOLUME_SPIKE
                    and adverse_pct >= EARLY_EXIT_MIN_ADVERSE_PCT):
                if pos.direction == "LONG" and pos.mark_price < pos.entry_price:
                    exit_reason = (
                        f"EARLY_EXIT: volume spike ({volume_ratio:.1f}×) "
                        f"+ adverse move {adverse_pct:.2%} (mark={pos.mark_price} < entry={pos.entry_price})"
                    )
                elif pos.direction == "SHORT" and pos.mark_price > pos.entry_price:
                    exit_reason = (
                        f"EARLY_EXIT: volume spike ({volume_ratio:.1f}×) "
                        f"+ adverse move {adverse_pct:.2%} (mark={pos.mark_price} > entry={pos.entry_price})"
                    )

        if not exit_reason:
            return

        if ctx.verbose:
            print(f"    EarlyExit [{pos.pair}]: {exit_reason}")

        # DRY_RUN: log only
        if ctx.dry_run or not ctx.exchange_clients:
            ctx.warnings.append(
                f"[DRY_RUN] {exit_reason}"
            )
            # Still set re-entry flag for simulation
            self._set_reentry(pos, ctx)
            return

        # LIVE: close position + cancel all orders
        client = ctx.exchange_clients.get(pos.platform, ctx.exchange_client)

        # Cancel all open orders for this pair
        try:
            open_orders = client.get_open_orders(pos.pair)
            for order in open_orders:
                oid = str(order.get("orderId", ""))
                if oid:
                    try:
                        client.cancel_order(pos.pair, oid)
                    except Exception as e:
                        logger.debug(f"[{pos.pair}] cancel order {oid} skipped: {e}")
        except Exception as e:
            ctx.warnings.append(
                f"EarlyExit [{pos.pair}]: cancel orders failed: {e}"
            )

        # Market close
        try:
            client.close_position_market(pos.pair)
            logger.info(f"[{pos.pair}] Early exit executed: {exit_reason}")

            # Persist to trades.jsonl
            try:
                write_trade(
                    pos.pair, pos.direction, pos.entry_price,
                    exit_price=pos.mark_price,
                    pnl=pos.unrealized_pnl,
                    notes=f"early exit: {exit_reason}",
                )
            except Exception as wt_err:
                logger.warning(f"write_trade for early exit failed: {wt_err}")

            ctx.closed_positions.append(ClosedPosition(
                pair=pos.pair, direction=pos.direction,
                entry_price=pos.entry_price, exit_price=pos.mark_price,
                size=pos.size, pnl=pos.unrealized_pnl,
                reason=exit_reason, timestamp=ctx.timestamp_str,
            ))
            ctx.trade_log_entries.append(
                f"[{ctx.timestamp_str}] {exit_reason} "
                f"size={pos.size} entry={pos.entry_price} "
                f"exit={pos.mark_price} pnl={pos.unrealized_pnl:.2f}"
            )

            # Clear position state
            ctx.trade_state_updates.update({
                "POSITION_OPEN": "NO",
                "PAIR": "—",
                "DIRECTION": "—",
                "ENTRY_PRICE": "0",
                "SIZE": "0",
                "SL_PRICE": "0",
                "TP_PRICE": "0",
                "TP2_PRICE": "0",
                "TRAILING_SL_ACTIVE": "NO",
                "TP_EXTENDED": "NO",
                "TP_EXTEND_COUNT": "0",
            })

            # Set re-entry eligibility
            self._set_reentry(pos, ctx)

            ctx.telegram_messages.append(
                f"<b>Early Exit</b> [{pos.pair} {pos.direction}]\n"
                f"PnL: {pos.unrealized_pnl:.2f}\n"
                f"Reason: {exit_reason}\n"
                f"Re-entry eligible: {REENTRY_COOLDOWN_CYCLES} cycles"
            )
        except Exception as e:
            ctx.errors.append(f"EarlyExit [{pos.pair}]: close failed: {e}")

    # ──────────────────────────────────────────────
    # Operation 4: Re-Entry Eligibility
    # ──────────────────────────────────────────────

    def _set_reentry(self, pos, ctx: CycleContext) -> None:
        """Set re-entry eligibility after early exit (same cycle + cross-cycle)."""
        # Same-cycle flag (always set — useful for evaluate step this cycle)
        ctx.reentry_eligible = True
        ctx.reentry_pair = pos.pair
        ctx.reentry_direction = pos.direction

        # DRY_RUN: only set in-memory flags, don't persist to TRADE_STATE
        if ctx.dry_run:
            return

        # Cross-cycle persistence via TRADE_STATE
        ctx.trade_state_updates.update({
            "REENTRY_ELIGIBLE": "YES",
            "REENTRY_PAIR": pos.pair,
            "REENTRY_DIRECTION": pos.direction,
            "REENTRY_ORIGINAL_ENTRY": str(pos.entry_price),
            "REENTRY_EXIT_TIME": ctx.timestamp_str,
            "REENTRY_CYCLES_REMAINING": str(REENTRY_COOLDOWN_CYCLES),
        })

        if ctx.verbose:
            print(
                f"    ReEntry [{pos.pair}]: eligible for {REENTRY_COOLDOWN_CYCLES} cycles "
                f"(direction={pos.direction})"
            )

    def _load_reentry_state(self, ctx: CycleContext) -> None:
        """Load re-entry state from TRADE_STATE for cross-cycle persistence."""
        ts = ctx.trade_state
        if ts.get("REENTRY_ELIGIBLE") != "YES":
            return

        cycles_remaining = _parse_int(ts.get("REENTRY_CYCLES_REMAINING", 0))
        if cycles_remaining <= 0:
            # Expired — clear re-entry state (skip in DRY_RUN to avoid state pollution)
            if not ctx.dry_run:
                ctx.trade_state_updates.update({
                    "REENTRY_ELIGIBLE": "NO",
                    "REENTRY_PAIR": "—",
                    "REENTRY_DIRECTION": "—",
                    "REENTRY_ORIGINAL_ENTRY": "0",
                    "REENTRY_EXIT_TIME": "—",
                    "REENTRY_CYCLES_REMAINING": "0",
                })
            return

        # Active re-entry eligibility (in-memory only — safe for both modes)
        ctx.reentry_eligible = True
        ctx.reentry_pair = str(ts.get("REENTRY_PAIR", ""))
        ctx.reentry_direction = str(ts.get("REENTRY_DIRECTION", ""))

        # Decrement cycles remaining (skip in DRY_RUN)
        if not ctx.dry_run:
            ctx.trade_state_updates["REENTRY_CYCLES_REMAINING"] = str(
                cycles_remaining - 1
            )

        if ctx.verbose:
            print(
                f"    ReEntry: {ctx.reentry_pair} {ctx.reentry_direction} "
                f"eligible ({cycles_remaining} cycles left)"
            )

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _get_atr(self, symbol: str, ctx: CycleContext) -> float | None:
        """Get ATR from indicators (primary timeframe)."""
        if symbol not in ctx.indicators:
            return None
        tf_data = ctx.indicators[symbol].get(PRIMARY_TIMEFRAME, {})
        return tf_data.get("atr")

    def _get_indicators(self, symbol: str, ctx: CycleContext) -> dict:
        """Get indicators dict for primary timeframe."""
        if symbol not in ctx.indicators:
            return {}
        return ctx.indicators[symbol].get(PRIMARY_TIMEFRAME, {})

    def _find_orders_by_type(self, client, symbol: str,
                             order_type: str) -> list[dict]:
        """Find open orders of a specific type for a symbol."""
        try:
            orders = client.get_open_orders(symbol)
            return [o for o in orders if o.get("type") == order_type]
        except Exception as e:
            logger.warning(f"[{symbol}] get_open_orders failed: {e}")
            return []


# ─── Module-level helpers ───

def _parse_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
