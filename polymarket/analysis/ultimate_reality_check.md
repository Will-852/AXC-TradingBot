# 終極現實檢查：Polymarket Binary Arb 嘅真相
> 日期：2026-03-25
> 來源：5 個真錢包 trade data + 14 個錢包逆向工程 + analysis/ 全部研究 + 4-Phase BMD
> 格式：talk15 story + 數據 + 修正計劃

---

## 第一幕：殘酷現實

三個機械人。一個荷包。

```
┌─────┬──────────────┬──────────────┬──────┬───────────┐
│ Bot │ Bot 話嘅 PnL │ CSV 真實 PnL │  WR  │   狀態    │
├─────┼──────────────┼──────────────┼──────┼───────────┤
│ 15M │ +$55.64      │ -$72         │ ~35% │ 🔴 蝕緊   │
│ 1H  │ +$71.52      │ -$50.33      │ 27%  │ 🔴 蝕緊   │
│ 5M  │ N/A          │ dry-run      │ N/A  │ 🟡 冇數據 │
└─────┴──────────────┴──────────────┴──────┴───────────┘

Total real loss: ~-$122+ on ~$253 bankroll = -48%
```

1H bot 27% WR + 單筆 $49.99 disaster = 即刻停。
15M bot post-fix 數據太少（2 trades）。降 sizing 觀察。
5M 係唯一有 edge 嘅方向 — 但要做啱。

---

## 第二幕：五個錢包嘅真相

同一日（Mar 24），同一個戰場，2 小時。1,971 行真實 trade data。

```
┌──────────────────┬──────────┬───────────┬────────┬─────────┬──────────────────────────┐
│ Wallet           │ Combined │ < $1 率   │ Merges │ Trades  │ 結論                     │
├──────────────────┼──────────┼───────────┼────────┼─────────┼──────────────────────────┤
│ Unlawful-Shear   │ 0.884    │ 81%       │ 109 次 │ 6,757   │ 最強。Merge 係核心       │
│ Fickle-Spark x2  │ 0.894    │ 79%       │ SELL   │ 3,495   │ Hybrid：arb + 方向       │
│ Charming-Knee    │ 0.977    │ 58%       │ 0      │ 3,500   │ 薄利：$469 gross/76min   │
│ Realistic-Swivel │ 1.002    │ 52%       │ 少     │ 3,470   │ 🔴 蝕緊。Arb edge 負     │
└──────────────────┴──────────┴───────────┴────────┴─────────┴──────────────────────────┘
```

**0xd1eb 同 0xc173 係同一個人** — 同 pseudonym、同 trade count、同時間。

**Realistic-Swivel 最重要**：Profile 話 +$253K 但真實 arb edge **負數**。
呢個證明：用同一個策略，執行差嘅人會蝕。

---

## 第三幕：Polymarket 嘅遊戲規則

你發現咗一個關鍵事實：**Polymarket 自己創造 arb 空間**。

### 機制

1. **Market 開場**：Polymarket 放 0.01/0.99 placeholder spread（最寬）
2. **Market Maker 入場**：spread 收窄到 real price
3. **Polymarket 獎勵 MM**：
   - Maker fee = **0%**（免費掛單）
   - Taker fee = **dynamic**（50/50 附近 peak 3.15%）
   - Maker rebate = **20% of taker fees**（Polymarket 倒貼你）
   - **3x multiplier** for two-sided quoting（兩邊都掛 = 三倍獎勵）
   - Rewards zone = mid ± 4.5¢（掛得太遠冇獎勵）
4. **結果**：Up + Down combined < $1.00 嘅「arb 空間」係 **Polymarket 設計出嚟嘅**

Polymarket 嘅生意模型：
```
Taker（散戶 / 方向性玩家）付 2-3% fee
  → Polymarket 收一份
  → Maker（我哋）收 20% rebate
  → Maker 嘅 combined < $1.00 就係 "arb edge"
  → 呢個 edge 本質上係 Polymarket 付畀 Maker 嘅 "流動性租金"
```

