# AXC 交易系統：從原始數據到落單 — 完整數學流水線
> 最後更新：2026-03-14
> 目的：理解每一粒數據經過幾多重數學公式、幾多層思考，先變成一個交易決定

---

# 總覽：16 步流水線

```
交易所 API（原始價格）
    ↓ Step 1-2
讀狀態 + 攞數據（OHLCV + Ticker + Funding）
    ↓ Step 3
計算 ~20 個指標（RSI, MACD, BB, ATR, ADX, OBV...）
    ↓ Step 4
讀新聞情緒
    ↓ Step 5
6 票投票 → 判斷市場模式（RANGE / TREND / CRASH）
    ↓ Step 6
風控閘門（Volume / Funding / 倉位上限 / 冷卻期）
    ↓ Step 7-8
同步倉位 + 管理現有倉位（Trailing SL / 早出場）
    ↓ Step 9
策略評估 → 產生 Signal（方向 + 強度分數）
    ↓ Step 10
揀最強 Signal（分數排名）
    ↓ Step 11
計算倉位大小 + SL + TP（ATR → 風險金額 → 合約數量）
    ↓ Step 12
落單（Market Order → SL Order → TP Order）
    ↓ Step 13-16
寫狀態 + 記錄 + 發 Telegram
```

每 30 分鐘跑一次。以下逐步拆解每一層嘅數學。

---

# 第一層：原始數據

## 數據來源
- 4 個交易對：BTCUSDT、ETHUSDT、XRPUSDT、XAGUSDT
- 交易所：Aster DEX（主）+ Binance（備）
- 每個交易對攞：
  - **200 條 K 線**（4H + 1H 兩個時間框）
  - **24h Ticker**（價格、24h 變幅、成交量）
  - **Funding Rate**（資金費率、標記價、指數價）

## K 線結構（每條）
```
[timestamp, open, high, low, close, volume]
```

呢啲就係所有數學嘅原材料。200 條 4H K 線 = 800 小時 ≈ 33 日歷史。

---

# 第二層：指標計算（~20 個指標）

每個交易對 × 每個時間框都會計算一次。以下係每個指標嘅**精確數學公式**。

---

## 2.1 RSI — 相對強弱指數

**用途：** 判斷超買/超賣
**週期：** 14

```
第 1 步：計算每條 K 線嘅漲跌
    change = close[t] - close[t-1]
    gain = max(change, 0)
    loss = abs(min(change, 0))

第 2 步：用 RMA（Wilder's 平滑）計算平均漲跌
    avg_gain = RMA(gains, 14)
    avg_loss = RMA(losses, 14)

    RMA 公式：RMA[t] = (RMA[t-1] × (period-1) + value[t]) / period
    （即 α = 1/14 嘅指數移動平均）

第 3 步：
    RS = avg_gain / avg_loss
    RSI = 100 - (100 / (1 + RS))
```

**輸出：** 0-100 嘅數字
- RSI < 30 → 超賣（可能反彈）
- RSI > 70 → 超買（可能回落）
- 系統用 RSI < 32 或 > 68 嚟投「TREND」票

---

## 2.2 MACD — 移動平均匯聚背離

**用途：** 判斷動量方向同強度
**參數：** Fast=12, Slow=26, Signal=9

```
第 1 步：計算兩條 EMA
    EMA_fast = EMA(close, 12)
    EMA_slow = EMA(close, 26)

    EMA 公式：EMA[t] = α × close[t] + (1-α) × EMA[t-1]
    α = 2 / (period + 1)
    所以 EMA(12) 嘅 α = 2/13 ≈ 0.1538
        EMA(26) 嘅 α = 2/27 ≈ 0.0741

第 2 步：MACD 線
    MACD_line = EMA_fast - EMA_slow
    （正數 = 短期動量 > 長期 = 向上）

第 3 步：信號線
    Signal_line = EMA(MACD_line, 9)
    （MACD 線本身嘅 9 期平滑）

第 4 步：柱狀圖
    Histogram = MACD_line - Signal_line
    （正且擴大 = 動量加速向上）
    （負且擴大 = 動量加速向下）
```

