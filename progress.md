# Progress Log — AS Defense: Fix Fill Rate + Per-Order Logging

## Session: 2026-03-21

### Phase 1: Fix cancel defense TTL ✅
- Adverse threshold: BTC 0.3%→0.5%, ETH 0.5%→0.7%
- TTL: fixed 5min → dynamic `min(10min, window_end - 3min - entry_ts)`
  - Entry min 1.5 → 10 min on book (max)
  - Entry min 8 → 4 min on book (respects window end)
- Added book/end time to cancel logs

### Phase 2: Per-order logging ✅
- New: `mm_order_log.jsonl` — per-order lifecycle
  - submit: order_id, cid, outcome, price, size, status + signal context (fair, bridge, cvd, vol, m1)
  - fill: mid_at_fill
  - cancel: reason, time_on_book_s, dist_to_end_s
  - cancelled_external: detected by _check_fills
  - expired: window ended with pending orders
  - post_fill_60s: midpoint 60s after fill (AS cost measurement)
- _execute() now takes cid + signal_ctx params
- _check_fills() logs fill/cancel/expired events
- Deferred post-fill checker runs each cycle

### Phase 3: Round-dependent pricing ✅
- R2 bid × 0.90 ($0.36), R3 bid × 0.80 ($0.32)
- BTC move > 0.3% since window open → skip re-entry (regime change)
- **Fixed pre-existing bug**: _re_mkt was NameError (never defined) → re-entry never worked

### Phase 4: 2check ✅
- 🔴 Pre-existing: _re_mkt NameError → now fixed
- 🟡 _time_on_book computed twice (cosmetic)
- 🟡 _post_fill_checks lost on crash (acceptable for diagnostic data)
- 🟢 TTL math verified: entry min 1.5 → 10min, entry min 8 → 4min
- 🟢 No security issues
- 🟢 All imports verified OK

### Cluster 3+4: Vol Estimation + Fat-tail Haircut ✅
- Vol: `limit=60→120` in _vol_1m() — SE 9.2%→6.5%, zero API cost
- Fat-tail: Normal CDF + fixed 10% HC → Student-t(ν=5) CDF
  - market_maker.py: new _student_t_cdf() using Simpson's rule (~455μs)
  - compute_fair_up(): bridge = T5(d) instead of Φ(d)
  - run_mm_live.py: removed 2 fixed haircut lines (initial entry + re-entry)
  - coin_shadow_test.py: removed 1 fixed haircut line
  - KEY FINDING: old HC over-corrected by 3-5pp. T5 is LESS aggressive = more edge
  - Verified: T5(1.0)=0.818 (expected 0.818), T5(2.0)=0.949 (expected 0.949) ✅
