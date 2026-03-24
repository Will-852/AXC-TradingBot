# Task: AXC Dashboard — Flow/Vol 優化 + 數據持久化 + 價錢 Label 修正

## Goal
解決 3 個核心問題：
1. Flow/Vol 數據轉 TF 後消失（無 cache）
2. Flow worker interval change bug（唔 restart）
3. 價錢 label 被 OB 遮住

## 架構決定
- 用 IndexedDB（via vanilla wrapper，唔加 Dexie 因為單一 use case）儲存 footprint data per symbol:interval
- Cache key: `${symbol}:${interval}`，TTL 30 min（live 數據），歷史數據無 TTL
- 轉 TF 時：cache hit → 即顯示 → 背景 refresh；cache miss → 正常 fetch
- Price label：移到左 y-axis（KLineChart 支援 `yAxis.type: 'normal'` on left）

## Phases

### Phase 0: 偵察 + 確認改動範圍 `status: complete`
- 已完成：探索 vol/flow 完整 data pipeline
- 已完成：確認 bugs（interval change 唔 restart worker、fetch overwrite live data）
- 已完成：確認 KLineChart API for price mark positioning

### Phase 1: IndexedDB Cache Layer `status: complete`
- 新文件：`canvas/fp-cache.js` — IndexedDB wrapper for footprint data
- 修改：`backtest.html` — fetchFootprintData() 加 cache read/write
- 修改：`backtest.html` — onIntervalChange() 先查 cache
- 修改：`backtest.html` — handleOFMessage() live data 寫入 cache
- Cache schema: store `fpCache` with key `[symbol, interval]`, value `{delta_volume, large_trades, volume_profile, heatmap, cvd, timestamp}`
- 自動清理：startup 時刪 >24h entries

### Phase 2: Flow Worker Bug Fixes `status: complete`
- Bug 1: `onIntervalChange()` 加 `if (liveOFActive) startLiveOFWorker()`（~line 3684）
- Bug 2: `fetchFootprintData()` 完成後，如果 live active → merge 而唔係 overwrite
- 防禦：resetFootprintData() 唔清 cache，只清 in-memory state

### Phase 3: 價錢 Label 重新定位 `status: complete`
- KLineChart `priceMark.last` 移到左 y-axis
- 或者用 custom overlay 畫 floating badge（停喺 OB panel 左邊）
- 測試：OB 開/關兩個狀態都要正常顯示

### Phase 4: 2check + Audit `status: complete`
- Subagent audit：cross-check 所有改動
- 確認 cache 唔會 corrupt data
- 確認 live/historical merge 正確
- 確認 price label 喺所有 TF + OB 狀態下正常

### Phase 5: Delta-colored VOL bars `status: complete`
- VOL sub-pane bars 改用 delta 上色（正 delta = teal, 負 = red），取代 candle direction
- 需要 footprintData.delta_volume 有數據時才用 delta 色，否則 fallback 到原本行為
- 修改 VOL indicator override 或用 custom draw

### Phase 6: VP 移到右邊 + 加寬 `status: complete`
- y-axis 已移左，VP 左邊會撞 → 移到 chart 右邊
- maxBarWidth 15% → 25%
- bars 從右邊畫回去（right-aligned）

### Phase 7: POC/VA 視覺加強 `status: complete`
- POC: dashed → solid, lineWidth 1→2, opacity 0.50→0.75, label 加大
- VA band: opacity 0.06→0.12
- VA border: opacity 0.25→0.40

### Phase 8: 2check UI changes `status: complete`
- Subagent audit all visual changes
- 確認 delta-colored bars 喺冇 footprint data 時 graceful fallback
- 確認 VP right-side 唔同 OB panel 撞

## Errors
| Phase | 錯誤 | 解法 | 狀態 |
|-------|------|------|------|

## Verification
- 轉 TF 後 flow data 即顯示（<1s）
- Live flow + historical fetch 共存無 data loss
- 價錢 label 喺 OB 開啟時清楚可見
- VOL bars 即時反映 delta（teal/red）
- VP 喺右邊清楚可見，唔同 OB 撞
- POC line 一眼就見到
