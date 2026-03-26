# Progress: Trend Strategy Walk-Forward Validation

## 2026-03-26 — Session Start

### Phase 1: Fix grid_search.py ✅
- Bug 1 fixed: Removed BTTrendStrategy injection → monkey-patch production TrendStrategy
- Bug 2 fixed: auto_validate_top now patches trend globals + save/restore pattern
- Bug 3 fixed: pullback_tolerance range expanded 0.015-0.050 (was 0.010-0.025)
- Added 2 new params: confidence_threshold (0.25-0.45), macd_hist_decay (0.4-0.8)
- Verified monkey-patching works (sl_atr_mult, confidence_threshold, pullback_tolerance all mutable at runtime)
- Critical Python gotcha: `from X import Y` creates COPY not reference — must patch `_ts.TREND_SL_ATR_MULT` not `_settings.TREND_SL_ATR_MULT`

### Phase 2: Baseline Run ✅
- ETH 180d: +4.0%, AdjWR 46.2%, pullback_tolerance = dead param
- BTC 180d: -19.1%, WR 29.2% — dead

### Phase 3: Trend Exit/Sizing Sweep ✅
- 220 combos (sl_atr_mult_trend × min_rr × macd_hist_decay), ETH only
- ALL 5 WF rejected. OOS -2.7%, WFE -1.74
- macd_hist_decay = dead param (zero effect)
- **TREND = NO EDGE**

### Phase 4+5: Crash + Squeeze (parallel) 🔄
- Crash: 16 combos × 3 symbols (ETH/BTC/SOL) × 365d + WF — running
- Squeeze: 60 combos × 2 symbols (ETH/BTC) × 365d + WF — running
- Added SqueezeStrategy to engine.py
- Added crash/squeeze params to PARAM_REGISTRY
- Added monkey-patch for both in _worker_run + auto_validate_top
