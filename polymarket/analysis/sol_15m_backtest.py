#!/usr/bin/env python3
"""
SOL 15M Momentum Backtest — W4 Both-Sides Strategy

Uses SOL 1M klines (7 days) for precise 15M window simulation.
Uses BTC 1M klines for same-period comparison.
Uses SOL 1H klines for longer-term volatility analysis.

Design decisions:
- 15M windows aligned to 900s epoch boundaries (UTC)
- T+delay observation: use 1M candle close at delay offset
- Momentum signal: if |return| > threshold → lean with momentum direction
- Both-sides 1.5:1 lean ratio (not pure directional)
- PnL simulation: Polymarket binary market mechanics
"""

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────
BASE = Path("/Users/wai/projects/axc-trading")
DATA_DIR = BASE / "backtest" / "data"

# SOL 1M (7 days, JSON format)
SOL_1M_FILE = BASE / "polymarket" / "analysis" / "solusdt_1m_7days.json"

# BTC 1M (covers same period + more)
BTC_1M_FILE = DATA_DIR / "BTCUSDT_1m_20250921_20260320.csv"

# SOL 1H (longest file, for vol analysis)
SOL_1H_FILE = DATA_DIR / "SOLUSDT_1h_20250916_20260323.csv"

# BTC 5M (for comparison reference)
BTC_5M_FILE = DATA_DIR / "BTCUSDT_5m_20250324_20260318.csv"

# Sweep parameters
DELAYS_SEC = [180, 240, 300, 360]
THRESHOLDS_BPS = [0, 3, 5, 7, 10, 15, 20]

# PnL simulation: both-sides 1.5:1
LEAN_SHARES = 1.5
HEDGE_SHARES = 1.0

WINDOW_SEC = 900  # 15 minutes


# ─── DATA LOADING ────────────────────────────────────────────────────

def load_sol_1m_json(filepath):
    """Load SOL 1M klines from JSON format."""
    with open(filepath) as f:
        raw = json.load(f)
    rows = []
    for r in raw:
        rows.append({
            "open_time_ms": r["ts"],
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        })
    print(f"  Loaded {len(rows):,} SOL 1M candles from {filepath.name}")
    return rows


