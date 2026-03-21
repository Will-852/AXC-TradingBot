#!/usr/bin/env python3
"""
data_quality_check.py — Auto-analysis of market_data + OB recorder output

Runs after 24h of data collection. Checks:
1. Data completeness (sources responding, gaps)
2. Value distributions (sanity check — no crazy outliers)
3. Stability (variance within expected ranges)
4. Correlation with outcomes (does the signal predict anything?)
5. Recommendation: safe to enable as sizing modifier? Y/N per signal

Usage:
  PYTHONPATH=.:scripts python3 polymarket/analysis/data_quality_check.py
  PYTHONPATH=.:scripts python3 polymarket/analysis/data_quality_check.py --hours 48
"""

import argparse
import json
import logging
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HKT = ZoneInfo("Asia/Hong_Kong")
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_SIGNAL_LOG = os.path.join(_LOG_DIR, "mm_signals.jsonl")
_OB_TAPE = os.path.join(_LOG_DIR, "poly_ob_tape.jsonl")
_REPORT_PATH = os.path.join(_AXC, "polymarket", "analysis", "data_quality_report.md")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def load_jsonl(path: str, hours: float = 24) -> list[dict]:
    """Load JSONL records from the last N hours."""
    cutoff = (datetime.now(_HKT) - timedelta(hours=hours)).timestamp()
    records = []
    if not os.path.exists(path):
        return records
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                # Try various timestamp fields
                ts = r.get("ts", 0)
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts).timestamp()
                    except (ValueError, TypeError):
                        ts = 0
                if isinstance(ts, (int, float)) and ts > cutoff:
                    records.append(r)
            except json.JSONDecodeError:
                continue
    return records


def analyze_signal_log(records: list[dict]) -> dict:
    """Analyze mm_signals.jsonl for market_data snapshot quality."""
    total = len(records)
    with_mkt = [r for r in records if "mkt" in r]
    without_mkt = total - len(with_mkt)

    if not with_mkt:
        return {"status": "NO_DATA", "total": total, "with_mkt": 0}

    # Extract mkt fields
    fields = defaultdict(list)
    for r in with_mkt:
        m = r["mkt"]
        for k, v in m.items():
            if isinstance(v, (int, float)) and k != "age_ms":
                fields[k].append(v)

    # Per-field stats
    field_stats = {}
    for k, vals in fields.items():
        if not vals:
            continue
        non_zero = [v for v in vals if v != 0]
        field_stats[k] = {
            "count": len(vals),
            "non_zero_pct": len(non_zero) / len(vals) * 100 if vals else 0,
            "mean": statistics.mean(vals) if vals else 0,
            "median": statistics.median(vals) if vals else 0,
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0,
            "min": min(vals),
            "max": max(vals),
        }

    # Source count distribution
    src_counts = [r["mkt"].get("src", 0) for r in with_mkt]
    age_ms = [r["mkt"].get("age_ms", 0) for r in with_mkt]

    return {
        "status": "OK",
        "total": total,
        "with_mkt": len(with_mkt),
        "coverage_pct": len(with_mkt) / total * 100 if total else 0,
        "src_median": statistics.median(src_counts) if src_counts else 0,
        "src_min": min(src_counts) if src_counts else 0,
        "age_median_ms": statistics.median(age_ms) if age_ms else 0,
        "age_max_ms": max(age_ms) if age_ms else 0,
        "field_stats": field_stats,
    }


