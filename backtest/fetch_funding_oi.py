#!/usr/bin/env python3
"""
fetch_funding_oi.py — Fetch Binance Futures funding rate + OI history.

Design decisions:
- Funding rate: unlimited history via /fapi/v1/fundingRate (back to 2019)
- OI: only 30 days via /futures/data/openInterestHist (Binance hard limit)
- Long/Short ratio: 30 days via /futures/data/globalLongShortAccountRatio
- CSV cache per symbol (funding is sparse — 3 per day, not worth per-day files)
- Returns pandas DataFrame aligned to candle timestamps
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
CACHE_DIR = os.path.join(AXC_HOME, "backtest", "data", "funding_oi")
BINANCE_FAPI = "https://fapi.binance.com"

# Rate limiting — Binance /fapi/v1/fundingRate is 500 req/5min = ~0.6s safe interval
_FUNDING_SLEEP = 0.5
# /futures/data/* endpoints share a separate rate pool, more lenient
_DATA_SLEEP = 0.3
_FUNDING_PAGE_SIZE = 1000
_DATA_PAGE_SIZE = 500
_MAX_RETRIES = 4


def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(symbol: str, data_type: str, start_ms: int, end_ms: int) -> str:
    """Cache path: backtest/data/funding_oi/{SYMBOL}_{TYPE}_{START}_{END}.csv"""
    _ensure_cache_dir()
    s = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    e = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y%m%d")
    return os.path.join(CACHE_DIR, f"{symbol}_{data_type}_{s}_{e}.csv")


def _request_with_retry(
    url: str,
    params: dict,
    timeout: int = 15,
    sleep_between: float = _FUNDING_SLEEP,
) -> Optional[list]:
    """GET with exponential backoff on 429/5xx. Returns parsed JSON list or None."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = (2 ** attempt) * 2
                log.warning(
                    "Binance %d on attempt %d/%d for %s, backing off %ds",
                    resp.status_code, attempt + 1, _MAX_RETRIES, url.split("/")[-1], wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            log.warning("Timeout on attempt %d/%d for %s", attempt + 1, _MAX_RETRIES, url)
            time.sleep(2 ** attempt)
        except requests.ConnectionError as e:
            log.warning("Connection error on attempt %d/%d: %s", attempt + 1, _MAX_RETRIES, e)
            time.sleep(2 ** attempt)

    log.error("All %d retries exhausted for %s", _MAX_RETRIES, url)
    return None


def fetch_funding_rate_history(
    symbol: str, start_ms: int, end_ms: int
) -> pd.DataFrame:
    """
    Fetch historical funding rates from Binance.

    Endpoint: GET /fapi/v1/fundingRate
    - Unlimited history (back to contract inception)
    - One record per settlement (every 8h)
    - Pagination via startTime, limit=1000

    Why paginate by advancing startTime instead of offset: Binance fundingRate
    API has no offset param — only startTime/endTime/limit. We advance startTime
    to last record's fundingTime + 1 to avoid duplicates.

    Returns DataFrame: [timestamp, funding_rate, mark_price]
    """
    cache = _cache_path(symbol, "funding", start_ms, end_ms)
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        log.info("Funding cache hit: %s (%d records)", os.path.basename(cache), len(df))
        return df

    url = f"{BINANCE_FAPI}/fapi/v1/fundingRate"
    all_records: list[dict] = []
    cursor = start_ms
    page = 0

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": _FUNDING_PAGE_SIZE,
        }
        data = _request_with_retry(url, params, sleep_between=_FUNDING_SLEEP)
        if not data:
            break

        for rec in data:
            ft = int(rec["fundingTime"])
            if ft > end_ms:
                break
            all_records.append({
                "timestamp": ft,
                "funding_rate": float(rec["fundingRate"]),
                "mark_price": float(rec.get("markPrice", 0)),
            })

        page += 1
        # Advance cursor past last record to avoid duplicates
        cursor = int(data[-1]["fundingTime"]) + 1

        if len(data) < _FUNDING_PAGE_SIZE:
            break

        time.sleep(_FUNDING_SLEEP)

    if not all_records:
        log.warning("No funding rate data for %s in range", symbol)
        return pd.DataFrame(columns=["timestamp", "funding_rate", "mark_price"])

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    df.to_csv(cache, index=False)
    log.info(
        "Cached %d funding records → %s (%d API calls)",
        len(df), os.path.basename(cache), page,
    )
    return df


