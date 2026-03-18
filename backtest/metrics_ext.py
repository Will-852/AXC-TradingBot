"""
metrics_ext.py — extend_summary() wrapper for engine.run() output.

設計決定：wrapper pattern，唔改 engine.py，
新指標喺呢度加，唔影響核心引擎。
"""

import logging

log = logging.getLogger(__name__)


def extend_summary(result: dict) -> dict:
    """Extend engine.run() output with additional computed metrics.
    Only adds new keys — never overwrites existing ones."""

    trades = result.get("trades", [])
    if not trades:
        return result

    kelly = _calc_kelly(trades)
    cagr = _calc_cagr(result)

    result.update({
        "kelly_pct": kelly,
        "cagr_pct": cagr,
    })

    # Derive initial balance: final_balance - total PnL
    _total_pnl = sum(t.pnl for t in trades)
    _init_bal = result.get("final_balance", 10000) - _total_pnl
    if _init_bal <= 0:
        _init_bal = 10000.0  # fallback if calculation yields nonsense

    # Monte Carlo robustness test
    try:
        from backtest.monte_carlo import run_monte_carlo
        mc = run_monte_carlo(trades, initial_balance=_init_bal)
        result["monte_carlo"] = mc
    except Exception as e:
        log.warning("Monte Carlo failed: %s", e)
        result["monte_carlo"] = None

    # Out-of-sample validation
    try:
        from backtest.oos_validation import run_oos_validation
        oos = run_oos_validation(trades, initial_balance=_init_bal)
        result["oos_validation"] = oos
    except Exception as e:
        log.warning("OOS validation failed: %s", e)
        result["oos_validation"] = None

    return result


def _calc_kelly(trades) -> float:
    """Kelly Criterion: kelly = win_rate - (1 - win_rate) / payoff_ratio.
    Returns percentage (e.g. 15.2 = suggest 15.2% risk per trade)."""
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    if not wins or not losses:
        return 0.0

    win_rate = len(wins) / len(trades)
    avg_win = sum(t.pnl for t in wins) / len(wins)
    avg_loss = abs(sum(t.pnl for t in losses) / len(losses))

    if avg_loss == 0:
        return 0.0

    payoff_ratio = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / payoff_ratio
    return round(kelly * 100, 2)


def _calc_cagr(result: dict) -> float:
    """CAGR = (final/initial)^(365/days) - 1.
    Uses equity_curve time span for day count."""
    eq = result.get("equity_curve", [])
    if len(eq) < 2:
        return 0.0

    initial = eq[0]["equity"]
    final = eq[-1]["equity"]

    if initial <= 0:
        return 0.0

    # Approximate days from hourly candle count
    days = len(eq) / 24
    if days < 1:
        return 0.0

    ratio = final / initial
    if ratio <= 0:
        return 0.0

    cagr = (ratio ** (365 / days)) - 1
    return round(cagr * 100, 2)
