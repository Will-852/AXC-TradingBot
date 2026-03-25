# Findings — 5M WR Gap Diagnosis

## CONFIRMED: lean_dir Hardcoded Bug
- mm_trades_5m.jsonl lean_dir = "UP" for ALL 153 entries (0 entries with "DOWN")
- Bug location: run_5m_live.py:1099 — `w4_lean_dir` reads result[0].get("outcome") which is always "UP"
- Corrected WR using entry log: **81.0%** (was reported as 50.3%)
- WR by signal strength: >20bps=100%, 10-20bps=87.9%, 5-10bps=77.3%

## BTC vs Poly Mismatch = 17.6%
- 5-10bps bucket: 22.5% mismatch (72% of trades are here)
- 10-20bps: 6.1% mismatch
- >20bps: 0% mismatch
- Not the root cause — signal works, especially at higher thresholds

## Bot Config (from code review)
- Entry delay: T+15s (code), but Q4 measured actual delay ~T+416s (needs verify)
- Threshold: 5bps
- Combined gate: < $0.99
- Contrarian: 4 trades only, WR=25% (should filter out)

## Implications
- The 73.3% backtest vs 50.3% live gap was a MEASUREMENT ERROR
- Real live WR = 81.0% — BETTER than the 73.3% backtest
- BMD fatal flaw #1 (WR margin) is RESOLVED — 81% WR >> 72% break-even = 9pp margin
- BMD fatal flaw #3 (median PnL negative) needs re-evaluation with correct WR
- BMD fatal flaw #2 (combined price) still valid — arb doesn't exist, edge is directional
