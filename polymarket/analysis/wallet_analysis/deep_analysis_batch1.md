# Polymarket 錢包深度逆向工程 — Batch 1（6 Wallets）
> Date: 2026-03-21
> Method: Profile + Position + Activity data reverse engineering
> Confidence markers: **SEEN** = 數據直接觀察 | **INFERRED** = 從數據推理 | **GUESSED** = 推測

---

## 一、總覽表

| # | Nickname | Address (short) | PnL | Volume | Markets | Days | Edge% | Strategy |
|---|----------|----------------|-----|--------|---------|------|-------|----------|
| 1 | blankandyellow | 0xdc1e... | $45.9K | $15.3M | 12,646 | 35 | 0.30% | 5m Arb+Momentum |
| 2 | blue-walnut | 0x4b18... | $103.9K | $21.7M | 4,649 | 50 | 0.48% | 15m Multi-Asset |
| 3 | BoneReader | 0xd84c... | $881.3K | $154.7M | 43,113 | 60 | 0.57% | Multi-TF Directional |
| 4 | Brundle | 0x76bc... | $131.2K | $14.5M | 2,300 | 420+ | 0.91% | Hourly+Range MM |
| 5 | stargate5 | 0xb4d2... | $126.7K | $22.4M | 13,238 | 120 | 0.57% | 5m Pure Arb |
| 6 | mapleghost | 0x3963... | $116.1K | $19.9M | 19,080 | 50 | 0.58% | 15m/5m Arb |

---

## 二、策略聚類（3 大類）

### Cluster A：Binary Arb — 兩邊買 + MERGE（stargate5, mapleghost, blankandyellow）
### Cluster B：Range MM + Active Management（Brundle, blue-walnut）
### Cluster C：Directional High-Confidence（BoneReader）

---

## 三、Cluster A 深度分析 — Binary Arb

### 3.1 核心機制

**SEEN**: stargate5、mapleghost、blankandyellow 全部做同一件事：
1. 每個 window 買 UP + DOWN 兩邊
2. MERGE 匹配嘅 pairs → 每 pair 收回 $1.00
3. 殘餘未匹配 shares → 等 expiry（贏嗰邊 REDEEM，輸嗰邊歸零）

**數學模型**:

```
Per window:
  Buy Q_up shares of UP at avg price P_up
  Buy Q_down shares of DOWN at avg price P_down

  Matched pairs = min(Q_up, Q_down)
  Excess = |Q_up - Q_down| shares on one side

  MERGE revenue = Matched × $1.00
  MERGE cost    = Matched × (P_up + P_down)
  MERGE profit  = Matched × (1 - P_up - P_down)
                = Matched × (1 - P_combined)

  Excess EV:
    If Q_up > Q_down: Excess × (P_fair_up - P_up)  [directional bet]
    If Q_down > Q_up: Excess × (P_fair_down - P_down)

  Total profit = MERGE profit + Excess EV + Maker rebate - Fees
```

**關鍵洞察：edge 來源**

**INFERRED**: Combined < $1.00 嘅機會來自 3 個源頭：

| Source | Mechanism | 持續性 |
|--------|-----------|--------|
| **Microstructure gap** | UP 和 DOWN 由唔同 taker 推動，價格唔係即時 sync | 高（結構性） |
| **Taker fee wedge** | Taker 買 UP 付 ~3% fee → UP market price 被壓低；DOWN 同理 | 高（protocol 設計） |
| **Volatility spike** | BTC 急動 → 一邊價格 spike，另一邊 lag → combined 跌穿 $1.00 | 中（event-driven） |
| **Maker rebate** | 25% of taker fees → 純利潤加成 | 高（guaranteed） |

### 3.2 stargate5 — 最純嘅 Arb Bot

**Profile**: $126.7K PnL / $22.4M volume / 13,238 markets / 100% BTC 5-min

