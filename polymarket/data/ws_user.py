#!/usr/bin/env python3
"""
ws_user.py — Polymarket User WebSocket Feed (fills + order status)

Connects to Polymarket CLOB user WebSocket for instant fill/cancel notifications.
Replaces REST polling of get_orders()/get_trades() as the primary fill detection
path (REST remains as fallback).

Design decisions:
  - Authenticated channel: API creds (key/secret/passphrase) in subscribe message
  - markets=[] → receive updates for ALL user markets (no need to track token_ids)
  - Order events: track status (LIVE/MATCHED/CANCELED) per order_id
  - Trade events: store recent fills for quick lookup
  - Client PING every 10s (Polymarket heartbeat requirement)
  - Daemon thread so it dies with the parent process
  - Exponential backoff: 1s→2s→4s→…→30s (with 10% jitter)
  - Fully optional: if WS fails, callers use existing REST fallback
  - No Redis, no external deps beyond websockets
  - on_fill callback for immediate reaction (called from WS thread)
"""

import asyncio
import json
import logging
import random
import threading
import time
from collections import deque
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────
_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

_MAX_RECONNECT_DELAY = 30       # seconds
_PING_INTERVAL_S = 10           # Polymarket requires PING every 10s
_MAX_RECENT_FILLS = 100         # keep last N fills in memory


