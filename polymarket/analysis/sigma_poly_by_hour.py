#!/usr/bin/env python3
"""
σ_poly Time-of-Day Analysis
----------------------------
Computes Polymarket mid-price volatility (σ_poly) per 15M market,
then aggregates by HKT hour to find optimal entry windows.

σ_poly = std(Δup_mid) where Δup_mid = consecutive changes per ~20s tick.
This is the key variable in P(fill) = 2Φ(-(M₀-b)/(σ_poly√τ)).

If σ_poly has predictable ToD patterns, we can time entries to high-σ
windows for better fill rates (wider mid-price movement = more fills).

Usage: python3 polymarket/analysis/sigma_poly_by_hour.py
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Constants ---
SIGNAL_TAPE_PATH = Path(__file__).resolve().parent.parent / "logs" / "signal_tape.jsonl"
OUTPUT_JSON_PATH = Path(__file__).resolve().parent / "sigma_poly_results.json"
HKT = timezone(timedelta(hours=8))
MIN_TICKS_PER_MARKET = 5  # Markets with fewer ticks are unreliable
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_signal_tape(path: Path) -> list[dict[str, Any]]:
    """Load signal_tape.jsonl, return list of parsed records."""
    records = []
    with open(path, "r") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Line %d: JSON parse error: %s", i + 1, e)
    logger.info("Loaded %d snapshots from %s", len(records), path)
    return records


def extract_market_series(records: list[dict]) -> dict[str, list[tuple[datetime, float]]]:
    """
    Group by condition_id -> list of (timestamp, up_mid) tuples.
    Each condition_id is one 15M market window.
    """
    market_data: dict[str, dict] = {}  # cid -> {series, coin, title}

    for rec in records:
        ts_str = rec["ts"]
        ts = datetime.fromisoformat(ts_str).astimezone(HKT)

        for poly in rec.get("poly", []):
            cid = poly["cid"]
            up_mid = poly.get("up_mid")

            # Skip invalid up_mid
            if up_mid is None or up_mid == 0 or np.isnan(up_mid):
                continue

            if cid not in market_data:
                market_data[cid] = {
                    "series": [],
                    "coin": poly.get("coin", "?"),
                    "title": poly.get("title", ""),
                }
            market_data[cid]["series"].append((ts, up_mid))

    logger.info("Extracted %d unique markets", len(market_data))
    return market_data


def compute_sigma_per_market(
    market_data: dict[str, dict],
) -> list[dict[str, Any]]:
    """
    For each market, compute σ_poly = std(Δup_mid).
    Returns list of {cid, coin, title, sigma, n_ticks, hkt_hour, dow, first_ts, last_ts}.
    """
    results = []
    skipped_low_ticks = 0
    skipped_zero_sigma = 0

    for cid, info in market_data.items():
        series = info["series"]
        # Sort by timestamp (should be mostly sorted, but be safe)
        series.sort(key=lambda x: x[0])

        n_ticks = len(series)
        if n_ticks < MIN_TICKS_PER_MARKET:
            skipped_low_ticks += 1
            continue

        # Compute consecutive differences in up_mid
        up_mids = np.array([s[1] for s in series])
        deltas = np.diff(up_mids)

        if len(deltas) == 0:
            continue

        sigma = float(np.std(deltas, ddof=1))  # Sample std

        if np.isnan(sigma) or sigma == 0:
            skipped_zero_sigma += 1
            continue

        # Determine HKT hour: use the midpoint of the market's observation window
        first_ts = series[0][0]
        last_ts = series[-1][0]
        mid_ts = first_ts + (last_ts - first_ts) / 2
        hkt_hour = mid_ts.hour
        dow = mid_ts.weekday()  # 0=Mon, 6=Sun

        results.append({
            "cid": cid,
            "coin": info["coin"],
            "title": info["title"],
            "sigma": sigma,
            "n_ticks": n_ticks,
            "hkt_hour": hkt_hour,
            "dow": dow,
            "first_ts": first_ts.isoformat(),
            "last_ts": last_ts.isoformat(),
        })

    logger.info(
        "Computed σ for %d markets (skipped: %d low-ticks, %d zero-sigma)",
        len(results), skipped_low_ticks, skipped_zero_sigma,
    )
    return results


def aggregate_by_hour(market_sigmas: list[dict]) -> dict[int, dict]:
    """Aggregate σ_poly by HKT hour (0-23)."""
    by_hour: dict[int, list[float]] = defaultdict(list)

    for m in market_sigmas:
        by_hour[m["hkt_hour"]].append(m["sigma"])

    result = {}
    for hour in range(24):
        sigmas = by_hour.get(hour, [])
        if not sigmas:
            result[hour] = {
                "n_markets": 0,
                "mean": None, "median": None, "p25": None, "p75": None,
            }
            continue

        arr = np.array(sigmas)
        result[hour] = {
            "n_markets": len(sigmas),
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "p25": float(np.percentile(arr, 25)),
            "p75": float(np.percentile(arr, 75)),
        }
    return result


def aggregate_by_dow_hour(market_sigmas: list[dict]) -> dict[str, dict]:
    """Aggregate σ_poly by (day_of_week, hour) -> 168-hour weekly pattern."""
    by_dow_hour: dict[tuple[int, int], list[float]] = defaultdict(list)

    for m in market_sigmas:
        key = (m["dow"], m["hkt_hour"])
        by_dow_hour[key].append(m["sigma"])

    result = {}
    for dow in range(7):
        for hour in range(24):
            key = (dow, hour)
            sigmas = by_dow_hour.get(key, [])
            label = f"{DOW_NAMES[dow]}-{hour:02d}"

            if not sigmas:
                result[label] = {
                    "dow": dow, "hour": hour, "n_markets": 0,
                    "mean": None, "median": None, "p25": None, "p75": None,
                }
                continue

            arr = np.array(sigmas)
            result[label] = {
                "dow": dow,
                "hour": hour,
                "n_markets": len(sigmas),
                "mean": float(np.mean(arr)),
                "median": float(np.median(arr)),
                "p25": float(np.percentile(arr, 25)),
                "p75": float(np.percentile(arr, 75)),
            }
    return result


def aggregate_by_coin_hour(market_sigmas: list[dict]) -> dict[str, dict[int, dict]]:
    """Aggregate σ_poly by coin × HKT hour."""
    by_coin_hour: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    for m in market_sigmas:
        by_coin_hour[m["coin"]][m["hkt_hour"]].append(m["sigma"])

    result = {}
    for coin in sorted(by_coin_hour.keys()):
        result[coin] = {}
        for hour in range(24):
            sigmas = by_coin_hour[coin].get(hour, [])
            if not sigmas:
                result[coin][hour] = {
                    "n_markets": 0,
                    "mean": None, "median": None, "p25": None, "p75": None,
                }
                continue
            arr = np.array(sigmas)
            result[coin][hour] = {
                "n_markets": len(sigmas),
                "mean": float(np.mean(arr)),
                "median": float(np.median(arr)),
                "p25": float(np.percentile(arr, 25)),
                "p75": float(np.percentile(arr, 75)),
            }
    return result


def print_hourly_table(hourly: dict[int, dict], title: str = "ALL COINS"):
    """Print formatted hourly σ_poly table."""
    print(f"\n{'='*72}")
    print(f"  σ_poly by HKT Hour — {title}")
    print(f"{'='*72}")
    print(f"{'Hour(HKT)':>10}  {'N_mkts':>7}  {'σ_mean':>8}  {'σ_median':>8}  {'σ_p25':>8}  {'σ_p75':>8}")
    print("-" * 72)

    for hour in range(24):
        h = hourly[hour]
        if h["n_markets"] == 0:
            print(f"{hour:>10}  {'—':>7}  {'—':>8}  {'—':>8}  {'—':>8}  {'—':>8}")
        else:
            print(
                f"{hour:>10}  {h['n_markets']:>7}  "
                f"{h['mean']:>8.4f}  {h['median']:>8.4f}  "
                f"{h['p25']:>8.4f}  {h['p75']:>8.4f}"
            )


def print_top_bottom(hourly: dict[int, dict], n: int = 5):
    """Print top-N and bottom-N hours by σ_poly mean."""
    ranked = [
        (hour, h) for hour, h in hourly.items()
        if h["n_markets"] > 0 and h["mean"] is not None
    ]
    ranked.sort(key=lambda x: x[1]["mean"], reverse=True)

    print(f"\n{'='*72}")
    print("  TOP-5 Hours (HIGHEST σ_poly → best fill probability)")
    print(f"{'='*72}")
    for rank, (hour, h) in enumerate(ranked[:n], 1):
        print(
            f"  #{rank}  HKT {hour:02d}:00  "
            f"σ_mean={h['mean']:.4f}  σ_median={h['median']:.4f}  "
            f"N={h['n_markets']}"
        )

    print(f"\n{'='*72}")
    print("  BOTTOM-5 Hours (LOWEST σ_poly → worst fill probability)")
    print(f"{'='*72}")
    for rank, (hour, h) in enumerate(ranked[-n:], 1):
        print(
            f"  #{rank}  HKT {hour:02d}:00  "
            f"σ_mean={h['mean']:.4f}  σ_median={h['median']:.4f}  "
            f"N={h['n_markets']}"
        )


def print_dow_hour_heatmap(dow_hour: dict[str, dict]):
    """Print a text heatmap of σ_poly by day-of-week × hour."""
    print(f"\n{'='*72}")
    print("  σ_poly Weekly Heatmap (Day × Hour, HKT)")
    print(f"{'='*72}")

    # Header
    hours_header = "       " + "".join(f"{h:>6}" for h in range(24))
    print(hours_header)
    print("       " + "-" * 144)

    for dow in range(7):
        row = f"{DOW_NAMES[dow]:>5} |"
        for hour in range(24):
            label = f"{DOW_NAMES[dow]}-{hour:02d}"
            entry = dow_hour.get(label, {})
            mean = entry.get("mean")
            if mean is None or entry.get("n_markets", 0) == 0:
                row += "     ."
            else:
                row += f"{mean:>6.3f}"
        print(row)

    # Legend
    all_means = [
        v["mean"] for v in dow_hour.values()
        if v.get("mean") is not None and v.get("n_markets", 0) > 0
    ]
    if all_means:
        print(f"\n  Range: {min(all_means):.4f} — {max(all_means):.4f}")
        print(f"  (Higher = more mid-price movement = better fill probability)")


def print_summary_stats(market_sigmas: list[dict]):
    """Print overall summary statistics."""
    all_sigmas = np.array([m["sigma"] for m in market_sigmas])
    btc_sigmas = np.array([m["sigma"] for m in market_sigmas if m["coin"] == "BTC"])
    eth_sigmas = np.array([m["sigma"] for m in market_sigmas if m["coin"] == "ETH"])

    print(f"\n{'='*72}")
    print("  Summary Statistics")
    print(f"{'='*72}")
    print(f"  Total markets analyzed: {len(all_sigmas)}")
    print(f"  Date range: {market_sigmas[0]['first_ts'][:10]} to {market_sigmas[-1]['last_ts'][:10]}")

    for label, arr in [("ALL", all_sigmas), ("BTC", btc_sigmas), ("ETH", eth_sigmas)]:
        if len(arr) == 0:
            continue
        print(f"\n  {label} (N={len(arr)}):")
        print(f"    Mean σ_poly:   {np.mean(arr):.4f}")
        print(f"    Median σ_poly: {np.median(arr):.4f}")
        print(f"    Std of σ_poly: {np.std(arr):.4f}")
        print(f"    Min:           {np.min(arr):.4f}")
        print(f"    Max:           {np.max(arr):.4f}")
        print(f"    P10:           {np.percentile(arr, 10):.4f}")
        print(f"    P90:           {np.percentile(arr, 90):.4f}")


def save_results(
    hourly: dict,
    dow_hour: dict,
    coin_hourly: dict,
    market_sigmas: list[dict],
    output_path: Path,
):
    """Save all results to JSON (atomic write)."""
    import tempfile

    results = {
        "metadata": {
            "generated_at": datetime.now(HKT).isoformat(),
            "source": str(SIGNAL_TAPE_PATH),
            "n_markets": len(market_sigmas),
            "min_ticks": MIN_TICKS_PER_MARKET,
        },
        "hourly": {str(k): v for k, v in hourly.items()},
        "dow_hour": dow_hour,
        "coin_hourly": {
            coin: {str(h): v for h, v in hours.items()}
            for coin, hours in coin_hourly.items()
        },
        "per_market": market_sigmas,
    }

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=output_path.parent, suffix=".tmp", prefix=output_path.stem
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(results, f, indent=2, default=str)
        os.replace(tmp_path, output_path)
        logger.info("Results saved to %s", output_path)
    except Exception:
        os.unlink(tmp_path)
        raise


def main():
    if not SIGNAL_TAPE_PATH.exists():
        logger.error("Signal tape not found: %s", SIGNAL_TAPE_PATH)
        sys.exit(1)

    # 1. Load data
    records = load_signal_tape(SIGNAL_TAPE_PATH)

    # 2. Extract per-market time series
    market_data = extract_market_series(records)

    # 3. Compute σ_poly per market
    market_sigmas = compute_sigma_per_market(market_data)

    if not market_sigmas:
        logger.error("No valid markets found after filtering")
        sys.exit(1)

    # 4. Aggregate
    hourly = aggregate_by_hour(market_sigmas)
    dow_hour = aggregate_by_dow_hour(market_sigmas)
    coin_hourly = aggregate_by_coin_hour(market_sigmas)

    # 5. Print results
    print_summary_stats(market_sigmas)
    print_hourly_table(hourly, title="ALL COINS")

    for coin in sorted(coin_hourly.keys()):
        print_hourly_table(coin_hourly[coin], title=coin)

    print_top_bottom(hourly)
    print_dow_hour_heatmap(dow_hour)

    # 6. Save JSON
    save_results(hourly, dow_hour, coin_hourly, market_sigmas, OUTPUT_JSON_PATH)

    print(f"\n  JSON saved: {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
