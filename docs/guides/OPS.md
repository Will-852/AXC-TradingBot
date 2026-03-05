# 維運操作指南

## Proxy 切換（緊急）

### 症狀
- Telegram bot 無回應
- Claude 分析功能失效

### 一鍵切換至官方 API

```bash
sed -i '' \
  's|PROXY_BASE_URL=.*|PROXY_BASE_URL=https://api.anthropic.com|g' \
  ~/.openclaw/secrets/.env
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram
```

### 切換回 Proxy

```bash
sed -i '' \
  's|PROXY_BASE_URL=.*|PROXY_BASE_URL=https://tao.plus7.plus/v1|g' \
  ~/.openclaw/secrets/.env
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram
```

---

## VOYAGE_API_KEY Rotate

```bash
# 1. 去 dash.voyageai.com 建立新 key

# 2. 替換
sed -i '' 's|VOYAGE_API_KEY=.*|VOYAGE_API_KEY=新key|g' \
  ~/.openclaw/secrets/.env

# 3. 清除 cache
rm ~/.openclaw/memory/index/embed_cache.json

# 4. 重新備份 .env 到 iCloud
cp ~/.openclaw/secrets/.env \
  ~/Library/Mobile\ Documents/com~apple~CloudDocs/openclaw_secrets.env
```

---

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
grep "KEY_NAME" ~/.openclaw/scripts/trader_cycle/config/settings.py
```

---

## 出事排查

| 症狀 | 檢查 |
|------|------|
| Telegram 冇反應 | `launchctl list ai.openclaw.telegram`，睇 PID |
| 409 Conflict | 確認 tg_bot.py 同 gateway 用唔同 token |
| 下單失敗 | `tail -50 ~/.openclaw/logs/telegram.err.log` |
| Scanner 卡住 | `rm ~/.openclaw/shared/scanner_runner.lock` |
| TRADE_STATE 過期 | 通過 Telegram 下單觸發自動同步 |
| Dashboard 冇數據 | `python3 ~/.openclaw/scripts/dashboard.py` |
