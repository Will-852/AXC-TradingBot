# Dashboard NG 維護修正 — Task Plan
> Created: 2026-03-28 | Scope: scripts/dashboard_ng/ only
> ⚠️ 唔涉及 polymarket bot 邏輯、唔涉及 indicator_engine、唔涉及 BMD P2/P3

## Goal
提升 Dashboard NG 可維護性：消滅 silent exceptions、拆 God file、統一 colors、減 timer。

---

## Phases

### Phase 1: Silent Exceptions → Logger (P0) `complete`
19 bare except:pass → log.warning/log.error across 5 files. 2check passed.

### Phase 2: polymarket.py Split (P1) `complete`
939→4 sub-modules: poly_helpers.py (104L), poly_auth_ui.py (172L), poly_display.py (313L), polymarket.py (425L). 2check passed all 4 roles.

### Phase 3: Color Centralization (P1) `complete`
| Category | Count | Action |
|----------|-------|--------|
| ECharts hex (duplicates theme) | ~35 | Replace with theme constants |
| Quasar named colors | ~70 | Map to theme semantic names |
| Tailwind text-color | ~80 | Keep（CSS class 唔易換 Python constant）|
| bg-gray-* (duplicates surface/border) | ~20 | Replace with theme CSS vars |
| Coin brand colors | 5 | Keep hardcoded（品牌色唔入 theme）|
| New theme shades needed | ~8 | #22c55e, #6366f1, #34d399, #f87171, #334155, #1e3a5f, #1e293b |

**Strategy**:
1. Add CHART_PALETTE dict to theme.py（ECharts 專用）
2. Add semantic constants: UP_LINE, DN_LINE, TOOLTIP_BG, TOOLTIP_BORDER
3. Replace hex in ECharts configs → theme constants
4. Tailwind classes 留喺原位（改動 blast radius 太大，收益低）

**Estimate**: ~1.5h | **Files**: ~12 | **Trading risk**: None

---

### Phase 4: Timer Consolidation (P2) `complete`
| Group | Current | Target | Saving |
|-------|---------|--------|--------|
| risk_boxes.py 3×5s | 3 timers, 3× get_data() | 1 timer, 1× get_data() | 2 redundant reads/5s |
| stats_cards.py 4×2s | 4 timers, 4× get_data() | 1 timer, 1× get_data() | 3 redundant reads/2s |
| analytics.py 4×10s | 4 timers, 4× get_data() | 1 timer, 1× get_data() | 3 redundant reads/10s |
| strategy_panel.py 3×5s | 3 timers, 2× file read | 2 timers, 1× shared read | 1 redundant file read |
| polymarket.py once pile | 6 once=True | 1 on_load() | Cleaner init |

**Estimate**: ~1h | **Files**: 5 | **Trading risk**: None（唔改 data source，只改 read pattern）

---

## Errors Log
| # | Phase | Error | Resolution |
|---|-------|-------|------------|
| 1 | P2 | progress.md + task_plan.md overwritten by 1H Strategy Upgrade session | Restored manually |

## Decisions
| # | Decision | Why |
|---|----------|-----|
| 1 | Tailwind text-color 唔換 | Blast radius 太大（80處），收益低（已經 consistent） |
| 2 | Coin brand colors 唔入 theme | 品牌色唔應該跟 theme 變 |
| 3 | Phase 1 先做 | 零風險，30-45 min，即時改善 debug 能力 |
| 4 | Phase 2 前要 2check auth flow | 💰 Auth 斷 = Polymarket 操作失敗 |
| 5 | Phase 2 用 ctx dict pattern | 最低風險方式 pass closure refs 跨 module |
