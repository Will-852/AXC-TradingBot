# Backtest 回測頁面指南
> 對象：用戶（包括新用戶）
> 更新：2026-03-11

## 呢個頁面做咩？

回測頁面讓你用**歷史數據測試 AXC 嘅交易策略**。你可以改參數、跑模擬、睇結果 — 全部喺 browser 入面完成，**唔需要行任何 command line**。

## 點樣打開

啟動 dashboard 後，訪問：
```
http://127.0.0.1:5555/backtest
```
或者喺主控台 (/) 點「回測」連結。

## 基本操作流程

### 1. 設定條件
頂部控制列有以下選項：
- **Symbol** — 揀要測嘅幣對（BTCUSDT、ETHUSDT 等）
- **Days** — 用幾多日嘅歷史數據（7d 到 180d）
- **Balance** — 模擬起始資金（預設 $10,000）

### 2. 執行回測
撳 **「執行回測」** 按鈕。系統會：
1. 自動從 Binance 拉取歷史 K 線（有 cache，第二次秒開）
2. 用 AXC 嘅策略引擎跑模擬交易
3. 大約 30-60 秒後顯示結果

狀態列會顯示進度：`LOADING → RUNNING → LOADED`

### 3. 睇結果

| 區域 | 顯示咩 |
|------|--------|
| **K 線圖** | 蠟燭圖 + 入場（▲/▼）出場（◇）markers + 連接線 |
| **Performance** | 回報率、勝率、盈虧比、最大回撤、夏普比率 |
| **Trade Log** | 每筆交易嘅時間、方向、策略、PnL |
| **Equity Curve** | 淨值曲線（下方子圖） |
| **Trade Detail** | 撳任何一筆交易顯示詳情（SL/TP/Duration/R:R） |

## 進階功能

### 改參數
撳 **PARAMETERS** 展開參數面板：

| 參數 | 意義 | 預設 |
|------|------|------|
| Range SL (ATR×) | Range 策略止損距離 = ATR 乘數 | 1.2 |
| Range RR | Range 最低風險回報比 | 2.3 |
| Trend SL (ATR×) | Trend 策略止損距離 | 1.5 |
| Trend RR | Trend 最低風險回報比 | 3.0 |
| Risk % | 每次交易最多冒幾多%本金 | 2% |
| Range/Trend Lev | 最大槓桿 | 8× / 7× |
| BB Touch Tol | BB 觸及容差 | 0.005 |
| ADX Range Max | ADX 低於呢個值算 Range 市場 | 20 |
| Mode Confirm | 模式確認需要幾次連續相同判定 | 2 |

留空 = 用 production 預設值。

### A/B 對比
1. 跑一次回測
2. 撳 **「A/B 對比」** — 儲存為 Run A
3. 改參數 → 再撳「執行回測」
4. 自動顯示兩組結果對比

### 指標 Overlay
工具列有以下開關：
- **BB** — Bollinger Bands
- **EMA** — EMA 快/慢線
- **MA** — MA 50/200
- **RSI / MACD / Stoch** — 子圖指標（只喺 1H 顯示）
- **Mode** — 模式轉換標記（RANGE ↔ TREND）

### Order Flow 功能（新）
- **Whale** — 大額成交標記（$100K+ 嘅 trades，綠=買/紅=賣）
- **Delta** — Delta Volume 柱狀圖（買 - 賣 嘅淨量）
- **VP** — Volume Profile（右側橫條圖，顯示邊個價位成交最多）
- **FP** — Footprint Heatmap（蠟燭背景色塊，顯示每根 candle 嘅成交分佈）

首次開啟會從 Binance 拉取 aggTrades 數據（需時 1-8 分鐘視乎日數）。之後有 cache。

### 時間週期
支持 1m / 5m / 15m / 1H / 4H / 1D。但指標同回測結果只喺 **1H** 顯示（策略引擎用 1H timeframe）。其他週期只顯示 K 線 + trade markers。

### 載入舊結果
撳 **「載入舊結果」** 可以打開以前跑過嘅回測。系統自動儲存每次跑嘅結果到 JSONL 文件。

