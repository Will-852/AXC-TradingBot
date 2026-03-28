# 逐個文件夾分析指南 — 2026-03-10
> 按建議順序，由最影響交易行為嘅開始

---

## Step 1: `config/` — 設定面板

**你要睇嘅文件：**
- `config/params.py` — 所有參數嘅中央控制台

**重點搵：**
```
Section 1: EXCHANGE_ROTATION — 9 間交易所輪詢順序
Section 3: BB 指標參數 (BB_WIDTH_MIN, BB_TOUCH_TOL)
Section 4: 交易 profiles (AGGRESSIVE/MODERATE/CONSERVATIVE)
Section 8: ASTER_SYMBOLS, BINANCE_SYMBOLS, HL_SYMBOLS
```

**你要問自己：**
- 我知唔知每個 profile 嘅 trigger_pct 代表咩？
- 改 BB_WIDTH_MIN 會影響邊個策略？（答：Range）

---

## Step 2: `shared/` — 系統公告板

**你要睇嘅文件：**
- `shared/TRADE_STATE.md` — 而家有冇持倉？方向？SL/TP？
- `shared/SCAN_CONFIG.md` — 最新掃描結果（ATR、價格、S/R）
- `shared/SIGNAL.md` — 最新信號
- `shared/prices_cache.json` — 各交易所最新價格

**點睇：**
```bash
cat shared/TRADE_STATE.md    # 持倉狀態
cat shared/SCAN_CONFIG.md    # 掃描結果
cat shared/SIGNAL.md         # 最新信號
```

**你要問自己：**
- TRADE_STATE 顯示嘅 SL/TP 同你預期嘅一唔一樣？
- SCAN_CONFIG 有冇 SOL_ATR、XAU_ATR？（有 = trader cycle 已經跑過）

---

## Step 3: `scripts/trader_cycle/strategies/` — 策略邏輯

**你要睇嘅文件（按順序）：**
1. `mode_detector.py` — 5 個投票者決定 RANGE 定 TREND
2. `range_strategy.py` — BB 觸碰 + RSI 反轉 + volume 確認
3. `trend_strategy.py` — MA + MACD + RSI + 價格位置
4. `base.py` — PositionParams（risk_pct, leverage, sl_atr_mult）

**重點搵：**
- Mode detector 嘅 5 票：RSI, MACD, Volume, MA, Funding
- Range 入場 3 個條件：C1（BB touch）, C2（RSI reversal）, C3（support/resistance）
- Trend 入場 4 個條件：MA direction, MACD, RSI, Price at MA

---

## Step 4: `scripts/trader_cycle/risk/` — 風控

**你要睇嘅文件：**
1. `position_sizer.py` — 倉位計算 + TP 計算（Range 用 BB，Trend 用 S/R）
2. `risk_manager.py` — 風險檢查（最大持倉、連虧暫停）
3. `adjust_positions.py` — 移動止損 + TP 延伸

**重點搵：**
- SL = ATR × sl_atr_mult（Range: 1.2, Trend: 1.5）
- 最大持倉 = MAX_CRYPTO_POSITIONS = 2
- 移動止損：profit > 1×ATR → SL 移到入場價

---

## Step 5: `scripts/trader_cycle/exchange/` — 交易所

**你要睇嘅文件：**
1. `market_data.py` — FetchMarketDataStep + CalcIndicatorsStep
2. `execute_trade.py` — 統一落單流程
3. `aster_client.py` — Aster DEX 具體 API
4. `position_sync.py` — 持倉同步

---

## Step 6: `scripts/` 根目錄 — 獨立工具

| 文件 | 做咩 | 幾時睇 |
|------|------|--------|
| `indicator_calc.py` | 計 RSI/BB/MACD/ATR | 想理解指標計算 |
| `async_scanner.py` | 9 交易所掃描 | 想理解價格點嚟 |
| `dashboard.py` | Web Dashboard | 想改 UI |
| `tg_bot.py` | Telegram Bot | 想改落單邏輯 |
| `light_scan.py` | SCAN_CONFIG 讀寫 | 想理解狀態管理 |
| `slash_cmd.py` | /pos /bal /pnl 指令 | 想加新指令 |

---

## Step 7: `ai/` — 策略文檔

| 文件 | 內容 |
|------|------|
| `CONTEXT.md` | 系統完整描述（畀 AI 讀嘅） |
| `STRATEGY.md` | Range + Trend 策略完整邏輯 |
| `RULES.md` | 行為規則 |
| `MEMORY.md` | 近期狀態 |

---

## Step 8: `agents/` — OpenClaw Agents

每個 agent 有 `SOUL.md` = 性格設定。`main/` agent 最重要，有 60+ session 記錄。

大部分 agent 唔直接影響交易，係 OpenClaw gateway 嘅組件。

---

## Step 9: `backtest/` — 回測系統

| 文件 | 做咩 |
|------|------|
| `engine.py` | 核心模擬器（candle-by-candle） |
| `run_backtest.py` | 單 pair CLI 測試 |
| `compare_configs.py` | A/B 多 config 對比 |
| `fetch_historical.py` | Binance 歷史數據 + CSV 快取 |

```bash
# 跑 BTC 180 日回測
/opt/homebrew/bin/python3 backtest/run_backtest.py --symbol BTCUSDT --days 180

# 跑 8 pairs 對比
/opt/homebrew/bin/python3 backtest/compare_configs.py
```

---

## Step 10: `docs/` — 按需查閱

| 子目錄 | 內容 |
|--------|------|
| `guides/00-15` | 完整教學系列 |
| `architecture/` | 系統架構文檔 |
| `indicators/` | 指標研究筆記 |
| `setup/` | 安裝 + 恢復 |
