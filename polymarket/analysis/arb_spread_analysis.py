#!/usr/bin/env python3
"""
Polymarket 15M BTC Arb Spread Analysis
=======================================
Quantify how often up_mid + dn_mid < 1.0 (arb opportunity) in BTC 15M markets.

Key insight from wallet reverse engineering: 64% of profitable wallets use
arbitrage (buying both UP + DOWN when combined < $1.00).

Limitations:
  - up_mid + dn_mid is mid-to-mid price. Actual taker cost to arb would be
    best_ask(UP) + best_ask(DOWN) which is wider than mid+mid. The real arb
    threshold is therefore HIGHER than what mid-mid shows. This analysis
    overstates arb frequency; true executable arb is a subset.
  - Fees: ~0 at extreme prices on Polymarket (fee = max(0, p*(1-p)*0.0222)),
    so near 0/1 the fee is negligible. Near 0.5 the fee peaks at ~0.56%.
    We model this explicitly.
  - Data is ~20s snapshots; real arb windows may be shorter (sub-second bots
    compete for these).

Usage:
    python3 arb_spread_analysis.py
"""

import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SIGNAL_TAPE = Path(__file__).resolve().parent.parent / "logs" / "signal_tape.jsonl"
TICK_INTERVAL_S = 20  # approximate seconds between snapshots
HKT = timezone(timedelta(hours=8))

