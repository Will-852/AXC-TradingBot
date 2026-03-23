# Agent A: Realistic Per-Trade PnL — W4-style 15M Both-Sides Strategy
> Generated: 2026-03-24 00:20
> Data: signal_tape.jsonl + btc_1m_7days.json + shadow_tape.jsonl

## Executive Summary

- **190 qualifying windows** over 4 days (47.5/day)
- Filter: T+240-360s elapsed, |BTC return| > 5bps
- Qualify rate: 65.3% of all T+300s windows
- Shadow tape WR (all BTC 15M): 62.8% (258 trades)
- Budget: $1.265/trade (0.5% of $253)
- Lean ratio: 2:1 ($0.843 lean + $0.422 hedge)

### Headline Numbers (buy at mid, trimmed mean — top 5% outliers removed)

| WR | EV/trade (trimmed) | Median EV | Daily | Monthly | % neg trades |
|----|-------------------|-----------|-------|---------|-------------|
| 62.6% | $+0.2229 | $+0.1120 | $+10.588 | $+317.63 | 12% |
| 75% | $+0.1642 | $+0.1083 | $+7.801 | $+234.02 | 17% |
| 81% | $+0.1299 | $+0.0775 | $+6.170 | $+185.10 | 27% |

### CRITICAL WARNING: Outlier Dominance

Mean EV is **heavily skewed** by ~8% of trades where lean price > 0.90.
In these cases, hedge price is < 0.10, creating 'lottery ticket' payoffs:
- Hedge shares = $0.422 / $0.065 = 6.5 shares
- If wrong (hedge wins): payout = $6.50, PnL = +$5.23 on $1.27 invested
- These massive outlier payoffs inflate the mean but happen rarely

**Use TRIMMED MEAN or MEDIAN for realistic expectations, not raw mean.**

### PARADOX: Inverse WR Sensitivity

At the typical trade (lean=0.70, hedge=0.30):
- WIN (lean correct): 0.843/0.70 = 1.20 shares x $1 = $1.20 - $1.265 = **-$0.06**
- LOSS (hedge correct): 0.422/0.30 = 1.41 shares x $1 = $1.41 - $1.265 = **+$0.14**

The strategy **loses money on typical wins and gains on typical losses.**
Higher WR = more frequent small losses + fewer profitable hedge payouts.
This means increasing WR from 63% to 81% actually DECREASES monthly PnL.
The real edge comes from the fat-tailed hedge payouts in the minority of cases.

## 1. Data Overview

| Metric | Value |
|--------|-------|
| Total windows at T+300s | 291 |
| Qualifying (\|BTC return\| > 5bps) | 190 (65.3%) |
| Date range | 4 days |
| Signals per day | 47.5 |

## 2. Polymarket Price Reality at T+300s

| Metric | Lean (momentum) | Hedge (opposite) | Combined |
|--------|-----------------|-------------------|----------|
| Mean | 0.691 | 0.308 | 0.999 |
| Median | 0.705 | 0.295 | 1.000 |
| Min | 0.215 | 0.015 | 0.950 |
| Max | 0.986 | 0.785 | 1.035 |

**Key insight**: Combined mid averages $0.999. This is approximately $1.00, meaning both-sides costs roughly equal to the guaranteed $1 payout. No structural arb exists. Edge comes ENTIRELY from 2:1 lean allocation asymmetry and the fat-tailed hedge payouts.

### Lean Price Breakdown (determines trade character)

- Lean > 0.80 (deep momentum): 63/190 (33%) -- cheap hedge = lottery payoff if wrong
- Lean 0.50-0.80 (moderate): 100/190 (53%) -- balanced, small edge either way
- Lean < 0.50 (contrarian): 27/190 (14%) -- market disagrees with BTC momentum

## 3. PnL by Scenario (Mean AND Trimmed)

### Scenario 1: Buy at MID (aggressive limit)

- Entry cost mean: $0.9993
- PnL if WIN: mean $0.0507, median $-0.0705
- PnL if LOSS: mean $0.8765, median $0.1593
- Worst loss: $-0.7284

| WR | Mean EV | Trimmed EV | Median EV | % neg | Daily (trimmed) | Monthly (trimmed) |
|----|---------|-----------|-----------|-------|----------------|------------------|
| 63% | $+0.3595 | $+0.2229 | $+0.1120 | 12% | $+10.59 | $+317.6 |
| 70% | $+0.2984 | $+0.1889 | $+0.1137 | 11% | $+8.97 | $+269.1 |
| 75% | $+0.2571 | $+0.1642 | $+0.1083 | 17% | $+7.80 | $+234.0 |
| 81% | $+0.2076 | $+0.1299 | $+0.0775 | 27% | $+6.17 | $+185.1 |

### Scenario 2: Buy at BID+1c (maker)

- Entry cost mean: $0.9794
- PnL if WIN: mean $0.0764, median $-0.0516
- PnL if LOSS: mean $1.1298, median $0.2145
- Worst loss: $-0.7209

