#!/usr/bin/env python3
"""
Polymarket Anon wallet analysis
Wallet: 0xe38b7a6553cbcac3bf6d9e22c83cdce092951fdc
Strategy: Buy BOTH sides of BTC/ETH/XRP 15-min Up/Down markets
"""

import json
import requests
import time
from collections import defaultdict
from datetime import datetime, timezone

WALLET = "0xe38b7a6553cbcac3bf6d9e22c83cdce092951fdc"
BASE_URL = "https://data-api.polymarket.com"


def fetch_all_trades():
    """Fetch all trades with pagination."""
    all_trades = []
    offset = 0
    limit = 100
    while True:
        url = f"{BASE_URL}/trades?user={WALLET}&limit={limit}&offset={offset}"
        resp = requests.get(url, timeout=30)
        data = resp.json()
        if not data:
            break
        all_trades.extend(data)
        print(f"  Fetched {len(data)} trades at offset {offset}, total: {len(all_trades)}")
        if len(data) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return all_trades


def fetch_all_positions():
    """Fetch all positions."""
    all_positions = []
    offset = 0
    limit = 500
    while True:
        url = f"{BASE_URL}/positions?user={WALLET}&limit={limit}&offset={offset}&sizeThreshold=0"
        resp = requests.get(url, timeout=30)
        data = resp.json()
        if not data:
            break
        all_positions.extend(data)
        print(f"  Fetched {len(data)} positions at offset {offset}, total: {len(all_positions)}")
        if len(data) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return all_positions


def analyze_positions(positions):
    """Group positions by market (conditionId) and calculate per-market PnL."""
    # Group by conditionId (each market has Up + Down)
    markets = defaultdict(dict)

    for pos in positions:
        cid = pos['conditionId']
        outcome = pos['outcome']
        markets[cid][outcome] = pos

    results = []
    for cid, sides in markets.items():
        up = sides.get('Up', {})
        down = sides.get('Down', {})

        title = up.get('title', down.get('title', 'Unknown'))
        slug = up.get('slug', down.get('slug', ''))

        up_cost = float(up.get('initialValue', 0))
        down_cost = float(down.get('initialValue', 0))
        up_bought = float(up.get('totalBought', 0))
        down_bought = float(down.get('totalBought', 0))
        up_size = float(up.get('size', 0))
        down_size = float(down.get('size', 0))
        up_avg = float(up.get('avgPrice', 0))
        down_avg = float(down.get('avgPrice', 0))
        up_cur = float(up.get('curPrice', 0))
        down_cur = float(down.get('curPrice', 0))
        up_pnl = float(up.get('cashPnl', 0))
        down_pnl = float(down.get('cashPnl', 0))

        combined_cost = up_cost + down_cost
        combined_bought = up_bought + down_bought

        # Determine winner
        if up_cur == 1:
            winner = 'Up'
            payout = up_size  # winning side pays $1 per share
        elif down_cur == 1:
            winner = 'Down'
            payout = down_size
        else:
            winner = 'Unresolved'
            payout = 0

        actual_pnl = up_pnl + down_pnl

        # Combined price = total cost / shares (roughly)
        # Since buying both sides: combined entry = up_avg + down_avg (per $1 of shares)
        # But shares differ. Better: combined_cost / max(up_size, down_size)
        # Actually: if you buy N_up shares at p_up and N_down shares at p_down,
        # cost = N_up * p_up + N_down * p_down
        # If you size equally: cost ~= N * (p_up + p_down)
        # combined_price_per_unit = combined_cost / avg_shares

        # Determine asset type
        if 'Bitcoin' in title or 'btc' in slug:
            asset = 'BTC'
        elif 'Ethereum' in title or 'eth' in slug:
            asset = 'ETH'
        elif 'XRP' in title or 'xrp' in slug:
            asset = 'XRP'
        else:
            asset = 'OTHER'

        results.append({
            'title': title,
            'slug': slug,
            'asset': asset,
            'conditionId': cid,
            'up_cost': up_cost,
            'down_cost': down_cost,
            'combined_cost': combined_cost,
            'up_bought': up_bought,
            'down_bought': down_bought,
            'combined_bought': combined_bought,
            'up_size': up_size,
            'down_size': down_size,
            'up_avg': up_avg,
            'down_avg': down_avg,
            'up_pnl': up_pnl,
            'down_pnl': down_pnl,
            'actual_pnl': actual_pnl,
            'winner': winner,
            'payout': payout,
            'combined_avg': up_avg + down_avg,  # key metric: if < 1, guaranteed profit
        })

    return results