# Polymarket fee formula: fee_rate = max(0, p * (1 - p) * 0.0222)
# Applied to each leg; total fee for arb = fee(up_ask) + fee(dn_ask)
# For simplicity we use mid prices as proxy for ask (slight underestimate of cost)
FEE_COEFFICIENT = 0.0222

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_btc_records(path: Path) -> list[dict]:
    """
    Load signal_tape.jsonl → flat list of per-market-per-tick records.
    Filter to BTC 15M markets only (title contains 'Bitcoin').
    """
    records = []
    with open(path) as f:
        for line_no, raw in enumerate(f, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Skipping malformed line %d", line_no)
                continue

            ts = datetime.fromisoformat(row["ts"])
            for mkt in row.get("poly", []):
                if mkt.get("coin") != "BTC":
                    continue
                title = mkt.get("title", "")
                if "Bitcoin" not in title:
                    continue

                up_mid = mkt.get("up_mid")
                dn_mid = mkt.get("dn_mid")
                if up_mid is None or dn_mid is None:
                    continue

                records.append({
                    "ts": ts,
                    "cid": mkt["cid"],
                    "title": title,
                    "up_mid": float(up_mid),
                    "dn_mid": float(dn_mid),
                    "combined": float(up_mid) + float(dn_mid),
                    "ob_imbalance": mkt.get("ob_imbalance", 0.0),
                    "ob_bid_vol": mkt.get("ob_bid_vol", 0.0),
                    "ob_ask_vol": mkt.get("ob_ask_vol", 0.0),
                    "hour_hkt": ts.astimezone(HKT).hour,
                })
    return records


def polymarket_fee(p: float) -> float:
    """Fee rate for a single leg at price p."""
    return max(0.0, p * (1.0 - p) * FEE_COEFFICIENT)


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------
def section(title: str) -> None:
    log.info("\n" + "=" * 70)
    log.info(title)
    log.info("=" * 70)


def analyze_distribution(records: list[dict]) -> None:
    """3a: Distribution of combined mid."""
    section("1. COMBINED MID DISTRIBUTION (up_mid + dn_mid)")

    combined = np.array([r["combined"] for r in records])
    log.info(f"  Total BTC 15M snapshots : {len(combined):,}")
    log.info(f"  Mean                    : {np.mean(combined):.4f}")
    log.info(f"  Median                  : {np.median(combined):.4f}")
    log.info(f"  Std Dev                 : {np.std(combined):.4f}")
    log.info(f"  P5                      : {np.percentile(combined, 5):.4f}")
    log.info(f"  P25                     : {np.percentile(combined, 25):.4f}")
    log.info(f"  P75                     : {np.percentile(combined, 75):.4f}")
    log.info(f"  P95                     : {np.percentile(combined, 95):.4f}")
    log.info(f"  Min                     : {np.min(combined):.4f}")
    log.info(f"  Max                     : {np.max(combined):.4f}")

    # Histogram buckets
    log.info("\n  Distribution buckets:")
    buckets = [
        (0.0, 0.85, "< 0.85  (deep arb)"),
        (0.85, 0.90, "0.85-0.90"),
        (0.90, 0.95, "0.90-0.95"),
        (0.95, 0.98, "0.95-0.98"),
        (0.98, 1.00, "0.98-1.00 (fair)"),
        (1.00, 1.02, "1.00-1.02"),
        (1.02, 1.05, "1.02-1.05"),
        (1.05, 1.10, "1.05-1.10"),
        (1.10, 2.00, "> 1.10  (overpriced)"),
    ]
    for lo, hi, label in buckets:
        count = np.sum((combined >= lo) & (combined < hi))
        pct = count / len(combined) * 100
        bar = "#" * int(pct / 2)
        log.info(f"    {label:25s} : {count:6,} ({pct:5.1f}%) {bar}")


def analyze_frequency(records: list[dict]) -> None:
    """3b: Frequency of arb opportunities at various thresholds."""
    section("2. ARB FREQUENCY (combined < threshold)")

    combined = np.array([r["combined"] for r in records])
    n = len(combined)
    thresholds = [1.00, 0.99, 0.98, 0.97, 0.96, 0.95, 0.93, 0.90, 0.85]
    log.info(f"  {'Threshold':>10s}  {'Count':>8s}  {'%':>7s}  {'Avg Discount':>13s}")
    log.info(f"  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*13}")
    for t in thresholds:
        mask = combined < t
        count = int(np.sum(mask))
        pct = count / n * 100
        if count > 0:
            avg_disc = float(np.mean(t - combined[mask]))
            log.info(f"  < {t:.2f}       {count:8,}  {pct:6.1f}%  {avg_disc:12.4f}")
        else:
            log.info(f"  < {t:.2f}       {count:8,}  {pct:6.1f}%  {'N/A':>12s}")


def analyze_by_hour(records: list[dict]) -> None:
    """3c: Arb frequency by hour of day (HKT)."""
    section("3. ARB BY HOUR OF DAY (HKT) — combined < 0.98")

    hour_total = defaultdict(int)
    hour_arb = defaultdict(int)
    hour_discount = defaultdict(list)

    for r in records:
        h = r["hour_hkt"]
        hour_total[h] += 1
        if r["combined"] < 0.98:
            hour_arb[h] += 1
            hour_discount[h].append(1.0 - r["combined"])

    log.info(f"  {'Hour':>4s}  {'Total':>7s}  {'Arb<0.98':>8s}  {'%':>7s}  {'Avg Disc':>9s}  {'Bar'}")
    log.info(f"  {'-'*4}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*9}  -----")
    for h in range(24):
        total = hour_total.get(h, 0)
        arb = hour_arb.get(h, 0)
        if total == 0:
            log.info(f"  {h:4d}  {0:7d}  {0:8d}  {'N/A':>7s}  {'N/A':>9s}")
            continue
        pct = arb / total * 100
        avg_d = np.mean(hour_discount[h]) if hour_discount[h] else 0
        bar = "#" * int(pct / 2)
        log.info(f"  {h:4d}  {total:7,}  {arb:8,}  {pct:6.1f}%  {avg_d:8.4f}  {bar}")


def analyze_duration(records: list[dict]) -> None:
    """3d: Duration of arb windows (consecutive ticks below threshold)."""
    section("4. ARB WINDOW DURATION (consecutive ticks with combined < 0.98)")

    # Group by market (cid), sort by time, find consecutive runs
    by_market = defaultdict(list)
    for r in records:
        by_market[r["cid"]].append(r)

    all_run_lengths = []
    all_run_discounts = []  # average discount per run

    for cid, mkt_records in by_market.items():
        mkt_records.sort(key=lambda x: x["ts"])
        run_len = 0
        run_discounts = []
        for r in mkt_records:
            if r["combined"] < 0.98:
                run_len += 1
                run_discounts.append(1.0 - r["combined"])
            else:
                if run_len > 0:
                    all_run_lengths.append(run_len)
                    all_run_discounts.append(np.mean(run_discounts))
                run_len = 0
                run_discounts = []
        # Handle run at end
        if run_len > 0:
            all_run_lengths.append(run_len)
            all_run_discounts.append(np.mean(run_discounts))

    if not all_run_lengths:
        log.info("  No arb windows found.")
        return

    runs = np.array(all_run_lengths)
    discs = np.array(all_run_discounts)

    log.info(f"  Total arb windows       : {len(runs):,}")
    log.info(f"  Mean duration (ticks)   : {np.mean(runs):.1f} (~{np.mean(runs)*TICK_INTERVAL_S:.0f}s)")
    log.info(f"  Median duration (ticks) : {np.median(runs):.0f} (~{np.median(runs)*TICK_INTERVAL_S:.0f}s)")
    log.info(f"  Max duration (ticks)    : {np.max(runs):,} (~{np.max(runs)*TICK_INTERVAL_S/60:.1f} min)")
    log.info(f"  P90 duration (ticks)    : {np.percentile(runs, 90):.0f} (~{np.percentile(runs, 90)*TICK_INTERVAL_S:.0f}s)")
    log.info(f"  Mean discount in window : {np.mean(discs):.4f} ({np.mean(discs)*100:.2f}%)")

    # Distribution of run lengths
    log.info("\n  Run length distribution:")
    length_buckets = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 20), (21, 50), (51, 1000)]
    for lo, hi in length_buckets:
        count = int(np.sum((runs >= lo) & (runs <= hi)))
        pct = count / len(runs) * 100
        if lo == hi:
            label = f"{lo} tick"
        else:
            label = f"{lo}-{hi} ticks"
        time_lo = lo * TICK_INTERVAL_S
        time_hi = hi * TICK_INTERVAL_S
        log.info(f"    {label:15s} ({time_lo:4d}s-{time_hi:4d}s) : {count:5,} ({pct:5.1f}%)")


