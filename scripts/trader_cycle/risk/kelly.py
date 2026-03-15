"""
kelly.py — Per-regime Kelly Criterion position sizing

Computes risk-adjusted Kelly fraction from live trade history.
Replaces fixed base_risk (params.risk_pct) when sufficient per-regime data exists.

Design decisions:
- Reads trades.jsonl directly (not via metrics.py) to avoid coupling to display layer.
- CV correction penalises high-variance edge: high CV → smaller fraction.
- Half-Kelly (×0.5): standard practice — sacrifices ~25% growth for ~50% variance reduction.
- Returns None if insufficient data → caller falls back to fixed risk.
- Returns KELLY_NO_EDGE if f*≤0 → caller must block trade (no statistical edge).

Limitation: exit records use ctx.market_mode at close time, which may differ from
entry-time regime for cross-regime trades. Acceptable: most trades open+close in
same regime; cross-regime trades are noise diluted as data grows.

Revisit if trades.jsonl exceeds ~5000 records — add tail-read optimisation.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path

from ..config.settings import (
    KELLY_MIN_TRADES_TREND, KELLY_MIN_TRADES_RANGE, KELLY_MIN_TRADES_CRASH,
    KELLY_WINDOW_N, KELLY_MIN_RISK, KELLY_MAX_RISK, KELLY_NO_EDGE,
)

log = logging.getLogger(__name__)

_BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
_TRADES_FILE = _BASE_DIR / "memory" / "store" / "trades.jsonl"

_MODE_TO_STRATEGY = {
    "RANGE": "range",
    "TREND": "trend",
    "CRASH": "crash",
}

_MIN_TRADES_BY_MODE = {
    "RANGE": KELLY_MIN_TRADES_RANGE,
    "TREND": KELLY_MIN_TRADES_TREND,
    "CRASH": KELLY_MIN_TRADES_CRASH,
}


def compute_kelly_base_risk(market_mode: str) -> float | None:
    """Compute half-Kelly base risk for the given market mode.

    Returns:
      float > 0      Kelly-derived base_risk (clamped to [KELLY_MIN_RISK, KELLY_MAX_RISK])
      KELLY_NO_EDGE  enough data but f*≤0 → caller MUST block trade
      None           insufficient data → caller uses fixed params.risk_pct

    Note: KELLY_NO_EDGE is -1.0 — compared with == because it is a direct
    constant return, not a computed float. Do not change to approximate comparison.
    """
    strategy_key = _MODE_TO_STRATEGY.get(market_mode)
    if strategy_key is None:
        log.debug("Kelly: unknown market_mode=%s → skip", market_mode)
        return None

    closed = _load_closed_trades_for_regime(strategy_key)

    min_trades = _MIN_TRADES_BY_MODE.get(market_mode, KELLY_MIN_TRADES_TREND)
    if len(closed) < min_trades:
        log.info(
            "Kelly[%s]: insufficient data (%d/%d required) → fixed fallback",
            market_mode, len(closed), min_trades,
        )
        return None

    return _kelly_fraction(closed, market_mode)


def _load_closed_trades_for_regime(strategy_key: str) -> list[dict]:
    """Load last KELLY_WINDOW_N closed trades for a specific strategy.

    A closed trade has: strategy == strategy_key, exit is not None, pnl is not None.
    Old records without 'strategy' field are excluded (they default to "unknown").
    """
    if not _TRADES_FILE.exists():
        return []

    matched: list[dict] = []
    with open(_TRADES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (rec.get("strategy") == strategy_key
                    and rec.get("exit") is not None
                    and rec.get("pnl") is not None):
                matched.append(rec)

    # Chronological order (jsonl is append-only) → last N = most recent
    return matched[-KELLY_WINDOW_N:]


def _kelly_fraction(trades: list[dict], market_mode: str) -> float:
    """Full Kelly with CV correction and half-Kelly.

    Formula:
      b       = avg_win / avg_loss                         (payoff ratio)
      f*      = (wr × b − (1 − wr)) / b                   (raw Kelly)
      CV      = std(edge_i) / (mean(edge_i) × √n)         (SE/mean of edge estimate)
      f_adj   = f* × max(0, 1 − CV)                       (CV-corrected)
      result  = clamp(f_adj × 0.5, KELLY_MIN_RISK, KELLY_MAX_RISK)

    Returns KELLY_NO_EDGE if f*≤0.
    """
    wins = [t for t in trades if float(t["pnl"]) > 0]
    losses = [t for t in trades if float(t["pnl"]) <= 0]

    if not wins or not losses:
        log.info("Kelly[%s]: all wins or all losses → cannot compute", market_mode)
        return KELLY_NO_EDGE

    wr = len(wins) / len(trades)
    avg_win = sum(float(t["pnl"]) for t in wins) / len(wins)
    avg_loss = abs(sum(float(t["pnl"]) for t in losses) / len(losses))

    if avg_loss == 0:
        log.warning("Kelly[%s]: avg_loss=0 → cannot compute payoff ratio", market_mode)
        return KELLY_NO_EDGE

    b = avg_win / avg_loss
    f_star = (wr * b - (1 - wr)) / b

    if f_star <= 0:
        log.warning(
            "Kelly[%s]: f*=%.4f ≤ 0 → no edge (wr=%.1f%%, b=%.2f) → block trade",
            market_mode, f_star, wr * 100, b,
        )
        return KELLY_NO_EDGE

    # ── CV Correction ──
    # CV of the EDGE ESTIMATE, not individual trades.
    # Population CV (std/mean) is always >1 for binary win/loss outcomes,
    # which would permanently zero out Kelly — useless.
    # Instead: CV_estimate = SE / mean = std / (mean × √n).
    # This penalises small samples (high SE) and rewards data accumulation.
    n = len(trades)
    edges = [float(t["pnl"]) / avg_loss for t in trades]
    edge_mean = sum(edges) / n

    if edge_mean > 0:
        edge_var = sum((e - edge_mean) ** 2 for e in edges) / n
        edge_std = math.sqrt(edge_var)
        cv = edge_std / (edge_mean * math.sqrt(n))
    else:
        cv = float("inf")

    cv_factor = max(0.0, 1.0 - cv)
    f_adjusted = f_star * cv_factor

    # Half-Kelly + clamp
    base_risk = f_adjusted * 0.5
    base_risk = max(KELLY_MIN_RISK, min(KELLY_MAX_RISK, base_risk))

    log.info(
        "Kelly[%s]: wr=%.1f%% b=%.2f f*=%.4f CV=%.2f cv_factor=%.2f "
        "f_adj=%.4f → base_risk=%.2f%%",
        market_mode, wr * 100, b, f_star, cv, cv_factor,
        f_adjusted, base_risk * 100,
    )
    return base_risk
