<!--
title: 15 個 Scripts
section: 機械體架構
order: 12
audience: human,claude,github
-->

# Scripts 總覽

## 根目錄 Scripts

| Script | 功能 |
|--------|------|
| async_scanner.py | v7 九路輪轉掃描器（9 exchanges × 20s，常駐 daemon） |
| light_scan.py | 3 min Aster 輕量掃描（BTC/ETH/XRP/XAG/XAU） |
| indicator_calc.py | 技術指標計算（25+ indicators，支持 aster/binance） |
| tg_bot.py | Telegram Bot（69KB，自然語言 + 14 slash commands） |
| slash_cmd.py | 14 個 slash commands（零 AI，純 Python） |
| dashboard.py | ICU Dashboard（port 5555，105KB 最大文件） |
| heartbeat.py | 15 min 健康檢查 |
| news_scraper.py | RSS 新聞收集（CoinTelegraph + CoinDesk） |
| news_sentiment.py | Claude Haiku 情緒分析 → shared/news_sentiment.json |
| public_feeds.py | 9 exchange API adapters |
| weekly_strategy_review.py | 每週回顧 → ai/STRATEGY.md |
| load_env.sh | LaunchAgent .env wrapper |
| backup_agent.sh | git + push + zip backup |
| health_check.sh | 7 類別系統診斷 |

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
| backtest/engine.py | Candle-by-candle 模擬器（reuse production 策略） |
| backtest/run_backtest.py | 單 pair CLI |
| backtest/compare_configs.py | A/B 4 configs × 8 pairs |
