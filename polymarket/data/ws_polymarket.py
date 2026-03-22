#!/usr/bin/env python3
"""
ws_polymarket.py — Polymarket WebSocket Order Book Feed (in-process, thread-safe)

Connects to Polymarket CLOB market WebSocket for live order book data.
Stores latest bid/ask/mid/depth/imbalance per token_id in a thread-safe dict.
Runs in a daemon thread with its own asyncio event loop.

Design decisions:
  - Full book snapshot on subscribe (initial_dump=true) → delta via price_change
  - For v1: price_change events update BBO from event data directly
    (full book rebuild deferred to v2 — simplicity > correctness)
  - Client PING every 10s (Polymarket heartbeat requirement)
  - Daemon thread so it dies with the parent process
  - Exponential backoff: 1s→2s→4s→…→30s (with 10% jitter)
  - Fully optional: if WS fails, callers use existing REST fallback
  - No Redis, no external deps beyond websockets
  - Subscribe/unsubscribe triggers clean reconnect (Polymarket only sends
    initial book snapshot on the first subscribe message per connection)
"""

import asyncio
import json
import logging
import random
import threading
import time

import websockets

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────
_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

_MAX_RECONNECT_DELAY = 30       # seconds
_PING_INTERVAL_S = 10           # Polymarket requires PING every 10s
_DEFAULT_MAX_AGE = 10.0         # seconds — stale threshold


