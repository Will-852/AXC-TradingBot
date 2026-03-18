# AXC Dashboard & Chart Research Report
> Generated: 2026-03-18 | 10 parallel agents | Sources: 150+ URLs

---

## Executive Summary: Top 10 Actions by ROI

| # | Action | Effort | Impact | Priority |
|---|--------|--------|--------|----------|
| 1 | **WebSocket aggTrade stream** — 取代 REST pagination | 中 | 徹底解決 429 | P0 |
| 2 | **Drawing tools** — trendline, horizontal line, Fibonacci | 中 | 最多 trader 要求嘅功能 | P1 |
| 3 | **Volume Profile overlay** — VPVR horizontal histogram | 高 | 專業級圖表標配 | P1 |
| 4 | **Keyboard shortcuts** — Alt+T/F/H, 數字鍵切 timeframe | 低 | 用家體驗即時提升 | P1 |
| 5 | **Monte Carlo simulation** — equity path confidence bands | 中 | 回測可靠性驗證 | P2 |
| 6 | **Trade markers upgrade** — MFE/MAE rectangles + hover detail | 中 | 策略分析深度 | P2 |
| 7 | **Rolling metrics panel** — rolling Sharpe/Sortino/volatility | 低 | 穩定性診斷 | P2 |
| 8 | **Bar Replay** — 倒帶逐根回放 | 中 | 策略練習神器 | P3 |
| 9 | **Multi-chart layout** — synced crosshairs across timeframes | 高 | 專業交易員標配 | P3 |
| 10 | **Symbol overlay/compare** — BTC vs ETH vs DXY on same chart | 低 | 聯動分析 | P3 |

---

## 1. KLineChart v9/v10 Advanced Features

### Custom Indicators
- `registerIndicator({ name, calcParams, figures, calc })` → `createIndicator('NAME')`
- v10 breaking: `calc` returns `Record<Timestamp, unknown>` (v9 was array)
- External package: `klinecharts-technical-indicator` (npm)

### Custom Overlays (Trade Markers, Drawings)
- v9 unified `registerOverlay` / `createOverlay` replaces old `createShape`/`createAnnotation`
- Key properties: `totalStep`, `createPointFigures`, event callbacks
- Built-in: `priceLine`, `horizontalRayLine`, `segment`, `straightLine`, `parallelLine`

### Multi-Pane
- `createIndicator('RSI', false, { height, minHeight, dragEnabled, order })`
- `isStack = true` to overlay indicators in same pane
- v10: `layout.position` → `layout.order`

### Performance (10K+ candles)
- Canvas-based, only renders visible candles
- v10 `setDataLoader` replaces `applyNewData/applyMoreData/updateData`
- Async `calc` via Promise for web worker offloading

### KLineChart Pro
- Out-of-box trading terminal UI (toolbar, symbol search, drawing tools, period selector)
- `@klinecharts/pro` on npm

---

## 2. TradingView Feature Gap Analysis

### AXC Missing (Priority Order)
1. **Drawing tools** — trendline, Fibonacci, horizontal line, rectangles for S/R zones
2. **Multi-timeframe switching** — with indicator persistence across timeframes
3. **Alert system** — price-level and indicator-condition based
4. **Volume Profile / VWAP** overlay
5. **Keyboard shortcuts** — Alt+T, Alt+F, Alt+H, number keys for timeframes
6. **Chart templates** — save/load entire chart configs
7. **Multi-chart layout** — synced crosshairs, 2-16 charts
8. **Bar replay** — rewind and play bar-by-bar
9. **Symbol overlay** — compare mode (BTC vs ETH)
10. **Screener** — filter by technical criteria

### Competitors Better Than TradingView
- **GoCharting**: DOM/price ladder, footprint, volume profile display modes
- **Exocharts**: Best footprint + delta analysis for crypto
- **Coinalyze**: Funding rates, OI, liquidations
- **Bookmap**: Real-time order book heatmap

---

## 3. Backtest Dashboard Best Practices

### Metrics Hierarchy
**Tier 1 (Must)**: Max Drawdown, Sharpe, CAGR, Profit Factor
**Tier 2 (Decision)**: Sortino, Calmar, Win Rate + Avg W/L, Expectancy, Trade Count
**Tier 3 (Advanced)**: Rolling beta, volatility, turnover, exposure

