# AXC Quick Start

## 1. 安裝 Python
- Mac: `brew install python3`
- Windows: https://python.org/downloads/ → 勾選 "Add to PATH"

## 2. 安裝依賴
```
pip install -r axc_requirements.txt
```

## 3. 設定 .env
```
cp secrets/.env.example secrets/.env
```
然後編輯 `secrets/.env`，填入你嘅 API keys。

### 取得 Telegram Bot Token
1. Telegram 搵 @BotFather
2. `/newbot` → 跟指示
3. 複製 token 到 `.env` 嘅 `TELEGRAM_BOT_TOKEN`

### 取得 Chat ID
1. Telegram 搵 @userinfobot
2. `/start` → 佢會話你知你嘅 chat ID
3. 填入 `.env` 嘅 `TELEGRAM_CHAT_ID`

### 取得 Aster DEX API Keys
1. https://asterdex.com → Settings → API
2. 建 API key（開 Futures 權限）
3. 複製 key + secret 到 `.env` 嘅 `ASTER_API_KEY` / `ASTER_API_SECRET`

## 4. 啟動

### Mac / Linux
```bash
AXC_HOME=$(pwd) python3 scripts/tg_bot.py
```

### Windows (CMD)
```cmd
set AXC_HOME=%cd%
python scripts\tg_bot.py
```

### Windows (PowerShell)
```powershell
$env:AXC_HOME = (Get-Location).Path
python scripts\tg_bot.py
```

## 5. 功能一覽

| Command | 功能 | 需要 OpenClaw? |
|---------|------|----------------|
| /pos | 持倉 | No |
| /bal | 餘額 | No |
| /pnl | 盈虧 | No |
| /sl | 止損 | No |
| /report | 完整報告 | No（部分欄位需要）|
| /ask | AI 分析 | No（需要 PROXY_API_KEY）|
| 自然語言落單 | 交易 | No（需要 PROXY_API_KEY）|
| /mode | 切換模式 | Yes |
| /pause /resume | 暫停恢復 | Yes |
| /scan | 掃描信號 | Yes |
| /health | 系統健康 | 部分 |
