#!/usr/bin/env python3
"""
telegram_sender.py — OpenClaw Telegram 發送工具
用途: 由 agent 調用，發送繁體中文匯報到指定 chat
"""

import os
import sys
import json
import requests
from datetime import datetime


CHAT_ID = "2060972655"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def get_bot_token():
    """從環境變數讀取 bot token"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        # 嘗試從 .env 讀取
        env_path = os.path.join(os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading")), "secrets", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    return token


def send_message(text: str, chat_id: str = CHAT_ID, parse_mode: str = "HTML") -> dict:
    """
    發送 Telegram 訊息

    Args:
        text: 訊息內容（支援 HTML 格式）
        chat_id: Telegram chat ID（預設用戶 chat）
        parse_mode: HTML 或 Markdown

    Returns:
        API 回應 dict
    """
    token = get_bot_token()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN 未設定", file=sys.stderr)
        return {"ok": False, "error": "No bot token"}

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        if not result.get("ok"):
            print(f"ERROR: Telegram API 錯誤: {result}", file=sys.stderr)
        return result
    except requests.exceptions.RequestException as e:
        print(f"ERROR: 網絡錯誤: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


def format_timestamp() -> str:
    """返回 UTC+8 時間戳"""
    from datetime import timezone, timedelta
    utc8 = timezone(timedelta(hours=8))
    now = datetime.now(utc8)
    return now.strftime("%Y-%m-%d %H:%M UTC+8")


def send_heartbeat_ok(extra: str = "") -> dict:
    """發送心跳正常通知（靜默模式下不發）"""
    ts = format_timestamp()
    msg = f"✅ {ts} HEARTBEAT_OK"
    if extra:
        msg += f" | {extra}"
    return send_message(msg)


def send_alert(title: str, body: str, urgent: bool = False) -> dict:
    """發送警報"""
    ts = format_timestamp()
    prefix = "🚨 URGENT" if urgent else "⚠️"
    msg = f"{prefix} [{ts}] {title}\n{'━' * 16}\n{body}"
    return send_message(msg)


def send_trade_report(report: str) -> dict:
    """發送交易報告"""
    return send_message(report)


if __name__ == "__main__":
    # 命令行使用: python telegram_sender.py "訊息內容"
    if len(sys.argv) < 2:
        print("用法: python telegram_sender.py <訊息>")
        sys.exit(1)

    message = sys.argv[1]
    result = send_message(message)

    if result.get("ok"):
        print("✅ 發送成功")
    else:
        print(f"❌ 發送失敗: {result}")
        sys.exit(1)
