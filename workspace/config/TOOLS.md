# TOOLS.md — 確認可用工具及 Endpoints
# 版本: 2026-03-03

## Aster DEX API

Base URL: https://fapi.asterdex.com

### 確認可用（✅ 測試通過）

```
GET  /fapi/v1/premiumIndex          → Funding Rate + Mark/Index price
GET  /fapi/v1/openInterest          → 未平倉合約量
GET  /fapi/v1/depth?limit=20        → Order Book（動態 S/R）
GET  /fapi/v1/aggTrades             → 歷史成交（鯨魚偵測）
GET  /fapi/v1/ticker/24hr           → 24小時統計
GET  /fapi/v1/klines                → K線數據
GET  /fapi/v1/exchangeInfo          → 交易對規格
GET  /fapi/v1/forceOrders           → 強平記錄（需 Auth）
POST /fapi/v1/order                 → 下單（需 Auth）
```

### 禁用 endpoints（❌ 404 確認不可用）

```
globalLongShortAccountRatio    → 404
takerlongshortRatio            → 404
topLongShortAccountRatio       → 404
```

## Telegram Bot

- Bot token: 儲於 OpenClaw config（唔在 MD）
- Chat ID: 2060972655
- 發送工具: {ROOT}/tools/telegram_sender.py
- 語言: 繁體中文（所有匯報）

## OpenClaw Gateway

- WebSocket: ws://127.0.0.1:18789
- 用途: OpenClaw agent 通訊

## Python 工具

- telegram_sender.py: 發送 Telegram 訊息
- 依賴: requests, python-dotenv（見 requirements.txt）

## 模型 Alias（OpenClaw 已登記）

| Alias | 完整模型名 | 狀態 |
|-------|-----------|------|
| sonnet | anthropic/claude-sonnet-4-6 | ✅ 可用 |
| haiku45 | anthropic/claude-haiku-4-5-20251001 | ✅ 可用 |
| haiku3 | anthropic/claude-3-haiku-20240307 | ❌ 404 禁用 |

**注意：** haiku3 已確認 404。TIER_2 和 TIER_3 暫時都用 haiku45。
詳見 {ROOT}/routing/MODEL_ROUTER.md

## Telegram Slash Commands

收到以下指令時**立即執行**，唔需要額外確認或解釋。
所有回覆使用 SOUL.md 定義嘅 code block 格式（`<pre>` 包裹、emoji、上限 25 行）。

```
/report  — 發送完整狀態報告（SOUL.md 格式）
/pos     — 從 Aster DEX 查詢當前倉位
/bal     — 查詢 USDT 餘額及可用保證金
/run     — 執行一次 live trader cycle
/dryrun  — 執行一次 dry-run cycle
/new     — 掃描 4 個交易對尋找入場信號，無信號回覆 "NO SIGNAL · [timestamp]"
/stop    — 暫停自動交易（SILENT_MODE: ON，停 cron）
/resume  — 恢復自動交易（SILENT_MODE: OFF，啟動 cron）
/sl      — 顯示所有當前 stop-loss 水平
/pnl     — 顯示今日已實現 + 未實現 P&L 摘要
/log     — 顯示 trader cycle log 最後 10 行
/mode    — 顯示當前市場模式（RANGE/TREND）及指標投票
/health  — 系統健康檢查（gateway、Telegram、Aster API、model tiers）
/reset   — 清除 TRIGGER_PENDING 並重設 cycle 狀態（bot 卡住時使用）
```

### 回覆格式規則

- 使用 SOUL.md code block 格式
- 上限 25 行
- 🟢 正數、🔴 負數、⚪ 中性
- 除 /log 和 /health 外，唔加長篇解釋