def load_csv_1m(filepath, label=""):
    """Load Binance 1M kline CSV."""
    rows = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "open_time_ms": int(row["open_time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            })
    print(f"  Loaded {len(rows):,} rows from {filepath.name} ({label})")
    return rows


def load_csv_1h(filepath, label=""):
    """Load Binance 1H kline CSV."""
    rows = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "open_time_ms": int(row["open_time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "timestamp": row.get("timestamp", ""),
            })
    print(f"  Loaded {len(rows):,} rows from {filepath.name} ({label})")
    return rows


def build_1m_index(rows_1m):
    """Build dict: open_time_seconds -> candle for O(1) lookup."""
    idx = {}
    for r in rows_1m:
        ts_sec = r["open_time_ms"] // 1000
        idx[ts_sec] = r
    return idx


def get_price_at(idx_1m, ts_sec):
    """Get price at a specific second using 1M candle close."""
    candle = idx_1m.get(ts_sec)
    if candle:
        return candle["close"]
    # Try nearby seconds (within same minute)
    minute_start = (ts_sec // 60) * 60
    candle = idx_1m.get(minute_start)
    if candle:
        return candle["close"]
    return None


def get_open_price_at(idx_1m, ts_sec):
    """Get opening price at a specific second using 1M candle open."""
    minute_start = (ts_sec // 60) * 60
    candle = idx_1m.get(minute_start)
    if candle:
        return candle["open"]
    return None


# ─── SECTION 1: SOL Momentum Persistence ─────────────────────────────

def analyze_momentum_persistence(idx_1m, min_ts, max_ts, label="SOL"):
    """
    For every 15M window aligned to 900s epoch:
    Calculate WR at various delay/threshold combos.
    """
    print(f"\n{'='*70}")
    print(f"  SECTION 1: {label} Momentum Persistence (15M windows)")
    print(f"{'='*70}")

    # Find aligned 15M window starts
    first_window = ((min_ts // WINDOW_SEC) + 1) * WINDOW_SEC
    last_window = (max_ts // WINDOW_SEC) * WINDOW_SEC

    windows = []
    ts = first_window
    while ts + WINDOW_SEC <= max_ts:
        # Window open price = open of first 1M candle in window
        open_price = get_open_price_at(idx_1m, ts)
        close_price = get_price_at(idx_1m, ts + WINDOW_SEC - 60)  # last 1M candle close

        if open_price and close_price:
            delay_prices = {}
            for delay in DELAYS_SEC:
                p = get_price_at(idx_1m, ts + delay - 60)  # price at delay point
                if p:
                    delay_prices[delay] = p

            if delay_prices:
                window_return_bps = (close_price / open_price - 1) * 10000
                windows.append({
                    "ts": ts,
                    "open": open_price,
                    "close": close_price,
                    "return_bps": window_return_bps,
                    "delay_prices": delay_prices,
                })
        ts += WINDOW_SEC

    print(f"  Total 15M windows: {len(windows)}")
    if not windows:
        print("  NO WINDOWS FOUND!")
        return {}, windows

    # Date range
    dt_first = datetime.fromtimestamp(windows[0]["ts"], tz=timezone.utc)
    dt_last = datetime.fromtimestamp(windows[-1]["ts"], tz=timezone.utc)
    print(f"  Range: {dt_first.strftime('%Y-%m-%d %H:%M')} → {dt_last.strftime('%Y-%m-%d %H:%M')} UTC")

    # Calculate WR for each delay/threshold combo
    results = {}
    print(f"\n  {'Delay':>6s} {'Thresh':>6s} {'Total':>6s} {'Qual':>6s} {'Qual%':>6s} {'Hits':>6s} {'WR':>7s}")
    print(f"  {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")

    for delay in DELAYS_SEC:
        for thresh in THRESHOLDS_BPS:
            total = 0
            qualifying = 0
            hits = 0

            for w in windows:
                if delay not in w["delay_prices"]:
                    continue
                total += 1

                delay_price = w["delay_prices"][delay]
                delay_return_bps = (delay_price / w["open"] - 1) * 10000

                # Check threshold
                if abs(delay_return_bps) < thresh:
                    continue
                qualifying += 1

                # Direction at delay
                delay_dir = 1 if delay_return_bps > 0 else -1
                # Direction at close
                close_dir = 1 if w["return_bps"] > 0 else -1

                # Momentum persists?
                if delay_dir == close_dir:
                    hits += 1

            wr = hits / qualifying * 100 if qualifying > 0 else 0
            qual_pct = qualifying / total * 100 if total > 0 else 0
            results[(delay, thresh)] = {
                "total": total,
                "qualifying": qualifying,
                "qual_pct": qual_pct,
                "hits": hits,
                "wr": wr,
            }
            print(f"  {delay:>5}s {thresh:>5}bp {total:>6} {qualifying:>6} {qual_pct:>5.1f}% {hits:>6} {wr:>6.1f}%")

    return results, windows


# ─── SECTION 2: SOL vs BTC Comparison ────────────────────────────────

def compare_sol_btc(sol_results, btc_results):
    """Compare momentum persistence between SOL and BTC."""
    print(f"\n{'='*70}")
    print(f"  SECTION 2: SOL vs BTC Momentum Persistence Comparison")
    print(f"{'='*70}")

    print(f"\n  {'Delay':>6s} {'Thresh':>6s} │ {'SOL WR':>7s} {'SOL N':>6s} │ {'BTC WR':>7s} {'BTC N':>6s} │ {'Diff':>7s}")
    print(f"  {'-'*6} {'-'*6} │ {'-'*7} {'-'*6} │ {'-'*7} {'-'*6} │ {'-'*7}")

    for delay in DELAYS_SEC:
        for thresh in THRESHOLDS_BPS:
            key = (delay, thresh)
            sol = sol_results.get(key, {})
            btc = btc_results.get(key, {})

            sol_wr = sol.get("wr", 0)
            btc_wr = btc.get("wr", 0)
            sol_n = sol.get("qualifying", 0)
            btc_n = btc.get("qualifying", 0)
            diff = sol_wr - btc_wr

            marker = ""
            if sol_n >= 10 and btc_n >= 10:
                if diff > 5:
                    marker = " ★ SOL better"
                elif diff < -5:
                    marker = " ✗ BTC better"

            print(f"  {delay:>5}s {thresh:>5}bp │ {sol_wr:>6.1f}% {sol_n:>5} │ {btc_wr:>6.1f}% {btc_n:>5} │ {diff:>+6.1f}%{marker}")


# ─── SECTION 3: SOL Volatility Profile ───────────────────────────────

def analyze_volatility(sol_windows, btc_windows, sol_1h_rows):
    """SOL vs BTC volatility at 15M level + threshold pass rates."""
    print(f"\n{'='*70}")
    print(f"  SECTION 3: SOL Volatility Profile")
    print(f"{'='*70}")

    # 15M window absolute returns
    sol_abs_returns = [abs(w["return_bps"]) for w in sol_windows]
    btc_abs_returns = [abs(w["return_bps"]) for w in btc_windows]

    if sol_abs_returns:
        sol_mean = sum(sol_abs_returns) / len(sol_abs_returns)
        sol_median = sorted(sol_abs_returns)[len(sol_abs_returns) // 2]
        sol_max = max(sol_abs_returns)
        sol_std = (sum((x - sol_mean)**2 for x in sol_abs_returns) / len(sol_abs_returns)) ** 0.5
    else:
        sol_mean = sol_median = sol_max = sol_std = 0

    if btc_abs_returns:
        btc_mean = sum(btc_abs_returns) / len(btc_abs_returns)
        btc_median = sorted(btc_abs_returns)[len(btc_abs_returns) // 2]
        btc_max = max(btc_abs_returns)
        btc_std = (sum((x - btc_mean)**2 for x in btc_abs_returns) / len(btc_abs_returns)) ** 0.5
    else:
        btc_mean = btc_median = btc_max = btc_std = 0

    print(f"\n  15M Window Absolute Returns (bps):")
    print(f"  {'':>15s} {'SOL':>10s} {'BTC':>10s} {'Ratio':>10s}")
    print(f"  {'Mean':>15s} {sol_mean:>9.1f}  {btc_mean:>9.1f}  {sol_mean/btc_mean if btc_mean else 0:>9.2f}x")
    print(f"  {'Median':>15s} {sol_median:>9.1f}  {btc_median:>9.1f}  {sol_median/btc_median if btc_median else 0:>9.2f}x")
    print(f"  {'Std Dev':>15s} {sol_std:>9.1f}  {btc_std:>9.1f}  {sol_std/btc_std if btc_std else 0:>9.2f}x")
    print(f"  {'Max':>15s} {sol_max:>9.1f}  {btc_max:>9.1f}  {sol_max/btc_max if btc_max else 0:>9.2f}x")

    # Threshold pass rates
    print(f"\n  Windows passing threshold (from {len(sol_windows)} SOL / {len(btc_windows)} BTC windows):")
    print(f"  {'Threshold':>10s} {'SOL #':>7s} {'SOL %':>7s} {'BTC #':>7s} {'BTC %':>7s}")
    for thresh in [0, 3, 5, 7, 10, 15, 20, 30, 50]:
        sol_pass = sum(1 for w in sol_windows if abs((w["delay_prices"].get(300, w["open"]) / w["open"] - 1) * 10000) >= thresh)
        btc_pass = sum(1 for w in btc_windows if abs((w["delay_prices"].get(300, w["open"]) / w["open"] - 1) * 10000) >= thresh)
        sol_pct = sol_pass / len(sol_windows) * 100 if sol_windows else 0
        btc_pct = btc_pass / len(btc_windows) * 100 if btc_windows else 0
        print(f"  {thresh:>9}bp {sol_pass:>7} {sol_pct:>6.1f}% {btc_pass:>7} {btc_pct:>6.1f}%")

    # Longer-term SOL 1H vol
    if sol_1h_rows:
        print(f"\n  SOL 1H Volatility (from {len(sol_1h_rows)} hourly candles):")
        sol_1h_ranges = [(r["high"] - r["low"]) / r["open"] * 10000 for r in sol_1h_rows if r["open"] > 0]
        if sol_1h_ranges:
            mean_range = sum(sol_1h_ranges) / len(sol_1h_ranges)
            max_range = max(sol_1h_ranges)
            print(f"    Mean 1H range: {mean_range:.1f} bps")
            print(f"    Max 1H range:  {max_range:.1f} bps")
            # Daily vol proxy
            print(f"    Annualized vol (from 1H ranges): ~{mean_range * math.sqrt(24 * 365) / 100:.1f}%")


# ─── SECTION 4: BTC-SOL Correlation ──────────────────────────────────

def analyze_correlation(sol_windows, btc_idx_1m):
    """BTC-SOL correlation at 15M level."""
    print(f"\n{'='*70}")
    print(f"  SECTION 4: BTC-SOL Correlation at 15M Level")
    print(f"{'='*70}")

    pairs = []
    for w in sol_windows:
        ts = w["ts"]
        # Get BTC prices for same window
        btc_open = get_open_price_at(btc_idx_1m, ts)
        btc_close = get_price_at(btc_idx_1m, ts + WINDOW_SEC - 60)

        if btc_open and btc_close and btc_open > 0:
            btc_ret = (btc_close / btc_open - 1) * 10000
            sol_ret = w["return_bps"]
            pairs.append((sol_ret, btc_ret))

    if not pairs:
        print("  No overlapping windows found!")
        return

    print(f"  Overlapping 15M windows: {len(pairs)}")

    sol_rets = [p[0] for p in pairs]
    btc_rets = [p[1] for p in pairs]

    # Correlation coefficient
    n = len(pairs)
    mean_sol = sum(sol_rets) / n
    mean_btc = sum(btc_rets) / n

    cov = sum((s - mean_sol) * (b - mean_btc) for s, b in pairs) / n
    std_sol = (sum((s - mean_sol)**2 for s in sol_rets) / n) ** 0.5
    std_btc = (sum((b - mean_btc)**2 for b in btc_rets) / n) ** 0.5

    corr = cov / (std_sol * std_btc) if std_sol > 0 and std_btc > 0 else 0

    print(f"  Correlation coefficient: {corr:.4f}")

    # Direction agreement
    both_up = sum(1 for s, b in pairs if s > 0 and b > 0)
    both_down = sum(1 for s, b in pairs if s < 0 and b < 0)
    sol_up_btc_down = sum(1 for s, b in pairs if s > 0 and b < 0)
    sol_down_btc_up = sum(1 for s, b in pairs if s < 0 and b > 0)
    same_dir = both_up + both_down

    print(f"\n  Direction agreement:")
    print(f"    Both UP:              {both_up:>5} ({both_up/n*100:.1f}%)")
    print(f"    Both DOWN:            {both_down:>5} ({both_down/n*100:.1f}%)")
    print(f"    SOL up / BTC down:    {sol_up_btc_down:>5} ({sol_up_btc_down/n*100:.1f}%)")
    print(f"    SOL down / BTC up:    {sol_down_btc_up:>5} ({sol_down_btc_up/n*100:.1f}%)")
    print(f"    Same direction:       {same_dir:>5} ({same_dir/n*100:.1f}%)")

    # When BTC goes UP, what % of time does SOL go UP?
    btc_up_total = sum(1 for _, b in pairs if b > 0)
    btc_up_sol_up = both_up
    if btc_up_total > 0:
        print(f"\n  When BTC UP → SOL UP:   {btc_up_sol_up}/{btc_up_total} = {btc_up_sol_up/btc_up_total*100:.1f}%")

    btc_down_total = sum(1 for _, b in pairs if b < 0)
    btc_down_sol_down = both_down
    if btc_down_total > 0:
        print(f"  When BTC DOWN → SOL DN: {btc_down_sol_down}/{btc_down_total} = {btc_down_sol_down/btc_down_total*100:.1f}%")

    # SOL beta to BTC
    if std_btc > 0:
        beta = cov / (std_btc ** 2)
        print(f"\n  SOL beta to BTC (15M): {beta:.3f}")
        print(f"  (beta > 1 = SOL amplifies BTC moves)")

    # Check contrarian signal: does SOL reverse after following BTC?
    # When BTC & SOL both move >5bps same direction at T+300s,
    # does SOL reverse more often than BTC?
    print(f"\n  Contrarian check (>5bps same direction at T+300s):")
    contrarian_sol = 0
    contrarian_btc = 0
    contrarian_total = 0

    for w in sol_windows:
        ts = w["ts"]
        if 300 not in w["delay_prices"]:
            continue
        btc_open = get_open_price_at(btc_idx_1m, ts)
        btc_mid = get_price_at(btc_idx_1m, ts + 300 - 60)
        btc_close = get_price_at(btc_idx_1m, ts + WINDOW_SEC - 60)
        if not (btc_open and btc_mid and btc_close):
            continue

        sol_mid_ret = (w["delay_prices"][300] / w["open"] - 1) * 10000
        btc_mid_ret = (btc_mid / btc_open - 1) * 10000

        # Both must pass threshold and same direction
        if abs(sol_mid_ret) < 5 or abs(btc_mid_ret) < 5:
            continue
        if (sol_mid_ret > 0) != (btc_mid_ret > 0):
            continue

        contrarian_total += 1

        # Did SOL reverse?
        sol_close_ret = w["return_bps"]
        btc_close_ret = (btc_close / btc_open - 1) * 10000

        sol_reversed = (sol_mid_ret > 0 and sol_close_ret < 0) or (sol_mid_ret < 0 and sol_close_ret > 0)
        btc_reversed = (btc_mid_ret > 0 and btc_close_ret < 0) or (btc_mid_ret < 0 and btc_close_ret > 0)

        if sol_reversed:
            contrarian_sol += 1
        if btc_reversed:
            contrarian_btc += 1

    if contrarian_total > 0:
        print(f"    Qualifying windows (both >5bps same dir): {contrarian_total}")
        print(f"    SOL reversal rate: {contrarian_sol}/{contrarian_total} = {contrarian_sol/contrarian_total*100:.1f}%")
        print(f"    BTC reversal rate: {contrarian_btc}/{contrarian_total} = {contrarian_btc/contrarian_total*100:.1f}%")


# ─── SECTION 5: PnL Simulation ───────────────────────────────────────

def simulate_pnl(results, windows, label="SOL"):
    """
    PnL simulation with both-sides 1.5:1 lean.

    Polymarket binary: buy YES/NO shares at price p.
    If correct: payout $1/share. If wrong: lose cost.

    Both-sides: buy 1.5 shares on lean side + 1.0 shares on hedge side.
    Combined cost varies by market price.
    """
    print(f"\n{'='*70}")
    print(f"  SECTION 5: {label} PnL Simulation (Both-Sides 1.5:1)")
    print(f"{'='*70}")

    # Focus on key combo: 300s delay, 5bps threshold
    key_combos = [
        (300, 5, "Primary: 300s/5bp"),
        (300, 7, "Conservative: 300s/7bp"),
        (300, 10, "Strict: 300s/10bp"),
        (240, 5, "Faster: 240s/5bp"),
        (180, 5, "Fastest: 180s/5bp"),
    ]

    for delay, thresh, combo_label in key_combos:
        key = (delay, thresh)
        r = results.get(key)
        if not r or r["qualifying"] == 0:
            continue

        wr = r["wr"] / 100
        n_qual = r["qualifying"]
        n_total = r["total"]

        print(f"\n  --- {combo_label} ---")
        print(f"  WR: {wr*100:.1f}% | Qualifying: {n_qual}/{n_total} ({r['qual_pct']:.1f}%)")

        # Multiple price scenarios
        for combined_cost_str, lean_price, hedge_price in [
            ("$0.97 combined (fair)", 0.49, 0.48),
            ("$0.98 combined", 0.50, 0.48),
            ("$1.00 combined (break-even line)", 0.51, 0.49),
            ("$1.02 combined (typical spread)", 0.52, 0.50),
            ("$1.04 combined (wide spread)", 0.53, 0.51),
        ]:
            lean_cost = lean_price * LEAN_SHARES
            hedge_cost = hedge_price * HEDGE_SHARES
            total_cost = lean_cost + hedge_cost

            # Win: lean side pays $1/share, hedge side loses
            # Correct direction: lean pays out
            win_pnl = (1.0 * LEAN_SHARES - lean_cost) + (0 - hedge_cost)
            # Wrong direction: hedge pays out
            lose_pnl = (0 - lean_cost) + (1.0 * HEDGE_SHARES - hedge_cost)

            expected_per_trade = wr * win_pnl + (1 - wr) * lose_pnl

            # Daily projection
            days = (windows[-1]["ts"] - windows[0]["ts"]) / 86400 if len(windows) > 1 else 1
            trades_per_day = n_qual / days if days > 0 else 0
            daily_pnl = expected_per_trade * trades_per_day
            monthly_pnl = daily_pnl * 30

            print(f"    {combined_cost_str}:")
            print(f"      Win PnL: ${win_pnl:+.4f} | Lose PnL: ${lose_pnl:+.4f}")
            print(f"      E[per trade]: ${expected_per_trade:+.4f}")
            print(f"      Trades/day: {trades_per_day:.1f} | Daily: ${daily_pnl:+.2f} | Monthly: ${monthly_pnl:+.2f}")

    # Break-even WR calculation
    print(f"\n  Break-even WR (combined cost scenarios):")
    for combined_cost_str, lean_price, hedge_price in [
        ("$0.97", 0.49, 0.48),
        ("$1.00", 0.51, 0.49),
        ("$1.02", 0.52, 0.50),
        ("$1.04", 0.53, 0.51),
    ]:
        lean_cost = lean_price * LEAN_SHARES
        hedge_cost = hedge_price * HEDGE_SHARES
        win_pnl = (1.0 * LEAN_SHARES - lean_cost) + (0 - hedge_cost)
        lose_pnl = (0 - lean_cost) + (1.0 * HEDGE_SHARES - hedge_cost)
        # BE: wr * win + (1-wr) * lose = 0
        # wr = -lose / (win - lose)
        if win_pnl != lose_pnl:
            be_wr = -lose_pnl / (win_pnl - lose_pnl) * 100
        else:
            be_wr = float("inf")
        print(f"    {combined_cost_str}: BE WR = {be_wr:.1f}%")


# ─── SECTION 6: Risk Analysis ────────────────────────────────────────

def analyze_risk(sol_windows, btc_windows, sol_idx_1m, btc_idx_1m):
    """SOL-specific risk factors."""
    print(f"\n{'='*70}")
    print(f"  SECTION 6: Risk Analysis — SOL-Specific Factors")
    print(f"{'='*70}")

    # Max drawdown in single 15M window
    sol_returns = [w["return_bps"] for w in sol_windows]
    btc_returns = [w["return_bps"] for w in btc_windows]

    if sol_returns:
        sol_worst = min(sol_returns)
        sol_best = max(sol_returns)
        print(f"\n  SOL 15M window extremes:")
        print(f"    Worst: {sol_worst:+.1f} bps")
        print(f"    Best:  {sol_best:+.1f} bps")

    if btc_returns:
        btc_worst = min(btc_returns)
        btc_best = max(btc_returns)
        print(f"  BTC 15M window extremes:")
        print(f"    Worst: {btc_worst:+.1f} bps")
        print(f"    Best:  {btc_best:+.1f} bps")

    # Intra-window max adverse excursion
    print(f"\n  Intra-window max adverse excursion (MAE):")
    sol_maes = []
    for w in sol_windows:
        ts = w["ts"]
        open_p = w["open"]
        worst_adverse = 0
        for offset in range(0, WINDOW_SEC, 60):
            p = get_price_at(sol_idx_1m, ts + offset)
            if p and open_p > 0:
                ret = abs(p / open_p - 1) * 10000
                worst_adverse = max(worst_adverse, ret)
        sol_maes.append(worst_adverse)

    btc_maes = []
    for w in btc_windows:
        ts = w["ts"]
        btc_open = get_open_price_at(btc_idx_1m, ts)
        if not btc_open:
            continue
        worst_adverse = 0
        for offset in range(0, WINDOW_SEC, 60):
            p = get_price_at(btc_idx_1m, ts + offset)
            if p and btc_open > 0:
                ret = abs(p / btc_open - 1) * 10000
                worst_adverse = max(worst_adverse, ret)
        btc_maes.append(worst_adverse)

    if sol_maes:
        sol_mae_mean = sum(sol_maes) / len(sol_maes)
        sol_mae_p95 = sorted(sol_maes)[int(len(sol_maes) * 0.95)]
        sol_mae_max = max(sol_maes)
        print(f"    SOL: mean={sol_mae_mean:.1f}bp, P95={sol_mae_p95:.1f}bp, max={sol_mae_max:.1f}bp")

    if btc_maes:
        btc_mae_mean = sum(btc_maes) / len(btc_maes)
        btc_mae_p95 = sorted(btc_maes)[int(len(btc_maes) * 0.95)]
        btc_mae_max = max(btc_maes)
        print(f"    BTC: mean={btc_mae_mean:.1f}bp, P95={btc_mae_p95:.1f}bp, max={btc_mae_max:.1f}bp")

    # Reversal analysis: how often does momentum reverse after 5min?
    print(f"\n  Reversal frequency (momentum at T+300s reverses by close):")
    for thresh in [3, 5, 7, 10]:
        sol_rev = 0
        sol_qual = 0
        btc_rev = 0
        btc_qual = 0

        for w in sol_windows:
            if 300 not in w["delay_prices"]:
                continue
            mid_ret = (w["delay_prices"][300] / w["open"] - 1) * 10000
            if abs(mid_ret) < thresh:
                continue
            sol_qual += 1
            close_ret = w["return_bps"]
            if (mid_ret > 0 and close_ret < 0) or (mid_ret < 0 and close_ret > 0):
                sol_rev += 1

        for w in btc_windows:
            if 300 not in w["delay_prices"]:
                continue
            btc_open = get_open_price_at(btc_idx_1m, w["ts"])
            if not btc_open:
                continue
            mid_ret = (w["delay_prices"][300] / btc_open - 1) * 10000
            if abs(mid_ret) < thresh:
                continue
            btc_qual += 1
            close_ret = w["return_bps"]
            if (mid_ret > 0 and close_ret < 0) or (mid_ret < 0 and close_ret > 0):
                btc_rev += 1

        sol_rev_pct = sol_rev / sol_qual * 100 if sol_qual else 0
        btc_rev_pct = btc_rev / btc_qual * 100 if btc_qual else 0
        print(f"    >{thresh}bp: SOL {sol_rev}/{sol_qual} ({sol_rev_pct:.1f}%) | BTC {btc_rev}/{btc_qual} ({btc_rev_pct:.1f}%)")

    # Flash crash detection: >50bps move in a single 1M candle
    print(f"\n  Flash crash detection (>50bps in single 1M candle):")
    sol_flash = 0
    sol_1m_count = 0
    for ts_sec, candle in sol_idx_1m.items():
        sol_1m_count += 1
        if candle["open"] > 0:
            ret = abs(candle["close"] / candle["open"] - 1) * 10000
            if ret > 50:
                sol_flash += 1

    btc_flash = 0
    btc_1m_count = 0
    # Only count BTC candles in the overlapping period
    sol_min_ts = min(sol_idx_1m.keys()) if sol_idx_1m else 0
    sol_max_ts = max(sol_idx_1m.keys()) if sol_idx_1m else 0
    for ts_sec, candle in btc_idx_1m.items():
        if ts_sec < sol_min_ts or ts_sec > sol_max_ts:
            continue
        btc_1m_count += 1
        if candle["open"] > 0:
            ret = abs(candle["close"] / candle["open"] - 1) * 10000
            if ret > 50:
                btc_flash += 1

    print(f"    SOL: {sol_flash} flash candles / {sol_1m_count} total ({sol_flash/sol_1m_count*100:.2f}%)" if sol_1m_count else "    SOL: no data")
    print(f"    BTC: {btc_flash} flash candles / {btc_1m_count} total ({btc_flash/btc_1m_count*100:.2f}%)" if btc_1m_count else "    BTC: no data")

    # Consecutive loss streaks
    print(f"\n  Consecutive loss streaks (300s/5bp):")
    for asset_label, asset_windows, asset_idx in [("SOL", sol_windows, sol_idx_1m), ("BTC", btc_windows, btc_idx_1m)]:
        streak = 0
        max_streak = 0
        for w in asset_windows:
            if 300 not in w["delay_prices"]:
                continue
            if asset_label == "BTC":
                op = get_open_price_at(asset_idx, w["ts"])
                if not op:
                    continue
            else:
                op = w["open"]

            mid_ret = (w["delay_prices"][300] / op - 1) * 10000
            if abs(mid_ret) < 5:
                streak = 0
                continue

            close_ret = w["return_bps"]
            mid_dir = 1 if mid_ret > 0 else -1
            close_dir = 1 if close_ret > 0 else -1

            if mid_dir != close_dir:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

        print(f"    {asset_label}: max consecutive losses = {max_streak}")


# ─── SECTION 7: Verdict ──────────────────────────────────────────────

def verdict(sol_results, btc_results, sol_windows):
    """Final recommendation."""
    print(f"\n{'='*70}")
    print(f"  SECTION 7: VERDICT — Should SOL go LIVE on 15M?")
    print(f"{'='*70}")

    key = (300, 5)  # Primary combo
    sol = sol_results.get(key, {})
    btc = btc_results.get(key, {})

    sol_wr = sol.get("wr", 0)
    btc_wr = btc.get("wr", 0)
    sol_n = sol.get("qualifying", 0)
    btc_n = btc.get("qualifying", 0)

    print(f"\n  Primary signal (300s delay, 5bp threshold):")
    print(f"    SOL WR: {sol_wr:.1f}% (N={sol_n})")
    print(f"    BTC WR: {btc_wr:.1f}% (N={btc_n})")
    print(f"    Diff:   {sol_wr - btc_wr:+.1f}pp")

    # Check multiple combos for robustness
    print(f"\n  Robustness check across combos:")
    good_combos = 0
    total_combos = 0
    for delay in [240, 300, 360]:
        for thresh in [5, 7, 10]:
            k = (delay, thresh)
            r = sol_results.get(k, {})
            if r.get("qualifying", 0) >= 10:
                total_combos += 1
                if r["wr"] >= 60:
                    good_combos += 1
                status = "PASS" if r["wr"] >= 60 else "FAIL"
                print(f"    {delay}s/{thresh}bp: WR={r['wr']:.1f}% N={r['qualifying']} [{status}]")

    # Daily qualifying windows
    days = (sol_windows[-1]["ts"] - sol_windows[0]["ts"]) / 86400 if len(sol_windows) > 1 else 1
    qual_per_day = sol_n / days if days > 0 else 0

    print(f"\n  Operational metrics:")
    print(f"    Qualifying windows/day: {qual_per_day:.1f}")
    print(f"    Days of data: {days:.1f}")
    print(f"    Good combos: {good_combos}/{total_combos}")

    # Verdict
    print(f"\n  {'='*50}")
    if sol_wr >= 70:
        print(f"  VERDICT: YES — SOL should go LIVE on 15M")
        print(f"  WR {sol_wr:.1f}% >= 70% threshold")
    elif sol_wr >= 60:
        print(f"  VERDICT: CAUTIOUS — SOL borderline for 15M")
        print(f"  WR {sol_wr:.1f}% in 60-70% range")
        print(f"  Recommend: paper trade 48h first, then review")
    else:
        print(f"  VERDICT: NO — SOL should NOT go LIVE on 15M")
        print(f"  WR {sol_wr:.1f}% < 60% threshold")
        print(f"  Momentum signal does not persist reliably for SOL")
    print(f"  {'='*50}")

    # Caveats
    print(f"\n  CAVEATS:")
    print(f"    1. Only {days:.0f} days of 1M data — small sample")
    print(f"    2. SOL Polymarket liquidity likely much thinner than BTC")
    print(f"    3. SOL 15M markets may not exist yet on Polymarket")
    print(f"    4. Higher vol = higher spread = higher execution cost")
    if sol_n < 50:
        print(f"    5. *** N={sol_n} is VERY small sample — low confidence ***")


# ─── MAIN ────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  SOL 15M MOMENTUM BACKTEST — W4 Both-Sides Strategy")
    print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 70)

    # Load data
    print("\n  Loading data...")
    sol_1m = load_sol_1m_json(SOL_1M_FILE)
    btc_1m = load_csv_1m(BTC_1M_FILE, "BTC 1M")
    sol_1h = load_csv_1h(SOL_1H_FILE, "SOL 1H")

    # Build indexes
    sol_idx = build_1m_index(sol_1m)
    btc_idx = build_1m_index(btc_1m)

    sol_min_ts = min(sol_idx.keys())
    sol_max_ts = max(sol_idx.keys())
    print(f"\n  SOL 1M range: {datetime.fromtimestamp(sol_min_ts, tz=timezone.utc)} → {datetime.fromtimestamp(sol_max_ts, tz=timezone.utc)}")

    # For BTC comparison, restrict to same period as SOL
    btc_overlap_idx = {k: v for k, v in btc_idx.items() if sol_min_ts <= k <= sol_max_ts}
    print(f"  BTC 1M overlap: {len(btc_overlap_idx):,} candles (of {len(btc_idx):,} total)")

    # SECTION 1: SOL Momentum Persistence
    sol_results, sol_windows = analyze_momentum_persistence(sol_idx, sol_min_ts, sol_max_ts, "SOL")

    # BTC same period for comparison
    btc_results, btc_windows = analyze_momentum_persistence(btc_overlap_idx, sol_min_ts, sol_max_ts, "BTC")

    if not sol_windows or not btc_windows:
        print("\nINSUFFICIENT DATA — cannot continue")
        return

    # SECTION 2: SOL vs BTC comparison
    compare_sol_btc(sol_results, btc_results)

    # SECTION 3: Volatility
    analyze_volatility(sol_windows, btc_windows, sol_1h)

    # SECTION 4: Correlation
    analyze_correlation(sol_windows, btc_idx)

    # SECTION 5: PnL simulation
    simulate_pnl(sol_results, sol_windows, "SOL")

    # SECTION 6: Risk
    analyze_risk(sol_windows, btc_windows, sol_idx, btc_idx)

    # SECTION 7: Verdict
    verdict(sol_results, btc_results, sol_windows)

    print(f"\n{'='*70}")
    print(f"  BACKTEST COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
