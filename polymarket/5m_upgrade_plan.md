# 5M Bot Upgrade Plan: Confidence-Tiered Lean Ratio

## Goal
Upgrade 5M bot from fixed lean=1.0 to data-validated confidence-tiered system.
Fix lean_dir logging bug. Add taker flow veto. Keep it simple.

## Data Validation Summary
- Real lean WR = 81.0% (was reported 50.3% due to logging bug)
- Taker flow: +60bps lift when agrees, **-500bps when disagrees** → VETO
- Poly OB imbalance: 100% WR at |imb|>0.2, but only fires 1% of time → BOOST
- Smart money: ANTI-signal for UP → DO NOT USE as positive signal
- Optimal lean: T1(5-10bps)=2:1, T2(10-20bps)=5:1, T3(>20bps)=8:1

## Phases

### Phase 1: Fix lean_dir Bug `status: complete`
- Bug: `run_5m_live.py:1099` — `result[0].get("outcome")` always = "UP"
- Fix: `_w4_entry()` attaches `_w4_dir` + `_w4_mag_bps` to order result dicts
- Caller reads `result[0].get("_w4_dir")` instead of `result[0].get("outcome")`
- Also stores `_w4_mag_bps` correctly (was hardcoded 0)

### Phase 2: Tiered Lean Ratio `status: complete`
- Removed `lean_ratio` from CoinConfig (was fixed 1.0 / 12.5)
- Added `_LEAN_TIERS` table + `_tier_lean_ratio(mag_bps)` function
- T1(5-10bps)=2:1, T2(10-20bps)=5:1, T3(>20bps)=8:1
- Entry log includes `tier` and dynamic `lean_ratio`
- Tested: tier logic works correctly for all thresholds

### Phase 3: Taker Flow Veto `status: complete`
- New function: `_binance_taker_ratio(coin, lookback_s=120)`
  - Fetches Binance futures aggTrades, 10s cache per symbol
  - Returns buy_qty/total_qty ∈ [0,1]
- Veto gate: momentum=UP but taker<0.45 → SKIP; momentum=DOWN but taker>0.55 → SKIP
- Vetoed entries logged as `event=w4_veto` in W4 log (for post-analysis)
- Taker ratio included in entry log
- Tested: BTC fetch returns valid ratio (0.63 at time of test)

### Also Changed: Combined Gate
- Old: `>= $0.99` (arb mode, blocked 96%+ of markets)
- New: `>= $1.06` (directional mode, only rejects extreme spreads)
- Reason: with tiered lean, edge comes from WR × lean, not combined < $1.00

### Phase 4: Poly OB Boost (optional) `status: deferred`
- Deferred until 48h data collected from Phase 5

### Phase 5: Dry-Run Validation (48h) `status: pending`
- Run updated bot in dry-run for 48h
- Metrics to collect:
  - Tier distribution (expect ~72% T1, ~22% T2, ~6% T3)
  - Veto rate (expect ~15%)
  - Lean WR per tier (expect T1≥75%, T2≥85%, T3≥95%)
  - PnL per trade by tier
  - Taker ratio distribution
- Decision gate:
  - Q1: WR per tier matches ±5pp of backtest → proceed
  - Q2: Veto correctly skips >60% losers → keep veto
  - Q3: PnL per trade > $0 → go live (BTC only first)

## Changes Summary
| Line(s) | Change |
|---------|--------|
| 107-117 | CoinConfig: removed `lean_ratio` field |
| 119-138 | NEW: `_LEAN_TIERS` + `_tier_lean_ratio()` |
| 141-149 | CoinConfig instances: removed `lean_ratio=1.0` |
| 267-302 | NEW: `_binance_taker_ratio()` with 10s cache |
| 470-501 | NEW: tier assignment + taker veto gate in `_w4_entry()` |
| 503-505 | Updated SIGNAL log format (includes tier + taker ratio) |
| 527-533 | Combined gate: $0.99 → $1.06 |
| 535-541 | Sizing uses dynamic `_lean_ratio` instead of `cfg.lean_ratio` |
| 581-584 | Entry log: added `tier`, `taker_ratio` fields |
| 596-600 | Results carry `_w4_dir`, `_w4_mag_bps`, `_w4_tier`, `_w4_lean_ratio` |
| 1199-1202 | Market state: reads `_w4_dir` + `_w4_mag_bps` from results (bug fix) |
