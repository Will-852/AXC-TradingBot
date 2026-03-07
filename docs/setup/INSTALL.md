# 安裝指南

## 系統要求
- macOS 12+
- Python 3.11+
- 500MB 磁碟空間

## 安裝步驟

### 1. Clone

```bash
git clone https://github.com/Will-852/AXC-TradingBot ~/.openclaw
cd ~/.openclaw
pip3 install -r requirements.txt --break-system-packages
```

### 2. API Keys

```bash
cp docs/friends/.env.example secrets/.env
nano secrets/.env
```

### 3. 啟動 Dashboard

```bash
python3 ~/.openclaw/scripts/dashboard.py
```

瀏覽器開啟：http://127.0.0.1:5555

### 4. 啟動 Telegram Bot（選填）

```bash
python3 ~/.openclaw/scripts/tg_bot.py
```

## 驗證安裝

```bash
# Dashboard 正常
curl -s http://127.0.0.1:5555/api/data | python3 -m json.tool

# RAG 記憶正常
python3 -c "
import sys
sys.path.insert(0, str(__import__('pathlib').Path.home()/'.openclaw/memory'))
from retriever import retrieve_full
print('RAG 正常')
"
```
