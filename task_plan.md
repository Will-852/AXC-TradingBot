# Task: Polymarket 5M Momentum — Taker Lean + Maker Hedge Implementation

## Goal
改造 `strategies/five_m_momentum/` 實現三層 decision tree：
- < 8bps → SKIP
- 8-15bps → MAKER 兩邊 (arb, combined < $0.98)
- > 15bps → TAKER lean (aggressive GTC limit) + MAKER hedge (optional)

解決 12h live data 嘅核心問題：maker both-sides WR 35.1% (momentum paradox)。

## Key Constraints
- Bankroll: >$200, max loss 30% ($60)
- Min bet: $5/trade (Polymarket minimum)
- 5M spreads wider than 15M (combined 0.80-0.98 from Uncommon-Oat data)
- FOK 唔係真 market order → 用 aggressive GTC limit (ask+2¢, cap $0.55)
- Fee: 1.53% taker (唔係 0.55%)
- 每個 Phase 完成後由 opus subagent line-by-line audit

## Phases

### Phase 1: config.py 重寫 `status: complete`
- 三層 threshold (SKIP / ARB / DIRECTIONAL)
- Taker lean params (ASK_BUFFER=0.02, ASK_CAP=0.55)
- Session risk (MAX_LOSS=60, CONSECUTIVE_STOP=5)
- 5M-specific: σ_5m = 0.16% (vs 15M 0.28%)
- **Audit**: opus subagent 逐行 review

### Phase 2: signal.py 重寫 `status: complete`
- 三個 Mode: SKIP / MAKER_ARB / TAKER_DIRECTIONAL
- Confidence = momentum magnitude → controls mode selection
- 8-15bps → MAKER_ARB
- > 15bps → TAKER_DIRECTIONAL
- AXC indicator integration point 保留
- **Audit**: opus subagent 逐行 review

### Phase 3: modes/ 重寫 `status: complete`
- `maker_arb.py` — 低信心 arb mode (combined < $0.98, 5+5 maker both sides)
- `taker_directional.py` — 高信心 mode (aggressive GTC lean + optional maker hedge)
- `skip.py` — 保留 + 加 vol regime filter
- **Key**: taker lean 用 GTC at ask+2¢ 唔係 FOK
- **Audit**: opus subagent 逐行 review

### Phase 4: run.py 重寫 `status: in_progress`

**Sub-steps (each audited separately):**

4A: Imports + config wiring (kill all hardcodes, use config params)  `status: complete`
4B: Three-tier decision tree (SKIP / MAKER_ARB / TAKER_DIRECTIONAL)  `status: complete`
4C: Fill tracking + resolution (BTC price at window close → PnL)     `status: complete`
4D: State persistence + session risk (crash recovery, loss caps)      `status: complete`

- **Audit**: opus subagent after EACH sub-step

### Phase 5: Integration test `status: complete`
- Unit tests for signal computation
- Unit tests for order planning (all three modes)
- Dry-run smoke test (connect to real Gamma/Binance, paper orders)
- Verify state save/load cycle
- **Audit**: opus subagent full integration review

### Phase 6: 2check + Final audit `status: complete`
- 角色 0-3 full 2check
- Cross-file consistency (config ↔ signal ↔ modes ↔ run)
- Live safety: crash recovery, double submit prevention, max loss enforcement
- **Audit**: opus subagent final sign-off

## Errors
| Phase | 錯誤 | 解法 | 狀態 |
|-------|------|------|------|

## Key Data Points (from 12h live + 30-day backtest)
- Momentum WR (T+45s): 8bps=76%, 15bps=83.2%, 20bps=83.6%
- Lean accuracy (live): 58.1% (191 trades)
- Trade WR (live): 35.1% ← momentum paradox, maker both-sides
- Fill rate: 83% orders filled, but only 28% signals → orders
- Combined cost: median $1.02 (85% > $1.00)
- 5M spread: wider than 15M (combined 0.80-0.98)
- Taker fee: 1.53% (not 0.55%)
- Break-even single-side: ~55.8% WR

## Verification
- [ ] config.py 所有 param 有 docstring + 來源 reference
- [ ] signal.py 三個 mode 邊界正確 (8/15 bps)
- [ ] maker_arb combined < $0.98 hard enforced
- [ ] taker_directional 用 GTC (唔係 FOK), ask+2¢, cap $0.55
- [ ] run.py lean→hedge sequential with fill guard
- [ ] Session stop at -$60 cumulative
- [ ] State file atomic write + crash recovery
- [ ] No double submit after crash
- [ ] Dry-run mode works end-to-end
