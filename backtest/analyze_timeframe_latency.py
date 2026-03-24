#!/usr/bin/env python3
"""
analyze_timeframe_latency.py — Multi-TF analysis: how much move is left after detection?

Design decision: We compare 15m vs 1H squeeze detection to quantify the latency cost
of using 1H candles. The key question is whether 15m trigger + 1H confirmation captures
significantly more of the explosive move than pure 1H detection.

Data: 180 days of BTCUSDT futures klines from Binance (15m + 1H).
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

# --- Project path setup (gotcha: scripts/ not in sys.path) ---
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)

from backtest.fetch_historical import fetch_klines_range

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ─── Constants ───
SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 180
BB_PERIOD = 20
BB_STD = 2.0
BB_WIDTH_PCTL_WINDOW = 100  # rolling window for bb_width percentile
EXPLOSIVE_THRESHOLD = 0.02  # 2% range for 1H candle
SQUEEZE_PCTL_THRESHOLD = 30  # bb_width pctl < 30% = squeeze
ADX_THRESHOLD = 25
VOL_RATIO_THRESHOLD = 0.8


# ─── Helper: Bollinger Bands ───
def compute_bb(df: pd.DataFrame, period: int = BB_PERIOD, std: float = BB_STD) -> pd.DataFrame:
    """Add BB columns: bb_mid, bb_upper, bb_lower, bb_width, bb_width_pctl."""
    df = df.copy()
    df["bb_mid"] = df["close"].rolling(period).mean()
    df["bb_std"] = df["close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + std * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - std * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_width_pctl"] = df["bb_width"].rolling(BB_WIDTH_PCTL_WINDOW).apply(
        lambda x: (x.values[-1] <= x.values).sum() / len(x) * 100, raw=False
    )
    return df


# ─── Helper: ADX (simplified) ───
def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Compute ADX indicator."""
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / period, min_periods=period).mean()
    return df


