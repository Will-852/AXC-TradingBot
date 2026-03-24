#!/usr/bin/env python3
"""
sol_1h_backtest.py — SOL 1H momentum persistence backtest

Tests whether early price movement within a 1-hour window predicts
the final direction — pure momentum persistence, no indicators.

Methodology:
  For each 1H window:
    p_open  = price at T+0
    p_delay = price at T+delay (signal measurement point)
    p_close = price at T+3600s (window end)
    signal_return = log(p_delay / p_open) in bps
    If |signal_return| > threshold → signal fires
    Win if sign(signal_return) == sign(p_close - p_open)

Sweep:
  delay:     [300, 600, 900, 1200, 1800] seconds
  threshold: [5, 10, 15, 20, 30] bps

Run:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/analysis/sol_1h_backtest.py
  python3 polymarket/analysis/sol_1h_backtest.py --days 180
"""

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sol_1h_bt")

_UA = "AXC-Backtest/1.0"
_BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1/klines"
_CACHE_DIR = Path(__file__).parent.parent / "logs" / "bt_cache"

DELAYS_SEC = [300, 600, 900, 1200, 1800]
THRESHOLDS_BPS = [5, 10, 15, 20, 30]


# ═══════════════════════════════════════
#  Fetch helpers
# ═══════════════════════════════════════

def _get(url: str, timeout: int = 15):
    try:
        with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=timeout) as r:
            return json.loads(r.read())
    except (HTTPError, URLError, TimeoutError) as e:
        log.warning("Fetch failed %s — %s", url[:80], e)
        return None


