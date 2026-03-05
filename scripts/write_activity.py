#!/usr/bin/env python3
"""
scripts/write_activity.py
活動日誌寫入器 — append 模式（唔讀舊記錄，唔阻塞）
格式：shared/activity_log.jsonl（每行一條 JSON）
"""
import json
import os
import time
from datetime import datetime

HOME = os.path.expanduser("~")
ACTIVITY_LOG = os.path.join(HOME, ".openclaw/shared/activity_log.jsonl")
MAX_ENTRIES = 500


def write_activity(event_type: str, message: str, data: dict = None) -> None:
    """
    Append 寫入一條活動記錄。O(1) 寫入，只有 trim 時才讀。

    event_type:
        "mode_change"  — 切換交易 profile
        "heartbeat"    — 系統心跳
        "trade_entry"  — 入場
        "trade_exit"   — 出場
        "system"       — 啟動/停止
        "signal"       — scanner 觸發信號
        "error"        — 錯誤事件
    """
    entry = {
        "ts": int(time.time()),
        "time": datetime.now().strftime("%m-%d %H:%M"),
        "type": event_type,
        "msg": message,
    }
    if data:
        entry["data"] = data

    line = json.dumps(entry, ensure_ascii=False) + "\n"

    try:
        with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
            f.write(line)

        # trim：只有 >1MB 先讀+寫（約5000條）
        if os.path.getsize(ACTIVITY_LOG) > 1_000_000:
            _trim_log()
    except Exception as e:
        print(f"[write_activity] error: {e}")


def _trim_log() -> None:
    """保留最新 MAX_ENTRIES 條，原子寫入。"""
    try:
        with open(ACTIVITY_LOG, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        lines = lines[-MAX_ENTRIES:]
        tmp = ACTIVITY_LOG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, ACTIVITY_LOG)
    except Exception as e:
        print(f"[write_activity] trim error: {e}")
