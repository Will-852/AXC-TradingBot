# Polymarket 錢包逆向工程 — 最終綜合報告
> Date: 2026-03-21
> Wallets analyzed: 14
> Total PnL across wallets: $2,221,452
> Total Volume: $319.4M

---

## 一、14 Wallets Master Table

| # | Nickname | PnL | Volume | Markets | Edge% | $/Mkt | $/Day | Strategy | Grade |
|---|----------|-----|--------|---------|-------|-------|-------|----------|-------|
| 1 | **BoneReader** | **$881.3K** | $154.7M | 43,113 | 0.57% | $20.44 | **$14,688** | Directional 0.99 multi-TF | **S** |
| 2 | Brundle | $131.2K | $14.5M | 2,300 | **0.91%** | **$57.04** | $312 | Active MM hourly+range | A |
| 3 | stargate5 | $126.7K | $22.4M | 13,238 | 0.57% | $9.57 | $1,056 | Pure arb 5m BTC | A |
| 4 | Ugly-Knock | $116.3K | $27.2M | 11,534 | 0.43% | $10.08 | $373 | ★ Cross-market 0.999 | A |
| 5 | mapleghost | $116.1K | $19.9M | 19,080 | 0.58% | $6.08 | $2,321 | Arb 15m/5m | A |
| 6 | kafwhsd | $108.2K | $27.5M | 10,502 | 0.39% | $10.30 | $361 | Pure arb 15m | A |
| 7 | likebot | $107.7K | $25.5M | 27,038 | 0.42% | $3.98 | $829 | Both-sides 5m (5-lot) | A |
| 8 | blue-walnut | $103.9K | $21.7M | 4,649 | 0.48% | $22.35 | $2,078 | Multi-asset + lottery | A |
| 9 | xr9-PLM42 | $101.1K | $28.5M | 22,071 | 0.36% | $4.58 | $3,888 | 5m multi-asset + mgmt | A |
| 10 | Anon-0x8e9c | $100.1K | $14.5M | 7,282 | **0.69%** | $13.74 | $834 | Pure arb 5m BTC | A |
| 11 | VOID-PEPPER | $99.0K | $11.1M | 22,965 | **0.89%** | $4.31 | $1,980 | Multi-asset 5m+15m | A |
| 12 | purple-lamp-tree | $95.5K | $14.2M | 27,294 | 0.67% | $3.50 | $2,894 | Multi-TF both-sides | A |
| 13 | MangoTrolley7 | $88.6K | $12.8M | 6,710 | 0.69% | $13.20 | $1,772 | Lottery + arb hybrid | B |
| 14 | blankandyellow | $45.9K | $15.3M | 12,646 | 0.30% | $3.63 | $1,313 | Arb + momentum 5m | B |

---

## 二、Strategy Taxonomy（最終分類）

### 5 大策略類型

```
                        14 Wallets Strategy Distribution

  Binary Arb (A)     ████████████████████████░░░░░░░░  9 wallets (64%)
  Directional (C)    ████████░░░░░░░░░░░░░░░░░░░░░░░░  1 wallet  (7%)
  Cross-Mkt Cert (C2)████████░░░░░░░░░░░░░░░░░░░░░░░░  1 wallet  (7%)
  Active MM (B)      ████████░░░░░░░░░░░░░░░░░░░░░░░░  1 wallet  (7%)
  Lottery Hybrid (D) ████████████████░░░░░░░░░░░░░░░░  2 wallets (14%)
```

### A. Binary Arb（9 wallets — 64%）

**共同特徵**:
- 兩邊買 UP + DOWN
- MERGE matched pairs for $1.00 guaranteed
- Maker orders dominant
- Zero/minimal SELL activity
- Automated 24/7 operation

| Sub-type | Wallets | Distinguishing Feature |
|----------|---------|----------------------|
| **A1: Pure Arb** | stargate5, kafwhsd, Anon-0x8e9c | Minimal management, hold to expiry |
| **A2: Multi-Asset** | VOID-PEPPER, xr9-PLM42 | 4 assets (BTC/ETH/SOL/XRP) |
| **A3: Arb + Direction** | mapleghost, likebot, purple-lamp-tree, blankandyellow | Arb base + directional overlay |

**Edge 範圍**: 0.30% - 0.89%
**Per-market profit**: $3.50 - $13.74
**Key formula**:
```
Profit = Σ(windows) × [P(both_fill) × (1 - combined) + P(one_fill) × directional_EV] + maker_rebate
```

### B. Active MM（1 wallet — 7%）

