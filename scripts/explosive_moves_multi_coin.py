"""
Multi-coin explosive move analysis: ETH, XRP, SOL + BTC contagion.
WHY: Understand per-coin explosive patterns and cross-coin contagion rates
to inform 4H trading decisions across the crypto complex.
"""

import requests
import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────
BINANCE_FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"
DAYS = 360
INTERVAL = "4h"
CANDLES_PER_DAY = 6
TOTAL_CANDLES = DAYS * CANDLES_PER_DAY  # 2160
MAX_PER_REQUEST = 1500
ROLLING_WINDOW = 30  # candles for volume average

# Per-coin explosive thresholds (range_pct)
# BTC baseline = 2%, others scaled by vol_mult
EXPLOSIVE_THRESHOLDS = {
    "BTCUSDT": 2.0,
    "ETHUSDT": 3.0,   # vol_mult 1.5
    "XRPUSDT": 4.0,   # vol_mult 2.0
    "SOLUSDT": 3.8,    # vol_mult 1.9
}

COINS = list(EXPLOSIVE_THRESHOLDS.keys())


def fetch_klines(symbol: str, interval: str, total_candles: int) -> pd.DataFrame:
    """Fetch klines with pagination. Binance max 1500 per request."""
    all_klines = []
    end_time = int(time.time() * 1000)

    remaining = total_candles
    while remaining > 0:
        limit = min(remaining, MAX_PER_REQUEST)
        params = {
            "symbol": symbol,
            "interval": interval,
            "endTime": end_time,
            "limit": limit,
        }
        resp = requests.get(BINANCE_FAPI_KLINES, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines = data + all_klines  # prepend (older data first)
        end_time = data[0][0] - 1  # before the oldest candle we got
        remaining -= len(data)
        logger.info(f"  {symbol}: fetched {len(data)} candles, {remaining} remaining")
        time.sleep(0.2)  # rate limit courtesy

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df


def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate per-candle metrics."""
    df = df.copy()
    df["range_pct"] = (df["high"] - df["low"]) / df["open"] * 100
    df["body_pct"] = (df["close"] - df["open"]).abs() / df["open"] * 100
    df["change_pct"] = (df["close"] - df["open"]) / df["open"] * 100
    df["vol_avg_30"] = df["volume"].rolling(ROLLING_WINDOW, min_periods=1).mean()
    df["volume_ratio"] = df["volume"] / df["vol_avg_30"]
    return df


def classify_explosive(df: pd.DataFrame, idx: int) -> str:
    """
    Classify the pattern leading into an explosive candle.
    Look at 3 candles BEFORE the explosive candle.

    QUIET_THEN_BOOM: prior 3 candles all have below-median range + low volume
    VOLUME_SURGE: prior candle has volume_ratio > 2 but small range (accumulation)
    PANIC_SELL: prior candle is strongly negative (change_pct < -threshold/2) with high volume
    SQUEEZE: prior 3 candles show progressively tightening range (each < previous)
    """
    if idx < 3:
        return "INSUFFICIENT_DATA"

    prior = df.iloc[idx - 3 : idx]
    median_range = df["range_pct"].median()
    explosive_threshold = df.attrs.get("explosive_threshold", 2.0)

    # Check QUIET_THEN_BOOM: all 3 prior candles have below-median range and volume_ratio < 1
    if (prior["range_pct"] < median_range).all() and (prior["volume_ratio"] < 1.0).all():
        return "QUIET_THEN_BOOM"

    # Check PANIC_SELL: the candle right before is strongly negative with high volume
    prev = df.iloc[idx - 1]
    if prev["change_pct"] < -(explosive_threshold / 2) and prev["volume_ratio"] > 1.5:
        return "PANIC_SELL"

    # Check VOLUME_SURGE: prior candle has volume spike but small range
    if prev["volume_ratio"] > 2.0 and prev["range_pct"] < median_range:
        return "VOLUME_SURGE"

    # Check SQUEEZE: progressively tightening range over prior 3 candles
    ranges = prior["range_pct"].values
    if ranges[0] > ranges[1] > ranges[2]:
        return "SQUEEZE"

    return "OTHER"


def analyze_coin(symbol: str, df: pd.DataFrame) -> dict:
    """Full analysis for one coin."""
    threshold = EXPLOSIVE_THRESHOLDS[symbol]
    df = compute_metrics(df)
    df.attrs["explosive_threshold"] = threshold

    explosive_mask = df["range_pct"] > threshold
    explosive_indices = df.index[explosive_mask].tolist()
    n_explosive = len(explosive_indices)
    total = len(df)

    # Classification
    classifications = {}
    quiet_gaps = []  # candles between QUIET_THEN_BOOM events and their preceding explosive
    for idx in explosive_indices:
        cat = classify_explosive(df, idx)
        classifications[cat] = classifications.get(cat, 0) + 1

        if cat == "QUIET_THEN_BOOM":
            # Count how many quiet candles before this boom (look back until range > median)
            median_range = df["range_pct"].median()
            quiet_count = 0
            for lookback in range(idx - 1, max(idx - 50, -1), -1):
                if df.iloc[lookback]["range_pct"] < median_range and df.iloc[lookback]["volume_ratio"] < 1.0:
                    quiet_count += 1
                else:
                    break
            quiet_gaps.append(quiet_count)

    avg_quiet = np.mean(quiet_gaps) if quiet_gaps else 0

    # Stats
    explosive_df = df.loc[explosive_mask]

    result = {
        "symbol": symbol,
        "total_candles": total,
        "n_explosive": n_explosive,
        "frequency": f"1 every {total / n_explosive:.1f} candles" if n_explosive > 0 else "N/A",
        "frequency_hours": f"~{total / n_explosive * 4:.0f}h" if n_explosive > 0 else "N/A",
        "threshold_pct": threshold,
        "classifications": classifications,
        "avg_quiet_before_boom": round(avg_quiet, 1),
        "avg_range_pct": round(df["range_pct"].mean(), 2),
        "avg_explosive_range": round(explosive_df["range_pct"].mean(), 2) if n_explosive > 0 else 0,
        "max_range_pct": round(df["range_pct"].max(), 2),
        "pct_up_explosive": round(
            (explosive_df["change_pct"] > 0).sum() / n_explosive * 100, 1
        ) if n_explosive > 0 else 0,
    }
    return result, df


def compute_contagion(all_data: dict) -> dict:
    """
    When BTC has an explosive move, how often do ETH/XRP/SOL also explode
    within the same or next 4H candle?
    """
    btc_df = all_data["BTCUSDT"]
    btc_explosive_times = set(
        btc_df.loc[btc_df["range_pct"] > EXPLOSIVE_THRESHOLDS["BTCUSDT"], "open_time"]
    )

    # Build set of "BTC explosive windows" = same candle or next candle
    btc_windows = set()
    for t in btc_explosive_times:
        btc_windows.add(t)
        btc_windows.add(t + pd.Timedelta(hours=4))

    contagion = {}
    for symbol in ["ETHUSDT", "XRPUSDT", "SOLUSDT"]:
        coin_df = all_data[symbol]
        threshold = EXPLOSIVE_THRESHOLDS[symbol]
        coin_explosive_times = set(
            coin_df.loc[coin_df["range_pct"] > threshold, "open_time"]
        )

        # How many BTC explosions also see this coin explode within window?
        btc_with_coin = 0
        for t in btc_explosive_times:
            window = {t, t + pd.Timedelta(hours=4)}
            if window & coin_explosive_times:
                btc_with_coin += 1

        rate = btc_with_coin / len(btc_explosive_times) * 100 if btc_explosive_times else 0
        contagion[symbol] = {
            "btc_explosive_events": len(btc_explosive_times),
            "coin_also_explosive": btc_with_coin,
            "contagion_rate_pct": round(rate, 1),
        }

    return contagion


def main():
    # ── Fetch all data ──
    all_dfs = {}
    for symbol in COINS:
        logger.info(f"Fetching {symbol}...")
        raw_df = fetch_klines(symbol, INTERVAL, TOTAL_CANDLES)
        logger.info(f"  {symbol}: {len(raw_df)} candles fetched")
        all_dfs[symbol] = raw_df

    # ── Analyze each coin ──
    results = {}
    computed_dfs = {}
    for symbol in COINS:
        logger.info(f"Analyzing {symbol}...")
        result, df = analyze_coin(symbol, all_dfs[symbol])
        results[symbol] = result
        computed_dfs[symbol] = df

    # ── Cross-coin contagion ──
    contagion = compute_contagion(computed_dfs)

    # ── Print results ──
    print("\n" + "=" * 80)
    print("MULTI-COIN EXPLOSIVE MOVE ANALYSIS (4H, 360 days)")
    print("=" * 80)

    for symbol in COINS:
        r = results[symbol]
        print(f"\n{'─' * 60}")
        print(f"  {r['symbol']}  |  Explosive threshold: range > {r['threshold_pct']}%")
        print(f"{'─' * 60}")
        print(f"  Total candles:        {r['total_candles']}")
        print(f"  Explosive candles:    {r['n_explosive']}")
        print(f"  Frequency:            {r['frequency']} ({r['frequency_hours']})")
        print(f"  Avg range (all):      {r['avg_range_pct']}%")
        print(f"  Avg explosive range:  {r['avg_explosive_range']}%")
        print(f"  Max range:            {r['max_range_pct']}%")
        print(f"  % Up in explosive:    {r['pct_up_explosive']}%")
        print(f"  Avg quiet before boom: {r['avg_quiet_before_boom']} candles")
        print(f"\n  Classification breakdown:")
        total_classified = sum(r["classifications"].values())
        for cat, count in sorted(r["classifications"].items(), key=lambda x: -x[1]):
            pct = count / total_classified * 100 if total_classified > 0 else 0
            print(f"    {cat:20s}  {count:4d}  ({pct:5.1f}%)")

    print(f"\n{'=' * 80}")
    print("CONTAGION ANALYSIS: BTC explosive -> other coins within same/next 4H candle")
    print(f"{'=' * 80}")
    for symbol, c in contagion.items():
        print(f"  {symbol:10s}  {c['coin_also_explosive']:3d}/{c['btc_explosive_events']} BTC events  "
              f"= {c['contagion_rate_pct']}% contagion rate")

    # ── Summary table ──
    print(f"\n{'=' * 80}")
    print("SUMMARY COMPARISON TABLE")
    print(f"{'=' * 80}")
    header = f"{'Coin':>10} {'Threshold':>10} {'Explosive':>10} {'Freq':>12} {'AvgRange':>10} {'%Up':>6} {'Contagion':>10}"
    print(header)
    print("-" * len(header))
    for symbol in COINS:
        r = results[symbol]
        cont = contagion.get(symbol, {}).get("contagion_rate_pct", "N/A (BTC)")
        cont_str = f"{cont}%" if isinstance(cont, (int, float)) else cont
        freq_val = f"{r['total_candles'] / r['n_explosive']:.0f}c" if r['n_explosive'] > 0 else "N/A"
        print(f"{symbol:>10} {r['threshold_pct']:>9.1f}% {r['n_explosive']:>10} {freq_val:>12} "
              f"{r['avg_explosive_range']:>9.1f}% {r['pct_up_explosive']:>5.1f}% {cont_str:>10}")


if __name__ == "__main__":
    main()
