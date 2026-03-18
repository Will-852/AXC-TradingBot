#!/usr/bin/env python3
"""
ws_manager.py — Binance BTC WebSocket → Redis Streams

永續進程（KeepAlive via LaunchAgent）。
接收 Binance Futures WebSocket 實時數據，normalize 後 XADD 入 Redis Streams。

Streams:
  market:klines  — kline close/update events (3m/15m/1h/4h)
  market:ticker  — miniTicker (~2s, live price for dashboard)

設計決策：
  - Combined stream = 1 connection, 5 subscriptions
  - 只 subscribe BTC — 減少 bandwidth + complexity
  - Auto-reconnect: exponential backoff (2^n, max 60s, jitter)
  - Binance 24h forced disconnect → graceful reconnect
  - Redis down → log warning, skip XADD, 唔 crash
  - websockets lib 自動處理 ping/pong
"""

import asyncio
import json
import logging
import logging.handlers
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

# ── 路徑設定 ─────────────────────────────────────
BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))

from scripts.shared_infra.redis_bus import (
    STREAM_KLINES,
    STREAM_TICKER,
    is_available as redis_available,
    xadd,
)
from scripts.shared_infra.telegram import send_telegram

# ── Config ───────────────────────────────────────
SYMBOL = "btcusdt"
KLINE_INTERVALS = ["3m", "15m", "1h", "4h"]
WS_BASE = "wss://fstream.binance.com/stream?streams="

# Build combined stream URL
_streams = [f"{SYMBOL}@kline_{i}" for i in KLINE_INTERVALS]
_streams.append(f"{SYMBOL}@miniTicker")
WS_URL = WS_BASE + "/".join(_streams)

# Reconnect
MAX_RECONNECT_DELAY = 60  # seconds
RECONNECT_ALERT_THRESHOLD = 3  # alert after N consecutive failures

# Heartbeat
HEARTBEAT_PATH = LOGS_DIR / "ws_heartbeat.txt"
HEARTBEAT_INTERVAL = 30  # seconds

# Stats
STATS_LOG_INTERVAL = 300  # log stats every 5 min

# ── Logging ──────────────────────────────────────
logger = logging.getLogger("ws_manager")
logger.setLevel(logging.INFO)

_handler = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "ws_manager.log",
    maxBytes=5_000_000,
    backupCount=3,
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_handler)

# Also log to stderr for launchd capture
_stderr = logging.StreamHandler(sys.stderr)
_stderr.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
logger.addHandler(_stderr)

# ── State ────────────────────────────────────────
_shutdown = False
_stats = {
    "klines_received": 0,
    "klines_closed": 0,
    "tickers_received": 0,
    "redis_writes": 0,
    "redis_failures": 0,
    "reconnects": 0,
    "started_at": time.monotonic(),
}


# ── Signal handling ──────────────────────────────
def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down gracefully", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Normalize ────────────────────────────────────
def _normalize_kline(data: dict) -> dict:
    """Normalize Binance kline event → flat dict for Redis."""
    k = data["k"]
    return {
        "symbol": k["s"],
        "interval": k["i"],
        "o": k["o"],
        "h": k["h"],
        "l": k["l"],
        "c": k["c"],
        "v": k["v"],
        "q": k["q"],
        "n": str(k["n"]),  # trade count
        "is_closed": "1" if k["x"] else "0",
        "open_time": str(k["t"]),
        "close_time": str(k["T"]),
        "ts": str(data["E"]),  # event time
    }


def _normalize_ticker(data: dict) -> dict:
    """Normalize Binance miniTicker → flat dict for Redis."""
    return {
        "symbol": data["s"],
        "price": data["c"],  # close = last price
        "open_24h": data["o"],
        "high_24h": data["h"],
        "low_24h": data["l"],
        "volume": data["v"],
        "quote_volume": data["q"],
        "ts": str(data["E"]),
    }


# ── Core loop ────────────────────────────────────
async def _write_heartbeat():
    """Write heartbeat file periodically."""
    while not _shutdown:
        try:
            uptime = time.monotonic() - _stats["started_at"]
            line = (
                f"{datetime.now(timezone.utc).isoformat()} "
                f"up={uptime:.0f}s "
                f"klines={_stats['klines_closed']} "
                f"tickers={_stats['tickers_received']} "
                f"redis_ok={_stats['redis_writes']} "
                f"redis_fail={_stats['redis_failures']} "
                f"reconnects={_stats['reconnects']}\n"
            )
            HEARTBEAT_PATH.write_text(line)
        except OSError:
            pass
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def _log_stats():
    """Log stats periodically."""
    while not _shutdown:
        await asyncio.sleep(STATS_LOG_INTERVAL)
        logger.info(
            "stats: klines=%d closed=%d tickers=%d redis_ok=%d redis_fail=%d reconnects=%d",
            _stats["klines_received"],
            _stats["klines_closed"],
            _stats["tickers_received"],
            _stats["redis_writes"],
            _stats["redis_failures"],
            _stats["reconnects"],
        )