**SEEN patterns**:
- 每個 trade 固定 ~100 shares（**SEEN**: 100-104 shares per trade）
- 每個 window 買 8-14 次每邊（**SEEN**: offset data）
- MERGE 緊接每個 window（**SEEN**: $300-$1,100 per merge）
- REDEEM 只有 $0.10-$1.19（**SEEN**: tiny residuals）
- 零 SELL（**SEEN**: no sell in 400+ records）
- 價格 range: 0.09 到 0.92（**SEEN**）

**INFERRED 策略邏輯**:
```
stargate5 嘅 bot cycle（每 5 分鐘）:

1. Window 開始 → 開始落 maker limit orders
2. UP side: 由低價開始往上掃（0.38, 0.45, 0.52...0.89）
3. DOWN side: 同步由低往上掃（0.22, 0.26, 0.31...0.57）
4. 每次 fill 100 shares → 控制 per-fill risk
5. 兩邊都 fill 到足夠 → 停止
6. MERGE 匹配 pairs → 鎖定利潤
7. Residual → 等 expiry，贏嗰邊 REDEEM
8. 資金回收 → 下一個 window
```

**數學推算**:

```
Per market average:
  Volume deployed: $22.4M / 13,238 = $1,692
  Profit: $126.7K / 13,238 = $9.57
  Edge: $9.57 / $1,692 = 0.565%

Volume 拆解（INFERRED）:
  假設 volume 包含 buy + merge + redeem:
  Buy volume ≈ 60% of total = $1,015
  → $507 per side
  → ~1,000 shares per side at avg $0.50

  MERGE = ~1,000 pairs × $1.00 = $1,000
  → 呢啲都計入 volume

  Actual capital per window ≈ $1,015
  Net profit = $9.57 → 0.94% return per window

Daily capacity:
  5m windows per day = 288
  If bot covers 50-80 windows/day: $478-$766/day
  Actual: $126.7K / 120 days = $1,056/day
  → 覆蓋 ~110 windows/day（38% coverage）
```

**Combined price 分佈（INFERRED from position + merge data）**:

```
stargate5 一個 window 嘅 example（SEEN）:
  UP buys:   0.38, 0.45, 0.52, 0.53, 0.58, 0.60, 0.62, 0.64, 0.70, 0.85, 0.89
  DOWN buys: 0.25, 0.31, 0.33, 0.38, 0.41, 0.43, 0.47, 0.50, 0.53, 0.57, 0.66

  早期 fills (combined): 0.38+0.25=0.63, 0.45+0.31=0.76 → 好平
  中期 fills: 0.58+0.41=0.99, 0.60+0.43=1.03 → 接近/超過 $1.00
  後期 fills: 0.85+0.57=1.42, 0.89+0.66=1.55 → 虧損

  策略：早期 fills 係利潤核心，後期 fills 係成本
  MERGE 只 match 有利嘅 pairs → 選擇性配對
```

**💡 Critical insight**: stargate5 唔係盲目 match。佢 buy both sides 嘅策略令到佢有 OPTIONALITY — 可以選擇點樣 pair up shares to maximize profit。

**INFERRED 配對邏輯**:
```
Optimal MERGE strategy:
  Sort UP fills by price (ascending): [0.38, 0.45, 0.52, ...]
  Sort DOWN fills by price (ascending): [0.25, 0.31, 0.33, ...]

  Pair cheapest UP with cheapest DOWN:
    Pair 1: 0.38 + 0.25 = 0.63 → profit $0.37/share
    Pair 2: 0.45 + 0.31 = 0.76 → profit $0.24/share
    ...continue until combined ≥ $1.00

  Stop pairing when next pair would be ≥ $1.00
  MERGE all profitable pairs
  Hold excess as directional bet
```

**但實際上 MERGE 係 atomic operation**（**INFERRED**）— Polymarket MERGE 係將等量 UP + DOWN 一次過合併，唔能選擇性配對。所以 stargate5 嘅策略更可能係：

