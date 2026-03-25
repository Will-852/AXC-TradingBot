# Task: Diagnose 5M WR Gap — 50.3% (live) vs 73.3% (backtest)

## Goal
Find why 5M bot lean WR = 50.3% when backtest says 73.3%. Four parallel investigations.

## SMOKING GUN (found during planning)
mm_w4_5m.jsonl lean_dir=DOWN but mm_trades_5m.jsonl lean_dir=UP for SAME cid.
**lean_dir field may be inverted in trade log → 50.3% WR measurement is wrong.**

## Phases

### Phase 1: Q1 — Lean Dir Logging Bug + Entry Timing `status: in_progress`
- Cross-reference mm_w4_5m.jsonl (entry) vs mm_trades_5m.jsonl (result) for EVERY cid
- Confirm lean_dir inversion pattern
- Check actual entry delay (ts in w4 entry vs window open time)
- Deliverable: match rate, true lean WR, actual entry delays

### Phase 2: Q2 — Signal Filter Implementation `status: in_progress`
- Read run_5m_live.py signal/filter logic
- Check: does it actually filter by momentum > 5bps? Or enter unconditionally?
- Check: what delay does code use? T+15s? T+60s? T+120s?
- Deliverable: actual filter params vs proposed params

### Phase 3: Q3 — Poly vs BTC Resolution Mismatch `status: in_progress`
- For each 5M trade: compare BTC direction (from w4_ret) vs Polymarket result
- How often does BTC direction ≠ Poly resolution?
- This would explain WR gap even if signal is correct
- Deliverable: mismatch rate, examples

### Phase 4: Q4 — Backtest vs Live Window Comparison `status: in_progress`
- Take 20 specific windows from mm_w4_5m.jsonl
- Reconstruct: what would backtest predict? What did bot do? What was result?
- Identify systematic differences
- Deliverable: side-by-side comparison table

## Verification
- All 4 phases deliver quantified answers
- Root cause identified with confidence level
