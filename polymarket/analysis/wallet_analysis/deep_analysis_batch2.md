# Polymarket 錢包深度逆向工程 — Batch 2（8 Wallets）
> Date: 2026-03-21
> Confidence: **SEEN** = 數據直接觀察 | **INFERRED** = 推理 | **GUESSED** = 推測

---

## 一、總覽表

| # | Nickname | Address | PnL | Volume | Markets | Edge% | Strategy |
|---|----------|---------|-----|--------|---------|-------|----------|
| 7 | kafwhsd | 0xfdb8... | $108.2K | $27.5M | 10,502 | 0.39% | 15m Pure Arb |
| 8 | likebot | 0x03c3... | $107.7K | $25.5M | 27,038 | 0.42% | 5m Both-Sides (5-share lots) |
| 9 | xr9-PLM42 | 0xa130... | $101.1K | $28.5M | 22,071 | 0.36% | 5m Multi-Asset Arb+MERGE |
| 10 | Anon-0x8e9c | 0x8e9c... | $100.1K | $14.5M | 7,282 | 0.69% | 5m BTC Pure Arb (micro) |
| 11 | VOID-PEPPER | 0xa84e... | $99.0K | $11.1M | 22,965 | 0.89% | 5m+15m Multi-Asset |
| 12 | Ugly-Knock | 0x7846... | $116.3K | $27.2M | 11,534 | 0.43% | ★ Cross-Market 0.999 Certainty |
| 13 | purple-lamp-tree | 0x6fdc... | $95.5K | $14.2M | 27,294 | 0.67% | Multi-TF Both-Sides |
| 14 | MangoTrolley7 | 0xed86... | $88.6K | $12.8M | 6,710 | 0.69% | 5m Lottery + Arb Hybrid |

---

## 二、個別深度分析

### 7. kafwhsd (0xfdb8...) — 15m Pure Binary Arb

**Profile**: $108.2K / $27.5M / 10,502 markets / Joined Dec 2025

**SEEN**:
- 100% 15-minute windows（**SEEN**: all 200 sampled = 15m）
- BTC 41% + ETH 59%（**SEEN**: asset breakdown）
- All positions = -100% cashPnl（**SEEN**: losing side remnants）
- Bulk REDEEM: 98 positions in 66 seconds（**SEEN**: automated sweep）
- Largest single REDEEM: $103 BTC, $29.53 ETH（**SEEN**）
- 零 SELL, 零 MERGE in activity（**SEEN**: but MERGE must happen elsewhere）

**INFERRED 策略**:
```
kafwhsd = 純粹 15m binary arb bot
- 只做 BTC + ETH 15m（唔做 5m/1h/4h，唔做 SOL/XRP）
- 兩邊買 → MERGE → REDEEM cycle
- Activity API 只返 REDEEM（BUY 被 archived）
- 偏好 ETH（59%）→ 可能因為 ETH 15m 有更大 mispricing
- Per-market: $27.5M / 10,502 = $2,617 volume, $10.30 profit
- Daily: ~35 markets/day × $10.30 = $361/day
```

**Edge 分析**: 0.39% 偏低。可能因為 15m markets 比 5m 更 efficient（更多 liquidity）。

---

### 8. likebot (0x03c3...) — 5m Both-Sides, Fixed 5-Share Lots

**Profile**: $107.7K / $25.5M / 27,038 markets / Joined Nov 2025

**SEEN**（最詳細嘅 activity data）:
- **固定 5 shares per trade**（**SEEN**: 83/83 BUY trades = 5 shares or ~5）
- 100% BTC 5m in activity sample（**SEEN**）
- Positions = 95% 15m + 5% 5m（**SEEN**: 歷史上做過 15m）
- Both Up AND Down in same window（**SEEN**: comprehensive window-by-window data）
- REDEEM every ~5 minutes（**SEEN**: 17 REDEEMs in sample）
- Zero SELL, zero MERGE in activity（**SEEN**）
- Price range: 0.07 to 0.90（**SEEN**）
- Position sizes: 2,400-10,900 shares（**SEEN**: accumulated from many 5-share trades）