**輸出：** MACD_line, Signal_line, Histogram（三個數字）
- Histogram > 0 且 |hist| > |hist_prev| → 動量擴大 → 投「TREND」票
- 係 Trend 策略入面權重最高嘅 Key（你揀 45%）

---

## 2.3 Bollinger Bands — 布林帶

**用途：** 判斷價格相對波動範圍嘅位置
**參數：** Length=20, Multiplier=2

```
第 1 步：中軌（Basis）
    Basis = SMA(close, 20)
    SMA = (close[t] + close[t-1] + ... + close[t-19]) / 20

第 2 步：標準差
    σ = √(Σ(close[i] - Basis)² / 20)    （i = t-19 到 t）

第 3 步：上下軌
    Upper = Basis + 2σ
    Lower = Basis - 2σ

第 4 步：帶寬
    BB_Width = (Upper - Lower) / Basis
```

**輸出：** Upper, Basis, Lower, BB_Width
- BB_Width < 0.008 → 太窄（squeeze，蓄勢待發）→ 唔入場
- BB_Width > 0.05 → 太寬（可能 breakout）→ 唔入場
- Price ≤ Lower × 1.005 → 碰到下軌 → Range LONG 信號

---

## 2.4 ATR — 平均真實波幅

**用途：** 量度波動性，計算 SL 距離
**週期：** 14

```
第 1 步：True Range（每條 K 線）
    TR = max(
        High - Low,              ← 當根 K 線嘅全幅
        |High - Close[t-1]|,     ← 跳空高開
        |Low  - Close[t-1]|      ← 跳空低開
    )

第 2 步：平均
    ATR = RMA(TR, 14)
    （同 RSI 用嘅 RMA 一樣：α = 1/14）
```

**輸出：** 一個美元數字（例如 BTC 嘅 ATR = $485）
- ATR 越大 = 市場越波動 = SL 要設得越遠
- SL 距離 = ATR × 倍數（Range 1.2×, Trend 1.5×, Crash 2.0×）

---

## 2.5 ADX — 平均方向指數

**用途：** 量度趨勢強度（唔理方向）
**週期：** 14

```
第 1 步：方向運動
    +DM = High[t] - High[t-1]    （如果 > 0 且 > -DM）
    -DM = Low[t-1] - Low[t]      （如果 > 0 且 > +DM）

第 2 步：方向指標
    +DI = 100 × RMA(+DM, 14) / ATR(14)
    -DI = 100 × RMA(-DM, 14) / ATR(14)

第 3 步：ADX
    DX = 100 × |+DI - -DI| / (+DI + -DI)
    ADX = RMA(DX, 14)
```

**輸出：** ADX（0-100），+DI，-DI
- ADX < 20-25 → 冇趨勢 → RANGE 市
- ADX > 25 → 有趨勢
- Range 策略閘門：ADX < 25 先畀入場

---

## 2.6 Stochastic — 隨機指標

**用途：** 判斷價格喺近期區間嘅相對位置
**參數：** K=14, K_smooth=1, D_smooth=3

```
第 1 步：Raw %K
    %K_raw = (Close - Min(Low, 14期)) / (Max(High, 14期) - Min(Low, 14期)) × 100

第 2 步：平滑
    %K = SMA(%K_raw, 1)    （K_smooth=1 即冇平滑）
    %D = SMA(%K, 3)        （%K 嘅 3 期移動平均）
```

**輸出：** %K（0-100），%D（0-100）
- %K < 20 → 超賣
- %K > 80 → 超買
- %K 由下穿上 %D → 黃金交叉（Range LONG 加分條件 C4）

---

## 2.7 OBV — 能量潮

**用途：** 用成交量確認價格方向
**冇參數**

