# 災難恢復指南
> 換電腦 / 文件遺失 / 系統重裝

## 恢復時間：約 15 分鐘

## 步驟

### 1. Clone 代碼（2分鐘）

```bash
git clone https://github.com/Will-852/openclaw ~/.openclaw
pip3 install -r ~/.openclaw/requirements.txt --break-system-packages
```

### 2. 恢復 .env（1分鐘）

```bash
# 從 iCloud 恢復
cp ~/Library/Mobile\ Documents/com~apple~CloudDocs/openclaw_secrets.env \
   ~/.openclaw/secrets/.env
```

### 3. 重建記憶索引（5分鐘）

```bash
# RAG 向量索引需要重建（原始記憶 jsonl 已在 GitHub）
python3 ~/.openclaw/scripts/memory_init.py
```

### 4. 驗證

```bash
python3 ~/.openclaw/scripts/dashboard.py &
sleep 3
curl -s http://127.0.0.1:5555/api/data | python3 -m json.tool
```

### 5. 啟動 Telegram Bot

```bash
python3 ~/.openclaw/scripts/tg_bot.py &
```

### 6. 恢復 LaunchAgents（選填）

```bash
# 如需系統開機自啟
cp ~/.openclaw/launchagents/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.openclaw.*.plist
```

### 7. 恢復 crontab

```bash
(crontab -l 2>/dev/null; \
 echo "0 3 * * * bash ~/.openclaw/scripts/backup_agent.sh >> ~/.openclaw/logs/backup.log 2>&1") \
 | crontab -
```

## 注意

- `.env` 唔喺 GitHub，必須從 iCloud 或其他備份取回
- 記憶內容（jsonl）喺 GitHub，但向量索引需重建
- 重建向量索引約需 5 分鐘（視記憶條數）
- LaunchAgent plist 需要手動恢復