### 你嘅洞察係啱嘅

> 「理論上我哋嘅做法係啱嘅，但係我哋嘅基礎設施追唔上人哋。」

Polymarket 創造 3-12% 嘅 arb 空間（combined 0.88-0.97）。
但呢個空間係 **先到先得**。

```
Arb 空間生命週期：
  T=0    Market 開場。Combined ~0.50（huge spread）
  T+5s   最快嘅 bot 入場。Amsterdam VPS, 1ms latency。食咗 combined 0.88
  T+15s  第二梯隊入場。US-based bots。食 combined 0.90-0.93
  T+60s  散兵游勇。HK-based bots (我哋)。Combined 已經 0.95-0.98
  T+120s 基本冇肉。Combined ≈ 0.99-1.00
```

Unlawful-Shear combined 0.884 = 佢係 **T+5-15s 嗰班人**。
Charming-Knee combined 0.977 = 佢係 **T+30-60s 嗰班人**。
Realistic-Swivel combined 1.002 = 佢係 **遲到嗰個**。蝕緊。

我哋喺香港，CLOB 喺美國東岸。**光速 + 網絡 = 最少 150-300ms**。
Amsterdam bots = **1-5ms**。

呢個唔係「引擎少兩個缸」。呢個係 **你喺香港跑，佢喺賽道旁邊跑**。

---

## 第四幕：四面牆（BMD 結果）

我設計咗四個 Phase。四個全部被 BMD 殺死。

### 牆 1：Preemptive Cancel → 💀💀💀 自殺

| 發現 | 嚴重度 |
|------|--------|
| 3bps threshold = BTC 300s 內觸及機率 **87%** → cancel 八成七嘅單 → fill rate 28% → **4%** | 💀 |
| HK → CLOB latency **300ms**（唔係 100ms）。Amsterdam bots 用 1ms。 | 💀 |
| 53.6% edge 蒸發嘅根因係 **fill rate 低**（69 個 signal 冇 fill），唔係 toxic fill | 💀 |
| 60% toxic fill rate 基於 **n=10**，95% CI [26%, 88%]，同 random 無法區分 | 🟡 |

**真正嘅問題唔係 cancel 太慢，係掛單冇人接。**

### 牆 2：5M W4 Clone → 💀💀💀 過期功課

| 發現 | 嚴重度 |
|------|--------|
| WR 由 Sep 93.1% 跌到 Mar **85.6%**，趨勢 **-1.06pp/月**（bot 競爭加劇） | 💀 |
| 冇 entry price model。WR=85.6% 買到 $0.856 = **EV 零** | 💀 |
| Polymarket 5M dynamic taker fee **3.15%** near 50/50。Arb edge 0.87% < fee | 💀 |
| 930 行 architecture spec **零次提及 fees** | 🔴 |

**89.5% WR 係舊數據。今日 85.6%。下個月可能 84.5%。**

### 牆 3：Multi-TF Cascade → 💀💀 統計幻覺

| 發現 | 嚴重度 |
|------|--------|
| 54.7% WR, n=371, **p=0.0702**。95% CI 包含 50%。 | 💀 |
| Double-lock 55.7%, n=97, **p=0.2615**。完全 noise。 | 💀 |
| 6:1 lean ratio = 85.7% allocation = **18x Full Kelly** | 🟡 |

**你分唔到呢個同擲硬幣有咩分別。**

### 牆 4：Merge → 💀💀 解決唔存在嘅問題

| 發現 | 嚴重度 |
|------|--------|
| Capital utilization = **4%**。$253/$5 = 51 個 position 容量。唔缺錢。 | 💀 |
| Merge incremental value = **$0/day**（hold to resolution 收到同樣嘅 profit） | 💀 |
| Merge 係 on-chain smart contract call，唔係 API call。全新技術棧。 | 🟡 |

**但係** — BMD 分析嘅前提係 15M market（同一時間最多 1-2 個 window）。
如果轉去 5M market（每 5 分鐘 4 個 coin = 同時有 4+ 個 window），
capital 可能真係 bottleneck。呢個要 paper trade 先知。

