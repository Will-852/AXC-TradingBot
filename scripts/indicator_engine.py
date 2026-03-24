#!/usr/bin/env python3
"""
indicator_engine.py — Real-time multi-coin indicator consumer

永續進程（KeepAlive via LaunchAgent）。
訂閱 Redis market:klines → on kline close → calc_indicators → write cache。

2026-03-23: 擴展到多幣 (config/coins/ Binance exchange coins)。
BTC only → BTC + ETH + XRP + SOL + POL。

生命週期:
  1. Cold start: REST backfill 200 klines × N coins × 4 TF → initial indicator state
  2. Subscribe Redis consumer group → incremental mode
  3. Each kline close → append to rolling DataFrame → recalc → write cache
  4. Fallback: Redis/WS down → REST fetch every 180s

角色: Data processor ONLY。唔做 decision，唔落單，唔 send Telegram（除 health alert）。

Output: shared/indicator_cache.json
  Schema = {SYMBOL: {TF: indicators_dict}, ..., _meta: {...}, _macro: {...}}
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
from datetime import datetime, timezone
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
    STREAM_VOL_TRIGGER,
    ensure_group,
    is_available as redis_available,
    xreadgroup,
    xack,
    xadd,
)
from scripts.shared_infra.telegram import send_telegram

# ── Config ───────────────────────────────────────
TIMEFRAMES = ["3m", "15m", "1h", "4h"]
KLINE_LIMIT = 200       # backfill candles per TF
MAX_ROWS = 300           # rolling DataFrame max rows (trim oldest)
CACHE_PATH = SHARED_DIR / "indicator_cache.json"
HEARTBEAT_PATH = LOGS_DIR / "indicator_engine_heartbeat.txt"

# Coins to process — Binance exchange coins from config/coins/
try:
    from config.coins.loader import get_exchange_symbols, get_regime_anchor
    SYMBOLS = get_exchange_symbols("binance")
    REGIME_ANCHOR = get_regime_anchor()
except ImportError:
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "POLUSDT"]
    REGIME_ANCHOR = "BTCUSDT"

# Lowercase symbol set for fast lookup from Redis messages
_SYMBOLS_LOWER = {s.lower() for s in SYMBOLS}
_SYMBOL_MAP = {s.lower(): s for s in SYMBOLS}  # btcusdt → BTCUSDT

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

# ── State (per-symbol) ──────────────────────────
_shutdown = False
_dataframes: dict[str, dict[str, pd.DataFrame]] = {}   # {symbol: {tf: DataFrame}}
_indicators: dict[str, dict[str, dict]] = {}            # {symbol: {tf: {34 fields}}}
_macro: dict = {}                                        # BTC-primary Fib/MACD/MA
_started_at = time.monotonic()
_stats = {
    "backfill_done": False,
    "klines_processed": 0,
    "cache_writes": 0,
    "fallback_fetches": 0,
    "errors": 0,
}

# ── Volume trigger state ─────────────────────────
VOL_PROJECTION_THRESHOLD = 5.0    # projected vol_ratio > 5x → trigger
VOL_PROJECTION_MIN_ELAPSED = 0.30  # 至少 30% candle 過咗先計算（reduce noise）

# Per-symbol squeeze readiness (updated on every 1H close)
_squeeze_ready: dict[str, bool] = {}

# Per-symbol volume projection state (for open kline monitoring)
# {symbol: {open_time: int, triggered: bool}}
_vol_projection_state: dict[str, dict] = {}


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Helpers ──────────────────────────────────────

def _get_params(symbol: str, timeframe: str) -> dict:
    """Get indicator params for (symbol, timeframe), with per-coin overrides."""
    params = TIMEFRAME_PARAMS[timeframe].copy()
    # Per-coin overrides from config/coins/ via PRODUCT_OVERRIDES backward compat
    if symbol in PRODUCT_OVERRIDES:
        params.update(PRODUCT_OVERRIDES[symbol])
    return params


def _calc_volume_ratio(df: pd.DataFrame) -> float:
    """volume_ratio = last candle volume / 30-candle avg."""
    if len(df) >= 30:
        avg_vol = df["volume"].tail(30).mean()
        current_vol = df["volume"].iloc[-1]
        return round(current_vol / avg_vol, 6) if avg_vol > 0 else 1.0
    return 1.0


def _update_squeeze_ready(symbol: str) -> None:
    """Update squeeze readiness from latest 1H indicators.

    Squeeze ready = bb_width_pctl < 30 AND adx < 25.
    Called on every 1H kline close.
    """
    ind_1h = _indicators.get(symbol, {}).get("1h", {})
    bb_pctl = ind_1h.get("bb_width_pctl")
    adx = ind_1h.get("adx")

    volume_ratio = ind_1h.get("volume_ratio")
    ready = (
        (bb_pctl is not None and bb_pctl < 30.0)
        and (adx is None or adx < 25.0)
        and (volume_ratio is None or volume_ratio < 0.80)
    )
    prev = _squeeze_ready.get(symbol, False)
    _squeeze_ready[symbol] = ready

    if ready != prev:
        logger.info("Squeeze ready %s → %s for %s (pctl=%.1f adx=%s)",
                     prev, ready, symbol,
                     bb_pctl if bb_pctl is not None else -1,
                     f"{adx:.1f}" if adx is not None else "N/A")


def _monitor_volume_projection(symbol: str, kline: dict) -> None:
    """Monitor open kline volume for early spike detection.

    Called on every open (is_closed=0) 1H kline update.
    Projects full-candle volume ratio from partial data.
    If projected > threshold AND squeeze_ready → emit trigger.

    設計決定：
    - 只 monitor 1H（3m 太 noisy, 15m 可能漏, 4H 太慢）
    - elapsed < 30% 唔計算（early noise）
    - 每個 candle 每個 symbol 最多 trigger 1 次（debounce）
    """
    try:
        open_time = int(kline.get("open_time", 0))
        close_time = int(kline.get("close_time", 0))
        current_vol = float(kline.get("v", 0))
    except (ValueError, TypeError):
        return

    if close_time <= open_time or current_vol <= 0:
        return

    # Debounce: check if already triggered for this candle
    state = _vol_projection_state.get(symbol, {})
    if state.get("open_time") == open_time and state.get("triggered"):
        return

    # Reset state if new candle
    if state.get("open_time") != open_time:
        _vol_projection_state[symbol] = {"open_time": open_time, "triggered": False}

    # Calculate elapsed percentage
    now_ms = int(time.time() * 1000)
    candle_duration = close_time - open_time
    if candle_duration <= 0:
        return
    elapsed_pct = min((now_ms - open_time) / candle_duration, 1.0)

    if elapsed_pct < VOL_PROJECTION_MIN_ELAPSED:
        return  # Too early — projection unreliable

    # Get avg volume from rolling DataFrame
    sym_dfs = _dataframes.get(symbol, {})
    df_1h = sym_dfs.get("1h")
    if df_1h is None or len(df_1h) < 30:
        return
    avg_vol = df_1h["volume"].tail(30).mean()
    if avg_vol <= 0:
        return

    # Project full-candle volume ratio
    projected = (current_vol / elapsed_pct) / avg_vol

    if projected < VOL_PROJECTION_THRESHOLD:
        return

    # Check squeeze readiness
    if not _squeeze_ready.get(symbol, False):
        return

    # ── Trigger! ──
    _vol_projection_state[symbol]["triggered"] = True

    # Determine direction from price movement
    try:
        price = float(kline.get("c", 0))
        open_price = float(kline.get("o", 0))
        direction = "LONG" if price > open_price else "SHORT"
    except (ValueError, TypeError):
        direction = "UNKNOWN"

    trigger_data = {
        "symbol": symbol,
        "projected_ratio": f"{projected:.2f}",
        "elapsed_pct": f"{elapsed_pct:.2f}",
        "direction": direction,
        "squeeze_ready": "1",
        "ts": str(int(time.time())),
    }

    xadd(STREAM_VOL_TRIGGER, trigger_data)

    logger.info(
        "VOL TRIGGER %s: projected=%.1fx elapsed=%.0f%% dir=%s squeeze=ready",
        symbol, projected, elapsed_pct * 100, direction,
    )


def _calc_indicators_for(symbol: str, timeframe: str) -> dict | None:
    """Calculate all indicators for one (symbol, timeframe)."""
    sym_dfs = _dataframes.get(symbol, {})
    df = sym_dfs.get(timeframe)
    if df is None or len(df) < 20:
        return None
    try:
        params = _get_params(symbol, timeframe)
        result = calc_indicators(df, params)
        result["volume_ratio"] = _calc_volume_ratio(df)
        return result
    except Exception as exc:
        logger.error("calc_indicators failed for %s %s: %s", symbol, timeframe, exc)
        _stats["errors"] += 1
        return None


def _calc_macro() -> dict:
    """Macro S/R: Fibonacci + MACD divergence + MA trend (BTC-primary)."""
    sym = REGIME_ANCHOR
    df = _dataframes.get(sym, {}).get("4h")
    ind = _indicators.get(sym, {}).get("4h")
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
        from scripts.indicator_calc import MACD_FAST, MACD_SLOW, MACD_SIGNAL
        import tradingview_indicators as tv
        macd_obj = tv.MACD(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
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
    """Atomic write indicator_cache.json — all symbols."""
    cache = {}
    for sym in SYMBOLS:
        sym_ind = _indicators.get(sym, {})
        if sym_ind:
            cache[sym] = sym_ind.copy()

    cache["_meta"] = {
        "last_update": datetime.now(timezone.utc).isoformat(),
        "source": "ws" if _stats["backfill_done"] and redis_available() else "rest_fallback",
        "ws_connected": redis_available(),
        "engine_uptime_s": round(time.monotonic() - _started_at),
        "symbols": list(cache.keys()),
    }
    cache["_macro"] = _macro

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
        n_symbols = sum(1 for s in SYMBOLS if _indicators.get(s))
        line = (
            f"{datetime.now(timezone.utc).isoformat()} "
            f"up={uptime:.0f}s "
            f"coins={n_symbols}/{len(SYMBOLS)} "
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
    """REST fetch 200 klines × N coins × 4 TF → build initial indicator state."""
    logger.info(
        "Cold start: backfilling %d klines × %d symbols × %d timeframes",
        KLINE_LIMIT, len(SYMBOLS), len(TIMEFRAMES),
    )
    success_symbols = 0

    for symbol in SYMBOLS:
        _dataframes.setdefault(symbol, {})
        _indicators.setdefault(symbol, {})
        sym_success = 0

        # Determine platform for this symbol
        platform = "binance"  # All WS symbols are Binance

        for tf in TIMEFRAMES:
            try:
                # Throttle: 50ms between REST calls (20 calls × 50ms = 1s total)
                # Protects against burst weight spike if SYMBOLS grows
                time.sleep(0.05)
                df = fetch_klines(symbol, tf, KLINE_LIMIT, platform=platform)
                if df is None or len(df) < 20:
                    logger.warning("Backfill %s %s: insufficient data", symbol, tf)
                    continue

                _dataframes[symbol][tf] = df
                ind = _calc_indicators_for(symbol, tf)
                if ind:
                    _indicators[symbol][tf] = ind
                    sym_success += 1
            except Exception as exc:
                logger.error("Backfill %s %s failed: %s", symbol, tf, exc)
                _stats["errors"] += 1

        if sym_success >= 2:  # At least 4h + 1h
            success_symbols += 1
            price = _indicators.get(symbol, {}).get("4h", {}).get("price", "?")
            logger.info("Backfill %s: %d/%d TFs, price=%s", symbol, sym_success, len(TIMEFRAMES), price)
        else:
            logger.warning("Backfill %s: only %d/%d TFs", symbol, sym_success, len(TIMEFRAMES))

    # Require at least regime anchor (BTC) to have primary TFs
    anchor_ok = (
        "4h" in _indicators.get(REGIME_ANCHOR, {})
        and "1h" in _indicators.get(REGIME_ANCHOR, {})
    )

    if anchor_ok:
        global _macro
        _macro = _calc_macro()
        _write_cache()
        logger.info(
            "Backfill complete: %d/%d symbols ready, macro=%s",
            success_symbols, len(SYMBOLS), _macro.get("ma_trend", "?"),
        )
        return True
    else:
        logger.error("Backfill failed: regime anchor %s missing primary TFs", REGIME_ANCHOR)
        return False


# ── Kline event processing ───────────────────────

def _append_kline_to_df(symbol: str, tf: str, kline: dict) -> None:
    """Append a closed kline to the rolling DataFrame."""
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
        "taker_buy_volume": float(kline.get("V", 0)),
        "taker_buy_quote_volume": 0.0,
        "ignore": 0,
        "timestamp": pd.to_datetime(int(kline["open_time"]), unit="ms"),
    }

    sym_dfs = _dataframes.setdefault(symbol, {})
    df = sym_dfs.get(tf)

    if df is None:
        sym_dfs[tf] = pd.DataFrame([new_row])
        return

    new_df = pd.DataFrame([new_row])
    sym_dfs[tf] = pd.concat([df, new_df], ignore_index=True)

    # Trim to MAX_ROWS
    if len(sym_dfs[tf]) > MAX_ROWS:
        sym_dfs[tf] = sym_dfs[tf].tail(MAX_ROWS).reset_index(drop=True)


def _process_kline_close(symbol: str, tf: str, kline: dict) -> None:
    """Process a closed kline: append, recalc, update cache."""
    _append_kline_to_df(symbol, tf, kline)
    _indicators.setdefault(symbol, {})

    # Determine what to recalc based on timeframe hierarchy
    recalc_tfs = [tf]
    if tf in ("15m", "1h", "4h"):
        recalc_tfs = TIMEFRAMES  # Higher TF close → recalc all

    for rtf in recalc_tfs:
        ind = _calc_indicators_for(symbol, rtf)
        if ind:
            _indicators[symbol][rtf] = ind

    # Macro update on 4H or 1H close (regime anchor only)
    if symbol == REGIME_ANCHOR and tf in ("4h", "1h"):
        global _macro
        _macro = _calc_macro()

    # Update squeeze readiness on 1H close (for volume trigger)
    if tf == "1h":
        _update_squeeze_ready(symbol)

    _stats["klines_processed"] += 1
    _write_cache()

    logger.info(
        "Processed %s %s close: price=%s rsi=%s",
        symbol, tf,
        _indicators.get(symbol, {}).get(tf, {}).get("price"),
        _indicators.get(symbol, {}).get(tf, {}).get("rsi"),
    )


# ── Main loops ───────────────────────────────────

async def _redis_consumer_loop() -> None:
    """Subscribe to market:klines consumer group. Process closed klines for all symbols."""
    if not ensure_group(STREAM_KLINES, GROUP):
        logger.error("Cannot create consumer group — entering fallback mode")
        return

    logger.info("Redis consumer started: group=%s consumer=%s symbols=%d", GROUP, CONSUMER, len(SYMBOLS))
    consecutive_empty = 0

    while not _shutdown:
        entries = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: xreadgroup(GROUP, CONSUMER, STREAM_KLINES, count=50, block_ms=3000),
        )

        if not entries:
            consecutive_empty += 1
            if consecutive_empty > 10:
                logger.warning("No data from Redis for 30s — checking availability")
                if not redis_available():
                    logger.warning("Redis unavailable — switching to REST fallback")
                    return
                consecutive_empty = 0
            continue

        consecutive_empty = 0

        for entry_id, fields in entries:
            is_closed = fields.get("is_closed", "0") == "1"
            interval = fields.get("interval", "")
            raw_symbol = fields.get("symbol", "").lower()

            if raw_symbol not in _SYMBOLS_LOWER:
                xack(STREAM_KLINES, GROUP, entry_id)
                continue

            symbol = _SYMBOL_MAP[raw_symbol]

            if is_closed and interval in TIMEFRAMES:
                # Full indicator recalc on candle close
                _process_kline_close(symbol, interval, fields)
            elif not is_closed and interval == "1h":
                # Open 1H kline → volume projection monitor
                _monitor_volume_projection(symbol, fields)

            xack(STREAM_KLINES, GROUP, entry_id)


async def _fallback_loop() -> None:
    """REST fallback: fetch klines every 180s when Redis/WS unavailable."""
    logger.info("Fallback mode: REST fetch every %ds for %d symbols", FALLBACK_INTERVAL, len(SYMBOLS))

    while not _shutdown:
        if redis_available():
            logger.info("Redis recovered — switching back to consumer mode")
            return

        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                try:
                    df = fetch_klines(symbol, tf, KLINE_LIMIT, platform="binance")
                    if df is not None and len(df) >= 20:
                        _dataframes.setdefault(symbol, {})[tf] = df
                        ind = _calc_indicators_for(symbol, tf)
                        if ind:
                            _indicators.setdefault(symbol, {})[tf] = ind
                except Exception as exc:
                    logger.error("Fallback fetch %s %s failed: %s", symbol, tf, exc)
                    _stats["errors"] += 1

        global _macro
        _macro = _calc_macro()
        _write_cache()
        _stats["fallback_fetches"] += 1
        anchor_price = _indicators.get(REGIME_ANCHOR, {}).get("4h", {}).get("price")
        logger.info("Fallback cycle done: %s price=%s", REGIME_ANCHOR, anchor_price)

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
        n_ready = sum(1 for s in SYMBOLS if _indicators.get(s))
        logger.info(
            "stats: coins=%d/%d klines=%d cache=%d fallbacks=%d errors=%d up=%ds",
            n_ready, len(SYMBOLS),
            _stats["klines_processed"],
            _stats["cache_writes"],
            _stats["fallback_fetches"],
            _stats["errors"],
            round(time.monotonic() - _started_at),
        )


async def main():
    """Entry point."""
    logger.info(
        "indicator_engine starting — %d symbols: %s",
        len(SYMBOLS), ", ".join(SYMBOLS),
    )

    # Step 1: Cold start backfill
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
            await asyncio.sleep(5)

    for t in bg_tasks:
        t.cancel()
    logger.info("indicator_engine stopped")


if __name__ == "__main__":
    asyncio.run(main())
