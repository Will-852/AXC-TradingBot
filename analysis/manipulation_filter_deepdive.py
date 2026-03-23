"""
Deep-dive on the most promising manipulation filter signals.
Focuses on: close_gap < $10 bucket and Level 3b (tight+ratio).
Also tests a "pure close_gap" filter since that was the strongest single signal.
"""

import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Reuse cached data
CACHE_DIR = Path(__file__).parent / "cache"
HKT = timezone(timedelta(hours=8))


def klines_to_df(raw_klines):
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
    return df


def aggregate_15m(df_1m):
    df_1m = df_1m.copy()
    df_1m["window_start"] = df_1m["timestamp"].dt.floor("15min")
    df_1m["min_in_window"] = ((df_1m["timestamp"] - df_1m["window_start"])
                               .dt.total_seconds() / 60).astype(int)

    windows = []
    for window_start, group in df_1m.groupby("window_start"):
        if len(group) < 14:
            continue
        group = group.sort_values("timestamp")

        w_open = group.iloc[0]["open"]
        w_close = group.iloc[-1]["close"]
        w_high = group["high"].max()
        w_low = group["low"].min()
        w_volume = group["volume"].sum()

        close_gap = abs(w_close - w_open)
        w_range = w_high - w_low

        group_returns = group["close"].pct_change().abs() * 100
        avg_1min_return = group_returns.mean()

        last_2 = group[group["min_in_window"] >= 13]
        first_13 = group[group["min_in_window"] < 13]

        last_2min_volume = last_2["volume"].sum()
        first_13min_volume = first_13["volume"].sum()
        last_2min_volume_ratio = (last_2min_volume / first_13min_volume
                                   if first_13min_volume > 0 else 0)

        # Last 2 min max return
        if len(first_13) > 0 and len(last_2) > 0:
            transition_return = abs(last_2.iloc[0]["open"] - first_13.iloc[-1]["close"]) / first_13.iloc[-1]["close"] * 100
            last_2_returns = last_2["close"].pct_change().abs() * 100
            last_2min_max_return = max(last_2_returns.max() if not last_2_returns.isna().all() else 0,
                                        transition_return)
        else:
            last_2min_max_return = 0

        # Direction tracking
        if len(first_13) > 0:
            price_at_t120 = first_13.iloc[-1]["close"]
        else:
            price_at_t120 = w_open

        direction_at_t120 = "UP" if price_at_t120 >= w_open else "DOWN"
        final_direction = "UP" if w_close >= w_open else "DOWN"
        is_reversal = direction_at_t120 != final_direction

        # Also track: price at various time points for more granular analysis
        # T-60s (1 min before close)
        last_1 = group[group["min_in_window"] >= 14]
        first_14 = group[group["min_in_window"] < 14]
        if len(first_14) > 0:
            price_at_t60 = first_14.iloc[-1]["close"]
            direction_at_t60 = "UP" if price_at_t60 >= w_open else "DOWN"
        else:
            price_at_t60 = w_open
            direction_at_t60 = "UP"

        reversal_at_t60 = direction_at_t60 != final_direction

        # Track the exact movement in last 2 min
        if len(first_13) > 0:
            last_2min_move = w_close - price_at_t120
        else:
            last_2min_move = 0

        windows.append({
            "window_start": window_start,
            "open": w_open, "close": w_close, "high": w_high, "low": w_low,
            "volume": w_volume, "close_gap": close_gap, "range": w_range,
            "avg_1min_return": avg_1min_return,
            "last_2min_max_return": last_2min_max_return,
            "last_2min_volume_ratio": last_2min_volume_ratio,
            "last_2min_volume": last_2min_volume,
            "price_at_t120": price_at_t120,
            "price_at_t60": price_at_t60,
            "direction_at_t120": direction_at_t120,
            "direction_at_t60": direction_at_t60,
            "final_direction": final_direction,
            "is_reversal": is_reversal,
            "reversal_at_t60": reversal_at_t60,
            "last_2min_move": last_2min_move,
        })

    return pd.DataFrame(windows)


