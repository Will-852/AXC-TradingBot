<!--
title: 系統運作流程
section: 快速入門
order: 2
audience: human,claude,github
-->

# 系統運作流程

## 完整流程圖

```
┌──────────────────────────────────────────────────────────────┐
│                    AXC Trading System                        │
│                                                              │
│  ┌─────────────────┐     ┌──────────────────────────────┐   │
│  │ 掃描層（常駐）    │     │ trader_cycle 16 步（每 30 min）│   │
│  │                 │     │                              │   │
│  │ async_scanner ──┼────▶│ 1. 讀取持倉狀態              │   │
│  │  9 交易所輪轉     │     │ 2. 安全檢查（熔斷/冷卻）     │   │
│  │  20s 一個        │     │ 3. 過濾死市場                │   │
│  │  寫 prices_cache │     │ 4. 拉市場數據（按幣種路由）   │   │
│  │                 │     │ 5. 計算 25+ 技術指標         │   │
│  │ light_scan ─────┼────▶│ 6. 5 票偵測 RANGE/TREND      │   │
│  │  3 min Aster     │     │ 7-8. 策略產生信號            │   │
│  │  5 pairs 觸發    │     │ 9. 新聞情緒過濾              │   │
│  └─────────────────┘     │ 10. 評分排名                  │   │
│                          │ 11. 計算倉位大小              │   │
│  ┌─────────────────┐     │ 12. 調整現有倉位 SL/TP       │   │
│  │ 新聞層           │     │ 13. 落盤執行                 │   │
│  │                 │     │ 14. 超時/費率強制平倉         │   │
│  │ news_scraper ───┼─┐  │ 15. 寫入狀態                  │   │
│  │  RSS 抓新聞      │ │  │ 16. Telegram 通知            │   │
│  │                 │ │  └──────────────────────────────┘   │
│  │ news_sentiment ─┼─┘                                     │
│  │  🤖 Haiku 分析   │      ┌────────────────────────┐      │
│  └─────────────────┘      │ 監察層                  │      │
│                           │ heartbeat（15 min）     │      │
│  ┌─────────────────┐      │ 檢查服務 + 告警         │      │
│  │ 介面層           │      └────────────────────────┘      │
│  │                 │                                       │
│  │ tg_bot.py ──────┼─── 🤖 Haiku（自然語言對話）            │
│  │  14 slash 指令   │       slash 指令 = 零 AI cost          │
│  │                 │                                       │
│  │ dashboard.py ───┼─── http://localhost:5555              │
│  └─────────────────┘                                       │
└──────────────────────────────────────────────────────────────┘

🤖 = 呢個步驟調用 LLM（要 API Key，有費用）
其餘全部 = 純 Python（零 AI 費用）
```

## 每個步驟做咩？（talk15 版）

想像你請咗一個助手幫你炒幣。佢每 30 分鐘做一輪檢查：

| 步驟 | 人類版本 | 系統做咩 |
|------|---------|---------|
| 1 | 「我而家有冇持倉？」 | 讀 TRADE_STATE.md |
| 2 | 「我仲啱唔啱做嘢？」 | 檢查有冇觸發熔斷器、連虧冷卻 |
| 3 | 「個市有冇嘢做？」 | 成交量太低 / 資金費率太極端 / 同組已有倉 → 跳過 |
| 4 | 「而家咩價？」 | 拉 ticker + funding rate（Aster 幣用 Aster API，Binance 幣用 Binance API） |
| 5 | 「畫圖分析」 | 計算 BB、RSI、MACD、EMA、ADX、ATR、Stochastic... |
| 6 | 「個市係橫行定趨勢？」 | 5 個指標投票：RSI、MACD、成交量、費率、BB 寬度 |
| 7-8 | 「有冇入場機會？」 | Range 策略 or Trend 策略產生信號 |
| 9 | 「新聞有冇利好利淡？」 | 讀 news_sentiment.json（🤖 由 Haiku 寫入） |
| 10 | 「邊隻最值得做？」 | 7 隻幣排名，揀分數最高嗰隻 |
| 11 | 「落幾多？」 | 根據 ATR 計 SL/TP 距離 → 反推倉位大小（唔超過 2-3% 風險） |
| 12 | 「已有嘅倉要唔要調？」 | 移動止蝕到保本、延伸 TP、提前出場 |
| 13 | 「落單！」 | 設 isolated margin → 設槓桿 → 落盤 → 驗證成交 → 設 SL → 設 TP |
| 14 | 「超時嘅倉要唔要平？」 | >72 小時 or 資金費 > 50% unrealized PnL → 強制平 |
| 15 | 「記低結果」 | 更新 TRADE_STATE.md + SCAN_CONFIG.md |
| 16 | 「通知老闆」 | Telegram 推送 |

## 兩層掃描

| 層 | Script | 類型 | 頻率 | 範圍 |
|----|--------|------|------|------|
| Layer 1 | async_scanner.py | 常駐 daemon | 9 exchanges × 20s = 3 min 一輪 | 全部交易所 |
| Layer 2 | light_scan.py | cron 排程 | 每 3 分鐘 | Aster only（BTC/ETH/XRP/XAG/XAU） |

兩層獨立運作。async_scanner 係主力，light_scan 係快速補充。

## 平台路由

market_data.py 會自動判斷幣種用邊個 API：

| 幣種 | 路由去 |
|------|--------|
| BTC, ETH, XRP, XAG, XAU | Aster API |
| SOL, POL | Binance API |
| BTC, ETH（重疊） | 按 ASTER_SYMBOLS 優先用 Aster |

改路由：編輯 `config/params.py` 嘅 `ASTER_SYMBOLS` / `BINANCE_SYMBOLS`。