```
第 1 步：判斷方向
    if close[t] > close[t-1]:  direction = +1
    if close[t] < close[t-1]:  direction = -1
    if close[t] = close[t-1]:  direction = 0

第 2 步：累加
    OBV[t] = OBV[t-1] + volume[t] × direction

第 3 步：平滑
    OBV_EMA = EMA(OBV, 20)
```

**輸出：** OBV, OBV_EMA
- OBV > OBV_EMA → 量價配合（方向確認）→ 信號加 +0.5 分
- OBV < OBV_EMA → 量價背離（方向懷疑）→ 信號扣 -0.3 分

---

## 2.8 EMA（快/慢）

**參數因時間框而異：**
| 時間框 | Fast | Slow |
|--------|------|------|
| 15m | 8 | 20 |
| 1h | 10 | 30 |
| 4h | 10 | 50 |

公式同上（2.2 入面嘅 EMA 公式）。

---

## 2.9 SMA — MA50 + MA200

```
MA50  = SMA(close, 50)    ← 50 期簡單平均
MA200 = SMA(close, 200)   ← 200 期簡單平均
```

- Trend KEY 1（MA_aligned）：price > MA50 AND price > MA200 → 確認上升趨勢
- 模式投票：price 喺 MA50 同 MA200 之間 → RANGE 票

---

## 2.10 Volume Ratio

```
avg_vol = 最近 30 條 K 線嘅成交量平均值
volume_ratio = 最新一條 K 線成交量 / avg_vol
```

- < 0.5 或 > 1.5 → 投「TREND」票
- ≥ 2.0 → 信號加 +1.0 分
- ≥ 1.5 → 信號加 +0.5 分
- < 0.5 → 唔畀入場（volume gate）

---

## 2.11 VWAP — 成交量加權平均價

```
Typical_Price = (High + Low + Close) / 3
VWAP = Σ(TP × Volume) / Σ(Volume)

帶寬：
    variance = Σ((TP - VWAP)² × Volume) / Σ(Volume)
    std = √(variance)
    VWAP_upper = VWAP + std
    VWAP_lower = VWAP - std
```

---

## 2.12 Rolling Support / Resistance

```
Rolling_High = max(High, 最近 30 條 K 線)
Rolling_Low  = min(Low, 最近 30 條 K 線)
```

- Range 信號 C3：Price ≤ Rolling_Low × 1.005 → 接近支撐

---

## 指標計算總結

每次 cycle，每個交易對 × 2 個時間框 = **8 組指標**。
每組約 **20 個數值**。
總共 **~160 個數字**從 800 條原始 K 線計算出嚟。

---

# 第三層：市場模式偵測（6 票投票）

用 4H 數據，6 個投票函數各投 RANGE 或 TREND。

```
投票 1 — RSI：
    RSI < 32 或 > 68 → TREND
    32-68 → RANGE

投票 2 — MACD：
    |histogram| > |histogram_prev| AND |histogram| > 0.001 → TREND
    否則 → RANGE

投票 3 — Volume：
    volume_ratio < 0.5 或 > 1.5 → TREND
    0.5-1.5 → RANGE

投票 4 — MA 位置：
    price > max(MA50, MA200) 或 price < min(MA50, MA200) → TREND
    price 喺 MA50 同 MA200 之間 → RANGE

投票 5 — Funding Rate：
    |funding| > 0.07% → TREND
    ≤ 0.07% → RANGE

投票 6 — HMM（隱馬爾可夫模型，可選）：
    HMM 判斷 regime + confidence → 對應票

結果：
    ≥ 4/6 投 TREND → 市場模式 = TREND
    ≥ 4/6 投 RANGE → 市場模式 = RANGE
    平手 → 維持上一次嘅模式

特殊：HMM 判斷 CRASH + confidence ≥ 70% → 直接覆蓋為 CRASH
```

---

# 第四層：風控閘門（入場前）

通過模式偵測後，仲要過以下閘門：

