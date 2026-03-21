# Polymarket 錢包逆向工程 — 分析框架
> Created: 2026-03-21
> Purpose: Scalable wallet reverse engineering system using sub-agents

---

## 1. 資料夾結構

```
wallet_analysis/
├── ANALYSIS_FRAMEWORK.md          ← 本文件（方法論 + prompts）
├── CLASSIFICATION_INDEX.md        ← 分類索引（所有錢包歸類）
├── raw/                           ← 原始數據（JSON trades）
│   ├── {address}_trades.json
│   └── {address}_summary.json
├── profiles/                      ← 每個錢包嘅獨立分析報告
│   ├── {nickname}_{address_short}.md
│   └── ...
├── clusters/                      ← 同類錢包歸納報告
│   ├── cluster_binary_arb.md
│   ├── cluster_directional.md
│   ├── cluster_mm.md
│   └── ...
└── summary_report.md              ← 最終綜合報告
```

---

## 2. 分析維度（每個錢包必須覆蓋）

### A. 基礎數據
| Field | Source | Notes |
|-------|--------|-------|
| Address | Input | Full 0x address |
| Nickname | Polymarket profile | If available |
| PnL (total) | Profile / Dune | Lifetime profit/loss |
| Volume (total) | Profile / Dune | Total traded volume |
| Markets count | Profile / Dune | Total markets participated |
| Join date | Profile | First trade timestamp |
| Active days | Calculated | Days with ≥1 trade |

### B. 策略行為分析
| Metric | How to Calculate | Why It Matters |
|--------|-----------------|----------------|
| **Maker/Taker ratio** | Count maker vs taker fills | >90% maker = MM strategy |
| **Both-side fill rate** | % of markets where both UP+DOWN filled | High = arb/MM, Low = directional |
| **Average combined price** | Mean(UP_price + DOWN_price) per market | <1.00 = arb edge, ≥1.00 = loss |
| **Entry timing** | Seconds after market open | Early = informed/fast, Late = reactive |
| **Position size distribution** | Histogram of trade sizes | Fixed = bot, Variable = manual/adaptive |
| **Win rate (WR)** | % of markets with profit | Key performance metric |
| **Average profit per market** | Mean PnL per market | Absolute edge per trade |
| **Market type distribution** | BTC/ETH/XRP/Events | Specialist vs generalist |
| **Time-of-day pattern** | Trade distribution across hours | Bot = 24/7, Human = cluster |
| **Sequential vs simultaneous** | Time gap between UP and DOWN orders | Sequential = checking fill, Simultaneous = blind arb |
| **Order management** | Cancel rate, modify rate, unwind trades | Active MM vs set-and-forget |
| **Drawdown pattern** | Max drawdown, recovery time | Risk tolerance indicator |

### C. 進階分析（如數據充足）
| Metric | Description |
|--------|-------------|
| **Price sensitivity** | How combined price changes with market volatility |
| **Volume acceleration** | Does sizing increase after wins? |
| **Multi-asset correlation** | Same strategy across BTC/ETH/XRP? |
| **Time decay response** | Does entry timing shift with remaining time? |
| **Loss response** | Behaviour change after losing streak |
| **Fee optimization** | Maker rebate vs taker fee pattern |

---

## 3. 分類系統

### Tier 1: 策略類型
| Category | Signature | Example |
|----------|-----------|---------|
| **Binary Arb (BA)** | Both-fill >80%, combined <$1.00, maker >90%, zero management | LampStore, Anon |
| **Informed MM (IMM)** | Both-fill >60%, asymmetric sizing, signal-based cancel | swisstony (suspected) |
| **Directional (DIR)** | Single-side >70%, conviction-based, variable sizing | j2f2 |
| **Hybrid (HYB)** | Mix of arb + directional depending on confidence | BoneReader (suspected) |
| **Scalper (SCA)** | High frequency, tight spread, small size, high cancel rate | TBD |
| **Event Trader (EVT)** | Primarily non-crypto markets, news-driven | TBD |

### Tier 2: 表現等級
| Grade | Criteria |
|-------|----------|
| **S** | PnL >$500K, WR >75%, Sharpe >3 |
| **A** | PnL >$50K, WR >65%, consistent growth |
| **B** | PnL >$5K, WR >55%, some drawdowns |
| **C** | PnL breakeven to +$5K, learning curve visible |
| **F** | PnL negative, strategy not working |

### Tier 3: 操作特徵
| Tag | Description |
|-----|-------------|
| `bot` | 24/7 operation, fixed patterns |
| `semi-auto` | Bot-assisted but human oversight |
| `manual` | Irregular timing, variable patterns |
| `multi-asset` | Trades >1 asset type |
| `specialist` | Only 1 asset type |
| `high-vol` | >100 markets/day |
| `low-vol` | <10 markets/day |

---

## 4. Sub-Agent Prompt Templates

### 4A. Data Collection Agent (Haiku — per wallet)
```
**Context**: Polymarket wallet reverse engineering. Collecting trade data for strategy analysis.
**Task**: Fetch and summarize trade data for wallet {ADDRESS}.
**Scope**:
  - Use WebFetch to get: https://polymarket.com/profile/{ADDRESS}
  - Extract: PnL, Volume, Markets count, Join date, recent activity
  - If profile page gives summary stats, capture them
**Format**: Return structured JSON:
  {
    "address": "0x...",
    "nickname": "...",
    "pnl": "$...",
    "volume": "$...",
    "markets_count": ...,
    "join_date": "...",
    "top_markets": ["BTC 15M", "ETH 15M", ...],
    "recent_activity": "active/inactive",
    "notes": "..."
  }
**Anti**: Do NOT analyze strategy — just collect raw data.
```

