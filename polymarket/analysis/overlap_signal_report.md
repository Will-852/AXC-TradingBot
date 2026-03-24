# Multi-Timeframe Overlap Signal Analysis
> Generated: 2026-03-23 20:18 UTC
> Data: BTC 1M klines, 7 days (2026-03-16 to 2026-03-23)
> Total candles: 10,080

---
## 1. Overlap Point Map (24h period)

### Window counts per day
| Timeframe | Windows/Day | Resolution Interval |
|-----------|-------------|---------------------|
| 5M  | 288 | Every 5 min |
| 15M | 96 | Every 15 min |
| 1H  | 24 | Every 60 min |
| 4H  | 6 | Every 240 min |

### Simultaneous resolution points per day
| Overlap | Count/Day | Pattern |
|---------|-----------|---------|
| 5M + 15M | 96 | Every 3rd 5M = every 15 min |
| 5M + 15M + 1H | 24 | Every 12th 5M = every 60 min |
| 5M + 15M + 1H + 4H | 6 | Every 48th 5M = every 240 min |

### Key insight: Exploitable windows per day
- **5M windows starting in last 5min of a 15M**: 96/day (every 15M has exactly one such 5M)
- **5M windows starting in last 15min of a 1H**: 72/day (3 x 5M per 1H tail)
- **15M windows starting in last 15min of a 1H**: 24/day (1 x 15M per 1H tail)

---
## 2. Locked Direction Signals

### Primary lock check
| TF | Check Point | Threshold | Total Windows | Locked | Locked % | Correct | WR |
|----|-----------  |-----------|---------------|--------|----------|---------|-----|
| 15M | T+600s | >10bps | 671 | 371 | 55.3% | 352 | **94.9%** |
| 1H | T+2700s | >20bps | 167 | 105 | 62.9% | 98 | **93.3%** |

### Sensitivity analysis: 15M lock accuracy by threshold
| Check Point | Threshold (bps) | Locked Count | Locked % | WR |
|-------------|-----------------|--------------|----------|-----|
| 5min | >5 | 458 | 68.3% | **79.3%** |
| 5min | >8 | 331 | 49.3% | **82.2%** |
| 5min | >10 | 276 | 41.1% | **84.1%** |
| 5min | >15 | 179 | 26.7% | **87.7%** |
| 5min | >20 | 118 | 17.6% | **89.8%** |
| 5min | >30 | 53 | 7.9% | **94.3%** |
| 10min | >5 | 502 | 74.8% | **93.0%** |
| 10min | >8 | 424 | 63.2% | **94.3%** |
| 10min | >10 | 371 | 55.3% | **94.9%** |
| 10min | >15 | 273 | 40.7% | **98.2%** |
| 10min | >20 | 184 | 27.4% | **98.4%** |
| 10min | >30 | 99 | 14.8% | **100.0%** |
| 12min | >5 | 517 | 77.0% | **97.1%** |
| 12min | >8 | 425 | 63.3% | **98.4%** |
| 12min | >10 | 380 | 56.6% | **98.9%** |
| 12min | >15 | 287 | 42.8% | **99.3%** |
| 12min | >20 | 218 | 32.5% | **100.0%** |
| 12min | >30 | 111 | 16.5% | **100.0%** |

### Sensitivity analysis: 1H lock accuracy by threshold
| Check Point | Threshold (bps) | Locked Count | Locked % | WR |
|-------------|-----------------|--------------|----------|-----|
| 30min | >5 | 151 | 90.4% | **79.5%** |
| 30min | >8 | 141 | 84.4% | **80.1%** |
| 30min | >10 | 131 | 78.4% | **80.2%** |
| 30min | >15 | 105 | 62.9% | **84.8%** |
| 30min | >20 | 84 | 50.3% | **86.9%** |
| 30min | >30 | 55 | 32.9% | **87.3%** |
| 45min | >5 | 151 | 90.4% | **91.4%** |
| 45min | >8 | 142 | 85.0% | **92.3%** |
| 45min | >10 | 137 | 82.0% | **92.7%** |
| 45min | >15 | 123 | 73.7% | **93.5%** |
| 45min | >20 | 105 | 62.9% | **93.3%** |
| 45min | >30 | 70 | 41.9% | **98.6%** |
| 55min | >5 | 150 | 89.8% | **96.0%** |
| 55min | >8 | 137 | 82.0% | **97.1%** |
| 55min | >10 | 127 | 76.0% | **98.4%** |
| 55min | >15 | 116 | 69.5% | **100.0%** |
| 55min | >20 | 101 | 60.5% | **100.0%** |
| 55min | >30 | 72 | 43.1% | **100.0%** |

