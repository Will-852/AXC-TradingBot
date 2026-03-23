# Wallet 4 (Decent-Dune) — 15M Strategy Mathematical Specification

> Wallet: `0x818f214c7f3e479cce1d964d53fe3db7297558cb`
> Pseudonym: Decent-Dune / livebreathevolatility
> Generated: 2026-03-23
> Data sources: 2,516 on-chain trades (March 23 snapshot) + 11,808 backtested 15M windows (Oct 2025 - Jan 2026) + 6 wallet reverse-engineering studies
> Period of interest: Oct 12, 2025 - Mar 23, 2026 (162 days, $507 -> $426K = 840x)

---

## 0. EXECUTIVE SUMMARY

W4 runs a **momentum-following both-sides market maker** on Polymarket BTC (and ETH/SOL/XRP) 15M binary markets. The strategy:

1. Observes BTC price movement after a 15M window opens
2. Determines lean direction from BTC momentum (UP or DOWN)
3. Places limit orders on BOTH sides, with more capital on the momentum side
4. Holds all positions to resolution (zero sells, zero management)
5. Compounds profits across ~60 qualifying windows per day

The edge comes from **buying below fair value** on both sides (combined < $1.00 target), amplified by a **directional lean** that captures momentum continuation at 81% win rate.

---

## 1. SIGNAL FORMULA

### 1.1 Definition

```
Let T_0 = window start time (Unix timestamp, aligned to 900s boundaries)
Let P_0 = BTC/USD price at T_0 (Binance 1M candle open at T_0)
Let P_d = BTC/USD price at T_0 + d seconds (Binance 1M candle close at T_0 + d)

btc_return = log(P_d / P_0)                    -- log return, NOT simple return
magnitude  = |btc_return|                       -- absolute value in natural units
magnitude_bps = magnitude * 10000               -- convert to basis points

signal_fires = (magnitude_bps >= threshold_bps)

direction = UP   if btc_return > 0
          = DOWN if btc_return < 0
          = SKIP if btc_return == 0
```

### 1.2 Parameter Values (from backtest + growth simulation fit)

| Parameter | 15M Value | Evidence | Confidence |
|-----------|-----------|----------|------------|
| Entry delay `d` | **300s** (5 min) | Best fit in simulation; WR table peak at 300s | HIGH |
| Threshold `threshold_bps` | **5 bps** | Growth simulation match; WR=81% with n=7,364 | HIGH |
| Return type | **Log return** | Confirmed in `w4_15m_era_analysis.py:180` and `w4_longterm_backtest.py:216` | HIGH |

### 1.3 Price Source

**Binance BTCUSDT 1M klines**, NOT Chainlink, NOT Polymarket mid.

Evidence:
- All backtest code (`w4_longterm_backtest.py`, `w4_15m_era_analysis.py`, `w4_signal_detection.py`) uses Binance kline data
- Binance provides sub-second price updates aggregated into 1M candles
- Chainlink is the **resolution** oracle, not the signal source -- it updates more slowly
- The signal formula uses `1M candle close` at the delay offset (not open)

**Exact lookup** (from `w4_15m_era_analysis.py:86-102`):
```python
target_ts = window_open_ts_sec + delay_sec
candle = idx_1m.get(target_ts)         # Exact match on 1M boundary
if candle:
    return candle["close"]             # Use CLOSE, not open
# Fallback: search +/- 2 minutes if exact match missing
```

**Critical distinction**: The `w4_longterm_backtest.py:88` uses `candle["open"]` instead of `close`. The 15M-specific analysis (`w4_15m_era_analysis.py:101`) uses `close`. The difference is ~1 bps on average (negligible for 5+ bps threshold), but for replication the 15M analysis (close) should be canonical since it produced the matching WR table.

### 1.4 "Window Open" Definition

```
window_start = floor(current_unix_timestamp / 900) * 900
```

15M windows are aligned to 900-second boundaries in UTC. The "window open price" `P_0` is the Binance 1M candle **open** at that exact timestamp.

### 1.5 WR Table (15M, Oct 2025 - Jan 2026, n=11,808 total windows)

