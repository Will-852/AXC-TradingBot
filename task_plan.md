# Task: Cycle-Based → Time-Based Constants

## Goal
Convert 4 cycle-denominated constants to wall-clock time so they work correctly at any cycle interval (120s, 900s, etc.).

## Phases

### Phase 1: REENTRY_COOLDOWN — cycle → seconds `status: complete`
- settings.py: added `REENTRY_COOLDOWN_SEC = 5400`
- adjust_positions.py: _set_reentry stores timestamp, _load_reentry_state compares wall clock
- REENTRY_CYCLES_REMAINING deprecated (kept for compat, set to "0")

### Phase 2: SILENT_MODE — threshold adjustment `status: complete`
- settings.py: `SILENT_MODE_THRESHOLD_CYCLES` 2 → 15 (= 30 min at 120s)

### Phase 3: SIGNAL_PERSISTENCE — time-based `status: complete`
- params.py: added `SIGNAL_PERSISTENCE_SEC = {"range": 2700, "trend": 0, ...}`
- signal_filter.py: persistence check uses first_seen_ts + elapsed seconds instead of count

### Phase 4: FAST mode gate — fix `status: complete`
- main.py: `age_min < 25` → `age_min < 5`

## Verification
- All imports OK ✅
- Full pipeline dry run: 10s, 0 errors ✅