async def _process_message(raw: str) -> None:
    """Parse combined stream message, normalize, write to Redis."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON: %s", raw[:200])
        return

    stream_name = msg.get("stream", "")
    data = msg.get("data")
    if not data:
        return

    if "kline" in stream_name:
        normalized = _normalize_kline(data)
        _stats["klines_received"] += 1

        if normalized["is_closed"] == "1":
            _stats["klines_closed"] += 1
            logger.info(
                "kline CLOSED: %s %s c=%s v=%s",
                normalized["symbol"],
                normalized["interval"],
                normalized["c"],
                normalized["v"],
            )

        eid = xadd(STREAM_KLINES, normalized)
        if eid:
            _stats["redis_writes"] += 1
        else:
            _stats["redis_failures"] += 1

    elif "miniTicker" in stream_name:
        normalized = _normalize_ticker(data)
        _stats["tickers_received"] += 1

        eid = xadd(STREAM_TICKER, normalized)
        if eid:
            _stats["redis_writes"] += 1
        else:
            _stats["redis_failures"] += 1


async def _ws_loop() -> None:
    """Main WebSocket loop with auto-reconnect."""
    consecutive_failures = 0
    alert_sent = False

    while not _shutdown:
        try:
            logger.info("Connecting to %s", WS_URL[:80] + "...")

            # Check Redis before connecting
            if not redis_available():
                logger.warning("Redis unavailable — will connect WS but skip writes")

            async with websockets.connect(
                WS_URL,
                ping_interval=180,   # send ping every 3min (match Binance server)
                ping_timeout=600,    # 10min timeout (Binance spec)
                close_timeout=10,
                max_size=1_048_576,  # 1MB max message
            ) as ws:
                logger.info("Connected! Subscriptions: %s", ", ".join(_streams))
                consecutive_failures = 0
                alert_sent = False

                # Schedule 24h reconnect (Binance forces disconnect)
                reconnect_at = time.monotonic() + 23 * 3600  # reconnect at 23h to be safe
                planned_reconnect = False

                async for raw in ws:
                    if _shutdown:
                        break
                    await _process_message(raw)

                    # 24h preemptive reconnect
                    if time.monotonic() > reconnect_at:
                        logger.info("24h preemptive reconnect")
                        planned_reconnect = True
                        break

                if planned_reconnect:
                    continue  # skip backoff, reconnect immediately

        except websockets.ConnectionClosed as exc:
            logger.warning("WS connection closed: code=%s reason=%s", exc.code, exc.reason)
        except (OSError, websockets.WebSocketException) as exc:
            logger.error("WS error: %s", exc)
        except Exception as exc:
            logger.error("Unexpected WS error: %s: %s", type(exc).__name__, exc)

        if _shutdown:
            break

        # Reconnect with exponential backoff + jitter
        consecutive_failures += 1
        _stats["reconnects"] += 1
        delay = min(2 ** consecutive_failures, MAX_RECONNECT_DELAY)
        delay += random.uniform(0, delay * 0.1)  # 10% jitter

        logger.info("Reconnecting in %.1fs (attempt %d)", delay, consecutive_failures)

        # Alert after N consecutive failures
        if consecutive_failures >= RECONNECT_ALERT_THRESHOLD and not alert_sent:
            alert_msg = (
                f"⚠️ <b>WS Manager</b>: {consecutive_failures} consecutive reconnect failures\n"
                f"Last error logged. Auto-retrying."
            )
            send_telegram(alert_msg)
            alert_sent = True
            logger.warning("Telegram alert sent: %d consecutive failures", consecutive_failures)

        await asyncio.sleep(delay)


async def main():
    """Entry point — run WS loop + heartbeat + stats logger."""
    logger.info("ws_manager starting — symbol=%s intervals=%s", SYMBOL, KLINE_INTERVALS)
    logger.info("URL: %s", WS_URL)

    tasks = [
        asyncio.create_task(_ws_loop()),
        asyncio.create_task(_write_heartbeat()),
        asyncio.create_task(_log_stats()),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        logger.info("ws_manager stopped")


if __name__ == "__main__":
    asyncio.run(main())
