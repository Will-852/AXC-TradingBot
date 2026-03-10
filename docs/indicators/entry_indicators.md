# 入場指標深度研究
> 更新：2026-03-10
> 涵蓋：BB、RSI、MACD、STOCH、EMA、ADX

### talk16 — 呢個文件係咩
你做交易嘅時候，要決定「幾時入場」。呢六個指標就係六把尺，各自量唔同嘅嘢：價格去到邊（BB）、買過頭未（RSI）、趨勢變緊未（MACD）、位置高定低（STOCH）、大方向係咩（EMA）、趨勢有幾強（ADX）。每把尺嘅最佳刻度（參數）喺唔同市場唔同，呢度列晒 crypto 嘅建議值，同 AXC 而家用緊嘅值逐個比較。重點係：六把裡面有啲量嘅嘢重疊（例如 RSI 同 STOCH 都量動力），唔好全部塞入去，揀代表就夠。

---

## 1. Bollinger Bands (BB)

### 核心概念
- 中線 = SMA(n)，上下帶 = SMA ± k × StdDev
- 衡量：價格相對波動率嘅位置
- 95% 時間價格喺 2σ 帶內

### Crypto 最佳參數
| Timeframe | bb_length | bb_mult | 備註 |
|---|---|---|---|
| 15m | 20 | 2.0 | 標準，可考慮 2.0-2.5 |
| 1h | 20 | 2.0 | 標準 |
| 4h | 20 | 2.0-2.5 | crypto 波動大，2.5 減少假訊號 |

### AXC 現有值 vs 建議
- `bb_length=20, bb_mult=2` — 標準值，合理
- `BB_TOUCH_TOL_DEFAULT=0.005` — 0.5% 容忍度，合理
- `BB_TOUCH_TOL_XRP=0.008` — XRP 波動較大，正確做法
- `BB_WIDTH_MIN=0.05` — 固定閾值。**建議改進**：用相對閾值（同 125-bar BB Width 低位比較），因為唔同幣種基礎波動率唔同

### 進階用法
1. **BB Squeeze 偵測**：BB Width 跌到 125-bar 低位 = 突破即將來臨
2. **BB/Keltner Squeeze**：BB 收窄到 Keltner Channel 入面 = 更強嘅 squeeze 訊號。BB 突破 Keltner = squeeze 發射
3. **BB %B 指標**：%B = (Price - Lower) / (Upper - Lower)。%B > 1 = 突破上帶；%B < 0 = 突破下帶
4. **BB Width 作為環境指標**：低 Width = range 市；高 Width = trend 市

### 常見錯誤
- ❌ 觸碰上帶就做空 — 趨勢市會沿住帶 walk
- ❌ 固定 BB_WIDTH_MIN — 唔同幣種需要唔同閾值
- ✅ BB 配合 RSI/volume 確認先入場

---

## 2. RSI (Relative Strength Index)

### 核心概念
- RSI = 100 - (100 / (1 + RS))，RS = Avg Gain / Avg Loss
- 衡量：近期升跌嘅相對強度
- 範圍：0-100

### Crypto 最佳參數
| Timeframe | Period | Overbought | Oversold | 備註 |
|---|---|---|---|---|
| 15m | 14 | 70 | 30 | 標準 |
| 1h | 14 | 65 | 35 | crypto 波動大，收窄範圍減少假訊號 |
| 4h | 14 | 65 | 35 | 同上 |
| Daily | 14 | 70 | 30 | 可以用標準值 |

### AXC 現有值 vs 建議
- 15m: `rsi_long=30, rsi_short=70` — 標準，OK
- 1h/4h: `rsi_long=35, rsi_short=65` — **正確！已經調整咗**
- RSI period 全部 14 — 標準，合理

### 進階用法
1. **RSI Divergence（背馳）**：
   - Regular Divergence：價格新高但 RSI 冇新高 = 動力衰竭，可能反轉
   - **Hidden Divergence**：價格 higher low 但 RSI lower low = 趨勢延續訊號。研究指比 regular divergence 可靠約 14%（需 backtest 驗證）
2. **RSI 50 線**：RSI > 50 = 多頭控制；RSI < 50 = 空頭控制。簡單但有效嘅趨勢過濾器
3. **動態 OB/OS**：根據波動率調整。高波動時 OB=75/OS=25；低波動時 OB=65/OS=35
4. **RSI + Volume**：Volume-Weighted RSI 過濾低量假訊號

