# Findings — 15M Bot Parameter Optimization
> Updated: 2026-03-24 18:00 HKT (v3, comprehensive)

---

## F0: Current Bot Mechanics (code confirmed)
- Entry: T1 60% at T+300s, T2 40% at T+480s if confirmed
- Pricing: OB mid - 1¢ tick (maker attempt) — BOTH sides same
- Lean ratio: FIXED 1.5:1 (share count, not budget)
- Repricing: downward only, 30s cooldown, max 3x
- Cancel: window-end 2min + Door B (20s/90s lean-unfilled protection)

## F1: Staged Entry (90 days backtest, 5,215 windows with signal)
- T+480s CONFIRMS: **86.1% WR** (n=3,971)
- T+480s FLIPS: **21.5% WR** (n=362)
- Skip rate: 23.9%. Already implemented as T1/T2.

## F2: SOL 5M (90 days, 25,920 windows)
- 78.7% WR at 120s/5bps (vs BTC 84.0%) → weaker by 5.3pp

## F3: SOL 1H (6 months, 4,319 windows)
- 87.9% WR at 1800s/30bps (very strong)

## F4: Partial Fill Analysis (33 live W4 entries)

| Outcome | n | % | WR | PnL | Avg PnL/trade |
|---------|---|---|-----|-----|---|
| BOTH fill ✅ | 19 | 57.6% | 74% | +$1.94 | +$0.10 |
| LEAN miss ❌ | 10 | 30.3% | 60% | -$2.30 | -$0.23 |
| HEDGE miss ⚠️ | 4 | 12.1% | 75% | -$2.35 | -$0.59 |
| **TOTAL** | **33** | | **70%** | **-$2.71** | **-$0.08** |

Signal → fill rate:
- 5-10 bps: 52% both fill, 29% lean miss, 19% hedge miss
- 10-20 bps: 56% both fill, **44% lean miss**, 0% hedge miss
- >20 bps: 100% both fill (n=3)

Fill time: lean median 13s (max 87s), hedge median 21s (max 97s)

## F5: Random Baseline Test (CRITICAL)

| Strategy | WR | Alpha vs 50% |
|----------|-----|---|
| Coin flip | 50.0% | — |
| Always DOWN | 53.5% | +3.5pp |
| **Bot lean** | **78.8%** | **+28.8pp** |

**Z-test: z = 3.31 (p < 0.001) → ALPHA EXISTS**

But: avg win +$0.41, avg loss -$1.90 (loss/win = 4.6x)
→ 79% WR still loses money because payout structure broken

## F6: Payout Structure Analysis (the core problem)

```
Win:  lean 6 shares × $1 = $6.00 - $5.51 cost = +$0.49
Loss: hedge 5 shares × $1 = $5.00 - $5.51 cost = -$0.51

Break-even: 51% WR (comfortable)
But actual avg win = +$0.41 (less than theoretical $0.49)
And actual avg loss = -$1.90 (far worse than theoretical $0.51)
```

Why actual ≠ theoretical:
- Partial fills: lean miss → only hedge → direction right = $0 payout, lose hedge cost
- Hedge miss: direction wrong → lose lean cost (larger than hedge cost)
- Large lean positions on wrong trades (10:35 = -$4.14)

## F7: Time-of-Day Analysis (202 trades all-time)

Golden hours (HKT): 9-10 (58% WR, +$100), 1-2 AM (68%, +$34)
Dead hours (HKT): 4-5 AM (0% WR), 22-23 (11% WR)
Anomaly: **:45 windows = 27% WR** (vs 44-47% for :00/:15/:30)

## F8: Cap Ratio BMD (from session 3)
- Simulation had bug: reduced cost but not redeem
- Corrected: cap ratio improves PnL by $10.95 (not $20.81)
- +$1.67 over 26 trades = noise range ($0.064/trade)
- Cap = risk management (蝕少啲), NOT profit generator
- If WR > 75% → cap hurts (贏少啲 > 蝕少啲)

## F9: Market Manipulation Test
- BTC volatility at boundaries: 0.82x (LESS volatile, not more)
- :00 co-resolution: 47% WR (same as :15/:30)
- No evidence of systematic manipulation
- User's "last 15-30s flip" = tail events (p99+), not systematic

