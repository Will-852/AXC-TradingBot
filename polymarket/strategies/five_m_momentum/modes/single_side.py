"""
Mode B: TAKER_DIRECTIONAL — aggressive limit lean + optional maker hedge.

For strong momentum signals (>= 15bps). Uses aggressive GTC limit (NOT FOK)
at ask + buffer to guarantee fill.

Data basis:
  T+45s 15bps = 83.2% WR (backtest), lean accuracy 58.1% (12h live)
  Break-even single-side at $0.55 fill = ~55.8% WR (with 1.53% fee)
  58.1% > 55.8% → marginal positive edge

⚠️ RISK: 12h live trade WR = 35.1% (maker mode). Taker mode should give
higher WR (~58%) because lean fills guaranteed, but UNVERIFIED.
"""

from dataclasses import dataclass
from typing import Optional

from ..momentum_signal import Signal


@dataclass
class TakerDirectionalOrder:
    lean_side: str         # "UP" or "DOWN"
    lean_price: float      # Aggressive GTC limit price (ask + buffer)
    lean_shares: int       # Number of shares
    expected_ev: float     # EV estimate (for logging only)


def plan_taker_directional(
    signal: Signal,
    poly_ask: float,
    bet_size_usd: float = 5.0,
    ask_buffer: float = 0.02,
    ask_cap: float = 0.55,
    min_shares: int = 5,
    estimated_wr: float = 0.76,  # ⚠️ RISK: backtest WR, live may differ. 12h lean acc = 58%.
) -> Optional[TakerDirectionalOrder]:
    """
    Plan aggressive GTC limit order on lean side.

    NOT FOK — FOK fails on thin books (SDK fetches stale book).
    GTC at ask + buffer stays on book if not instant fill.

    Returns None if ask > cap (CLOB already repriced, no edge).
    """
    if poly_ask > ask_cap:
        return None

    # Aggressive limit: ask + buffer, capped
    # ⚠️ RISK: if ask + buffer > ask_cap, clamp to ask_cap
    price = min(round(poly_ask + ask_buffer, 2), ask_cap)

    shares = max(min_shares, int(bet_size_usd / price))

    # EV estimate (for logging — NOT used for trade decision)
    win_pnl = shares * (1.00 - price)
    lose_pnl = shares * price
    ev = estimated_wr * win_pnl - (1 - estimated_wr) * lose_pnl

    return TakerDirectionalOrder(
        lean_side=signal.direction,
        lean_price=price,
        lean_shares=shares,
        expected_ev=round(ev, 4),
    )


@dataclass
class HedgeOrder:
    side: str              # Opposite of lean
    price: float           # Maker limit price (below mid)
    shares: int


def plan_hedge(
    signal: Signal,
    poly_mid_hedge: float,
    spread_from_mid: float = 0.025,
    max_price: float = 0.50,
    min_shares: int = 5,
) -> Optional[HedgeOrder]:
    """
    Plan optional maker hedge on opposite side.

    Placed AFTER lean fill confirmed. May or may not fill.
    If fills: combined < $1.00 → arb bonus.
    If doesn't fill: pure single-side directional bet.

    Returns None if price > max_price.
    """
    hedge_side = "DOWN" if signal.direction == "UP" else "UP"
    raw_price = round(poly_mid_hedge - spread_from_mid, 2)

    # Return None if raw price exceeds max (don't clamp nonsensical prices)
    if raw_price > max_price or raw_price <= 0.01:
        return None

    price = raw_price

    return HedgeOrder(
        side=hedge_side,
        price=price,
        shares=min_shares,
    )
