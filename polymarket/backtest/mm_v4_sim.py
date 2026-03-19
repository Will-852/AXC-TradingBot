#!/usr/bin/env python3
"""
mm_v4_sim.py — V4 Dual-Layer Historical Simulation

Uses actual market_maker.py plan_opening() + Brownian Bridge fair price
on historical 1m klines. Simulates fill rate as random gate per order.

Usage:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/mm_v4_sim.py \
        --symbol ETHUSDT --days 360 --fill-rate 0.15 --bankroll 100
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range
from polymarket.strategy.market_maker import (
    MMConfig, PlannedOrder, compute_fair_up,
)

logger = logging.getLogger(__name__)
_norm = NormalDist()

ONE_MIN_MS = 60_000
LOG_DIR = os.path.join(_PROJECT_ROOT, "polymarket", "logs")


# ═══════════════════════════════════════
#  V4 plan_opening (inlined to allow fill_rate per order)
# ═══════════════════════════════════════

def v4_plan_opening(fair_up: float, config: MMConfig, bankroll: float) -> list[dict]:
    """Replicate plan_opening logic, return dicts with outcome/price/size."""
    ZONE_1_BOUND = 0.57
    ZONE_2_BOUND = 0.65

    fair_down = 1.0 - fair_up
    confidence = max(fair_up, fair_down)

    if confidence < 0.50:
        return []

    full_budget = min(bankroll * config.bet_pct, bankroll * 0.05)
    total_cost = full_budget

    MAX_BID = 0.475
    MIN_BID = 0.35
    up_bid = round(min(MAX_BID, max(MIN_BID, fair_up - config.half_spread)), 2)
    dn_bid = round(min(MAX_BID, max(MIN_BID, fair_down - config.half_spread)), 2)
    combined = up_bid + dn_bid

    hedge_min_cost = config.min_order_size * combined
    can_hedge = total_cost >= hedge_min_cost

    if fair_up >= fair_down:
        dir_side = "UP"
    else:
        dir_side = "DOWN"
    dir_bid = up_bid if dir_side == "UP" else dn_bid

    # Zone classification
    if confidence <= ZONE_1_BOUND:
        hedge_pct, dir_pct = 1.0, 0.0
    elif confidence <= ZONE_2_BOUND:
        hedge_pct, dir_pct = 0.50, 0.50
    else:
        hedge_pct, dir_pct = 0.25, 0.75

    orders = []

    # Layer 1: Hedge
    if can_hedge and hedge_pct > 0:
        hedge_budget = total_cost * hedge_pct
        hedge_shares = hedge_budget / combined
        if hedge_shares >= config.min_order_size:
            hedge_shares = round(hedge_shares, 2)
            orders.append({"outcome": "UP", "price": up_bid, "size": hedge_shares, "layer": "hedge"})
            orders.append({"outcome": "DOWN", "price": dn_bid, "size": hedge_shares, "layer": "hedge"})

    # Layer 2: Directional
    if dir_pct > 0 and confidence > ZONE_1_BOUND:
        dir_budget = total_cost * dir_pct
        if not orders:
            dir_budget = total_cost
        dir_shares = dir_budget / dir_bid
        if dir_shares >= config.min_order_size:
            dir_shares = round(dir_shares, 2)
            orders.append({"outcome": dir_side, "price": dir_bid, "size": dir_shares, "layer": "directional"})
        elif dir_budget > 0:
            min_cost = config.min_order_size * dir_bid
            if min_cost <= bankroll * 0.05:
                orders.append({"outcome": dir_side, "price": dir_bid, "size": config.min_order_size, "layer": "directional"})

    return orders


# ═══════════════════════════════════════
#  Simulation
# ═══════════════════════════════════════

def estimate_1m_vol(klines_1m: pd.DataFrame, lookback: int = 60) -> pd.Series:
    close = klines_1m["close"].astype(float)
    log_ret = np.log(close / close.shift(1))
    vol = log_ret.rolling(lookback, min_periods=20).std()
    return vol.bfill().fillna(0.001)


def prepare_windows(klines_1m: pd.DataFrame, window_minutes: int = 15) -> list:
    windows = []
    df = klines_1m.sort_values("open_time").reset_index(drop=True)
    open_times = df["open_time"].values.astype(np.int64)
    window_ms = window_minutes * ONE_MIN_MS

    first_t = int(open_times[0])
    boundary = first_t - (first_t % window_ms) + window_ms

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


def simulate_v4(windows: list, vol_values: np.ndarray, vol_index: np.ndarray,
                config: MMConfig, bankroll: float, fill_rate: float,
                window_minutes: int = 15, seed: int = 42) -> dict:
    """Run v4 dual-layer simulation on historical windows.

    For each window:
    1. Compute fair_up at minute 1 (no look-ahead)
    2. Generate orders via v4 plan_opening
    3. Apply fill_rate per ORDER (not per market)
    4. Resolve: winning side = $1/share
    """
    rng = np.random.default_rng(seed)
    results = []
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    equity_curve = []

    skipped_no_edge = 0
    skipped_no_fill = 0
    total_orders = 0
    filled_orders = 0

    # Per-zone stats
    zone_stats = {1: {"n": 0, "wins": 0, "pnl": 0},
                  2: {"n": 0, "wins": 0, "pnl": 0},
                  3: {"n": 0, "wins": 0, "pnl": 0}}

    for w in windows:
        candles = w["candles"]
        s_open = candles[0]["open"]
        s_close = candles[-1]["close"]
        actual = "UP" if s_close > s_open else "DOWN"

        # Fair price at minute 1 (avoid look-ahead)
        s_1 = candles[min(1, len(candles)-1)]["close"]
        t0 = candles[0]["open_time"]
        idx = min(np.searchsorted(vol_index, t0, side="right") - 1, len(vol_values) - 1)
        idx = max(0, idx)
        vol_1m = float(vol_values[idx])

        fair_up = compute_fair_up(s_1, s_open, vol_1m, window_minutes - 1)

        # Generate orders
        orders = v4_plan_opening(fair_up, config, bankroll)
        if not orders:
            skipped_no_edge += 1
            continue

        # Apply fill_rate per order
        filled = []
        for o in orders:
            total_orders += 1
            if rng.random() < fill_rate:
                filled.append(o)
                filled_orders += 1

        if not filled:
            skipped_no_fill += 1
            continue

        # Calculate PnL
        cost = sum(o["price"] * o["size"] for o in filled)
        payout = sum(o["size"] for o in filled if o["outcome"] == actual)
        pnl = payout - cost

        # Zone classification
        confidence = max(fair_up, 1.0 - fair_up)
        if confidence <= 0.57:
            zone = 1
        elif confidence <= 0.65:
            zone = 2
        else:
            zone = 3

        zone_stats[zone]["n"] += 1
        zone_stats[zone]["pnl"] += pnl
        if pnl > 0:
            zone_stats[zone]["wins"] += 1

        # Check if hedge both-fill (guaranteed profit)
        hedge_up = [o for o in filled if o["layer"] == "hedge" and o["outcome"] == "UP"]
        hedge_dn = [o for o in filled if o["layer"] == "hedge" and o["outcome"] == "DOWN"]
        both_hedge_filled = len(hedge_up) > 0 and len(hedge_dn) > 0

        results.append({
            "ts": w["start"],
            "fair_up": round(fair_up, 4),
            "actual": actual,
            "zone": zone,
            "n_orders": len(orders),
            "n_filled": len(filled),
            "both_hedge": both_hedge_filled,
            "cost": round(cost, 4),
            "payout": round(payout, 4),
            "pnl": round(pnl, 4),
        })

        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        equity_curve.append(equity)

    # Summary
    if not results:
        return {"error": "No trades executed", "skipped_no_edge": skipped_no_edge,
                "skipped_no_fill": skipped_no_fill}

    pnls = np.array([r["pnl"] for r in results])
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    avg_pnl = float(np.mean(pnls))
    std_pnl = float(np.std(pnls))
    sharpe = (avg_pnl / std_pnl * math.sqrt(len(pnls))) if std_pnl > 0 else 0

    # Both-hedge stats
    both_hedge_trades = [r for r in results if r["both_hedge"]]
    both_hedge_pnl = sum(r["pnl"] for r in both_hedge_trades)

    summary = {
        "total_windows": len(windows) if windows else 0,
        "skipped_no_edge": skipped_no_edge,
        "skipped_no_fill": skipped_no_fill,
        "traded": len(results),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(results) * 100, 1),
        "total_pnl": round(float(np.sum(pnls)), 2),
        "avg_pnl": round(avg_pnl, 4),
        "median_pnl": round(float(np.median(pnls)), 4),
        "std_pnl": round(std_pnl, 4),
        "sharpe": round(sharpe, 3),
        "max_dd": round(max_dd, 2),
        "max_win": round(float(np.max(pnls)), 4),
        "max_loss": round(float(np.min(pnls)), 4),
        "total_orders": total_orders,
        "filled_orders": filled_orders,
        "actual_fill_rate": round(filled_orders / total_orders * 100, 1) if total_orders > 0 else 0,
        "both_hedge_count": len(both_hedge_trades),
        "both_hedge_pnl": round(both_hedge_pnl, 2),
        "zone_stats": {},
    }

    for z in [1, 2, 3]:
        zs = zone_stats[z]
        if zs["n"] > 0:
            summary["zone_stats"][f"Z{z}"] = {
                "n": zs["n"],
                "wins": zs["wins"],
                "wr": round(zs["wins"] / zs["n"] * 100, 1),
                "pnl": round(zs["pnl"], 2),
            }

    return {
        "summary": summary,
        "equity_curve": equity_curve,
        "trades": results,
    }


def run_sim(symbol: str = "ETHUSDT", days: int = 360, window_minutes: int = 15,
            fill_rate: float = 0.15, bankroll: float = 100.0):
    """Main simulation runner."""

    config = MMConfig(
        half_spread=0.025,
        bet_pct=0.01,   # will use max(bet_pct, 5%) = 5% in plan_opening
        min_order_size=5.0,
    )
    # Override bet_pct to match v4 live: 10% of bankroll per market
    config.bet_pct = 0.10

    end_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    print(f"\n{'='*70}")
    print(f"  V4 DUAL-LAYER SIMULATION — {symbol}")
    print(f"  Period: {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d} ({days}d)")
    print(f"  Window: {window_minutes}min | Fill rate: {fill_rate:.0%}")
    print(f"  Bankroll: ${bankroll:.0f} | Budget/market: ${bankroll * config.bet_pct:.2f}")
    print(f"  Spread: {config.half_spread:.1%} | Min order: {config.min_order_size} shares")
    print(f"{'='*70}\n")

    # Fetch data
    print(f"  Fetching {symbol} 1m klines ({days}d)...")
    klines_1m = fetch_klines_range(symbol, "1m", start_ms, end_ms)
    print(f"  ✓ {len(klines_1m):,} candles")

    windows = prepare_windows(klines_1m, window_minutes)
    print(f"  ✓ {len(windows):,} market windows")

    vol = estimate_1m_vol(klines_1m, lookback=60)
    vol_values = vol.values
    vol_index = klines_1m["open_time"].values.astype(np.int64)

    # Run simulation
    print(f"\n  Running v4 simulation (fill_rate={fill_rate:.0%})...")
    result = simulate_v4(windows, vol_values, vol_index, config, bankroll,
                         fill_rate, window_minutes)

    if "error" in result:
        print(f"\n  ❌ {result['error']}")
        return result

    s = result["summary"]

    # Print results
    print(f"\n{'='*70}")
    print(f"  RESULTS — {symbol} {days}d @ {fill_rate:.0%} fill rate")
    print(f"{'='*70}")
    print(f"  Total windows:    {s['total_windows']:>8,}")
    print(f"  Skipped (no edge):{s['skipped_no_edge']:>8,}")
    print(f"  Skipped (no fill):{s['skipped_no_fill']:>8,}")
    print(f"  Traded:           {s['traded']:>8,}")
    print(f"  Win / Loss:       {s['wins']:>5} / {s['losses']}")
    print(f"  Win Rate:         {s['win_rate']:>7.1f}%")
    print(f"  Total PnL:       ${s['total_pnl']:>10.2f}")
    print(f"  Avg PnL/trade:   ${s['avg_pnl']:>10.4f}")
    print(f"  Median PnL:      ${s['median_pnl']:>10.4f}")
    print(f"  Sharpe:           {s['sharpe']:>8.3f}")
    print(f"  Max Drawdown:    ${s['max_dd']:>10.2f}")
    print(f"  Max Win:         ${s['max_win']:>10.4f}")
    print(f"  Max Loss:        ${s['max_loss']:>10.4f}")
    print(f"  Orders placed:    {s['total_orders']:>8,}")
    print(f"  Orders filled:    {s['filled_orders']:>8,}")
    print(f"  Actual fill rate: {s['actual_fill_rate']:>7.1f}%")
    print(f"  Both-hedge fills: {s['both_hedge_count']:>8,} (PnL: ${s['both_hedge_pnl']:.2f})")

    print(f"\n  ── Zone Breakdown ──")
    for zname, zdata in s["zone_stats"].items():
        print(f"  {zname}: {zdata['n']:>5} trades | WR {zdata['wr']:>5.1f}% | PnL ${zdata['pnl']:>8.2f}")

    # Annualized
    if s['traded'] > 0 and days > 0:
        daily_pnl = s['total_pnl'] / days
        annual_pnl = daily_pnl * 365
        trades_per_day = s['traded'] / days
        print(f"\n  ── Annualized ──")
        print(f"  Daily PnL:       ${daily_pnl:>8.2f}")
        print(f"  Annual PnL:      ${annual_pnl:>8.2f}")
        print(f"  Trades/day:       {trades_per_day:>7.1f}")

    # Equity curve (ASCII)
    eq = result["equity_curve"]
    if eq:
        print(f"\n  ── Equity Curve ──")
        step = max(1, len(eq) // 20)
        for i in range(0, len(eq), step):
            v = eq[i]
            if v >= 0:
                bar = "█" * min(50, int(v * 0.5))
            else:
                bar = "░" * min(30, int(-v * 0.5))
            print(f"  #{i+1:>5}  ${v:>10.2f}  {bar}")
        print(f"  #{len(eq):>5}  ${eq[-1]:>10.2f}  ← final")

    # Save
    os.makedirs(LOG_DIR, exist_ok=True)
    result_path = os.path.join(LOG_DIR, f"mm_v4_sim_{symbol}_{days}d_fr{int(fill_rate*100)}.json")
    output = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "days": days,
        "window_minutes": window_minutes,
        "fill_rate": fill_rate,
        "bankroll": bankroll,
        "config": {"half_spread": config.half_spread, "bet_pct": config.bet_pct,
                   "min_order_size": config.min_order_size},
        "summary": s,
    }
    fd, tmp = tempfile.mkstemp(dir=LOG_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        os.replace(tmp, result_path)
        print(f"\n  Results → {result_path}")
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return output


def main():
    parser = argparse.ArgumentParser(description="V4 Dual-Layer MM Simulation")
    parser.add_argument("--symbol", default="ETHUSDT", help="Symbol (default: ETHUSDT)")
    parser.add_argument("--days", type=int, default=360, help="Days (default: 360)")
    parser.add_argument("--window", type=int, default=15, help="Window minutes (default: 15)")
    parser.add_argument("--fill-rate", type=float, default=0.15, help="Fill rate (default: 0.15)")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll (default: 100)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run_sim(symbol=args.symbol, days=args.days, window_minutes=args.window,
            fill_rate=args.fill_rate, bankroll=args.bankroll)


if __name__ == "__main__":
    main()
