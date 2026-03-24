# Findings: AXC Flow/Vol 優化

## 1. 現有架構（from exploration）
- KLineChart v10 for charting
- Web Worker (`live-orderflow-worker.js`) for live order flow
- Backend: `fetch_agg_trades.py` aggregation → `backtest.py` async job → poll API
- All footprint data in-memory: `footprintData = {delta_volume, large_trades, volume_profile, heatmap, cvd}`
- `resetFootprintData()` at line 1888 clears everything on TF switch
- No IndexedDB, no persistent cache for footprint data

## 2. Bug: Interval Change 唔 Restart Flow Worker
- `onIntervalChange()` (line 3640-3690) restarts kline WS but NOT flow worker
- `onSymbolChange()` correctly restarts flow worker (line 4072)
- Fix: add `if (liveOFActive) startLiveOFWorker()` after interval change

## 3. Bug: Fetch Overwrites Live Data
- `_pollAggtrades()` on completion (line 1767-1772): direct assignment overwrites live accumulation
- VP merge has `_vpHistBase` protection, but delta/heatmap/cvd don't
- Fix: if liveOFActive, merge instead of replace; or warn user

## 4. Price Label
- KLineChart priceMark.last renders on right y-axis by default (line 1579)
- OB panel is fixed position overlay at right:12px, 520px wide
- KLineChart supports `yAxis` config per pane — can set price axis to left

## 5. Professional Order Flow Display Patterns (from research)
- Tier 1 indicators: CVD divergence + absorption at VP levels
- Tier 2: Footprint imbalances (stacked 3+), large trade clusters
- IndexedDB cache recommended: store per symbol:interval, TTL-based cleanup
- Price label: left y-axis or floating badge pattern
