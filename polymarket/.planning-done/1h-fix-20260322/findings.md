# Findings: 1H Conviction Bot Diagnosis

## Research Date: 2026-03-22

## 1. Order Log Analysis (151 orders, 338 events)

### Order Lifecycle
```
submit → fill:      29 (19%)
submit → cancelled:  3 ( 2%)  ← actual cancelled_external with matched submit
submit → expired:   55 (36%)
submit → unknown:   65 (43%)  ← CRITICAL: no outcome tracked
```

### Per-Market Breakdown (top 4 account for 98 of 151 submits)
```
0x01c55d (BTC 4AM ET): 27S  0F 100C 27E ← 0 fills, 100 cancel events!
0x948872 (ETH 4AM ET): 20S 19F   1C  0E ← 19 fills = $47.51 (infinite re-entry bug)
0xd0334f (BTC 5AM ET): 26S  0F   0C  0E ← all at $0.54-$0.57, 0 fills
0xddc331 (ETH 5AM ET): 25S  0F   0C  0E ← all at $0.55-$0.57, 0 fills
```

### Key: 0x01c55d Order Spam Pattern
```
04:21 → $0.37  (conviction low, price OK)
04:25 → $0.37
04:28 → $0.41  (conviction rising)
04:31 → $0.44
04:32 → $0.47  ← entering danger zone
04:35 → $0.51  ← structural edge almost gone
04:40 → $0.53
04:48 → $0.51
04:49 → $0.47
```
27 orders in 28 min, ONE PER HEAVY CYCLE, none cancelled before next.
100 cancelled_external events = CLOB rejecting/cancelling due to insufficient balance
(0x948872 ETH was eating all USDC simultaneously)

### Key: 0xd0334f Post-Depletion Pattern
```
05:40 → $0.56 5.0sh  ← bankroll depleted, still submitting at crazy prices
05:41 → $0.54 5.0sh
...
05:51 → $0.57 5.1sh  ← 26 orders over 11 min, ALL at $0.54-$0.57, ZERO fills
```
Entry price $0.56 → break-even WR = 56% → no structural edge.

## 2. Fill Rate Actual vs Theory

### Filled trades (real positions that resolved)
```
Phase 1 (3/20 22:00-3/21 05:00): 8 filled, $77.48 cost
  - Includes $47.51 ETH disaster (bug)
  - Clean trades: 6 markets, 3W/3L = 50% WR
  - Avg entry (wins): $0.452

Phase 2 (3/21 05:00-3/22): 7 "filled", $1.93 cost
  - 0.4-1.0 shares per fill (micro orders, below practical minimum)
  - All lost (12L/0W in state file for this period)
  - 33 markets discovered but 0 meaningful fills
```

### Submit price distribution
```
$0.34-$0.40:  59 orders (39%) ← good structural edge zone
$0.41-$0.50:  27 orders (18%) ← marginal
$0.51-$0.57:  65 orders (43%) ← NO structural edge, 0% fill rate
```

## 3. Root Cause Chain
```
Infinite re-entry bug (0x948872)
  → $47.51 loss on single market
  → bankroll depleted
  → subsequent orders too small to fill
  → meanwhile: no cancel-before-reorder
  → 25-27 orders stacked per market
  → CLOB rejects (insufficient balance)
  → 100+ cancelled_external
  → 65 orders lost to "unknown" (leak)
```

## 4. Entry Price × Fill Rate (1H specific)
```
Avg fill price: $0.410 (29 fills)
Avg submit price: $0.461 (151 submits)
  → Fills cluster at LOWER prices ($0.34-$0.47)
  → High-price orders ($0.51-$0.57) = 0% fill rate

Implication: conviction engine's price_cap allows up to $0.60
but nothing above $0.51 ever fills in 1H markets.
Practical ceiling ≈ $0.47 (highest fill observed).
```

## 5. cancelled_external = Insufficient Balance
Evidence:
- 0x01c55d had 100 cancel events for 27 submits (duplicate counting)
- Cancel timing: after 0x948872 started consuming balance (04:28+)
- All 3 explicitly cancelled orders had valid sizes (5.8-6.9 shares)
- Pattern: NOT market-close or self-trade — timing matches balance depletion

## 6. Re-Submission Loop Mechanism (refined from lifecycle tracing)
```
Order lifecycle tracing (151 orders):
  submit → fill:       29 (19%)
  submit → cancelled:   3 ( 2%)  ← actual matched cancel
  submit → expired:    55 (36%)
  submit → unknown:    65 (43%)  ← CLOB rejected, never tracked
```

The "27 submits on 0x01c55d" is NOT 27 concurrent orders:
1. Submit A → CLOB cancels A (no balance) → `_check_fills()` removes A from pending
2. budget_remaining resets to 100% → conviction says ENTER → submit B
3. Repeat 27 times in 28 minutes

This is a **cancel-then-resubmit loop**, not order accumulation.
Fix = dedup guard (one order per market per window), not cancel management.

## 7. Wallet Reverse Engineering Insights (14 wallets, $2.2M PnL)

### Entry Prices
- **Arb bots (9/14)**: combined UP+DN < $1.00. Early fills at $0.07-$0.45 = where ALL profit lives.
  Later fills push combined to $1.00+ = cost, not edge.
- **BoneReader ($881K, 1H specialist)**: ONLY enters at $0.990-$0.999 on near-settled markets.
  Completely different strategy (certainty play, not conviction).
- **likebot**: fixed 5-share lots, sweeps entire book. Simple = scalable.
- **Fill probability model**: optimal EV bid = $0.20 across ALL vol terciles and entry times.

### Order Management
- **12/14 wallets**: set-and-forget maker. BUY → MERGE → REDEEM. No cancels.
- **Brundle (Active MM)**: only wallet with SELL (15%). Buys both → sells one → rebalance.
- **distinct_baguette (competitor bot)**: 500ms requote, sub-second adverse cancel.
  AXC 8s reaction time vs competitor 50ms = 160x slower.

### Fill × Volatility × Price
- σ_poly ≈ independent of σ_btc (r=0.063) — Poly mid has own rhythm
- At bid=$0.37: low σ = 51% fill, high σ = 91% fill (40pp difference!)
- Higher bid barely improves fill: $0.37→$0.40 only +0.4pp fill rate
- Time decay: min 1→9, fill rate drops ~12pp

### Adverse Selection
- Fast fills (TTF < 90s): 100% adverse in real data (2/2)
- Slow fills (TTF > 296s): mixed — safer
- VPIN estimate: 0.40 (40% of fills are informed-adversarial)
- AXC Implementation Shortfall: 53.6% of potential edge lost to execution quality

### Implications for 1H Bot
1. **Lower entry price is ALWAYS better** (structural edge > signal accuracy)
2. **Set-and-forget > aggressive cancel/reorder** (wallet consensus)
3. **$0.54-$0.57 entries = dead zone** (0% fill, 0% structural edge)
4. **1H markets less liquid than 15M** → fill model numbers may be worse
5. **One-order-per-window** aligns with likebot pattern (5 shares, simple, scalable)
