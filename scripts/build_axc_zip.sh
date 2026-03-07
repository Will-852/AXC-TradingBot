#!/usr/bin/env bash
# build_axc_zip.sh — Build standalone AXC deployment ZIP
# Usage: bash scripts/build_axc_zip.sh
set -euo pipefail

OPENCLAW_DIR="${HOME}/.openclaw"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ZIP_NAME="axc-${TIMESTAMP}.zip"
STAGE_DIR=$(mktemp -d)

echo "=== Building AXC ZIP ==="
echo "Staging dir: ${STAGE_DIR}"

# ── Copy files (preserve directory structure) ──
copy_file() {
    local src="${OPENCLAW_DIR}/$1"
    local dest="${STAGE_DIR}/$1"
    if [ ! -f "$src" ]; then
        echo "WARNING: missing $src"
        return 1
    fi
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
}

# Scripts
copy_file scripts/tg_bot.py
copy_file scripts/axc_client.py
copy_file scripts/slash_cmd.py
copy_file scripts/write_activity.py
copy_file scripts/trader_cycle/__init__.py
copy_file scripts/trader_cycle/exchange/__init__.py
copy_file scripts/trader_cycle/exchange/aster_client.py
copy_file scripts/trader_cycle/exchange/exceptions.py

# Memory
copy_file memory/__init__.py
copy_file memory/writer.py
copy_file memory/retriever.py
copy_file memory/embedder.py

# ── Copy with rename ──
cp "${OPENCLAW_DIR}/axc_requirements.txt" "${STAGE_DIR}/requirements.txt"
mkdir -p "${STAGE_DIR}/secrets"
cp "${OPENCLAW_DIR}/.env.example" "${STAGE_DIR}/secrets/.env.example"

if [ -f "${OPENCLAW_DIR}/docs/architecture/AXC.md" ]; then
    cp "${OPENCLAW_DIR}/docs/architecture/AXC.md" "${STAGE_DIR}/AXC.md"
fi

# ── Create empty directories ──
mkdir -p "${STAGE_DIR}/shared"
mkdir -p "${STAGE_DIR}/memory/store"
mkdir -p "${STAGE_DIR}/memory/index"
mkdir -p "${STAGE_DIR}/logs"

# ── Build ZIP ──
cd "${STAGE_DIR}"
zip -r "${OPENCLAW_DIR}/${ZIP_NAME}" . -x '*.DS_Store'
cd /

# ── Cleanup ──
rm -rf "${STAGE_DIR}"

echo "=== Done ==="
echo "Output: ${OPENCLAW_DIR}/${ZIP_NAME}"
echo "Size: $(du -h "${OPENCLAW_DIR}/${ZIP_NAME}" | cut -f1)"
