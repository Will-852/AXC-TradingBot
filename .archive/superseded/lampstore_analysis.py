#!/usr/bin/env python3
"""
LampStore (0x56bad0e7a00913c6e35c00dce3ec7f7cd6a311d7) Polymarket MM Bot Analysis
Fetches trades + positions, calculates per-market PnL, distribution stats.
"""

import json
import time
import requests
from collections import defaultdict
from statistics import mean, stdev, median
from datetime import datetime, timezone

WALLET = "0x56bad0e7a00913c6e35c00dce3ec7f7cd6a311d7"
BASE_URL = "https://data-api.polymarket.com"

def fetch_all_trades(wallet, batch_size=100, max_trades=5000):
    """Fetch trades in batches. Handles API offset limits gracefully."""
    all_trades = []
    offset = 0
    while offset < max_trades:
        url = f"{BASE_URL}/trades?user={wallet}&limit={batch_size}&offset={offset}"
        print(f"  Fetching trades offset={offset}...")
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 400:
                print(f"  API returned 400 at offset={offset}, reached API limit. Stopping.")
                break
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"  HTTP error at offset={offset}: {e}. Stopping.")
            break
        data = resp.json()
        if not data:
            break
        all_trades.extend(data)
        if len(data) < batch_size:
            break
        offset += batch_size
        time.sleep(0.3)  # rate limit
    return all_trades

def fetch_all_positions(wallet, batch_size=500):
    """Fetch all positions."""
    all_positions = []
    offset = 0
    while True:
        url = f"{BASE_URL}/positions?user={wallet}&limit={batch_size}&offset={offset}&sizeThreshold=0"
        print(f"  Fetching positions offset={offset}...")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_positions.extend(data)
        if len(data) < batch_size:
            break
        offset += batch_size
        time.sleep(0.3)
    return all_positions