| 閘門 | 條件 | 結果 |
|------|------|------|
| Volume | volume_ratio < 0.5 | 唔入場 |
| Funding | \|funding\| > 0.2% | 封鎖該交易對 |
| 倉位上限 | 同組已有倉 | 唔入場 |
| 冷卻期 | 連續虧損後 | 等 30min-2hr |
| 新聞情緒 | bearish confidence > 70% | 封鎖 LONG |

---

# 第五層：策略評估 → 產生 Signal

市場模式決定用邊個策略。每個策略有自己嘅入場條件。

---

## 5.1 RANGE 策略（均值回歸）

**前提條件：**
- R0：BB_Width 喺 0.008-0.05 之間（唔太窄唔太寬）
- R1：ADX < 25（冇強趨勢）

**LONG 入場（要 3-4 個條件）：**
```
C1：Price ≤ BB_Lower × (1 + 0.5%)        ← 碰到布林帶下軌
C2：RSI < 40 AND RSI > RSI_prev          ← 超賣且開始回升
C3：Price ≤ Rolling_Low × (1 + 0.5%)     ← 接近支撐
C4（可選）：Stoch %K < 20 AND %K > %D    ← 超賣 + 黃金交叉
```

- C1+C2+C3+C4 全過 → **STRONG**（base 4.0 分）
- C1+C2+C3 過、C4 冇過 → **WEAK**（base 3.0 分）

**SHORT 入場（鏡像）：**
- Price ≥ BB_Upper × (1 - 0.5%)
- RSI > 60 AND RSI < RSI_prev
- Price ≥ Rolling_High × (1 - 0.5%)
- Stoch %K > 80 AND %K < %D

---

## 5.2 TREND 策略（順勢回調）

**前提條件：**
- 市場模式 = TREND
- 4H 價格波幅 ≥ TREND_MIN_CHANGE_PCT
- Volume Ratio ≥ ENTRY_VOLUME_MIN

**LONG 入場（4 Key 投票）：**
```
KEY 1 — MA_aligned（4H）：
    price > MA50_4H AND price > MA200_4H
    （價格喺兩條均線之上 = 上升趨勢結構）

KEY 2 — MACD_bullish（4H）：
    histogram > 0 AND |histogram| > |histogram_prev|
    （動量正面且加速中）

KEY 3 — RSI_pullback（1H）：
    40 ≤ RSI_1H ≤ 55
    （唔係超買，係回調區 — 趨勢嘅甜蜜入場點）

KEY 4 — Price_at_MA（1H）：
    |price - MA50_1H| / MA50_1H < 1.5%
    （價格接近 1H MA50 — 回調到支撐位）
```

- 4/4 pass → **STRONG**（base 5.0 分）
- 3/4 pass（Day-of-week bias 放寬）→ **BIAS**（base 3.5 分）

**SHORT 入場（鏡像）：**
- MA_aligned：price < MA50 AND price < MA200
- MACD_bearish：histogram < 0 且擴大
- RSI_bounce：45 ≤ RSI ≤ 60
- Price_at_MA：同上

**Day-of-week Bias：**
- Thu 21:00-Fri 01:00 UTC+8 → SHORT bias → SHORT 只需 3/4
- Fri 21:00-Sat 03:00 UTC+8 → LONG bias → LONG 只需 3/4

---

## 5.3 CRASH 策略（只做 SHORT）

**入場（2/3 條件）：**
```
條件 1：RSI > 60（反彈嚟到盡頭）
條件 2：MACD histogram < 0（空頭動量）
條件 3：volume_ratio > 1.5（恐慌性拋售量）
```

**分數：**
```
base = 3.0
+ 0.5  if RSI > 80
+ 0.5  if volume_ratio > 3.0
+ 0.5  if |histogram| > 0.01
= 最高 4.5
```

---

## 5.4 信號分數公式

所有策略共用嘅加分/減分：

