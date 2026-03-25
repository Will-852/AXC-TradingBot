# Progress Log

## Session: 2026-03-25

### Strategic Pivot: 5M 鬥快 → 1H+ 鬥分析

**Decision**: User decided to abandon 5M speed competition. Focus on 1H/4H/24H where information advantage > speed advantage.

**Key data supporting pivot**:
- 5M: 3 rounds of BMD reduced edge from $3.61 → $0.43/trade
- 1H: 72% WR already (25 trades), $0.415 EV/share, zero repricing = room to improve
- 4H/24H: uncontested territory, user can provide news + on-chain data

### Phase 1: 15M Quick Win (reprice 30s→5s)
- **Status:** in_progress
- **Started:** 2026-03-25
- Actions:
  - Subagent confirmed: single line change at `run_mm_live.py:439`
  - `_REPRICE_COOLDOWN_S = 30` → `5`
  - `_REPRICE_MAX_PER_ORDER = 3` remains as backstop
  - Phantom fill protection already in place (lines 2784-2851)
  - Window-end guard (3 min) unchanged
- Pending:
  - [ ] Make the change
  - [ ] Record baseline fill rate
  - [ ] Monitor 24h

### Phase 1: 15M Quick Win ✅
- Changed `_REPRICE_COOLDOWN_S = 30` → `5` at run_mm_live.py:439
- Baseline recorded: fill rate 41.6%, 0-reprice=18.8%, 1+-reprice=72%
- Monitoring 24h for comparison

### Phase 2: 1H Mild Repricing
- **Status:** 2A (design + BMD) complete
- **BMD result:** 2 FATAL, 3 WEAKENS → all fixable
- **Key BMD fixes integrated into plan:**
  - [CANCEL-SAFETY] REST double-check (500ms gap between two get_trades calls)
  - [REPRICE-LOCK] `_repricing_in_progress` flag prevents duplicate orders
  - [WR-ASSUMPTION] 72% WR contaminated (n=8 at cheap). Range: 55-72%
  - [EV-ESTIMATE] Range -15% to +32%, not point +28%
  - [DATA-SOURCE] Don't cite 15M data as 1H evidence
- **2B-2E complete:**
  - Config: 6 constants at run_1h_live.py:931-936
  - Function: `_reprice_1h()` ~170 lines with all BMD markers
  - Wiring: line 1438, guard: line 1583
  - REST double-check: pre-cancel + 500ms + post-cancel
  - Syntax verified ✅
- **Next:** 2F (paper test 48h)

### 1H Discovery Summary
- 72% WR, $71.52 PnL, $21.57 bankroll
- 4 signals: Bridge + OB + VolImbal + HolderImbal
- ZERO repricing → biggest improvement opportunity
- BTC live, ETH+SOL observe-only

### 4H/24H Discovery Summary
- Markets exist (confirmed by whale wallets)
- Slug format unknown — need Gamma API fetch
- No implementation code exists
- Multi-TF cascade showed 77.8% WR on 9 events

## 💀 Goldsky On-Chain Discovery (2026-03-25)
- **Analyzed 21 wallets** using Goldsky public subgraphs (maker/taker + PnL)
- **ALL top wallets use same strategy: Maker Buy → Taker Sell (spread capture)**
- **WR ~50% across ALL wallets** — nobody profits from direction at 5M/15M
- **swisstony #1**: +$348K from $15M gross volume (2.3% edge)
- **Decent-Dune actually -$35K** (was +$426K on profile = unrealized included)
- **data-api.polymarket.com was WRONG**: shows all as "BUY", doesn't split maker/taker
- **Impact**: 5M confirmed dead for us. 1H+ strategy (directional, 72% WR) is orthogonal — not competing on same game
- Scripts: `analysis/goldsky_batch_analysis.py`, `analysis/goldsky_wallet_query.py`
- New tool: `pmxt-dev/pmxt` repo cloned to `~/projects/pmxt-ref/` — Goldsky endpoints extracted

## Previous Work (5M analysis, same session)
- 5 math models for repricing (findings.md)
- 2 rounds BMD: edge shrunk from $3.61 → $0.43/trade
- Selection bias insight: repricing removes bias but α decays
- Final verdict: 5M not worth it for retail
- blankandyellow reverse engineering: 3500 trades, 2s cycle, dynamic lean
- **CORRECTED**: blankandyellow is NOT maker arb — it's 50/50 maker/taker spread capture

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | Goldsky on-chain 全行業分析完成，更新 findings |
| 目標？ | 長線策略：1H/4H/24H information competition |
| 學到咩？ | ALL top wallets = spread capture MM (MB→TS), WR ~50%, 唔係 directional。1H 72% WR = genuine edge。 |
| 做咗咩？ | 21 wallets Goldsky analysis + findings rewrite + pmxt repo analysis |
| 下一步？ | Continue Phase 2 (1H repricing) — strategy confirmed orthogonal to competitors |
