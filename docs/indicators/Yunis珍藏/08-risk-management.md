# 08 — Risk Management
> 風險管理 | Trader_Yunis
> TradingView: 公開（closed-source）
> 注意：呢個係計算工具，唔係交易指標

---

## Talk12 — 用最簡單嘅方式講

想像你去超市買嘢，你有 $100。

你想買一樣嘢，但你唔想全部錢都花晒。所以你決定：「呢次最多蝕 $2（2%）。」

**Risk Management 工具就係幫你計**：
- 如果我最多肯蝕 $2
- 而呢樣嘢如果跌咗 5% 我就要賣走（止損）
- 咁我應該買幾多？

答案：$2 ÷ 5% = $40 嘅嘢。

就係咁簡單！但喺真正交易入面，仲要計埋：
- **手續費**（買同賣都要俾錢）
- **槓桿**（借錢買，風險加倍）
- **分批入場**（唔係一次買晒，分幾次買）
- **幾個止盈目標**（賺到某個位先賣一部分）

呢個工具幫你一次過計晒。

---

## 技術細節

### 單筆交易模式
```
輸入：
- Portfolio Size（賬戶大小）
- Risk %（每筆風險百分比）
- Entry Price（入場價）
- Stop Loss Price（止損價）
- Take Profit Price（止盈價）
- Leverage（槓桿倍數）
- Taker/Maker Fees（手續費率）

輸出：
- Position Size（建議倉位大小）
- R:R Ratio（風險回報比）
- Expected Profit/Loss（預期盈虧，扣除手續費）
- Margin Required（需要嘅保證金）
```

### 分批入場模式
- 支持最多 5 個唔同價位嘅入場
- 自動計算加權平均入場價
- 每個入場分配唔同比例資金

### 動態 P&L
- 入場後隨住價格變動，實時顯示浮動盈虧
- 顏色 zone 顯示 TP/SL 範圍
- 虛線連接入場價同現價

### 計算公式
```
Position Size = Risk Amount / |Entry - Stop Loss|
Risk Amount = Portfolio × Risk%
Margin = Position Size / Leverage

Fee Impact = Position Size × (Entry Fee% + Exit Fee%)
Net Profit = Gross Profit - Total Fees
R:R = |Entry - TP| / |Entry - SL|
```

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| ATR-based position sizing | 有 MAX_POSITION_SIZE_USDT 固定值 | ⭐ 改為動態（已研究） |
| Fee impact calculation | 冇計手續費 | 加入 expectancy 計算 |
| R:R 預計算 | 有 MIN_RR 門檻 | OK |
| 分批入場 | 冇 | 長期考慮 |
| Trailing P&L display | Dashboard 有基本 | 可加強 |