| Delay | 0 bps | 5 bps | 10 bps | 15 bps | 20 bps |
|-------|-------|-------|--------|--------|--------|
| 180s | 68.8% (11808) | 76.8% (6630) | 80.8% (3588) | 84.0% (1976) | 85.6% (1169) |
| 240s | 70.7% (11808) | 77.9% (7036) | 83.1% (4029) | 85.9% (2341) | 88.4% (1432) |
| **300s** | **73.7% (11808)** | **81.0% (7364)** | **85.4% (4431)** | **88.1% (2717)** | **90.3% (1675)** |
| 360s | 75.9% (11808) | 83.1% (7717) | 87.5% (4764) | 90.5% (3022) | 92.6% (1937) |
| 420s | 78.2% (11808) | 85.8% (7896) | 90.0% (4976) | 92.7% (3203) | 94.1% (2102) |

**W4's operating point: 300s delay, 5 bps threshold = 81.0% WR on 7,364 qualifying windows (62.3% of all windows).**

### 1.6 WR by Regime (at 300s / 5 bps)

| Phase | BTC Change | Daily Vol | Avg 15M Range | WR | Opps/Day |
|-------|-----------|-----------|---------------|-----|----------|
| Testing (Oct 13-27) | -0.3% | 2.23% | 32.7 bps | **80.7%** | 61.6 |
| Slow Grind (Oct 27 - Nov 12) | -10.1% | 2.32% | 33.8 bps | **80.4%** | 66.6 |
| Exponential (Nov 12 - Dec 4) | -9.3% | 2.94% | 41.8 bps | **77.9%** | 70.9 |
| Steady (Dec 4 - Jan 28) | -4.3% | 2.07% | 26.8 bps | **82.6%** | 52.6 |

Key insight: WR is **regime-stable** (77.9% - 82.6%). The exponential growth phase had LOWER WR but MORE qualifying windows per day. The compound math, not better signal, drove the explosion.

---

## 2. ENTRY FORMULA

### 2.1 Both-Sides Structure

W4 places orders on **both** the UP and DOWN outcomes of every qualifying window:

```
For each qualifying 15M window:
    lean_side    = signal direction (UP or DOWN)
    hedge_side   = opposite of lean_side

    Place limit BUY on lean_side  at price p_lean
    Place limit BUY on hedge_side at price p_hedge

    Target: p_lean + p_hedge < $1.00 (guaranteed profit if both fill)
```

### 2.2 Pricing

W4 does NOT compute a sophisticated fair value. The pricing is **order-book reactive** -- placing limit bids at or slightly below the current Polymarket mid price.

From on-chain trade data (March 23, 2026):

| Metric | UP side | DOWN side | Combined |
|--------|---------|-----------|----------|
| Weighted avg price | $0.50 | $0.50 | $1.00 (median) |
| Range | $0.02 - $0.98 | $0.02 - $0.98 | $0.83 - $1.22 |
| % below $0.50 | 42.5% | 57.5% | — |

From the 15M era analysis (best-fit simulation):
```
lean_cost  ≈ $0.55 (momentum side)
hedge_cost ≈ $0.41 (anti-momentum side)
combined   ≈ $0.96
```

**Critical caveat**: The March 23 on-chain data shows combined costs **above** $1.00 for most windows (only 25% below $1.00). This means W4 is NOT achieving guaranteed arb on every trade in the current market. The combined < $1.00 guarantee was more achievable in the Oct-Dec 2025 era when competition was lower.

### 2.3 Lean Ratio

The lean ratio is **NOT fixed at 2:1**. Empirical data from 8 both-sides 15M windows (March 23):

| Lean Ratio | Count | % |
|-----------|-------|---|
| 1.0x - 1.5x | 3 | 37.5% |
| 2.0x - 3.0x | 2 | 25.0% |
| 3.0x - 5.0x | 1 | 12.5% |
| 10.0x - 100.0x | 2 | 25.0% |

```
Median lean ratio:  2.29x
Mean lean ratio:    4.52x
Range:              1.04x to 12.71x
```