---
## 3. Overlap Signal Backtest

### Core question: Does parent locked direction predict child direction?

| Signal | With Signal (N) | WR (with) | Without Signal (N) | WR (without) | WR Lift |
|--------|-----------------|-----------|-------------------|--------------|---------|
| 15M->{'5M'} @10min >10bps | 371 | **54.7%** | 300 | 48.0% | **+6.7pp** |
| 15M->{'5M'} @10min >15bps | 273 | **53.8%** | 398 | 50.3% | **+3.6pp** |
| 15M->{'5M'} @10min >20bps | 184 | **54.3%** | 487 | 50.7% | **+3.6pp** |
| 15M->{'5M'} @12min >10bps | 371 | **54.7%** | 300 | 48.0% | **+6.7pp** |
| 15M->{'5M'} @12min >15bps | 273 | **53.8%** | 398 | 50.3% | **+3.6pp** |
| 15M_any->5M (last5min) >10bps | 371 | **54.7%** | 300 | 48.0% | **+6.7pp** |
| 15M_any->5M (last5min) >15bps | 273 | **53.8%** | 398 | 50.3% | **+3.6pp** |
| 15M_any->5M (last5min) >20bps | 184 | **54.3%** | 487 | 50.7% | **+3.6pp** |
| 1H->15M (last15min) >15bps | 123 | **45.5%** | 44 | 45.5% | **+0.1pp** |
| 1H->15M (last15min) >20bps | 105 | **45.7%** | 62 | 45.2% | **+0.6pp** |
| 1H->15M (last15min) >30bps | 70 | **44.3%** | 97 | 46.4% | **-2.1pp** |
| 1H->5M (last15min) >20bps | 301 | **53.5%** | 200 | 41.5% | **+12.0pp** |
| 1H->5M (last15min) >30bps | 212 | **50.0%** | 289 | 47.8% | **+2.2pp** |

---
## 4. Pricing Edge Estimation

Assumption: Fresh 5M window opens at ~50c (50/50 pricing).
If parent is locked, true probability != 50%. The gap = edge.

| Parent Threshold | Signals (7d) | True Prob | Market Price | Edge | EV/$1 | Daily Signals | Daily EV |
|-----------------|-------------|-----------|--------------|------|-------|---------------|----------|
| >10bps | 371 | 54.7% | 50c | **4.7%** | $0.047 | 53.0 | **$2.50** |
| >15bps | 273 | 53.8% | 50c | **3.8%** | $0.038 | 39.0 | **$1.50** |
| >20bps | 184 | 54.3% | 50c | **4.3%** | $0.043 | 26.3 | **$1.14** |
| >25bps | 132 | 56.1% | 50c | **6.1%** | $0.061 | 18.9 | **$1.14** |
| >30bps | 99 | 51.5% | 50c | **1.5%** | $0.015 | 14.1 | **$0.21** |

*Note: Daily EV assumes $1 bet per signal. Scale linearly with bet size.*

---
## 5. Multi-Timeframe Cascade Signal

How many parent timeframes are locked at the same time?

| Parents Locked | Count (7d) | Correct | WR | Note |
|---------------|-----------|---------|-----|------|
| 0 | 1271 | 0 | **0.0%** | Baseline (no signal) |
| 1 | 639 | 326 | **51.0%** | Single parent locked (15M or 1H) |
| 2 | 97 | 54 | **55.7%** | Double lock (15M + 1H same dir) |
| 3 | 9 | 7 | **77.8%** | Triple lock (15M + 1H + 4H) |