def analyze_magnitude(records: list[dict]) -> None:
    """3e: Magnitude of discount when combined < 0.98."""
    section("5. ARB MAGNITUDE (when combined < 0.98)")

    arb_records = [r for r in records if r["combined"] < 0.98]
    if not arb_records:
        log.info("  No arb opportunities found at < 0.98 threshold.")
        return

    discounts = np.array([1.0 - r["combined"] for r in arb_records])
    log.info(f"  Arb snapshots           : {len(discounts):,}")
    log.info(f"  Mean discount           : {np.mean(discounts):.4f} ({np.mean(discounts)*100:.2f}%)")
    log.info(f"  Median discount         : {np.median(discounts):.4f} ({np.median(discounts)*100:.2f}%)")
    log.info(f"  P90 discount            : {np.percentile(discounts, 90):.4f} ({np.percentile(discounts, 90)*100:.2f}%)")
    log.info(f"  Max discount            : {np.max(discounts):.4f} ({np.max(discounts)*100:.2f}%)")
    log.info(f"  Std discount            : {np.std(discounts):.4f}")


def analyze_per_market(records: list[dict]) -> None:
    """3f: Per-market arb patterns — are some markets consistently cheaper?"""
    section("6. PER-MARKET ARB PATTERNS")

    by_market = defaultdict(list)
    for r in records:
        by_market[r["cid"]].append(r)

    market_stats = []
    for cid, mkt_records in by_market.items():
        combined = np.array([r["combined"] for r in mkt_records])
        arb_mask = combined < 0.98
        n_arb = int(np.sum(arb_mask))
        pct_arb = n_arb / len(combined) * 100
        avg_combined = float(np.mean(combined))
        avg_discount = float(np.mean(1.0 - combined[arb_mask])) if n_arb > 0 else 0
        title = mkt_records[0]["title"]
        # Extract time window from title for readability
        # e.g. "Bitcoin Up or Down - March 20, 3:15AM-3:30AM ET"
        short_title = title.replace("Bitcoin Up or Down - ", "")

        market_stats.append({
            "cid": cid[:10],
            "title": short_title,
            "n_ticks": len(combined),
            "n_arb": n_arb,
            "pct_arb": pct_arb,
            "avg_combined": avg_combined,
            "avg_discount": avg_discount,
            "up_mid_std": float(np.std([r["up_mid"] for r in mkt_records])),
        })

    # Sort by arb frequency
    market_stats.sort(key=lambda x: x["pct_arb"], reverse=True)

    log.info(f"  Total unique BTC markets: {len(market_stats)}")
    log.info(f"\n  Top 20 markets by arb frequency (combined < 0.98):")
    log.info(f"  {'Market':40s} {'Ticks':>6s} {'Arb':>5s} {'%Arb':>6s} {'AvgCmb':>7s} {'AvgDisc':>8s} {'σ_up':>6s}")
    log.info(f"  {'-'*40} {'-'*6} {'-'*5} {'-'*6} {'-'*7} {'-'*8} {'-'*6}")
    for s in market_stats[:20]:
        log.info(
            f"  {s['title']:40s} {s['n_ticks']:6d} {s['n_arb']:5d} "
            f"{s['pct_arb']:5.1f}% {s['avg_combined']:7.4f} {s['avg_discount']:8.4f} "
            f"{s['up_mid_std']:6.4f}"
        )

    # Summary by arb bucket
    log.info(f"\n  Market arb frequency distribution:")
    n_high = sum(1 for s in market_stats if s["pct_arb"] > 50)
    n_med = sum(1 for s in market_stats if 20 < s["pct_arb"] <= 50)
    n_low = sum(1 for s in market_stats if 0 < s["pct_arb"] <= 20)
    n_zero = sum(1 for s in market_stats if s["pct_arb"] == 0)
    log.info(f"    >50% arb ticks  : {n_high}")
    log.info(f"    20-50% arb ticks: {n_med}")
    log.info(f"    1-20% arb ticks : {n_low}")
    log.info(f"    0% arb ticks    : {n_zero}")

    return market_stats


