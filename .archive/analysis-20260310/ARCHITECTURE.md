# AXC 系統架構分析 — 2026-03-10
> 完整系統拆解，用嚟理解「邊個做咩」同「數據點流」

## 系統一句話
本地 AI 加密貨幣交易系統：掃描市場 → 計算指標 → 判斷模式 → 策略出信號 → 自動落單。

---

## 骨架圖

```
AXC-TRADING/
│
├── ai/                  ← 老闆手冊：系統上下文、策略、規則
│   ├── CONTEXT.md       ← 完整系統描述
│   ├── STRATEGY.md      ← 交易策略（Range / Trend）
│   ├── RULES.md         ← 行為規則
│   └── MEMORY.md        ← 近期狀態
│
├── agents/              ← AI Agents（OpenClaw gateway 用）
│   ├── main/            ← 主 agent + 60+ session 記錄
│   ├── aster_scanner/   ← Aster 掃描 agent
│   ├── aster_trader/    ← Aster 交易 agent
│   ├── heartbeat/       ← 心跳監控 agent
│   ├── news_agent/      ← 新聞 agent
│   ├── analyst/         ← 分析 agent（SOUL.md only）
│   ├── binance_scanner/ ← Binance 掃描 agent（SOUL.md only）
│   ├── binance_trader/  ← Binance 交易 agent（SOUL.md only）
│   ├── decision/        ← 決策 agent（SOUL.md only）
│   └── haiku_filter/    ← Haiku 過濾 agent（SOUL.md only）
│
├── config/              ← 設定面板（改參數來呢度）
│   ├── params.py        ← ⭐ 所有數字參數（BB、RSI、掃描頻率、交易所 symbol list）
│   └── modes/           ← RANGE.py / TREND.py / VOLATILE.py 模式定義
│
├── scripts/             ← ⭐ 真正做嘢嘅代碼
│   ├── indicator_calc.py      ← 技術指標計算（RSI、BB、MACD、ATR...）
│   ├── async_scanner.py       ← 9 交易所輪詢掃描（20s/exchange）
│   ├── dashboard.py           ← Web Dashboard（port 5555）
│   ├── tg_bot.py              ← Telegram Bot（查詢 + AI 落單）
│   ├── light_scan.py          ← 輕量掃描 + SCAN_CONFIG 讀寫
│   ├── public_feeds.py        ← Bybit/OKX/KuCoin/Gate/MEXC/Bitget feeds
│   ├── axc_client.py          ← OpenClaw API client
│   ├── slash_cmd.py           ← Telegram 查詢指令處理
│   ├── heartbeat.py           ← 系統心跳
│   ├── news_scraper.py        ← 新聞抓取
│   ├── news_sentiment.py      ← 新聞情緒分析
│   ├── telegram_sender.py     ← Telegram 發送工具
│   ├── health_check.sh        ← 系統健康檢查
│   ├── backup_agent.sh        ← 自動備份
│   └── trader_cycle/          ← ⭐ 核心交易引擎
│       ├── main.py            ← 主循環入口
│       ├── strategies/        ← 策略
│       │   ├── mode_detector.py   ← 5 票制模式判斷
│       │   ├── range_strategy.py  ← Range 策略（BB 觸碰 + RSI 反轉）
│       │   ├── trend_strategy.py  ← Trend 策略（MA + MACD + RSI）
│       │   ├── evaluate.py        ← 策略評估調度
│       │   └── base.py            ← 策略基類 + PositionParams
│       ├── exchange/          ← 交易所連接
│       │   ├── aster_client.py      ← Aster DEX API
│       │   ├── binance_client.py    ← Binance Futures API
│       │   ├── hyperliquid_client.py ← HyperLiquid API
│       │   ├── execute_trade.py     ← 統一落單
│       │   ├── market_data.py       ← 行情 + 指標計算
│       │   └── position_sync.py     ← 持倉同步
│       ├── risk/              ← 風控
│       │   ├── risk_manager.py      ← 風險檢查
│       │   ├── position_sizer.py    ← 倉位計算 + TP 計算
│       │   └── adjust_positions.py  ← 移動止損 + TP 延伸
│       ├── state/             ← 狀態管理
│       │   ├── trade_state.py       ← TRADE_STATE.md 讀寫
│       │   ├── scan_config.py       ← SCAN_CONFIG.md 讀寫
│       │   ├── trade_journal.py     ← 交易日誌
│       │   ├── trade_log.py         ← 交易記錄
│       │   ├── memory_keeper.py     ← 記憶寫入
│       │   ├── read_sentiment.py    ← 情緒讀取
│       │   └── file_lock.py         ← fcntl 文件鎖
│       ├── config/            ← 交易設定
│       │   ├── pairs.py       ← ⭐ 交易對定義（6 pairs）
│       │   └── settings.py    ← ⭐ 風控常數 + 交易參數
│       ├── notify/            ← 通知
│       │   └── telegram.py    ← Telegram 推送
│       ├── analysis/          ← 分析
│       │   └── metrics.py     ← 交易統計
│       └── core/              ← 核心框架
│           ├── context.py     ← CycleContext 數據結構
│           ├── pipeline.py    ← 步驟管線
│           └── registry.py    ← 步驟註冊
│
├── backtest/            ← 回測系統（2026-03-10 新建）
│   ├── engine.py        ← 核心模擬器
│   ├── run_backtest.py  ← CLI 入口
│   ├── compare_configs.py ← A/B 測試
│   ├── fetch_historical.py ← 歷史數據拉取
│   └── data/            ← CSV 快取 + 結果
│
├── shared/              ← 公告板（各部門共用數據）
│   ├── TRADE_STATE.md   ← 持倉狀態
│   ├── SCAN_CONFIG.md   ← 掃描結果（ATR、S/R、價格）
│   ├── SIGNAL.md        ← 最新信號
│   ├── prices_cache.json ← 價格快取
│   ├── pnl_history.json ← PnL 歷史
│   └── ...
│
├── memory/              ← RAG 記憶系統
│   ├── writer.py        ← 寫入（voyage-3 embedding）
│   ├── retriever.py     ← 搜尋
│   ├── embedder.py      ← 向量化
│   └── index/           ← jsonl + npy 儲存
│
├── docs/                ← 文檔（15+ 教學指南）
├── canvas/              ← Dashboard 前端（HTML + SVG icons）
├── secrets/.env         ← API keys（gitignored）
└── logs/                ← 日誌
```

