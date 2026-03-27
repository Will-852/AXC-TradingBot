"""
mm/state_io.py — State persistence and logging for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🟡 VERIFY: atomic write pattern must be preserved exactly.
"""

import json
import logging
import os
import tempfile
from datetime import datetime

from polymarket.mm.constants import (
    _FILL_STATS_DEFAULT, _HKT, _LOG_DIR, _MIN_VIABLE_BUDGET,
    _ORDER_LOG, _POS_LOG, _STATE_PATH, _TRADE_LOG,
)

log = logging.getLogger(__name__)


def load() -> dict:
    """Load MM state from disk. Returns default state if file missing/corrupt.

    💀 WARNING: on corruption, silently returns default with bankroll=100.0.
    Caller should use validate.validate_bankroll() to catch unexpected resets.
    """
    if not os.path.exists(_STATE_PATH):
        return _default_state()
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d = _migrate(d)
        # ⚠️ 容易錯 #4: warning after json.load success, before return
        # 💀 Bankroll sanity check — catches silent state resets
        bankroll = d.get("bankroll", 0)
        if isinstance(bankroll, (int, float)) and bankroll < _MIN_VIABLE_BUDGET:
            log.warning("⚠️ Loaded bankroll $%.2f < min viable $%.2f — "
                        "check if state was corrupted or balance depleted",
                        bankroll, _MIN_VIABLE_BUDGET)
        return d
    except Exception:
        log.warning("⚠️ Failed to load state from %s — returning default", _STATE_PATH)
        return _default_state()


_STATE_VERSION = 1  # Increment when schema changes; add migration below.


def _default_state() -> dict:
    return {"_version": _STATE_VERSION,
            "markets": {}, "watchlist": {}, "daily_pnl": 0.0,
            "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
            "consecutive_losses": 0, "cooldown_until": "",
            "daily_pnl_date": "", "last_scan": "",
            "fill_stats": dict(_FILL_STATS_DEFAULT)}


def _migrate(state: dict) -> dict:
    """Run forward migrations. Each step upgrades one version.

    💀 ⚠️ #15: migration failure → return state as-is (don't crash loop).
    """
    v = state.get("_version", 0)
    try:
        # v0 → v1: add _version field + fill_stats default
        if v < 1:
            state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
            state["_version"] = 1
            log.info("State migrated v0 → v1 (added _version + fill_stats)")
        # Future: if v < 2: ... state["_version"] = 2
    except Exception as e:
        log.warning("State migration failed at v%d: %s — using state as-is", v, e)
    return state


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


def log_trade(record: dict, log_path: str = "", coin: str = ""):
    """Append trade record to JSONL log. Per-coin file if coin provided."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    _coin = coin or record.get("coin", "")
    if _coin:
        from polymarket.mm.constants import trade_path
        _path = log_path or trade_path(_coin)
    else:
        _path = log_path or _TRADE_LOG
    with open(_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def log_order(event: str, order_id: str, cid: str, coin: str = "", **kwargs):
    """Per-order lifecycle log: submit/fill/cancel/post_fill.

    Writes to per-coin file if coin provided. Enables AS analysis.
    """
    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": event,
        "order_id": order_id[:16] if order_id else "",
        "cid": cid[:8] if cid else "",
    }
    if coin:
        record["coin"] = coin
    record.update(kwargs)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        if coin:
            from polymarket.mm.constants import order_path
            _path = order_path(coin)
        else:
            _path = _ORDER_LOG
        with open(_path, "a") as f:
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
