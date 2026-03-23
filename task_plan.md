# Task: AXC 2-Zone Leverage Refactor
> Created: 2026-03-23 HKT | Coding phase

## Goal
簡化 3 profiles (AGG/BAL/CON) → 2 zones (A: 1-10x / B: 11-20x)。
3% margin per trade。Per-coin SL scaling。最少改動。

## Current Phase
Phase 2 COMPLETE — dry run passed ✅

## Phases

### Phase 1: 止血 — `complete`
### Phase 2: Code — `complete`

**Core (8 files):**
- [x] 2.1 zone_a.py + zone_b.py created
- [x] 2.2 _base.py updated (margin_pct, sl_pct_base, tp_pct_base, zone)
- [x] 2.3 loader.py fallback → ZONE_A + old profiles in _SKIP_FILES
- [x] 2.4 pairs.py: vol_mult + max_leverage per coin
- [x] 2.5 position_sizer.py: 3% margin + zone SL + signal-level zone override + TP floor
- [x] 2.6 regime_risk.py: confidence → zone + 2-cycle hysteresis for upgrade
- [x] 2.7 params.py ACTIVE_PROFILE → ZONE_A
- [x] 2.8 settings.py fallbacks → ZONE_A

**UI/Bot (8 files):**
- [x] 2.9 handlers.py: valid modes → [ZONE_A, ZONE_B]
- [x] 2.10 controls.py: toggle → [ZONE_A, ZONE_B]
- [x] 2.11 health.py: color map → ZONE_A/ZONE_B
- [x] 2.12 tg_bot.py: VALID_MODES + mode_labels
- [x] 2.13 constants.py, action_plan.py, services.py, collectors.py: fallbacks

**Remaining:**
- [ ] canvas/index.html (CSS + JS + dropdown) — cosmetic, non-blocking
- [ ] tests/test_regime_risk.py — rewrite for zones
- [ ] agents/decision/SOUL.md + docs — text updates

### Phase 3: Paper Trading — `pending`
### Phase 4: Overfitting Validation — `pending`

## Dry Run Results
```
2026-03-23 21:32 — PASSED
- 24/24 pipeline steps completed
- Zone A selected (hysteresis working: regime_conf=100% but consecutive<2)
- BTC LONG trend signal: conf=0.58, stayed Zone A (correct)
- SL: 1.000% (base 0.01 × vol_mult 1.0) ✅
- Leverage: 7x (trend, capped by Zone A) ✅
- R:R: 1.8:1 (> min 1.5:1) ✅
- TP floor: applied (71986.2 = entry × 1.015) ✅
- Loss reduction: 0.70× (1 prior loss) ✅
- Telegram report sent ✅
```