def analyze_sigma_correlation(records: list[dict], market_stats: list[dict] | None) -> None:
    """
    4: Cross-reference with σ_poly.
    No explicit σ_poly in data, so we compute price volatility (std of up_mid)
    per market as a proxy and correlate with arb frequency.
    """
    section("7. σ (PRICE VOLATILITY) vs ARB FREQUENCY")

    if not market_stats:
        log.info("  No market stats available.")
        return

    # Only markets with enough ticks
    valid = [s for s in market_stats if s["n_ticks"] >= 5]
    if len(valid) < 5:
        log.info("  Not enough markets with sufficient data.")
        return

    sigmas = np.array([s["up_mid_std"] for s in valid])
    arb_pcts = np.array([s["pct_arb"] for s in valid])

    # Correlation
    if np.std(sigmas) > 0 and np.std(arb_pcts) > 0:
        corr = np.corrcoef(sigmas, arb_pcts)[0, 1]
    else:
        corr = 0.0

    log.info(f"  Markets analyzed        : {len(valid)}")
    log.info(f"  σ_up_mid range          : [{np.min(sigmas):.4f}, {np.max(sigmas):.4f}]")
    log.info(f"  Correlation(σ, %arb)    : {corr:.4f}")

    if corr > 0.3:
        log.info("  → Higher volatility markets DO show more arb opportunities.")
    elif corr < -0.3:
        log.info("  → Surprisingly, lower volatility markets show more arb (possible stale quotes).")
    else:
        log.info("  → Weak/no linear relationship between volatility and arb frequency.")

    # Bucket analysis
    log.info(f"\n  Arb frequency by volatility quartile:")
    quartiles = np.percentile(sigmas, [25, 50, 75])
    labels = ["Q1 (low σ)", "Q2", "Q3", "Q4 (high σ)"]
    bounds = [0] + list(quartiles) + [999]
    for i in range(4):
        mask = (sigmas >= bounds[i]) & (sigmas < bounds[i + 1])
        if np.sum(mask) > 0:
            avg_arb = np.mean(arb_pcts[mask])
            log.info(f"    {labels[i]:15s} : σ=[{bounds[i]:.4f},{bounds[i+1]:.4f})  "
                     f"n={int(np.sum(mask)):3d}  avg_arb={avg_arb:.1f}%")


