"""
position_sync.py — CheckPositionsStep: 從交易所同步倉位 + 餘額
Pipeline Step 7 (NoTradeCheck 之後)

LIVE: 從 Aster DEX 攞 balance + open positions → 填 ctx
DRY_RUN: 從 TRADE_STATE.md 讀取（現有邏輯）
Orphan detection: 倉位冇 SL order → 自動補 SL（crash recovery）
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import sys
from pathlib import Path
_base = os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
if _base not in sys.path:
    sys.path.insert(0, _base)

from ..core.context import CycleContext, Position, ClosedPosition
from ..config.settings import HKT
from ..config.pairs import get_pair
from memory.writer import write_trade

logger = logging.getLogger(__name__)


class CheckPositionsStep:
    """
    Step 7: Sync positions + balance from exchange.
    - LIVE: query Aster DEX via ctx.exchange_client
    - DRY_RUN: read from TRADE_STATE.md (existing behavior)
    - Orphan detection: positions without SL → auto-place SL
    """
    name = "check_positions"

    def run(self, ctx: CycleContext) -> CycleContext:
        if ctx.dry_run or not ctx.exchange_client:
            return self._dry_run_positions(ctx)
        return self._live_positions(ctx)

    def _dry_run_positions(self, ctx: CycleContext) -> CycleContext:
        """Read position info from TRADE_STATE.md (Phase 1-2 behavior)."""
        ts = ctx.trade_state

        # Parse position from trade state if exists
        # Support both old keys (PAIR/DIRECTION/...) and new keys (POSITION_PAIR/...)
        pos_pair = ts.get("POSITION_PAIR", "") or ts.get("PAIR", "")
        pos_dir = ts.get("POSITION_DIRECTION", "") or ts.get("DIRECTION", "")
        pos_entry = _parse_float(ts.get("POSITION_ENTRY", 0)) or _parse_float(ts.get("ENTRY_PRICE", 0))

        # Normalize pair format: "XAG/USDT" → "XAGUSDT"
        if "/" in pos_pair:
            pos_pair = pos_pair.replace("/", "")

        if pos_pair and pos_dir and pos_entry > 0:
            snap = ctx.market_data.get(pos_pair)
            mark = snap.price if snap else 0.0

            # SIZE may contain unit text like "1.059 XAG" — extract the number
            raw_size = ts.get("POSITION_SIZE", "") or ts.get("SIZE", "")
            size = _parse_float_with_unit(raw_size)

            pos = Position(
                pair=pos_pair,
                direction=pos_dir,
                entry_price=pos_entry,
                mark_price=mark,
                size=size,
                sl_price=_parse_float(ts.get("POSITION_SL", 0)) or _parse_float(ts.get("SL_PRICE", 0)),
                tp_price=_parse_float(ts.get("POSITION_TP", 0)) or _parse_float(ts.get("TP_PRICE", 0)),
            )
            ctx.open_positions.append(pos)

        # Balance from trade state (support both key names)
        balance = _parse_float(ts.get("ACCOUNT_BALANCE", 0)) or _parse_float(ts.get("BALANCE_USDT", 0))
        if balance > 0:
            ctx.account_balance = balance

        if ctx.verbose:
            print(f"    [DRY_RUN] Positions: {len(ctx.open_positions)} | Balance: ${ctx.account_balance:.2f}")

        return ctx

    def _live_positions(self, ctx: CycleContext) -> CycleContext:
        """Query ALL connected exchanges for positions + balance."""
        # Build list of exchanges to query: exchange_clients dict + fallback to exchange_client
        clients_to_query: dict[str, object] = {}
        if ctx.exchange_clients:
            clients_to_query.update(ctx.exchange_clients)
        elif ctx.exchange_client:
            clients_to_query["aster"] = ctx.exchange_client

        total_balance = 0.0

        for name, client in clients_to_query.items():
            # ─── Balance ───
            try:
                bal = client.get_usdt_balance()
                total_balance += bal
            except Exception as e:
                ctx.warnings.append(f"Balance fetch failed ({name}): {e}")
                logger.warning(f"Balance fetch failed ({name}): {e}")

            # ─── Positions ───
            try:
                raw_positions = client.get_positions()
                for p in raw_positions:
                    amt = float(p.get("positionAmt", 0))
                    direction = "LONG" if amt > 0 else "SHORT"
                    symbol = p.get("symbol", "")

                    pos = Position(
                        pair=symbol,
                        direction=direction,
                        entry_price=_parse_float(p.get("entryPrice", 0)),
                        mark_price=_parse_float(p.get("markPrice", 0)),
                        size=abs(amt),
                        unrealized_pnl=_parse_float(p.get("unRealizedProfit", 0)),
                        platform=name,
                        # Margin health (Sprint 2B)
                        liquidation_price=_parse_float(p.get("liquidationPx") or p.get("liquidationPrice", 0)),
                        maint_margin=_parse_float(p.get("maintMargin", 0)),
                        isolated_wallet=_parse_float(p.get("isolatedWallet") or p.get("marginUsed", 0)),
                    )

                    # Try to get SL/TP from trade state (support both key formats)
                    state_pair = ctx.trade_state.get("PAIR", "").replace("/", "")
                    if symbol == state_pair:
                        pos.sl_price = _parse_float(ctx.trade_state.get("SL_PRICE", 0))
                        pos.tp_price = _parse_float(ctx.trade_state.get("TP_PRICE", 0))

                    ctx.open_positions.append(pos)

            except Exception as e:
                ctx.warnings.append(f"Position fetch failed ({name}): {e}")
                logger.warning(f"Position fetch failed ({name}): {e}")

        ctx.account_balance = total_balance

        # ─── Orphan Detection ───
        self._detect_orphans(ctx)

        # ─── Auto-detect SL/TP close ───
        self._detect_exchange_close(ctx)

        if ctx.verbose:
            print(f"    [LIVE] Balance: ${ctx.account_balance:.2f} | Positions: {len(ctx.open_positions)}")
            for pos in ctx.open_positions:
                print(f"      [{pos.platform}] {pos.pair} {pos.direction} size={pos.size} entry={pos.entry_price} pnl={pos.unrealized_pnl:.2f}")

        return ctx

    def _detect_exchange_close(self, ctx: CycleContext) -> None:
        """
        Detect positions closed by exchange (SL/TP fill).
        If TRADE_STATE says POSITION_OPEN=YES but exchange has 0 positions,
        the position was closed externally. Query income for PnL and auto-log.
        Pure Python — zero AI tokens.
        """
        ts = ctx.trade_state
        was_open = str(ts.get("POSITION_OPEN", "NO")).upper() == "YES"
        if not was_open or ctx.open_positions:
            return  # No transition — either wasn't open or still is

        pair = str(ts.get("PAIR", "—")).replace("/", "")
        direction = str(ts.get("DIRECTION", "—"))
        entry_price = _parse_float(ts.get("ENTRY_PRICE", 0))
        size = _parse_float_with_unit(ts.get("SIZE", 0))

        if not pair or pair == "—":
            return

        # Query exchange for realized PnL
        realized_pnl = 0.0
        exit_reason = "SL/TP"
        try:
            income = ctx.exchange_client.get_income(
                income_type="REALIZED_PNL", limit=10
            )
            # Find most recent PnL for this pair
            for entry in reversed(income):
                if entry.get("symbol", "") == pair:
                    realized_pnl = float(entry.get("income", 0))
                    break
        except Exception as e:
            logger.warning(f"Income query for close detection failed: {e}")

        # Log the close
        ctx.trade_log_entries.append(
            f"[{ctx.timestamp_str}] EXIT {direction} {pair} "
            f"size={size} entry={entry_price} "
            f"pnl={realized_pnl:.4f} reason={exit_reason}"
        )

        # Calculate exit price from PnL: LONG = entry + pnl/size, SHORT = entry - pnl/size
        if size > 0 and entry_price > 0:
            pnl_per_unit = realized_pnl / size
            if direction == "LONG":
                calc_exit = entry_price + pnl_per_unit
            else:
                calc_exit = entry_price - pnl_per_unit
        else:
            calc_exit = entry_price

        # Persist exit record to trades.jsonl
        try:
            write_trade(pair, direction, entry_price, exit_price=calc_exit,
                        pnl=realized_pnl,
                        notes=f"auto-close {exit_reason}")
        except Exception as e:
            logger.warning(f"write_trade for close failed: {e}")

        ctx.closed_positions.append(ClosedPosition(
            pair=pair, direction=direction,
            entry_price=entry_price, exit_price=calc_exit,
            size=size, pnl=realized_pnl,
            reason=exit_reason, timestamp=ctx.timestamp_str,
        ))

        # Clear position in trade state
        ctx.trade_state_updates.update({
            "POSITION_OPEN": "NO",
            "PAIR": "—",
            "DIRECTION": "—",
            "ENTRY_PRICE": "0",
            "SIZE": "0",
            "SL_PRICE": "0",
            "TP_PRICE": "0",
        })

        logger.info(
            f"[AUTO-CLOSE] {pair} {direction} closed by exchange "
            f"(entry={entry_price}, pnl={realized_pnl:.4f})"
        )
        if ctx.verbose:
            print(f"    ⚠ AUTO-CLOSE detected: {pair} {direction} pnl={realized_pnl:.4f}")

    def _detect_orphans(self, ctx: CycleContext) -> None:
        """
        Orphan detection: positions without SL order → auto-place SL.
        This handles crash recovery (entry filled but SL never placed).
        Routes to correct exchange per position.platform.
        """
        if not ctx.open_positions or (not ctx.exchange_client and not ctx.exchange_clients):
            return

        for pos in ctx.open_positions:
            client = ctx.exchange_clients.get(pos.platform, ctx.exchange_client)
            if not client:
                continue
            try:
                open_orders = client.get_open_orders(pos.pair)
            except Exception as e:
                ctx.warnings.append(f"Orphan check failed for {pos.pair}: {e}")
                continue

            # Check if there's a STOP_MARKET order for this position
            has_sl = any(
                o.get("type") in ("STOP_MARKET", "STOP")
                and o.get("reduceOnly", False)
                for o in open_orders
            )

            if not has_sl:
                # ─── Emergency SL placement ───
                logger.warning(f"ORPHAN detected: {pos.pair} {pos.direction} has no SL order!")
                ctx.warnings.append(
                    f"ORPHAN: {pos.pair} {pos.direction} has no SL → placing emergency SL"
                )

                sl_price = self._calc_emergency_sl(pos, ctx)
                if sl_price and sl_price > 0:
                    exit_side = "SELL" if pos.direction == "LONG" else "BUY"
                    try:
                        result = client.create_stop_market(
                            pos.pair, exit_side, pos.size,
                            sl_price, reduce_only=True,
                        )
                        logger.info(f"Emergency SL placed: {pos.pair} @ {sl_price}")
                        ctx.warnings.append(
                            f"Emergency SL placed: {pos.pair} @ {sl_price}"
                        )
                    except Exception as e:
                        # SL failed → force close position (no unprotected positions)
                        logger.error(f"Emergency SL FAILED for {pos.pair}: {e}")
                        ctx.warnings.append(
                            f"Emergency SL FAILED: {pos.pair} → force closing"
                        )
                        try:
                            client.close_position_market(pos.pair)
                            ctx.warnings.append(f"Force closed orphan: {pos.pair}")
                            try:
                                write_trade(pos.pair, pos.direction, pos.entry_price,
                                            exit_price=pos.mark_price,
                                            pnl=pos.unrealized_pnl,
                                            notes="orphan force close (SL placement failed)")
                            except Exception:
                                pass
                            ctx.closed_positions.append(ClosedPosition(
                                pair=pos.pair, direction=pos.direction,
                                entry_price=pos.entry_price, exit_price=pos.mark_price,
                                size=pos.size, pnl=pos.unrealized_pnl,
                                reason="orphan force close",
                                timestamp=ctx.timestamp_str,
                            ))
                        except Exception as e2:
                            ctx.errors.append(
                                f"CRITICAL: Cannot close orphan {pos.pair}: {e2}"
                            )

    def _calc_emergency_sl(self, pos: Position, ctx: CycleContext) -> Optional[float]:
        """
        Calculate emergency SL for orphan position.
        Uses: saved SL from trade state, or ATR-based fallback.
        """
        # 1. Try saved SL from trade state
        if pos.sl_price and pos.sl_price > 0:
            return pos.sl_price

        # 2. ATR-based fallback
        pair_ind = ctx.indicators.get(pos.pair, {})
        ind_4h = pair_ind.get("4h", {})
        atr = ind_4h.get("atr")

        if atr and atr > 0 and pos.entry_price > 0:
            # Use 1.5× ATR as conservative SL
            sl_mult = 1.5
            try:
                pair_cfg = get_pair(pos.pair)
                if pair_cfg.sl_mult_override is not None:
                    sl_mult = pair_cfg.sl_mult_override
            except KeyError:
                pass

            if pos.direction == "LONG":
                return pos.entry_price - atr * sl_mult
            else:
                return pos.entry_price + atr * sl_mult

        # 3. Mark price fallback (3% from mark price)
        if pos.mark_price and pos.mark_price > 0:
            if pos.direction == "LONG":
                return pos.mark_price * 0.97
            else:
                return pos.mark_price * 1.03

        return None


# ─── Helpers ───

def _parse_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_float_with_unit(val, default: float = 0.0) -> float:
    """Parse float from values like '1.059 XAG' or '0.003 BTC'."""
    if not val:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        # Try extracting the leading number
        s = str(val).strip()
        parts = s.split()
        if parts:
            try:
                return float(parts[0])
            except (TypeError, ValueError):
                pass
        return default
