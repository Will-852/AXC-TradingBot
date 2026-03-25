"""
mm/state_io.py — State persistence and logging for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🟡 VERIFY: atomic write pattern must be preserved exactly.
"""

import json
import os
import tempfile
from datetime import datetime

from polymarket.mm.constants import (
    _FILL_STATS_DEFAULT, _HKT, _LOG_DIR, _ORDER_LOG, _POS_LOG,
    _STATE_PATH, _TRADE_LOG,
)


def load() -> dict:
    """Load MM state from disk. Returns default state if file missing/corrupt."""
    if not os.path.exists(_STATE_PATH):
        return _default_state()
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        return d
    except Exception:
        return _default_state()


def _default_state() -> dict:
    return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
            "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
            "consecutive_losses": 0, "cooldown_until": "",
            "daily_pnl_date": "", "last_scan": "",
            "fill_stats": dict(_FILL_STATS_DEFAULT)}


def save(state: dict):
    """Atomic write: tempfile → os.replace. Never leaves partial state on disk."""
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_STATE_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def log_positions(state: dict):
    """Append position snapshot to mm_positions.jsonl (post-session analysis)."""
    try:
        markets = state.get("markets", {})
        if not markets:
            return
        ts = datetime.now(_HKT).isoformat()
        for cid, m in markets.items():
            up_s = m.get("up_shares", 0)
            dn_s = m.get("down_shares", 0)
            if up_s == 0 and dn_s == 0:
                continue
            row = {"ts": ts, "cid": cid[:16], "up_s": round(up_s, 2),
                   "dn_s": round(dn_s, 2), "up_a": round(m.get("up_avg_price", 0), 4),
                   "dn_a": round(m.get("down_avg_price", m.get("dn_avg_price", 0)), 4),
                   "cost": round(m.get("entry_cost", 0), 2)}
            with open(_POS_LOG, "a") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # non-critical logging


def to_dict(s) -> dict:
    """MMMarketState → dict. Preserves dataclass fields only."""
    from polymarket.strategy.market_maker import MMMarketState
    d = {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}
    return d


def from_dict(d: dict):
    """dict → MMMarketState."""
    from polymarket.strategy.market_maker import MMMarketState
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def log_trade(record: dict, log_path: str = ""):
    """Append trade record to JSONL log."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    _path = log_path or _TRADE_LOG
    with open(_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def log_order(event: str, order_id: str, cid: str, **kwargs):
    """Per-order lifecycle log: submit/fill/cancel/post_fill.

    Enables AS analysis: time_to_fill, mid_at_fill, mid_60s_post_fill.
    """
    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": event,
        "order_id": order_id[:16] if order_id else "",
        "cid": cid[:8] if cid else "",
    }
    record.update(kwargs)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_ORDER_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def bump_fill(state: dict, event: str, n: int = 1):
    """Increment fill rate counter. event: submitted/filled/cancelled/expired."""
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


def fill_rate(state: dict) -> tuple[float, int, int]:
    """Returns (fill_rate_pct, filled, submitted). 0% if no data."""
    fs = state.get("fill_stats", _FILL_STATS_DEFAULT)
    s, f = fs.get("submitted", 0), fs.get("filled", 0)
    return (f / s * 100 if s > 0 else 0.0), f, s
