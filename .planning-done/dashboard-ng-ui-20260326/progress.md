# Progress: Dashboard NG UI Enhancement
> Started: 2026-03-26

## Session 1

### Round 1: Scout + Plan (completed)
- 3 scouts launched: default mode, indicators, notification overlay
- Web research: Binance/Bybit/OKX order form UX
- Code exploration: trade_modal.py full 235-line analysis
- Plan created with 4 phases

### Execution order decided:
1. ✅ Phase 3: Notification overlay fix — `ui.dialog` → `ui.menu` dropdown
2. ✅ Phase 4: Button colors — `indigo`/`blue` → `teal` (18 locations across 10 files)
3. ✅ Phase 1: Trade modal overhaul — margin badge, notional display, bidirectional calc, validation
4. ⬜ Phase 2: Hidden indicators (6 new)

### Audit results Phase 1/3/4 (subagent):
- Syntax: PASS (both files)
- Logic: PASS (calc lock prevents infinite loop)
- 3 minor fixes applied: badge kwargs, dead variable, polymarket blue badges

### Round 2: Phase 2 — 6 Hidden Indicators (completed)
- 6 registerIndicator blocks added (AXC_SR, AXC_ADX, AXC_OBV, AXC_BBWP, AXC_VOLR, AXC_ZROB)
- 6 toggle checkboxes with 人體比喻 tooltips
- activeIndicators defaults, applyIndicatorData names, isSubPane, re-apply array all updated
- AXC_SR = overlay (dashed red/green), others = sub-pane

### Audit results Phase 2 (subagent):
- 6/6 registration completeness: PASS
- Data key mapping: PASS (all 10 keys confirmed in indicator_cache.json)
- isSubPane correctness: PASS (SR=overlay, others=sub-pane)
- JS syntax: PASS
- Color uniqueness: 1 fix (VOLR amber→teal to avoid ADX collision)

**All 4 phases COMPLETE.**
