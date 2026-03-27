#!/bin/bash
# install_hooks.sh — Install git hooks for AXC
# Usage: bash scripts/install_hooks.sh

set -e
AXC_HOME="${AXC_HOME:-$HOME/projects/axc-trading}"
HOOKS_DIR="$AXC_HOME/.git/hooks"

# ── Pre-push hook: run pytest before push ──
cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/bin/bash
# pre-push hook — run pytest before allowing push
# Skip with: git push --no-verify (only when you know what you're doing)

AXC_HOME="${AXC_HOME:-$HOME/projects/axc-trading}"
cd "$AXC_HOME"

echo "🧪 Running tests before push..."
if python3 -m pytest -q --tb=line 2>&1; then
    echo "✅ Tests passed"
    exit 0
else
    echo "❌ Tests failed — push blocked"
    echo "   Fix the failing tests, then push again."
    echo "   (Emergency: git push --no-verify)"
    exit 1
fi
HOOK

chmod +x "$HOOKS_DIR/pre-push"
echo "✅ Installed pre-push hook → $HOOKS_DIR/pre-push"
