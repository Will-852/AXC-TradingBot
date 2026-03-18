#!/usr/bin/env python3
"""
fetch_onchain.py — Fetch on-chain metrics from Coin Metrics Community API.

Design decisions:
- Free tier, no API key required
- Daily granularity only (sufficient for macro signals)
- Focus on exchange flow + network activity metrics
- CSV cache per asset per metric group, 24h staleness
- Response values are STRINGS — must convert to float
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
CACHE_DIR = os.path.join(AXC_HOME, "backtest", "data", "onchain")
CM_BASE = "https://community-api.coinmetrics.io/v4"

# Rate limit: 10 requests per 6 seconds → 0.7s between requests
_RATE_LIMIT_SLEEP = 0.7

# Cache staleness threshold
_CACHE_MAX_AGE = timedelta(hours=24)

# Map AXC symbols to Coin Metrics asset IDs
SYMBOL_TO_CM = {
    "BTCUSDT": "btc",
    "ETHUSDT": "eth",
    "SOLUSDT": "sol",
    "XRPUSDT": "xrp",
    "BNBUSDT": "bnb",
}

# Metrics we fetch (all in one request, comma-separated)
METRICS = [
    "FlowInExNtv",   # Exchange inflow (native units) — sell pressure
    "FlowOutExNtv",  # Exchange outflow (native units) — accumulation
    "FlowInExUSD",   # Exchange inflow (USD)
    "FlowOutExUSD",  # Exchange outflow (USD)
    "SplyExNtv",     # Supply on exchanges — trend indicator
    "AdrActCnt",     # Active addresses — network health
]

# Map API metric names → DataFrame column names
_METRIC_TO_COL = {
    "FlowInExNtv":  "flow_in_ntv",
    "FlowOutExNtv": "flow_out_ntv",
    "FlowInExUSD":  "flow_in_usd",
    "FlowOutExUSD": "flow_out_usd",
    "SplyExNtv":    "supply_ex",
    "AdrActCnt":    "active_addr",
}


def _cache_path(asset: str, start: str, end: str) -> str:
    """Build cache file path: backtest/data/onchain/{asset}_{start}_{end}_onchain.csv"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{asset}_{start}_{end}_onchain.csv")


def _cache_is_fresh(path: str) -> bool:
    """Check if cache file exists and is less than 24h old."""
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    age = datetime.now(timezone.utc) - mtime
    return age < _CACHE_MAX_AGE


