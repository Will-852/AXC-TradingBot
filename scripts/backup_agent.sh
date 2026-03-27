#!/bin/bash
# backup_agent.sh — AXC git backup + zip archive + guardians
# Usage: bash ~/projects/axc-trading/scripts/backup_agent.sh

set -e
AXC_HOME="${AXC_HOME:-$HOME/projects/axc-trading}"
cd "$AXC_HOME"

TIMESTAMP=$(date +%Y-%m-%d-%H%M)
DATE=$(date +%Y-%m-%d)

echo "🔄 AXC Backup — $TIMESTAMP"

# ── Guardian checks ─────────────────────────────

# Guardian 1: Update ai/MEMORY.md timestamp
MEMORY_FILE="$AXC_HOME/ai/MEMORY.md"
if [ -f "$MEMORY_FILE" ]; then
    sed -i '' "s/^> 最後更新：.*/> 最後更新：$(date '+%Y-%m-%d %H:%M')/" "$MEMORY_FILE"
fi

# Guardian 2: CLAUDE.md line count warning
CLAUDE_LINES=$(wc -l < "$AXC_HOME/CLAUDE.md" 2>/dev/null || echo 0)
if [ "$CLAUDE_LINES" -gt 200 ]; then
    echo "⚠️  CLAUDE.md 超過200行 ($CLAUDE_LINES 行)！請立即精簡。"
fi

# Guardian 3: STRATEGY.md empty warning
STRATEGY_LINES=$(wc -l < "$AXC_HOME/ai/STRATEGY.md" 2>/dev/null || echo 0)
if [ "$STRATEGY_LINES" -lt 10 ]; then
    echo "ℹ️  ai/STRATEGY.md 不足10行，weekly_review 尚未運行。"
fi

# Guardian 4: Stale old-path reference warning
OLD_REFS=$(grep -r "docs/ops\|docs/operations\|docs/telegram\|OPS_GUIDE\|ADDING_SYMBOLS" \
           "$AXC_HOME" --include="*.md" --include="*.py" \
           --include="*.sh" -l 2>/dev/null | grep -v ".git" | grep -v "backups/" | grep -v "backup_agent.sh" | grep -v "health_check.sh" | wc -l)
if [ "$OLD_REFS" -gt 0 ]; then
    echo "⚠️  發現 $OLD_REFS 個文件仍引用舊路徑，請更新。"
fi

# ── Git backup ──────────────────────────────────
git add -A
git commit -m "[$DATE] backup" 2>/dev/null && echo "✅ Git commit done" || echo "⚪ No changes to commit"
git push origin main 2>/dev/null && echo "✅ Pushed to GitHub" || echo "⚠️  Push failed (check auth)"

# ── Zip archive ─────────────────────────────────
mkdir -p backups
zip -rq "backups/backup-${TIMESTAMP}.zip" \
  config/ agents/ shared/ scripts/ \
  ai/ docs/ CLAUDE.md \
  2>/dev/null
echo "✅ Zip: backups/backup-${TIMESTAMP}.zip"

# Clean old zips (keep last 10, any naming pattern)
cd backups
ls -t *.zip 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null
cd "$AXC_HOME"

# ── Off-site sync (iCloud Drive) ──────────────
ICLOUD_BACKUP="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AXC-Backup"
if ! mkdir -p "$ICLOUD_BACKUP" 2>/dev/null; then
  echo "⚠️  iCloud Drive not available, skipping off-site sync"
  echo "✅ Backup complete (local only)"
  exit 0
fi

rsync -a --delete \
  "$AXC_HOME/shared/" "$ICLOUD_BACKUP/shared/"
rsync -a --delete \
  "$AXC_HOME/config/" "$ICLOUD_BACKUP/config/"
rsync -a --delete \
  "$AXC_HOME/ai/" "$ICLOUD_BACKUP/ai/"

# Secrets — single file, no --delete
if [ -f "$AXC_HOME/secrets/.env" ]; then
  mkdir -p "$ICLOUD_BACKUP/secrets"
  rsync -a "$AXC_HOME/secrets/.env" "$ICLOUD_BACKUP/secrets/.env"
fi

# Latest zip only (save iCloud space)
LATEST_ZIP=$(ls -t "$AXC_HOME/backups/"*.zip 2>/dev/null | head -1)
if [ -n "$LATEST_ZIP" ]; then
  mkdir -p "$ICLOUD_BACKUP/backups"
  rsync -a "$LATEST_ZIP" "$ICLOUD_BACKUP/backups/"
fi

echo "✅ iCloud sync done → $ICLOUD_BACKUP"
echo "✅ Backup complete"