---

## 第五幕：企喺巷子中間

四面牆冇一面有出路。但你手上有真實數據。

**核心矛盾**：
- Polymarket 設計咗 3-12% arb 空間 ✅
- 我哋嘅理論做法（兩邊買 + hold）係啱嘅 ✅
- 但 arb 空間係先到先得 ✅
- 我哋嘅 latency 排最後 ✅

**所以真正嘅問題唔係「點樣做」—— 係「幾時輪到我哋」？**

答案可能好殘忍：**永遠輪唔到**。如果 arb 空間喺 T+15s 已經被食晒，
我哋 T+120s 入場時 combined 已經 0.99-1.00。冇肉。

但 Charming-Knee 嘅數據話：佢 T+?? 入場，combined 0.977，仲有 $469 gross profit / 76 min。
佢唔係最快嘅。佢係 **夠快嘅**。

所以問題變成：**我哋夠唔夠快去攞到 combined < 0.96？**

**唔知。因為從來冇量過。**

---

## 第六幕：唯一正確嘅下一步

### 你唔需要更快嘅引擎。你需要一個碼錶。

```
問題 1：我哋買到幾錢？             → 48h paper trade
問題 2：Fill rate at $5.06 幾多？   → 48h paper trade
問題 3：Fee 扣完 net EV 正定負？    → 48h paper trade
```

三個問題，同一個答案：**跑 48 小時 dry-run，量度真實數據**。

#### 操作

```bash
# 已有 dry-run mode，0 行新 code
cd ~/projects/axc-trading
PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --dry-run
```

48 小時後量度：
- **Actual combined entry price**（我哋嘅 orders fill 到幾錢？）
- **Fill rate**（幾多 signal 最終 fill 到？）
- **Fee impact**（maker orders = 0% fee，但有冇被 taker fill？）
- **Latency**（signal 到 order 幾多 ms？order 到 fill 幾多 ms？）

#### 決策樹

```
Paper Trade 結果
│
├── Combined > 0.96 after fees
│   └── STOP。我哋太慢。Arb 空間已經被食晒。
│       考慮：(a) VPS migration to Amsterdam
│              (b) 完全唔同嘅策略（directional, 唔係 arb）
│              (c) 停止 Polymarket，專注 AXC 主系統
│
├── Combined 0.93-0.96 after fees
│   └── 有微薄 edge。繼續但保守：
│       Phase A: Dynamic pricing（vol-based spread）~4-6h
│       Phase B: BTC live at $5/market
│       Phase C: 如果 30 天正數 → 加 ETH
│       Expected: $3-8/day
│
└── Combined < 0.93 after fees
    └── 有真實 edge。加速：
        Phase A: BTC live immediately
        Phase B: Multi-coin（+ETH +SOL +XRP）~2h
        Phase C: Dynamic pricing optimization ~4-6h
        Phase D: Merge（如果 capital 成為 bottleneck）~8-12h
        Expected: $8-20/day
```

### 如果 Paper Trade 話「太慢」（Combined > 0.96）

呢個係最可能嘅結果。唔好迴避。

**選項 A：VPS Migration**
- Amsterdam / NYC VPS，~$20-50/月
- Latency 300ms → 5-20ms
- 可能足以將 combined 由 0.97 推到 0.92
- 但加咗一層 complexity + cost

**選項 B：轉 directional（唔係 arb）**
- Female-Billing：單邊 directional 1H，27 日 $220K
- Unlawful-Shear：60% combined > $1.00，靠 directional accuracy 賺錢
- 需要 signal quality 我哋未證明過
- 風險更高（錯咗就全蝕）

**選項 C：認輸**
- $253 bankroll - $122 已蝕 = $131 剩
- 如果 paper trade 話冇 edge，最理性嘅決定係 **停**
- 將精力放喺 AXC 主系統（已有 infrastructure）
- 「唔做」有時候就係最好嘅 trade