**Brundle**: 唯一有 SELL activity 嘅錢包
- Hourly + daily range markets
- Buy → Monitor → Sell/Rebalance → MERGE
- Highest edge% (0.91%) but lowest throughput (5.5/day)
- 最複雜但最唔 scalable

### C. Directional High-Confidence（1 wallet — 7%）

**BoneReader**: 14 個錢包中嘅冠軍
- $881K PnL = 其他 13 個加埋嘅 66%
- Multi-timeframe cascade (5m → 15m → 4h → 1h)
- Entry at 0.990-0.999 on winning side only
- Win rate ≈ 99.56%
- Capital recycling: REDEEM → immediate BUY

### C2. Cross-Market Certainty（1 wallet — 7%）

**Ugly-Knock**: 最創新嘅策略
- 唯一一個做 sports + crypto + weather
- ALL entries at 0.999
- Exploit: near-settled markets across ALL categories
- BoneReader variant but applied globally

### D. Lottery + Arb Hybrid（2 wallets — 14%）

**MangoTrolley7 + blue-walnut**:
- Buy extreme cheap (0.005-0.02) lottery tickets
- Fat-tail mispricing in 5m binary markets
- Plus steady arb income from normal-priced trades
- Highest single-trade wins ($13K, $3.4K)
- Barbell: most income from arb, occasional windfall

---

## 三、數學模型比較

### Edge% vs Scalability Matrix

```
High Edge                          ★ Sweet Spot
  0.91% │  Brundle
        │                     VOID-PEPPER
  0.69% │  Anon-0x8e9c   MangoTrolley7
  0.67% │                     purple-lamp-tree
  0.58% │  mapleghost
  0.57% │  stargate5                          BoneReader ★★★
  0.48% │        blue-walnut
  0.43% │                         Ugly-Knock
  0.42% │        likebot
  0.39% │                    kafwhsd
  0.36% │                              xr9-PLM42
  0.30% │  blankandyellow
        └────────────────────────────────────────────
         $300   $1K    $2K   $5K   $10K   $15K
                      Daily Income →
```

### 三條賺錢路線

```
Route 1: HIGH EDGE × LOW VOLUME = Brundle model
  Edge: 0.91% | $57/market | 5.5 markets/day = $312/day
  Pros: Sustainable, less competition
  Cons: Hard to scale, needs active management
  Capital: ~$10K per market

Route 2: MEDIUM EDGE × HIGH VOLUME = Cluster A model
  Edge: 0.45% avg | $7/market | 200+ markets/day = $1,400/day
  Pros: Scalable, automatable, lower risk per trade
  Cons: Competition erodes edge, fee changes kill it
  Capital: ~$1K per market

Route 3: MEDIUM EDGE × EXTREME VOLUME = BoneReader model
  Edge: 0.57% | $20/market | 700+ markets/day = $14,688/day
  Pros: Highest income, leverages information cascade
  Cons: Unhedged directional risk, needs perfect execution
  Capital: ~$3.5K per market
```

### Win Rate vs Edge Size Trade-off

```
Strategy        | WR     | Edge/Win | Edge/Loss | Net Edge | Risk
Pure Arb        | ~70%   | +$0.03   | -$0.50    | +$7/mkt  | Low
Arb+Direction   | ~65%   | +$0.05   | -$0.50    | +$5/mkt  | Low-Med
Directional 0.99| ~99.5% | +$0.01   | -$0.99    | +$20/mkt | Med-High
Lottery Ticket  | ~3%    | +$50     | -$2.50    | +$13/mkt | High
Active MM       | ~75%   | +$0.10   | -$0.30    | +$57/mkt | Medium
Cross-Mkt Cert  | ~99.9% | +$0.001  | -$0.999   | +$10/mkt | Med-High
```

---

## 四、Key Findings

### Finding 1: Binary Arb 係最常見嘅盈利策略
9/14 wallets (64%) 用某種形式嘅 binary arb。呢個係 Polymarket 最基本嘅 edge — 利用 UP + DOWN 唔 sync 嘅 microstructure gap。

### Finding 2: 5m Markets > 15m Markets
- 9/14 wallets trade 5m markets（部分兼做 15m）
- 只有 2 wallets (kafwhsd, Brundle) 唔做 5m
- 5m = 更多 windows/day = 更多 opportunities
- 但 5m liquidity 更薄 → fill rate 可能更低

### Finding 3: BoneReader 獨佔 40% 嘅總 PnL
- BoneReader: $881K = 39.7% of total $2.22M
- 其餘 13 wallets 分享 60.3%
- 原因：directional strategy + massive scale + multi-timeframe

