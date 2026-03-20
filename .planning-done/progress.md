# Progress Log

## Session: 2026-03-20

### Phase 1: 偵察 + swisstony 對比
- **Status:** in_progress
- **Started:** 18:10 HKT
- Actions:
  - ✅ blue-walnut 3500 trades fetched + analyzed (whale_1h_timing.py)
  - ✅ 15M boundary clustering confirmed (1.58x)
  - ✅ swisstony 1000 trades analyzed — 主力做 5M (唔係 1H)，0.04% margin 靠量
  - ✅ 1H market structure: Binance OHLC resolution (唔係 Chainlink!)，同 15M 同 liquidity
  - ✅ 結論：跟 blue-walnut pattern (高 margin)，唔跟 swisstony (高 volume)
- Files touched:
  - new: polymarket/analysis/whale_1h_timing.py
  - new: polymarket/logs/whale_1h_analysis.json

### Phase 2: 設計 + BMD
- **Status:** complete
- Actions:
  - ✅ 4-phase bot architecture designed
  - ✅ BMD 攻擊 → 發現 15M boundary data confounded
  - ✅ 用戶確認：先驗證再寫 bot

### Phase 3: 驗證性實作
- **Status:** in_progress
- **Started:** 18:30 HKT
- Actions:
  - ✅ 3A: whale_1h_timing.py 加 proper t-test → **price dislocation 唔顯著**
  - ✅ 3B: Gamma API 1H slug 實測確認 → 全小寫人類可讀格式
  - ✅ 3C: signal_recorder.py 加 1H support:
    - 1H market discovery (slug-based + tag fallback)
    - Detailed OB depth (spread, top-3, imbalance)
    - Binance OHLC open price
    - 15M boundary burst mode (5s tick at ±2min)
    - Separate tape: signal_tape_1h.jsonl
  - ⚠️ 初次發現 1H book 空心 → 再測發現係到期窗口問題
  - ✅ Fresh window spread=$0.01，dense mid-market liquidity
  - 🐛 Fix: _parse_ob_depth best_bid/ask 取 max/min 唔靠 sort order
  - ✅ Recorder PID 54255 running, writing to signal_tape_1h.jsonl
- Files touched:
  - modified: polymarket/analysis/whale_1h_timing.py
  - modified: polymarket/tools/signal_recorder.py
  - new: polymarket/logs/signal_tape_1h.jsonl (recorder output)

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | Phase 3C: signal recorder with 1H support |
| 目標？ | 收集 7 日 1H OB data 驗證 boundary dislocation |
| 學到咩？ | 15M price dislocation 唔顯著; 1H book 空心; slug 確認 |
| 做咗咩？ | whale t-test + slug test + recorder extension |
| 下一步？ | 跑 recorder, 等 7 日 data |