def fetch_oi_history(
    symbol: str, start_ms: int, end_ms: int, period: str = "1h"
) -> pd.DataFrame:
    """
    Fetch historical open interest from Binance.

    Endpoint: GET /futures/data/openInterestHist
    - ONLY last 30 days available (Binance hard limit)
    - period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
    - limit max 500

    Why no deeper history: Binance deletes OI snapshots older than 30 days
    from this endpoint. For longer backtests, OI data will be partial.

    Returns DataFrame: [timestamp, oi, oi_value]
    oi = open interest in contracts (base asset)
    oi_value = open interest in USD
    """
    cache = _cache_path(symbol, f"oi_{period}", start_ms, end_ms)
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        log.info("OI cache hit: %s (%d records)", os.path.basename(cache), len(df))
        return df

    url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
    all_records: list[dict] = []
    cursor = start_ms
    page = 0

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "period": period,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": _DATA_PAGE_SIZE,
        }
        data = _request_with_retry(url, params, sleep_between=_DATA_SLEEP)
        if not data:
            break

        for rec in data:
            ts = int(rec["timestamp"])
            if ts > end_ms:
                break
            all_records.append({
                "timestamp": ts,
                "oi": float(rec["sumOpenInterest"]),
                "oi_value": float(rec["sumOpenInterestValue"]),
            })

        page += 1
        cursor = int(data[-1]["timestamp"]) + 1

        if len(data) < _DATA_PAGE_SIZE:
            break

        time.sleep(_DATA_SLEEP)

    if not all_records:
        log.warning("No OI data for %s in range (30-day limit may apply)", symbol)
        return pd.DataFrame(columns=["timestamp", "oi", "oi_value"])

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    df.to_csv(cache, index=False)
    log.info(
        "Cached %d OI records → %s (%d API calls)",
        len(df), os.path.basename(cache), page,
    )
    return df


def fetch_longshort_ratio(
    symbol: str, start_ms: int, end_ms: int, period: str = "1h"
) -> pd.DataFrame:
    """
    Fetch global long/short account ratio from Binance.

    Endpoint: GET /futures/data/globalLongShortAccountRatio
    - 30 days limit (same as OI)

    Why ls_ratio is redundant with long_ratio/short_ratio: convenience —
    ls_ratio = longShortRatio from API, long_ratio + short_ratio = 1.0 always.
    Having all three avoids downstream recalculation.

    Returns DataFrame: [timestamp, long_ratio, short_ratio, ls_ratio]
    """
    cache = _cache_path(symbol, f"lsratio_{period}", start_ms, end_ms)
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        log.info("LS ratio cache hit: %s (%d records)", os.path.basename(cache), len(df))
        return df

    url = f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio"
    all_records: list[dict] = []
    cursor = start_ms
    page = 0

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "period": period,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": _DATA_PAGE_SIZE,
        }
        data = _request_with_retry(url, params, sleep_between=_DATA_SLEEP)
        if not data:
            break

        for rec in data:
            ts = int(rec["timestamp"])
            if ts > end_ms:
                break
            all_records.append({
                "timestamp": ts,
                "long_ratio": float(rec["longAccount"]),
                "short_ratio": float(rec["shortAccount"]),
                "ls_ratio": float(rec["longShortRatio"]),
            })

        page += 1
        cursor = int(data[-1]["timestamp"]) + 1

        if len(data) < _DATA_PAGE_SIZE:
            break

        time.sleep(_DATA_SLEEP)

    if not all_records:
        log.warning("No L/S ratio data for %s in range (30-day limit may apply)", symbol)
        return pd.DataFrame(columns=["timestamp", "long_ratio", "short_ratio", "ls_ratio"])

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    df.to_csv(cache, index=False)
    log.info(
        "Cached %d L/S ratio records → %s (%d API calls)",
        len(df), os.path.basename(cache), page,
    )
    return df


def align_to_candles(
    df: pd.DataFrame, candle_timestamps: list[int], col: str
) -> dict:
    """
    Align irregularly-spaced data to candle timestamps via forward-fill.

    For each candle_ts, find the most recent data point <= candle_ts.
    Uses numpy searchsorted for O(n log n) efficiency instead of O(n*m) loop.

    Why forward-fill: funding settles every 8h but candles are 1h. Each 1h
    candle inherits the most recent funding rate until the next settlement.
    Same logic for OI (1h) aligned to 15m candles, etc.

    Returns: {str(candle_ts): value} — same format as footprintData keys,
    consistent with aggregate_delta_volume() output convention.
    """
    if df.empty or not candle_timestamps:
        return {}

    data_ts = df["timestamp"].values.astype(np.int64)
    data_vals = df[col].values.astype(np.float64)
    candle_arr = np.array(candle_timestamps, dtype=np.int64)

    # searchsorted(side="right") - 1 gives index of last data_ts <= candle_ts
    idx = np.searchsorted(data_ts, candle_arr, side="right") - 1

    result = {}
    for i, ts in enumerate(candle_timestamps):
        j = idx[i]
        if j < 0:
            # No data point before this candle — skip
            continue
        result[str(ts)] = round(float(data_vals[j]), 8)

    return result