```
Revised model:
1. 先喺低價位買兩邊（確保 combined < $1.00）
2. 累積到足夠 → MERGE 第一批
3. 繼續喺較高價位買 → 但只買 delta（差額）
4. 隨住 window 進行，combined 逐漸接近/超過 $1.00 → 停止
5. Final MERGE → 清理所有 matched pairs
6. Residual = 淨方向性暴露 → 等 expiry
```

### 3.3 mapleghost — 15M 為主 + 5M 混合

**Profile**: $116.1K PnL / $19.9M volume / 19,080 markets / BTC 96%

**SEEN patterns**:
- 15m (65%) + 5m (35%) 混合
- BTC 96%, ETH 2%, XRP 2%
- 500 activity records 全部集中喺一個 15m window（**SEEN**）
- 498 TRADE + 2 MERGE（**SEEN**）
- 價格 range: DOWN 0.13-0.64, UP 0.43-0.97（**SEEN**）
- Single MERGE = 521 shares（**SEEN**）
- TotalBought per position 達 $5,000+（**SEEN**）
- RealizedPnl 大幅波動: -$930 to +$959（**SEEN**）

**INFERRED 策略差異 vs stargate5**:

| Dimension | stargate5 | mapleghost |
|-----------|-----------|------------|
| Asset | 100% BTC | 96% BTC + ETH/XRP |
| Timeframe | 100% 5m | 65% 15m + 35% 5m |
| Trade size | ~100 shares fixed | Variable (micro-trades) |
| Per-market volume | $1,692 | $1,041 |
| Per-market profit | $9.57 | $6.08 |
| Management | MERGE per window | MERGE mid-window + RealizedPnl non-zero |

**關鍵差異：mapleghost 有 active management**（**INFERRED from SEEN data**）

```
Evidence:
- RealizedPnl 非零（-$930 to +$959）→ 有 partial exit/rebalance
- 而 stargate5 嘅 RealizedPnl 幾乎全部 = 0
- mapleghost MERGE 出現喺 window 中間（唔係尾段）
- mapleghost 嘅 price range 更闊（UP 到 0.97）→ 追高
```

**INFERRED**: mapleghost = stargate5 + partial directional overlay。當 market trend 明確時，mapleghost 會加倉贏嗰邊（追高到 0.97），同時 MERGE 匹配部分提早鎖定利潤。

### 3.4 blankandyellow — 5M Arb + Late-Window Momentum

**Profile**: $45.9K PnL / $15.3M volume / 12,646 markets / 35 days

**SEEN patterns**:
- 5-minute BTC + ETH（**SEEN**: exclusively 5m）
- MERGE 頻繁（$580-$621 per merge）
- 明確嘅 late-window behavior:
  - 3:25PM window end: 買 UP at 0.93-0.99 AND DOWN at 0.05-0.18（**SEEN**）
  - 3:40PM window: 買 UP at 0.73-0.87（**SEEN**: momentum chasing）
  - 96-99% positions = -100% cashPnl（**SEEN**）
- Entry timing: 多次 fill 喺 2-3 second window 內（**SEEN**）

**INFERRED 雙重策略模型**:

```
Phase 1 — Binary Arb（window 前半段，0-3 分鐘）:
  同 stargate5 一樣 → 兩邊買 + MERGE

Phase 2 — Momentum Chase（window 後半段，3-5 分鐘）:
  BTC 方向已定 → 單邊加注
  例：UP at 0.93-0.99 when BTC clearly up
  同時 hedge with DOWN at 0.05-0.18（但金額少得多）

  Late-window combined: 0.99 + 0.05 = $1.04 → 唔係 arb！
  呢個 phase 係 directional bet disguised as both-sides
```

**數學模型（Late-Window Momentum）**:

