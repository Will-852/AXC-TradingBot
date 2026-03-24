# Progress: AXC Flow/Vol 優化

## Session Log

### 2026-03-25 — Phase 0 Complete
- Explored full vol/flow data pipeline (backtest.html, live-orderflow-worker.js, fetch_agg_trades.py, backtest.py)
- Identified 2 bugs: interval change no-restart, fetch overwrites live
- Confirmed no persistent cache exists
- Research: professional order flow platforms (Bookmap, Sierra, Quantower, ATAS, Exocharts)
- Created task_plan.md with 4 phases
- Awaiting Phase 1 start

### 2026-03-25 — Phase 1-4 Complete
- **Phase 1**: Created `canvas/fp-cache.js` (IndexedDB IIFE module), integrated into backtest.html
  - resetFootprintData() saves before clearing
  - loadKlines() restores from cache → instant display → background refresh
  - Fetch completion saves to cache
  - DOMContentLoaded runs cleanup
- **Phase 2**: Fixed 2 bugs
  - onIntervalChange() now restarts flow worker (both code paths)
  - Fetch completion resets _vpHistBase when live active
- **Phase 3**: yAxis position moved to 'left' (OB no longer blocks price)
- **Phase 4**: 2check audit — 0 critical, 2 medium (both fixed):
  - Added cache shape validation in _tryRestoreFromCache
  - Simplified first-load cache key logic
- 4 LOW findings accepted (cosmetic VP flash, stale markers, error overlay removal, 24h TTL)

### 2026-03-25 — Phase 5-8 Complete (Flow UI Upgrade)
- **Phase 5**: Custom `AXC_VOL` indicator replaces built-in VOL
  - Per-bar delta coloring (teal=positive delta, red=negative)
  - Fallback to candle direction when no footprint data
  - MA 5/10/20 lines preserved
  - 🔴 CRITICAL found + fixed: KLineChart v9 `styles` callback path was wrong (`data.indicator` → `d.indicatorData`)
- **Phase 6**: VP moved from left → right side
  - maxBarWidth 15% → 25%
  - Right-aligned bars (grow leftward from chart edge)
  - 🟡 Legend repositioned to `chartRight - maxBarWidth - 150` to avoid overlap
- **Phase 7**: POC/VA visual enhancement
  - POC: dashed→solid, lineWidth 1→1.5, opacity 0.50→0.75, label 10→11px
  - VA: fill 0.06→0.10, border 0.25→0.40, lineWidth 1→1.5
  - Added VAH/VAL labels at boundary lines
- **Phase 8**: 2check audit — 1 CRITICAL (fixed) + 2 MEDIUM (fixed)

### 2026-03-25 — NiceGUI Position Card Fix
- **File**: `scripts/dashboard_ng/components/positions.py`
- **Fix 1**: Mark/PnL 精度 — 加 `_fmt_price()` (BTC>100 → 1dp, small → 4dp) + `_fmt_pnl()` (2dp with sign)
- **Fix 2**: Hold Score raw JSON — 加 `_parse_hold_score()` 支援 dict + JSON string + Python repr string
  - Factors 改為 inline badges（唔再 dump raw JSON）
- **Fix 3 🔴**: SL/TP 完全壞 — `handle_modify_sltp` expects JSON string + `sl_price`/`tp_price` keys，但 NiceGUI dialog 傳 dict + `sl`/`tp` keys
  - 同樣修 `_close_position` (一樣嘅 JSON string 問題)
  - handler 返回 `(status, data)` tuple，加 isinstance check

### 2026-03-25 — 2check Audit Fixes (positions.py)
- 🔴 **cancel_order 同一個 JSON string bug** — 一齊修（JSON string + tuple handling + try/except）
- 🟡 **_parse_hold_score** — 加 True/False/None → true/false/null regex 替換
- 🟡 **exception handling** — `_close_position` + `_show_modify_dialog` 加 except + log + notify
- 🟡 **Close confirmation** — 加 dialog 確認（真金白銀，唔可以 mis-click）
- 🟡 **dict | None type hint** — 改為冇 type annotation（3.9 compat）
