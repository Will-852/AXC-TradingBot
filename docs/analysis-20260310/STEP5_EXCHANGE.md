# Step 5: `scripts/trader_cycle/exchange/` — 交易所連接（外交部）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → scripts → trader_cycle → exchange/
├── market_data.py          ← 拉價格 + 計指標（Step 4-5）
├── execute_trade.py        ← 落單！7 步執行序列（Step 12）
├── aster_client.py         ← Aster DEX API（主交易所）15KB
├── binance_client.py       ← Binance Futures API 14KB
├── hyperliquid_client.py   ← HyperLiquid API 20KB
├── position_sync.py        ← 持倉同步 + 孤兒偵測（Step 7）15KB
└── exceptions.py           ← 錯誤分類 1KB
```

---

## 1. `market_data.py` — 情報員（Step 4 + 5）

### FetchMarketDataStep（Step 4）
對每個 pair（BTC, ETH, XRP, SOL, XAG, XAU）從 Aster API 拉：
- 24H ticker：價格、變動%、成交量
- Funding rate + mark price + index price

### CalcIndicatorsStep（Step 5）
對每個 pair 嘅 4H + 1H timeframe：
1. `fetch_klines(symbol, timeframe, 200)` — 拉 200 根蠟燭
2. `calc_indicators(df, params)` — 計 RSI/BB/MACD/ATR/MA/OBV
3. 額外計 `volume_ratio` = 最新 1 根 volume ÷ 最近 30 根平均

**容錯：** 全部 pair 都 fail → `RecoverableError`（唔 crash，等下個 cycle 重試）

---

## 2. `execute_trade.py` — 執行官（Step 12）⭐

**比喻：** 7 步拆彈流程。每步有安全網。

### 7 步落單序列（30 秒內完成）

```
① set_margin_mode("ISOLATED")     ← 獨立保證金（唔牽連其他倉）
② set_leverage(7x/8x)              ← 設槓桿
③ create_market_order(BUY/SELL)     ← 市價入場
④ verify fill (qty > 0?)            ← 驗證成交
⑤ create_stop_market(SL)           ← 止損單 ⚠️ CRITICAL
⑥ create_take_profit_market(TP)    ← 止盈單
⑦ update TRADE_STATE + trade log   ← 寫入白板
```

### 安全規則

| 情況 | 處理 | 原因 |
|------|------|------|
| SL 落單失敗 | 即刻市價平倉 | 冇 SL 嘅倉位唔可以存在 |
| TP 落單失敗 | 記 warning，保留倉位 | SL 保護緊，TP 唔急 |
| AuthenticationError | CriticalError → 停 pipeline | API key 問題要人手處理 |
| InsufficientFunds | 記 warning，取消信號 | 錢唔夠 |
| DRY_RUN | 只 log，唔執行 | 模擬模式 |

### Range 策略 TP 分拆
```
如果有 TP1 + TP2（Range 策略）：
  TP1 qty = fill_qty ÷ 2      → 一半倉位喺 BB 中線平
  TP2 qty = fill_qty - TP1     → 另一半喺對面 BB band 平
```

---

## 3. `aster_client.py` — 主交易所驅動

### 初始化流程
```
AsterClient.__init__():
  1. _load_credentials() → 讀 .env（ASTER_API_KEY + ASTER_API_SECRET）
  2. _sync_time()        → 同 Aster 伺服器校正時間（防 timestamp reject）
  3. _validate_connection() → 試一次 get_account_balance()（確認連得到）
```

### 認證
- HMAC-SHA256 簽名
- 每個 request 加 `timestamp` + `recvWindow`(10s)
- Header: `X-MBX-APIKEY`

### 重試策略（Quadratic Backoff）
```
失敗 → 1s → 4s → 9s → 16s → 25s（n² 遞增）
最多 5 次

重試嘅錯誤：429 rate limit、500+ server error
唔重試嘅錯誤：餘額不足、無效 order、auth error
Auth error → 直接升級 CriticalError → 停 pipeline
```

### 精度驗證
```
每個 order 之前：
  1. validate_symbol_precision(symbol) → 攞 tickSize + stepSize
  2. _round_to_precision(qty, stepSize) → 四捨五入到正確精度
  防止因為精度問題被交易所 reject
