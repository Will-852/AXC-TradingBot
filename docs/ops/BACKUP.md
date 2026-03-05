# 備份機制說明

## 備份架構

```
每次觸發
    |
backup_agent.sh
    |
    +-- git commit + push（GitHub）
    +-- zip 壓縮（本地 backups/）
         保留最近 10 個
```

## 觸發方式

### 手動
```bash
bash ~/.openclaw/scripts/backup_agent.sh
```

### 自動（每日凌晨3點）
```bash
# 查看 crontab
crontab -l

# 設定（如未設定）
(crontab -l 2>/dev/null; \
 echo "0 3 * * * bash ~/.openclaw/scripts/backup_agent.sh >> ~/.openclaw/logs/backup.log 2>&1") \
 | crontab -
```

### 對話觸發詞
- `backup`
- `今日完結`
- `聽日處理`

## 備份內容

| 內容 | 備份方式 | 位置 |
|------|----------|------|
| 代碼 + 文件 | GitHub | github.com/Will-852/openclaw |
| 本地 zip | 本地保留10個 | ~/.openclaw/backups/ |
| .env secrets | 手動 | iCloud |
| 記憶 jsonl | GitHub | memory/store/ |
| 向量索引 | 不備份 | 可重建 |

## 換電腦後

見 [災難恢復指南](../setup/RECOVERY.md)
