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
            entry_result = client.create_market_order(pair, side, qty)
            order_id = str(entry_result.get("orderId", ""))
            fill_price = float(entry_result.get("avgPrice", 0)) or signal.entry_price
            fill_qty = float(entry_result.get("executedQty", 0)) or qty

            logger.info(
                f"[{pair}] ENTRY {side} filled: id={order_id} "
                f"price={fill_price} qty={fill_qty}"
            )

            ctx.entry_order_id = order_id
            ctx.order_result = OrderResult(
                success=True,
                order_id=order_id,
                symbol=pair,
                side=side,
                price=fill_price,
                quantity=fill_qty,
            )

            # ④ Verify fill
            if fill_qty <= 0:
                ctx.warnings.append(f"Entry order {order_id} not filled (qty=0)")
                return ctx

            # ④-b Write entry to trades.jsonl
            try:
                write_trade(pair, side, fill_price, notes="auto entry via trader_cycle")
            except Exception as wt_err:
                logger.warning(f"[{pair}] write_trade failed: {wt_err}")

            # ⑤ Stop Loss (CRITICAL — must succeed)
            try:
                sl_result = client.create_stop_market(
                    pair, exit_side, fill_qty,
                    signal.sl_price, reduce_only=True,
                )
                ctx.sl_order_id = str(sl_result.get("orderId", ""))
                logger.info(f"[{pair}] SL placed: {signal.sl_price} id={ctx.sl_order_id}")

            except Exception as sl_err:
                # SL FAILED → EMERGENCY: close position immediately
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
                try:
                    tp_result = client.create_take_profit_market(
                        pair, exit_side, fill_qty,
                        signal.tp1_price, reduce_only=True,
                    )
                    ctx.tp_order_id = str(tp_result.get("orderId", ""))
                    logger.info(f"[{pair}] TP placed: {signal.tp1_price} id={ctx.tp_order_id}")

                except Exception as tp_err:
                    # TP failure is OK — SL is protecting us
                    ctx.warnings.append(f"TP placement failed for {pair}: {tp_err} (SL active)")
                    logger.warning(f"[{pair}] TP failed: {tp_err} (SL protecting)")

            # ⑦ Update trade state (keys must match TRADE_STATE.md format)
            ctx.trade_state_updates.update({
                "POSITION_OPEN": "YES",
                "PAIR": pair,
                "DIRECTION": signal.direction,
                "ENTRY_PRICE": str(fill_price),
                "SIZE": str(fill_qty),
                "SL_PRICE": str(signal.sl_price),
                "TP_PRICE": str(signal.tp1_price),
                "LAST_TRADE_TIME": ctx.timestamp_str,
            })

            # Trade log entry
            ctx.trade_log_entries.append(
                f"[{ctx.timestamp_str}] ENTRY {signal.direction} {pair} "
                f"qty={fill_qty} @ {fill_price} "
                f"SL={signal.sl_price} TP={signal.tp1_price} "
                f"leverage={leverage}x margin=${signal.margin_required:.2f}"
            )

            if ctx.verbose:
                print(f"    ✅ LIVE ENTRY: {pair} {signal.direction}")
                print(f"      Order: {order_id} | Price: {fill_price} | Qty: {fill_qty}")
                print(f"      SL: {signal.sl_price} ({ctx.sl_order_id})")
                if ctx.tp_order_id:
                    print(f"      TP: {signal.tp1_price} ({ctx.tp_order_id})")

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
