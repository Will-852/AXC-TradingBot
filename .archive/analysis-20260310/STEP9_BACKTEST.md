# Step 9: `backtest/` — 回測系統（時光機）
> talk12 風格分析 | 2026-03-10

## 點樣搵到
```
axc-trading → backtest/
├── __init__.py             ← 空（Python package 門牌）
├── fetch_historical.py     ← 拉歷史數據 + CSV 快取 4KB
├── engine.py               ← 核心模擬器 ⭐ 22KB
├── run_backtest.py         ← 單 pair CLI 入口 7KB
├── compare_configs.py      ← A/B 多 config 對比 6KB
└── data/                   ← 快取 CSV + 結果（34 個文件）
    ├── *_1h_*.csv          ← 歷史 1H candles
    ├── *_4h_*.csv          ← 歷史 4H candles
    ├── bt_*_trades.jsonl   ← 回測交易記錄
    └── bt_*_equity.png     ← 資金曲線圖
```

**4 個 Python 文件 + 34 個數據文件。** 同 production 代碼零交叉（唔改任何 production 文件）。

---

## 比喻

**時光機：** 你坐時光機返去 180 日前，帶住你而家嘅策略，一根蠟燭一根蠟燭咁交易。睇吓結果會點。

關鍵：你唔可以「偷看未來」— 每個決策只能用嗰個時間點已知嘅數據。

---

## 1. `fetch_historical.py` — 數據收集員

**比喻：** 去圖書館借舊報紙。借咗一次就影印留底（CSV 快取），下次唔使再借。

```
Binance API /fapi/v1/klines
  ├── 每次最多 1000 根 candle
  ├── 自動分頁（loop 直到覆蓋全部範圍）
  ├── Rate limit: 每頁之間 sleep 0.2s
  └── 快取: backtest/data/{SYMBOL}_{INTERVAL}_{START}_{END}.csv

例：BTCUSDT_1h_20250903_20260310.csv（6 個月 1H 數據）
```

**快取設計：** 檔名包含起止日期。同一範圍第二次 run → 直接讀 CSV，唔 call API。

---

## 2. `engine.py` — 核心模擬器 ⭐

**比喻：** 時光機嘅引擎。每 1 小時推前一格，模擬所有交易決策。

### Main Loop（每根 1H candle）

```
for 每根 1H candle（由第 201 根開始）:
    ① 檢查 4H 邊界 → 更新 4H 指標 + mode detection
    ② 執行上一根嘅待處理信號（entry at THIS candle's open）⭐
    ③ 檢查 SL/TP（用 candle high/low）
    ④ 計 1H 指標（rolling 200 根 window）
    ⑤ 如果 mode confirmed + 有空位 → 跑策略 evaluate()
    ⑥ 有信號 → 存為「待處理」（下一根才執行）
    ⑦ 記錄 equity（mark-to-market）
```

### 防偷看未來（No Look-Ahead）

```
candle i: 策略評估 → 發現信號 → 存為 pending
candle i+1: 用 candle i+1 嘅 open price 入場

唔會用同一根 candle 嘅 close 入場
（因為你喺實際交易中見到 close 時，蠟燭已經結束）
```

### MTF 同步（Multi-Timeframe）

```
1H 係主要時鐘。每根 1H candle 行一次。
4H 邊界 = 每 4 根 1H candle
跨 4H 邊界 → 重新計算 4H 指標 + mode detection
只用已完成嘅 4H candle（唔用正在形成嘅）
```

### SL/TP 處理

| 情況 | 處理 | 原因 |
|------|------|------|
| SL hit | sl_price × (1 ± 0.02%) | 市價單有滑點 |
| TP hit | 精確 tp_price | 限價單冇滑點 |
| 同根兩個都 hit | 假設 SL 先 | 保守估算 |
| 回測結束仲有倉 | 用最後 close 平倉 | 標記 "END" |

### 手續費
```
入場 0.05% + 出場 0.05% = 每 trade 0.10% 成本
SL 額外 0.02% 滑點（市價單）
```

### Reuse Production 代碼

| 來自 | 函數 | 用途 |
|------|------|------|
| indicator_calc.py | `calc_indicators()` | 全部指標計算 |
| mode_detector.py | `detect_mode_for_pair()` | 5 票 mode detection |
| range_strategy.py | `RangeStrategy.evaluate()` | Range 信號 |
| trend_strategy.py | `TrendStrategy.evaluate()` | Trend 信號 |
| context.py | `CycleContext` | 數據結構 |
| settings.py | `MAX_CRYPTO_POSITIONS` 等 | 風控常數 |

### Cluster Analysis（分叢統計）

**比喻：** 如果你 2 小時內連續做咗 3 個 trade，呢 3 個可能係同一個「決定」嘅結果。

```
Cluster = 同 pair + 同 direction + 間隔 < 4 小時嘅 trade
每個 cluster 視為 1 個獨立決策

Cluster-Adjusted Win Rate =
  (非叢 wins + 淨利叢數) ÷ (非叢 trades + 叢數)

目的：避免因為連續入場膨脹 trade 數量
```

### Scope Boundary（唔做）
- Funding rate
- Trailing SL / Partial TP
- News sentiment
- Multi-symbol（每次只跑一個 pair）
- Re-entry cooldown

---

## 3. `run_backtest.py` — 單 pair CLI

**比喻：** 時光機嘅啟動按鈕。你話去邊（pair）、去幾遠（days），佢就開始。

