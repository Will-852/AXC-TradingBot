"""
order_chaser.py — Limit-order chase loop to reduce slippage.

Why: market orders on low-liquidity pairs can slip 0.5%+.
A chase loop places a limit order near best price, waits for fill,
then reprices if not filled. Timeout → fallback to market order.

Design:
- Synchronous loop (pipeline is already sequential, no async needed)
- Uses existing retry_quadratic on individual API calls
- Returns dict matching create_market_order() format for drop-in compatibility
- External cancel via cancel() method (for Telegram /cancel)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, Optional

from ..config.settings import (
    CHASER_INITIAL_OFFSET_TICKS,
    CHASER_REPRICE_INTERVAL_SEC,
    CHASER_MAX_ITERATIONS,
    CHASER_TIMEOUT_SEC,
    CHASER_STATE_PATH,
)

logger = logging.getLogger(__name__)

# Sentinel status for timeout/cancel — checked by execute_trade.py
CHASER_TIMEOUT = "CHASER_TIMEOUT"
CHASER_CANCELLED = "CHASER_CANCELLED"


class OrderChaser:
    """Chase a limit order by repricing near best bid/ask until filled or timeout.

    Usage:
        chaser = OrderChaser(client, "BTCUSDT", "BUY", 0.003, 50000.0)
        result = chaser.run()
        if result.get("status") == CHASER_TIMEOUT:
            # fallback to market order
    """

    def __init__(
        self,
        client,
        symbol: str,
        side: str,
        qty: float,
        ref_price: float,
        offset_ticks: int = CHASER_INITIAL_OFFSET_TICKS,
        max_iterations: int = CHASER_MAX_ITERATIONS,
        reprice_interval: float = CHASER_REPRICE_INTERVAL_SEC,
        timeout: float = CHASER_TIMEOUT_SEC,
    ) -> None:
        self._client = client
        self._symbol = symbol
        self._side = side  # "BUY" or "SELL"
        self._qty = qty
        self._ref_price = ref_price
        self._offset_ticks = offset_ticks
        self._max_iterations = max_iterations
        self._reprice_interval = reprice_interval
        self._timeout = timeout
        self._cancelled = False
        self._current_order_id: Optional[str] = None

    def run(self) -> Dict[str, Any]:
        """Synchronous chase loop. Returns order result dict or timeout sentinel."""
        start = time.monotonic()
        last_order_id = ""

        self._write_state("chasing")

        for iteration in range(1, self._max_iterations + 1):
            if self._cancelled:
                self._cancel_current()
                self._write_state("cancelled")
                return {"status": CHASER_CANCELLED, "iterations": iteration}

            elapsed = time.monotonic() - start
            if elapsed >= self._timeout:
                self._cancel_current()
                self._write_state("timeout")
                logger.warning(
                    f"[CHASER] {self._symbol} timeout after {elapsed:.1f}s "
                    f"({iteration} iterations)"
                )
                return {"status": CHASER_TIMEOUT, "iterations": iteration}

            # Get current best price from order book
            try:
                book = self._client.get_order_book(self._symbol, limit=5)
            except Exception as e:
                logger.warning(f"[CHASER] {self._symbol} order book error: {e}")
                time.sleep(self._reprice_interval)
                continue

            limit_price = self._calc_chase_price(book)
            if limit_price <= 0:
                logger.warning(f"[CHASER] {self._symbol} invalid chase price")
                time.sleep(self._reprice_interval)
                continue

            # Cancel previous order before placing new one
            if last_order_id:
                try:
                    self._client.cancel_order(self._symbol, last_order_id)
                except Exception as e:
                    logger.debug(f"[CHASER] cancel previous order: {e}")

            # Place limit order
            try:
                result = self._client.create_limit_order(
                    self._symbol, self._side, self._qty, limit_price
                )
                order_id = str(result.get("orderId", "") or
                               result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid", ""))
                last_order_id = order_id
                self._current_order_id = order_id
            except Exception as e:
                logger.warning(f"[CHASER] {self._symbol} limit order error: {e}")
                time.sleep(self._reprice_interval)
                continue

            logger.info(
                f"[CHASER] {self._symbol} iter={iteration} "
                f"price={limit_price} id={order_id}"
            )

            # Wait for fill
            time.sleep(self._reprice_interval)

            # Check if filled
            if self._is_filled(order_id):
                fill_result = self._build_fill_result(order_id, limit_price, iteration)
                self._write_state("filled")
                logger.info(
                    f"[CHASER] {self._symbol} filled at {limit_price} "
                    f"after {iteration} iterations"
                )
                return fill_result

        # Max iterations exhausted
        self._cancel_current()
        self._write_state("timeout")
        logger.warning(
            f"[CHASER] {self._symbol} max iterations ({self._max_iterations}) reached"
        )
        return {"status": CHASER_TIMEOUT, "iterations": self._max_iterations}

    def cancel(self) -> None:
        """External cancel (e.g. from Telegram /cancel command)."""
        self._cancelled = True
        self._cancel_current()

    def _calc_chase_price(self, book: Dict[str, Any]) -> float:
        """Calculate limit price: best bid/ask ± offset ticks.

        BUY: best_ask - offset (try to buy cheaper than ask)
        SELL: best_bid + offset (try to sell higher than bid)
        If offset puts us beyond mid, use mid instead.
        """
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return 0.0

        best_bid = bids[0][0]
        best_ask = asks[0][0]

        if best_bid <= 0 or best_ask <= 0:
            return 0.0

        # Estimate tick size from spread
        tick_size = (best_ask - best_bid) / max(self._offset_ticks + 1, 2)
        if tick_size <= 0:
            tick_size = best_ask * 0.0001  # 1 bps fallback

        if self._side == "BUY":
            # Place at best_ask minus offset — aggressive enough to likely fill
            price = best_ask - tick_size * self._offset_ticks
            # Don't go below best bid
            return max(price, best_bid)
        else:
            # Place at best_bid plus offset
            price = best_bid + tick_size * self._offset_ticks
            # Don't go above best ask
            return min(price, best_ask)

    def _is_filled(self, order_id: str) -> bool:
        """Check if order is fully filled by querying open orders."""
        if not order_id:
            return False
        try:
            open_orders = self._client.get_open_orders(self._symbol)
            # If order is NOT in open orders, it was filled (or cancelled)
            for o in open_orders:
                oid = str(o.get("orderId", "") or o.get("oid", ""))
                if oid == order_id:
                    return False  # still open
            return True  # not found in open orders → filled
        except Exception as e:
            logger.debug(f"[CHASER] fill check error: {e}")
            return False

    def _cancel_current(self) -> None:
        """Cancel the current outstanding limit order."""
        if self._current_order_id:
            try:
                self._client.cancel_order(self._symbol, self._current_order_id)
            except Exception as e:
                logger.debug(f"[CHASER] cancel error: {e}")
            self._current_order_id = None

    def _build_fill_result(
        self, order_id: str, fill_price: float, iterations: int
    ) -> Dict[str, Any]:
        """Build result dict matching create_market_order() format."""
        return {
            "orderId": order_id,
            "avgPrice": str(fill_price),
            "executedQty": str(self._qty),
            "status": "FILLED",
            "type": "LIMIT",
            "chaser_iterations": iterations,
            "fills": [
                {
                    "price": str(fill_price),
                    "qty": str(self._qty),
                    "commission": "0",
                    "commissionAsset": "USDT",
                }
            ],
        }

    def _write_state(self, status: str) -> None:
        """Atomic write of chaser state for monitoring/dashboard."""
        state = {
            "symbol": self._symbol,
            "side": self._side,
            "qty": self._qty,
            "status": status,
            "timestamp": time.time(),
        }
        try:
            dir_name = os.path.dirname(CHASER_STATE_PATH)
            os.makedirs(dir_name, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(state, f)
            os.replace(tmp, CHASER_STATE_PATH)
        except OSError as e:
            logger.debug(f"[CHASER] state write error: {e}")
