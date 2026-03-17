"""
binary_kelly.py — Binary outcome Kelly Criterion for prediction markets

同 trader_cycle/risk/kelly.py 唔同：
- Perp Kelly: win/loss distribution from trade history, payoff ratio varies
- Binary Kelly: known payout structure (win = 1/price, lose = 0), estimated probability

Binary Kelly formula:
  p = estimated probability of winning
  b = net odds = (1/price) - 1   (e.g., buy at 0.40 → b = 1.5)
  q = 1 - p
  f* = (p × b - q) / b           (optimal fraction of bankroll to bet)

Half Kelly: f = f* × 0.5 (standard practice — 75% of growth, 50% of variance)

Confidence adjustment: scale Kelly fraction by AI confidence score.
Lower confidence → bet less, even if edge looks large.
"""

import logging

from ..config.settings import (
    KELLY_FRACTION,
    KELLY_MIN_BET_USDC,
    KELLY_MAX_BET_USDC,
    MAX_PER_MARKET,
    MAX_TOTAL_EXPOSURE,
    MAX_PER_CATEGORY,
)
from ..core.context import PolySignal, PolyPosition

logger = logging.getLogger(__name__)


def _gto_kelly_scale(signal: PolySignal) -> float:
    """Scale Kelly fraction by GTO unexploitability [0.3, 1.0].

    Dominant strategy → full Kelly (1.0).
    Otherwise: max(0.30, unexploitability_score).
    Disabled if GTO_KELLY_SCALE_ENABLED is False.
    """
    from ..config.settings import GTO_KELLY_SCALE_ENABLED
    if not GTO_KELLY_SCALE_ENABLED:
        return 1.0

    # Dominant strategies get full Kelly — always profitable in expectation
    if getattr(signal, "is_dominant_strategy", False):
        return 1.0

    unexploit = getattr(signal, "unexploitability_score", 0.0)
    if unexploit <= 0:
        return 0.50  # no GTO data → conservative default (half scale)

    return max(0.30, min(1.0, unexploit))


def compute_kelly_bet(
    signal: PolySignal,
    bankroll: float,
    total_exposure: float = 0.0,
    category_exposure: float = 0.0,
    kelly_fraction: float = KELLY_FRACTION,
) -> float:
    """Compute Kelly-optimal bet size in USDC.

    Args:
        signal: Trading signal with price and edge
        bankroll: Total USDC balance
        total_exposure: Current total exposure across all markets
        category_exposure: Current exposure in signal's category
        kelly_fraction: Kelly multiplier (0.5 = half Kelly)

    Returns:
        Bet size in USDC (0 if no bet should be placed)
    """
    if bankroll <= 0 or signal.price <= 0 or signal.price >= 1.0:
        return 0.0

    # Estimated true probability
    if signal.side == "YES":
        p = signal.price + signal.edge
    else:
        # For NO side, edge is the mispricing on the NO token
        # NO price = 1 - yes_price, our edge shifts it
        p = (1.0 - signal.price) + signal.edge

    p = max(0.01, min(0.99, p))

    # Payout odds: buy at price, win pays $1 per share
    # signal.price is already the correct token price (yes_price or no_price)
    buy_price = max(0.01, min(0.99, signal.price))
    b = (1.0 / buy_price) - 1.0  # net odds

    if b <= 0:
        logger.debug("Kelly: non-positive odds b=%.4f for %s", b, signal.title[:30])
        return 0.0

    # Raw Kelly fraction
    q = 1.0 - p
    f_star = (p * b - q) / b

    if f_star <= 0:
        logger.debug(
            "Kelly: no edge f*=%.4f (p=%.3f, b=%.2f) for %s",
            f_star, p, b, signal.title[:30],
        )
        return 0.0

    # Apply Kelly multiplier (half Kelly)
    f = f_star * kelly_fraction

    # Confidence adjustment: scale bet by AI confidence
    # Low confidence (0.5) → bet only 50% of Kelly amount
    confidence_factor = max(0.3, min(1.0, signal.confidence))
    f *= confidence_factor

    # GTO adjustment: scale by unexploitability (if enabled)
    gto_scale = _gto_kelly_scale(signal)
    f *= gto_scale

    # Convert to USDC
    bet = f * bankroll

    # ─── Exposure Limits ───
    max_available = bankroll * MAX_TOTAL_EXPOSURE - total_exposure
    if max_available <= 0:
        return 0.0

    max_per_market = bankroll * MAX_PER_MARKET
    max_per_cat = bankroll * MAX_PER_CATEGORY - category_exposure

    bet = min(bet, max_available, max_per_market, max_per_cat)

    # Clamp to absolute min/max
    bet = max(KELLY_MIN_BET_USDC, min(KELLY_MAX_BET_USDC, bet))

    # Final check: don't bet more than available
    if bet > max_available:
        bet = max_available
    if bet < KELLY_MIN_BET_USDC:
        return 0.0  # too small to bother

    logger.info(
        "Kelly: %s %s p=%.3f b=%.2f f*=%.4f half=%.4f conf=%.2f gto=%.2f → $%.2f",
        signal.side, signal.title[:30], p, b, f_star, f, signal.confidence, gto_scale, bet,
    )
    return round(bet, 2)


def size_signals(
    signals: list[PolySignal],
    bankroll: float,
    positions: list[PolyPosition],
    kelly_fraction: float = KELLY_FRACTION,
) -> list[PolySignal]:
    """Size all signals using Kelly criterion.

    Modifies signals in place (sets bet_size_usdc and kelly_fraction).
    Respects aggregate exposure limits.
    """
    # Current exposure
    total_exposure = sum(p.cost_basis for p in positions)

    # Category exposure
    cat_exposure: dict[str, float] = {}
    for p in positions:
        cat_exposure[p.category] = cat_exposure.get(p.category, 0) + p.cost_basis

    for signal in signals:
        cat_exp = cat_exposure.get(signal.category, 0.0)

        bet = compute_kelly_bet(
            signal=signal,
            bankroll=bankroll,
            total_exposure=total_exposure,
            category_exposure=cat_exp,
            kelly_fraction=kelly_fraction,
        )

        # Category-specific cap (15M fast markets → smaller bets)
        if signal.category == "crypto_15m":
            from ..config.settings import CRYPTO_15M_MAX_BET_USDC
            bet = min(bet, CRYPTO_15M_MAX_BET_USDC)

        signal.bet_size_usdc = bet
        signal.kelly_fraction = bet / bankroll if bankroll > 0 else 0.0

        # Update running exposure
        if bet > 0:
            total_exposure += bet
            cat_exposure[signal.category] = cat_exp + bet

    return signals