def analyze_arb_ev(records: list[dict]) -> None:
    """
    5: Theoretical arb EV calculation.
    If we buy UP@best_ask + DOWN@best_ask when combined < threshold,
    guaranteed profit = 1.0 - combined - total_fees.
    """
    section("8. THEORETICAL ARB EV & REVENUE PROJECTION")

    log.info("  NOTE: This uses mid-to-mid prices. Actual taker cost = best_ask + best_ask")
    log.info("        which is wider. Real executable arb is a SUBSET of what's shown here.")
    log.info("        Treat these numbers as an UPPER BOUND.")
    log.info("")

    # Calculate fee for each arb snapshot
    arb_records = [r for r in records if r["combined"] < 1.0]

    if not arb_records:
        log.info("  No combined < 1.0 found.")
        return

    # For each arb record, compute theoretical profit
    ev_data = []
    for r in arb_records:
        up_price = r["up_mid"]
        dn_price = r["dn_mid"]
        cost = up_price + dn_price  # mid-mid cost
        fee_up = polymarket_fee(up_price)
        fee_dn = polymarket_fee(dn_price)
        total_fee = fee_up + fee_dn
        gross_profit = 1.0 - cost
        net_profit = gross_profit - total_fee
        ev_data.append({
            "gross": gross_profit,
            "fee": total_fee,
            "net": net_profit,
            "combined": r["combined"],
            "up_mid": up_price,
            "dn_mid": dn_price,
        })

    gross = np.array([e["gross"] for e in ev_data])
    fees = np.array([e["fee"] for e in ev_data])
    net = np.array([e["net"] for e in ev_data])

    n_profitable = int(np.sum(net > 0))
    n_total = len(ev_data)

    log.info(f"  Snapshots with combined < 1.0 : {n_total:,} / {len(records):,} ({n_total/len(records)*100:.1f}%)")
    log.info(f"  Snapshots with net > 0 (after fee) : {n_profitable:,} ({n_profitable/n_total*100:.1f}% of arb snapshots)")
    log.info(f"")
    log.info(f"  Gross profit (mid-mid):")
    log.info(f"    Mean   : {np.mean(gross):.4f} ({np.mean(gross)*100:.2f}%)")
    log.info(f"    Median : {np.median(gross):.4f}")
    log.info(f"    P90    : {np.percentile(gross, 90):.4f}")
    log.info(f"  Fee per trade pair:")
    log.info(f"    Mean   : {np.mean(fees):.4f} ({np.mean(fees)*100:.2f}%)")
    log.info(f"    Median : {np.median(fees):.4f}")
    log.info(f"  Net profit (mid-mid - fee):")
    log.info(f"    Mean   : {np.mean(net):.4f} ({np.mean(net)*100:.2f}%)")
    if n_profitable > 0:
        profitable_net = net[net > 0]
        log.info(f"    Mean (profitable only) : {np.mean(profitable_net):.4f}")
        log.info(f"    Median (profitable)    : {np.median(profitable_net):.4f}")

    # Revenue projection
    section("9. MONTHLY ARB REVENUE PROJECTION")

    log.info("  Assumptions:")
    log.info(f"    - Data span: ~{len(records):,} BTC snapshots over observed period")

    # Calculate data timespan
    timestamps = sorted(set(r["ts"] for r in records))
    if len(timestamps) >= 2:
        span_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
        span_days = span_hours / 24
    else:
        span_hours = 1
        span_days = 1 / 24

    log.info(f"    - Data covers {span_days:.1f} days ({span_hours:.0f} hours)")
    log.info(f"    - Profitable arb ticks per day: {n_profitable / span_days:.1f}")
    log.info(f"    - Each tick = one arb opportunity (buy UP + DOWN pair)")
    log.info(f"    - Mid-mid net profit (upper bound, before spread slippage)")
    log.info(f"    - Real execution: assume 30-50% capture rate (spread, latency, fill)")
    log.info(f"")

    # Arb opportunities per month (30 days)
    if n_profitable > 0 and span_days > 0:
        profitable_net = net[net > 0]
        opps_per_month = n_profitable / span_days * 30
        avg_net_pct = float(np.mean(profitable_net))

        log.info(f"  Estimated profitable opportunities / month: {opps_per_month:,.0f}")
        log.info(f"  Average net per arb (mid-mid): {avg_net_pct:.4f} ({avg_net_pct*100:.2f}%)")
        log.info(f"")

        capitals = [100, 500, 1000, 5000, 10000]
        capture_rates = [1.0, 0.5, 0.3]

        header = f"  {'Capital':>10s}"
        for cr in capture_rates:
            header += f"  {'CR='+str(int(cr*100))+'%':>12s}"
        log.info(header)
        sep = f"  {'-'*10}"
        for _ in capture_rates:
            sep += f"  {'-'*12}"
        log.info(sep)

        for cap in capitals:
            row = f"  ${cap:>8,}"
            for cr in capture_rates:
                # Each arb deploys full capital (buy both sides)
                # Profit per trade = capital * avg_net_pct
                # But we can only do one trade at a time per market
                # Max trades = opps_per_month * capture_rate
                monthly_rev = opps_per_month * cr * cap * avg_net_pct
                row += f"  ${monthly_rev:>10,.2f}"
            log.info(row)

        log.info(f"\n  ⚠ IMPORTANT CAVEATS:")
        log.info(f"    1. Mid-mid overstates arb. Real spread adds ~1-3c per side.")
        log.info(f"    2. Sub-100ms bots compete for same arb (avg window ~2.7s per knowledge base).")
        log.info(f"    3. Capital lock-up: tokens lock until market resolution (15 min max).")
        log.info(f"    4. Execution risk: partial fills leave directional exposure.")
        log.info(f"    5. Revenue above assumes unlimited opportunities, but each market")
        log.info(f"       has limited liquidity (check ob_ask_vol for realistic size).")

    # Liquidity-constrained projection
    section("10. LIQUIDITY-CONSTRAINED PROJECTION")

    if n_profitable > 0:
        profitable_records = [r for r, n in zip(arb_records, net) if n > 0]
        ask_vols = np.array([r["ob_ask_vol"] for r in profitable_records])
        log.info(f"  When arb is profitable:")
        log.info(f"    Mean ob_ask_vol   : ${np.mean(ask_vols):,.0f}")
        log.info(f"    Median ob_ask_vol : ${np.median(ask_vols):,.0f}")
        log.info(f"    P10 ob_ask_vol    : ${np.percentile(ask_vols, 10):,.0f}")
        log.info(f"  → Realistic max size per trade: ~${np.percentile(ask_vols, 10)/2:,.0f} per side")
        log.info(f"    (using P10 / 2 to avoid excessive slippage)")

        # Realistic monthly with liquidity cap
        max_per_trade = np.percentile(ask_vols, 10) / 2
        log.info(f"\n  Liquidity-capped monthly projection (cap=${max_per_trade:,.0f}/trade):")
        for cr in capture_rates:
            monthly = opps_per_month * cr * min(max_per_trade, 10000) * avg_net_pct
            log.info(f"    CR={int(cr*100)}%: ${monthly:,.2f}/mo")

    # Capital-turnover-realistic projection
    section("10b. REALISTIC CAPITAL-TURNOVER PROJECTION")

    if n_profitable > 0:
        log.info("  Key reality: capital locks until market resolution (max 15 min).")
        log.info("  So we can't deploy the same $ into every tick -- we deploy once,")
        log.info("  wait for resolution, then redeploy.")
        log.info("")

        # Count unique arb WINDOWS (not ticks) -- already computed in duration analysis
        # More precisely: how many distinct markets per day had at least one arb tick?
        arb_markets_per_day = defaultdict(set)
        for r in records:
            if r["combined"] < 1.0:
                day = r["ts"].date()
                arb_markets_per_day[day].add(r["cid"])

        total_arb_market_entries = sum(len(v) for v in arb_markets_per_day.values())
        avg_arb_markets_per_day = total_arb_market_entries / max(span_days, 0.01)

        # Each market = one arb entry (buy both sides), capital locked 15 min
        # In 24h we have 96 x 15min slots. We can do at most 96 trades/day per $ unit.
        # But we're limited by how many markets actually have arb.
        # Each entry: profit = avg_net_pct on the capital deployed
        max_turns_per_day = 96  # 24h / 15min
        actual_turns = min(avg_arb_markets_per_day, max_turns_per_day)

        log.info(f"  Distinct markets with arb (<1.0) per day: {avg_arb_markets_per_day:.0f}")
        log.info(f"  Max capital turns/day (15min lock): {max_turns_per_day}")
        log.info(f"  Effective turns/day: {actual_turns:.0f}")
        log.info(f"  Avg net per turn (mid-mid): {avg_net_pct:.4f} ({avg_net_pct*100:.2f}%)")
        log.info(f"")

        capitals_r = [500, 1000, 2000, 5000]
        capture_rates_r = [0.5, 0.3, 0.1]
        log.info(f"  Monthly revenue = capital x turns/day x capture_rate x avg_net x 30")
        log.info(f"")
        header = f"  {'Capital':>10s}"
        for cr in capture_rates_r:
            header += f"  {'CR='+str(int(cr*100))+'%':>12s}"
        log.info(header)
        sep = f"  {'-'*10}"
        for _ in capture_rates_r:
            sep += f"  {'-'*12}"
        log.info(sep)

        for cap in capitals_r:
            row = f"  ${cap:>8,}"
            for cr in capture_rates_r:
                monthly = cap * actual_turns * cr * avg_net_pct * 30
                row += f"  ${monthly:>10,.2f}"
            row += f"  ({actual_turns * 0.3 * avg_net_pct * 30 * 100:.1f}% ROI @CR=30%)"
            log.info(row)

        log.info(f"")
        log.info(f"  At CR=10% (very conservative):")
        log.info(f"    $1,000 capital → ${1000 * actual_turns * 0.1 * avg_net_pct * 30:,.2f}/mo")
        log.info(f"    Daily ROI: {actual_turns * 0.1 * avg_net_pct * 100:.3f}%")
        log.info(f"")
        log.info(f"  NOTE: This is STILL an upper bound because:")
        log.info(f"    - Mid-mid, not ask-ask (real cost ~1-3c higher per side)")
        log.info(f"    - Sub-100ms bots may take the arb before us")
        log.info(f"    - Partial fills create directional risk")
        log.info(f"    - Only 1.4 days of data -- small sample")


