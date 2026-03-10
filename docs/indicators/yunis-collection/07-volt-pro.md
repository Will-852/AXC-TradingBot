# 07 — VOLT PRO
> Volume & Oscillator Logic Tracker PRO | Trader_Yunis
> TradingView: 邀請制（invite-only）
> ⭐⭐⭐ Collection 入面最有啟發性嘅一個

---

## Talk12 — 用最簡單嘅方式講

想像你同朋友打籃球，要判斷對手嘅進攻有冇威脅。

**成交量（Volume）**就好似「幾多人企喺你前面」— 如果對手一個人衝過嚟，你唔驚。但如果十個人一齊衝，你就知大鑊(wok6)。

**動量（Momentum）**就好似「佢哋跑幾快」。

**VOLT PRO 嘅核心概念：雙共振（Dual Resonance）**

普通指標只睇一樣嘢。但 VOLT PRO 同時睇兩樣：
- 有幾多人衝過嚟？（成交量 CVD）
- 佢哋跑幾快？（價格動量 Z-Score）

如果**兩個同時話「危險」**→ ⭐⭐ 2-Star Signal（非常可靠）
如果**只有一個話「危險」**→ ⭐ 1-Star Signal（可能係假嘅）

好似你判斷：
- 10 個人跑緊過嚟 + 跑得好快 = ⭐⭐ 真正嘅攻擊！
- 10 個人企喺度 + 唔郁 = ⭐ 可能只係企喺度傾偈(gai2)
- 2 個人跑過嚟 + 好快 = ⭐ 可能只係跑步練習

**Squeeze（壓縮）**就好似你用兩隻手壓住一個彈弓(goeng1)。你壓得越耐，放手嗰陣飛得越遠。市場靜嘅時候（Squeeze ON）= 你壓住個彈弓；突然有動靜（Squeeze OFF）= 彈弓射出去，一定有大動作。

---

## 技術細節

### 核心系統 1：Dual Resonance（雙共振）

**CVD (Cumulative Volume Delta)**：
```
每根 K 線：Volume Delta = Taker Buy Volume - Taker Sell Volume
CVD = Σ(Volume Delta)    # 累積

CVD 上升 = 買壓 > 賣壓
CVD 下降 = 賣壓 > 買壓
```

**Z-Score（標準化偏差）**：
```
Z = (Value - Mean) / StdDev

Z > 2  = 極端偏高（2個標準差以上）
Z < -2 = 極端偏低
```
- 將 CVD 同 Price momentum 都轉成 Z-Score
- 統一標準，可以直接比較

**Signal Rating**：
```
2-Star ⭐⭐ = CVD Z-Score 同 Price Z-Score 同方向且都極端
           → 成交量同價格雙重確認 → 最高勝率

1-Star ⭐  = 只有 PV Stochastic 過極端
           → 較早但較多假訊號
```

### 核心系統 2：Squeeze-Momentum

**Squeeze 偵測**：
```
Bollinger Bands: SMA(20) ± 2.0 × StdDev
Keltner Channel: EMA(20) ± 1.5 × ATR(20)

Squeeze ON:  BB 完全收入 Keltner 入面（dark dots）
Squeeze OFF: BB 突破 Keltner（orange dots）
```

**Squeeze + Z-Score Histogram**：
```
Squeeze ON  + Z-Score 開始擴張 = 最佳入場時機
Squeeze OFF + Z-Score 極端     = breakout 方向確認
```

### 核心系統 3：Stochastic CVD

**創新概念**：將 CVD 放入 Stochastic 公式
```
input = CVD + Price EMA（複合值）
Stochastic CVD = Stochastic(input, K_period)

> 80 = 買壓過度（overbought）
< 20 = 賣壓過度（oversold）
50   = 中性
```
- 比單獨睇 CVD 更易用（有 0-100 範圍）
- 比單獨 Stochastic 更可靠（有 volume 支持）

### Divergence Detection
```
Bullish Div: 價格新低 + Z-Score 冇新低 → "Div▴"
Bearish Div: 價格新高 + Z-Score 冇新高 → "Div▾"
```
- 用 Z-Score（包含 volume）做 divergence 比用 RSI 多一個維度

### 關鍵參數
| 參數 | 默認 | 作用 |
|---|---|---|
| CVD Period | 14 | CVD 計算期 |
| BB Period | 20 | Squeeze 偵測用 |
| BB StdDev | 2.0 | Squeeze 偵測用 |
| Stoch OB/OS | 80/20 | Stochastic CVD 嘅極端區 |

---

## 點解係 Collection 入面最有啟發性？

1. **Star Rating 系統**直接對應 AXC 嘅加權評分 — 唔同 confidence → 唔同 position size
2. **CVD + Z-Score** 填補咗 AXC 最大嘅盲點（冇 volume 確認）
3. **Squeeze detection** 幫助 mode_detector 偵測「就嚟爆發」嘅時機
4. **Stochastic CVD** 係 VW-RSI 嘅替代方案，概念更清晰

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 | 優先級 |
|---|---|---|---|
| Signal confidence rating（star）| 冇 | ⭐⭐ 唔同 level → 唔同 position size | 高 |
| CVD calculation | 冇 volume 指標 | ⭐⭐ 填補最大盲點 | 高 |
| Z-Score 標準化 | 冇 | 統一所有指標嘅 scale | 中 |
| BB/Keltner squeeze | 有 BB_WIDTH_MIN 但唔完整 | ⭐ 加入完整 squeeze 偵測 | 中 |
| Stochastic CVD | 冇 | 替代 Stochastic oscillator | 中 |
| Z-Score divergence | 冇 divergence detection | 中期加入 | 低 |