**SEEN — Window-by-Window Pattern**:
```
Window 3:35-3:40PM ET:
  BUY Up @ 0.18, 0.19, 0.20 (5 shares each)
  BUY Down @ 0.75, 0.75, 0.88 (5 shares each)
  Combined: 0.18+0.75=0.93, 0.19+0.75=0.94, 0.20+0.88=1.08
  → First 2 pairs = profitable, 3rd = loss

Window 3:05-3:10PM ET:
  BUY Up @ 0.10, 0.61 (5 shares)
  BUY Down @ 0.59, 0.78, 0.87 (5 shares each)
  Combined range: 0.10+0.59=0.69 (GREAT) to 0.61+0.87=1.48 (BAD)

Window 2:50-2:55PM ET:
  BUY Up @ 0.70
  BUY Down @ 0.08, 0.16, 0.16, 0.57
  → Buying cheap Down tickets + expensive Up = directional UP bias?
```

**INFERRED — likebot 嘅獨特策略**:
```
1. Fixed 5-share lots = EXTREME position granularity
   - 每個 trade 只 $0.50-$4.50
   - 一個 window 積累 10-20 trades → 50-100 shares per side
   - Smallest unit = easiest to fill as maker

2. Wide price range (0.07-0.90) = sweep entire book
   - 唔係等特定 price → 掃所有有利 level

3. Pattern: 方向明確後加注贏嗰邊
   - 3:05PM window: BTC going down → heavy Down buying (3 trades)
   - 但同時 buy Up @ 0.10 作為 cheap hedge

4. 歷史上做 15m，轉咗去 5m
   - Positions: 95% 15m（Jan-Feb history）
   - Activity: 100% 5m（current Mar 20）
   - → 策略進化：15m → 5m（更多 windows = 更多 opportunities）
```

**數學**:
```
27,038 markets / ~130 days = 208 markets/day
$107.7K / 27,038 = $3.98/market
$25.5M / 27,038 = $943/market volume
Edge: 0.42%

5-share lots × ~15 trades per window = 75 shares per side
Deployed per window: 75 × $0.50 avg = $37.50 per side = $75 total
Very capital efficient — can run 100+ windows simultaneously
```

---

### 9. xr9-PLM42 (0xa130...) — 5m Multi-Asset Arb + Active Management

**Profile**: $101.1K / $28.5M / 22,071 markets / Joined Feb 22, 2026

**SEEN**:
- 100% 5-minute windows（**SEEN**: positions + activity all 5m）
- 4 assets: BTC 55%, ETH 22%, SOL 12%, XRP 11%（**SEEN**: activity breakdown）
- Both Up and Down in same window（**SEEN**: clear in activity burst）
- MERGE present: $798 BTC merge in sample（**SEEN**）
- **Realized PnL non-zero**: -$459 to +$840 per market（**SEEN**）
- 100 trades in 86 seconds（**SEEN**: bot）
- Variable trade sizes: $0.11 to $94.19（**SEEN**）

**💡 Key Discovery — Active Management Evidence**:
```
SEEN in position data:
  BTC Mar 18 4:35PM: Size 876, avgPrice 0.0523, realizedPnl +$789.18
  BTC Mar 20 12:15PM: Size 682, avgPrice 0.0287, realizedPnl +$644.45
  BTC Mar 17 7:30PM: Size 362, avgPrice 0.1078, realizedPnl +$772.56

  realizedPnl >> 0 on many positions = actively closing for profit
  Some with realizedPnl < 0 = cutting losses too

  This is NOT set-and-forget arb → active management with partial exits
```

**INFERRED 策略**:
```
xr9-PLM42 = Binary Arb + Active Directional Overlay:

1. 開 window → 兩邊買（BTC dominant, then ETH/SOL/XRP）
2. Direction 明確 → MERGE 匹配 pairs
3. 有 directional alpha → 加注贏嗰邊（variable sizing）
4. realizedPnl 反映 partial close before expiry

Key difference from pure arb:
- Pure arb: hold to expiry, MERGE all
- xr9: actively manage, partial close, directional overlay
- Result: higher per-market variance but potentially higher edge
```

**數學**:
```
22,071 markets / 26 days = 849 markets/day (HIGHEST throughput!)
$101.1K / 22,071 = $4.58/market
$28.5M / 22,071 = $1,290/market volume
Edge: 0.355% (lowest in batch)
But daily: 849 × $4.58 = $3,888/day (second highest after BoneReader)
```

---

### 10. Anon-0x8e9c — 5m BTC Pure Arb (Most Detailed Data)

**Profile**: $100.1K / $14.5M / 7,282 markets

