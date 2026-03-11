<!--
title: Scripts 總覽
section: 機械體架構
order: 12
audience: human,claude,github
-->

# Scripts 總覽

## 根目錄 Scripts（26 個）

### 核心服務

| Script | 功能 |
|--------|------|
| async_scanner.py | v7 九路輪轉掃描器（9 exchanges × 20s，常駐 daemon） |
| light_scan.py | 3 min Aster 輕量掃描（BTC/ETH/XRP/XAG/XAU） |
| scanner_runner.py | Orchestrator: light_scan → trader_cycle 協調執行 |
| indicator_calc.py | 技術指標計算（25+ indicators，支持 aster/binance） |
| tg_bot.py | @AXCTradingBot Telegram 交易 bot（自然語言 + slash commands） |
| news_bot.py | @AXCnews_bot 獨立新聞 Telegram Bot |
| slash_cmd.py | slash commands 實作（零 AI，純 Python） |
| dashboard.py | ICU Dashboard（port 5555） |
| heartbeat.py | 15 min 健康檢查 |
| news_scraper.py | RSS 新聞收集（CoinTelegraph + CoinDesk） |
| news_sentiment.py | Claude Haiku 情緒分析 → shared/news_sentiment.json |
| macro_monitor.py | 宏觀市場流動性監察（DXY、VIX、原油、黃金等） |
| x_monitor.py | X 帳號推文監察（透過 LunarCrush） |
| public_feeds.py | 9 exchange API adapters |
| binance_feed.py | Binance 市場數據模組（公開端點，免 key） |
| weekly_strategy_review.py | 每週回顧 → ai/STRATEGY.md |

### 工具 / 橋接

| Script | 功能 |
|--------|------|
| axc_client.py | AXC → OpenClaw 連接層（透過 dashboard HTTP API） |
| openclaw_bridge.py | OpenClaw 平台偵測 + helpers（optional，唔 fail） |
| telegram_sender.py | Agent 專用 Telegram 發送工具 |
| write_activity.py | 活動日誌寫入器 → shared/activity_log.jsonl |
| memory_init.py | RAG 記憶初始化（import 既有數據） |

### Shell

| Script | 功能 |
|--------|------|
| load_env.sh | LaunchAgent .env wrapper |
| backup_agent.sh | git + push + zip backup |
| health_check.sh | 7 類別系統診斷 |
| integration_test.sh | 5 場景整合測試 |
| build_axc_zip.sh | 打包獨立部署 ZIP |

## Trader Cycle 子模組（~30 個 Python 文件）

| 子目錄 | 功能 | 關鍵文件 |
|--------|------|----------|
| config/ | 7 pairs 定義 + 所有常數 | pairs.py, settings.py |
| strategies/ | 策略邏輯 | range_strategy.py, trend_strategy.py, mode_detector.py, evaluate.py |
| exchange/ | 交易所接口（Aster/Binance 路由） | market_data.py, aster_client.py, execute_trade.py, position_sync.py |
| risk/ | 風控 + 倉位計算 | risk_manager.py (熔斷+POSITION_GROUPS), position_sizer.py |
| state/ | 狀態讀寫 | state_manager.py, scan_config_writer.py |

## Backtest 系統

| 文件 | 功能 |
|------|------|
| backtest/fetch_historical.py | Binance klines + CSV cache |
| backtest/engine.py | Candle-by-candle 模擬器（score filtering + sizing + MAX_RISK_PCT cap） |
| backtest/scoring.py | WeightedScorer — 可配置評分公式（乘法 volume + OBV 加減分） |
| backtest/weight_config.py | 優化搜索空間定義 + 預設值 |
| backtest/optimizer.py | 核心優化器（Stage 1 LHS + Stage 2 權重搜索 + walk-forward） |
| backtest/run_backtest.py | 單 pair CLI |
| backtest/run_optimizer.py | 優化器 CLI 入口 |
| backtest/compare_configs.py | A/B 4 configs × 8 pairs |
| backtest/grid_search.py | Grid search 參數掃描 |
| backtest/validate.py | 回測結果驗證 |