class PolymarketUserFeed:
    """Thread-safe Polymarket user event feed (order status + fills).

    Usage:
        feed = PolymarketUserFeed()
        feed.start(api_key, api_secret, api_passphrase)
        status = feed.get_order_status("order_id")   # str | None
        fills = feed.get_recent_fills(since_ts=time.time() - 60)
        feed.stop()
    """

    def __init__(self):
        # {order_id: {"status": str, "size_matched": float, "price": float,
        #             "asset_id": str, "side": str, "ts": float}}
        self._orders: dict[str, dict] = {}
        # Recent fills: [{order_id, price, size, side, asset_id, market, ts}]
        self._recent_fills: deque[dict] = deque(maxlen=_MAX_RECENT_FILLS)
        self._lock = threading.Lock()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Auth creds (set at start time)
        self._api_key: str = ""
        self._api_secret: str = ""
        self._api_passphrase: str = ""

        # Fill callbacks (called from WS thread — keep fast)
        self._fill_callbacks: list[Callable] = []
        self._cb_lock = threading.Lock()

        # Connection state for health checks
        self._connected = False
        self._last_msg_ts: float = 0.0

    # ── Public API ───────────────────────────────

    def start(self, api_key: str, api_secret: str, api_passphrase: str) -> None:
        """Start the user WS feed in a daemon thread. Non-blocking."""
        if self._thread and self._thread.is_alive():
            logger.warning("PolymarketUserFeed already running")
            return

        if not api_key or not api_secret or not api_passphrase:
            logger.error("PolymarketUserFeed: missing API creds — not starting")
            return

        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="ws-user", daemon=True
        )
        self._thread.start()
        logger.info("PolymarketUserFeed started (daemon thread)")

    def stop(self) -> None:
        """Signal the feed to stop. Non-blocking."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._connected = False
        logger.info("PolymarketUserFeed stop requested")

    def get_order_status(self, order_id: str) -> str | None:
        """Return latest known status (LIVE/MATCHED/CANCELED) or None if unknown."""
        with self._lock:
            entry = self._orders.get(order_id)
        if entry is None:
            return None
        return entry["status"]

    def get_recent_fills(self, since_ts: float = 0) -> list[dict]:
        """Return fills since timestamp (or all if since_ts=0)."""
        with self._lock:
            if since_ts <= 0:
                return list(self._recent_fills)
            return [f for f in self._recent_fills if f["ts"] >= since_ts]

    def is_filled(self, order_id: str) -> bool:
        """Shorthand: is order fully matched?"""
        status = self.get_order_status(order_id)
        return status == "MATCHED"

    def is_cancelled(self, order_id: str) -> bool:
        """Shorthand: is order cancelled?"""
        status = self.get_order_status(order_id)
        # Polymarket uses CANCELED (one L) in WS events
        return status in ("CANCELED", "CANCELLED")

    def on_fill(self, callback: Callable) -> None:
        """Register callback for fill events. Called from WS thread — keep fast.

        Callback receives a dict: {order_id, price, size, side, asset_id, market, ts}
        """
        with self._cb_lock:
            self._fill_callbacks.append(callback)

    @property
    def connected(self) -> bool:
        """Whether the WS is currently connected."""
        return self._connected

    # ── Internal ─────────────────────────────────

    def _run_loop(self) -> None:
        """Entry point for daemon thread — creates event loop and runs."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_loop())
        except RuntimeError:
            pass  # "Event loop stopped" on clean shutdown — expected
        except Exception as exc:
            if not self._stop_event.is_set():
                logger.error("PolymarketUserFeed loop crashed: %s", exc)
        finally:
            # Cancel remaining tasks to suppress "Task was destroyed" warnings
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _ws_loop(self) -> None:
        """Main WS loop with auto-reconnect (mirrors ws_polymarket.py pattern)."""
        consecutive_failures = 0

        while not self._stop_event.is_set():
            try:
                logger.info("WS-USER connecting: %s", _WS_URL)

                async with websockets.connect(
                    _WS_URL,
                    ping_interval=None,   # We handle PING ourselves
                    ping_timeout=None,
                    close_timeout=10,
                    max_size=1_048_576,   # 1MB
                ) as ws:
                    logger.info("WS-USER connected to Polymarket user channel")
                    consecutive_failures = 0
                    self._connected = True

                    # Send authenticated subscribe
                    await self._subscribe(ws)

                    # Start PING task
                    ping_task = asyncio.ensure_future(self._ping_loop(ws))

                    try:
                        while not self._stop_event.is_set():
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                self._last_msg_ts = time.time()
                                self._process_message(raw)
                            except asyncio.TimeoutError:
                                pass  # normal — just check stop flag
                    finally:
                        self._connected = False
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except websockets.ConnectionClosed as exc:
                self._connected = False
                logger.warning("WS-USER closed: code=%s reason=%s", exc.code, exc.reason)
            except (OSError, websockets.WebSocketException) as exc:
                self._connected = False
                logger.error("WS-USER error: %s", exc)
            except Exception as exc:
                self._connected = False
                logger.error("WS-USER unexpected: %s: %s", type(exc).__name__, exc)

            if self._stop_event.is_set():
                break

            # Exponential backoff with jitter
            consecutive_failures += 1
            delay = min(2 ** consecutive_failures, _MAX_RECONNECT_DELAY)
            delay += random.uniform(0, delay * 0.1)  # 10% jitter
            logger.info("WS-USER reconnecting in %.1fs (attempt %d)",
                        delay, consecutive_failures)

            # Sleep in small increments so stop_event is checked
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not self._stop_event.is_set():
                await asyncio.sleep(min(0.5, deadline - time.monotonic()))

        logger.info("PolymarketUserFeed stopped")

    async def _ping_loop(self, ws) -> None:
        """Send PING every 10s as required by Polymarket."""
        while not self._stop_event.is_set():
            try:
                await ws.send("PING")
            except Exception:
                break
            await asyncio.sleep(_PING_INTERVAL_S)

    async def _subscribe(self, ws) -> None:
        """Send authenticated subscribe message.

        markets=[] means receive updates for ALL user markets.
        """
        msg = {
            "auth": {
                "apiKey": self._api_key,
                "secret": self._api_secret,
                "passphrase": self._api_passphrase,
            },
            "type": "user",
            "markets": [],  # all markets
        }
        try:
            await ws.send(json.dumps(msg))
            logger.info("WS-USER subscribed (all markets)")
        except Exception as exc:
            logger.error("WS-USER subscribe failed: %s", exc)

    def _process_message(self, raw: str) -> None:
        """Parse user channel WS message, update _orders and _recent_fills."""
        # Handle PONG response to our PING
        if raw == "PONG":
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("WS-USER invalid JSON: %s", str(raw)[:200])
            return

        # Messages can be a list of events or a single event
        events = msg if isinstance(msg, list) else [msg]

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("event_type", "")
            if event_type == "order":
                self._handle_order(event)
            elif event_type == "trade":
                self._handle_trade(event)
            # Other event types → ignore

    def _handle_order(self, event: dict) -> None:
        """Process order status update.

        Event fields: id, owner, market, asset_id, side, original_size,
        size_matched, price, status, order_type, timestamp
        """
        order_id = event.get("id", "")
        if not order_id:
            return

        status = event.get("status", "")
        try:
            size_matched = float(event.get("size_matched", "0"))
            price = float(event.get("price", "0"))
        except (ValueError, TypeError):
            size_matched = 0.0
            price = 0.0

        now = time.time()

        with self._lock:
            self._orders[order_id] = {
                "status": status,
                "size_matched": size_matched,
                "price": price,
                "asset_id": event.get("asset_id", ""),
                "market": event.get("market", ""),
                "side": event.get("side", ""),
                "original_size": event.get("original_size", "0"),
                "ts": now,
            }

        logger.debug("WS-USER order %s: %s (matched=%.1f @ %.3f)",
                      order_id[:12], status, size_matched, price)

        # If fully matched, also record as a fill
        if status == "MATCHED":
            fill = {
                "order_id": order_id,
                "price": price,
                "size": size_matched,
                "side": event.get("side", ""),
                "asset_id": event.get("asset_id", ""),
                "market": event.get("market", ""),
                "ts": now,
            }
            with self._lock:
                self._recent_fills.append(fill)
            self._fire_fill_callbacks(fill)

    def _handle_trade(self, event: dict) -> None:
        """Process trade event.

        Event fields: id, taker_order_id, market, asset_id, side, size,
        price, status, trader_side, maker_orders, timestamp
        """
        trade_id = event.get("id", "")
        if not trade_id:
            return

        try:
            size = float(event.get("size", "0"))
            price = float(event.get("price", "0"))
        except (ValueError, TypeError):
            size = 0.0
            price = 0.0

        # Determine the relevant order_id (our order)
        taker_order_id = event.get("taker_order_id", "")
        maker_orders = event.get("maker_orders", [])

        now = time.time()
        fill = {
            "order_id": taker_order_id,
            "trade_id": trade_id,
            "price": price,
            "size": size,
            "side": event.get("side", ""),
            "asset_id": event.get("asset_id", ""),
            "market": event.get("market", ""),
            "trader_side": event.get("trader_side", ""),
            "status": event.get("status", ""),
            "ts": now,
        }

        with self._lock:
            self._recent_fills.append(fill)

        logger.debug("WS-USER trade %s: %s %.1f @ %.3f (side=%s)",
                      trade_id[:12], event.get("trader_side", ""),
                      size, price, event.get("side", ""))

        self._fire_fill_callbacks(fill)

    def _fire_fill_callbacks(self, fill: dict) -> None:
        """Invoke registered fill callbacks (from WS thread)."""
        with self._cb_lock:
            callbacks = list(self._fill_callbacks)
        for cb in callbacks:
            try:
                cb(fill)
            except Exception as exc:
                logger.warning("WS-USER fill callback error: %s", exc)

    # ── Housekeeping ─────────────────────────────

    def cleanup_old_orders(self, max_age_s: float = 3600) -> int:
        """Remove order entries older than max_age_s. Returns count removed.

        Call periodically to prevent unbounded memory growth.
        Keeps recent fills (bounded by deque maxlen) untouched.
        """
        cutoff = time.time() - max_age_s
        removed = 0
        with self._lock:
            stale_ids = [oid for oid, data in self._orders.items()
                         if data["ts"] < cutoff]
            for oid in stale_ids:
                del self._orders[oid]
                removed += 1
        if removed:
            logger.debug("WS-USER cleanup: removed %d stale order entries", removed)
        return removed
