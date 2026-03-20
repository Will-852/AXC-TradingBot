#!/usr/bin/env python3
"""
mm_v9_compare.py — Compare v8 vs v9 strategies on 180d data.

Three strategies head-to-head:
  A) v8 baseline:  fixed $0.475, enter at minute 1, no M1 filter
  B) v9 dir-only:  $0.40 cap, M1 filter → directional only (skip if M1 weak)
  C) v9 hybrid:    $0.40 cap, M1 confirmed → directional, M1 weak → hedge

Usage:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/mm_v9_compare.py --days 180
"""

import argparse
import math
import os
import sys
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range
from polymarket.strategy.market_maker import MMConfig, compute_fair_up

ONE_MIN_MS = 60_000


def estimate_1m_vol(klines_1m: pd.DataFrame, lookback: int = 60) -> pd.Series:
    close = klines_1m["close"].astype(float)
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(lookback, min_periods=20).std().bfill().fillna(0.001)


def prepare_windows(klines_1m: pd.DataFrame, window_minutes: int = 15) -> list:
    df = klines_1m.sort_values("open_time").reset_index(drop=True)
    open_times = df["open_time"].values.astype(np.int64)
    window_ms = window_minutes * ONE_MIN_MS
    first_t = int(open_times[0])
    boundary = first_t - (first_t % window_ms) + window_ms
    windows = []
    while boundary + window_ms <= int(open_times[-1]):
        mask = (open_times >= boundary) & (open_times < boundary + window_ms)
        subset = df.loc[mask]
        if len(subset) >= window_minutes:
            candles = []
            for _, row in subset.head(window_minutes).iterrows():
                candles.append({
                    "open_time": int(row["open_time"]),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
            windows.append({"start": boundary, "candles": candles})
        boundary += window_ms
    return windows


def plan_orders(fair_up: float, bankroll: float, strategy: str,
                m1_confirmed: bool, config: MMConfig) -> list[dict]:
    """Generate orders for a given strategy."""
    fair_down = 1.0 - fair_up
    confidence = max(fair_up, fair_down)

    if confidence < 0.50:
        return []

    full_budget = min(bankroll * config.bet_pct, bankroll * 0.05)

    # Pricing depends on strategy
    if strategy == "v8":
        MAX_BID = 0.475
        MIN_BID = 0.35
    else:  # v9
        MAX_BID = 0.40   # directional cap
        MIN_BID = 0.25

    HEDGE_MAX = 0.475  # hedge always uses 0.475
    up_bid_h = round(min(HEDGE_MAX, max(0.25, fair_up - 0.025)), 2)
    dn_bid_h = round(min(HEDGE_MAX, max(0.25, fair_down - 0.025)), 2)
    up_bid_d = round(min(MAX_BID, max(MIN_BID, fair_up - 0.025)), 2)
    dn_bid_d = round(min(MAX_BID, max(MIN_BID, fair_down - 0.025)), 2)

    combined = up_bid_h + dn_bid_h
    hedge_min_cost = 5.0 * combined
    can_hedge = full_budget >= hedge_min_cost

    dir_side = "UP" if fair_up >= fair_down else "DOWN"
    dir_bid = up_bid_d if dir_side == "UP" else dn_bid_d

    if strategy == "v8":
        # v8: Zone system, enter at minute 1
        up_bid = round(min(0.475, max(0.35, fair_up - 0.025)), 2)
        dn_bid = round(min(0.475, max(0.35, fair_down - 0.025)), 2)
        dir_bid_v8 = up_bid if dir_side == "UP" else dn_bid
        combined_v8 = up_bid + dn_bid
        can_hedge_v8 = full_budget >= 5.0 * combined_v8

        if confidence <= 0.57:
            # Zone 1: hedge only
            if can_hedge_v8:
                shares = round(full_budget / combined_v8, 2)
                if shares >= 5:
                    return [{"outcome": "UP", "price": up_bid, "size": shares, "layer": "hedge"},
                            {"outcome": "DOWN", "price": dn_bid, "size": shares, "layer": "hedge"}]
            return []
        elif confidence <= 0.65:
            # Zone 2: 50/50
            orders = []
            if can_hedge_v8:
                h_shares = round(full_budget * 0.5 / combined_v8, 2)
                if h_shares >= 5:
                    orders.append({"outcome": "UP", "price": up_bid, "size": h_shares, "layer": "hedge"})
                    orders.append({"outcome": "DOWN", "price": dn_bid, "size": h_shares, "layer": "hedge"})
            d_budget = full_budget * 0.5 if orders else full_budget
            d_shares = round(d_budget / dir_bid_v8, 2)
            if d_shares >= 5:
                orders.append({"outcome": dir_side, "price": dir_bid_v8, "size": d_shares, "layer": "directional"})
            return orders
        else:
            # Zone 3: 25/75
            d_shares = round(full_budget * 0.75 / dir_bid_v8, 2)
            if d_shares >= 5:
                return [{"outcome": dir_side, "price": dir_bid_v8, "size": d_shares, "layer": "directional"}]
            return []

    elif strategy == "v9_dir":
        # v9 directional only: skip if M1 weak
        if not m1_confirmed:
            return []
        d_shares = round(full_budget / dir_bid, 2)
        if d_shares >= 5:
            return [{"outcome": dir_side, "price": dir_bid, "size": d_shares, "layer": "directional"}]
        elif 5 * dir_bid <= bankroll * 0.05:
            return [{"outcome": dir_side, "price": dir_bid, "size": 5.0, "layer": "directional"}]
        return []

    elif strategy == "v9_hybrid":
        # v9 hybrid: M1 confirmed → directional, M1 weak → hedge
        if m1_confirmed:
            # Directional at lower price
            d_shares = round(full_budget / dir_bid, 2)
            if d_shares >= 5:
                return [{"outcome": dir_side, "price": dir_bid, "size": d_shares, "layer": "directional"}]
            elif 5 * dir_bid <= bankroll * 0.05:
                return [{"outcome": dir_side, "price": dir_bid, "size": 5.0, "layer": "directional"}]
            return []
        else:
            # Hedge (both sides, guaranteed profit)
            if can_hedge:
                shares = round(full_budget / combined, 2)
                if shares >= 5:
                    return [{"outcome": "UP", "price": up_bid_h, "size": shares, "layer": "hedge"},
                            {"outcome": "DOWN", "price": dn_bid_h, "size": shares, "layer": "hedge"}]
            return []

    return []


def simulate(windows: list, vol_values: np.ndarray, vol_index: np.ndarray,
             strategy: str, config: MMConfig, bankroll: float,
             fill_rate: float, window_minutes: int = 15, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    results = []
    equity_curve = []

    for w in windows:
        candles = w["candles"]
        s_open = candles[0]["open"]
        s_close = candles[-1]["close"]
        actual = "UP" if s_close > s_open else "DOWN"

        # M1 return (minute 0 → minute 1)
        s_0 = candles[0]["close"]
        s_1 = candles[min(1, len(candles) - 1)]["close"]
        m1_ret = math.log(s_1 / s_0) if s_0 > 0 and s_1 > 0 else 0

        # Vol at this point
        t0 = candles[0]["open_time"]
        idx = min(max(0, np.searchsorted(vol_index, t0, side="right") - 1), len(vol_values) - 1)
        vol_1m = float(vol_values[idx])

        # M1 confirmed = |M1 ret| > 1σ
        m1_thresh = max(0.0005, vol_1m * 1.0)
        m1_confirmed = abs(m1_ret) >= m1_thresh

        # Fair price at minute 1
        fair_up = compute_fair_up(s_1, s_open, vol_1m, window_minutes - 1)

        # Generate orders
        orders = plan_orders(fair_up, bankroll, strategy, m1_confirmed, config)
        if not orders:
            continue

        # Fill rate
        filled = [o for o in orders if rng.random() < fill_rate]
        if not filled:
            continue

        cost = sum(o["price"] * o["size"] for o in filled)
        payout = sum(o["size"] for o in filled if o["outcome"] == actual)
        pnl = payout - cost

        hedge_up = [o for o in filled if o["layer"] == "hedge" and o["outcome"] == "UP"]
        hedge_dn = [o for o in filled if o["layer"] == "hedge" and o["outcome"] == "DOWN"]
        both_hedge = len(hedge_up) > 0 and len(hedge_dn) > 0

        results.append({
            "pnl": pnl, "m1_confirmed": m1_confirmed,
            "both_hedge": both_hedge, "cost": cost,
        })
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        equity_curve.append(equity)

    if not results:
        return {"error": "No trades"}

    pnls = np.array([r["pnl"] for r in results])
    wins = sum(1 for p in pnls if p > 0)
    n = len(results)
    avg = float(np.mean(pnls))
    std = float(np.std(pnls))
    sharpe = (avg / std * math.sqrt(n)) if std > 0 else 0

    m1c_trades = [r for r in results if r["m1_confirmed"]]
    m1w_trades = [r for r in results if not r["m1_confirmed"]]
    hedge_trades = [r for r in results if r["both_hedge"]]

    return {
        "strategy": strategy,
        "trades": n,
        "wins": wins,
        "wr": round(wins / n * 100, 1),
        "total_pnl": round(float(np.sum(pnls)), 2),
        "avg_pnl": round(avg, 4),
        "sharpe": round(sharpe, 2),
        "max_dd": round(max_dd, 2),
        "m1c_trades": len(m1c_trades),
        "m1c_wr": round(sum(1 for r in m1c_trades if r["pnl"] > 0) / max(1, len(m1c_trades)) * 100, 1),
        "m1c_pnl": round(sum(r["pnl"] for r in m1c_trades), 2),
        "m1w_trades": len(m1w_trades),
        "m1w_wr": round(sum(1 for r in m1w_trades if r["pnl"] > 0) / max(1, len(m1w_trades)) * 100, 1),
        "m1w_pnl": round(sum(r["pnl"] for r in m1w_trades), 2),
        "hedge_trades": len(hedge_trades),
        "hedge_pnl": round(sum(r["pnl"] for r in hedge_trades), 2),
        "equity_final": round(equity, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--fill-rate", type=float, default=1.0)
    ap.add_argument("--bankroll", type=float, default=140)
    args = ap.parse_args()

    config = MMConfig()
    config.bet_pct = 0.05

    end_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=args.days)

    print(f"\n{'='*70}")
    print(f"  V9 STRATEGY COMPARISON — {args.symbol} {args.days}d")
    print(f"  {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}")
    print(f"  Bankroll: ${args.bankroll:.0f} | Fill rate: {args.fill_rate:.0%}")
    print(f"{'='*70}")

    print(f"\n  Fetching {args.symbol} 1m klines...")
    klines = fetch_klines_range(args.symbol, "1m",
                                int(start_dt.timestamp() * 1000),
                                int(end_dt.timestamp() * 1000))
    print(f"  ✓ {len(klines):,} candles")

    windows = prepare_windows(klines, 15)
    print(f"  ✓ {len(windows):,} windows")

    vol = estimate_1m_vol(klines, 60)
    vol_vals = vol.values
    vol_idx = klines["open_time"].values.astype(np.int64)

    strategies = ["v8", "v9_dir", "v9_hybrid"]
    results = {}
    for s in strategies:
        print(f"\n  Running {s}...")
        results[s] = simulate(windows, vol_vals, vol_idx, s, config,
                              args.bankroll, args.fill_rate)

    # Print comparison
    print(f"\n{'='*70}")
    print(f"  HEAD-TO-HEAD COMPARISON")
    print(f"{'='*70}")

    header = f"  {'':20} {'v8 (old)':>12} {'v9 dir':>12} {'v9 hybrid':>12}"
    print(header)
    print(f"  {'─'*56}")

    def row(label, key, fmt="{}"):
        vals = [fmt.format(results[s].get(key, "N/A")) for s in strategies]
        print(f"  {label:20} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")

    row("Trades", "trades")
    row("Win Rate", "wr", "{}%")
    row("Total PnL", "total_pnl", "${}")
    row("Avg PnL/trade", "avg_pnl", "${}")
    row("Sharpe", "sharpe")
    row("Max Drawdown", "max_dd", "${}")
    row("M1 confirmed trades", "m1c_trades")
    row("M1 confirmed WR", "m1c_wr", "{}%")
    row("M1 confirmed PnL", "m1c_pnl", "${}")
    row("M1 weak trades", "m1w_trades")
    row("M1 weak WR", "m1w_wr", "{}%")
    row("M1 weak PnL", "m1w_pnl", "${}")
    row("Hedge trades", "hedge_trades")
    row("Hedge PnL", "hedge_pnl", "${}")

    # Winner
    best = max(strategies, key=lambda s: results[s].get("total_pnl", -99999))
    print(f"\n  WINNER: {best} (PnL ${results[best]['total_pnl']:.2f})")
    print()


if __name__ == "__main__":
    main()
