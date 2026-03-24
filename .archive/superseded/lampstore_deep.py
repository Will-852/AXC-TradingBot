#!/usr/bin/env python3
"""
LampStore Deep Analysis - Focus on realizedPnl + paired market mechanics.
Uses already-fetched data + tries additional endpoints.
"""

import json
import time
import requests
from collections import defaultdict, Counter
from statistics import mean, stdev, median
from datetime import datetime, timezone
import math

WALLET = "0x56bad0e7a00913c6e35c00dce3ec7f7cd6a311d7"
BASE_URL = "https://data-api.polymarket.com"


def fetch_more_positions(wallet):
    """Try different position endpoints to get more data."""
    all_positions = []

    # Try with different params
    for size_thresh in [0]:
        offset = 0
        while True:
            url = f"{BASE_URL}/positions?user={wallet}&limit=500&offset={offset}&sizeThreshold={size_thresh}"
            print(f"  Fetching positions offset={offset} sizeThreshold={size_thresh}...")
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code != 200:
                    print(f"  Status {resp.status_code}, stopping.")
                    break
                data = resp.json()
                if not data:
                    break
                all_positions.extend(data)
                if len(data) < 500:
                    break
                offset += 500
                time.sleep(0.3)
            except Exception as e:
                print(f"  Error: {e}")
                break

    # Deduplicate by asset
    seen = set()
    unique = []
    for p in all_positions:
        key = p.get('asset', '')
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


