"""
migrations.py — JSON state schema version migrations.

Sequential migration functions: v0→v1, v1→v2, etc.
Each migration transforms the JSON data dict in-place.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_migrations(data: dict, from_version: int, to_version: int) -> dict:
    """Apply all migrations from from_version to to_version.

    Each migration function takes a dict and returns the updated dict.
    Migrations are applied sequentially: v0→v1→v2→...
    """
    for v in range(from_version, to_version):
        if v < len(_MIGRATIONS):
            migrator = _MIGRATIONS[v]
            logger.info(f"Running migration v{v} → v{v+1}")
            data = migrator(data)
            data["_version"] = v + 1
        else:
            logger.warning(f"No migration defined for v{v} → v{v+1}")
            break
    return data


def _migrate_v0_to_v1(data: dict) -> dict:
    """v0 → v1: Ensure positions is an array, add missing sections.

    v0 had no formal schema — could be anything from MD conversion.
    v1 establishes: positions[], system{}, risk{}, account{}, reentry{}, meta{}.
    """
    # If positions key missing or not a list, wrap single position
    if "positions" not in data or not isinstance(data["positions"], list):
        data["positions"] = []

    # Ensure required sections exist with defaults
    data.setdefault("system", {
        "status": "ACTIVE",
        "last_updated": "—",
        "market_mode": "UNKNOWN",
        "mode_confirmed_cycles": 0,
    })
    data.setdefault("risk", {
        "daily_loss": 0.0,
        "daily_loss_limit": 0.15,
        "consecutive_losses": 0,
        "cooldown_active": False,
        "cooldown_ends": "—",
        "cooldown_until": "—",
        "trades_today": 0,
        "wins_today": 0,
        "losses_today": 0,
    })
    data.setdefault("account", {
        "balance_usdt": 0.0,
        "available_margin": 0.0,
        "last_balance_check": "—",
    })
    data.setdefault("reentry", {
        "eligible": False,
        "pair": "—",
        "direction": "—",
        "original_entry": 0.0,
        "exit_time": "—",
        "cycles_remaining": 0,
    })
    data.setdefault("meta", {
        "last_trade_time": "—",
        "timestamp": "—",
    })

    return data


# Migration registry — index = from_version
_MIGRATIONS = [
    _migrate_v0_to_v1,  # v0 → v1
]
