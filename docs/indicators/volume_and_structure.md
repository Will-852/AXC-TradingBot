# ATR、Volume、支撐阻力、結構指標深度研究
> 更新：2026-03-10
> 涵蓋：ATR、OBV、VWAP、Volume Profile、Fibonacci、Pivot、Ichimoku、MTF

---

## 1. ATR (Average True Range)

### 核心概念
- True Range = max(H-L, |H-Prev Close|, |L-Prev Close|)
- ATR = Smoothed Average of True Range over N periods
- 衡量：波動率（唔分方向）

### Crypto 最佳 Period
| 交易風格 | Period | 理由 |
|---|---|---|
| Scalping/Day | 7-10 | 快速反應波動變化 |
| Swing（4H-Daily）| **14** | 平衡（標準）|
| Position/Weekly | 20-50 | 平滑長期波動 |

### ATR 止損放置
```
Long SL = Entry - (ATR × Multiplier)
Short SL = Entry + (ATR × Multiplier)
```

| 市況 | Multiplier | 備註 |
|---|---|---|
| Range / 低波動 | 1.5-2.0x | 較緊 |
| 正常趨勢 | 2.0-3.0x | 標準 |
| 高波動 / 強趨勢 | 3.0-4.0x | 較鬆 |

**AXC 現有值**：
- CONSERVATIVE: `sl_atr_mult=1.5` ✅
- BALANCED: `sl_atr_mult=1.2` — ⚠️ 偏緊，range 市可能被假突破踢走
- AGGRESSIVE: `sl_atr_mult=1.0` — ⚠️ 非常緊

**研究發現**：ATR + 方向性指標組合，比單獨方向性指標提升 34% 利潤

### ATR Position Sizing
```
Position Size = (Account × Risk%) / (ATR × Multiplier)
```
- 高波動 → ATR 大 → 自動減倉
- 低波動 → ATR 小 → 自動加倉
- **保持每筆交易固定 dollar risk**

### ATR Take Profit
| 方法 | 公式 | R:R |
|---|---|---|
| 固定倍數 | TP = Entry + ATR × 2 | 1:1（if SL=2x ATR）|
| 非對稱 | TP = ATR×3, SL = ATR×1.5 | 2:1 |
| 階梯出場 | TP1=1x, TP2=2x, TP3=3x | 分批止盈 |
| Trailing | 隨價格移動，保持 ATR 距離 | 捕捉趨勢 |

### ATR Squeeze / Expansion（波動率偵測）
- ATR < 20th percentile（100 bar）= **Squeeze** → 突破將至
- ATR > 80th percentile = **Extreme Volatility** → 減倉或觀望

**BB/Keltner Squeeze**：
- Keltner Channel = EMA ± ATR × 1.5
- BB 收入 Keltner 入面 = Squeeze ON
- BB 突破 Keltner = Squeeze FIRE（方向由 momentum 決定）

---

## 2. OBV (On-Balance Volume)

### 計算
```
if Close > Prev Close: OBV += Volume
if Close < Prev Close: OBV -= Volume
if Close = Prev Close: OBV unchanged
```

### 訊號
| OBV | 價格 | 解讀 |
|---|---|---|
| 升 | 升 | 確認上升 ✅ |
| 跌 | 跌 | 確認下跌 ❌ |
| 升 | 跌/平 | Bullish divergence（smart money 吸貨）🔍 |
| 跌 | 升/平 | Bearish divergence（smart money 出貨）⚠️ |

### 實作建議
- OBV 絕對值冇意義 — 只睇斜率同 divergence
- 加 EMA(20) 喺 OBV 上面做平滑
- OBV divergence + S/R = 高勝率 setup
- **AXC 建議**：加入做 volume 確認，填補系統盲點

---

## 3. VWAP (Volume-Weighted Average Price)

### 計算
```
VWAP = Σ(Price × Volume) / Σ(Volume)
```
- 通常每日 reset

### 用法
- Price > VWAP = intraday bullish bias
- Price < VWAP = intraday bearish bias
- VWAP 係動態 S/R

### Anchored VWAP (aVWAP)
- 唔 reset，錨定到指定事件（大低/大高/breakout）
- Crypto 特別有用 — 冇「session open」概念
- 多條 aVWAP = 多個 volume-weighted S/R 層

### 適用性
- 最佳 timeframe：1m-15m（intraday）
- 4H 以上用處較少
- AXC 掃描頻率（3 分鐘）勉強可用 VWAP

---

## 4. Volume Profile

### 核心概念
- 將 volume 按價格分佈（而唔係按時間）
- **POC (Point of Control)**：成交量最大嘅價格 = 磁鐵/pivot
- **Value Area (VA)**：包含 70% 總成交量嘅價格範圍
- **HVN (High Volume Node)**：成交密集區 = 吸引力（consolidation）
- **LVN (Low Volume Node)**：成交稀疏區 = 排斥力（快速穿過）

### 交易規則
- 價格 > POC = bullish；< POC = bearish
- **Naked POC**（未測試嘅前 session POC）= 強磁鐵
- POC retest 入場，target 下一個 POC
- VA High / VA Low = 動態 S/R

### 限制
- 計算量大（需要逐 bar 按價格聚合）
- 唔係所有交易所 API 直接提供

---

## 5. Fibonacci Retracement

