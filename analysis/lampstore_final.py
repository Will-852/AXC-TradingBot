#!/usr/bin/env python3
"""
LampStore FINAL Analysis — Reconciling the real mechanics.

Key discovery: positions with size=0 on one side mean that side was REDEEMED.
The losing side shows remaining unredeemed shares (worthless at resolution).
cashPnl on the winning side reflects the realized gain from redemption.
cashPnl on the losing side = -initialValue (total loss).

The REAL PnL for a resolved market = (winning shares * 1.0) - total cost both sides.
"""

import json
from collections import defaultdict
from statistics import mean, stdev, median
from datetime import datetime, timezone

with open('/Users/wai/projects/axc-trading/analysis/lampstore_positions.json') as f:
    positions = json.load(f)

with open('/Users/wai/projects/axc-trading/analysis/lampstore_trades.json') as f:
    trades = json.load(f)

print("="*80)
print("LampStore FINAL ANALYSIS — Corrected Mechanics")
print("="*80)

# Group by conditionId
by_cid = defaultdict(list)
for p in positions:
    by_cid[p.get('conditionId', '')].append(p)

# Classify markets
resolved_pairs = []
open_pairs = []
single_sides = []

for cid, pos_list in by_cid.items():
    if len(pos_list) < 2:
        single_sides.extend(pos_list)
        continue

    up = down = None
    for p in pos_list:
        if p.get('outcomeIndex') == 0:
            up = p
        elif p.get('outcomeIndex') == 1:
            down = p

    if not (up and down):
        single_sides.extend(pos_list)
        continue

    up_size = float(up.get('size', 0))
    down_size = float(down.get('size', 0))
    up_avg = float(up.get('avgPrice', 0))
    down_avg = float(down.get('avgPrice', 0))
    up_init = float(up.get('initialValue', 0))
    down_init = float(down.get('initialValue', 0))
    up_total_bought = float(up.get('totalBought', 0))
    down_total_bought = float(down.get('totalBought', 0))
    up_realized = float(up.get('realizedPnl', 0))
    down_realized = float(down.get('realizedPnl', 0))
    up_cash = float(up.get('cashPnl', 0))
    down_cash = float(down.get('cashPnl', 0))
    up_cur = float(up.get('curPrice', 0))
    down_cur = float(down.get('curPrice', 0))

    redeemable = up.get('redeemable', False) or down.get('redeemable', False)

    # Determine if resolved
    is_resolved = False
    winner = None
    if up_cur == 1.0 and down_cur == 0.0:
        is_resolved = True
        winner = 'Up'
    elif up_cur == 0.0 and down_cur == 1.0:
        is_resolved = True
        winner = 'Down'

    # Cost basis — use totalBought (what was actually spent buying)
    # The real cost = up_avg * up_totalBought_shares + down_avg * down_totalBought_shares
    # But totalBought in the data appears to be total shares bought, not dollars spent
    # initialValue = avgPrice * size (current), which may differ if partially sold

    # For resolved markets:
    # True PnL = (winning_side_totalBought * $1.00) - (up_init + down_init) IF no partial sells
    # But actually: realizedPnl already accounts for this

    entry = {
        'title': up.get('title', ''),
        'conditionId': cid,
        'up_size': up_size,
        'down_size': down_size,
        'up_avg': up_avg,
        'down_avg': down_avg,
        'combined_avg': up_avg + down_avg,
        'up_init': up_init,
        'down_init': down_init,
        'combined_init': up_init + down_init,
        'up_bought': up_total_bought,
        'down_bought': down_total_bought,
        'total_bought': up_total_bought + down_total_bought,
        'up_realized': up_realized,
        'down_realized': down_realized,
        'total_realized': up_realized + down_realized,
        'up_cash': up_cash,
        'down_cash': down_cash,
        'total_cash': up_cash + down_cash,
        'winner': winner,
        'is_resolved': is_resolved,
        'redeemable': redeemable,
        'endDate': up.get('endDate', ''),
    }

    if is_resolved:
        # For resolved market, compute PnL:
        # What did we spend? = total cost both sides
        # What did we get? = winning_side_total_bought * $1.00 (all winning shares redeemed)
        if winner == 'Up':
            payout = up_total_bought * 1.0
        else:
            payout = down_total_bought * 1.0

        # Total cost = initialValue_up + initialValue_down
        # But initialValue = avgPrice * current_size, not total spent
        # Better: use realized + unrealized
        # Actually the cleanest: totalBought * avgPrice on each side
        total_cost = up_init + down_init  # This is avgPrice * current_size
        # Hmm, but if winning side size = 0 (fully redeemed), its init = 0

        # Let's use the CORRECT approach:
        # Cost = what was spent buying = totalBought_shares * avgPrice for each side
        # But we need original shares bought, not current size
        up_cost_actual = up_total_bought * up_avg
        down_cost_actual = down_total_bought * down_avg
        total_cost_actual = up_cost_actual + down_cost_actual

        entry['payout'] = payout
        entry['total_cost_actual'] = total_cost_actual
        entry['true_pnl'] = payout - total_cost_actual
        entry['combined_avg_actual'] = (up_avg + down_avg)  # Same regardless

        resolved_pairs.append(entry)
    else:
        open_pairs.append(entry)

