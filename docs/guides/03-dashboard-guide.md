<!--
title: Dashboard Guide (NiceGUI v3)
section: 快速入門
order: 3
audience: human,claude,github
-->

# AXC Dashboard — NiceGUI Edition

> Pure Python UI, no HTML/JS to maintain. Port **5567**.

## Quick Start

```bash
cd ~/projects/axc-trading
python3 scripts/dashboard_ng/main.py
# → http://127.0.0.1:5567
```

### Remote Access (LAN)

```python
# In main.py, change:
ui.run(host='0.0.0.0', port=5567, ...)
```

Then open `http://<your-mac-ip>:5567` from any device on the same WiFi.

### Remote Access (Internet)

```bash
brew install cloudflared
cloudflared tunnel --url http://127.0.0.1:5567
# → gives you a public https URL
```

---

## Pages

| Path | Page | Description |
|------|------|-------------|
| `/` | Dashboard | KPIs, positions, action plan, charts, news, trades |
| `/backtest` | Backtest Studio | KLineChart with 12 custom indicators, live WS |
| `/polymarket` | Polymarket | Live wallet, positions, orders, strategy config |
| `/paper` | Paper Trading | Start/stop dry-run, trade log |
| `/docs` | Documentation | Markdown docs browser |

---

## Dashboard (/) Features

### Header
- **Hamburger menu** (☰) — toggle sidebar
- **Exchange badges** — Aster / Binance / HL connection status
- **Connect button** — open exchange connect/disconnect dialog
- **Notification bell** (🔔) — 24h alert history (amber = unread)
- **Dark mode toggle**

### KPI Row
- Today PnL, Total PnL (glow: green=profit, red=loss)
- Triggers count, Open positions count

### Risk Boxes
- Market mode (TREND/RANGE/SIDEWAYS) + regime engine
- Consecutive losses / daily loss progress bars
- Drawdown % + peak value

### Controls
- **Profile**: CONSERVATIVE / BALANCED / AGGRESSIVE
- **Regime**: classic / classic_cp / bocpd / full
- **Trading**: Enabled/Disabled switch

### Positions
- Open positions with entry/mark/SL/TP/PnL/hold score
- **Close** button — market close
- **Modify** button — change SL/TP
- Pending orders with **Cancel** button

### Action Plan
- Per-symbol: price, 24h/4h/1h change, threshold, distance, SL/TP, ATR
- **Click row → opens trade modal** (5-step execution)
- **OB buttons** — order book depth for each symbol

### Charts & Analytics
- PnL history (ECharts, time filter 1H/4H/1D/7D/ALL)
- Fees breakdown, trade stats, funding rates
- News sentiment (scrollable: per-symbol, narratives, risk events)
- Scan log, trade history, activity log

### System
- Exchange connect/disconnect (Aster/Binance/HL)
- System health + mode suggestion
- 6 Mermaid workflow diagrams
- AI chat (floating button)

### Keyboard Shortcuts
- **R** — force refresh (page reload)

---

## Backtest (/backtest)

Full KLineChart v9 studio embedded via iframe:
- Candlestick chart with BB, EMA, MA, VWAP overlays
- RSI, MACD, Stoch oscillators
- Volume Profile, Footprint, CVD, Delta (custom indicators)
- Live Binance WebSocket feed
- Drawing tools (H-line, trend, rect, fib, arrow)
- Backtest engine: Classic Range+Trend / NFS+FVZ
- A/B compare, Monte Carlo, shootout
- Sidebar auto-collapsed for maximum chart space

---

## Polymarket (/polymarket)

### KPIs
- USDC Balance (live CLOB query, 20s refresh)
- Total PnL + Win Rate (from mm_trades.jsonl)
- Positions, Exposure %, Last Updated

### Controls
- **Run Cycle** — trigger 17-step pipeline (polls for result)
- **Force Scan** — Gamma API scan
- **Mode Toggle** — DRY RUN / LIVE (confirm dialog for live)
- **Check Merge** — detect mergeable positions

### Strategy Config
23 sliders + 3 toggles — writes directly to `polymarket/config/params.py`:
- Scan interval, max markets, AI temperature
- Edge thresholds (general, 15M, CVD, micro)
- Kelly fraction, min/max bet
- Risk limits, GTO thresholds
- Signal toggles (CVD, Microstructure, Hedge)

### Live Wallet Monitor
Direct CLOB API query (via miniforge subprocess):
- Real-time USDC balance
- Open orders list
- Total trades count

### Running Processes
- PID, start time, uptime for all polymarket processes
- Terminal commands for log viewing

### Pipeline Status
- Running/idle state, last run time, duration, errors

### Command Log
- Timestamped audit trail of all button actions

---

## Architecture

```
scripts/dashboard_ng/          (27 files, ~3500 lines Python)
├── main.py                    Entry point (port 5567)
├── layout.py                  Header + sidebar + footer
├── theme.py                   Design system (IBKR dark)
├── state.py                   Background data collector
├── pages/
│   ├── backtest.py            iframe → KLineChart HTML
│   ├── polymarket.py          Full polymarket controls
│   ├── paper.py               Paper trading
│   └── docs.py                Docs browser
├── components/
│   ├── stats_cards.py         KPI stat cards
│   ├── risk_boxes.py          Risk status
│   ├── controls.py            Profile/regime/trading
│   ├── positions.py           Position management
│   ├── action_plan.py         Action plan + trade modal trigger
│   ├── trade_modal.py         Order entry (5-step)
│   ├── orderbook.py           Order book display
│   ├── pnl_chart.py           PnL ECharts
│   ├── analytics.py           Fees, stats, funding, news, trades
│   ├── chat.py                AI chat panel
│   ├── notifications.py       Bell + 24h alert history
│   ├── exchange_connect.py    Exchange auth
│   ├── health.py              System health + suggest mode
│   ├── poly_config.py         Polymarket strategy sliders
│   └── diagrams.py            6 Mermaid workflow diagrams
└── utils/
    ├── backtest_api.py        FastAPI proxy for backtest endpoints
    └── poly_live.py           Miniforge subprocess bridge
```

### Key Design Decisions

| Decision | Why |
|---|---|
| NiceGUI (not Dash/Streamlit) | Event-driven, WebSocket-native, zero build pipeline |
| ECharts for dashboard charts | Built-in, candlestick native |
| KLineChart for backtest (iframe) | 12 custom indicators, Web Workers — can't replicate in Python |
| `dialog.move()` + `await dialog` | Prevents parent slot deletion from timer refresh |
| Subprocess bridge for Polymarket | py_clob_client only in miniforge, NiceGUI in homebrew |
| `run.io_bound()` everywhere | All exchange/file calls are blocking — must not freeze asyncio |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Backtest page empty | Clear browser cache: `Cmd+Shift+R`, or DevTools → Disable cache |
| Trade dialog disappears | Fixed — uses `dialog.move()` + `await dialog` |
| Polymarket balance stale | Check if `poly_live.py` subprocess works: look for errors in `logs/dashboard_ng.log` |
| Server won't start | Check port: `lsof -ti :5567` — kill existing process |
| "parent slot deleted" error | All dialogs should use `dialog.move()` pattern |