### 匯出報告
跑完回測或載入舊結果後，撳 **「匯出」** 按鈕（📥 圖示），會下載一個完整 JSON 報告。格式見下方。

匯出嘅文件同時會存一份到 `backtest/data/exports/` 方便本地歸檔。

**CLI 跑完回測都會自動存結果**（JSONL + meta），唔需要額外操作：
```
python3 backtest/run_backtest.py --symbol BTCUSDT --days 30
→ backtest/data/bt_BTCUSDT_30d_trades.jsonl   (交易記錄)
→ backtest/data/bt_BTCUSDT_30d_meta.json      (config + stats)
→ backtest/data/bt_BTCUSDT_30d_equity.png     (淨值曲線)
```
跑完之後直接開 Dashboard → **載入舊結果** 或 **匯出** 就得。

### 匯入外部報告
撳 **「匯入」** 按鈕（📤 圖示），選一個 JSON 文件。系統會：
1. 驗證格式
2. 儲存到本地（下次可以喺「載入舊結果」搵返）
3. 原始 JSON 同時存入 `backtest/data/exports/`
4. 自動載入到圖表顯示

你可以用自己嘅策略引擎跑完回測，只要輸出符合以下格式就可以喺 Dashboard 睇。

### 報告 JSON 格式（v1.0）

```json
{
  "format_version": "1.0",
  "source": "你嘅引擎名",
  "config": {
    "symbol": "BTCUSDT",
    "days": 30,
    "balance": 10000,
    "interval": "1h",
    "strategy_params": {},
    "param_overrides": {}
  },
  "stats": {
    "return_pct": 12.5,
    "win_rate": 58.3,
    "profit_factor": 1.8,
    "max_drawdown_pct": 5.2,
    "total_trades": 24,
    "expectancy": 52.1,
    "sharpe_ratio": 1.5
  },
  "trades": [
    {
      "side": "LONG",
      "entry": 84500.0,
      "exit": 85200.0,
      "pnl": 82.35,
      "entry_time": "2026-03-01T08:00:00",
      "exit_time": "2026-03-01T14:00:00",
      "strategy": "RANGE",
      "sl_price": 84100.0,
      "tp_price": 85300.0,
      "exit_reason": "TP"
    }
  ]
}
```

#### 必填欄位

| 欄位 | 位置 | 說明 |
|------|------|------|
| `config.symbol` | config | 交易對（如 BTCUSDT） |
| `trades` | root | 交易陣列（至少 1 筆） |
| `trades[].side` | trade | `LONG` 或 `SHORT` |
| `trades[].entry` | trade | 入場價 |
| `trades[].exit` | trade | 出場價 |

#### 選填欄位

| 欄位 | 說明 | 冇填會點 |
|------|------|----------|
| `trades[].pnl` | 盈虧（USD） | 自動由 entry/exit/side 計算 |
| `trades[].entry_time` | 入場時間 | 冇時間 → 圖表唔顯示 marker |
| `trades[].exit_time` | 出場時間 | 同上 |
| `trades[].strategy` | 策略名 | 顯示為 `--` |
| `trades[].sl_price` | 止損價 | 詳情面板冇顯示 |
| `trades[].tp_price` | 止盈價 | 同上 |
| `trades[].exit_reason` | 出場原因（TP/SL/TIMEOUT） | 顯示為 `--` |
| `config.days` | 數據日數 | 自動從 trade 時間推算 |
| `config.balance` | 起始資金 | 預設 $10,000 |
| `stats` | 績效指標 | 自動從 trades 計算基本指標 |

### Live（即時 K 線）
撳 **Live** 按鈕開啟，按鈕會變成 **Live ON**。

**做啲咩：**
1. 先載入歷史 K 線（日數 = dropdown 選擇），畫出完整圖表
2. 透過 Binance Futures WebSocket（`wss://fstream.binance.com`）即時推送更新
3. 圖表最右邊嘅蠟燭每秒級更新（延遲 <1s）

