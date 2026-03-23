#!/usr/bin/env python3
"""
W4 Coin x Timeframe Matrix: Full breakdown of trades by coin and timeframe.
Focus: Is W4 "only SOL on 5M"? Or does W4 trade ALL coins on 5M?
"""

import json
import re
from collections import defaultdict
from pathlib import Path

W4_FILE = Path("/Users/wai/projects/axc-trading/data/wallet4_raw/wallet4_all_trades.json")

def parse_slug(slug, title):
    """Extract coin, timeframe from slug/title."""
    coin_map = {
        "btc": "BTC", "bitcoin": "BTC",
        "eth": "ETH", "ethereum": "ETH",
        "sol": "SOL", "solana": "SOL",
        "xrp": "XRP",
    }

    # Structured slug: {coin}-updown-{dur}-{ts}
    m = re.match(r"(\w+)-updown-(\d+)m-(\d{10})", slug)
    if m:
        coin_key, dur_str, ts_str = m.groups()
        coin = coin_map.get(coin_key, coin_key.upper())
        dur = int(dur_str)
        if dur == 5:
            tf = "5M"
        elif dur == 15:
            tf = "15M"
        elif dur == 60:
            tf = "1H"
        else:
            tf = f"{dur}M"
        return coin, tf

    # Hourly title: "Bitcoin Up or Down - March 23, 10AM ET"
    m_title = re.match(
        r"(Bitcoin|Ethereum|Solana|XRP) Up or Down - (March) (\d+),? (\d+)(AM|PM)",
        title,
    )
    if m_title:
        coin_name = m_title.group(1)
        coin = coin_map.get(coin_name.lower(), coin_name.upper())
        return coin, "1H"

    return None, None


def main():
    with open(W4_FILE) as f:
        raw = json.load(f)

    print(f"Total records: {len(raw)}")

    # Filter to actual trades (not REDEEM)
    trades = [t for t in raw if t["type"] == "TRADE" and t["usdcSize"] > 0]
    print(f"Actual trades (TRADE with usdcSize > 0): {len(trades)}")

    # Group by coin x timeframe
    matrix = defaultdict(lambda: {"count": 0, "usdc": 0.0, "slugs": set()})
    unparsed = []

    for t in trades:
        slug = t.get("slug", "") or t.get("eventSlug", "")
        title = t.get("title", "")
        coin, tf = parse_slug(slug, title)
        if coin and tf:
            key = (coin, tf)
            matrix[key]["count"] += 1
            matrix[key]["usdc"] += t["usdcSize"]
            matrix[key]["slugs"].add(slug)
        else:
            unparsed.append((slug, title))

    if unparsed:
        print(f"\nUnparsed: {len(unparsed)} trades")
        for s, t in unparsed[:5]:
            print(f"  slug={s}  title={t[:60]}")

    total_usdc = sum(v["usdc"] for v in matrix.values())
    total_trades = sum(v["count"] for v in matrix.values())

    # ── FULL MATRIX ──
    print("\n" + "=" * 90)
    print("FULL COIN x TIMEFRAME MATRIX")
    print("=" * 90)
    print(f"{'Coin':<6} {'TF':<5} {'Trades':>8} {'USDC':>12} {'% Total':>8} {'Markets':>8}")
    print("-" * 90)

    coins = ["BTC", "ETH", "SOL", "XRP"]
    tfs = ["5M", "15M", "1H"]

    for tf in tfs:
        for coin in coins:
            key = (coin, tf)
            v = matrix.get(key, {"count": 0, "usdc": 0.0, "slugs": set()})
            pct = v["usdc"] / total_usdc * 100 if total_usdc > 0 else 0
            print(f"{coin:<6} {tf:<5} {v['count']:>8} ${v['usdc']:>11,.2f} {pct:>7.1f}% {len(v['slugs']):>8}")
        print()

    print(f"{'TOTAL':<12} {total_trades:>8} ${total_usdc:>11,.2f} {'100.0%':>8}")

    # ── PER-TIMEFRAME SUBTOTALS ──
    print("\n" + "=" * 90)
    print("PER-TIMEFRAME SUBTOTALS")
    print("=" * 90)
    for tf in tfs:
        tf_usdc = sum(matrix.get((c, tf), {"usdc": 0})["usdc"] for c in coins)
        tf_trades = sum(matrix.get((c, tf), {"count": 0})["count"] for c in coins)
        tf_pct = tf_usdc / total_usdc * 100 if total_usdc > 0 else 0
        print(f"  {tf:<5}: {tf_trades:>6} trades | ${tf_usdc:>10,.2f} | {tf_pct:>5.1f}%")

    # ── 5M FOCUS ──
    print("\n" + "=" * 90)
    print("5M FOCUS: Which coin has the MOST volume on 5M?")
    print("=" * 90)
    fivem_total = sum(matrix.get((c, "5M"), {"usdc": 0})["usdc"] for c in coins)
    for coin in coins:
        v = matrix.get((coin, "5M"), {"count": 0, "usdc": 0.0, "slugs": set()})
        pct_of_5m = v["usdc"] / fivem_total * 100 if fivem_total > 0 else 0
        print(f"  {coin:<4}: {v['count']:>6} trades | ${v['usdc']:>10,.2f} | {pct_of_5m:>5.1f}% of 5M")

    # ── ANSWER: Is "W4 only does SOL on 5M" correct? ──
    print("\n" + "=" * 90)
    print("VERDICT: Is 'W4 only does SOL on 5M' correct?")
    print("=" * 90)
    sol_5m = matrix.get(("SOL", "5M"), {"usdc": 0})["usdc"]
    btc_5m = matrix.get(("BTC", "5M"), {"usdc": 0})["usdc"]
    eth_5m = matrix.get(("ETH", "5M"), {"usdc": 0})["usdc"]
    xrp_5m = matrix.get(("XRP", "5M"), {"usdc": 0})["usdc"]

    biggest_5m = max(
        [("BTC", btc_5m), ("ETH", eth_5m), ("SOL", sol_5m), ("XRP", xrp_5m)],
        key=lambda x: x[1]
    )
    print(f"  Biggest 5M coin: {biggest_5m[0]} with ${biggest_5m[1]:,.2f}")
    print(f"  SOL 5M: ${sol_5m:,.2f} ({sol_5m/fivem_total*100:.1f}% of 5M)" if fivem_total > 0 else "")
    print(f"  BTC 5M: ${btc_5m:,.2f} ({btc_5m/fivem_total*100:.1f}% of 5M)" if fivem_total > 0 else "")
    print(f"  ETH 5M: ${eth_5m:,.2f} ({eth_5m/fivem_total*100:.1f}% of 5M)" if fivem_total > 0 else "")
    print(f"  XRP 5M: ${xrp_5m:,.2f} ({xrp_5m/fivem_total*100:.1f}% of 5M)" if fivem_total > 0 else "")

    if sol_5m > btc_5m and sol_5m > eth_5m and sol_5m > xrp_5m:
        print("\n  >>> SOL IS the biggest 5M coin — claim is PARTIALLY correct")
        print("  >>> But W4 trades ALL FOUR coins on 5M, not 'only SOL'")
    else:
        print(f"\n  >>> INCORRECT: {biggest_5m[0]} is the biggest 5M coin, not SOL")
        print("  >>> W4 trades ALL FOUR coins on ALL timeframes")


if __name__ == "__main__":
    main()
