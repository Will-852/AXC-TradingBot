# Progress Log — AXC Dashboard NiceGUI Migration

## Session: 2026-03-20

### Phase 0: 環境 + scaffolding ✅
- NiceGUI 3.9.0 installed (Python 3.14.3, --break-system-packages)
- Directory: scripts/dashboard_ng/{pages,components/js,utils}
- Hello world: all 5 routes 200 OK on port 5567
- Backend imports verified: services.py, collectors.py

### Phase 1: Layout Shell + Dark Theme ✅
- layout.py: header (AXC logo + exchange badges) + sidebar (nav + services) + footer
- theme.py: dark-first color palette
- state.py: background collector with run.io_bound() (BMD fix #1)
- Dark mode default + toggle + persist to app.storage.user
- Services: 7 services with live status + restart buttons
- Exchange badges: Aster/Binance/HL live status

### Phase 2: Stats + Controls ✅
- stats_cards.py: Today PnL, Total PnL, Triggers, Positions (2s refresh)
- risk_boxes.py: market mode, consecutive losses, daily loss, drawdown
- controls.py: Profile/Regime/Trading toggle → writes params.py directly
- Fixed: TRADING_ENABLED append when key doesn't exist in params.py

### Phase 3: Positions + Orders ✅
- positions.py: position cards with close/modify SL-TP
- action_plan.py: AG Grid with color-coded changes + row highlighting

### Phase 5: Charts + Analytics ✅
- pnl_chart.py: ECharts sparkline + time range filter (1H/4H/1D/7D/ALL)
- analytics.py: fee breakdown, trade stats, funding rates, news sentiment, trade history, activity log

### Phase 6: AI Chat ✅
- chat.py: floating FAB → dialog panel, Fast/Deep toggle, markdown rendering

### Phase 7: Services Management ✅
- Integrated into sidebar (layout.py) — live status + restart buttons

### Phase 8: Polymarket ✅
- pages/polymarket.py: KPIs, PnL chart, trades grid, circuit breakers, run cycle/force scan/mode toggle

### Phase 9: Paper Trading ✅
- pages/paper.py: start/stop subprocess, status display, DRY_RUN log entries

### Phase 11: Docs Browser ✅
- pages/docs.py: splitter layout, file search, markdown rendering, path traversal guard

### Phase 4: Trade Entry Modal ✅
- trade_modal.py: OKX-style order entry dialog
- 5-step execution: margin mode → leverage → entry → SL → TP
- Debounce lock (BMD fix #2): button disabled during submit
- Auto qty calc from USDT notional + leverage + live price
- Symbol info fetch (step size, min qty)
- Balance display (parallel fetch)
- Wired to action plan table: click row → opens trade modal for that symbol

### Phase 10: Backtest Studio ✅ (Basic — ECharts version)
- pages/backtest.py: full backtest page
- ECharts candlestick chart + volume bars + data zoom
- Symbol/interval/days selector
- Parameter override panel (SL/TP mult, risk %, leverage)
- Async backtest run with polling
- Results panel: stats cards + trades AG Grid
- Saved runs browser with load

### Remaining
- Phase 10 upgrade: TradingView custom component (optional, future)
- Phase 12: Migration + Cleanup (switch LaunchAgent plist)

## Files Created (19 files)
```
scripts/dashboard_ng/
├── __init__.py
├── main.py                  # Entry point (port 5567)
├── layout.py                # Shared layout (header/sidebar/footer)
├── theme.py                 # Colors/classes
├── state.py                 # Background collector (run.io_bound)
├── pages/
│   ├── __init__.py
│   ├── backtest.py          # ECharts candlestick + backtest run
│   ├── polymarket.py        # Full polymarket controls
│   ├── paper.py             # Paper trading start/stop
│   └── docs.py              # Docs browser with splitter
├── components/
│   ├── __init__.py
│   ├── stats_cards.py       # 4 KPI cards
│   ├── risk_boxes.py        # Market mode, risk meters, drawdown
│   ├── controls.py          # Profile/Regime/Trading toggles
│   ├── positions.py         # Position cards + modify SL/TP
│   ├── action_plan.py       # AG Grid action plan (click → trade)
│   ├── trade_modal.py       # Order entry dialog (5-step execution)
│   ├── pnl_chart.py         # ECharts PnL sparkline
│   ├── analytics.py         # Fees, stats, funding, news, history
│   └── chat.py              # Floating AI chat panel
└── utils/
```

## Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| All 5 routes | 200 | 200 | ✅ |
| Background collector | starts + logs | OK | ✅ |
| Exchange init | Aster+Binance connect | OK | ✅ |
| TRADING_ENABLED write | append if missing | OK (fixed) | ✅ |

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | NiceGUI dashboard migration — 12/13 phases complete |
| 目標？ | Replace 14K+ line HTML dashboard with pure Python NiceGUI |
| 做咗咩？ | 19 files, ALL pages functional, live data + controls + trade execution |
| 下一步？ | Phase 12 (Migration cleanup: switch LaunchAgent, archive old HTML) |

## Error Log
| Timestamp | Error | Resolution |
|-----------|-------|------------|
| 21:25 | TRADING_ENABLED regex not found | Added append fallback (matches existing dashboard behavior) |