**SEEN — Per-Window P&L（最珍貴嘅數據）**:
```
Window         | UP cost | DOWN cost | Total In | Redeemed | Net P&L
4:05-4:10PM    |   $0.00 |    $2.31  |    $2.31 |   $89.30 | +$86.99 ★
4:10-4:15PM    |  $40.08 |    $8.76  |   $48.84 |   $59.25 | +$10.41
4:15-4:20PM    |  $29.40 |   $78.54  |  $107.94 |  $108.47 | +$0.53
4:20-4:25PM    |  $33.33 |   $60.51  |   $93.84 |   $80.47 | -$13.37
4:25-4:30PM    |   $9.02 |    $6.44  |   $15.46 |   $13.78 | -$1.68
4:30-4:35PM    |   $6.71 |   $11.70  |   $18.41 |   $17.78 | -$0.63
4:35-4:40PM    |  $50.72 |   $76.05  |  $126.77 |  $152.15 | +$25.38
```

**INFERRED — 逐 Window Win Rate**:
```
7 closed windows: 3 wins, 1 breakeven, 3 losses
Win rate: 43-57% (counting breakeven)
But net = +$66.67 (profitable because wins > losses in magnitude)

Winner bias:
  Big win ($86.99): Only bought DOWN $2.31 → BTC went down hard
    → This was a DIRECTIONAL play, not arb
  Medium win ($25.38): Balanced buy → arb profit
  Small win ($10.41): Balanced buy → arb profit

Loser sizes: -$13.37, -$1.68, -$0.63 (small)
Winners: +$86.99, +$25.38, +$10.41, +$0.53 (large)

Positive skew! Losses are capped but wins can be large.
```

**💡 Critical Insight — Bimodal Price Distribution**:
```
SEEN price buckets:
  ~0.2: 20 trades (buying cheap)
  ~0.7-0.8: 37 trades (buying expensive)

  Cheap buys (0.2) = one side
  Expensive buys (0.7-0.8) = other side
  Combined: 0.2 + 0.7 = $0.90 (10% edge!)
  Combined: 0.2 + 0.8 = $1.00 (breakeven)

  The bot buys CHEAP first, then buys EXPENSIVE up to breakeven
  Edge is entirely from the early cheap fills
```

**數學**:
```
7,282 markets / ~120 days = 61 markets/day
$100.1K / 7,282 = $13.74/market (2nd highest per-market)
$14.5M / 7,282 = $1,990/market volume
Edge: 0.69% (high)

Session data: +$66.67 on $413.58 = 16.1% return in 34 minutes
Annualized: meaningless but shows the edge is real

Per-window average: +$9.52 (consistent with $13.74 overall average)
```

---

### 11. VOID-PEPPER (0xa84e...) — 5m+15m Multi-Asset

**Profile**: $99.0K / $11.1M / 22,965 markets / Joined Jan 30, 2026

**SEEN**:
- BTC + ETH + XRP + SOL（**SEEN**: all 4 in activity）
- 5m + 15m mix（**SEEN**）
- Both Up and Down simultaneously（**SEEN**）
- Price range: 0.10-0.90（**SEEN**）
- Trade sizes: 1-100 shares（**SEEN**: variable）
- MERGE present（**SEEN**: 3 in sample）

**INFERRED**:
```
VOID-PEPPER = multi-asset arb bot covering all 4 assets × 2 timeframes

最高 edge% in batch: 0.89%
But lowest volume ($11.1M) → concentrated on best opportunities?

Daily: 22,965 / 50 days = 459 markets/day
$99K / 459 / 50 = $43/day → seems low
Actually: $99K / 50 days = $1,980/day → healthy

Per-market: $99K / 22,965 = $4.31
Volume per market: $11.1M / 22,965 = $483 (SMALLEST)
→ Small bets, high selectivity, high edge
```

---

### 12. Ugly-Knock (0x7846...) — ★ CROSS-MARKET CERTAINTY ARB ★

**Profile**: $116.3K / $27.2M / 11,534 markets / Joined Sep 2025

**SEEN（最獨特嘅策略）**:
- **ALL positions at 0.999 avg price**（**SEEN**: every single one）
- **SPORTS markets**: Football, Basketball, Esports, Tennis（**SEEN**）
- **CRYPTO markets**: BTC 5m, SOL, weather（**SEEN**: in activity）
- **Position sizes**: 91 to 8,000 shares（**SEEN**: huge range）
- **Trade count**: 89 BUY + 11 REDEEM in sample（**SEEN**）
- **All buys at 0.99-0.999**（**SEEN**: uniform price）

