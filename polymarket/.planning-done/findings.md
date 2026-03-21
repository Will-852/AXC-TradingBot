# Findings
> Task: Fix 2check findings

## Phase 0 — Code verification results

### Consecutive loss threshold — TWO SYSTEMS
- Pipeline (`risk/risk_manager.py:165`): `consecutive >= 3` → circuit breaker
- MM bot (`run_mm_live.py:782`): `consecutive_losses >= 5` → 24h cooldown
- CORE.md says "3" — correct for pipeline, wrong for MM bot

### MM bot exit thresholds — ALL CONFIRMED
- Profit Lock: `mid >= 0.95`, sell 90%, keep 10% (`run_mm_live.py:1427-1428`)
- Cost Recovery: `mid >= 0.64` (`run_mm_live.py:1429`)
- Stop Loss: `pnl_pct < -0.25` @ `mid × 0.97` (`run_mm_live.py:1426,1530`)
- Forced hold: last 5 min (`end_ms - 300_000`, `run_mm_live.py:1437`)

### Kill switch — ALL CONFIRMED
- Daily: `-20% of current wallet` (`run_mm_live.py:848`)
- Total: `-20% of initial bankroll` (`run_mm_live.py:839`)
- Rolling WR: 30 trades, <48% = STOPPED (`run_mm_live.py:696,725`)
