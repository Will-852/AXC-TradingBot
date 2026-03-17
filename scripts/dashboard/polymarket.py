"""polymarket.py — Polymarket dashboard data + mode toggle endpoint."""

import json
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


def handle_polymarket_set_mode(body: str) -> tuple[int, dict]:
    """POST /api/polymarket/set_mode — toggle dry_run / live in state file."""
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"ok": False, "error": "Invalid JSON"}

    mode = payload.get("mode", "")
    if mode not in ("dry_run", "live"):
        return 400, {"ok": False, "error": "mode must be 'dry_run' or 'live'"}

    try:
        from polymarket.state.poly_state import read_state, write_state
        state = read_state()
        state["dry_run"] = (mode == "dry_run")
        ok = write_state(state)
        if not ok:
            return 500, {"ok": False, "error": "State write failed"}
        log.info("Polymarket mode set to %s via dashboard", mode)
        return 200, {"ok": True, "mode": mode, "message": f"Mode set to {mode.upper().replace('_', ' ')}"}
    except Exception as e:
        log.error("set_mode error: %s", e)
        return 500, {"ok": False, "error": str(e)}


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
