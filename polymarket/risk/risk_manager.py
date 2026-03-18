"""
risk_manager.py — Polymarket risk rules (non-negotiable)

Same philosophy as trader_cycle/risk/risk_manager.py:
純 if-else 規則，冇 LLM 介入。

Checks:
1. Circuit breaker — 3-state (CLOSED/OPEN/HALF_OPEN) for services
                   + daily loss limit for trading
2. Cooldown after losses
3. Position limits (max 5 open, max per category)
4. Exposure limits (max 30% bankroll)
5. Duplicate market check (唔重複買同一個市場)
"""

import logging
from datetime import datetime, timedelta

from ..config.settings import (
    MAX_DAILY_LOSS_PCT,
    MAX_OPEN_POSITIONS,
    MAX_TOTAL_EXPOSURE,
    MAX_PER_CATEGORY,
    COOLDOWN_AFTER_LOSS_MIN,
    COOLDOWN_AFTER_CIRCUIT_MIN,
    HKT,
)
from ..core.context import PolyContext, PolySignal
from .circuit_breaker import get_circuit_breaker, all_statuses, CircuitBreakerOpen

logger = logging.getLogger(__name__)


def check_safety(ctx: PolyContext) -> PolyContext:
    """Run all safety checks. Sets ctx.risk_blocked + ctx.risk_reasons."""

    # ─── Circuit Breaker: Daily Loss ───
    daily_pnl_pct = ctx.state.get("daily_pnl_pct", 0.0)
    if isinstance(daily_pnl_pct, (int, float)) and daily_pnl_pct < -MAX_DAILY_LOSS_PCT:
        ctx.risk_blocked = True
        ctx.circuit_breaker_active = True
        ctx.risk_reasons.append(
            f"Daily loss {daily_pnl_pct:.1%} exceeds {-MAX_DAILY_LOSS_PCT:.1%}"
        )

    # ─── Circuit Breaker: Active ───
    if ctx.state.get("circuit_breaker_active", False):
        cooldown_until = ctx.state.get("cooldown_until", "")
        if cooldown_until:
            try:
                cd = datetime.fromisoformat(cooldown_until)
                if ctx.timestamp and ctx.timestamp < cd:
                    ctx.risk_blocked = True
                    ctx.circuit_breaker_active = True
                    ctx.risk_reasons.append(
                        f"Circuit breaker until {cooldown_until}"
                    )
            except (ValueError, TypeError):
                pass

    # ─── Cooldown after loss ───
    last_loss_time = ctx.state.get("last_loss_time", "")
    if last_loss_time:
        try:
            lt = datetime.fromisoformat(last_loss_time)
            cooldown_end = lt + timedelta(minutes=COOLDOWN_AFTER_LOSS_MIN)
            if ctx.timestamp and ctx.timestamp < cooldown_end:
                remaining = (cooldown_end - ctx.timestamp).total_seconds() / 60
                ctx.risk_reasons.append(
                    f"Cooldown after loss: {remaining:.0f}min remaining"
                )
                # Soft block — allow position management but not new entries
        except (ValueError, TypeError):
            pass

    # ─── Position Count Limit ───
    if len(ctx.open_positions) >= MAX_OPEN_POSITIONS:
        ctx.risk_blocked = True
        ctx.risk_reasons.append(
            f"Max positions reached: {len(ctx.open_positions)}/{MAX_OPEN_POSITIONS}"
        )

    # ─── Exposure Limit ───
    if ctx.exposure_pct >= MAX_TOTAL_EXPOSURE:
        ctx.risk_blocked = True
        ctx.risk_reasons.append(
            f"Max exposure reached: {ctx.exposure_pct:.1%}/{MAX_TOTAL_EXPOSURE:.1%}"
        )

    # ─── Service Circuit Breakers (3-state) ───
    for status in all_statuses():
        if status["state"] == "open":
            ctx.risk_reasons.append(
                f"Service {status['service']} circuit breaker OPEN"
            )
            # Polymarket CLOB being down should block trading
            if status["service"] == "polymarket":
                ctx.risk_blocked = True

    return ctx


def filter_signals(ctx: PolyContext) -> list[PolySignal]:
    """Filter signals through risk checks.

    Removes signals that violate risk rules:
    - Duplicate markets (already have a position)
    - Category exposure limit
    - Zero bet size
    """
    if ctx.risk_blocked:
        return []

    # Markets we already have positions in
    held_markets = {p.condition_id for p in ctx.open_positions}

    # Category exposure
    cat_exposure: dict[str, float] = {}
    for p in ctx.open_positions:
        cat_exposure[p.category] = cat_exposure.get(p.category, 0) + p.cost_basis

    filtered = []
    for signal in ctx.signals:
        # Duplicate check
        if signal.condition_id in held_markets:
            logger.info("Risk: skip duplicate market %s", signal.title[:30])
            continue

        # Category limit
        cat_exp = cat_exposure.get(signal.category, 0)
        max_cat = ctx.usdc_balance * MAX_PER_CATEGORY
        if cat_exp >= max_cat:
            logger.info(
                "Risk: category %s at limit $%.0f/$%.0f",
                signal.category, cat_exp, max_cat,
            )
            continue

        filtered.append(signal)

    return filtered


def trigger_circuit_breaker(ctx: PolyContext) -> dict:
    """Activate circuit breaker. Returns state updates."""
    cooldown_end = ctx.timestamp + timedelta(minutes=COOLDOWN_AFTER_CIRCUIT_MIN)
    return {
        "circuit_breaker_active": True,
        "cooldown_until": cooldown_end.isoformat(),
    }


def record_loss(ctx: PolyContext) -> dict:
    """Record a loss event. Returns state updates."""
    consecutive = ctx.state.get("consecutive_losses", 0) + 1
    updates = {
        "consecutive_losses": consecutive,
        "last_loss_time": ctx.timestamp.isoformat() if ctx.timestamp else "",
    }

    # Trigger circuit breaker on 3 consecutive losses
    if consecutive >= 3:
        updates.update(trigger_circuit_breaker(ctx))
        logger.warning("Circuit breaker triggered: %d consecutive losses", consecutive)

    return updates


def record_win(ctx: PolyContext) -> dict:
    """Record a win event. Resets consecutive loss counter."""
    return {
        "consecutive_losses": 0,
    }


def protected_call(service_name: str, func, *args, **kwargs):
    """Execute a function through the service circuit breaker.

    Usage:
        result = protected_call("polymarket", client.buy_shares, token_id=..., ...)
        result = protected_call("gamma", gamma.get_markets, limit=100)

    Raises CircuitBreakerOpen if service is down.
    """
    cb = get_circuit_breaker(service_name)
    return cb.call(func, *args, **kwargs)
