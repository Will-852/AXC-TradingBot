# Progress: W4 Signal Validation → Implementation
> Session: 2026-03-23/24

## Phase 3: Code Implementation — COMPLETE ✅
- Added `_w4_signal()` function (log return from window open, 5bps threshold)
- Upgraded both-sides entry: Poly OB mid pricing + 1.5:1 lean + W4 signal
- Added `--w4-live` flag (paper by default, live only with explicit flag)
- Safety: combined < $1.00 cap, duplicate order prevention, budget logging
- 7 × `🔴 2CHECK` markers placed at all critical paths

## Phase 4: 2check Audit — COMPLETE ✅
- 3 MEDIUM findings, 2 MINOR, 0 CRITICAL
- Fix #1: Safety cap lowered $1.05 → $1.00 ✅
- Fix #2: `_w4_live` reset on every startup from CLI args ✅
- Fix #3: Cancel triggers 2+3 skip both-sides markets ✅
- Syntax check: PASS

## Phase 5: Paper Test 15M — NEXT
- Ready to start: `--dry-run --both-sides --bet-pct 0.02 --bankroll 253`