```
final_score = base_score × volume_multiplier + OBV_adjustment

volume_multiplier = clamp(1.0 + 0.3 × (volume_ratio - 1.0), 0.7, 1.5)
    例：volume_ratio = 2.0 → mult = 1.3
    例：volume_ratio = 0.5 → mult = 0.85

OBV_adjustment:
    OBV > OBV_EMA（量價配合）→ +0.5
    OBV < OBV_EMA（量價背離）→ -0.3
    OBV_adj × min(volume_ratio, 1.0)
```

---

# 第六層：Signal 排名

如果多個交易對都有 Signal，揀分數最高嘅：
```
排名：分數最高贏
平手：BTC > ETH/SOL > XRP/POL > XAG/XAU
```

每次 cycle 最多只會有 **1 個** Signal 進入下一步。

---

# 第七層：倉位計算（SL → TP → Size）

呢一層係最多數學嘅地方。

---

## 7.1 Stop Loss（止損）

```
SL 距離 = ATR(4H) × SL 倍數

SL 倍數（按策略）：
    RANGE: 1.2×
    TREND: 1.5×
    CRASH: 2.0×

    交易對特殊覆蓋：例如 XRP 用 1.0×

SL 價格：
    LONG:  SL = entry_price - SL 距離
    SHORT: SL = entry_price + SL 距離
```

**例子：** BTC ATR = $485, TREND 策略
```
SL 距離 = $485 × 1.5 = $727.50
Entry = $45,230
SL = $45,230 - $727.50 = $44,502.50
```

**Conformal Prediction 修正（可選）：**
```
如果開啟 CP：
    atr_for_sl = ATR + q_hat
    q_hat = 近期 ATR 預測誤差嘅 90th percentile
    → SL 會設得更遠，更保守
```

---

## 7.2 Take Profit（止盈）

### Range TP
```
TP1 = BB_Basis（布林帶中軌）   ← 平倉 50%
TP2 = BB 對面嘅軌（上軌/下軌） ← 平倉剩餘 50%
Fallback: entry ± (SL 距離 × 2.3)
```

### Trend TP
```
TP1 = 下一個 S/R 水平（從 SCAN_CONFIG 讀）
      如果冇 S/R → entry ± (SL 距離 × 3.0)
最低 R:R = 3.0（即 TP 至少要係 SL 嘅 3 倍遠）
```

### Crash TP
```
TP1 = entry - (ATR × 3.0)    ← 只做 SHORT
最低 R:R = 1.5
```

### Funding 成本修正
```
如果 Funding Rate 唔利：
    （LONG + 正 funding 或 SHORT + 負 funding）

    periods = {range: 3, trend: 6, crash: 2}
    total_funding_pct = |funding_rate| × periods
    funding_impact = entry_price × total_funding_pct

    LONG: TP ↑ （加上 funding 成本）
    SHORT: TP ↓ （扣除 funding 成本）
```

---

## 7.3 R:R 驗證

```
R:R = |TP - entry| / |SL - entry|

最低要求：
    RANGE: R:R ≥ 2.3
    TREND: R:R ≥ 3.0
    CRASH: R:R ≥ 1.5

如果 R:R 唔達標 → Signal 被拒絕，唔入場
```

---

## 7.4 Position Sizing（倉位大小）

呢度係成個系統最核心嘅計算：

```
第 1 步：基礎風險
    base_risk = 策略風險百分比
        RANGE: 2%
        TREND: 2%
        CRASH: 1%

第 2 步：信心調整
    if signal_score ≥ 4.5:
        adjusted_risk = base_risk × 1.25    ← 高信心加碼 25%
    elif signal_score ≥ 3.0:
        adjusted_risk = base_risk × 1.0     ← 正常
    else:
        adjusted_risk = base_risk × 0.6     ← 低信心減碼 40%

    adjusted_risk = min(adjusted_risk, 3%)  ← 絕對上限

第 3 步：HMM 信心修正（如開啟）
    adjusted_risk × = HMM_confidence
    （例：HMM 80% 信心 → 風險再打 8 折）

第 4 步：連續虧損懲罰
    if consecutive_losses > 0:
        risk_amount = balance × adjusted_risk × 0.7
        （減碼 30%）
    else:
        risk_amount = balance × adjusted_risk

第 5 步：計算合約數量
    sl_pct = SL 距離 / entry_price
    position_notional = risk_amount / sl_pct
    position_size = position_notional / entry_price
    margin_required = position_notional / leverage
```

