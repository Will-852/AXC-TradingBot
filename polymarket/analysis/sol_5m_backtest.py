#!/usr/bin/env python3
"""
SOL 5M Momentum Backtest

Methodology:
- Fetch SOLUSDT 1M futures klines from Binance (3+ months)
- Build 5M windows aligned to epoch boundaries (:00/:05/:10/...)
- For each window: measure signal at T+delay vs T+0, outcome at T+300 vs T+0
- Signal: |log(p_delay/p_open)| > threshold → fire in direction of return
- Outcome: sign(log(p_close/p_open))
- Win if signal direction == outcome direction

Design decisions:
- Uses 1M candle open prices for precise sub-5M timing
- 5M window = 300s, aligned to 300s epoch (UTC)
- p_open = open price of 1M candle at window start
- p_delay = open price of 1M candle at T+delay (nearest candle)
- p_close = open price of 1M candle at T+300 (next window start)
- BTC baseline for comparison: 84% WR at delay=120s, threshold=5bps
"""

import math
import time
from datetime import datetime, timezone

import requests

# ─── CONFIG ───────────────────────────────────────────────────────────────────

SYMBOL = "SOLUSDT"
INTERVAL = "1m"
BINANCE_FUTURES_URL = "https://fapi.binance.com/fapi/v1/klines"

# 3 months back from 2026-03-24
END_MS   = 1742774400000   # 2026-03-24 00:00 UTC in ms
START_MS = 1734998400000   # 2025-12-24 00:00 UTC (~3 months)

WINDOW_SEC = 300  # 5 minutes

# Parameter sweep
DELAYS_SEC      = [15, 30, 60, 90, 120]
THRESHOLDS_BPS  = [3, 5, 7, 10]

# Polymarket binary market EV: win pays +$0.99, loss pays -$0.01 (on $1 bet)
# Both sides total $1.00, so win = +$0.99 on the winning side
# EV = WR * 0.99 - (1-WR) * 1.0  (lose full $1 on wrong side)
# But standard Polymarket: buy YES at price p, if win get $1 back → profit = (1-p)
# Simplified: assume fair market at ~0.50 → EV = WR - (1-WR) = 2*WR - 1
# Per task spec: combined=$0.99 total outlay → EV = WR * 0.99 - (1-WR) * 0.99?
# Task says: "EV = WR × win_amount - (1-WR) × loss_amount, assuming combined=$0.99"
# Standard binary: buy one side at ~0.50 → if win get $1, net +$0.50; if lose net -$0.50
# With $0.99 combined (both sides): each side ~$0.495 → win one, lose other
# Net if correct: +$0.505 (one side wins $1 - cost $0.495); lose side cost = $0.495
# Per-trade EV = WR * (1 - 0.495) - (1 - WR) * 0.495
#              = WR * 0.505 - (1 - WR) * 0.495
# Simplified as per task: treat as win_amount = (1 - 0.495) = 0.505, loss_amount = 0.495
WIN_AMOUNT  = 0.505   # profit on winning side ($1 payout - $0.495 cost)
LOSS_AMOUNT = 0.495   # loss on losing side

# BTC baseline for comparison
BTC_BASELINE = {"delay": 120, "threshold": 5, "win_rate": 0.84}


# ─── DATA FETCH ───────────────────────────────────────────────────────────────