def fetch_5m_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """
    Fetch 5-minute futures klines for SOL with disk cache.
    Uses 5M interval (288 candles/day) — good resolution for delay measurements.
    Each candle represents a 5-min bucket; open_time is the candle start.
    """
    cache_file = _CACHE_DIR / f"5m_{symbol}_{start_ms}_{end_ms}.json"
    if cache_file.exists():
        log.info("Cache hit: %s", cache_file.name)
        return json.loads(cache_file.read_text())

    all_klines = []
    cursor = start_ms
    page = 0
    while cursor < end_ms:
        url = (
            f"{_BINANCE_FUTURES}?symbol={symbol}&interval=5m"
            f"&startTime={cursor}&endTime={end_ms}&limit=1500"
        )
        data = _get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            log.warning("Empty response at cursor=%d (page %d)", cursor, page)
            break
        for k in data:
            all_klines.append({
                "open_time_ms": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        last_time = data[-1][0]
        cursor = last_time + 5 * 60 * 1000  # advance 5 minutes
        page += 1
        log.info(
            "Fetched page %d — %d candles so far, last: %s",
            page,
            len(all_klines),
            datetime.fromtimestamp(last_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        if len(data) < 1500:
            break
        time.sleep(0.15)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(all_klines))
    log.info("Cached %d 5M candles to %s", len(all_klines), cache_file.name)
    return all_klines


# ═══════════════════════════════════════
#  Build price lookup from 5M klines
# ═══════════════════════════════════════

def build_price_index(klines_5m: list[dict]) -> dict[int, float]:
    """
    Returns {open_time_ms: open_price} for fast lookup.
    We use candle OPEN as the price at that timestamp — cleaner than interpolation.
    """
    return {k["open_time_ms"]: k["open"] for k in klines_5m}


def price_at(index: dict[int, float], target_ms: int, tolerance_ms: int = 150_000) -> float | None:
    """
    Look up price at target_ms. Snaps to nearest 5M candle open within tolerance.
    5M candles = 300_000ms apart; tolerance 150s = half a candle.
    """
    # Round to nearest 5M bucket
    bucket_ms = 5 * 60 * 1000  # 300_000
    snapped = round(target_ms / bucket_ms) * bucket_ms
    p = index.get(snapped)
    if p is not None:
        return p
    # Try exact
    p = index.get(target_ms)
    if p is not None:
        return p
    # Search within tolerance
    for delta in range(0, tolerance_ms + 1, 60_000):
        p = index.get(target_ms + delta) or index.get(target_ms - delta)
        if p is not None:
            return p
    return None


# ═══════════════════════════════════════
#  Core backtest logic
# ═══════════════════════════════════════

def run_backtest(klines_5m: list[dict], delays: list[int], thresholds: list[float]) -> dict:
    """
    For each 1H window aligned to :00, measure momentum signal vs outcome.

    Returns nested dict: results[delay][threshold] = {"wins": int, "total": int, "fires": int}
    """
    if not klines_5m:
        raise ValueError("No kline data")

    price_idx = build_price_index(klines_5m)

    # All unique 1H window starts in dataset
    first_ms = klines_5m[0]["open_time_ms"]
    last_ms = klines_5m[-1]["open_time_ms"]

    # Align to next full hour
    first_hour_ms = math.ceil(first_ms / 3_600_000) * 3_600_000
    # Last window must end before data ends
    last_hour_start_ms = (last_ms // 3_600_000 - 1) * 3_600_000

    window_starts = []
    t = first_hour_ms
    while t <= last_hour_start_ms:
        window_starts.append(t)
        t += 3_600_000

    log.info(
        "Backtest: %d 1H windows, %s → %s",
        len(window_starts),
        datetime.fromtimestamp(first_hour_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
        datetime.fromtimestamp(last_hour_start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
    )

    # Initialize accumulators
    results = {}
    for d in delays:
        results[d] = {}
        for thr in thresholds:
            results[d][thr] = {"wins": 0, "total": 0, "fires": 0, "no_data": 0}

    for w_start in window_starts:
        w_end = w_start + 3_600_000  # +60 minutes

        p_open = price_at(price_idx, w_start)
        p_close = price_at(price_idx, w_end)

        if p_open is None or p_close is None or p_open <= 0:
            continue

        actual_return_bps = math.log(p_close / p_open) * 10_000
        actual_up = actual_return_bps > 0

        for delay_sec in delays:
            delay_ms = delay_sec * 1000
            p_delay = price_at(price_idx, w_start + delay_ms)

            if p_delay is None or p_delay <= 0:
                for thr in thresholds:
                    results[delay_sec][thr]["no_data"] += 1
                continue

            signal_bps = math.log(p_delay / p_open) * 10_000

            for thr in thresholds:
                results[delay_sec][thr]["total"] += 1

                if abs(signal_bps) > thr:
                    results[delay_sec][thr]["fires"] += 1
                    signal_up = signal_bps > 0
                    if signal_up == actual_up:
                        results[delay_sec][thr]["wins"] += 1

    return results, len(window_starts)


# ═══════════════════════════════════════
#  Output formatting
# ═══════════════════════════════════════

def print_matrix(results: dict, total_windows: int, delays: list, thresholds: list):
    """Print WR + fire_rate + EV matrix for each delay × threshold combo."""

    print("\n" + "=" * 80)
    print("SOL 1H MOMENTUM PERSISTENCE BACKTEST")
    print("=" * 80)
    print(f"Total 1H windows evaluated: {total_windows}")
    print()

    # EV note: assuming binary Polymarket at fair 50¢, payout 100¢
    # EV per bet = WR * 100 - 100¢ cost → but we simplify to WR vs 50% baseline

    for delay in delays:
        print(f"\n{'─'*70}")
        print(f"DELAY = {delay}s ({delay//60}min into window)")
        print(f"{'─'*70}")
        print(f"{'Threshold':>12} {'Fires':>8} {'Fire%':>7} {'WR%':>7} {'EV (bps)':>10} {'Edge':>8}")
        print(f"{'(bps)':>12} {'':>8} {'':>7} {'':>7} {'':>10} {'':>8}")
        print(f"{'─'*12} {'─'*8} {'─'*7} {'─'*7} {'─'*10} {'─'*8}")

        for thr in thresholds:
            r = results[delay][thr]
            fires = r["fires"]
            total = r["total"]
            wins = r["wins"]

            fire_pct = 100.0 * fires / total if total > 0 else 0.0
            wr = 100.0 * wins / fires if fires > 0 else 0.0
            # EV in bps assuming 50¢ fair binary, win payout = 2x
            # EV = WR * 1 - (1-WR) * 1 = 2*WR - 1 (in fraction)
            # In bps: (2*WR - 1) * 10000
            ev_bps = (2 * (wr / 100) - 1) * 10_000
            edge_marker = ""
            if fires >= 30:
                if wr >= 60:
                    edge_marker = "★★★"
                elif wr >= 57:
                    edge_marker = "★★"
                elif wr >= 55:
                    edge_marker = "★"
                elif wr <= 45:
                    edge_marker = "✗"

            print(
                f"{thr:>12} {fires:>8} {fire_pct:>6.1f}% {wr:>6.1f}% {ev_bps:>+10.0f} {edge_marker:>8}"
            )


def print_summary(results: dict, delays: list, thresholds: list):
    """Find and highlight best combos."""
    print("\n" + "=" * 80)
    print("TOP COMBOS (min 50 fires, WR ranked)")
    print("=" * 80)
    print(f"{'Delay':>8} {'Threshold':>10} {'Fires':>8} {'WR%':>7} {'EV(bps)':>10}")

    rows = []
    for d in delays:
        for thr in thresholds:
            r = results[d][thr]
            fires = r["fires"]
            wins = r["wins"]
            if fires >= 50:
                wr = 100.0 * wins / fires
                ev = (2 * (wr / 100) - 1) * 10_000
                rows.append((wr, d, thr, fires, ev))

    rows.sort(reverse=True)
    for wr, d, thr, fires, ev in rows[:10]:
        print(f"{d:>6}s {thr:>10}bps {fires:>8} {wr:>6.1f}% {ev:>+10.0f}")

    if not rows:
        print("  (no combos with 50+ fires)")


def print_volatility_note(klines_5m: list[dict]):
    """Quick SOL volatility stats for context."""
    if len(klines_5m) < 288:
        return

    # Hourly log returns
    hourly_returns = []
    price_idx = build_price_index(klines_5m)
    first_ms = klines_5m[0]["open_time_ms"]
    last_ms = klines_5m[-1]["open_time_ms"]
    t = math.ceil(first_ms / 3_600_000) * 3_600_000
    while t + 3_600_000 <= last_ms:
        p0 = price_at(price_idx, t)
        p1 = price_at(price_idx, t + 3_600_000)
        if p0 and p1 and p0 > 0:
            hourly_returns.append(math.log(p1 / p0) * 10_000)
        t += 3_600_000

    if len(hourly_returns) < 10:
        return

    sorted_r = sorted(hourly_returns)
    n = len(sorted_r)
    mean_r = sum(hourly_returns) / n
    variance = sum((r - mean_r) ** 2 for r in hourly_returns) / n
    std_bps = math.sqrt(variance)
    up_pct = 100.0 * sum(1 for r in hourly_returns if r > 0) / n
    p25 = sorted_r[n // 4]
    p75 = sorted_r[3 * n // 4]
    p95 = sorted_r[int(0.95 * n)]
    p05 = sorted_r[int(0.05 * n)]

    print("\n" + "=" * 80)
    print("SOL 1H VOLATILITY PROFILE")
    print("=" * 80)
    print(f"  Windows:      {n}")
    print(f"  Mean return:  {mean_r:+.1f} bps")
    print(f"  Std dev:      {std_bps:.1f} bps")
    print(f"  Up %:         {up_pct:.1f}%")
    print(f"  P5/P25/P75/P95: {p05:.0f} / {p25:.0f} / {p75:.0f} / {p95:.0f} bps")


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="SOL 1H momentum backtest")
    parser.add_argument("--days", type=int, default=180, help="Days of history (default 180)")
    parser.add_argument("--symbol", type=str, default="SOLUSDT", help="Binance futures symbol")
    parser.add_argument("--no-cache", action="store_true", help="Ignore disk cache")
    args = parser.parse_args()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 24 * 3_600_000

    log.info("Fetching %d days of %s 5M futures klines...", args.days, args.symbol)

    if args.no_cache:
        # Remove any existing cache
        for f in _CACHE_DIR.glob(f"5m_{args.symbol}_*.json"):
            f.unlink()
            log.info("Removed cache: %s", f.name)

    klines = fetch_5m_klines(args.symbol, start_ms, now_ms)

    if not klines:
        log.error("No data fetched. Check connectivity and symbol name.")
        sys.exit(1)

    log.info("Loaded %d 5M candles", len(klines))

    # Volatility profile
    print_volatility_note(klines)

    # Run backtest
    results, total_windows = run_backtest(klines, DELAYS_SEC, THRESHOLDS_BPS)

    # Print matrices
    print_matrix(results, total_windows, DELAYS_SEC, THRESHOLDS_BPS)

    # Print summary ranking
    print_summary(results, DELAYS_SEC, THRESHOLDS_BPS)

    print("\n" + "=" * 80)
    print("INTERPRETATION GUIDE")
    print("=" * 80)
    print("  WR > 55% (min 50 fires) = potential edge worth investigating")
    print("  WR > 57%                = strong signal, explore further")
    print("  WR > 60%                = exceptional (BTC 15M level)")
    print("  EV > 0                  = profitable direction at fair 50¢")
    print("  EV +1000 bps            = +10% edge per bet (before fees)")
    print()
    print("  Polymarket 15M fee = 2% each side = need WR > 52% to break even")
    print("  Polymarket 1H fee  = typically tighter spread, 1-2%")
    print()
    print("  Note: WR here = signal direction vs BTC price direction.")
    print("  Poly market WR may differ due to market maker pricing.")
    print("=" * 80)


if __name__ == "__main__":
    main()
