"""
bridge_weight_bt.py — Test bridge-only vs indicator weight for 15M + 1H

Core question: What % weight should Brownian Bridge get vs indicators?
Tests both timeframes with same methodology.

For each window:
  1. At minute N, compute bridge P(Up) from BTC price vs open
  2. Simulate "indicator says opposite" (worst case) at various weights
  3. Check if bridge-only or blended is more accurate

Run: PYTHONPATH=.:scripts python3 polymarket/backtest/bridge_weight_bt.py
"""
import json
import logging
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("bridge_weight")

_BINANCE = "https://api.binance.com/api/v3/klines"
_UA = "AXC-BT/1.0"
_norm_cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _get(url, timeout=10):
    try:
        with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_klines(symbol, interval, start_ms, end_ms):
    all_k = []
    cursor = start_ms
    while cursor < end_ms:
        data = _get(f"{_BINANCE}?symbol={symbol}&interval={interval}"
                    f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        if not data:
            break
        for k in data:
            all_k.append({"t": k[0], "o": float(k[1]), "h": float(k[2]),
                          "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
        cursor = data[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.1)
    return all_k


def bridge_fair(current, open_price, vol_1m, minutes_remaining, haircut=0.10):
    """Brownian Bridge P(close >= open) with fat-tail haircut."""
    if minutes_remaining <= 0 or vol_1m <= 0:
        return 0.995 if current >= open_price else 0.005
    sigma = vol_1m * math.sqrt(minutes_remaining)
    if sigma < 1e-10:
        return 0.995 if current >= open_price else 0.005
    d = math.log(current / open_price) / sigma
    fair = max(0.005, min(0.995, _norm_cdf(d)))
    if haircut > 0:
        fair = 0.50 + (fair - 0.50) * (1.0 - haircut)
    return fair


def test_timeframe(symbol, interval_str, window_minutes, days=30):
    """Test bridge accuracy at different wait times for a given timeframe."""
    log.info("Testing %s %s (%d-min windows, %d days)...", symbol, interval_str, window_minutes, days)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000

    # Fetch candles for resolution
    candles = fetch_klines(symbol, interval_str, start_ms, end_ms)
    log.info("Got %d %s candles", len(candles), interval_str)

    # Fetch 1m candles for intra-window analysis
    log.info("Fetching 1m klines...")
    all_1m = fetch_klines(symbol, "1m", start_ms, end_ms)
    log.info("Got %d 1m candles", len(all_1m))

    # Index 1m by parent window
    minute_by_window = defaultdict(list)
    for k in all_1m:
        # Find which window this belongs to
        window_ms = window_minutes * 60 * 1000
        ws = (k["t"] // window_ms) * window_ms
        minute_by_window[ws].append(k)

    # Compute historical vol
    returns = []
    for i in range(1, min(500, len(all_1m))):
        if all_1m[i-1]["c"] > 0:
            returns.append(math.log(all_1m[i]["c"] / all_1m[i-1]["c"]))
    vol_1m = statistics.stdev(returns) if len(returns) > 10 else 0.00077
    log.info("vol_1m = %.5f", vol_1m)

    # Test grid: wait_time × signal_scenario
    wait_times = [1, 3, 5, 10, 15, 20, 25, 30]
    if window_minutes == 60:
        wait_times = [3, 5, 10, 15, 20, 25, 30, 40, 50]

    results = []

    for wait in wait_times:
        if wait >= window_minutes - 2:
            continue

        bridge_correct = 0
        bridge_wrong = 0
        bridge_skip = 0  # too close to 50/50
        contrarian_correct = 0  # indicator says opposite, is it right?

        for candle in candles:
            ws = candle["t"]
            actual = "UP" if candle["c"] >= candle["o"] else "DOWN"
            mins = minute_by_window.get(ws, [])

            if len(mins) < wait + 1:
                continue

            # Price at wait_time
            price_at_wait = mins[wait]["c"]
            open_price = candle["o"]

            # Bridge prediction
            remaining = window_minutes - wait
            fair = bridge_fair(price_at_wait, open_price, vol_1m, remaining)
            bridge_dir = "UP" if fair >= 0.50 else "DOWN"
            confidence = abs(fair - 0.50) * 2

            # Skip coin-flip zone
            if confidence < 0.10:
                bridge_skip += 1
                continue

            if bridge_dir == actual:
                bridge_correct += 1
            else:
                bridge_wrong += 1

            # Contrarian: if indicator says opposite of bridge
            contrarian_dir = "DOWN" if bridge_dir == "UP" else "UP"
            if contrarian_dir == actual:
                contrarian_correct += 1

        total = bridge_correct + bridge_wrong
        if total < 5:
            continue

        bridge_wr = bridge_correct / total
        contrarian_wr = contrarian_correct / total

        # Optimal blend weight (bridge_w) that maximizes accuracy
        # If bridge WR = 70% and contrarian WR = 30%, pure bridge is best
        # If bridge WR = 55% and contrarian WR = 45%, blending helps
        # Breakeven: at what bridge_weight does blended WR = bridge WR?
        # No analytical formula — just report the raw numbers

        results.append({
            "wait": wait,
            "total": total,
            "skipped": bridge_skip,
            "bridge_wr": round(bridge_wr * 100, 1),
            "contrarian_wr": round(contrarian_wr * 100, 1),
            "entry_rate": round(total / (total + bridge_skip) * 100, 1),
        })

    return results, vol_1m


def print_results(label, interval, results, vol):
    print(f"\n{'=' * 70}")
    print(f"  {label} — Bridge-Only Accuracy")
    print(f"  vol_1m={vol:.5f}")
    print(f"{'=' * 70}")
    print(f"  {'Wait':>5s} {'N':>6s} {'Skip':>6s} {'Entry%':>7s} {'Bridge WR':>10s} {'Contra WR':>10s} {'Verdict':>12s}")
    print(f"  {'-' * 65}")

    for r in results:
        # Verdict: how much better is bridge vs random (50%)?
        edge = r["bridge_wr"] - 50
        if r["bridge_wr"] >= 80:
            verdict = "STRONG"
        elif r["bridge_wr"] >= 65:
            verdict = "GOOD"
        elif r["bridge_wr"] >= 55:
            verdict = "WEAK"
        else:
            verdict = "COIN-FLIP"

        print(f"  {r['wait']:>4d}m {r['total']:>6d} {r['skipped']:>6d} "
              f"{r['entry_rate']:>6.1f}% {r['bridge_wr']:>9.1f}% "
              f"{r['contrarian_wr']:>9.1f}% {verdict:>12s}")

    # Summary: optimal wait time (highest WR with decent entry rate)
    good = [r for r in results if r["entry_rate"] > 30 and r["total"] > 20]
    if good:
        best = max(good, key=lambda x: x["bridge_wr"])
        print(f"\n  Best: Wait {best['wait']}m → Bridge WR {best['bridge_wr']}% "
              f"(entry rate {best['entry_rate']}%, n={best['total']})")

    # Weight recommendation
    print(f"\n  WEIGHT RECOMMENDATION:")
    for r in results:
        if r["total"] < 20:
            continue
        # If bridge WR > 65%, indicators only hurt (contrarian < 35%)
        # → recommend 90%+ bridge weight
        if r["bridge_wr"] >= 75:
            rec = "90% bridge / 10% indicator"
        elif r["bridge_wr"] >= 65:
            rec = "80% bridge / 20% indicator"
        elif r["bridge_wr"] >= 55:
            rec = "60% bridge / 40% indicator"
        else:
            rec = "50/50 (bridge has no edge)"
        print(f"    Wait {r['wait']:>3d}m: {rec} (bridge {r['bridge_wr']:.0f}%)")


def main():
    end_ms = int(time.time() * 1000)
    days = 30

    # Test 15M
    r15, v15 = test_timeframe("BTCUSDT", "15m", 15, days)
    print_results("BTC 15-MINUTE", "15m", r15, v15)

    # Test 1H
    r1h, v1h = test_timeframe("BTCUSDT", "1h", 60, days)
    print_results("BTC 1-HOUR", "1h", r1h, v1h)


if __name__ == "__main__":
    main()