```
At t = 4 minutes into 5-min window:
  Remaining time = 60 seconds
  BTC σ_annual ≈ 50%
  σ_1min = 50% / √(525,600) ≈ 0.069%

  If BTC is already +0.20% above open:
    Need to drop 0.20% in 1 min to reverse
    = 0.20 / 0.069 = 2.90 sigma
    P(reversal) ≈ 0.19%
    P(UP wins) ≈ 99.81%

    Fair price UP = $0.998
    Buying at $0.99 → edge = $0.008/share
    Buying at $0.95 → edge = $0.048/share

  If BTC is only +0.05% above open:
    = 0.05 / 0.069 = 0.72 sigma
    P(reversal) ≈ 23.6%
    P(UP wins) ≈ 76.4%

    Fair price UP = $0.764
    Buying at $0.93 → NEGATIVE edge!
    Buying at $0.73 → edge = $0.034/share
```

**INFERRED**: blankandyellow 嘅 late-window plays 只有喺 BTC move > 0.15% 嘅時候先至 +EV。佢嘅 bot 大概有個 threshold filter。

**blankandyellow vs stargate5 比較**:

| | stargate5 | blankandyellow |
|--|-----------|----------------|
| Edge source | Pure arb (combined < $1) | Arb + momentum |
| Risk | Near-zero (matched pairs) | Higher (directional exposure) |
| Per-market profit | $9.57 | $3.63 |
| Per-market volume | $1,692 | $1,214 |
| Edge% | 0.565% | 0.299% |
| Capacity | 110 windows/day | 361 windows/day |
| Daily income | $1,056 | $1,313 |

**INFERRED**: blankandyellow 用更高 throughput 彌補更低 edge%。但 0.30% edge 係 6 個錢包中最低 → 最脆弱，fee 結構改變就可能翻負。

---

## 四、Cluster B 深度分析 — Range MM + Active Management

### 4.1 Brundle — 最複雜嘅策略

**Profile**: $131.2K PnL / $14.5M volume / 2,300 markets / Joined Feb 2025

**SEEN patterns**:
- 最舊嘅錢包（>13 個月歷史）（**SEEN**: joined Feb 2025）
- **有 SELL activity**（**SEEN**: 48 sells in 313 trades = 15.3%）
- Hourly Up/Down + Daily range markets（**SEEN**: BTC <$110K, $110-112K, etc.）
- Multi-asset: ETH 60%, BTC 33%, SOL 3%, XRP 1%（**SEEN**）
- Burst trading: 68 trades in 240 seconds（**SEEN**）
- 兩邊買 + 賣 pattern: BUY Down@0.71, BUY Up@0.30, SELL Down@0.70（**SEEN**）
- 批量 MERGE at end of day: 20,626 USDC in 13 simultaneous merges（**SEEN**）
- 批量 REDEEM: 32,066 USDC across 74 records（**SEEN**）
- All sampled positions = -100% cashPnl（**SEEN**）

**INFERRED — Brundle 係真正嘅 Market Maker**:

```
Key evidence:
1. SELL activity → 唔只係 buy-and-hold，主動 exit positions
2. Buy Down@0.71, Sell Down@0.70 → 1¢ spread capture
3. 同時 Buy Up@0.30 → combined = 0.71+0.30 = $1.01 → slightly over par
4. 但 SELL at 0.70 recovers → net combined after sell = ????

Actually:
  Buy Down 1398 shares @ $0.71 = $992
  Buy Up 1285 shares @ $0.30 = $386
  Sell Down 626 shares @ $0.70 = +$438 (recovered)

  Net position:
    Up: 1285 shares @ $0.30
    Down: 772 shares (1398-626) @ $0.71
    Cash recovered: $438

  Net cost: $992 + $386 - $438 = $940
  Net shares: 1285 Up + 772 Down

  If MERGE 772 pairs: 772 × $1.00 = $772
  Remaining: 513 Up shares @ $0.30 → directional bet

  Net cost after merge: $940 - $772 = $168
  513 Up shares at effective cost $168/513 = $0.327/share
  Fair value if 50/50: $0.50 → edge = $0.173/share
```

