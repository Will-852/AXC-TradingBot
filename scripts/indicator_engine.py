#!/usr/bin/env python3
"""
indicator_engine.py — Real-time indicator consumer

永續進程（KeepAlive via LaunchAgent）。
訂閱 Redis market:klines → on kline close → calc_indicators → write cache。

生命週期:
  1. Cold start: REST backfill 200 klines × 4 TF → initial indicator state
  2. Subscribe Redis consumer group → incremental mode
  3. Each kline close → append to rolling DataFrame → recalc → write cache
  4. Fallback: Redis/WS down → REST fetch every 180s

角色: Data processor ONLY。唔做 decision，唔落單，唔 send Telegram（除 health alert）。

Output: shared/indicator_cache.json
  Schema = EXACT match calc_indicators() 34 fields + volume_ratio + _meta + _macro
"""

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── 路徑設定 ─────────────────────────────────────
BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SHARED_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))

from scripts.indicator_calc import (
    calc_indicators,
    fetch_klines,
    TIMEFRAME_PARAMS,
    PRODUCT_OVERRIDES,
)
from scripts.shared_infra.redis_bus import (
    STREAM_KLINES,
    ensure_group,
    is_available as redis_available,
    xreadgroup,
    xack,
)
from scripts.shared_infra.telegram import send_telegram

# ── Config ───────────────────────────────────────
SYMBOL = "BTCUSDT"
TIMEFRAMES = ["3m", "15m", "1h", "4h"]
KLINE_LIMIT = 200       # backfill candles per TF
MAX_ROWS = 300           # rolling DataFrame max rows (trim oldest)
CACHE_PATH = SHARED_DIR / "indicator_cache.json"
HEARTBEAT_PATH = LOGS_DIR / "indicator_engine_heartbeat.txt"

# Consumer group
GROUP = "indicators"
CONSUMER = "indicator-1"

# Fallback: REST poll interval when Redis/WS unavailable
FALLBACK_INTERVAL = 180  # 3 minutes

# Cache staleness: trader_cycle considers cache stale after this
CACHE_MAX_AGE = 600  # 10 minutes

# ── Logging ──────────────────────────────────────
logger = logging.getLogger("indicator_engine")
logger.setLevel(logging.INFO)

_handler = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "indicator_engine.log",
    maxBytes=5_000_000,
    backupCount=3,
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_handler)

_stderr = logging.StreamHandler(sys.stderr)
_stderr.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
logger.addHandler(_stderr)

# ── State ────────────────────────────────────────
_shutdown = False
_dataframes: dict[str, pd.DataFrame] = {}  # {timeframe: DataFrame}
_indicators: dict[str, dict] = {}           # {timeframe: {34 fields + volume_ratio}}
_macro: dict = {}                           # Fib/MACD divergence/MA trend
_started_at = time.monotonic()
_stats = {
    "backfill_done": False,
    "klines_processed": 0,
    "cache_writes": 0,
    "fallback_fetches": 0,
    "errors": 0,
}


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Helpers ──────────────────────────────────────

def _get_params(timeframe: str) -> dict:
    """Get indicator params for timeframe, with product overrides."""
    params = TIMEFRAME_PARAMS[timeframe].copy()
    if SYMBOL in PRODUCT_OVERRIDES:
        params.update(PRODUCT_OVERRIDES[SYMBOL])
    return params


def _calc_volume_ratio(df: pd.DataFrame) -> float:
    """volume_ratio = last candle volume / 30-candle avg. Matches market_data.py:160-167."""
    if len(df) >= 30:
        avg_vol = df["volume"].tail(30).mean()
        current_vol = df["volume"].iloc[-1]
        return round(current_vol / avg_vol, 6) if avg_vol > 0 else 1.0
    return 1.0


def _calc_indicators_for_tf(timeframe: str) -> dict | None:
    """Calculate all 34 indicators + volume_ratio for one timeframe."""
    df = _dataframes.get(timeframe)
    if df is None or len(df) < 20:
        logger.warning("Insufficient data for %s: %d rows", timeframe, len(df) if df is not None else 0)
        return None
    try:
        params = _get_params(timeframe)
        result = calc_indicators(df, params)
        result["volume_ratio"] = _calc_volume_ratio(df)
        return result
    except Exception as exc:
        logger.error("calc_indicators failed for %s: %s", timeframe, exc)
        _stats["errors"] += 1
        return None