def analyze_trades(trades):
    """Analyze trade-level data."""
    print(f"\n{'='*80}")
    print(f"TRADE-LEVEL ANALYSIS ({len(trades)} trades)")
    print(f"{'='*80}")

    # Check available fields
    if trades:
        print(f"\nAvailable fields: {list(trades[0].keys())}")

    # Fee analysis
    fee_rates = [t.get('fee_rate_bps', t.get('feeRateBps', None)) for t in trades]
    fee_rates_valid = [f for f in fee_rates if f is not None]
    if fee_rates_valid:
        print(f"\n--- Fee Analysis ---")
        print(f"Trades with fee data: {len(fee_rates_valid)}")
        print(f"Fee rates: {set(fee_rates_valid)}")
        maker_count = sum(1 for f in fee_rates_valid if f == 0)
        taker_count = sum(1 for f in fee_rates_valid if f > 0)
        print(f"MAKER (fee=0): {maker_count} ({100*maker_count/len(fee_rates_valid):.1f}%)")
        print(f"TAKER (fee>0): {taker_count} ({100*taker_count/len(fee_rates_valid):.1f}%)")
    else:
        print(f"\nNo fee_rate_bps field found in trades. Checking 'maker' field...")
        # Some APIs use a 'maker' boolean
        maker_flags = [t.get('maker', None) for t in trades]
        maker_valid = [m for m in maker_flags if m is not None]
        if maker_valid:
            makers = sum(1 for m in maker_valid if m)
            print(f"Maker trades: {makers}/{len(maker_valid)} ({100*makers/len(maker_valid):.1f}%)")

    # Side analysis
    sides = [t.get('side', 'unknown') for t in trades]
    from collections import Counter
    side_counts = Counter(sides)
    print(f"\n--- Side Distribution ---")
    for side, count in side_counts.most_common():
        print(f"  {side}: {count} ({100*count/len(trades):.1f}%)")

    # Price distribution
    prices = [float(t.get('price', 0)) for t in trades if t.get('price')]
    if prices:
        print(f"\n--- Price Distribution ---")
        print(f"  Mean: {mean(prices):.4f}")
        print(f"  Median: {median(prices):.4f}")
        print(f"  Min: {min(prices):.4f}")
        print(f"  Max: {max(prices):.4f}")
        # Histogram
        buckets = [0]*10
        for p in prices:
            idx = min(int(p * 10), 9)
            buckets[idx] += 1
        print(f"\n  Price histogram:")
        for i, count in enumerate(buckets):
            bar = '#' * (count * 50 // max(buckets)) if max(buckets) > 0 else ''
            print(f"    {i*10:2d}-{(i+1)*10:2d}c: {count:5d} {bar}")

    # Size distribution
    sizes = [float(t.get('size', 0)) for t in trades if t.get('size')]
    if sizes:
        print(f"\n--- Size Distribution ---")
        print(f"  Mean: {mean(sizes):.2f}")
        print(f"  Median: {median(sizes):.2f}")
        print(f"  Min: {min(sizes):.2f}")
        print(f"  Max: {max(sizes):.2f}")
        print(f"  Total volume: {sum(sizes):.2f}")

    # Time analysis
    timestamps = []
    for t in trades:
        ts = t.get('timestamp', t.get('matchTime', t.get('createdAt', None)))
        if ts:
            timestamps.append(ts)

    if timestamps:
        # Try to parse timestamps
        try:
            if isinstance(timestamps[0], (int, float)):
                ts_sorted = sorted(timestamps)
                oldest = datetime.fromtimestamp(ts_sorted[0], tz=timezone.utc)
                newest = datetime.fromtimestamp(ts_sorted[-1], tz=timezone.utc)
            else:
                ts_sorted = sorted(timestamps)
                oldest = ts_sorted[0]
                newest = ts_sorted[-1]
            print(f"\n--- Time Range ---")
            print(f"  Oldest trade in sample: {oldest}")
            print(f"  Newest trade in sample: {newest}")
        except Exception as e:
            print(f"\n  Time parsing error: {e}")
            print(f"  Sample timestamps: {timestamps[:3]}")

    # Group trades by market (conditionId)
    by_market = defaultdict(list)
    for t in trades:
        cid = t.get('conditionId', t.get('condition_id', 'unknown'))
        by_market[cid].append(t)

    print(f"\n--- Markets in Trade Sample ---")
    print(f"  Unique markets: {len(by_market)}")

    # Analyze per-market from trades
    market_stats = []
    for cid, market_trades in by_market.items():
        title = market_trades[0].get('title', 'unknown')
        up_cost = 0
        down_cost = 0
        up_shares = 0
        down_shares = 0
        for t in market_trades:
            outcome = t.get('outcome', t.get('outcomeIndex', ''))
            size = float(t.get('size', 0))
            price = float(t.get('price', 0))
            cost = size * price
            side = t.get('side', 'BUY')

            # Determine if Up or Down
            outcome_str = str(outcome).lower()
            if outcome_str in ['yes', 'up', '0'] or t.get('outcomeIndex', -1) == 0:
                up_cost += cost
                up_shares += size
            elif outcome_str in ['no', 'down', '1'] or t.get('outcomeIndex', -1) == 1:
                down_cost += cost
                down_shares += size

        if up_shares > 0 and down_shares > 0:
            min_shares = min(up_shares, down_shares)
            up_avg = up_cost / up_shares if up_shares > 0 else 0
            down_avg = down_cost / down_shares if down_shares > 0 else 0
            combined = up_avg + down_avg
            market_stats.append({
                'title': title[:60],
                'conditionId': cid,
                'up_shares': up_shares,
                'down_shares': down_shares,
                'up_avg': up_avg,
                'down_avg': down_avg,
                'combined': combined,
                'up_cost': up_cost,
                'down_cost': down_cost,
            })

    if market_stats:
        print(f"\n--- Markets with Both Sides (from trades) ---")
        print(f"  Count: {len(market_stats)}")
        combineds = [m['combined'] for m in market_stats]
        print(f"  Combined avg price - Mean: {mean(combineds):.4f}")
        print(f"  Combined avg price - Median: {median(combineds):.4f}")
        print(f"  Combined < $1.00: {sum(1 for c in combineds if c < 1.0)}")
        print(f"  Combined > $1.00: {sum(1 for c in combineds if c > 1.0)}")
        print(f"  Combined = $1.00: {sum(1 for c in combineds if c == 1.0)}")

    return by_market, market_stats


def analyze_positions(positions):
    """Analyze position-level data for paired markets."""
    print(f"\n{'='*80}")
    print(f"POSITION-LEVEL ANALYSIS ({len(positions)} positions)")
    print(f"{'='*80}")

    if not positions:
        print("No positions returned.")
        return

    # Check fields
    print(f"\nAvailable fields: {list(positions[0].keys())}")

    # Group by conditionId (each market has Up + Down positions)
    by_condition = defaultdict(list)
    for p in positions:
        cid = p.get('conditionId', p.get('condition_id', 'unknown'))
        by_condition[cid].append(p)

    print(f"\nUnique conditionIds (markets): {len(by_condition)}")

    # Analyze paired positions
    paired_markets = []
    single_side = 0
    resolved_count = 0
    unresolved_count = 0

    for cid, pos_list in by_condition.items():
        title = pos_list[0].get('title', 'unknown')
        resolved = pos_list[0].get('redeemable', False)
        mergeable = pos_list[0].get('mergeable', False)

        if len(pos_list) >= 2:
            # Both sides present
            up_pos = None
            down_pos = None
            for p in pos_list:
                outcome = str(p.get('outcome', '')).lower()
                idx = p.get('outcomeIndex', -1)
                if outcome in ['up', 'yes'] or idx == 0:
                    up_pos = p
                elif outcome in ['down', 'no'] or idx == 1:
                    down_pos = p

            if up_pos and down_pos:
                up_size = float(up_pos.get('size', 0))
                down_size = float(down_pos.get('size', 0))
                up_avg = float(up_pos.get('avgPrice', 0))
                down_avg = float(down_pos.get('avgPrice', 0))
                up_cost = float(up_pos.get('initialValue', up_size * up_avg))
                down_cost = float(down_pos.get('initialValue', down_size * down_avg))

                combined_cost = up_cost + down_cost
                min_shares = min(up_size, down_size)

                # For resolved markets: payout = winning side shares * $1
                # For unresolved: estimate
                up_pnl = float(up_pos.get('cashPnl', 0))
                down_pnl = float(down_pos.get('cashPnl', 0))
                total_pnl = up_pnl + down_pnl

                # Realized PnL
                up_realized = float(up_pos.get('realizedPnl', 0))
                down_realized = float(down_pos.get('realizedPnl', 0))

                paired_markets.append({
                    'title': title[:70],
                    'conditionId': cid,
                    'up_size': up_size,
                    'down_size': down_size,
                    'up_avg': up_avg,
                    'down_avg': down_avg,
                    'combined_avg': up_avg + down_avg,
                    'up_cost': up_cost,
                    'down_cost': down_cost,
                    'combined_cost': combined_cost,
                    'min_shares': min_shares,
                    'cash_pnl': total_pnl,
                    'up_pnl': up_pnl,
                    'down_pnl': down_pnl,
                    'up_realized': up_realized,
                    'down_realized': down_realized,
                    'resolved': resolved,
                    'mergeable': mergeable,
                    'redeemable': pos_list[0].get('redeemable', False),
                })

                if resolved or pos_list[0].get('redeemable', False):
                    resolved_count += 1
                else:
                    unresolved_count += 1
        else:
            single_side += 1

    print(f"\nPaired markets (both sides): {len(paired_markets)}")
    print(f"Single-side only: {single_side}")
    print(f"Resolved: {resolved_count}")
    print(f"Unresolved: {unresolved_count}")

    if not paired_markets:
        print("No paired markets found for analysis.")
        return

    # ---- COMBINED ENTRY PRICE ANALYSIS ----
    print(f"\n{'='*80}")
    print("COMBINED ENTRY PRICE ANALYSIS")
    print(f"{'='*80}")

    combineds = [m['combined_avg'] for m in paired_markets]
    print(f"\n  Mean combined entry: {mean(combineds):.6f}")
    print(f"  Median combined entry: {median(combineds):.6f}")
    if len(combineds) > 1:
        print(f"  Std dev: {stdev(combineds):.6f}")
    print(f"  Min: {min(combineds):.6f}")
    print(f"  Max: {max(combineds):.6f}")

    below_1 = [m for m in paired_markets if m['combined_avg'] < 1.0]
    above_1 = [m for m in paired_markets if m['combined_avg'] > 1.0]
    equal_1 = [m for m in paired_markets if m['combined_avg'] == 1.0]

    print(f"\n  Combined < $1.00: {len(below_1)} ({100*len(below_1)/len(paired_markets):.1f}%)")
    print(f"  Combined > $1.00: {len(above_1)} ({100*len(above_1)/len(paired_markets):.1f}%)")
    print(f"  Combined = $1.00: {len(equal_1)} ({100*len(equal_1)/len(paired_markets):.1f}%)")

    # Combined entry histogram
    print(f"\n  Combined entry price histogram:")
    bucket_edges = [0.90, 0.92, 0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06, 1.08, 1.10, 1.15, 1.20]
    buckets = [0] * (len(bucket_edges))
    for c in combineds:
        placed = False
        for i in range(len(bucket_edges) - 1):
            if c < bucket_edges[i+1]:
                buckets[i] += 1
                placed = True
                break
        if not placed:
            buckets[-1] += 1

    max_b = max(buckets) if max(buckets) > 0 else 1
    for i in range(len(bucket_edges) - 1):
        bar = '#' * (buckets[i] * 50 // max_b) if buckets[i] > 0 else ''
        print(f"    {bucket_edges[i]:.2f}-{bucket_edges[i+1]:.2f}: {buckets[i]:5d} {bar}")
    print(f"    >{bucket_edges[-1]:.2f}:       {buckets[-1]:5d}")

    # ---- PnL ANALYSIS ----
    print(f"\n{'='*80}")
    print("PER-MARKET PnL ANALYSIS")
    print(f"{'='*80}")

    pnls = [m['cash_pnl'] for m in paired_markets]
    print(f"\n  Total PnL (all positions): ${sum(pnls):.2f}")
    print(f"  Mean PnL per market: ${mean(pnls):.4f}")
    if len(pnls) > 1:
        print(f"  Median PnL: ${median(pnls):.4f}")
        print(f"  Std Dev PnL: ${stdev(pnls):.4f}")
    print(f"  Min PnL: ${min(pnls):.4f}")
    print(f"  Max PnL: ${max(pnls):.4f}")

    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]

    print(f"\n  Winners: {len(winners)} ({100*len(winners)/len(pnls):.1f}%)")
    print(f"  Losers: {len(losers)} ({100*len(losers)/len(pnls):.1f}%)")
    print(f"  Breakeven: {len(breakeven)} ({100*len(breakeven)/len(pnls):.1f}%)")

    if winners:
        print(f"  Avg winner: ${mean(winners):.4f}")
    if losers:
        print(f"  Avg loser: ${mean(losers):.4f}")

    # PnL by combined entry
    if below_1:
        below_pnls = [m['cash_pnl'] for m in below_1]
        print(f"\n  --- Combined < $1.00 ({len(below_1)} markets) ---")
        print(f"    Total PnL: ${sum(below_pnls):.2f}")
        print(f"    Avg PnL: ${mean(below_pnls):.4f}")
        if len(below_pnls) > 1:
            print(f"    Std Dev: ${stdev(below_pnls):.4f}")
        print(f"    Win rate: {100*sum(1 for p in below_pnls if p > 0)/len(below_pnls):.1f}%")

    if above_1:
        above_pnls = [m['cash_pnl'] for m in above_1]
        print(f"\n  --- Combined > $1.00 ({len(above_1)} markets) ---")
        print(f"    Total PnL: ${sum(above_pnls):.2f}")
        print(f"    Avg PnL: ${mean(above_pnls):.4f}")
        if len(above_pnls) > 1:
            print(f"    Std Dev: ${stdev(above_pnls):.4f}")
        print(f"    Win rate: {100*sum(1 for p in above_pnls if p > 0)/len(above_pnls):.1f}%")

    # PnL histogram
    print(f"\n  PnL histogram (per market):")
    pnl_edges = [-50, -20, -10, -5, -2, -1, -0.5, 0, 0.5, 1, 2, 5, 10, 20, 50]
    pnl_buckets = [0] * (len(pnl_edges))
    for p in pnls:
        placed = False
        for i in range(len(pnl_edges) - 1):
            if p < pnl_edges[i+1]:
                pnl_buckets[i] += 1
                placed = True
                break
        if not placed:
            pnl_buckets[-1] += 1
    max_pb = max(pnl_buckets) if max(pnl_buckets) > 0 else 1
    for i in range(len(pnl_edges) - 1):
        bar = '#' * (pnl_buckets[i] * 50 // max_pb) if pnl_buckets[i] > 0 else ''
        print(f"    ${pnl_edges[i]:7.1f} to ${pnl_edges[i+1]:7.1f}: {pnl_buckets[i]:5d} {bar}")
    print(f"    >${pnl_edges[-1]:7.1f}:              {pnl_buckets[-1]:5d}")

    # Sharpe-like ratio per trade
    if len(pnls) > 1 and stdev(pnls) > 0:
        sharpe_per_trade = mean(pnls) / stdev(pnls)
        print(f"\n  Sharpe per trade (mean/stdev): {sharpe_per_trade:.4f}")

    # ---- TOP/BOTTOM MARKETS ----
    print(f"\n{'='*80}")
    print("TOP 10 BEST MARKETS")
    print(f"{'='*80}")
    sorted_best = sorted(paired_markets, key=lambda m: m['cash_pnl'], reverse=True)[:10]
    for m in sorted_best:
        print(f"  PnL: ${m['cash_pnl']:8.2f} | Combined: {m['combined_avg']:.4f} | {m['title']}")

    print(f"\n{'='*80}")
    print("TOP 10 WORST MARKETS")
    print(f"{'='*80}")
    sorted_worst = sorted(paired_markets, key=lambda m: m['cash_pnl'])[:10]
    for m in sorted_worst:
        print(f"  PnL: ${m['cash_pnl']:8.2f} | Combined: {m['combined_avg']:.4f} | {m['title']}")

    # ---- BALANCE ANALYSIS ----
    print(f"\n{'='*80}")
    print("BALANCE ANALYSIS (Up vs Down sizing)")
    print(f"{'='*80}")
    ratios = []
    for m in paired_markets:
        if m['down_size'] > 0:
            ratio = m['up_size'] / m['down_size']
            ratios.append(ratio)
    if ratios:
        print(f"  Up/Down size ratio - Mean: {mean(ratios):.4f}")
        print(f"  Up/Down size ratio - Median: {median(ratios):.4f}")
        print(f"  Perfectly balanced (0.95-1.05): {sum(1 for r in ratios if 0.95 <= r <= 1.05)}/{len(ratios)}")

    # ---- STRATEGY EVOLUTION (if we have timestamps from trades) ----
    # This will be done in trade analysis section

    return paired_markets


def analyze_strategy_evolution(trades):
    """Compare oldest vs newest trades."""
    print(f"\n{'='*80}")
    print("STRATEGY EVOLUTION OVER TIME")
    print(f"{'='*80}")

    if not trades:
        print("No trades for evolution analysis.")
        return

    # Sort by timestamp
    def get_ts(t):
        ts = t.get('timestamp', t.get('matchTime', t.get('createdAt', 0)))
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
            except:
                return 0
        return float(ts) if ts else 0

    sorted_trades = sorted(trades, key=get_ts)

    # Split into quartiles
    n = len(sorted_trades)
    q1 = sorted_trades[:n//4]
    q4 = sorted_trades[3*n//4:]

    for label, quarter in [("OLDEST 25%", q1), ("NEWEST 25%", q4)]:
        prices = [float(t.get('price', 0)) for t in quarter if t.get('price')]
        sizes = [float(t.get('size', 0)) for t in quarter if t.get('size')]
        ts_val = get_ts(quarter[0]) if quarter else 0
        ts_end = get_ts(quarter[-1]) if quarter else 0

        print(f"\n  --- {label} ({len(quarter)} trades) ---")
        if ts_val > 0:
            print(f"    Time range: {datetime.fromtimestamp(ts_val, tz=timezone.utc)} to {datetime.fromtimestamp(ts_end, tz=timezone.utc)}")
        if prices:
            print(f"    Avg price: {mean(prices):.4f}")
            print(f"    Median price: {median(prices):.4f}")
        if sizes:
            print(f"    Avg size: {mean(sizes):.2f}")
            print(f"    Median size: {median(sizes):.2f}")
            print(f"    Total volume: {sum(sizes):.2f}")

        # Group by market and check combined entries
        by_market = defaultdict(list)
        for t in quarter:
            cid = t.get('conditionId', 'unknown')
            by_market[cid].append(t)

        combined_entries = []
        for cid, mtrades in by_market.items():
            up_cost = 0
            down_cost = 0
            up_shares = 0
            down_shares = 0
            for t in mtrades:
                outcome = str(t.get('outcome', t.get('outcomeIndex', ''))).lower()
                idx = t.get('outcomeIndex', -1)
                size = float(t.get('size', 0))
                price = float(t.get('price', 0))
                if outcome in ['up', 'yes', '0'] or idx == 0:
                    up_cost += size * price
                    up_shares += size
                elif outcome in ['down', 'no', '1'] or idx == 1:
                    down_cost += size * price
                    down_shares += size
            if up_shares > 0 and down_shares > 0:
                combined = (up_cost/up_shares) + (down_cost/down_shares)
                combined_entries.append(combined)

        if combined_entries:
            print(f"    Markets with both sides: {len(combined_entries)}")
            print(f"    Avg combined entry: {mean(combined_entries):.4f}")
            print(f"    < $1.00: {sum(1 for c in combined_entries if c < 1.0)}")
            print(f"    > $1.00: {sum(1 for c in combined_entries if c > 1.0)}")


def main():
    print("="*80)
    print("LampStore Polymarket MM Bot Analysis")
    print(f"Wallet: {WALLET}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("="*80)

    # Step 1: Fetch trades
    print("\n[1/3] Fetching trades...")
    trades = fetch_all_trades(WALLET, batch_size=100, max_trades=5000)
    print(f"  Total trades fetched: {len(trades)}")

    # Save raw data
    with open('/Users/wai/projects/axc-trading/analysis/lampstore_trades.json', 'w') as f:
        json.dump(trades, f, indent=2)
    print(f"  Saved to lampstore_trades.json")

    # Step 2: Fetch positions
    print("\n[2/3] Fetching positions...")
    positions = fetch_all_positions(WALLET)
    print(f"  Total positions fetched: {len(positions)}")

    with open('/Users/wai/projects/axc-trading/analysis/lampstore_positions.json', 'w') as f:
        json.dump(positions, f, indent=2)
    print(f"  Saved to lampstore_positions.json")

    # Step 3: Analysis
    print("\n[3/3] Running analysis...")

    # Trade analysis
    by_market, market_stats = analyze_trades(trades)

    # Position analysis
    paired_markets = analyze_positions(positions)

    # Strategy evolution
    analyze_strategy_evolution(trades)

    # ---- FINAL SUMMARY ----
    print(f"\n{'='*80}")
    print("EXECUTIVE SUMMARY")
    print(f"{'='*80}")
    print(f"  Total trades fetched: {len(trades)}")
    print(f"  Total positions: {len(positions)}")
    if paired_markets:
        pnls = [m['cash_pnl'] for m in paired_markets]
        combineds = [m['combined_avg'] for m in paired_markets]
        print(f"  Paired markets analyzed: {len(paired_markets)}")
        print(f"  Total PnL (positions): ${sum(pnls):.2f}")
        print(f"  Avg PnL per market: ${mean(pnls):.4f}")
        if len(pnls) > 1:
            print(f"  PnL Std Dev: ${stdev(pnls):.4f}")
            sharpe = mean(pnls) / stdev(pnls) if stdev(pnls) > 0 else 0
            print(f"  Sharpe per trade: {sharpe:.4f}")
        print(f"  Win rate: {100*sum(1 for p in pnls if p > 0)/len(pnls):.1f}%")
        print(f"  Avg combined entry: {mean(combineds):.4f}")
        print(f"  Markets < $1.00 combined: {sum(1 for c in combineds if c < 1.0)}/{len(combineds)}")

    print(f"\n{'='*80}")
    print("Analysis complete.")


if __name__ == '__main__':
    main()
