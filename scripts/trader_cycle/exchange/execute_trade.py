"""
execute_trade.py — ExecuteTradeStep: 落盤！
Pipeline Step 12 (SizePosition 之後)

執行序列（30 秒內完成）：
  ① set_margin_mode(pair, 'ISOLATED')
  ② set_leverage(pair, leverage)
  ③ create_market_order(pair, side, qty)  → 入場
  ④ 驗證 fill
  ⑤ create_stop_market(pair, exit_side, qty, sl_price)  → SL
  ⑥ create_take_profit_market(pair, exit_side, qty, tp_price) → TP
  ⑦ 更新 ctx.order_result + trade_state_updates

安全規則：
  - SL 失敗 → 立即 market close（冇 SL 嘅倉位唔可以存在）
  - TP 失敗 → 記錄 warning，保留倉位（SL 保護緊）
  - AuthenticationError → CriticalError → 停 pipeline
  - DRY_RUN → 只 log，唔執行
"""

from __future__ import annotations

import logging

from ..core.context import CycleContext, OrderResult
from ..exchange.exceptions import (
    ExchangeError, InsufficientFundsError, InvalidOrderError,
    AuthenticationError, CriticalError,
)
from memory.writer import write_trade

logger = logging.getLogger(__name__)

# ─── Slippage alert threshold ───
SLIPPAGE_ALERT_PCT = 0.005  # 0.5% — alert if slippage exceeds this


def _extract_commission(order_result: dict) -> float:
    """Extract total commission from exchange order response.
    Binance/Aster format: fills[].commission (USDT).
    Returns 0.0 on parse failure — fee tracking never blocks trading.
    """
    try:
        fills = order_result.get("fills", [])
        if fills:
            return sum(float(f.get("commission", 0)) for f in fills)
    except (TypeError, ValueError, AttributeError):
        pass
    return 0.0


def _calc_slippage(signal_price: float, fill_price: float,
                   direction: str) -> float:
    """Calculate direction-aware slippage percentage.
    Positive = unfavourable (paid more for LONG, received less for SHORT).
    Negative = favourable (got better price than expected).
    """
    if signal_price <= 0:
        return 0.0
    if direction == "LONG":
        # LONG: buying — higher fill = worse
        return (fill_price - signal_price) / signal_price
    else:
        # SHORT: selling — lower fill = worse
        return (signal_price - fill_price) / signal_price