def simulate_trades(df, mask, label):
    """Simulate the 'follow the manipulator' strategy."""
    flagged = df[mask].copy()
    n = len(flagged)
    if n == 0:
        print(f"  {label}: 0 trades")
        return

    trades = []
    for _, row in flagged.iterrows():
        our_side = "DOWN" if row["direction_at_t120"] == "UP" else "UP"

        # Entry price: for very tight gaps, both sides near 0.50
        # Use a more realistic model based on actual close_gap
        # At T-120s, the gap between price and open tells us the "spread"
        mid_gap_at_t120 = abs(row["price_at_t120"] - row["open"])
        if row["range"] > 0:
            relative_position = mid_gap_at_t120 / row["range"]
        else:
            relative_position = 0
        # Underdog price: 0.50 when gap=0, lower when gap is larger
        entry_price = max(0.35, 0.50 - relative_position * 0.15)

        won = (our_side == row["final_direction"])
        pnl = (1.0 - entry_price - 0.02) if won else -entry_price

        trades.append({
            "won": won, "pnl": pnl, "entry": entry_price,
            "close_gap": row["close_gap"],
            "is_reversal": row["is_reversal"],
            "window_start": row["window_start"],
        })

    df_t = pd.DataFrame(trades)
    wr = df_t["won"].mean()
    total_pnl = df_t["pnl"].sum()
    avg_pnl = df_t["pnl"].mean()
    avg_entry = df_t["entry"].mean()
    roi = avg_pnl / avg_entry if avg_entry > 0 else 0

    # Break-even WR
    # E[PnL] = WR * (1 - entry - fee) - (1-WR) * entry = 0
    # WR = entry / (1 - fee) ≈ entry / 0.98
    be_wr = avg_entry / 0.98

    print(f"\n  {label}:")
    print(f"    N={n}, WR={wr:.1%}, BE_WR={be_wr:.1%}")
    print(f"    Total PnL={total_pnl:.2f}, Avg PnL={avg_pnl:.4f}, ROI={roi:.1%}")
    print(f"    Avg entry={avg_entry:.3f}")

    # Per-window detail for small samples
    if n <= 20:
        print(f"    --- Individual trades ---")
        for _, t in df_t.iterrows():
            print(f"      {t['window_start']} | gap=${t['close_gap']:.1f} "
                  f"| entry={t['entry']:.3f} | {'WIN' if t['won'] else 'LOSS'} "
                  f"| PnL={t['pnl']:.3f} | rev={t['is_reversal']}")