class PolymarketBookFeed:
    """Thread-safe Polymarket order book feed.

    Usage:
        feed = PolymarketBookFeed()
        feed.start()                              # non-blocking, spawns daemon thread
        feed.subscribe([up_tok, dn_tok], cid)     # subscribe to tokens
        mid = feed.get_midpoint(token_id)          # float | None
        imb = feed.get_ob_imbalance(token_id)      # float | None
        feed.stop()
    """

    def __init__(self, max_age: float = _DEFAULT_MAX_AGE):
        self._max_age = max_age
        # {token_id: {"mid", "bid", "ask", "bid_depth", "ask_depth",
        #             "imbalance", "last_trade", "ts"}}
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Subscription tracking: token_id → condition_id (for re-subscribe on reconnect)
        self._subscriptions: dict[str, str] = {}  # token_id → condition_id
        self._sub_lock = threading.Lock()
        # Flag to force reconnect (for dynamic subscribe — need initial_dump)
        self._reconnect_requested = threading.Event()

        # Full order book storage for accurate mid/depth computation
        # {token_id: {"bids": {price_str: size_float}, "asks": {price_str: size_float}}}
        self._books: dict[str, dict] = {}

    # ── Public API ───────────────────────────────

    def start(self) -> None:
        """Start the WS feed in a daemon thread. Non-blocking."""
        if self._thread and self._thread.is_alive():
            logger.warning("PolymarketBookFeed already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="ws-polymarket", daemon=True
        )
        self._thread.start()
        logger.info("PolymarketBookFeed started (daemon thread)")

    def stop(self) -> None:
        """Signal the feed to stop. Non-blocking."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("PolymarketBookFeed stop requested")

    def get_midpoint(self, token_id: str) -> float | None:
        """Return mid price if fresh (< max_age), else None."""
        with self._lock:
            entry = self._data.get(token_id)
        if entry is None:
            return None
        age = time.time() - entry["ts"]
        if age > self._max_age:
            return None
        return entry["mid"]

    def get_ob_imbalance(self, token_id: str) -> float | None:
        """Return order book imbalance if fresh (< max_age), else None.
        Range: -1 (all asks) to +1 (all bids)."""
        with self._lock:
            entry = self._data.get(token_id)
        if entry is None:
            return None
        age = time.time() - entry["ts"]
        if age > self._max_age:
            return None
        return entry["imbalance"]

    def get_book_state(self, token_id: str) -> dict | None:
        """Return full book state dict if fresh, else None."""
        with self._lock:
            entry = self._data.get(token_id)
        if entry is None:
            return None
        age = time.time() - entry["ts"]
        if age > self._max_age:
            return None
        return dict(entry)  # shallow copy

    def subscribe(self, token_ids: list[str], condition_id: str = "") -> None:
        """Subscribe to new token_ids. Can call multiple times.

        Triggers a reconnect so the server sends initial_dump for new tokens.
        (Polymarket only sends initial book snapshots on the first subscribe
        message per connection — dynamic subscribes don't trigger initial_dump.)
        """
        if not token_ids:
            return
        new_tokens = False
        with self._sub_lock:
            for tid in token_ids:
                if tid not in self._subscriptions:
                    new_tokens = True
                self._subscriptions[tid] = condition_id

        if new_tokens:
            # Signal reconnect so we get initial_dump for the new tokens
            self._reconnect_requested.set()
            logger.info("PolymarketBookFeed: subscribe %d tokens → reconnect queued",
                        len(token_ids))

    def unsubscribe(self, token_ids: list[str]) -> None:
        """Remove tokens from subscription. Triggers reconnect to clean up."""
        if not token_ids:
            return
        with self._sub_lock:
            for tid in token_ids:
                self._subscriptions.pop(tid, None)
        with self._lock:
            for tid in token_ids:
                self._data.pop(tid, None)
                self._books.pop(tid, None)
        # Reconnect to cleanly re-subscribe without removed tokens
        self._reconnect_requested.set()
        logger.info("PolymarketBookFeed: unsubscribe %d tokens → reconnect queued",
                    len(token_ids))

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
                logger.error("PolymarketBookFeed loop crashed: %s", exc)
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
        """Main WS loop with auto-reconnect (mirrors ws_binance.py pattern)."""
        consecutive_failures = 0

        while not self._stop_event.is_set():
            # Wait for at least one subscription before connecting
            # (Polymarket closes connections that don't subscribe immediately)
            while not self._stop_event.is_set():
                with self._sub_lock:
                    has_subs = bool(self._subscriptions)
                if has_subs:
                    break
                await asyncio.sleep(0.5)

            if self._stop_event.is_set():
                break

            try:
                logger.info("WS connecting: %s", _WS_URL)

                async with websockets.connect(
                    _WS_URL,
                    ping_interval=None,   # We handle PING ourselves
                    ping_timeout=None,
                    close_timeout=10,
                    max_size=5_242_880,   # 5MB — order books can be large
                ) as ws:
                    logger.info("WS connected to Polymarket CLOB")
                    consecutive_failures = 0

                    # 2check fix: clear stale book data before fresh subscribe
                    # Prevents serving old prices during the gap before initial_dump arrives
                    with self._lock:
                        self._books.clear()
                        self._data.clear()

                    # Send initial subscribe with all known tokens
                    await self._subscribe_all(ws)

                    # Clear reconnect flag (we just connected fresh)
                    self._reconnect_requested.clear()

                    # Start PING task
                    ping_task = asyncio.ensure_future(self._ping_loop(ws))
                    reconnect_now = False

                    try:
                        while not self._stop_event.is_set():
                            # Use recv with timeout so we can check reconnect flag
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                self._process_message(raw)
                            except asyncio.TimeoutError:
                                pass  # normal — just check flags

                            # Check if reconnect was requested (new subscribe/unsubscribe)
                            if self._reconnect_requested.is_set():
                                logger.info("WS reconnect requested (subscription change)")
                                reconnect_now = True
                                break
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

                    # If reconnect was requested, skip backoff → reconnect immediately
                    if reconnect_now:
                        self._reconnect_requested.clear()
                        continue

            except websockets.ConnectionClosed as exc:
                logger.warning("WS closed: code=%s reason=%s", exc.code, exc.reason)
            except (OSError, websockets.WebSocketException) as exc:
                logger.error("WS error: %s", exc)
            except Exception as exc:
                logger.error("WS unexpected: %s: %s", type(exc).__name__, exc)

            if self._stop_event.is_set():
                break

            # Exponential backoff with jitter
            consecutive_failures += 1
            delay = min(2 ** consecutive_failures, _MAX_RECONNECT_DELAY)
            delay += random.uniform(0, delay * 0.1)  # 10% jitter
            logger.info("WS reconnecting in %.1fs (attempt %d)",
                        delay, consecutive_failures)

            # Sleep in small increments so stop_event is checked
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not self._stop_event.is_set():
                await asyncio.sleep(min(0.5, deadline - time.monotonic()))

        logger.info("PolymarketBookFeed stopped")

    async def _ping_loop(self, ws) -> None:
        """Send PING every 10s as required by Polymarket."""
        while not self._stop_event.is_set():
            try:
                await ws.send("PING")
            except Exception:
                break
            await asyncio.sleep(_PING_INTERVAL_S)

    async def _subscribe_all(self, ws) -> None:
        """Send initial subscribe with ALL tracked tokens.

        Uses the initial subscribe format (type: "market" + initial_dump)
        which must be the first message on a new connection.
        """
        with self._sub_lock:
            all_tokens = list(self._subscriptions.keys())

        if not all_tokens:
            return

        # Single subscribe message with all tokens
        msg = {
            "assets_ids": all_tokens,
            "type": "market",
            "initial_dump": True,
            "level": 2,
            "custom_feature_enabled": False,
        }
        try:
            await ws.send(json.dumps(msg))
            logger.info("WS subscribed %d tokens", len(all_tokens))
        except Exception as exc:
            logger.error("WS subscribe failed: %s", exc)

    def _process_message(self, raw: str) -> None:
        """Parse Polymarket WS message, update _data."""
        # Handle PONG response to our PING
        if raw == "PONG":
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("WS invalid JSON: %s", str(raw)[:200])
            return

        # Messages can be a list of events or a single event
        events = msg if isinstance(msg, list) else [msg]

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("event_type", "")
            if event_type == "book":
                self._handle_book(event)
            elif event_type == "price_change":
                self._handle_price_change(event)
            elif event_type == "last_trade_price":
                self._handle_last_trade(event)
            # tick_size_change, best_bid_ask, new_market, market_resolved → ignore

    def _handle_book(self, event: dict) -> None:
        """Full order book snapshot — rebuild local book + compute metrics."""
        asset_id = event.get("asset_id", "")
        if not asset_id:
            return

        bids_raw = event.get("bids", [])
        asks_raw = event.get("asks", [])

        # Build local book: {price_str: size_float}
        bids = {}
        for b in bids_raw:
            try:
                price = b.get("price", "0")
                size = float(b.get("size", "0"))
                if size > 0:
                    bids[price] = size
            except (ValueError, TypeError):
                continue

        asks = {}
        for a in asks_raw:
            try:
                price = a.get("price", "0")
                size = float(a.get("size", "0"))
                if size > 0:
                    asks[price] = size
            except (ValueError, TypeError):
                continue

        self._books[asset_id] = {"bids": bids, "asks": asks}
        self._update_metrics(asset_id)

    def _handle_price_change(self, event: dict) -> None:
        """Delta update — apply changes to local book.

        price_change events contain best_bid/best_ask directly, plus
        individual price level updates. We apply level updates to our
        local book, then recompute metrics.
        """
        changes = event.get("price_changes", [])
        for change in changes:
            asset_id = change.get("asset_id", "")
            if not asset_id:
                continue

            # If we don't have a book for this asset, skip (will get snapshot on next sub)
            if asset_id not in self._books:
                continue

            # Apply the price level change
            try:
                price_str = change.get("price", "")
                size = float(change.get("size", "0"))
                side = change.get("side", "").upper()
            except (ValueError, TypeError):
                continue

            if not price_str or not side:
                continue

            book = self._books[asset_id]
            if side == "BUY":
                if size > 0:
                    book["bids"][price_str] = size
                else:
                    book["bids"].pop(price_str, None)
            elif side == "SELL":
                if size > 0:
                    book["asks"][price_str] = size
                else:
                    book["asks"].pop(price_str, None)

            self._update_metrics(asset_id)

    def _handle_last_trade(self, event: dict) -> None:
        """Store last trade price for a token."""
        asset_id = event.get("asset_id", "")
        if not asset_id:
            return
        try:
            price = float(event.get("price", "0"))
        except (ValueError, TypeError):
            return

        with self._lock:
            entry = self._data.get(asset_id)
            if entry:
                entry["last_trade"] = price
                entry["ts"] = time.time()  # refresh staleness

    def _update_metrics(self, asset_id: str) -> None:
        """Recompute mid/bid/ask/depth/imbalance from local book."""
        book = self._books.get(asset_id)
        if not book:
            return

        bids = book["bids"]  # {price_str: size_float}
        asks = book["asks"]

        # 2check fix: empty book = no real midpoint. Skip update to avoid
        # phantom mid=0.50 reaching trading logic. Staleness timer will
        # expire and consumers fall back to REST.
        if not bids and not asks:
            return
        # One-sided book: also skip — mid would be misleading
        if not bids or not asks:
            return

        bid_prices = [float(p) for p in bids]
        ask_prices = [float(p) for p in asks]
        best_bid = max(bid_prices)
        best_ask = min(ask_prices)

        mid = (best_bid + best_ask) / 2.0

        # Depth
        bid_depth = sum(bids.values())
        ask_depth = sum(asks.values())
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0

        now = time.time()

        with self._lock:
            existing = self._data.get(asset_id, {})
            self._data[asset_id] = {
                "mid": mid,
                "bid": best_bid,
                "ask": best_ask,
                "bid_depth": round(bid_depth, 2),
                "ask_depth": round(ask_depth, 2),
                "imbalance": round(imbalance, 4),
                "last_trade": existing.get("last_trade", 0.0),
                "ts": now,
            }
