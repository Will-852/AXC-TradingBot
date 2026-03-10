<!--
title: AXC 點樣運行？（鬧鐘 vs 班長）
section: 架構理解
order: 18
audience: human,claude,github
-->

# AXC 點樣運行？

## 一句講曬

AXC 用「鬧鐘 + 共享文件夾」運行。每個鬧鐘定時響，跑一個 Python 腳本，腳本之間透過 shared/ 入面嘅文件溝通。

## 三層架構

```
┌─────────────────────────────────────────────┐
│  OpenClaw Gateway（可選，唔裝都得）           │
│  功能：@axccommandbot、Agent sessions         │
│  類比：班長 — 識自己安排任務                   │
│  冇佢？交易系統完全唔受影響。                  │
└──────────────────┬──────────────────────────┘
                   │ 可選
┌──────────────────┴──────────────────────────┐
│  AXC 交易系統（核心，必須）                    │
│  功能：掃描、交易、監控、新聞、Telegram         │
│  類比：一堆鬧鐘 — 固定時間做固定嘢             │
└──────────────────┬──────────────────────────┘
                   │ AI 功能需要
┌──────────────────┴──────────────────────────┐
│  Proxy API（LLM 接口）                       │
│  功能：轉發你嘅 request 去 Claude/GPT         │
│  類比：電話線 — 打電話畀 AI 問嘢              │
│  冇佢？核心交易照跑，AI 分析停。               │
└─────────────────────────────────────────────┘
```

三層之間**完全獨立**。Proxy API 唔係 OpenClaw 嘅一部分。

## 鬧鐘系統（LaunchAgents）

macOS 內建嘅排程系統。每個 plist 文件 = 一個鬧鐘。

```
~/Library/LaunchAgents/
│
├── ai.openclaw.scanner.plist       每 5 分鐘    → 掃描 9 個交易所
├── ai.openclaw.tradercycle.plist   觸發時跑      → 分析 + 下單
├── ai.openclaw.heartbeat.plist     每 15 分鐘   → 檢查倉位 + SL
├── ai.openclaw.newsagent.plist     每 15 分鐘   → 抓新聞 + AI 分析
├── ai.openclaw.telegram.plist      長駐          → @AXCTradingBot
├── ai.openclaw.newsbot.plist       長駐          → @AXCnews_bot
├── ai.openclaw.lightscan.plist     每 3 分鐘    → 輕量價格更新
├── ai.openclaw.report.plist        每日          → 生成報告
└── ai.openclaw.strategyreview.plist 每週一       → 策略回顧
```

### 鬧鐘點管？

```bash
# 睇邊啲鬧鐘在跑
launchctl list | grep openclaw

# 停一個
launchctl stop ai.openclaw.scanner

# 啟動一個
launchctl start ai.openclaw.scanner

# 完全移除（重開機都唔跑）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.scanner.plist

# 重新裝返
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.scanner.plist
```

## 共享文件夾（shared/）

腳本之間唔直接對話。佢哋透過 shared/ 入面嘅文件溝通，好似大家寫紙仔放喺桌面。

```
shared/
├── TRADE_STATE.md       誰寫：trader_cycle    誰讀：heartbeat, dashboard, tg_bot
├── SIGNAL.md            誰寫：scanner          誰讀：trader_cycle, dashboard
├── SCAN_CONFIG.md       誰寫：scanner          誰讀：heartbeat, dashboard
├── prices_cache.json    誰寫：lightscan        誰讀：dashboard
├── news_feed.json       誰寫：news_scraper     誰讀：news_sentiment
├── news_sentiment.json  誰寫：news_sentiment   誰讀：dashboard, news_bot, trader
├── news_manual.json     誰寫：news_bot         誰讀：news_sentiment
└── balance_baseline.json 誰寫：dashboard       誰讀：dashboard
```

### 資料流（一個完整循環）

```
① scanner 掃描市場
     ↓ 寫 SIGNAL.md
② trader_cycle 讀信號 → 決定開倉
     ↓ 寫 TRADE_STATE.md
③ heartbeat 讀倉位 → 檢查止損
     ↓ 有問題發 Telegram 警報
④ dashboard 讀所有文件 → 顯示畀你睇
```

### 新聞流（獨立循環）

```
① news_scraper 抓 RSS
     ↓ 寫 news_feed.json
② 你 send 訊息畀 @AXCnews_bot
     ↓ 寫 news_manual.json
③ news_sentiment 讀兩邊 → Claude Haiku 分析
     ↓ 寫 news_sentiment.json
④ dashboard 顯示情緒卡片
⑤ news_bot 偵測變化 → 推送通知
⑥ trader_cycle 讀情緒 → 影響交易決策
```

## 班長 vs 鬧鐘

| | 班長（OpenClaw Agent sessions） | 鬧鐘（LaunchAgents） |
|--|------|------|
| 識自己諗 | ✅ 「市場大跌，我主動加密掃描」 | ❌ 固定時間做固定嘢 |
| 複雜任務 | ✅ 「先分析→寫報告→發 Telegram」 | ❌ 每個腳本只做一件事 |
| 穩定性 | ❌ 複雜，Gateway 壞就全停 | ✅ 每個獨立，一個壞唔影響其他 |
| 需要裝 | OpenClaw binary + Gateway | macOS 內建，零依賴 |

**AXC 選擇鬧鐘模式。** 交易系統最重要係穩定，唔係聰明。

## Telegram Bot 分工

```
@AXCTradingBot（tg_bot.py）
  ├── /pos /bal /pnl     查詢倉位、餘額、盈虧
  ├── /mode /sl /pause    控制交易模式、止損
  ├── /trade /close       手動下單、平倉
  └── /ask                AI 分析（用 Haiku）

@AXCnews_bot（news_bot.py）
  ├── /news               查詢新聞情緒
  ├── /submit             提交新聞
  ├── 直接打字             自動收錄
  └── 自動推送             情緒變化通知

@axccommandbot（OpenClaw Gateway）
  └── 需要 OpenClaw 先可用，而家停緊
```

## Collaborator 快速上手

```bash
# 1. Clone
git clone https://github.com/Will-852/AXC-TradingBot.git ~/projects/axc-trading

# 2. 填 API key
cp docs/friends/.env.example secrets/.env
nano secrets/.env    # 填入你自己嘅 key

# 3. 啟動 Dashboard（最低要求）
python3 scripts/dashboard.py

# 4.（選填）啟動 Telegram bot
python3 scripts/tg_bot.py

# 5.（選填）啟動新聞 bot
python3 scripts/news_bot.py
```

唔需要 OpenClaw。唔需要特別嘅平台。任何 macOS + Python 3.11+ 就跑到。
