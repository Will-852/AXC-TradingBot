"""
hourly_conviction_bt.py — Backtest 1H conviction pricing strategy

Core thesis: 1H window gives enough observation time to confirm BTC direction.
When direction is confirmed → enter at conviction price → hold to resolution.

Tests: wait_time × confirmation_threshold × entry_price grid
Uses Binance 1m klines as ground truth.

Resolution: Binance 1H candle close >= open → Up, else Down.

Run: PYTHONPATH=.:scripts python3 polymarket/backtest/hourly_conviction_bt.py [--days 30]
"""
import argparse
import json
import logging
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("1h_bt")

_UA = "AXC-Backtest/1.0"
_BINANCE = "https://api.binance.com/api/v3/klines"


# ═══════════════════════════════════════
# Data fetch
# ═══════════════════════════════════════

def _get(url: str, timeout: int = 10):
    try:
        with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=timeout) as r:
            return json.loads(r.read())
    except (HTTPError, URLError, TimeoutError) as e:
        log.warning("Fetch failed: %s", e)
        return None


def fetch_1m_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch 1-minute klines from Binance. Paginates automatically."""
    all_klines = []
    cursor = start_ms

    while cursor < end_ms:
        url = (f"{_BINANCE}?symbol={symbol}&interval=1m"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        data = _get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break

        for k in data:
            all_klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        cursor = data[-1][0] + 60_000  # next minute
        if len(data) < 1000:
            break
        time.sleep(0.1)  # rate limit

    return all_klines


def fetch_1h_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch 1-hour klines for resolution ground truth."""
    all_klines = []
    cursor = start_ms

    while cursor < end_ms:
        url = (f"{_BINANCE}?symbol={symbol}&interval=1h"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        data = _get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break

        for k in data:
            all_klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "close": float(k[4]),
                "high": float(k[2]),
                "low": float(k[3]),
                "volume": float(k[5]),
                "result": "UP" if float(k[4]) >= float(k[1]) else "DOWN",
            })

        cursor = data[-1][0] + 3_600_000
        if len(data) < 1000:
            break
        time.sleep(0.1)

    return all_klines


# ═══════════════════════════════════════
# Signal: direction confirmation
# ═══════════════════════════════════════

def compute_vol_1m(klines_1m: list[dict], lookback: int = 60) -> float:
    """Compute per-minute volatility from recent 1m klines."""
    if len(klines_1m) < 10:
        return 0.001
    returns = []
    for i in range(1, min(lookback, len(klines_1m))):
        r = math.log(klines_1m[i]["close"] / klines_1m[i - 1]["close"])
        returns.append(r)
    if not returns:
        return 0.001
    return max(statistics.stdev(returns), 0.0001)


def check_confirmation(klines_1m: list[dict], hour_open: float,
                       wait_minutes: int, threshold_sigma: float,
                       vol_1m: float) -> dict | None:
    """
    After waiting `wait_minutes`, check if BTC has moved enough from open.

    Returns direction signal or None if not confirmed.
    """
    if len(klines_1m) < wait_minutes:
        return None

    # Current price at wait_minutes
    current = klines_1m[wait_minutes - 1]["close"]
    move_pct = (current - hour_open) / hour_open
    threshold_pct = threshold_sigma * vol_1m * math.sqrt(wait_minutes)

    if abs(move_pct) < threshold_pct:
        return None  # not confirmed

    direction = "UP" if move_pct > 0 else "DOWN"

    # Compute fair value (Brownian Bridge)
    minutes_remaining = 60 - wait_minutes
    if minutes_remaining <= 0:
        fair_up = 0.99 if direction == "UP" else 0.01
    else:
        sigma_remaining = vol_1m * math.sqrt(minutes_remaining)
        d = math.log(current / hour_open) / sigma_remaining if sigma_remaining > 0 else 0
        # Standard normal CDF approximation
        fair_up = _norm_cdf(d)

    return {
        "direction": direction,
        "move_pct": move_pct,
        "threshold_pct": threshold_pct,
        "sigma_move": abs(move_pct) / (vol_1m * math.sqrt(wait_minutes)) if vol_1m > 0 else 0,
        "fair_up": fair_up,
        "current_price": current,
    }


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ═══════════════════════════════════════
# Backtest engine
# ═══════════════════════════════════════

