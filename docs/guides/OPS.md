# 維運操作指南

## Proxy 切換（緊急）

### 症狀
- Telegram bot 無回應
- Claude 分析功能失效

### 一鍵切換至官方 API

```bash
sed -i '' \
  's|PROXY_BASE_URL=.*|PROXY_BASE_URL=https://api.anthropic.com|g' \
  ~/projects/axc-trading/secrets/.env
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram
```

### 切換回 Proxy

```bash
sed -i '' \
  's|PROXY_BASE_URL=.*|PROXY_BASE_URL=https://tao.plus7.plus/v1|g' \
  ~/projects/axc-trading/secrets/.env
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram
```

---

## VOYAGE_API_KEY Rotate

```bash
# 1. 去 dash.voyageai.com 建立新 key

# 2. 替換
sed -i '' 's|VOYAGE_API_KEY=.*|VOYAGE_API_KEY=新key|g' \
  ~/projects/axc-trading/secrets/.env

# 3. 清除 cache
rm ~/projects/axc-trading/memory/index/embed_cache.json

# 4. 重新備份 .env 到 iCloud
cp ~/projects/axc-trading/secrets/.env \
  ~/Library/Mobile\ Documents/com~apple~CloudDocs/openclaw_secrets.env
```

---

## Proxy 測試

```bash
API_KEY=$(grep PROXY_API_KEY ~/projects/axc-trading/secrets/.env | cut -d= -f2)
curl -s https://tao.plus7.plus/v1/messages \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}' \
  | python3 -m json.tool
```

---

## 服務管理

```bash
# 查看全部
launchctl list | grep openclaw

# 重啟 Telegram bot
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram

# 重啟 Gateway
launchctl stop ai.openclaw.gateway && launchctl start ai.openclaw.gateway

# 重啟 Scanner
launchctl stop ai.openclaw.lightscan && launchctl start ai.openclaw.lightscan
```

## LaunchAgent 列表

| Service | 功能 |
|---------|------|
| ai.openclaw.telegram | Telegram 交易 bot（tg_bot.py） |
| ai.openclaw.gateway | 系統 gateway + @axccommandbot |
| ai.openclaw.lightscan | 每 180 秒市場掃描 |
| ai.openclaw.tradercycle | 交易執行 |
| ai.openclaw.heartbeat | 系統健康監控 |
| ai.openclaw.report | 定時報告 |

---

## 環境變數速查

| 變數 | 用途 |
|------|------|
| `PROXY_API_KEY` | Claude API（注意：唔係 ANTHROPIC_API_KEY） |
| `VOYAGE_API_KEY` | 向量 embedding |
| `TELEGRAM_BOT_TOKEN` | @AXCTradingBot |
| `TELEGRAM_CHAT_ID` | 2060972655 |
| `PROXY_BASE_URL` | Proxy endpoint |

---

## 改交易參數

想改交易行為（SL/TP/leverage）：
```
用戶層（UI 可見）：  config/params.py 的 TRADING_PROFILES
引擎層（內部邏輯）：scripts/trader_cycle/config/settings.py
```

注意：TRADING_PROFILES 只能覆蓋 settings.py 已有的 key。
新增 key 前先確認 settings.py 有對應定義：
```bash
grep "KEY_NAME" ~/projects/axc-trading/scripts/trader_cycle/config/settings.py
```

---

## 出事排查

| 症狀 | 檢查 |
|------|------|
| Telegram 冇反應 | `launchctl list ai.openclaw.telegram`，睇 PID |
| 409 Conflict | 確認 tg_bot.py 同 gateway 用唔同 token |
| 下單失敗 | `tail -50 ~/projects/axc-trading/logs/telegram.err.log` |
| Scanner 卡住 | `rm ~/projects/axc-trading/shared/scanner_runner.lock` |
| TRADE_STATE 過期 | 通過 Telegram 下單觸發自動同步 |
| Dashboard 冇數據 | `python3 ~/projects/axc-trading/scripts/dashboard.py` |
| TG Bot Conflict 409 | 見下方「TG Bot 重複 Instance」 |

---

## TG Bot 重複 Instance（常見！）

### 症狀
- Telegram bot 時有時冇反應
- `tg_bot.log` 出現 `Conflict: terminated by other getUpdates request`
- `pgrep -f tg_bot.py` 返回 2 個或以上 PID

### 原因
Telegram Bot API 只容許 **一個** getUpdates 長輪詢連接。多個 instance 會互相踢走對方。

常見觸發場景：
1. **LaunchAgent 自動啟動** + **手動啟動** → 兩個 process 撞
2. **Claude Code 重啟 tg_bot** 後 LaunchAgent 偵測到舊 process 死咗，再 respawn 一個
3. **kill 唔乾淨** — `kill` 後 LaunchAgent 即刻 respawn，形成 race condition

### 解決步驟
```bash
# 1. 停 LaunchAgent（阻止自動 respawn）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.telegram.plist

# 2. 殺晒所有殘留 process
pkill -9 -f tg_bot.py
sleep 2

# 3. 確認全部死曬
pgrep -f tg_bot.py || echo "All killed"

# 4. 手動啟動唯一一個 instance
nohup python3 ~/projects/axc-trading/scripts/tg_bot.py > logs/tg_bot.log 2>&1 &

# 5. 確認啟動正常
sleep 3 && tail -3 logs/tg_bot.log
```

### 恢復 LaunchAgent 自動管理（optional）
```bash
# 唔再手動管理時，恢復 LaunchAgent
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.telegram.plist
# ⚠️ 之後唔好再手動 python3 tg_bot.py，否則又撞
```

### 預防
- 要嘛全用 LaunchAgent 管理，要嘛全手動。**唔好混用**
- 重啟 tg_bot 前必須先 `launchctl bootout` 停 LaunchAgent
- Claude Code 改完 tg_bot.py 後，用 `launchctl stop/start` 而唔係 `kill + python3`
