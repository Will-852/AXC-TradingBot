"""
oos_validation.py — Out-of-Sample validation for backtest robustness.

Design decisions:
- Single split (70/30 default) — simplest, sufficient for initial implementation
- Computes same core metrics for IS and OOS segments independently
- Stability = OOS_metric / IS_metric * 100 (inverted for DD: IS_DD / OOS_DD)
- Grade based on how many metrics pass stability threshold
- No numpy dependency — plain Python math only
"""

import logging
import math

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────
MIN_TOTAL_TRADES = 10
MIN_OOS_TRADES = 5
STABILITY_PASS = 60.0   # >= 60% = PASS
STABILITY_WARN = 40.0   # 40-60% = WARN, < 40% = FAIL
METRICS_TO_PASS = 4     # >= 4/5 = PASS, 3/5 = WARN, < 3 = FAIL
ANNUALISATION_FACTOR = 365.25  # for Sharpe (daily PnL assumed)


def run_oos_validation(trades, initial_balance=10000.0, split_ratio=0.7):
    """
    Run out-of-sample validation on backtest trades.

    Accepts BTTrade objects (.pnl attr) or dicts with 'pnl' key.
    Returns None with logged message if insufficient data.
    """
    if not trades:
        log.warning("OOS validation: no trades provided")
        return None

    n = len(trades)
    if n < MIN_TOTAL_TRADES:
        log.warning("OOS validation: only %d trades (need >= %d)", n, MIN_TOTAL_TRADES)
        return None

    split_idx = int(n * split_ratio)
    # Guard: ensure at least 1 IS trade and MIN_OOS_TRADES OOS trades
    split_idx = max(1, min(split_idx, n - MIN_OOS_TRADES))

    is_trades = trades[:split_idx]
    oos_trades = trades[split_idx:]

    is_pnls = _extract_pnls(is_trades)
    oos_pnls = _extract_pnls(oos_trades)

    oos_count = len(oos_pnls)

    # Insufficient OOS trades → special grade
    if oos_count < MIN_OOS_TRADES:
        log.warning("OOS validation: only %d OOS trades (need >= %d)", oos_count, MIN_OOS_TRADES)
        return {
            "split_ratio": split_ratio,
            "is_trade_count": len(is_pnls),
            "oos_trade_count": oos_count,
            "is_stats": _compute_segment_stats(is_pnls, initial_balance),
            "oos_stats": _compute_segment_stats(oos_pnls, initial_balance),
            "stability": {},
            "metric_grades": {},
            "pass_count": 0,
            "total_metrics": 5,
            "grade": "INSUFFICIENT",
            "split_after_trade": split_idx,
        }

    is_stats = _compute_segment_stats(is_pnls, initial_balance)
    oos_stats = _compute_segment_stats(oos_pnls, initial_balance)

    metric_keys = ["return_pct", "win_rate", "profit_factor", "max_dd_pct", "sharpe"]
    stability = {}
    metric_grades = {}
    pass_count = 0

    for key in metric_keys:
        inverted = (key == "max_dd_pct")
        stab = _calc_stability(is_stats[key], oos_stats[key], inverted=inverted)
        stability[key] = stab

        if stab is None:
            grade = "FAIL"
        elif stab >= STABILITY_PASS:
            grade = "PASS"
            pass_count += 1
        elif stab >= STABILITY_WARN:
            grade = "WARN"
        else:
            grade = "FAIL"

        metric_grades[key] = grade

    if pass_count >= METRICS_TO_PASS:
        overall = "PASS"
    elif pass_count >= 3:
        overall = "WARN"
    else:
        overall = "FAIL"

    log.info(
        "OOS validation: %d IS trades / %d OOS trades, grade=%s",
        len(is_pnls), oos_count, overall,
    )

    return {
        "split_ratio": split_ratio,
        "is_trade_count": len(is_pnls),
        "oos_trade_count": oos_count,
        "is_stats": is_stats,
        "oos_stats": oos_stats,
        "stability": stability,
        "metric_grades": metric_grades,
        "pass_count": pass_count,
        "total_metrics": len(metric_keys),
        "grade": overall,
        "split_after_trade": split_idx,
    }


# ─── Helpers ──────────────────────────────────────────────────────────

def _extract_pnls(trades):
    """Extract PnL values from BTTrade objects or dicts."""
    pnls = []
    for t in trades:
        if isinstance(t, dict):
            pnls.append(float(t.get("pnl", 0.0)))
        else:
            pnls.append(float(getattr(t, "pnl", 0.0)))
    return pnls


def _compute_segment_stats(pnls, initial_balance):
    """
    Compute core metrics for a PnL segment.

    Why these 5 metrics: they cover profitability (return, profit_factor),
    consistency (win_rate, sharpe), and risk (max_dd). Together they give
    a balanced view without over-weighting any single dimension.
    """
    n = len(pnls)
    if n == 0:
        return {
            "return_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_dd_pct": 0.0,
            "sharpe": 0.0,
        }

    total_pnl = sum(pnls)
    return_pct = round(total_pnl / initial_balance * 100, 2)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = round(len(wins) / n * 100, 2)

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (
        999.99 if gross_profit > 0 else 0.0
    )

    max_dd_pct = round(_max_drawdown_pct(pnls, initial_balance), 2)

    sharpe = round(_sharpe_ratio(pnls), 2)

    return {
        "return_pct": return_pct,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe,
    }


def _max_drawdown_pct(pnls, initial_balance):
    """
    Max drawdown as % of running peak equity.

    Uses cumulative PnL from initial_balance, tracking peak and trough.
    Returns positive number (e.g. 12.5 means 12.5% drawdown).
    """
    equity = initial_balance
    peak = equity
    max_dd = 0.0

    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return max_dd


def _sharpe_ratio(pnls):
    """
    Annualised Sharpe ratio from per-trade PnL.

    Why per-trade (not daily): backtest trades don't map 1:1 to days.
    Annualisation uses sqrt(N) where N = trade count, which is a
    rough approximation — acceptable for IS/OOS comparison since both
    segments use the same formula.
    """
    n = len(pnls)
    if n < 2:
        return 0.0

    mean = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0

    if std == 0:
        return 0.0

    # Annualise: multiply by sqrt(trades_per_year_proxy)
    # Using sqrt(n) as a normalisation factor so the ratio scales
    # with sample size, making IS and OOS comparable.
    return mean / std * math.sqrt(n)


def _calc_stability(is_val, oos_val, inverted=False):
    """
    Stability = OOS / IS * 100.
    For inverted metrics (max_dd): stability = IS / OOS * 100
    (lower OOS DD = better stability).

    Returns None on sign flip or division by zero (treated as FAIL).
    """
    # Sign flip detection: IS positive but OOS negative (or vice versa)
    # For non-inverted metrics, a sign flip means OOS contradicts IS
    if not inverted:
        if (is_val > 0 and oos_val < 0) or (is_val < 0 and oos_val > 0):
            return None

    if inverted:
        # max_dd: both should be positive (drawdown as positive %)
        # Lower OOS DD = better → stability = IS_DD / OOS_DD * 100
        if oos_val == 0:
            # Zero OOS drawdown = perfect stability
            return 999.99
        if is_val == 0:
            # Zero IS drawdown but OOS has DD = unstable
            return 0.0
        return round(is_val / oos_val * 100, 2)
    else:
        if is_val == 0:
            # Can't compute ratio; treat as FAIL
            return 0.0 if oos_val == 0 else None
        return round(oos_val / is_val * 100, 2)
