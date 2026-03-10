# 03 — MACD PRO
> 平滑異同移動平均線 | Trader_Yunis
> TradingView: 公開（closed-source）

---

## Talk12 — 用最簡單嘅方式講

想像你養咗兩隻唔同速度嘅狗 — 一隻快狗（fast EMA），一隻慢狗（slow EMA）。你每日帶佢哋散步。

**MACD 線**就係快狗同慢狗之間嘅距離。如果快狗跑喺前面（價格升得快），距離會越拉越遠 → MACD 升。如果快狗慢落嚟，距離收窄 → MACD 跌。

**Signal 線**就係 MACD 自己嘅「平均數」— 好似你用幾日嘅距離計一個平均。

**Histogram（柱狀圖）**就係 MACD 同 Signal 之間嘅差距。普通版只有兩隻色（正/負），但 MACD PRO 有**四隻色**：

想像一架車：
- 🔵 **淺藍** = 油門踩緊，加速中（bullish 加強）
- 🔷 **深藍** = 腳離開油門，仲喺行但慢緊（bullish 減弱）
- 🔴 **淺紅** = 制動踩緊，減速中（bearish 加強）
- 🟥 **深紅** = 制動放開，仲喺倒但好快停（bearish 減弱）

**深色 → 淺色嘅變化比交叉更早**！車未掉頭之前，你已經可以感覺到佢喺制動。

**Divergence（背馳）**就係「隻狗話向左，但主人行向右」— 價格同動量唔同步，代表有人講大話，快啲會被揭穿。

---

## 技術細節

### 基本計算
```
MACD Line = EMA(fast) - EMA(slow)    # 默認 12-26
Signal Line = EMA(MACD, signal)       # 默認 9
Histogram = MACD - Signal
```

### 四色 Histogram 邏輯
```
if histogram > 0 AND histogram > histogram[1]:
    color = LIGHT_BLUE    # bullish 加強 ↑↑
elif histogram > 0 AND histogram <= histogram[1]:
    color = DARK_BLUE     # bullish 減弱 ↑↓
elif histogram < 0 AND histogram < histogram[1]:
    color = LIGHT_RED     # bearish 加強 ↓↓
elif histogram < 0 AND histogram >= histogram[1]:
    color = DARK_RED      # bearish 減弱 ↓↑
```

### Auto Divergence Detection
| 類型 | 含義 | 標籤 |
|---|---|---|
| Regular Bullish | 價格新低 + MACD 冇新低 → 可能反轉向上 | Bull |
| Regular Bearish | 價格新高 + MACD 冇新高 → 可能反轉向下 | Bear |
| Hidden Bullish | 價格 higher low + MACD lower low → 趨勢繼續向上 | H Bull |
| Hidden Bearish | 價格 lower high + MACD higher high → 趨勢繼續向下 | H Bear |

### 關鍵參數
| 參數 | 作用 | 建議值 |
|---|---|---|
| Pivot Lookback | 控制 divergence 靈敏度 | 2-3（多訊號）/ 5-10（少但準）|
| Don't Cross Zero | MACD 要留同一邊先算有效 divergence | 開啟（減假訊號）|
| Timeframe Lock | 鎖定特定 TF（例如 15m 圖用 1H MACD）| 用嚟做 MTF |

### 其他訊號
- **Gold/Death Cross dots**：MACD 穿越 Signal
- **Zero-line cross ▲▼**：MACD 穿越零線（較強確認）

---

## AXC 可借鑒

| 概念 | 現狀 | 行動 |
|---|---|---|
| 4-color histogram | AXC 只用 MACD cross | ⭐ 可加入做 early warning |
| Auto divergence detection | 冇 | 中期加入（需要 pivot detection）|
| Hidden divergence | 冇 | 趨勢延續訊號，價值高 |
| MTF MACD | 冇 | 長期：higher TF MACD 做 filter |
| Don't Cross Zero filter | 冇 | 簡單實現，減少假 divergence |
