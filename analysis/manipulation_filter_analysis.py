"""
Manipulation Filter Analysis for Polymarket 15M BTC Binary Markets
==================================================================
Fetches Binance 1-min klines, aggregates to 15M windows, and characterizes
manipulation-suspect windows using multi-level filters.

Key hypothesis: manipulation requires (1) small close gap AND (2) low volume.
"""

import requests
import time
import json
import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

# === Constants ===
BINANCE_API = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
HKT = timezone(timedelta(hours=8))
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Date range for analysis
# 7 days of 1-min data for detailed analysis (last_2min metrics)
# For 15M aggregation we'll use what we can fetch
END_DATE = datetime(2026, 3, 23, tzinfo=timezone.utc)
START_DATE_1M = END_DATE - timedelta(days=7)   # 1-min: last 7 days
START_DATE_15M = datetime(2026, 2, 18, tzinfo=timezone.utc)  # 15M: full 28 days


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int,
                 cache_key: str = None) -> list:
    """Fetch klines from Binance with pagination and caching."""
    if cache_key:
        cache_file = CACHE_DIR / f"{cache_key}.json"
        if cache_file.exists():
            print(f"  [cache hit] {cache_key}")
            with open(cache_file) as f:
                return json.load(f)

    all_klines = []
    current_start = start_ms
    limit = 1000

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": limit,
        }
        resp = requests.get(BINANCE_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        all_klines.extend(data)
        # Next batch starts after the last kline's close time
        current_start = data[-1][6] + 1  # close_time + 1ms
        print(f"  Fetched {len(data)} klines, total={len(all_klines)}, "
              f"up to {datetime.fromtimestamp(data[-1][0]/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")

        if len(data) < limit:
            break

        time.sleep(0.15)  # rate limit

    if cache_key and all_klines:
        with open(CACHE_DIR / f"{cache_key}.json", "w") as f:
            json.dump(all_klines, f)
        print(f"  [cached] {cache_key}: {len(all_klines)} klines")

    return all_klines


def klines_to_df(raw_klines: list) -> pd.DataFrame:
    """Convert raw Binance klines to DataFrame."""
    df = pd.DataFrame(raw_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_volume", "taker_buy_quote_volume"]:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_timestamp"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


def aggregate_15m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1-min klines into 15-min windows with manipulation metrics."""
    # Assign each 1-min bar to its 15-min window
    df_1m = df_1m.copy()
    df_1m["window_start"] = df_1m["timestamp"].dt.floor("15min")
    # Position within the 15-min window (0-14)
    df_1m["min_in_window"] = ((df_1m["timestamp"] - df_1m["window_start"])
                               .dt.total_seconds() / 60).astype(int)

    windows = []
    for window_start, group in df_1m.groupby("window_start"):
        if len(group) < 14:  # skip incomplete windows
            continue

        group = group.sort_values("timestamp")

        w_open = group.iloc[0]["open"]
        w_close = group.iloc[-1]["close"]
        w_high = group["high"].max()
        w_low = group["low"].min()
        w_volume = group["volume"].sum()
        w_trades = group["trades"].sum()

        close_gap = abs(w_close - w_open)
        w_range = w_high - w_low

        # 1-min absolute returns
        group_returns = group["close"].pct_change().abs() * 100  # in %
        avg_1min_return = group_returns.mean()

        # Last 2 minutes metrics (minutes 13-14, i.e. the last 2 bars)
        last_2 = group[group["min_in_window"] >= 13]
        first_13 = group[group["min_in_window"] < 13]

        last_2min_volume = last_2["volume"].sum()
        first_13min_volume = first_13["volume"].sum()
        last_2min_volume_ratio = (last_2min_volume / first_13min_volume
                                   if first_13min_volume > 0 else 0)

        # Max absolute 1-min return in last 2 minutes
        last_2_returns = last_2["close"].pct_change().abs() * 100
        # Also include the return from min 12->13
        if len(first_13) > 0 and len(last_2) > 0:
            transition_return = abs(last_2.iloc[0]["open"] - first_13.iloc[-1]["close"]) / first_13.iloc[-1]["close"] * 100
            last_2min_max_return = max(last_2_returns.max() if not last_2_returns.isna().all() else 0,
                                        transition_return)
        else:
            last_2min_max_return = last_2_returns.max() if not last_2_returns.isna().all() else 0

        # Direction at T-120s (beginning of last 2 min)
        if len(first_13) > 0:
            price_at_t120 = first_13.iloc[-1]["close"]
            direction_at_t120 = "UP" if price_at_t120 >= w_open else "DOWN"
        else:
            price_at_t120 = w_open
            direction_at_t120 = "UP"

        # Final outcome direction
        final_direction = "UP" if w_close >= w_open else "DOWN"

        # Reversal = direction changed in last 2 min
        is_reversal = direction_at_t120 != final_direction

        # Bridge-style outcome (for Polymarket): UP if close >= open
        # The "bridge" bet would be on the direction that was winning at T-120s
        # Manipulation = flip in last 2 min
        bridge_direction_at_t120 = direction_at_t120
        bridge_outcome = final_direction

        windows.append({
            "window_start": window_start,
            "open": w_open,
            "close": w_close,
            "high": w_high,
            "low": w_low,
            "volume": w_volume,
            "trades": w_trades,
            "close_gap": close_gap,
            "range": w_range,
            "avg_1min_return": avg_1min_return,
            "last_2min_max_return": last_2min_max_return,
            "last_2min_volume_ratio": last_2min_volume_ratio,
            "last_2min_volume": last_2min_volume,
            "first_13min_volume": first_13min_volume,
            "price_at_t120": price_at_t120,
            "direction_at_t120": direction_at_t120,
            "final_direction": final_direction,
            "is_reversal": is_reversal,
        })

    return pd.DataFrame(windows)


def aggregate_15m_from_15m_klines(raw_15m: list) -> pd.DataFrame:
    """For the 28-day period, create 15M windows from 15M klines (no last_2min metrics)."""
    df = klines_to_df(raw_15m)
    df["close_gap"] = (df["close"] - df["open"]).abs()
    df["range"] = df["high"] - df["low"]
    df["window_start"] = df["timestamp"]
    # No last_2min metrics available for these
    df["avg_1min_return"] = np.nan
    df["last_2min_max_return"] = np.nan
    df["last_2min_volume_ratio"] = np.nan
    df["last_2min_volume"] = np.nan
    df["first_13min_volume"] = np.nan
    df["price_at_t120"] = np.nan
    df["direction_at_t120"] = np.where(df["close"] >= df["open"], "UP", "DOWN")  # approx
    df["final_direction"] = np.where(df["close"] >= df["open"], "UP", "DOWN")
    df["is_reversal"] = False  # can't determine without 1-min data

    return df[["window_start", "open", "close", "high", "low", "volume", "trades",
               "close_gap", "range", "avg_1min_return", "last_2min_max_return",
               "last_2min_volume_ratio", "last_2min_volume", "first_13min_volume",
               "price_at_t120", "direction_at_t120", "final_direction", "is_reversal"]]


def get_session(hour_hkt: int) -> str:
    """Map HKT hour to trading session."""
    if 8 <= hour_hkt < 16:
        return "Asia"
    elif 16 <= hour_hkt < 21:
        return "EU"
    elif 21 <= hour_hkt or hour_hkt < 2:
        return "US"
    else:
        return "Eve"  # 2-8 HKT


def run_analysis():
    """Main analysis pipeline."""
    print("=" * 70)
    print("MANIPULATION FILTER ANALYSIS — Polymarket 15M BTC")
    print("=" * 70)

    # === Step 1: Fetch Data ===
    print("\n[1] Fetching Binance data...")

    # 1a: 15M klines for full 28-day period (for volume/gap statistics)
    print("\n  --- 15M klines (28 days) ---")
    start_ms_15m = int(START_DATE_15M.timestamp() * 1000)
    end_ms = int(END_DATE.timestamp() * 1000)
    raw_15m = fetch_klines(SYMBOL, "15m", start_ms_15m, end_ms,
                           cache_key="btc_15m_20260218_20260323")

    # 1b: 1M klines for last 7 days (for last_2min metrics)
    print("\n  --- 1M klines (7 days) ---")
    start_ms_1m = int(START_DATE_1M.timestamp() * 1000)
    raw_1m = fetch_klines(SYMBOL, "1m", start_ms_1m, end_ms,
                          cache_key="btc_1m_7d_20260316_20260323")

    print(f"\n  15M klines: {len(raw_15m)}")
    print(f"  1M klines:  {len(raw_1m)}")

    # === Step 2: Process Data ===
    print("\n[2] Processing data...")

    # 15M windows from 15M klines (full 28 days, no last_2min detail)
    df_15m_full = aggregate_15m_from_15m_klines(raw_15m)
    print(f"  28-day 15M windows: {len(df_15m_full)}")

    # 15M windows from 1M klines (7 days, with last_2min detail)
    df_1m = klines_to_df(raw_1m)
    df_15m_detailed = aggregate_15m(df_1m)
    print(f"  7-day detailed 15M windows: {len(df_15m_detailed)}")

    # === Step 3: Statistics ===
    print("\n[3] Baseline Statistics (28-day 15M windows)")
    print("-" * 50)

    vol_median = df_15m_full["volume"].median()
    vol_p25 = df_15m_full["volume"].quantile(0.25)
    vol_p75 = df_15m_full["volume"].quantile(0.75)
    gap_median = df_15m_full["close_gap"].median()
    gap_p25 = df_15m_full["close_gap"].quantile(0.25)
    range_median = df_15m_full["range"].median()

    print(f"  Volume (BTC):  median={vol_median:.2f}  P25={vol_p25:.2f}  P75={vol_p75:.2f}")
    print(f"  Close gap ($): median={gap_median:.1f}  P25={gap_p25:.1f}")
    print(f"  Range ($):     median={range_median:.1f}")
    print(f"  Total windows: {len(df_15m_full)}")

    # 7-day detailed stats
    print(f"\n  --- 7-day Detailed Statistics ---")
    if len(df_15m_detailed) > 0:
        overall_reversal_rate = df_15m_detailed["is_reversal"].mean()
        avg_1min_ret = df_15m_detailed["avg_1min_return"].mean()
        avg_l2m_ratio = df_15m_detailed["last_2min_volume_ratio"].mean()
        print(f"  Overall reversal rate:     {overall_reversal_rate:.3f} ({overall_reversal_rate*100:.1f}%)")
        print(f"  Avg 1-min return:          {avg_1min_ret:.4f}%")
        print(f"  Avg last-2min vol ratio:   {avg_l2m_ratio:.3f}")
    else:
        overall_reversal_rate = 0
        print("  WARNING: No detailed windows available")

    # === Step 4: Filter Levels ===
    print("\n" + "=" * 70)
    print("[4] FILTER LEVEL ANALYSIS (7-day detailed data)")
    print("=" * 70)

    # Use 28-day statistics for thresholds but 7-day data for reversal analysis
    df = df_15m_detailed.copy()
    n_total = len(df)

    if n_total == 0:
        print("ERROR: No detailed data to analyze")
        return

    # Recalculate percentiles on the 7-day data for consistency
    vol_median_7d = df["volume"].median()
    vol_p25_7d = df["volume"].quantile(0.25)

    filters = {
        "Level 1 (loose)": {
            "desc": "close_gap < $100 AND volume < median",
            "mask": (df["close_gap"] < 100) & (df["volume"] < vol_median_7d),
        },
        "Level 2 (medium)": {
            "desc": "close_gap < $50 AND volume < P25",
            "mask": (df["close_gap"] < 50) & (df["volume"] < vol_p25_7d),
        },
        "Level 3 (tight)": {
            "desc": "close_gap < $30 AND volume < P25 AND last_2min spike",
            "mask": (
                (df["close_gap"] < 30)
                & (df["volume"] < vol_p25_7d)
                & (df["last_2min_max_return"] > 2 * df["avg_1min_return"])
            ),
        },
    }

    # Also add volume-ratio enhanced filters
    filters["Level 2b (vol-ratio)"] = {
        "desc": "close_gap < $50 AND volume < P25 AND last_2min_vol_ratio > 0.3",
        "mask": (
            (df["close_gap"] < 50)
            & (df["volume"] < vol_p25_7d)
            & (df["last_2min_volume_ratio"] > 0.3)
        ),
    }
    filters["Level 3b (tight+ratio)"] = {
        "desc": "close_gap < $30 AND volume < P25 AND l2m_spike AND l2m_vol_ratio > 0.3",
        "mask": (
            (df["close_gap"] < 30)
            & (df["volume"] < vol_p25_7d)
            & (df["last_2min_max_return"] > 2 * df["avg_1min_return"])
            & (df["last_2min_volume_ratio"] > 0.3)
        ),
    }

    print(f"\n  Baseline: {n_total} windows, "
          f"reversal rate = {overall_reversal_rate:.1%}")
    print(f"  Volume thresholds: median={vol_median_7d:.2f} BTC, P25={vol_p25_7d:.2f} BTC")
    print()

    results_table = []
    for name, filt in filters.items():
        mask = filt["mask"]
        flagged = df[mask]
        n_flagged = len(flagged)
        pct_of_total = n_flagged / n_total if n_total > 0 else 0

        if n_flagged > 0:
            reversal_rate = flagged["is_reversal"].mean()
            precision_lift = reversal_rate / overall_reversal_rate if overall_reversal_rate > 0 else 0
        else:
            reversal_rate = 0
            precision_lift = 0

        results_table.append({
            "Filter": name,
            "Description": filt["desc"],
            "Flagged": n_flagged,
            "% of Total": f"{pct_of_total:.1%}",
            "Reversal Rate": f"{reversal_rate:.1%}",
            "Precision Lift": f"{precision_lift:.2f}x",
        })

        print(f"  {name}: {filt['desc']}")
        print(f"    Flagged:        {n_flagged} ({pct_of_total:.1%} of total)")
        print(f"    Reversal rate:  {reversal_rate:.1%} (baseline: {overall_reversal_rate:.1%})")
        print(f"    Precision lift: {precision_lift:.2f}x")
        if n_flagged > 0:
            print(f"    Avg volume:     {flagged['volume'].mean():.2f} BTC")
            print(f"    Avg close_gap:  ${flagged['close_gap'].mean():.1f}")
            print(f"    Avg range:      ${flagged['range'].mean():.1f}")
        print()

    # === Step 5: Time-of-Day Distribution ===
    print("\n" + "=" * 70)
    print("[5] TIME-OF-DAY DISTRIBUTION OF SUSPECT WINDOWS")
    print("=" * 70)

    # Use Level 2 as the primary filter for time analysis
    primary_mask = filters["Level 2 (medium)"]["mask"]
    df_flagged = df[primary_mask].copy()

    if len(df_flagged) > 0:
        df["hour_hkt"] = df["window_start"].dt.tz_convert(HKT).dt.hour
        df["session"] = df["hour_hkt"].apply(get_session)
        df_flagged["hour_hkt"] = df_flagged["window_start"].dt.tz_convert(HKT).dt.hour
        df_flagged["session"] = df_flagged["hour_hkt"].apply(get_session)

        # 2-hour buckets
        df["bucket_2h"] = (df["hour_hkt"] // 2) * 2
        df_flagged["bucket_2h"] = (df_flagged["hour_hkt"] // 2) * 2

        print("\n  --- By 2-hour HKT bucket (Level 2 filter) ---")
        print(f"  {'Bucket':>8} | {'Total':>6} | {'Flagged':>7} | {'Flag%':>6} | {'Rev%':>6}")
        print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}")

        for bucket in sorted(df["bucket_2h"].unique()):
            total_in_bucket = len(df[df["bucket_2h"] == bucket])
            flagged_in_bucket = df_flagged[df_flagged["bucket_2h"] == bucket]
            n_flag = len(flagged_in_bucket)
            flag_pct = n_flag / total_in_bucket if total_in_bucket > 0 else 0
            rev_rate = flagged_in_bucket["is_reversal"].mean() if n_flag > 0 else 0
            print(f"  {bucket:02d}-{bucket+2:02d} HKT | {total_in_bucket:6d} | {n_flag:7d} | {flag_pct:5.1%} | {rev_rate:5.1%}")

        print(f"\n  --- By Session (Level 2 filter) ---")
        print(f"  {'Session':>8} | {'Total':>6} | {'Flagged':>7} | {'Flag%':>6} | {'Rev%':>6}")
        print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}")

        for session in ["Asia", "EU", "US", "Eve"]:
            total_in_session = len(df[df["session"] == session])
            flagged_in_session = df_flagged[df_flagged["session"] == session]
            n_flag = len(flagged_in_session)
            flag_pct = n_flag / total_in_session if total_in_session > 0 else 0
            rev_rate = flagged_in_session["is_reversal"].mean() if n_flag > 0 else 0
            print(f"  {session:>8} | {total_in_session:6d} | {n_flag:7d} | {flag_pct:5.1%} | {rev_rate:5.1%}")
    else:
        print("  No flagged windows for time analysis")

    # === Step 6: Volume Profile ===
    print("\n" + "=" * 70)
    print("[6] VOLUME PROFILE OF SUSPECT WINDOWS")
    print("=" * 70)

    for name, filt in filters.items():
        mask = filt["mask"]
        flagged = df[mask]
        if len(flagged) == 0:
            continue

        avg_vol = flagged["volume"].mean()
        avg_range = flagged["range"].mean()
        # Estimate cost to push $50: if range is X and volume is V,
        # then to move price $50 you need roughly ($50/X) * V BTC
        push_cost_btc = (50 / avg_range * avg_vol) if avg_range > 0 else float("inf")
        # Estimate in USD (using ~$85000 BTC price)
        btc_price = df["close"].mean()
        push_cost_usd = push_cost_btc * btc_price

        print(f"\n  {name}:")
        print(f"    Avg BTC volume:    {avg_vol:.2f} BTC")
        print(f"    Avg range:         ${avg_range:.1f}")
        print(f"    Est. cost to push $50: {push_cost_btc:.2f} BTC (${push_cost_usd:,.0f})")

    # === Step 7: ROI Simulation ===
    print("\n" + "=" * 70)
    print("[7] ROI SIMULATION — Follow the Manipulator")
    print("=" * 70)
    print("  Strategy: At T-120s, if flagged, buy OPPOSITE of current direction")
    print("  (i.e., bet that the last-2-min flip will happen)")
    print()

    for name, filt in filters.items():
        mask = filt["mask"]
        flagged = df[mask].copy()
        n_flagged = len(flagged)

        if n_flagged == 0:
            print(f"  {name}: No flagged windows")
            continue

        # For each flagged window:
        # "our" side = opposite of direction at T-120s
        # If direction_at_t120 is UP, we bet DOWN (we buy the underdog/cheap side)
        # Entry price estimate: if the winning side is at ~0.55, the underdog is at ~0.45
        # Simplification: use close_gap as proxy for how "tight" the market is
        # Tighter gap = higher underdog price (closer to 0.50)

        trades = []
        for _, row in flagged.iterrows():
            our_side = "DOWN" if row["direction_at_t120"] == "UP" else "UP"

            # Estimate entry price (underdog side)
            # With close_gap near 0, both sides ~0.50
            # With larger gap, winning side ~0.55+, underdog ~0.45-
            # Rough model: underdog_price = 0.50 - (close_gap / range) * 0.05
            if row["range"] > 0:
                gap_ratio = min(row["close_gap"] / row["range"], 1.0)
            else:
                gap_ratio = 0
            # More realistic: underdog price is roughly 0.45-0.50 for these tight windows
            entry_price = max(0.40, 0.50 - gap_ratio * 0.10)

            # Did our side win?
            won = (our_side == row["final_direction"])

            if won:
                pnl = 1.0 - entry_price  # pay entry, receive 1.0
                pnl_after_fees = pnl - 0.02  # ~2% Polymarket fees
            else:
                pnl = -entry_price
                pnl_after_fees = pnl

            trades.append({
                "window_start": row["window_start"],
                "our_side": our_side,
                "entry_price": entry_price,
                "won": won,
                "pnl": pnl,
                "pnl_after_fees": pnl_after_fees,
                "is_reversal": row["is_reversal"],
            })

        df_trades = pd.DataFrame(trades)
        n_wins = df_trades["won"].sum()
        win_rate = n_wins / len(df_trades) if len(df_trades) > 0 else 0
        total_pnl = df_trades["pnl"].sum()
        total_pnl_fees = df_trades["pnl_after_fees"].sum()
        avg_entry = df_trades["entry_price"].mean()
        avg_pnl = df_trades["pnl_after_fees"].mean()
        roi_per_trade = avg_pnl / avg_entry if avg_entry > 0 else 0

        # Also: what if we only trade when reversal actually happened?
        reversals = df_trades[df_trades["is_reversal"]]
        non_reversals = df_trades[~df_trades["is_reversal"]]

        print(f"  {name}:")
        print(f"    Trades:     {len(df_trades)}")
        print(f"    Win rate:   {win_rate:.1%}")
        print(f"    Avg entry:  {avg_entry:.3f}")
        print(f"    Total PnL:  {total_pnl:.2f} units (after fees: {total_pnl_fees:.2f})")
        print(f"    Avg PnL:    {avg_pnl:.4f}/trade")
        print(f"    ROI/trade:  {roi_per_trade:.1%}")
        print(f"    Reversals:  {len(reversals)}/{len(df_trades)} "
              f"({len(reversals)/len(df_trades):.1%})")
        if len(reversals) > 0:
            rev_wr = reversals["won"].mean()
            print(f"    WR on reversals:     {rev_wr:.1%}")
        if len(non_reversals) > 0:
            non_rev_wr = non_reversals["won"].mean()
            print(f"    WR on non-reversals: {non_rev_wr:.1%}")
        print()

    # === Step 8: Advanced — Close Gap Distribution ===
    print("\n" + "=" * 70)
    print("[8] CLOSE GAP DISTRIBUTION")
    print("=" * 70)

    percentiles = [5, 10, 25, 50, 75, 90, 95]
    print("\n  Close gap ($) percentiles:")
    for p in percentiles:
        val = df["close_gap"].quantile(p/100)
        print(f"    P{p:2d}: ${val:.1f}")

    print("\n  Close gap distribution:")
    buckets = [(0, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, 200), (200, 500), (500, float("inf"))]
    for lo, hi in buckets:
        count = len(df[(df["close_gap"] >= lo) & (df["close_gap"] < hi)])
        pct = count / n_total
        label = f"${lo}-${hi}" if hi < float("inf") else f"${lo}+"
        rev_rate_bucket = df[(df["close_gap"] >= lo) & (df["close_gap"] < hi)]["is_reversal"].mean()
        print(f"    {label:>12}: {count:4d} ({pct:5.1%}) | reversal rate: {rev_rate_bucket:.1%}")

    # === Step 9: Volume Distribution ===
    print("\n  Volume (BTC) percentiles:")
    for p in percentiles:
        val = df["volume"].quantile(p/100)
        print(f"    P{p:2d}: {val:.2f} BTC")

    # === Step 10: Last 2-min Volume Ratio ===
    print("\n  Last-2min Volume Ratio percentiles:")
    valid_ratio = df["last_2min_volume_ratio"].dropna()
    for p in percentiles:
        val = valid_ratio.quantile(p/100)
        print(f"    P{p:2d}: {val:.3f}")

    # === Final Verdict ===
    print("\n" + "=" * 70)
    print("[FINAL VERDICT]")
    print("=" * 70)

    # Use Level 2 as the benchmark
    l2_mask = filters["Level 2 (medium)"]["mask"]
    l2_flagged = df[l2_mask]
    if len(l2_flagged) > 0:
        l2_reversal = l2_flagged["is_reversal"].mean()
        precision = l2_reversal * 100

        if precision > 40:
            verdict = "ACTIONABLE"
        elif precision > 30:
            verdict = "MARGINAL"
        else:
            verdict = "NOT_USEFUL"

        print(f"\n  Level 2 reversal rate: {l2_reversal:.1%}")
        print(f"  Overall reversal rate: {overall_reversal_rate:.1%}")
        print(f"  Precision lift: {l2_reversal/overall_reversal_rate:.2f}x" if overall_reversal_rate > 0 else "  N/A")
        print(f"\n  VERDICT: {verdict}")
        print(f"  (Criteria: ACTIONABLE >40% precision, MARGINAL 30-40%, NOT_USEFUL <30%)")
    else:
        print("\n  VERDICT: INSUFFICIENT DATA")

    print("\n" + "=" * 70)
    print("Analysis complete.")


if __name__ == "__main__":
    run_analysis()
