"""
5M Momentum Signal — Pure signal computation. No execution logic.

Three-tier mode selection based on momentum magnitude:
  < 8bps  → SKIP
  8-15bps → MAKER_ARB (both sides, earn spread)
  >= 15bps → TAKER_DIRECTIONAL (aggressive lean + optional hedge)

Data basis:
  T+45s WR: 8bps=76%(n=1432), 15bps=83.2%(n=340)
  12h live: lean accuracy 58.1%, trade WR 35.1% (maker paradox)
  5M σ ≈ 0.16% = 16bps. So 8bps ≈ 0.5σ, 15bps ≈ 1σ.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Mode(Enum):
    SKIP = "skip"
    MAKER_ARB = "maker_arb"               # 8-15bps: both-sides maker, earn spread
    TAKER_DIRECTIONAL = "taker_directional"  # >15bps: aggressive lean + optional hedge


@dataclass
class Signal:
    momentum_bps: float       # BTC return since window open, in bps
    direction: str            # "UP" or "DOWN" or "FLAT"
    mode: Mode                # Which execution mode to use
    confidence: float         # 0.0 - 1.0 (momentum-based, AXC indicators can boost)
    lean_ratio: float         # ⚠️ RISK: used by maker_arb.py for sizing. 1.0 = equal sides.
    raw_prices: dict          # {open, current, delay_s} for audit trail


def compute_signal(
    open_price: float,
    current_price: float,
    delay_s: int,
    skip_below_bps: float = 8.0,
    arb_upper_bps: float = 15.0,
) -> Signal:
    """
    Pure function. Given open and current BTC price, compute signal.

    Returns Signal with mode and direction.
    No side effects. No API calls. Easy to test.

    Mode selection:
      |momentum| < skip_below_bps          → SKIP
      skip_below_bps ≤ |momentum| < arb_upper_bps → MAKER_ARB
      |momentum| ≥ arb_upper_bps           → TAKER_DIRECTIONAL
    """
    if open_price <= 0:
        return Signal(
            momentum_bps=0, direction="FLAT", mode=Mode.SKIP,
            confidence=0, lean_ratio=1.0,
            raw_prices={"open": 0, "current": current_price, "delay_s": delay_s},
        )

    momentum_bps = (current_price - open_price) / open_price * 10000
    abs_mom = abs(momentum_bps)

    # Direction
    if momentum_bps > 0:
        direction = "UP"
    elif momentum_bps < 0:
        direction = "DOWN"
    else:
        direction = "FLAT"

    # Mode selection — three tiers
    # ⚠️ RISK: float precision at boundaries. 8.0bps can compute as 7.9999 → SKIP.
    # Real BTC prices never land exactly on boundary, so this is cosmetic.
    if direction == "FLAT" or abs_mom < skip_below_bps:
        mode = Mode.SKIP
        confidence = 0.0
        lean_ratio = 1.0
    elif abs_mom < arb_upper_bps:
        mode = Mode.MAKER_ARB
        # Confidence 0.3-0.6 for arb range (8-15bps)
        confidence = 0.3 + (abs_mom - skip_below_bps) / (arb_upper_bps - skip_below_bps) * 0.3
        lean_ratio = 1.0  # Arb = equal both sides (5+5, direction irrelevant)
    else:
        mode = Mode.TAKER_DIRECTIONAL
        # Confidence 0.6-1.0 for directional (15bps+)
        confidence = min(1.0, 0.6 + (abs_mom - arb_upper_bps) / 30.0)
        lean_ratio = float("inf")  # Single-side = infinite lean

    return Signal(
        momentum_bps=round(momentum_bps, 2),
        direction=direction,
        mode=mode,
        confidence=round(confidence, 3),
        lean_ratio=lean_ratio,  # ⚠️ RISK: inf serializes to Infinity in JSON
        raw_prices={
            "open": open_price,
            "current": current_price,
            "delay_s": delay_s,
        },
    )


# ---------------------------------------------------------------------------
# Future: AXC indicator integration point
# ---------------------------------------------------------------------------

def enhance_signal_with_indicators(
    signal: Signal,
    cvd_buy_ratio: Optional[float] = None,
    ob_imbalance: Optional[float] = None,
    vol_regime: Optional[str] = None,
) -> Signal:
    """
    Placeholder for AXC indicator integration.

    When ready:
    - CVD buy ratio > 0.6 + momentum agrees → boost confidence +0.1
    - OB imbalance |imb| > 0.2 agrees → boost confidence +0.05
    - Vol regime = "low" → downgrade mode one tier
      (TAKER_DIRECTIONAL → MAKER_ARB, MAKER_ARB → SKIP)

    For now: pass-through. Returns signal unchanged.
    ⚠️ RISK: must clamp confidence to [0, 1] after boosting. compute_signal
    already returns up to 1.0, so any boost without clamp → confidence > 1.0.
    """
    return signal