The lean ratio appears to be **dynamic**, not a fixed 2:1 parameter. It likely depends on:
1. Magnitude of BTC move (bigger move = higher confidence = more aggressive lean)
2. Available liquidity on each side
3. Fill execution (if one side fills heavily but the other doesn't, the ratio skews)

**Best-fit for growth simulation**: 2:1 lean ratio with $0.55/$0.41 pricing matched W4's $507 -> $241K growth at **0.5% allocation per trade** (daily compound model).

### 2.4 Budget Allocation

```
total_budget = bankroll * bet_pct

if lean_direction == UP:
    up_budget   = total_budget * (lean_ratio / (lean_ratio + 1))
    down_budget = total_budget * (1 / (lean_ratio + 1))
else:
    up_budget   = total_budget * (1 / (lean_ratio + 1))
    down_budget = total_budget * (lean_ratio / (lean_ratio + 1))

up_shares   = up_budget / p_up
down_shares = down_budget / p_down
```

For the canonical 2:1 lean:
```
lean_budget  = total_budget * 2/3 = total_budget * 0.667
hedge_budget = total_budget * 1/3 = total_budget * 0.333
```

---

## 3. ENTRY TIMING

### 3.1 When Does W4 Enter?

The timing is **bimodal** -- W4 enters at two distinct times within a window:

| Entry Offset | Count | % | Interpretation |
|-------------|-------|---|---------------|
| 0s - 30s | 3 | 27.3% | Immediate entry (small/scout orders) |
| 600s - 900s | 4 | 36.4% | Late entry (deep into window) |
| 60s - 120s | 1 | 9.1% | Early-mid |
| Median | **741s** | — | Much later than the backtest 300s |

**This contradicts the backtest assumption of 300s entry.** Possible explanations:

1. **March 23 data is a single day** -- small sample, may not represent the 15M era behavior
2. **W4 evolved**: Oct-Dec 2025 (15M era) may have had different timing than March 2026 (5M era with 15M as secondary)
3. **The 300s is the SIGNAL time, not the ORDER time**: W4 may observe at T+300s, decide direction, then place orders that take additional time to fill. The on-chain timestamp records FILL time, not order placement time.

### 3.2 Trade Spread Within a Window

Once W4 starts filling, trades continue for:
```
Min:    0s   (single fill)
Median: 140s (2.3 minutes of filling)
Mean:   282s (4.7 minutes)
Max:    662s (11 minutes)
```

This confirms W4 places **many small limit orders** that fill over time, not a single market order.

### 3.3 Fill Clustering

From the detailed timestamp analysis, fills arrive in **bursts every 2-4 seconds**, consistent with a bot placing orders on a fast loop:

Example (btc-updown-15m-1774276200):
```
T+29s, T+41s, T+43s, T+51s, T+53s, T+63s ... (64 unique timestamps over 650s)
```

This is **not** a single order getting partially filled -- it's **repeated order placement** on a loop.

---

## 4. FILL SIZE

### 4.1 Individual Fill Sizes (15M windows)

```
Min:    $0.00 (dust)
Median: $3.15
Mean:   $5.93
Max:    $61.73
P95:    ~$25.92
```

### 4.2 Fills Per Window

```
Min:    1
Median: 81
Mean:   68.3
Max:    172
```

### 4.3 Why Small Fills?

Three complementary explanations:

1. **Thin CLOB depth**: Polymarket 15M order books have limited depth at each price level. A $100 order would walk the book and get terrible fill prices. Small orders ($3-$5) hit the best available price.

2. **Adverse selection defense**: Small orders are less visible to informed traders. If someone sees a $500 order, they may front-run it. Multiple $3 orders spread over 2 minutes are harder to detect.

3. **Price improvement**: By placing many small limit orders over time, W4 captures price fluctuations within the window. Some fills are at $0.22, others at $0.73 -- the average may be better than a single large market order.

### 4.4 Fills Per Side Per Window

From March 23 data (15M windows with both sides):
```
Typical window: 40-90 UP fills + 20-55 DOWN fills
Large window:   84 UP + 88 DOWN = 172 total
```

---

## 5. POSITION MANAGEMENT (or lack thereof)

### 5.1 Confirmed Rules

| Rule | Status | Evidence |
|------|--------|----------|
| Zero sells | **CONFIRMED** | 100% BUY across 30,930 lifetime markets |
| Hold to resolution | **CONFIRMED** | No SELL trades in entire history |
| No take-profit | **CONFIRMED** | No position reduction mid-window |
| No stop-loss | **CONFIRMED** | No position exit on adverse move |
| No cancel-on-adverse | **UNKNOWN** | On-chain data only shows fills, not cancellations |

### 5.2 Order Cancellation

W4 likely cancels unfilled limit orders, but we cannot observe this on-chain. Probable behavior:
- **Cancel at window end**: Any unfilled orders auto-expire when the market resolves
- **Cancel on adverse**: UNKNOWN -- W4 may cancel if BTC reverses strongly, or may leave orders to be price-improved

### 5.3 Why Zero Management Works

From the wallet reverse-engineering research:

```
Management ≡ breaking the combined < $1.00 guarantee

If you buy UP at $0.55 and DOWN at $0.41 (combined $0.96):
  - Guaranteed $1.00 payout → $0.04 profit NO MATTER WHAT

If you sell your UP at $0.60 mid-window:
  - You realize $0.60 - $0.55 = $0.05 profit on UP side
  - But now you only hold DOWN at $0.41
  - If DOWN loses: -$0.41 (total: +$0.05 - $0.41 = -$0.36)
  - Management converted guaranteed $0.04 profit into potential $0.36 loss
```

This is why all profitable wallets (LampStore, Uncommon-Oat, Decent-Dune) use hold-to-resolution as the default.

---

## 6. BANKROLL MANAGEMENT

### 6.1 Starting Capital and Growth

```
Starting bankroll:  $507 (Oct 12, 2025)
Final balance:      $426,000 (Mar 23, 2026)
Total return:       840x
Duration:           162 days
```

### 6.2 Growth Phases

| Phase | Start BR | End BR | Trades | WR | PnL | x Growth |
|-------|----------|--------|--------|-----|-----|----------|
| Testing (Oct 13-27) | $507 | $1,155 | 862 | 80.7% | $648 | 2.3x |
| Slow Grind (Oct 27 - Nov 12) | $1,155 | $3,148 | 1,065 | 80.4% | $1,992 | 2.7x |
| Exponential (Nov 12 - Dec 4) | $3,148 | $12,104 | 1,559 | 77.9% | $8,956 | 3.8x |
| Steady (Dec 4 - Jan 28) | $12,104 | $229,144 | 2,893 | 82.6% | $217,040 | 18.9x |

### 6.3 Allocation Per Trade

**Best-fit parameter: 0.5% of bankroll per trade** (daily compound model).

This is much smaller than intuition suggests. Why?

```
With ~60 qualifying windows/day and 81% WR:
  Expected daily return = 60 × 0.5% × EV_per_trade ≈ 60 × 0.005 × 0.20 = 6%/day

Over 107 days (Oct 13 - Jan 28):
  $507 × (1.06)^107 ≈ $507 × 476 = $241K  ✓ matches
```

The 0.5% allocation is conservative by design -- it avoids ruin while still compounding aggressively through sheer trade volume.

### 6.4 Concurrent Markets

W4 trades **multiple coins simultaneously in the same 15M window**:

From March 23 data: BTC + ETH + SOL + XRP all traded in the same 15M windows.

Maximum concurrent exposure per window:
```
4 coins × $300-$1400 per coin = up to $4,000+ deployed in a single 15M window
```

### 6.5 Compounding Model

From the era analysis (`w4_15m_era_analysis.py:551-604`):

```
Daily compound model (best fit):
  - Each trade uses alloc% of START-OF-DAY bankroll
  - Capital is locked for 15 min per trade
  - Cannot reinvest settled capital intraday (conservative assumption)
  - Bankroll updates at end of day when all positions settle

In reality, W4 likely does compound intraday:
  - A 15M trade settles in 15 minutes
  - The returned capital ($1.00 per winning share) is immediately available
  - Next window starts immediately (continuous markets)
  - So intra-day compounding is possible, making 0.5% per-trade even more conservative
```

---

## 7. PnL FORMULA (per window)

### 7.1 Both-Sides Fill (primary case)

```
Let S_L = shares on lean (momentum) side
Let S_H = shares on hedge (anti-momentum) side
Let p_L = price per share on lean side
Let p_H = price per share on hedge side
Let cost = S_L × p_L + S_H × p_H

If momentum correct (WR ≈ 81%):
    payout = S_L × $1.00 + S_H × $0.00 = S_L
    pnl = S_L - cost

If momentum wrong (1 - WR ≈ 19%):
    payout = S_L × $0.00 + S_H × $1.00 = S_H
    pnl = S_H - cost
```

### 7.2 With Canonical Parameters

```
Using: lean_ratio = 2:1, p_lean = $0.55, p_hedge = $0.41

Cost per unit trade:
    cpu = 2 × $0.55 + 1 × $0.41 = $1.51

If correct (81%):
    payout = 2 × $1.00 = $2.00
    pnl = $2.00 - $1.51 = +$0.49  (+32.5% ROI)

If wrong (19%):
    payout = 1 × $1.00 = $1.00
    pnl = $1.00 - $1.51 = -$0.51  (-33.8% ROI)

Expected PnL per unit:
    E[pnl] = 0.81 × $0.49 + 0.19 × (-$0.51)
           = $0.3969 - $0.0969
           = +$0.30 per unit trade (+19.9% EV)
```

### 7.3 Single-Side Fill (secondary case)

If only the lean side fills:
```
cost = S_L × p_L
If correct: pnl = S_L × $1.00 - cost = S_L × (1 - p_L)
If wrong:   pnl = 0 - cost = -S_L × p_L

E[pnl|lean_only] = WR × S_L × (1 - p_L) - (1 - WR) × S_L × p_L
                  = S_L × (WR - p_L)
                  = S_L × (0.81 - 0.55) = +$0.26 per share
```

If only the hedge side fills:
```
cost = S_H × p_H
If momentum wrong (hedge wins): pnl = S_H × $1.00 - cost = S_H × (1 - p_H)
If momentum correct (hedge loses): pnl = 0 - cost = -S_H × p_H

E[pnl|hedge_only] = (1 - WR) × S_H × (1 - p_H) - WR × S_H × p_H
                   = S_H × ((1 - WR) - p_H)
                   = S_H × (0.19 - 0.41) = -$0.22 per share  ← NEGATIVE EV!
```

**Hedge-only fills are -EV.** This is why W4 targets both-side fills -- the lean side provides the edge, the hedge reduces variance. A hedge-only fill is the worst outcome.

### 7.4 Expected PnL Per Window (accounting for fill rates)

Using the calibrated fill probability model (from LampStore data):
```
P_fill(delta) = exp(-4.84 × delta)    where delta = (1 - combined) / 2
P_both(delta) = P_fill(delta)^2.08

For combined = $0.96 (delta = 0.02):
    P_fill_one = exp(-4.84 × 0.02) = 0.908 (91%)
    P_both     = 0.908^2.08 = 0.818 (82%)
    P_lean_only  ≈ 0.09
    P_hedge_only ≈ 0.09
    P_neither    ≈ 0.00

E[pnl/window] = P_both × E[pnl|both] + P_lean × E[pnl|lean] + P_hedge × E[pnl|hedge]

With 2:1 lean, $0.55/$0.41:
    E[pnl|both]  = 0.81 × $0.49 + 0.19 × (-$0.51) = +$0.30
    E[pnl|lean]  = 0.81 × 2×$0.45 + 0.19 × (-2×$0.55) = +$0.52
                   (but at lower fill rate)
    E[pnl|hedge] = 0.19 × 1×$0.59 + 0.81 × (-1×$0.41) = -$0.22

    E[pnl/window] ≈ 0.82 × $0.30 + 0.09 × $0.52 + 0.09 × (-$0.22)
                   ≈ $0.246 + $0.047 - $0.020
                   ≈ +$0.27 per window
```

### 7.5 Polymarket Fee Impact

```
Fees:
  Taker: ~2% (1.56% net after maker rebate)
  Maker: ~0% (20% rebate on taker fee share)

W4 is 94.5%+ maker (from wallet analysis).
Maker rebate effectively means near-zero fees.

Effective fee per trade ≈ $0.00-$0.01 (negligible for limit orders)
```

---

## 8. CONCRETE WORKED EXAMPLE

### 8.1 Setup

```
Time:         2025-11-20 15:00:00 UTC (a window during the exponential phase)
Window:       btc-updown-15m-1732114800
Window start: 15:00:00 UTC
Window end:   15:15:00 UTC
BTC at open:  $96,500.00 (Binance 1M candle open at 15:00)
Bankroll:     $8,000 (mid-exponential phase)
```

### 8.2 Signal Detection (T+300s = 15:05:00 UTC)

```
BTC at T+300s: $96,573.00 (Binance 1M candle close at 15:05)

btc_return = log(96573 / 96500) = log(1.000757) = +0.000756
magnitude_bps = 0.000756 × 10000 = 7.56 bps

Check: 7.56 ≥ 5 bps? YES → SIGNAL FIRES
Direction: btc_return > 0 → lean UP
```

### 8.3 Entry Sizing

```
bet_pct = 0.5% = 0.005
total_budget = $8,000 × 0.005 = $40.00

Lean ratio = 2:1:
    up_budget   = $40.00 × (2/3) = $26.67 (lean = UP)
    down_budget = $40.00 × (1/3) = $13.33 (hedge = DOWN)
```

### 8.4 Entry Pricing

```
At T+300s with BTC +7.56 bps from open:

Empirical fair value (from backtest WR table):
    At 300s/5bps: WR = 81.0% → fair P(UP) ≈ 0.81

But Polymarket mid price at this point is NOT 0.81.
The CLOB is inefficient. Typical scenario:
    UP mid:   $0.55 (market hasn't fully priced in the 81% probability)
    DOWN mid: $0.43 (complement of UP mid ≈ $0.45, but spread exists)

W4 places limit BIDS slightly below mid:
    up_bid   = $0.55 (at mid, expecting fill as price rises toward fair)
    down_bid = $0.41 (below mid, expecting fill as opposing side cheapens)
    combined = $0.55 + $0.41 = $0.96
```

### 8.5 Order Execution

```
W4 doesn't place one large order. Instead:

Loop every 2-4 seconds for ~140 seconds:
    Check remaining budget
    Place small limit order ($3-$5) on UP side at $0.55
    Place small limit order ($1.50-$2.50) on DOWN side at $0.41

UP fills: 8 fills over 140s
    Fill 1: 5.45 shares × $0.55 = $3.00
    Fill 2: 9.09 shares × $0.55 = $5.00
    ... (6 more fills)
    Total UP: 48.49 shares × avg $0.55 = $26.67

DOWN fills: 5 fills over 140s
    Fill 1: 7.32 shares × $0.41 = $3.00
    Fill 2: 4.88 shares × $0.41 = $2.00
    ... (3 more fills)
    Total DOWN: 32.51 shares × avg $0.41 = $13.33

Total cost: $26.67 + $13.33 = $40.00
Combined average: $0.55 + $0.41 = $0.96
```

### 8.6 Resolution (T+900s = 15:15:00 UTC)

```
BTC at window close: $96,620.00
Close ≥ Open ($96,500)? YES → result = UP

W4 leaned UP → CORRECT

UP shares resolve at $1.00:  48.49 × $1.00 = $48.49
DOWN shares resolve at $0.00: 32.51 × $0.00 = $0.00

Gross payout:  $48.49
Total cost:    $40.00
Gross PnL:     +$8.49 (+21.2% ROI)
Fees (maker):  ~$0.00 (maker rebate)
Net PnL:       +$8.49
```

### 8.7 If Direction Was WRONG

```
BTC at close: $96,420 (hypothetical reversal)
Close < Open → result = DOWN → W4's lean (UP) was wrong

UP shares: 48.49 × $0.00 = $0.00
DOWN shares: 32.51 × $1.00 = $32.51

Gross payout:  $32.51
Total cost:    $40.00
Gross PnL:     -$7.49 (-18.7% ROI)
```

### 8.8 Expected Value of This Trade

```
E[PnL] = 0.81 × (+$8.49) + 0.19 × (-$7.49)
       = $6.88 - $1.42
       = +$5.45 per window (+13.6% EV)

At 60 qualifying windows/day:
    E[daily PnL] = 60 × $5.45 = $327
    Daily return  = $327 / $8,000 = 4.1%

With daily compounding:
    $8,000 × (1.041)^22 = $8,000 × 2.43 = $19,440
    (22 days = exponential phase)
```

---

## 9. REGIME SENSITIVITY

### 9.1 What Makes the Signal Work

The signal exploits **short-term momentum autocorrelation** in BTC:

```
If BTC moves +7 bps in the first 5 minutes of a 15-minute window,
there is an 81% probability it will still be positive at the end.

Why? Two mechanisms:
1. Trend inertia: macro flows that moved price in the first 5 min
   continue for the remaining 10 min (informed buying/selling)
2. Mean-reversion is weak: 10 minutes is too short for significant
   mean-reversion to overcome a 5+ bps move
```

### 9.2 When It Breaks Down

```
The WR drops in high-volatility regimes:
  Normal vol (2.07%/day): WR = 82.6%
  High vol (2.94%/day):   WR = 77.9%

Why? More volatility = more noise within the window = more reversals.
A 7 bps move at T+300s is less meaningful when 15M ranges are 41.8 bps
than when they are 26.8 bps.

But MORE windows pass the 5 bps threshold in high vol, so total EV/day
stays roughly constant:
  Low vol:  52.6 windows/day × higher WR ≈ similar daily EV
  High vol: 70.9 windows/day × lower WR  ≈ similar daily EV
```

---

## 10. COMPARISON: 15M vs 5M

| Metric | 15M (300s entry) | 5M (120s entry) |
|--------|-----------------|-----------------|
| WR at 5 bps | **81.0%** | **84.1%** |
| Windows qualifying | 7,364 | 17,085 |
| Time remaining after entry | 600s (10 min) | 180s (3 min) |
| Opportunities per day | ~60 | ~140 |
| Competition (Oct-Dec 2025) | LOW | N/A (didn't exist) |
| Competition (Mar 2026) | MEDIUM | HIGH |

**5M has higher WR but the 15M era had a PRICING EDGE** -- fewer competitors meant W4 could consistently buy below fair value. As 5M markets launched (Feb 2026), W4 migrated to trading both 5M and 15M.

---

## 11. COMPLETE DECISION TREE

```
Every 900 seconds (15M window boundary):

1. DISCOVER
   slug = f"btc-updown-15m-{floor(now / 900) * 900}"
   Fetch market from Polymarket Gamma API

2. WAIT (300 seconds)
   Observe BTC price via Binance

3. SIGNAL CHECK
   btc_return = log(P_300 / P_0)
   IF |btc_return| < 5 bps → SKIP this window
   IF |btc_return| >= 5 bps → CONTINUE

4. DETERMINE LEAN
   IF btc_return > 0 → lean = UP
   IF btc_return < 0 → lean = DOWN

5. COMPUTE BUDGET
   total = bankroll × 0.005
   lean_budget = total × 2/3
   hedge_budget = total × 1/3

6. EXECUTE (loop for ~140 seconds)
   REPEAT every 2-4 seconds:
     Place $3-$5 limit BID on lean side
     Place $1.50-$2.50 limit BID on hedge side
   UNTIL budget exhausted OR window approaching end

7. HOLD TO RESOLUTION
   No sells, no cancels (except unfilled orders at window end)
   Wait for Chainlink settlement

8. COMPOUND
   Settled capital returns to bankroll
   Next window: repeat from step 1
```

---

## 12. KEY UNKNOWNS AND CONFIDENCE LEVELS

| Parameter | Best Estimate | Confidence | Evidence |
|-----------|--------------|------------|----------|
| Signal: log return | log(P_d / P_0) | **HIGH** | Explicit in all backtest code |
| Delay: 300s | 300s for 15M era | **HIGH** | Best-fit in WR table + simulation |
| Threshold: 5 bps | 5 bps | **HIGH** | Growth simulation match |
| Lean ratio: 2:1 | 2:1 average, variable in practice | **MEDIUM** | Simulation fit is 2:1 but real data shows 1.04x - 12.71x |
| Combined cost: $0.96 | $0.96 in 15M era, >$1.00 in 2026 | **MEDIUM** | Era analysis best-fit vs current on-chain data |
| Allocation: 0.5% | 0.5% per trade | **HIGH** | Only value that matches $507 -> $241K growth |
| Fill model: many small orders | $3 median fills | **HIGH** | On-chain confirmation |
| Zero management | 100% hold-to-resolution | **HIGH** | Zero sells across 30,930 markets |
| Price source: Binance | Binance 1M klines | **MEDIUM** | Backtest uses Binance but W4 may use a different feed |
| Entry timing | 300s signal, fills over 140s median | **MEDIUM** | Backtest uses 300s but on-chain shows variable timing |
| Compounding model | Daily compound | **LOW** | Could be intraday compound (higher growth rate, lower allocation) |
| Multiple coins | Yes, concurrent | **HIGH** | On-chain confirms BTC+ETH+SOL+XRP in same windows |

---

## 13. FORMULAS IN LATEX-STYLE NOTATION

### Signal
```
r = ln(P_{T_0 + d} / P_{T_0})
|r| \geq \tau \Rightarrow \text{signal fires}
\text{direction} = \text{sgn}(r)
```

Where: `d = 300s`, `\tau = 5 \times 10^{-4}`

### Expected PnL (both-sides, 2:1 lean)
```
\text{cpu} = 2 p_L + p_H = 2(0.55) + 0.41 = 1.51

E[\text{PnL}] = WR \cdot (2 \cdot 1.00 - \text{cpu}) + (1 - WR) \cdot (1 \cdot 1.00 - \text{cpu})
             = 0.81 \cdot 0.49 + 0.19 \cdot (-0.51)
             = +0.30 \text{ per unit}
```

### Compound Growth
```
B_T = B_0 \cdot \prod_{d=1}^{D} \left(1 + n_d \cdot f \cdot \frac{E[\text{PnL}]}{\text{cpu}}\right)

\text{Where:}
B_0 = \$507
f = 0.005 \text{ (allocation fraction)}
n_d = \text{qualifying trades on day } d \approx 60
D = 107 \text{ trading days}
```

### Kelly Criterion Reference
```
f^* = \frac{WR \cdot W - (1 - WR) \cdot L}{W \cdot L}

\text{Where } W = 0.49/1.51 = 0.324, \; L = 0.51/1.51 = 0.338

f^* = \frac{0.81 \times 0.324 - 0.19 \times 0.338}{0.324 \times 0.338} = \frac{0.263 - 0.064}{0.110} = 1.81

\text{Full Kelly} = 181\% \text{ (absurd — the variance is low because of the hedge)}
\text{W4 actual} = 0.5\% \approx f^*/362 \text{ (extremely conservative)}
```

W4's 0.5% allocation is ~1/362 of full Kelly. This extreme conservatism is rational because:
1. The hedge reduces variance dramatically, so Kelly overestimates safe sizing
2. 60 trades/day means compound returns dominate -- even tiny edge × many trades = massive growth
3. Ruin avoidance is paramount -- a few bad days at high leverage would wipe out weeks of gains

---

## 14. REPLICATION CHECKLIST

To replicate W4's 15M strategy:

- [ ] Binance WebSocket for 1M BTC klines (real-time price feed)
- [ ] Polymarket CLOB client for limit order placement
- [ ] Slug calculator: `f"btc-updown-15m-{floor(now/900)*900}"`
- [ ] Signal engine: log return with 300s delay, 5 bps threshold
- [ ] Order engine: place $3-$5 limit bids every 2-4s on both sides
- [ ] Lean allocation: 2:1 ratio, 0.5% of bankroll per window
- [ ] Zero management: no sells, no cancels mid-window
- [ ] Bankroll tracking: compound after each window settles
- [ ] Multi-coin: optionally repeat for ETH, SOL, XRP
- [ ] 24/7 uptime: the strategy works best with maximum window coverage

---

*This specification was reverse-engineered from on-chain data, backtest results, and growth simulation. It represents our best mathematical reconstruction of Wallet 4's actual strategy. Individual parameters (especially lean ratio, combined cost, and entry timing) may vary from W4's true implementation.*
