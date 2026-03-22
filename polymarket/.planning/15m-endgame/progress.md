# Progress: 15M Last-Minute Strategy
> Session log

## 2026-03-22

### Phase 0 started
- Created planning files
- Saved AS binary market analysis to memory
- Z-score math review completed (5 corrections accepted)

### Phase 0 — Research Round 1 (3x opus agents)
- Agent 1 (microstructure): 209 markets analyzed. Cheap tokens in decided markets = 0% WR. 43.7% undecided at T-120s.
- Agent 2 (code paths): Full TTE timeline mapped. Forced hold T-300s, cancel T-120s, late gate T-90s. No black swan override.
- Agent 3 (signal quality): Whale signal DEAD (100% NORMAL). h_delta DEAD (100% zero). PnL deteriorating.

### BMD Round 1 (self-attack)
- Straw man fallacy: tested "buy losing side" not "buy in undecided market"
- 36.4% flip rate has CI [11%, 69%] — n=11 too small
- PnL decline correlates perfectly with code changes timeline
- "Market rejects at 4 min" = unverified assumption

### Phase 0 — Research Round 2 (3x opus gating tests)
- Gate 1: Late order rejection = LIKELY FALSE. Zero evidence. Self-imposed guards only.
- Gate 2: Vol regime = MIXED. Mar 21 vol dead, Mar 22 recovered but fills didn't → code issue
- Gate 3: Signal logging better than thought (cross-exchange IS in signal_log). But structural gaps remain.

### Key Corrections to Earlier Findings
1. Cross-exchange signals ARE logged (nested `mkt` dict in mm_signals.jsonl) → Agent 3 was wrong
2. "Cheap token 0% WR" answered wrong question → undecided markets are the real target
3. WR corrected (excl zero-fills): 79% → 78% → 20%(n=5) — model accuracy stable through Mar 21, collapsed Mar 22
4. Bridge confidence increased while momentum decreased → model overconfident after code changes

## 2026-03-23

### Phase 1: Gating Verification — `complete`
- **Gate 1 PASSED**: Market accepts orders until T+50s. "Rejects at 4 min" = 100% false.
  - Live poll: acceptingOrders=True from TTE=590s → TTE=-50s (22 readings, all True)
  - Data API: trades happen through T-0s and beyond (TTE=-13s latest)
  - 0/106 orders rejected in logs
- **Fill rate by rung**: $0.37 = 32.4% (highest), $0.43 = 23.5%, $0.31 = 11.1%, $0.26 = 10.0%
- **Fill rate decline**: Mar 20 34.2% → Mar 22 17.8%. Vol recovered on Mar 22 but fills didn't → likely code/pricing issue
- **Corrected WR**: Mar 20-21 stable at 78-79%. Mar 22 = 20% (n=5, too small for conclusion)
- Phase 1 result: **GREEN LIGHT for last-minute strategy development**

### Phase 4: Endgame Implementation — `in_progress`
- Added `from collections import deque` (line 38)
- Added 8 endgame constants after `_LIVE_TRADE_COINS` (lines 95-102)
- Added `_endgame_mid_buf` module-level state (line 105)
- Modified cancel trigger 1: skip orders with `endgame=True` (line 1843)
- Fixed stale comment: "market rejects at ~4 min" → "self-imposed guard (accepts until T+50s)"
- Added 80-line endgame block between post-fill-checks and exit section (lines ~1931-2010)
- Compile: ✅ OK
- Order path audit: ✅ 8 paths, all accounted for, endgame has 5 guards

### Phase 4: 2check + fixes
- Opus agent 2check: 2 🔴 CRITICAL + 5 🟡 MEDIUM
- 🔴 Fix 1: Missing `entry_cost` update on instant fill → added
- 🔴 Fix 2: Crash double-submit → moved `endgame_placed` before `_execute()`
- 🟡 Fix 3: `fair_value` never set → use current mid as proxy
- 🟡 Fix 4: `_endgame_mid_buf` cleanup → pop in cleanup block

### Phase 4: Final adjustments (BMD-driven)
- Changed Case 1 direction: bridge → **cheap side (underdog)** — buy the cheaper token
- Added daily cap: `_ENDGAME_DAILY_CAP = 10` → worst day = $2.50 (< 2% bankroll)
- Confirmed: Phase 1 = hold to resolution (no TP sell — saves code complexity + gives cleaner data)
- Log includes daily count in ENDGAME log message [N/10 today]
- Compile: ✅ OK

### Phase 0 — Research Round 3 (3x opus verification agents)
- Agent V1 (BTC volume): US volume only 1.3x Asia (NOT 5-10x). REFUTED "US dominance". Spike only at HKT 21-23.
- Agent V2 (momentum persistence): Momentum persists at ALL hours (77%). NO mean-reversion regime. Issue = early read reliability: T+2min WR = 63% at 00-02, T+5min WR = 79%.
- Agent V3 (fill rate): Asia fills less (21%) but 4.3x more profitable ($2.69/trade). 08-10 HKT = star bucket ($3.83/trade).
- Key correction: "flip signal" is WRONG mechanism → "delay signal read" is correct

### Other agent structural critique (accepted)
1. Forced hold = no exit strategy = prayer not MM
2. Bridge at reverse-indicator hours = speed doing wrong direction faster
3. Bot has no human trader's adaptability (no SL, no time awareness, no hedge)

### Plan major revision
- Added Phase A (delay bridge read at noisy hours) — biggest single improvement
- Added Phase B (time-of-day confidence gate)
- Added Phase C (last-minute hedge for wrong-direction insurance)
- Worst trade improved: -$9.97 → -$2.74 with all fixes
- Phase D (endgame) already complete

### 💀 Data source correction (analysis session, 2026-03-23 ~15:00 HKT)
- btc_15m_predictions.jsonl = pipeline predictions (lead_minutes ~20min) ≠ MM bridge signal
- "US WR=39.8%, Asia=50.2%, Eve=54.3%" were WRONG — measured wrong system
- Validated with 2,688 windows (28 days, 1-min candle data):
  - T+2min WR: **65-67% everywhere** (no session difference)
  - T+5min WR: **71-72% everywhere** (+6pp universal improvement)
  - T+7min WR: 76-79%, T+10min: 84-87%
- Phase B (time-of-day gate) **DROPPED** — data doesn't support session-based WR difference
- Phase A (delay read) upgraded from "noisy hours only" to **UNIVERSAL FIX**
- Phase numbering changed: 1(daily cap) → A(delay) → C(hedge) → E(whale) → F(2check)

### User frustration points addressed
- 3 trades -$25.06 single day → root cause = no exit strategy + no daily cap
- "Bot worse than manual" → correct: human would stop loss, bot prays
- "Signal completely reversed?" → NO, signal is 66% right, but 34% wrong + no exit = disaster

### Handoff prepared
- memory/handoff.md updated with full analysis summary
- task_plan.md already updated by parallel process
- Ready for execution agent
