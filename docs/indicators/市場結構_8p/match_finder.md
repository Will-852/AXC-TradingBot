# Match Finder

> 作者: theUltimator5
> 連結: https://tw.tradingview.com/script/ddvP5qAZ-Match-Finder-theUltimator5/
> 類型: Pine Script 指標

---

![Preview](../market_structure/match_finder_preview.png)

---

## 功能

Match Finder 係「indicators 既 dating app」。佢會幫你既 current ticker 搵最近期最 compatible 既 match。

---

## 點運作？

1. **設定**：預設掃描 40 隻 liquid ETFs（你可以自定義）
2. **Correlation**：對於每支蠟燭，script 會：
   -拎 current symbol 既最後 N 支蠟燭（Correlation Window Length）
   -拎每隻 comparison ticker 既最後 N 支蠟燭
   -計算 Pearson correlation
3. **Match**：搵出 correlation 最高既 ticker（唔包括你自己）
4. **Overlay**：將 matched 既 segment rescale 同overlay 等你可以視覺化比較形狀
5. **Table**（可選）：顯示所有 tickers 既 correlation values

---

## 使用場景

呢個指標幫你睇：
- 最近咩 symbol 既價格行上升/下降最似你睇緊既 chart
- 咩 sector 佢可能 follow 得最貼
- 將 matched pattern overlay 上去，你可以直接比較形狀

---

## 用途

- **Sector Analysis** — 發現某隻股票究竟跟邊個 sector 或者 ETF 最相關
- **Leading Indicator** — 有時某隻 ETF 會領，你可以先用黎做 leading signal
- **Correlation Trading** — 搵相關性高既 instruments 做對沖或者pairs trade

---

## 局限性

- 佢唔會預測未來既 connection
- 佢話俾你知既係「今日既兼容性」
- 過去既相關性唔代表將來都一樣

---

## 使用建議

呢個工具適合：
- 想了解市場結構既交易者
- 做 sector rotation 既投資者
- 想搵 leading indicators 既人

淨係睇 correlation table 既數字唔夠，要睇埋 overlay 既 shape 先最有用。

---

*最後更新: 2025-03-11*