class ExecuteTradeStep:
    """
    Step 12: Execute the selected signal on Aster DEX.
    DRY_RUN: log only, no orders.
    LIVE: full execution sequence with safety rules.
    """
    name = "execute_trade"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.selected_signal:
            return ctx

        signal = ctx.selected_signal

        # DRY_RUN: log and skip
        if ctx.dry_run or not ctx.exchange_client:
            self._log_dry_run(ctx)
            return ctx

        # ─── LIVE EXECUTION ───
        # Multi-exchange: use signal.platform to select client
        client = ctx.exchange_clients.get(signal.platform, ctx.exchange_client)
        pair = signal.pair
        side = "BUY" if signal.direction == "LONG" else "SELL"
        exit_side = "SELL" if signal.direction == "LONG" else "BUY"
        qty = signal.position_size_qty
        leverage = signal.leverage

        if qty <= 0:
            ctx.warnings.append(f"Cannot execute: qty={qty} for {pair}")
            ctx.selected_signal = None
            return ctx

        try:
            # ① Set margin mode (ISOLATED)
            client.set_margin_mode(pair, "ISOLATED")
            logger.info(f"[{pair}] Margin mode: ISOLATED")

            # ② Set leverage
            client.set_leverage(pair, leverage)
            logger.info(f"[{pair}] Leverage: {leverage}x")

            # ③ Market order (entry)
            entry_intent_id = ""
            if ctx.wal:
                entry_intent_id = ctx.wal.log_intent(
                    "entry", pair, signal.direction, qty,
                    signal.entry_price, signal.sl_price, signal.platform,
                )
            entry_result = client.create_market_order(pair, side, qty)
            order_id = str(entry_result.get("orderId", ""))
            fill_price = float(entry_result.get("avgPrice", 0)) or signal.entry_price
            fill_qty = float(entry_result.get("executedQty", 0)) or qty

            logger.info(
                f"[{pair}] ENTRY {side} filled: id={order_id} "
                f"price={fill_price} qty={fill_qty}"
            )

            # Fee extraction (Sprint 1B)
            commission = _extract_commission(entry_result)
            # Slippage calculation (Sprint 1B)
            slippage = _calc_slippage(signal.entry_price, fill_price, signal.direction)

            if ctx.wal and entry_intent_id:
                ctx.wal.log_done(entry_intent_id, order_id)

            ctx.entry_order_id = order_id
            ctx.order_result = OrderResult(
                success=True,
                order_id=order_id,
                symbol=pair,
                side=side,
                price=fill_price,
                quantity=fill_qty,
                commission=commission,
                signal_price=signal.entry_price,
                slippage_pct=slippage,
            )

            # Slippage alert (Sprint 1B)
            if abs(slippage) > SLIPPAGE_ALERT_PCT:
                slip_msg = (
                    f"<b>Slippage Alert</b> [{pair}]\n"
                    f"Signal: {signal.entry_price} → Fill: {fill_price}\n"
                    f"Slippage: {slippage:+.3%}"
                )
                ctx.telegram_messages.append(slip_msg)
                logger.warning(f"[{pair}] Slippage {slippage:+.3%} exceeds threshold")

            # ④ Verify fill
            if fill_qty <= 0:
                ctx.warnings.append(f"Entry order {order_id} not filled (qty=0)")
                return ctx

            # ④-b Write entry to trades.jsonl
            try:
                write_trade(pair, side, fill_price,
                            sl_price=ctx.selected_signal.sl_price,
                            strategy=ctx.selected_signal.strategy,
                            notes="auto entry via trader_cycle")
            except Exception as wt_err:
                logger.warning(f"[{pair}] write_trade failed: {wt_err}")

            # ⑤ Stop Loss (CRITICAL — must succeed)
            sl_intent_id = ""
            if ctx.wal:
                sl_intent_id = ctx.wal.log_intent(
                    "sl_placement", pair, signal.direction, fill_qty,
                    fill_price, signal.sl_price, signal.platform,
                )
            try:
                sl_result = client.create_stop_market(
                    pair, exit_side, fill_qty,
                    signal.sl_price, reduce_only=True,
                )
                ctx.sl_order_id = str(sl_result.get("orderId", ""))
                if ctx.wal and sl_intent_id:
                    ctx.wal.log_done(sl_intent_id, ctx.sl_order_id)
                logger.info(f"[{pair}] SL placed: {signal.sl_price} id={ctx.sl_order_id}")

            except Exception as sl_err:
                # SL FAILED → EMERGENCY: close position immediately
                if ctx.wal and sl_intent_id:
                    ctx.wal.log_failed(sl_intent_id, str(sl_err))
                logger.error(f"[{pair}] SL FAILED: {sl_err} → emergency close!")
                ctx.errors.append(
                    f"SL placement failed for {pair}: {sl_err} → emergency market close"
                )
                try:
                    client.close_position_market(pair)
                    ctx.warnings.append(f"Emergency close after SL failure: {pair}")
                    ctx.order_result.success = False
                    ctx.order_result.error = f"SL failed: {sl_err}"
                except Exception as close_err:
                    ctx.errors.append(
                        f"CRITICAL: Cannot close {pair} after SL failure: {close_err}"
                    )
                return ctx

            # ⑥ Take Profit (nice-to-have — SL is protecting)
            if signal.tp1_price and signal.tp1_price > 0:
                # Range strategy: split qty 50/50 between TP1 and TP2
                if signal.tp2_price and signal.tp2_price > 0:
                    tp1_qty = fill_qty // 2
                    tp2_qty = fill_qty - tp1_qty  # remainder goes to TP2
                else:
                    tp1_qty = fill_qty
                    tp2_qty = 0

                try:
                    tp_result = client.create_take_profit_market(
                        pair, exit_side, tp1_qty,
                        signal.tp1_price, reduce_only=True,
                    )
                    ctx.tp_order_id = str(tp_result.get("orderId", ""))
                    logger.info(f"[{pair}] TP1 placed: {signal.tp1_price} qty={tp1_qty} id={ctx.tp_order_id}")

                except Exception as tp_err:
                    ctx.warnings.append(f"TP1 placement failed for {pair}: {tp_err} (SL active)")
                    logger.warning(f"[{pair}] TP1 failed: {tp_err} (SL protecting)")

                # TP2 (Range strategy only)
                if tp2_qty > 0:
                    try:
                        tp2_result = client.create_take_profit_market(
                            pair, exit_side, tp2_qty,
                            signal.tp2_price, reduce_only=True,
                        )
                        ctx.tp2_order_id = str(tp2_result.get("orderId", ""))
                        logger.info(f"[{pair}] TP2 placed: {signal.tp2_price} qty={tp2_qty} id={ctx.tp2_order_id}")

                    except Exception as tp2_err:
                        ctx.warnings.append(f"TP2 placement failed for {pair}: {tp2_err} (SL active)")
                        logger.warning(f"[{pair}] TP2 failed: {tp2_err} (SL protecting)")

            # ⑦ Update trade state (keys must match TRADE_STATE.md format)
            ctx.trade_state_updates.update({
                "POSITION_OPEN": "YES",
                "PAIR": pair,
                "DIRECTION": signal.direction,
                "ENTRY_PRICE": str(fill_price),
                "SIZE": str(fill_qty),
                "SL_PRICE": str(signal.sl_price),
                "TP_PRICE": str(signal.tp1_price),
                "TP2_PRICE": str(signal.tp2_price or 0),
                "LAST_TRADE_TIME": ctx.timestamp_str,
            })

            # Trade log entry (with fee + slippage)
            tp1_qty_log = tp1_qty if signal.tp1_price and signal.tp1_price > 0 else fill_qty
            tp2_qty_log = tp2_qty if signal.tp1_price and signal.tp1_price > 0 and signal.tp2_price else 0
            tp_info = f"TP1={signal.tp1_price}"
            if signal.tp2_price:
                tp_info += f" TP2={signal.tp2_price} (split {tp1_qty_log}/{tp2_qty_log})"
            fee_info = f" fee=${commission:.4f}" if commission > 0 else ""
            slip_info = f" slip={slippage:+.3%}" if slippage != 0 else ""
            ctx.trade_log_entries.append(
                f"[{ctx.timestamp_str}] ENTRY {signal.direction} {pair} "
                f"qty={fill_qty} @ {fill_price} "
                f"SL={signal.sl_price} {tp_info} "
                f"leverage={leverage}x margin=${signal.margin_required:.2f}"
                f"{fee_info}{slip_info}"
            )

            if ctx.verbose:
                print(f"    ✅ LIVE ENTRY: {pair} {signal.direction}")
                print(f"      Order: {order_id} | Price: {fill_price} | Qty: {fill_qty}")
                print(f"      SL: {signal.sl_price} ({ctx.sl_order_id})")
                if ctx.tp_order_id:
                    print(f"      TP1: {signal.tp1_price} qty={tp1_qty} ({ctx.tp_order_id})")
                if ctx.tp2_order_id:
                    print(f"      TP2: {signal.tp2_price} qty={tp2_qty} ({ctx.tp2_order_id})")

        except AuthenticationError as e:
            raise CriticalError(f"Auth error during trade execution: {e}")

        except InsufficientFundsError as e:
            ctx.warnings.append(f"Insufficient funds for {pair}: {e}")
            ctx.selected_signal = None
            logger.warning(f"[{pair}] Insufficient funds: {e}")

        except InvalidOrderError as e:
            ctx.warnings.append(f"Invalid order for {pair}: {e}")
            ctx.selected_signal = None
            logger.warning(f"[{pair}] Invalid order: {e}")

        except ExchangeError as e:
            ctx.warnings.append(f"Exchange error for {pair}: {e}")
            logger.error(f"[{pair}] Exchange error: {e}")

        return ctx

    def _log_dry_run(self, ctx: CycleContext) -> None:
        """Log what would happen in DRY_RUN mode."""
        signal = ctx.selected_signal
        if not signal:
            return

        ctx.trade_log_entries.append(
            f"[{ctx.timestamp_str}] [DRY_RUN] ENTRY {signal.direction} {signal.pair} "
            f"qty={signal.position_size_qty} @ {signal.entry_price} "
            f"SL={signal.sl_price} TP={signal.tp1_price} "
            f"leverage={signal.leverage}x margin=${signal.margin_required:.2f}"
        )

        if ctx.verbose:
            print(f"    [DRY_RUN] Would execute: {signal.pair} {signal.direction}")
            print(f"      Qty: {signal.position_size_qty} | Entry: {signal.entry_price}")
            print(f"      SL: {signal.sl_price} | TP: {signal.tp1_price}")
            print(f"      Leverage: {signal.leverage}x | Margin: ${signal.margin_required:.2f}")