def analyze_trades(trades):
    """Analyze trade timing and pricing."""
    # Group by slug (market)
    by_market = defaultdict(list)
    for i, t in enumerate(trades):
        if not isinstance(t, dict):
            print(f"  WARNING: trade[{i}] is {type(t)}: {repr(t)[:100]}")
            continue
        by_market[t['slug']].append(t)

    timing_data = []
    pricing_data = []

    for slug, market_trades in by_market.items():
        # Sort by timestamp
        market_trades.sort(key=lambda x: x['timestamp'])

        # Extract market open time from slug
        # slug format: btc-updown-15m-1773907200
        parts = slug.split('-')
        try:
            market_open_ts = int(parts[-1])
        except (ValueError, IndexError):
            continue

        # Separate Up and Down trades
        up_trades = [t for t in market_trades if t['outcome'] == 'Up']
        down_trades = [t for t in market_trades if t['outcome'] == 'Down']

        first_trade_ts = market_trades[0]['timestamp']
        delay_from_open = first_trade_ts - market_open_ts

        # Time gap between first Up and first Down
        if up_trades and down_trades:
            first_up_ts = up_trades[0]['timestamp']
            first_down_ts = down_trades[0]['timestamp']
            gap = abs(first_up_ts - first_down_ts)
        else:
            gap = None

        timing_data.append({
            'slug': slug,
            'market_open': market_open_ts,
            'first_trade': first_trade_ts,
            'delay_seconds': delay_from_open,
            'up_down_gap': gap,
            'num_trades': len(market_trades),
            'num_up_trades': len(up_trades),
            'num_down_trades': len(down_trades),
        })

        for t in market_trades:
            pricing_data.append({
                'slug': slug,
                'outcome': t['outcome'],
                'price': float(t['price']),
                'size': float(t['size']),
                'side': t['side'],
                'timestamp': t['timestamp'],
            })

    return timing_data, pricing_data


