#!/usr/bin/env python3
"""
blankandyellow Fill Rate Analysis
從 trade data 推斷 fill rate：
  - 每個 market 有幾多 trades？（proxy for order attempts）
  - Both-side fill 比例
  - 每邊 shares 對稱度（balanced = good fill）
  - Entry timing（幾秒入 window）
  - Price distribution（bid aggressiveness）
  - Combined price spread
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from statistics import median, mean, stdev

ADDRESS = "0xdc1e9e397b479e11591b1f3025c8ecd641e6d9c8"
API_BASE = "https://data-api.polymarket.com/trades"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_all_trades() -> list[dict]:
    """Fetch max trades (3500)."""
    all_trades = []
    for offset in range(0, 3500, 500):
        url = f"{API_BASE}?user={ADDRESS}&limit=500&offset={offset}"
        print(f"  offset={offset}...", end=" ", flush=True)
        try:
            req = Request(url, headers={"User-Agent": "AXC-Research/1.0"})
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            print(f"{len(data)}")
            if not data:
                break
            all_trades.extend(data)
            if len(data) < 500:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"ERR: {e}")
            break

    # Dedup
    seen = set()
    unique = []
    for t in all_trades:
        key = (t.get("transactionHash", ""), t.get("asset", ""),
               t.get("outcome", ""), str(t.get("size", "")), str(t.get("price", "")))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    print(f"  {len(all_trades)} raw → {len(unique)} unique")
    return unique


def extract_window_ts(slug: str) -> int | None:
    """Extract window start timestamp from slug like btc-updown-5m-1774426800."""
    import re
    m = re.search(r'-(\d{10})$', slug or "")
    return int(m.group(1)) if m else None


def analyze_fill_rate(trades: list[dict]):
    """Detailed fill rate analysis."""

    # Group by market
    markets = defaultdict(list)
    for t in trades:
        key = t.get("eventSlug") or t.get("slug") or "unknown"
        markets[key].append(t)

    print(f"\n{'='*70}")
    print(f"  blankandyellow — Fill Rate Analysis")
    print(f"  Total trades: {len(trades)}  |  Markets: {len(markets)}")
    print(f"{'='*70}")

    # Per-market analysis
    market_stats = []
    for slug, mtrades in markets.items():
        window_ts = extract_window_ts(slug)

        up_trades = [t for t in mtrades if t.get("outcome", "").lower() in ("up", "yes")]
        down_trades = [t for t in mtrades if t.get("outcome", "").lower() in ("down", "no")]

        up_shares = sum(float(t.get("size", 0)) for t in up_trades)
        down_shares = sum(float(t.get("size", 0)) for t in down_trades)
        up_cost = sum(float(t.get("size", 0)) * float(t.get("price", 0)) for t in up_trades)
        down_cost = sum(float(t.get("size", 0)) * float(t.get("price", 0)) for t in down_trades)

        avg_up_price = mean([float(t.get("price", 0)) for t in up_trades]) if up_trades else 0
        avg_down_price = mean([float(t.get("price", 0)) for t in down_trades]) if down_trades else 0
        combined = avg_up_price + avg_down_price

        # Entry timing: seconds into 5M window (300s)
        entry_times = []
        if window_ts:
            for t in mtrades:
                ts = t.get("timestamp", 0)
                offset = ts - window_ts
                if 0 <= offset <= 300:
                    entry_times.append(offset)

        # Fill symmetry: ratio of smaller side to larger side
        max_shares = max(up_shares, down_shares, 0.01)
        min_shares = min(up_shares, down_shares)
        symmetry = min_shares / max_shares if max_shares > 0 else 0

        has_both = up_shares > 0 and down_shares > 0

        # Coin from slug
        coin = "BTC" if "btc" in slug.lower() else "ETH" if "eth" in slug.lower() else "OTHER"

        market_stats.append({
            "slug": slug,
            "coin": coin,
            "num_trades": len(mtrades),
            "up_trades": len(up_trades),
            "down_trades": len(down_trades),
            "up_shares": round(up_shares, 2),
            "down_shares": round(down_shares, 2),
            "up_cost": round(up_cost, 2),
            "down_cost": round(down_cost, 2),
            "avg_up_price": round(avg_up_price, 4),
            "avg_down_price": round(avg_down_price, 4),
            "combined": round(combined, 4),
            "symmetry": round(symmetry, 4),
            "has_both": has_both,
            "entry_times": entry_times,
            "first_entry": min(entry_times) if entry_times else None,
            "last_entry": max(entry_times) if entry_times else None,
            "matched_shares": round(min_shares, 2),
            "excess_shares": round(abs(up_shares - down_shares), 2),
            "excess_side": "UP" if up_shares > down_shares else "DOWN" if down_shares > up_shares else "EVEN",
        })

    # ═══ Summary Stats ═══
    both_count = sum(1 for m in market_stats if m["has_both"])
    single_count = len(market_stats) - both_count
    print(f"\n  --- Fill Rate (Both-Sides) ---")
    print(f"  Both sides filled: {both_count}/{len(market_stats)} ({both_count/len(market_stats)*100:.1f}%)")
    print(f"  Single side only:  {single_count}")

    if single_count > 0:
        singles = [m for m in market_stats if not m["has_both"]]
        up_only = sum(1 for m in singles if m["up_shares"] > 0)
        down_only = sum(1 for m in singles if m["down_shares"] > 0)
        print(f"    UP only: {up_only}  |  DOWN only: {down_only}")

    # ═══ Fill symmetry ═══
    symmetries = [m["symmetry"] for m in market_stats if m["has_both"]]
    if symmetries:
        print(f"\n  --- Fill Symmetry (both-side markets) ---")
        print(f"  Mean symmetry:   {mean(symmetries):.4f}  (1.0 = perfect match)")
        print(f"  Median symmetry: {median(symmetries):.4f}")

        # Distribution
        buckets = {"0.0-0.5": 0, "0.5-0.8": 0, "0.8-0.95": 0, "0.95-1.0": 0}
        for s in symmetries:
            if s < 0.5:
                buckets["0.0-0.5"] += 1
            elif s < 0.8:
                buckets["0.5-0.8"] += 1
            elif s < 0.95:
                buckets["0.8-0.95"] += 1
            else:
                buckets["0.95-1.0"] += 1
        print(f"  Distribution:")
        for b, c in buckets.items():
            bar = "█" * int(c / max(len(symmetries), 1) * 40)
            print(f"    {b}: {c:>4} ({c/len(symmetries)*100:5.1f}%) {bar}")

    # ═══ Excess shares analysis ═══
    excess_list = [m["excess_shares"] for m in market_stats if m["has_both"]]
    matched_list = [m["matched_shares"] for m in market_stats if m["has_both"]]
    if excess_list:
        excess_pcts = [e / (e + m) * 100 if (e + m) > 0 else 0 for e, m in zip(excess_list, matched_list)]
        print(f"\n  --- Excess Shares (unmatched, = directional exposure) ---")
        print(f"  Mean excess:   {mean(excess_list):.1f} shares ({mean(excess_pcts):.1f}% of total)")
        print(f"  Median excess: {median(excess_list):.1f} shares ({median(excess_pcts):.1f}%)")

        excess_sides = [m["excess_side"] for m in market_stats if m["has_both"]]
        up_excess = sum(1 for s in excess_sides if s == "UP")
        down_excess = sum(1 for s in excess_sides if s == "DOWN")
        even = sum(1 for s in excess_sides if s == "EVEN")
        print(f"  Excess side: UP={up_excess}  DOWN={down_excess}  EVEN={even}")

    # ═══ Trades per market ═══
    trades_per = [m["num_trades"] for m in market_stats]
    print(f"\n  --- Trades per Market (proxy for order activity) ---")
    print(f"  Mean:   {mean(trades_per):.1f}")
    print(f"  Median: {median(trades_per):.1f}")
    print(f"  Min:    {min(trades_per)}  |  Max: {max(trades_per)}")

    # Breakdown: how many trades per side
    up_per = [m["up_trades"] for m in market_stats if m["has_both"]]
    down_per = [m["down_trades"] for m in market_stats if m["has_both"]]
    if up_per:
        print(f"  UP trades/market:   mean={mean(up_per):.1f}  median={median(up_per):.1f}")
        print(f"  DOWN trades/market: mean={mean(down_per):.1f}  median={median(down_per):.1f}")

    # ═══ Combined price (arb spread) ═══
    combined_prices = [m["combined"] for m in market_stats if m["has_both"]]
    if combined_prices:
        print(f"\n  --- Combined Price (arb spread) ---")
        print(f"  Mean:   {mean(combined_prices):.4f}")
        print(f"  Median: {median(combined_prices):.4f}")
        print(f"  Min:    {min(combined_prices):.4f}  (best arb)")
        print(f"  Max:    {max(combined_prices):.4f}  (worst)")
        profitable = sum(1 for c in combined_prices if c < 1.0)
        print(f"  < $1.00 (profitable merge): {profitable}/{len(combined_prices)} ({profitable/len(combined_prices)*100:.1f}%)")

        # Distribution
        print(f"\n  Combined price distribution:")
        ranges = [
            ("<0.92", 0, 0.92),
            ("0.92-0.95", 0.92, 0.95),
            ("0.95-0.97", 0.95, 0.97),
            ("0.97-0.99", 0.97, 0.99),
            ("0.99-1.00", 0.99, 1.00),
            ("1.00-1.02", 1.00, 1.02),
            (">1.02", 1.02, 2.00),
        ]
        for label, lo, hi in ranges:
            c = sum(1 for p in combined_prices if lo <= p < hi)
            bar = "█" * int(c / len(combined_prices) * 40)
            print(f"    {label:>10}: {c:>4} ({c/len(combined_prices)*100:5.1f}%) {bar}")

    # ═══ Entry Timing ═══
    all_first_entries = [m["first_entry"] for m in market_stats if m["first_entry"] is not None]
    all_last_entries = [m["last_entry"] for m in market_stats if m["last_entry"] is not None]
    if all_first_entries:
        print(f"\n  --- Entry Timing (seconds into 5M window) ---")
        print(f"  First entry: mean={mean(all_first_entries):.1f}s  median={median(all_first_entries):.1f}s")
        print(f"  Last entry:  mean={mean(all_last_entries):.1f}s  median={median(all_last_entries):.1f}s")
        print(f"  Window coverage: mean={mean([l - f for f, l in zip(all_first_entries, all_last_entries)]):.1f}s")

        # Timing distribution for first entry
        timing_buckets = {"0-10s": 0, "10-30s": 0, "30-60s": 0, "60-120s": 0, "120-300s": 0}
        for t in all_first_entries:
            if t <= 10:
                timing_buckets["0-10s"] += 1
            elif t <= 30:
                timing_buckets["10-30s"] += 1
            elif t <= 60:
                timing_buckets["30-60s"] += 1
            elif t <= 120:
                timing_buckets["60-120s"] += 1
            else:
                timing_buckets["120-300s"] += 1
        print(f"  First entry distribution:")
        for b, c in timing_buckets.items():
            bar = "█" * int(c / max(len(all_first_entries), 1) * 40)
            print(f"    {b:>10}: {c:>4} ({c/len(all_first_entries)*100:5.1f}%) {bar}")

    # ═══ Price per side ═══
    up_prices = [m["avg_up_price"] for m in market_stats if m["avg_up_price"] > 0]
    down_prices = [m["avg_down_price"] for m in market_stats if m["avg_down_price"] > 0]
    if up_prices:
        print(f"\n  --- Price per Side ---")
        print(f"  UP avg:   {mean(up_prices):.4f}  median: {median(up_prices):.4f}")
        print(f"  DOWN avg: {mean(down_prices):.4f}  median: {median(down_prices):.4f}")

    # ═══ Coin breakdown ═══
    btc_markets = [m for m in market_stats if m["coin"] == "BTC"]
    eth_markets = [m for m in market_stats if m["coin"] == "ETH"]
    print(f"\n  --- Coin Breakdown ---")
    for label, subset in [("BTC", btc_markets), ("ETH", eth_markets)]:
        if not subset:
            continue
        both = sum(1 for m in subset if m["has_both"])
        combos = [m["combined"] for m in subset if m["has_both"]]
        tpm = [m["num_trades"] for m in subset]
        print(f"  {label}: {len(subset)} markets, both-sides={both} ({both/len(subset)*100:.1f}%)")
        if combos:
            print(f"    Combined: mean={mean(combos):.4f}  median={median(combos):.4f}")
        print(f"    Trades/mkt: mean={mean(tpm):.1f}  median={median(tpm):.1f}")

    # ═══ Inferred Fill Rate ═══
    print(f"\n  {'='*70}")
    print(f"  INFERRED FILL RATE SUMMARY")
    print(f"  {'='*70}")

    # Method 1: Both-side fill rate
    print(f"  1. Both-side fill rate: {both_count/len(market_stats)*100:.1f}%")
    print(f"     (= % of markets where BOTH Up and Down got filled)")

    # Method 2: Shares matched vs total
    total_shares = sum(m["up_shares"] + m["down_shares"] for m in market_stats)
    total_matched = sum(m["matched_shares"] * 2 for m in market_stats if m["has_both"])
    if total_shares > 0:
        print(f"  2. Shares utilization: {total_matched/total_shares*100:.1f}%")
        print(f"     (= matched_pairs × 2 / total_shares)")

    # Method 3: Implied order-to-fill ratio
    # If they're using maker orders, some won't fill. The number of filled trades
    # per market hints at how many attempts were needed.
    # A pure taker would get 1 trade per side = 2 trades per market (both sides)
    # More trades = either scaling in, or partial fills from maker orders
    avg_trades = mean(trades_per)
    print(f"  3. Avg filled trades/market: {avg_trades:.1f}")
    print(f"     (high = multiple partial fills or scaling)")

    # Method 4: Profitable merge rate
    if combined_prices:
        profitable_rate = profitable / len(combined_prices) * 100
        avg_spread = mean([1.0 - c for c in combined_prices if c < 1.0]) if profitable > 0 else 0
        print(f"  4. Profitable merge rate: {profitable_rate:.1f}%")
        print(f"     Avg spread when profitable: {avg_spread:.4f} (${avg_spread:.4f}/share)")

    print()


def main():
    print("Fetching blankandyellow trades...")
    trades = fetch_all_trades()
    analyze_fill_rate(trades)

    # Save raw trades for further analysis
    output_path = os.path.join(OUTPUT_DIR, "data", "blankandyellow_trades.json")
    os.makedirs(os.path.join(OUTPUT_DIR, "data"), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(trades, f)
    print(f"  Raw trades saved to: {output_path}")


if __name__ == "__main__":
    main()
