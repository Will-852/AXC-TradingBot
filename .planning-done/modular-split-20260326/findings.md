# Findings: Phase 3+4 Audit
> Created: 2026-03-26

## run_1h_live.py (2,069 lines, SEEN)
- 28 constants, 7 mutable globals, 28 functions
- Biggest: _reprice_1h (270 lines), _collect_analysis (100 lines), run_cycle (390 lines)
- Shared with mm/: 14 functions near-identical (data feeds + state mgmt)
- Unique: hourly_engine integration, conviction-based repricing, analysis tape collection

## run_5m_live.py (1,660 lines, SEEN)
- 27 constants, 8 mutable globals, 22 functions
- Biggest: _w4_entry (200 lines), run_cycle (195 lines), _check_resolutions (110 lines)
- Shared with 1h: 13 functions near-identical
- Unique: CoinConfig dataclass, per-coin sizing, taker_ratio signal, _tier_lean_ratio

## Cross-Bot Duplication (candidates for future shared module)
| Function | mm/ | 1H | 5M | Identical? |
|----------|-----|----|----|-----------|
| _load/_save | mm/state_io.py | line 1376/1393 | line 1077/1102 | YES (different file paths only) |
| _to_dict/_from_dict | mm/state_io.py | line 1406/1414 | line 1116/1125 | YES |
| _log_trade/_log_order | mm/state_io.py | line 1422/1428 | line 1134/1141 | YES (different paths) |
| _bump_fill | mm/state_io.py | line 1371 | line 1071 | YES |
| _btc_price/price | mm/data_feeds.py | line 289 | line 211 | NEAR (WS+cache pattern same) |
| _vol_1m | mm/data_feeds.py | line 546 | line 246 | YES |
| _poly_midpoint | mm/data_feeds.py | line 567 | line 305 | YES |

## Dead Code (confirmed zero imports)
| File | Lines | Verdict |
|------|-------|---------|
| polymarket/strategy/signal_engine.py | 523 | DEAD — shelved 5M arb design |
| polymarket/data/ob_recorder.py | 622 | DEAD — standalone data collector, never called |
| polymarket/config/params.py | ~30 | DEAD — risk overrides, never imported |

## Exit Threshold Differences (intentional — DO NOT unify)
| Bot | BLACK_SWAN_MID | Reason |
|-----|---------------|--------|
| mm 15M | 0.96 | 15M windows resolve fast, 96¢ is near-certain |
| 1H | 0.95 | 1H has more time uncertainty |
| 5M | 0.99 (_PROFIT_LOCK_MID) | 5M is very short, only lock at 99¢ |
