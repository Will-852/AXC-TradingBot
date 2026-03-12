"""
diagnostics.py — Business-level diagnostic data collection.

Collects trading system status for /status command.
Uses read_trade_state() (not direct file parse) to ensure
forward-compatibility with Sprint 2A JSON state migration.

Each diag_*() function is independent — one failure does not block others.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from ..config.settings import (
    AXC_HOME, HKT, CYCLE_LOG_DIR, PAIRS,
    CIRCUIT_BREAKER_DAILY, MAX_CRYPTO_POSITIONS,
)
from ..state.trade_state import read_trade_state

logger = logging.getLogger(__name__)

_SHARED = os.path.join(AXC_HOME, "shared")


def diag_positions() -> dict[str, Any]:
    """Current position info: pair, direction, entry, PnL, age."""
    state = read_trade_state()
    pos_open = str(state.get("POSITION_OPEN", "NO")).upper()

    if pos_open != "YES":
        return {"has_position": False}

    pair = state.get("PAIR", "—")
    direction = state.get("DIRECTION", "—")
    entry = _safe_float(state.get("ENTRY_PRICE", 0))
    sl = _safe_float(state.get("SL_PRICE", 0))
    tp = _safe_float(state.get("TP_PRICE", 0))
    size = state.get("SIZE", "0")

    return {
        "has_position": True,
        "pair": pair,
        "direction": direction,
        "entry_price": entry,
        "sl_price": sl,
        "tp_price": tp,
        "size": size,
    }


def diag_risk() -> dict[str, Any]:
    """Risk status: daily loss, consecutive losses, cooldown, circuit breaker."""
    state = read_trade_state()
    daily_loss = _safe_float(state.get("DAILY_LOSS", 0))
    consecutive_losses = _safe_int(state.get("CONSECUTIVE_LOSSES", 0))
    cooldown_active = str(state.get("COOLDOWN_ACTIVE", "NO")).upper() == "YES"
    cooldown_ends = state.get("COOLDOWN_ENDS", "—")
    balance = _safe_float(state.get("BALANCE_USDT", 0))

    daily_loss_pct = (daily_loss / balance * 100) if balance > 0 and daily_loss > 0 else 0
    circuit_breaker_pct = CIRCUIT_BREAKER_DAILY * 100

    return {
        "daily_loss": daily_loss,
        "daily_loss_pct": daily_loss_pct,
        "circuit_breaker_pct": circuit_breaker_pct,
        "consecutive_losses": consecutive_losses,
        "cooldown_active": cooldown_active,
        "cooldown_ends": cooldown_ends,
        "balance": balance,
    }


def diag_scanner() -> dict[str, Any]:
    """Scanner status from scan log (last entry timestamp + content)."""
    scan_log = os.path.join(_SHARED, "SCAN_LOG.md")
    if not os.path.exists(scan_log):
        return {"status": "missing", "detail": "SCAN_LOG.md not found"}

    try:
        with open(scan_log, "r") as f:
            lines = f.readlines()

        # Get last non-empty line
        last_line = ""
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                last_line = stripped
                break

        if not last_line:
            return {"status": "empty", "detail": "no scan entries"}

        # Extract timestamp: [2026-03-13 14:30 UTC+8]
        if last_line.startswith("["):
            ts_end = last_line.find("]")
            ts_str = last_line[1:ts_end].replace(" UTC+8", "").strip()
            try:
                scan_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                scan_time = scan_time.replace(tzinfo=HKT)
                age_min = (datetime.now(HKT) - scan_time).total_seconds() / 60
                return {
                    "status": "ok" if age_min < 35 else "stale",
                    "last_scan": ts_str,
                    "age_min": round(age_min, 1),
                    "detail": last_line[:80],
                }
            except ValueError:
                pass

        return {"status": "unknown", "detail": last_line[:80]}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80]}


def diag_pipeline() -> dict[str, Any]:
    """Last pipeline cycle info from CYCLE_LOG_DIR."""
    if not os.path.exists(CYCLE_LOG_DIR):
        return {"status": "missing", "detail": "cycle log dir not found"}

    try:
        files = sorted(os.listdir(CYCLE_LOG_DIR))
        if not files:
            return {"status": "empty", "detail": "no cycle logs"}

        last_file = files[-1]
        last_path = os.path.join(CYCLE_LOG_DIR, last_file)
        mtime = os.path.getmtime(last_path)
        age_min = (datetime.now().timestamp() - mtime) / 60

        return {
            "status": "ok" if age_min < 35 else "stale",
            "last_cycle": last_file,
            "age_min": round(age_min, 1),
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80]}


def diag_system() -> dict[str, Any]:
    """System resources: disk usage for shared/."""
    result: dict[str, Any] = {}

    # Disk usage (always available)
    try:
        stat = os.statvfs(_SHARED)
        total_gb = (stat.f_blocks * stat.f_frsize) / (1024 ** 3)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        used_pct = ((total_gb - free_gb) / total_gb * 100) if total_gb > 0 else 0
        result["disk_total_gb"] = round(total_gb, 1)
        result["disk_free_gb"] = round(free_gb, 1)
        result["disk_used_pct"] = round(used_pct, 1)
    except Exception:
        pass

    # CPU/memory via psutil (optional dependency)
    try:
        import psutil
        result["cpu_pct"] = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        result["memory_used_pct"] = mem.percent
    except ImportError:
        pass
    except Exception:
        pass

    return result


def diag_data_freshness() -> dict[str, Any]:
    """Per-pair price timestamp freshness from SCAN_CONFIG.md."""
    scan_config_path = os.path.join(_SHARED, "SCAN_CONFIG.md")
    if not os.path.exists(scan_config_path):
        return {"status": "missing"}

    try:
        from ..state.scan_config import read_scan_config
        config = read_scan_config()
        if not config:
            return {"status": "empty"}

        from ..config.settings import PAIR_PREFIX
        freshness = {}
        for sym, prefix in PAIR_PREFIX.items():
            ts_key = f"{prefix}_price_ts"
            ts_val = config.get(ts_key, "")
            if ts_val:
                freshness[sym] = str(ts_val)

        return {"status": "ok", "pairs": freshness}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:80]}


def run_diagnostics() -> dict[str, Any]:
    """Run all diagnostic functions. Each is independent — failure is caught."""
    results: dict[str, Any] = {"timestamp": datetime.now(HKT).strftime("%Y-%m-%d %H:%M")}

    for name, func in [
        ("positions", diag_positions),
        ("risk", diag_risk),
        ("scanner", diag_scanner),
        ("pipeline", diag_pipeline),
        ("data_freshness", diag_data_freshness),
        ("system", diag_system),
    ]:
        try:
            results[name] = func()
        except Exception as e:
            results[name] = {"error": str(e)[:80]}
            logger.warning(f"diag_{name} failed: {e}")

    return results


def format_status_message(diag: dict[str, Any]) -> str:
    """Format diagnostics dict into a Telegram-friendly HTML message."""
    lines = [f"<b>AXC Status</b> ({diag.get('timestamp', '?')})"]

    # Positions
    pos = diag.get("positions", {})
    if pos.get("has_position"):
        lines.append(
            f"\n<b>Position:</b> {pos.get('pair')} {pos.get('direction')}"
            f"\n  Entry: {pos.get('entry_price')} | SL: {pos.get('sl_price')} | TP: {pos.get('tp_price')}"
        )
    else:
        lines.append("\n<b>Position:</b> None")

    # Risk
    risk = diag.get("risk", {})
    loss_pct = risk.get("daily_loss_pct", 0)
    losses = risk.get("consecutive_losses", 0)
    bal = risk.get("balance", 0)
    lines.append(
        f"\n<b>Risk:</b> Balance ${bal:.2f}"
        f"\n  Daily loss: {loss_pct:.1f}% | Losses: {losses}"
    )
    if risk.get("cooldown_active"):
        lines.append(f"  ⚠️ Cooldown until {risk.get('cooldown_ends')}")

    # Scanner
    scan = diag.get("scanner", {})
    scan_icon = "✅" if scan.get("status") == "ok" else "⚠️"
    scan_age = scan.get("age_min", "?")
    lines.append(f"\n{scan_icon} <b>Scanner:</b> {scan_age}min ago")

    # Pipeline
    pipe = diag.get("pipeline", {})
    pipe_icon = "✅" if pipe.get("status") == "ok" else "⚠️"
    pipe_age = pipe.get("age_min", "?")
    lines.append(f"{pipe_icon} <b>Pipeline:</b> {pipe_age}min ago")

    # System
    sys_info = diag.get("system", {})
    if sys_info.get("disk_free_gb"):
        lines.append(
            f"\n<b>System:</b> Disk {sys_info.get('disk_used_pct', '?')}% used"
            f" ({sys_info.get('disk_free_gb', '?')}GB free)"
        )

    return "\n".join(lines)


# ─── Helpers ───

def _safe_float(val, default: float = 0.0) -> float:
    try:
        s = str(val).replace("$", "").replace(",", "")
        return float(s)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
