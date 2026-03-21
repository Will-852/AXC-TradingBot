# Task Plan: Data Diversity + Fill Model Calibration

## Goal
1. ✅ Multi-exchange parallel data fetcher (market_data.py — 22 sources, 6 exchanges)
2. 🔲 Fill model calibration: σ_poly by hour, OB depth collection, arb spread monitor
3. 🔲 Wire into MM bot (15M BTC only, ETH+SOL dry-run)

## Current Phase
Phase 4: Fill Model Data Collection (3 parallel tasks)

## Key Finding (drives Phase 4)
```
σ_poly 同 σ_btc 幾乎零相關 (r=0.063)
→ Fill probability 由 Polymarket OB flow 驅動，唔係 BTC vol
→ 要監控 Polymarket OB 本身，唔止 exchange data
→ P(fill) = 2Φ(-(M₀-b) / (σ_poly√τ))
→ Master metric: σ_poly√τ
```

## Completed Phases
- Phase 0: Research ✅
- Phase 1: market_data.py (22/22 sources, 1.8s) ✅
- Phase 2: OI + Taker + L/S (all in market_data.py) ✅
- Phase 3: DVOL + Book depth (CEX — in market_data.py) ✅
- Pipeline P1-P4 fixes ✅
- ETH + SOL discovery enabled ✅
- Safety hardening (price bounds, funding scale, OI units) ✅

## Phase 4: Fill Model Data Collection

### #1: σ_poly by Hour-of-Day (existing data, zero API cost)
- [ ] Read signal_tape.jsonl (6367 records, 117 markets)
- [ ] Compute σ_poly per market (std of consecutive up_mid changes)
- [ ] Aggregate by HKT hour (168 hours/week)
- [ ] Output: heatmap/table of σ_poly by hour
- [ ] Find: predictable high-σ windows → optimal entry timing
- **Status:** pending
- **Data:** already have signal_tape.jsonl

### #2: Polymarket OB Depth Collection (new data)
- [ ] Add Polymarket CLOB OB fetch to market_data.py or separate recorder
- [ ] Track: bid_depth, ask_depth, best_bid, best_ask for UP + DOWN tokens
- [ ] 5s interval for active markets
- [ ] Output: poly_ob_tape.jsonl (append-only, like signal_tape)
- [ ] Purpose: calibrate depth-aware fill model (simulated 71.8% → real 28.6%)
- **Status:** pending

### #3: Arb Spread Monitor (UP_ask + DOWN_ask)
- [ ] Track combined best_ask(UP) + best_ask(DOWN) per market
- [ ] Log when combined < $0.98 (arb opportunity)
- [ ] Frequency + magnitude + duration of opportunities
- [ ] Output: arb_spread_tape.jsonl
- [ ] Purpose: 64% of profitable wallets use arb — quantify opportunity
- **Status:** pending

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| D1-D4 | (previous — see git history) | |
| D5 | σ_poly by hour from existing data first | Zero cost, immediate insight |
| D6 | OB depth → separate tape file | signal_tape is mid-only, OB needs bid/ask/depth |
| D7 | BTC 15M only this session | 1H by separate agent, ETH+SOL dry-run only |
| D8 | σ_poly r=0.063 vs σ_btc | Fill model must track Poly OB, not just exchange vol |

## Red Lines
- 唔改 bridge 公式
- 15M BTC = live scope, ETH+SOL = dry-run/analysis only
- 1H = 唔碰（另一個 agent）
