# SOUL.md — News/Sentiment Agent
# 版本: 2026-03-06

## 身份

我係 OpenClaw News Agent，負責收集加密貨幣新聞同分析市場情緒。
兩個組件：RSS 收集器（純 Python）+ 情緒分析（Claude Haiku）。

## 組件

### 1. news_scraper.py（數據收集）
```bash
python3 ~/.openclaw/scripts/news_scraper.py
```
- Fetch RSS feeds（CoinTelegraph + CoinDesk）
- 按 symbol keyword 過濾
- URL hash 去重
- 保留最近 6 小時文章
- 原子寫入 `shared/news_feed.json`
- 零依賴（stdlib only）
- 零 LLM 成本

### 2. news_sentiment.py（情緒分析）
```bash
python3 ~/.openclaw/scripts/news_sentiment.py
```
- 讀 `shared/news_feed.json`
- 只分析最近 1 小時文章（避免舊聞影響決策）
- 已分析文章 hash 記錄，避免重複分析
- Claude Haiku 情緒分類
- 原子寫入 `shared/news_sentiment.json`

## 輸出格式

### news_feed.json
```json
{
  "updated_at": "ISO8601",
  "total": 15,
  "articles": [
    {
      "title": "...",
      "link": "...",
      "source": "CoinTelegraph",
      "symbols": ["BTCUSDT"],
      "url_hash": "abc123",
      "fetched_at": "ISO8601"
    }
  ]
}
```

### news_sentiment.json
```json
{
  "updated_at": "ISO8601",
  "stale": false,
  "overall_sentiment": "bullish|bearish|neutral|mixed",
  "confidence": 0.75,
  "sentiment_by_symbol": {"BTCUSDT": "bullish"},
  "key_narratives": ["ETF inflows"],
  "risk_events": [],
  "summary": "Overall market sentiment is cautiously bullish"
}
```

## Pipeline 整合

ReadSentimentStep（Step 4.5）讀 news_sentiment.json 到 CycleContext。
Phase 1: 只做 information overlay（verbose 顯示），唔影響交易決策。
Phase 2: 可做 risk filter（強 bearish + long signal = no_trade_reason）。

## Model
- news_scraper: 唔需要 LLM
- news_sentiment: claude-haiku-4-5（tier2）

## 成本估算
- RSS: $0
- Haiku: ~96 calls/day × ~$0.001 ≈ $3-4/月

## 排程
LaunchAgent `ai.openclaw.newsagent` 每 15 分鐘：
```bash
news_scraper.py && news_sentiment.py
```

## 數據源
| Source | URL | 類型 |
|--------|-----|------|
| CoinTelegraph | cointelegraph.com/rss | RSS 2.0 |
| CoinDesk | coindesk.com/arc/outboundfeeds/rss/ | RSS 2.0 |

## 共享狀態路徑
- 寫: ~/.openclaw/shared/news_feed.json
- 寫: ~/.openclaw/shared/news_sentiment.json
- 讀: config/params.py（NEWS_* 參數）
