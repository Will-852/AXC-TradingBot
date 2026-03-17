#!/usr/bin/env python3
"""
weather_paper_track.py — Standalone CLI for weather prediction paper tracking.

唔改 pipeline — 獨立運行。
Usage:
  PYTHONPATH=.:scripts python3 polymarket/run_weather_paper.py --predict
  PYTHONPATH=.:scripts python3 polymarket/run_weather_paper.py --resolve
  PYTHONPATH=.:scripts python3 polymarket/run_weather_paper.py --report
  PYTHONPATH=.:scripts python3 polymarket/run_weather_paper.py --predict --dry-run
"""

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ─── Path Setup ───
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if _AXC not in sys.path:
    sys.path.insert(0, _AXC)                          # for polymarket.*
_SCRIPTS = os.path.join(_AXC, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)                       # for shared_infra.*

from polymarket.config.categories import WEATHER_CITIES
from polymarket.config.settings import LOG_DIR
from polymarket.exchange.gamma_client import GammaClient
from polymarket.strategy.edge_finder import _parse_weather_market
from polymarket.strategy.weather_tracker import (
    ENSEMBLE_MODELS,
    RESOLUTION_SOURCES,
    _RESOLUTION_LOG,
    _TRACKER_LOG,
    compute_brier_score,
    compute_bucket_probabilities,
    fetch_ensemble_forecast,
    fetch_resolution,
    log_weather_prediction,
)

_HKT = ZoneInfo("Asia/Hong_Kong")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weather_paper_track")

