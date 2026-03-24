#!/opt/homebrew/bin/python3
"""
Momentum Win Rate Calculator for Polymarket 5M BTC Binary Markets.

Fetches BTC/USDT 1-second klines from Binance, then evaluates a simple
momentum signal: use the first N seconds of price movement within each
5-minute window to predict whether BTC closes higher or lower than its open.

Design decisions:
- 1s klines cached to disk to avoid hammering Binance on reruns.
- All timestamps are UTC-aligned; 5M windows start at :00, :05, :10, ...
- Close price of each 1s candle is used as the reference price at that second.
- Missing seconds are filled via nearest-available (forward then backward).

Usage:
    python3 momentum_wr_calculator.py [--days 30] [--no-cache]
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "1s"
KLINE_LIMIT = 1000  # Binance max per request

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_FILE = DATA_DIR / "btc_1s_klines.json"
RESULTS_FILE = DATA_DIR / "momentum_wr_results.json"

# Analysis parameters
ENTRY_DELAYS = [7, 9, 15, 25, 30, 60, 120]
MOMENTUM_THRESHOLDS_BPS = [0, 2, 5, 8, 10, 15, 20, 30]
WINDOW_SECONDS = 300  # 5 minutes

# Break-even parameters (from wallet reverse-engineering data)
LEAN_RATIOS = [1.20, 1.36, 1.50]  # R values
COMBINED_COSTS = [1.00, 1.02, 1.04]  # C values (combined price per pair)

# Rate limiting
REQUEST_DELAY_S = 0.12  # ~8 req/s, well under Binance 1200/min

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_klines_for_range(start_ms: int, end_ms: int) -> dict[int, float]:
    """
    Fetch 1s klines from Binance for [start_ms, end_ms].
    Returns dict mapping open_time_ms -> close_price.
    Paginates automatically; respects rate limits.
    """
    prices: dict[int, float] = {}
    cursor = start_ms
    total_ms = end_ms - start_ms
    fetched_chunks = 0

    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": KLINE_LIMIT,
        }
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        for candle in data:
            # candle: [open_time, open, high, low, close, volume, close_time, ...]
            open_time_ms = int(candle[0])
            close_price = float(candle[4])
            prices[open_time_ms] = close_price

        last_open = int(data[-1][0])
        cursor = last_open + 1000  # next second

        fetched_chunks += 1
        progress_pct = min(100.0, (cursor - start_ms) / total_ms * 100)
        if fetched_chunks % 50 == 0:
            log.info(
                "  Fetching... %.1f%% (%d klines so far)",
                progress_pct,
                len(prices),
            )

        time.sleep(REQUEST_DELAY_S)

    return prices


def load_or_fetch_data(days: int, use_cache: bool) -> dict[int, float]:
    """
    Load cached klines or fetch from Binance.
    Returns dict: timestamp_ms -> close_price.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Determine date range: last N full days ending yesterday (UTC)
    now_utc = datetime.now(timezone.utc)
    end_date = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=days)
    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000) - 1  # exclusive of midnight today

    # Try cache
    if use_cache and CACHE_FILE.exists():
        log.info("Loading cached data from %s", CACHE_FILE.name)
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)

        cached_start = cache.get("start_ms", 0)
        cached_end = cache.get("end_ms", 0)
        cached_prices = cache.get("prices", {})

        if cached_start <= start_ms and cached_end >= end_ms and cached_prices:
            log.info(
                "Cache covers requested range (%d klines). Using cache.",
                len(cached_prices),
            )
            # Filter to requested range
            prices = {
                int(k): v
                for k, v in cached_prices.items()
                if start_ms <= int(k) <= end_ms
            }
            return prices
        else:
            log.info("Cache range doesn't cover request. Re-fetching.")

    # Fetch day by day to show progress
    log.info(
        "Fetching BTC/USDT 1s klines: %s to %s (%d days)",
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
        days,
    )
    all_prices: dict[int, float] = {}
    for day_offset in range(days):
        day_start = start_date + timedelta(days=day_offset)
        day_end = day_start + timedelta(days=1)
        day_start_ms = int(day_start.timestamp() * 1000)
        day_end_ms = int(day_end.timestamp() * 1000) - 1

        log.info(
            "  Day %d/%d: %s",
            day_offset + 1,
            days,
            day_start.strftime("%Y-%m-%d"),
        )
        day_prices = fetch_klines_for_range(day_start_ms, day_end_ms)
        all_prices.update(day_prices)

    # Save cache
    log.info("Caching %d klines to %s", len(all_prices), CACHE_FILE.name)
    cache_data = {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "fetched_at": now_utc.isoformat(),
        "days": days,
        "prices": {str(k): v for k, v in all_prices.items()},
    }
    # Atomic write via tempfile
    tmp_path = CACHE_FILE.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(cache_data, f)
    os.replace(tmp_path, CACHE_FILE)

    return all_prices


