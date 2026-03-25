#!/usr/bin/env python3
"""
Stable Wallet Daily Analysis
搵出最穩定同埋盈利最少嘅錢包，分析：
  - 每日總盈利 / 總虧蝕
  - 平均賠率（每邊）
  - 每邊買咗幾多
  - 總共買咗幾多類型（market type）
  - 30 日數據

Data source: data-api.polymarket.com/trades
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from statistics import median, mean

# ─── Wallets to analyze ───
WALLETS = {
    "blankandyellow": "0xdc1e9e397b479e11591b1f3025c8ecd641e6d9c8",
    "kafwhsd": "0xfdb826a0fb4a90b4cc9049e408ea3ef1b73ae4c9",
    "likebot": "0x03c3b0236c5a01051381482e77f2210349073a1d",
    "Anon": "0xe38b7a6553cbcac3bf6d9e22c83cdce092951fdc",
}

API_BASE = "https://data-api.polymarket.com/trades"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_trades(address: str, days: int = 30) -> list[dict]:
    """Fetch trades for a wallet, paginating until we have ~30 days."""
    all_trades = []
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    offset = 0
    page_size = 500

    while True:
        url = f"{API_BASE}?user={address}&limit={page_size}&offset={offset}"
        print(f"  Fetching offset={offset}...", end=" ", flush=True)
        try:
            req = Request(url, headers={"User-Agent": "AXC-Research/1.0"})
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            print(f"{len(data)} trades")

            if not data:
                break

            all_trades.extend(data)

            # Check if oldest trade in this batch is before cutoff
            oldest_ts = min(t.get("timestamp", 9999999999) for t in data)
            if oldest_ts < cutoff_ts:
                break

            if len(data) < page_size:
                break

            offset += page_size
            time.sleep(0.3)

        except Exception as e:
            print(f"ERROR: {e}")
            break

    # Dedup
    seen = set()
    unique = []
    for t in all_trades:
        key = (
            t.get("transactionHash", ""),
            t.get("asset", ""),
            t.get("outcome", ""),
            str(t.get("size", "")),
            str(t.get("price", "")),
        )
        if key not in seen:
            seen.add(key)
            unique.append(t)

    # Filter to last N days
    filtered = [t for t in unique if t.get("timestamp", 0) >= cutoff_ts]
    print(f"  Total: {len(all_trades)} raw → {len(unique)} unique → {len(filtered)} in {days}d")
    return filtered


def parse_slug(slug: str) -> dict:
    """Extract coin, timeframe from slug."""
    slug_lower = (slug or "").lower()

    coin = "UNKNOWN"
    if "btc" in slug_lower or "bitcoin" in slug_lower:
        coin = "BTC"
    elif "eth" in slug_lower or "ethereum" in slug_lower:
        coin = "ETH"
    elif "sol" in slug_lower or "solana" in slug_lower:
        coin = "SOL"
    elif "xrp" in slug_lower or "ripple" in slug_lower:
        coin = "XRP"
    elif "doge" in slug_lower:
        coin = "DOGE"
    elif "bnb" in slug_lower:
        coin = "BNB"
    elif "hype" in slug_lower:
        coin = "HYPE"

    tf = "UNKNOWN"
    if "-5m-" in slug_lower or "5-minute" in slug_lower or "5-min" in slug_lower:
        tf = "5M"
    elif "-15m-" in slug_lower or "15-minute" in slug_lower or "15-min" in slug_lower:
        tf = "15M"
    elif "-1h-" in slug_lower or "hourly" in slug_lower:
        tf = "1H"
    elif "-4h-" in slug_lower or "4-hour" in slug_lower:
        tf = "4H"
    elif "daily" in slug_lower or "-daily-" in slug_lower:
        tf = "DAILY"

    return {"coin": coin, "tf": tf}


def group_by_market(trades: list[dict]) -> dict:
    """Group trades by market (slug/eventSlug)."""
    markets = defaultdict(list)
    for t in trades:
        key = t.get("eventSlug") or t.get("slug") or t.get("conditionId", "unknown")
        markets[key].append(t)
    return dict(markets)


def estimate_market_pnl(trades_in_market: list[dict]) -> dict:
    """
    Estimate P&L for a single market.
    Binary market: if both sides bought → merge arb (profit = shares_matched * (1 - combined_price))
    Single side → directional bet (need resolution, approximate with price)

    Returns dict with pnl_estimate, up_cost, down_cost, up_shares, down_shares, etc.
    """
    up_shares = 0.0
    up_cost = 0.0
    down_shares = 0.0
    down_cost = 0.0
    up_prices = []
    down_prices = []

    for t in trades_in_market:
        side = t.get("outcome", "").lower()
        shares = float(t.get("size", 0))
        price = float(t.get("price", 0))
        cost = shares * price

        if side == "up" or side == "yes":
            up_shares += shares
            up_cost += cost
            up_prices.append(price)
        elif side == "down" or side == "no":
            down_shares += shares
            down_cost += cost
            down_prices.append(price)

    matched = min(up_shares, down_shares)
    total_cost = up_cost + down_cost

    # Average prices
    avg_up = mean(up_prices) if up_prices else 0
    avg_down = mean(down_prices) if down_prices else 0
    combined = avg_up + avg_down if (avg_up and avg_down) else 0

    # P&L estimation
    if matched > 0 and combined > 0:
        # Merge arb: matched pairs redeem for $1.00
        merge_profit = matched * (1.0 - combined)
        # Excess shares: assume 50% win rate for directional portion
        excess = abs(up_shares - down_shares)
        excess_side_price = avg_up if up_shares > down_shares else avg_down
        # Conservative: excess has ~50% chance of winning
        excess_ev = excess * (0.5 - excess_side_price) * 0.5  # very rough
        pnl = merge_profit + excess_ev
    elif up_shares > 0 and down_shares == 0:
        # Pure directional up
        pnl = up_shares * (0.5 - avg_up)  # rough: assume 50% fair
    elif down_shares > 0 and up_shares == 0:
        pnl = down_shares * (0.5 - avg_down)
    else:
        pnl = 0

    return {
        "pnl": pnl,
        "up_shares": up_shares,
        "down_shares": down_shares,
        "up_cost": up_cost,
        "down_cost": down_cost,
        "avg_up_price": avg_up,
        "avg_down_price": avg_down,
        "combined_price": combined,
        "matched": matched,
        "both_sides": matched > 0,
        "total_cost": total_cost,
        "num_trades": len(trades_in_market),
    }


def analyze_wallet(name: str, trades: list[dict]) -> dict:
    """Full analysis for one wallet."""
    if not trades:
        return {"name": name, "error": "no trades"}

    # ─── Group by date ───
    daily = defaultdict(list)
    for t in trades:
        ts = t.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        day_key = dt.strftime("%Y-%m-%d")
        daily[day_key].append(t)

    # ─── Group by market ───
    markets = group_by_market(trades)

    # ─── Per-market P&L ───
    market_results = {}
    for slug, mtrades in markets.items():
        market_results[slug] = estimate_market_pnl(mtrades)

    # ─── Daily P&L ───
    daily_pnl = {}
    for day, day_trades in sorted(daily.items()):
        day_markets = group_by_market(day_trades)
        day_profit = 0.0
        day_loss = 0.0
        day_total_cost = 0.0
        day_up_cost = 0.0
        day_down_cost = 0.0
        day_market_count = len(day_markets)
        day_both_sides = 0
        day_single_side = 0
        day_prices = []
        day_coins = set()
        day_tfs = set()

        for slug, mtrades in day_markets.items():
            result = estimate_market_pnl(mtrades)
            if result["pnl"] >= 0:
                day_profit += result["pnl"]
            else:
                day_loss += result["pnl"]

            day_total_cost += result["total_cost"]
            day_up_cost += result["up_cost"]
            day_down_cost += result["down_cost"]

            if result["both_sides"]:
                day_both_sides += 1
            else:
                day_single_side += 1

            if result["combined_price"] > 0:
                day_prices.append(result["combined_price"])

            parsed = parse_slug(slug)
            day_coins.add(parsed["coin"])
            day_tfs.add(parsed["tf"])

        daily_pnl[day] = {
            "profit": round(day_profit, 2),
            "loss": round(day_loss, 2),
            "net": round(day_profit + day_loss, 2),
            "markets": day_market_count,
            "trades": len(day_trades),
            "total_cost": round(day_total_cost, 2),
            "up_cost": round(day_up_cost, 2),
            "down_cost": round(day_down_cost, 2),
            "both_sides": day_both_sides,
            "single_side": day_single_side,
            "avg_combined_price": round(mean(day_prices), 4) if day_prices else 0,
            "coins": sorted(day_coins),
            "timeframes": sorted(day_tfs),
        }

    # ─── Overall stats ───
    all_combined = [r["combined_price"] for r in market_results.values() if r["combined_price"] > 0]
    all_up_prices = [r["avg_up_price"] for r in market_results.values() if r["avg_up_price"] > 0]
    all_down_prices = [r["avg_down_price"] for r in market_results.values() if r["avg_down_price"] > 0]

    total_up_cost = sum(r["up_cost"] for r in market_results.values())
    total_down_cost = sum(r["down_cost"] for r in market_results.values())
    total_up_shares = sum(r["up_shares"] for r in market_results.values())
    total_down_shares = sum(r["down_shares"] for r in market_results.values())
    both_count = sum(1 for r in market_results.values() if r["both_sides"])
    single_count = sum(1 for r in market_results.values() if not r["both_sides"])

    # Coin + TF breakdown
    coin_counts = defaultdict(int)
    tf_counts = defaultdict(int)
    for slug in markets:
        parsed = parse_slug(slug)
        coin_counts[parsed["coin"]] += 1
        tf_counts[parsed["tf"]] += 1

    return {
        "name": name,
        "total_trades": len(trades),
        "total_markets": len(markets),
        "days_active": len(daily_pnl),
        "date_range": f"{min(daily_pnl.keys())} → {max(daily_pnl.keys())}",
        "summary": {
            "avg_combined_price": round(mean(all_combined), 4) if all_combined else 0,
            "median_combined_price": round(median(all_combined), 4) if all_combined else 0,
            "avg_up_price": round(mean(all_up_prices), 4) if all_up_prices else 0,
            "avg_down_price": round(mean(all_down_prices), 4) if all_down_prices else 0,
            "total_up_cost": round(total_up_cost, 2),
            "total_down_cost": round(total_down_cost, 2),
            "total_up_shares": round(total_up_shares, 2),
            "total_down_shares": round(total_down_shares, 2),
            "up_down_cost_ratio": round(total_up_cost / total_down_cost, 3) if total_down_cost else 0,
            "both_sides_markets": both_count,
            "single_side_markets": single_count,
            "both_sides_pct": round(both_count / len(markets) * 100, 1) if markets else 0,
        },
        "coin_breakdown": dict(sorted(coin_counts.items(), key=lambda x: -x[1])),
        "timeframe_breakdown": dict(sorted(tf_counts.items(), key=lambda x: -x[1])),
        "daily_pnl": daily_pnl,
    }


def print_report(result: dict):
    """Pretty print the analysis."""
    if "error" in result:
        print(f"\n{'='*60}")
        print(f"  {result['name']}: {result['error']}")
        return

    print(f"\n{'='*70}")
    print(f"  {result['name']}")
    print(f"{'='*70}")
    print(f"  Trades: {result['total_trades']}  |  Markets: {result['total_markets']}  |  Days: {result['days_active']}")
    print(f"  Date range: {result['date_range']}")

    s = result["summary"]
    print(f"\n  --- 賠率 (Odds) ---")
    print(f"  Avg combined price:    {s['avg_combined_price']}")
    print(f"  Median combined price: {s['median_combined_price']}")
    print(f"  Avg UP price:          {s['avg_up_price']}")
    print(f"  Avg DOWN price:        {s['avg_down_price']}")

    print(f"\n  --- 每邊買咗幾多 ---")
    print(f"  Total UP cost:    ${s['total_up_cost']:,.2f}  ({s['total_up_shares']:,.1f} shares)")
    print(f"  Total DOWN cost:  ${s['total_down_cost']:,.2f}  ({s['total_down_shares']:,.1f} shares)")
    print(f"  UP/DOWN ratio:    {s['up_down_cost_ratio']}")
    print(f"  Both-sides:       {s['both_sides_markets']} ({s['both_sides_pct']}%)  |  Single-side: {s['single_side_markets']}")

    print(f"\n  --- 類型 (Coins) ---")
    for coin, count in result["coin_breakdown"].items():
        pct = count / result["total_markets"] * 100
        print(f"    {coin}: {count} markets ({pct:.1f}%)")

    print(f"\n  --- Timeframes ---")
    for tf, count in result["timeframe_breakdown"].items():
        pct = count / result["total_markets"] * 100
        print(f"    {tf}: {count} markets ({pct:.1f}%)")

    print(f"\n  --- 每日 P&L (Daily) ---")
    print(f"  {'Date':<12} {'Profit':>10} {'Loss':>10} {'Net':>10} {'Markets':>8} {'Trades':>8} {'AvgOdds':>8} {'Both%':>7}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*7}")

    total_profit = 0
    total_loss = 0
    for day, d in sorted(result["daily_pnl"].items()):
        total_profit += d["profit"]
        total_loss += d["loss"]
        both_pct = d["both_sides"] / (d["both_sides"] + d["single_side"]) * 100 if (d["both_sides"] + d["single_side"]) > 0 else 0
        print(f"  {day:<12} ${d['profit']:>8.2f} ${d['loss']:>8.2f} ${d['net']:>8.2f} {d['markets']:>8} {d['trades']:>8} {d['avg_combined_price']:>8.4f} {both_pct:>6.1f}%")

    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*10}")
    print(f"  {'TOTAL':<12} ${total_profit:>8.2f} ${total_loss:>8.2f} ${total_profit+total_loss:>8.2f}")

    # Stability metric
    daily_nets = [d["net"] for d in result["daily_pnl"].values()]
    if len(daily_nets) > 1:
        from statistics import stdev
        avg_net = mean(daily_nets)
        std_net = stdev(daily_nets)
        sharpe_like = avg_net / std_net if std_net > 0 else 0
        winning_days = sum(1 for n in daily_nets if n > 0)
        print(f"\n  --- 穩定性 ---")
        print(f"  Daily avg net:  ${avg_net:.2f}")
        print(f"  Daily stdev:    ${std_net:.2f}")
        print(f"  Sharpe-like:    {sharpe_like:.3f}")
        print(f"  Winning days:   {winning_days}/{len(daily_nets)} ({winning_days/len(daily_nets)*100:.1f}%)")
        print(f"  Max daily profit: ${max(daily_nets):.2f}")
        print(f"  Max daily loss:   ${min(daily_nets):.2f}")


def main():
    all_results = {}

    for name, address in WALLETS.items():
        print(f"\n{'='*70}")
        print(f"Fetching {name} ({address})...")
        trades = fetch_trades(address, days=30)
        result = analyze_wallet(name, trades)
        all_results[name] = result
        print_report(result)

    # ─── Comparison table ───
    print(f"\n\n{'='*70}")
    print("  COMPARISON: Most Stable & Least Profitable")
    print(f"{'='*70}")
    print(f"  {'Wallet':<18} {'Markets':>8} {'Days':>6} {'Net$':>10} {'$/Day':>8} {'AvgOdds':>8} {'Both%':>7} {'Sharpe':>7}")
    print(f"  {'-'*18} {'-'*8} {'-'*6} {'-'*10} {'-'*8} {'-'*8} {'-'*7} {'-'*7}")

    for name, r in sorted(all_results.items(), key=lambda x: x[1].get("summary", {}).get("avg_combined_price", 999)):
        if "error" in r:
            continue
        s = r["summary"]
        daily_nets = [d["net"] for d in r["daily_pnl"].values()]
        avg_net = mean(daily_nets) if daily_nets else 0
        total_net = sum(daily_nets)
        from statistics import stdev
        std_net = stdev(daily_nets) if len(daily_nets) > 1 else 0
        sharpe = avg_net / std_net if std_net > 0 else 0

        print(f"  {name:<18} {r['total_markets']:>8} {r['days_active']:>6} ${total_net:>8.2f} ${avg_net:>6.2f} {s['avg_combined_price']:>8.4f} {s['both_sides_pct']:>6.1f}% {sharpe:>7.3f}")

    # Save results
    output_path = os.path.join(OUTPUT_DIR, "stable_wallet_results.json")
    # Convert sets to lists for JSON serialization
    serializable = {}
    for name, r in all_results.items():
        if "daily_pnl" in r:
            for day, d in r["daily_pnl"].items():
                d["coins"] = list(d["coins"]) if isinstance(d["coins"], set) else d["coins"]
                d["timeframes"] = list(d["timeframes"]) if isinstance(d["timeframes"], set) else d["timeframes"]
        serializable[name] = r

    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    main()