---

## Appendix A：14 個成功錢包嘅共同特徵

從 6 份逆向工程 + wallet_analysis/summary_report 綜合：

| 特徵 | 出現率 | 我哋有冇 |
|------|--------|---------|
| BTC 為主（60-70%） | 14/14 | ✅ |
| 5M 為主 timeframe | 10/14 | ⚠️ 有 bot 但未 live |
| Both-sides 買入 | 12/14 | ✅ |
| 100% maker orders | 11/14 | ✅ |
| Multi-coin（3-4 coins） | 12/14 | ❌ BTC only |
| Combined < $1.00 | 11/14 | ❓ 未量過 |
| Automated（0s inter-trade gap） | 14/14 | ✅ |
| 低 latency（VPS / co-located） | ~10/14 (estimated) | ❌ HK Mac |
| Hold to resolution | 11/14 | ✅ |
| Active merge | 2/14 | ❌ |
| Active sell（mid-market exit） | 3/14 | ❌ |

**最大缺口：latency + multi-coin + combined price 未知。**

## Appendix B：WR Decay 數據（5M, 5bps threshold, T+120s）

```
2025-09: 93.1% (n=957)
2025-10: 91.0% (n=4,434)
2025-11: 90.2% (n=5,033)
2025-12: 90.5% (n=4,087)
2026-01: 91.5% (n=3,527)
2026-02: 86.8% (n=5,105)  ← 跌
2026-03: 85.6% (n=3,068)  ← 繼續跌

趨勢：-1.06pp/月
原因：Bloomberg 報導 5M 熱潮、Whop 有商業 bot、市場效率提升
推算 2026-04: ~84.5%
```

## Appendix C：Fee Structure

```
Taker fee = max(0, p × (1-p) × 0.0222)
  p=0.50: 3.15% (peak)
  p=0.90: 0.20%
  p=0.99: 0.02%

Maker fee = 0%
Maker rebate = 20% of taker fees (daily USDC)
3x rebate for two-sided quoting

→ Maker orders 唔付 fee
→ 但如果 combined < $1.00 嘅 arb edge < 2%，taker side 嘅 fee 已經食晒
→ W4 94.5%+ maker = 有效 fee ≈ $0.00-0.01/trade
```

## Appendix D：Polymarket 點樣創造 Arb 空間

```
1. Market 開場 → 0.01/0.99 placeholder spread
2. Polymarket rewards zone = mid ± 4.5¢ → 引 MM 入場收窄 spread
3. Up + Down combined < $1.00 = "arb edge" = Polymarket 付畀 MM 嘅流動性租金
4. Taker（散戶）付 2-3% fee → 資助 MM 嘅利潤
5. 最快嘅 MM 食最肥嘅 spread → 慢嘅 MM 食剩嘅

本質：Polymarket 用 taker fee 資助 maker，創造一個 speed race。
贏家係最快到達 CLOB 嘅 bot。
我哋喺香港 = 我哋永遠係最後到嘅。
```

---

## 最終判決

| 維度 | 狀態 |
|------|------|
| 理論 | ✅ 做法啱（兩邊買 + combined < $1 + hold to resolution） |
| 數據 | ✅ 有 edge 存在（5 個錢包 3 個賺緊） |
| Parameters | ⚠️ W4 parameters 有 decay（85.6% 仲跌緊） |
| Execution | 🔴 未量過。Combined price 唔知。Fill rate 唔知。 |
| Infrastructure | 🔴 HK latency 300ms vs Amsterdam 1ms |
| Bankroll | 🔴 $131 剩（-48%） |

**結論**：你知道點煮呢碟菜。你有食譜。但你嘅廚房喺 300 公里外，而且菜已經差唔多被人哋煮晒。

**下一步**：48 小時 paper trade。量度你嘅 combined entry price。如果 > 0.96 = 你嘅廚房太遠。如果 < 0.93 = 你仲有位。

> 唔好再設計引擎。揸架車上 track day。48 小時後你就知道答案。