### Essential Visualizations ("Big 5")
1. Equity Curve (+ benchmark overlay, log scale toggle)
2. Drawdown Chart (underwater plot, top 5 highlighted)
3. Monthly Returns Heatmap (years × months)
4. Rolling Sharpe (6m + 12m lines)
5. Return Distribution Histogram (+ normal overlay)

### Robustness
- Walk-Forward Analysis (in-sample/out-of-sample windows)
- Monte Carlo (1000+ sims, fan chart with percentile bands)
- Crisis Event Overlays (auto-detect stress periods)

### Best Tools
- **QuantStats**: Full HTML tear sheet, Monte Carlo built-in
- **Pyfolio**: Institutional tear sheets (Bayesian analysis)
- **VectorBT**: Fastest parameter sweep + Plotly heatmaps

---

## 4. Volume Profile Rendering

### Types
- **VPVR** — visible range, recalculates on scroll/zoom
- **VPFR** — fixed range, user-selected start/end
- **VPSV** — per-session, separate histogram per day

### Algorithm
1. Define price range (high-low of window)
2. Create N equal bins
3. Allocate volume: proportional distribution across overlapping bins
4. Body/wick weighting for buy/sell accuracy

### Key Levels
- **POC**: argmax(volume) across all bins
- **Value Area** (70%): expand outward from POC until 70% of total volume included
- **HVN/LVN**: bins above/below median volume

### Rendering
- Canvas overlay anchored to right edge — bars extend left
- Buy (green) + Sell (red) stacked per bin row
- TradingView uses Catmull-Rom spline (smooth curve) instead of stepped bars
- Performance: dirty-rectangle tracking, batch draw calls, OffscreenCanvas in Web Worker

---

## 5. Footprint Chart Implementation

### Types
- **Bid×Ask**: buy volume left, sell volume right per price level per candle
- **Delta**: net buy-sell per level (green=buy dominant, red=sell)
- **Volume**: total volume per level (heatmap intensity)
- **Imbalance**: diagonal comparison (buy at level N vs sell at level N+1)

### Imbalance Detection
- **Diagonal**: compare bid[N] vs ask[N+1], ratio > 3:1 = imbalance
- **Stacked**: 3+ consecutive imbalances in same direction = absorption signal

### Color Coding
- Intensity scales with volume magnitude relative to session average
- POC row highlighted distinctly
- Imbalance cells get accent color (blue/orange)

### Performance
- Each candle may have 20-100+ price level cells
- Canvas batch rendering essential
- Group by fill color → single `fillRect` call per color
- Pre-compute cell positions in typed array

---

## 6. Trading Dashboard UI/UX Design

### Dark Theme Rules
- Base: `#121212` (not pure black)
- Body text: off-white (not `#FFFFFF`)
- Gridlines: `#FFFFFF` at 5-10% opacity
- Max 2-3 accent colors
- Desaturate accents ~15-20% vs light mode

### Layout Pattern
- Top: KPI banner (CAGR, Sharpe, DD, PF)
- Center: chart (largest area)
- Side: stats, trade log, order book
- Bottom: order entry, positions

### Accessibility
- **Blue + Orange** over red/green (8% men are R/G colorblind)
- Shape encodes action (arrow=entry, circle=exit)
- Color encodes outcome (green=win, red=loss)
- Never rely on color alone

### Animation
- Under 300ms, purposeful only
- Price tick: brief color flash that fades
- No bouncing/elastic/overshooting

---

## 7. Equity Curve & Performance Visualization

### Equity Curve
- Line chart for multi-strategy comparison, area for single hero
- Log scale if backtest >2 years or returns >100%

### Drawdown
- Underwater chart (inverted area below zero)
- Triple Penance Rule: recovery ≈ 2-3× drawdown duration
- V-shaped (healthy), U-shaped (concerning), L-shaped (dangerous)

### Monte Carlo
- 1000-10000 simulated equity paths (gray lines)
- Percentile bands: 5th, 25th, 50th, 75th, 95th
- Key outputs: VaR 95%, CVaR, 95th percentile max DD, bust probability

### Rolling Metrics
- Rolling Sharpe, Sortino, volatility, win rate, profit factor
- Horizontal reference line (Sharpe=1.0)
- Shade above/below for visual clarity

---

## 8. Trade Markers & Annotations

