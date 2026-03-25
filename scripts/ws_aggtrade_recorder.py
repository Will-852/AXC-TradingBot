#!/usr/bin/env python3
"""
ws_aggtrade_recorder.py — Persist Binance aggTrades to CSV via WebSocket.

Subscribes to @aggTrade streams for configured symbols.
Writes to backtest/data/aggtrades/{SYMBOL}_{YYYYMMDD}_agg.live.csv.
At UTC midnight: renames .live.csv → .csv (becomes cache for fetch_agg_trades_day).
Cleans up files older than AGG_RETENTION_DAYS.

LaunchAgent: ai.openclaw.aggtrades
"""

import asyncio
import csv
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)

AGG_DATA_DIR = os.path.join(AXC_HOME, "backtest", "data", "aggtrades")
LOG_DIR = os.path.join(AXC_HOME, "logs")
HEARTBEAT_FILE = os.path.join(LOG_DIR, "ws_aggtrade_heartbeat.txt")

# Config
SYMBOLS = ["BTCUSDT", "ETHUSDT"]  # symbols to record
FLUSH_INTERVAL_S = 5  # flush buffer to disk every N seconds
FLUSH_MAX_TRADES = 1000  # or when buffer reaches N trades
AGG_RETENTION_DAYS = 30  # delete CSVs older than this
RECONNECT_BASE_S = 1
RECONNECT_MAX_S = 30
BINANCE_WS_URL = "wss://fstream.binance.com/stream?streams={streams}"

os.makedirs(AGG_DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Logging
log = logging.getLogger("aggtrade_recorder")
log.setLevel(logging.INFO)
_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "ws_aggtrade_recorder.log"),
    maxBytes=5 * 1024 * 1024, backupCount=3,
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)
# Also log to stderr for LaunchAgent
log.addHandler(logging.StreamHandler())

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    log.info("Signal %s received, shutting down", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _live_path(symbol: str, day_str: str) -> str:
    return os.path.join(AGG_DATA_DIR, f"{symbol}_{day_str}_agg.live.csv")


def _cache_path(symbol: str, day_str: str) -> str:
    return os.path.join(AGG_DATA_DIR, f"{symbol}_{day_str}_agg.csv")


def _cleanup_old_files():
    """Delete aggtrades CSVs older than AGG_RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=AGG_RETENTION_DAYS)
    cutoff_str = cutoff.strftime("%Y%m%d")
    deleted = 0
    for fname in os.listdir(AGG_DATA_DIR):
        if not fname.endswith(".csv"):
            continue
        # Extract date from filename: SYMBOL_YYYYMMDD_agg*.csv
        parts = fname.split("_")
        if len(parts) >= 2 and len(parts[1]) == 8 and parts[1].isdigit():
            if parts[1] < cutoff_str:
                try:
                    os.remove(os.path.join(AGG_DATA_DIR, fname))
                    deleted += 1
                except OSError:
                    pass
    if deleted:
        log.info("Cleaned up %d old aggtrades files (>%d days)", deleted, AGG_RETENTION_DAYS)


class TradeBuffer:
    """Buffer trades per symbol, flush to CSV periodically."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.buffer = []
        self.current_day = ""
        self.file_path = ""
        self.trade_count = 0
        self._ensure_day(datetime.now(timezone.utc).strftime("%Y%m%d"))

    def _ensure_day(self, day_str: str):
        """Handle day rollover: rename .live.csv → .csv, start new file."""
        if day_str == self.current_day:
            return
        # Finalize previous day
        if self.current_day:
            self.flush()
            old_live = _live_path(self.symbol, self.current_day)
            old_cache = _cache_path(self.symbol, self.current_day)
            if os.path.exists(old_live) and not os.path.exists(old_cache):
                try:
                    os.rename(old_live, old_cache)
                    log.info("Day rollover: %s → %s (%d trades)",
                             os.path.basename(old_live), os.path.basename(old_cache),
                             self.trade_count)
                except OSError as e:
                    log.warning("Rename failed: %s", e)

        self.current_day = day_str
        self.file_path = _live_path(self.symbol, day_str)
        self.trade_count = 0

        # Write CSV header if file doesn't exist
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["agg_id", "price", "qty", "timestamp", "is_buyer_maker"])

    def add(self, trade: dict):
        """Add a trade to the buffer. Check for day rollover."""
        ts_ms = trade["timestamp"]
        day_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
        self._ensure_day(day_str)
        self.buffer.append(trade)

    def flush(self):
        """Write buffered trades to CSV (append mode)."""
        if not self.buffer:
            return
        try:
            with open(self.file_path, "a", newline="") as f:
                writer = csv.writer(f)
                for t in self.buffer:
                    writer.writerow([
                        t["agg_id"], t["price"], t["qty"],
                        t["timestamp"], t["is_buyer_maker"],
                    ])
            self.trade_count += len(self.buffer)
            self.buffer.clear()
        except OSError as e:
            log.error("Flush failed for %s: %s", self.file_path, e)

    @property
    def needs_flush(self):
        return len(self.buffer) >= FLUSH_MAX_TRADES