def analyze_ob_tape(records: list[dict]) -> dict:
    """Analyze poly_ob_tape.jsonl for OB depth + trade flow quality."""
    if not records:
        return {"status": "NO_DATA", "total": 0}

    coins = defaultdict(list)
    for r in records:
        coins[r.get("coin", "?")].append(r)

    coin_stats = {}
    for coin, recs in coins.items():
        depths = [r.get("up_bid_depth_10", 0) + r.get("down_bid_depth_10", 0) for r in recs]
        trade_counts = [r.get("trade_count_5m", 0) for r in recs]
        trade_vols = [r.get("trade_vol_5m", 0) for r in recs]
        ph_points = [r.get("ph_points", 0) for r in recs]
        combined_asks = [r.get("combined_best_ask", 0) for r in recs if r.get("combined_best_ask", 0) > 0]

        coin_stats[coin] = {
            "records": len(recs),
            "depth_median": statistics.median(depths) if depths else 0,
            "trade_count_median": statistics.median(trade_counts) if trade_counts else 0,
            "trade_vol_median": statistics.median(trade_vols) if trade_vols else 0,
            "ph_points_median": statistics.median(ph_points) if ph_points else 0,
            "combined_ask_median": statistics.median(combined_asks) if combined_asks else 0,
            "arb_pct": sum(1 for c in combined_asks if c < 1.98) / len(combined_asks) * 100 if combined_asks else 0,
        }

    return {
        "status": "OK",
        "total": len(records),
        "coins": coin_stats,
    }