**SEEN — Open Positions（snapshot of strategy in action）**:
```
Football: Karlsruher vs Greuther Fürth O/U 2.5 → Over @ 0.999 → 8,000 shares
Football: Cagliari vs Napoli BTTS → No @ 0.999 → 8,000 shares
Esports: Falcons vs NaVi Map 2 → NaVi @ 0.999 → 7,668 shares
Basketball: Akron vs Texas Tech O/U 155.5 → Over @ 0.999 → 2,903 shares
Basketball: Zalgiris vs Real Madrid → Zalgiris @ 0.999 → 2,833 shares
Weather: Tel Aviv temp 21°C → No @ 0.999 → tiny
```

**💡💡 Ugly-Knock = BoneReader 嘅 cross-market 版本**:

```
Strategy: Buy nearly-settled outcomes at $0.999

How it works:
1. Monitor ALL Polymarket markets (sports, crypto, weather, politics)
2. When outcome is >99.9% certain (game nearly over, BTC direction clear)
3. Buy the winning side at $0.999
4. Profit: $0.001/share × thousands of shares

Edge per trade:
  Buy at $0.999, settle at $1.00
  Profit: $0.001/share
  On 8,000 shares: $8.00 per market

  But risk: if somehow wrong, lose $0.999 × 8,000 = $7,992

  Break-even: P(win) > 99.9%
  For +EV: need certainty > 99.9%

Math verification:
  $116.3K / 11,534 = $10.08/market
  $27.2M / 11,534 = $2,358/market volume

  If avg 2,000 shares at $0.999:
    Cost = $1,998
    Revenue if win = $2,000
    Profit = $2.00/market

  But actual profit = $10.08/market → higher than $2
  → Average position larger OR price slightly below $0.999

  Implied win rate:
    $10.08 = P(win) × $2 - P(lose) × $1,998
    If P(win) = 0.9994: 0.9994×2 - 0.0006×1998 = 1.9988 - 1.1988 = $0.80 ← too low

  Need to recalculate with actual position sizes:
    8,000 shares: profit if win = $8, loss if lose = $7,992
    For $10.08 avg profit across mix of sizes...

  The key: Ugly-Knock has MASSIVE volume across many markets
  Sports have known outcomes (game is essentially over)
  Crypto has late-window certainty
  Weather has hourly resolution with known data

  TRUE edge: being FAST enough to buy at 0.999 before market settles
  → Latency arb on near-certain outcomes across ALL market categories
```

**INFERRED — 操作模型**:
```
Ugly-Knock's bot:
1. Monitor ALL active markets on Polymarket (sports, crypto, weather, etc.)
2. For each market:
   a. Determine current certainty level (from external data feeds)
   b. If certainty > 99.9% AND market price < 0.999 for winning side
   c. → BUY maximum size
3. Revenue = volume × 0.001 (tiny edge but massive scale)

Why it works:
- Polymarket has thousands of markets daily
- Many resolve predictably in final minutes
- Sports games with 3-goal leads, crypto windows with 0.5% moves
- Market makers may not update prices fast enough
- Taker fee at 0.999 is near-zero (fee function → 0 at extremes)

Revenue model:
  11,534 markets × $10.08/market = $116.3K
  ~37 markets/day × $10.08 = $373/day

  But across ALL market categories → near-infinite supply of opportunities
```

---

### 13. purple-lamp-tree (0x6fdc...) — Multi-TF Both-Sides

**Profile**: $95.5K / $14.2M / 27,294 markets / Joined Feb 16, 2026

**SEEN**:
- Multi-timeframe: 5m, 15m, 1h, 4h（**SEEN**: all four in positions）
- Multi-asset: BTC, ETH, XRP, SOL（**SEEN**）
- Both Up AND Down positions in same market（**SEEN**: ETH 5m has both）
- Variable sizing: 1 to 313 shares（**SEEN**）
- Price range: 0.04 to 0.95（**SEEN**）
- Some positions winning, some losing simultaneously（**SEEN**）

**SEEN — Simultaneous Both-Side Positions**:
```
ETH 4:25-4:30PM ET:
  Down: 313 shares @ $0.832 → +$16.61 (+6.4%)
  Up: 312 shares @ $0.382 → -$83.40 (-69.9%)
  Combined: 0.832 + 0.382 = $1.214 → OVER PAR!

  This is NOT arb — combined > $1.00
  → purple-lamp-tree is buying both sides at MARKET prices
  → Taking liquidity, paying taker fees
  → Relying on winning side more than covering losing side
```

