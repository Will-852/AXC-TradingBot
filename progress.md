# Progress — 15M Bot Fix Payout Structure
> Started: 2026-03-24 05:10 HKT | Plan v3: 18:00 HKT

---

## Timeline
- 05:10 — Session start. Staged entry + repricing + discovery implemented.
- 05:40 — Bot restarted (PID 13588) with T1/T2 + repricing + 120s discovery
- 13:40 — Root cause: partial fills = -$6.30 wiped +$2.33 profit
- 14:30 — Lean fix verified ✅. New plan: parameter optimization with own data.
- 14:40 — 4 parallel agents read codebase. Plan v1 created.
- 15:00 — Phase 0 complete: Door B coded, 2check + BMD passed, 2 fixes.
- 15:30 — Full report (8 sections). Session 3 BMD absorbed.
- 16:00 — CSV analysis: full account -$233.64. 60% avoidable, 40% bot.
- 16:30 — Random baseline: WR 78.8%, z=3.31 → ALPHA EXISTS.
- 17:00 — Core finding: alpha exists but payout broken (win $0.41, lose $1.90)
- 17:30 — Manipulation test: no evidence. :45 window anomaly found (27% WR).
- 18:00 — Plan v3: focus on payout structure fix. Audit launched.

## Key Numbers (carry forward)

| Metric | Value | Source |
|--------|-------|--------|
| Bot lean WR | 78.8% (26/33) | mm_trades + W4 entries |
| Z-test vs 50% | 3.31 (p<0.001) | Statistical test |
| Avg win | +$0.41 | 26 correct-lean trades |
| Avg loss | -$1.90 | 7 wrong-lean trades |
| Loss/Win ratio | 4.6x | Derived |
| Partial fill rate | 42% (14/33) | Order lifecycle |
| Lean miss rate | 30.3% (10/33) | Order lifecycle |
| Door B projected save | +$6.30 | Backtest on 33 trades |
| Bankroll | $232.31 | Live balance |
| Kill switch | -$50 ($202) | User confirmed |
| Total account PnL | -$233.64 | CSV export |

## Current State
- Bot: STOPPED (killed PID 13588)
- Door B: code complete, audit passed, NOT yet live
- Daily dashboard: NOT yet built
- Phase 1 analysis: NOT yet started
- Next action: restart bot with Door B + build dashboard

## Sessions Integrated
- Session 1: W4 Enhancement (staged entry, repricing, discovery)
- Session 2: Second agent (P1-P6 improvements, 8 questions, overlap signal)
- Session 3: BMD attack, cap ratio bug, bankroll discussion
- Session 4 (this): Full analysis, random baseline, manipulation test, plan v3

## Audit Results (v3 plan audit, end of session 4)
3 🔴 MUST FIX:
- ✅ R1: Door B $6.30 save overstated → CORRECTED: Stage 1 ~$1.84 expected, Stage 2 $0. Door B = risk mgmt, not profit gen.
- ✅ R2: Hedge-miss (12.1%) → DOCUMENTED GAP + Phase 2 aggressive repricing. Mirror Door A impossible (lean filled).
- ✅ R3: F10 sim bug → HARD GATE written: `analysis/f10_validate.py` (6 checks, self-test passed)

4 🟡 SHOULD FIX:
- ✅ Y1: :45 window skip → CODE DEPLOYED in run_mm_live.py (line ~1553)
- ✅ Y2: Fee analysis → DONE: 92% maker, $0 fee, combined mid $0.909 = 9.1% structural edge. Fees NOT a problem.
- ⬜ Y3: Success criteria WR>60% contradicts payout ratio → OPEN (deferred, needs Q1 ratio analysis)
- ⬜ Y4: Stage 1 at 20s too aggressive → OPEN (needs more data)
- ⬜ Y5: Cap ratio hurts at WR>75% → OPEN (needs explicit decision after WR stabilizes)

Phase 1 order updated: **Q1(ratio) → Q2(timing) → Q4(pricing)** — Q3 fees DONE.