### 常見錯誤
- ❌ RSI 超買就做空 — 強勢趨勢 RSI 可以長期停留 70+
- ❌ 只用 RSI 單一指標 — 需要趨勢確認
- ✅ RSI 配合趨勢方向用（趨勢向上只做 oversold 買入）

---

## 3. MACD (Moving Average Convergence Divergence)

### 核心概念
- MACD Line = EMA(fast) - EMA(slow)
- Signal Line = EMA(MACD Line, signal period)
- Histogram = MACD Line - Signal Line
- 衡量：兩條 EMA 之間嘅動態關係

### Crypto 最佳參數
| Timeframe | Fast | Slow | Signal | 備註 |
|---|---|---|---|---|
| 15m | **8** | **17** | 9 | 比標準 12-26 更快，適合短線 |
| 1h | 12 | 26 | 9 | 標準值 OK |
| 4h | 12 | 26 | 9 | 標準值 OK |

### AXC 現有值 vs 建議
- `MACD_FAST=12, MACD_SLOW=26, MACD_SIGNAL=9` — 標準值
- **建議**：15m timeframe 可以試 8-17-9（研究顯示 crypto 短線更適合快參數）
- 但全局改唔好，建議 per-timeframe MACD 參數

### 進階用法
1. **Histogram 分析**：
   - Histogram 由負轉正 = bullish momentum 開始
   - Histogram 縮小（趨近零）= 動力衰退，可能交叉
   - Histogram 嘅斜率比絕對值更重要
2. **Zero-line crossover**：MACD 穿越零線 = 較強嘅趨勢確認（但較慢）
3. **MACD Divergence**：同 RSI divergence 概念一樣，但用 MACD
4. **雙重確認**：Signal crossover + histogram 方向一致 = 較強訊號

### 常見錯誤
- ❌ MACD 喺 range 市頻繁交叉 = 大量假訊號
- ❌ 唔理 ADX 直接用 MACD — ADX < 20 時 MACD 訊號可靠度大跌
- ✅ 先確認趨勢存在（ADX > 20-25）先用 MACD

### 冗餘問題
⚠️ MACD 本質上係 EMA crossover + momentum 嘅混合體。同時用 MACD + EMA crossover + RSI 有冗餘風險。

---

## 4. Stochastic Oscillator (STOCH)

### 核心概念
- %K = (Close - Lowest Low) / (Highest High - Lowest Low) × 100
- %D = SMA(%K, d_smooth)
- 衡量：收盤價喺近期高低範圍嘅相對位置
- 範圍：0-100

### Crypto 最佳參數
| 參數 | 值 | 備註 |
|---|---|---|
| K Period | 14 | 標準 |
| K Smooth | 1 | Fast Stochastic（唔平滑） |
| D Smooth | 3 | 標準 |
| Oversold | 20 | 標準 |
| Overbought | 80 | 標準 |

### AXC 現有值 vs 建議
- 所有參數標準，合理
- **但有冗餘問題**：Stochastic 同 RSI 高度相關，兩者都係 momentum oscillator

### 進階用法
1. **%K/%D Crossover**：%K 上穿 %D 喺 oversold zone = 買入訊號
2. **Stochastic RSI**：將 RSI 值代入 Stochastic 公式，sensitivity 更高
3. **Bull/Bear Setup**：%D 形成 double bottom 喺 oversold zone = 強 bullish

### ⚠️ 冗餘評估
- RSI 同 STOCH 量度嘅嘢高度重疊（都係 momentum/overbought-oversold）
- **建議**：揀一個就夠。RSI 更通用，STOCH 喺 range 市稍好
- 如果保留 STOCH，建議唔好同 RSI 同時投票（等於一個觀點投兩票）
- **替代方案**：用 STOCH 嘅 slot 改為 volume indicator（OBV 或 VW-RSI），填補系統冇 volume 確認嘅盲點

---

## 5. EMA (Exponential Moving Average)

### 核心概念
- EMA = Price × k + EMA_prev × (1-k)，k = 2/(n+1)
- 比 SMA 對近期價格更敏感
- 衡量：趨勢方向

### Crypto 最佳組合
| Timeframe | Fast EMA | Slow EMA | 用途 |
|---|---|---|---|
| 15m | 8 | 20-21 | 短線入場 |
| 1h | 10 | 30 | 中線趨勢 |
| 4h | 10 | 50 | 主趨勢（AXC 主 timeframe） |
| Daily | 21 | 200 | 大方向 |

