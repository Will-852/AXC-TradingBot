"""
hourly_engine.py — Dynamic conviction pricing for 1H crypto prediction markets

Core formula: conviction(t, fair, ob) → (action, direction, entry_price, size_fraction)

No fixed parameters. Three inputs interact continuously:
  1. Time elapsed (t) — more observation → more trust in signal
  2. Fair value (fair) — Brownian Bridge P(close >= open), fat-tail adjusted
  3. Order book state (ob) — spread, depth → fill probability + conviction gate

The bot evaluates this function every tick. When conviction crosses
the dynamic threshold → enter. Size scales with conviction × OB quality.

Math:
  confidence = |fair - 0.50| × 2          (0 = coin flip, 1 = certain)
  time_trust = min(t / 40, 1.0)           (saturates at 40 min)
  ob_factor  = f(ob_quality)              (1.0 if unknown, <1.0 if bad OB)
  conviction = confidence × time_trust × ob_factor  (all three must be high)
  entry_price = fair - dynamic_spread      (tighter at high conviction)
  size = base × conviction² × ob_quality × budget_remaining  (scales with all)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from statistics import NormalDist

logger = logging.getLogger(__name__)
_norm = NormalDist()


@dataclass
class ConvictionSignal:
    """Output of conviction_signal() — everything the bot needs to act."""
    action: str          # WAIT / ENTER / SKIP / EXIT / ADD / HOLD
    direction: str       # UP / DOWN
    conviction: float    # 0-1 combined score
    confidence: float    # 0-1 from fair value alone
    time_trust: float    # 0-1 from elapsed time
    entry_price: float   # optimal limit price (0 if WAIT/SKIP/EXIT/HOLD)
    size_fraction: float # fraction of remaining budget (0 if WAIT/SKIP/EXIT/HOLD)
    fair_up: float       # Brownian Bridge output (after fat-tail haircut)
    p_win: float         # probability of winning side
    reason: str          # human-readable explanation


@dataclass
class OBState:
    """Order book snapshot for conviction calculation."""
    spread: float = 0.0        # best_ask - best_bid
    bid_depth: float = 0.0     # total bid volume
    ask_depth: float = 0.0     # total ask volume
    mid_depth: float = 0.0     # depth within +/-10c of midpoint
    imbalance: float = 0.0     # (bid - ask) / total


@dataclass
class HourlyConfig:
    """
    Tunable parameters for the conviction engine.
    These are meta-parameters, not fixed trading parameters.
    The actual entry decisions are computed dynamically.
    """
    # Time trust curve
    time_saturation_min: float = 40.0    # time_trust saturates at this minute
    # Conviction threshold curve — higher start = wait longer before first entry
    # 0.45 at t=0, dropping 0.005/min → crosses 0.25 at ~t=40min for small moves
    # But large moves (confidence=0.8) can cross at ~t=20min: 0.8 × 0.5 = 0.40 > 0.35
    min_conviction_start: float = 0.33   # scout early (0.3x size dampen) + ADD later at full size
    min_conviction_decay: float = 0.005  # threshold drops by this per minute
    min_conviction_floor: float = 0.12   # never enter below this conviction (was 0.10)
    # Entry price: v3 aggressive — 1H has no taker fee, market price entry is optimal.
    # Backtest (30d, 719 hours): 0c spread → Sharpe +0.28 (SOL), +0.24 (ETH), +0.14 (BTC).
    # Old $0.39 cap killed fill rate (11%). New: entry near fair, capped at $0.75.
    price_cap_base: float = 0.55         # cap at zero conviction (was 0.25)
    price_cap_scale: float = 0.20        # cap grows by this x conviction → max 0.75 (was 0.12)
    max_entry_price: float = 0.75        # hard ceiling (was 0.39). 1H no taker fee = buy closer to fair.
    min_entry_price: float = 0.20        # never pay less than this (too far = no fill)
    min_ev_per_share: float = 0.03       # minimum 3c EV per share (was 5c, tighter with higher entry)
    # Spread scaling — v3: tiny spread (3c base). Edge is direction accuracy, not cheap entry.
    base_spread: float = 0.03           # spread at zero conviction (was 0.15)
    spread_compression: float = 0.7     # how much conviction compresses spread
    # Size scaling: conviction^2 (quadratic, not linear)
    max_size_fraction: float = 0.05     # max 5% of bankroll per window
    min_size_fraction: float = 0.01     # min 1% of bankroll
    # OB quality thresholds
    ob_spread_baseline: float = 0.02    # "good" spread = 2c
    ob_depth_baseline: float = 5000.0   # "good" depth = 5000 shares
    # OB conviction gate: below this quality, OB penalizes conviction too
    ob_bad_threshold: float = 0.30      # ob_quality < 0.30 = bad, penalize conviction
    ob_depth_override_mult: float = 10.0  # depth >= 10x baseline → override spread penalty
    # Late window cutoff
    late_cutoff_min: float = 56.0       # normal entries stop at minute 56
    late_entry_cutoff_min: float = 58.0 # high-conviction entries allowed until minute 58
    late_entry_min_confidence: float = 0.79  # ≥79% confidence = BTC +0.2%+ at t=57. User-set threshold.
    # Skip near coin-flip
    min_fair_deviation: float = 0.05    # fair must deviate >=5c from 0.50
    # Fat-tail adjustment: BTC kurtosis > 9, normal CDF overestimates confidence
    fat_tail_haircut: float = 0.10      # 10% haircut toward 0.50
    # Stop loss: tighter with v3 higher entries (more to lose per share)
    stop_loss_pct: float = -0.25         # -25% unrealized → EXIT (was -49%, aligned with 15M now)
    # Mid sanity: Polymarket market mid must agree with our direction
    min_market_mid: float = 0.28         # mid for our side must be ≥28¢ (below = market strongly disagrees)
    # Min order size for Polymarket (5 shares x $0.50 = $2.50)
    min_order_usd: float = 2.50         # skip if computed order < this


def conviction_signal(
    t_elapsed: float,
    btc_current: float,
    btc_open: float,
    vol_1m: float,
    ob: OBState | None = None,
    config: HourlyConfig | None = None,
    bankroll: float = 0,
    budget_remaining_frac: float = 1.0,
    current_position: dict | None = None,
) -> ConvictionSignal:
    """
    Dynamic conviction pricing — the core formula.

    Evaluates whether to enter/exit/add, at what price, and how much.
    Called every tick (5-20s). No fixed wait times or thresholds.

    Args:
        t_elapsed: minutes into the 1H window (0-60)
        btc_current: current BTC/USDT price
        btc_open: BTC price at hour open (Binance 1H candle open)
        vol_1m: per-minute volatility (stdev of 1m log returns)
        ob: order book state (optional, degrades gracefully)
        config: tunable meta-parameters
        bankroll: total bankroll in USD (0 = skip min-size check)
        budget_remaining_frac: 1.0 = fresh, 0.5 = half spent, 0 = fully invested
        current_position: existing position dict with keys:
            direction (UP/DOWN), avg_price (float), unrealized_pnl_pct (float)
            None = no position (default)
    """
    if config is None:
        config = HourlyConfig()
    if ob is None:
        ob = OBState()

    t_remaining = 60.0 - t_elapsed

    # ─── Guard: fully invested ───
    # FIX: check budget regardless of position. Old code skipped this guard
    # when current_position existed → allowed infinite re-entry (119 shares bug).
    if budget_remaining_frac <= 0:
        if current_position is not None:
            return ConvictionSignal(
                action="HOLD", direction=current_position.get("direction", ""),
                conviction=0, confidence=0,
                time_trust=0, entry_price=0, size_fraction=0,
                fair_up=0, p_win=0, reason="budget fully invested, holding position")
        return ConvictionSignal(
            action="SKIP", direction="", conviction=0, confidence=0,
            time_trust=0, entry_price=0, size_fraction=0,
            fair_up=0, p_win=0, reason="budget fully invested")

    # ─── Guard: too late ───
    # Normal entries: stop at minute 56
    # Late high-conviction entries: allowed t=56-58 if confidence ≥ 40%
    # Rationale: at t=57, BTC +0.5% above open → P(UP) > 90% = "you know the answer"
    # Inspired by Woeful-Analyst wallet (63K markets, 100% WR, enters when outcome is near-certain)
    _hard_cutoff = t_elapsed >= config.late_entry_cutoff_min
    _soft_cutoff = t_elapsed >= config.late_cutoff_min and t_elapsed < config.late_entry_cutoff_min
    if _hard_cutoff:
        return ConvictionSignal(
            action="SKIP", direction="", conviction=0, confidence=0,
            time_trust=1.0, entry_price=0, size_fraction=0,
            fair_up=0, p_win=0, reason=f"too late ({t_elapsed:.0f}min, hard cutoff)")

    # ─── 1. Fair value (Brownian Bridge) ───
    if vol_1m <= 0 or btc_current <= 0 or btc_open <= 0 or t_remaining <= 0:
        fair_up = 0.5
    else:
        sigma = vol_1m * math.sqrt(t_remaining)
        if sigma < 1e-10:
            fair_up = 0.995 if btc_current >= btc_open else 0.005
        else:
            d = math.log(btc_current / btc_open) / sigma
            fair_up = max(0.005, min(0.995, _norm.cdf(d)))

    # ─── 1b. Fat-tail haircut ───
    # Normal CDF overestimates confidence for BTC (kurtosis > 9).
    # Pull fair_up toward 0.50 by haircut percentage to be more conservative.
    if config.fat_tail_haircut > 0:
        fair_up = 0.50 + (fair_up - 0.50) * (1.0 - config.fat_tail_haircut)

    # ─── 2. Direction + confidence ───
    direction = "UP" if fair_up >= 0.50 else "DOWN"
    p_win = max(fair_up, 1.0 - fair_up)  # probability of winning side
    confidence = (p_win - 0.50) * 2.0     # scale to [0, 1]

    # ─── 2a. Soft cutoff: t=56-58 needs high confidence ───
    if _soft_cutoff and confidence < config.late_entry_min_confidence:
        return ConvictionSignal(
            action="SKIP", direction=direction, conviction=0, confidence=confidence,
            time_trust=1.0, entry_price=0, size_fraction=0,
            fair_up=fair_up, p_win=p_win,
            reason=f"late ({t_elapsed:.0f}min) + low confidence {confidence:.2f} < {config.late_entry_min_confidence}")

    # ─── 2b. Position awareness: EXIT / HOLD check ───
    if current_position is not None:
        pos_dir = current_position.get("direction", "")
        signal = _check_position(
            pos_dir, direction, fair_up, confidence, current_position, config)
        if signal is not None:
            return signal

    # Too close to coin-flip?
    if abs(fair_up - 0.50) < config.min_fair_deviation:
        return ConvictionSignal(
            action="WAIT", direction=direction, conviction=0,
            confidence=confidence, time_trust=0, entry_price=0,
            size_fraction=0, fair_up=fair_up, p_win=p_win,
            reason=f"fair {fair_up:.3f} too close to 50/50")

    # ─── 3. Time trust ───
    time_trust = min(t_elapsed / config.time_saturation_min, 1.0)

    # ─── 4. OB quality + OB factor for conviction ───
    ob_quality = _compute_ob_quality(ob, config)

    # ob_factor: how OB affects conviction (separate from sizing)
    # - Unknown OB (quality = 0.5): don't penalize conviction → ob_factor = 1.0
    # - Bad OB (quality < 0.30): wide spread = can't fill = don't enter → penalize
    # - Good OB (quality >= 0.30): no penalty → ob_factor = 1.0
    if ob_quality < config.ob_bad_threshold:
        ob_factor = ob_quality  # bad OB drags conviction down
    else:
        ob_factor = 1.0  # good or unknown → no penalty on conviction

    # ─── 5. Combined conviction: three-factor ───
    conviction = confidence * time_trust * ob_factor

    # ─── 6. Dynamic threshold ───
    threshold = max(
        config.min_conviction_floor,
        config.min_conviction_start - t_elapsed * config.min_conviction_decay,
    )

    if conviction < threshold:
        return ConvictionSignal(
            action="WAIT", direction=direction, conviction=conviction,
            confidence=confidence, time_trust=time_trust, entry_price=0,
            size_fraction=0, fair_up=fair_up, p_win=p_win,
            reason=f"conviction {conviction:.3f} < threshold {threshold:.3f}")

    # ─── 7. Entry price: dynamic spread + dynamic cap ───
    # High conviction → spread compresses → price closer to fair → more fills
    # Low conviction → wide spread → cheaper entry → more margin of safety
    dynamic_spread = config.base_spread * (1.0 - conviction * config.spread_compression)
    entry_price = p_win - dynamic_spread

    # Dynamic cap: scales with conviction (low conviction → tighter cap)
    # v2: conviction 0.2 → $0.274 | 0.5 → $0.31 | 0.8 → $0.346 | 1.0 → $0.37
    price_cap = config.price_cap_base + conviction * config.price_cap_scale
    entry_price = min(entry_price, price_cap)

    # Hard ceiling: structural edge protection (break-even WR = entry price)
    if config.max_entry_price > 0:
        entry_price = min(entry_price, config.max_entry_price)

    # EV floor: entry must leave at least min_ev per share
    ev_cap = p_win - config.min_ev_per_share
    entry_price = min(entry_price, ev_cap)

    # Floor
    entry_price = max(config.min_entry_price, entry_price)

    # Sanity: entry must be below fair
    if entry_price >= p_win:
        entry_price = p_win - 0.02

    # ─── 8. Size: conviction^2 x OB quality x remaining budget x time dampen ───
    # conviction^2 = quadratic scaling (low conviction → tiny, high → full)
    size_fraction = config.max_size_fraction * (conviction ** 2) * ob_quality

    # Early-window dampening: first 30 min → size further reduced
    # t=0: 0.3x | t=15: 0.65x | t=30: 1.0x | t=45: 1.0x
    # This keeps early entries as tiny scouts, late entries as full conviction
    early_dampen = min(1.0, max(0.3, t_elapsed / 30.0))
    size_fraction *= early_dampen

    size_fraction = max(config.min_size_fraction, min(config.max_size_fraction, size_fraction))

    # Scale by remaining budget (multi-entry support)
    size_fraction *= budget_remaining_frac

    # ─── 8b. Min order size check ───
    if bankroll > 0 and config.min_order_usd > 0:
        order_usd = size_fraction * bankroll
        if order_usd < config.min_order_usd:
            min_frac = config.min_order_usd / bankroll
            if min_frac <= config.max_size_fraction * budget_remaining_frac:
                # Bump to minimum viable order
                size_fraction = min_frac
            else:
                # Can't even place min order within budget → skip
                return ConvictionSignal(
                    action="SKIP", direction=direction, conviction=conviction,
                    confidence=confidence, time_trust=time_trust,
                    entry_price=entry_price, size_fraction=0,
                    fair_up=fair_up, p_win=p_win,
                    reason=f"order ${order_usd:.2f} < min ${config.min_order_usd:.2f}")

    # ─── 9. EV check ───
    ev = p_win * (1.0 - entry_price) - (1.0 - p_win) * entry_price
    if ev <= 0:
        return ConvictionSignal(
            action="SKIP", direction=direction, conviction=conviction,
            confidence=confidence, time_trust=time_trust,
            entry_price=entry_price, size_fraction=0,
            fair_up=fair_up, p_win=p_win,
            reason=f"negative EV: {ev:.4f} at entry ${entry_price:.3f}")

    # ─── 10. Determine action: ENTER vs ADD ───
    action = "ENTER"
    if current_position is not None:
        pos_dir = current_position.get("direction", "")
        if pos_dir == direction:
            action = "ADD"  # same direction, conviction high enough → scale in

    return ConvictionSignal(
        action=action, direction=direction, conviction=conviction,
        confidence=confidence, time_trust=time_trust,
        entry_price=round(entry_price, 2),
        size_fraction=round(size_fraction, 4),
        fair_up=fair_up, p_win=p_win,
        reason=(f"conviction {conviction:.3f} > {threshold:.3f} | "
                f"EV ${ev:.3f} | entry ${entry_price:.2f} | "
                f"size {size_fraction:.1%} | OB qual {ob_quality:.2f}"))


def _check_position(
    pos_dir: str,
    signal_dir: str,
    fair_up: float,
    confidence: float,
    position: dict,
    config: HourlyConfig | None = None,
) -> ConvictionSignal | None:
    """
    Position-aware logic: detect when we should EXIT or HOLD.

    Returns a ConvictionSignal for EXIT/HOLD, or None to let normal
    ENTER/ADD logic proceed.

    Checks (in order):
    1. Stop loss: unrealized PnL < -40% → EXIT regardless
    2. Fair flip hard: direction strongly reversed → EXIT
    3. Mild flip → HOLD (don't panic on noise)
    4. Same direction → None (let ADD logic proceed)
    """
    stop_pct = config.stop_loss_pct if config else -0.40

    # ─── 1. Stop loss: unrealized too deep ───
    unrealized = position.get("unrealized_pnl_pct", 0)
    if unrealized < stop_pct:
        return ConvictionSignal(
            action="EXIT", direction=pos_dir, conviction=0,
            confidence=confidence, time_trust=0, entry_price=0,
            size_fraction=0, fair_up=fair_up,
            p_win=max(fair_up, 1.0 - fair_up),
            reason=f"STOP LOSS: unrealized {unrealized:.0%} < {stop_pct:.0%} — EXIT")

    # ─── 2. Same direction → let ADD logic proceed ───
    if pos_dir == signal_dir:
        return None

    # ─── 3. Direction flipped: check severity ───
    #   pos=UP,  fair < 0.40 → strong flip → EXIT
    #   pos=DOWN, fair > 0.60 → strong flip → EXIT
    #   Otherwise → mild flip → HOLD
    if pos_dir == "UP":
        strong_flip = fair_up < 0.40
    else:
        strong_flip = fair_up > 0.60

    if strong_flip:
        return ConvictionSignal(
            action="EXIT", direction=signal_dir, conviction=0,
            confidence=confidence, time_trust=0, entry_price=0,
            size_fraction=0, fair_up=fair_up,
            p_win=max(fair_up, 1.0 - fair_up),
            reason=f"direction flipped to {signal_dir}, fair {fair_up:.3f} — EXIT")

    # Mild flip — hold, don't panic on noise
    return ConvictionSignal(
        action="HOLD", direction=pos_dir, conviction=0,
        confidence=confidence, time_trust=0, entry_price=0,
        size_fraction=0, fair_up=fair_up,
        p_win=max(fair_up, 1.0 - fair_up),
        reason=f"mild flip to {signal_dir} (fair {fair_up:.3f}) — HOLD position")


def _compute_ob_quality(ob: OBState, config: HourlyConfig) -> float:
    """
    OB quality score (0-1). Combines spread tightness + depth.
    Hollow book override: depth >= 10x baseline → quality floor 0.50.
    """
    if ob.spread <= 0 and ob.bid_depth <= 0:
        return 0.5

    if ob.spread > 0:
        spread_factor = min(1.0, config.ob_spread_baseline / max(ob.spread, 0.001))
    else:
        spread_factor = 0.5

    total_depth = ob.bid_depth + ob.ask_depth
    if total_depth > 0:
        depth_factor = min(1.0, total_depth / config.ob_depth_baseline)
    else:
        depth_factor = 0.5

    quality = math.sqrt(spread_factor * depth_factor)

    # Hollow book override: massive depth compensates for wide spread
    if total_depth >= config.ob_depth_baseline * config.ob_depth_override_mult:
        quality = max(quality, 0.50)

    return quality


# =======================================
#  Visualization / debugging helpers
# =======================================

def describe_conviction_surface(vol_1m: float = 0.00077,
                                btc_open: float = 83000.0,
                                config: HourlyConfig | None = None):
    """Print the conviction surface for debugging. Shows when ENTER triggers."""
    if config is None:
        config = HourlyConfig()

    print("\n  Conviction Surface: minutes x BTC move -> action")
    print(f"  vol_1m={vol_1m:.5f}, btc_open=${btc_open:,.0f}")
    print(f"  fat_tail_haircut={config.fat_tail_haircut:.0%}")
    print()

    moves_pct = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.80, 1.00]
    print(f"  {'min':>4s}", end="")
    for m in moves_pct:
        print(f"  {m:>+5.2f}%", end="")
    print()
    print("  " + "-" * (6 + 8 * len(moves_pct)))

    for t in range(0, 58, 3):
        print(f"  {t:>3d}m", end="")
        for m in moves_pct:
            btc = btc_open * (1 + m / 100)
            sig = conviction_signal(t, btc, btc_open, vol_1m, config=config)
            if sig.action in ("ENTER", "ADD"):
                print(f"  ${sig.entry_price:.2f}", end="")
            elif sig.action == "WAIT":
                print(f"  {'wait':>5s}", end="")
            elif sig.action == "EXIT":
                print(f"  {'EXIT':>5s}", end="")
            elif sig.action == "HOLD":
                print(f"  {'hold':>5s}", end="")
            else:
                print(f"  {'skip':>5s}", end="")
        print()


if __name__ == "__main__":
    describe_conviction_surface()