**INFERRED**:
```
purple-lamp-tree = Aggressive Both-Sides Taker:

Unlike makers (stargate5, kafwhsd), purple-lamp-tree appears to:
1. TAKE liquidity (buy at market price)
2. Combined often > $1.00 (no arb edge from spread)
3. Edge comes from: DIRECTIONAL selection
   - Buy more of the side that's likely to win
   - UP: 312 shares vs DOWN: 313 shares → nearly equal
   - But price allocation favors DOWN (higher avg price on DOWN)

4. Multi-timeframe gives diversification
   - Same direction across 5m/15m/1h/4h
   - Similar to BoneReader's cascade but both sides

Daily: 27,294 / 33 days = 827 markets/day (massive throughput)
Per-market: $95.5K / 27,294 = $3.50
Edge: 0.67%
```

---

### 14. MangoTrolley7 (0xed86...) — Lottery Ticket + Arb Hybrid

**Profile**: $88.6K / $12.8M / 6,710 markets / Joined Jan 30, 2026

**SEEN**:
- BTC dominant（**SEEN**: minimal ETH）
- **Extreme low-price entries**（**SEEN**: avg $0.013-$0.018）
- **Massive share counts**: 194,350 shares in single position（**SEEN**）
- **Huge losses on losers**: -$2,743, -$2,581（**SEEN**: expected for lottery）
- **Largest single win**: $13,087.56（**SEEN**: highest of all 14 wallets）
- Normal activity too: MERGE $669, BUY at 0.02-0.87, REDEEM $306（**SEEN**）
- Trade sizes: 20-30 shares per trade（**SEEN**）

**INFERRED — Lottery Ticket Math**:
```
Example position:
  BTC Feb 27 10:20-10:25PM: 170,543 shares @ $0.0161
  Cost: $2,743
  If wins: 170,543 × $1.00 = $170,543 payout
  If loses: -$2,743

  Break-even: P(win) = $2,743 / $170,543 = 1.61%
  Market price implies: 1.61% chance of winning

  For this to be +EV:
  True P(win) must be > 1.61%

  If true P = 3%: EV = 0.03 × $170,543 - $2,743 = $5,116 - $2,743 = +$2,373
  If true P = 2%: EV = 0.02 × $170,543 - $2,743 = $3,411 - $2,743 = +$668

Where does MangoTrolley7 find edge?
1. 5-minute BTC markets at extreme prices (0.01-0.02)
2. Market says "1-2% chance BTC reverses direction in 5 minutes"
3. MangoTrolley7 believes true reversal rate is higher
4. Fat-tailed BTC returns mean extreme moves happen more than expected
5. If kurtosis >9 (from our BTC volatility data), reversal rate IS higher

Implied kurtosis edge:
  Normal distribution: P(>2σ reversal in 5 min) ≈ 2.28%
  Fat-tailed (kurtosis=9): P(>2σ) ≈ 4-6%
  Market prices at 0.015 = 1.5% implied probability
  True probability ≈ 4% → 2.67x edge!
```

**Portfolio model**:
```
Mix of:
1. Lottery tickets (0.01-0.03): ~30% of capital, ~70% of upside
2. Normal arb (0.30-0.87): ~70% of capital, ~30% of upside

$88.6K profit breakdown (GUESSED):
  Lottery wins: ~$50K (few but huge)
  Arb income: ~$35K (steady)
  Maker rebates: ~$3K

Largest win $13,088 = single lottery ticket payout
= 15% of total PnL from one trade
→ High concentration risk
```

---

## 三、Batch 2 策略聚類

| Cluster | Wallets | Core Edge | Risk |
|---------|---------|-----------|------|
| **A1: Pure Arb** | kafwhsd, Anon-0x8e9c | Combined < $1.00 | Low |
| **A2: Multi-Asset Arb** | VOID-PEPPER, xr9-PLM42 | Same + asset diversification | Low |
| **A3: Arb + Direction** | likebot, purple-lamp-tree | Arb base + directional overlay | Medium |
| **C2: Cross-Market Certainty** | ★ Ugly-Knock | Near-settled outcomes at 0.999 | Medium-High |
| **D: Lottery + Arb** | MangoTrolley7 | Fat-tail mispricing | High |