def fetch_klines_paginated(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """
    Fetch Binance futures klines via paginated calls.
    Returns list of dicts with open_time_ms and open (price).
    Uses 1000 candles per page (Binance max).
    """
    all_candles = []
    cursor = start_ms
    limit = 1000

    print(f"Fetching {symbol} {interval} klines from Binance futures...")
    print(f"  Range: {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d')} "
          f"→ {datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")

    page = 0
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": limit,
        }
        resp = requests.get(BINANCE_FUTURES_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        for row in data:
            all_candles.append({
                "open_time_ms": int(row[0]),
                "open":         float(row[1]),
                "close":        float(row[4]),
            })

        last_ts = int(data[-1][0])
        page += 1
        if page % 5 == 0:
            print(f"  Page {page}: fetched {len(all_candles):,} candles, last = "
                  f"{datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")

        # Advance cursor past last candle (1 minute = 60_000ms)
        cursor = last_ts + 60_000

        if len(data) < limit:
            break

        # Polite rate limit
        time.sleep(0.12)

    # Deduplicate by open_time_ms (in case of overlap at boundaries)
    seen = set()
    deduped = []
    for c in all_candles:
        if c["open_time_ms"] not in seen:
            seen.add(c["open_time_ms"])
            deduped.append(c)

    deduped.sort(key=lambda x: x["open_time_ms"])
    print(f"  Done: {len(deduped):,} candles total")
    return deduped


# ─── WINDOW BUILDER ───────────────────────────────────────────────────────────

def build_price_indices(candles: list) -> tuple[dict, dict]:
    """
    Build two dicts: open_time_ms → open price, open_time_ms → close price.
    Used to look up prices at 1M candle open and close.
    """
    open_idx  = {c["open_time_ms"]: c["open"]  for c in candles}
    close_idx = {c["open_time_ms"]: c["close"] for c in candles}
    return open_idx, close_idx


def floor_to_minute(ts_ms: int) -> int:
    """Floor timestamp to 1-minute boundary."""
    return (ts_ms // 60_000) * 60_000


def get_delay_price(open_idx: dict, close_idx: dict, window_start_sec: int, delay_sec: int) -> float | None:
    """
    Get price at T+delay_sec relative to window_start_sec.

    With 1M klines, we have prices at each minute boundary.
    - delay < 60s: the delay lands within minute 0 of the window.
      Use the CLOSE of that first candle as proxy (≈ price ~60s in, best available).
      NOTE: this slightly overestimates delay effect for 15s/30s — a known limitation.
    - delay >= 60s: use the OPEN of the candle at that minute boundary.

    Design decision: using close for <60s delays gives a meaningful (non-zero) signal
    while clearly labeling the limitation. The alternative (floor to open) produces
    zero fires since p_delay == p_open, which is worse than a biased proxy.
    """
    delay_ms  = window_start_sec * 1000 + delay_sec * 1000
    candle_ms = floor_to_minute(delay_ms)

    if delay_sec < 60:
        # Delay lands within first candle; use its close as best proxy
        return close_idx.get(candle_ms)
    else:
        # Delay lands at or after next candle boundary; use that candle's open
        return open_idx.get(candle_ms)


def run_backtest(candles: list) -> list:
    """
    Core backtest loop.

    Returns list of result dicts for each (delay, threshold) combo.
    """
    if not candles:
        raise ValueError("No candle data")

    open_idx, close_idx = build_price_indices(candles)

    # 5M window boundaries aligned to 300s UTC epoch
    first_ts_sec = candles[0]["open_time_ms"] // 1000
    last_ts_sec  = candles[-1]["open_time_ms"] // 1000

    # Start at first 300s boundary >= first candle
    first_window = ((first_ts_sec + 299) // 300) * 300
    # End: last window where T+300 is still covered
    last_window  = ((last_ts_sec - 300) // 300) * 300

    print(f"\nWindow range:")
    print(f"  First: {datetime.fromtimestamp(first_window, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    print(f"  Last:  {datetime.fromtimestamp(last_window,  tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")

    total_windows = (last_window - first_window) // 300 + 1
    print(f"  Total potential windows: {total_windows:,}")

    # Note on sub-minute delays
    print(f"  NOTE: delays <60s use 1M candle close as proxy (limitation of 1M resolution)")

    results = []

    for delay in DELAYS_SEC:
        for threshold in THRESHOLDS_BPS:
            fires       = 0
            wins        = 0
            total_valid = 0

            w_ts = first_window
            while w_ts <= last_window:
                open_ms  = w_ts * 1000
                close_ms = w_ts * 1000 + 300_000   # next window start = close of this window

                p_open  = open_idx.get(open_ms)
                p_delay = get_delay_price(open_idx, close_idx, w_ts, delay)
                p_close = open_idx.get(close_ms)

                if p_open is None or p_delay is None or p_close is None:
                    w_ts += 300
                    continue
                if p_open <= 0 or p_delay <= 0 or p_close <= 0:
                    w_ts += 300
                    continue

                total_valid += 1

                signal_return_bps = math.log(p_delay / p_open) * 10_000
                outcome_return    = math.log(p_close / p_open)

                if abs(signal_return_bps) > threshold:
                    fires += 1
                    signal_dir  = 1 if signal_return_bps > 0 else -1
                    outcome_dir = 1 if outcome_return > 0 else -1
                    if signal_dir == outcome_dir:
                        wins += 1

                w_ts += 300

            win_rate  = wins / fires if fires > 0 else 0.0
            fire_rate = fires / total_valid if total_valid > 0 else 0.0
            ev        = win_rate * WIN_AMOUNT - (1 - win_rate) * LOSS_AMOUNT if fires > 0 else 0.0

            results.append({
                "delay":        delay,
                "threshold":    threshold,
                "total_valid":  total_valid,
                "fires":        fires,
                "wins":         wins,
                "win_rate":     win_rate,
                "fire_rate":    fire_rate,
                "ev":           ev,
            })

    return results


# ─── REPORTING ────────────────────────────────────────────────────────────────

def print_summary(results: list, candles: list):
    """Print clean summary table sorted by EV descending."""
    results_sorted = sorted(results, key=lambda x: x["ev"], reverse=True)

    first_ts = candles[0]["open_time_ms"] // 1000
    last_ts  = candles[-1]["open_time_ms"] // 1000
    days     = (last_ts - first_ts) / 86400

    print("\n" + "=" * 80)
    print(f"SOL 5M MOMENTUM BACKTEST — {SYMBOL}")
    print(f"Data: {datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime('%Y-%m-%d')} → "
          f"{datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime('%Y-%m-%d')} "
          f"({days:.0f} days, {len(candles):,} 1M candles)")
    print("=" * 80)
    print(f"\nBTC BASELINE (for comparison): delay=120s | threshold=5bps | WR=84%")
    print()

    header = f"{'Delay':>7} {'Thresh':>7} {'Fires':>7} {'Valid':>8} {'FireRate':>9} {'WinRate':>9} {'EV/trade':>10}"
    print(header)
    print("-" * len(header))

    for r in results_sorted:
        flag = ""
        if r["delay"] == BTC_BASELINE["delay"] and r["threshold"] == BTC_BASELINE["threshold"]:
            flag = " ◄ BTC equiv"
        elif r["ev"] == results_sorted[0]["ev"]:
            flag = " ◄ BEST EV"

        print(f"{r['delay']:>6}s {r['threshold']:>6}bps {r['fires']:>7,} {r['total_valid']:>8,} "
              f"{r['fire_rate']:>8.1%} {r['win_rate']:>8.1%} {r['ev']:>+9.4f}{flag}")

    print("-" * len(header))

    # Best combo summary
    best = results_sorted[0]
    btc_equiv = next((r for r in results if r["delay"] == 120 and r["threshold"] == 5), None)

    print(f"\nSOL BEST: delay={best['delay']}s | threshold={best['threshold']}bps | "
          f"WR={best['win_rate']:.1%} | fires={best['fires']:,} | EV={best['ev']:+.4f}")

    if btc_equiv:
        diff = btc_equiv["win_rate"] - BTC_BASELINE["win_rate"]
        verdict = "MATCHES" if abs(diff) < 0.03 else ("BEATS" if diff > 0 else "UNDERPERFORMS")
        print(f"\nSOL vs BTC (120s/5bps): SOL={btc_equiv['win_rate']:.1%} vs BTC={BTC_BASELINE['win_rate']:.1%} "
              f"→ SOL {verdict} BTC by {abs(diff):.1%}")

    print()
    print("EV assumes Polymarket binary: combined=$0.99, win net=+$0.505, lose net=-$0.495")
    print("Positive EV = edge over random; EV>0.01 = meaningful edge")
    print("NOTE: delays 15s/30s use 1M candle close as proxy (~60s actual) — treat as indicative only")
    print("=" * 80)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"SOL 5M Momentum Backtest")
    print(f"Delays: {DELAYS_SEC}s | Thresholds: {THRESHOLDS_BPS}bps")
    print(f"Period: {datetime.fromtimestamp(START_MS/1000, tz=timezone.utc).strftime('%Y-%m-%d')} "
          f"→ {datetime.fromtimestamp(END_MS/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    print()

    candles = fetch_klines_paginated(SYMBOL, INTERVAL, START_MS, END_MS)

    if len(candles) < 1000:
        print(f"WARNING: Only {len(candles)} candles fetched — results may not be reliable")

    print(f"\nRunning backtest sweep ({len(DELAYS_SEC) * len(THRESHOLDS_BPS)} combos)...")
    results = run_backtest(candles)

    print_summary(results, candles)


if __name__ == "__main__":
    main()
