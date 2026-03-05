# OpenClaw 運維指南
# 最後更新：2026-03-05

---

## 日常指令

| 指令 | 用途 |
|------|------|
| `bash ~/.openclaw/scripts/backup_agent.sh` | 手動備份（git+push+zip） |
| `launchctl list \| grep openclaw` | 查看所有服務狀態 |
| `tail -20 ~/.openclaw/logs/telegram.err.log` | Telegram bot log |
| `tail -20 ~/.openclaw/logs/lightscan.log` | Scanner log |

## 服務管理

```bash
# 重啟 Telegram bot
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram

# 重啟 Gateway
launchctl stop ai.openclaw.gateway && launchctl start ai.openclaw.gateway

# 查看全部
launchctl list | grep openclaw
```

## LaunchAgent 列表

| Service | Plist | 功能 |
|---------|-------|------|
| ai.openclaw.telegram | tg_bot.py | Telegram 交易 bot |
| ai.openclaw.gateway | openclaw gateway | 系統 gateway + @axccommandbot |
| ai.openclaw.lightscan | scanner_runner.py | 每 180 秒市場掃描 |
| ai.openclaw.tradercycle | trader_cycle | 交易執行 |
| ai.openclaw.heartbeat | heartbeat.py | 系統健康監控 |
| ai.openclaw.report | report | 定時報告 |

## 備份策略

- **自動**: crontab 每日 03:00 執行 backup_agent.sh
- **Git**: commit + push to github.com/Will-852/openclaw (private)
- **Zip**: ~/.openclaw/backups/，保留最近 10 個
- **Log**: ~/.openclaw/logs/backup.log

## 關鍵路徑

```
~/.openclaw/
├── openclaw.json          # 主設定
├── ARCHITECTURE_DECISIONS.md  # 架構決策（唔好改）
├── CLAUDE.md              # 人類地圖
├── DEV_LOG.md             # 開發日誌
├── secrets/.env           # API keys（唔入 git）
├── scripts/
│   ├── tg_bot.py          # Telegram bot 主程式
│   ├── slash_cmd.py       # Slash command 處理
│   ├── dashboard.py       # Web dashboard
│   ├── scanner_runner.py  # Scanner 調度
│   └── backup_agent.sh    # 備份腳本
├── shared/
│   ├── TRADE_STATE.md     # 即時交易狀態
│   └── SIGNAL.md          # 信號狀態
├── config/
│   └── params.py          # 所有數字參數
├── canvas/
│   └── index.html         # Dashboard UI
└── memory/
    ├── writer.py          # 記憶寫入
    ├── retriever.py       # 記憶檢索（voyage-3）
    └── index/             # 向量索引
```

## 出事排查

| 症狀 | 檢查 |
|------|------|
| Telegram 冇反應 | `launchctl list ai.openclaw.telegram`，睇 PID 存唔存在 |
| 409 Conflict | 兩個 bot 用同一 token，確認 tg_bot.py 同 gateway 用唔同 token |
| 下單失敗 | `tail -50 ~/.openclaw/logs/telegram.err.log` |
| Scanner 卡住 | `rm ~/.openclaw/shared/scanner_runner.lock` |
| TRADE_STATE 過期 | tg_bot.py 應自動同步，手動：通過 Telegram 下單觸發 |
| Dashboard 冇數據 | `python3 ~/.openclaw/scripts/dashboard.py` 手動跑 |
| Proxy 測試 | `curl` 測試見下面 |

## Proxy 測試

```bash
API_KEY=$(grep PROXY_API_KEY ~/.openclaw/secrets/.env | cut -d= -f2)
curl -s https://tao.plus7.plus/v1/messages \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}' \
  | python3 -m json.tool
```

## 環境變數（.env）

```
PROXY_API_KEY=sk-...     # Claude/GPT proxy（唔係 ANTHROPIC_API_KEY）
TELEGRAM_BOT_TOKEN=...   # @AXCTradingBot
TELEGRAM_CHAT_ID=2060972655
VOYAGE_API_KEY=...       # voyage-3 embeddings
PROXY_BASE_URL=https://tao.plus7.plus/v1
```