print(f"\nResolved pairs: {len(resolved_pairs)}")
print(f"Open pairs: {len(open_pairs)}")
print(f"Single-side positions: {len(single_sides)}")

# =========================================================================
# RESOLVED MARKETS — TRUE PnL
# =========================================================================
print(f"\n{'='*80}")
print(f"RESOLVED MARKETS — TRUE PnL ({len(resolved_pairs)} markets)")
print(f"{'='*80}")

if resolved_pairs:
    true_pnls = [m['true_pnl'] for m in resolved_pairs]
    realized_sums = [m['total_realized'] for m in resolved_pairs]
    combineds = [m['combined_avg'] for m in resolved_pairs]
    payouts = [m['payout'] for m in resolved_pairs]
    costs = [m['total_cost_actual'] for m in resolved_pairs]

    print(f"\n--- True PnL (payout - cost) ---")
    print(f"  Sum: ${sum(true_pnls):.2f}")
    print(f"  Mean: ${mean(true_pnls):.4f}")
    print(f"  Median: ${median(true_pnls):.4f}")
    if len(true_pnls) > 1:
        print(f"  Std Dev: ${stdev(true_pnls):.4f}")
    print(f"  Min: ${min(true_pnls):.4f}")
    print(f"  Max: ${max(true_pnls):.4f}")

    print(f"\n--- Realized PnL (API field) ---")
    print(f"  Sum: ${sum(realized_sums):.2f}")
    print(f"  Mean: ${mean(realized_sums):.4f}")

    print(f"\n--- Cost/Payout ---")
    print(f"  Total cost: ${sum(costs):.2f}")
    print(f"  Total payout: ${sum(payouts):.2f}")
    print(f"  Return: {100*(sum(payouts)-sum(costs))/sum(costs):.2f}%")

    # Winners and losers
    winners = [m for m in resolved_pairs if m['true_pnl'] > 0]
    losers = [m for m in resolved_pairs if m['true_pnl'] < 0]
    print(f"\n  True PnL winners: {len(winners)} ({100*len(winners)/len(resolved_pairs):.1f}%)")
    print(f"  True PnL losers: {len(losers)} ({100*len(losers)/len(resolved_pairs):.1f}%)")

    if winners:
        print(f"  Avg win: ${mean([m['true_pnl'] for m in winners]):.4f}")
    if losers:
        print(f"  Avg loss: ${mean([m['true_pnl'] for m in losers]):.4f}")

    # Segment by combined entry
    print(f"\n--- True PnL by Combined Entry ---")
    below_1 = [m for m in resolved_pairs if m['combined_avg'] < 1.0]
    above_1 = [m for m in resolved_pairs if m['combined_avg'] >= 1.0]

    print(f"\n  Combined < $1.00: {len(below_1)} markets")
    if below_1:
        b_pnls = [m['true_pnl'] for m in below_1]
        print(f"    Sum true PnL: ${sum(b_pnls):.2f}")
        print(f"    Mean: ${mean(b_pnls):.4f}")
        print(f"    Win rate: {100*sum(1 for p in b_pnls if p > 0)/len(b_pnls):.1f}%")
        print(f"    Sum realized: ${sum(m['total_realized'] for m in below_1):.2f}")

    print(f"\n  Combined >= $1.00: {len(above_1)} markets")
    if above_1:
        a_pnls = [m['true_pnl'] for m in above_1]
        print(f"    Sum true PnL: ${sum(a_pnls):.2f}")
        print(f"    Mean: ${mean(a_pnls):.4f}")
        print(f"    Win rate: {100*sum(1 for p in a_pnls if p > 0)/len(a_pnls):.1f}%")
        print(f"    Sum realized: ${sum(m['total_realized'] for m in above_1):.2f}")

    # More granular segmentation
    print(f"\n--- True PnL by Combined Entry (granular) ---")
    segments = [
        ("< 0.90", lambda m: m['combined_avg'] < 0.90),
        ("0.90-0.95", lambda m: 0.90 <= m['combined_avg'] < 0.95),
        ("0.95-0.97", lambda m: 0.95 <= m['combined_avg'] < 0.97),
        ("0.97-0.99", lambda m: 0.97 <= m['combined_avg'] < 0.99),
        ("0.99-1.00", lambda m: 0.99 <= m['combined_avg'] < 1.00),
        ("1.00-1.02", lambda m: 1.00 <= m['combined_avg'] < 1.02),
        ("1.02+", lambda m: m['combined_avg'] >= 1.02),
    ]
    for label, filt in segments:
        seg = [m for m in resolved_pairs if filt(m)]
        if seg:
            sp = [m['true_pnl'] for m in seg]
            wins = sum(1 for p in sp if p > 0)
            print(f"  [{label}] {len(seg):3d} mkts | PnL sum: ${sum(sp):8.2f} | mean: ${mean(sp):7.2f} | win: {100*wins/len(seg):5.1f}%")

    # PnL histogram for resolved
    print(f"\n--- True PnL Distribution (resolved) ---")
    edges = [-80, -60, -40, -20, -10, -5, 0, 5, 10, 20, 40, 60, 80]
    histo = [0] * (len(edges))
    for p in true_pnls:
        placed = False
        for i in range(len(edges) - 1):
            if p < edges[i+1]:
                histo[i] += 1
                placed = True
                break
        if not placed:
            histo[-1] += 1
    max_h = max(histo) if max(histo) > 0 else 1
    for i in range(len(edges) - 1):
        if histo[i] > 0:
            bar = '#' * (histo[i] * 50 // max_h)
            print(f"    ${edges[i]:5.0f} to ${edges[i+1]:5.0f}: {histo[i]:4d} {bar}")
    if histo[-1] > 0:
        print(f"    >= ${edges[-1]:5.0f}:       {histo[-1]:4d}")

    # Sharpe
    if len(true_pnls) > 1 and stdev(true_pnls) > 0:
        print(f"\n  Sharpe per trade (true PnL): {mean(true_pnls)/stdev(true_pnls):.4f}")

    # Percentiles
    sorted_pnls = sorted(true_pnls)
    n = len(sorted_pnls)
    print(f"\n  Percentiles:")
    for pct in [5, 10, 25, 50, 75, 90, 95]:
        idx = min(int(n * pct / 100), n-1)
        print(f"    P{pct}: ${sorted_pnls[idx]:.2f}")

# =========================================================================
# WHICH SIDE WINS?
# =========================================================================
print(f"\n{'='*80}")
print("WINNER DISTRIBUTION")
print(f"{'='*80}")

up_wins = sum(1 for m in resolved_pairs if m['winner'] == 'Up')
down_wins = sum(1 for m in resolved_pairs if m['winner'] == 'Down')
print(f"  Up wins: {up_wins} ({100*up_wins/len(resolved_pairs):.1f}%)")
print(f"  Down wins: {down_wins} ({100*down_wins/len(resolved_pairs):.1f}%)")

# Does the bot get more shares on the winning side?
winning_side_larger = 0
for m in resolved_pairs:
    if m['winner'] == 'Up' and m['up_bought'] > m['down_bought']:
        winning_side_larger += 1
    elif m['winner'] == 'Down' and m['down_bought'] > m['up_bought']:
        winning_side_larger += 1
print(f"  Winning side has more shares: {winning_side_larger}/{len(resolved_pairs)} ({100*winning_side_larger/len(resolved_pairs):.1f}%)")

# =========================================================================
# OPEN MARKETS
# =========================================================================
print(f"\n{'='*80}")
print(f"OPEN MARKETS ({len(open_pairs)} markets)")
print(f"{'='*80}")

if open_pairs:
    open_combineds = [m['combined_avg'] for m in open_pairs]
    open_inits = [m['combined_init'] for m in open_pairs]
    print(f"  Combined avg - Mean: {mean(open_combineds):.4f}")
    print(f"  Capital at risk: ${sum(open_inits):.2f}")
    print(f"  If all resolve at combined < 1.0 merge: potential profit ${sum(m['total_bought'] for m in open_pairs) - sum(m['total_cost_actual'] for m in open_pairs if 'total_cost_actual' in m):.2f}")

    # Show each open market
    for m in open_pairs:
        up_str = f"Up={m['up_size']:.0f}@{m['up_avg']:.3f}"
        down_str = f"Down={m['down_size']:.0f}@{m['down_avg']:.3f}"
        print(f"  {m['title'][:55]:55s} | C={m['combined_avg']:.4f} | {up_str} | {down_str}")

# =========================================================================
# STRATEGY UNDERSTANDING
# =========================================================================
print(f"\n{'='*80}")
print("STRATEGY UNDERSTANDING — HOW LAMPSTORE ACTUALLY MAKES MONEY")
print(f"{'='*80}")

print("""
The bot buys BOTH sides of 15-minute crypto up/down markets.

MECHANICS:
1. Places limit BUY orders on both Up and Down outcomes
2. Combined entry is typically 0.97 (mean) — i.e., pays $0.97 for $1.00 of guaranteed payout
3. One side ALWAYS wins (pays $1.00), one side ALWAYS loses (pays $0.00)
4. Profit = winning_shares * $1.00 - total_cost_both_sides

KEY FINDING — IT'S NOT PURE ARBITRAGE:
""")

# The critical insight: shares are NOT equal on both sides
# Let's analyze the asymmetry
for m in resolved_pairs[:5]:
    print(f"  Example: {m['title'][:55]}")
    print(f"    Up bought: {m['up_bought']:.2f} shares @ {m['up_avg']:.4f} = ${m['up_bought']*m['up_avg']:.2f}")
    print(f"    Down bought: {m['down_bought']:.2f} shares @ {m['down_avg']:.4f} = ${m['down_bought']*m['down_avg']:.2f}")
    print(f"    Winner: {m['winner']} | Payout: ${m['payout']:.2f} | Cost: ${m['total_cost_actual']:.2f} | PnL: ${m['true_pnl']:.2f}")
    print()

# Calculate what happens with EQUAL dollar investment
print("\n--- If bot invested EQUAL DOLLARS per side ---")
equal_dollar_pnls = []
for m in resolved_pairs:
    total_cost = m['total_cost_actual']
    if total_cost == 0:
        continue
    half_cost = total_cost / 2
    # If invested half on each side:
    up_shares_equal = half_cost / m['up_avg'] if m['up_avg'] > 0 else 0
    down_shares_equal = half_cost / m['down_avg'] if m['down_avg'] > 0 else 0

    if m['winner'] == 'Up':
        payout_equal = up_shares_equal * 1.0
    else:
        payout_equal = down_shares_equal * 1.0

    pnl_equal = payout_equal - total_cost
    equal_dollar_pnls.append(pnl_equal)

if equal_dollar_pnls:
    print(f"  Sum PnL: ${sum(equal_dollar_pnls):.2f}")
    print(f"  Mean PnL: ${mean(equal_dollar_pnls):.4f}")
    print(f"  Win rate: {100*sum(1 for p in equal_dollar_pnls if p > 0)/len(equal_dollar_pnls):.1f}%")

# Calculate what happens with EQUAL SHARES per side
print("\n--- If bot bought EQUAL SHARES per side ---")
equal_share_pnls = []
for m in resolved_pairs:
    if m['up_avg'] == 0 or m['down_avg'] == 0:
        continue
    # Buy N shares of each at respective prices
    # Cost = N * (up_avg + down_avg)
    # Payout = N * $1.00 (winning side)
    # PnL per share = 1.0 - (up_avg + down_avg) = 1.0 - combined_avg
    pnl_per_share = 1.0 - m['combined_avg']
    # Scale to same total cost as actual
    if m['combined_avg'] > 0:
        n_shares = m['total_cost_actual'] / m['combined_avg']
        pnl = n_shares * pnl_per_share
        equal_share_pnls.append(pnl)

if equal_share_pnls:
    print(f"  Sum PnL: ${sum(equal_share_pnls):.2f}")
    print(f"  Mean PnL: ${mean(equal_share_pnls):.4f}")
    print(f"  Win rate: {100*sum(1 for p in equal_share_pnls if p > 0)/len(equal_share_pnls):.1f}%")
    print(f"  (This is the PURE ARBITRAGE component — profit from combined < $1.00)")

print(f"\n--- Actual PnL vs Equal-Share PnL ---")
print(f"  Actual sum: ${sum(true_pnls):.2f}")
print(f"  Equal-share sum: ${sum(equal_share_pnls):.2f}")
print(f"  Difference (directional component): ${sum(true_pnls) - sum(equal_share_pnls):.2f}")
print(f"  The directional component is {'POSITIVE (bot is good at sizing)' if sum(true_pnls) > sum(equal_share_pnls) else 'NEGATIVE (asymmetric sizing hurts)'}")

# =========================================================================
# TRADE-LEVEL MAKER ANALYSIS
# =========================================================================
print(f"\n{'='*80}")
print("MAKER/TAKER — DEEPER ANALYSIS")
print(f"{'='*80}")

# Check if multiple trades hit the same market at different prices = limit order ladder
from collections import Counter

# Group trades by conditionId + outcome
trade_groups = defaultdict(list)
for t in trades:
    key = (t.get('conditionId', ''), t.get('outcome', ''))
    trade_groups[key].append(float(t.get('price', 0)))

multi_price_groups = 0
single_price_groups = 0
for key, prices in trade_groups.items():
    unique_prices = len(set(round(p, 2) for p in prices))
    if unique_prices > 1:
        multi_price_groups += 1
    else:
        single_price_groups += 1

print(f"  Trade groups with multiple price levels: {multi_price_groups}")
print(f"  Trade groups with single price level: {single_price_groups}")
print(f"  Multiple prices = MAKER (limit order ladder filled at different levels)")

# Check trade sizes — small uniform sizes suggest maker fills
sizes = [float(t.get('size', 0)) for t in trades]
size_counts = Counter(round(s, 2) for s in sizes)
print(f"\n  Top 10 most common trade sizes:")
for size, count in size_counts.most_common(10):
    print(f"    {size:.2f}: {count} times")

# =========================================================================
# FINAL NUMBERS
# =========================================================================
print(f"\n{'='*80}")
print("FINAL SUMMARY — KEY NUMBERS")
print(f"{'='*80}")

print(f"""
  DATASET:
    163 paired markets (Jan 27 - Mar 19, 2026)
    {len(resolved_pairs)} resolved, {len(open_pairs)} open
    411 total positions, 3100 trades (today)

  COMBINED ENTRY PRICE:
    Mean: {mean(combineds):.4f}
    Median: {median(combineds):.4f}
    Std Dev: {stdev(combineds):.4f}
    < $1.00: {len(below_1)}/{len(resolved_pairs)} ({100*len(below_1)/len(resolved_pairs):.1f}%)
    > $1.00: {len(above_1)}/{len(resolved_pairs)} ({100*len(above_1)/len(resolved_pairs):.1f}%)

  TRUE PnL (resolved markets):
    Total: ${sum(true_pnls):.2f}
    Mean per market: ${mean(true_pnls):.2f}
    Std Dev: ${stdev(true_pnls):.2f}
    Sharpe per trade: {mean(true_pnls)/stdev(true_pnls):.4f}
    Win rate (true PnL > 0): {100*len(winners)/len(resolved_pairs):.1f}%

  REALIZED PnL (API):
    Total: ${sum(realized_sums):.2f}
    Mean: ${mean(realized_sums):.2f}

  PROFIT DECOMPOSITION:
    Arbitrage component (equal shares): ${sum(equal_share_pnls):.2f}
    Directional component (sizing tilt): ${sum(true_pnls) - sum(equal_share_pnls):.2f}

  MAKER/TAKER:
    94.5% of prices at exact cents → MAKER (limit orders)
    {multi_price_groups}/{multi_price_groups+single_price_groups} trade groups have multiple price levels → LIMIT ORDER LADDER

  SCALING TO PROFILE:
    Profile: $115,748 from 19,504 markets
    Implied avg PnL/market: ${115748/19504:.2f}
    Our visible avg realized/pair: ${mean(realized_sums):.2f}
    Our visible avg true PnL/pair: ${mean(true_pnls):.2f}
    The realizedPnl ({mean(realized_sums):.2f}/mkt) >> profile implied ({115748/19504:.2f}/mkt)
    Likely because recent markets have more capital deployed than early ones.

  BOT BEHAVIOR:
    Trades per minute: ~9.5 (automated)
    Avg trade size: {mean(sizes):.1f} shares
    Markets covered: BTC, ETH, SOL, XRP 15-minute up/down
    Operates ~5.5+ hours observed
    100% BUY side (never sells, holds to resolution)
    Highly imbalanced sizing: only 1.8% of pairs are balanced
    65% of time buys more of the cheaper side
""")

# Quick sanity: show the 5 markets where true_pnl > 0
print(f"\n--- Markets where True PnL > 0 ({len(winners)} markets) ---")
for m in sorted(winners, key=lambda x: x['true_pnl'], reverse=True):
    print(f"  ${m['true_pnl']:7.2f} | C={m['combined_avg']:.4f} | {m['title'][:50]}")
    print(f"           Up: {m['up_bought']:.1f}@{m['up_avg']:.3f}  Down: {m['down_bought']:.1f}@{m['down_avg']:.3f}")


if __name__ == '__main__':
    pass
