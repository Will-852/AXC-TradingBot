# AXC — Autonomous eXchange Controller
> 最後更新：2026-03-08

## 係咩？

AXC 係一個 Telegram 交易控制 bot。你用 Telegram 發訊息，佢幫你：
- 查倉位、餘額、盈虧
- 自然語言落單（「做多 ETH $50」）
- 切換交易模式
- AI 分析市場
- 自動推送平倉報告同系統告警

Bot handle：@AXCTradingBot

---

## 快速開始

### 前置條件
1. OpenClaw 系統已安裝並運行
2. Dashboard 已運行（`:5555`）
3. Telegram app 已安裝

### 環境變數（`~/.openclaw/secrets/.env`）
```
TELEGRAM_BOT_TOKEN=<BotFather 拎到嘅 token>
TELEGRAM_CHAT_ID=<你嘅 Telegram chat ID>
PROXY_API_KEY=<Claude API proxy key>
PROXY_BASE_URL=https://tao.plus7.plus/v1
ASTER_API_KEY=<交易所 API key>
ASTER_API_SECRET=<交易所 API secret>
VOYAGE_API_KEY=<voyage-3 embedding key>
```

點拎 TELEGRAM_CHAT_ID：
1. Telegram 搵 @userinfobot
2. 發任何訊息
3. 佢會回覆你嘅 chat ID（數字）

### 啟動
```bash
# 確保 dashboard 已運行
curl http://127.0.0.1:5555/api/health

# 啟動 bot
cd ~/.openclaw
python3 scripts/tg_bot.py
```

Bot 啟動後會 log：
```
🦞 OpenClaw Telegram v2.0 啟動
  Chat ID: 2060972655
  Claude: claude-haiku-4-5-20251001 via https://tao.plus7.plus/v1
```

### 第一次用
打開 Telegram → 搵 @AXCTradingBot → 發 `/start`

---

## 所有指令

### 查詢（零 AI 費用，即時回覆）

| 指令 | 做咩 | 例子 |
|------|------|------|
| `/start` | 顯示指令列表 | `/start` |
| `/report` | 完整交易報告 | `/report` |
| `/pos` | 當前持倉 | `/pos` |
| `/bal` | USDT 餘額 | `/bal` |
| `/pnl` | 今日盈虧 | `/pnl` |
| `/log` | 最近活動記錄 | `/log` |
| `/scan` | 觸發手動掃描 | `/scan` |
| `/health` | 系統健康（所有 agent 狀態） | `/health` |

### 控制

| 指令 | 做咩 | 例子 |
|------|------|------|
| `/mode` | 顯示當前模式 + 選擇按鈕 | `/mode` |
| `/mode AGGRESSIVE` | 直接切換模式 | `/mode BALANCED` |
| `/pause` | 暫停自動交易 | `/pause` |
| `/resume` | 恢復自動交易 | `/resume` |
| `/sl breakeven` | 所有倉位 SL 移到入場價 | `/sl breakeven` |
| `/sl breakeven XAGUSDT` | 指定幣對 SL 移到入場價 | `/sl breakeven ETHUSDT` |
| `/cancel` | 取消等待中嘅訂單確認 | `/cancel` |

三種模式：
- CONSERVATIVE — 低風險，窄 trigger
- BALANCED — 中等
- AGGRESSIVE — 高風險，寬 trigger

### AI 分析

| 指令 | 做咩 | 例子 |
|------|------|------|
| `/ask <問題>` | AI 分析（帶 RAG 記憶 + 系統狀態） | `/ask ETH 短線點睇？` |
| `/forget` | 清除對話記憶（最近 5 輪） | `/forget` |

直接打字（唔加 `/`）都得：
- 「BTC 跌咗咁多應唔應該撈底？」→ AI 分析
- 「做多 ETH $50」→ 偵測為落單（見下面）

---

## 自然語言落單

### 點用
直接打字描述你想做嘅交易：

```
做多 ETH $50
short XAG 全倉
賣 BTC $30 SL 2%
long XRPUSDT $20 TP 3%
```

### 流程
```
你：做多 ETH $50
       ↓
Bot：[解析中...]
       ↓
Bot：🔴 確認下單？
     ━━━━━━━━━━━━━━
     PAIR: ETHUSDT
     SIDE: LONG
     AMOUNT: $50
     LEVERAGE: 10x
     SL_EST: -2.5%
     TP_EST: +4%
     ━━━━━━━━━━━━━━
     ⏱ 60秒內確認

     [✅ 確認下單] [❌ 取消]
       ↓
你：按 ✅
       ↓
Bot：✅ 已執行 LONG ETHUSDT $50
```

### 關鍵字觸發
Bot 偵測到以下關鍵字就當落單：
`買` `賣` `做多` `做空` `long` `short` `平倉` `close` `入場` `開倉` `buy` `sell` `all in`

### 確認超時
- 普通訂單：60 秒
- 高風險（>=80% 餘額 或 平倉）：90 秒
- 超時自動取消

### SL/TP
- 你唔指定 → 自動用 SL 2.5%、TP 4%
- 你可以指定絕對價或百分比：`SL 2089` 或 `SL 2.5%`

---

## 自動推送

Bot 喺背景每 60 秒檢查一次，自動推送：

| 事件 | 推送內容 |
|------|----------|
| 倉位被平（SL/TP 觸發） | Claude 生成嘅廣東話平倉報告（入場分析、盈虧、建議） |
| 掃描器停咗 >10 分鐘 | `⚠️ 掃描器已 X 分鐘無更新！請檢查 lightscan。` |

你唔需要做任何嘢，bot 會自己通知你。

---

## 安全