### Finding 4: Edge% 同 Income 無直接關係
- Brundle highest edge (0.91%) but lowest income ($312/day)
- xr9-PLM42 lowest edge (0.36%) but 3rd highest income ($3,888/day)
- **Income = Edge% × Volume**，volume 嘅差異遠大於 edge%

### Finding 5: Ugly-Knock 發現咗新策略空間
- 唯一做 sports/esports/weather + crypto 嘅錢包
- Cross-market 0.999 certainty arb = 全新嘅 edge source
- TAM（total addressable market）最大 — Polymarket 有 thousands of markets daily

### Finding 6: All 14 wallets 係 bots
- 零例外。每秒多次交易、24/7 operation、fixed patterns
- Manual trading 喺 Polymarket prediction markets 無法 compete

### Finding 7: Maker > Taker
- 12/14 wallets are maker-dominant
- Taker fee at 50¢ = ~3.15% → 直接蠶食 edge
- Maker rebate = 25% of taker fees → free additional income
- 只有 purple-lamp-tree 同 BoneReader 可能有較高 taker ratio

---

## 五、For Our AXC Strategy — Actionable Insights

### Immediate Actions

| # | Action | Source | Priority | Effort |
|---|--------|--------|----------|--------|
| 1 | **考慮 5m markets** | 9/14 wallets do 5m | HIGH | Medium |
| 2 | **Add MERGE operation** | stargate5, mapleghost etc | HIGH | Low |
| 3 | **Capital recycling** | BoneReader, all arb bots | HIGH | Low |
| 4 | **Multi-asset (ETH/SOL/XRP)** | VOID-PEPPER, xr9-PLM42 | MEDIUM | Medium |
| 5 | **Late-window directional overlay** | blankandyellow, BoneReader | MEDIUM | High |

### Strategy Direction Decision

```
Option A: Pure Arb Bot (像 stargate5)
  + 最安全，near-zero risk
  + 已驗證（9 wallets 做緊）
  - Edge 正在被 compress（太多 competitors）
  - 需要高 throughput 先有意義
  Expected: $500-$1,500/day

Option B: Informed MM (像 Brundle + AXC indicators)
  + 最高 per-market edge（0.91%）
  + AXC 嘅 8 indicators 係 unique advantage
  - 較複雜，需要 active management
  - Throughput limited
  Expected: $300-$800/day

Option C: Directional + Arb Hybrid (像 BoneReader)
  + 最高 income potential
  + Multi-timeframe cascade 利用 AXC pipeline
  - 較高 risk（unhedged）
  - 需要極快 execution
  Expected: $2,000-$10,000/day (if successful)

Recommendation: Start with Option A (pure arb) to validate
  infrastructure, then evolve toward Option C as confidence grows.
```

### Risk Warnings

| Risk | Wallets Affected | Mitigation |
|------|-----------------|------------|
| Fee structure change | All (especially low-edge: blankandyellow, xr9) | Monitor Polymarket announcements |
| 5m market removal | 9/14 wallets | Diversify to 15m/1h |
| Latency arms race | All maker strategies | Optimize WebSocket handling |
| Edge compression | All arb strategies | Differentiate with directional alpha |
| Black swan (BTC flash crash) | BoneReader, directional | Position limits, stop loss |

---

## 六、Open Research Questions

1. **Ugly-Knock 嘅 sports prediction model**: 點樣判斷 game 已經 settled？External feeds?
2. **BoneReader 嘅 direction prediction**: Pure momentum? Or deeper model?
3. **5m vs 15m optimal split**: 幾多 5m 幾多 15m 先至最大化 Sharpe?
4. **MERGE timing optimization**: Window 內做定 window 後做？
5. **Cross-asset correlation**: BTC move 幾快 propagate 到 ETH/SOL/XRP price?
6. **Maker rebate sizing**: 佔 total edge 幾多 %？值唔值得 quote more?

---

## 附錄：File Index

```
wallet_analysis/
├── ANALYSIS_FRAMEWORK.md          ← 方法論 + sub-agent prompts
├── CLASSIFICATION_INDEX.md        ← 分類索引（需更新）
├── deep_analysis_batch1.md        ← Wallets 1-6 深度分析
├── deep_analysis_batch2.md        ← Wallets 7-14 深度分析
├── summary_report.md              ← 本文件
├── raw/                           ← (data in agent memory)
├── profiles/                      ← (embedded in batch files)
└── clusters/                      ← (embedded in batch files)
```
