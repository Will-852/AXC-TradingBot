#!/usr/bin/env python3
"""
fetch_agg_trades.py — Binance aggTrades 拉取 + 四個聚合函數

設計決定：
- 只用 public endpoint /fapi/v1/aggTrades（唔需要 auth）
- 每日 cache 完成嘅日子到 CSV，today 唔 cache（數據仲未完）
- Sleep 0.6s between requests（保守 100 req/min，Binance 限制 2400 weight/min）
- 所有聚合 server-side，唔送 raw trades 去 frontend
- Vectorized pandas operations，唔 row-by-row iterate
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
AGG_DATA_DIR = os.path.join(AXC_HOME, "backtest", "data", "aggtrades")

BINANCE_FAPI = "https://fapi.binance.com"

# BTC 高 volume 時段一小時可能超過 1000 trades per request
# 所以用 30-min windows 而唔係 1-hour windows
_WINDOW_MS = 30 * 60 * 1000  # 30 minutes
_BASE_SLEEP = 0.25  # seconds between API calls（Binance 限制 2400 weight/min = 120 req/min at 20 weight each）

# 每個 symbol 嘅默認 price bucket size（用於 volume profile + heatmap）
AGG_BUCKET_DEFAULTS = {
    "BTCUSDT": 50,
    "ETHUSDT": 10,
    "SOLUSDT": 1,
    "XRPUSDT": 0.01,
    "BNBUSDT": 2,
}


def _cache_path(symbol: str, day_str: str) -> str:
    """Cache path: backtest/data/aggtrades/{SYMBOL}_{YYYYMMDD}_agg.csv"""
    os.makedirs(AGG_DATA_DIR, exist_ok=True)
    return os.path.join(AGG_DATA_DIR, f"{symbol}_{day_str}_agg.csv")


def fetch_agg_trades_day(symbol: str, day: datetime) -> pd.DataFrame:
    """
    拉取指定日期嘅 aggTrades，自動分頁 + CSV cache。

    只 cache 完成嘅日子（today UTC 唔 cache）。
    每個 30-min window paginate 直到冇更多數據。

    Returns DataFrame: agg_id, price, qty, timestamp, is_buyer_maker
    """
    day_str = day.strftime("%Y%m%d")
    cache = _cache_path(symbol, day_str)

    # Check cache (skip today — data still flowing)
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    if day_str != today_str and os.path.exists(cache):
        df = pd.read_csv(cache)
        log.info("AggTrades cache hit: %s (%d trades)", os.path.basename(cache), len(df))
        return df

    # Day boundaries in UTC
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ms = int(day_start.timestamp() * 1000)
    end_ms = start_ms + 24 * 3600 * 1000 - 1  # end of day

    url = f"{BINANCE_FAPI}/fapi/v1/aggTrades"
    all_trades = []
    window_start = start_ms
    req_count = 0

    while window_start < end_ms:
        window_end = min(window_start + _WINDOW_MS - 1, end_ms)
        # Paginate within this window
        last_id = None
        while True:
            params = {"symbol": symbol, "limit": 1000}
            if last_id is not None:
                # fromId pagination — Binance ignores endTime when fromId is set,
                # so we rely on exceeded_window check below to enforce boundary
                params["fromId"] = last_id + 1
            else:
                params["startTime"] = window_start
                params["endTime"] = window_end

            # Retry with exponential backoff on 429 / 5xx
            for attempt in range(4):
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = (2 ** attempt) * 2  # 2, 4, 8, 16 seconds
                    log.warning("Binance %d on attempt %d, backing off %ds", resp.status_code, attempt + 1, wait)
                    time.sleep(wait)
                    continue
                break
            resp.raise_for_status()
            data = resp.json()
            req_count += 1

            if not data:
                break

            exceeded_window = False
            for t in data:
                ts = int(t["T"])
                if ts > window_end:
                    # fromId ignores endTime — stop at window boundary
                    exceeded_window = True
                    break
                all_trades.append({
                    "agg_id": t["a"],
                    "price": float(t["p"]),
                    "qty": float(t["q"]),
                    "timestamp": ts,
                    "is_buyer_maker": t["m"],
                })
                last_id = t["a"]

            if exceeded_window or len(data) < 1000:
                break

            time.sleep(_BASE_SLEEP)

        window_start = window_end + 1
        time.sleep(_BASE_SLEEP)

    if not all_trades:
        log.warning("No aggTrades for %s on %s", symbol, day_str)
        return pd.DataFrame(columns=["agg_id", "price", "qty", "timestamp", "is_buyer_maker"])

    df = pd.DataFrame(all_trades)
    df = df.drop_duplicates(subset=["agg_id"]).reset_index(drop=True)

    # Cache completed days only
    if day_str != today_str:
        df.to_csv(cache, index=False)
        log.info("Cached %d aggTrades → %s (%d requests)", len(df), os.path.basename(cache), req_count)
    else:
        log.info("Fetched %d aggTrades for %s (today, not cached, %d requests)", len(df), day_str, req_count)

    return df


def fetch_agg_trades_range(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """
    拉取指定時間範圍嘅 aggTrades，逐日 fetch + merge。

    Returns DataFrame: agg_id, price, qty, timestamp, is_buyer_maker
    """
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    frames = []
    current = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = (end_dt.date() - current.date()).days + 1

    for day_num in range(total_days):
        day = current + timedelta(days=day_num)
        log.info("Fetching aggTrades %s day %d/%d: %s", symbol, day_num + 1, total_days, day.strftime("%Y-%m-%d"))
        df = fetch_agg_trades_day(symbol, day)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["agg_id", "price", "qty", "timestamp", "is_buyer_maker"])

    result = pd.concat(frames, ignore_index=True)
    # Trim to exact range
    result = result[(result["timestamp"] >= start_ms) & (result["timestamp"] <= end_ms)].reset_index(drop=True)
    log.info("Total aggTrades for %s: %d", symbol, len(result))
    return result


# ═══════════════════════════════════════════
#  Aggregation functions
# ═══════════════════════════════════════════

def aggregate_delta_volume(
    trades_df: pd.DataFrame,
    candle_timestamps: list[int],
    interval_ms: int,
) -> dict:
    """
    每根 candle 嘅 buy/sell volume 同 delta。

    Returns: {candle_ts: {buy_vol, sell_vol, delta, buy_usd, sell_usd, delta_usd}}
    """
    if trades_df.empty:
        return {}

    df = trades_df.copy()
    df["usd"] = df["price"] * df["qty"]

    # Assign each trade to a candle bucket
    ts_arr = np.array(candle_timestamps, dtype=np.int64)
    trade_ts = df["timestamp"].values
    # searchsorted: find which candle each trade belongs to
    bucket_idx = np.searchsorted(ts_arr, trade_ts, side="right") - 1
    df["bucket"] = np.where(bucket_idx >= 0, ts_arr[np.clip(bucket_idx, 0, len(ts_arr) - 1)], -1)
    df = df[df["bucket"] >= 0]

    # is_buyer_maker=True means SELL aggressor, False means BUY aggressor
    buys = df[~df["is_buyer_maker"]]
    sells = df[df["is_buyer_maker"]]

    buy_grp = buys.groupby("bucket").agg(buy_vol=("qty", "sum"), buy_usd=("usd", "sum"))
    sell_grp = sells.groupby("bucket").agg(sell_vol=("qty", "sum"), sell_usd=("usd", "sum"))

    result = {}
    for ts in candle_timestamps:
        bv = float(buy_grp.loc[ts, "buy_vol"]) if ts in buy_grp.index else 0.0
        sv = float(sell_grp.loc[ts, "sell_vol"]) if ts in sell_grp.index else 0.0
        if bv == 0 and sv == 0:
            continue  # skip empty candles to reduce response size
        bu = float(buy_grp.loc[ts, "buy_usd"]) if ts in buy_grp.index else 0.0
        su = float(sell_grp.loc[ts, "sell_usd"]) if ts in sell_grp.index else 0.0
        result[str(ts)] = {
            "buy_vol": round(bv, 4),
            "sell_vol": round(sv, 4),
            "delta": round(bv - sv, 4),
            "buy_usd": round(bu, 2),
            "sell_usd": round(su, 2),
            "delta_usd": round(bu - su, 2),
        }

    return result


def aggregate_large_trades(
    trades_df: pd.DataFrame,
    threshold_usd: float = 100_000,
) -> list[dict]:
    """
    大額成交（USD value 超過 threshold 嘅 trades）。

    Returns: [{timestamp, price, qty, usd_value, side}]
    """
    if trades_df.empty:
        return []

    df = trades_df.copy()
    df["usd"] = df["price"] * df["qty"]
    large = df[df["usd"] >= threshold_usd]

    result = []
    for _, row in large.iterrows():
        result.append({
            "timestamp": int(row["timestamp"]),
            "price": round(float(row["price"]), 2),
            "qty": round(float(row["qty"]), 4),
            "usd_value": round(float(row["usd"]), 0),
            "side": "SELL" if row["is_buyer_maker"] else "BUY",
        })

    # Sort by usd_value descending, cap at 500 to avoid overlay overload
    result.sort(key=lambda x: x["usd_value"], reverse=True)
    return result[:500]


def aggregate_volume_profile(
    trades_df: pd.DataFrame,
    bucket_size: float = 50.0,
) -> list[dict]:
    """
    全時段 volume profile（按 price bucket 聚合）。

    Returns: [{price, buy_vol, sell_vol, total_vol}]
    """
    if trades_df.empty:
        return []

    df = trades_df.copy()
    # Round price down to bucket (integer math to avoid float precision issues)
    if bucket_size < 1:
        scale = int(round(1 / bucket_size))
        df["bucket_price"] = (df["price"] * scale).astype(int) / scale
    else:
        df["bucket_price"] = (df["price"] // bucket_size) * bucket_size

    buys = df[~df["is_buyer_maker"]].groupby("bucket_price")["qty"].sum()
    sells = df[df["is_buyer_maker"]].groupby("bucket_price")["qty"].sum()

    all_prices = sorted(set(buys.index) | set(sells.index))
    result = []
    for p in all_prices:
        bv = float(buys.get(p, 0))
        sv = float(sells.get(p, 0))
        result.append({
            "price": float(p),
            "buy_vol": round(bv, 4),
            "sell_vol": round(sv, 4),
            "total_vol": round(bv + sv, 4),
        })

    return result


FOOTPRINT_IMBALANCE_RATIO = 3.0  # buy:sell or sell:buy > 3:1 = imbalance


def aggregate_footprint_heatmap(
    trades_df: pd.DataFrame,
    candle_timestamps: list[int],
    interval_ms: int,
    bucket_size: float = 50.0,
    max_levels: int = 40,
) -> dict:
    """
    每根 candle 嘅 price-level heatmap（含 delta + imbalance 標記）。

    設計決定：max_levels 40 而唔係 20，因為 BTC $50 bucket 喺 4H candle
    價格範圍 ~$2000 = 40 levels 先夠覆蓋。imbalance ratio 3:1 係業界標準。

    Returns: {candle_ts: [{price, buy_vol, sell_vol, total_vol, delta, imbalance}]}
    """
    if trades_df.empty:
        return {}

    df = trades_df.copy()
    if bucket_size < 1:
        scale = int(round(1 / bucket_size))
        df["bucket_price"] = (df["price"] * scale).astype(int) / scale
    else:
        df["bucket_price"] = (df["price"] // bucket_size) * bucket_size

    # Assign trades to candle buckets
    ts_arr = np.array(candle_timestamps, dtype=np.int64)
    bucket_idx = np.searchsorted(ts_arr, df["timestamp"].values, side="right") - 1
    df["candle_ts"] = np.where(bucket_idx >= 0, ts_arr[np.clip(bucket_idx, 0, len(ts_arr) - 1)], -1)
    df = df[df["candle_ts"] >= 0]

    result = {}
    grouped = df.groupby("candle_ts")

    for candle_ts, group in grouped:
        buys = group[~group["is_buyer_maker"]].groupby("bucket_price")["qty"].sum()
        sells = group[group["is_buyer_maker"]].groupby("bucket_price")["qty"].sum()
        all_prices = set(buys.index) | set(sells.index)

        levels = []
        for p in all_prices:
            bv = float(buys.get(p, 0))
            sv = float(sells.get(p, 0))
            delta = bv - sv
            # Imbalance: one side > 3× the other (skip if either side is negligible)
            minor = min(bv, sv)
            imbalance = (minor > 0 and max(bv, sv) / minor >= FOOTPRINT_IMBALANCE_RATIO)
            levels.append({
                "price": float(p),
                "buy_vol": round(bv, 4),
                "sell_vol": round(sv, 4),
                "total_vol": round(bv + sv, 4),
                "delta": round(delta, 4),
                "imbalance": imbalance,
            })

        # Keep top N levels by total_vol
        levels.sort(key=lambda x: x["total_vol"], reverse=True)
        levels = levels[:max_levels]
        # Re-sort by price for rendering
        levels.sort(key=lambda x: x["price"])

        result[str(int(candle_ts))] = levels

    return result


def aggregate_cvd(
    trades_df: pd.DataFrame,
    candle_timestamps: list[int],
    interval_ms: int,
) -> dict:
    """
    Cumulative Volume Delta — per-candle delta 嘅 running sum。

    重用 aggregate_delta_volume 嘅 per-candle buy-sell delta，做 cumulative sum。
    CVD 上升 = 買方主導趨勢，CVD 下降 = 賣方主導。

    Returns: {candle_ts: {delta, cvd}}
    """
    delta_data = aggregate_delta_volume(trades_df, candle_timestamps, interval_ms)
    if not delta_data:
        return {}

    result = {}
    cvd = 0.0
    for ts in candle_timestamps:
        ts_str = str(ts)
        dv = delta_data.get(ts_str)
        if dv:
            cvd += dv["delta_usd"]
        result[ts_str] = {
            "delta": dv["delta_usd"] if dv else 0,
            "cvd": round(cvd, 2),
        }

    return result