**💡 Brundle 嘅真正策略：Active Rebalancing MM**

```
Brundle's cycle:
1. Open: Buy both sides at favorable prices
2. Monitor: Watch for price movement
3. If one side moves against → SELL that side (cut loss / capture spread)
4. Rebalance: Adjust position to maintain hedge
5. End of day: MERGE remaining matched pairs
6. Excess: Hold to expiry or sell before

vs stargate5 (passive arb):
  stargate5: Buy → MERGE → done
  Brundle: Buy → Sell → Rebuy → Adjust → MERGE → Redeem

  More complex = more edge opportunities but also more execution risk
```

**數學模型 — Spread Capture + Binary Arb Hybrid**:

```
Revenue sources for Brundle:

1. Spread capture: Buy at bid, sell at ask (1-2¢ per round trip)
   Per trade: ~$0.01 × shares
   Volume: 48 sells / 265 buys = 18% turnover

2. Binary arb: Matched pairs MERGE for $1.00
   Per pair: (1 - combined) × shares
   End-of-day MERGE: 20,626 USDC

3. Directional residual: Unmatched shares to expiry
   Win rate dependent on selection skill

4. Range market plays: Daily price range brackets
   Example: BTC $110K-$112K → binary outcome
   If BTC ends at $111K, "Yes" pays $1.00
   Buy "Yes" at $0.40, "No" at $0.40 → combined $0.80 → $0.20 profit per pair

Combined:
  $131.2K / 2,300 markets = $57.04/market (HIGHEST per-market profit)
  $14.5M / 2,300 = $6,297/market volume
  Edge: 0.91% (HIGHEST edge%)
```

**INFERRED**: Brundle 嘅 0.91% edge 係最高，因為：
1. Range markets（daily）有更大 mispricing（$0.80 combined vs $0.95-0.97 in 5m/15m）
2. Active management 可以 cut losses + add winners
3. 較長 time horizon（hourly/daily）= 更多 time for edge to materialize
4. 但 throughput 最低（2,300 markets / 420 days = 5.5/day）→ 唔 scalable

### 4.2 blue-walnut — Multi-Asset 15M Player

**Profile**: $103.9K PnL / $21.7M volume / 4,649 markets / 50 days

**SEEN patterns**:
- Multi-asset: BTC, ETH, SOL, XRP（**SEEN**: all four）
- 15M windows（**SEEN**: hourly notation implies ~15m-1h）
- Extreme 低價 entry with 大 win（**SEEN**: avg 0.01 → +$3,443; avg 0.005 → +$1,131）
- Extreme 高價 entry with 大 loss（**SEEN**: avg 0.545 → -$2,264）
- totalBought per position: $512 to $15,750（**SEEN**: massive range）
- Fragmented execution: $0.01 to $16.75 per individual trade（**SEEN**）
- MERGE present（**SEEN**: $543 ETH merge）
- All 4 assets traded simultaneously（**SEEN**）

**INFERRED — blue-walnut 嘅 Lottery Ticket + Arb 策略**:

```
Two distinct modes observed:

Mode 1 — Cheap Lottery Tickets:
  Buy at extreme prices: $0.005-$0.05
  Position size: $6,000-$9,000 (huge for such low prices)
  Shares: $6,000 / $0.01 = 600,000 shares
  If wins: 600,000 × $1.00 = $600,000 payout
  If loses: -$6,000

  Break-even P(win) = $6,000 / $600,000 = 1%
  Actual P(win): depends on market

  Example: BTC Feb 6 1PM, avg 0.01, totalBought $9,031, realizedPnl +$3,443
  → Won! Payout = shares × $1.00 = big
  → But only realized +$3,443 of it (partial? fees?)

Mode 2 — Standard Binary Arb:
  Buy both sides at ~0.40-0.60
  MERGE matched pairs
  Same as Cluster A approach

Mode 3 — Directional Conviction:
  Buy at 0.50-0.55 (mid-price)
  Large positions ($10-15K per market)
  If correct: big win; if wrong: big loss
  Example: BTC Feb 24 9PM, avg 0.545, -$2,264 → wrong direction
```

