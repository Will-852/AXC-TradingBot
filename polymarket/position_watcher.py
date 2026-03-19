#!/usr/bin/env python3
"""
position_watcher.py — 獨立 30s position 監控（take-profit）

Pipeline 之間嘅 gap 做 take-profit 監控：
- 每 30s check 所有 positions 嘅 CLOB midpoint
- Token price ≥ TAKE_PROFIT_TOKEN_PRICE → market sell 即走
- 用 FileLock 同 pipeline 互斥（pipeline 跑緊就 skip）

Usage:
  PYTHONPATH=.:scripts python3 polymarket/position_watcher.py --live
  PYTHONPATH=.:scripts python3 polymarket/position_watcher.py --dry-run --once
"""

from __future__ import annotations
import argparse
import logging
import os
import sys
import time

# ─── Setup import paths ───
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if _AXC not in sys.path:
    sys.path.insert(0, _AXC)
_scripts_dir = os.path.join(_AXC, "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from polymarket.config.settings import (
    HKT, POLY_STATE_PATH, POLY_PIPELINE_LOCK_PATH,
    TAKE_PROFIT_TOKEN_PRICE, WATCHER_INTERVAL_SEC, LOG_DIR,
    AUTOMATED_CATEGORIES,
)
from polymarket.state.poly_state import read_state, write_state
from polymarket.state.trade_log import log_trade
from shared_infra.file_lock import FileLock

logger = logging.getLogger("poly_watcher")


def _check_positions(client, dry_run: bool, verbose: bool) -> int:
    """Check all positions for take-profit. Returns number of exits executed."""
    state = read_state()
    positions = state.get("positions", [])

    if not positions:
        if verbose:
            logger.info("No positions — sleeping")
        return 0

    exits_done = 0
    exited_ids: set[str] = set()

    for pos in positions:
        token_id = pos.get("token_id", "")
        if not token_id:
            continue

        # ─── Scope guard: only auto-sell AUTOMATED_CATEGORIES ───
        category = pos.get("category", "")
        if category not in AUTOMATED_CATEGORIES:
            continue

        title = pos.get("title", "?")[:40]
        shares = float(pos.get("shares", 0))
        avg_price = float(pos.get("avg_price", 0))
        cost_basis = float(pos.get("cost_basis", 0))

        # ─── Query live midpoint ───
        try:
            midpoint = client.get_midpoint(token_id)
        except Exception as e:
            logger.warning("Midpoint failed for %s: %s", title, e)
            continue

        if not midpoint or midpoint <= 0:
            continue

        if verbose:
            logger.info("  %s mid=%.4f (threshold=%.2f)", title, midpoint, TAKE_PROFIT_TOKEN_PRICE)

        # ─── Take profit trigger ───
        if midpoint < TAKE_PROFIT_TOKEN_PRICE:
            continue

        pnl = (midpoint - avg_price) * shares
        market_value = midpoint * shares

        logger.info(
            "TAKE PROFIT: %s mid=%.4f ≥ %.2f — selling %.2f shares (PnL=$%.2f)",
            title, midpoint, TAKE_PROFIT_TOKEN_PRICE, shares, pnl,
        )

        if dry_run:
            log_trade(
                condition_id=pos.get("condition_id", ""),
                title=pos.get("title", ""),
                category=pos.get("category", ""),
                side=pos.get("side", ""),
                action="sell",
                shares=shares,
                price=midpoint,
                amount_usdc=market_value,
                reasoning=f"watcher take-profit: mid={midpoint:.4f}",
                pnl=pnl,
                dry_run=True,
            )
        else:
            try:
                result = client.sell_shares(token_id=token_id, shares=shares, price=0)
                order_id = result.get("orderID", result.get("id", ""))
                log_trade(
                    condition_id=pos.get("condition_id", ""),
                    title=pos.get("title", ""),
                    category=pos.get("category", ""),
                    side=pos.get("side", ""),
                    action="sell",
                    shares=shares,
                    price=midpoint,
                    amount_usdc=market_value,
                    reasoning=f"watcher take-profit: mid={midpoint:.4f}",
                    order_id=order_id,
                    pnl=pnl,
                    dry_run=False,
                )
            except Exception as e:
                logger.error("Sell failed for %s: %s", title, e)
                continue

        exited_ids.add(pos.get("condition_id", ""))
        exits_done += 1

        # ─── Telegram alert ───
        try:
            from shared_infra.telegram import send_telegram
            mode = "DRY" if dry_run else "LIVE"
            send_telegram(
                f"<b>💰 Watcher Take Profit ({mode})</b>\n"
                f"{pos.get('side', '')} {pos.get('title', '')[:50]}\n"
                f"Mid: {midpoint:.4f} ≥ {TAKE_PROFIT_TOKEN_PRICE}\n"
                f"PnL: ${pnl:+.2f}"
            )
        except Exception as e:
            logger.debug("Telegram send failed: %s", e)

    # ─── Update state: remove exited positions ───
    if exited_ids:
        state["positions"] = [
            p for p in state.get("positions", [])
            if p.get("condition_id", "") not in exited_ids
        ]
        write_state(state)
        logger.info("Removed %d position(s) from state", len(exited_ids))

    return exits_done


def main():
    parser = argparse.ArgumentParser(description="Polymarket Position Watcher")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run once then exit")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    dry_run = not args.live

    # ─── Logging ───
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(LOG_DIR, "position_watcher.log")),
        ],
    )

    mode_str = "DRY_RUN" if dry_run else "LIVE"
    logger.info("Position watcher starting (%s)", mode_str)

    # ─── Init exchange client ───
    from polymarket.exchange.polymarket_client import PolymarketClient
    client = PolymarketClient(dry_run=dry_run)

    while True:
        try:
            # Try acquire pipeline lock (non-blocking)
            try:
                lock = FileLock(POLY_PIPELINE_LOCK_PATH, timeout=0)
                lock.__enter__()
            except TimeoutError:
                logger.debug("Pipeline lock held — skipping this cycle")
                if args.once:
                    break
                time.sleep(WATCHER_INTERVAL_SEC)
                continue

            try:
                _check_positions(client, dry_run=dry_run, verbose=args.verbose)
            finally:
                lock.__exit__(None, None, None)

        except KeyboardInterrupt:
            logger.info("Watcher stopped by user")
            break
        except Exception as e:
            logger.error("Watcher error: %s", e, exc_info=True)

        if args.once:
            break
        time.sleep(WATCHER_INTERVAL_SEC)


if __name__ == "__main__":
    main()
