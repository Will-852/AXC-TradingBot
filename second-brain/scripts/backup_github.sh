#!/bin/bash

DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y-%m-%d\ %H:%M:%S)
BACKUP_DIR="$HOME/.openclaw/second-brain/backup"

mkdir -p "$BACKUP_DIR"

COMM_LOG="$BACKUP_DIR/COMMUNICATION_LOG_${DATE}.md"

# 如果檔案唔存在，建立
if [ ! -f "$COMM_LOG" ]; then
  cat > "$COMM_LOG" << ENDLOG
# COMMUNICATION_LOG_${DATE}.md

每日對話記錄。

---

## 對話紀錄

ENDLOG
fi

# 加入新行（留待用戶粘貼內容）
cat >> "$COMM_LOG" << ENDLOG

---

### 📍 $TIMESTAMP - GitHub Backup Trigger

**內容待輸入**

ENDLOG

echo "✅ Backup entry created at $TIMESTAMP"
echo "📝 編輯位置: $COMM_LOG"
echo ""
echo "💡 提示：你可以手動編輯檔案加入對話內容，或用："
echo "   open $COMM_LOG"
echo ""

# Git commit + push
cd "$HOME/.openclaw"

if [ -d ".git" ]; then
  git add second-brain/
  git commit -m "Manual backup: ${TIMESTAMP}" 2>/dev/null
  
  if git remote -v | grep -q origin; then
    git push origin main 2>/dev/null
    echo "✅ GitHub push 完成"
  else
    echo "⚠️  尚未設置 GitHub remote (git remote add origin ...)"
  fi
fi
