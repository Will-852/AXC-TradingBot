#!/bin/bash

DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$HOME/.openclaw/second-brain/backup"
PROFILES_DIR="$HOME/.openclaw/second-brain/profiles"

mkdir -p "$BACKUP_DIR"

# 備份 PERSONALITY 和 TRADING_KNOWLEDGE
cp "$PROFILES_DIR/PERSONALITY.md" "$BACKUP_DIR/PERSONALITY_${DATE}.md"
cp "$PROFILES_DIR/TRADING_KNOWLEDGE.md" "$BACKUP_DIR/TRADING_KNOWLEDGE_${DATE}.md"

# 檢查今日對話日誌是否存在
COMM_LOG="$BACKUP_DIR/COMMUNICATION_LOG_${DATE}.md"
if [ ! -f "$COMM_LOG" ]; then
  cat > "$COMM_LOG" << ENDLOG
# COMMUNICATION_LOG_${DATE}.md

每日對話記錄。

---

## 對話紀錄

（自動追加）

ENDLOG
fi

echo "✅ Backup created: $BACKUP_DIR"

# Git commit + push
cd "$HOME/.openclaw"

if [ -d ".git" ]; then
  git add second-brain/
  git commit -m "Daily backup: ${DATE} at $(date +%H:%M:%S)" 2>/dev/null
  
  # 檢查是否有 remote
  if git remote -v | grep -q origin; then
    git push origin main 2>/dev/null
    echo "✅ GitHub push 完成"
  fi
fi

# 檢查 backup size
$HOME/.openclaw/second-brain/scripts/backup_monitor.sh
