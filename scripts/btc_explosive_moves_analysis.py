"""
BTC 4H Explosive Moves Analysis
--------------------------------
Fetches 360 days of BTC/USDT 4H klines from Binance Futures,
identifies explosive candles (range > 2%), classifies what preceded them.

Design: One-shot analysis script, not for production use.
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── Constants ──
SYMBOL = "BTCUSDT"
INTERVAL = "4h"
DAYS = 360
CANDLES_PER_DAY = 6  # 24h / 4h
TOTAL_CANDLES_NEEDED = DAYS * CANDLES_PER_DAY  # 2160
MAX_PER_REQUEST = 1500
ROLLING_WINDOW = 30
EXPLOSIVE_THRESHOLD_PCT = 2.0  # range_pct > 2%
QUIET_VOL_RATIO = 0.7
QUIET_RANGE_PCT = 1.0
VOLUME_SURGE_RATIO = 2.0
LOOKBACK = 3  # candles before explosive

BASE_URL = "https://fapi.binance.com/fapi/v1/klines"


def fetch_klines(symbol: str, interval: str, total: int) -> pd.DataFrame:
    """Paginate Binance Futures klines API. Returns DataFrame with OHLCV."""
    all_rows = []
    end_time = int(datetime.utcnow().timestamp() * 1000)

    remaining = total
    while remaining > 0:
        limit = min(remaining, MAX_PER_REQUEST)
        params = {
            "symbol": symbol,
            "interval": interval,
            "endTime": end_time,
            "limit": limit,
        }
        logger.info(f"Fetching {limit} candles ending at {datetime.utcfromtimestamp(end_time/1000).isoformat()}")
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            logger.warning("No more data returned, stopping.")
            break

        all_rows = data + all_rows  # prepend (older data first)
        # Next batch ends just before the earliest candle we got
        end_time = data[0][0] - 1
        remaining -= len(data)
        time.sleep(0.3)  # rate limit courtesy

    # Binance kline format: [open_time, o, h, l, c, vol, close_time, quote_vol, trades, taker_buy_vol, taker_buy_quote_vol, ignore]
    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    # Convert types
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    # Drop duplicates (overlapping pagination)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df


def calculate_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add range_pct, body_pct, change_pct, volume_ratio columns."""
    df["range_pct"] = (df["high"] - df["low"]) / df["open"] * 100
    df["body_pct"] = (df["close"] - df["open"]).abs() / df["open"] * 100
    df["change_pct"] = (df["close"] - df["open"]) / df["open"] * 100
    df["vol_ma30"] = df["volume"].rolling(ROLLING_WINDOW, min_periods=1).mean()
    df["volume_ratio"] = df["volume"] / df["vol_ma30"]
    df["is_bullish"] = df["close"] > df["open"]
    return df


def classify_explosive(df: pd.DataFrame) -> pd.DataFrame:
    """Find explosive candles and classify by preceding pattern."""
    explosive_mask = df["range_pct"] > EXPLOSIVE_THRESHOLD_PCT
    explosive_idx = df.index[explosive_mask].tolist()
    logger.info(f"Found {len(explosive_idx)} explosive candles (range > {EXPLOSIVE_THRESHOLD_PCT}%)")

    records = []
    for idx in explosive_idx:
        if idx < LOOKBACK:
            continue  # not enough preceding candles

        row = df.loc[idx]
        preceding = df.loc[idx - LOOKBACK : idx - 1]

        avg_vol_ratio = preceding["volume_ratio"].mean()
        avg_range_pct = preceding["range_pct"].mean()
        prev_bullish = preceding["is_bullish"].tolist()
        prev_candle_vol_ratio = df.loc[idx - 1, "volume_ratio"]

        # Classify
        classifications = []

        # QUIET_THEN_BOOM
        if avg_vol_ratio < QUIET_VOL_RATIO and avg_range_pct < QUIET_RANGE_PCT:
            classifications.append("QUIET_THEN_BOOM")

        # VOLUME_SURGE: preceding candle had volume_ratio > 2
        if prev_candle_vol_ratio > VOLUME_SURGE_RATIO:
            classifications.append("VOLUME_SURGE")

        # PANIC_SELL: explosive is bearish AND preceding was also bearish
        explosive_bearish = row["close"] < row["open"]
        prev_bearish = df.loc[idx - 1, "close"] < df.loc[idx - 1, "open"]
        if explosive_bearish and prev_bearish:
            classifications.append("PANIC_SELL")

        # SQUEEZE: preceding 3 all same direction, explosive reverses
        all_same_dir = all(prev_bullish) or all(not b for b in prev_bullish)
        if all_same_dir:
            preceding_bullish = all(prev_bullish)
            explosive_bullish = row["close"] > row["open"]
            if preceding_bullish != explosive_bullish:
                classifications.append("SQUEEZE")

        if not classifications:
            classifications.append("OTHER")

        records.append({
            "datetime": row["open_time"],
            "open": row["open"],
            "close": row["close"],
            "range_pct": row["range_pct"],
            "change_pct": row["change_pct"],
            "volume_ratio": row["volume_ratio"],
            "pre_avg_vol_ratio": avg_vol_ratio,
            "pre_avg_range_pct": avg_range_pct,
            "classifications": classifications,
            "primary_class": classifications[0],
        })

    return pd.DataFrame(records)


