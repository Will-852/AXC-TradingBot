#!/usr/bin/env python3
"""Bridge Fair vs Polymarket Mid — by entry time bucket.

Compares our bridge fair_up to Polymarket's actual midpoint across timeframes.
Answers: does the gap grow larger for longer TFs (4H, Daily)?

Usage:
    python3 polymarket/analysis/fair_vs_poly_mid.py          # all TFs with data
    python3 polymarket/analysis/fair_vs_poly_mid.py --tf 1h   # 1H only
    python3 polymarket/analysis/fair_vs_poly_mid.py --tf 4h   # 4H only
    python3 polymarket/analysis/fair_vs_poly_mid.py --coin BTC # BTC only
"""
import argparse
import json
import math
import os
import sys

_AXC = os.path.expanduser("~/projects/axc-trading")
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")

# Add scripts to path for compute_fair_up
sys.path.insert(0, _AXC)
sys.path.insert(0, os.path.join(_AXC, "scripts"))


# ─── Student-t CDF (copied from market_maker.py for standalone use) ───
def _student_t_cdf(x: float, nu: float = 5.0) -> float:
    if nu <= 0:
        from scipy.stats import norm
        return norm.cdf(x)
    if abs(x) < 1e-12:
        return 0.5
    coeff = math.gamma((nu + 1) / 2) / (math.sqrt(nu * math.pi) * math.gamma(nu / 2))

    def pdf(t):
        return coeff * (1 + t * t / nu) ** (-(nu + 1) / 2)

    # Simpson's rule integration from -10 to x
    a, b = -10.0, x
    n = 2000
    h = (b - a) / n
    s = pdf(a) + pdf(b)
    for i in range(1, n):
        s += (4 if i % 2 else 2) * pdf(a + i * h)
    return max(0.0, min(1.0, s * h / 3))


def compute_fair_up(spot, open_price, vol_1m, minutes_remaining):
    if minutes_remaining <= 0:
        return 0.995 if spot >= open_price else 0.005
    if vol_1m <= 0 or spot <= 0 or open_price <= 0:
        return 0.5
    sigma = vol_1m * math.sqrt(minutes_remaining)
    if sigma < 1e-10:
        return 0.995 if spot >= open_price else 0.005
    d = math.log(spot / open_price) / sigma
    return max(0.005, min(0.995, _student_t_cdf(d, nu=5.0)))


# ─── Parse 1H signal tape (different schema) ───
def _load_1h(coin_filter=None):
    """1H tape: nested poly_1h[] with up_mid, btc.binance, btc_open, elapsed_min."""
    path = os.path.join(_LOG_DIR, "signal_tape_1h.jsonl")
    if not os.path.exists(path):
        return []

    records = []
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            markets = row.get("poly_1h", [])
            for mkt in markets:
                coin = mkt.get("coin", "")
                if coin_filter and coin != coin_filter:
                    continue

                up_mid = mkt.get("up_mid")
                elapsed = mkt.get("elapsed_min")
                btc_open = mkt.get("btc_open")
                if up_mid is None or elapsed is None or btc_open is None:
                    continue
                if elapsed < 0:
                    continue  # future market, not started
                if btc_open <= 0:
                    continue

                # Get spot price for this coin
                coin_data = row.get(coin.lower(), row.get("btc", {}))
                spot = coin_data.get("binance", 0)
                if spot <= 0:
                    continue

                # We don't have vol_1m in the 1H tape, estimate from price movement
                # Use a reasonable BTC vol_1m default (0.0006 typical for BTC)
                vol_1m = 0.0006

                minutes_remaining = max(1, 60 - elapsed)
                fair = compute_fair_up(spot, btc_open, vol_1m, minutes_remaining)

                records.append({
                    "coin": coin,
                    "t_elapsed": elapsed,
                    "fair_up": round(fair, 4),
                    "up_mid": round(up_mid, 4),
                    "entry_price": 0,  # not available in 1H tape
                    "window_min": 60,
                })
    return records


