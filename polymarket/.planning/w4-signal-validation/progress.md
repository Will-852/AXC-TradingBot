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

## Phase 4 Round 2: 2check + BMD — COMPLETE ✅
- 2check round 2: 10/10 areas CLEAN, 1 MINOR (endgame/hedge guard) fixed
- BMD: 2 FATAL fixed (combined cap $1.00→$1.05, exit logic guard for both-sides)
- Additional guards: endgame skip, hedge skip for both_sides markets

## Phase 5: Paper Tests — RUNNING 🟢
### 15M (PID 33209, started 00:57 HKT Mar 24)
- `--dry-run --both-sides --bankroll 253 --bet-pct 0.02`
- Log: polymarket/logs/mm_w4_paper.log
- First trade: 0x8712b1 ↑ PnL +$0.62, both_filled=True, combined=$0.960

### 5M (PID 51284, started 03:10 HKT Mar 24)
- `--dry-run --bankroll 253 --bet-pct 0.02` (run_5m_live.py)
- Log: polymarket/logs/mm_5m_paper.log
- W4-exact params: delay=15s, lean=12.5:1, sweep pricing
- Per-coin config: BTC live-ready, ETH/SOL/XRP paper
- SOL contrarian=True
- First cycle: 20 markets discovered, 4 signals fired (BTC/ETH/SOL/XRP)

## 5M Bot — NEW FILE CREATED ✅
- polymarket/run_5m_live.py (1358 lines, standalone)
- Independent state: mm_state_5m.json
- Per-coin on/off: COIN_CONFIG dict
- 10 × 🔴 2CHECK markers
- Syntax: PASS
- Smoke test: PASS (discovered markets, signals fired, paper entries placed)

## NEXT: 2check 5M bot + collect 48h data from both tests
