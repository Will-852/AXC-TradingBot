"""
trade_state.py — Trade state 讀寫（Sprint 2A: JSON + MD dual-read）

Storage evolution:
  Phase 1-2: TRADE_STATE.md (regex-parsed flat key-value)
  Phase 3+:  TRADE_STATE.json (structured, supports positions[])

Dual-read priority:
  1. JSON path → parse → return flat dict
  2. MD path   → regex → return flat dict (fallback / rollback)
  3. Backup    → latest snapshot → return flat dict
  4. Defaults  → empty state

Public interface returns flat dict for backward compat.
All 8+ consumer files use ctx.trade_state["KEY"] — zero changes needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Any

from ..config.settings import (
    TRADE_STATE_PATH, TRADE_STATE_JSON_PATH,
    TRADE_STATE_BACKUP_DIR, STATE_FORMAT, HKT,
)

logger = logging.getLogger(__name__)

# Current JSON schema version — bump when schema changes
_SCHEMA_VERSION = 1


# ─── Public Interface (backward-compatible flat dict) ───

def read_trade_state(path: str | None = None) -> dict:
    """Read trade state → flat dict (backward-compatible).

    Dual-read: JSON first, then MD fallback, then backup, then defaults.
    Override format with STATE_FORMAT env var ("json" or "md").
    """
    fmt = os.environ.get("STATE_FORMAT", STATE_FORMAT)

    if fmt == "json":
        # Try JSON
        json_path = path if (path and path.endswith(".json")) else TRADE_STATE_JSON_PATH
        state = _read_json(json_path)
        if state is not None:
            return state

        # Fallback: try MD
        md_path = path if (path and path.endswith(".md")) else TRADE_STATE_PATH
        state = _read_md(md_path)
        if state is not None:
            return state

        # Fallback: try latest backup
        state = _read_latest_backup()
        if state is not None:
            logger.warning("Using backup state — primary files unreadable")
            return state

    elif fmt == "md":
        # Legacy / rollback mode
        md_path = path or TRADE_STATE_PATH
        state = _read_md(md_path)
        if state is not None:
            return state

    return _default_state()


def write_trade_state(updates: dict, path: str | None = None) -> bool:
    """Write trade state updates.

    JSON mode: merge updates into existing JSON, atomic write + backup.
    MD mode:   regex-replace values in TRADE_STATE.md (legacy).
    """
    fmt = os.environ.get("STATE_FORMAT", STATE_FORMAT)

    if fmt == "json":
        return _write_json(updates, path)
    else:
        return _write_md(updates, path)


def migrate_md_to_json() -> bool:
    """One-time migration: read MD → write JSON.
    Safe to run multiple times (idempotent — skips if JSON already exists).
    Returns True if migration performed.
    """
    if os.path.exists(TRADE_STATE_JSON_PATH):
        logger.info("JSON state already exists — skipping migration")
        return False

    state = _read_md(TRADE_STATE_PATH)
    if state is None:
        logger.warning("No MD state to migrate")
        return False

    json_data = _flat_to_json(state)
    _atomic_write_json(TRADE_STATE_JSON_PATH, json_data)
    logger.info(f"Migrated MD → JSON: {TRADE_STATE_JSON_PATH}")
    return True


# ─── JSON Read/Write ───

def _read_json(path: str) -> dict | None:
    """Read JSON state → flat dict. Returns None on failure."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Version check + migration
        version = data.get("_version", 0)
        if version < _SCHEMA_VERSION:
            from .migrations import run_migrations
            data = run_migrations(data, version, _SCHEMA_VERSION)
            # Write back migrated data
            _atomic_write_json(path, data)

        return _json_to_flat(data)
    except (json.JSONDecodeError, IOError, KeyError) as e:
        logger.warning(f"JSON state read failed ({path}): {e}")
        return None


def _write_json(updates: dict, path: str | None = None) -> bool:
    """Merge flat dict updates into JSON state, with backup."""
    json_path = path or TRADE_STATE_JSON_PATH

    # Read existing JSON (or build from defaults)
    existing_flat = _default_state()
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_json = json.load(f)
            existing_flat = _json_to_flat(existing_json)
        except (json.JSONDecodeError, IOError):
            pass

    # Merge updates
    existing_flat.update(updates)

    # Convert back to structured JSON
    json_data = _flat_to_json(existing_flat)

    # Backup before write
    _backup_state(json_path)

    # Atomic write
    _atomic_write_json(json_path, json_data)
    return True


