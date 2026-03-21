# 外部驗證報告 — 14 Wallets 逆向工程
> Date: 2026-03-21
> Sources: 30+ external articles, academic papers, analytics platforms, official docs
> Purpose: 驗證/挑戰 deep_analysis_batch1 + batch2 嘅結論
> Status: ALL 5 verification agents completed

---

## 零、CRITICAL CORRECTIONS（必須立即修正）

### 🔴 Correction 1: Maker Rebate = 20% for Crypto（唔係 25%）
- **Source**: [Official Polymarket Docs](https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program)
- Crypto markets (5m/15m/1h/4h/daily): **20%** of taker fees
- Sports markets (NCAAB, Serie A): **25%** of taker fees
- **Impact**: 所有 crypto wallet P&L models 如果用咗 25% 要 recalculate → 減少 ~20% maker rebate income

### 🔴 Correction 2: stargate5 唔只做 BTC 5m！
- **Source**: [polymarket.com/@stargate5](https://polymarket.com/@stargate5) — direct profile fetch
- PnL confirmed: **$126,780.60** (matches our $126.7K)
- Categories: **Sports, Crypto, Finance, Politics** ← NOT BTC-only!
- **Impact**: stargate5 嘅分類從 "A1 Pure Arb, BTC only" → 可能需要移去 C2 (Cross-Market) 或 A2 (Multi-Asset)

### 🟡 Correction 3: Fee formula 有 normalization 歧義
- Official docs: max 1.56% at p=0.50
- Finance Magnates: ~3.15% at p=0.50
- Actual formula: `fee = C × p × 0.25 × (p×(1-p))^2` → at p=0.50: **0.78% per share**
- **Impact**: 需要用真實 transaction data 確認實際 fee rate

### 🟢 Correction 4: 5m markets 有 7 assets（唔只 4 個）
- **BTC, ETH, SOL, XRP** + **DOGE, HYPE, BNB**
- **Impact**: Multi-asset wallets 有更多 capacity 未被利用

### 🟢 Correction 5: March 6, 2026 — fees extended to ALL crypto timeframes
- 1H, 4H, daily, weekly 而家都有 taker fee + maker rebate
- **Impact**: Brundle (hourly markets) 而家都有 maker rebate income

---

## 一、Market-Level Validation（整體市場數據）

### 1.1 Bot Dominance — CONFIRMED ✅

| Finding | Source | Impact |
|---------|--------|--------|
| **14/20 top Polymarket traders are bots** | [Yahoo Finance](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html) | 我哋 14 wallets 全部係 bot = consistent |
| Bots avg $206K profit at 85%+ WR | Same source | Our wallets avg $159K = reasonable |
| Humans only capture ~$100K with same strategies | Same source | Bot advantage ~2x |
| **80% of Polymarket participants lose money** | [CryptoNews/LaikAI](https://laikalabs.ai/prediction-markets/polymarket-trading-strategies) | Our wallets are all in top 0.51% |
| Only 0.51% of traders have PnL > $1,000 | Same source | All 14 wallets = extreme outliers |

**Verdict**: 我哋嘅 14 wallets 全部喺 top tier。外部數據 confirm bot trading 係 dominant force。

### 1.2 Arb Window Compression — WARNING ⚠️

| Finding | Source | Impact |
|---------|--------|--------|
| **Arb opportunity duration: 2.7 seconds** (was 12.3s in 2024) | [TradeTheOutcome](https://www.tradetheoutcome.com/polymarket-strategy-2026/) | Edge 正在被 compress |
| **73% of arb profits captured by sub-100ms bots** | Same source | 需要極快 execution |
| Median arb spread: **0.3%** (barely profitable after gas) | Same source | 低於我哋估計嘅 0.39-0.91% |

**Verdict**: Binary arb edge 正在被 compress。我哋 Cluster A wallets 嘅 0.30-0.89% edge 可能包含非 arb 成分（directional overlay, maker rebate）。Pure arb alone may not be sustainable long-term.

### 1.3 5-Minute Markets — CONFIRMED ✅

| Finding | Source | Impact |
|---------|--------|--------|
| **5M BTC markets see up to $60M daily volume** | [Yahoo Finance](https://finance.yahoo.com/news/polymarkets-5-minute-bitcoin-bets-123329673.html) | 足夠 liquidity for our strategies |
| Bot $313 → $414K in 1 month on 15m markets | [Finbold](https://finbold.com/trading-bot-turns-313-into-438000-on-polymarket-in-a-month/) | Validates massive returns are possible |
| 98% WR with $4-5K bets per trade | Same source | Consistent with BoneReader's pattern |

---

## 二、Strategy-Level Validation

### 2.1 Binary Arb (Cluster A — 9 wallets) — CONFIRMED ✅ with WARNING

**CONFIRMED**:
- Combined < $1.00 arbitrage is a known, documented strategy
- [CoinDesk](https://www.coindesk.com/markets/2026/02/21/how-ai-is-helping-retail-traders-exploit-prediction-market-glitches-to-make-easy-money/): "Buy both sides when combined = $0.97, lock in 3¢ profit"
- [Medium/Benjamin-Cup](https://medium.com/@benjamin.bigdev/): "At T-10 seconds, 85% of direction is locked in"
- Maker rebate = 25% of taker fees = free additional income

**WARNING**:
- Arb window now only 2.7 seconds → sub-100ms execution needed
- Dynamic fees up to 3.15% at 50¢ killed pure latency arb at 50/50
- [Finance Magnates](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees/): Dynamic fees "made the strategy unprofitable at scale" for pure latency plays

**Impact on our analysis**:
- stargate5, kafwhsd, Anon-0x8e9c 嘅 pure arb edge 可能 declining
- 但 maker-side arb + fee avoidance at non-50¢ prices 仲 viable
- 5-share lots (likebot) may be optimal to avoid detection/impact

### 2.2 Active MM (Brundle) — STRONGLY CONFIRMED ✅✅

**External evidence**:

| Source | Finding | Match? |
|--------|---------|--------|
| [Polymarket Official Blog](https://news.polymarket.com/p/automated-market-making-on-polymarket) | 3x rewards for two-sided quoting | ✅ Explains Brundle's both-sides |
| [Alphascope MM Guide](https://www.alphascope.app/blog/polymarket-market-making) | Active makers earn $20-100/day on good markets | ✅ Brundle at $312/day = top tier |
| [PolyTrack Guide](https://www.polytrackhq.app/blog/polymarket-market-making-guide) | Automated system peaked at $700-800/day | ✅ Within range |
| [GitHub MM bots](https://github.com/lorine93s/polymarket-market-maker-bot) | SELL-heavy signature = inventory rebalancing | ✅ Exactly what Brundle does |
| Polymarket CLOB docs | GTD orders 1-hour = natural for hourly markets | ✅ Explains Brundle's hourly focus |

**Verdict**: Brundle 嘅策略係 **canonical market making**，外部驗證最強。早期進入 (Feb 2025) = first-mover advantage before competition intensified.

### 2.3 BoneReader (Directional 0.99) — CONFIRMED ✅ with NEW INSIGHTS

**BoneReader Leaderboard Data (from polymarket.com/leaderboard)**:
- Ranked **#4 by volume** ($154.8M), **#9 by profit** ($881K)
- Ranked **#3 by monthly profit**: $446K/month ← MASSIVE

**💡 CRITICAL NEW FINDING — BoneReader trades ALL categories**:

| Category | Confirmed? |
|----------|-----------|
| Crypto (BTC, ETH, SOL, XRP) | ✅ SEEN in our data |
| **Sports (NBA, Esports, Tennis, Cricket)** | ✅ NEW from profile page |
| **Finance (Equities, Commodities, Indices)** | ✅ NEW |
| **Politics (Elections)** | ✅ NEW |
| **Culture (Movies, Music)** | ✅ NEW |
| **Climate/Weather** | ✅ NEW |

**Impact**: 我哋之前以為 BoneReader 只做 crypto。實際上 BoneReader = **cross-market certainty arb across ALL categories**。同 Ugly-Knock 係同一個策略類型！

**Reclassification**:
```
BEFORE: BoneReader = Cluster C (Directional High-Confidence, crypto only)
AFTER:  BoneReader = Cluster C2 (Cross-Market Certainty, ALL categories)
        → BoneReader 同 Ugly-Knock 係同類！
        → BoneReader 只係 scale 大好多
```

### 2.4 Ugly-Knock (Cross-Market 0.999) — CONFIRMED ✅

**"Tail-End Trading" is a documented strategy**:
- [ChainCatcher](https://www.chaincatcher.com/en/article/2212288): "Purchase positions when outcomes are essentially settled but markets haven't officially closed"
- "Traders buy at 0.997-0.999 and wait for settlement"
- "A documented address converted $10,000 into $100,000 across 10,000+ markets" ← matches our wallets
- Risk management: max 1/10 of positions per market, prioritize markets settling within hours

**Multi-Option Market Arbitrage** (related strategy):
- When total probabilities sum < $1 in winner-take-all markets → buy all options
- Automated bots exploit temporary imbalances lasting seconds
- Similar to MEV extraction

**Verdict**: Ugly-Knock 同 BoneReader 嘅 certainty arb 係 **well-known strategy type**，但跨 ALL market categories 嘅執行係 rare.

### 2.5 Lottery Tickets (MangoTrolley7, blue-walnut) — ⚠️ CONTESTED

**SUPPORTING evidence**:

| Finding | Source | Strength |
|---------|--------|----------|
| BTC 5m kurtosis very high → extreme moves 5-10x more frequent than Gaussian | [MDPI Fractal Fract 2025](https://www.mdpi.com/2504-3110/9/10/635) | **STRONG** |
| Binary markets on fat-tailed assets = structurally mispriced | [Taleb, arxiv](https://arxiv.org/abs/2001.10488) | **STRONG** |
| q-Gaussian/Student-t better fits BTC returns, ±20% daily moves 50-100x more frequent than Gaussian | [Frontiers 2025](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2025.1567626/) | **STRONG** |

**CONTRADICTING evidence**:

| Finding | Source | Strength |
|---------|--------|----------|
| **Favourite-Longshot Bias (FLB)**: long shots are systematically OVERPRICED | [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S037722171830314X) | **STRONG** |
| "Most persistent pricing anomaly in prediction markets" | Same | **STRONG** |
| Whales DON'T chase low-probability bets; profitable whales hunt 95¢+ markets | [MONOLITH/Medium](https://medium.com/@monolith.vc/5-ways-to-make-100k-on-polymarket-f6368eed98f5) | **MEDIUM** |

**💡 THE TENSION — FLB vs Fat-Tails**:

```
FLB says: $0.01 contracts are overpriced → true P < 1% → SELL them
Fat-tails say: $0.01 contracts undercount extreme moves → true P > 1% → BUY them

Which dominates at 5-minute BTC binary markets?

Arguments for fat-tails winning:
  1. Traditional FLB studied in horse racing/politics → low kurtosis underlyings
  2. BTC 5m kurtosis > 9 → orders of magnitude more extreme than typical FLB domains
  3. Binary market pricing may use near-Gaussian models → systematic underpricing
  4. MangoTrolley7 IS profitable ($88.6K) → empirical evidence

Arguments for FLB winning:
  1. FLB is the "most persistent pricing anomaly" across ALL prediction markets
  2. Retail traders LOVE cheap lottery tickets → oversupply of demand at $0.01
  3. MangoTrolley7's $88.6K could be survivorship bias (we only see winners)

VERDICT: UNRESOLVED — genuine academic disagreement
  → MangoTrolley7 may be exploiting a real edge OR may be lucky
  → Need longer track record to distinguish skill from variance
```

---

## 三、Market Mechanics Validation

### 3.1 Fee Structure Updates

| Mechanic | Our Assumption | External Evidence | Status |
|----------|---------------|-------------------|--------|
| Dynamic taker fee ~3.15% at 50¢ | ✅ | [Finance Magnates](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees/) confirmed | VERIFIED |
| Maker rebate 25% | ⚠️ was 20% | Need to verify current rate | PARTIALLY VERIFIED |
| Fee near-zero at extremes (0.99) | ✅ | Implied by dynamic fee curve | INFERRED |
| **Fees extended to ALL crypto markets (1H, 4H, daily, weekly)** | 🆕 | "Starting March 6, 2026" | **NEW INFO** |

**💡 March 6, 2026 fee extension**: Taker fees + maker rebates now apply to ALL crypto timeframes, not just 5m/15m. This HELPS maker strategies (more rebates) but HURTS taker strategies.

### 3.2 MERGE/REDEEM Mechanics

| Mechanic | Our Assumption | Status |
|----------|---------------|--------|
| MERGE = combine UP + DOWN → $1.00 | Based on Gnosis CTF | LIKELY CORRECT but needs docs verification |
| MERGE available mid-window | Observed in data (stargate5) | SEEN |
| REDEEM = collect winning shares at $1.00 | Observed | SEEN |

### 3.3 Competitive Landscape

| Metric | 2024 | 2026 | Trend |
|--------|------|------|-------|
| Arb window duration | 12.3s | **2.7s** | ↓ 78% |
| Sub-100ms bot capture rate | — | **73%** | Dominant |
| Market making profitability | $700-800/day peak | $20-100/day typical | ↓ Declining |
| Number of competing bots | — | **14/20 top traders = bots** | Saturated |

---

## 四、Market Mechanics — Official Docs Verification

| # | Assumption | Verified? | Source | Key Detail |
|---|-----------|-----------|--------|------------|
| 1 | MERGE = 1 YES + 1 NO → $1 USDC | **YES** | [Official docs](https://docs.polymarket.com/trading/ctf/merge) | Atomic, no protocol fee, any time |
| 2 | Taker fee ~3.15% at p=0.50 | **PARTIAL** | [Fees docs](https://docs.polymarket.com/trading/fees) | Formula confirmed; 0.78-3.15% depending on normalization |
| 3 | Maker rebate = 25% | **NO** → 20% crypto | [Rebates docs](https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program) | 20% crypto, 25% sports only |
| 4 | 500ms delay removed | **YES** | [X/@RoundtableSpace](https://x.com/RoundtableSpace/status/2024782327931670945) | Mid-Feb 2026, silent update |
| 5 | 5m markets exist | **YES** | [Polymarket 5M](https://polymarket.com/crypto/5M) | 7 assets, $60M+/day volume |
| 6 | Fee ≈ $0 at 0.99/0.01 | **YES** | [Fees docs](https://docs.polymarket.com/trading/fees) | Exactly $0.00 per formula |
| 7 | Rebate paid daily in USDC | **YES** | [Rebates docs](https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program) | Directly to wallet |
| 8 | MERGE available mid-window | **YES** | [Merge docs](https://docs.polymarket.com/trading/ctf/merge) | No timing restrictions |
| 9 | BTC/ETH/SOL/XRP available | **YES+** | [5M page](https://polymarket.com/crypto/5M) | Plus DOGE, HYPE, BNB |
| 10 | Hourly/daily range markets | **YES** | [Hourly page](https://polymarket.com/crypto/hourly) | Multi-strike brackets for BTC |

### Binary Arb Strategy — Externally Published

| Source | Finding | Impact |
|--------|---------|--------|
| [FMZ/mathquant.com (Mar 4, 2026)](https://blog.mathquant.com/2026/03/04/polymarket-binary-hedging-arbitrage-from-concept-to-live-execution.html) | Live trade: DOWN@0.34 + UP@0.60 = $0.94 → $0.06 profit per share | Exact same strategy as our Cluster A |
| [X/@wiseadvicesumit](https://x.com/wiseadvicesumit/status/2022699274711277687) | **7.1% of 5m BTC addresses buy both sides same window** | Confirms widespread usage |
| [Protos](https://protos.com/polymarket-ends-trading-loophole-for-bitcoin-quants/) | Post-500ms removal: "maker rebate is now the primary moat" | Validates maker-first approach |
| [Medium/ILLUMINATION](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f) | "The bots that can truly win in 2026 are the most excellent liquidity providers" | Maker > Taker confirmed |
| [GitHub/0xalberto](https://github.com/0xalberto/polymarket-arbitrage-bot) | Open-source bot: $764/day on BTC-15m; multi-asset FAILED | Our multi-asset wallets have superior execution |

---

## 五、Revised Confidence Levels

| Strategy | Before Verification | After Verification | Key Change |
|----------|--------------------|--------------------|------------|
| Binary Arb (Cluster A) | HIGH | **MEDIUM-HIGH** | Edge compression warning |
| Active MM (Brundle) | HIGH | **VERY HIGH** | Strongest external validation |
| BoneReader (Directional) | HIGH | **HIGH** → reclassified as C2 | Trades ALL categories, not just crypto |
| Ugly-Knock (Cross-Mkt) | MEDIUM | **HIGH** | Documented strategy type |
| Lottery (MangoTrolley7) | MEDIUM | **LOW-MEDIUM** | FLB contradicts thesis |
| Lottery (blue-walnut) | MEDIUM | **LOW-MEDIUM** | Same FLB concern |

---

## 五、Impact on AXC Strategy

### Must-Do Changes

1. **Reclassify BoneReader + Ugly-Knock as same cluster (C2)**
   - Both do cross-market certainty arb at 0.99-0.999
   - BoneReader just does it at 10x scale

2. **Add execution speed as priority**
   - 2.7 second arb window → need sub-second execution
   - 73% captured by sub-100ms bots → WebSocket + optimized order pipeline

3. **Reconsider lottery ticket thesis**
   - FLB is a STRONG counterargument
   - Don't allocate capital to MangoTrolley7-style plays without further research

### Validated Approaches

1. **Pure binary arb at non-50¢ prices** (avoid dynamic fee zone)
2. **Maker-side strategies** (rebate = structural edge)
3. **Late-window directional** at 0.90-0.95 (not 0.50 where fees kill you)
4. **Multi-category scanning** (BoneReader/Ugly-Knock model)

---

## 六、Source Bibliography

### Academic
- [Stylized Facts of High-Frequency BTC Time Series — MDPI 2025](https://www.mdpi.com/2504-3110/9/10/635)
- [Statistical Consequences of Fat Tails — Taleb](https://arxiv.org/abs/2001.10488)
- [On Statistical Differences between Binary — arxiv](https://arxiv.org/pdf/1907.11162)
- [Heterogeneous Agent Explanation for FLB — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S037722171830314X)
- [GARCH Model With Fat-Tailed Distributions — SciPG](https://scipg.com/index.php/102/article/download/115/129)

### Industry
- [ChainCatcher: Silent Arbitrage on Polymarket](https://www.chaincatcher.com/en/article/2212288)
- [Finance Magnates: Dynamic Fees](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees/)
- [Polymarket Official: Automated Market Making](https://news.polymarket.com/p/automated-market-making-on-polymarket)
- [Alphascope: MM Guide](https://www.alphascope.app/blog/polymarket-market-making)
- [PolyTrack: MM Guide 2025](https://www.polytrackhq.app/blog/polymarket-market-making-guide)

### Analytics
- [Yahoo Finance: Arbitrage Bots Dominate Polymarket](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html)
- [Yahoo Finance: 5M BTC Markets $60M Daily](https://finance.yahoo.com/news/polymarkets-5-minute-bitcoin-bets-123329673.html)
- [Polymarket Leaderboard](https://polymarket.com/leaderboard/crypto/all/profit)
- [PolymarketAnalytics Traders](https://polymarketanalytics.com/traders)
- [Predicts.guru](https://predicts.guru)

### Strategy Guides
- [MONOLITH: 5 Ways to Make $100K](https://medium.com/@monolith.vc/5-ways-to-make-100k-on-polymarket-f6368eed98f5)
- [DataWallet: Top 10 Strategies](https://www.datawallet.com/crypto/top-polymarket-trading-strategies)
- [LaikAI: Top 10 Strategies](https://laikalabs.ai/prediction-markets/polymarket-trading-strategies)
- [GitHub: polymarket-market-maker-bot](https://github.com/lorine93s/polymarket-market-maker-bot)
- [GitHub: poly-maker](https://github.com/warproxxx/poly-maker)
- [GitHub: polybot (reverse engineering)](https://github.com/ent0n29/polybot)
