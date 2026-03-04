#!/bin/bash

BACKUP_DIR="$HOME/.openclaw/second-brain/backup"
LIMIT_MB=100

if [ ! -d "$BACKUP_DIR" ]; then
  exit 0
fi

SIZE_MB=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1 | sed 's/[^0-9].*//')
SIZE_INT=${SIZE_MB%.*}

if [ "$SIZE_INT" -ge "$LIMIT_MB" ]; then
  echo ""
  echo "╔════════════════════════════════════════════════════════╗"
  echo "║          📌 BACKUP SIZE WARNING                        ║"
  echo "╠════════════════════════════════════════════════════════╣"
  echo "║ Size: ${SIZE_INT}MB (Limit: ${LIMIT_MB}MB)              ║"
  echo "║ Location: $BACKUP_DIR                                   ║"
  echo "║                                                        ║"
  echo "║ Action: Archive old files + git commit                ║"
  echo "╚════════════════════════════════════════════════════════╝"
  echo ""
  exit 1
fi

exit 0