def find_quiet_period_lengths(df: pd.DataFrame, explosive_indices: list) -> list:
    """For QUIET_THEN_BOOM events, how many consecutive quiet candles preceded?"""
    quiet_lengths = []
    for idx in explosive_indices:
        count = 0
        check = idx - 1
        while check >= 0:
            row = df.loc[check]
            if row["volume_ratio"] < QUIET_VOL_RATIO and row["range_pct"] < QUIET_RANGE_PCT:
                count += 1
                check -= 1
            else:
                break
        if count > 0:
            quiet_lengths.append(count)
    return quiet_lengths


def main():
    # ── Fetch ──
    logger.info(f"Fetching {TOTAL_CANDLES_NEEDED} 4H candles for {SYMBOL}")
    df = fetch_klines(SYMBOL, INTERVAL, TOTAL_CANDLES_NEEDED)
    logger.info(f"Got {len(df)} candles from {df['open_time'].iloc[0]} to {df['open_time'].iloc[-1]}")

    # ── Calculate ──
    df = calculate_metrics(df)

    # ── Classify ──
    explosive_df = classify_explosive(df)

    # ── Quiet period analysis ──
    # Get indices of QUIET_THEN_BOOM events in original df
    qtb_datetimes = explosive_df[explosive_df["classifications"].apply(lambda x: "QUIET_THEN_BOOM" in x)]["datetime"].tolist()
    qtb_indices = df[df["open_time"].isin(qtb_datetimes)].index.tolist()
    quiet_lengths = find_quiet_period_lengths(df, qtb_indices)

    # ── P&L Analysis ──
    # Assume perfect entry at open, exit at close of explosive candle
    # Use change_pct as the P&L per trade (long if bullish, short if bearish = always profit from body)
    # More realistic: we catch direction correctly = abs(change_pct)
    explosive_df["abs_change_pct"] = explosive_df["change_pct"].abs()
    total_abs_change_all = df["change_pct"].abs().sum()
    total_abs_change_explosive = explosive_df["abs_change_pct"].sum()
    pnl_concentration = total_abs_change_explosive / total_abs_change_all * 100

    # ── Output ──
    print("\n" + "=" * 70)
    print(f"  BTC 4H EXPLOSIVE MOVES ANALYSIS ({DAYS} days)")
    print("=" * 70)

    print(f"\n📊 DATASET")
    print(f"  Period:          {df['open_time'].iloc[0].strftime('%Y-%m-%d')} to {df['open_time'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"  Total 4H candles: {len(df)}")
    print(f"  Days covered:    {(df['open_time'].iloc[-1] - df['open_time'].iloc[0]).days}")

    print(f"\n💥 EXPLOSIVE CANDLES (range > {EXPLOSIVE_THRESHOLD_PCT}%)")
    print(f"  Count:           {len(explosive_df)}")
    print(f"  Frequency:       every {len(df) / max(len(explosive_df), 1):.1f} candles ({len(df) / max(len(explosive_df), 1) * 4:.0f} hours)")
    print(f"  % of all candles: {len(explosive_df) / len(df) * 100:.1f}%")

    print(f"\n📋 CLASSIFICATION BREAKDOWN")
    # Count each class (a candle can have multiple)
    class_counts = {}
    for _, row in explosive_df.iterrows():
        for c in row["classifications"]:
            class_counts[c] = class_counts.get(c, 0) + 1

    for cls, count in sorted(class_counts.items(), key=lambda x: -x[1]):
        pct = count / len(explosive_df) * 100
        print(f"  {cls:20s}: {count:4d} ({pct:5.1f}%)")

    # Primary class (first classification)
    print(f"\n  Primary class distribution:")
    primary_counts = explosive_df["primary_class"].value_counts()
    for cls, count in primary_counts.items():
        pct = count / len(explosive_df) * 100
        print(f"    {cls:20s}: {count:4d} ({pct:5.1f}%)")

    print(f"\n📈 VOLUME ANALYSIS")
    all_avg_vol_ratio = df["volume_ratio"].mean()
    pre_explosive_avg = explosive_df["pre_avg_vol_ratio"].mean()
    explosive_avg_vol = explosive_df["volume_ratio"].mean()
    print(f"  All candles avg volume_ratio:         {all_avg_vol_ratio:.2f}")
    print(f"  Pre-explosive (3 candle) avg vol_ratio: {pre_explosive_avg:.2f}")
    print(f"  Explosive candle avg volume_ratio:     {explosive_avg_vol:.2f}")

    print(f"\n🔇 QUIET_THEN_BOOM ANALYSIS")
    if quiet_lengths:
        print(f"  Events:              {len(qtb_datetimes)}")
        print(f"  Median quiet period: {np.median(quiet_lengths):.0f} candles ({np.median(quiet_lengths) * 4:.0f} hours)")
        print(f"  Mean quiet period:   {np.mean(quiet_lengths):.1f} candles ({np.mean(quiet_lengths) * 4:.0f} hours)")
        print(f"  Max quiet period:    {max(quiet_lengths)} candles ({max(quiet_lengths) * 4} hours)")
    else:
        print(f"  No QUIET_THEN_BOOM events found")

    print(f"\n💰 P&L CONCENTRATION (perfect entry/exit)")
    print(f"  Total abs(change) all candles:      {total_abs_change_all:.1f}%")
    print(f"  Total abs(change) explosive only:   {total_abs_change_explosive:.1f}%")
    print(f"  Explosive candles hold {pnl_concentration:.1f}% of total movement")
    print(f"  ({len(explosive_df)} candles = {len(explosive_df)/len(df)*100:.1f}% of candles, {pnl_concentration:.1f}% of movement)")

    # Directional breakdown
    bull_explosive = explosive_df[explosive_df["change_pct"] > 0]
    bear_explosive = explosive_df[explosive_df["change_pct"] < 0]
    print(f"\n  Bullish explosive: {len(bull_explosive)} (avg +{bull_explosive['change_pct'].mean():.2f}%)")
    print(f"  Bearish explosive: {len(bear_explosive)} (avg {bear_explosive['change_pct'].mean():.2f}%)")

    # Top 10 most explosive
    print(f"\n🏆 TOP 10 MOST EXPLOSIVE 4H CANDLES")
    top10 = explosive_df.nlargest(10, "range_pct")
    for _, row in top10.iterrows():
        direction = "🟢" if row["change_pct"] > 0 else "🔴"
        print(f"  {direction} {row['datetime'].strftime('%Y-%m-%d %H:%M')} | "
              f"range={row['range_pct']:.2f}% | change={row['change_pct']:+.2f}% | "
              f"vol_ratio={row['volume_ratio']:.1f}x | {row['classifications']}")

    # Average range by hour of day
    print(f"\n🕐 EXPLOSIVE MOVES BY HOUR (UTC)")
    explosive_df["hour"] = explosive_df["datetime"].dt.hour
    hour_counts = explosive_df.groupby("hour").size()
    for hour in sorted(hour_counts.index):
        bar = "█" * hour_counts[hour]
        print(f"  {hour:02d}:00 UTC | {hour_counts[hour]:3d} | {bar}")

    # Day of week
    print(f"\n📅 EXPLOSIVE MOVES BY DAY OF WEEK")
    explosive_df["dow"] = explosive_df["datetime"].dt.day_name()
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow_counts = explosive_df.groupby("dow").size().reindex(dow_order, fill_value=0)
    for day in dow_order:
        bar = "█" * dow_counts[day]
        print(f"  {day:10s} | {dow_counts[day]:3d} | {bar}")

    print("\n" + "=" * 70)
    print("  DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