def _atomic_write_json(path: str, data: dict) -> None:
    """Atomic JSON write: tempfile → os.replace."""
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── JSON ↔ Flat Dict Conversion ───

def _flat_to_json(flat: dict) -> dict:
    """Convert flat dict → structured JSON schema.

    Designed so positions[] is an array (forward-compat for multi-position).
    The flat dict's PAIR/DIRECTION/ENTRY_PRICE/etc. map to positions[0].
    """
    pos_open = str(flat.get("POSITION_OPEN", "UNKNOWN")).upper()
    positions = []
    if pos_open == "YES":
        positions.append({
            "pair": str(flat.get("PAIR", "—")),
            "direction": str(flat.get("DIRECTION", "—")),
            "entry_price": _to_float(flat.get("ENTRY_PRICE", 0)),
            "mark_price": _to_float(flat.get("MARK_PRICE", 0)),
            "size": _to_float(flat.get("SIZE", 0)),
            "sl_price": _to_float(flat.get("SL_PRICE", 0)),
            "tp_price": _to_float(flat.get("TP_PRICE", 0)),
            "tp2_price": _to_float(flat.get("TP2_PRICE", 0)),
            "leverage": int(_to_float(flat.get("LEVERAGE", 0))),
            "margin_type": str(flat.get("MARGIN_TYPE", "isolated")),
            "margin": _to_float(flat.get("MARGIN", 0)),
            "liquidation": _to_float(flat.get("LIQUIDATION", 0)),
            "unrealized_pnl": _to_float(flat.get("UNREALIZED_PNL", 0)),
            "platform": str(flat.get("PLATFORM", "aster")),
            "trailing_sl_active": str(flat.get("TRAILING_SL_ACTIVE", "NO")).upper() == "YES",
            "trailing_sl_last_move": str(flat.get("TRAILING_SL_LAST_MOVE", "—")),
            "tp_extended": str(flat.get("TP_EXTENDED", "NO")).upper() == "YES",
            "tp_extend_count": int(_to_float(flat.get("TP_EXTEND_COUNT", 0))),
        })

    return {
        "_version": _SCHEMA_VERSION,
        "system": {
            "status": str(flat.get("SYSTEM_STATUS", "ACTIVE")),
            "last_updated": str(flat.get("LAST_UPDATED", "—")),
            "market_mode": str(flat.get("MARKET_MODE", "UNKNOWN")),
            "mode_confirmed_cycles": int(_to_float(flat.get("MODE_CONFIRMED_CYCLES", 0))),
        },
        "positions": positions,
        "risk": {
            "daily_loss": _to_float(flat.get("DAILY_LOSS", 0)),
            "daily_loss_limit": _to_float(flat.get("DAILY_LOSS_LIMIT", 0.15)),
            "consecutive_losses": int(_to_float(flat.get("CONSECUTIVE_LOSSES", 0))),
            "cooldown_active": str(flat.get("COOLDOWN_ACTIVE", "NO")).upper() == "YES",
            "cooldown_ends": str(flat.get("COOLDOWN_ENDS", "—")),
            "cooldown_until": str(flat.get("COOLDOWN_UNTIL", "—")),
            "trades_today": int(_to_float(flat.get("TRADES_TODAY", 0))),
            "wins_today": int(_to_float(flat.get("WINS_TODAY", 0))),
            "losses_today": int(_to_float(flat.get("LOSSES_TODAY", 0))),
        },
        "account": {
            "balance_usdt": _to_float(flat.get("BALANCE_USDT", 0)),
            "available_margin": _to_float(flat.get("AVAILABLE_MARGIN", 0)),
            "last_balance_check": str(flat.get("LAST_BALANCE_CHECK", "—")),
        },
        "reentry": {
            "eligible": str(flat.get("REENTRY_ELIGIBLE", "NO")).upper() == "YES",
            "pair": str(flat.get("REENTRY_PAIR", "—")),
            "direction": str(flat.get("REENTRY_DIRECTION", "—")),
            "original_entry": _to_float(flat.get("REENTRY_ORIGINAL_ENTRY", 0)),
            "exit_time": str(flat.get("REENTRY_EXIT_TIME", "—")),
            "cycles_remaining": int(_to_float(flat.get("REENTRY_CYCLES_REMAINING", 0))),
        },
        "meta": {
            "last_trade_time": str(flat.get("LAST_TRADE_TIME", "—")),
            "timestamp": datetime.now(HKT).strftime("%Y-%m-%d %H:%M"),
        },
    }