def analyze_overpriced(records: list[dict]) -> None:
    """Bonus: also check combined > 1.0 (overpriced = sell both sides arb)."""
    section("11. OVERPRICED ANALYSIS (combined > 1.0)")

    combined = np.array([r["combined"] for r in records])
    over = combined[combined > 1.0]

    if len(over) == 0:
        log.info("  No overpriced snapshots found.")
        return

    n = len(combined)
    log.info(f"  Snapshots with combined > 1.0 : {len(over):,} / {n:,} ({len(over)/n*100:.1f}%)")
    log.info(f"  > 1.02                        : {int(np.sum(combined > 1.02)):,} ({np.sum(combined > 1.02)/n*100:.1f}%)")
    log.info(f"  > 1.05                        : {int(np.sum(combined > 1.05)):,} ({np.sum(combined > 1.05)/n*100:.1f}%)")
    log.info(f"  Mean excess (when > 1.0)      : {np.mean(over - 1.0):.4f}")
    log.info(f"  Max excess                    : {np.max(over - 1.0):.4f}")
    log.info(f"  Note: Selling both sides requires existing position or minting capability.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 70)
    log.info("  POLYMARKET BTC 15M ARB SPREAD ANALYSIS")
    log.info("  Data: signal_tape.jsonl (BTC markets only)")
    log.info("=" * 70)

    if not SIGNAL_TAPE.exists():
        log.error(f"Data file not found: {SIGNAL_TAPE}")
        sys.exit(1)

    log.info(f"\nLoading data from: {SIGNAL_TAPE}")
    records = load_btc_records(SIGNAL_TAPE)
    log.info(f"Loaded {len(records):,} BTC 15M snapshots")

    if not records:
        log.error("No BTC records found. Check data file.")
        sys.exit(1)

    # Time range
    ts_min = min(r["ts"] for r in records)
    ts_max = max(r["ts"] for r in records)
    log.info(f"Time range: {ts_min.isoformat()} → {ts_max.isoformat()}")

    # Unique markets
    unique_markets = set(r["cid"] for r in records)
    log.info(f"Unique BTC 15M markets: {len(unique_markets)}")

    # Run all analyses
    analyze_distribution(records)
    analyze_frequency(records)
    analyze_by_hour(records)
    analyze_duration(records)
    analyze_magnitude(records)
    market_stats = analyze_per_market(records)
    analyze_sigma_correlation(records, market_stats)
    analyze_arb_ev(records)
    analyze_overpriced(records)

    log.info("\n" + "=" * 70)
    log.info("  ANALYSIS COMPLETE")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
