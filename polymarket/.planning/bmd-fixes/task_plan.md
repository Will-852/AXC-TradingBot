# BMD Fixes — Task Plan
> Created: 2026-03-23 | Status: IN PROGRESS

## Goal
Fix all 💀 + 🔴 findings from BMD audit. Step by step, audited by opus.

## Fix 1: Revert hourly_engine.py v3 pricing (💀)
**File:** `polymarket/strategy/hourly_engine.py`
**What:** Revert 6 parameters to v2 values (proven correct by wallet reverse engineering)
**Values:**
| Param | v3 (wrong) | v2 (correct) |
|-------|-----------|-------------|
| price_cap_base | 0.55 | 0.25 |
| price_cap_scale | 0.20 | 0.12 |
| max_entry_price | 0.75 | 0.39 |
| min_ev_per_share | 0.03 | 0.05 |
| base_spread | 0.03 | 0.15 |
| stop_loss_pct | -0.25 | -0.49 |
**Keep:** vol_imbalance filter, ToD gate, SOL/ETH support, signal_tape — these are NEW features, not pricing.

## Fix 2: Paper PnL tracker uses real Poly mid (💀)
**File:** `polymarket/run_1h_live.py`
**What:** `_paper_enter()` currently uses `sig.entry_price` (engine price, unfillable).
Change to use real Poly mid for our side's token, so paper PnL reflects reality.
Also: add "fill_feasible" flag comparing engine price vs market mid.

## Fix 3: Periodic REST reconciliation for WS fills (🔴)
**File:** `polymarket/run_mm_live.py`
**What:** Add a 5-minute REST reconciliation in _check_fills() to verify WS-based fills
match actual exchange state. Log warning on mismatch.

## Fix 4: Data freshness log (🔴)
**File:** `polymarket/run_mm_live.py`
**What:** Every heavy cycle, log each WS feed's status + age for monitoring.

## Fix 5: handin — save session state
**What:** Update handoff.md + progress files with session findings.