## F10: Full Account Decomposition (from CSV, -$233.64 total)

| Category | PnL | % of total | WR |
|----------|-----|---|---|
| Weather (manual) | -$38 | 16% | 8% |
| Sports + XRP | -$13 | 6% | 33% |
| BTC single-side | -$37 | 16% | 32% |
| ETH single-side | -$51 | 22% | 30% |
| **BTC both-sides** | **-$94** | **40%** | **50%** |
| ETH both-sides | -$0.13 | 0% | 50% |

BTC both-sides trajectory:
- Mar 19: -$62 (sizing bug, one trade -$50)
- Mar 21: -$11 (learning)
- Mar 22: +$8 (first profitable day)
- Mar 23: -$8 (WR 64%, near break-even)
- Mar 24: -$21 (partial fills + large losses)

## F11: T2 Confirmation Stats
- T2 confirmed: 21, skipped: 11 (34% skip rate, all flips)
- T2 saves ~34% of losing capital on flipped windows

## F12: Door B Implementation (R1 CORRECTED 2026-03-24)
- Stage 1: 20s preemptive hedge cancel (catches 8/10 lean-miss)
- Stage 2: 90s reactive lean cancel + block T2 (catches 2/10)
- 2 audit fixes: WS pre-check (BMD) + orphan hedge (2CHECK)
- Worst case: $4.18/trade < 2% bankroll threshold

### R1 Correction: $6.30 save was WRONG

$6.30 = total partial fill impact (lean-miss + hedge-miss + opportunity cost), NOT Door B save.

**Stage 1 (8 trades, hedge unfilled at 20s):**
- Cancel both → $0. Actual PnL without Door B = +$0.10 (6 wins, 2 losses)
- Save on this sample: -$0.10 (forfeit small net profit)
- BUT: lean-miss = negative EV position (lean correct 60% → hedge wrong 60%)
- Expected save over large sample: +$0.23/trade × 8 = +$1.84

**Stage 2 (2 trades, hedge filled at 4.4s and 10s):**
- Hedge commits BEFORE 20s → Stage 1 can't help
- Stage 2 at 90s cancels lean (never filled anyway) + blocks T2
- Save: $0 (naked hedge is committed regardless)

| Metric | Original | Corrected (sample) | Expected (large N) |
|--------|----------|-------------------|-------------------|
| Stage 1 | +$4.47 | -$0.10 | +$1.84 |
| Stage 2 | +$1.83 | $0.00 | $0.00 |
| **Total** | **+$6.30** | **-$0.10** | **+$1.84** |
| **PnL proj** | **+$3.59** | **-$2.81** | **-$0.87** |

**Verdict**: Door B = risk management (variance reduction), NOT profit generator.
Deploy for downside protection. Don't count on it for positive PnL.

## F13: Hedge-Miss Gap Analysis (R2, 2026-03-24)

**Problem**: 4/33 trades (12.1%) = lean fills, hedge doesn't. Door B only handles lean-miss.

| Outcome | n | WR | PnL | Avg |
|---------|---|-----|-----|-----|
| Hedge-miss, direction correct | 3 | — | ~+$0.95 | +$0.32 |
| Hedge-miss, direction WRONG | 1 | — | ~-$3.30 | -$3.30 |
| **Total hedge-miss** | **4** | **75%** | **-$2.35** | **-$0.59** |

**Why mirror Door A doesn't work**: Lean already filled = capital committed. Can't un-fill.
- Cancel hedge pending → removes protection (WORSE)
- Mirror cancel lean → can't (already matched on CLOB)

**Options evaluated**:
1. ~~Mirror cancel~~ — impossible (lean filled)
2. Aggressive hedge reprice (mid+1¢ taker) — viable but needs fee analysis first
3. Accept as known gap — 12.1% rate, -$0.07/trade avg drag

**Decision**: Documented gap. Aggressive hedge repricing deferred to Phase 2 (depends on Q3 fee analysis).
- At current bet size ($5.51/trade), worst case = -$3.30 = 1.4% bankroll. Acceptable.
- If hedge-miss rate stays <15% AND WR stays >70%, gap is tolerable.

