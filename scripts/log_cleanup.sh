#!/usr/bin/env bash
# log_cleanup.sh — 定期清理 logs/（保留最近 12 小時）
# 設計決定：用 Python 解析多種 timestamp 格式，bash 處理 stale files
# 建議 crontab: 0 4 * * 0  bash ~/projects/axc-trading/scripts/log_cleanup.sh
#                           ^^^^^ 每週日 04:00

set -euo pipefail
LOG_DIR="${AXC_HOME:-$HOME/projects/axc-trading}/logs"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$LOG_DIR" || exit 1

# ── 時間制截斷（保留 12 小時）──
/opt/homebrew/bin/python3 "${SCRIPT_DIR}/_log_trim.py" "$LOG_DIR" 12

# ── 刪除已知廢棄文件 ──
for stale in cache-trace.jsonl config-audit.jsonl newsagent.log tg_bot.log strategyreview.log; do
    [[ -f "$stale" ]] && rm -f "$stale" && echo "[cleanup] deleted stale: $stale"
done

# ── 刪除 >30 日冇改過嘅 .log 文件（排除狀態文件）──
find "$LOG_DIR" -maxdepth 1 -name "*.log" -mtime +30 -exec rm -f {} \; -print \
    | while read -r f; do echo "[cleanup] deleted old: $(basename "$f")"; done

# ── 清空空目錄 ──
find "$LOG_DIR" -maxdepth 1 -type d -empty -not -name "." -exec rmdir {} \; 2>/dev/null

echo "[cleanup] dir size: $(du -sh "$LOG_DIR" | cut -f1)"
