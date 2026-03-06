<!--
title: 15 個 Scripts
section: 機械體架構
order: 12
audience: human,claude,github
-->

# 15 個 Scripts

| Script | 功能 |
|--------|------|
| async_scanner.py | 主掃描循環（每 3 分鐘） |
| dashboard.py | Web 儀表板（port 5555） |
| tg_bot.py | Telegram Bot（14 指令 + NLP） |
| indicator_calc.py | BB / RSI / ADX / EMA / Stoch / ATR |
| scanner_runner.py | 掃描編排（light_scan → trader_cycle，fcntl 鎖） |
| light_scan.py | 輕量掃描（純數學，零 LLM） |
| binance_feed.py | Binance WebSocket 數據（唔需 API key） |
| heartbeat.py | 心跳監察（23:00-08:00 靜音） |
| telegram_sender.py | Telegram 發送封裝 |
| news_scraper.py | RSS 新聞抓取（原子寫入） |
| news_sentiment.py | 新聞情緒分析（Haiku） |
| weekly_strategy_review.py | 每週策略回顧 → ai/STRATEGY.md |
| write_activity.py | 活動日誌（append mode，max 500） |
| memory_init.py | RAG 記憶初始化 |
| slash_cmd.py | 指令解析（零 LLM） |

## Trader Cycle 子模組（28 個 Python 文件）

| 子目錄 | 功能 | 關鍵文件 |
|--------|------|----------|
| core/ | 主循環 + pipeline | main.py (--dry-run / --live), pipeline.py |
| exchange/ | 交易所 API | aster_client.py (HMAC-SHA256), execute_trade.py (30秒下單流程) |
| strategies/ | RANGE + TREND | range_strategy.py, trend_strategy.py, mode_detector.py |
| risk/ | 風控 | risk_manager.py (熔斷器), position_sizer.py (2% Kelly sizing) |
| state/ | 狀態管理 | trade_state.py, position_sync.py (crash recovery) |
| notify/ | 通知 | telegram.py（繁體中文模板） |
| config/ | 引擎參數 | settings.py (60+ 常數), pairs.py (幣種精度) |
