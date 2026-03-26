# Findings: Range Strategy Analysis

## Grid Search Data (2026-03-26)

### Sweep 1: sl_atr_mult_range × min_rr (BTC 180d)
- 32 combos, 0 pass walk-forward
- sl_atr_mult_range: 8 values → ZERO EFFECT
- min_rr: 4 values → some effect but WFE=0.04

### Sweep 2: bb_touch_tol × rsi_long (BTC+ETH 365d)
- 32 combos, 0 pass walk-forward
- bb_touch_tol: 8 values → SIGNIFICANT EFFECT (193→212 trades, -5.6%→+2.8%)
- rsi_long: 4 values → ZERO EFFECT (25/30/35/40 identical)

### Per-Symbol Breakdown (bb_touch_tol=0.009)
| Symbol | Trades | WR | PF | Return |
|--------|--------|-----|-----|--------|
| BTC | 67 | 38.8% | 0.65 | -13.0% |
| ETH | 145 | 47.6% | 1.28 | +15.8% |
| **Aggregate** | 212 | 44.7% | 1.43 | +2.8% |

## BMD Conclusions
- Entry signal = 5-15% of PnL
- c2 (RSI) = dead condition (100% pass when c1 fires)
- AND gate = unfalsifiable at 10 trades/year
- bb_touch_tol single knob > structural gate change
- BTC range strategy has NO edge (WR 38-40% stable across all params)
