#!/usr/bin/env python3
"""
ws_binance.py — Binance WebSocket Price Feed (in-process, thread-safe)

Connects to Binance spot bookTicker streams for BTC/ETH/SOL.
Stores latest bid/ask/mid in a thread-safe dict.
Runs in a daemon thread with its own asyncio event loop.

Design decisions:
  - bookTicker = fastest price update (~10ms, no aggregation delay)
  - Daemon thread so it dies with the parent process
  - Exponential backoff: 1s→2s→4s→8s→…→30s (with 10% jitter)
  - Preemptive reconnect at 23h (Binance forces disconnect at 24h)
  - Fully optional: if WS fails, callers use existing REST fallback
  - No Redis, no external deps beyond websockets
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
_SYMBOLS = ["btcusdt", "ethusdt", "solusdt"]
_WS_URL = (
    "wss://stream.binance.com:9443/stream?streams="
    + "/".join(f"{s}@bookTicker" for s in _SYMBOLS)
)

_MAX_RECONNECT_DELAY = 30       # seconds
_PREEMPTIVE_RECONNECT_H = 23    # reconnect before 24h forced disconnect
_DEFAULT_MAX_AGE = 5.0           # seconds — stale threshold


class BinancePriceFeed:
    """Thread-safe Binance bookTicker price feed.

    Usage:
        feed = BinancePriceFeed()
        feed.start()          # non-blocking, spawns daemon thread
        price = feed.get_price("BTCUSDT")   # float | None
        bid, ask = feed.get_bbo("BTCUSDT")  # tuple | None
        feed.stop()
    """

    def __init__(self, max_age: float = _DEFAULT_MAX_AGE):
        self._max_age = max_age
        # {symbol: {"mid": float, "bid": float, "ask": float, "ts": float}}
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── Public API ───────────────────────────────

    def start(self) -> None:
        """Start the WS feed in a daemon thread. Non-blocking."""
        if self._thread and self._thread.is_alive():
            logger.warning("BinancePriceFeed already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="ws-binance", daemon=True
        )
        self._thread.start()
        logger.info("BinancePriceFeed started (daemon thread)")

    def stop(self) -> None:
        """Signal the feed to stop. Non-blocking."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("BinancePriceFeed stop requested")

    def get_price(self, symbol: str) -> float | None:
        """Return mid price if fresh (< max_age), else None."""
        with self._lock:
            entry = self._data.get(symbol.upper())
        if entry is None:
            return None
        age = time.time() - entry["ts"]
        if age > self._max_age:
            return None
        return entry["mid"]

    def get_bbo(self, symbol: str) -> tuple[float, float] | None:
        """Return (bid, ask) if fresh, else None."""
        with self._lock:
            entry = self._data.get(symbol.upper())
        if entry is None:
            return None
        age = time.time() - entry["ts"]
        if age > self._max_age:
            return None
        return (entry["bid"], entry["ask"])

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
                logger.error("BinancePriceFeed loop crashed: %s", exc)
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
        """Main WS loop with auto-reconnect (mirrors ws_manager.py pattern)."""
        consecutive_failures = 0

        while not self._stop_event.is_set():
            try:
                logger.info("WS connecting: %s", _WS_URL[:80] + "...")

                async with websockets.connect(
                    _WS_URL,
                    ping_interval=180,    # match Binance server (~3min)
                    ping_timeout=600,     # 10min (Binance spec)
                    close_timeout=10,
                    max_size=1_048_576,   # 1MB
                ) as ws:
                    logger.info("WS connected — streams: %s",
                                ", ".join(f"{s}@bookTicker" for s in _SYMBOLS))
                    consecutive_failures = 0

                    # Schedule 23h preemptive reconnect
                    reconnect_at = time.monotonic() + _PREEMPTIVE_RECONNECT_H * 3600
                    planned_reconnect = False

                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        self._process_message(raw)

                        # 24h preemptive reconnect
                        if time.monotonic() > reconnect_at:
                            logger.info("23h preemptive reconnect")
                            planned_reconnect = True
                            break

                    if planned_reconnect:
                        continue  # skip backoff, reconnect immediately

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

        logger.info("BinancePriceFeed stopped")

    def _process_message(self, raw: str) -> None:
        """Parse bookTicker combined-stream message, update _data."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("WS invalid JSON: %s", str(raw)[:200])
            return

        data = msg.get("data")
        if not data:
            return

        symbol = data.get("s", "")        # e.g. "BTCUSDT"
        bid_str = data.get("b", "0")      # best bid price
        ask_str = data.get("a", "0")      # best ask price

        try:
            bid = float(bid_str)
            ask = float(ask_str)
        except (ValueError, TypeError):
            return

        if bid <= 0 or ask <= 0:
            return

        mid = (bid + ask) / 2.0
        now = time.time()

        with self._lock:
            self._data[symbol] = {
                "mid": mid,
                "bid": bid,
                "ask": ask,
                "ts": now,
            }
