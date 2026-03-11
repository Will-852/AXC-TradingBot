# Volatility Risk Premium

> 作者: EdgeTools
> 連結: https://tw.tradingview.com/script/UJgVgUvT-Volatility-Risk-Premium/
> 類型: Pine Script 指標

---

![Preview](../market_structure/volatility_risk_premium_preview.png)

---

## 功能

Volatility Risk Premium (VRP) 係股票市場既「保險費」。佢量度緊係：

> **市場期望既波動率**（Implied Volatility，IV — 反映喺期權價格度） 
> **VS**
> **實際發生既波動率**（Realized Volatility，RV — 從真實價格移動計算）

呢個指標就係將呢個差距量化，等你可以做有actionable既 intelligence。

---

## 諗下保險既比喻

諗吓股票市場就好似一個社區，居民買保險去防止火災。保險公司根據佢哋對火災風險既估計收費。但有趣既位係：保險公司系統性地收多過實際預期損失。

呢個「你付既」同「實際發生既」之間既差別，就係「保險費」。

金融市場都係一樣既道理 — 不過唔係火險，而係通過期權合約對沖市場波動既「保險」。

---

## 學術基礎

VRP 既學術研究喺 2000 年代初開始獲得 serious traction。指標既方法論基於三篇研究論文：

- **Peter Carr and Liuren Wu** - Variance Risk Premiums
- 及其他相關學術研究

---

## 解讀方式

| VRP 狀態 | 市場含義 |
|---------|---------|
| **喺「正常」範圍內** | 波動率同不確定性「正常」— 期權市場覺得一切 OK |
| **高於「正常」範圍** | 投資者願意付多啲買期權 — 表示佢哋覺得市場不確定性增加 |
| **低於「正常」範圍** | 期權相對便宜 — 可能係低風險環境 |

---

## 使用建議

VRP 可以幫你：
- 評估市場情緒（緊張定輕鬆）
- 判斷期權係咪貴定平
- 識別潛在既市場風險累積

記住：VRP 係一個「領先指標」(leading indicator)，可以幫你預視市場情緒既變化。

---

*最後更新: 2025-03-11*
