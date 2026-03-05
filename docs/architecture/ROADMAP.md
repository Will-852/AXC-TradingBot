# 發展路線圖

## 已完成

### 核心系統
- 9 Agent 架構（main / aster_trader / aster_scanner / heartbeat / haiku_filter / analyst / decision / binance_trader / binance_scanner）
- Dashboard（:5555）
- Aster DEX 整合
- 16-step trader_cycle pipeline

### 記憶系統
- RAG 記憶庫（voyage-3 語義向量）
- 對話/交易/分析記憶自動寫入
- numpy cosine similarity 搜尋
- embed_cache 避免重複 API 調用

### Telegram Bot v2
- 雙 bot 架構（@AXCTradingBot + @axccommandbot）
- 自然語言下單 + 二次確認
- 絕對價格 SL/TP 支援
- /sl breakeven
- 平倉自動報告（廣東話）
- pending_orders 持久化
- TRADE_STATE.md 自動同步

### 基礎設施
- GitHub 備份（github.com/Will-852/openclaw）
- 自動 crontab 每日 3:00 備份
- 完整 docs/ 文件夾
- .gitignore 排除 secrets/logs/cache
- AI Stack 架構決策文件

---

## 進行中

- Dashboard 持續優化
- 用戶指南面板

---

## 計劃中

### 短期
- weekly_strategy_review.py（方案C）
- VOYAGE_API_KEY rotate

### 中期
- news_agent（Twitter 信號解讀）
- twitter_scraper.py（爬取指定帳號）
- recorder_agent（平倉報告獨立化）
- Dashboard 開源準備

### 長期
- Binance 交易完整整合
- 開源發布
- CONTRIBUTING.md