def _calc_macro() -> dict:
    """
    Macro S/R: Fibonacci + MACD divergence + MA trend.
    Runs on 4H data. Updates _macro.
    """
    df = _dataframes.get("4h")
    ind = _indicators.get("4h")
    if df is None or ind is None or len(df) < 30:
        return _macro

    result = {}

    # Fibonacci from rolling high/low
    rolling_high = ind.get("rolling_high")
    rolling_low = ind.get("rolling_low")
    if rolling_high and rolling_low and rolling_high > rolling_low:
        diff = rolling_high - rolling_low
        result["fib_swing_high"] = rolling_high
        result["fib_swing_low"] = rolling_low
        result["fib_levels"] = [
            round(rolling_low + diff * r, 2)
            for r in [0.236, 0.382, 0.5, 0.618, 0.786]
        ]
    else:
        result["fib_swing_high"] = rolling_high
        result["fib_swing_low"] = rolling_low
        result["fib_levels"] = []

    # MACD divergence (4-bar comparison)
    try:
        prices = df["close"].tail(5).values
        macd_hist = []
        # Get last 5 MACD histograms from DataFrame recalc
        close = df["close"]
        from scripts.indicator_calc import MACD_FAST, MACD_SLOW, MACD_SIGNAL
        import tradingview_indicators as tv
        macd_obj = tv.MACD(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        hist_series = macd_obj.macd_histogram
        if hist_series is not None and len(hist_series) >= 5:
            macd_hist = hist_series.tail(5).values

            price_rising = prices[-1] > prices[0]
            macd_rising = macd_hist[-1] > macd_hist[0]

            if price_rising and not macd_rising:
                result["macd_divergence"] = "bearish"
            elif not price_rising and macd_rising:
                result["macd_divergence"] = "bullish"
            else:
                result["macd_divergence"] = "none"
        else:
            result["macd_divergence"] = "none"
    except Exception:
        result["macd_divergence"] = "none"

    # MA trend
    price = ind.get("price")
    ma50 = ind.get("ma50")
    ma200 = ind.get("ma200")
    if price and ma50 and ma200:
        if price > ma50 > ma200:
            result["ma_trend"] = "bullish"
        elif price < ma50 < ma200:
            result["ma_trend"] = "bearish"
        else:
            result["ma_trend"] = "neutral"
    else:
        result["ma_trend"] = "neutral"

    # Previous day H/L/C (from 4H: aggregate last 6 candles = 24h)
    if len(df) >= 6:
        day_slice = df.tail(6)
        result["prev_day_high"] = round(float(day_slice["high"].max()), 2)
        result["prev_day_low"] = round(float(day_slice["low"].min()), 2)
        result["prev_day_close"] = round(float(day_slice["close"].iloc[-1]), 2)

    return result


def _write_cache() -> None:
    """Atomic write indicator_cache.json. Same pattern as existing AXC code."""
    cache = {
        SYMBOL: _indicators.copy(),
        "_meta": {
            "last_update": datetime.now(timezone.utc).isoformat(),
            "source": "ws" if _stats["backfill_done"] and redis_available() else "rest_fallback",
            "ws_connected": redis_available(),
            "engine_uptime_s": round(time.monotonic() - _started_at),
        },
        "_macro": _macro,
    }
    try:
        fd, tmp = tempfile.mkstemp(dir=str(SHARED_DIR), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f, indent=2, default=str)
        os.replace(tmp, str(CACHE_PATH))
        _stats["cache_writes"] += 1
    except OSError as exc:
        logger.error("Cache write failed: %s", exc)
        _stats["errors"] += 1


def _write_heartbeat() -> None:
    """Write heartbeat file."""
    try:
        uptime = time.monotonic() - _started_at
        line = (
            f"{datetime.now(timezone.utc).isoformat()} "
            f"up={uptime:.0f}s "
            f"backfill={'Y' if _stats['backfill_done'] else 'N'} "
            f"klines={_stats['klines_processed']} "
            f"cache_writes={_stats['cache_writes']} "
            f"fallbacks={_stats['fallback_fetches']} "
            f"errors={_stats['errors']}\n"
        )
        HEARTBEAT_PATH.write_text(line)
    except OSError:
        pass


# ── Cold start: REST backfill ────────────────────

def _backfill() -> bool:
    """
    REST fetch 200 klines × 4 TF → build initial indicator state.
    Returns True on success (at least primary TFs loaded).
    """
    logger.info("Cold start: backfilling %d klines × %d timeframes", KLINE_LIMIT, len(TIMEFRAMES))
    success = 0

    for tf in TIMEFRAMES:
        try:
            df = fetch_klines(SYMBOL, tf, KLINE_LIMIT, platform="binance")
            if df is None or len(df) < 20:
                logger.warning("Backfill %s: insufficient data (%d rows)", tf, len(df) if df is not None else 0)
                continue

            _dataframes[tf] = df
            ind = _calc_indicators_for_tf(tf)
            if ind:
                _indicators[tf] = ind
                success += 1
                logger.info("Backfill %s: %d rows, price=%s", tf, len(df), ind.get("price"))
            else:
                logger.warning("Backfill %s: calc_indicators returned None", tf)
        except Exception as exc:
            logger.error("Backfill %s failed: %s", tf, exc)
            _stats["errors"] += 1

    if "4h" in _indicators and "1h" in _indicators:  # primary TFs required
        # Compute macro on initial data
        global _macro
        _macro = _calc_macro()
        _write_cache()
        logger.info("Backfill complete: %d/%d timeframes, macro=%s",
                     success, len(TIMEFRAMES), _macro.get("ma_trend", "?"))
        return True
    else:
        logger.error("Backfill failed: only %d/%d timeframes loaded", success, len(TIMEFRAMES))
        return False


# ── Kline event processing ───────────────────────

def _append_kline_to_df(tf: str, kline: dict) -> None:
    """Append a closed kline to the rolling DataFrame for this timeframe."""
    new_row = {
        "open_time": int(kline["open_time"]),
        "open": float(kline["o"]),
        "high": float(kline["h"]),
        "low": float(kline["l"]),
        "close": float(kline["c"]),
        "volume": float(kline["v"]),
        "close_time": int(kline["close_time"]),
        "quote_volume": float(kline.get("q", 0)),
        "trades": int(kline.get("n", 0)),
        "taker_buy_volume": 0.0,
        "taker_buy_quote_volume": 0.0,
        "ignore": 0,
        "timestamp": pd.to_datetime(int(kline["open_time"]), unit="ms"),
    }

    df = _dataframes.get(tf)
    if df is None:
        # No backfill for this TF — create from scratch (will be short)
        _dataframes[tf] = pd.DataFrame([new_row])
        return

    new_df = pd.DataFrame([new_row])
    _dataframes[tf] = pd.concat([df, new_df], ignore_index=True)

    # Trim to MAX_ROWS
    if len(_dataframes[tf]) > MAX_ROWS:
        _dataframes[tf] = _dataframes[tf].tail(MAX_ROWS).reset_index(drop=True)


def _process_kline_close(tf: str, kline: dict) -> None:
    """Process a closed kline: append, recalc, update cache."""
    _append_kline_to_df(tf, kline)

    # Determine what to recalc based on timeframe hierarchy
    recalc_tfs = [tf]
    if tf == "15m":
        recalc_tfs = TIMEFRAMES  # 15m close → recalc all
    elif tf == "1h":
        recalc_tfs = TIMEFRAMES  # 1h close → recalc all + S/R check
    elif tf == "4h":
        recalc_tfs = TIMEFRAMES  # 4h close → recalc all + macro

    for rtf in recalc_tfs:
        ind = _calc_indicators_for_tf(rtf)
        if ind:
            _indicators[rtf] = ind

    # Macro update on 4H or 1H close
    if tf in ("4h", "1h"):
        global _macro
        _macro = _calc_macro()

    _stats["klines_processed"] += 1
    _write_cache()

    logger.info(
        "Processed %s %s close: price=%s rsi=%s recalc=%s",
        kline.get("symbol", SYMBOL), tf,
        _indicators.get(tf, {}).get("price"),
        _indicators.get(tf, {}).get("rsi"),
        recalc_tfs,
    )


# ── Main loops ───────────────────────────────────

async def _redis_consumer_loop() -> None:
    """Subscribe to market:klines consumer group. Process closed klines."""
    if not ensure_group(STREAM_KLINES, GROUP):
        logger.error("Cannot create consumer group — entering fallback mode")
        return

    logger.info("Redis consumer started: group=%s consumer=%s", GROUP, CONSUMER)
    consecutive_empty = 0

    while not _shutdown:
        entries = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: xreadgroup(GROUP, CONSUMER, STREAM_KLINES, count=50, block_ms=3000),
        )

        if not entries:
            consecutive_empty += 1
            # If Redis seems dead for >30s, break to fallback
            if consecutive_empty > 10:  # 10 × 3s block = 30s
                logger.warning("No data from Redis for 30s — checking availability")
                if not redis_available():
                    logger.warning("Redis unavailable — switching to REST fallback")
                    return
                consecutive_empty = 0  # Redis alive, just no data (WS might be down)
            continue

        consecutive_empty = 0

        for entry_id, fields in entries:
            is_closed = fields.get("is_closed", "0") == "1"
            interval = fields.get("interval", "")

            if is_closed and interval in TIMEFRAMES:
                _process_kline_close(interval, fields)

            # ACK regardless (we don't want to re-process open klines)
            xack(STREAM_KLINES, GROUP, entry_id)