```bash
# 基本用法
python3 backtest/run_backtest.py --symbol BTCUSDT --days 14
python3 backtest/run_backtest.py --symbol ETHUSDT --days 30 --platform binance
python3 backtest/run_backtest.py --symbol BTCUSDT --days 180 --balance 5000
```

輸出 3 樣嘢：
| 輸出 | 格式 | 位置 |
|------|------|------|
| 統計結果 | Terminal 表格 | 螢幕 |
| 交易記錄 | bt_{SYMBOL}_{DAYS}d_trades.jsonl | backtest/data/ |
| 資金曲線 | bt_{SYMBOL}_{DAYS}d_equity.png | backtest/data/ |

Terminal 輸出樣本：
```
─────────────────────────────────────────────
                  RESULTS
─────────────────────────────────────────────
  Final Balance:  $   10,850.00
  Return:               +8.50%
  Total Trades:              12
  Win Rate:              58.3%
  Adj Win Rate:          60.0%  (10 indep, 1 clusters)
  Profit Factor:           1.45
  Expectancy:           $+70.83/trade
  Max Drawdown:            3.2%
  Range:          3W / 5L
  Trend:          4W / 0L
─────────────────────────────────────────────
```

---

## 4. `compare_configs.py` — A/B 測試

**比喻：** 你有 4 個唔同嘅策略設定，想知邊個最好。將佢哋放入同一部時光機，跑 180 日 × 8 個 pair。

```bash
python3 backtest/compare_configs.py
```

### 4 個測試 Config

| Config | 描述 | 特點 |
|--------|------|------|
| A_baseline | Production 設定 | 原封不動 |
| B_xrp_range_only | XRP 只做 Range | 禁 Trend |
| C_relaxed_range | 放寬 Range 條件 | BB_WIDTH 0.07, tol 0.008, ADX 25 |
| D_moderate | 中間路線 | BB_WIDTH 0.06, tol 0.007, ADX 22 |

### 測試 Pairs（8 個）
```
BTC, ETH, XRP, SOL, DOGE, LINK, ADA, AVAX
```

**輸出：** 超大比較表格 — 每個 config × 每個 pair 嘅 trades/W-L/WR/PnL/PF/DD + Range/Trend 分拆。

---

## 已有數據

```
backtest/data/
├── 歷史 CSV（8 pairs × 2 timeframes = 16+ 文件）
│   ├── BTCUSDT_1h_20250903_20260310.csv
│   ├── BTCUSDT_4h_20250809_20260310.csv
│   ├── ETHUSDT_*.csv, XRPUSDT_*.csv, SOLUSDT_*.csv
│   ├── DOGEUSDT_*.csv, LINKUSDT_*.csv, ADAUSDT_*.csv, AVAXUSDT_*.csv
│
├── 回測結果
│   ├── bt_BTCUSDT_14d/60d/180d_trades.jsonl + equity.png
│   ├── bt_ETHUSDT_60d/180d_trades.jsonl + equity.png
│   └── bt_XRPUSDT_180d_trades.jsonl + equity.png
```

---

## 數據流

```
Binance API ──→ fetch_historical.py ──→ CSV 快取
                                           │
                                           ▼
                    ┌──────────────────────────────────┐
                    │         engine.py                 │
                    │                                  │
                    │  CSV → calc_indicators (reuse)   │
                    │      → detect_mode (reuse)       │
                    │      → strategy.evaluate (reuse) │
                    │      → SL/TP check               │
                    │      → equity tracking            │
                    └──────────┬───────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
              run_backtest.py      compare_configs.py
              (單 pair CLI)        (A/B 多 config)
                    │                     │
                    ▼                     ▼
              trades.jsonl          比較表格
              equity.png
```

---

## ⚠️ 分析中觀察到嘅特點

### 🟢 Zero production code changes
整個 backtest 系統唔改任何 production 文件。透過 import reuse。

### 🟢 No look-ahead bias
Signal 在 candle i 生成，entry 在 candle i+1 open。正確。

### 🟢 保守估算
- 同根 SL+TP 都 hit → 假設 SL 先
- SL 有 slippage，TP 冇（SL 係市價單，TP 係限價單）
- Commission 0.10% per trade

### 🟢 Cluster analysis
避免因為連續入場膨脹 win rate。Adjusted WR 更反映真實表現。

### 🟡 Scope 有限
唔計 funding rate、trailing SL、partial TP、news。呢啲喺 production 有但 backtest 冇。
意味住 backtest 結果同實際表現可能有差異（尤其 XAG funding 每日 0.64%）。

### 🟡 param_overrides 用 monkey-patching
`engine.py` 直接修改 `TIMEFRAME_PARAMS["1h"]` dict + `indicator_calc.BB_WIDTH_MIN` 全局變數。
有 try/finally 恢復，但唔係 thread-safe。單線程用冇問題。

---

## 自檢問題

1. **回測結果可信嗎？** → 大方向可信（reuse production 策略），但缺 funding/trailing 會有偏差
2. **BTC 180d 表現？** → 有 trades.jsonl 可以查。Trend 策略通常表現好過 Range
3. **點解 compare_configs 用 8 個 pair？** → 因為 backtest 唔受 POSITION_GROUPS 限制，可以測更多 pair
4. **CSV 會唔會過時？** → 會。每次新日期 range 會生成新 CSV。舊嘅唔自動刪
5. **同 metrics.py 兼容？** → 係。`to_jsonl()` 格式同 `_load_trades()` 一致
