#!/usr/bin/env python3
"""
fetch_historical.py — 歷史 K 線數據拉取 + CSV 快取

設計決定：獨立於 production fetch_klines()，支持 pagination + cache。
Binance /fapi/v1/klines max 1000/request，自動分頁直到覆蓋全部 range。

用法:
    from backtest.fetch_historical import fetch_klines_range
    df = fetch_klines_range("BTCUSDT", "1h", start_ms, end_ms)
"""

import logging
import os
import random
import time
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
DATA_DIR = os.path.join(AXC_HOME, "backtest", "data")

# ─── Circuit Breaker ───
# 3-state: CLOSED (normal) → OPEN (failing, reject calls) → HALF_OPEN (probe one call)
# Prevents hammering a failing exchange API.

class CircuitBreaker:
    """Lightweight circuit breaker for API endpoints."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = self.CLOSED
        self.failure_count = 0
        self._opened_at: float = 0.0

    def allow_request(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.time() - self._opened_at >= self.recovery_timeout:
                self.state = self.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow one probe
        return True

    def record_success(self):
        self.failure_count = 0
        self.state = self.CLOSED

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            self._opened_at = time.time()
            log.warning("Circuit breaker OPEN after %d failures", self.failure_count)


# Per-platform circuit breakers (module-level singletons)
_breakers: dict[str, CircuitBreaker] = {}

API_BASES = {
    "binance": "https://fapi.binance.com",
    "aster": "https://fapi.asterdex.com",
}

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]


def _cache_path(symbol: str, interval: str, start_ms: int, end_ms: int) -> str:
    """Build cache file path: backtest/data/{SYMBOL}_{INTERVAL}_{START}_{END}.csv"""
    os.makedirs(DATA_DIR, exist_ok=True)
    s = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    e = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    return os.path.join(DATA_DIR, f"{symbol}_{interval}_{s}_{e}.csv")


def fetch_klines_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    platform: str = "binance",
) -> pd.DataFrame:
    """
    拉取指定時間範圍嘅 K 線數據，自動分頁 + CSV 快取。

    Returns DataFrame with same schema as indicator_calc.fetch_klines():
        columns: open_time, open, high, low, close, volume,
                 close_time, quote_volume, trades, taker_buy_volume,
                 taker_buy_quote_volume, ignore, timestamp
    """
    cache = _cache_path(symbol, interval, start_ms, end_ms)

    if os.path.exists(cache):
        df = pd.read_csv(cache)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
        log.info("Cache hit: %s (%d candles)", os.path.basename(cache), len(df))
        return df

    base_url = API_BASES.get(platform, API_BASES["binance"])
    url = f"{base_url}/fapi/v1/klines"

    # Circuit breaker per platform
    if platform not in _breakers:
        _breakers[platform] = CircuitBreaker()
    cb = _breakers[platform]

    all_data = []
    cursor = start_ms
    page = 0

    while cursor < end_ms:
        if not cb.allow_request():
            raise ConnectionError(f"Circuit breaker OPEN for {platform} — too many failures")
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        # Retry with exponential backoff + random jitter
        data = None
        for attempt in range(4):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                cb.record_success()
                break
            except (requests.RequestException, ValueError) as e:
                cb.record_failure()
                if attempt == 3:
                    raise
                delay = (2 ** attempt) * (1 + random.random() * 0.5)
                log.warning("Kline fetch attempt %d failed: %s, retry in %.1fs", attempt + 1, e, delay)
                time.sleep(delay)

        if not data:
            break

        all_data.extend(data)
        page += 1

        # Advance cursor past last candle's close_time
        last_close_time = int(data[-1][6])
        cursor = last_close_time + 1

        if len(data) < 1000:
            break

        time.sleep(0.2)  # rate limit

    if not all_data:
        raise ValueError(
            f"No kline data for {symbol} {interval} "
            f"({datetime.fromtimestamp(start_ms/1000, tz=timezone.utc)} → "
            f"{datetime.fromtimestamp(end_ms/1000, tz=timezone.utc)})"
        )

    df = pd.DataFrame(all_data, columns=KLINE_COLUMNS)
    df = df.drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    df = df[
        (df["open_time"] >= start_ms) & (df["open_time"] <= end_ms)
    ].reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")

    df.to_csv(cache, index=False)
    print(f"    Cached {len(df)} candles → {os.path.basename(cache)} ({page} API calls)")

    return df