```

### API 方法一覽
| 方法 | 類型 | 用途 |
|------|------|------|
| `create_market_order` | POST | 市價入場/平倉 |
| `create_stop_market` | POST | 止損（STOP_MARKET, reduceOnly） |
| `create_take_profit_market` | POST | 止盈（TAKE_PROFIT_MARKET, reduceOnly） |
| `cancel_order` | DELETE | 取消訂單 |
| `get_positions` | GET | 查倉位（過濾 amt≠0） |
| `get_account_balance` | GET | 查餘額 |
| `get_usdt_balance` | GET | USDT 可用餘額（便捷方法） |
| `get_open_orders` | GET | 未成交訂單 |
| `get_income` | GET | 已實現 PnL + funding 記錄 |
| `close_position_market` | 組合 | 讀倉位 → 反向 reduceOnly 市價單 |
| `set_margin_mode` | POST | 設 ISOLATED 模式 |
| `set_leverage` | POST | 設槓桿倍數 |

---

## 4. `binance_client.py` + `hyperliquid_client.py`

同 Aster 一樣嘅 interface，但連接唔同交易所。
- Binance: 標準 futures API
- HyperLiquid: REST API（幣對名稱需要轉換：BTCUSDT → BTC）
- 所有 client 都有相同嘅 method 名（`create_market_order`, `get_positions`, etc.）

Multi-exchange 支持：`ctx.exchange_clients = {"aster": AsterClient(), "binance": BinanceClient()}`

---

## 5. `position_sync.py` — 持倉同步（Step 7）

### DRY_RUN 模式
從 `TRADE_STATE.md` 讀取倉位資訊（唔查 API）

### LIVE 模式
1. 遍歷所有已連接嘅 exchange client
2. 查 `get_usdt_balance()` → 加總餘額
3. 查 `get_positions()` → 填入 `ctx.open_positions`

### 孤兒偵測（Crash Recovery）⭐

**比喻：** 有個士兵上咗戰場但冇著防彈衣（有倉但冇 SL）。

```
偵測：倉位存在但冇 STOP_MARKET order → 孤兒！

修復優先順序：
1. 用 TRADE_STATE 記錄嘅 SL 價格補 SL
2. 用 1.5 × ATR 計 emergency SL
3. 冇 ATR → 用 mark price ±3% 做 fallback SL
4. SL 都放唔到 → 即刻市價平倉 + 記錄

原則：冇 SL 嘅倉位唔可以存在
```

### Auto-Close 偵測

```
情況：TRADE_STATE 話 POSITION_OPEN=YES，但交易所冇倉位
原因：SL 或 TP 已被交易所執行（system crash 時發生嘅）

處理：
1. 查 income API 攞已實現 PnL
2. 從 PnL + entry price 反算 exit price
3. 自動記錄到 trade log + trades.jsonl
4. 清除 TRADE_STATE（POSITION_OPEN=NO）
```

---

## 6. `exceptions.py` — 錯誤分類

```python
ExchangeError           ← 所有交易所錯誤嘅基類
├── TemporaryError      ← 可重試（網絡問題、server error）
├── DDosProtection      ← 可重試（429 rate limit）
├── OrderError          ← 唔重試（order rejected）
│   ├── InsufficientFundsError  ← 錢唔夠
│   └── InvalidOrderError       ← 無效 order（精度、minimum）
├── AuthenticationError ← 唔重試 → 升級 CriticalError
└── CriticalError       ← 停 pipeline！
```

---

## 數據流

```
Step 4: FetchMarketData  → Aster API → ctx.market_data
Step 5: CalcIndicators   → Aster klines → ctx.indicators
Step 7: CheckPositions   → ALL exchanges → ctx.open_positions + ctx.account_balance
  └── Orphan Detection   → 冇 SL? → 補 SL 或平倉
  └── Auto-Close         → 倉冇咗? → 查 PnL + 記錄
Step 12: ExecuteTrade    → 7 步落單 → ctx.order_result
```

---

## ⚠️ 分析中觀察到嘅特點

### 🟢 安全設計完善
- SL 失敗 → 即刻平倉 ✅
- 孤兒偵測 + auto-repair ✅
- reduceOnly 強制 ✅（SL/TP 永遠唔會意外開新倉）
- Quadratic backoff 重試 ✅
- 精度自動驗證 ✅

### 🟡 市場數據只從 Aster 拉
`FetchMarketDataStep` 只 call Aster API（`ASTER_FAPI`），唔查 Binance/HL。
如果要喺 Binance/HL 交易，佢哋嘅 klines 可能同 Aster 有差異。

### 🟢 Multi-exchange 架構已就緒
`ctx.exchange_clients` dict 支持多交易所。`position_sync` 會遍歷所有 client。
`execute_trade` 用 `signal.platform` 選擇 client。

---

## 自檢問題

1. **所有 order 都用 reduceOnly** → SL/TP 只平倉，唔會意外開反向倉 ✅
2. **SL 失敗 = 即刻平倉** → 呢個係 #1 安全規則，冇 SL 嘅倉位唔可以存在
3. **孤兒偵測** → crash 後自動修復，唔使人手介入
4. **Quadratic backoff** → 1-4-9-16-25 秒，唔會 DDoS 交易所
5. **市場數據只用 Aster** → 如果 Aster API down，成個 cycle 會 skip（RecoverableError）
