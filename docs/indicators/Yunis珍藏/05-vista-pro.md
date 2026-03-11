# 05 — VISTA PRO
> 視野指標 | Trader_Yunis
> TradingView: 邀請制（invite-only）

---

## Talk12 — 用最簡單嘅方式講

想像你去海灘，想知幾時浪會打嚟。

**Peak Detection（頂底偵測）**就好似你企喺沙灘睇海浪：
- 水慢慢升高 → 你開始留意（Initial Test）
- 水繼續升 → 你行後少少（Level 1）
- 水升到好高 → 你開始跑（Level 2）
- 浪打到最高點 → 你已經安全（Level 3 — 最可靠嘅訊號）

唔係一見到水升就叫「浪嚟啦」，而係等到越嚟越多證據先確認。呢個就叫「**漸進確認**」— Level 越高，越肯定，但等得越耐。

**MR Oscillator（複合震盪器）**就好似一個溫度計，但唔係量一樣嘢，而係同時量咗幾樣嘢（RSI、動量、波動率等等）然後混合成一個數字。呢個數字去到極端（太熱或太凍）= 市場太偏向一邊，可能反轉。

**Volatility 分區**就好似天氣預報：
- 🟠 橙色 = 暴風雨（高波動，小心反轉）
- ⚪ 灰色 = 無風（低波動，壓縮中，突破即將來臨）

**Divergence**就係「溫度計話冷，但你覺得熱」— 價格同震盪器唔同步，有人講大話，快啲會被揭穿。

---

## 技術細節

### Peak Detection 四級系統
```
Level 0 (Initial Test) → 最敏感，多假訊號
Level 1              → 第一層確認
Level 2              → 第二層確認
Level 3              → 最可靠，但最慢

用法：Level 越高 → position size 越大
```

### MR Oscillator
- 結合多個技術指標計算出一個 0-100 composite score
- 具體成分未公開（closed-source）
- OB zone（> 80）= 超買 → mean reversion 做空
- OS zone（< 20）= 超賣 → mean reversion 做多

### Reversal Histogram
- 藍色柱 = bearish reversal 條件形成
- 紅色柱 = bullish reversal 條件形成
- 柱嘅高度 = reversal 力度

### MR Divergence
| 標籤 | 含義 |
|---|---|
| R▴ | Regular bullish divergence（反轉向上）|
| R▾ | Regular bearish divergence（反轉向下）|
| H▴ | Hidden bullish divergence（趨勢延續向上）|
| H▾ | Hidden bearish divergence（趨勢延續向下）|

### Trend Strength Bands
- RSI-based moving average envelopes
- 藍色 = bullish momentum
- 紅色 = bearish momentum

### 關鍵參數
| 參數 | 作用 |
|---|---|
| Sensitivity (0-100%) | 高 = 多訊號但多假；低 = 少但準 |
| Max Bar Interval | Divergence lookback 範圍（建議 60-120） |
| Color Theme | Classic warm / Neon cold |

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| 漸進確認系統（4 level）| AXC 係一個訊號就決定 | ⭐⭐ 高價值：唔同 level → 唔同 position size |
| Composite oscillator | 各指標獨立投票 | 可以做加權合成分數 |
| Sensitivity 參數 | 冇全局靈敏度控制 | 加入做 global signal filter |
| Volatility 分區（橙/灰）| 有 mode_detector | 概念相似，可加視覺化 |
| Bar coloring | Dashboard 有但唔精細 | Nice to have |
