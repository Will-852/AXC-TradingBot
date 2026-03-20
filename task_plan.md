# Task Plan: AS Defense — Fix Fill Rate + Per-Order Logging

## Goal
v14 live fill rate = 0% (6 submitted, 6 cancelled, 0 filled)。
修復 cancel defense TTL + 加 per-order logging 為 AS 分析打基礎。

## 診斷
- fill_stats: submitted=6, filled=0, cancelled=6 → 全部被自己 cancel
- Cancel defense 有 3 trigger: window_end-2min, adverse move 0.3%, TTL 5min
- M1 gate 要等 60s → entry at min 1-3 → TTL 5min = cancel at min 6-8
- Maker order 在 15M market 可能需要 10+ min 先有 taker 食
- **根因: TTL 太短，唔係 AS**

## Phases

### Phase 1: Fix cancel defense TTL (P0 — 解決 0% fill)
- [ ] 1A: TTL 改為動態: `min(10min, window_end - 3min - entry_ts)` 代替固定 5min
- [ ] 1B: Adverse move threshold BTC 0.3% → 0.5% (v14 data顯示 0.3% 太敏感)
- [ ] 1C: 加 cancel reason log (reason + time_on_book + distance_to_end)
- **Status:** pending

### Phase 2: Per-order logging (P1 — AS 分析基礎)
- [ ] 2A: 新 JSONL: `mm_order_log.jsonl` — 每個 order 獨立記錄
  - submit: order_id, submit_ts, token_id, outcome, price, size, fair, mid, cvd, vol, bridge
  - fill: fill_ts, mid_at_fill
  - cancel: cancel_ts, cancel_reason
  - post_fill: mid_60s_post_fill (scheduled check)
- [ ] 2B: _execute() 寫 submit record
- [ ] 2C: _check_fills() 寫 fill/cancel record
- [ ] 2D: 新 deferred check — fill 後 60s 記錄 mid (AS cost measurement)
- **Status:** pending

### Phase 3: Round-dependent pricing (P2 — 減少 re-entry loss)
- [ ] 3A: R2 bid × 0.90, R3 bid × 0.80 (更保守)
- [ ] 3B: BTC move > 0.3% since window open → skip re-entry
- **Status:** pending

### Phase 4: 2check + bmd
- [ ] 重讀所有改動文件
- [ ] 2check all changes
- **Status:** pending

## Decisions
| Decision | Rationale |
|----------|-----------|
| 動態 TTL, 唔係延長固定 | 早 entry → 長 TTL; 遲 entry → 短 TTL (唔需要 sit 到 window end) |
| Adverse 0.3% → 0.5% | 0.3% BTC = $210, 日常 noise; 0.5% = $350, 真正 adverse |
| Per-order JSONL 唔係 DB | 同 mm_trades.jsonl 格式一致，簡單可靠 |
| 唔做 logistic model | n=15 太少，先收 6 個月 data |
| 唔做 EW toxicity | CVD 已 cover 大部分，加 complexity 冇必要 |

## Risk
| Risk | Mitigation |
|------|------------|
| TTL 太長 → orphan orders at window end | window_end - 3min 硬 cancel 依然生效 |
| Per-order log 增加 I/O | append-only JSONL, 每次 1 line, negligible |
| Round pricing 太保守 → R2/R3 永遠唔 fill | R2 × 0.90 = $0.36 仍在合理範圍 |

## Files to modify
1. `polymarket/run_mm_live.py` — cancel defense + per-order logging + round pricing