def _json_to_flat(data: dict) -> dict:
    """Convert structured JSON → flat dict (backward-compatible).

    All consumers use flat["KEY"] — this preserves that interface.
    """
    flat = _default_state()

    # System
    sys_data = data.get("system", {})
    flat["SYSTEM_STATUS"] = sys_data.get("status", "ACTIVE")
    flat["LAST_UPDATED"] = sys_data.get("last_updated", "—")
    flat["MARKET_MODE"] = sys_data.get("market_mode", "UNKNOWN")
    flat["MODE_CONFIRMED_CYCLES"] = sys_data.get("mode_confirmed_cycles", 0)

    # Positions — flatten first position (backward compat)
    positions = data.get("positions", [])
    if positions:
        pos = positions[0]
        flat["POSITION_OPEN"] = "YES"
        flat["PAIR"] = pos.get("pair", "—")
        flat["DIRECTION"] = pos.get("direction", "—")
        flat["ENTRY_PRICE"] = pos.get("entry_price", 0.0)
        flat["MARK_PRICE"] = pos.get("mark_price", 0.0)
        flat["SIZE"] = pos.get("size", 0.0)
        flat["SL_PRICE"] = pos.get("sl_price", 0.0)
        flat["TP_PRICE"] = pos.get("tp_price", 0.0)
        flat["TP2_PRICE"] = pos.get("tp2_price", 0.0)
        flat["LEVERAGE"] = pos.get("leverage", 0)
        flat["MARGIN_TYPE"] = pos.get("margin_type", "isolated")
        flat["MARGIN"] = pos.get("margin", 0.0)
        flat["LIQUIDATION"] = pos.get("liquidation", 0.0)
        flat["UNREALIZED_PNL"] = pos.get("unrealized_pnl", 0.0)
        flat["PLATFORM"] = pos.get("platform", "aster")
        flat["TRAILING_SL_ACTIVE"] = "YES" if pos.get("trailing_sl_active") else "NO"
        flat["TRAILING_SL_LAST_MOVE"] = pos.get("trailing_sl_last_move", "—")
        flat["TP_EXTENDED"] = "YES" if pos.get("tp_extended") else "NO"
        flat["TP_EXTEND_COUNT"] = pos.get("tp_extend_count", 0)
    else:
        flat["POSITION_OPEN"] = "NO"

    # Risk
    risk = data.get("risk", {})
    flat["DAILY_LOSS"] = risk.get("daily_loss", 0.0)
    flat["DAILY_LOSS_LIMIT"] = risk.get("daily_loss_limit", 0.15)
    flat["CONSECUTIVE_LOSSES"] = risk.get("consecutive_losses", 0)
    flat["COOLDOWN_ACTIVE"] = "YES" if risk.get("cooldown_active") else "NO"
    flat["COOLDOWN_ENDS"] = risk.get("cooldown_ends", "—")
    flat["COOLDOWN_UNTIL"] = risk.get("cooldown_until", "—")
    flat["TRADES_TODAY"] = risk.get("trades_today", 0)
    flat["WINS_TODAY"] = risk.get("wins_today", 0)
    flat["LOSSES_TODAY"] = risk.get("losses_today", 0)

    # Account
    acct = data.get("account", {})
    flat["BALANCE_USDT"] = acct.get("balance_usdt", 0.0)
    flat["AVAILABLE_MARGIN"] = acct.get("available_margin", 0.0)
    flat["LAST_BALANCE_CHECK"] = acct.get("last_balance_check", "—")

    # Reentry
    reentry = data.get("reentry", {})
    flat["REENTRY_ELIGIBLE"] = "YES" if reentry.get("eligible") else "NO"
    flat["REENTRY_PAIR"] = reentry.get("pair", "—")
    flat["REENTRY_DIRECTION"] = reentry.get("direction", "—")
    flat["REENTRY_ORIGINAL_ENTRY"] = reentry.get("original_entry", 0.0)
    flat["REENTRY_EXIT_TIME"] = reentry.get("exit_time", "—")
    flat["REENTRY_CYCLES_REMAINING"] = reentry.get("cycles_remaining", 0)

    # Meta
    meta = data.get("meta", {})
    flat["LAST_TRADE_TIME"] = meta.get("last_trade_time", "—")

    return flat


# ─── MD Read/Write (legacy) ───

