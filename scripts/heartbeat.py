#!/usr/bin/env python3
"""
heartbeat.py — Python-based Heartbeat Monitor (No LLM)
每 15 分鐘執行一次，純 file I/O + 比較，唔使 LLM

功能：
1. 讀 TRADE_STATE.md → 檢查倉位 + SL/TP 確認狀態
2. 讀 SCAN_CONFIG.md → 檢查 TRIGGER_PENDING 超時
3. 讀 COST_TRACKER.md → 檢查日成本異常
4. SCAN_LOG.md → 超過 180 行就 trim
5. 有異常 → 發 Telegram 警報（繁中）
6. 靜音模式：23:00-08:00 UTC+8 只發 URGENT

Exit codes:
0 = HEARTBEAT_OK（無警報）
1 = ALERT sent（有警報已發送）
2 = ERROR

Output: JSON summary for cron/log consumption
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────
# SETUP IMPORTS — reuse existing code
# ─────────────────────────────────────────
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_SHARED = os.path.join(AXC_HOME, "shared")
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Import from light_scan.py
from light_scan import parse_scan_config, send_telegram, now_hkt, now_str

# Import from trader_cycle package
from trader_cycle.state.trade_state import read_trade_state
from trader_cycle.state.file_lock import FileLock

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
TRADE_STATE_PATH = os.path.join(_SHARED, "TRADE_STATE.md")
SCAN_CONFIG_PATH = os.path.join(_SHARED, "SCAN_CONFIG.md")
COST_TRACKER_PATH = os.path.join(os.path.expanduser("~/.openclaw/workspace"), "routing/COST_TRACKER.md")
SCAN_LOG_PATH = os.path.join(_SHARED, "SCAN_LOG.md")

# Thresholds
COST_SOFT_LIMIT = 0.50
TRIGGER_STALE_MINUTES = 25
SCAN_LOG_TRIM_THRESHOLD = 180
SCAN_LOG_TRIM_TARGET = 100

# Silent hours (UTC+8): 23:00 - 08:00
SILENT_HOUR_START = 23
SILENT_HOUR_END = 8

# Timezone
HKT = timezone(timedelta(hours=8))


# ─────────────────────────────────────────
# ALERT CHECKS
# ─────────────────────────────────────────
def check_position_alerts(state: dict) -> list:
    """Check position-related anomalies.

    Uses SL_PRICE / TP_PRICE to determine if orders are set.
    SL_CONFIRMED / TP_CONFIRMED are NOT written by any component,
    so we rely on price values instead (0 or "—" = not set).
    """
    alerts = []

    if state.get("POSITION_OPEN") != "YES":
        return alerts

    pair = state.get("PAIR", "UNKNOWN")

    # SL check: price must be a positive number
    sl_price = state.get("SL_PRICE", 0)
    sl_set = isinstance(sl_price, (int, float)) and sl_price > 0
    if not sl_set:
        alerts.append({
            "type": "URGENT",
            "reason": f"{pair} 倉位開啟但無止損設定",
            "action": "請到 Aster DEX 設定止損訂單"
        })

    # TP check: price must be a positive number (warning, not urgent)
    tp_price = state.get("TP_PRICE", 0)
    tp_set = isinstance(tp_price, (int, float)) and tp_price > 0
    if not tp_set:
        alerts.append({
            "type": "WARNING",
            "reason": f"{pair} 止盈未設定",
            "action": "建議設定止盈訂單"
        })

    return alerts


def check_trigger_alerts(config: dict) -> list:
    """Check TRIGGER_PENDING stale."""
    alerts = []

    trigger = str(config.get("TRIGGER_PENDING", "OFF"))
    if trigger != "ON":
        return alerts

    # Check age of last_updated
    last_updated = str(config.get("last_updated", "INIT"))
    if last_updated == "INIT":
        return alerts

    try:
        lu = datetime.strptime(last_updated, "%Y-%m-%d %H:%M")
        lu = lu.replace(tzinfo=HKT)
        age_min = (now_hkt() - lu).total_seconds() / 60
        if age_min > TRIGGER_STALE_MINUTES:
            alerts.append({
                "type": "WARNING",
                "reason": f"TRIGGER_PENDING 已超過 {age_min:.0f} 分鐘未處理",
                "action": "Trader-cycle 可能未運行，請檢查 launchd"
            })
    except (ValueError, TypeError):
        pass

    return alerts


def check_cost_alerts(cost_path: str) -> list:
    """Check daily cost anomaly."""
    alerts = []

    config = parse_scan_config(cost_path)
    daily_total_str = str(config.get("DAILY_TOTAL", "$0.00"))

    # Parse "$0.02" or "~$0.02" format
    cleaned = re.sub(r'[~$]', '', daily_total_str).strip()
    try:
        daily_total = float(cleaned)
    except (ValueError, TypeError):
        daily_total = 0.0

    if daily_total > COST_SOFT_LIMIT:
        alerts.append({
            "type": "WARNING",
            "reason": f"今日 API 成本 {daily_total_str} 超過軟限 ${COST_SOFT_LIMIT}",
            "action": "檢查是否有異常 LLM 調用"
        })

    return alerts


# ─────────────────────────────────────────
# SCAN_LOG TRIMMING
# ─────────────────────────────────────────
def trim_scan_log_if_needed(path: str) -> bool:
    """Trim SCAN_LOG if > threshold lines. Uses FileLock for safety."""
    if not os.path.exists(path):
        return False

    with FileLock(path):
        with open(path, "r") as f:
            lines = f.readlines()

        data_lines = [l for l in lines if not l.strip().startswith("#") and l.strip()]
        if len(data_lines) <= SCAN_LOG_TRIM_THRESHOLD:
            return False

        comment_lines = [l for l in lines if l.strip().startswith("#")]
        trimmed = data_lines[-SCAN_LOG_TRIM_TARGET:]

        ts = now_str()
        trimmed.append(
            f"[{ts} UTC+8] HEARTBEAT 自動清理 SCAN_LOG: "
            f"{len(data_lines)} → {SCAN_LOG_TRIM_TARGET} 行\n"
        )

        with open(path, "w") as f:
            f.writelines(comment_lines)
            f.writelines(trimmed)

    return True


# ─────────────────────────────────────────
# SILENT MODE
# ─────────────────────────────────────────
def is_silent_hour() -> bool:
    """23:00-08:00 UTC+8 = silent (only URGENT)."""
    hour = now_hkt().hour
    if SILENT_HOUR_START <= hour or hour < SILENT_HOUR_END:
        return True
    return False


# ─────────────────────────────────────────
# TELEGRAM FORMATTING
# ─────────────────────────────────────────
def format_alert_telegram(ts: str, alerts: list) -> str:
    """Format alerts as Traditional Chinese HTML for Telegram."""
    urgent = [a for a in alerts if a["type"] == "URGENT"]
    warnings = [a for a in alerts if a["type"] == "WARNING"]

    emoji = "🚨" if urgent else "⚠️"
    lines = [f"<b>{emoji} [{ts} UTC+8] 心跳警報</b>"]
    lines.append("━━━━━━━━━━━━━━")

    for a in urgent:
        lines.append(f"🚨 {a['reason']}")
        lines.append(f"   → {a['action']}")

    for a in warnings:
        lines.append(f"⚠️ {a['reason']}")
        lines.append(f"   → {a['action']}")

    return "\n".join(lines)


# ─────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────
def run_heartbeat() -> int:
    """
    Main heartbeat logic.
    Returns: 0 = OK, 1 = ALERT sent, 2 = ERROR
    """
    ts = now_str()
    result = {
        "timestamp": ts,
        "status": "ok",
        "alerts": [],
        "telegram": "none"
    }

    # ─── Step 1: Read TRADE_STATE.md ───
    try:
        trade_state = read_trade_state(TRADE_STATE_PATH)
    except Exception as e:
        trade_state = {"POSITION_OPEN": "UNKNOWN", "_error": str(e)}
        result.setdefault("errors", []).append(f"trade_state: {e}")

    # ─── Step 2: Read SCAN_CONFIG.md ───
    try:
        scan_config = parse_scan_config(SCAN_CONFIG_PATH)
    except Exception as e:
        scan_config = {}
        result.setdefault("errors", []).append(f"scan_config: {e}")

    # ─── Step 3: Read COST_TRACKER.md (via check) ───
    # (parsed inside check_cost_alerts)

    # ─── Step 4: Collect all alerts ───
    all_alerts = []
    all_alerts.extend(check_position_alerts(trade_state))
    all_alerts.extend(check_trigger_alerts(scan_config))

    try:
        all_alerts.extend(check_cost_alerts(COST_TRACKER_PATH))
    except Exception as e:
        result.setdefault("errors", []).append(f"cost_tracker: {e}")

    # ─── Step 5: SCAN_LOG trim ───
    try:
        trimmed = trim_scan_log_if_needed(SCAN_LOG_PATH)
        result["scan_log_trimmed"] = trimmed
    except Exception as e:
        result.setdefault("errors", []).append(f"scan_log_trim: {e}")

    # ─── Step 6: Silent mode + Telegram ───
    silent = is_silent_hour()
    has_urgent = any(a["type"] == "URGENT" for a in all_alerts)

    if all_alerts:
        if not silent or has_urgent:
            # Filter: in silent mode only send URGENT
            send_list = all_alerts if not silent else [a for a in all_alerts if a["type"] == "URGENT"]
            msg = format_alert_telegram(ts, send_list)
            tg_result = send_telegram(msg)
            result["telegram"] = "sent" if "error" not in tg_result else f"error: {tg_result.get('error')}"
        else:
            result["telegram"] = "silent_suppressed"
    else:
        result["telegram"] = "no_alert"

    # ─── Step 7: Build summary ───
    pos = "有" if trade_state.get("POSITION_OPEN") == "YES" else "無"
    pair = trade_state.get("PAIR", "—") if trade_state.get("POSITION_OPEN") == "YES" else "—"
    trigger = str(scan_config.get("TRIGGER_PENDING", "OFF"))

    cost_config = parse_scan_config(COST_TRACKER_PATH)
    cost = str(cost_config.get("DAILY_TOTAL", "$0.00"))

    summary = (
        f"HEARTBEAT_OK | {ts} UTC+8 | "
        f"倉位:{pos}({pair}) | 成本:{cost} | Trigger:{trigger}"
    )
    result["summary"] = summary
    result["alerts"] = [
        {"type": a["type"], "reason": a["reason"]} for a in all_alerts
    ]
    result["position_open"] = trade_state.get("POSITION_OPEN", "UNKNOWN")
    result["silent_mode"] = silent

    # ─── Output ───
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if all_alerts:
        return 1
    return 0


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────
if __name__ == "__main__":
    try:
        exit_code = run_heartbeat()
        sys.exit(exit_code)
    except Exception as e:
        error_result = {
            "timestamp": now_str(),
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        print(json.dumps(error_result, indent=2, ensure_ascii=False))
        # Try to send urgent Telegram
        try:
            send_telegram(
                f"🚨 <b>HEARTBEAT ERROR</b>\n"
                f"時間: {error_result['timestamp']} UTC+8\n"
                f"錯誤: {str(e)[:200]}"
            )
        except Exception:
            pass
        sys.exit(2)
