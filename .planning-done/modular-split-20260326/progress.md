# Progress: Modular Split
> Started: 2026-03-25

## Session 1 — 2026-03-25

### Round 1: Architecture Audit (completed)
- 4 parallel agents scanned 373 files (121K lines)
- Mapped: file structure, imports, responsibilities, config sprawl
- Identified 5 monster files, config divergence, dead code, 2 bugs

### Round 2: Deep Audit (completed)
- Agent 1: read all 3,980 lines of run_mm_live.py — mapped every function, constant, mutable state, cross-cutting dependency
- Agent 2: structural survey of tg_bot.py, run_1h_live.py, run_5m_live.py, backtest/engine.py
- Created task_plan.md with 5 phases + detailed split specs

### Round 3: Plan Review (completed)
- Phase 1 (run_mm_live.py) ready to execute
- Plan: 10 steps, bottom-up (constants → state_io → data_feeds → ... → orchestrator)

### Round 4: Risk Marking (completed)
- 全部 10 steps 加咗風險標記：🔴 2CHECK / 🟡 VERIFY / 🟢 SAFE
- 🔴 2CHECK 出現喺 Step 1.5-1.8（order_lifecycle, risk_guards, exit_logic, entry_logic）+ Step 1.10 驗證
- 每個 🔴 step 列出具體 2CHECK 重點（邊個值/邊個邏輯/搬錯會點）
- git diff 顯示 run_1h_live.py 有 276 行新增 → Phase 3 需重新 audit
- Awaiting user confirmation to start Phase 1 execution

### Round 5: Step 1.1-1.4 Execution (completed)
- Step 1.1 constants.py — 20 assertions passed, all values verified
- Step 1.2 state_io.py — round-trip test passed, atomic write preserved
- Step 1.3 data_feeds.py — 14 functions, _discover() rewritten after agent audit error caught
- Step 1.4 signal_pipeline.py — BPS formula verified, w4_dynamic_ratio returns 1.0

### Round 6: 2CHECK Step 1.1-1.4 (completed) — 3 parallel audit agents
- constants.py: 57/57 MATCH + 1 NEW (ts_hkt bug fix)
- data_feeds.py: 14/14 functions MATCH (logic, TTL, error handling)
- state_io.py: 9/9 functions MATCH (atomic write, to_dict 15 fields, default 11 keys)
- signal_pipeline.py: 2/2 functions MATCH (BPS formula, ws_binance backward-compatible)
- Known issue: dual cache during migration (resolves at Step 1.9)
- Known issue: exit threshold local→module promotion (handle at Step 1.9)

### Round 7: Step 1.5-1.7 Execution (completed)
- Step 1.5 order_lifecycle.py — 800+ lines, 7 functions. 2CHECK: 7/7 MATCH. No critical issues.
- Step 1.6 risk_guards.py — 4 functions. Thresholds tested with assertions. PASSED.
- Step 1.7 exit_logic.py — 4 functions (~600 lines). 2CHECK: 4/4 MATCH. All exit thresholds correct.
- Call site note: ws_poly + ws_binance must be passed explicitly in Step 1.9 wiring.

### Round 8: Step 1.8 Execution (completed)
- Step 1.8 entry_logic.py — ~1000 lines, 4 functions + 2 classes. 2CHECK: 4/4 MATCH. 0 critical issues.
  - try_entries(): ALL critical values verified ($0.99 gate, cheap tiers, share ratio, double order prevent)
  - try_w4_t2(): T2 confirmation verified (timing, budget, lean ratio)
  - place_phased_rungs(): 3-cycle cooldown + 3 checkpoints verified
  - try_reentry(): round discount + 12 state resets verified
  - Fixed: _holder_ttl hardcoded 30 → _HOLDER_CACHE_TTL constant

### Round 9: Step 1.9 + 1.10 (completed)
- Step 1.9 run_mm_live_v2.py — 682 lines (3,980 → 682, -82%). 2CHECK: PASS.
  - Call sequence 30/30 MATCH
  - Parameter wiring ALL CORRECT
  - Inline code ALL MATCH
  - Missing code NONE
- Step 1.10 Testing:
  - Full import chain: ALL OK
  - --status: PASSED (live state displayed correctly: $210.96 bankroll, 132 markets)
  - pytest: 194 passed, 0 failed (3 pre-existing config failures excluded)
- ✅ SWAP DONE: v2 → run_mm_live.py, original → .bak.2026-03-26
- Post-swap --status: PASSED (live state correct)
- Final line counts: orchestrator 682 + mm/ 3,440 = 4,122 total (was 3,980 monolith)

### Phase 1: COMPLETE ✅

## Session 2 — 2026-03-26

### Round 1: Config + Backtest Audit (completed)
- Agent 1: config divergence — 7 critical divergences found, 2 TG token hardcodes, 4 python path hardcodes
- Agent 2: backtest/engine.py — 1,554 lines, 17 import sites (not 14), 16 public API symbols to re-export
- Plan updated for Phase A (config fix) + Phase B (engine split)

### Round 2: Phase A Execution (completed)
- Step A.1: settings.py stubs fixed (5 values synced with params.py)
  - CRASH_RSI_ENTRY 75→60, CRASH_VOLUME_MIN 2.0→1.5
  - MODE_RSI_TREND_LOW 32→34, MODE_RSI_TREND_HIGH 68→69
  - MODE_CONFIRMATION_REQUIRED 2→1, SCAN_LOG_MAX_LINES 200→500
- Step A.2: backtest/engine.py now imports from params.py (7 divergent params fixed)
  - REGIME_ADJUST_ENABLED True→False, CONF_GATE/MODE_AFFINITY/PERSISTENCE all synced
  - 15 tests passed
- Step A.3: TG token moved to secrets/.env, 2 source files → os.environ.get
- Step A.4: Python path — deferred (low priority, shell scripts)

### Round 3: Phase B Execution (completed)
- Created backtest/engine_types.py (183 lines: constants + dataclasses + helpers)
- engine.py slimmed to 1,421 lines (facade re-export from engine_types)
- ALL 17 import sites verified working (re-export pattern)
- 15 tests passed post-split

### Round 4: TICK Widening (completed)
- Data analysis: 59 W4 entries, 100% fill rate, combined locked at $0.96, mid oscillates 10-15¢
- EV model: TICK=0.02 → EV +30% ($0.625 → $0.814/market)
- Changed: entry_logic.py T1 TICK 0.01→0.02, T2 0.01→0.02
- 2CHECK: all gates verified (combined, cheap side, floor), no zero-bid risk
- Reprice kept at 0.01 (intentional tighter chase) — commented
- **Monitor: need 50 markets at new TICK to validate fill rate estimate (~85%)**
Created files so far:
- polymarket/mm/__init__.py
- polymarket/mm/constants.py (~120 lines, 57 constants + ts_hkt bug fix)
- polymarket/mm/state_io.py (~130 lines, 9 functions)
- polymarket/mm/data_feeds.py (~320 lines, 14 functions)
- polymarket/mm/signal_pipeline.py (~50 lines, 2 functions)
- polymarket/mm/order_lifecycle.py (~800 lines, 7 functions)
- polymarket/mm/risk_guards.py (~160 lines, 4 functions)
- polymarket/mm/exit_logic.py (~600 lines, 4 functions)
