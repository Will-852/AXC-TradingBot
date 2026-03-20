"""
market_maker.py — v4 Dual-Layer Strategy

Dual-layer: hedge (guaranteed) + directional (EV play)。
- Layer 1 HEDGE: equal shares UP + DN, combined < $1 → guaranteed if both fill
- Layer 2 DIRECTIONAL: naked shares on likely side, 68% accuracy → +EV
- Zone 1 (0.50-0.57): pure hedge
- Zone 2 (0.57-0.65): 50% hedge + 50% directional
- Zone 3 (>0.65): 25% hedge + 75% directional
- 10% bankroll hard cap per market
- CLOB minimum 5 shares per order

Signal priority (15M timeframe):
1. Order book imbalance + CVD + M1 momentum (short-term)
2. RSI/MACD/BB etc (background context, lower weight)

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
    """v4 Dual-Layer parameters — from Anon/LampStore real data.

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

    # Dynamic pricing caps
    max_hedge_bid: float = 0.475    # hedge: both sides, combined < $1
    max_directional_bid: float = 0.40  # directional: lower = better win/loss ratio (1.5x)
    min_bid: float = 0.25           # floor — below this, market strongly disagrees

    # Phased entry: split budget across multiple orders over time
    # Tranche count adapts to bankroll (more money = more splits)
    max_tranches: int = 4        # max splits per market
    tranche_interval_s: int = 30 # seconds between tranches


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
                    vol_1m: float, minutes_remaining: int,
                    indicator_p_up: float = 0.0) -> float:
    """P(BTC close >= open) — blended: Brownian Bridge + indicator score.

    If indicator_p_up > 0 (from crypto_15m._score_direction), blend it
    with the Brownian Bridge estimate. Indicator weight increases as
    minutes_remaining decreases (more data = more confident).

    Weights:
      T=15 min left: 80% bridge, 20% indicator (little data)
      T=5  min left: 50% bridge, 50% indicator
      T=1  min left: 30% bridge, 70% indicator (price almost decided)
    """
    if minutes_remaining <= 0:
        return 0.995 if btc_current >= btc_open else 0.005

    if vol_1m <= 0 or btc_current <= 0 or btc_open <= 0:
        return indicator_p_up if indicator_p_up > 0 else 0.5

    sigma = vol_1m * math.sqrt(minutes_remaining)
    if sigma < 1e-10:
        return 0.995 if btc_current >= btc_open else 0.005

    d = math.log(btc_current / btc_open) / sigma
    bridge = max(0.005, min(0.995, _norm.cdf(d)))

    # Blend with indicator score if available
    if indicator_p_up > 0:
        # Indicator weight: max 30%. Bridge is near-deterministic at T≤2min.
        ind_weight = max(0.10, min(0.30, 0.30 - minutes_remaining / 50.0))
        fair = bridge * (1 - ind_weight) + indicator_p_up * ind_weight
        return max(0.005, min(0.995, fair))

    return bridge


# ═══════════════════════════════════════
#  Plan Opening
# ═══════════════════════════════════════

def calc_tranches(bankroll: float, config: MMConfig) -> int:
    """How many tranches can we split the 5% market budget into?
    Each tranche must afford at least min_order_size shares.

    5% cap per market:
    $55  → 1 tranche  ($2.75)
    $110 → 2 tranches ($2.75 each)
    $165 → 3 tranches
    $220 → 4 tranches (max)
    """
    total = min(bankroll * config.bet_pct, bankroll * 0.05)
    min_per_tranche = config.min_order_size * 0.55  # ~$2.75 at typical bid
    if total <= 0 or min_per_tranche <= 0:
        return 1
    n = int(total / min_per_tranche)
    return max(1, min(n, config.max_tranches))


