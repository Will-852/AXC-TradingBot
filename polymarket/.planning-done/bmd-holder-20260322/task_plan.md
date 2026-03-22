# Task Plan: bmd Fix — Holder Imbalance Gate

## Goal
修復 bmd 發現嘅 6 個問題，restart bot，commit，2check。

## Findings Summary
| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | 💀 | FLIP entry always $0.39 (ceiling) | ✅ Fixed: `min(p_win - base_spread, 0.35)` |
| 2 | 🔴 | AGREE does nothing (size=1.0) | ✅ Fixed: `_size_mult = 1.3` |
| 3 | 🔴 | n=1 threshold calibration | Phase 2: add monitoring |
| 4 | 🟡 | Dedup + FLIP conflict | By design, document |
| 5 | 🟡 | Top 10 ≠ full market | Document as known limitation |
| 6 | 🟡 | FLIP trades not tagged | ✅ Fixed: `[WHALE_FLIP]` log tag |

## Phases

### Phase 1: Verify code fixes ✅
- [x] 1A: FLIP entry price: `min(p_win - base_spread, 0.35)` → entry ~$0.32-$0.35
- [x] 1B: AGREE boost: `_size_mult = 1.3`
- [x] 1C: FLIP log tag: `[WHALE_FLIP]`
- [x] 1D: Import OK
- **Status:** ✅ complete

### Phase 2: Add threshold monitoring + order log tag
- [ ] 2A: Log `h_imbal` value in order log (mm_order_log_1h.jsonl) for every ENTER/ADD
- [ ] 2B: Log `_flip` flag in order log for post-hoc FLIP vs bridge WR analysis
- [ ] 2C: Add inline comment documenting n=1 limitation + dedup conflict + top10
- **Status:** pending

### Phase 3: Restart + validate
- [ ] 3A: Restart bot
- [ ] 3B: Wait for heavy cycle, confirm no crash
- **Status:** pending

### Phase 4: 2check (Opus subagent)
- [ ] 4A: Verify FLIP entry math is correct
- [ ] 4B: Verify AGREE boost doesn't exceed window budget
- [ ] 4C: Ripple check: any doc references to old FLIP price logic?
- **Status:** pending

### Phase 5: Commit + archive
- [ ] 5A: Commit
- [ ] 5B: Archive planning files
- **Status:** pending

## Red Lines
- 唔改 conviction formula
- 唔改 one-order guard（dedup by design）
- 唔改 threshold without more data
