#!/usr/bin/env python3
"""
fetch_agg_trades.py — Multi-source aggTrades 拉取 + 四個聚合函數

Source priority (waterfall, single source per day):
  1. CSV cache (completed days)
  2. WS live cache (.live.csv for today)
  3. Binance Data Vision CDN (T-1, zero rate limit)
  4. Binance API (rate limited, 2400 weight/min)
  5. Bybit API (backup, 120 req/min)

設計決定：
- 唔 merge 唔同交易所 — 同一 day 只用一個 source（防 double-count delta）
- 所有聚合 server-side，唔送 raw trades 去 frontend
- Vectorized pandas operations，唔 row-by-row iterate
"""

import io
import logging
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
AGG_DATA_DIR = os.path.join(AXC_HOME, "backtest", "data", "aggtrades")

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_DATA_VISION = "https://data.binance.vision"
BYBIT_API = "https://api.bybit.com"

_EMPTY_DF_COLS = ["agg_id", "price", "qty", "timestamp", "is_buyer_maker"]

# BTC 高 volume 時段一小時可能超過 1000 trades per request
# 所以用 30-min windows 而唔係 1-hour windows
_WINDOW_MS = 30 * 60 * 1000  # 30 minutes
_BASE_SLEEP = 0.25  # seconds between API calls
_CDN_RETRIES = 3
_CDN_RETRY_DELAY = 5  # seconds

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


# ═══════════════════════════════════════════
#  Source 1: WS live cache (.live.csv)
# ═══════════════════════════════════════════

def _fetch_from_ws_cache(symbol: str, day: datetime) -> pd.DataFrame | None:
    """Read aggTrades from WS recorder's live or completed cache file."""
    day_str = day.strftime("%Y%m%d")
    # Check completed file first (WS daemon renames .live.csv at midnight)
    cache = _cache_path(symbol, day_str)
    if os.path.exists(cache):
        return None  # let normal cache path handle it

    # Check live file (today's in-progress recording)
    live_path = cache.replace("_agg.csv", "_agg.live.csv")
    if not os.path.exists(live_path):
        return None

    try:
        df = pd.read_csv(live_path, on_bad_lines="skip")
        if df.empty or "agg_id" not in df.columns:
            return None
        log.info("WS cache hit: %s (%d trades from live recording)", os.path.basename(live_path), len(df))
        return df
    except Exception as e:
        log.warning("WS cache read failed %s: %s", live_path, e)
        return None


# ═══════════════════════════════════════════
#  Source 2: Binance Data Vision CDN
# ═══════════════════════════════════════════

def _fetch_from_cdn(symbol: str, day: datetime) -> pd.DataFrame | None:
    """Download aggTrades from data.binance.vision CDN (zero rate limit, T-1).

    Hardened: retry 3x with backoff, stream to temp file (唔 load 入 memory).
    """
    date_str = day.strftime("%Y-%m-%d")
    url = (
        f"{BINANCE_DATA_VISION}/data/futures/um/daily/aggTrades/"
        f"{symbol}/{symbol}-aggTrades-{date_str}.zip"
    )

    for attempt in range(_CDN_RETRIES):
        try:
            resp = requests.get(url, timeout=180, stream=True)
            if resp.status_code == 404:
                log.debug("CDN: %s %s not available yet", symbol, date_str)
                return None
            resp.raise_for_status()

            # Stream to temp file (avoid loading 100MB+ into memory)
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name
                shutil.copyfileobj(resp.raw, tmp)

            try:
                with zipfile.ZipFile(tmp_path) as zf:
                    csv_name = zf.namelist()[0]
                    with zf.open(csv_name) as f:
                        df = pd.read_csv(f, dtype={
                            "agg_trade_id": np.int64,
                            "price": np.float64,
                            "quantity": np.float64,
                            "transact_time": np.int64,
                            "is_buyer_maker": str,
                        })
            finally:
                os.unlink(tmp_path)

            df = df.rename(columns={
                "agg_trade_id": "agg_id",
                "quantity": "qty",
                "transact_time": "timestamp",
            })
            df = df[["agg_id", "price", "qty", "timestamp", "is_buyer_maker"]]
            df["is_buyer_maker"] = df["is_buyer_maker"].str.strip().str.lower() == "true"

            log.info("CDN: downloaded %d trades for %s %s", len(df), symbol, date_str)
            return df

        except (requests.RequestException, zipfile.BadZipFile, KeyError, IndexError) as e:
            log.warning("CDN attempt %d/%d failed for %s %s: %s",
                        attempt + 1, _CDN_RETRIES, symbol, date_str, e)
            if attempt < _CDN_RETRIES - 1:
                time.sleep(_CDN_RETRY_DELAY)

    return None


