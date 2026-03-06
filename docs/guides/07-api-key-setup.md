<!--
title: 換 API Key 完整指南
section: 操作指南
order: 7
audience: human,claude,github
-->

# 換 API Key 完整指南

OpenClaw 有**兩個地方**存 API key，兩邊都要改。

## 兩個設定文件

| 文件 | 管邊個 | 點改 |
|------|--------|------|
| `openclaw.json` | Gateway + AI agents | `openclaw config set` |
| `secrets/.env` | Python scripts（掃描、TG、新聞） | `nano` 編輯 |

## 換 AI API Key（最常見）

```bash
# 第 1 步：改 Gateway（3 個 tier）
openclaw config set models.providers.tier1.apiKey "sk-新key"
openclaw config set models.providers.tier2.apiKey "sk-新key"
openclaw config set models.providers.tier3.apiKey "sk-新key"

# 第 2 步：改 Scripts
nano ~/.openclaw/secrets/.env
# 搵 PROXY_API_KEY=舊key → 改成新key
# Ctrl+O 存檔，Ctrl+X 退出

# 第 3 步：重啟全部服務
launchctl stop ai.openclaw.scanner
launchctl stop ai.openclaw.telegram
launchctl stop ai.openclaw.gateway
sleep 3
launchctl start ai.openclaw.scanner
launchctl start ai.openclaw.telegram
launchctl start ai.openclaw.gateway
```

兩邊嘅 key 必須一致，否則部分服務用舊 key 失敗。

## 換 API Proxy 地址

```bash
# Gateway
openclaw config set models.providers.tier1.baseUrl "https://新地址/v1"
openclaw config set models.providers.tier2.baseUrl "https://新地址/v1"
openclaw config set models.providers.tier3.baseUrl "https://新地址/v1"

# Scripts
nano ~/.openclaw/secrets/.env    # 改 PROXY_BASE_URL

# 重啟
launchctl stop ai.openclaw.scanner && launchctl start ai.openclaw.scanner
```

### 緊急切換至官方 API

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

### Proxy 連通測試

```bash
API_KEY=$(grep PROXY_API_KEY ~/.openclaw/secrets/.env | cut -d= -f2)
curl -s https://tao.plus7.plus/v1/messages \
  -H "x-api-key: $API_KEY" \
  -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}' \
  | python3 -m json.tool
```

## 換交易所 API

```bash
# 只需改 secrets/.env（Gateway 唔直接用交易所 key）
nano ~/.openclaw/secrets/.env    # 改 ASTER_API_KEY + ASTER_API_SECRET

# 重啟
launchctl stop ai.openclaw.scanner && launchctl start ai.openclaw.scanner
```

## 換 Telegram Bot Token

```bash
# 1. 同 @BotFather 拎新 token
# 2. Gateway
openclaw config set channels.telegram.botToken "新token"
# 3. Scripts
nano ~/.openclaw/secrets/.env    # 改 TELEGRAM_BOT_TOKEN
# 4. 重啟
launchctl stop ai.openclaw.telegram && launchctl start ai.openclaw.telegram
```

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

## secrets/.env 完整 Key 清單（9 個）

| Key | 用途 | 換完重啟 |
|-----|------|----------|
| PROXY_API_KEY | Claude / GPT 推理 | scanner + tg_bot + news |
| PROXY_BASE_URL | API proxy 地址 | scanner + tg_bot + news |
| ASTER_API_KEY | Aster DEX 交易 | scanner + tradercycle |
| ASTER_API_SECRET | Aster DEX 簽名 | scanner + tradercycle |
| BINANCE_API_KEY | Binance 交易 | scanner |
| BINANCE_API_SECRET | Binance 簽名 | scanner |
| TELEGRAM_BOT_TOKEN | Telegram Bot | tg_bot |
| TELEGRAM_CHAT_ID | 通知對象 | tg_bot |
| VOYAGE_API_KEY | 向量 embedding（RAG） | memory_init |

永遠唔好將 key commit 到 Git 或分享畀人。
