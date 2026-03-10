#!/usr/bin/env python3
"""
macro_monitor.py — 宏觀市場流動性監察

監察 DXY（美元指數）趨勢變化，寫入 shared/news_manual.json。
DXY 走弱趨勢 → crypto 利好信號。DXY 走強 → crypto 利空。

排程：每 4 小時（配合 4H 交易框架）
手動：python3 ~/projects/axc-trading/scripts/macro_monitor.py
"""

import hashlib
import json
import logging
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
NEWS_MANUAL_FILE = SHARED_DIR / "news_manual.json"
MACRO_STATE_FILE = SHARED_DIR / "macro_state.json"

# DXY significant move threshold (%)
DXY_ALERT_PCT = 0.5       # 單日變動 ≥0.5% 觸發警報
DXY_TREND_DAYS = 3         # 連續 N 日同方向 = 趨勢

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = "macro_monitor/1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MACRO] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("macro_monitor")


def fetch_dxy() -> dict | None:
    """Fetch DXY daily data from Yahoo Finance. Returns dict with price info."""
    url = f"{YAHOO_BASE}/DX-Y.NYB?interval=1d&range=10d"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.error(f"Yahoo Finance fetch failed: {e}")
        return None

    result = data.get("chart", {}).get("result", [{}])[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp", [])
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    closes = quotes.get("close", [])

    # Filter out None values
    valid = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    if len(valid) < 2:
        log.warning("Not enough DXY data points")
        return None

    current_price = meta.get("regularMarketPrice", valid[-1][1])
    daily_changes = []
    for i in range(1, len(valid)):
        prev_close = valid[i - 1][1]
        curr_close = valid[i][1]
        pct = (curr_close - prev_close) / prev_close * 100
        daily_changes.append(pct)

    return {
        "price": round(current_price, 3),
        "prev_close": round(valid[-2][1], 3),
        "daily_change_pct": round(daily_changes[-1], 3) if daily_changes else 0,
        "daily_changes": [round(c, 3) for c in daily_changes[-5:]],
        "data_points": len(valid),
    }


def detect_trend(daily_changes: list[float]) -> str | None:
    """Detect DXY trend from recent daily changes.

    Returns 'weakening', 'strengthening', or None.
    """
    if len(daily_changes) < DXY_TREND_DAYS:
        return None

    recent = daily_changes[-DXY_TREND_DAYS:]

    if all(c < 0 for c in recent):
        return "weakening"
    if all(c > 0 for c in recent):
        return "strengthening"
    return None


def load_macro_state() -> dict:
    """Load previous macro state for change detection."""
    if not MACRO_STATE_FILE.exists():
        return {}
    try:
        return json.loads(MACRO_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def atomic_write_json(path: Path, data):
    """Atomic JSON write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_to_news_manual(text: str):
    """Append macro alert to news_manual.json with dedup."""
    existing = {"entries": [], "processed_before": ""}
    if NEWS_MANUAL_FILE.exists():
        try:
            existing = json.loads(NEWS_MANUAL_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Dedup by text hash
    seen = {hashlib.md5(e.get("text", "").encode()).hexdigest() for e in existing.get("entries", [])}
    text_hash = hashlib.md5(text.encode()).hexdigest()
    if text_hash in seen:
        return False

    existing["entries"].append({
        "text": text,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "source": "macro-monitor",
    })
    existing["entries"] = existing["entries"][-100:]
    atomic_write_json(NEWS_MANUAL_FILE, existing)
    return True


def main():
    log.info("Macro monitor starting...")

    # Fetch DXY
    dxy = fetch_dxy()
    if not dxy:
        return

    log.info(f"DXY: {dxy['price']} ({dxy['daily_change_pct']:+.2f}%)")

    prev_state = load_macro_state()
    alerts = []

    # Check 1: Significant daily move
    if abs(dxy["daily_change_pct"]) >= DXY_ALERT_PCT:
        direction = "走弱" if dxy["daily_change_pct"] < 0 else "走強"
        crypto_impact = "利好crypto" if dxy["daily_change_pct"] < 0 else "利空crypto"
        alerts.append(
            f"[DXY] 美元指數顯著{direction} {dxy['daily_change_pct']:+.2f}% "
            f"(現價 {dxy['price']}) — {crypto_impact}"
        )

    # Check 2: Trend detection (N consecutive days same direction)
    trend = detect_trend(dxy["daily_changes"])
    prev_trend = prev_state.get("dxy_trend")

    if trend and trend != prev_trend:
        if trend == "weakening":
            alerts.append(
                f"[DXY] 美元連續{DXY_TREND_DAYS}日走弱趨勢確認 "
                f"(現價 {dxy['price']}) — crypto 利好信號"
            )
        else:
            alerts.append(
                f"[DXY] 美元連續{DXY_TREND_DAYS}日走強趨勢確認 "
                f"(現價 {dxy['price']}) — crypto 利空警告"
            )

    # Save state
    new_state = {
        "dxy_price": dxy["price"],
        "dxy_daily_change_pct": dxy["daily_change_pct"],
        "dxy_trend": trend,
        "dxy_daily_changes": dxy["daily_changes"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(MACRO_STATE_FILE, new_state)

    # Write alerts
    if not alerts:
        log.info("No significant macro events")
        return

    for alert in alerts:
        added = append_to_news_manual(alert)
        if added:
            log.info(f"Alert: {alert}")
        else:
            log.info(f"Dedup skipped: {alert}")


if __name__ == "__main__":
    main()
