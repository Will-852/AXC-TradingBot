"""polymarket.py — Polymarket dashboard data + command endpoints.

Serves the Polymarket dashboard tab:
- GET  /api/polymarket/data       → state + trades + CB + strategy + calibration
- POST /api/polymarket/set_mode   → toggle dry_run / live
- POST /api/polymarket/force_scan → run Gamma API scan, return market list
- POST /api/polymarket/reset_cb   → reset a circuit breaker by service name
- POST /api/polymarket/check_merge → run merge detection, return results
"""

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


def _get_circuit_breaker_statuses() -> list[dict]:
    """Get 3-state circuit breaker statuses for all services."""
    try:
        from polymarket.risk.circuit_breaker import all_statuses
        return all_statuses()
    except Exception as e:
        log.debug("CB statuses unavailable: %s", e)
        return []


def _get_strategy_breakdown(trades: list[dict]) -> dict:
    """Compute signal_source distribution from trade log."""
    breakdown: dict[str, int] = {}
    for t in trades:
        src = "unknown"
        # Check reasoning field for source hint
        reasoning = t.get("reasoning", "")
        if "Logical arb" in reasoning or "Ordering violation" in reasoning:
            src = "logical_arb"
        elif "cvd" in reasoning.lower() or "divergence" in reasoning.lower():
            src = "cvd"
        elif "microstructure" in reasoning.lower() or "volume spike" in reasoning.lower():
            src = "microstructure"
        elif "indicator" in reasoning.lower():
            src = "indicator"
        elif t.get("confidence", 0) > 0:
            src = "ai"
        # Fallback: check action (only if no source identified)
        if src == "unknown":
            action = t.get("action", "")
            if action == "sell":
                src = "exit"

        breakdown[src] = breakdown.get(src, 0) + 1

    return breakdown


def _compute_pnl_series(trades: list[dict]) -> list[dict]:
    """Compute cumulative PnL time series from trade log."""
    series = []
    cumulative = 0.0
    for t in trades:
        pnl = t.get("pnl")
        if pnl is not None:
            cumulative += pnl
            series.append({
                "timestamp": t.get("timestamp", ""),
                "pnl": round(pnl, 4),
                "cumulative": round(cumulative, 4),
            })
        elif t.get("action") == "buy":
            # Buy trades: show as cost event
            series.append({
                "timestamp": t.get("timestamp", ""),
                "pnl": 0,
                "cumulative": round(cumulative, 4),
                "action": "buy",
                "amount": t.get("amount_usdc", 0),
            })
    return series


# ─────────────────────────────────────────────────────────────────────
# Main data endpoint
# ─────────────────────────────────────────────────────────────────────

def handle_polymarket_data() -> tuple[int, dict]:
    """GET /api/polymarket/data — full dashboard payload."""
    try:
        from polymarket.state.poly_state import read_state
        state = read_state()
    except Exception as e:
        log.warning("read_state failed: %s", e)
        state = {"error": f"read_state: {e}"}

    try:
        from polymarket.state.trade_log import read_trades
        trades = read_trades(last_n=50)  # more trades for chart
    except Exception as e:
        log.warning("read_trades failed: %s", e)
        trades = []

    return 200, {
        "state": state,
        "trades": trades[:20],  # table: last 20
        "trades_all": trades,   # chart: all 50
        "pnl_series": _compute_pnl_series(trades),
        "calibration": _get_calibration(),
        "circuit_breakers": _get_circuit_breaker_statuses(),
        "strategy_breakdown": _get_strategy_breakdown(trades),
    }


# ─────────────────────────────────────────────────────────────────────
# Command endpoints
# ─────────────────────────────────────────────────────────────────────

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


