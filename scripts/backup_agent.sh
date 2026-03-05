#!/bin/bash
# backup_agent.sh — OpenClaw git backup + zip archive
# Usage: bash ~/.openclaw/scripts/backup_agent.sh

set -e
cd ~/.openclaw

TIMESTAMP=$(date +%Y-%m-%d-%H%M)
DATE=$(date +%Y-%m-%d)

echo "🔄 OpenClaw Backup — $TIMESTAMP"

# Git backup
git add -A
git commit -m "[$DATE] backup" 2>/dev/null && echo "✅ Git commit done" || echo "⚪ No changes to commit"
git push origin main 2>/dev/null && echo "✅ Pushed to GitHub" || echo "⚠️  Push failed (check auth)"

# Zip archive
mkdir -p backups
zip -rq "backups/backup-${TIMESTAMP}.zip" \
  openclaw.json config/ agents/ shared/ scripts/ \
  ARCHITECTURE_DECISIONS.md CLAUDE.md \
  2>/dev/null
echo "✅ Zip: backups/backup-${TIMESTAMP}.zip"

# Clean old zips (keep last 10)
cd backups
ls -t backup-*.zip 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null
echo "✅ Backup complete"