# ─── Helper: Volume ratio ───
def compute_vol_ratio(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Volume / SMA(volume, period)."""
    df = df.copy()
    df["vol_sma"] = df["volume"].rolling(period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma"].replace(0, np.nan)
    return df


# ─── Data Fetch ───
def fetch_data():
    """Fetch 180d of 15m and 1H BTC klines."""
    now = datetime.now(tz=timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp() * 1000)

    print(f"\n{'='*70}")
    print(f"Fetching {LOOKBACK_DAYS}d of BTCUSDT data...")
    print(f"Range: {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → "
          f"{datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    print(f"{'='*70}")

    df_15m = fetch_klines_range(SYMBOL, "15m", start_ms, end_ms)
    df_1h = fetch_klines_range(SYMBOL, "1h", start_ms, end_ms)

    print(f"\n15m candles: {len(df_15m):,}")
    print(f"1H candles:  {len(df_1h):,}")

    return df_15m, df_1h


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 1: Explosive 1H candle decomposition into 15m sub-candles
# ═══════════════════════════════════════════════════════════════════════
def analysis_1_explosive_decomposition(df_15m: pd.DataFrame, df_1h: pd.DataFrame):
    """For each explosive 1H candle (range > 2%), decompose into 15m sub-candles."""
    print(f"\n{'='*70}")
    print("ANALYSIS 1: Explosive 1H Candle Decomposition")
    print(f"{'='*70}")

    df_1h_bb = compute_bb(df_1h.copy())

    # Identify explosive 1H candles
    df_1h_bb["range_pct"] = (df_1h_bb["high"] - df_1h_bb["low"]) / df_1h_bb["low"] * 100
    explosive = df_1h_bb[df_1h_bb["range_pct"] > EXPLOSIVE_THRESHOLD * 100].copy()
    print(f"\nExplosive 1H candles (range > {EXPLOSIVE_THRESHOLD*100:.0f}%): {len(explosive)}")

    if len(explosive) == 0:
        print("No explosive candles found!")
        return

    results = []

    for idx, row in explosive.iterrows():
        h1_open_time = row["open_time"]
        h1_close_time = row["close_time"]
        h1_open = row["open"]
        h1_close = row["close"]
        h1_high = row["high"]
        h1_low = row["low"]
        h1_range = h1_high - h1_low
        h1_direction = "UP" if h1_close > h1_open else "DOWN"

        # Previous 1H candle's BB (for breakout detection)
        if idx == 0:
            continue
        prev_bb_upper = df_1h_bb.loc[idx - 1, "bb_upper"] if idx - 1 in df_1h_bb.index else np.nan
        prev_bb_lower = df_1h_bb.loc[idx - 1, "bb_lower"] if idx - 1 in df_1h_bb.index else np.nan
        if pd.isna(prev_bb_upper) or pd.isna(prev_bb_lower):
            continue

        # Get the 4 x 15m candles within this 1H window
        sub = df_15m[
            (df_15m["open_time"] >= h1_open_time) &
            (df_15m["open_time"] < h1_close_time)
        ].copy()

        if len(sub) < 2:
            continue

        # Find the first 15m candle where price crosses previous 1H BB
        breakout_15m_idx = None
        for i, (_, s_row) in enumerate(sub.iterrows()):
            if h1_direction == "UP" and s_row["high"] > prev_bb_upper:
                breakout_15m_idx = i
                break
            elif h1_direction == "DOWN" and s_row["low"] < prev_bb_lower:
                breakout_15m_idx = i
                break

        if breakout_15m_idx is None:
            # No clear BB breakout within the 15m candles
            continue

        # How much of the 1H move happened before vs after breakout 15m candle
        breakout_candle = sub.iloc[breakout_15m_idx]

        if h1_direction == "UP":
            move_before = breakout_candle["close"] - h1_open
            move_after = h1_close - breakout_candle["close"]
            total_move = h1_close - h1_open
            remaining_from_15m_close = h1_high - breakout_candle["close"]
            peak = h1_high
        else:
            move_before = h1_open - breakout_candle["close"]
            move_after = breakout_candle["close"] - h1_close
            total_move = h1_open - h1_close
            remaining_from_15m_close = breakout_candle["close"] - h1_low
            peak = h1_low

        if abs(total_move) < 1e-8:
            continue

        pct_before = move_before / total_move * 100 if total_move != 0 else 0
        pct_after = move_after / total_move * 100 if total_move != 0 else 0
        pct_remaining = remaining_from_15m_close / h1_range * 100

        # If we entered at 1H close: what % remains in NEXT 1H candle?
        next_idx = idx + 1
        if next_idx in df_1h_bb.index:
            next_row = df_1h_bb.loc[next_idx]
            if h1_direction == "UP":
                next_continuation = next_row["high"] - h1_close
            else:
                next_continuation = h1_close - next_row["low"]
            pct_remaining_1h_entry = next_continuation / h1_range * 100
        else:
            pct_remaining_1h_entry = np.nan

        results.append({
            "timestamp": row["timestamp"],
            "direction": h1_direction,
            "h1_range_pct": row["range_pct"],
            "breakout_15m_position": breakout_15m_idx + 1,  # 1-indexed
            "pct_move_before_breakout": pct_before,
            "pct_move_after_breakout": pct_after,
            "pct_remaining_from_15m_close": pct_remaining,
            "pct_remaining_from_1h_close": pct_remaining_1h_entry,
        })

    if not results:
        print("No decomposable explosive candles found.")
        return pd.DataFrame()

    df_res = pd.DataFrame(results)
    print(f"\nDecomposable explosive candles: {len(df_res)}")

    print(f"\n--- Breakout Position (which 15m candle within the 1H had the breakout) ---")
    pos_counts = df_res["breakout_15m_position"].value_counts().sort_index()
    for pos, count in pos_counts.items():
        print(f"  15m candle #{pos:.0f}: {count} times ({count/len(df_res)*100:.1f}%)")

    print(f"\n--- Move Distribution ---")
    print(f"  Avg % of move BEFORE 15m breakout candle close: {df_res['pct_move_before_breakout'].mean():.1f}%")
    print(f"  Avg % of move AFTER 15m breakout candle close:  {df_res['pct_move_after_breakout'].mean():.1f}%")
    print(f"  Avg % of 1H range remaining from 15m entry:     {df_res['pct_remaining_from_15m_close'].mean():.1f}%")
    valid_1h = df_res["pct_remaining_from_1h_close"].dropna()
    print(f"  Avg % of 1H range remaining from 1H entry:      {valid_1h.mean():.1f}%")

    print(f"\n--- By Direction ---")
    for d in ["UP", "DOWN"]:
        sub = df_res[df_res["direction"] == d]
        if len(sub) == 0:
            continue
        print(f"  {d} ({len(sub)} events):")
        print(f"    Avg breakout at 15m candle #{sub['breakout_15m_position'].mean():.1f}")
        print(f"    Remaining from 15m entry: {sub['pct_remaining_from_15m_close'].mean():.1f}%")
        v = sub["pct_remaining_from_1h_close"].dropna()
        print(f"    Remaining from 1H entry:  {v.mean():.1f}%")

    return df_res


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 2: Continuation after explosive 1H candle
# ═══════════════════════════════════════════════════════════════════════
def analysis_2_continuation(df_1h: pd.DataFrame):
    """After an explosive 1H candle, does the move continue?"""
    print(f"\n{'='*70}")
    print("ANALYSIS 2: Continuation After Explosive 1H Candle")
    print(f"{'='*70}")

    df = df_1h.copy()
    df["range_pct"] = (df["high"] - df["low"]) / df["low"] * 100
    df["direction"] = np.where(df["close"] > df["open"], "UP", "DOWN")

    explosive_mask = df["range_pct"] > EXPLOSIVE_THRESHOLD * 100
    explosive_indices = df.index[explosive_mask].tolist()
    print(f"\nExplosive 1H candles: {len(explosive_indices)}")

    continuation_results = []

    for idx in explosive_indices:
        row = df.loc[idx]
        h1_range = row["high"] - row["low"]
        direction = row["direction"]

        # Look ahead 1-3 hours
        for lookahead in [1, 2, 3]:
            future_idx = idx + lookahead
            if future_idx not in df.index:
                continue
            future = df.loc[future_idx]

            if direction == "UP":
                continuation = future["close"] - row["close"]
                future_direction = "SAME" if future["close"] > future["open"] else "REVERSE"
                max_continuation = future["high"] - row["close"]
            else:
                continuation = row["close"] - future["close"]
                future_direction = "SAME" if future["close"] < future["open"] else "REVERSE"
                max_continuation = row["close"] - future["low"]

            continuation_results.append({
                "timestamp": row["timestamp"],
                "direction": direction,
                "h1_range": h1_range,
                "lookahead_hours": lookahead,
                "continuation_pct": continuation / row["close"] * 100,
                "continuation_vs_range": continuation / h1_range * 100,
                "max_continuation_vs_range": max_continuation / h1_range * 100,
                "future_direction": future_direction,
            })

    if not continuation_results:
        print("No continuation data.")
        return

    df_cont = pd.DataFrame(continuation_results)

    for la in [1, 2, 3]:
        sub = df_cont[df_cont["lookahead_hours"] == la]
        if len(sub) == 0:
            continue
        print(f"\n--- Next {la}H after explosive candle ---")
        print(f"  Avg continuation (% of price): {sub['continuation_pct'].mean():+.3f}%")
        print(f"  Avg continuation (% of 1H range): {sub['continuation_vs_range'].mean():+.1f}%")
        print(f"  Avg max favorable move (% of 1H range): {sub['max_continuation_vs_range'].mean():.1f}%")
        same = (sub["future_direction"] == "SAME").sum()
        reverse = (sub["future_direction"] == "REVERSE").sum()
        print(f"  Same direction: {same} ({same/len(sub)*100:.1f}%) | Reversal: {reverse} ({reverse/len(sub)*100:.1f}%)")
        gt50 = (sub["continuation_vs_range"] > 50).sum()
        print(f"  Continuation > 50% of 1H range: {gt50} ({gt50/len(sub)*100:.1f}%)")

    # Cumulative continuation (1-3H total)
    print(f"\n--- Cumulative Continuation (sum of next 1-3H) ---")
    cum = df_cont.groupby("timestamp").agg(
        total_cont_pct=("continuation_pct", "sum"),
        total_cont_vs_range=("continuation_vs_range", "sum"),
    ).reset_index()
    print(f"  Avg total continuation (% of price):  {cum['total_cont_pct'].mean():+.3f}%")
    print(f"  Avg total continuation (% of range):  {cum['total_cont_vs_range'].mean():+.1f}%")
    positive = (cum["total_cont_vs_range"] > 0).sum()
    print(f"  Positive continuation: {positive}/{len(cum)} ({positive/len(cum)*100:.1f}%)")


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 3: 15m Squeeze Detection vs 1H
# ═══════════════════════════════════════════════════════════════════════
def analysis_3_15m_squeeze(df_15m: pd.DataFrame, df_1h: pd.DataFrame):
    """Compare 15m squeeze breakout detection to 1H."""
    print(f"\n{'='*70}")
    print("ANALYSIS 3: 15m Squeeze Detection vs 1H")
    print(f"{'='*70}")

    df_15m_bb = compute_bb(df_15m.copy())
    df_1h_bb = compute_bb(df_1h.copy())

    # 15m squeeze breakouts: bb_width_pctl < 30 + price breaks BB
    df_15m_bb["in_squeeze"] = df_15m_bb["bb_width_pctl"] < SQUEEZE_PCTL_THRESHOLD

    # Breakout: price was in squeeze on prev candle, now breaks BB
    df_15m_bb["prev_in_squeeze"] = df_15m_bb["in_squeeze"].shift(1)
    df_15m_bb["breaks_upper"] = df_15m_bb["close"] > df_15m_bb["bb_upper"]
    df_15m_bb["breaks_lower"] = df_15m_bb["close"] < df_15m_bb["bb_lower"]
    df_15m_bb["squeeze_breakout"] = (
        df_15m_bb["prev_in_squeeze"] &
        (df_15m_bb["breaks_upper"] | df_15m_bb["breaks_lower"])
    )
    df_15m_bb["breakout_direction"] = np.where(
        df_15m_bb["breaks_upper"], "UP",
        np.where(df_15m_bb["breaks_lower"], "DOWN", "NONE")
    )

    breakouts_15m = df_15m_bb[df_15m_bb["squeeze_breakout"]].copy()
    print(f"\n15m squeeze breakouts: {len(breakouts_15m)}")

    # 1H squeeze breakouts
    df_1h_bb["in_squeeze"] = df_1h_bb["bb_width_pctl"] < SQUEEZE_PCTL_THRESHOLD
    df_1h_bb["prev_in_squeeze"] = df_1h_bb["in_squeeze"].shift(1)
    df_1h_bb["breaks_upper"] = df_1h_bb["close"] > df_1h_bb["bb_upper"]
    df_1h_bb["breaks_lower"] = df_1h_bb["close"] < df_1h_bb["bb_lower"]
    df_1h_bb["squeeze_breakout"] = (
        df_1h_bb["prev_in_squeeze"] &
        (df_1h_bb["breaks_upper"] | df_1h_bb["breaks_lower"])
    )
    df_1h_bb["breakout_direction"] = np.where(
        df_1h_bb["breaks_upper"], "UP",
        np.where(df_1h_bb["breaks_lower"], "DOWN", "NONE")
    )

    breakouts_1h = df_1h_bb[df_1h_bb["squeeze_breakout"]].copy()
    print(f"1H squeeze breakouts:  {len(breakouts_1h)}")

    # Match 15m breakouts to 1H breakouts: does 15m detect first?
    matched = 0
    early_detections = []

    for _, h1_row in breakouts_1h.iterrows():
        h1_start = h1_row["open_time"]
        h1_end = h1_row["close_time"]
        # Look for 15m breakouts in the same 1H window or up to 4 hours before
        window_start = h1_start - 4 * 3600 * 1000  # 4H before
        nearby_15m = breakouts_15m[
            (breakouts_15m["open_time"] >= window_start) &
            (breakouts_15m["open_time"] <= h1_end)
        ]
        if len(nearby_15m) > 0:
            first_15m = nearby_15m.iloc[0]
            time_diff_min = (h1_end - first_15m["close_time"]) / 1000 / 60
            if time_diff_min > 0:  # 15m detected earlier
                early_detections.append(time_diff_min)
                matched += 1

    print(f"\n15m detected before 1H: {matched}/{len(breakouts_1h)} ({matched/max(1,len(breakouts_1h))*100:.1f}%)")
    if early_detections:
        print(f"  Avg earlier by: {np.mean(early_detections):.0f} min")
        print(f"  Median earlier by: {np.median(early_detections):.0f} min")
        print(f"  Max earlier by: {np.max(early_detections):.0f} min")

    # After 15m breakout: what's the average remaining move in next 1H / 4H?
    print(f"\n--- Remaining Move After 15m Squeeze Breakout ---")
    remaining_1h = []
    remaining_4h = []

    for _, row in breakouts_15m.iterrows():
        entry_price = row["close"]
        entry_time = row["close_time"]
        direction = row["breakout_direction"]

        # Next 1H: find 15m candles in next 60 min
        future_1h = df_15m[
            (df_15m["open_time"] > entry_time) &
            (df_15m["open_time"] <= entry_time + 3600 * 1000)
        ]
        if len(future_1h) > 0:
            if direction == "UP":
                max_move = (future_1h["high"].max() - entry_price) / entry_price * 100
            else:
                max_move = (entry_price - future_1h["low"].min()) / entry_price * 100
            remaining_1h.append(max_move)

        # Next 4H
        future_4h = df_15m[
            (df_15m["open_time"] > entry_time) &
            (df_15m["open_time"] <= entry_time + 4 * 3600 * 1000)
        ]
        if len(future_4h) > 0:
            if direction == "UP":
                max_move = (future_4h["high"].max() - entry_price) / entry_price * 100
            else:
                max_move = (entry_price - future_4h["low"].min()) / entry_price * 100
            remaining_4h.append(max_move)

    if remaining_1h:
        arr = np.array(remaining_1h)
        print(f"  Next 1H: avg max favorable = {arr.mean():.3f}%, median = {np.median(arr):.3f}%")
        print(f"           > 0.5%: {(arr > 0.5).sum()}/{len(arr)} ({(arr > 0.5).sum()/len(arr)*100:.1f}%)")
        print(f"           > 1.0%: {(arr > 1.0).sum()}/{len(arr)} ({(arr > 1.0).sum()/len(arr)*100:.1f}%)")
    if remaining_4h:
        arr = np.array(remaining_4h)
        print(f"  Next 4H: avg max favorable = {arr.mean():.3f}%, median = {np.median(arr):.3f}%")
        print(f"           > 1.0%: {(arr > 1.0).sum()}/{len(arr)} ({(arr > 1.0).sum()/len(arr)*100:.1f}%)")
        print(f"           > 2.0%: {(arr > 2.0).sum()}/{len(arr)} ({(arr > 2.0).sum()/len(arr)*100:.1f}%)")

    return breakouts_15m, breakouts_1h


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 4: Latency Table
# ═══════════════════════════════════════════════════════════════════════
def analysis_4_latency_table(df_15m: pd.DataFrame, df_1h: pd.DataFrame):
    """Create latency comparison table for different detection timeframes."""
    print(f"\n{'='*70}")
    print("ANALYSIS 4: Latency Table — Detection Delay vs Move Captured")
    print(f"{'='*70}")

    # Strategy: for each "explosive event" (defined as 4H window with > 3% range),
    # measure when each TF would detect it and how much remains.

    # Build 4H candles from 1H
    df_1h_copy = df_1h.copy()
    df_1h_copy["group_4h"] = df_1h_copy.index // 4

    events_4h = df_1h_copy.groupby("group_4h").agg(
        open_time=("open_time", "first"),
        close_time=("close_time", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        timestamp=("timestamp", "first"),
    ).reset_index(drop=True)
    events_4h["range_pct"] = (events_4h["high"] - events_4h["low"]) / events_4h["low"] * 100
    events_4h["direction"] = np.where(events_4h["close"] > events_4h["open"], "UP", "DOWN")

    # Focus on significant moves (> 2% 4H range)
    big_moves = events_4h[events_4h["range_pct"] > 2.0].copy()
    print(f"\n4H windows with > 2% range: {len(big_moves)}")

    latency_results = {"15m": [], "1h": [], "4h": []}

    for _, event in big_moves.iterrows():
        ev_start = event["open_time"]
        ev_end = event["close_time"]
        ev_open = event["open"]
        ev_direction = event["direction"]

        if ev_direction == "UP":
            peak = event["high"]
            total_move = peak - ev_open
        else:
            peak = event["low"]
            total_move = ev_open - peak

        if total_move <= 0:
            continue

        # For each TF, find when the move is "detected" (candle close that breaks
        # the opening price by > 0.3%)
        detection_threshold = ev_open * 0.003  # 0.3% from event open

        # 15m detection
        sub_15m = df_15m[
            (df_15m["open_time"] >= ev_start) &
            (df_15m["open_time"] <= ev_end)
        ]
        detected_15m = False
        for _, c in sub_15m.iterrows():
            if ev_direction == "UP" and c["close"] > ev_open + detection_threshold:
                delay_min = (c["close_time"] - ev_start) / 1000 / 60
                if ev_direction == "UP":
                    remaining = peak - c["close"]
                else:
                    remaining = c["close"] - peak
                latency_results["15m"].append({
                    "delay_min": delay_min,
                    "remaining_pct": remaining / ev_open * 100,
                    "total_move_pct": total_move / ev_open * 100,
                    "captured_pct": max(0, remaining / total_move * 100),
                })
                detected_15m = True
                break
            elif ev_direction == "DOWN" and c["close"] < ev_open - detection_threshold:
                delay_min = (c["close_time"] - ev_start) / 1000 / 60
                remaining = c["close"] - peak
                latency_results["15m"].append({
                    "delay_min": delay_min,
                    "remaining_pct": remaining / ev_open * 100,
                    "total_move_pct": total_move / ev_open * 100,
                    "captured_pct": max(0, remaining / total_move * 100),
                })
                detected_15m = True
                break

        # 1H detection
        sub_1h = df_1h[
            (df_1h["open_time"] >= ev_start) &
            (df_1h["open_time"] <= ev_end)
        ]
        for _, c in sub_1h.iterrows():
            if ev_direction == "UP" and c["close"] > ev_open + detection_threshold:
                delay_min = (c["close_time"] - ev_start) / 1000 / 60
                remaining = peak - c["close"]
                latency_results["1h"].append({
                    "delay_min": delay_min,
                    "remaining_pct": remaining / ev_open * 100,
                    "total_move_pct": total_move / ev_open * 100,
                    "captured_pct": max(0, remaining / total_move * 100),
                })
                break
            elif ev_direction == "DOWN" and c["close"] < ev_open - detection_threshold:
                delay_min = (c["close_time"] - ev_start) / 1000 / 60
                remaining = c["close"] - peak
                latency_results["1h"].append({
                    "delay_min": delay_min,
                    "remaining_pct": remaining / ev_open * 100,
                    "total_move_pct": total_move / ev_open * 100,
                    "captured_pct": max(0, remaining / total_move * 100),
                })
                break

        # 4H detection (= the full window close)
        delay_min = (ev_end - ev_start) / 1000 / 60
        if ev_direction == "UP":
            remaining = peak - event["close"]
        else:
            remaining = event["close"] - peak
        latency_results["4h"].append({
            "delay_min": delay_min,
            "remaining_pct": remaining / ev_open * 100,
            "total_move_pct": total_move / ev_open * 100,
            "captured_pct": max(0, remaining / total_move * 100),
        })

    # Print latency table
    print(f"\n{'TF':<6} {'Avg Delay':>12} {'Avg Remaining':>16} {'Avg Total Move':>16} {'% Captured':>12} {'N':>6}")
    print("-" * 70)

    for tf in ["15m", "1h", "4h"]:
        data = latency_results[tf]
        if not data:
            continue
        df_tf = pd.DataFrame(data)
        avg_delay = df_tf["delay_min"].mean()
        avg_remaining = df_tf["remaining_pct"].mean()
        avg_total = df_tf["total_move_pct"].mean()
        avg_captured = df_tf["captured_pct"].mean()
        n = len(df_tf)

        delay_str = f"{avg_delay:.0f} min"
        print(f"{tf:<6} {delay_str:>12} {avg_remaining:>15.3f}% {avg_total:>15.3f}% {avg_captured:>11.1f}% {n:>6}")

    # Bonus: median values
    print(f"\n{'TF':<6} {'Med Delay':>12} {'Med Remaining':>16} {'Med Captured':>14}")
    print("-" * 50)
    for tf in ["15m", "1h", "4h"]:
        data = latency_results[tf]
        if not data:
            continue
        df_tf = pd.DataFrame(data)
        print(f"{tf:<6} {df_tf['delay_min'].median():>11.0f}m {df_tf['remaining_pct'].median():>15.3f}% {df_tf['captured_pct'].median():>13.1f}%")


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 5: Hybrid — 1H Squeeze Confirmation + 15m Trigger
# ═══════════════════════════════════════════════════════════════════════
def analysis_5_hybrid(df_15m: pd.DataFrame, df_1h: pd.DataFrame):
    """
    The key question: 1H squeeze state confirmed + 15m trigger.
    1H: bb_width_pctl < 30 + ADX < 25 + vol_ratio < 0.8
    15m: price breaks 1H BB upper/lower
    """
    print(f"\n{'='*70}")
    print("ANALYSIS 5: HYBRID — 1H Squeeze Confirmation + 15m BB Break Trigger")
    print(f"{'='*70}")

    df_1h_full = compute_bb(df_1h.copy())
    df_1h_full = compute_adx(df_1h_full)
    df_1h_full = compute_vol_ratio(df_1h_full)

    # 1H squeeze state
    df_1h_full["squeeze_state"] = (
        (df_1h_full["bb_width_pctl"] < SQUEEZE_PCTL_THRESHOLD) &
        (df_1h_full["adx"] < ADX_THRESHOLD) &
        (df_1h_full["vol_ratio"] < VOL_RATIO_THRESHOLD)
    )

    squeeze_hours = df_1h_full[df_1h_full["squeeze_state"]].copy()
    print(f"\n1H candles in squeeze state (pctl<{SQUEEZE_PCTL_THRESHOLD} + ADX<{ADX_THRESHOLD} + vol<{VOL_RATIO_THRESHOLD}): "
          f"{len(squeeze_hours)} / {len(df_1h_full)} ({len(squeeze_hours)/max(1,len(df_1h_full))*100:.1f}%)")

    # For each squeeze hour, check if any 15m candle breaks the 1H BB
    hybrid_signals = []

    for idx, h1_row in squeeze_hours.iterrows():
        h1_bb_upper = h1_row["bb_upper"]
        h1_bb_lower = h1_row["bb_lower"]
        h1_start = h1_row["open_time"]
        h1_end = h1_row["close_time"]

        # Get 15m candles in this 1H window
        sub_15m = df_15m[
            (df_15m["open_time"] >= h1_start) &
            (df_15m["open_time"] < h1_end)
        ]

        for _, m15_row in sub_15m.iterrows():
            direction = None
            if m15_row["close"] > h1_bb_upper:
                direction = "UP"
            elif m15_row["close"] < h1_bb_lower:
                direction = "DOWN"

            if direction is None:
                continue

            entry_price = m15_row["close"]
            entry_time = m15_row["close_time"]

            # Measure remaining move: next 1H, 2H, 4H
            for horizon_hours, horizon_label in [(1, "1H"), (2, "2H"), (4, "4H")]:
                future = df_15m[
                    (df_15m["open_time"] > entry_time) &
                    (df_15m["open_time"] <= entry_time + horizon_hours * 3600 * 1000)
                ]
                if len(future) == 0:
                    continue

                if direction == "UP":
                    max_favorable = (future["high"].max() - entry_price) / entry_price * 100
                    close_move = (future.iloc[-1]["close"] - entry_price) / entry_price * 100
                else:
                    max_favorable = (entry_price - future["low"].min()) / entry_price * 100
                    close_move = (entry_price - future.iloc[-1]["close"]) / entry_price * 100

                hybrid_signals.append({
                    "timestamp": h1_row["timestamp"],
                    "entry_time": m15_row["timestamp"],
                    "direction": direction,
                    "horizon": horizon_label,
                    "max_favorable_pct": max_favorable,
                    "close_move_pct": close_move,
                })

            break  # Only take the first breakout per 1H window

    if not hybrid_signals:
        print("No hybrid signals found.")
        return

    df_hyb = pd.DataFrame(hybrid_signals)
    unique_signals = df_hyb.groupby("timestamp").first().reset_index()
    print(f"\nHybrid signals (unique 1H windows): {len(unique_signals)}")
    print(f"  UP: {len(unique_signals[unique_signals['direction']=='UP'])}")
    print(f"  DOWN: {len(unique_signals[unique_signals['direction']=='DOWN'])}")

    print(f"\n--- Remaining Move After Hybrid Signal ---")
    for horizon in ["1H", "2H", "4H"]:
        sub = df_hyb[df_hyb["horizon"] == horizon]
        if len(sub) == 0:
            continue
        print(f"\n  Horizon {horizon} ({len(sub)} signals):")
        print(f"    Avg max favorable:  {sub['max_favorable_pct'].mean():.3f}%")
        print(f"    Med max favorable:  {sub['max_favorable_pct'].median():.3f}%")
        print(f"    Avg close-to-close: {sub['close_move_pct'].mean():+.3f}%")
        print(f"    Med close-to-close: {sub['close_move_pct'].median():+.3f}%")
        profitable = (sub["close_move_pct"] > 0).sum()
        print(f"    Profitable:         {profitable}/{len(sub)} ({profitable/len(sub)*100:.1f}%)")
        gt05 = (sub["max_favorable_pct"] > 0.5).sum()
        gt10 = (sub["max_favorable_pct"] > 1.0).sum()
        print(f"    Max fav > 0.5%:     {gt05}/{len(sub)} ({gt05/len(sub)*100:.1f}%)")
        print(f"    Max fav > 1.0%:     {gt10}/{len(sub)} ({gt10/len(sub)*100:.1f}%)")

    # ── Compare to pure 1H detection ──
    print(f"\n--- Comparison: Hybrid (15m trigger) vs Pure 1H Detection ---")

    df_1h_full_bb = df_1h_full.copy()
    pure_1h_signals = df_1h_full_bb[
        df_1h_full_bb["squeeze_state"].shift(1).fillna(False) &
        (
            (df_1h_full_bb["close"] > df_1h_full_bb["bb_upper"]) |
            (df_1h_full_bb["close"] < df_1h_full_bb["bb_lower"])
        )
    ].copy()
    pure_1h_signals["direction"] = np.where(
        pure_1h_signals["close"] > pure_1h_signals["bb_upper"], "UP", "DOWN"
    )

    print(f"\n  Pure 1H signals: {len(pure_1h_signals)}")
    print(f"  Hybrid signals:  {len(unique_signals)}")

    # Pure 1H remaining moves
    pure_results = []
    for _, row in pure_1h_signals.iterrows():
        entry_price = row["close"]
        entry_time = row["close_time"]
        direction = row["direction"]

        for horizon_hours, horizon_label in [(1, "1H"), (2, "2H"), (4, "4H")]:
            future = df_15m[
                (df_15m["open_time"] > entry_time) &
                (df_15m["open_time"] <= entry_time + horizon_hours * 3600 * 1000)
            ]
            if len(future) == 0:
                continue

            if direction == "UP":
                max_favorable = (future["high"].max() - entry_price) / entry_price * 100
                close_move = (future.iloc[-1]["close"] - entry_price) / entry_price * 100
            else:
                max_favorable = (entry_price - future["low"].min()) / entry_price * 100
                close_move = (entry_price - future.iloc[-1]["close"]) / entry_price * 100

            pure_results.append({
                "horizon": horizon_label,
                "max_favorable_pct": max_favorable,
                "close_move_pct": close_move,
            })

    if pure_results:
        df_pure = pd.DataFrame(pure_results)
        print(f"\n  {'Metric':<25} {'Hybrid':>12} {'Pure 1H':>12} {'Diff':>12}")
        print("  " + "-" * 63)
        for horizon in ["1H", "2H", "4H"]:
            hyb_sub = df_hyb[df_hyb["horizon"] == horizon]
            pure_sub = df_pure[df_pure["horizon"] == horizon]
            if len(hyb_sub) == 0 or len(pure_sub) == 0:
                continue
            hyb_fav = hyb_sub["max_favorable_pct"].mean()
            pure_fav = pure_sub["max_favorable_pct"].mean()
            hyb_close = hyb_sub["close_move_pct"].mean()
            pure_close = pure_sub["close_move_pct"].mean()
            print(f"  {horizon} max favorable       {hyb_fav:>11.3f}% {pure_fav:>11.3f}% {hyb_fav-pure_fav:>+11.3f}%")
            print(f"  {horizon} close-to-close      {hyb_close:>+11.3f}% {pure_close:>+11.3f}% {hyb_close-pure_close:>+11.3f}%")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    start_time = time.time()
    df_15m, df_1h = fetch_data()

    analysis_1_explosive_decomposition(df_15m, df_1h)
    analysis_2_continuation(df_1h)
    analysis_3_15m_squeeze(df_15m, df_1h)
    analysis_4_latency_table(df_15m, df_1h)
    analysis_5_hybrid(df_15m, df_1h)

    elapsed = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"Done in {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
