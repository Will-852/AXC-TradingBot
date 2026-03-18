"""
poly_state.py — POLYMARKET_STATE.json 讀寫

Atomic write pattern (tempfile + os.replace) 防止 crash 時文件損壞。
State schema 定義喺呢度，方便集中管理。
"""

import json
import logging
import os
import tempfile

from ..config.settings import POLY_STATE_PATH

logger = logging.getLogger(__name__)


def read_state(path: str = POLY_STATE_PATH) -> dict:
    """Read state file. Returns empty dict if missing or corrupt."""
    if not os.path.exists(path):
        logger.info("No state file at %s — fresh start", path)
        return {}

    try:
        with open(path, "r") as f:
            state = json.load(f)
        logger.debug("State loaded: %d keys from %s", len(state), path)
        return state
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("State read error (%s): %s", path, e)
        return {}


def write_state(state: dict, path: str = POLY_STATE_PATH) -> bool:
    """Atomic write state to JSON file.

    Returns True on success, False on error.
    """
    state_dir = os.path.dirname(path)
    os.makedirs(state_dir, exist_ok=True)

    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            fd = None  # fdopen takes ownership
        os.replace(tmp_path, path)
        return True
    except (IOError, OSError) as e:
        logger.error("State write error: %s", e)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False


def build_state_snapshot(ctx) -> dict:
    """Build complete state dict from PolyContext.

    Called by WriteStateStep to create the state to persist.
    """
    state = dict(ctx.state)

    # Meta
    state["last_updated"] = ctx.timestamp_str
    state["cycle_id"] = ctx.cycle_id
    state["usdc_balance"] = ctx.usdc_balance
    state["total_exposure"] = ctx.total_exposure
    state["exposure_pct"] = round(ctx.exposure_pct, 4)
    state["daily_pnl_pct"] = ctx.daily_pnl
    state["circuit_breaker_active"] = ctx.circuit_breaker_active
    state["dry_run"] = ctx.dry_run

    # Positions
    state["positions"] = [
        {
            "condition_id": p.condition_id,
            "title": p.title,
            "category": p.category,
            "side": p.side,
            "token_id": p.token_id,
            "shares": p.shares,
            "avg_price": p.avg_price,
            "current_price": p.current_price,
            "cost_basis": p.cost_basis,
            "market_value": p.market_value,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 4),
            "entry_time": p.entry_time,
            "end_date": p.end_date,
            "hedge_side": p.hedge_side,
            "hedge_size": p.hedge_size,
            "hedge_entry_px": p.hedge_entry_px,
        }
        for p in ctx.open_positions
    ]

    # Scan summary
    state["last_scan"] = {
        "scanned": len(ctx.scanned_markets),
        "filtered": len(ctx.filtered_markets),
        "assessments": len(ctx.edge_assessments),
        "signals": len(ctx.signals),
        "executed": len(ctx.executed_trades),
    }

    # Merge step-specific updates (e.g., circuit breaker state)
    state.update(ctx.state_updates)

    return state
