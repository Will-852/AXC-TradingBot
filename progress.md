# Progress Log — AXC 2-Zone Refactor

## Session: 2026-03-23 (coding)

### Phase 2: Code
- **Status:** in_progress
- **Started:** 2026-03-23
- Files to modify:
  - config/profiles/zone_a.py (new)
  - config/profiles/zone_b.py (new)
  - config/profiles/_base.py (update)
  - config/profiles/loader.py (update fallback)
  - scripts/trader_cycle/config/pairs.py (add fields)
  - scripts/trader_cycle/risk/position_sizer.py (3% margin + zone SL)
  - scripts/trader_cycle/risk/regime_risk.py (zone mapping)
  - config/params.py (ACTIVE_PROFILE)
  - scripts/trader_cycle/config/settings.py (fallbacks)

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | 寫 code：3 profiles → 2 zones |
| 下一步？ | Create zone_a.py + zone_b.py, then update pipeline |