def _run_predict(dry_run: bool = False) -> None:
    """Scan weather markets → ensemble forecast → log predictions."""
    gamma = GammaClient()
    log.info("Fetching weather markets from Gamma API...")
    # No tag filter — pipeline-style fetch, parse titles locally
    markets = gamma.get_markets(limit=500, active=True, order="liquidity", ascending=False)
    log.info("Fetched %d markets, parsing for weather...", len(markets))

    # Parse and group by (city, date)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for mkt in markets:
        title = mkt.get("question", "") or ""
        parsed = _parse_weather_market(title)
        if not parsed:
            continue

        key = (parsed["city"], parsed["date"])
        # Extract YES price from outcomePrices
        try:
            prices = json.loads(mkt.get("outcomePrices", "[]"))
            yes_price = float(prices[0]) if prices else None
        except (json.JSONDecodeError, IndexError, TypeError):
            yes_price = None

        grouped.setdefault(key, []).append({
            **parsed,
            "title": title,
            "yes_price": yes_price,
            "condition_id": mkt.get("conditionId", ""),
        })

    if not grouped:
        log.warning("No weather markets found in %d markets", len(markets))
        return

    log.info("Found %d weather city-date pairs", len(grouped))
    today = date.today()

    for (city, target_date), bucket_markets in sorted(grouped.items()):
        try:
            td = date.fromisoformat(target_date)
        except ValueError:
            continue
        lead_days = (td - today).days
        if lead_days < 0:
            continue  # Past date, skip for predictions

        lat, lon, unit = WEATHER_CITIES[city]
        res = RESOLUTION_SOURCES.get(city, {
            "type": "archive", "station": "open-meteo",
            "precision": "whole" if unit == "F" else "whole",
        })

        # Fetch ensemble forecast (°F for US cities)
        log.info("Fetching ensemble for %s %s (lead=%dd)...", city, target_date, lead_days)
        ensemble_data = fetch_ensemble_forecast(lat, lon, target_date, fahrenheit=(unit == "F"))
        if not ensemble_data:
            log.warning("No ensemble data for %s %s", city, target_date)
            continue

        # Flatten all members across models
        all_members = []
        models_used = []
        for model, members in ensemble_data.items():
            all_members.extend(members)
            models_used.append(model)

        if not all_members:
            continue

        # Build bucket boundaries from parsed markets
        boundaries = []
        market_prices = {}
        for bm in sorted(bucket_markets, key=lambda x: x.get("threshold_low") or -999):
            bt = bm["bucket_type"]
            low = bm["threshold_low"]
            high = bm["threshold_high"]

            if bt == "floor":
                label = str(int(high)) + "_or_below"
                boundaries.append((label, None, high))
            elif bt == "ceiling":
                label = str(int(low)) + "_or_above"
                boundaries.append((label, low, None))
            elif bt == "exact":
                # For °C: exact val with ±0.5 parsed → reconstruct [val, val+1)
                mid = (low + high) / 2
                label = str(int(mid))
                boundaries.append((label, mid - 0.5, mid + 0.5))
            elif bt == "range":
                label = f"{int(low + 0.5)}-{int(high - 0.5)}"
                boundaries.append((label, low, high))

            if bm["yes_price"] is not None:
                market_prices[boundaries[-1][0]] = bm["yes_price"]

        if not boundaries:
            continue

        # Compute ensemble bucket probabilities
        probs = compute_bucket_probabilities(all_members, boundaries)

        # Build buckets dict with edge
        buckets = {}
        best_edge_bucket = ""
        best_edge_pct = -999.0
        for label in probs:
            ep = probs[label]
            mp = market_prices.get(label, 0.0)
            edge = round(ep - mp, 4)
            buckets[label] = {
                "ensemble_prob": ep,
                "market_price": round(mp, 4),
                "edge": edge,
            }
            if edge > best_edge_pct:
                best_edge_pct = edge
                best_edge_bucket = label

        ens_mean = round(statistics.mean(all_members), 2)
        ens_std = round(statistics.stdev(all_members), 2) if len(all_members) > 1 else 0.0

        # Print summary
        print(f"\n{'='*60}")
        print(f"  {city.upper()} | {target_date} | Lead: {lead_days}d")
        print(f"  Ensemble: {len(all_members)} members from {models_used}")
        print(f"  Mean: {ens_mean}°C  Std: {ens_std}°C  "
              f"Range: [{min(all_members):.1f}, {max(all_members):.1f}]")
        print(f"  {'Bucket':<15} {'Ensemble':>10} {'Market':>10} {'Edge':>10}")
        print(f"  {'-'*45}")
        for label, bd in sorted(buckets.items(), key=lambda x: -x[1]["edge"]):
            flag = " ***" if bd["edge"] > 0.05 else ""
            print(f"  {label:<15} {bd['ensemble_prob']:>10.1%} "
                  f"{bd['market_price']:>10.1%} {bd['edge']:>+10.1%}{flag}")
        print(f"  Best edge: {best_edge_bucket} ({best_edge_pct:+.1%})")

        if not dry_run:
            log_weather_prediction(
                city=city,
                target_date=target_date,
                resolution_source=res["type"],
                resolution_station=res["station"],
                precision=res["precision"],
                lead_days=lead_days,
                ensemble_count=len(all_members),
                models_used=models_used,
                ensemble_mean=ens_mean,
                ensemble_std=ens_std,
                ensemble_min=round(min(all_members), 2),
                ensemble_max=round(max(all_members), 2),
                buckets=buckets,
                best_edge_bucket=best_edge_bucket,
                best_edge_pct=best_edge_pct,
            )

    if dry_run:
        print("\n[DRY RUN] No predictions logged.")
    else:
        print(f"\nPredictions logged to: {_TRACKER_LOG}")


