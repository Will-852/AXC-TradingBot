# Progress: Phase 3+4 Split
> Started: 2026-03-26

## Session 1

### Round 1: Audit (completed)
- Agent 1: run_1h_live.py — 2,069 lines, 28 functions, 8-module split planned
- Agent 2: run_5m_live.py — 1,660 lines, 22 functions, 5-module split planned
- Agent 3: quick fixes done — 4 python path hardcodes fixed, 3 dead code files identified

### Quick fixes applied:
- scanner_runner.py: PYTHON hardcode → os.environ.get("PYTHON3", "python3")
- crypto_15m.py: subprocess hardcode → os.environ.get
- integration_test.sh: hardcode → ${PYTHON3:-python3}
- log_cleanup.sh: hardcode → ${PYTHON3:-$(which python3)}

### Round 2: Phase 3 Steps 3.1-3.3 (completed)
- Step 3.1 constants.py — 28+ constants, all values verified
- Step 3.2 state_io.py — 9 functions (load/save/log/bump_fill/to_dict/from_dict/tg_alert/record_signal_tape)
- Step 3.3 data_feeds.py — 8 functions (btc_price/holder/vol_imbalance/vol_1m/poly_midpoint/poly_ob/binance_open/get_json)
- All imports verified OK

## Session 2

### Round 3: Phase 3 Steps 3.4-3.8 (in progress)

**Scout findings (corrected from Session 1 estimates):**
- order_lifecycle.py: ~520 lines (was ~370) — `_reprice_1h` alone is 277 lines
- exit_logic.py: NON-CONTIGUOUS — Block A (lines 634-734) + Block B (lines 1309-1380)
- 3 completed modules clean, no TODO/FIXME

**Execution order (🟢 first, 🔴 last):**
1. ✅ 3.4 analysis.py 🟢 — 130 lines, _collect_analysis + 1 mutable global
2. ✅ 3.5 paper_trading.py 🟡 — 147 lines, 3 functions + _paper_state global
3. ✅ 3.8 discovery.py 🟢 — 64 lines, _build_slug + _discover
4. ✅ 3.7 exit_logic.py 🔴 — 202 lines, merged 2 non-contiguous blocks OK
5. ✅ 3.6 order_lifecycle.py 🔴 — 552 lines, _repricing_cid preserved + get_repricing_cid() accessor

**Dependency map per module:**
| Module | From constants | From data_feeds | From state_io | From paper_trading | External |
|--------|---------------|-----------------|---------------|-------------------|----------|
| analysis | _ANALYSIS_TAPE, _DATA_API | _get_json | — | — | — |
| paper_trading | _PAPER_PNL_LOG, _HKT | — | — | — | — |
| discovery | _GAMMA, _COIN_SLUGS, _ET | _get_json | — | — | GammaClient, datetime |
| exit_logic | _BINANCE, _HKT, _coin_from_title | poly_midpoint, _get_json | log_trade, to_dict, from_dict, bump_fill, log_order | _paper_resolve | resolve_market |
| order_lifecycle | reprice consts, _coin_from_title | btc_price, poly_midpoint, poly_ob | log_order, bump_fill | — | conviction_signal, OBState |

**Fixes applied during extraction:**
- state_io.py: added per-coin log paths (`_trade_path`/`_order_path`) + `coin` param to `log_trade`/`log_order`
- constants.py: added `_coin_from_title` utility (shared by exit_logic + order_lifecycle)
- order_lifecycle.py: added `get_repricing_cid()` accessor for orchestrator's one-order guard

**Total: 8 modules, 1,551 lines extracted.**

### Round 4: Steps 3.9-3.10 (completed)
- Step 3.9 slim orchestrator — 2,069 → 713 lines (-65%)
  - import-as aliases: zero body changes except `_repricing_cid` → `get_repricing_cid()`
  - Only 4 `def` remain: _shutdown, run_cycle, _status, main
  - Backup: run_1h_live.py.bak.pre-slim
- Step 3.10 test — all passed:
  - ✅ `import polymarket.run_1h_live` — OK
  - ✅ `py_compile` — OK
  - ✅ `--status` mode — OK (reads real state file)
  - ✅ `--dry-run --cycle` — OK (9 markets, heavy loop 3.4s, zero errors)
  - ✅ No orphaned function definitions (grep: 4 `def` only)
  - ✅ All exports accessible

**Phase 3 COMPLETE.**

## Session 3 — Phase 4: run_5m_live.py split

### Round 5: Audit + Plan (completed)
- Scout: run_5m_live.py = 1,660 lines, 28 functions, 5-module split planned
- mom_5m/ directory does NOT exist yet
- Key risks: _w4_entry implicit Union return, WS global injection, cross-layer _check_resolutions

### Execution order (🟢 first, 🔴 last):
1. ⬜ 4.1 config.py 🟢 — CoinConfig + all constants + TG (~115 lines)
2. ⬜ 4.2 data.py 🟡 — 6 functions + 4 caches (~120 lines)
3. ⬜ 4.3 signal.py 🟡 — w4_signal + discover_5m (~100 lines)
4. ⬜ 4.5 state.py 🟡 — 10 functions incl check_resolutions (~200 lines)
5. ⬜ 4.4 execution.py 🔴 — 5 functions incl w4_entry(200L) (~500 lines)
6. ⬜ 4.6 slim run_5m_live.py — orchestrator (~300 lines)
7. ⬜ 4.7 test

### Round 6: Phase 4 Steps 4.1-4.7 (completed)
- 4.1 config.py 🟢 — 128 lines (CoinConfig + all constants + TG + _tier_lean_ratio)
- 4.2 data.py 🟡 — 150 lines (6 functions + 4 caches + set_ws_feeds)
- 4.3 signal.py 🟡 — 113 lines (w4_signal + discover_5m)
- 4.5 state.py 🟡 — 263 lines (state IO + kill switches + check_resolutions)
- 4.4 execution.py 🔴 — 535 lines (w4_entry + execute_order + check_fills + cancel + profit_lock)
- 4.6 slim run_5m_live.py — 1,660 → 546 lines (-67%)
- 4.7 test — all passed:
  - ✅ import + py_compile OK
  - ✅ --status mode (reads real state, shows 206 markets traded)
  - ✅ --dry-run --cycle (20 markets discovered, W4 signals firing, 0 errors)
  - ✅ Only 4 `def` in orchestrator (_shutdown, run_cycle, _status, main)
  - ✅ WS connected (Binance + Polymarket)

**Phase 4 COMPLETE.**

### Round 7: Dead code archive (completed)
- signal_engine.py (523L) → .archive/ (zero imports confirmed)
- ob_recorder.py (622L) → .archive/ (zero imports confirmed)
- params.py (~30L) → .archive/ (zero imports confirmed)
- Both bots import OK after archive

**All phases COMPLETE.** Ready for handin.
