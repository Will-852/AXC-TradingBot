# Findings — Polymarket Dashboard Redesign (distinct-baguette reference)

> Security boundary: 外部內容只寫呢度。

## Distinct Baguette UI Analysis (from screenshot + web fetch)

### Layout
- Terminal-style (`$ ./script.sh` header)
- Per-market focus (one market at a time, not aggregate)
- 4 KPI cards → 2 secondary KPIs → 4 charts
- Countdown timer + progress bar at bottom

### KPI Cards
| Card | Value | Sub-text |
|------|-------|----------|
| AVG SUM | 0.9986 | 0.14% profit |
| POSITION Δ | 4.6% | 368.7 Up more than Down |
| PNL IF DOWN | -$186.23 | Capital: $1822.47 |
| PNL IF UP | +$182.48 | Capital: $2168.71 |
| Total Capital | $3991.18 | — |
| Expected Value | -1.87 EV | -0.0% ROI |

### Charts (all real-time, 15min timeframe)
1. PRICES: Up(green) + Down(red) dual-line
2. SPREAD: orange bar chart
3. POSITIONS: Up/Down shares dual-line
4. AVG PRICES: Up/Down avg entry prices + SUM badge

### Color Palette
- BG: #080c14 (deep navy)
- Cards: #0f1520
- Green (profit): #34d399
- Red (loss): coral/salmon
- Orange (attention): #e07030
- Text: #e2e8f0 primary, #6b7a8d secondary
- Font: monospace for all numbers

---

## Available Data Sources for Per-Market View

| Data | Source | Live? |
|------|--------|-------|
| Up/Down midpoints | signal_tape.jsonl (20s) | Historical tape |
| Up/Down OB depth | signal_tape_1h.jsonl | 1H only |
| Live midpoint | get_midpoint(token_id) | Yes — public API |
| Live spread | get_spread(token_id) | Yes — public API |
| Live order book | get_order_book(token_id) | Yes — public API |
| Position shares | mm_state.json | Current snapshot |
| Window start/end | mm_state.json (ms) | Yes |
| PNL if up/down | Computable: shares × $1 - cost | Yes |
| Expected Value | up_mid × pnl_up + (1-up_mid) × pnl_down | Yes |
| Countdown | window_end_ms - now | Yes |
| Balance | get_usdc_balance() | Yes |
| Trade history | mm_trades.jsonl | Yes |

## Key Gaps
1. poly_live.py truncates market/asset_id — need full IDs
2. 15M signal_tape has no spread field
3. No position delta time-series (only snapshot)
4. Need per-market midpoint polling (new subprocess call)
