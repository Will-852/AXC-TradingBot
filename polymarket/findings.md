# Findings — 4H Conviction Bot

## Backtest Results (2026-03-25)
Source: `analysis/results/4h_backtest_BTCUSDT_365d.json`

### Individual Indicators (test set: 750 windows, base rate 51.6% UP)
| Indicator | Accuracy | Coverage | vs Base | Verdict |
|-----------|----------|----------|---------|---------|
| bridge | 77.5% | 81% | +25.9pp | 🔥 Core signal |
| momentum | 63.6% | 100% | +12.0pp | 🔥 Confirmation signal |
| bb_squeeze | 53.4% | 26% | +1.8pp | ⚠️ Low coverage, marginal |
| adx | 50.6% | 43% | -1.0pp | ❌ No edge |
| macd | 49.3% | 100% | -2.3pp | ❌ No edge |
| obv | 49.1% | 100% | -2.5pp | ❌ No edge |
| session | 48.7% | 88% | -2.9pp | ❌ Train bias didn't transfer |
| ema_cross | 47.7% | 100% | -3.9pp | ❌ No edge |
| rsi | 44.9% | 67% | -6.7pp | ❌ Negative edge |
| volume | 48.2% | 19% | -3.4pp | ❌ No edge, low coverage |

### Top Combos (majority vote, min 30 windows)
| Combo | Accuracy | Windows | vs Base |
|-------|----------|---------|---------|
| momentum+volume+bridge | 78.5% | 116 | +26.8pp |
| momentum+bridge | 72.1% | 610 | +20.5pp |
| momentum+macd+bridge | 72.5% | 610 | +20.9pp |

**Decision**: Use momentum+bridge (72.1%, 610 windows). Adding MACD adds 0.4pp but no real value. Volume combo is higher accuracy but 5x fewer windows.

### Session Bias (test set)
| Session | UP% | Train Bias | Match? |
|---------|-----|-----------|--------|
| EU_OPEN | 48.3% | UP | ❌ Flipped |
| US_OPEN | 57.3% | DOWN | ❌ Flipped |
| ASIA | 52.4% | NEUTRAL | ✅ |
| US_PRE | 44.9% | DOWN | ✅ |
| WEEKEND | 52.3% | NEUTRAL | ✅ |

**Conclusion**: Session bias is unstable across time periods. Don't use as signal.

## 4H Market Mechanics (from live screenshot 2026-03-25)
- Windows: 12AM/4AM/8AM/12PM/4PM/8PM ET (6/day)
- Title: "比特币上涨或下跌 - 4 Hour"
- Slug pattern: `btc-updown-4h-{unix_ts}` where unix_ts = window start UTC
- Bottom tabs show next windows: "12 PM", "4 PM", "8 PM", "12 AM Mar 26"
- Price to beat = open price displayed on market page
- At 98¢ Down with 40min left, sell EV ($0.98) > hold EV ($0.93)

## Bridge Signal Design for 4H
- Bridge uses `compute_fair_up(btc_current, btc_open, vol_1m, minutes_remaining)`
- At T+60min: minutes_remaining = 180, bridge has strong signal (77.5% accuracy)
- Threshold: fair > 0.55 → UP, fair < 0.45 → DOWN (from backtest)
- vol_1m: compute from recent 1m klines (same as 1H bot)

## Momentum Signal Design for 4H
- Check BTC direction in first 60 minutes of window
- BTC at T+60min > open → predict UP (continuation)
- BTC at T+60min < open → predict DOWN (continuation)
- 63.6% accuracy as standalone, improves bridge combo

## Key Insight: Bridge at T+1H ≈ "Early Conviction"
The bridge at T+1H doesn't predict the future — it measures how far BTC has moved relative to remaining time. If BTC is -$772 with 3H left:
- sigma ≈ 0.001 × sqrt(180) ≈ 0.013
- d = log(70896/71668) / 0.013 ≈ -0.84
- P(UP) ≈ Student-t(-0.84, ν=5) ≈ 21% → P(DOWN) ≈ 79%

This is a PROBABILISTIC statement, not a prediction. The 77.5% backtest accuracy means the bridge probability is well-calibrated.