# ═══════════════════════════════════════════
#  Source 3: Binance API (rate limited)
# ═══════════════════════════════════════════

def _fetch_from_binance_api(symbol: str, day: datetime) -> pd.DataFrame | None:
    """Paginated fetch from Binance futures aggTrades API. Rate limited."""
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ms = int(day_start.timestamp() * 1000)
    end_ms = start_ms + 24 * 3600 * 1000 - 1

    url = f"{BINANCE_FAPI}/fapi/v1/aggTrades"
    all_trades = []
    window_start = start_ms
    req_count = 0
    throttle = _BASE_SLEEP
    binance_blocked = False

    while window_start < end_ms:
        window_end = min(window_start + _WINDOW_MS - 1, end_ms)
        last_id = None
        while True:
            params = {"symbol": symbol, "limit": 1000}
            if last_id is not None:
                params["fromId"] = last_id + 1
            else:
                params["startTime"] = window_start
                params["endTime"] = window_end

            success = False
            for attempt in range(6):
                try:
                    resp = requests.get(url, params=params, timeout=15)
                except requests.RequestException as e:
                    log.warning("Binance request error: %s", e)
                    time.sleep(2 ** attempt)
                    continue

                if resp.status_code == 418:
                    # IP banned — abort entire Binance fetch, let Bybit handle
                    log.warning("Binance 418 IP ban — aborting, will try Bybit")
                    binance_blocked = True
                    break
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = (2 ** attempt) * 2
                    log.warning("Binance %d on attempt %d/6, backing off %ds",
                                resp.status_code, attempt + 1, wait)
                    time.sleep(wait)
                    throttle = min(throttle * 2, 2.0)
                    continue
                if resp.status_code == 200:
                    success = True
                    break
                # Other error
                log.warning("Binance unexpected %d", resp.status_code)
                break

            if binance_blocked:
                break
            if not success:
                # Give up on this window after 6 retries
                log.warning("Binance: giving up window %d after retries", window_start)
                break

            data = resp.json()
            req_count += 1
            if not data:
                break

            exceeded_window = False
            for t in data:
                ts = int(t["T"])
                if ts > window_end:
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
            time.sleep(throttle)

        if binance_blocked:
            break
        window_start = window_end + 1
        time.sleep(throttle)

    if binance_blocked and not all_trades:
        return None  # signal caller to try Bybit

    if not all_trades:
        return pd.DataFrame(columns=_EMPTY_DF_COLS)

    df = pd.DataFrame(all_trades)
    log.info("Binance API: fetched %d trades (%d requests)", len(df), req_count)
    return df


# ═══════════════════════════════════════════
#  Source 4: Bybit API (backup)
# ═══════════════════════════════════════════

