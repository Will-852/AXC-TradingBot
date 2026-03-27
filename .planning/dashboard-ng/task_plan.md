# Dashboard NG 維護修正 — Task Plan
> Created: 2026-03-28 | Scope: scripts/dashboard_ng/ only
> ⚠️ 唔涉及 polymarket bot 邏輯、唔涉及 indicator_engine、唔涉及 BMD P2/P3

## Goal
提升 Dashboard NG 可維護性：消滅 silent exceptions、拆 God file、統一 colors、減 timer。

---

## Phases

### Phase 1: Silent Exceptions → Logger (P0) `complete`
| # | File | Line | Context | Fix | 💰Risk |
|---|------|------|---------|-----|--------|
| 1 | state.py | 78 | _check_for_alerts() | log.warning | — |
| 2 | poly_market_data.py | 152 | up_mid fetch | log.warning + keep fallback 0 | — |
| 3 | poly_market_data.py | 156 | dn_mid fetch | log.warning + keep fallback 0 | — |
| 4 | poly_market_data.py | 160 | up_spread fetch | log.warning + keep fallback 0 | — |
| 5 | poly_market_data.py | 164 | dn_spread fetch | log.warning + keep fallback 0 | — |
| 6 | poly_market_data.py | 203 | mm_signals.jsonl read | log.debug (high freq) | — |
| 7 | poly_market_data.py | 224 | mm_state.json read | log.warning | — |
| 8 | polymarket.py | 49 | wallet portfolio value | log.warning | — |
| 9 | polymarket.py | 60 | positions count | log.warning | — |
| 10 | polymarket.py | 72 | CLOB balance | log.warning | — |
| 11 | polymarket.py | 92 | data API + deposit | log.warning | — |
| 12 | polymarket.py | 123 | 💰 Auth L1 key check | **log.error** | Auth fail = 操作冇權限 |
| 13 | polymarket.py | 136 | 💰 Auth L2 creds | **log.error** | Auth fail = 操作冇權限 |
| 14 | polymarket.py | 734 | risk_mode KPI | log.warning | 顯示錯誤 risk mode |
| 15 | polymarket.py | 579 | 💰 refresh_live() CLOB | log.warning | Balance 顯示 stale |
| 16 | strategy_panel.py | 43 | cache read | log.debug | — |
| 17 | strategy_panel.py | 51 | journal read | log.debug | — |
| 18 | strategy_panel.py | 177 | coin config import | log.warning | Matrix 全 disabled |
| 19 | positions.py | 128 | repr parse | log.debug | — |

**Estimate**: ~45 min | **Files**: 5 | **Trading risk**: None（只加 logging）

---

### Phase 2: polymarket.py Split (P1) `pending`
| Sub-module | Functions | Est. Lines |
|------------|-----------|------------|
| `poly_auth.py` | _check_auth, refresh_auth, open_settings, save_creds | ~180 |
| `poly_ops.py` | run_cycle, _poll_cycle, force_scan, toggle_mode, check_merge, reset_cb, on_start, on_stop, on_sched_change, refresh_procs, log_cmd | ~250 |
| `poly_analytics.py` | _fetch_data_api, refresh, refresh_live, refresh_cycle, _update_cycle_status, update_all | ~380 |
| `polymarket.py` (slim) | page layout + wire + container declarations | ~130 |

**Key challenge**: update_all() 260 lines = mega function，touches 所有 container refs。
**💰 2check points**:
- poly_auth.py: Auth state 喺 closure → 要改成 parameter passing 或 shared dict
- poly_ops.py: 💰 toggle_mode() 控制 DRY/LIVE mode，改錯 = 真錢操作
- poly_analytics.py: refresh_live() 讀 CLOB balance，display 錯 = 用戶誤判
- Circular import risk: auth state 被多處引用

**Estimate**: ~2h | **Files**: 4 new + 1 modified | **Trading risk**: Medium（改 UI wiring 可能斷操作流）

---

### Phase 3: Color Centralization (P1) `pending`
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

### Phase 4: Timer Consolidation (P2) `pending`
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
| — | — | — | — |

## Decisions
| # | Decision | Why |
|---|----------|-----|
| 1 | Tailwind text-color 唔換 | Blast radius 太大（80處），收益低（已經 consistent） |
| 2 | Coin brand colors 唔入 theme | 品牌色唔應該跟 theme 變 |
| 3 | Phase 1 先做 | 零風險，30-45 min，即時改善 debug 能力 |
| 4 | Phase 2 前要 2check auth flow | 💰 Auth 斷 = Polymarket 操作失敗 |
