#!/bin/bash

DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$HOME/.openclaw/second-brain/backup"
PROFILES_DIR="$HOME/.openclaw/second-brain/profiles"

mkdir -p "$BACKUP_DIR"

cp "$PROFILES_DIR/PERSONALITY.md" "$BACKUP_DIR/PERSONALITY_${DATE}.md"
cp "$PROFILES_DIR/TRADING_KNOWLEDGE.md" "$BACKUP_DIR/TRADING_KNOWLEDGE_${DATE}.md"

COMM_LOG="$BACKUP_DIR/COMMUNICATION_LOG_${DATE}.md"
if [ ! -f "$COMM_LOG" ]; then
  cat > "$COMM_LOG" << ENDLOG
# COMMUNICATION_LOG_${DATE}.md

記錄當日 Claude 對話內容。

---

## 對話記錄

（自動追加）

ENDLOG
fi

echo "✅ Backup created: $BACKUP_DIR"

$HOME/.openclaw/second-brain/scripts/backup_monitor.sh