async def _fallback_loop() -> None:
    """REST fallback: fetch klines every 180s when Redis/WS unavailable."""
    logger.info("Fallback mode: REST fetch every %ds", FALLBACK_INTERVAL)

    while not _shutdown:
        # Check if Redis came back
        if redis_available():
            logger.info("Redis recovered — switching back to consumer mode")
            return

        for tf in TIMEFRAMES:
            try:
                df = fetch_klines(SYMBOL, tf, KLINE_LIMIT, platform="binance")
                if df is not None and len(df) >= 20:
                    _dataframes[tf] = df
                    ind = _calc_indicators_for_tf(tf)
                    if ind:
                        _indicators[tf] = ind
            except Exception as exc:
                logger.error("Fallback fetch %s failed: %s", tf, exc)
                _stats["errors"] += 1

        global _macro
        _macro = _calc_macro()
        _write_cache()
        _stats["fallback_fetches"] += 1
        logger.info("Fallback cycle done: price=%s", _indicators.get("4h", {}).get("price"))

        # Wait FALLBACK_INTERVAL, checking for Redis recovery every 30s
        for _ in range(FALLBACK_INTERVAL // 30):
            if _shutdown:
                return
            await asyncio.sleep(30)
            if redis_available():
                logger.info("Redis recovered during fallback wait")
                return
        await asyncio.sleep(FALLBACK_INTERVAL % 30)


async def _heartbeat_loop() -> None:
    """Write heartbeat every 30s."""
    while not _shutdown:
        _write_heartbeat()
        await asyncio.sleep(30)


async def _stats_loop() -> None:
    """Log stats every 5 min."""
    while not _shutdown:
        await asyncio.sleep(300)
        logger.info(
            "stats: klines=%d cache=%d fallbacks=%d errors=%d up=%ds",
            _stats["klines_processed"],
            _stats["cache_writes"],
            _stats["fallback_fetches"],
            _stats["errors"],
            round(time.monotonic() - _started_at),
        )


async def main():
    """Entry point."""
    logger.info("indicator_engine starting — symbol=%s timeframes=%s", SYMBOL, TIMEFRAMES)

    # Step 1: Cold start backfill (always, regardless of Redis)
    backfill_ok = await asyncio.get_running_loop().run_in_executor(None, _backfill)
    _stats["backfill_done"] = backfill_ok

    if not backfill_ok:
        logger.error("Backfill failed — retrying in 60s")
        await asyncio.sleep(60)
        backfill_ok = await asyncio.get_running_loop().run_in_executor(None, _backfill)
        _stats["backfill_done"] = backfill_ok
        if not backfill_ok:
            send_telegram("🔴 <b>Indicator Engine</b>: backfill failed after retry. Running degraded.")

    # Step 2: Start background tasks
    bg_tasks = [
        asyncio.create_task(_heartbeat_loop()),
        asyncio.create_task(_stats_loop()),
    ]

    # Step 3: Main loop — switch between Redis consumer and REST fallback
    while not _shutdown:
        if redis_available():
            logger.info("Entering Redis consumer mode")
            await _redis_consumer_loop()
        else:
            logger.info("Entering REST fallback mode")
            await _fallback_loop()

        if not _shutdown:
            await asyncio.sleep(5)  # brief pause before mode switch

    # Cleanup
    for t in bg_tasks:
        t.cancel()
    logger.info("indicator_engine stopped")


if __name__ == "__main__":
    asyncio.run(main())
