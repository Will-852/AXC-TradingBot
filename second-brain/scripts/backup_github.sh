#!/bin/bash

DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y-%m-%d\ %H:%M:%S)
BACKUP_DIR="$HOME/.openclaw/second-brain/backup"

mkdir -p "$BACKUP_DIR"

COMM_LOG="$BACKUP_DIR/COMMUNICATION_LOG_${DATE}.md"

if [ ! -f "$COMM_LOG" ]; then
  cat > "$COMM_LOG" << ENDLOG
# COMMUNICATION_LOG_${DATE}.md

每日對話記錄。

---

## 對話紀錄

ENDLOG
fi

cat >> "$COMM_LOG" << ENDLOG

---

### 📍 $TIMESTAMP - GitHub Backup Trigger

**內容待輸入**

ENDLOG

echo "✅ Backup entry created at $TIMESTAMP"
echo "📝 編輯位置: $COMM_LOG"
echo ""

# Git commit (本地，唔 push)
cd "$HOME/.openclaw"

if [ -d ".git" ]; then
  git add second-brain/
  git commit -m "Manual backup: ${TIMESTAMP}" 2>/dev/null
  echo "✅ Local commit 完成"
fi