def backtest_strategy(hourly: list[dict], minute_data: dict,
                      wait_minutes: int, threshold_sigma: float,
                      entry_price: float) -> dict:
    """
    Backtest one parameter combination.

    For each 1H window:
    1. Wait `wait_minutes`
    2. Check if direction confirmed (move > threshold_sigma * vol * sqrt(t))
    3. If confirmed → enter at `entry_price`
    4. Resolve: did we win?
    """
    trades = []
    skipped = 0

    for h in hourly:
        hour_start_ms = h["open_time"]
        klines = minute_data.get(hour_start_ms, [])

        if len(klines) < wait_minutes + 5:
            continue

        vol = compute_vol_1m(klines, lookback=60)
        signal = check_confirmation(klines, h["open"], wait_minutes, threshold_sigma, vol)

        if signal is None:
            skipped += 1
            continue

        # Did we win?
        won = (signal["direction"] == h["result"])

        # PnL at entry_price
        if won:
            pnl = 1.0 - entry_price  # win: pay entry_price, receive $1
        else:
            pnl = -entry_price  # lose: pay entry_price, receive $0

        trades.append({
            "hour_start": hour_start_ms,
            "direction": signal["direction"],
            "result": h["result"],
            "won": won,
            "entry_price": entry_price,
            "pnl": pnl,
            "move_pct": signal["move_pct"],
            "sigma_move": signal["sigma_move"],
            "fair_up": signal["fair_up"],
        })

    if not trades:
        return {"trades": 0, "skipped": skipped}

    wins = sum(1 for t in trades if t["won"])
    total_pnl = sum(t["pnl"] for t in trades)
    wr = wins / len(trades) if trades else 0
    avg_pnl = total_pnl / len(trades) if trades else 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t["pnl"]
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    return {
        "trades": len(trades),
        "skipped": skipped,
        "wins": wins,
        "losses": len(trades) - wins,
        "wr": round(wr * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 4),
        "max_dd": round(max_dd, 2),
        "entry_rate": round(len(trades) / (len(trades) + skipped) * 100, 1),
        "pnl_per_day": round(total_pnl / max(1, (len(trades) + skipped) / 24), 2),
    }


# ═══════════════════════════════════════
# Report
# ═══════════════════════════════════════