唔需要交易所 API key — 用嘅係 Binance 公開 WebSocket endpoint。

**典型用法：**
1. 揀 symbol + 日數 → 撳 Live → 歷史 + 即時一次過載入
2. 可以加 BB / EMA / RSI 做即時技術分析
3. 切 symbol / interval → WS 自動斷開 + 重連新 stream
4. 斷網 → 自動 reconnect（1s → 2s → 4s ... 最多 30s backoff）

再撳一次 Live ON → 關閉。

### Live Pos（即時持倉顯示）
撳 **Live Pos** 按鈕開啟，按鈕會變成 **Pos ON**。

**做啲咩：** 每 30 秒從你嘅 Binance 交易帳號讀取當前持倉，然後喺圖表上畫三條線：
- **藍色實線** — 入場價
- **紅色虛線** — 止損價
- **綠色虛線** — 止盈價

**前提條件：** 需要 AXC 系統已經設定好交易所 API credentials（`secrets/.env` 入面嘅 `BINANCE_API_KEY` 同 `BINANCE_API_SECRET`）。如果冇設定，開啟後會提示「連接失敗」。

**典型用法：**
1. 你透過 AXC 開咗一個 BTCUSDT 嘅倉位
2. 喺回測頁面揀 BTCUSDT → 開 Live Pos
3. 圖上即時顯示你嘅入場價同 SL/TP 位置
4. 可以同時開 Live → 睇住現價同你嘅持倉位置嘅關係

再撳一次 Pos ON → 關閉。

### 其他功能
- **畫圖工具** — 水平線、趨勢線、Fibonacci
- **CSV 匯出** — 匯出交易記錄
- **鍵盤快捷鍵**：`Cmd+Enter` 執行回測、`[`/`]` 上下筆交易、`F` 展開圖表、`P` 開參數面板、`I` 開指標列

## 常見問題

**Q: 回測結果同真實交易會一樣嗎？**
唔會。回測唔模擬 funding rate、滑點、API 延遲。經驗法則：回測回報 × 0.5-0.7 ≈ 真實預期。

**Q: 點解指標喺 5m/15m 冇顯示？**
策略引擎只喺 1H timeframe 產生指標數據。其他週期只顯示 K 線。

**Q: Whale/Delta/VP/FP 第一次開好慢？**
因為要從 Binance 拉取逐筆成交數據（aggTrades）。完成後會 cache 到本地，下次秒開。

**Q: 想改策略邏輯點算？**
策略邏輯喺 `backtest/engine.py`。呢個頁面只調參數，唔改策略本身。改策略需要寫 code。

## 文件位置一覽

所有 backtest 數據都喺 `~/projects/axc-trading/backtest/data/`：

| 文件 | 格式 | 說明 |
|------|------|------|
| `bt_{SYMBOL}_{days}d_trades.jsonl` | JSONL | 交易記錄，每行一筆 JSON |
| `bt_{SYMBOL}_{days}d_meta.json` | JSON | 參數快照 + 統計數據 |
| `bt_{SYMBOL}_{days}d_equity.png` | PNG | 權益曲線圖（CLI 產出） |
| `bt_{SYMBOL}_{days}d_v2_trades.jsonl` | JSONL | 匯入版本（自動加 v{N} 避免覆蓋） |
| `exports/{SYMBOL}_{days}d_{timestamp}.json` | JSON | 匯出副本 / 匯入原始文件 |
| `{SYMBOL}_1h_{start}_{end}.csv` | CSV | Binance 歷史價格 cache（自動產生） |

### 手動新增舊結果

三種方法：

1. **Dashboard 匯入** — 撳「匯入」按鈕，選 `.json` 文件（格式見上方）
2. **手動放 JSONL** — 將 JSONL 文件放入 `backtest/data/`，命名為 `bt_BTCUSDT_60d_trades.jsonl`，然後撳「載入舊結果」
3. **CLI 跑完自動存** — `python3 backtest/run_backtest.py --symbol BTCUSDT --days 60` 會自動產生 JSONL + meta + equity PNG