def plan_opening(market: PolyMarket, fair_up: float,
                 config: MMConfig, bankroll: float = 0,
                 tranche: int = 0, total_tranches: int = 1,
                 risk_mode: str = "NORMAL") -> list[PlannedOrder]:
    """Dual-layer: hedge (guaranteed) + directional (EV play).

    Layer 1 — HEDGE: Equal shares UP + DN at informed prices.
      Combined < $1 → guaranteed profit if both fill.
      Needs 5 shares each side → minimum ~$4.75 budget.

    Layer 2 — DIRECTIONAL: Extra naked shares on likely side.
      Bid at fair - spread (same pricing as hedge side).
      Only when fair outside 0.50-0.57.

    Zones:
      0.43-0.50: skip (no edge either direction)
      0.50-0.57: Zone 1 — pure hedge (if bankroll allows) or skip
      0.57-0.65: Zone 2 — 50% hedge + 50% directional
      >0.65:     Zone 3 — 25% hedge + 75% directional

    Bankroll gates (CLOB 5-share minimum per order):
      < $48: hedge impossible → directional only (Zone 2/3) or skip (Zone 1)
      $48+:  full dual-layer

    Returns [] if no edge or bankroll too small.
    """
    ZONE_1_BOUND = 0.57   # below: pure hedge (or skip)
    ZONE_2_BOUND = 0.65   # above: strong directional

    fair_down = 1.0 - fair_up
    confidence = max(fair_up, fair_down)

    # No edge zone
    if confidence < 0.50:
        return []

    # Total budget: 5% hard cap per market (2 markets × 5% = 10% max exposure)
    if bankroll > 0:
        full_budget = min(bankroll * config.bet_pct, bankroll * 0.05)
    else:
        full_budget = 5.0
    total_cost = full_budget / max(1, total_tranches)

    # Bid pricing: separate caps for hedge vs directional
    # Hedge: $0.475 cap (both sides, combined < $1 = guaranteed profit)
    # Directional: $0.40 cap (lower entry = better win/loss ratio 1.5x vs 1.1x)
    HEDGE_MAX = config.max_hedge_bid       # 0.475
    DIR_MAX = config.max_directional_bid   # 0.40
    MIN_BID = config.min_bid               # 0.25
    # Hedge pricing (used for Layer 1)
    up_bid_hedge = round(min(HEDGE_MAX, max(MIN_BID, fair_up - config.half_spread)), 2)
    dn_bid_hedge = round(min(HEDGE_MAX, max(MIN_BID, fair_down - config.half_spread)), 2)
    # Directional pricing (used for Layer 2) — lower cap = better EV
    up_bid_dir = round(min(DIR_MAX, max(MIN_BID, fair_up - config.half_spread)), 2)
    dn_bid_dir = round(min(DIR_MAX, max(MIN_BID, fair_down - config.half_spread)), 2)
    # For hedge calculations, use hedge bids
    up_bid = up_bid_hedge
    dn_bid = dn_bid_hedge
    combined = up_bid + dn_bid  # always <= $0.95

    # Sanity: if our directional side bid = MIN_BID, market strongly disagrees
    # Fair-spread < 0.35 means fair < 0.375 → market says <37.5% chance
    # Our bridge might say otherwise but respect the market's view

    # Can we afford hedge? (5 shares each side)
    hedge_min_cost = config.min_order_size * combined
    can_hedge = total_cost >= hedge_min_cost

    # Direction
    if fair_up >= fair_down:
        dir_side = "UP"
        dir_token = market.yes_token_id
        hedge_token = market.no_token_id
    else:
        dir_side = "DOWN"
        dir_token = market.no_token_id
        hedge_token = market.yes_token_id
    dir_bid = up_bid_dir if dir_side == "UP" else dn_bid_dir

    # Zone classification — adjusted by risk mode
    # NORMAL: standard allocation
    # DEFENSIVE: shift budget toward hedge (reduce directional exposure)
    # HEDGE_ONLY: no directional at all
    if risk_mode == "HEDGE_ONLY":
        hedge_pct, dir_pct = 1.0, 0.0
    elif confidence <= ZONE_1_BOUND:
        hedge_pct, dir_pct = 1.0, 0.0
    elif confidence <= ZONE_2_BOUND:
        if risk_mode == "DEFENSIVE":
            hedge_pct, dir_pct = 0.70, 0.30  # shift toward hedge
        else:
            hedge_pct, dir_pct = 0.50, 0.50
    else:
        if risk_mode == "DEFENSIVE":
            hedge_pct, dir_pct = 0.40, 0.60  # shift toward hedge
        else:
            hedge_pct, dir_pct = 0.25, 0.75

    orders = []

    # ── Layer 1: Hedge (equal shares both sides) ──
    if can_hedge and hedge_pct > 0:
        hedge_budget = total_cost * hedge_pct
        hedge_shares = hedge_budget / combined
        if hedge_shares >= config.min_order_size:
            hedge_shares = round(hedge_shares, 2)
            orders.append(PlannedOrder(
                token_id=market.yes_token_id, side="BUY",
                price=up_bid, size=hedge_shares, outcome="UP"))
            orders.append(PlannedOrder(
                token_id=market.no_token_id, side="BUY",
                price=dn_bid, size=hedge_shares, outcome="DOWN"))

    # ── Layer 2: Directional (naked, likely side) ──
    # Zone 1 normally hedge-only, but if can't afford hedge → allow directional
    # at lower dynamic price (better EV than skipping entirely)
    _allow_dir = dir_pct > 0 and confidence > ZONE_1_BOUND
    _zone1_fallback = not _allow_dir and not orders and confidence > 0.52
    if _allow_dir or _zone1_fallback:
        dir_budget = total_cost * dir_pct if _allow_dir else total_cost
        # If hedge couldn't fire, give full budget to directional
        if not orders:
            dir_budget = total_cost
        dir_shares = dir_budget / dir_bid
        if dir_shares >= config.min_order_size:
            dir_shares = round(dir_shares, 2)
            orders.append(PlannedOrder(
                token_id=dir_token, side="BUY",
                price=dir_bid, size=dir_shares, outcome=dir_side))
        elif dir_budget > 0:
            # Clamp to minimum if within 10% cap
            min_cost = config.min_order_size * dir_bid
            if bankroll <= 0 or min_cost <= bankroll * 0.05:
                orders.append(PlannedOrder(
                    token_id=dir_token, side="BUY",
                    price=dir_bid, size=config.min_order_size, outcome=dir_side))

    if not orders:
        if confidence <= ZONE_1_BOUND:
            logger.info("skip %s: Zone 1 but bankroll $%.0f too small for hedge",
                        market.condition_id[:8], bankroll)
        else:
            logger.info("skip %s: bankroll $%.0f too small",
                        market.condition_id[:8], bankroll)
        return []

    # Merge duplicate token+price orders (hedge UP + directional UP at same price)
    merged = {}
    for o in orders:
        key = (o.token_id, o.price)
        if key in merged:
            merged[key] = PlannedOrder(
                token_id=o.token_id, side=o.side, price=o.price,
                size=round(merged[key].size + o.size, 2), outcome=o.outcome)
        else:
            merged[key] = o
    orders = list(merged.values())

    # Log
    actual = sum(o.size * o.price for o in orders)
    hedge_count = sum(1 for o in orders if o.outcome != dir_side or
                      (sum(1 for x in orders if x.outcome == o.outcome) > 1))
    has_hedge = any(o.outcome == "UP" for o in orders) and any(o.outcome == "DOWN" for o in orders)
    zone = 1 if confidence <= ZONE_1_BOUND else (2 if confidence <= ZONE_2_BOUND else 3)
    mode = f"Z{zone}"
    if has_hedge:
        mode += " H+D" if dir_pct > 0 and confidence > ZONE_1_BOUND else " HEDGE"
    else:
        mode += " DIR"
    sides = " + ".join(f"{o.outcome}@{o.price:.2f}×{o.size:.1f}" for o in orders)
    logger.info("plan %s: %s %s | $%.2f (%.0f%%) | conf=%.3f",
                market.condition_id[:8], mode, sides,
                actual, actual / bankroll * 100 if bankroll > 0 else 0,
                confidence)
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
    """BTC + ETH 15M binary markets."""
    if market.category != "crypto_15m":
        return False
    if market.liquidity < config.min_liquidity:
        return False
    t = market.title.lower()
    return ("bitcoin" in t or "ethereum" in t) and "up or down" in t
