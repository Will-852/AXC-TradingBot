# Dashboard NG 維護 — Findings
> 所有外部/掃描內容只入呢度，唔入 task_plan.md

## Audit 1: Silent Exceptions (Sonnet agent)
- **19 處**（原估 11）— 多出嘅 8 處：
  - state.py:78 (alert check)
  - poly_market_data.py:152,156,160,164 (4× midpoint/spread)
  - strategy_panel.py:43,51,177 (cache + journal + coin config)
  - polymarket.py:579 (refresh_live — `except as e` but e never used)
- 最高風險：polymarket.py:123,136（Auth fail 零 feedback）
- 已 logged 嘅 exceptions（唔使改）：state.py:47,87,103,107 等 ~20 處

## Audit 2: polymarket.py Structure (Sonnet agent)
- 939 lines, 1 module-level `log`, 2 module-level helpers
- 所有其他 functions 都係 `render_polymarket_page()` 內嘅 closure
- 19 closure-level shared state variables（UI containers + poly_data dict）
- update_all() = 260 lines mega function = 最難拆嘅部分
- 唯一 caller: main.py:148/151
- 7 deferred imports from `scripts.dashboard.polymarket` (old backend)

## Audit 3: Hardcoded Colors (Sonnet agent)
- **~210 instances** across 20 files（原估 20+）
- 分佈：ECharts hex ~35, Quasar named ~70, Tailwind text ~80, bg-gray ~20, coin brand 5
- theme.py 已有 constants（GREEN, RED, AMBER, CYAN, ACCENT 等）但大部分文件唔用
- 值得注意：#22c55e (Tailwind green-500) vs #10b981 (theme GREEN = emerald-500) — 兩個唔同嘅 green！
- ECharts unique shades 冇 theme equivalent：#6366f1 (indigo), #34d399/#f87171 (chart up/dn), #334155, #1e3a5f

## Audit 4: Timers (Sonnet agent)
- **40+ timers** total across all pages
- Main page alone: ~28 timers
- Polymarket page: ~9 timers
- 5 clear merge groups (see task_plan.md Phase 4)
- Background loops (state.py): 5s data, 30s services, 60s exchanges — 呢啲唔使改
- 最大浪費：stats_cards 4×2s = 每 2 秒讀 4 次同一個 storage dict