async def _run_ws():
    """Main WebSocket loop with auto-reconnect."""
    try:
        import websockets
    except ImportError:
        log.error("websockets not installed: pip install websockets")
        return

    streams = "/".join(f"{s.lower()}@aggTrade" for s in SYMBOLS)
    url = BINANCE_WS_URL.format(streams=streams)
    buffers = {s: TradeBuffer(s) for s in SYMBOLS}
    reconnect_delay = RECONNECT_BASE_S

    while not _shutdown:
        try:
            log.info("Connecting to Binance aggTrade WS (%d symbols)", len(SYMBOLS))
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                reconnect_delay = RECONNECT_BASE_S
                log.info("WS connected")
                last_flush = time.monotonic()
                last_heartbeat = 0

                while not _shutdown:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        continue

                    import json
                    data = json.loads(msg)
                    # Combined stream format: {"stream": "btcusdt@aggTrade", "data": {...}}
                    trade_data = data.get("data", data)
                    symbol = trade_data.get("s", "").upper()

                    if symbol not in buffers:
                        continue

                    buffers[symbol].add({
                        "agg_id": trade_data["a"],
                        "price": float(trade_data["p"]),
                        "qty": float(trade_data["q"]),
                        "timestamp": int(trade_data["T"]),
                        "is_buyer_maker": trade_data["m"],
                    })

                    # Flush check
                    now = time.monotonic()
                    needs_flush = (
                        now - last_flush >= FLUSH_INTERVAL_S
                        or any(b.needs_flush for b in buffers.values())
                    )
                    if needs_flush:
                        for b in buffers.values():
                            b.flush()
                        last_flush = now

                    # Heartbeat (every 60s)
                    if now - last_heartbeat >= 60:
                        total = sum(b.trade_count for b in buffers.values())
                        with open(HEARTBEAT_FILE, "w") as hb:
                            hb.write(f"{datetime.now(timezone.utc).isoformat()}\n"
                                     f"trades_today={total}\n"
                                     f"symbols={','.join(SYMBOLS)}\n")
                        last_heartbeat = now

        except Exception as e:
            # Flush before reconnect
            for b in buffers.values():
                b.flush()
            if _shutdown:
                break
            log.warning("WS error: %s, reconnecting in %ds", e, reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, RECONNECT_MAX_S)

    # Final flush
    for b in buffers.values():
        b.flush()
    log.info("Shutdown complete. Total trades: %s",
             {s: b.trade_count for s, b in buffers.items()})


def main():
    log.info("Starting aggTrade recorder for %s", SYMBOLS)
    _cleanup_old_files()
    asyncio.run(_run_ws())


if __name__ == "__main__":
    main()
