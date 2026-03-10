"""
trade_state.py — TRADE_STATE.md 讀寫
TRADE_STATE.md 格式比 SCAN_CONFIG 複雜（有 sections + code blocks）
"""

from __future__ import annotations
import os
import re
import tempfile
from datetime import datetime

from ..config.settings import TRADE_STATE_PATH, HKT


def read_trade_state(path: str | None = None) -> dict:
    """
    Parse TRADE_STATE.md into flat dict.
    Handles sections, code blocks, and key-value pairs.
    """
    p = path or TRADE_STATE_PATH
    if not os.path.exists(p):
        return _default_state()

    state = {}
    in_code_block = False

    with open(p, "r") as f:
        for line in f:
            line = line.strip()

            # Toggle code blocks
            if line.startswith("```"):
                in_code_block = not in_code_block
                continue

            # Skip empty, comments, headers, warnings
            if not line or line.startswith("#") or line.startswith("⚠"):
                continue

            # Parse key: value (both inside and outside code blocks)
            match = re.match(r'^([A-Z_]+):\s*(.+)$', line)
            if match:
                key = match.group(1)
                val = match.group(2).strip()
                # Strip trailing comments like "（上限 1）"
                val = re.sub(r'（.+）$', '', val).strip()
                # Try parse as number
                state[key] = _try_parse_value(val)

    # Ensure all expected keys exist
    defaults = _default_state()
    for k, v in defaults.items():
        if k not in state:
            state[k] = v

    return state


def write_trade_state(updates: dict, path: str | None = None) -> bool:
    """
    Update specific fields in TRADE_STATE.md.
    Preserves structure (headers, code blocks, comments).
    """
    p = path or TRADE_STATE_PATH
    if not os.path.exists(p):
        return False

    with open(p, "r") as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        updated = False
        stripped = line.strip()
        for key, val in updates.items():
            pattern = f'^{key}:'
            if re.match(pattern, stripped):
                # Preserve any trailing comment
                old_match = re.match(r'^([A-Z_]+):\s*\S+(\s*（.+）)?', stripped)
                suffix = old_match.group(2) if old_match and old_match.group(2) else ""
                new_lines.append(f"{key}: {val}{suffix}\n")
                updated = True
                break
        if not updated:
            new_lines.append(line)

    # Atomic write: tempfile in same dir → os.replace
    dir_name = os.path.dirname(p)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, p)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return True


def _try_parse_value(val: str):
    """Try to parse as float/int, else return string."""
    if val in ("—", "UNKNOWN", "NO", "YES"):
        return val
    try:
        if "." in val:
            return float(val)
        return int(val)
    except (ValueError, TypeError):
        return val


def _default_state() -> dict:
    """Default values for all expected TRADE_STATE fields."""
    return {
        "SYSTEM_STATUS": "ACTIVE",
        "LAST_UPDATED": "—",
        "DAILY_LOSS": 0.0,
        "DAILY_LOSS_LIMIT": 0.15,
        "CONSECUTIVE_LOSSES": 0,
        "COOLDOWN_ACTIVE": "NO",
        "COOLDOWN_ENDS": "—",
        "MARKET_MODE": "UNKNOWN",
        "MODE_CONFIRMED_CYCLES": 0,
        "POSITION_OPEN": "UNKNOWN",
        "PAIR": "—",
        "DIRECTION": "—",
        "ENTRY_PRICE": 0.0,
        "MARK_PRICE": 0.0,
        "SIZE": 0.0,
        "SL_PRICE": 0.0,
        "TP_PRICE": 0.0,
        "BALANCE_USDT": 0.0,
        "AVAILABLE_MARGIN": 0.0,
        "TRADES_TODAY": 0,
        "WINS_TODAY": 0,
        "LOSSES_TODAY": 0,
        # Trailing SL/TP + Re-entry
        "TRAILING_SL_ACTIVE": "NO",
        "TRAILING_SL_LAST_MOVE": "—",
        "TP_EXTENDED": "NO",
        "TP_EXTEND_COUNT": 0,
        "REENTRY_ELIGIBLE": "NO",
        "REENTRY_PAIR": "—",
        "REENTRY_DIRECTION": "—",
        "REENTRY_ORIGINAL_ENTRY": 0.0,
        "REENTRY_EXIT_TIME": "—",
        "REENTRY_CYCLES_REMAINING": 0,
    }
