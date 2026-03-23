# Task: W4 策略複製 → 15M + 5M Both-Sides → Real Money
> Created: 2026-03-23 | Updated: 2026-03-24 | Status: PHASE 5 (15M paper running) + PHASE 5M-1 (5M design)

## Goal
W4 style both-sides momentum lean → 15M paper testing → 5M independent file → live。

## ✅ 已完成（Research Phase）
- [x] 13 個月 backtest（103K windows, 81% WR on 15M at 300s/5bps）
- [x] 7 個錢包逆向工程（W4: $507→$433K, lean 3:1, hold to resolution）
- [x] OB pricing gap 驗證（T+300s Poly mid = $1.00 combined, but discount at T+120s）
- [x] W4 on-chain PnL（287 points, 7 growth phases）
- [x] BMD ×4（direction, both-sides, 5M, no-change）
- [x] 5-Agent analysis（realistic PnL $0.05/trade, risk sizing, code spec）
- [x] Key insight: v15 冇改 = 繼續蝕（TP 砍 winner, adverse selection, oversizing）

## Phase 3: Code Implementation — `current`
3 個必要改動 + safety checks

### 3A. Both-sides entry（replace Wide Ladder）
- [ ] Replace lines 1518-1601 with both-sides logic
- [ ] Lean with BTC momentum（ratio from Agent B: median 3:1）
- [ ] Price at Poly OB mid（唔係 fixed $0.43）
- [ ] Keep bridge compute_fair_up for informed pricing
- [ ] Gate: `if both_sides:` → 唔影響 directional mode

### 3B. Remove exit logic for both-sides
- [ ] Skip TP/SL/cost recovery/re-entry when `mkt.get("both_sides")`
- [ ] Keep resolution handling
- [ ] Keep cancel unfilled at T-120s

### 3C. Sizing + parameters
- [ ] bet_pct: --bet-pct 0.02（2% for $253 bankroll, meets Poly 5-share minimum）
- [ ] Half spread: 0.020（combined $0.96 target, adjust if fills too low）
- [ ] Lean ratio: 1.5:1（Agent D found 1.5:1 > 2:1 on Sharpe）

### 3D. Safety
- [ ] assert combined < 1.02（allow slight overshoot like W4）
- [ ] Daily loss cap: keep existing graduated tiers
- [ ] Kill switch: keep existing（HWM -20%）
- [ ] Both-sides still requires --both-sides flag
- [ ] Remove --dry-run enforcement → add --w4-live for live gate

## Phase 4: Audit — `pending`
Sub-agent code review: every changed line

### 4A. Logic audit
- [ ] Entry flow correctness（both orders created, sizes correct）
- [ ] Exit skip correctness（no orphan sells on both-sides markets）
- [ ] Resolution PnL calculation correct for both-sides

### 4B. Safety audit
- [ ] Max loss per trade capped
- [ ] No path where combined > $1.02 passes
- [ ] State correctly tracks both UP and DOWN shares

### 4C. 2check（角色 0-3）
- [ ] Slop scan, silent errors, architecture, live system

## Phase 5: Paper Test 15M — `pending`
- [ ] --both-sides --dry-run --bet-pct 0.02 --bankroll 253
- [ ] Run 48h continuous
- [ ] Collect: fill rate, combined cost, PnL per market, WR
- [ ] Compare to Agent D simulation predictions

## Phase 6: Live Micro-Test — `pending`
- [ ] --both-sides --w4-live --bet-pct 0.01
- [ ] 1 share per side minimum → ~$1/market
- [ ] Run 24h
- [ ] Verify: real CLOB fills, real combined cost, real PnL
- [ ] If PnL negative after 50 markets → STOP

## Phase 7: Live Full — `pending`
- [ ] --both-sides --w4-live --bet-pct 0.02
- [ ] Monitor 48h
- [ ] Daily report
- [ ] If 3 consecutive losing days → review

## Decisions
| # | Decision | Rationale |
|---|----------|-----------|
| 1 | 15M first, 5M later | Lower risk, existing infra, same signal |
| 2 | Lean 1.5:1 not 2:1 | Agent D: 1.5:1 Sharpe 0.110 > 2:1 Sharpe 0.071 |
| 3 | bet_pct 2% not 0.5% | Agent E: $253 bankroll needs ≥2% to meet Poly minimums |
| 4 | Keep bridge model | Informed pricing helps fill rate（better than blind 50/50 mid）|

## Errors
| # | Error | Fix |
|---|-------|-----|
| 1 | $0.96 combined assumption wrong | Real combined ≈ $1.00 at T+300s |
| 2 | Paper fill rate = fake 100% | Phase 6 live micro-test resolves |
| 3 | v15 $151 profit = 1 lucky day | Stripped: -$16 over remaining 4 days |
| 4 | TP costs $1.11/winning trade | Phase 3B removes all TP |

## Key Files
- `polymarket/run_mm_live.py` — main bot（all changes here）
- `polymarket/strategy/market_maker.py` — compute_fair_up（keep）
- `polymarket/analysis/agent_c_code_spec.md` — code change guide
- `polymarket/analysis/w4_strategy_spec.md` — W4 strategy reference