## F14: Fee Analysis (Y2, 2026-03-24)

**Source**: Polymarket CSV export (619 rows) + mm_trades.jsonl (202 entries)
**Script**: `polymarket/analysis/fee_analysis.py`

### Key Numbers (40 both-sides BTC 15M trades)

| Metric | Value | Implication |
|--------|-------|-------------|
| Maker rate | 92% (37/40) | POST_ONLY working as intended |
| Taker rate | 5% (2/40) | Rare spread-crossing |
| Avg combined mid | $0.909 | **9.1% structural entry discount** |
| Est. maker fee/trade | $0.00 | Zero fee |
| Est. taker fee/trade | $0.19 | Rare, negligible impact |
| Total PnL (40 trades) | -$112.18 | -$2.80/trade avg |

### Verdict: Fees are NOT the problem

1. **92% maker** → fee is essentially $0 per trade
2. **Combined mid $0.909** → buying Up+Down for $0.91, resolves to $1.00 = structural 9.1% edge
3. PnL still -$2.80/trade despite zero fees → **payout structure is the sole problem**
4. Fee analysis should NOT block Phase 1 — proceed with dynamic ratio (Q1) as priority

### Outliers driving losses
- `btc_19_0415`: -$49.63 (sizing bug, 8.3x lean ratio, pre-fix)
- `btc_21_2330`: -$9.13 (early both-sides, no lean protection)
- `btc_23_2200`: -$10.44 (2.7x ratio, scaled bet)
- Top 3 losses = -$69.20 = 62% of total loss

### Phase 1 Reorder (based on fee analysis)
Original: Q3(fees) → Q1(ratio) → Q2(timing) → Q4(pricing)
**Updated: Q1(ratio) → Q2(timing) → Q4(pricing)** — Q3 is DONE, fees are not a factor.

## F15: Q1 Dynamic Lean Ratio Analysis (2026-03-24)

### Data Sources
- 6,479 sim windows (3-month 1m klines, adverse selection model)
- 321 live entries (shadow_tape.jsonl, BTC 15M)
- 67 resolved W4 trades (BTC + ETH mm_trades + mm_bothsides)
- 11,808 historical windows (w4_15m_era_analysis.py)

### Core Finding: Adverse Selection Confirmed

| Signal Tier | Direction WR (6,479 sim) | Profit WR (67 live) | Adverse Drag @R=1.2 |
|---|---|---|---|
| 5-10 bps | 70.6% | 76% | -$0.007 (small) |
| 10-20 bps | 77.3% | 52% (coin flip!) | -$0.014 (medium) |
| 20-50 bps | 88.1% | 86% (n=7) | -$0.028 (large) |
| >50 bps | 95.3% | — | -$0.061 (massive) |

**Momentum is real** (95% WR at >50bps) but **market prices it in** (drag from $0.007 to $0.061).

### Shadow Tape Validation (321 entries)
- Entry timing is strongest predictor: 3.1min = 66.7% WR → 3.5min = 50%
- Low conviction (< 0.280) = 65.9% WR vs high conviction (> 0.295) = 56.0%
- Adverse selection in entry price: +0.75pp worse for high conviction

### BTC + ETH Pattern (both consistent)
| Tier | BTC WR (33) | ETH WR (34) | Combined (67) |
|---|---|---|---|
| 5-10 bps | 71.4% | 81.2% | 75.7% |
| 10-20 bps | 55.6% | 50.0% | 52.2% |

### Implementation
```python
_W4_RATIO_BY_TIER = {"5-10": 1.2, "10+": 1.0}
# 5-10 bps: R=1.2 (lean small, sweet spot)
# >10 bps:  R=1.0 (arb only, no directional risk)
```

### Expected Impact
| Tier | n/day (est) | R | EV/trade | Daily EV |
|---|---|---|---|---|
| 5-10 bps | ~12 | 1.2 | +$0.034 | +$0.41 |
| >10 bps | ~8 | 1.0 | +$0.03 (arb) | +$0.24 |
| **Total** | **~20** | — | — | **+$0.65** |

Conservative. With Door B + :45 skip reducing losses, actual should be higher.
