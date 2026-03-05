# 環境變數設定指南

## 文件位置
`~/.openclaw/secrets/.env`

> 此文件唔會上傳 GitHub（已加入 .gitignore）
> 必須另外備份（建議 iCloud 或 1Password）

## 完整變數說明

| 變數 | 必填 | 說明 | 取得方式 |
|------|------|------|----------|
| `PROXY_API_KEY` | 必填 | Claude API | console.anthropic.com |
| `VOYAGE_API_KEY` | 必填 | 語義向量 | dash.voyageai.com |
| `TELEGRAM_BOT_TOKEN` | 選填 | Telegram Bot | @BotFather |
| `TELEGRAM_CHAT_ID` | 選填 | 你的 Chat ID | @userinfobot |
| `PROXY_BASE_URL` | 選填 | Proxy endpoint | 預設 api.anthropic.com |

## 範本

```bash
PROXY_API_KEY=sk-ant-...
PROXY_BASE_URL=https://api.anthropic.com
VOYAGE_API_KEY=pa-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## iCloud 備份

```bash
cp ~/.openclaw/secrets/.env \
  ~/Library/Mobile\ Documents/com~apple~CloudDocs/openclaw_secrets.env
```
