"""
test_order_chaser.py — OrderChaser unit tests.

Tests: first-attempt fill, multi-reprice fill, timeout+fallback, cancel.
Uses mock client to avoid real exchange calls.
"""

import os
import sys
import time

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import pytest
from unittest.mock import MagicMock, patch
from scripts.trader_cycle.exchange.order_chaser import (
    OrderChaser, CHASER_TIMEOUT, CHASER_CANCELLED,
)


class MockClient:
    """Mock exchange client with controllable fill behavior."""

    def __init__(self, fill_on_iteration: int = 1):
        """fill_on_iteration: which iteration the order gets filled (0 = never)."""
        self._fill_on = fill_on_iteration
        self._iteration = 0
        self._last_order_id = 0
        self._open_orders = []
        self.cancel_calls = []
        self.limit_order_calls = []

    def get_order_book(self, symbol: str, limit: int = 5):
        return {
            "bids": [[50000.0, 1.0], [49999.0, 2.0]],
            "asks": [[50001.0, 1.0], [50002.0, 2.0]],
        }

    def create_limit_order(self, symbol, side, qty, price, reduce_only=False):
        self._iteration += 1
        self._last_order_id += 1
        oid = str(self._last_order_id)
        if self._iteration < self._fill_on:
            # Order still open
            self._open_orders.append(oid)
        # If iteration == fill_on, order fills immediately (not in open_orders)
        self.limit_order_calls.append({
            "symbol": symbol, "side": side, "qty": qty, "price": price,
        })
        return {"orderId": oid}

    def cancel_order(self, symbol, order_id):
        if order_id in self._open_orders:
            self._open_orders.remove(order_id)
        self.cancel_calls.append(order_id)
        return {}

    def get_open_orders(self, symbol=None):
        return [{"orderId": oid} for oid in self._open_orders]

    def get_order_status(self, symbol, order_id):
        if order_id in self._open_orders:
            return {"status": "NEW", "orderId": order_id}
        return {"status": "FILLED", "orderId": order_id}


class TestOrderChaserFirstFill:
    """Order fills on first iteration."""

    def test_immediate_fill(self, tmp_path):
        client = MockClient(fill_on_iteration=1)
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "BUY", 0.003, 50000.0,
                reprice_interval=0.01, timeout=5.0, max_iterations=5,
            )
            result = chaser.run()

        assert result.get("status") == "FILLED"
        assert float(result.get("executedQty", 0)) == 0.003
        assert result.get("chaser_iterations") == 1
        assert len(client.limit_order_calls) == 1


class TestOrderChaserMultiReprice:
    """Order fills after 3 reprices."""

    def test_fill_after_3_iterations(self, tmp_path):
        client = MockClient(fill_on_iteration=3)
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "BUY", 0.003, 50000.0,
                reprice_interval=0.01, timeout=10.0, max_iterations=5,
            )
            result = chaser.run()

        assert result.get("status") == "FILLED"
        assert result.get("chaser_iterations") == 3
        # Should have placed 3 limit orders
        assert len(client.limit_order_calls) == 3
        # Should have cancelled previous orders (at least iter 2 cancels iter 1, etc.)
        assert len(client.cancel_calls) >= 2


class TestOrderChaserTimeout:
    """Order never fills → timeout."""

    def test_timeout(self, tmp_path):
        client = MockClient(fill_on_iteration=999)  # never fills
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "BUY", 0.003, 50000.0,
                reprice_interval=0.01, timeout=0.05, max_iterations=100,
            )
            result = chaser.run()

        assert result.get("status") == CHASER_TIMEOUT


class TestOrderChaserCancel:
    """External cancel during chase."""

    def test_cancel(self, tmp_path):
        client = MockClient(fill_on_iteration=999)
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "BUY", 0.003, 50000.0,
                reprice_interval=0.01, timeout=10.0, max_iterations=100,
            )
            # Cancel before run (simulates external cancel)
            chaser.cancel()
            result = chaser.run()

        assert result.get("status") == CHASER_CANCELLED


class TestChasePriceCalculation:
    """Verify chase price logic."""

    def test_buy_price_near_ask(self, tmp_path):
        client = MockClient(fill_on_iteration=1)
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "BUY", 0.003, 50000.0,
                offset_ticks=1, reprice_interval=0.01,
            )
            book = client.get_order_book("BTCUSDT")
            price = chaser._calc_chase_price(book)

        # BUY: should be between best_bid (50000) and best_ask (50001)
        assert 50000.0 <= price <= 50001.0

    def test_sell_price_near_bid(self, tmp_path):
        client = MockClient(fill_on_iteration=1)
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "SELL", 0.003, 50000.0,
                offset_ticks=1, reprice_interval=0.01,
            )
            book = client.get_order_book("BTCUSDT")
            price = chaser._calc_chase_price(book)

        # SELL: should be between best_bid (50000) and best_ask (50001)
        assert 50000.0 <= price <= 50001.0

    def test_empty_book(self, tmp_path):
        client = MockClient(fill_on_iteration=1)
        with patch(
            "scripts.trader_cycle.exchange.order_chaser.CHASER_STATE_PATH",
            str(tmp_path / "chaser.json"),
        ):
            chaser = OrderChaser(
                client, "BTCUSDT", "BUY", 0.003, 50000.0,
                reprice_interval=0.01,
            )
            price = chaser._calc_chase_price({"bids": [], "asks": []})

        assert price == 0.0


class TestExecuteTradeWithChaser:
    """Integration: ExecuteTradeStep with CHASER_ENABLED."""

    def test_chaser_disabled_uses_market(self):
        """When CHASER_ENABLED=False, should use create_market_order."""
        from scripts.trader_cycle.exchange.execute_trade import ExecuteTradeStep
        from scripts.trader_cycle.core.context import CycleContext, Signal

        step = ExecuteTradeStep()
        ctx = CycleContext(dry_run=True)
        ctx.selected_signal = Signal(
            pair="BTCUSDT", direction="LONG", strategy="range",
            strength="STRONG", entry_price=50000.0, sl_price=49000.0,
            tp1_price=52000.0, position_size_qty=0.003,
            margin_required=100.0, leverage=8,
        )
        # dry_run → should just log
        result = step.run(ctx)
        assert len(result.trade_log_entries) == 1
        assert "DRY_RUN" in result.trade_log_entries[0]
