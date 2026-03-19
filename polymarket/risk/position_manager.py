"""
position_manager.py — Monitor existing positions for exit triggers

Binary prediction markets (crypto_15m): asymmetric SL=9% + hold winners.
hybrid_backtest 證明 asymmetric SL 大幅優於 HOLD 同 symmetric exit：
  - HOLD:          +$91, Sharpe 0.244, DD 6.9%
  - Symmetric 25%: +$69, Sharpe 0.228, DD 6.7%
  - SL=9% (ours):  +$150, Sharpe 0.457, DD 3.4% ★

核心邏輯：cut losers fast (9% SL at 5m+10m), let winners ride to resolution.
W/L ratio = 2.27, Kelly = 33.2%.

長期市場（weather、general crypto）用 drift + loss cut。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..config.settings import (
    BINARY_SL_PCT,
    EXIT_PROBABILITY_DRIFT,
    PROFIT_TAKE_PCT,
    LOSS_CUT_PCT,
    MIN_DAYS_TO_RESOLUTION,
    TAKE_PROFIT_TOKEN_PRICE,
)
from ..core.context import PolyPosition

logger = logging.getLogger(__name__)

# Binary markets: asymmetric SL=9% at checkpoints, hold winners to resolution
# hybrid_backtest: SL9% +$150 / Sharpe 0.457 vs HOLD +$91 / Sharpe 0.244
_BINARY_SL_CATEGORIES = {"crypto_15m"}


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
    """Evaluate a single position.

    Binary markets (crypto_15m): asymmetric SL=9% + hold winners.
    其他市場: drift + loss cut（threshold 較寬鬆）。
    """
    signal = ExitSignal(position=pos)
    is_binary_sl = pos.category in _BINARY_SL_CATEGORIES

    # ─── 1. Resolution Check（全部 category）───
    # Try datetime-level parse first (15M markets have intra-day end times),
    # fall back to date-level for longer markets.
    if pos.end_date:
        try:
            # Try ISO datetime first (e.g., "2026-03-19T16:50:00Z")
            try:
                end = datetime.fromisoformat(pos.end_date.replace("Z", "+00:00"))
                # Make now timezone-aware if end is
                now_aware = now if now.tzinfo else now.replace(tzinfo=end.tzinfo)
                time_left = end - now_aware
                if time_left.total_seconds() < 0:
                    signal.reasons.append("Market resolved")
                    signal.urgency = "high"
                    signal.action = "exit"
                    return signal
            except (ValueError, AttributeError):
                # Fall back to date-only (e.g., "2026-03-19")
                end = datetime.strptime(pos.end_date[:10], "%Y-%m-%d")
                days_left = (end - now).days
                if days_left < 0:
                    signal.reasons.append("Market resolved")
                    signal.urgency = "high"
                    signal.action = "exit"
                    return signal
                elif not is_binary_sl and days_left < MIN_DAYS_TO_RESOLUTION:
                    signal.reasons.append(f"Expiry in {days_left}d")
                    signal.urgency = "medium"
        except (ValueError, TypeError):
            pass

    # ─── 2. Token Price Take Profit（全部 category）───
    current_token_price = pos.current_price if pos.current_price > 0 else 0.0
    if current_token_price >= TAKE_PROFIT_TOKEN_PRICE:
        signal.reasons.append(
            f"Token price {current_token_price:.2f} ≥ {TAKE_PROFIT_TOKEN_PRICE:.2f} — lock profit"
        )
        signal.urgency = "high"
        signal.action = "exit"
        return signal

    # ─── 3. Binary SL=9% (crypto_15m) ───
    # Asymmetric: cut losers fast, let winners ride to resolution.
    # hybrid_backtest: SL9% PnL +$150 vs HOLD +$91 (Sharpe 0.457 vs 0.244)
    if is_binary_sl:
        if pos.unrealized_pnl_pct < -BINARY_SL_PCT:
            signal.reasons.append(
                f"Binary SL: unrealized {pos.unrealized_pnl_pct:.1%} < -{BINARY_SL_PCT:.0%}"
            )
            signal.urgency = "high"
            signal.action = "exit"
        else:
            signal.action = ""
        return signal

    # ─── 3. Weather: NO EXIT — hold to resolution ───
    # Weather = binary, resolves in 1-3 days, $1.42 per bet (1% bankroll).
    # Selling on thin books ($200-$1500 liquidity) costs more than it saves.
    # ColdMath ($77K PnL) holds $70K portfolio = he holds everything.
    # Token ≥93% still applies above (step 2), but that's all.
    if pos.category == "weather":
        return signal  # no exit evaluation — hold to resolution

    # ══════════════════════════════════════════════
    # 以下只適用於長期市場（weather, general crypto 等）
    # ══════════════════════════════════════════════

    # ─── 4. Probability Drift ───
    drift = pos.probability_drift
    drift_against = False
    if drift < -EXIT_PROBABILITY_DRIFT:
        drift_against = True
        signal.reasons.append(f"Probability drift {abs(drift):.1%} against us")
        signal.urgency = "medium"

    # ─── 4. Profit Taking (PnL %) ───
    if pos.unrealized_pnl_pct > PROFIT_TAKE_PCT:
        signal.reasons.append(f"Profit {pos.unrealized_pnl_pct:.1%} > {PROFIT_TAKE_PCT:.1%}")
        signal.urgency = "low"

    # ─── 5. Loss Cutting ───
    if pos.unrealized_pnl_pct < -LOSS_CUT_PCT:
        signal.reasons.append(f"Loss {pos.unrealized_pnl_pct:.1%} > {-LOSS_CUT_PCT:.1%}")
        signal.urgency = "high"

    # ─── Determine Action ───
    if not signal.reasons:
        signal.action = ""
    elif signal.urgency == "high":
        signal.action = "exit"
    elif drift_against and pos.unrealized_pnl_pct < -LOSS_CUT_PCT * 0.5:
        # Drift against + significant loss → exit
        signal.action = "exit"
    elif len(signal.reasons) >= 2:
        signal.action = "exit"
    else:
        signal.action = "monitor"

    return signal
