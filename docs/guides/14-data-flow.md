<!--
title: 數據流 + 文件結構
section: 機械體架構
order: 14
audience: human,claude,github
-->

# 數據流 + 文件結構

## shared/ 文件（實時數據）

| 文件 | 寫入者 | 讀取者 |
|------|--------|--------|
| SIGNAL.md | async_scanner / light_scan | trader_cycle |
| TRADE_STATE.md | trader_cycle | main, dashboard |
| SCAN_CONFIG.md | async_scanner + trader_cycle | dashboard |
| SCAN_LOG.md | async_scanner / light_scan | dashboard |
| prices_cache.json | async_scanner | dashboard, light_scan |
| news_feed.json | news_scraper | news_sentiment |
| news_sentiment.json | news_sentiment | trader_cycle (Step 9) |
| activity_log.jsonl | write_activity | dashboard |
| SYSTEM_STATUS.md | heartbeat | dashboard |

## 文件結構

```
~/projects/axc-trading/
├── openclaw.json          ← Gateway 設定（唔好動，放 ~/.openclaw/）
├── CLAUDE.md              ← Claude Code 入口
├── ai/                    ← AI 讀（CONTEXT / MEMORY / RULES / STRATEGY）
├── docs/                  ← 人類文件（setup / guides / architecture）
├── agents/                ← 10 agents，各有 SOUL.md
├── scripts/               ← 15 scripts + trader_cycle/（28 files）
├── config/                ← params.py + modes/（RANGE/TREND/VOLATILE）
├── secrets/.env           ← 9 API keys
├── shared/                ← 實時數據（8 文件，見上方數據流）
├── memory/                ← RAG（retriever + writer + embedder + store/）
├── canvas/                ← 前端（index.html / details.html）
├── logs/                  ← scanner.log / telegram.log / heartbeat
└── backups/               ← 自動備份（保留 10 份）
```

## 參數管理

所有可調參數集中喺 `config/params.py`（25+ 參數）：

| Section | 關鍵參數 | 當前值 |
|---------|----------|--------|
| 掃描 | SCAN_INTERVAL_SEC | 20 秒/交易所（9 交易所輪轉 = 180 秒/輪） |
| 掃描 | SCHEDULED_CYCLE_HOURS | [0,3,6,9,12,15,18,21] |
| BB | BB_TOUCH_TOL_DEFAULT / XRP | 0.005 / 0.008 |
| 倉位 | MAX_POSITION_SIZE_USDT | $50 |
| 倉位 | MAX_OPEN_POSITIONS | 3 |
| 倉位 | RISK_PER_TRADE_PCT | 2% |
| 打法 | ACTIVE_PROFILE | AGGRESSIVE |
| 幣種 | ASTER_SYMBOLS | BTC, ETH, XRP, XAG, XAU |
| 幣種 | BINANCE_SYMBOLS | BTC, ETH, SOL, POL |
| 新聞 | NEWS_SCRAPE_INTERVAL_MIN | 15 分鐘 |

改完 params.py 必須重啟相關服務先生效。

## RAG 記憶系統

| 組件 | 路徑 | 功能 |
|------|------|------|
| retriever.py | memory/ | RAG 查詢（cosine similarity） |
| writer.py | memory/ | 寫入信號 / 對話 / 交易記錄 |
| embedder.py | memory/ | voyage-3 向量化（有快取） |
| trades.jsonl | memory/store/ | 交易記錄 |
| signals.jsonl | memory/store/ | 信號記錄 |
| embeddings.npy | memory/index/ | numpy 向量索引 |

```bash
# 查詢記憶
python3 ~/projects/axc-trading/memory/retriever.py "上次 BTC 入場嘅結果"

# 重建索引
python3 ~/projects/axc-trading/scripts/memory_init.py
```
