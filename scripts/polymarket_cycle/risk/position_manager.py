"""
position_manager.py — Monitor existing positions for exit triggers

Checks:
1. Probability drift — market moved significantly since entry
2. Approaching resolution — market about to close
3. Profit taking — position in profit beyond threshold
4. Loss cutting — position in loss beyond threshold
5. Resolution — market has resolved, record outcome

唔直接執行 exit（Phase 5），只標記需要 review 嘅 positions。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..config.settings import (
    EXIT_PROBABILITY_DRIFT,
    PROFIT_TAKE_PCT,
    LOSS_CUT_PCT,
    MIN_DAYS_TO_RESOLUTION,
)
from ..core.context import PolyPosition

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Exit recommendation for a position."""
    position: PolyPosition
    action: str = ""          # "exit" / "reduce" / "monitor"
    urgency: str = "low"      # "high" / "medium" / "low"
    reasons: list[str] = field(default_factory=list)

    @property
    def should_exit(self) -> bool:
        return self.action == "exit"


def evaluate_positions(
    positions: list[PolyPosition],
    now: datetime | None = None,
    verbose: bool = False,
) -> list[ExitSignal]:
    """Evaluate all positions for exit triggers.

    Returns list of ExitSignal — one per position that needs attention.
    Positions with no triggers are NOT included.
    """
    if now is None:
        now = datetime.now()

    signals = []
    for pos in positions:
        exit_signal = _evaluate_single(pos, now)
        if exit_signal.reasons:
            signals.append(exit_signal)
            if verbose:
                logger.info(
                    "Position %s: %s (%s) — %s",
                    pos.title[:30], exit_signal.action,
                    exit_signal.urgency, "; ".join(exit_signal.reasons),
                )

    return signals


def _evaluate_single(pos: PolyPosition, now: datetime) -> ExitSignal:
    """Evaluate a single position."""
    signal = ExitSignal(position=pos)

    # ─── 1. Probability Drift ───
    # probability_drift = current_price - avg_price
    # For YES: current_price = yes_price, positive drift = good
    # For NO: current_price = no_price, positive drift = good (NO token gained value)
    # Both sides: negative drift = price dropped = against us
    drift = pos.probability_drift
    drift_against = False

    if drift < -EXIT_PROBABILITY_DRIFT:
        drift_against = True

    if drift_against:
        signal.reasons.append(f"Probability drift {abs(drift):.1%} against us")
        signal.urgency = "medium"

    # ─── 2. Approaching Resolution ───
    if pos.end_date:
        try:
            end = datetime.strptime(pos.end_date, "%Y-%m-%d")
            days_left = (end - now).days
            if days_left < 0:
                signal.reasons.append("Market resolved")
                signal.urgency = "high"
                signal.action = "exit"
                return signal  # resolved → always exit
            elif days_left < MIN_DAYS_TO_RESOLUTION:
                signal.reasons.append(f"Expiry in {days_left}d")
                signal.urgency = "medium"
        except (ValueError, TypeError):
            pass

    # ─── 3. Profit Taking ───
    if pos.unrealized_pnl_pct > PROFIT_TAKE_PCT:
        signal.reasons.append(f"Profit {pos.unrealized_pnl_pct:.1%} > {PROFIT_TAKE_PCT:.1%}")
        signal.urgency = "low"

    # ─── 4. Loss Cutting ───
    if pos.unrealized_pnl_pct < -LOSS_CUT_PCT:
        signal.reasons.append(f"Loss {pos.unrealized_pnl_pct:.1%} > {-LOSS_CUT_PCT:.1%}")
        signal.urgency = "high"

    # ─── Determine Action ───
    if not signal.reasons:
        signal.action = ""  # no action needed
    elif signal.urgency == "high":
        signal.action = "exit"
    elif drift_against and pos.unrealized_pnl_pct < 0:
        # Drift against us AND losing → exit
        signal.action = "exit"
    elif len(signal.reasons) >= 2:
        # Multiple triggers → exit
        signal.action = "exit"
    else:
        signal.action = "monitor"

    return signal
