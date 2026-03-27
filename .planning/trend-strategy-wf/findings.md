# Findings: Trend Strategy Walk-Forward Validation

## Pre-Sweep Analysis

### Production vs Backtest Logic Gap (FIXED)
- Production: 6 weighted sub-scores (MA 0.25, MACD 0.25, RSI 0.20, Price@MA 0.15, Vol 0.10, OBV 0.05) → continuous confidence 0-1
- BT (old): Binary 4-KEY count — completely different trade set
- Fix: Grid search now uses production TrendStrategy via monkey-patch

### Current Production Defaults
| Param | Value | Source |
|-------|-------|--------|
| PULLBACK_TOLERANCE | 0.039 | params.py optimizer |
| TREND_RSI_LONG_LOW | 33 | params.py optimizer |
| TREND_RSI_LONG_HIGH | 54 | params.py optimizer |
| TREND_SL_ATR_MULT | 1.5 | settings.py |
| TREND_MIN_RR | 3.0 | settings.py |
| MACD_HIST_DECAY_THRESHOLD | 0.6 | settings.py |
| CONFIDENCE_THRESHOLD | 0.30 | trend_strategy.py |

### Symbol Status
- BTC: **DISABLED** in production (PF < 1.0)
- ETH: **ENABLED** (conf_gate 0.50)

## Sweep Results

### Baseline (pullback_tolerance × 8 values, 180d)

**ETH**: 111 trades, **+4.0%**, AdjWR 46.2%, PF 5.00, MaxDD 19.9%
**BTC**: 90 trades, **-19.1%**, AdjWR 29.2%, PF 0.00, MaxDD 19.1%

**pullback_tolerance = DEAD param** — 8 values (0.015-0.050), trade count 104-111, WR identical. Same as RSI for range.

**BTC trend = NO EDGE** — WR 29%, all combos negative. Production disabled 決定正確。Skip BTC for all subsequent sweeps.

### Sweep A: Exit/Sizing (ETH only, 220 combos) ❌ NO EDGE

Best IS: sl_atr_mult=1.1, min_rr=2.0 → +8.6%, 65 trades, AdjWR 40.5%, MaxDD 11.9%
Walk-Forward: **ALL 5 REJECTED**
- WFE = -1.74 (IS +1.5%, OOS -2.7%)
- OOS positive folds: 1/5 = 20% (need >40%)

**macd_hist_decay = DEAD param** — 0.4-0.8 全部完全一樣（MACD exit 冇效果）
**Top 5 combos 實際上係同一個** — 只差 macd_hist_decay（dead param）

## Conclusion: Trend Strategy = NO EDGE

Both Range and Trend strategies fail walk-forward validation:
- Range: 96 combos, 3 sweeps → 0 pass WF
- Trend: 220 combos (exit/sizing) + 8 combos (entry) → 0 pass WF
- BTC: -19% return, WR 29% — dead
- ETH: IS +8.6% → OOS -2.7% — pure overfit

Dead params confirmed:
- pullback_tolerance (entry): zero effect across 8 values
- macd_hist_decay (exit): zero effect across 5 values
- RSI (from Range): zero effect across 4 values

The **only** params that change results are sl_atr_mult and min_rr (position sizing), but they overfit.

## Crash Sweep A: Exit/Sizing (16 combos × 3 symbols × 365d) ❌ NO EDGE

Best: sl_atr_mult=3.0 → -3.8%, 358 trades. Production baseline: -6.6%.
Walk-Forward: 4/5 REJECTED. 1 marginal pass (sl_atr=2.5, rr=1.0) but **OOS = -4.0%** (蝕錢)
- min_rr = DEAD param (1.0-2.5 identical results)
- SOL only positive symbol (+9.4%), ETH (-7.6%) and BTC (-13.2%) dead
- WFE=1.31 is misleading — ratio of two negatives

## Squeeze Sweep A: Exit/Sizing (60 combos × 2 symbols × 365d) ❌ NO EDGE (FALSE POSITIVE)

5/5 WF "passed" but **both IS and OOS are negative**:
- IS = -5.79%, OOS = -4.11% → WFE = 0.71 (ratio of negatives)
- PF = 0.0, return = -8.99% aggregate, AdjWR 42.8%
- sqz_min_rr = DEAD param (1.5-3.0 identical)
- sqz_tp_atr_mult = nearly dead (2.0-2.5 identical)
- PF=1.65 from handoff was specific to session-filtered subset, not general

**WF validation bug found**: WFE > 0.50 passes when both IS and OOS are negative. Need `mean_oos > 0` additional condition.

## FINAL CONCLUSION: ALL 4 STRATEGIES = NO EDGE

| Strategy | Status | Evidence |
|----------|--------|----------|
| Range | DEAD | 96 combos, 3 sweeps, 0 WF pass |
| Trend | DEAD | 228 combos, 0 WF pass, OOS -2.7% |
| Crash | DEAD | 16 combos, 1 marginal pass, OOS -4.0% |
| Squeeze | DEAD | 60 combos, 5 false+ pass, IS+OOS both negative |

## Next Steps (Strategy Direction Change Required)
1. **Fix WF validation**: add `mean_oos > 0` requirement
2. **Stop tuning params** — entry/exit params are mostly dead across all strategies
3. **Fundamental rethink needed**: indicator-based strategies on 1H/4H timeframes don't have edge
4. Possible directions: order flow (sub-minute), ML features, or pure market making
