# Plan: Dashboard vs TradingView — Fill All Gaps

## Scout Summary
| 功能 | 狀態 | 行數 |
|------|------|------|
| Drawing tools | PARTIAL (6/10 tools) | +20 |
| Template system | EXISTS (minor bugs) | +15 fix |
| Crosshair sync | PARTIAL (KLC 內建) | 0 |
| Alert system | MISSING | +120 |
| Indicator calc | EXISTS (backend only) | +200 rewrite |
| Multi-TF hints | MISSING | +80 |
| Replay | MISSING (defer) | — |
| Screener | MISSING (defer) | — |

## Phases (priority order)

### Phase 1: JS Real-time Indicators `status: pending`
**最大 UX improvement — 唔使再 click 回測就有指標**
- Rewrite 7 calc functions: BB, EMA, MA, VWAP, RSI, MACD, Stoch
- Hybrid: JS 計 + backend override（如果有 backtest data）
- Remove ⚡回測 限制 + gateIndicators non-1H block
- ~200 lines changed

### Phase 2: Drawing Tools Complete `status: pending`
- Add 4 missing tools: straightLine, simpleAnnotation, priceLine, verticalStraightLine
- ~20 lines HTML

### Phase 3: Price Alert Lines `status: pending`
- Alert button → prompt price → yellow dashed line
- Live check: price cross → toast + beep
- localStorage persistence
- ~120 lines

### Phase 4: Template System Fix `status: pending`
- Sync checkbox DOM on load
- Close menu after selection
- XSS escape template names
- ~15 lines

### Phase 5: Multi-TF Hints `status: pending`
- Badges in toolbar: [1H ▲] [4H ▼]
- Aggregate candles → trend detection
- ~80 lines

## Deferred (too complex for now)
- Replay/Playback
- Full Multi-chart split view
- Screener
