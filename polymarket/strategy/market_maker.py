"""
market_maker.py — Strategy C: 兩邊買，hold to resolution（v3）

模仿 Anon + LampStore 嘅真實策略：
- 兩邊掛 maker limit bid near fair price
- 唔做任何管理（冇 unwind，冇 add_winner）
- Hold to resolution → winning side $1.00, losing side $0
- 3 收入來源：spread capture + maker rebate (20%) + liquidity rewards

真實數據驗證：
- Anon:      $16K / 2,241 markets / avg combined $0.979 / WR 79%
- LampStore: $115K / 19,504 markets / avg combined $0.970 / WR 69%
- 兩個都 94.5% maker orders，冇 SELL trades

參數來源：
- half_spread 2.5% → bid $0.475（Anon 買 $0.47-$0.49）
- rewardsMaxSpread 4.5¢ → $0.475 在 reward zone 內
- rewardsMinSize $50 → 需要 $5K+ bankroll for rewards
- CLOB minimum 5 shares → 需要 $450+ bankroll at 1%

用法：
    from polymarket.strategy.market_maker import (
        MMConfig, MMMarketState, PlannedOrder,
        compute_fair_up, plan_opening, apply_fill,
        resolve_market, should_enter_market,
    )
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from statistics import NormalDist

from ..core.context import PolyMarket

logger = logging.getLogger(__name__)

_norm = NormalDist()


# ═══════════════════════════════════════
#  Config
# ═══════════════════════════════════════

@dataclass
class MMConfig:
    """Strategy C parameters — from Anon/LampStore real data.

    half_spread 2.5%:
      bid = fair - 0.025 = $0.475 at open (fair=0.50)
      combined = $0.475 + $0.475 = $0.95 → 5% edge
      Anon real avg: $0.979 → 佢哋有時買到 $0.49（tighter）
      我哋 $0.475 更 conservative → 更大 edge 但 fill rate 可能低啲

    rewardsMaxSpread 4.5¢:
      Polymarket rewards zone = mid ± 4.5¢
      If mid = $0.50: zone = $0.455 - $0.545
      我哋 bid $0.475 → inside zone ✅
    """
    half_spread: float = 0.025      # 2.5%（Anon/LampStore range: 2-3%）

    bet_pct: float = 0.01           # bankroll × 1%
    max_concurrent_markets: int = 3
    min_liquidity: float = 100.0
    min_order_size: float = 5.0     # Polymarket CLOB minimum
    rewards_min_size: float = 50.0  # $50 for liquidity rewards

    # Circuit breaker
    max_consecutive_losses: int = 5
    cooldown_hours: int = 24


# ═══════════════════════════════════════
#  State
# ═══════════════════════════════════════

@dataclass
class PlannedOrder:
    """Order to submit. Runner converts to SDK call."""
    token_id: str
    side: str       # "BUY"
    price: float    # limit price
    size: float     # shares
    outcome: str    # "UP" or "DOWN"


@dataclass
class MMMarketState:
    """One 15M market window."""
    condition_id: str = ""
    title: str = ""
    up_token_id: str = ""
    down_token_id: str = ""
    window_start_ms: int = 0
    window_end_ms: int = 0
    btc_open_price: float = 0.0

    phase: str = "IDLE"  # IDLE → OPEN → RESOLVED

    up_shares: float = 0.0
    up_avg_price: float = 0.0
    down_shares: float = 0.0
    down_avg_price: float = 0.0

    entry_cost: float = 0.0
    payout: float = 0.0
    realized_pnl: float = 0.0

    @property
    def total_cost(self) -> float:
        return self.entry_cost

    @property
    def has_position(self) -> bool:
        return self.up_shares > 0 or self.down_shares > 0

    @property
    def combined_entry(self) -> float:
        if self.up_avg_price > 0 and self.down_avg_price > 0:
            return self.up_avg_price + self.down_avg_price
        return 0.0


# ═══════════════════════════════════════
#  Fair Price
# ═══════════════════════════════════════

def compute_fair_up(btc_current: float, btc_open: float,
                    vol_1m: float, minutes_remaining: int) -> float:
    """P(BTC close >= open) — Polymarket: flat = UP wins."""
    if minutes_remaining <= 0:
        return 0.995 if btc_current >= btc_open else 0.005

    if vol_1m <= 0 or btc_current <= 0 or btc_open <= 0:
        return 0.5

    sigma = vol_1m * math.sqrt(minutes_remaining)
    if sigma < 1e-10:
        return 0.995 if btc_current >= btc_open else 0.005

    d = math.log(btc_current / btc_open) / sigma
    return max(0.005, min(0.995, _norm.cdf(d)))


# ═══════════════════════════════════════
#  Plan Opening
# ═══════════════════════════════════════

def plan_opening(market: PolyMarket, fair_up: float,
                 config: MMConfig, bankroll: float = 0) -> list[PlannedOrder]:
    """Generate two maker limit bids, one each side.

    Entry logic:
      UP bid  = fair_up - half_spread
      DOWN bid = (1 - fair_up) - half_spread
      Combined < 1.0 → positive EV

    Sizing:
      shares = bankroll × bet_pct / combined
      Must ≥ min_order_size (5 shares)

    Returns [] if no edge or bankroll too small.
    """
    fair_down = 1.0 - fair_up
    up_price = round(max(0.01, fair_up - config.half_spread), 2)
    down_price = round(max(0.01, fair_down - config.half_spread), 2)

    combined = up_price + down_price
    if combined >= 1.0:
        return []

    # Sizing
    if bankroll > 0:
        max_cost = bankroll * config.bet_pct
    else:
        max_cost = 5.0  # safe fallback

    shares_per_side = max_cost / combined  # equal shares both sides

    # Clamp to min_order_size (5 shares) — small bankroll still trades at minimum
    # but check actual cost doesn't exceed 10% of bankroll (hard safety cap)
    if shares_per_side < config.min_order_size:
        min_cost = config.min_order_size * combined
        if bankroll > 0 and min_cost > bankroll * 0.10:
            logger.warning("skip %s: min order $%.2f > 10%% bankroll $%.0f",
                           market.condition_id[:8], min_cost, bankroll)
            return []
        shares_per_side = config.min_order_size
        logger.info("clamp %s to min %d shares (cost $%.2f, bankroll $%.0f)",
                    market.condition_id[:8], config.min_order_size,
                    shares_per_side * combined, bankroll)

    shares_per_side = round(shares_per_side, 2)

    # Log cost for transparency (no hard cap — user decides bet_pct)
    actual_cost = shares_per_side * (up_price + down_price)

    orders = [
        PlannedOrder(token_id=market.yes_token_id, side="BUY",
                     price=up_price, size=shares_per_side, outcome="UP"),
        PlannedOrder(token_id=market.no_token_id, side="BUY",
                     price=down_price, size=shares_per_side, outcome="DOWN"),
    ]

    logger.info("plan %s: UP@%.2f + DOWN@%.2f = %.3f | %d shares | $%.2f",
                market.condition_id[:8], up_price, down_price, combined,
                shares_per_side, actual_cost)
    return orders


# ═══════════════════════════════════════
#  Fill Processing
# ═══════════════════════════════════════

def apply_fill(state: MMMarketState, outcome: str, side: str,
               price: float, size: float) -> None:
    """Update state on fill. Runner calls this."""
    if size <= 0 or price <= 0:
        return

    if outcome == "UP" and side == "BUY":
        old = state.up_shares * state.up_avg_price
        state.up_shares += size
        state.up_avg_price = (old + size * price) / state.up_shares
        state.entry_cost += size * price
    elif outcome == "DOWN" and side == "BUY":
        old = state.down_shares * state.down_avg_price
        state.down_shares += size
        state.down_avg_price = (old + size * price) / state.down_shares
        state.entry_cost += size * price


# ═══════════════════════════════════════
#  Resolution
# ═══════════════════════════════════════

def resolve_market(state: MMMarketState, result: str) -> float:
    """Market resolved. Winning side = $1.00/share."""
    state.payout = state.up_shares if result == "UP" else state.down_shares
    state.realized_pnl = state.payout - state.total_cost
    state.phase = "RESOLVED"

    logger.info("resolved %s → %s | payout=$%.2f cost=$%.2f → PnL=$%.2f",
                state.condition_id[:8], result, state.payout,
                state.total_cost, state.realized_pnl)
    return state.realized_pnl


# ═══════════════════════════════════════
#  Pre-flight
# ═══════════════════════════════════════

def should_enter_market(market: PolyMarket, config: MMConfig) -> bool:
    """BTC 15M only."""
    if market.category != "crypto_15m":
        return False
    if market.liquidity < config.min_liquidity:
        return False
    t = market.title.lower()
    return "bitcoin" in t and "up or down" in t
