# Volume Gaps & Imbalances

> 作者: Zeiierman
> 連結: https://tw.tradingview.com/script/Q7YQQq7g-Volume-Gaps-Imbalances-Zeiierman/
> 類型: Pine Script 指標

---

![Preview](../volume_analysis/volume_gaps_preview.png)

---

## 功能

一個先進既市場結構同 Order Flow 視覺化工具，測繪：
- 市場喺邊度交易過
- 市場喺邊度冇交易過
- 買家同賣家既壓力點樣响整個價格範圍度累積

---

## 核心概念

呢個指標既核心係一個 price-by-price volume profile，基於 Bullish 同 Bearish volume assignment。

佢會 highlight：
- **真正既零成交量空隙**（完全冇交易既價格區域）
- **Bull/Bear imbalance rows**（每個價格水平既橫向成交量切片）
- **多區段 Delta Panel** — 顯示每個垂直區域既聚合買-賣壓力

---

## ICT 概念基礎

呢個指標建基於 ICT (Inner Circle Trader) 既概念：

| 術語 | 定義 |
|------|------|
| **Volume Gap** | 兩支連續蠟燭既 wick 冇交叉既情況 |
| **Imbalance** | 得 wick 重疊，但 body 唔重疊既情況 |

呢啲低效率區域經常扮演「磁石」既角色，市場會努力重新平衡 (rebalance) 佢哋。

---

## 點解咁噉?

根據 ICT 原則，Volume Gaps/Imbalances 係價格傳遞既高效率區域。呢啲區域可以提供：
- 高概率既交易 entry 目標（反方向）
- 或者前度 entry 既 Take Profit 目標（順住 Gap/Imbalance 方向）

---

## 使用建議

1. **搵 rebalance zones** — 價格通常會重返呢啲區域
2. **睇成交效率** — 識別真正既低成交量區域
3. **Delta 分析** — 睇邊邊（買定賣）主導每個價格區域

配合其他 ICT 概念一齊用效果更好，例如：
- Market Structure (BOS / CHoCH)
- Liquidity pools
- Fair Value Gaps (FVGs)
- Premium/Discount arrays

---

*最後更新: 2025-03-11*