def _run_resolve() -> None:
    """Find unresolved predictions → fetch actual temps → write resolutions."""
    # Load existing resolutions
    resolved_keys = set()
    if os.path.exists(_RESOLUTION_LOG):
        with open(_RESOLUTION_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                resolved_keys.add((rec["city"], rec["target_date"]))

    # Load predictions, find unresolved past dates
    if not os.path.exists(_TRACKER_LOG):
        log.warning("No predictions file found: %s", _TRACKER_LOG)
        return

    today = date.today()
    to_resolve = []
    with open(_TRACKER_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = (rec["city"], rec["target_date"])
            if key in resolved_keys:
                continue
            try:
                td = date.fromisoformat(rec["target_date"])
            except ValueError:
                continue
            if td < today:  # Only resolve past dates
                to_resolve.append(rec)

    if not to_resolve:
        log.info("No unresolved past predictions found.")
        return

    log.info("Resolving %d predictions...", len(to_resolve))
    now = datetime.now(tz=_HKT)
    new_resolutions = 0

    for rec in to_resolve:
        city = rec["city"]
        target_date = rec["target_date"]
        actual = fetch_resolution(city, target_date)
        if actual is None:
            log.warning("Could not resolve %s %s", city, target_date)
            continue

        resolution = {
            "ts": now.isoformat(),
            "city": city,
            "target_date": target_date,
            "actual_max": round(actual, 1),
            "source": rec.get("resolution_source", "archive"),
            "ensemble_mean": rec["ensemble_mean"],
            "bias": round(rec["ensemble_mean"] - actual, 2),
        }

        os.makedirs(os.path.dirname(_RESOLUTION_LOG), exist_ok=True)
        with open(_RESOLUTION_LOG, "a") as f:
            f.write(json.dumps(resolution, ensure_ascii=False) + "\n")

        new_resolutions += 1
        print(f"  Resolved: {city} {target_date} → actual={actual:.1f}°C "
              f"(model={rec['ensemble_mean']:.1f}°C, bias={rec['ensemble_mean'] - actual:+.1f}°C)")

    print(f"\n{new_resolutions} resolutions written to: {_RESOLUTION_LOG}")


def _run_report() -> None:
    """Compute and print Brier score + bias report."""
    result = compute_brier_score()

    if "error" in result:
        print(f"Error: {result['error']}")
        if "predictions_count" in result:
            print(f"  Predictions: {result['predictions_count']}")
        return

    if result["matched"] == 0:
        print("No matched prediction-resolution pairs yet.")
        print(f"  Predictions: {result['predictions_count']}")
        print(f"  Resolutions: {result['resolutions_count']}")
        return

    print(f"\n{'='*60}")
    print(f"  WEATHER PAPER TRACKER — CALIBRATION REPORT")
    print(f"{'='*60}")
    print(f"  Matched pairs: {result['matched']}")
    print(f"  Overall Brier: {result['brier_overall']:.4f}")
    print(f"  Overall Bias:  {result['bias_overall']:+.2f}°C (σ={result['bias_std']:.2f}°C)")

    print(f"\n  {'City':<15} {'Brier':>8} {'Bias':>8} {'N':>5}")
    print(f"  {'-'*36}")
    for city, data in result["by_city"].items():
        print(f"  {city:<15} {data['brier']:>8.4f} {data['bias']:>+8.2f} {data['n']:>5}")

    print(f"\n  {'Lead (days)':<15} {'Brier':>8} {'N':>5}")
    print(f"  {'-'*28}")
    for lead, data in result["by_lead_days"].items():
        print(f"  {lead:<15} {data['brier']:>8.4f} {data['n']:>5}")

    print(f"\n  Interpretation:")
    brier = result["brier_overall"]
    if brier < 0.1:
        print(f"  Brier {brier:.4f} = EXCELLENT calibration")
    elif brier < 0.2:
        print(f"  Brier {brier:.4f} = Good, edge likely real")
    elif brier < 0.3:
        print(f"  Brier {brier:.4f} = Fair, needs more data")
    else:
        print(f"  Brier {brier:.4f} = Poor, model needs calibration")

    bias = result["bias_overall"]
    if abs(bias) > 1.0:
        print(f"  Bias {bias:+.2f}°C = SIGNIFICANT systematic error — apply correction!")
    elif abs(bias) > 0.5:
        print(f"  Bias {bias:+.2f}°C = Moderate — monitor and consider correction")
    else:
        print(f"  Bias {bias:+.2f}°C = Acceptable")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weather prediction paper tracker for Polymarket",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--predict", action="store_true", help="Fetch forecast + log prediction")
    group.add_argument("--resolve", action="store_true", help="Fetch actuals, match predictions")
    group.add_argument("--report", action="store_true", help="Brier score + bias report")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no log write")

    args = parser.parse_args()

    if args.predict:
        _run_predict(dry_run=args.dry_run)
    elif args.resolve:
        _run_resolve()
    elif args.report:
        _run_report()


if __name__ == "__main__":
    main()