- **單一白名單**：只有 `TELEGRAM_CHAT_ID` 指定嘅 chat 可以操作
- 其他人發訊息：靜默忽略（唔回覆，log warning）
- Bot token 同所有 API key 存喺 `~/.openclaw/secrets/.env`（唔 commit 到 git）

---

## 架構

```
┌─────────────────────────────────────────────────┐
│                    Telegram                      │
│                  (@AXCTradingBot)                │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                 tg_bot.py (AXC)                  │
│                                                  │
│  指令處理    AI 分析     落單流程    自動告警     │
│  (/pos等)   (Claude)   (確認→執行)  (60s loop)  │
│                                                  │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐  │
│  │axc_client│  │ slash_cmd  │  │ AsterClient  │  │
│  │ (API)    │  │ (查詢)    │  │ (交易執行)   │  │
│  └────┬─────┘  └─────┬─────┘  └──────┬───────┘  │
└───────┼──────────────┼───────────────┼───────────┘
        │              │               │
        ▼              │               ▼
┌───────────────┐      │      ┌────────────────┐
│  Dashboard    │      │      │   Aster DEX    │
│  :5555/api/*  │      │      │   (交易所)     │
│               │      │      │                │
│ /api/state    │      └──────│► REST API      │
│ /api/config   │             │                │
│ /api/health   │             │                │
└───────────────┘             └────────────────┘
```

axc_client 透過 HTTP API 讀寫 OpenClaw 狀態，唔直接讀文件。
如果 dashboard down，自動 fallback 到直接 file read。

---

## API Endpoints（dashboard :5555）

AXC 用嘅 endpoints：

| Endpoint | Method | 用途 |
|----------|--------|------|
| `/api/state` | GET | 交易狀態 + 信號 + 當前模式 |
| `/api/config` | GET | 所有交易參數（profile-aware） |
| `/api/config/mode` | POST | 切換交易模式 `{"mode": "AGGRESSIVE"}` |
| `/api/config/trading` | POST | 開關交易 `{"enabled": true}` |
| `/api/scan-log` | GET | 最近掃描日誌 |
| `/api/health` | GET | Agent 狀態 + scanner 心跳 + 記憶庫計數 |

測試：
```bash
curl http://127.0.0.1:5555/api/state | python3 -m json.tool
curl http://127.0.0.1:5555/api/health | python3 -m json.tool
curl -X POST http://127.0.0.1:5555/api/config/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "BALANCED"}'
```

---

## 常見問題

### Bot 無反應
```bash
# 最常見原因：多個 instance 撞 409
# 1. 停 LaunchAgent
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.telegram.plist

# 2. 殺所有 tg_bot
pkill -9 -f tg_bot.py
sleep 2

# 3. 確認殺乾淨
pgrep -f tg_bot.py || echo "OK"

# 4. 重新啟動（只啟一個）
cd ~/.openclaw && python3 scripts/tg_bot.py
```

### AI 回覆好慢
- Claude Haiku 通常 2-3 秒
- 如果 proxy 慢，檢查 `PROXY_BASE_URL` 是否可達
- `/ask` 要額外做 RAG 搜索，會慢 1-2 秒

### 落單失敗
- 檢查 `ASTER_API_KEY` 同 `ASTER_API_SECRET` 是否正確
- `/bal` 確認有足夠餘額
- `/pos` 確認唔超過最大持倉數

### /health 顯示紅色
- 🟢 ✅ = 10 分鐘內有活動
- 🟡 ⚠️ = 10-30 分鐘無活動
- 🔴 ❌ = 30+ 分鐘無活動
- 掃描器紅色 → 檢查 `tail -20 ~/.openclaw/logs/scanner.log`

### 記憶清除
- `/forget` 只清短期對話記憶（最近 5 輪，10 分鐘過期）
- 長期 RAG 記憶唔受影響

---

## 文件位置

| 文件 | 用途 |
|------|------|
| `scripts/tg_bot.py` | Bot 主程式 |
| `scripts/axc_client.py` | OpenClaw API client |
| `scripts/slash_cmd.py` | 交易所查詢指令 |
| `secrets/.env` | 所有 API keys |
| `shared/pending_orders.json` | 等待確認嘅訂單（重啟後保留） |
| `logs/tg_bot.log` | Bot 日誌（via LaunchAgent） |
| `config/params.py` | 交易參數（mode 切換改呢度） |

---

## 開發路線圖

| Phase | 內容 | 狀態 |
|-------|------|------|
| 1 | OpenClaw State API（dashboard.py 加 endpoints） | 完成 ✅ |
| 2 | axc_client.py（API client） | 完成 ✅ |
| 3 | tg_bot.py 改用 API（保留 file fallback） | 完成 ✅ |
| 4 | Memory API + write_activity API | 未開始 |
| 5 | AXC 搬出成獨立 repo | 未開始 |

### 依賴清單（剩餘耦合點）

Phase 3 已解耦（6 個 file read + 2 個 file write → API）：
- ✅ TRADE_STATE.md read → `/api/state`
- ✅ SIGNAL.md read → `/api/state`
- ✅ params.py read → `/api/config`
- ✅ params.py write (mode) → `/api/config/mode`
- ✅ params.py write (trading) → `/api/config/trading`
- ✅ SCAN_LOG.md read → `/api/scan-log`
- ✅ Agent timestamps → `/api/health`
- ✅ Scanner heartbeat → `/api/health`

Phase 4 待解耦（3 個 import）：
- memory.writer（write_conversation / write_analysis / write_trade）
- memory.retriever（RAG 搜索）
- write_activity（活動日誌）

保持不變（AXC 核心功能）：
- slash_cmd.py — 交易所查詢
- AsterClient — 交易執行
- call_claude() — 已獨立（urllib + env）