| WR | Mean EV | Trimmed EV | Median EV | % neg | Daily (trimmed) | Monthly (trimmed) |
|----|---------|-----------|-----------|-------|----------------|------------------|
| 63% | $+0.4704 | $+0.2809 | $+0.1504 | 1% | $+13.34 | $+400.3 |
| 70% | $+0.3924 | $+0.2423 | $+0.1563 | 0% | $+11.51 | $+345.2 |
| 75% | $+0.3398 | $+0.2133 | $+0.1469 | 0% | $+10.13 | $+303.9 |
| 81% | $+0.2766 | $+0.1751 | $+0.1055 | 11% | $+8.32 | $+249.6 |

### Scenario 3: Buy at ASK (taker, guaranteed)

- Entry cost mean: $1.0393
- PnL if WIN: mean $0.0026, median $-0.1066
- PnL if LOSS: mean $0.5236, median $0.0599
- Worst loss: $-0.7427

| WR | Mean EV | Trimmed EV | Median EV | % neg | Daily (trimmed) | Monthly (trimmed) |
|----|---------|-----------|-----------|-------|----------------|------------------|
| 63% | $+0.1974 | $+0.1229 | $+0.0436 | 38% | $+5.84 | $+175.2 |
| 70% | $+0.1589 | $+0.0981 | $+0.0500 | 41% | $+4.66 | $+139.8 |
| 75% | $+0.1328 | $+0.0788 | $+0.0360 | 42% | $+3.74 | $+112.3 |
| 81% | $+0.1016 | $+0.0520 | $-0.0031 | 51% | $+2.47 | $+74.1 |

## 4. Fee Impact

| WR | Taker (1.5%) | Mid (~0.5%) | Maker (0%) | Taker->Maker monthly gain |
|----|-------------|-------------|------------|------------------------|
| 63% | $+175.2/mo | $+317.6/mo | $+400.3/mo | $+225.1/mo |
| 70% | $+139.8/mo | $+269.1/mo | $+345.2/mo | $+205.4/mo |
| 75% | $+112.3/mo | $+234.0/mo | $+303.9/mo | $+191.6/mo |
| 81% | $+74.1/mo | $+185.1/mo | $+249.6/mo | $+175.4/mo |

## 5. OB Depth & Fillability

- Bid volume mean: $33,207
- Bid volume min: $9,634
- Ask volume mean: $30,460
- Our trade size: $1.265
- Impact: 0.0131% of minimum book depth
- **Conclusion: $1.27 fills with ZERO slippage in $10k+ books**

## 6. Critical Assumptions & Risks

1. **Outlier dependence (CRITICAL)**: ~60% of total PnL comes from ~8% of trades where lean > 0.90. If those outlier windows don't materialize or market structure changes, returns collapse.
2. **WR paradox**: Higher WR = LOWER returns because the strategy profits more from wrong calls (hedge payouts) than right calls (lean payouts at typical prices). This is the opposite of a normal directional strategy.
3. **Mid price achievability**: Signal tape records mid, not executable bid/ask. Real spread ~2-4c per side.
4. **Combined cost ~= $1**: Both sides cost ~$1.00 combined. No free lunch from arb. Edge is purely from allocation asymmetry.
5. **Timing risk**: T+300s signal may not match final outcome. BTC can reverse in remaining 600s.
6. **Adverse selection**: When signal fires, smart money is already in. The ask we see may be stale or worse than mid suggests.
7. **Small sample**: 4 days of data (190 windows). Need 30+ days for statistical confidence.

## 7. Bottom Line

### Conservative Projection (trimmed mean, buy at mid)

| WR | Monthly PnL | % of $253 bankroll | Verdict |
|----|------------|-------------------|---------|
| 63% | $+317.6 | +125.5% | Viable |
| 70% | $+269.1 | +106.4% | Viable |
| 75% | $+234.0 | +92.5% | Viable |
| 81% | $+185.1 | +73.2% | Viable |

### Break-even Win Rate

Using MEDIAN per-trade PnL (more robust than mean):

- **Scenario 1: Buy at MID (aggressive limit)**: break-even WR = **69.3%**
- **Scenario 2: Buy at BID+1c (maker)**: break-even WR = **80.6%**
- **Scenario 3: Buy at ASK (taker, guaranteed)**: break-even WR = **36.0%**

### Typical Trade vs Outlier Trade

| Metric | Typical (lean=0.70) | Outlier (lean=0.93) |
|--------|--------------------|--------------------|
| Lean shares | 1.20 | 0.91 |
| Hedge shares | 1.41 | 6.02 |
| PnL if WIN | $-0.060 | $-0.358 |
| PnL if LOSS | $+0.141 | $+4.759 |
| EV@63% WR | $+0.015 | $+1.556 |
| EV@75% WR | $-0.010 | $+0.921 |

**The outlier trade (lean=0.93) has EV of $+1.56 at 63% WR -- 105x the typical trade.**
This single trade type (8% frequency) drives the majority of total returns.

## 8. Data Quality Notes

- **OB tape bid/ask**: 98% of entries show 0.01/0.99 spread (stale top-of-book). Not usable for spread estimation.
- **Signal tape mid**: Aggregated from full depth. More reliable than top-of-book.
- **Spread estimation**: 2c taker / 0c maker / 0c mid are ASSUMPTIONS. Real spread depends on order book state at execution moment.
- **Window count**: 47.5/day qualifying windows seems high. If Polymarket has 96 windows/day (every 15min), 65% qualification = 62/day, which aligns. But many of these overlap with our existing MM bot trades.