**數學模型 — Portfolio Construction**:

```
blue-walnut treats markets as a portfolio:

Expected value of cheap tickets:
  Buy 100,000 shares at $0.01 each = $1,000 deployed
  P(BTC up in 15m) ≈ 50% (before window starts)
  But market prices at $0.01 = market says 1% chance

  True P(event) vs Market P(event):
    If true P = 3% but market says 1%:
    EV = 0.03 × $100,000 - 0.97 × $1,000
       = $3,000 - $970 = +$2,030 per ticket

  Edge = (True P - Market P) / Market P × 100%
  At extreme prices, even small edge = massive multiplier

Portfolio:
  Many cheap tickets (high expected multiplier, low hit rate)
  + Steady arb income (low return, high certainty)
  = Barbell strategy: most income from arb, occasional windfall from tickets

  $103.9K PnL breakdown (GUESSED):
    ~$60K from binary arb (steady)
    ~$40K from lottery wins (lumpy)
    ~$4K from maker rebates
```

---

## 五、Cluster C 深度分析 — Directional High-Confidence

### 5.1 BoneReader — $881K Monster

**Profile**: $881.3K PnL / $154.7M volume / 43,113 markets / 60 days

**SEEN patterns**:
- Multi-timeframe: 5m, 15m, 4h, hourly（**SEEN**: all four in same session）
- 90 BUY in 138 seconds（**SEEN**: bot）
- 89/90 trades = UP in one session（**SEEN**: overwhelmingly directional）
- Entry at 0.990 (5m) and 0.999 (hourly)（**SEEN**: fixed price levels）
- Zero SELL（**SEEN**: only BUY + MERGE + REDEEM）
- REDEEM $14,237 + BUY $4,571 in same session（**SEEN**: recycle capital）
- Largest win: $29,244（**SEEN**）
- Dust positions from early period（**SEEN**: 0.01-0.99 range, looks experimental）
- PnL trajectory: -$47K → +$881K（**SEEN in prior research**）

**INFERRED — BoneReader 嘅策略演化**:

```
Phase 1 (Jan 2026, first 2-3 weeks): Experimentation
  - Dust positions at various prices → testing different approaches
  - PnL dropped to -$47K → strategies were failing
  - Tried both cheap and expensive entries

Phase 2 (Feb 2026 onwards): Found the edge
  - Switched to high-confidence directional entries
  - Only buys when P(correct) >> implied probability
  - Scale up massively
  - PnL reversed from -$47K to +$881K (+$928K swing!)

Phase 3 (Current): Optimized execution
  - Multi-timeframe layering
  - Capital recycling (REDEEM → BUY in <3 minutes)
  - Fixed position sizes per timeframe
```

**數學模型 — Late-Window Certainty Arbitrage**:

```
BoneReader 買 UP at $0.990 in 5m market:
  Revenue if correct: $1.00
  Cost: $0.99
  Profit per share: $0.01
  Loss per share if wrong: $0.99

  Break-even: P(win) = 0.99 / (0.99 + 0.01) = 99.0%

  For +EV at $0.990:
    P(win) must be > 99.0%

BoneReader 嘅 actual 數字:
  PnL: $881K
  Volume: $154.7M
  Markets: 43,113

  Per market:
    Volume: $3,589
    Profit: $20.44

  如果全部 at $0.990 entry:
    Shares per market: $3,589 / $0.99 = 3,625 shares
    Profit = (P_win × $0.01 - P_lose × $0.99) × 3,625
    $20.44 = (P_win × 0.01 - (1-P_win) × 0.99) × 3,625
    $20.44 = (P_win - 0.99) × 3,625
    P_win = 0.99 + 20.44/3,625
    P_win = 0.99 + 0.00564
    P_win ≈ 99.56%

  所以 BoneReader 嘅 true win rate at $0.990 entries ≈ 99.56%
  Market implies 99.0% → 0.56% edge in probability space

  但呢個係平均。佢唔係全部 at $0.990：
  - Hourly markets at $0.999 → 更高確信度
  - 有啲 5m at lower prices → 更高 edge per trade
```

