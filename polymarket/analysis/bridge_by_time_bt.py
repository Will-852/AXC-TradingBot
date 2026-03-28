#!/usr/bin/env python3
"""Bridge accuracy by entry time — backtest across 1H, 4H, Daily windows.

Computes bridge fair_up at multiple T+min slices within each window,
checks against ground truth (close >= open).

This answers: "at what T+min does bridge become accurate enough to trade?"
and "do longer TFs have better/worse bridge accuracy at equivalent % elapsed?"

Uses cached Binance 1m klines for precise intra-window computation.

Run:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/analysis/bridge_by_time_bt.py
  PYTHONPATH=.:scripts python3 polymarket/analysis/bridge_by_time_bt.py --tf 4h --days 180
  PYTHONPATH=.:scripts python3 polymarket/analysis/bridge_by_time_bt.py --tf daily --coin ETH
"""
import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import compute_fair_up

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bridge_bt")

_BINANCE = "https://api.binance.com/api/v3/klines"
_UA = "AXC-BridgeBT/1.0"
_CACHE_DIR = os.path.join(_AXC, "polymarket", "logs", "bt_cache")

# Time slices to evaluate (minutes into window)
_SLICES = {
    "1h":    [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55],
    "4h":    [15, 30, 45, 60, 75, 90, 105, 120, 150, 180, 210],
    "daily": [60, 120, 180, 240, 360, 480, 600, 720, 840, 960, 1080, 1200],
}

_WINDOW_MIN = {"1h": 60, "4h": 240, "daily": 1440}


