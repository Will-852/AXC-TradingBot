# Progress — MM v4

## Session: 2026-03-19

### v3 ❌（蝕 $5.50 — 廢棄）
- 重複落單 bug：15 DOWN shares（應 5）
- Indicator 從未被用到（`.probability` field 唔存在）
- Entry filter 同 strategy 互相矛盾
- 兩邊買策略只一邊 fill → adverse selection
- 兩邊 equal sizing 只追手續費 $0.25 → 唔值得

### v4 Strategy ✅（用戶確認）
- [x] Opus code review: 12 issues (5 RED, 5 YELLOW)
- [x] Data source audit: 7 signal sources available but unused
- [x] 用戶確認策略方向：Hybrid Asymmetric
  - 信心高 → 方向側重大注碼 + hedge
  - 信心中等 → 接近 equal（spread capture）
  - 冇 edge → skip
- [x] 用戶確認 signal priority：OB + CVD + M1 > 傳統 indicator
- [x] Backtest reference from another agent: Hybrid 75.8% WR, $14.3/day (OOS)
- [x] Planning files updated to reflect v4 strategy
- [x] Memory saved: strategy direction + sizing logic + signal priority

### Phase 1: Fix 12 bugs `status: complete`
- [x] #9: Removed contradictory entry filter
- [x] #4: `.probability` → `.ai_probability`
- [x] #8: Cancel GTC 2 min before window end
- [x] #1: Use get_trades() for fill confirmation (not just open_orders)
- [x] #2: Verified: SDK `market` field = condition_id ✅
- [x] #5: Docstrings updated (both files)
- [x] #7: Don't reset instant fills on _check_fills
- [x] #10: _to_dict preserves runtime fields
- [x] #11: Daily loss limit = 15% bankroll (not absolute $50)
- [x] #12: Binance ≠ Chainlink — accepted limitation, noted
- Float fix: fair_down >= MIN_CONFIDENCE (was failing at boundary)
### Phase 2+3: Dual-Layer Hybrid + Signal Integration `status: complete`
- [x] plan_opening → Dual-Layer (hedge + directional, bankroll-aware)
- [x] Zone 0 (<0.50): skip
- [x] Zone 1 (0.50-0.57): pure hedge if bankroll allows
- [x] Zone 2 (0.57-0.65): 50% hedge + 50% directional
- [x] Zone 3 (>0.65): 25% hedge + 75% directional
- [x] Bankroll gates: $40 = DIR only; $50 = Z1 hedge; $100+ = full dual-layer
- [x] Equal shares in hedge → combined < $1 ALWAYS (verified 0.940-0.950)
- [x] assess_edge() integration (indicator + CVD + microstructure)
- [x] Order book imbalance adjustment (±5%)
- [x] Signal blend: 70% assess_edge + 30% bridge + OB

### Phase 2b: Cancel Defense `status: complete`
- [x] entry_price_snapshot stored per market
- [x] Trigger 1: cancel ALL pending 2 min before window end
- [x] Trigger 2: cancel DIRECTIONAL if spot moves >0.05% (hedge kept)
- [x] Trigger 3: cancel DIRECTIONAL after 60s TTL (hedge kept)
- [x] Layer-specific: hedge pair never cancelled by spot/TTL triggers

### Phase 3b: Indicator Weight `status: complete`
- [x] Max 30% indicator weight (was 70% at T=1min)
- [x] Verified: T=1min bridge=0.924 blended=0.889
### Phase 4: Backtest verify `status: complete`
- [x] 180d bridge-only: 66.3% WR, $13.32/day, $2,398 total
- [x] STRONG >0.60: 70.0% WR | LEAN 0.55-0.60: 58.8% WR
- [x] Train/Test OOS: 66.4% vs 66.1% (drift 0.3% ✅ no overfit)
- [x] 76% positive days, Sharpe 12-13
- [x] Bridge-only baseline — real system adds indicator+CVD+OB for higher accuracy
### Phase 5: Paper 24h `status: pending`
### Phase 6: Live `status: pending`
