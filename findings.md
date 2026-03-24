# Findings

## Affected Constants (from audit)
| Constant | File | Original (15min) | At 120s | Target |
|----------|------|-------------------|---------|--------|
| REENTRY_COOLDOWN_CYCLES=3 | settings.py:165, profiles/*.py | 45 min | 6 min | 1.5h (time-based) |
| SILENT_MODE_THRESHOLD_CYCLES=2 | settings.py:172 | 30 min | 4 min | 30 min (bump to 15) |
| SIGNAL_PERSISTENCE={"range":3} | params.py:305 | 45 min range | 6 min | 45 min (time-based) |
| age_min < 25 (FAST mode) | main.py:96 | occasionally true | always true | age_min < 5 |

## Re-entry State Flow
- _set_reentry() writes REENTRY_CYCLES_REMAINING to TRADE_STATE
- _load_reentry_state() reads and decrements each cycle
- Need to change to: store REENTRY_EXIT_TIME (already exists!), compare wall clock

## Signal Persistence Flow
- signal_filter.py stores {strategy, direction, count} per pair in persist_state dict
- persist_state lives in-memory across cycles (state dict passed through)
- Need to add first_seen_ts, compare against min duration instead of count