## Session 5 Timeline (2026-03-24, audit fix session)
- 19:00 — Restored context from 3 planning files + 6 memory files
- 19:30 — R1: Recalculated Door B save. $6.30 → ~$1.84 expected (Stage 1 only). Stage 2 = $0.
- 19:45 — R2: Hedge-miss documented as known gap. Mirror logic impossible. Added Phase 2 item.
- 20:00 — R3: Wrote f10_validate.py (6 validation checks, catches both cost-only and redeem-only bugs)
- 20:15 — Y1: Added :45 window skip in run_mm_live.py (instant, zero risk)
- 20:30 — Y2: Wrote fee_analysis.py, ran on 619 CSV rows. Key finding: 92% maker, $0 fees, fees NOT the blocker.
- 20:45 — Updated all planning files.
- 21:00 — Q1 analysis: 3 parallel agents (WR by tier, live trades, optimal ratio math)
- 21:30 — Adverse selection confirmed: 5-10bps=76% WR, 10-20bps=52%. Momentum real but market prices it in.
- 21:45 — Shadow tape validation (321 entries): entry timing is strongest predictor (3.1min=67%, 3.5min=50%)
- 22:00 — 3-month sim (6,479 windows): drag monotonically increases with signal strength
- 22:15 — BTC data confirms ETH pattern (both coins show inverse signal-WR relationship)
- 22:30 — Implemented `_w4_dynamic_ratio()`: 5-10bps→R=1.2, >10bps→R=1.0. 2check + order path audit passed.
- 23:00 — Per-coin sizing: BTC 3%, SOL 1%. XRP added to discovery + coin detection. ETH+XRP dry-run.
- 23:15 — Heavy cycle 5s→3s. Bot restarted (PID 90322, nohup).
- 23:30 — First live trade with dynamic ratio: BTC R=1.0 (18.1bps >10), ETH R=1.0 paper (28.3bps).
- 23:45 — Opus 2check #2: found bug #6 (SOL min_order_size floor 4.2x overshoot). Fixed with budget cap. 5 latent XRP coin-detection bugs (all observe-only, safe).
- 24:00 — User 3-path analysis reviewed (路1 R=1.0全arb / 路2 sub-range filter / 路3 加速). Decision tree for 50-trade checkpoint.

## CURRENT STATE (2026-03-24 24:00 HKT)
**Bot: 🟢 RUNNING (PID 90322, nohup)**
- BTC 15M: LIVE, 3%, dynamic R (1.2 at 5-10bps, 1.0 at >10bps)
- SOL 15M: LIVE, 1% (⚠️ min_order_size inflate to ~$5, budget cap fix pending restart)
- ETH 15M: DRY-RUN
- XRP 15M: DRY-RUN (new)
- Heavy cycle: 3s (was 5s)
- :45 windows: SKIPPED
- Door B: ACTIVE

## PENDING NEXT SESSION
1. **Monitor 50 trades** → decision tree:
   - WR ≥ 67%: maintain, consider scale
   - WR 60-66%: 路 2 (split 5-7 vs 8-10 bps sub-range)
   - WR < 55%: STOP, question edge existence
2. **Restart to apply**: budget cap fix (#6)
3. **Phase 0.4**: Daily dashboard (still not built)
4. **5 latent XRP bugs**: fix coin detection in cancel/T2/checkpoint/endgame when XRP goes live
5. **Y3/Y4/Y5**: need Q1 live data to decide

**New files this session**:
- `polymarket/analysis/f10_validate.py` — F10 sim validation gate
- `polymarket/analysis/fee_analysis.py` — Fee analysis script

**Files modified this session**:
- `polymarket/run_mm_live.py` — dynamic ratio, :45 skip, per-coin sizing, XRP, budget cap, heavy 3s
- `task_plan.md` — R1 correction, Q1+Q3 done, Phase 1 reordered, Phase 2 hedge-miss item
- `findings.md` — F12 corrected, F13 hedge-miss, F14 fees, F15 Q1 adverse selection
- `progress.md` — session 5 full log