**例子：** BTC TREND STRONG，餘額 $10,000
```
base_risk = 2%
signal_score = 5.5 → adjusted_risk = 2% × 1.25 = 2.5%
冇連續虧損
risk_amount = $10,000 × 2.5% = $250

SL 距離 = $727.50
entry = $45,230
sl_pct = $727.50 / $45,230 = 1.608%

position_notional = $250 / 0.01608 = $15,547
position_size = $15,547 / $45,230 = 0.3437 BTC
margin_required = $15,547 / 7（leverage）= $2,221

即：用 $2,221 保證金，7 倍槓桿，買 0.34 BTC。
如果蝕 $727.50（1.6%），最多蝕 $250（餘額嘅 2.5%）。
```

---

## 7.5 Leverage（槓桿）

固定，按策略分：
| 策略 | 槓桿 | Profile 覆蓋 |
|------|------|------------|
| RANGE | 8× | Conservative: 5×, Aggressive: 10× |
| TREND | 7× | Conservative: 3×, Aggressive: 8× |
| CRASH | 5× | — |

---

# 第八層：落單

```
執行順序（30 秒限時）：

1. 設定保證金模式 → ISOLATED（隔離）
2. 設定槓桿
3. Market Order 入場
    → 記錄 fill_price, fill_qty, commission
4. 計算滑點
    LONG:  slippage = (fill_price - signal_price) / signal_price
    SHORT: slippage = (signal_price - fill_price) / signal_price
    |slippage| > 0.5% → 警告
5. Stop Loss Order（reduce_only）
    ⚠️ 如果失敗 → 立即 Market Close（保護措施）
6. TP1 Order（平一半倉）
7. TP2 Order（平剩餘，Range only）
```

---

# 第九層：風險管理（持倉後）

| 規則 | 門檻 | 動作 |
|------|------|------|
| 單筆虧損 | 25% | 即時平倉 |
| 日虧損 | 20% | 停止所有交易 |
| 連續 2 次虧損 | — | 冷卻 30 分鐘 |
| 連續 3 次虧損 | — | 冷卻 2 小時 |
| 最長持倉 | 72 小時 | 強制平倉 |
| 極端 funding | ±0.2% | 封鎖交易對 |

**倉位分組限制：**
```
crypto_correlated: [BTC, ETH, SOL] → 最多 1 倉
crypto_independent: [XRP, POL]     → 最多 1 倉
commodity: [XAG, XAU]              → 最多 1 倉
全部加埋：最多 2 crypto + 1 commodity = 3 倉
```

---

# 總結：一個 Signal 經過幾多步？

```
原始 K 線（200 條 × 6 個數字 = 1,200 個原始數據點）
    ↓
20 個指標計算（RSI, MACD, BB, ATR, ADX, Stoch, OBV, EMA, MA, VWAP...）
    ↓
160 個中間數值
    ↓
6 個投票函數 → 1 個市場模式
    ↓
5 個風控閘門
    ↓
3-4 個入場條件 → 1 個 pass/fail + 基礎分數
    ↓
3 個加減分（Volume, OBV, Reentry）→ 最終分數
    ↓
4 個交易對排名 → 1 個最強 Signal
    ↓
ATR → SL 距離 → SL 價格
    ↓
BB/SR/ATR → TP 價格 → Funding 修正
    ↓
R:R 驗證（唔合格 → 拒絕）
    ↓
Balance × Risk% × Confidence × HMM × Loss penalty → Risk Amount
    ↓
Risk Amount / SL% → Notional → Quantity
    ↓
Market Order → SL Order → TP Order
```

**從 1,200 個原始數據點 → 1 個交易決定，中間經過大約 15 層數學轉換。**
