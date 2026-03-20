# Plan: NiceGUI Dashboard — Remaining Features

## Goal
補齊所有舊 HTML dashboard 有但 NiceGUI 冇嘅功能。唔改外觀，只加功能。

## Phases

### Phase A: Exchange Connect/Disconnect UI
- [ ] A1: Connect modal — API key + secret 輸入
- [ ] A2: Disconnect button (per exchange)
- [ ] A3: Badge 更新 after connect/disconnect
- [ ] A4: Aster + Binance + HyperLiquid 三個都做
- **Backend:** `handle_aster_connect/disconnect`, `handle_binance_connect/disconnect`, `handle_hl_connect/disconnect`
- **Status:** pending

### Phase B: Order Book Display
- [ ] B1: Order book modal (click symbol → show depth)
- [ ] B2: Bids/Asks display (horizontal bars)
- [ ] B3: Spread + mid price
- [ ] B4: Auto-refresh 10s (ui.timer)
- **Backend:** `handle_orderbook(qs)`
- **Status:** pending

### Phase C: Health Page
- [ ] C1: Agent statuses (scanner heartbeat, file timestamps)
- [ ] C2: Memory count (embeddings)
- [ ] C3: Uptime display
- [ ] C4: Suggest mode (AI recommendation based on BTC 24h change)
- **Backend:** `handle_api_health()`, `handle_suggest_mode()`
- **Status:** pending

### Phase D: Utility Features
- [ ] D1: Open folder in Finder button (action plan rows)
- [ ] D2: File read endpoint (for docs page fallback)
- [ ] D3: Debug endpoint (/api/debug)
- **Status:** pending

## Not Doing (low value / collaborator-only)
- Share page + ZIP download — collaborator feature
- Details page — replaced by /docs
