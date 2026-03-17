"""
telegram.py — Standalone Telegram delivery (零依賴)

只負責 send + format_urgent_alert。
各 cycle 專屬 formatting 函數留喺各自嘅 notify/telegram.py。
"""

import json
import logging
import os
from urllib.request import Request, urlopen

__all__ = ["send_telegram", "format_urgent_alert"]

logger = logging.getLogger(__name__)


def send_telegram(text: str, token: str = "", chat_id: str = "") -> dict:
    """Send Telegram message via Bot API. Returns API response dict."""
    token = token or os.environ.get("TG_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TG_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TG_BOT_TOKEN or TG_CHAT_ID not set, skipping Telegram")
        return {"error": "missing credentials"}
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps(
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        ).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return {"error": str(e)}


def format_urgent_alert(title: str, body: str) -> str:
    """Format an urgent alert message (HTML)."""
    return f"<b>🚨 {title}</b>\n{body}"
