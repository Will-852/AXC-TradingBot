# 06 — TrendSync
> 趨勢協同指標 | Trader_Yunis
> TradingView: 邀請制（invite-only）

---

## Talk12 — 用最簡單嘅方式講

想像你睇一條公路上面嘅車流。

**EMA Ribbon（5 條 EMA 彩帶）**就好似 5 條車道嘅車。如果 5 條車道嘅車全部向同一個方向行，好整齊、好有秩序 → 代表趨勢好強。但如果啲車亂晒，有啲向左有啲向右，交叉嚟交叉去 → 代表冇趨勢，好混亂。

彩帶展開（5 條 EMA 排列整齊、距離拉開）= 🟢 **強趨勢**
彩帶糾結（5 條 EMA 攪埋一舊(gau2)）= 🔴 **冇趨勢**

**多層 Bollinger Bands（2σ、2.5σ、3σ）**就好似一條馬路有三層護欄(wut6 laan4)：
- 第一層護欄(laan4)（2σ）= 輕微偏離，正常
- 第二層（2.5σ）= 開始危險
- 第三層（3σ）= 撞到呢度幾乎肯定彈返

**RSI Kernel Density**就好似你統計過去幾百次考試嘅成績分佈。普通 RSI 只話你「呢次考咗 75 分」，但 Kernel Density 話你「歷史上考 75 分係 top 5% 定 top 50%？」— 知道「呢個分數有幾罕見」比知道分數本身更有用。

**SMC Zones（Smart Money Concepts）**就好似標記「上次大戶喺邊度買/賣」嘅位置。大戶通常喺同一個位置重複操作，所以呢啲位成日有反應。

---

## 技術細節

### 5-Layer EMA Ribbon（量子彩帶）
```
EMA 21 → EMA 34 → EMA 55（5 條）
Teal gradient = bullish（展開向上）
Red gradient = bearish（展開向下）
Mixed = consolidation（糾結）
```
- 比傳統 2-EMA cross 更精細
- 可以睇到趨勢「正在形成」（ribbon 開始展開）而唔係等到 cross

### Multi-Band Bollinger Bands
```
Band 1: SMA(20) ± 2.0σ    ← 正常範圍
Band 2: SMA(20) ± 2.5σ    ← 警告區
Band 3: SMA(20) ± 3.0σ    ← 極端區
```
- 價格突破 2.7σ = 做空條件
- 三層比單層更精細，可以分級反應

### RSI Kernel Density Estimation
- 唔係簡單 RSI > 70 = overbought
- 用統計方法計算 RSI 值嘅概率分佈
- 喺「低概率區域」出現嘅 RSI 值 = 真正罕見嘅極端 → 訊號更可靠
- 好處：自動適應唔同市場嘅 RSI 分佈特徵

### SMC (Smart Money Concepts) Zones
- **Supply Zone**：之前大幅下跌嘅起點（機構賣出位）
- **Demand Zone**：之前大幅上升嘅起點（機構買入位）
- 偵測方法：swing high/low + 連續 K 線 pattern
- ATR filter：只保留有足夠波動嘅 zone

### Order Block Detection
```
Bullish OB: 最後一根下跌 K 線（喺上升前）
Bearish OB: 最後一根上升 K 線（喺下跌前）
Filter: ATR > threshold（過濾小型 OB）
```

### Volume Candles
- 成交量 > 2x 平均 = 標記
- 成交量 > 4x 平均 = 強標記
- 高量 K 線嘅高低位 = key S/R

### 完整組件列表
| 組件 | 參數 |
|---|---|
| Quantum Ribbon | EMA 21-55 |
| RSI Signals | RSI 14 + kernel density |
| Moving Averages | EMA 21, 55, 100, 200 |
| Bollinger Bands | 2σ, 2.5σ, 3σ |
| ATR Stops | ATR(14) × 0.5 |
| Volume Candles | 2x / 4x avg threshold |
| SMC Zones | Swing detection |
| Order Blocks | Consecutive candle + ATR filter |

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| EMA ribbon（5 條）| AXC 只有 fast/slow 2 條 | 可加 ribbon 做趨勢強度視覺化 |
| Multi-band BB（2σ/2.5σ/3σ）| 只有 2σ | ⭐ 加 2.5σ + 3σ 做分級反應 |
| RSI kernel density | 固定 OB/OS 閾值 | 長期：動態 OB/OS 替代固定值 |
| SMC zones | 有 S/R 偵測（lookback_support）| 概念相似但可加 order block |
| Volume candles（2x/4x）| 有 MODE_VOLUME_HIGH=1.5x | ⭐ 可調高到 2x，加 4x 強訊號 |
| ATR stops | 有 sl_atr_mult | 概念一致 |
