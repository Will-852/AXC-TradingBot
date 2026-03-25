"""
Mode A: MAKER_ARB — both-sides maker, pure arb (no directional edge).

Buy both Up and Down via MAKER limit orders at equal shares (R=1.0).
Combined target < $0.98 = guaranteed profit regardless of direction.

⚠️ RISK: at $5 budget + min 5 shares, lean ratio is always 5:5 = 1:1.
Directional signal is WASTED in this mode. Edge comes purely from
combined < $1.00, not from direction (BMD proven, 2026-03-25).

5M spreads wider than 15M (Uncommon-Oat: 5M combined 0.80-0.98).
This mode captures that wider spread at low momentum (8-15bps).
"""

from dataclasses import dataclass
from typing import Optional

from ..signal import Signal


@dataclass
class MakerArbOrder:
    lean_side: str          # "UP" or "DOWN"
    lean_price: float       # Limit price for lean side
    lean_shares: int        # Number of shares
    hedge_side: str         # Opposite of lean
    hedge_price: float      # Limit price for hedge side
    hedge_shares: int       # Number of shares
    combined_cost: float    # lean_price + hedge_price (per matched pair)
    expected_arb_pnl: float # (1.00 - combined) × matched_shares


def plan_maker_arb(
    signal: Signal,
    poly_mid_up: float,
    poly_mid_down: float,
    bet_size_usd: float = 5.0,
    spread_from_mid: float = 0.025,
    max_combined: float = 0.98,    # ⚠️ RISK: must match config ARB_MAX_COMBINED
    max_lean_price: float = 0.55,  # ⚠️ RISK: must match config ARB_MAX_PRICE
    max_hedge_price: float = 0.50, # ⚠️ RISK: must match config HEDGE_MAX_PRICE
    min_shares: int = 5,
) -> Optional[MakerArbOrder]:
    """
    Plan maker arb orders based on signal and Polymarket mid prices.

    Returns None if order would violate constraints.
    Does NOT execute. Pure planning.
    """
    lean_side = signal.direction
    hedge_side = "DOWN" if lean_side == "UP" else "UP"

    # Compute bid prices (below mid)
    if lean_side == "UP":
        lean_price = round(poly_mid_up - spread_from_mid, 2)
        hedge_price = round(poly_mid_down - spread_from_mid, 2)
    else:
        lean_price = round(poly_mid_down - spread_from_mid, 2)
        hedge_price = round(poly_mid_up - spread_from_mid, 2)

    # Clamp to max prices
    lean_price = min(lean_price, max_lean_price)
    hedge_price = min(hedge_price, max_hedge_price)

    # Floor at $0.01
    lean_price = max(lean_price, 0.01)
    hedge_price = max(hedge_price, 0.01)

    combined = lean_price + hedge_price
    if combined > max_combined:
        return None  # Too expensive

    # Size allocation with lean ratio
    R = signal.lean_ratio
    if R <= 0 or R != R or R == float("inf"):  # guard: nan, inf, negative
        R = 1.0
    total_budget = bet_size_usd

    lean_budget = total_budget * R / (R + 1)
    hedge_budget = total_budget / (R + 1)

    lean_shares = max(min_shares, int(lean_budget / lean_price))
    hedge_shares = max(min_shares, int(hedge_budget / hedge_price))

    matched = min(lean_shares, hedge_shares)
    arb_pnl = matched * (1.00 - combined)

    return MakerArbOrder(
        lean_side=lean_side,
        lean_price=lean_price,
        lean_shares=lean_shares,
        hedge_side=hedge_side,
        hedge_price=hedge_price,
        hedge_shares=hedge_shares,
        combined_cost=round(combined, 4),
        expected_arb_pnl=round(arb_pnl, 4),
    )