**💡 BoneReader 嘅核心洞察 — Cross-Timeframe Information Cascade**:

```
BoneReader 同時入 5m + 15m + 4h + hourly，全部同方向。

為咩？因為信息從短 timeframe cascade 到長 timeframe：

1. 5m window 已經 90% resolved（BTC clearly up）
   → UP@0.99 is nearly certain

2. 同一個 BTC move 影響 15m window（which includes the 5m）
   → If BTC up in first 5m, likely UP for 15m too
   → But 15m UP might only be at 0.85（因為仲有 10 min uncertainty）
   → More edge in 15m!

3. Same logic extends to 4h and hourly
   → Even more uncertainty = even more edge if direction is correct

Multi-timeframe 嘅 edge structure:
| Timeframe | Typical entry | Remaining uncertainty | Edge per share |
|-----------|--------------|----------------------|----------------|
| 5m        | $0.990       | Very low              | $0.006         |
| 15m       | $0.85-0.95   | Low-Medium            | $0.01-0.10     |
| 1h        | $0.999       | Very low (near end)   | $0.001         |
| 4h        | $0.80-0.95   | Medium                | $0.02-0.15     |

BoneReader 嘅 REDEEM breakdown 證實：
  5m REDEEM: $9,104 (largest — highest volume)
  4h REDEEM: $2,508 + $1,280 + $435 + $112 = $4,336
  15m REDEEM: $98 (tiny — maybe 15m is secondary)

  5m 係主要收入來源，4h 係 secondary
```

**BoneReader vs LampStore（之前分析嘅）比較**:

| | LampStore | BoneReader |
|--|-----------|------------|
| Strategy | Binary Arb (both sides) | Directional (one side) |
| Entry | $0.485 each side | $0.990 one side |
| WR | 68.6% (both fill) | ~99.56% (direction correct) |
| Edge/market | $5.93 | $20.44 |
| Risk/market | Low (hedged) | HIGH (unhedged) |
| Drawdown | Minimal | -$47K before recovery |
| Sharpe (GUESSED) | High (>3) | Medium (1-2) |
| Capital efficiency | Low (tied up both sides) | HIGH (only one side) |
| Scalability | HIGH | HIGH |
| Daily income | ~$1,542 | ~$14,688 |

---

## 六、跨 Cluster 數學比較

### 6.1 Edge Decomposition

```
Edge 來源拆解:

                    Arb Spread  Maker Rebate  Directional  Active Mgmt  TOTAL
stargate5           ████████░░  ██░░░░░░░░░░  ░░░░░░░░░░  ░░░░░░░░░░  0.57%
mapleghost          ██████░░░░  ██░░░░░░░░░░  ██░░░░░░░░  ░░░░░░░░░░  0.58%
blankandyellow      ████░░░░░░  █░░░░░░░░░░░  ██░░░░░░░░  ░░░░░░░░░░  0.30%
blue-walnut         ████░░░░░░  █░░░░░░░░░░░  ████░░░░░░  ░░░░░░░░░░  0.48%
Brundle             ████░░░░░░  █░░░░░░░░░░░  ██░░░░░░░░  ████░░░░░░  0.91%
BoneReader          ░░░░░░░░░░  ░░░░░░░░░░░░  ██████████  ░░░░░░░░░░  0.57%
```

### 6.2 Risk-Return Profile

