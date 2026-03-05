#!/bin/bash
# ~/.openclaw/scripts/load_env.sh
# LaunchAgent wrapper：載入 .env 後執行目標腳本
#
# 用法（plist）：
#   /bin/bash /path/to/load_env.sh /path/to/python3 /path/to/script.py

ENV_FILE="$HOME/.openclaw/secrets/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
    echo "[$(date '+%H:%M:%S')] [load_env] .env 載入完成"
else
    echo "[$(date '+%H:%M:%S')] [load_env] ⚠️ .env 不存在：$ENV_FILE"
fi

exec "$@"
