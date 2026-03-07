#!/bin/bash
# OpenClaw — macOS Launcher
# Double-click this file to start the Dashboard

set -e

# Resolve script directory (handles symlinks and spaces)
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "============================================"
echo "  OpenClaw — Starting..."
echo "============================================"
echo ""

# ─── Check Python ───
if command -v python3 &>/dev/null; then
    PY=python3
else
    echo "[ERROR] Python 3 not found."
    echo "Install: brew install python3"
    echo ""
    read -n 1 -s -r -p "Press any key to exit..."
    exit 1
fi

PY_VER=$($PY --version 2>&1)
echo "[OK] $PY_VER"

# ─── Install dependencies if needed ───
if ! $PY -c "import requests, pandas, dotenv" 2>/dev/null; then
    echo ""
    echo "[SETUP] Installing dependencies..."
    $PY -m pip install --user -r "$DIR/requirements.txt" --break-system-packages -q
    echo "[OK] Dependencies installed"
fi

# ─── Setup secrets/.env on first run ───
ENV_FILE="$DIR/secrets/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "============================================"
    echo "  First Run Setup"
    echo "============================================"
    echo ""
    mkdir -p "$DIR/secrets"
    cp "$DIR/docs/friends/.env.example" "$ENV_FILE"
    echo "Created secrets/.env from template."
    echo ""
    read -r -p "Paste your PROXY_API_KEY (or press Enter to skip): " KEY
    if [ -n "$KEY" ]; then
        sed -i '' "s|^PROXY_API_KEY=.*|PROXY_API_KEY=$KEY|" "$ENV_FILE"
        echo "[OK] API key saved"
    else
        echo "[SKIP] Edit secrets/.env later with your API key"
    fi
    echo ""
fi

# ─── Check if dashboard already running ───
if lsof -i :5555 &>/dev/null; then
    echo ""
    echo "[INFO] Dashboard already running on port 5555"
    open "http://localhost:5555"
    echo ""
    read -n 1 -s -r -p "Press any key to exit..."
    exit 0
fi

# ─── Launch Dashboard ───
echo ""
echo "[LAUNCH] Starting Dashboard on http://localhost:5555"
echo "[INFO] Press Ctrl+C to stop"
echo ""

# Open browser after short delay
(sleep 2 && open "http://localhost:5555") &

$PY scripts/dashboard.py
