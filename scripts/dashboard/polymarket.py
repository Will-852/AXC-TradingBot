"""polymarket.py — Polymarket dashboard data endpoint."""

import logging
import time

log = logging.getLogger(__name__)

# ── Calibration cache (30 min) ──────────────────────────────────────
_cal_cache: dict | None = None
_cal_ts: float = 0
_CAL_TTL = 1800  # 30 min


def _get_calibration() -> dict:
    """Cached calibration: Brier score + edge accuracy."""
    global _cal_cache, _cal_ts
    now = time.time()
    if _cal_cache is not None and (now - _cal_ts) < _CAL_TTL:
        return _cal_cache

    result = {}
    try:
        from polymarket.strategy.weather_tracker import compute_brier_score
        result["brier"] = compute_brier_score()
    except Exception as e:
        log.debug("Brier score unavailable: %s", e)
        result["brier"] = None

    try:
        from polymarket.strategy.weather_tracker import compute_edge_calibration
        result["edge"] = compute_edge_calibration()
    except Exception as e:
        log.debug("Edge calibration unavailable: %s", e)
        result["edge"] = None

    _cal_cache = result
    _cal_ts = now
    return result


def handle_polymarket_data() -> tuple[int, dict]:
    """GET /api/polymarket/data — state + trades + calibration."""
    try:
        from polymarket.state.poly_state import read_state
        state = read_state()
    except Exception as e:
        log.warning("read_state failed: %s", e)
        state = {"error": f"read_state: {e}"}

    try:
        from polymarket.state.trade_log import read_trades
        trades = read_trades(last_n=20)
    except Exception as e:
        log.warning("read_trades failed: %s", e)
        trades = []

    calibration = _get_calibration()

    return 200, {
        "state": state,
        "trades": trades,
        "calibration": calibration,
    }
