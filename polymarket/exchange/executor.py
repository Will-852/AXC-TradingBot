"""
executor.py — 統一 buy/sell execution logic（WAL + log_trade + position tracking）

設計決定：
- 抽取自 pipeline.py ExecuteExitStep + ExecuteTradesStep 嘅重複 pattern
- WAL intent → exchange call → WAL done/fail → log_trade → position tracking
- dry-run 同 live 用同一個 interface，內部分支
- scope guard (AUTOMATED_CATEGORIES) 由 caller 負責，呢度唔 check
"""

import logging
from polymarket.core.context import PolyContext, PolyPosition, PolySignal
from polymarket.state.trade_log import log_trade

logger = logging.getLogger(__name__)


def execute_buy(ctx: PolyContext, signal: PolySignal) -> PolyPosition | None:
    """Execute a buy order. Returns new PolyPosition on success, None on failure.

    dry_run: simulates fill, logs trade, builds position.
    live: WAL intent → buy_shares FOK → WAL done/fail → log_trade → build position.
    """
    if signal.price <= 0:
        logger.error("signal.price=%.4f for %s — skipping", signal.price, signal.title[:30])
        return None

    shares = signal.bet_size_usdc / signal.price

    if ctx.dry_run:
        if ctx.verbose:
            print(f"    DRY_RUN: would buy {signal.side} {signal.title[:40]} ${signal.bet_size_usdc:.2f}")

        log_trade(
            condition_id=signal.condition_id,
            title=signal.title,
            category=signal.category,
            side=signal.side,
            action="buy",
            shares=shares,
            price=signal.price,
            amount_usdc=signal.bet_size_usdc,
            edge=signal.edge,
            confidence=signal.confidence,
            kelly_fraction=signal.kelly_fraction,
            reasoning=signal.reasoning,
            dry_run=True,
        )

        pos = _build_position(signal, shares, ctx)
        return pos

    # ─── Live Execution with WAL ───
    intent_id = None
    try:
        if ctx.wal:
            intent_id = ctx.wal.log_intent(
                op="buy",
                pair=signal.condition_id,
                direction=signal.side,
                qty=shares,
                price=signal.price,
                sl_price=0,
                platform="polymarket",
            )

        # FOK — fills or fails atomically
        # GTO may recommend LIMIT, but pipeline uses FOK for safety.
        # Maker limit orders belong in run_mm_live.py, not here.
        result = ctx.exchange_client.buy_shares(
            token_id=signal.token_id,
            amount_usdc=signal.bet_size_usdc,
            price=0,
        )

        order_id = result.get("orderID", result.get("id", ""))

        if ctx.wal and intent_id:
            ctx.wal.log_done(intent_id, order_id)

        log_trade(
            condition_id=signal.condition_id,
            title=signal.title,
            category=signal.category,
            side=signal.side,
            action="buy",
            shares=shares,
            price=signal.price,
            amount_usdc=signal.bet_size_usdc,
            edge=signal.edge,
            confidence=signal.confidence,
            kelly_fraction=signal.kelly_fraction,
            reasoning=signal.reasoning,
            order_id=order_id,
            dry_run=False,
        )

        if ctx.verbose:
            print(f"    LIVE: bought {signal.side} {signal.title[:40]} ${signal.bet_size_usdc:.2f} → {order_id}")

        pos = _build_position(signal, shares, ctx)
        return pos

    except Exception as e:
        if ctx.wal and intent_id:
            ctx.wal.log_failed(intent_id, str(e))
        ctx.errors.append(f"Trade failed: {signal.title[:30]} — {e}")
        logger.error("Trade execution failed: %s", e)
        return None


def execute_sell(ctx: PolyContext, pos: PolyPosition, reasons: list[str],
                 urgency: str = "") -> bool:
    """Execute a sell order. Returns True on success.

    dry_run: logs trade, marks as exited.
    live: WAL intent → sell_shares FOK → WAL done/fail → log_trade.
    """
    if ctx.dry_run:
        if ctx.verbose:
            print(
                f"    DRY_RUN EXIT: would sell {pos.side} "
                f"{pos.title[:40]} ({urgency}) "
                f"PnL=${pos.unrealized_pnl:+.2f}"
            )

        log_trade(
            condition_id=pos.condition_id,
            title=pos.title,
            category=pos.category,
            side=pos.side,
            action="sell",
            shares=pos.shares,
            price=pos.current_price,
            amount_usdc=pos.market_value,
            reasoning="; ".join(reasons),
            pnl=pos.unrealized_pnl,
            dry_run=True,
        )
        return True

    # ─── Live Exit with WAL ───
    intent_id = None
    try:
        if ctx.wal:
            intent_id = ctx.wal.log_intent(
                op="sell",
                pair=pos.condition_id,
                direction=pos.side,
                qty=pos.shares,
                price=pos.current_price,
                sl_price=0,
                platform="polymarket",
            )

        result = ctx.exchange_client.sell_shares(
            token_id=pos.token_id,
            shares=pos.shares,
            price=0,
        )

        order_id = result.get("orderID", result.get("id", ""))

        if ctx.wal and intent_id:
            ctx.wal.log_done(intent_id, order_id)

        log_trade(
            condition_id=pos.condition_id,
            title=pos.title,
            category=pos.category,
            side=pos.side,
            action="sell",
            shares=pos.shares,
            price=pos.current_price,
            amount_usdc=pos.market_value,
            reasoning="; ".join(reasons),
            order_id=order_id,
            pnl=pos.unrealized_pnl,
            dry_run=False,
        )

        if ctx.verbose:
            print(
                f"    LIVE EXIT: sold {pos.side} "
                f"{pos.title[:40]} → {order_id}"
            )
        return True

    except Exception as e:
        if ctx.wal and intent_id:
            ctx.wal.log_failed(intent_id, str(e))
        ctx.errors.append(f"Exit failed: {pos.title[:30]} — {e}")
        logger.error("Exit execution failed: %s", e)
        return False


def recalc_exposure(ctx: PolyContext) -> None:
    """Recalculate total exposure + exposure % after position changes."""
    ctx.total_exposure = sum(p.cost_basis for p in ctx.open_positions)
    bankroll = ctx.usdc_balance + ctx.total_exposure
    ctx.exposure_pct = ctx.total_exposure / bankroll if bankroll > 0 else 0.0


def _build_position(signal: PolySignal, shares: float, ctx: PolyContext) -> PolyPosition:
    """Build PolyPosition from signal + execution result."""
    end_date = ""
    for m in ctx.filtered_markets:
        if m.condition_id == signal.condition_id:
            end_date = m.end_date
            break
    return PolyPosition(
        condition_id=signal.condition_id,
        title=signal.title,
        category=signal.category,
        side=signal.side,
        token_id=signal.token_id,
        shares=shares,
        avg_price=signal.price,
        current_price=signal.price,
        cost_basis=signal.bet_size_usdc,
        market_value=signal.bet_size_usdc,
        entry_time=ctx.timestamp_str,
        end_date=end_date,
    )