def run_deepdive():
    print("=" * 70)
    print("DEEP DIVE — Manipulation Filter Signals")
    print("=" * 70)

    # Load cached 1m data
    with open(CACHE_DIR / "btc_1m_7d_20260316_20260323.json") as f:
        raw_1m = json.load(f)
    df_1m = klines_to_df(raw_1m)
    df = aggregate_15m(df_1m)
    n_total = len(df)

    print(f"\n  Total 15M windows (7-day): {n_total}")
    overall_reversal = df["is_reversal"].mean()
    overall_reversal_t60 = df["reversal_at_t60"].mean()
    print(f"  Overall reversal rate (T-120s): {overall_reversal:.1%}")
    print(f"  Overall reversal rate (T-60s):  {overall_reversal_t60:.1%}")

    vol_p25 = df["volume"].quantile(0.25)
    vol_p10 = df["volume"].quantile(0.10)

    # === Test 1: Pure close_gap filters (no volume requirement) ===
    print("\n" + "=" * 70)
    print("[A] PURE CLOSE_GAP FILTERS (no volume requirement)")
    print("=" * 70)

    for gap_thresh in [10, 20, 30, 50]:
        mask = df["close_gap"] < gap_thresh
        flagged = df[mask]
        n = len(flagged)
        rev_rate = flagged["is_reversal"].mean() if n > 0 else 0
        lift = rev_rate / overall_reversal if overall_reversal > 0 else 0
        print(f"\n  gap < ${gap_thresh}: N={n} ({n/n_total:.1%}), "
              f"reversal={rev_rate:.1%}, lift={lift:.2f}x")

    # === Test 2: Close gap is clearly the dominant signal ===
    # Question: does adding volume filter actually help, or is it just gap?
    print("\n" + "=" * 70)
    print("[B] DOES VOLUME ADD SIGNAL ON TOP OF GAP?")
    print("=" * 70)

    for gap_thresh in [30, 50]:
        base = df[df["close_gap"] < gap_thresh]
        n_base = len(base)
        rev_base = base["is_reversal"].mean() if n_base > 0 else 0

        low_vol = base[base["volume"] < vol_p25]
        high_vol = base[base["volume"] >= vol_p25]

        n_lv = len(low_vol)
        n_hv = len(high_vol)
        rev_lv = low_vol["is_reversal"].mean() if n_lv > 0 else 0
        rev_hv = high_vol["is_reversal"].mean() if n_hv > 0 else 0

        print(f"\n  gap < ${gap_thresh}: N={n_base}, reversal={rev_base:.1%}")
        print(f"    + low vol (<P25):  N={n_lv}, reversal={rev_lv:.1%} "
              f"(lift vs gap-only: {rev_lv/rev_base:.2f}x)" if n_lv > 0 and rev_base > 0 else "")
        print(f"    + high vol (>=P25): N={n_hv}, reversal={rev_hv:.1%}")

    # === Test 3: Volume ratio as additional signal ===
    print("\n" + "=" * 70)
    print("[C] LAST-2MIN VOLUME RATIO AS SIGNAL")
    print("=" * 70)

    for gap_thresh in [30, 50]:
        base = df[df["close_gap"] < gap_thresh]
        n_base = len(base)
        rev_base = base["is_reversal"].mean() if n_base > 0 else 0

        for ratio_thresh in [0.2, 0.3, 0.5]:
            high_ratio = base[base["last_2min_volume_ratio"] > ratio_thresh]
            n_hr = len(high_ratio)
            rev_hr = high_ratio["is_reversal"].mean() if n_hr > 0 else 0
            print(f"  gap<${gap_thresh} + vol_ratio>{ratio_thresh}: "
                  f"N={n_hr}, reversal={rev_hr:.1%}"
                  + (f", lift={rev_hr/rev_base:.2f}x" if rev_base > 0 and n_hr > 0 else ""))

    # === Test 4: Last 2-min spike as signal ===
    print("\n" + "=" * 70)
    print("[D] LAST-2MIN RETURN SPIKE AS SIGNAL")
    print("=" * 70)

    for gap_thresh in [30, 50]:
        base = df[df["close_gap"] < gap_thresh]
        for spike_mult in [1.5, 2.0, 3.0]:
            spike = base[base["last_2min_max_return"] > spike_mult * base["avg_1min_return"]]
            n_sp = len(spike)
            rev_sp = spike["is_reversal"].mean() if n_sp > 0 else 0
            print(f"  gap<${gap_thresh} + spike>{spike_mult}x: N={n_sp}, reversal={rev_sp:.1%}")

    # === Test 5: Combined best filter ===
    print("\n" + "=" * 70)
    print("[E] CANDIDATE FILTERS — ROI SIMULATION")
    print("=" * 70)

    filters = {
        "F1: gap<$20": df["close_gap"] < 20,
        "F2: gap<$30": df["close_gap"] < 30,
        "F3: gap<$30 + low_vol": (df["close_gap"] < 30) & (df["volume"] < vol_p25),
        "F4: gap<$30 + vol_ratio>0.3": (df["close_gap"] < 30) & (df["last_2min_volume_ratio"] > 0.3),
        "F5: gap<$50 + vol_ratio>0.3": (df["close_gap"] < 50) & (df["last_2min_volume_ratio"] > 0.3),
        "F6: gap<$30 + spike>2x": (df["close_gap"] < 30) & (df["last_2min_max_return"] > 2 * df["avg_1min_return"]),
        "F7: gap<$30 + spike>2x + ratio>0.2": (
            (df["close_gap"] < 30)
            & (df["last_2min_max_return"] > 2 * df["avg_1min_return"])
            & (df["last_2min_volume_ratio"] > 0.2)
        ),
        "F8: gap<$10": df["close_gap"] < 10,
        "F9: gap<$10 + ratio>0.2": (df["close_gap"] < 10) & (df["last_2min_volume_ratio"] > 0.2),
    }

    for label, mask in filters.items():
        simulate_trades(df, mask, label)

    # === Test 6: The null hypothesis — is close_gap < $X just "coin flip"? ===
    print("\n" + "=" * 70)
    print("[F] NULL HYPOTHESIS CHECK")
    print("=" * 70)
    print("  If close_gap is small, outcome is essentially random (50/50)")
    print("  The 'reversal' rate should be close to 50% for gap≈0")
    print()

    for gap_thresh in [5, 10, 20, 30, 50, 100]:
        mask = df["close_gap"] < gap_thresh
        flagged = df[mask]
        n = len(flagged)
        if n == 0:
            continue
        # How often does the direction at T-120s equal the final?
        same_dir = (flagged["direction_at_t120"] == flagged["final_direction"]).mean()
        print(f"  gap<${gap_thresh:3d}: N={n:3d}, same_direction_rate={same_dir:.1%}, "
              f"reversal_rate={(1-same_dir):.1%}")

    # Also check: for gap < $10, what's the actual $ move in last 2 min?
    print("\n  --- Last 2-min absolute move for small gaps ---")
    small_gap = df[df["close_gap"] < 30]
    if len(small_gap) > 0:
        print(f"  gap<$30: avg |last_2min_move| = ${small_gap['last_2min_move'].abs().mean():.1f}")
        print(f"           median              = ${small_gap['last_2min_move'].abs().median():.1f}")
        print(f"           max                 = ${small_gap['last_2min_move'].abs().max():.1f}")

    # === Summary table ===
    print("\n" + "=" * 70)
    print("[SUMMARY TABLE]")
    print("=" * 70)
    print(f"\n  {'Filter':<42} | {'N':>4} | {'Rev%':>6} | {'Lift':>5} | {'Verdict':>12}")
    print(f"  {'-'*42}-+-{'-'*4}-+-{'-'*6}-+-{'-'*5}-+-{'-'*12}")

    for label, mask in filters.items():
        flagged = df[mask]
        n = len(flagged)
        rev = flagged["is_reversal"].mean() if n > 0 else 0
        lift = rev / overall_reversal if overall_reversal > 0 and n > 0 else 0

        if rev > 0.40 and n >= 5:
            verdict = "ACTIONABLE"
        elif rev > 0.30 and n >= 5:
            verdict = "MARGINAL"
        elif n < 5:
            verdict = "TOO_FEW"
        else:
            verdict = "NOT_USEFUL"

        print(f"  {label:<42} | {n:4d} | {rev:5.1%} | {lift:4.2f}x | {verdict:>12}")


if __name__ == "__main__":
    run_deepdive()
