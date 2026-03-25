# 5M Momentum Strategy
> Created: 2026-03-25
> Status: DESIGN COMPLETE → Ready for live experiment (20 trades)
> Bankroll: $131

## One-Line Summary
BTC 5M binary. T+30-45s read momentum. Maker limit orders both sides with lean. Hold to resolution.

## Three Independent Modes (test separately)

| Mode | Signal | Action | WR | BE | Risk/trade |
|------|--------|--------|-----|-----|-----------|
| A: Maker Arb | 8-20bps | Both sides, lean 1.4:1, combined < $1.00 | 73-76% | ~65% | -$1.31 max |
| B: Single-Side | ≥20bps | Lean side only, $5 | 84%+ | 50% | -$5.00 max |
| C: Skip | <8bps | Do nothing | — | — | $0 |

## Key Numbers (30-day BTC 1s data, 8,639 windows)

```
T+45s, 8bps threshold:  WR = 76.0% (n=1,432, ~48/day)
T+45s, 20bps threshold: WR = 83.6% (n=165, ~5.5/day)
Maker BE (R=1.36, C=$1.00): ~56% (lean_price $0.56)
Maker BE (R=1.36, C=$1.04): ~67%
DOWN baseline: 50.2% (no directional bias)
Regime: low-vol WR = 78.7% at 8bps (n=61, needs more data)
```

## What We Know vs Don't Know

| Known (data-backed) | Unknown (live only) |
|---------------------|-------------------|
| Momentum WR at all delays/thresholds | Fill rate at $5 bet size |
| Break-even for maker vs taker | Actual combined cost achieved |
| Regime stability (low/mid/high vol) | CLOB reprice speed at T+30-45s |
| Wallet strategies that work | Our latency impact (300ms) |

## File Structure

```
strategies/5m_momentum/
├── README.md              ← You are here
├── config.py              ← All tuneable parameters
├── signal.py              ← Momentum signal computation
├── modes/
│   ├── __init__.py
│   ├── maker_arb.py       ← Mode A: both-sides maker with lean
│   ├── single_side.py     ← Mode B: directional single-side
│   └── skip.py            ← Mode C: no-trade filter
├── data/
│   └── (symlink to analysis/data/)
└── research/
    └── (symlinks to key analysis files)
```

## Live Experiment Design (20 trades)

```
Phase 1 (10 trades): Mode A only (both-sides maker, lowest risk)
  - Entry: T+30-45s, 8bps threshold
  - $5/trade, maker limit orders
  - Measure: fill rate, combined cost, PnL
  - Stop if: 5 consecutive losses OR drawdown > $15

Phase 2 (10 trades): Mode B (single-side, if Phase 1 fill rate > 20%)
  - Entry: T+30-45s, 20bps threshold
  - $5/trade, lean side only
  - Measure: fill rate, fill price, WR
  - Stop if: 3 consecutive losses OR drawdown > $15

Decision after 20 trades:
  Fill rate > 30% + PnL positive → Scale up
  Fill rate 15-30% + PnL flat   → Tune pricing, run 20 more
  Fill rate < 15% OR PnL < -$20 → Stop. Answer = doesn't work for us.
```

## References
- `analysis/momentum_wr_calculator.py` — WR computation script
- `analysis/data/momentum_wr_results.json` — Full results
- `analysis/ultimate_reality_check.md` — Journey + context
- `analysis/wallet_0x{b27b,910e,d1eb,2eb5,c173}.md` — 5 wallet analyses
- `memory/trading/unlawful_shear_reverse_engineering.md` — Key wallet
- `memory/trading/fill_rate_momentum_paradox.md` — Fill rate analysis