def _read_md(path: str) -> dict | None:
    """Parse TRADE_STATE.md → flat dict. Returns None if file missing."""
    if not os.path.exists(path):
        return None

    state = {}
    in_code_block = False

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if not line or line.startswith("#") or line.startswith("⚠"):
                    continue
                match = re.match(r'^([A-Z_]+):\s*(.+)$', line)
                if match:
                    key = match.group(1)
                    val = match.group(2).strip()
                    val = re.sub(r'（.+）$', '', val).strip()
                    state[key] = _try_parse_value(val)
    except IOError as e:
        logger.warning(f"MD state read failed ({path}): {e}")
        return None

    # Ensure all expected keys exist
    defaults = _default_state()
    for k, v in defaults.items():
        if k not in state:
            state[k] = v

    return state


def _write_md(updates: dict, path: str | None = None) -> bool:
    """Update specific fields in TRADE_STATE.md (legacy writer)."""
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
                old_match = re.match(r'^([A-Z_]+):\s*\S+(\s*（.+）)?', stripped)
                suffix = old_match.group(2) if old_match and old_match.group(2) else ""
                new_lines.append(f"{key}: {val}{suffix}\n")
                updated = True
                break
        if not updated:
            new_lines.append(line)

    dir_name = os.path.dirname(p)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return True


# ─── Backup + Recovery ───

def _backup_state(json_path: str) -> None:
    """Create timestamped backup before overwrite."""
    if not os.path.exists(json_path):
        return
    try:
        os.makedirs(TRADE_STATE_BACKUP_DIR, exist_ok=True)
        ts = datetime.now(HKT).strftime("%Y%m%dT%H%M%S")
        backup_path = os.path.join(TRADE_STATE_BACKUP_DIR, f"{ts}_TRADE_STATE.json")
        with open(json_path, "r", encoding="utf-8") as src:
            content = src.read()
        with open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(content)
        _prune_backups()
    except Exception as e:
        logger.warning(f"Backup failed: {e}")


def _prune_backups(keep_recent: int = 48, keep_daily: int = 7) -> None:
    """Prune old backups. Keep recent N + daily snapshots for M days."""
    if not os.path.exists(TRADE_STATE_BACKUP_DIR):
        return
    try:
        files = sorted(
            [f for f in os.listdir(TRADE_STATE_BACKUP_DIR) if f.endswith(".json")],
            reverse=True,  # newest first
        )
        if len(files) <= keep_recent:
            return

        # Keep first `keep_recent` files unconditionally
        to_keep = set(files[:keep_recent])

        # Keep one per day for last `keep_daily` days
        seen_days: set[str] = set()
        for fname in files:
            day = fname[:8]  # "20260313" from "20260313T..."
            if day not in seen_days and len(seen_days) < keep_daily:
                to_keep.add(fname)
                seen_days.add(day)

        # Delete the rest
        for fname in files:
            if fname not in to_keep:
                try:
                    os.unlink(os.path.join(TRADE_STATE_BACKUP_DIR, fname))
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"Backup prune failed: {e}")


def _read_latest_backup() -> dict | None:
    """Read the most recent backup as flat dict."""
    if not os.path.exists(TRADE_STATE_BACKUP_DIR):
        return None
    try:
        files = sorted(
            [f for f in os.listdir(TRADE_STATE_BACKUP_DIR) if f.endswith(".json")],
            reverse=True,
        )
        if not files:
            return None
        path = os.path.join(TRADE_STATE_BACKUP_DIR, files[0])
        return _read_json(path)
    except Exception:
        return None


# ─── Helpers ───

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


def _to_float(val: Any) -> float:
    """Safe float conversion — strips $ and , and unit text."""
    try:
        s = str(val).replace("$", "").replace(",", "").replace("%", "").strip()
        # Handle "1.059 XAG" style
        parts = s.split()
        return float(parts[0])
    except (TypeError, ValueError, IndexError):
        return 0.0


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
        "COOLDOWN_UNTIL": "—",
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
        "TP2_PRICE": 0.0,
        "LEVERAGE": 0,
        "MARGIN_TYPE": "isolated",
        "MARGIN": 0.0,
        "LIQUIDATION": 0.0,
        "UNREALIZED_PNL": 0.0,
        "PLATFORM": "aster",
        "BALANCE_USDT": 0.0,
        "AVAILABLE_MARGIN": 0.0,
        "LAST_BALANCE_CHECK": "—",
        "TRADES_TODAY": 0,
        "WINS_TODAY": 0,
        "LOSSES_TODAY": 0,
        "LAST_TRADE_TIME": "—",
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