### Sample cascade events (2+ parents locked)
| Time (UTC) | 5M Dir | Parents | Consensus | Correct |
|-----------|--------|---------|-----------|---------|
| 03-16 15:55 | DOWN | 15M=DOWN(-39bps), 1H=DOWN(-40bps) | DOWN | Y |
| 03-16 16:55 | DOWN | 15M=UP(+16bps), 1H=UP(+69bps) | UP | N |
| 03-16 19:10 | DOWN | 15M=UP(+42bps), 4H=UP(+130bps) | UP | N |
| 03-16 20:55 | UP | 15M=UP(+17bps), 1H=UP(+27bps) | UP | Y |
| 03-16 23:25 | UP | 15M=UP(+19bps), 4H=UP(+76bps) | UP | Y |
| 03-16 23:40 | UP | 15M=UP(+20bps), 4H=UP(+106bps) | UP | Y |
| 03-17 02:55 | DOWN | 15M=DOWN(-23bps), 1H=DOWN(-53bps) | DOWN | Y |
| 03-17 03:40 | DOWN | 15M=DOWN(-45bps), 4H=DOWN(-51bps) | DOWN | Y |
| 03-17 03:45 | DOWN | 1H=DOWN(-116bps), 4H=DOWN(-75bps) | DOWN | Y |
| 03-17 03:50 | UP | 1H=DOWN(-120bps), 4H=DOWN(-79bps) | DOWN | N |
| 03-17 03:55 | UP | 15M=DOWN(-14bps), 1H=DOWN(-116bps), 4H=DOWN(-75bps) | DOWN | N |
| 03-17 09:55 | DOWN | 15M=DOWN(-44bps), 1H=DOWN(-45bps) | DOWN | Y |
| 03-17 12:55 | DOWN | 15M=DOWN(-13bps), 1H=DOWN(-38bps) | DOWN | Y |
| 03-17 13:55 | UP | 15M=DOWN(-20bps), 1H=DOWN(-24bps) | DOWN | N |
| 03-17 16:55 | DOWN | 15M=UP(+11bps), 1H=UP(+40bps) | UP | N |
| 03-17 19:10 | UP | 15M=UP(+14bps), 4H=UP(+116bps) | UP | Y |
| 03-17 23:40 | DOWN | 15M=DOWN(-18bps), 4H=DOWN(-60bps) | DOWN | Y |
| 03-17 23:45 | UP | 1H=DOWN(-47bps), 4H=DOWN(-73bps) | DOWN | N |
| 03-17 23:50 | DOWN | 1H=DOWN(-37bps), 4H=DOWN(-63bps) | DOWN | Y |
| 03-17 23:55 | DOWN | 1H=DOWN(-43bps), 4H=DOWN(-70bps) | DOWN | Y |

---
## 6. Concrete Strategy: Multi-Timeframe Overlap

### Entry Rules
**Rule 1: 15M LOCKED -> 5M LEAN**
- Condition: 15M return > 10bps at T+600s
- Action: Lean 5M in same direction as 15M
- Sizing: 5:1 ratio (80% locked dir, 20% opposite)
- Expected WR: 54.7%
- Edge vs 50/50: 4.7%

**Rule 2: CASCADE (15M + 1H LOCKED) -> 5M STRONG LEAN**
- Condition: Both 15M (>10bps@10min) AND 1H (>20bps@45min) locked same dir
- Action: Lean 5M heavily in consensus direction
- Sizing: 8:1 ratio or skip opposite side entirely
- Expected WR: 55.7%
- Frequency: ~97 in 7 days

### Timing Windows
- **5m_within_15m**: Only enter 5M in last 5 min of 15M (T+600 to T+900)
- **15m_within_1h**: Only enter 15M in last 15 min of 1H (T+2700 to T+3600)
- **cascade_window**: When multiple parents locked, ANY child in window gets signal

### Risk Parameters
- **max_exposure_per_signal**: 3% of bankroll
- **daily_cap**: 15 signals/day (limited by overlap windows)
- **stop_loss**: None (binary hold to resolution)

### Implementation Pseudocode
```python
# Every 5 seconds (main loop):
now = time.time()

# Check parent timeframe locks
parent_15m_start = (now // 900) * 900
elapsed_15m = now - parent_15m_start
if elapsed_15m >= 600:  # 10+ min into 15M
    btc_return_15m = (btc_now - btc_at_15m_open) / btc_at_15m_open * 10000
    if abs(btc_return_15m) > 10:  # locked
        locked_dir = 'UP' if btc_return_15m > 0 else 'DOWN'

        # Find any 5M window opening now
        window_5m_start = (now // 300) * 300
        if now - window_5m_start < 30:  # just opened
            # SIGNAL: lean 5M in locked_dir
            lean_ratio = 5.0  # 5:1 in locked direction
            place_5m_order(locked_dir, lean_ratio)
```

---
## Summary

- **Best edge config**: >10bps threshold
- **True probability when locked**: 54.7%
- **Edge vs 50/50 market**: 4.7%
- **Daily signal count**: ~53
- **Daily EV per $1/signal**: $2.50
- **Cascade (2+ parents)**: 97 events, 55.7% WR

### Caveats
1. **Market price assumption**: Fresh 5M may NOT be exactly 50c. Smart money may already price in the parent signal.
2. **Sample size**: 7 days is small. Need 30+ days for statistical significance.
3. **Execution**: Fill probability on 5M is unknown. If market already adjusts, our limit orders won't fill.
4. **Fee drag**: Polymarket fees (1-2%) eat into thin edges.
5. **Regime dependency**: Works in trending markets, may fail in choppy/reversal regimes.