### Entry/Exit Convention
- Entry long: upward arrow below bar (green)
- Entry short: downward arrow above bar (red)
- Exit: circle at exit bar
- Use different shapes for entry vs exit (distinguishable without color)

### SL/TP Lines
- SL: horizontal dashed red, extending from entry
- TP: horizontal dashed green, extending from entry
- Shaded zones: semi-transparent fill between entry and SL/TP

### Position Duration (MFE/MAE)
- Rectangle from entry to exit price, colored by outcome
- MFE rectangle: extends to most favorable price during trade
- MAE rectangle: extends to most adverse price during trade
- Background z-order, 10-15% opacity

### PnL Annotation
- Label at exit: show $, %, and R-multiple
- R-multiple is preferred for strategy evaluation
- Progressive disclosure: markers default, rectangles on hover/click

---

## 9. Real-Time Streaming Architecture

### Recommended Stack
```
Binance WS (@kline + @aggTrade)
  → WebSocket Client (reconnect + backoff + heartbeat)
  → Message Parser
  → Data Layer (candle buffers per timeframe)
    ├── Partial candle update / new candle logic
    ├── Incremental indicator recalculation
    └── Gap detection → REST backfill
  → Event Bus / State Store
  → rAF Render Loop (coalesces to 60fps)
  → Chart Components
```

### Key Patterns
- **Buffer + rAF coalescing**: WS handler writes to buffer, rAF loop reads once per frame
- **Timestamp bucketing**: floor trade timestamp to interval boundary for update vs new candle
- **Incremental indicators**: SMA (circular buffer), EMA (prev value only), RSI (avg gain/loss)
- **Reconnection**: exponential backoff (1→2→4→8→30s cap), re-subscribe, REST gap fill
- **Multi-TF sync**: subscribe to 1m, aggregate to 5m/15m/1h client-side

---

## 10. Open-Source Dashboard Comparison

| Project | Stars | Stack | Best Feature |
|---------|-------|-------|-------------|
| **Freqtrade + FreqUI** | 47.8k | Python+Vue+Plotly | Strategy-driven `plot_config` |
| **NautilusTrader** | 21.3k | Rust+Python+Plotly | Self-contained HTML tearsheets |
| **Backtrader** | 20.8k | Python+Matplotlib | Plugin architecture for chart backends |
| **LEAN** | 17.9k | C#+Python | Multi-asset institutional grade |
| **Hummingbot** | 17.8k | Python+Streamlit→React | Full strategy lifecycle UI |
| **TradingView LW Charts** | 14k | TypeScript+Canvas | Industry standard web chart lib |
| **Jesse** | 7.6k | Python+JS | TradingView Pine Script export |
| **VectorBT** | 6.9k | Python+Plotly | Fastest parameter sweep viz |
| **KLineChart Pro** | 3.6k | TypeScript+Canvas | Embeddable trading terminal |
| **Apache ECharts** | 66k | TypeScript | Best for heatmaps/non-chart viz |

### Patterns to Steal
1. FreqUI: strategy code declares its own chart layers (plot_config)
2. NautilusTrader: self-contained HTML file output
3. Superalgos: hover-to-inspect-any-variable at any candle
4. Jesse: export to TradingView Pine Script for verification
5. VectorBT: Plotly parameter sweep heatmaps

---

## Volume Data Solution: WebSocket Migration

### Current Problem
- BTC 1 day ≈ 800K aggTrades → 800+ REST calls → hits 2400 req/min limit
- Scanner + trader + dashboard all share same IP

### Solution: Binance WebSocket aggTrade Stream
```
wss://fstream.binance.com/ws/btcusdt@aggTrade
```
- **1 connection** replaces 800+ REST calls
- Real-time, 100ms update (futures)
- Same payload as REST: `a`, `p`, `q`, `T`, `m`
- Connection limit: 300/5min per IP, 1024 streams/connection

### Libraries (Python)
| Library | Notes |
|---------|-------|
| `unicorn-binance-websocket-api` | Production-grade, Cython, auto-reconnect |
| `python-binance` | Most popular, ThreadedWebsocketManager |
| `ccxt.pro` | Unified API across 100+ exchanges |

### Migration Plan
1. REST for historical backfill (cached CSV, one-time per day)
2. WebSocket for live/today data
3. Aggregate in-memory → push to dashboard via SSE
