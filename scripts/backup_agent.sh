#!/bin/bash
# backup_agent.sh — OpenClaw git backup + zip archive + guardians
# Usage: bash ~/.openclaw/scripts/backup_agent.sh

set -e
cd ~/.openclaw

TIMESTAMP=$(date +%Y-%m-%d-%H%M)
DATE=$(date +%Y-%m-%d)

echo "🔄 OpenClaw Backup — $TIMESTAMP"

# ── Guardian checks ─────────────────────────────

# Guardian 1: Update ai/MEMORY.md timestamp
MEMORY_FILE="$HOME/.openclaw/ai/MEMORY.md"
if [ -f "$MEMORY_FILE" ]; then
    sed -i '' "s/^> 最後更新：.*/> 最後更新：$(date '+%Y-%m-%d %H:%M')/" "$MEMORY_FILE"
fi

# Guardian 2: CLAUDE.md line count warning
CLAUDE_LINES=$(wc -l < "$HOME/.openclaw/CLAUDE.md" 2>/dev/null || echo 0)
if [ "$CLAUDE_LINES" -gt 200 ]; then
    echo "⚠️  CLAUDE.md 超過200行 ($CLAUDE_LINES 行)！請立即精簡。"
fi

# Guardian 3: STRATEGY.md empty warning
STRATEGY_LINES=$(wc -l < "$HOME/.openclaw/ai/STRATEGY.md" 2>/dev/null || echo 0)
if [ "$STRATEGY_LINES" -lt 10 ]; then
    echo "ℹ️  ai/STRATEGY.md 不足10行，weekly_review 尚未運行。"
fi

# Guardian 4: Stale old-path reference warning
OLD_REFS=$(grep -r "docs/ops\|docs/operations\|docs/telegram\|OPS_GUIDE\|ADDING_SYMBOLS" \
           "$HOME/.openclaw" --include="*.md" --include="*.py" \
           --include="*.sh" -l 2>/dev/null | grep -v ".git" | grep -v "backups/" | grep -v "backup_agent.sh" | wc -l)
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
  openclaw.json config/ agents/ shared/ scripts/ \
  ai/ docs/ CLAUDE.md \
  2>/dev/null
echo "✅ Zip: backups/backup-${TIMESTAMP}.zip"

# Clean old zips (keep last 10)
cd backups
ls -t backup-*.zip 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null
echo "✅ Backup complete"
