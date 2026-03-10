# Step 1: `config/params.py` — 設定面板（遙控器）
> talk12 風格分析 | 2026-03-10

---

## Section 1: 掃描設定（Line 29-47）

**比喻：** 你有 9 個朋友分散喺唔同嘅街市，幫你睇邊度啲蘋果平。

```python
EXCHANGE_ROTATION = ["aster", "binance", "hyperliquid", "bybit", "okx", "kucoin", "gate", "mexc", "bitget"]
```

每 20 秒打電話畀一個朋友問價。9 個朋友輪住問，所以每個朋友大約 3 分鐘先被問一次。

**記住：** 加新交易所 = 加新朋友入 list。但問一圈嘅時間會變長。

---

## Section 2: BB 指標（Line 49-54）

**比喻：** Bollinger Band 就好似一條橡筋纏住價格。BB_TOUCH_TOL = 你幾近條橡筋先算「掂到」。

- `BB_TOUCH_TOL_DEFAULT = 0.005` → 距離 0.5% 就算掂到
- `BB_TOUCH_TOL_XRP = 0.008` → XRP 波動大啲，畀多少少空間（0.8%）
- `BB_WIDTH_MIN = 0.05` → 橡筋太窄（< 5%）= 市場瞓咗覺，唔交易

---

## Section 3: 指標時間框（Line 57-111）

**比喻：** 你可以用望遠鏡（4h）、眼鏡（1h）、放大鏡（15m）睇市場。每個鏡頭有自己嘅設定。

```python
TIMEFRAME_PARAMS = {
    "15m": { "ema_fast": 8,  "ema_slow": 20 },   # 放大鏡：反應最快
    "1h":  { "ema_fast": 10, "ema_slow": 30 },   # 眼鏡：主力交易用
    "4h":  { "ema_fast": 10, "ema_slow": 50 },   # 望遠鏡：判斷大方向
}
```

重點數字：
| 參數 | 意思 | 影響 |
|------|------|------|
| `rsi_long / rsi_short` | RSI 低過 = 超賣（買），高過 = 超買（賣） | 入場時機 |
| `adx_range_max` | ADX 低過呢個數 = 市場冇方向 = RANGE mode | 模式判斷 |
| `lookback_support` | 翻查幾多根蠟燭搵支撐/阻力位 | S/R 準確度 |

MACD / Stochastic / OBV 參數都喺呢度設（Line 94-110），全部畀 `indicator_calc.py` 讀。

---

## Section 4: Trend 策略參數（Line 112-123）

```python
TREND_RSI_LONG_LOW = 40       # LONG: RSI 最少要 40
TREND_RSI_LONG_HIGH = 55      # LONG: RSI 最多 55
PULLBACK_TOLERANCE = 0.015    # 價格距 MA50 1.5% 先算回調
```

**意思：** 追趨勢時，RSI 唔可以太低（冇力）也唔可以太高（已經衝太遠）。要 40-55 之間先入場做 LONG。

---

## Section 5: 模式偵測（Line 125-133）

```python
MODE_RSI_TREND_LOW = 32       # RSI < 32 = 有趨勢
MODE_RSI_TREND_HIGH = 68      # RSI > 68 = 有趨勢
MODE_CONFIRMATION_REQUIRED = 2 # 連續 2 次同 mode 先切換
```

**比喻：** 判斷而家係「橫行市」定「趨勢市」。要連續問 2 次都話趨勢，先相信真係趨勢。避免一時衝動。

---

## Section 6: 倉位管理（Line 135-140）

```python
MAX_POSITION_SIZE_USDT = 50    # 單倉最大 $50
MAX_OPEN_POSITIONS = 3         # 最多同時 3 盤
RISK_PER_TRADE_PCT = 0.02      # 每次冒 2% 本金風險
```

⚠️ 呢啲值會被 Section 7 嘅 Profile 覆蓋。

---

## Section 7: 三種打法 Profiles（Line 142-196）⭐ 最重要

**比喻：** 打機揀難度。

| Profile | 好似 | 每次賭幾多 | SL 距離 | 最少贏幾倍先入 | 最多同時開幾盤 | 追唔追趨勢 |
|---------|------|-----------|---------|--------------|--------------|-----------|
| CONSERVATIVE | Easy | 1% 本金 | 1.5×ATR | Range 2.3, Trend 3.0 | 1 盤 | 唔追 |
| BALANCED | Normal | 2% 本金 | 1.2×ATR | Range 2.3, Trend 3.0 | 2 盤 | 追（要 5% 變動） |
| AGGRESSIVE | Hard | 3% 本金 | 1.0×ATR | Range 2.0, Trend 2.5 | 3 盤 | 追（要 2% 變動） |

**而家揀咗：`ACTIVE_PROFILE = "AGGRESSIVE"`**

Profile 點影響系統：
1. `settings.py` 啟動時讀 `TRADING_PROFILES[ACTIVE_PROFILE]`
2. 將 `risk_per_trade_pct` 覆蓋 `RANGE_RISK_PCT` + `TREND_RISK_PCT`
3. 將 `sl_atr_mult` 覆蓋 `RANGE_SL_ATR_MULT` + `TREND_SL_ATR_MULT`
4. 將 `max_open_positions` 覆蓋 `MAX_CRYPTO_POSITIONS`

---

## Section 8: 幣種清單（Line 198-236）

**比喻：** 你喺邊間街市買邊種生果。

| 交易所 | 監察啲咩 |
|--------|---------|
| Aster | BTC, ETH, XRP, XAG, XAU |
| Binance | BTC, ETH, SOL |
| HyperLiquid | BTC, ETH, SOL |

⚠️ 改咗呢度之後要**重啟 scanner** 先生效。

---

## Section 9: user_params.py 覆蓋（Line 248-262）

**比喻：** 你有一張「VIP 卡」，可以覆蓋任何設定。

如果 `config/user_params.py` 存在，入面嘅值會蓋過 params.py 所有同名變數。
好處：`git pull` 更新代碼時唔會衝突，因為 user_params.py 係 gitignored。

---

## 自檢問題

1. **AGGRESSIVE 係咪你想要嘅？** → 每次用 3% 本金，$10,000 本 = 每單 $300 風險
2. **BB_TOUCH_TOL 夠唔夠鬆？** → 太嚴 = 錯過機會，太鬆 = 假信號多
3. **ASTER_SYMBOLS 有 XAU 但 BINANCE_SYMBOLS 冇** → XAU 只能喺 Aster 交易
4. **MODE_CONFIRMATION_REQUIRED = 2** → 會唔會太慢反應？（連續 2 次 = 8 小時先切換 mode）
