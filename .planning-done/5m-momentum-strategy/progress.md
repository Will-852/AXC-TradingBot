# Progress: Polymarket 5M Momentum Strategy

## Session Log

### 2026-03-25 — Research + Analysis Complete
- Analyzed 5 wallets (real trade data, 1,971 lines)
- Ran 30-day momentum WR calculator (8,639 windows, 2.59M 1s klines)
- Multiple BMD rounds: 4 original phases all killed, rebuilt from data
- 12h live data analysis: WR 35.1%, lean accuracy 58.1%
- Identified root cause: momentum paradox (maker lean doesn't fill)
- Strategy evolved: arb → directional → hybrid (taker lean + maker hedge)
- Created `strategies/five_m_momentum/` package (config, signal, modes, run)
- Multiple 2check rounds with opus subagent audits
- Key discovery: 5+5 shares = zero directional edge at $5 budget
- Key discovery: FOK is not a true market order (SDK fetches book first)
- Key discovery: 5M spreads wider than 15M (combined 0.80-0.98)
- Saved learnings to gotchas.md, lesson_for_me.md, memory/

### 2026-03-25 — Implementation Plan Created
- Archived old planning files (dashboard-flow-vol)
- Created 6-phase implementation plan
- Phase 1-4: config → signal → modes → run.py rewrite
- Phase 5: integration test
- Phase 6: final 2check + audit
- Each phase: opus subagent line-by-line audit
- Awaiting Phase 1 start

### 2026-03-25 — Phase 1 Complete (config.py)
- Rewrote config.py with three-tier decision tree params
- New params: ARB_UPPER_BPS=15, TAKER_ASK_BUFFER=0.02, TAKER_ASK_CAP=0.55, SESSION_MAX_LOSS=60
- Added backwards-compatible aliases for run.py (to be removed after Phase 4)
- Opus audit found: 8 missing imports (fixed via aliases), dead params (expected, Phase 2-4 will wire)
- Fee formula docstring discrepancy noted (comment only, no code impact)
- ENTRY_CYCLE_S=2 (was 5), BANKROLL_USD=200 (was 131)
- All imports verified, run.py AST parse OK

### 2026-03-25 — Phase 2 Complete (signal.py)
- Rewrote signal.py with three modes: SKIP / MAKER_ARB / TAKER_DIRECTIONAL
- Opus audit found: lean_ratio AttributeError (FATAL) — fixed by restoring field
- Fixed: raw_prices empty dict → always 3 keys
- Fixed: docstring boundary > → >= consistency
- Fixed: enhance_signal clamp warning added
- Added ⚠️ RISK markers at: lean_ratio serialization, float boundary precision, confidence clamp
- 12/12 boundary tests pass, all modes produce correct lean_ratio
- Float precision note: 8.0bps exact → 7.9999 → SKIP (cosmetic, real prices never exact)

### 2026-03-25 — Phase 1 2check + Phase 3 Complete (config RISK + modes)
- config.py 2check by opus: 3 🔴 (cycle/buffer desync, live=True no gate), 5 🟡
- Added 6 ⚠️ RISK markers to config.py (desync, bankroll scale, daily cap, fee, live gate)
- Rewrote `single_side.py` → `taker_directional.py` semantics:
  - `plan_taker_directional()` — aggressive GTC limit (NOT FOK)
  - `plan_hedge()` — optional maker hedge (placed after lean fill)
  - Renamed `SingleSideOrder` → `TakerDirectionalOrder`
- Updated `maker_arb.py` docstring (removed "lean 1.36:1", clarified R=1.0 = equal sides)
- Aligned all function defaults with config values + added ⚠️ RISK markers
- All 5 mode tests pass: ARB 5+5, DIRECTIONAL 9sh, HEDGE 5sh, ASK_CAP, SKIP

### 2026-03-25 — Phase 4 Complete (run.py rewrite)
- Full rewrite: extracted _run_cycle, _execute_arb, _execute_directional
- Opus audit found 3 FATAL: naked hedge on lean fail, double buffer, no loop exception handling
- Fix 1: try/except around all buy_shares + pre-submit cid save (prevents double-submit)
- Fix 2: double buffer removed (pass lean_mid to plan_taker_directional, function adds buffer once)
- Fix 3: top-level try/except in while loop (catches transient errors, saves state, continues)
- All config params wired (no hardcodes remain): ENTRY_CYCLE_S, SCAN_INTERVAL_S, WINDOW_GIVE_UP_PCT, TAKER_ASK_BUFFER
- Resolution check: _check_resolution computes PnL from BTC open/close
- ⚠️ RISK: PnL assumes planned fills (not actual fills) — noted for fill tracking Phase 2
- 7 ⚠️ RISK markers in run.py

### 2026-03-25 — Phase 5 Complete (Integration Tests)
- 7/7 tests pass: imports, signal boundaries, arb planning, taker directional, hedge, skip, AST
- SessionState tested via AST parse (full test needs py_clob_client venv)
- run.py structure: main → _run_cycle → _execute_arb / _execute_directional / _check_resolution
- Note: run.py can only be fully tested in Polymarket venv (py_clob_client dependency)

### 2026-03-25 — Phase 6 Complete (Final 2check)
- Opus final audit: 2 🔴, 8 🟡, 3 🟢
- 🔴 FIX: `session.session.last_scan_s` typo → `session.last_scan_s` (crash bug)
- 🔴 FIX: `max_hedge_price=ARB_MAX_PRICE` → `HEDGE_MAX_PRICE` (arb hedge cap wrong)
- Cleaned 4 stale RISK markers (config params already wired to run.py)
- Remaining open: DAILY_LOSS_CAP unimplemented, BANKROLL_USD manual, fee change 3/30, resolution PnL assumes both fills
- Total ⚠️ RISK markers: 15 (config=4, signal=4, run=7)

### 2026-03-25 — Phase 6 Round 2 (Unchanged code paths)
- Opus deep audit on 10 specific untouched areas
- 🔴 FIX: mid filter 0.05-0.95 → 0.02-0.99 (was rejecting real 0.96 prices on strong signals)
- 🔴 FIX: HTTP timeout 10s → 3s per request (35s worst-case cascade → ~12s)
- 🟡 FIX: plan_hedge now returns None when raw_price > max or <= 0.01 (was always returning order)
- Confirmed clean: global scoping, dict iteration, float precision, $0.01 floor, kline timing
- Remaining open: #6 resolution kline off-by-1s (need to verify Polymarket resolution rule), #10 entered_cids unbounded (OK at 20 trades)