def _fetch_1m_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch 1m klines from Binance with caching."""
    cache_key = f"1m_{symbol}_{start_ms}_{end_ms}"
    cache_path = os.path.join(_CACHE_DIR, f"{cache_key}.json")
    os.makedirs(_CACHE_DIR, exist_ok=True)

    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    all_klines = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{_BINANCE}?symbol={symbol}&interval=1m"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        try:
            with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=15) as r:
                batch = json.loads(r.read())
        except (HTTPError, URLError, TimeoutError) as e:
            log.warning("Fetch fail at %d: %s, retry", cursor, e)
            time.sleep(2)
            continue
        if not batch:
            break
        all_klines.extend(batch)
        cursor = batch[-1][6] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.3)

    # Cache
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=_CACHE_DIR, suffix=".json", delete=False)
    json.dump(all_klines, tmp)
    tmp.close()
    os.replace(tmp.name, cache_path)

    return all_klines


def _vol_from_1m(closes: list[float], window: int = 120) -> float:
    """Per-minute log-return stdev from closes."""
    if len(closes) < 30:
        return 0.001
    recent = closes[-window:] if len(closes) > window else closes
    log_rets = [math.log(recent[i] / recent[i - 1])
                for i in range(1, len(recent))
                if recent[i] > 0 and recent[i - 1] > 0]
    if len(log_rets) < 10:
        return 0.001
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
    return max(0.0001, math.sqrt(var))


def _generate_windows(tf: str, start_date: datetime, end_date: datetime) -> list[dict]:
    """Generate window boundaries."""
    windows = []
    window_td = timedelta(minutes=_WINDOW_MIN[tf])

    if tf == "1h":
        # Every hour, UTC-aligned
        cursor = start_date.replace(minute=0, second=0, microsecond=0)
        while cursor < end_date:
            windows.append({"start": cursor, "end": cursor + window_td})
            cursor += window_td

    elif tf == "4h":
        # Every 4 hours, UTC-aligned (00, 04, 08, 12, 16, 20)
        cursor = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor < end_date:
            windows.append({"start": cursor, "end": cursor + window_td})
            cursor += window_td

    elif tf == "daily":
        # Noon ET → noon ET (16:00 UTC EDT)
        ET = timezone(timedelta(hours=-4))
        cursor = start_date.replace(hour=16, minute=0, second=0, microsecond=0,
                                    tzinfo=timezone.utc)
        while cursor < end_date:
            windows.append({"start": cursor, "end": cursor + window_td})
            cursor += window_td

    return windows


def run_backtest(tf: str, symbol: str = "BTCUSDT", days: int = 365):
    """Run bridge accuracy backtest at multiple time slices."""
    window_min = _WINDOW_MIN[tf]
    slices = _SLICES[tf]

    end_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    start_date = end_date - timedelta(days=days)

    log.info("Fetching %s 1m klines for %d days...", symbol, days)
    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)

    # Fetch 1m data in chunks (max 1000 per request = ~16h)
    # For 365 days we need many chunks
    chunk_days = 7
    all_closes = {}  # timestamp_ms → close
    all_opens = {}   # timestamp_ms → open

    chunk_start = start_ms
    total_fetched = 0
    while chunk_start < end_ms:
        chunk_end = min(chunk_start + chunk_days * 86400 * 1000, end_ms)
        klines = _fetch_1m_klines(symbol, chunk_start, chunk_end)
        for k in klines:
            all_opens[k[0]] = float(k[1])
            all_closes[k[0]] = float(k[4])
        total_fetched += len(klines)
        chunk_start = chunk_end
        if total_fetched % 50000 == 0:
            log.info("  ...fetched %dk 1m candles", total_fetched // 1000)

    log.info("Total 1m candles: %d", total_fetched)

    # Generate windows
    windows = _generate_windows(tf, start_date, end_date)
    log.info("Windows: %d", len(windows))

    # 70/30 chronological split
    split_idx = int(len(windows) * 0.7)
    test_windows = windows[split_idx:]
    log.info("Test windows: %d (from idx %d)", len(test_windows), split_idx)

    # Evaluate bridge at each time slice
    slice_results = {s: {"correct": 0, "wrong": 0, "skip": 0,
                         "fair_sum": 0, "fair_n": 0} for s in slices}
    base_up = 0
    base_total = 0

    for w in test_windows:
        w_start_ms = int(w["start"].timestamp() * 1000)
        w_end_ms = int(w["end"].timestamp() * 1000)

        # Ground truth: close at window end vs open at window start
        # Find the 1m candle at window start for open
        open_price = all_opens.get(w_start_ms, 0)
        if open_price <= 0:
            # Try nearby timestamps (±1min)
            for offset in [60000, -60000, 120000]:
                open_price = all_opens.get(w_start_ms + offset, 0)
                if open_price > 0:
                    break
        if open_price <= 0:
            continue

        # Close: last 1m candle close before window end
        close_price = 0
        for offset in [0, -60000, -120000]:
            close_price = all_closes.get(w_end_ms + offset, 0)
            if close_price > 0:
                break
        if close_price <= 0:
            continue

        actual = "UP" if close_price >= open_price else "DOWN"
        base_total += 1
        if actual == "UP":
            base_up += 1

        # Evaluate bridge at each time slice
        for t_min in slices:
            t_ms = w_start_ms + t_min * 60 * 1000
            spot = all_closes.get(t_ms, 0)
            if spot <= 0:
                # Try ±1min
                for offset in [60000, -60000]:
                    spot = all_closes.get(t_ms + offset, 0)
                    if spot > 0:
                        break
            if spot <= 0:
                slice_results[t_min]["skip"] += 1
                continue

            # Compute vol from 1m closes leading up to t_ms
            vol_closes = []
            for i in range(120):
                c = all_closes.get(t_ms - i * 60000, 0)
                if c > 0:
                    vol_closes.append(c)
            vol_closes.reverse()
            vol_1m = _vol_from_1m(vol_closes)

            minutes_remaining = window_min - t_min
            fair = compute_fair_up(spot, open_price, vol_1m, minutes_remaining)

            # Prediction
            if fair > 0.5:
                pred = "UP"
            elif fair < 0.5:
                pred = "DOWN"
            else:
                slice_results[t_min]["skip"] += 1
                continue

            if pred == actual:
                slice_results[t_min]["correct"] += 1
            else:
                slice_results[t_min]["wrong"] += 1

            slice_results[t_min]["fair_sum"] += fair
            slice_results[t_min]["fair_n"] += 1

    # Print results
    base_rate = base_up / base_total if base_total > 0 else 0.5
    print(f"\n{'=' * 80}")
    print(f"  Bridge Accuracy by Entry Time — {tf.upper()} ({symbol}, {days}d)")
    print(f"{'=' * 80}")
    print(f"  Test windows: {len(test_windows)} | Matched: {base_total} | "
          f"Base rate UP: {base_rate:.1%}")
    print(f"\n  {'T+min':>6} | {'%elapsed':>8} | {'N':>5} | {'Accuracy':>8} | "
          f"{'Lift':>6} | {'Avg Fair':>8} | {'Skip':>5}")
    print(f"  {'-' * 65}")

    for t_min in slices:
        r = slice_results[t_min]
        n = r["correct"] + r["wrong"]
        if n == 0:
            continue
        acc = r["correct"] / n
        lift = acc - base_rate
        pct_elapsed = t_min / window_min * 100
        avg_fair = r["fair_sum"] / r["fair_n"] if r["fair_n"] > 0 else 0.5
        print(f"  T+{t_min:>4} | {pct_elapsed:>6.0f}%  | {n:>5} | {acc:>7.1%} | "
              f"{lift:>+5.1%} | {avg_fair:>7.1%} | {r['skip']:>5}")

    # Summary
    early = [s for s in slices if s <= window_min * 0.3]
    mid = [s for s in slices if window_min * 0.3 < s <= window_min * 0.7]
    late = [s for s in slices if s > window_min * 0.7]

    print(f"\n  === SUMMARY ===")
    for label, group in [("Early (<30%)", early), ("Mid (30-70%)", mid), ("Late (>70%)", late)]:
        total_c = sum(slice_results[s]["correct"] for s in group)
        total_w = sum(slice_results[s]["wrong"] for s in group)
        total_n = total_c + total_w
        if total_n > 0:
            avg_acc = total_c / total_n
            print(f"  {label:>15}: {avg_acc:.1%} accuracy ({total_n} predictions, "
                  f"+{avg_acc - base_rate:.1%} lift)")

    # Save results
    results = {
        "tf": tf, "symbol": symbol, "days": days,
        "n_test": base_total, "base_rate": round(base_rate, 4),
        "slices": {str(t): {
            "accuracy": round(slice_results[t]["correct"] /
                              max(1, slice_results[t]["correct"] + slice_results[t]["wrong"]), 4),
            "n": slice_results[t]["correct"] + slice_results[t]["wrong"],
            "skip": slice_results[t]["skip"],
        } for t in slices},
    }
    out_path = os.path.join(_AXC, "polymarket", "analysis", "results",
                            f"bridge_by_time_{tf}_{symbol}_{days}d.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    import tempfile as _tf
    tmp = _tf.NamedTemporaryFile(mode="w", dir=os.path.dirname(out_path),
                                  suffix=".json", delete=False)
    json.dump(results, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, out_path)
    print(f"\n  Saved: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", choices=["1h", "4h", "daily"], default="4h")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--coin", default="BTC")
    args = parser.parse_args()

    symbol = f"{args.coin}USDT"
    run_backtest(args.tf, symbol, args.days)


if __name__ == "__main__":
    main()