### 關鍵水平
| Level | 解讀 |
|---|---|
| 23.6% | 淺回調（強趨勢）|
| 38.2% | 中等回調（健康趨勢）|
| 50.0% | 心理中位（唔係真正 Fib 數）|
| **61.8%** | **黃金比例 — 最重要嘅水平** |
| 78.6% | 深回調（接近完全反轉）|

### Crypto 可靠度
- **自我實現效應**：幾百萬交易者喺 Fib 水平掛單 → 真正嘅買賣壓力
- 數學本身有冇內在意義有爭議，但效果係真實嘅
- 4H 同 Daily 最可靠；15m 太多噪音
- **Confluence = 關鍵**：Fib level + S/R + MA + Volume Profile 重疊 = 高勝率

---

## 6. Pivot Points

### 三種類型
| 類型 | 最佳用途 | 基礎 |
|---|---|---|
| Traditional | 通用 S/R | (H+L+C)/3 |
| Fibonacci | 趨勢市 | Pivot + Fib ratios × Range |
| Camarilla | Intraday/Scalping | C + (H-L) × multipliers |

### Traditional Pivot 公式
```
Pivot = (H + L + C) / 3
R1 = 2×Pivot - L    S1 = 2×Pivot - H
R2 = Pivot + (H-L)  S2 = Pivot - (H-L)
```

### Camarilla（Intraday 最佳）
- S3/R3 = 反轉入場區
- S4/R4 = Breakout 區
- Fib + Camarilla 重疊 = 強 zone

---

## 7. Ichimoku Cloud

### 五個組件
| 組件 | 公式 | 功能 |
|---|---|---|
| Tenkan-sen | (9H+9L)/2 | 短期趨勢 |
| Kijun-sen | (26H+26L)/2 | 中期趨勢 |
| Senkou A | (Tenkan+Kijun)/2, 前移 26 | 雲上邊 |
| Senkou B | (52H+52L)/2, 前移 26 | 雲下邊 |
| Chikou | Close 後移 26 | 確認 |

### Crypto 專用參數
| 用途 | Tenkan | Kijun | Senkou B | Displacement |
|---|---|---|---|---|
| 股票標準 | 9 | 26 | 52 | 26 |
| **Crypto 建議** | **20** | **60** | **120** | **30** |
| Day trading | 6 | 13 | 26 | 6 |

原因：原始參數基於 5 日工作周。Crypto 24/7 需要 ×2.2 修正。

### 訊號
- Price > Cloud = bullish
- Tenkan/Kijun cross above cloud = 強 buy
- Cloud twist = 趨勢可能變
- Cloud 厚度 = 趨勢強度
- 4H / Daily 最可靠

### 同 EMA 嘅冗餘
- Ichimoku 本質上係多條 MA 嘅組合
- 同時用 Ichimoku + EMA system = 部分冗餘
- 選一個就夠

---

## 8. Market Structure（價格結構）

### 核心模式
```
Uptrend:   HH → HL → HH → HL
Downtrend: LH → LL → LH → LL
Break of Structure (BOS): 打破前一個 swing point
```

### 算法偵測
1. 確定 swing high/low（左右各 N bar lookback）
2. 比較新 swing 同前一個同類 swing
3. HH+HL = uptrend；LH+LL = downtrend
4. 突破前一個 HL（uptrend）= BOS = 潛在反轉

### AXC 現有值
- `lookback_support`: 15m=50, 1h/4h=30
- 用嚟偵測 S/R，但未有完整 market structure 偵測

---

## 9. Advanced Concepts

### 9.1 Multi-Timeframe Analysis (MTF)
**三層架構**：
| 角色 | Day Trading | Swing Trading |
|---|---|---|
| 趨勢（最高）| 4H | Daily |
| Setup（中）| 1H | 4H |
| 入場（最低）| 15M | 1H |

**規則**：timeframe 之間 4:1 到 6:1 比例。
**三層 align = 過濾 60-80% 假訊號**。

### 9.2 Mean Reversion vs Trend Following
| 條件 | 策略 | 偵測 |
|---|---|---|
| 低波動 / Range | Mean Reversion | ATR < 20th %ile, ADX < 20 |
| 高波動 / Trending | Trend Following | ATR > 60th %ile, ADX > 25 |
| 極端波動 | 減倉或觀望 | ATR > 90th %ile |

**AXC 已有 mode detection** — 可以深化用 ATR percentile 加強

### 9.3 Volatility Regime Detection
| Regime | ATR %ile | ADX | 策略 |
|---|---|---|---|
| Low Vol | < 20th | < 15 | Range + tight stops |
| Normal | 20-60th | 15-25 | 正常 |
| High Vol | 60-90th | > 25 | Trend + wide stops |
| Extreme | > 90th | > 40 | 減倉 / 只做 breakout |

### 9.4 Indicator Redundancy 完整對照
| 類別 | 重疊指標 | 揀一個 |
|---|---|---|
| Momentum | RSI, STOCH, Williams %R, CCI | RSI |
| 趨勢方向 | SMA, EMA, DEMA, TEMA | EMA |
| 趨勢強度 | ADX, Aroon, BB Width | ADX |
| 波動率 | BB Width, ATR, StdDev | ATR |
| Volume Flow | OBV, A/D, CMF | OBV |
| 混合 | MACD = 趨勢 + 動量 | MACD 或 EMA+RSI |

**Minimum viable set（零冗餘）**：
```
1. EMA   → 方向
2. ADX   → 強度 + mode selector
3. RSI   → 動量
4. ATR   → 波動率 + 止損 + 倉位
5. OBV   → Volume 確認
```
