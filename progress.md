# Dashboard NG 維護 — Progress Log
> Session: 2026-03-28

## Phase 0: Recon + Planning ✅
- [x] Read existing maintenance plan from memory
- [x] Spawned 4 Sonnet audit agents in parallel
- [x] Audit 1: Silent exceptions — 19 found (was 11)
- [x] Audit 2: polymarket.py structure — 939L, 20+ closures, update_all 260L
- [x] Audit 3: Hardcoded colors — ~210 instances, 20 files
- [x] Audit 4: Timers — 40+ total, 5 merge groups
- [x] Created task_plan.md + findings.md + progress.md
- [x] User confirmed → start Phase 1

## Phase 1: Silent Exceptions ✅
- [x] state.py:78 — log.warning
- [x] poly_market_data.py:152,156,160,164 — print to stderr (subprocess script)
- [x] poly_market_data.py:203 — log.debug
- [x] poly_market_data.py:224 — log.warning
- [x] polymarket.py:49,60,72,92 — log.warning
- [x] polymarket.py:123,136 — **log.error** (💰 auth)
- [x] polymarket.py:579 — log.warning (💰 CLOB balance)
- [x] polymarket.py:734 — log.warning
- [x] strategy_panel.py:43,51 — log.debug + added logger
- [x] strategy_panel.py:177 — log.warning
- [x] positions.py:128 — log.debug
- [x] py_compile all 5 files — OK
- [x] 2check agent — **Clean pass**

## Phase 2: polymarket.py Split ✅
- [x] Created poly_helpers.py (104L) — pure data functions
- [x] Created poly_auth_ui.py (172L) — auth section + credentials dialog
- [x] Created poly_display.py (313L) — update_all + display logic
- [x] Slimmed polymarket.py (941→425L, 55% reduction)
- [x] py_compile all 4 files — OK
- [x] main.py import unchanged — no caller changes needed
- [x] 2check agent — **🟢 Clean pass all 4 roles**
  - ctx dict: 10/10 keys matched
  - toggle_mode LIVE confirm: intact
  - Auth L1/L2: preserved exactly
  - Closure reference timing: safe

## Phase 3: Color Centralization ✅
- [x] Added 8 new theme constants: GREEN_LIGHT, RED_LIGHT, INDIGO, CHART_UP/DN, CHART_AXIS/GRID, CHART_TOOLTIP_BG/BORDER
- [x] pnl_chart.py: 7 hex → theme constants (tooltip, axis, grid, accent, green, red)
- [x] poly_market_view.py: ~20 hex → theme constants (all axis/grid/line colors)
- [x] health.py: status dot #22c55e/#ef4444 → GREEN_LIGHT/RED
- [x] exchange_connect.py: dot #22c55e/#6b7280 → GREEN_LIGHT/TEXT_MUTED
- [x] poly_display.py: CB dots → GREEN_LIGHT/RED
- [x] polymarket.py: PnL chart axis/grid → CHART_AXIS/CHART_GRID/INDIGO
- [x] layout.py: sidebar dot → GREEN/TEXT_MUTED
- [x] py_compile all 8 files — OK
- Decision: Tailwind text-color (80 instances) 唔換 — blast radius 太大
- Decision: Mermaid string template 唔換 — 唔能用 Python constant
- Decision: Coin brand colors 留 — 品牌色唔跟 theme

## Phase 4: Timer Consolidation ✅
- [x] risk_boxes.py: 3×5s timers → 1 timer, 1× get_data() (saved 2 redundant reads/5s)
- [x] stats_cards.py: 4×2s timers → 1 timer, 1× get_data() (saved 3 redundant reads/2s)
- [x] strategy_panel.py: 4 timers + shared file cache with 2s TTL (saved 2 file reads/5s)
- [x] py_compile all 3 files — OK
- Skipped: analytics.py — timers at different intervals (5s/10s/30s), merging breaks encapsulation
- Skipped: polymarket.py once pile — init-only, zero runtime cost