---

## 數據流（餐廳比喻）

```
                    ┌──────────────────────────────────────────┐
                    │            Scanner（侍應巡場）              │
                    │  async_scanner.py → 9 exchanges × 20s    │
                    │  寫入 → prices_cache.json + SCAN_CONFIG   │
                    └──────────────┬───────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────────┐
                    │         Trader Cycle（廚房）               │
                    │  每幾分鐘跑一次：                           │
                    │  1. fetch_market  → 拉即時價格+funding     │
                    │  2. calc_indicators → 計 RSI/BB/MACD/ATR  │
                    │  3. detect_mode → RANGE or TREND？        │
                    │  4. evaluate → 跑策略，出信號               │
                    │  5. position_sizer → 計倉位                │
                    │  6. execute_trade → 落單到交易所            │
                    │  7. 寫入 TRADE_STATE + SCAN_CONFIG         │
                    └──────────────┬───────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
┌─────────▼──────────┐  ┌─────────▼──────────┐  ┌─────────▼──────────┐
│   Dashboard        │  │   Telegram Bot     │  │   Risk Manager     │
│   讀 shared/ 顯示   │  │   查詢 + AI 落單    │  │   移動止損 + TP延伸  │
│   port 5555        │  │   tg_bot.py        │  │   adjust_positions │
└────────────────────┘  └────────────────────┘  └────────────────────┘
```

---

## 交易對（2026-03-10）

| Pair | Group | Exchange | 策略表現（180d backtest） |
|------|-------|----------|------------------------|
| BTCUSDT | crypto_correlated | Aster, Binance, HL | Trend 4W/2L, +$1,581 |
| ETHUSDT | crypto_correlated | Aster, Binance, HL | Trend 2W/1L, +$701 |
| XRPUSDT | crypto_independent | Aster, others | Range-only recommended |
| SOLUSDT | crypto_correlated | Aster, Binance, HL | Trend 5W/2L, +$3,113 ⭐ |
| XAGUSDT | commodity | Aster only | Silver |
| XAUUSDT | commodity | Aster only | Gold (新加) |

---

## LaunchAgents（自動服務）

| Service | Label | 功能 |
|---------|-------|------|
| Scanner | ai.openclaw.scanner | 9 交易所輪詢掃描 |
| Telegram | ai.openclaw.telegram | Telegram Bot |
| Trader Cycle | ai.openclaw.tradercycle | 自動交易引擎 |
| Heartbeat | ai.openclaw.heartbeat | 系統心跳監控 |
| Light Scan | ai.openclaw.lightscan | 輕量掃描 |
| Backup | com.openclaw.backup | 每日 03:00 備份 |
| Report | ai.openclaw.report | 定期報告 |
| Strategy Review | ai.openclaw.strategyreview | 策略回顧 |
| News Agent | ai.openclaw.newsagent | 新聞抓取 |

---

## 技術棧

| 層面 | 技術 |
|------|------|
| 語言 | Python 3.14 |
| AI 推理 | Claude API（tier1: Sonnet, tier2: Haiku, tier3: GPT-5-mini） |
| 向量嵌入 | Voyage AI（voyage-3） |
| 記憶儲存 | jsonl + numpy（無資料庫） |
| 交易所 | Aster DEX / Binance Futures / HyperLiquid |
| 介面 | Telegram Bot + Web Dashboard（port 5555） |
| 回測 | backtest/ (2026-03-10 新建) |
