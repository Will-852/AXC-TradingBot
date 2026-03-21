# Progress Log: Fix 1H Conviction Bot

## Session: 2026-03-22

### Phase 0: Research ✅
- Read full codebase: run_1h_live.py (1024L), hourly_engine.py (448L), market_maker.py (475L)
- Analyzed 151 orders / 338 events in mm_order_log_1h.jsonl
- Analyzed 48 trades in mm_trades_1h.jsonl
- Analyzed 40 resolved markets in mm_state_1h.json
- Root causes identified: (1) re-submission loop (2) price cap too high (3) lifecycle leak
- **Wallet reverse engineering** (14 wallets, $2.2M PnL) — key insights:
  - Set-and-forget > cancel-reorder (12/14 wallets)
  - Optimal EV bid = $0.20 (fill model, 117 markets)
  - σ_poly independent of σ_btc (r=0.063)
  - $0.54-$0.57 = 0% fill rate in 1H
- **Plan revised**: Phase 1 changed from cancel-before-reorder → one-order-per-market guard
- Plan written, ready for Phase 1

### Phase 1: One-Order-Per-Market Guard ✅
- Added dedup guard at run_1h_live.py:800-811
- ENTER blocked if pending_orders exists for that market
- ADD blocked if no prior fill OR pending exists
- Tested: import OK, no syntax errors

### Phase 2: Tighten Entry Price Cap ✅
- hourly_engine.py HourlyConfig: price_cap_base 0.30→0.25, price_cap_scale 0.30→0.12, base_spread 0.12→0.15
- Added max_entry_price=0.39 hard ceiling (conviction_signal line 249)
- Conviction surface verified: all entries $0.27-$0.36 (was $0.37-$0.57)

### Phase 3: Fix Order Lifecycle Tracking ✅
- Moved `mkt["pending_orders"] = still_open` OUTSIDE `if filled:` block (line 462)
- Added rejected order detection in _execute_order (no order_id = rejected)
- Added `list()` to _check_fills dict iteration for safety

### Phase 4: 2check + Fixes ✅
- Opus 2check found 2x 🔴, 3x 🟡, 2x 🟢
- 🔴 Fixed: _Mock added get_orders/get_trades + clear pending after dry-run instant fill
- 🔴 Fixed: _to_dict resolution preserves runtime keys (pending_orders, fills_confirmed, etc.)
- 🟡 Fixed: _check_fills uses list() for dict iteration safety
- 🟢 Fixed: removed dead hasattr check
- All imports + conviction surface verified

### Summary of Changes
| File | Changes |
|------|---------|
| `hourly_engine.py` | price_cap_base 0.25, price_cap_scale 0.12, max_entry_price 0.39, base_spread 0.15, hard ceiling logic |
| `run_1h_live.py` | one-order guard, lifecycle fix (pending cleanup outside if-filled), rejected detection, _Mock methods, _to_dict preservation, list() safety |

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | Fix 1H bot: order spam + price cap too high + lifecycle leak |
| 目標？ | 每個 market 最多 1 active order, max entry ≤$0.42, 0% unknown orders |
| 到邊？ | Phase 0 research done, ready for Phase 1 |
| 紅線？ | 唔碰 15M bot, 唔改 bridge/conviction formula |