def _fetch_from_bybit_api(symbol: str, day: datetime) -> pd.DataFrame | None:
    """Fetch aggTrades from Bybit as backup when Binance is rate limited.

    Bybit mapping:
      side="Buy"  → buyer is taker → is_buyer_maker=False (BUY aggressor)
      side="Sell" → seller is taker → is_buyer_maker=True  (SELL aggressor)

    Uses negative agg_id counter to avoid collision with Binance IDs.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ms = int(day_start.timestamp() * 1000)
    end_ms = start_ms + 24 * 3600 * 1000 - 1

    url = f"{BYBIT_API}/v5/market/recent-trade"
    all_trades = []
    cursor = ""
    req_count = 0
    agg_counter = -1  # negative to avoid Binance collision

    while True:
        params = {
            "category": "linear",
            "symbol": symbol,
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                log.warning("Bybit %d: %s", resp.status_code, resp.text[:200])
                break
            data = resp.json()
        except requests.RequestException as e:
            log.warning("Bybit request error: %s", e)
            break

        result = data.get("result", {})
        trades = result.get("list", [])
        req_count += 1

        if not trades:
            break

        for t in trades:
            ts = int(t["time"])
            if ts < start_ms or ts > end_ms:
                continue
            all_trades.append({
                "agg_id": agg_counter,
                "price": float(t["price"]),
                "qty": float(t["size"]),
                "timestamp": ts,
                # Bybit side = taker side: Buy=buy taker=is_buyer_maker False
                "is_buyer_maker": t["side"] == "Sell",
            })
            agg_counter -= 1

        cursor = result.get("nextPageCursor", "")
        if not cursor:
            break

        time.sleep(0.6)  # Bybit: 120 req/min = ~0.5s, conservative 0.6s

    if not all_trades:
        log.warning("Bybit: no trades for %s on %s", symbol, day.strftime("%Y%m%d"))
        return pd.DataFrame(columns=_EMPTY_DF_COLS)

    df = pd.DataFrame(all_trades)
    log.info("Bybit API: fetched %d trades (%d requests)", len(df), req_count)
    return df


def fetch_agg_trades_day(symbol: str, day: datetime) -> pd.DataFrame:
    """
    Waterfall fetch: cache → WS cache → CDN → Binance API → Bybit API.

    Single source per day (唔 merge)。Cache completed days to CSV。
    Returns DataFrame: agg_id, price, qty, timestamp, is_buyer_maker
    """
    day_str = day.strftime("%Y%m%d")
    cache = _cache_path(symbol, day_str)
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    is_today = (day_str == today_str)

    # ── 1. CSV cache (completed days only) ──
    if not is_today and os.path.exists(cache):
        df = pd.read_csv(cache)
        log.info("[cache] %s (%d trades)", os.path.basename(cache), len(df))
        return df

    # Also check Bybit cache
    bybit_cache = cache.replace("_agg.csv", "_agg_bybit.csv")
    if not is_today and os.path.exists(bybit_cache):
        df = pd.read_csv(bybit_cache)
        log.info("[cache/bybit] %s (%d trades)", os.path.basename(bybit_cache), len(df))
        return df

    # ── 2. WS live cache (today's in-progress recording) ──
    ws_df = _fetch_from_ws_cache(symbol, day)
    if ws_df is not None and not ws_df.empty:
        return ws_df.drop_duplicates(subset=["agg_id"]).reset_index(drop=True)

    # ── 3. Binance CDN (T-1 only, zero rate limit) ──
    if not is_today:
        cdn_df = _fetch_from_cdn(symbol, day)
        if cdn_df is not None and not cdn_df.empty:
            cdn_df = cdn_df.drop_duplicates(subset=["agg_id"]).reset_index(drop=True)
            cdn_df.to_csv(cache, index=False)
            log.info("[cdn→cache] %d trades → %s", len(cdn_df), os.path.basename(cache))
            return cdn_df

    # ── 4. Binance API (rate limited) ──
    bn_df = _fetch_from_binance_api(symbol, day)
    if bn_df is not None and not bn_df.empty:
        bn_df = bn_df.drop_duplicates(subset=["agg_id"]).reset_index(drop=True)
        if not is_today:
            bn_df.to_csv(cache, index=False)
            log.info("[binance→cache] %d trades → %s", len(bn_df), os.path.basename(cache))
        return bn_df

    # ── 5. Bybit API (backup when Binance blocked) ──
    log.info("[bybit] Attempting Bybit backup for %s %s", symbol, day_str)
    bb_df = _fetch_from_bybit_api(symbol, day)
    if bb_df is not None and not bb_df.empty:
        bb_df = bb_df.drop_duplicates(subset=["agg_id"]).reset_index(drop=True)
        if not is_today:
            bb_df.to_csv(bybit_cache, index=False)
            log.info("[bybit→cache] %d trades → %s", len(bb_df), os.path.basename(bybit_cache))
        return bb_df

    log.warning("All sources exhausted for %s on %s", symbol, day_str)
    return pd.DataFrame(columns=_EMPTY_DF_COLS)


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
        return pd.DataFrame(columns=_EMPTY_DF_COLS)

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