def print_report(results: list[dict], days: int):
    print("\n" + "=" * 90)
    print("  1H CONVICTION PRICING — BACKTEST RESULTS")
    print(f"  Period: {days} days | Asset: BTCUSDT | Resolution: Binance 1H close >= open")
    print("=" * 90)

    # Sort by total_pnl descending
    results.sort(key=lambda x: x.get("total_pnl", -999), reverse=True)

    print(f"\n{'Wait':>5s} {'Thresh':>7s} {'Entry$':>7s} {'Trades':>7s} {'Skip':>5s} "
          f"{'Entry%':>7s} {'WR%':>6s} {'Total$':>8s} {'$/trade':>8s} {'$/day':>7s} {'MaxDD':>7s}")
    print("-" * 90)

    for r in results:
        if r.get("trades", 0) == 0:
            continue
        print(f"{r['wait']:>4d}m {r['thresh']:>6.1f}σ ${r['entry']:>5.2f} "
              f"{r['trades']:>7d} {r['skipped']:>5d} "
              f"{r['entry_rate']:>6.1f}% {r['wr']:>5.1f}% "
              f"${r['total_pnl']:>7.2f} ${r['avg_pnl']:>7.4f} "
              f"${r['pnl_per_day']:>6.2f} ${r['max_dd']:>6.2f}")

    # Best combos
    print("\n── Top 5 by $/day ──")
    by_day = sorted([r for r in results if r.get("trades", 0) > 10],
                    key=lambda x: x.get("pnl_per_day", -999), reverse=True)
    for r in by_day[:5]:
        print(f"  Wait {r['wait']}m | {r['thresh']}σ | ${r['entry']:.2f} entry | "
              f"WR {r['wr']}% | ${r['pnl_per_day']:.2f}/day | "
              f"{r['trades']} trades ({r['entry_rate']}% entry rate)")

    # Win rate by wait time
    print("\n── Win Rate by Wait Time (best threshold per wait) ──")
    by_wait = defaultdict(list)
    for r in results:
        if r.get("trades", 0) > 5:
            by_wait[r["wait"]].append(r)
    for w in sorted(by_wait):
        best = max(by_wait[w], key=lambda x: x["wr"])
        print(f"  Wait {w:>3d}m: best WR={best['wr']}% "
              f"(thresh={best['thresh']}σ, entry=${best['entry']:.2f}, "
              f"n={best['trades']}, $/day=${best['pnl_per_day']:.2f})")

    # Fill rate trade-off
    print("\n── Entry Rate vs Win Rate (at $0.40 entry) ──")
    for r in sorted(results, key=lambda x: x.get("wait", 0)):
        if r.get("entry") == 0.40 and r.get("trades", 0) > 0:
            print(f"  Wait {r['wait']:>3d}m | {r['thresh']:>4.1f}σ | "
                  f"Entry rate {r['entry_rate']:>5.1f}% | WR {r['wr']:>5.1f}% | "
                  f"${r['pnl_per_day']:>6.2f}/day")

    print("\n" + "=" * 90)


# ═══════════════════════════════════════
# Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="1H conviction pricing backtest")
    ap.add_argument("--days", type=int, default=30, help="Days to backtest")
    ap.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    args = ap.parse_args()

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86_400_000

    # Step 1: Fetch 1H klines (resolution ground truth)
    log.info("Fetching %d days of 1H klines for %s...", args.days, args.symbol)
    hourly = fetch_1h_klines(args.symbol, start_ms, end_ms)
    log.info("Got %d hourly candles. Up: %d, Down: %d",
             len(hourly),
             sum(1 for h in hourly if h["result"] == "UP"),
             sum(1 for h in hourly if h["result"] == "DOWN"))

    # Step 2: Fetch 1m klines (for intra-hour analysis)
    log.info("Fetching 1m klines (this takes ~30s)...")
    all_1m = fetch_1m_klines(args.symbol, start_ms, end_ms)
    log.info("Got %d 1-minute candles", len(all_1m))

    # Index by hour
    minute_data = defaultdict(list)
    for k in all_1m:
        hour_start = (k["open_time"] // 3_600_000) * 3_600_000
        minute_data[hour_start].append(k)

    # Sort each hour's klines by time
    for hs in minute_data:
        minute_data[hs].sort(key=lambda x: x["open_time"])

    # Step 3: Grid search
    wait_times = [3, 5, 10, 15, 20, 25, 30, 40]
    thresholds = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]
    entry_prices = [0.30, 0.35, 0.40, 0.45]

    log.info("Running %d parameter combinations...",
             len(wait_times) * len(thresholds) * len(entry_prices))

    results = []
    for wait in wait_times:
        for thresh in thresholds:
            for entry in entry_prices:
                r = backtest_strategy(hourly, minute_data, wait, thresh, entry)
                r["wait"] = wait
                r["thresh"] = thresh
                r["entry"] = entry
                results.append(r)

    print_report(results, args.days)

    # Save raw results
    out_path = Path(__file__).parent.parent / "logs" / "hourly_conviction_bt.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    log.info("Results saved to %s", out_path)


if __name__ == "__main__":
    main()