# ---------------------------------------------------------------------------
# Price lookup with nearest-available fallback
# ---------------------------------------------------------------------------

def get_price(prices: dict[int, float], target_ms: int, max_drift_ms: int = 5000) -> float | None:
    """
    Get price at target_ms. If missing, search forward then backward
    within max_drift_ms. Returns None if nothing found.
    """
    if target_ms in prices:
        return prices[target_ms]

    # Forward search
    for offset in range(1000, max_drift_ms + 1, 1000):
        if (target_ms + offset) in prices:
            return prices[target_ms + offset]

    # Backward search
    for offset in range(1000, max_drift_ms + 1, 1000):
        if (target_ms - offset) in prices:
            return prices[target_ms - offset]

    return None


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_windows(
    prices: dict[int, float],
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """
    For each 5-minute window in [start_ms, end_ms], compute:
    - open price (T+0), close price (T+300s)
    - resolution (UP/DOWN/FLAT)
    - momentum at each entry delay
    """
    windows = []
    window_ms = WINDOW_SECONDS * 1000
    current = start_ms

    while current + window_ms <= end_ms + 1:
        open_price = get_price(prices, current)
        close_price = get_price(prices, current + window_ms)

        if open_price is None or close_price is None:
            current += window_ms
            continue

        # Resolution
        if close_price > open_price:
            resolution = "UP"
        elif close_price < open_price:
            resolution = "DOWN"
        else:
            resolution = "FLAT"

        # Momentum at each delay
        momentums = {}
        for delay in ENTRY_DELAYS:
            delay_price = get_price(prices, current + delay * 1000)
            if delay_price is not None and open_price != 0:
                mom_bps = (delay_price - open_price) / open_price * 10000
                momentums[delay] = mom_bps
            else:
                momentums[delay] = None

        dt = datetime.fromtimestamp(current / 1000, tz=timezone.utc)
        windows.append({
            "timestamp_ms": current,
            "datetime_utc": dt.isoformat(),
            "hour": dt.hour,
            "weekday": dt.weekday(),  # 0=Mon
            "open_price": open_price,
            "close_price": close_price,
            "resolution": resolution,
            "momentums": momentums,
        })

        current += window_ms

    return windows


def compute_wr_table(
    windows: list[dict],
    delay: int,
    thresholds: list[int],
) -> list[dict]:
    """
    For a given entry delay, compute WR at each momentum threshold.
    Returns list of dicts with threshold, wr, correct, incorrect, skip, total.
    """
    rows = []
    for thresh in thresholds:
        correct = 0
        incorrect = 0
        skip = 0

        for w in windows:
            if w["resolution"] == "FLAT":
                skip += 1
                continue

            mom = w["momentums"].get(delay)
            if mom is None:
                skip += 1
                continue

            abs_mom = abs(mom)
            if abs_mom < thresh:
                skip += 1
                continue

            # Signal direction
            if mom > 0:
                signal = "UP"
            elif mom < 0:
                signal = "DOWN"
            else:
                skip += 1
                continue

            if signal == w["resolution"]:
                correct += 1
            else:
                incorrect += 1

        total = correct + incorrect
        wr = (correct / total * 100) if total > 0 else 0.0

        rows.append({
            "threshold_bps": thresh,
            "wr_pct": round(wr, 2),
            "correct": correct,
            "incorrect": incorrect,
            "total": total,
            "skip": skip,
        })

    return rows


# ---------------------------------------------------------------------------
# Break-even analysis
# ---------------------------------------------------------------------------

def break_even_wr(R: float, C: float, lean_price: float = 0.50) -> float:
    """
    Calculate break-even WR for a both-sides strategy with lean ratio R
    and combined cost C.

    Model (per 1 unit of hedge shares):
      Lean shares: R, Hedge shares: 1
      Lean price: lean_price (around 0.50 for ~equal binary)
      Hedge price: C - lean_price (since combined = C for a matched pair,
                    but excess lean shares are unmatched)

    Actually, more precise model:
      Total shares = R + 1 (lean + hedge)
      Matched pairs = 1 (limited by hedge side)
      Excess lean = R - 1

      Matched pair cost = lean_price + hedge_price = C
      Matched pair PnL = 1.00 - C (win or lose, one side pays $1)

      Excess lean cost = (R-1) * lean_price
      If lean wins:  excess revenue = (R-1) * 1.00
      If lean loses: excess revenue = (R-1) * 0.00

    Total PnL:
      Win  = matched*(1-C) + (R-1)*(1.00 - lean_price)
      Lose = matched*(1-C) + (R-1)*(0.00 - lean_price)

    With matched = 1:
      Win  = (1-C) + (R-1)*(1 - lean_price)
      Lose = (1-C) - (R-1)*lean_price

    Break-even: WR * Win + (1-WR) * Lose = 0
    WR = -Lose / (Win - Lose)
    """
    win_pnl = (1 - C) + (R - 1) * (1 - lean_price)
    lose_pnl = (1 - C) - (R - 1) * lean_price

    # Win - Lose = (R-1)*(1-lean_price) - (-(R-1)*lean_price) = (R-1)
    # So WR = -lose_pnl / (R-1)
    denominator = win_pnl - lose_pnl
    if denominator == 0:
        return 50.0

    be_wr = -lose_pnl / denominator * 100
    return round(be_wr, 2)


def verify_break_even():
    """
    Verify with the user-provided example:
    58 lean at $0.56, 42 hedge at $0.48, combined = $1.04
    R = 58/42 ≈ 1.381, lean_price = 0.56
    Expected break-even ≈ 66.5%
    """
    R = 58 / 42
    C = 1.04
    lean_price = 0.56
    be = break_even_wr(R, C, lean_price)
    # Win: (1-1.04) + (1.381-1)*(1-0.56) = -0.04 + 0.381*0.44 = -0.04 + 0.1676 = 0.1276
    # Lose: (1-1.04) - (1.381-1)*0.56 = -0.04 - 0.2134 = -0.2534
    # WR = 0.2534 / (0.1276+0.2534) = 0.2534/0.381 = 66.5%
    assert abs(be - 66.5) < 0.5, f"Break-even verification failed: got {be}, expected ~66.5"


# ---------------------------------------------------------------------------
# Hourly / day-of-week breakdown
# ---------------------------------------------------------------------------

def compute_hourly_wr(windows: list[dict], delay: int, threshold_bps: float = 0) -> dict[int, dict]:
    """WR broken down by hour of day (UTC)."""
    by_hour: dict[int, dict] = defaultdict(lambda: {"correct": 0, "incorrect": 0, "skip": 0})

    for w in windows:
        h = w["hour"]
        if w["resolution"] == "FLAT":
            by_hour[h]["skip"] += 1
            continue
        mom = w["momentums"].get(delay)
        if mom is None or abs(mom) < threshold_bps:
            by_hour[h]["skip"] += 1
            continue
        if mom == 0:
            by_hour[h]["skip"] += 1
            continue
        signal = "UP" if mom > 0 else "DOWN"
        if signal == w["resolution"]:
            by_hour[h]["correct"] += 1
        else:
            by_hour[h]["incorrect"] += 1

    result = {}
    for h in range(24):
        d = by_hour[h]
        total = d["correct"] + d["incorrect"]
        wr = (d["correct"] / total * 100) if total > 0 else 0
        result[h] = {"wr_pct": round(wr, 2), "total": total, **d}
    return result


def compute_dow_wr(windows: list[dict], delay: int, threshold_bps: float = 0) -> dict[int, dict]:
    """WR broken down by day of week (0=Mon)."""
    DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow: dict[int, dict] = defaultdict(lambda: {"correct": 0, "incorrect": 0, "skip": 0})

    for w in windows:
        d = w["weekday"]
        if w["resolution"] == "FLAT":
            by_dow[d]["skip"] += 1
            continue
        mom = w["momentums"].get(delay)
        if mom is None or abs(mom) < threshold_bps:
            by_dow[d]["skip"] += 1
            continue
        if mom == 0:
            by_dow[d]["skip"] += 1
            continue
        signal = "UP" if mom > 0 else "DOWN"
        if signal == w["resolution"]:
            by_dow[d]["correct"] += 1
        else:
            by_dow[d]["incorrect"] += 1

    result = {}
    for d in range(7):
        data = by_dow[d]
        total = data["correct"] + data["incorrect"]
        wr = (data["correct"] / total * 100) if total > 0 else 0
        result[d] = {"name": DOW_NAMES[d], "wr_pct": round(wr, 2), "total": total, **data}
    return result


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def print_header(start_date: str, end_date: str, total_windows: int, flat_count: int):
    print("\n" + "=" * 60)
    print("  BTC 5M Momentum Signal Win Rate Analysis")
    print("=" * 60)
    print(f"  Date range : {start_date} to {end_date}")
    print(f"  Total 5M windows : {total_windows:,}")
    print(f"  FLAT (excluded)  : {flat_count:,}")
    print("=" * 60)


def print_wr_table(delay: int, rows: list[dict], total_windows: int):
    print(f"\n--- Entry Delay: {delay}s ---")
    print(f"{'Threshold':>10} {'WR':>7} {'Correct':>9} {'Total':>9} {'Skip':>7}")
    print("-" * 45)
    for r in rows:
        print(
            f"{r['threshold_bps']:>7} bps"
            f" {r['wr_pct']:>6.1f}%"
            f" {r['correct']:>8,}"
            f" {r['total']:>8,}"
            f" {r['skip']:>6,}"
        )


def print_break_even_table():
    """Print break-even WR for various lean ratios and combined costs."""
    print("\n" + "=" * 60)
    print("  Break-Even Win Rate Table")
    print("  (lean_price assumed = $0.50 for symmetric binary)")
    print("=" * 60)

    header = f"{'R \\ C':>8}"
    for C in COMBINED_COSTS:
        header += f"  C={C:.2f}"
    print(header)
    print("-" * (8 + 9 * len(COMBINED_COSTS)))

    results = {}
    for R in LEAN_RATIOS:
        line = f"  R={R:.2f}"
        for C in COMBINED_COSTS:
            be = break_even_wr(R, C, lean_price=0.50)
            line += f"  {be:>5.1f}%"
            results[(R, C)] = be
        print(line)

    # Also compute with lean_price=0.56 (Unlawful-Shear's actual avg)
    print("\n  (lean_price = $0.56, closer to real fills)")
    header = f"{'R \\ C':>8}"
    for C in COMBINED_COSTS:
        header += f"  C={C:.2f}"
    print(header)
    print("-" * (8 + 9 * len(COMBINED_COSTS)))
    for R in LEAN_RATIOS:
        line = f"  R={R:.2f}"
        for C in COMBINED_COSTS:
            be = break_even_wr(R, C, lean_price=0.56)
            line += f"  {be:>5.1f}%"
        print(line)

    return results


def print_viable_combos(
    all_tables: dict[int, list[dict]],
    be_targets: dict[tuple[float, float], float],
):
    """
    Print which (delay, threshold) combos exceed each break-even target.
    """
    print("\n" + "=" * 60)
    print("  Viable (Delay, Threshold) Combinations")
    print("=" * 60)

    # Use a few key break-even targets
    key_targets = [
        (1.36, 1.00),
        (1.36, 1.02),
        (1.36, 1.04),
    ]

    for (R, C) in key_targets:
        be = be_targets.get((R, C), 50.0)
        print(f"\n  R={R:.2f}, C={C:.2f} => break-even WR = {be:.1f}%")
        print(f"  {'Delay':>6}s  {'Thresh':>7}  {'WR':>7}  {'Trades':>7}  {'Pass?':>6}")
        print("  " + "-" * 42)

        found_any = False
        for delay in ENTRY_DELAYS:
            for row in all_tables[delay]:
                if row["total"] < 50:  # need minimum sample
                    continue
                if row["wr_pct"] >= be:
                    marker = "  YES"
                    found_any = True
                    print(
                        f"  {delay:>6}  {row['threshold_bps']:>5} bps"
                        f"  {row['wr_pct']:>5.1f}%"
                        f"  {row['total']:>6,}"
                        f"  {marker}"
                    )

        if not found_any:
            print("  (none)")


def print_hourly_breakdown(hourly: dict[int, dict]):
    """Print WR by hour of day."""
    print("\n--- Hourly WR Breakdown (UTC, delay=15s, threshold=0 bps) ---")
    print(f"{'Hour':>6} {'WR':>7} {'Correct':>9} {'Total':>7}")
    print("-" * 35)
    for h in range(24):
        d = hourly[h]
        print(f"  {h:02d}:00 {d['wr_pct']:>6.1f}% {d['correct']:>8,} {d['total']:>6,}")


def print_dow_breakdown(dow: dict[int, dict]):
    """Print WR by day of week."""
    print("\n--- Day-of-Week WR Breakdown (delay=15s, threshold=0 bps) ---")
    print(f"{'Day':>6} {'WR':>7} {'Correct':>9} {'Total':>7}")
    print("-" * 35)
    for d in range(7):
        data = dow[d]
        print(f"  {data['name']:>4} {data['wr_pct']:>6.1f}% {data['correct']:>8,} {data['total']:>6,}")


def print_trading_estimate(
    all_tables: dict[int, list[dict]],
    total_days: int,
    be_wr: float,
    best_delay: int = 15,
    lean_R: float = 1.36,
    combined: float = 1.04,
    bet_size_usd: float = 10.0,
):
    """
    Estimate: if you only trade windows where momentum signal > break-even,
    how many trades per day and what's the daily EV?
    """
    print("\n" + "=" * 60)
    print("  Trading Estimate (if only entering above break-even)")
    print("=" * 60)
    print(f"  Assumptions: delay={best_delay}s, R={lean_R}, C={combined}, bet=${bet_size_usd}")
    print(f"  Break-even WR: {be_wr:.1f}%")
    print()

    for row in all_tables[best_delay]:
        if row["total"] < 50:
            continue
        if row["wr_pct"] <= be_wr:
            continue

        trades_per_day = row["total"] / total_days if total_days > 0 else 0
        edge_pct = (row["wr_pct"] - be_wr) / 100.0

        # Simplified EV per trade:
        # Win PnL and Lose PnL per $1 notional (lean_price=0.50 model)
        win_pnl = (1 - combined) + (lean_R - 1) * 0.50
        lose_pnl = (1 - combined) - (lean_R - 1) * 0.50
        wr = row["wr_pct"] / 100.0
        ev_per_unit = wr * win_pnl + (1 - wr) * lose_pnl

        # Scale to bet_size (total notional = bet_size * (R+1), but EV scales linearly)
        ev_per_trade = ev_per_unit * bet_size_usd
        daily_ev = ev_per_trade * trades_per_day

        print(
            f"  Threshold {row['threshold_bps']:>3} bps: "
            f"WR={row['wr_pct']:.1f}%, "
            f"{trades_per_day:.1f} trades/day, "
            f"EV/trade=${ev_per_trade:.3f}, "
            f"Daily EV=${daily_ev:.2f}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="BTC 5M Momentum Signal WR Calculator"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days to analyze (default: 30)",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force re-fetch from Binance (ignore cache)",
    )
    args = parser.parse_args()

    # Verify break-even formula before running
    verify_break_even()

    # 1. Fetch / load data
    prices = load_or_fetch_data(args.days, use_cache=not args.no_cache)
    log.info("Loaded %d 1s price points", len(prices))

    if not prices:
        log.error("No price data available. Check Binance API access.")
        sys.exit(1)

    # Determine actual date range from data
    all_ts = sorted(prices.keys())
    start_ms = all_ts[0]
    end_ms = all_ts[-1]
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    # Align start to 5-minute boundary (round up)
    aligned_start_ms = start_ms - (start_ms % (WINDOW_SECONDS * 1000))
    if aligned_start_ms < start_ms:
        aligned_start_ms += WINDOW_SECONDS * 1000

    # 2. Analyze all 5-minute windows
    log.info("Analyzing 5-minute windows...")
    windows = analyze_windows(prices, aligned_start_ms, end_ms)
    log.info("Found %d valid 5M windows", len(windows))

    flat_count = sum(1 for w in windows if w["resolution"] == "FLAT")
    total_days = (end_ms - start_ms) / (86400 * 1000)

    # 3. Compute WR tables for each delay
    all_tables: dict[int, list[dict]] = {}
    for delay in ENTRY_DELAYS:
        all_tables[delay] = compute_wr_table(windows, delay, MOMENTUM_THRESHOLDS_BPS)

    # 4. Print results
    print_header(
        start_dt.strftime("%Y-%m-%d"),
        end_dt.strftime("%Y-%m-%d"),
        len(windows),
        flat_count,
    )

    for delay in ENTRY_DELAYS:
        print_wr_table(delay, all_tables[delay], len(windows))

    # 5. Break-even analysis
    be_targets = print_break_even_table()

    # 6. Viable combinations
    print_viable_combos(all_tables, be_targets)

    # 7. Hourly and day-of-week breakdowns (using delay=15s as reference)
    hourly = compute_hourly_wr(windows, delay=15, threshold_bps=0)
    print_hourly_breakdown(hourly)

    dow = compute_dow_wr(windows, delay=15, threshold_bps=0)
    print_dow_breakdown(dow)

    # 8. Trading estimate
    # Use R=1.36, C=1.04 (Unlawful-Shear reference)
    target_be = break_even_wr(1.36, 1.04, lean_price=0.50)
    print_trading_estimate(
        all_tables,
        total_days=max(1, int(total_days)),
        be_wr=target_be,
        best_delay=15,
    )

    # 9. Save results to JSON
    results_data = {
        "meta": {
            "start_date": start_dt.isoformat(),
            "end_date": end_dt.isoformat(),
            "total_windows": len(windows),
            "flat_count": flat_count,
            "days": args.days,
        },
        "wr_tables": {
            str(delay): all_tables[delay] for delay in ENTRY_DELAYS
        },
        "hourly_wr": {str(h): hourly[h] for h in range(24)},
        "dow_wr": {str(d): dow[d] for d in range(7)},
        "break_even": {
            f"R={R}_C={C}": break_even_wr(R, C, 0.50)
            for R in LEAN_RATIOS
            for C in COMBINED_COSTS
        },
    }

    tmp_results = RESULTS_FILE.with_suffix(".tmp")
    with open(tmp_results, "w") as f:
        json.dump(results_data, f, indent=2)
    os.replace(tmp_results, RESULTS_FILE)
    log.info("Results saved to %s", RESULTS_FILE)

    print("\n" + "=" * 60)
    print(f"  Results saved to: {RESULTS_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
