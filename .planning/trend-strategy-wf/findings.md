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

## Next Steps
1. Skip Sweep B (entry params) — pointless given Sweep A result
2. Focus on SQUEEZE edge (PF=1.65 from handoff) + exit/sizing optimization
3. Walk-forward validate Crash strategy (separate regime, may have edge)