### AXC 現有值 vs 建議
- 15m: `ema_fast=8, ema_slow=20` — 合理
- 1h: `ema_fast=10, ema_slow=30` — 合理
- 4h: `ema_fast=10, ema_slow=50` — 合理
- **整體 OK，唔需要改**

### 進階用法
1. **Multi-TF EMA Alignment**：
   - 4h EMA200 定大方向
   - 1h EMA50 定趨勢
   - 15m EMA21 入場
   - 三者 align = 高勝率 setup
2. **EMA 作為動態 S/R**：價格回到 EMA 彈起 = pullback entry
3. **Death Cross / Golden Cross**：
   - 50 EMA 上穿 200 EMA = Golden Cross（bullish）
   - 50 EMA 下穿 200 EMA = Death Cross（bearish）
   - Crypto 可靠度中等 — 因為極端波動會造成假 cross
4. **EMA Ribbon**：多條 EMA（8, 13, 21, 34, 55）展開 = 強趨勢；糾結 = 無趨勢

### 常見錯誤
- ❌ Golden/Death Cross 喺 range 市假訊號多
- ❌ 單靠 EMA cross 入場 — 需要 volume + momentum 確認
- ✅ EMA 主要用嚟定方向，唔係精確入場點

---

## 6. ADX (Average Directional Index)

### 核心概念
- ADX = Smoothed(|+DI - -DI| / (+DI + -DI))
- +DI = 正向方向指標（上升動力）
- -DI = 負向方向指標（下跌動力）
- ADX 只量度趨勢強度，唔分方向
- 範圍：0-100

### Crypto 最佳參數
| Timeframe | Period | Range Max | 備註 |
|---|---|---|---|
| 15m | 14 | 20 | 標準 |
| 1h | 14 | 20 | 標準 |
| 4h | 14 | 18 | AXC 已調低，合理 |

### AXC 現有值 vs 建議
- `adx_period=14` — 標準，OK
- `adx_range_max`: 15m/1h=20, 4h=18 — 合理
- **ADX 係 AXC 最有價值嘅指標之一**

### ADX 等級解讀
| ADX 值 | 含義 | 策略 |
|---|---|---|
| 0-15 | 極弱趨勢/無趨勢 | 唔交易 或 純 range 策略 |
| 15-20 | 弱趨勢 | Range 策略（BB bounce） |
| 20-25 | 趨勢開始 | 準備 trend 策略 |
| 25-40 | 強趨勢 | Trend following（EMA/MACD） |
| 40-50 | 極強趨勢 | 繼續持倉，唔好反向 |
| 50+ | 過熱 | 趨勢可能即將結束 |

### 🌟 最高價值用法：Strategy Mode Selector
**ADX 做策略選擇器係最高價值嘅整合方式：**
- ADX < 20 → 用 BB/RSI/STOCH mean reversion 策略
- ADX > 25 → 用 EMA/MACD trend following 策略
- 呢個單一 filter 可以消除大部分其他指標嘅 whipsaw 損失

### +DI / -DI 用法
- +DI > -DI = 多頭力量較強
- -DI > +DI = 空頭力量較強
- +DI 上穿 -DI（ADX > 20）= bullish crossover
- 但 DI crossover 較慢，唔係精確入場訊號

### 常見錯誤
- ❌ ADX 升 = 價格升（ADX 唔分方向！）
- ❌ ADX 跌就停止交易 — ADX 跌只表示趨勢減弱，唔等於反轉
- ✅ ADX 主要做 filter，唔做 trigger

---

## 指標冗餘總結

| 類別 | 重疊指標 | AXC 應該揀 |
|---|---|---|
| Momentum | RSI、STOCH、(MACD 部分) | RSI（最通用）|
| 趨勢方向 | EMA、(MACD 部分) | EMA |
| 趨勢強度 | ADX、BB Width | ADX |
| 波動率 | BB Width、ATR | ATR（position sizing 用）|
| Volume | ⚠️ 冇 | **盲點！加 OBV 或 VW-RSI** |

### 最低冗餘組合建議
```
1. EMA crossover  → 趨勢方向
2. ADX            → 趨勢強度 + 策略選擇器
3. RSI            → Momentum + OB/OS
4. ATR            → 止損 + 倉位 + 波動率偵測
5. OBV 或 VW-RSI  → Volume 確認
```
BB 保留做 range 策略嘅核心，但喺 trend 模式下降權。
STOCH 可以考慮移除（同 RSI 冗餘），slot 俾 volume 指標。