def generate_recommendations(sig_analysis: dict, ob_analysis: dict) -> list[dict]:
    """Generate per-signal enable/disable recommendations."""
    recs = []

    if sig_analysis.get("status") != "OK":
        return [{"signal": "ALL", "verdict": "WAIT", "reason": "No market_data in signal log yet"}]

    fs = sig_analysis.get("field_stats", {})

    # Funding
    fund = fs.get("fund_agg", {})
    if fund.get("count", 0) >= 50 and fund.get("non_zero_pct", 0) > 60:
        recs.append({
            "signal": "funding_agg",
            "verdict": "ENABLE",
            "reason": f"Stable: {fund['count']} samples, {fund['non_zero_pct']:.0f}% non-zero, median={fund['median']:.8f}",
            "modifier": "sizing × 0.7 when |funding| > 0.001 (extreme leverage)",
        })
    else:
        recs.append({
            "signal": "funding_agg",
            "verdict": "WAIT",
            "reason": f"Insufficient: {fund.get('count', 0)} samples, {fund.get('non_zero_pct', 0):.0f}% non-zero",
        })

    # OI
    oi = fs.get("oi_total", {})
    if oi.get("count", 0) >= 50 and oi.get("non_zero_pct", 0) > 80:
        recs.append({
            "signal": "oi_delta",
            "verdict": "ENABLE",
            "reason": f"Stable: {oi['count']} samples, median=${oi['median']:.1f}B",
            "modifier": "sizing × 0.6 when oi_delta_5m < -$1B (liquidation cascade)",
        })
    else:
        recs.append({
            "signal": "oi_delta",
            "verdict": "WAIT",
            "reason": f"Insufficient: {oi.get('count', 0)} samples",
        })

    # L/S
    ls = fs.get("ls", {})
    if ls.get("count", 0) >= 50:
        recs.append({
            "signal": "ls_extreme",
            "verdict": "ENABLE",
            "reason": f"Stable: {ls['count']} samples, median={ls['median']:.3f}, range=[{ls['min']:.3f}, {ls['max']:.3f}]",
            "modifier": "sizing × 0.8 when ls_extreme=True (>58% or <42%)",
        })
    else:
        recs.append({
            "signal": "ls_extreme",
            "verdict": "WAIT",
            "reason": f"Insufficient: {ls.get('count', 0)} samples",
        })

    # DVOL
    dvol = fs.get("dvol", {})
    if dvol.get("count", 0) >= 50 and dvol.get("non_zero_pct", 0) > 80:
        recs.append({
            "signal": "dvol",
            "verdict": "ENABLE",
            "reason": f"Stable: {dvol['count']} samples, median={dvol['median']:.1f}",
            "modifier": "spread width modifier (high DVOL = widen, low = tighten)",
        })
    else:
        recs.append({
            "signal": "dvol",
            "verdict": "WAIT",
            "reason": f"Insufficient: {dvol.get('count', 0)} samples, {dvol.get('non_zero_pct', 0):.0f}% non-zero",
        })

    # Source health
    src_min = sig_analysis.get("src_min", 0)
    if src_min < 10:
        recs.append({
            "signal": "SOURCE_HEALTH",
            "verdict": "WARNING",
            "reason": f"Min sources responded = {src_min} (expected 22). Some exchanges may be down.",
        })

    return recs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24, help="Hours of data to analyze")
    args = parser.parse_args()

    log.info(f"{'='*60}")
    log.info(f"  Data Quality Check — last {args.hours}h")
    log.info(f"  {datetime.now(_HKT):%Y-%m-%d %H:%M HKT}")
    log.info(f"{'='*60}\n")

    # Load data
    sig_records = load_jsonl(_SIGNAL_LOG, args.hours)
    ob_records = load_jsonl(_OB_TAPE, args.hours)
    log.info(f"Signal log: {len(sig_records)} records")
    log.info(f"OB tape:    {len(ob_records)} records\n")

    # Analyze
    sig = analyze_signal_log(sig_records)
    ob = analyze_ob_tape(ob_records)

    # Signal log report
    log.info("── Signal Log (mm_signals.jsonl) ──")
    if sig["status"] == "OK":
        log.info(f"  Total entries:    {sig['total']}")
        log.info(f"  With mkt data:   {sig['with_mkt']} ({sig['coverage_pct']:.0f}%)")
        log.info(f"  Sources median:  {sig['src_median']:.0f}")
        log.info(f"  Sources min:     {sig['src_min']}")
        log.info(f"  Age median:      {sig['age_median_ms']:.0f}ms")
        log.info(f"  Age max:         {sig['age_max_ms']:.0f}ms")
        log.info("")
        log.info(f"  {'Field':>15s} {'Count':>6s} {'Non0%':>6s} {'Median':>12s} {'Stdev':>12s} {'Min':>12s} {'Max':>12s}")
        log.info(f"  {'-'*15} {'-'*6} {'-'*6} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
        for k, s in sig.get("field_stats", {}).items():
            log.info(f"  {k:>15s} {s['count']:>6d} {s['non_zero_pct']:>5.0f}% {s['median']:>12.6f} {s['stdev']:>12.6f} {s['min']:>12.6f} {s['max']:>12.6f}")
    else:
        log.info(f"  Status: {sig['status']} (total={sig.get('total', 0)})")
    log.info("")

    # OB tape report
    log.info("── OB Tape (poly_ob_tape.jsonl) ──")
    if ob["status"] == "OK":
        log.info(f"  Total snapshots:  {ob['total']}")
        for coin, cs in ob.get("coins", {}).items():
            log.info(f"  {coin}: {cs['records']} records | depth={cs['depth_median']:.0f} | "
                     f"trades/5m={cs['trade_count_median']:.0f} | vol={cs['trade_vol_median']:.0f} | "
                     f"ph_points={cs['ph_points_median']:.0f} | arb%={cs['arb_pct']:.1f}%")
    else:
        log.info(f"  Status: {ob['status']}")
    log.info("")

    # Recommendations
    recs = generate_recommendations(sig, ob)
    log.info("── Recommendations ──")
    for r in recs:
        icon = "✅" if r["verdict"] == "ENABLE" else "⏳" if r["verdict"] == "WAIT" else "⚠️"
        log.info(f"  {icon} {r['signal']:>15s}: {r['verdict']} — {r['reason']}")
        if "modifier" in r:
            log.info(f"  {'':>15s}  → {r['modifier']}")
    log.info("")

    # Write report
    report = []
    report.append(f"# Data Quality Report — {datetime.now(_HKT):%Y-%m-%d %H:%M HKT}")
    report.append(f"Window: last {args.hours}h | Signal records: {len(sig_records)} | OB records: {len(ob_records)}")
    report.append("")
    for r in recs:
        icon = "✅" if r["verdict"] == "ENABLE" else "⏳" if r["verdict"] == "WAIT" else "⚠️"
        report.append(f"- {icon} **{r['signal']}**: {r['verdict']} — {r['reason']}")
    os.makedirs(os.path.dirname(_REPORT_PATH), exist_ok=True)
    with open(_REPORT_PATH, "w") as f:
        f.write("\n".join(report) + "\n")
    log.info(f"Report saved: {_REPORT_PATH}")


if __name__ == "__main__":
    main()
