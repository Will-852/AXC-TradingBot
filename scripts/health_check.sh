#!/bin/bash
# ~/projects/axc-trading/scripts/health_check.sh
# AXC 系統健康檢查（7 類別）
# 每次大改後執行：bash scripts/health_check.sh
# 結果自動寫入 logs/health_check.log

AXC_HOME="${AXC_HOME:-$HOME/projects/axc-trading}"
LOG="$AXC_HOME/logs/health_check.log"
PASS=0; FAIL=0; WARN=0
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')

log() { echo "$1" | tee -a "$LOG"; }
pass() { log "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { log "  ❌ $1"; FAIL=$((FAIL + 1)); }
warn() { log "  ⚠️  $1"; WARN=$((WARN + 1)); }

echo "" >> "$LOG"
log "=========================================="
log "健康檢查 $TIMESTAMP"
log "=========================================="

# ── 1. 路徑完整性 ────────────────────────────
log ""
log "[ 路徑完整性 ]"

OLD_REFS=$(grep -r "docs/ops\|docs/operations\|OPS_GUIDE\|ADDING_SYMBOLS" \
           "$AXC_HOME" \
           --include="*.py" --include="*.sh" --include="*.md" \
           -l 2>/dev/null | grep -v ".git" | grep -v "backups/" | grep -v "backup_agent.sh" | grep -v "health_check.sh" | wc -l)
[ "$OLD_REFS" -eq 0 ] && pass "零舊路徑引用" || \
    fail "有 $OLD_REFS 個文件仍引用舊路徑"

for f in \
    "ai/CONTEXT.md" "ai/MEMORY.md" "ai/RULES.md" "ai/STRATEGY.md" \
    "config/params.py" \
    "scripts/load_env.sh" "scripts/async_scanner.py" "scripts/tg_bot.py" \
    "scripts/backup_agent.sh" "scripts/integration_test.sh"; do
    [ -f "$AXC_HOME/$f" ] && pass "$f 存在" || fail "$f 唔存在"
done

# ── 2. LaunchAgent ───────────────────────────
log ""
log "[ LaunchAgent ]"

CORRECT_PY=$(which python3)
for plist in scanner telegram heartbeat lightscan report tradercycle newsmonitor; do
    PLIST="$HOME/Library/LaunchAgents/ai.openclaw.$plist.plist"
    if [ -f "$PLIST" ]; then
        # 檢查有冇 python3（gateway 係 node，冇 python3）
        if grep -q "python3" "$PLIST"; then
            if grep -q "python3\." "$PLIST"; then
                fail "$plist.plist 仲用緊 python3.X（應係 $CORRECT_PY）"
            elif grep -q "$CORRECT_PY" "$PLIST"; then
                pass "$plist.plist python3 路徑正確"
            else
                fail "$plist.plist python3 路徑唔係 $CORRECT_PY"
            fi
        fi
        grep -q "ThrottleInterval" "$PLIST" && \
            pass "$plist.plist 有 ThrottleInterval" || \
            warn "$plist.plist 冇 ThrottleInterval"
    fi
done

# ── 3. 服務運行狀態 ──────────────────────────
log ""
log "[ 服務狀態 ]"

for label in ai.openclaw.scanner ai.openclaw.telegram ai.openclaw.gateway; do
    PID=$(launchctl list 2>/dev/null | grep "$label" | awk '{print $1}')
    if [ -n "$PID" ] && [ "$PID" != "-" ]; then
        pass "$label 運行中 (PID: $PID)"
    else
        fail "$label 未運行"
    fi
done

HB="$AXC_HOME/logs/scanner_heartbeat.txt"
if [ -f "$HB" ]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$HB" 2>/dev/null || echo 0) ))
    [ "$AGE" -lt 300 ] && pass "心跳正常（${AGE}秒前）" || \
        warn "心跳過舊（${AGE}秒前，超過5分鐘）"
else
    fail "心跳文件唔存在"
fi

# ── 4. env 載入 ──────────────────────────────
log ""
log "[ 環境變數 ]"

ENV_KEYS=$(bash "$AXC_HOME/scripts/load_env.sh" env 2>/dev/null | \
           grep -cE "PROXY_API_KEY|VOYAGE_API_KEY|TELEGRAM_BOT_TOKEN")
[ "$ENV_KEYS" -ge 3 ] && pass ".env 載入正常（$ENV_KEYS 個 key）" || \
    fail ".env 載入問題（只有 $ENV_KEYS 個 key，需要 ≥3）"

# ── 5. 文件大小守護 ──────────────────────────
log ""
log "[ 文件大小 ]"

CLAUDE_LINES=$(wc -l < "$AXC_HOME/CLAUDE.md" 2>/dev/null || echo 0)
[ "$CLAUDE_LINES" -le 200 ] && \
    pass "CLAUDE.md ${CLAUDE_LINES}行（上限200）" || \
    fail "CLAUDE.md ${CLAUDE_LINES}行，超過200行上限！"

GLOBAL_CLAUDE="$HOME/.claude/CLAUDE.md"
if [ -f "$GLOBAL_CLAUDE" ]; then
    GC_LINES=$(wc -l < "$GLOBAL_CLAUDE" 2>/dev/null || echo 0)
    [ "$GC_LINES" -le 150 ] && \
        pass "全局 CLAUDE.md ${GC_LINES}行（上限150）" || \
        fail "全局 CLAUDE.md ${GC_LINES}行，超過150行上限！"
else
    warn "全局 ~/.claude/CLAUDE.md 唔存在"
fi

STRATEGY_LINES=$(wc -l < "$AXC_HOME/ai/STRATEGY.md" 2>/dev/null || echo 0)
[ "$STRATEGY_LINES" -ge 10 ] && \
    pass "ai/STRATEGY.md ${STRATEGY_LINES}行" || \
    warn "ai/STRATEGY.md 只有 ${STRATEGY_LINES}行（weekly_review 未運行）"

# ── 6. SOUL.md 完整性 ────────────────────────
log ""
log "[ Agent SOUL.md ]"

SOUL_COUNT=$(find "$AXC_HOME/agents" -name "SOUL.md" 2>/dev/null | wc -l | tr -d ' ')
[ "$SOUL_COUNT" -ge 9 ] && pass "$SOUL_COUNT 個 SOUL.md 原位（≥9）" || \
    warn "只有 $SOUL_COUNT 個 SOUL.md（預期 ≥9）"

# 檢查關鍵 agent 有 SOUL.md
for agent in main aster_scanner aster_trader heartbeat; do
    FOUND=$(find "$AXC_HOME/agents/$agent" -name "SOUL.md" 2>/dev/null | head -1)
    [ -n "$FOUND" ] && pass "$agent/SOUL.md 存在" || fail "$agent/SOUL.md 唔存在"
done

# ── 7. 根目錄清潔 ────────────────────────────
log ""
log "[ 根目錄 ]"

BAK_COUNT=$(ls "$AXC_HOME"/*.bak* 2>/dev/null | wc -l | tr -d ' ')
[ "$BAK_COUNT" -eq 0 ] && pass "根目錄無 .bak 垃圾文件" || \
    warn "根目錄有 $BAK_COUNT 個 .bak 文件，建議移至 backups/"

ROOT_MD=$(ls "$AXC_HOME"/*.md 2>/dev/null | wc -l | tr -d ' ')
[ "$ROOT_MD" -le 2 ] && pass "根目錄 $ROOT_MD 個 .md（≤2）" || \
    warn "根目錄 $ROOT_MD 個 .md（預期只有 CLAUDE.md + DEV_LOG.md）"

# ── 總結 ────────────────────────────────────
log ""
log "=========================================="
log "結果：$PASS 通過 | $WARN 警告 | $FAIL 失敗"
log "日誌：$LOG"
log "=========================================="

[ "$FAIL" -eq 0 ] && \
    log "✅ 系統健康" || \
    log "❌ 有 $FAIL 個失敗項，請修復"

exit $FAIL