def fetch_onchain_metrics(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch on-chain metrics from Coin Metrics for a given symbol.

    Why one request for all metrics: Coin Metrics supports comma-separated
    metrics in a single call, avoiding unnecessary rate limit consumption.

    Args:
        symbol: AXC symbol e.g. "BTCUSDT"
        start_date: "2024-01-01" format
        end_date: "2024-03-01" format

    Returns:
        DataFrame with columns: [date, flow_in_ntv, flow_out_ntv, flow_in_usd,
        flow_out_usd, supply_ex, active_addr]. Empty DataFrame on error.
    """
    asset = SYMBOL_TO_CM.get(symbol)
    if asset is None:
        log.warning("Symbol %s not mapped to Coin Metrics asset ID — skipping on-chain", symbol)
        return pd.DataFrame()

    cache = _cache_path(asset, start_date, end_date)
    if _cache_is_fresh(cache):
        df = pd.read_csv(cache, parse_dates=["date"])
        log.info("On-chain cache hit: %s (%d rows)", os.path.basename(cache), len(df))
        return df

    # Fetch all metrics in one request
    url = f"{CM_BASE}/timeseries/asset-metrics"
    params = {
        "assets": asset,
        "metrics": ",".join(METRICS),
        "frequency": "1d",
        "start_time": start_date,
        "end_time": end_date,
        "page_size": 10000,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        log.warning("Coin Metrics API error for %s: %s", symbol, e)
        return pd.DataFrame()
    except ValueError as e:
        log.warning("Coin Metrics JSON decode error for %s: %s", symbol, e)
        return pd.DataFrame()

    rows = payload.get("data", [])
    if not rows:
        log.warning("No on-chain data returned for %s (%s → %s)", symbol, start_date, end_date)
        return pd.DataFrame()

    # Parse response — values are STRINGS, convert to float
    records = []
    for row in rows:
        record = {"date": row["time"][:10]}  # "2024-01-01T00:00:00.000000000Z" → "2024-01-01"
        for api_name, col_name in _METRIC_TO_COL.items():
            raw = row.get(api_name)
            if raw is not None:
                try:
                    record[col_name] = float(raw)
                except (ValueError, TypeError):
                    record[col_name] = np.nan
            else:
                record[col_name] = np.nan
        records.append(record)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Cache to CSV
    df.to_csv(cache, index=False)
    log.info("On-chain cached: %s (%d rows)", os.path.basename(cache), len(df))

    return df


def compute_onchain_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute trading signals from raw on-chain data.

    Why these signals:
    - netflow_zscore: z-score normalises across different market regimes;
      >2 = extreme inflow = historically bearish
    - supply_ex_change: daily delta catches sudden deposit spikes
    - addr_momentum: 7d/30d SMA ratio detects network growth acceleration

    Returns same DataFrame with signal columns appended.
    """
    if df.empty:
        return df

    df = df.copy()

    # Exchange netflow: positive = more flowing IN = sell pressure
    df["exchange_netflow"] = df["flow_in_ntv"] - df["flow_out_ntv"]

    # Z-score of netflow over 30d rolling window
    roll_mean = df["exchange_netflow"].rolling(30, min_periods=1).mean()
    roll_std = df["exchange_netflow"].rolling(30, min_periods=1).std()
    # Avoid division by zero: where std is 0 or NaN, zscore = 0
    df["netflow_zscore"] = np.where(
        (roll_std == 0) | roll_std.isna(),
        0.0,
        (df["exchange_netflow"] - roll_mean) / roll_std,
    )

    # Daily change in exchange supply: positive = deposits = bearish
    df["supply_ex_change"] = df["supply_ex"].diff()

    # Active address momentum: 7d SMA / 30d SMA (>1 = growing activity)
    sma_7 = df["active_addr"].rolling(7, min_periods=1).mean()
    sma_30 = df["active_addr"].rolling(30, min_periods=1).mean()
    df["addr_momentum"] = np.where(
        (sma_30 == 0) | sma_30.isna(),
        1.0,
        sma_7 / sma_30,
    )

    # Fill NaN from rolling windows with neutral values
    df["netflow_zscore"] = df["netflow_zscore"].fillna(0.0)
    df["supply_ex_change"] = df["supply_ex_change"].fillna(0.0)
    df["addr_momentum"] = df["addr_momentum"].fillna(1.0)

    return df


def align_onchain_to_candles(df: pd.DataFrame, candle_timestamps: list[int]) -> dict:
    """
    Align daily on-chain data to hourly candle timestamps.

    Why searchsorted: O(n log n) instead of O(n*m) nested loop. Each hourly
    candle inherits the most recent daily data point (forward-fill semantics).

    Args:
        df: DataFrame from compute_onchain_signals (must have 'date' column)
        candle_timestamps: list of unix ms timestamps (e.g. from candle open_time)

    Returns:
        {str(candle_ts): {"netflow_zscore": float, "supply_ex_change": float,
                          "addr_momentum": float}}
    """
    if df.empty or not candle_timestamps:
        return {}

    signal_cols = ["netflow_zscore", "supply_ex_change", "addr_momentum"]

    # Verify required columns exist
    missing = [c for c in signal_cols if c not in df.columns]
    if missing:
        log.warning("Missing signal columns %s — run compute_onchain_signals first", missing)
        return {}

    # Convert daily dates to unix ms (midnight UTC)
    daily_ts = (df["date"].astype("int64") // 10**6).values  # nanoseconds → milliseconds

    candle_arr = np.array(candle_timestamps, dtype=np.int64)

    # searchsorted: find index of most recent daily data for each candle
    # side='right' - 1 gives the last daily_ts <= candle_ts
    indices = np.searchsorted(daily_ts, candle_arr, side="right") - 1

    result = {}
    for i, ts in enumerate(candle_timestamps):
        idx = indices[i]
        if idx < 0:
            # Candle is before first on-chain data point — use neutral values
            result[str(ts)] = {
                "netflow_zscore": 0.0,
                "supply_ex_change": 0.0,
                "addr_momentum": 1.0,
            }
        else:
            row = df.iloc[idx]
            result[str(ts)] = {
                "netflow_zscore": float(row["netflow_zscore"]),
                "supply_ex_change": float(row["supply_ex_change"]),
                "addr_momentum": float(row["addr_momentum"]),
            }

    return result
