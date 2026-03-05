# Agent 職責說明

## 現有 Agents（9個）

### 主要（停咗會死）

#### 主腦（main）
- 模型：tier2 claude-haiku-4-5
- 職責：Telegram 介面、slash commands、報告、路由任務
- SOUL：agents/main/workspace/core/SOUL.md
- 更新頻率：每個 session / on-demand

#### 交易員（aster_trader）
- 模型：tier1 claude-sonnet-4-6
- 職責：交易決策 + 執行下單 + SL/TP 管理
- SOUL：agents/aster_trader/workspace/SOUL.md
- 更新頻率：有信號時觸發

### 重要（停咗會病）

#### 掃描器（aster_scanner）
- 模型：tier2 claude-haiku-4-5
- 職責：技術分析，掃描入場信號
- SOUL：agents/aster_scanner/workspace/SOUL.md
- 更新頻率：每 3 分鐘（scanner_runner.py）

#### 心跳（heartbeat）
- 模型：tier3 gpt-5-mini
- 職責：系統健康監測
- SOUL：agents/heartbeat/workspace/SOUL.md
- 更新頻率：每 15 分鐘

### 支援（停咗會弱）

#### 信號過濾（haiku_filter）
- 模型：tier2 claude-haiku-4-5
- 職責：信號壓縮，過濾噪音
- SOUL：agents/haiku_filter/SOUL.md

#### 分析師（analyst）
- 模型：tier1 claude-sonnet-4-6
- 職責：模式/政體偵測
- SOUL：agents/analyst/SOUL.md

#### 決策（decision）
- 模型：opus（最高級）
- 職責：最終交易決策
- SOUL：agents/decision/SOUL.md

### Binance（已整合）

#### 掃描器（binance_scanner）
- 模型：唔需要 LLM（純數學）
- 職責：Binance Futures 市場掃描，整合入 async_scanner.py
- SOUL：agents/binance_scanner/SOUL.md
- 更新頻率：同 aster_scanner 一致（每 3 分鐘）

#### 交易員（binance_trader）
- 模型：tier1 claude-sonnet-4-6（共用 pipeline）
- 職責：Binance Futures 交易執行
- SOUL：agents/binance_trader/SOUL.md
- 更新頻率：有信號時觸發
- 依賴：BINANCE_API_KEY + BINANCE_API_SECRET

### 新聞（已實現）

#### 新聞情緒（news_agent）
- 模型：tier2 claude-haiku-4-5
- 職責：RSS 新聞收集 + 情緒分析
- SOUL：agents/news_agent/workspace/SOUL.md
- 更新頻率：每 15 分鐘（LaunchAgent）

---

## Agent vs Script 分工原則

| 類型 | 用途 | 原因 |
|------|------|------|
| Agent | 需要判斷/思考 | LLM 有價值 |
| Script | 搬運數據/執行 | Python 更快更平 |

## Scripts（16個）

| Script | 職責 |
|--------|------|
| tg_bot.py | Telegram 交易 bot 主程式 |
| slash_cmd.py | Slash command 處理 |
| dashboard.py | Web dashboard |
| async_scanner.py | 並行掃描器（Aster + Binance） |
| scanner_runner.py | Scanner 調度（fcntl.flock） |
| light_scan.py | 輕量市場掃描 |
| heartbeat.py | 系統健康檢查 |
| indicator_calc.py | 技術指標計算（多平台） |
| weekly_strategy_review.py | 每週策略回顧 → STRATEGY.md |
| news_scraper.py | RSS 新聞收集 |
| news_sentiment.py | Claude Haiku 情緒分析 |
| telegram_sender.py | Telegram 發送工具 |
| memory_init.py | 記憶索引重建 |
| backup_agent.sh | 備份腳本 |
| trader_cycle/ | 17-step 交易 pipeline（+ReadSentimentStep） |

---

## 未來計劃

| Agent | 職責 | 狀態 |
|-------|------|------|
| recorder_agent | 交易報告生成 | 計劃中 |

| Script | 職責 | 狀態 |
|--------|------|------|
| twitter_scraper.py | 爬 Twitter 指定帳號 | Phase 2（news_agent 擴展） |

---

## 核心運作鏈

```
眼(aster_scanner)發現訊號
  -> 血液(SIGNAL.md)傳遞
  -> 心臟(aster_trader)執行
  -> 血液(TRADE_STATE.md)記錄
  -> 大腦(main)匯報
  -> 聲帶(Telegram)通知你
```
