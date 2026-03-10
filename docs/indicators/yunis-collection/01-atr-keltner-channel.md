# 01 — ATR Keltner Channel（LITE + Pro）
> ATR 肯特納通道 | Trader_Yunis
> TradingView: LITE 版（公開）+ Pro 版（邀請制）

---

## Talk12 — 用最簡單嘅方式講

想像你企喺一條河嘅中間，河有兩邊堤壩。

**中間嗰條線**（baseline）就係河流嘅中心 — 價格通常會喺呢度附近流動。

**上面同下面嘅帶**就係堤壩 — 價格好少會衝出去。如果衝到堤壩邊，通常會彈返嚟。

但呢個 indicator 唔止得一層堤壩，佢有**三層**：
- **第一層**（最遠）= 大堤壩，衝到呢度幾乎肯定彈返
- **第二層**（0.5x）= 中間堤壩，回調支撐位
- **第三層**（0.618x）= 黃金比例位，精準入場

堤壩嘅闊度由 **ATR** 決定 — ATR 就係「最近價格每日跳幾多」。市場癲嘅時候，堤壩會變闊（因為波動大）；市場靜嘅時候，堤壩會收窄。

**點樣用？**
- 價格去到上面堤壩 → 可能跌返落嚟 → 考慮做空
- 價格去到下面堤壩 → 可能升返上去 → 考慮做多
- 但唔係撞到就入場！要睇 K 線有冇「被彈走」嘅跡象（留長燭芯）

**同 BB（布林帶）有咩分別？**
BB 用「標準差」計闊度 — 純數學。Keltner 用 ATR 計 — 基於真實價格波動。ATR 版對突然嘅暴漲暴跌反應更平滑，唔會好似 BB 咁一下子大幅收窄或擴闊。

---

## 技術細節

### 計算方式
```
Baseline = EMA(close, length)
Upper Main = Baseline + ATR(length) × multiplier
Lower Main = Baseline - ATR(length) × multiplier
Upper Half = Baseline + ATR(length) × multiplier × 0.5
Lower Half = Baseline - ATR(length) × multiplier × 0.5
Upper Phi  = Baseline + ATR(length) × multiplier × 0.618
Lower Phi  = Baseline - ATR(length) × multiplier × 0.618
```

### Baseline 顏色
| 顏色 | 含義 |
|---|---|
| 藍色 | 上升趨勢 |
| 紅色 | 下跌趨勢 |
| 灰色 | 冇方向 / consolidation |

### Wick Signal（燭芯訊號）
觸發條件：
1. K 線嘅身體穿過 band
2. 但收盤價返番 band 入面（即留咗長燭芯）
3. 燭芯長度 ÷ 整根 K 線 > Wick Ratio 門檻
4. （可選）SRSI 確認：做空需 SRSI > 80，做多需 SRSI < 20

訊號標籤：
- S / S₁ / S₂ / S₃ = 唔同 band 嘅做空訊號
- B / B₁ / B₂ / B₃ = 唔同 band 嘅做多訊號

### Pro 版額外功能
- **Volume Divergence**（◆ diamond）：價格去極端但量萎縮 = 動力衰竭
- **ATR Divergence**（▲ triangle）：價格新高但 ATR 冇新高 = 趨勢將結束
- **Hidden ATR Divergence**（✕ cross）：趨勢延續訊號
- **Heatmap candle coloring**：K 線顏色反映同 band 嘅距離

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| 三層 ATR band（1x / 0.5x / 0.618x）| AXC 只有單層 BB | 可加入做多層 S/R |
| Wick signal（rejection candle）| 冇 candle pattern detection | 中期加入 |
| Baseline 顏色（趨勢/中性判斷）| 有 mode_detector | 概念相似 |
| ATR divergence | 冇 | 可做趨勢衰退偵測 |
| SRSI filter | 有 Stochastic | 概念相似 |