```
                   Daily Income  Max Drawdown  Recovery    Risk Type
BoneReader         ~$14,688      -$47K         ~2 weeks    Tail risk (wrong direction)
Brundle            ~$312         Unknown       N/A         Spread risk + directional
blue-walnut        ~$2,078       ~-$5K (est)   Fast        Portfolio (diversified)
stargate5          ~$1,056       Near-zero     N/A         Execution risk only
mapleghost         ~$2,321       Unknown       N/A         Mixed (arb + directional)
blankandyellow     ~$1,313       Near-zero     N/A         Low (mostly arb)
```

### 6.3 Scalability Matrix

```
                   Capital Needed  Throughput   Market Depth Impact  Scalable?
stargate5          $1-2K/window    288/day max  Medium (5m thin)     ⚠️ Medium
mapleghost         $1-2K/window    288+96/day   Medium               ⚠️ Medium
blankandyellow     $1-2K/window    288/day max  Medium (5m thin)     ⚠️ Medium
blue-walnut        $3-5K/window    96/day       Low (multi-asset)    ✅ High
Brundle            $5-10K/market   5-10/day     Low (hourly/daily)   ❌ Low
BoneReader         $3-5K/window    All TF       Low (0.99 = deep)    ✅ High
```

---

## 七、關鍵發現

### 7.1 所有盈利錢包嘅共通點

1. **全部係 bot**（**SEEN**: 每秒多次交易、24/7、fixed patterns）
2. **Maker-dominant**（**INFERRED**: MERGE/REDEEM pattern = maker orders filled then consolidated）
3. **Capital recycling**（**SEEN**: REDEEM → 即刻 reinvest into next window）
4. **Fixed per-trade sizing**（**SEEN**: stargate5=100 shares, blankandyellow=150-300, BoneReader=500-1500）
5. **No manual intervention**（**INFERRED**: timing patterns suggest fully automated）

### 7.2 策略選擇 vs 表現嘅關係

```
Plot: Edge% vs Daily Income

  0.91% |  B(Brundle)
        |
  0.58% |                    M(maple)
  0.57% |  S(star)                        ★ BR(BoneReader)
  0.48% |           BW(blue-walnut)
  0.30% |      BA(blankandy)
        |________________________
        $300  $1K  $2K  $5K  $14K  Daily Income

Observation:
  - High edge% ≠ high income (Brundle: highest edge, lowest income)
  - BoneReader: medium edge but MASSIVE income (scale + speed)
  - Sweet spot: medium edge (0.5-0.6%) + high throughput
```

### 7.3 For Our AXC Strategy

| Learning | Source | Applicability |
|----------|--------|---------------|
| Pure arb is viable at scale | stargate5 | HIGH — matches our approach |
| Multi-asset increases capacity | blue-walnut, mapleghost | MEDIUM — need to verify ETH/SOL/XRP fill rates |
| Active management adds edge but complexity | Brundle | LOW — we should start simple |
| Directional at 0.99 is hugely profitable | BoneReader | HIGH — add as secondary strategy |
| Cross-timeframe cascade is real | BoneReader | HIGH — consider multi-TF approach |
| 5m markets are tradeable | stargate5, blankandyellow | NEW INFO — we only considered 15m |
| Late-window momentum is an edge | blankandyellow | MEDIUM — adds complexity |

---

## 八、Open Questions

1. **stargate5 嘅 MERGE timing**: 係 window 內做定 window 後做？影響 capital efficiency
2. **BoneReader 點知方向？**: Cross-market info? BTC momentum filter? 定係有 insider edge?
3. **blankandyellow 0.30% edge 夠唔夠？**: 一旦 fee 改變就可能翻負
4. **blue-walnut 嘅 lottery tickets 係 systematic 定 opportunistic?**: 需要更多 historical data
5. **Brundle 點解 volume 咁低？**: 係 capacity limited 定係 deliberately selective?
6. **5m vs 15m**: 邊個更適合新手 bot？5m = 更多機會但更薄 liquidity
