# Task: Range Strategy Fix — 3 Evidence-Based Changes

## Goal
修復 Range strategy 嘅三個已證實問題：
1. bb_touch_tol 太嚴（proven: 0.006→0.009 改善 -5.6%→+2.8%）
2. c2 (RSI) 係 dead condition（proven: rsi_long 25→40 零 effect）
3. BTC range strategy 穩定蝕錢（proven: WR 38-40%, PF 0.65-0.67）

## 風險標記
- 🔴 2CHECK：改動會改變 live + backtest 行為
- 所有改動必須 walk-forward validate 後先部署

## Phases

### Phase 1: bb_touch_tol 0.006→0.009 `status: pending`
🔴 2CHECK
- File: `config/params.py` — TIMEFRAME_PARAMS["1h"]["bb_touch_tol"]
- 改動：0.006 → 0.009
- Evidence: grid_search_20260326_1601.json — return -5.62% → +2.82%, PF 0.08 → 1.43
- Risk: 放寬 tolerance = 更多 trades，部分可能 WR 較低
- Verify: grep 所有引用 bb_touch_tol 嘅地方，確認只喺 params.py 定義

### Phase 2: 移除 c2 (RSI) dead condition `status: pending`
🔴 2CHECK — 最危險嘅改動
- File: `scripts/indicator_calc.py` — evaluate_range_signal()
- 現狀: `if c1_long AND c2_long AND c3_long:` (line 398)
- 改為: `if c1_long AND c3_long:` — 移除 c2 requirement
- Evidence: rsi_long 25→40 完全唔影響任何 metric（4 個值一模一樣）
- 但 c2 可能喺某啲 edge case 有 filtering 效果 — 需要驗證
- Alternative: 唔移除，改為 optional bonus（c2 pass → 加分 STRONG，c2 fail → 仍然入場但 WEAK）
- Verify: 改完後跑 walk-forward 比較有/冇 c2 嘅結果

### Phase 3: BTC 獨立處理 `status: pending`
🔴 2CHECK
- Option A: BTC 唔跑 Range strategy（只跑 Trend + Crash）
- Option B: BTC 用獨立 bb_touch_tol（更寬，因為 BTC vol 更大）
- Option C: BTC Range strategy 用唔同嘅 signal 組合
- Evidence: BTC WR 38-40%, PF 0.65 across ALL parameter combos
  ETH WR 45-48%, PF 1.2-1.28
- Decision: 需要 data 支持 — 跑 BTC-only vs ETH-only walk-forward 比較

### Phase 4: Walk-Forward Revalidation `status: pending`
- 用修改後嘅 config 重跑 grid_search --validate
- 比較 before/after WFE
- 如果仲係 0 pass → strategy 本身冇 edge（唔係參數問題）

## Evidence Log
| Data | Source | Finding |
|------|--------|---------|
| bb_touch_tol 0.009 = +2.8% | grid_search_20260326_1601.json | ✅ Strongest single-knob improvement |
| rsi_long 唔影響結果 | grid_search_20260326_1601.json | ✅ c2 is dead condition |
| BTC WR 38-40% across all combos | grid_search_20260326_1601.json | ✅ BTC has no range edge |
| ETH WR 45-48%, PF 1.2-1.28 | grid_search_20260326_1601.json | ✅ ETH has weak range edge |

## Errors
| Phase | Error | Fix | Status |
|-------|-------|-----|--------|