# ─── Parse 4H / Daily signal tape (flat schema, new poly_mid field) ───
def _load_flat_tape(tf, coin_filter=None):
    """4H/Daily tape: flat JSON with fair_up, poly_mid (new field)."""
    if tf == "4h":
        window_min = 240
    elif tf == "daily":
        window_min = 1440
    else:
        return []

    records = []
    # Try all coins
    for coin in ["BTC", "ETH", "SOL", "XRP"]:
        if coin_filter and coin != coin_filter:
            continue
        path = os.path.join(_LOG_DIR, f"signal_tape_{tf}_{coin}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                fair_up = row.get("fair_up")
                poly_mid = row.get("up_mid")  # new field name
                t_elapsed = row.get("t_elapsed")
                if fair_up is None or t_elapsed is None:
                    continue
                if fair_up <= 0.01 or fair_up >= 0.99:
                    continue  # skip near-resolved

                records.append({
                    "coin": row.get("coin", coin),
                    "t_elapsed": t_elapsed,
                    "fair_up": round(fair_up, 4),
                    "up_mid": round(poly_mid, 4) if poly_mid is not None else None,
                    "entry_price": row.get("entry_price", 0),
                    "window_min": window_min,
                })
    return records


def _print_table(tf_label, records, bucket_size=5, window_min=60):
    """Print the fair-vs-mid comparison table."""
    # Filter to records that have poly_mid
    with_mid = [r for r in records if r.get("up_mid") is not None]
    without_mid = len(records) - len(with_mid)

    print(f"\n{'=' * 75}")
    print(f"  {tf_label} — Bridge Fair vs Polymarket Mid (Edge by Entry Time)")
    print(f"{'=' * 75}")
    print(f"  Total records: {len(records)} | With poly_mid: {len(with_mid)}"
          f" | Missing: {without_mid}")

    if not with_mid:
        print(f"\n  ⚠️  No records with poly_mid data!")
        print(f"  → 4H/Daily: poly_mid logging was just added. Wait for data to accumulate.")
        print(f"  → Existing {len(records)} records only have fair_up (no market comparison).")

        # Show fair_up distribution as a preview
        if records:
            print(f"\n  Preview: fair_up distribution by t_elapsed (no poly_mid comparison)")
            print(f"  {'T+min':>6} | {'N':>5} | {'Avg Fair':>8} | {'Fair>0.5':>7}")
            print(f"  {'-' * 38}")

            buckets = {}
            for r in records:
                b = int(r["t_elapsed"] / bucket_size) * bucket_size
                buckets.setdefault(b, []).append(r)

            for b in sorted(buckets.keys()):
                recs = buckets[b]
                avg_fair = sum(r["fair_up"] for r in recs) / len(recs)
                pct_above = sum(1 for r in recs if r["fair_up"] > 0.5) / len(recs) * 100
                print(f"  T+{b:>3}  | {len(recs):>5} | {avg_fair:>7.1%} |  {pct_above:>5.1f}%")
        return

    # Bucket by t_elapsed
    buckets = {}
    for r in with_mid:
        b = int(r["t_elapsed"] / bucket_size) * bucket_size
        buckets.setdefault(b, []).append(r)

    print(f"\n  {'T+min':>6} | {'N':>5} | {'Our Fair':>8} | {'Poly Mid':>8} | "
          f"{'Edge':>8} | {'Edge>0':>6} | {'Avg Entry':>9} | {'Fillable':>8}")
    print(f"  {'-' * 77}")

    total_pos = 0
    total_neg = 0

    for b in sorted(buckets.keys()):
        recs = buckets[b]
        n = len(recs)
        avg_fair = sum(r["fair_up"] for r in recs) / n
        avg_mid = sum(r["up_mid"] for r in recs) / n
        edge = avg_fair - avg_mid
        pos = sum(1 for r in recs if r["fair_up"] > r["up_mid"])
        neg = n - pos
        total_pos += pos
        total_neg += neg
        pct_pos = pos / n * 100
        avg_entry = sum(r.get("entry_price", 0) for r in recs) / n

        # Fillable heuristic: if entry_price is close to poly_mid
        fill_label = "HARD"
        if avg_entry > 0 and avg_mid > 0:
            gap = avg_mid - avg_entry
            if gap < 0.01:
                fill_label = "EASY"
            elif gap < 0.03:
                fill_label = "OK"
            elif gap < 0.05:
                fill_label = "TOUGH"

        print(f"  T+{b:>3}  | {n:>5} | {avg_fair:>7.1%} | {avg_mid:>7.1%} | "
              f"{edge:>+7.1%} | {pct_pos:>5.0f}% | {avg_entry:>8.2f}¢ | {fill_label:>8}")

    total = total_pos + total_neg
    if total > 0:
        print(f"\n  === KEY FINDING ===")
        print(f"  Records with positive edge (fair > market): "
              f"{total_pos}/{total} = {total_pos / total:.1%}")
        print(f"  Records with negative edge (market > fair): "
              f"{total_neg}/{total} = {total_neg / total:.1%}")

        if total_neg > total_pos:
            print(f"\n  ⚠️  Polymarket OVERPRICES our direction most of the time!")
            print(f"     Market mid > bridge fair → the market knows more than our bridge.")
        else:
            print(f"\n  ✅  Bridge fair > Poly mid → potential edge window exists!")


def _cross_tf_summary(all_results):
    """Compare edge patterns across timeframes."""
    print(f"\n{'=' * 75}")
    print(f"  CROSS-TIMEFRAME COMPARISON")
    print(f"{'=' * 75}")
    print(f"\n  {'TF':>6} | {'N':>6} | {'Avg Edge':>9} | {'Edge>0%':>7} | "
          f"{'Avg Fair':>8} | {'Avg Mid':>8}")
    print(f"  {'-' * 57}")

    for tf_label, records in sorted(all_results.items()):
        with_mid = [r for r in records if r.get("up_mid") is not None]
        if not with_mid:
            print(f"  {tf_label:>6} | {len(records):>6} | {'NO DATA':>9} | "
                  f"{'':>7} | {'':>8} | {'':>8}")
            continue
        n = len(with_mid)
        avg_fair = sum(r["fair_up"] for r in with_mid) / n
        avg_mid = sum(r["up_mid"] for r in with_mid) / n
        avg_edge = avg_fair - avg_mid
        pct_pos = sum(1 for r in with_mid
                      if r["fair_up"] > r["up_mid"]) / n * 100
        print(f"  {tf_label:>6} | {n:>6} | {avg_edge:>+8.2%} | {pct_pos:>5.1f}% | "
              f"{avg_fair:>7.1%} | {avg_mid:>7.1%}")


def main():
    parser = argparse.ArgumentParser(description="Bridge Fair vs Poly Mid analysis")
    parser.add_argument("--tf", choices=["1h", "4h", "daily", "all"], default="all")
    parser.add_argument("--coin", default=None, help="Filter by coin (BTC, ETH, ...)")
    args = parser.parse_args()

    all_results = {}

    if args.tf in ("1h", "all"):
        recs = _load_1h(coin_filter=args.coin)
        if recs:
            all_results["1H"] = recs
            _print_table("1H (60min window)", recs, bucket_size=5, window_min=60)

    if args.tf in ("4h", "all"):
        recs = _load_flat_tape("4h", coin_filter=args.coin)
        if recs:
            all_results["4H"] = recs
            _print_table("4H (240min window)", recs, bucket_size=15, window_min=240)

    if args.tf in ("daily", "all"):
        recs = _load_flat_tape("daily", coin_filter=args.coin)
        if recs:
            all_results["Daily"] = recs
            _print_table("Daily (1440min window)", recs, bucket_size=60, window_min=1440)

    if len(all_results) > 1:
        _cross_tf_summary(all_results)

    if not all_results:
        print("No signal tape data found.")


if __name__ == "__main__":
    main()