def handle_polymarket_force_scan(body: str) -> tuple[int, dict]:
    """POST /api/polymarket/force_scan — run Gamma scan + arb detection."""
    try:
        from polymarket.exchange.gamma_client import GammaClient
        from polymarket.strategy.market_scanner import scan_markets
        from polymarket.strategy.logical_arb import detect_arb

        g = GammaClient()
        scanned, filtered = scan_markets(g, limit=100, verbose=False)

        # Run arb detection
        opps = detect_arb(scanned, gamma_client=g, verbose=False)

        markets_data = []
        for m in scanned:
            markets_data.append({
                "condition_id": m.condition_id,
                "title": m.title[:60],
                "category": m.category,
                "yes_price": m.yes_price,
                "no_price": m.no_price,
                "volume_24h": m.volume_24h,
                "liquidity": m.liquidity,
                "event_id": m.event_id,
                "neg_risk": m.neg_risk,
            })

        arb_data = []
        for o in opps:
            arb_data.append({
                "type": o.arb_type,
                "event_slug": o.event_slug,
                "edge_pct": round(o.edge_pct * 100, 2),
                "sum_prices": round(o.sum_prices, 4),
                "detail": o.detail,
                "n_markets": len(o.markets),
            })

        # Update state with last scan info
        try:
            from polymarket.state.poly_state import read_state, write_state
            state = read_state()
            state["last_scan"] = {
                "scanned": len(scanned),
                "filtered": len(filtered),
                "assessments": 0,
                "signals": 0,
                "executed": 0,
                "arb_opportunities": len(opps),
            }
            write_state(state)
        except Exception:
            pass

        log.info("Dashboard force scan: %d markets, %d filtered, %d arbs", len(scanned), len(filtered), len(opps))
        return 200, {
            "ok": True,
            "scanned": len(scanned),
            "filtered": len(filtered),
            "markets": markets_data,
            "arb_opportunities": arb_data,
        }
    except Exception as e:
        log.error("force_scan error: %s", e)
        return 500, {"ok": False, "error": str(e)}


def handle_polymarket_reset_cb(body: str) -> tuple[int, dict]:
    """POST /api/polymarket/reset_cb — reset a circuit breaker."""
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"ok": False, "error": "Invalid JSON"}

    service = payload.get("service", "")
    if not service:
        return 400, {"ok": False, "error": "service name required"}

    try:
        from polymarket.risk.circuit_breaker import get_circuit_breaker
        cb = get_circuit_breaker(service)
        old_state = cb.state.value
        cb.reset()
        log.info("Dashboard reset CB[%s]: %s → closed", service, old_state)
        return 200, {"ok": True, "service": service, "old_state": old_state, "new_state": "closed"}
    except Exception as e:
        log.error("reset_cb error: %s", e)
        return 500, {"ok": False, "error": str(e)}


def handle_polymarket_check_merge(body: str) -> tuple[int, dict]:
    """POST /api/polymarket/check_merge — run merge detection."""
    try:
        from polymarket.state.poly_state import read_state
        state = read_state()

        # Need proxy wallet address — check if available
        # In paper mode, return a message
        if state.get("dry_run", True):
            return 200, {
                "ok": True,
                "mergeables": [],
                "total_reclaimable": 0,
                "message": "Merge check skipped (DRY RUN mode — no real positions)",
            }

        # Try to get address from env
        import os
        address = os.environ.get("POLY_PROXY_ADDRESS", "")
        if not address:
            return 200, {
                "ok": True,
                "mergeables": [],
                "total_reclaimable": 0,
                "message": "No POLY_PROXY_ADDRESS configured",
            }

        from polymarket.risk.position_merger import detect_mergeable
        mergeables = detect_mergeable(address, verbose=False)

        result = []
        for m in mergeables:
            result.append({
                "condition_id": m.condition_id,
                "title": m.title,
                "yes_shares": m.yes_shares,
                "no_shares": m.no_shares,
                "mergeable_pairs": m.mergeable_pairs,
                "reclaimable_usdc": m.reclaimable_usdc,
            })

        total = sum(m.reclaimable_usdc for m in mergeables)
        return 200, {
            "ok": True,
            "mergeables": result,
            "total_reclaimable": round(total, 2),
        }
    except Exception as e:
        log.error("check_merge error: %s", e)
        return 500, {"ok": False, "error": str(e)}