def print_report(market_results, timing_data, pricing_data, trades):
    """Print comprehensive analysis report."""

    print("\n" + "=" * 100)
    print("POLYMARKET ANON WALLET ANALYSIS")
    print(f"Wallet: {WALLET}")
    print("=" * 100)

    # ─── OVERVIEW ───
    resolved = [m for m in market_results if m['winner'] != 'Unresolved']
    unresolved = [m for m in market_results if m['winner'] == 'Unresolved']

    total_pnl = sum(m['actual_pnl'] for m in resolved)
    total_cost = sum(m['combined_cost'] for m in resolved)

    print(f"\n{'─' * 60}")
    print("OVERVIEW")
    print(f"{'─' * 60}")
    print(f"Total markets:     {len(market_results)}")
    print(f"Resolved markets:  {len(resolved)}")
    print(f"Unresolved:        {len(unresolved)}")
    print(f"Total invested:    ${total_cost:,.2f}")
    print(f"Total PnL:         ${total_pnl:,.2f}")
    print(f"ROI:               {total_pnl/total_cost*100:.2f}%" if total_cost > 0 else "N/A")

    # ─── BY ASSET ───
    print(f"\n{'─' * 60}")
    print("PnL BY ASSET TYPE")
    print(f"{'─' * 60}")
    by_asset = defaultdict(lambda: {'count': 0, 'pnl': 0, 'cost': 0})
    for m in resolved:
        by_asset[m['asset']]['count'] += 1
        by_asset[m['asset']]['pnl'] += m['actual_pnl']
        by_asset[m['asset']]['cost'] += m['combined_cost']

    for asset in sorted(by_asset.keys()):
        d = by_asset[asset]
        roi = d['pnl']/d['cost']*100 if d['cost'] > 0 else 0
        print(f"  {asset:5s}: {d['count']:4d} markets | PnL: ${d['pnl']:>10,.2f} | Cost: ${d['cost']:>10,.2f} | ROI: {roi:.2f}%")

    # ─── COMBINED ENTRY PRICE DISTRIBUTION ───
    print(f"\n{'─' * 60}")
    print("COMBINED ENTRY PRICE (Up_avg + Down_avg) DISTRIBUTION")
    print("If combined < $1.00 = guaranteed profit per unit")
    print(f"{'─' * 60}")

    combined_prices = [m['combined_avg'] for m in resolved if m['combined_avg'] > 0]

    buckets = [
        (0, 0.80, "< $0.80"),
        (0.80, 0.85, "$0.80-$0.85"),
        (0.85, 0.90, "$0.85-$0.90"),
        (0.90, 0.95, "$0.90-$0.95"),
        (0.95, 0.98, "$0.95-$0.98"),
        (0.98, 1.00, "$0.98-$1.00"),
        (1.00, 1.00001, "= $1.00"),
        (1.00001, 1.02, "$1.00-$1.02"),
        (1.02, 1.05, "$1.02-$1.05"),
        (1.05, 1.10, "$1.05-$1.10"),
        (1.10, 2.00, "> $1.10"),
    ]

    for lo, hi, label in buckets:
        count = sum(1 for p in combined_prices if lo <= p < hi)
        if count > 0:
            bar = "█" * count
            print(f"  {label:12s}: {count:4d} {bar}")

    guaranteed_profit = sum(1 for p in combined_prices if p < 1.0)
    loss_territory = sum(1 for p in combined_prices if p >= 1.0)
    print(f"\n  Combined < $1.00 (guaranteed profit): {guaranteed_profit} ({guaranteed_profit/len(combined_prices)*100:.1f}%)")
    print(f"  Combined >= $1.00 (potential loss):    {loss_territory} ({loss_territory/len(combined_prices)*100:.1f}%)")

    if combined_prices:
        avg_combined = sum(combined_prices) / len(combined_prices)
        min_combined = min(combined_prices)
        max_combined = max(combined_prices)
        print(f"\n  Average combined price: ${avg_combined:.4f}")
        print(f"  Min combined price:     ${min_combined:.4f}")
        print(f"  Max combined price:     ${max_combined:.4f}")
        print(f"  Avg guaranteed spread:  ${1.0 - avg_combined:.4f} per unit")

    # ─── WIN/LOSS ANALYSIS ───
    print(f"\n{'─' * 60}")
    print("WIN/LOSS ANALYSIS (resolved markets)")
    print(f"{'─' * 60}")

    winners = [m for m in resolved if m['actual_pnl'] > 0]
    losers = [m for m in resolved if m['actual_pnl'] < 0]
    breakeven = [m for m in resolved if m['actual_pnl'] == 0]

    print(f"  Winners:   {len(winners)} ({len(winners)/len(resolved)*100:.1f}%)")
    print(f"  Losers:    {len(losers)} ({len(losers)/len(resolved)*100:.1f}%)")
    print(f"  Breakeven: {len(breakeven)} ({len(breakeven)/len(resolved)*100:.1f}%)")

    if winners:
        avg_win = sum(m['actual_pnl'] for m in winners) / len(winners)
        print(f"  Avg win:   ${avg_win:.2f}")
    if losers:
        avg_loss = sum(m['actual_pnl'] for m in losers) / len(losers)
        print(f"  Avg loss:  ${avg_loss:.2f}")

    avg_pnl = total_pnl / len(resolved) if resolved else 0
    print(f"  Avg PnL per market: ${avg_pnl:.2f}")

    # ─── TOP WINNERS ───
    print(f"\n{'─' * 60}")
    print("TOP 10 WINNERS")
    print(f"{'─' * 60}")
    sorted_by_pnl = sorted(resolved, key=lambda x: x['actual_pnl'], reverse=True)
    print(f"  {'Title':<55s} {'PnL':>10s} {'Cost':>10s} {'CombAvg':>8s} {'Winner':>6s}")
    for m in sorted_by_pnl[:10]:
        short_title = m['title'][:52] + "..." if len(m['title']) > 55 else m['title']
        print(f"  {short_title:<55s} ${m['actual_pnl']:>9,.2f} ${m['combined_cost']:>9,.2f} {m['combined_avg']:.4f} {m['winner']:>6s}")

    # ─── TOP LOSERS ───
    print(f"\n{'─' * 60}")
    print("TOP 10 LOSERS")
    print(f"{'─' * 60}")
    print(f"  {'Title':<55s} {'PnL':>10s} {'Cost':>10s} {'CombAvg':>8s} {'Winner':>6s}")
    for m in sorted_by_pnl[-10:]:
        short_title = m['title'][:52] + "..." if len(m['title']) > 55 else m['title']
        print(f"  {short_title:<55s} ${m['actual_pnl']:>9,.2f} ${m['combined_cost']:>9,.2f} {m['combined_avg']:.4f} {m['winner']:>6s}")

    # ─── TIMING ANALYSIS ───
    print(f"\n{'─' * 60}")
    print("TIMING ANALYSIS")
    print(f"{'─' * 60}")

    delays = [t['delay_seconds'] for t in timing_data if t['delay_seconds'] >= 0]
    gaps = [t['up_down_gap'] for t in timing_data if t['up_down_gap'] is not None]

    if delays:
        avg_delay = sum(delays) / len(delays)
        min_delay = min(delays)
        max_delay = max(delays)

        print(f"  Delay from market open to first trade:")
        print(f"    Average: {avg_delay:.1f} seconds")
        print(f"    Min:     {min_delay} seconds")
        print(f"    Max:     {max_delay} seconds")
        print(f"    Median:  {sorted(delays)[len(delays)//2]} seconds")

        # Delay distribution
        delay_buckets = [(0, 5), (5, 10), (10, 30), (30, 60), (60, 120), (120, 300), (300, 600), (600, 99999)]
        print(f"\n  Delay distribution:")
        for lo, hi in delay_buckets:
            count = sum(1 for d in delays if lo <= d < hi)
            if count > 0:
                label = f"{lo}-{hi}s" if hi < 99999 else f">{lo}s"
                bar = "█" * min(count, 80)
                print(f"    {label:>10s}: {count:4d} {bar}")

    if gaps:
        avg_gap = sum(gaps) / len(gaps)
        print(f"\n  Gap between Up buy and Down buy:")
        print(f"    Average: {avg_gap:.1f} seconds")
        print(f"    Min:     {min(gaps)} seconds")
        print(f"    Max:     {max(gaps)} seconds")
        print(f"    Median:  {sorted(gaps)[len(gaps)//2]} seconds")

        simultaneous = sum(1 for g in gaps if g <= 2)
        sequential = sum(1 for g in gaps if g > 2)
        print(f"    Simultaneous (<=2s): {simultaneous} ({simultaneous/len(gaps)*100:.1f}%)")
        print(f"    Sequential (>2s):    {sequential} ({sequential/len(gaps)*100:.1f}%)")

    # ─── PRICING ANALYSIS ───
    print(f"\n{'─' * 60}")
    print("PRICING ANALYSIS")
    print(f"{'─' * 60}")

    up_prices = [p['price'] for p in pricing_data if p['outcome'] == 'Up']
    down_prices = [p['price'] for p in pricing_data if p['outcome'] == 'Down']

    if up_prices:
        print(f"  Up side prices:")
        print(f"    Average: ${sum(up_prices)/len(up_prices):.4f}")
        print(f"    Min:     ${min(up_prices):.4f}")
        print(f"    Max:     ${max(up_prices):.4f}")

    if down_prices:
        print(f"  Down side prices:")
        print(f"    Average: ${sum(down_prices)/len(down_prices):.4f}")
        print(f"    Min:     ${min(down_prices):.4f}")
        print(f"    Max:     ${max(down_prices):.4f}")

    # Price distribution
    all_prices = [p['price'] for p in pricing_data]
    price_buckets = [
        (0, 0.30, "< $0.30"),
        (0.30, 0.40, "$0.30-$0.40"),
        (0.40, 0.50, "$0.40-$0.50"),
        (0.50, 0.60, "$0.50-$0.60"),
        (0.60, 0.70, "$0.60-$0.70"),
        (0.70, 0.80, "$0.70-$0.80"),
        (0.80, 1.00, "$0.80-$1.00"),
    ]
    print(f"\n  Price distribution (all trades):")
    for lo, hi, label in price_buckets:
        count = sum(1 for p in all_prices if lo <= p < hi)
        if count > 0:
            bar = "█" * min(count, 60)
            print(f"    {label:12s}: {count:4d} {bar}")

    # ─── FEE / MAKER VS TAKER ───
    print(f"\n{'─' * 60}")
    print("FEE / MAKER VS TAKER ANALYSIS")
    print(f"{'─' * 60}")

    # Check if fee_rate_bps exists in trades
    has_fee = any('fee_rate_bps' in t for t in trades)
    if has_fee:
        fee_rates = [t.get('fee_rate_bps', 'N/A') for t in trades]
        fee_counts = defaultdict(int)
        for f in fee_rates:
            fee_counts[f] += 1
        for rate, count in sorted(fee_counts.items()):
            role = "MAKER" if rate == 0 else "TAKER" if rate and rate > 0 else "UNKNOWN"
            print(f"    Fee rate {rate} bps: {count} trades ({role})")
    else:
        print("  fee_rate_bps field not found in trade data")
        # Check what fields exist
        if trades:
            print(f"  Available fields: {list(trades[0].keys())}")

    # ─── SIZE ANALYSIS ───
    print(f"\n{'─' * 60}")
    print("POSITION SIZE ANALYSIS")
    print(f"{'─' * 60}")

    costs = [m['combined_cost'] for m in resolved]
    if costs:
        avg_cost = sum(costs) / len(costs)
        print(f"  Average cost per market: ${avg_cost:,.2f}")
        print(f"  Min cost per market:     ${min(costs):,.2f}")
        print(f"  Max cost per market:     ${max(costs):,.2f}")
        print(f"  Total deployed capital:  ${sum(costs):,.2f}")

    # ─── THE EDGE ───
    print(f"\n{'=' * 100}")
    print("EDGE ANALYSIS: HOW DOES THIS STRATEGY ACTUALLY WORK?")
    print(f"{'=' * 100}")

    if combined_prices:
        avg_spread = 1.0 - avg_combined
        print(f"""
  MECHANISM:
  - Buy BOTH Up and Down on every BTC/ETH/XRP 15-minute market
  - One side ALWAYS resolves to $1.00, the other to $0.00
  - If combined entry price < $1.00, the payout ($1.00) exceeds cost = GUARANTEED PROFIT
  - If combined entry price > $1.00, the payout ($1.00) is less than cost = LOSS

  THIS WALLET'S NUMBERS:
  - Average combined entry: ${avg_combined:.4f}
  - Average spread (edge):  ${avg_spread:.4f} per unit
  - Markets with combined < $1.00: {guaranteed_profit}/{len(combined_prices)} ({guaranteed_profit/len(combined_prices)*100:.1f}%)
  - Markets with combined >= $1.00: {loss_territory}/{len(combined_prices)} ({loss_territory/len(combined_prices)*100:.1f}%)
  - Total PnL from resolved:       ${total_pnl:,.2f}
  - Average PnL per market:        ${avg_pnl:.2f}

  KEY INSIGHT:
  The edge comes from the market's pricing inefficiency. In a perfect market,
  Up + Down = $1.00 (no arbitrage). But in Polymarket's 15-min markets,
  the combined price is often < $1.00, creating a risk-free spread.

  The bot's job is simply to:
  1. Detect when Up_price + Down_price < $1.00
  2. Buy both sides
  3. Wait for resolution
  4. Collect the guaranteed spread
""")

    # Return data for further analysis
    return market_results, timing_data, pricing_data


def main():
    print("Fetching trades...")
    trades = fetch_all_trades()
    print(f"Total trades: {len(trades)}")

    print("\nFetching positions...")
    positions = fetch_all_positions()
    print(f"Total positions: {len(positions)}")

    # Analyze positions for PnL
    market_results = analyze_positions(positions)

    # Analyze trades for timing/pricing
    timing_data, pricing_data = analyze_trades(trades)

    # Print report
    print_report(market_results, timing_data, pricing_data, trades)

    # Save raw data for further analysis
    with open('/Users/wai/projects/axc-trading/analysis/anon_trades.json', 'w') as f:
        json.dump(trades, f, indent=2)
    with open('/Users/wai/projects/axc-trading/analysis/anon_positions.json', 'w') as f:
        json.dump(positions, f, indent=2)
    with open('/Users/wai/projects/axc-trading/analysis/anon_market_results.json', 'w') as f:
        json.dump(market_results, f, indent=2)

    print(f"\nRaw data saved to /Users/wai/projects/axc-trading/analysis/")


if __name__ == '__main__':
    main()