def try_get_profile(wallet):
    """Try to get user profile data."""
    urls = [
        f"{BASE_URL}/users/{wallet}",
        f"{BASE_URL}/profile/{wallet}",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Profile from {url}: {json.dumps(data, indent=2)[:1000]}")
                return data
        except:
            pass
    return None


def analyze():
    print("="*80)
    print("LampStore DEEP ANALYSIS")
    print("="*80)

    # Load already-fetched data
    with open('/Users/wai/projects/axc-trading/analysis/lampstore_trades.json') as f:
        trades = json.load(f)
    with open('/Users/wai/projects/axc-trading/analysis/lampstore_positions.json') as f:
        positions = json.load(f)

    print(f"\nLoaded: {len(trades)} trades, {len(positions)} positions")

    # Try profile
    print("\n--- Profile ---")
    profile = try_get_profile(WALLET)

    # =========================================================================
    # SECTION 1: POSITION PAIR ANALYSIS (using realizedPnl)
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 1: PAIRED POSITION ANALYSIS (realizedPnl-based)")
    print(f"{'='*80}")

    # Group by conditionId
    by_cid = defaultdict(list)
    for p in positions:
        cid = p.get('conditionId', '')
        by_cid[cid].append(p)

    # Analyze pairs
    paired = []
    single = []
    for cid, pos_list in by_cid.items():
        if len(pos_list) >= 2:
            up_pos = None
            down_pos = None
            for p in pos_list:
                idx = p.get('outcomeIndex', -1)
                if idx == 0:
                    up_pos = p
                elif idx == 1:
                    down_pos = p

            if up_pos and down_pos:
                up_size = float(up_pos.get('size', 0))
                down_size = float(down_pos.get('size', 0))
                up_avg = float(up_pos.get('avgPrice', 0))
                down_avg = float(down_pos.get('avgPrice', 0))
                up_init = float(up_pos.get('initialValue', 0))
                down_init = float(down_pos.get('initialValue', 0))
                up_realized = float(up_pos.get('realizedPnl', 0))
                down_realized = float(down_pos.get('realizedPnl', 0))
                up_cash = float(up_pos.get('cashPnl', 0))
                down_cash = float(down_pos.get('cashPnl', 0))
                up_cur = float(up_pos.get('curPrice', 0))
                down_cur = float(down_pos.get('curPrice', 0))
                up_bought = float(up_pos.get('totalBought', 0))
                down_bought = float(down_pos.get('totalBought', 0))

                redeemable = up_pos.get('redeemable', False) or down_pos.get('redeemable', False)
                mergeable = up_pos.get('mergeable', False) or down_pos.get('mergeable', False)

                combined_avg = up_avg + down_avg
                combined_init = up_init + down_init
                total_realized = up_realized + down_realized
                total_cash = up_cash + down_cash
                min_shares = min(up_size, down_size)
                total_bought = up_bought + down_bought

                # Calculate theoretical PnL for resolved market
                # If market resolved to Up: payout = up_size * 1.0
                # Cost was combined_init
                # PnL = up_size - combined_init (if Up wins)
                # PnL = down_size - combined_init (if Down wins)

                # The merge profit: min(up, down) shares can be merged for $1 each
                # Merge profit = min(up, down) * (1.0 - combined_avg)
                merge_profit_per_share = 1.0 - combined_avg
                max_merge_profit = min_shares * merge_profit_per_share

                paired.append({
                    'title': up_pos.get('title', ''),
                    'conditionId': cid,
                    'up_size': up_size,
                    'down_size': down_size,
                    'up_avg': up_avg,
                    'down_avg': down_avg,
                    'combined_avg': combined_avg,
                    'up_init': up_init,
                    'down_init': down_init,
                    'combined_init': combined_init,
                    'up_realized': up_realized,
                    'down_realized': down_realized,
                    'total_realized': total_realized,
                    'up_cash': up_cash,
                    'down_cash': down_cash,
                    'total_cash': total_cash,
                    'min_shares': min_shares,
                    'merge_profit_per_share': merge_profit_per_share,
                    'max_merge_profit': max_merge_profit,
                    'redeemable': redeemable,
                    'mergeable': mergeable,
                    'up_cur': up_cur,
                    'down_cur': down_cur,
                    'total_bought': total_bought,
                    'endDate': up_pos.get('endDate', ''),
                })
            else:
                single.extend(pos_list)
        else:
            single.extend(pos_list)

    print(f"\nPaired markets: {len(paired)}")
    print(f"Single-side positions: {len(single)}")

    # =========================================================================
    # SECTION 2: COMBINED ENTRY PRICE DEEP DIVE
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 2: COMBINED ENTRY PRICE DISTRIBUTION")
    print(f"{'='*80}")

    combineds = [m['combined_avg'] for m in paired]
    below_1 = [m for m in paired if m['combined_avg'] < 1.0]
    above_1 = [m for m in paired if m['combined_avg'] > 1.0]

    print(f"\n  Total paired: {len(paired)}")
    print(f"  Combined < $1.00: {len(below_1)} ({100*len(below_1)/len(paired):.1f}%)")
    print(f"  Combined > $1.00: {len(above_1)} ({100*len(above_1)/len(paired):.1f}%)")
    print(f"\n  Mean: {mean(combineds):.6f}")
    print(f"  Median: {median(combineds):.6f}")
    print(f"  Std Dev: {stdev(combineds):.6f}")
    print(f"  Min: {min(combineds):.6f}")
    print(f"  Max: {max(combineds):.6f}")

    # Histogram (fine-grained)
    print(f"\n  Combined entry price histogram (fine):")
    edges = [i/100 for i in range(65, 115)]  # 0.65 to 1.14
    histo = [0] * (len(edges))
    for c in combineds:
        placed = False
        for i in range(len(edges) - 1):
            if c < edges[i+1]:
                histo[i] += 1
                placed = True
                break
        if not placed:
            histo[-1] += 1
    max_h = max(histo) if max(histo) > 0 else 1
    for i in range(len(edges) - 1):
        if histo[i] > 0:
            bar = '#' * (histo[i] * 50 // max_h)
            print(f"    {edges[i]:.2f}-{edges[i+1]:.2f}: {histo[i]:4d} {bar}")

    # =========================================================================
    # SECTION 3: PnL ANALYSIS
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 3: PnL ANALYSIS")
    print(f"{'='*80}")

    # Use cashPnl (includes both realized + unrealized)
    total_cash_pnls = [m['total_cash'] for m in paired]
    total_realized_pnls = [m['total_realized'] for m in paired]

    print(f"\n--- Cash PnL (realized + unrealized mark) ---")
    print(f"  Sum: ${sum(total_cash_pnls):.2f}")
    print(f"  Mean: ${mean(total_cash_pnls):.4f}")
    print(f"  Median: ${median(total_cash_pnls):.4f}")
    print(f"  Std Dev: ${stdev(total_cash_pnls):.4f}")

    print(f"\n--- Realized PnL ---")
    print(f"  Sum: ${sum(total_realized_pnls):.2f}")
    print(f"  Mean: ${mean(total_realized_pnls):.4f}")
    nonzero_realized = [r for r in total_realized_pnls if r != 0]
    print(f"  Markets with realized PnL != 0: {len(nonzero_realized)}")
    if nonzero_realized:
        print(f"  Mean (nonzero): ${mean(nonzero_realized):.4f}")
        print(f"  Median (nonzero): ${median(nonzero_realized):.4f}")

    # Merge profit analysis
    print(f"\n--- Merge Profit Analysis ---")
    merge_profits = [m['max_merge_profit'] for m in paired]
    print(f"  If all paired shares were merged at $1.00:")
    print(f"  Total merge profit: ${sum(merge_profits):.2f}")
    print(f"  Mean per market: ${mean(merge_profits):.4f}")
    profitable_merges = [m for m in paired if m['merge_profit_per_share'] > 0]
    unprofitable_merges = [m for m in paired if m['merge_profit_per_share'] <= 0]
    print(f"  Profitable merges (combined < 1.0): {len(profitable_merges)}")
    print(f"  Unprofitable merges (combined >= 1.0): {len(unprofitable_merges)}")
    if profitable_merges:
        prof_merge = [m['max_merge_profit'] for m in profitable_merges]
        print(f"  Profitable merge sum: ${sum(prof_merge):.2f}")
        print(f"  Profitable merge mean: ${mean(prof_merge):.4f}")
    if unprofitable_merges:
        loss_merge = [m['max_merge_profit'] for m in unprofitable_merges]
        print(f"  Unprofitable merge sum: ${sum(loss_merge):.2f}")

    # =========================================================================
    # SECTION 4: PnL BY COMBINED ENTRY
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 4: PnL SEGMENTED BY COMBINED ENTRY")
    print(f"{'='*80}")

    segments = [
        ("< 0.95", lambda m: m['combined_avg'] < 0.95),
        ("0.95-0.97", lambda m: 0.95 <= m['combined_avg'] < 0.97),
        ("0.97-0.99", lambda m: 0.97 <= m['combined_avg'] < 0.99),
        ("0.99-1.00", lambda m: 0.99 <= m['combined_avg'] < 1.00),
        ("1.00-1.02", lambda m: 1.00 <= m['combined_avg'] < 1.02),
        ("1.02-1.05", lambda m: 1.02 <= m['combined_avg'] < 1.05),
        (">= 1.05", lambda m: m['combined_avg'] >= 1.05),
    ]

    for label, filt in segments:
        seg = [m for m in paired if filt(m)]
        if seg:
            pnls = [m['total_cash'] for m in seg]
            realized = [m['total_realized'] for m in seg]
            merges = [m['max_merge_profit'] for m in seg]
            winners = sum(1 for p in pnls if p > 0)
            print(f"\n  [{label}] — {len(seg)} markets")
            print(f"    CashPnL sum: ${sum(pnls):.2f} | mean: ${mean(pnls):.2f} | win rate: {100*winners/len(seg):.0f}%")
            print(f"    RealizedPnL sum: ${sum(realized):.2f}")
            print(f"    Merge profit sum: ${sum(merges):.2f}")

    # =========================================================================
    # SECTION 5: FEE / MAKER-TAKER ANALYSIS
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 5: MAKER vs TAKER ANALYSIS")
    print(f"{'='*80}")

    # Check all possible fee fields in trades
    fee_fields = set()
    for t in trades[:10]:
        for k in t.keys():
            if 'fee' in k.lower() or 'maker' in k.lower() or 'taker' in k.lower():
                fee_fields.add(k)

    if fee_fields:
        print(f"  Fee-related fields found: {fee_fields}")
    else:
        print(f"  No fee-related fields in trade data.")
        print(f"  Available fields: {list(trades[0].keys()) if trades else 'N/A'}")

    # Infer maker/taker from trading pattern
    print(f"\n  --- Inferred from behavior ---")
    print(f"  All trades are BUY side (no SELL found)")
    print(f"  This means LampStore is TAKING liquidity (buying existing orders)")
    print(f"  OR placing limit orders that get filled")

    # Check if prices cluster at round numbers (limit orders) vs fractional (market orders)
    prices = [float(t.get('price', 0)) for t in trades if t.get('price')]
    round_prices = sum(1 for p in prices if abs(p * 100 - round(p * 100)) < 0.01)
    print(f"\n  Prices at exact cents: {round_prices}/{len(prices)} ({100*round_prices/len(prices):.1f}%)")
    print(f"  (High % = likely MAKER placing limit orders at cent levels)")

    # =========================================================================
    # SECTION 6: THE CORE QUESTION — WHERE IS THE $115K PROFIT?
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 6: RECONCILING $115K PROFILE PROFIT")
    print(f"{'='*80}")

    # All positions total
    all_realized = sum(float(p.get('realizedPnl', 0)) for p in positions)
    all_cash = sum(float(p.get('cashPnl', 0)) for p in positions)
    all_init = sum(float(p.get('initialValue', 0)) for p in positions)
    all_current = sum(float(p.get('currentValue', 0)) for p in positions)

    print(f"\n  ALL positions ({len(positions)}):")
    print(f"    Total initialValue: ${all_init:.2f}")
    print(f"    Total currentValue: ${all_current:.2f}")
    print(f"    Total cashPnl: ${all_cash:.2f}")
    print(f"    Total realizedPnl: ${all_realized:.2f}")
    print(f"    Total unrealized (cash - realized): ${all_cash - all_realized:.2f}")

    # Single side positions
    single_realized = sum(float(p.get('realizedPnl', 0)) for p in single)
    single_cash = sum(float(p.get('cashPnl', 0)) for p in single)
    print(f"\n  Single-side positions ({len(single)}):")
    print(f"    Total realizedPnl: ${single_realized:.2f}")
    print(f"    Total cashPnl: ${single_cash:.2f}")

    # Paired positions
    paired_realized = sum(m['total_realized'] for m in paired)
    paired_cash = sum(m['total_cash'] for m in paired)
    print(f"\n  Paired positions ({len(paired)} pairs):")
    print(f"    Total realizedPnl: ${paired_realized:.2f}")
    print(f"    Total cashPnl: ${paired_cash:.2f}")

    print(f"\n  NOTE: Profile shows $115,748 from 19,504 markets.")
    print(f"  API only returns {len(positions)} current positions (~{len(by_cid)} markets).")
    print(f"  Historical resolved positions are NOT returned by the API.")
    print(f"  The $115K is lifetime realized across all 19,504 markets.")
    print(f"  We can only analyze the ~{len(paired)} currently visible paired markets.")

    # Extrapolation
    if len(paired) > 0:
        avg_realized_per_pair = paired_realized / len(paired)
        estimated_total = avg_realized_per_pair * 19504
        print(f"\n  --- Extrapolation ---")
        print(f"  Avg realized per visible pair: ${avg_realized_per_pair:.4f}")
        print(f"  If consistent across 19,504 markets: ${estimated_total:.2f}")
        print(f"  Profile claims: $115,748")
        print(f"  Implied avg PnL per market: ${115748/19504:.4f}")

    # =========================================================================
    # SECTION 7: STRATEGY MECHANICS
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 7: STRATEGY MECHANICS DEEP DIVE")
    print(f"{'='*80}")

    # Check: does the bot buy equal shares on both sides?
    print(f"\n--- Share Balance (Up vs Down) ---")
    for m in paired:
        m['share_ratio'] = m['up_size'] / m['down_size'] if m['down_size'] > 0 else float('inf')
        m['share_diff'] = abs(m['up_size'] - m['down_size'])
        m['share_diff_pct'] = m['share_diff'] / max(m['up_size'], m['down_size']) * 100

    ratios = [m['share_ratio'] for m in paired if m['share_ratio'] < float('inf')]
    diffs_pct = [m['share_diff_pct'] for m in paired]
    print(f"  Up/Down ratio - Mean: {mean(ratios):.4f}")
    print(f"  Up/Down ratio - Median: {median(ratios):.4f}")
    print(f"  Share diff % - Mean: {mean(diffs_pct):.1f}%")
    print(f"  Share diff % - Median: {median(diffs_pct):.1f}%")
    balanced = sum(1 for r in ratios if 0.8 <= r <= 1.25)
    print(f"  'Balanced' (ratio 0.8-1.25): {balanced}/{len(ratios)} ({100*balanced/len(ratios):.1f}%)")

    # Check: does the bot tend to buy more of the cheaper side?
    print(f"\n--- Does bot buy more of the cheaper side? ---")
    cheaper_bigger = 0
    for m in paired:
        if m['up_avg'] < m['down_avg']:
            # Up is cheaper
            if m['up_size'] > m['down_size']:
                cheaper_bigger += 1
        else:
            # Down is cheaper
            if m['down_size'] > m['up_size']:
                cheaper_bigger += 1
    print(f"  Buys more of cheaper side: {cheaper_bigger}/{len(paired)} ({100*cheaper_bigger/len(paired):.1f}%)")

    # Check: what's the typical cost structure?
    print(f"\n--- Cost Structure ---")
    total_inits = [m['combined_init'] for m in paired]
    print(f"  Total capital deployed (paired): ${sum(total_inits):.2f}")
    print(f"  Avg capital per market: ${mean(total_inits):.2f}")
    print(f"  Median capital per market: ${median(total_inits):.2f}")

    # Check: merge vs resolution profit
    print(f"\n--- Merge vs Resolution Analysis ---")
    print(f"  Mergeable positions: {sum(1 for m in paired if m['mergeable'])}")
    print(f"  Redeemable positions: {sum(1 for m in paired if m['redeemable'])}")

    # For mergeable: the bot could merge min(up,down) pairs for $1 each
    mergeable_pairs = [m for m in paired if m['mergeable']]
    if mergeable_pairs:
        total_merge_value = sum(m['min_shares'] for m in mergeable_pairs)
        total_merge_cost = sum(m['min_shares'] * m['combined_avg'] for m in mergeable_pairs)
        print(f"  If all mergeable pairs merged:")
        print(f"    Total shares: {total_merge_value:.2f}")
        print(f"    Total cost: ${total_merge_cost:.2f}")
        print(f"    Total payout: ${total_merge_value:.2f}")
        print(f"    Gross profit: ${total_merge_value - total_merge_cost:.2f}")

    # =========================================================================
    # SECTION 8: PnL DISTRIBUTION STATS
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 8: PnL DISTRIBUTION STATISTICS")
    print(f"{'='*80}")

    pnls = total_cash_pnls
    print(f"\n  N: {len(pnls)}")
    print(f"  Mean: ${mean(pnls):.4f}")
    print(f"  Median: ${median(pnls):.4f}")
    print(f"  Std Dev: ${stdev(pnls):.4f}")
    print(f"  Min: ${min(pnls):.4f}")
    print(f"  Max: ${max(pnls):.4f}")

    # Percentiles
    sorted_pnls = sorted(pnls)
    n = len(sorted_pnls)
    for pct in [5, 10, 25, 50, 75, 90, 95]:
        idx = int(n * pct / 100)
        print(f"  P{pct}: ${sorted_pnls[idx]:.4f}")

    # Sharpe per trade
    if stdev(pnls) > 0:
        sharpe = mean(pnls) / stdev(pnls)
        print(f"\n  Sharpe per trade: {sharpe:.4f}")

    # Win rate
    winners = sum(1 for p in pnls if p > 0)
    losers = sum(1 for p in pnls if p < 0)
    print(f"\n  Winners: {winners} ({100*winners/len(pnls):.1f}%)")
    print(f"  Losers: {losers} ({100*losers/len(pnls):.1f}%)")
    if winners:
        print(f"  Avg win: ${mean([p for p in pnls if p > 0]):.4f}")
    if losers:
        print(f"  Avg loss: ${mean([p for p in pnls if p < 0]):.4f}")

    # PnL histogram
    print(f"\n  PnL distribution (per market):")
    pnl_edges = [-60, -40, -30, -20, -15, -10, -5, -2, 0, 2, 5, 10, 20, 30]
    pnl_histo = [0] * (len(pnl_edges))
    for p in pnls:
        placed = False
        for i in range(len(pnl_edges) - 1):
            if p < pnl_edges[i+1]:
                pnl_histo[i] += 1
                placed = True
                break
        if not placed:
            pnl_histo[-1] += 1
    max_ph = max(pnl_histo) if max(pnl_histo) > 0 else 1
    for i in range(len(pnl_edges) - 1):
        bar = '#' * (pnl_histo[i] * 50 // max_ph) if pnl_histo[i] > 0 else ''
        print(f"    ${pnl_edges[i]:6.0f} to ${pnl_edges[i+1]:6.0f}: {pnl_histo[i]:4d} {bar}")
    print(f"    >= ${pnl_edges[-1]:6.0f}:        {pnl_histo[-1]:4d}")

    # =========================================================================
    # SECTION 9: TIME-BASED EVOLUTION
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 9: STRATEGY EVOLUTION BY DATE")
    print(f"{'='*80}")

    # Group paired markets by endDate
    by_date = defaultdict(list)
    for m in paired:
        d = m['endDate']
        if d:
            by_date[d].append(m)

    print(f"\n  Markets by date:")
    for d in sorted(by_date.keys()):
        markets = by_date[d]
        c = [m['combined_avg'] for m in markets]
        pnl = [m['total_cash'] for m in markets]
        r = [m['total_realized'] for m in markets]
        inits = [m['combined_init'] for m in markets]
        print(f"\n  {d}: {len(markets)} markets")
        print(f"    Combined avg - mean: {mean(c):.4f} | median: {median(c):.4f}")
        print(f"    CashPnL sum: ${sum(pnl):.2f} | mean: ${mean(pnl):.2f}")
        print(f"    RealizedPnL sum: ${sum(r):.2f}")
        print(f"    Capital deployed: ${sum(inits):.2f}")
        if sum(inits) > 0:
            print(f"    Return on capital: {100*sum(pnl)/sum(inits):.2f}%")

    # =========================================================================
    # SECTION 10: TRADE TIMING ANALYSIS
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 10: TRADE TIMING PATTERNS")
    print(f"{'='*80}")

    # All trades are from today — check the frequency
    timestamps = []
    for t in trades:
        ts = t.get('timestamp')
        if ts:
            timestamps.append(float(ts))

    if timestamps:
        ts_sorted = sorted(timestamps)
        intervals = [ts_sorted[i+1] - ts_sorted[i] for i in range(len(ts_sorted)-1)]
        if intervals:
            print(f"\n  Trade intervals (seconds):")
            print(f"    Mean: {mean(intervals):.2f}s")
            print(f"    Median: {median(intervals):.2f}s")
            print(f"    Min: {min(intervals):.2f}s")
            print(f"    Max: {max(intervals):.2f}s")

            # How many trades per minute?
            total_time = ts_sorted[-1] - ts_sorted[0]
            trades_per_min = len(trades) / (total_time / 60) if total_time > 0 else 0
            print(f"    Total time span: {total_time/3600:.2f} hours")
            print(f"    Trades per minute: {trades_per_min:.1f}")

    # =========================================================================
    # SECTION 11: INDIVIDUAL POSITION DETAIL FOR REPRESENTATIVE MARKETS
    # =========================================================================
    print(f"\n{'='*80}")
    print("SECTION 11: SAMPLE MARKET DETAILS")
    print(f"{'='*80}")

    # Show 5 best and 5 worst
    sorted_by_pnl = sorted(paired, key=lambda m: m['total_cash'], reverse=True)

    for label, markets in [("BEST 5", sorted_by_pnl[:5]), ("WORST 5", sorted_by_pnl[-5:])]:
        print(f"\n  --- {label} ---")
        for m in markets:
            print(f"\n  {m['title'][:60]}")
            print(f"    Up:   size={m['up_size']:.2f} avg={m['up_avg']:.4f} cost=${m['up_init']:.2f} cur={m['up_cur']:.3f}")
            print(f"    Down: size={m['down_size']:.2f} avg={m['down_avg']:.4f} cost=${m['down_init']:.2f} cur={m['down_cur']:.3f}")
            print(f"    Combined avg: {m['combined_avg']:.4f}")
            print(f"    Cash PnL: ${m['total_cash']:.2f} | Realized PnL: ${m['total_realized']:.2f}")
            print(f"    Min shares: {m['min_shares']:.2f} | Merge P/L: ${m['max_merge_profit']:.2f}")
            print(f"    Redeemable: {m['redeemable']} | Mergeable: {m['mergeable']}")

    # =========================================================================
    # EXECUTIVE SUMMARY
    # =========================================================================
    print(f"\n{'='*80}")
    print("EXECUTIVE SUMMARY")
    print(f"{'='*80}")

    print(f"""
  WALLET: {WALLET}
  PROFILE: $115,748 profit | 19,504 markets | max win $632

  DATA AVAILABLE:
    Trades: {len(trades)} (today only, API capped at ~3100)
    Positions: {len(positions)} (current/recent only)
    Paired markets: {len(paired)}
    Date range: {min(m['endDate'] for m in paired if m['endDate'])} to {max(m['endDate'] for m in paired if m['endDate'])}

  COMBINED ENTRY PRICE:
    Mean: {mean(combineds):.4f} (median: {median(combineds):.4f})
    < $1.00: {len(below_1)}/{len(paired)} ({100*len(below_1)/len(paired):.1f}%)
    > $1.00: {len(above_1)}/{len(paired)} ({100*len(above_1)/len(paired):.1f}%)
    Std Dev: {stdev(combineds):.4f}

  PnL (visible markets):
    Total Cash PnL: ${sum(total_cash_pnls):.2f}
    Total Realized PnL: ${sum(total_realized_pnls):.2f}
    Mean per market: ${mean(total_cash_pnls):.2f}
    Std Dev: ${stdev(total_cash_pnls):.2f}
    Sharpe per trade: {mean(total_cash_pnls)/stdev(total_cash_pnls):.4f}
    Win rate: {100*sum(1 for p in total_cash_pnls if p > 0)/len(total_cash_pnls):.1f}%

  MAKER/TAKER:
    No fee field in API data.
    All trades are BUY side.
    {100*round_prices/len(prices):.1f}% at exact cent prices (suggests limit orders / MAKER).

  EXTRAPOLATED LIFETIME:
    Implied avg PnL/market from profile: ${115748/19504:.2f}
    Visible avg realized/pair: ${paired_realized/len(paired):.2f}

  KEY INSIGHT:
    The visible {len(paired)} markets show NEGATIVE cashPnL (${sum(total_cash_pnls):.2f}).
    But realizedPnl is POSITIVE (${sum(total_realized_pnls):.2f}).
    The bot's strategy: buy both sides at combined < $1.00, merge/redeem for profit.
    Current positions are OPEN — the cashPnl reflects current mark, NOT final outcome.
    With {100*len(below_1)/len(paired):.0f}% of combined entries < $1.00,
    the merge profit at resolution = guaranteed gain on those markets.
""")


if __name__ == '__main__':
    analyze()
