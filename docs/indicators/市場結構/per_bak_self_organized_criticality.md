# Per Bak Self-Organized Criticality

> 作者: HenriqueCentieiro
> 連結: https://tw.tradingview.com/script/bZo4yadb-Per-Bak-Self-Organized-Criticality/
> 類型: Pine Script 指標

---

![Preview](../market_structure/per_bak_soc_preview.png)

---

## 功能

呢個指標量度市場既「脆弱性」(fragility)。佢量度系統對「級聯故障」(cascade failures) 同「相變」(phase transitions) 既易損程度。簡單啲講，就係幫你評估市場幾時「準備好山泥傾瀉」。

---

## 點解咁噉?

好似雪崩、山火、地震、疫情爆發同市場崩盤呢啲事件 — 佢哋唔係隨機既。

呢啲事件跟從「幂律分布」(power laws) — 穩定既系統會自然咁演化成「臨界狀態」(critical states)，响呢個狀態之下，小既trigger可以引發災難性既級聯效應。

例子：如果你堆緊一個沙堆，總會有一刻 — 再加少少沙就會引起山泥傾瀉。

市場都一樣，一粒一粒咁累積脆弱性，就好似一個接近雪崩既沙堆。

---

## 四個 Stress Vectors

指標加入左四個獨立既 stress vectors 去量化市場有幾易受不成比例既移動影響：

1. **Tail Risk（尾部風險）**
2. **Volatility Regime（波動率體制）**
3. **Credit Stress（信貸壓力）**
4. **Positioning Extremes（倉位極端）**

---

## Per Bak 語錄

> 「地震唔知自己會變幾大。因此，任何大型事件既預警狀態同小型事件既預警狀態基本上係一樣既。」

對於市場既意義：
- 我們唔能夠從初始條件預測個別崩盤既大小
- 我們可以預測崩盤既統計分布
- 我們可以識別系統風險增加既時期（臨界狀態附近）

---

## 使用建議

呢個指標唔係預測幾時會崩，而係話俾你知市場幾時處於「高危」狀態。當指標顯示高脆弱性既時候：
- 減少倉位
- 收緊止損
- 或者乾脆唔交易

記住：佢唔係預測工具，而係風險評估工具。

---

*最後更新: 2025-03-11*
