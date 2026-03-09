#!/bin/bash
# integration_test.sh — Fix R5: 5 場景整合測試
# 用法：bash scripts/integration_test.sh

set -euo pipefail

AXC_HOME="${AXC_HOME:-$HOME/projects/axc-trading}"
PASS=0
FAIL=0
TOTAL=5

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { PASS=$((PASS+1)); echo -e "${GREEN}✅ PASS${NC} — $1"; }
fail() { FAIL=$((FAIL+1)); echo -e "${RED}❌ FAIL${NC} — $1: $2"; }

echo "══════════════════════════════════════"
echo " AXC Integration Test (5 scenarios)"
echo "══════════════════════════════════════"
echo ""

# ── Test 1: python3 路徑 + 版本 ──────────────
PYTHON="/opt/homebrew/bin/python3"
if [ -x "$PYTHON" ]; then
    VER=$("$PYTHON" --version 2>&1)
    ok "python3 存在且可執行 ($VER)"
else
    fail "python3" "路徑 $PYTHON 不存在或不可執行"
fi

# ── Test 2: load_env.sh 載入 .env ─────────────
LOAD_ENV="$AXC_HOME/scripts/load_env.sh"
if [ -f "$LOAD_ENV" ]; then
    OUTPUT=$(bash "$LOAD_ENV" echo "ENV_LOADED" 2>&1)
    if echo "$OUTPUT" | grep -q "ENV_LOADED"; then
        ok "load_env.sh 載入 .env 並正確 exec 目標"
    else
        fail "load_env.sh" "exec 未正確傳遞 — output: $OUTPUT"
    fi
else
    fail "load_env.sh" "不存在: $LOAD_ENV"
fi

# ── Test 3: Python 依賴全部可 import ──────────
DEPS_CHECK=$("$PYTHON" -c "
missing = []
for mod in ['requests', 'pandas', 'dotenv', 'telegram']:
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print('MISSING:' + ','.join(missing))
else:
    print('ALL_OK')
" 2>&1)
if echo "$DEPS_CHECK" | grep -q "ALL_OK"; then
    ok "Python 依賴全部已安裝"
else
    fail "Python deps" "$DEPS_CHECK"
fi

# ── Test 4: async_scanner.py 可 import（語法+依賴）──
SCANNER="$AXC_HOME/scripts/async_scanner.py"
IMPORT_CHECK=$("$PYTHON" -c "
import sys, importlib.util
spec = importlib.util.spec_from_file_location('scanner', '$SCANNER')
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
    print('IMPORT_OK')
except Exception as e:
    print(f'IMPORT_FAIL:{e}')
" 2>&1)
if echo "$IMPORT_CHECK" | grep -q "IMPORT_OK"; then
    ok "async_scanner.py import 成功"
else
    fail "async_scanner.py import" "$IMPORT_CHECK"
fi

# ── Test 5: plist 格式驗證 ────────────────────
PLIST_OK=true
PLIST_ERR=""
for plist in "$HOME/Library/LaunchAgents/ai.openclaw.scanner.plist" \
             "$HOME/Library/LaunchAgents/ai.openclaw.telegram.plist"; do
    if [ -f "$plist" ]; then
        if ! plutil -lint "$plist" > /dev/null 2>&1; then
            PLIST_OK=false
            PLIST_ERR="$PLIST_ERR $(basename "$plist"): plutil lint failed;"
        fi
    else
        PLIST_OK=false
        PLIST_ERR="$PLIST_ERR $(basename "$plist"): not found;"
    fi
done
if $PLIST_OK; then
    ok "所有 plist 格式正確 (plutil lint)"
else
    fail "plist lint" "$PLIST_ERR"
fi

# ── 結果 ─────────────────────────────────────
echo ""
echo "══════════════════════════════════════"
echo " Results: $PASS/$TOTAL passed, $FAIL failed"
echo "══════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