### 4B. Strategy Analysis Agent (Sonnet — per wallet)
```
**Context**: Polymarket wallet reverse engineering for BTC 15M market making research.
  Known strategies:
  - Binary Arb (BA): buy both sides <$1.00, maker orders, no management
  - Informed MM (IMM): both sides but asymmetric, signal-based
  - Directional (DIR): single-side bets
  - Hybrid (HYB): mix strategies
  - Scalper (SCA): high frequency, tight spread
  - Event Trader (EVT): non-crypto markets

**Task**: Analyze wallet {ADDRESS} ({NICKNAME}) trade data and determine:
  1. Strategy classification (BA/IMM/DIR/HYB/SCA/EVT)
  2. Key metrics (see analysis dimensions in framework)
  3. Confidence level for classification

**Scope**:
  - Read raw trade data from: memory/trading/wallet_analysis/raw/{address}_trades.json
  - Cross-reference with existing analysis: memory/trading/polymarket_wallet_reverse_engineering.md
  - Focus on BTC 15M trades if they exist

**Format**: Markdown report with sections:
  ## {Nickname} — {Address_short}
  ### Classification: {TYPE} (Confidence: HIGH/MEDIUM/LOW)
  ### Key Metrics (table)
  ### Strategy Pattern (narrative)
  ### Notable Observations
  ### Comparison to Known Wallets

**Chain**: Output will be saved to profiles/ folder and used for cluster analysis.
**Anti**: Do NOT speculate beyond data. Mark inferences as INFERRED. Mark verified facts as SEEN.
```

### 4C. Cluster Analysis Agent (Sonnet — after all profiles done)
```
**Context**: Polymarket wallet reverse engineering. All individual profiles are complete.
  Classification system: BA/IMM/DIR/HYB/SCA/EVT with S/A/B/C/F grades.

**Task**: Group all analyzed wallets into clusters and write cluster reports:
  1. Read all profiles from profiles/ folder
  2. Group by strategy type
  3. For each cluster:
     - Common patterns (entry timing, sizing, management)
     - Performance range (best to worst)
     - Key differentiators within cluster
     - Actionable insights for our strategy
  4. Cross-cluster comparison

**Scope**: Read all files in profiles/ folder.
**Format**: One .md file per cluster in clusters/ folder + updated CLASSIFICATION_INDEX.md
**Chain**: Output feeds into summary_report.md
**Anti**: Do NOT mix clusters for convenience. If a wallet doesn't fit, create a new cluster or mark as "unclassified".
```

### 4D. Summary Report Agent (Main context — final step)
```
**Context**: All wallet analysis complete. Need executive summary.
**Task**: Synthesize all cluster reports into one summary:
  1. Total wallets analyzed
  2. Strategy distribution (pie chart description)
  3. Top performers + what makes them different
  4. Common patterns across profitable wallets
  5. Common patterns across losing wallets
  6. Lessons for our AXC MM strategy
  7. Recommended next steps

**Format**: summary_report.md with tables, rankings, and actionable takeaways
```

---

## 5. 執行流程

```
Phase 0: Receive wallet addresses
    └── User provides list of addresses

Phase 1: Data Collection (parallel — haiku agents)
    ├── Agent 1: Wallet A profile fetch
    ├── Agent 2: Wallet B profile fetch
    ├── ...
    └── Agent N: Wallet N profile fetch
    → Save to raw/ folder

Phase 2: Individual Analysis (parallel — sonnet agents)
    ├── Agent 1: Wallet A strategy analysis
    ├── Agent 2: Wallet B strategy analysis
    ├── ...
    └── Agent N: Wallet N strategy analysis
    → Save to profiles/ folder

Phase 3: Cluster Analysis (single — sonnet agent)
    └── Read all profiles → group → write cluster reports
    → Save to clusters/ folder + update CLASSIFICATION_INDEX.md

Phase 4: Summary (main context)
    └── Synthesize everything → summary_report.md
```

### Parallel Batching
- Sub-agents: max 5-8 concurrent for stability
- If >8 wallets: batch into groups of 5-8
- Each batch completes before next starts
- Progress tracking in main context

---

## 6. 數據源 + API

### Primary
| Source | URL Pattern | Data |
|--------|-------------|------|
| Profile page | `polymarket.com/profile/{address}` | PnL, volume, markets, positions |
| Trade history API | `clob.polymarket.com/api/trades/user/{address}` | Individual trades with timestamps |
| Dune: Leaderboard | `dune.com/genejp999/polymarket-leaderboard` | Rankings, aggregate stats |
| Dune: Terminal | `dune.com/no__hive/terminal-1` | Top wallets, flow analysis |

### Secondary (if needed)
| Source | Data |
|--------|------|
| Telonex (telonex.io) | Maker/taker per trade, fill probability |
| PolyWatch (polywatch.tech) | >$1K trade alerts, staking |
| On-chain (Polygonscan) | Raw transactions, token transfers |

---

## 7. 質量保證

- 每個 profile 必須標記 **SEEN** (從數據直接觀察) vs **INFERRED** (推理) vs **GUESSED** (推測)
- Classification confidence: HIGH (>3 supporting signals) / MEDIUM (2 signals) / LOW (1 signal)
- 交叉驗證：同類錢包之間嘅數據要 consistent
- 已知錢包 (Anon, LampStore) 作為 calibration — 新分析結果唔應該同已知結論矛盾
