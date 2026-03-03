# API_KEYS.md — API 金鑰管理
# 版本: 2026-03-02
# 安全: 此檔案唔應 commit 到任何 git repo
# 注意: Telegram bot token 在 OpenClaw config，唔在此檔

## Aster DEX

ASTER_API_KEY: a53cf43238a8149368cde7fb91e5896f76226e0507a9f8ce24ff5ea55fb2ce6d
ASTER_API_SECRET: 8d6491edd020b5d17514186d155c758408719b31e8b45cbedad5ce846bff427f
ASTER_TESTNET: false

## 使用方式

Python script 讀取：
```python
from dotenv import load_dotenv
import os
load_dotenv()
api_key = os.getenv('ASTER_API_KEY')
api_secret = os.getenv('ASTER_API_SECRET')
```

或直接從此檔案讀取（agent 用）：
- Agent 直接讀此 MD 檔案，解析 Key/Secret 行

## Telegram

- Bot token：儲於 OpenClaw config（唔在此）
- Chat ID：2060972655
- 用 telegram_sender.py 發送（見 {ROOT}/tools/telegram_sender.py）

## 安全提示

- 唔要把此檔案 share 或 copy 到不安全位置
- 定期 rotate API keys（建議每 3 個月）
- Aster DEX API key 只需要 Trade 權限，唔需要 Withdraw
