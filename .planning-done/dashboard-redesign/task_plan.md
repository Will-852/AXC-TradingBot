# Task Plan: Polymarket Dashboard Redesign (distinct-baguette style)

## Goal
將 Polymarket page 由 aggregate view 改為 **per-market focused dashboard**，
參考 distinct-baguette.com：PNL scenarios, price chart, spread, position delta, countdown timer。

## Current Phase
Phase 0

## Phases

### Phase 0: Data Layer — per-market live query
- [ ] 0A: Fix poly_live.py — return full condition_id + token_ids (唔 truncate)
- [ ] 0B: Add get_midpoint(token_id) + get_spread(token_id) to poly_live subprocess
- [ ] 0C: Read mm_state.json for active markets (up_shares, down_shares, window_end_ms)
- [ ] 0D: Compute PNL IF UP / PNL IF DOWN from shares + entry_cost
- [ ] 0E: Compute Expected Value = up_mid × pnl_up + (1-up_mid) × pnl_down
- [ ] 0F: Test all data sources return correct values
- **Status:** pending
- **Estimate:** 1 session

### Phase 1: Per-market KPI cards
- [ ] 1A: Market selector (dropdown of active markets from mm_state.json)
- [ ] 1B: AVG SUM card (up_mid + dn_mid — should ≈ 1.0)
- [ ] 1C: POSITION Δ card (up_shares - down_shares, % diff)
- [ ] 1D: PNL IF DOWN card ($, capital)
- [ ] 1E: PNL IF UP card ($, capital)
- [ ] 1F: Total Capital card
- [ ] 1G: Expected Value card (EV + ROI%)
- [ ] 1H: Countdown timer (window_end_ms - now → MM:SS)
- **Status:** pending
- **Estimate:** 1 session

### Phase 2: Per-market charts
- [ ] 2A: PRICES chart — Up(green) + Down(red) dual-line EChart
  - Data from signal_tape.jsonl filtered by condition_id
  - Or live polling get_midpoint() every 10s
- [ ] 2B: SPREAD chart — bar chart from get_spread()
- [ ] 2C: POSITIONS chart — Up/Down shares over time (if data available)
  - May need to poll mm_state.json snapshots
- [ ] 2D: AVG PRICES chart — entry avg prices + SUM badge
- **Status:** pending
- **Estimate:** 1-2 sessions

### Phase 3: Layout restructure
- [ ] 3A: Per-market focused top section (selected market's full data)
- [ ] 3B: Aggregate section below (balance, total PnL, orders list — existing)
- [ ] 3C: Remove/merge redundant sections
- [ ] 3D: Terminal-style header (optional aesthetic)
- [ ] 3E: Progress bar at bottom (% through market window)
- **Status:** pending
- **Estimate:** 1 session

### Phase 4: 2check + polish
- [ ] Re-read all changed files
- [ ] Test with live data
- [ ] BMD edge cases
- **Status:** pending

## Decisions
| Decision | Rationale |
|----------|-----------|
| Per-market focus, not replace aggregate | User still needs balance/orders/config |
| Read mm_state.json directly (not API) | Zero API cost, already updated by bot |
| signal_tape for historical, get_midpoint for live | Hybrid: tape = history, live = current |
| ECharts for all charts | Consistent with rest of dashboard |
| Market selector dropdown | User picks which market to focus on |

## Risk
| Risk | Mitigation |
|------|------------|
| mm_state.json empty (no active markets) | Show "No active markets" message |
| signal_tape missing for selected market | Fallback to live polling only |
| Too many API calls from per-market polling | Rate limit: 1 call per 10s per token |

## Errors
| Error | Attempt | Resolution |
|-------|---------|------------|
