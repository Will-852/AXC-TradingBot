"""
validators.py — Pre-trade validation (Sprint 3).

3 validators run before ExecuteTradeStep:
  1. DataFreshness: signal price not stale
  2. Balance: sufficient funds for order
  3. Duplicate: no existing position on same pair

Modes:
  HARD = block order (ctx.selected_signal = None)
  SOFT = warn only (ctx.warnings)
  Fail-open: validator crash → SOFT warn, never HARD block
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from ..core.context import CycleContext

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of a single validator check."""
    __slots__ = ("passed", "hard_block", "message")

    def __init__(self, passed: bool = True, hard_block: bool = False, message: str = ""):
        self.passed = passed
        self.hard_block = hard_block
        self.message = message


class BaseValidator(ABC):
    """Base class for pre-trade validators."""
    name: str = "base"

    @abstractmethod
    def validate(self, ctx: CycleContext) -> ValidationResult:
        """Check pre-trade conditions. Return ValidationResult."""
        ...


class DataFreshnessValidator(BaseValidator):
    """Order-level freshness: signal price vs current market time.

    Complements Sprint 0.3 pair-level validation (market_data.py).
    This checks: is the signal's entry price based on stale data?
    """
    name = "data_freshness"
    MAX_SIGNAL_AGE_SEC = 120  # signal older than 2 min = stale

    def validate(self, ctx: CycleContext) -> ValidationResult:
        signal = ctx.selected_signal
        if not signal:
            return ValidationResult()

        # Check if market data exists for this pair
        snap = ctx.market_data.get(signal.pair)
        if not snap or snap.price <= 0:
            return ValidationResult(
                passed=False, hard_block=True,
                message=f"No market data for {signal.pair}",
            )

        # Price divergence check: signal entry vs current market
        # Large divergence suggests signal was generated from old data
        if signal.entry_price > 0 and snap.price > 0:
            divergence = abs(signal.entry_price - snap.price) / snap.price
            if divergence > 0.02:  # >2% divergence = stale signal
                return ValidationResult(
                    passed=False, hard_block=True,
                    message=(
                        f"Stale signal: entry {signal.entry_price} vs "
                        f"market {snap.price} ({divergence:.1%} divergence)"
                    ),
                )

        return ValidationResult()


class BalanceValidator(BaseValidator):
    """Verify sufficient balance for the order."""
    name = "balance"

    def validate(self, ctx: CycleContext) -> ValidationResult:
        signal = ctx.selected_signal
        if not signal:
            return ValidationResult()

        # margin_required is set by SizePositionStep
        margin_needed = signal.margin_required
        if margin_needed <= 0:
            return ValidationResult()

        available = ctx.account_balance
        if available <= 0:
            return ValidationResult(
                passed=False, hard_block=True,
                message="Account balance unknown or zero",
            )

        if margin_needed > available:
            return ValidationResult(
                passed=False, hard_block=True,
                message=(
                    f"Insufficient balance: need ${margin_needed:.2f} "
                    f"margin, have ${available:.2f}"
                ),
            )

        # Warn if using >80% of balance
        usage_pct = margin_needed / available
        if usage_pct > 0.80:
            return ValidationResult(
                passed=True, hard_block=False,
                message=f"High margin usage: {usage_pct:.0%} of balance",
            )

        return ValidationResult()


class DuplicateValidator(BaseValidator):
    """Prevent duplicate entries on same pair."""
    name = "duplicate"

    def validate(self, ctx: CycleContext) -> ValidationResult:
        signal = ctx.selected_signal
        if not signal:
            return ValidationResult()

        for pos in ctx.open_positions:
            if pos.pair == signal.pair:
                return ValidationResult(
                    passed=False, hard_block=True,
                    message=(
                        f"Duplicate: already have {pos.direction} "
                        f"{pos.pair} (entry={pos.entry_price})"
                    ),
                )

        return ValidationResult()


# ─── Pipeline Step ───

# All validators in execution order
_VALIDATORS: list[BaseValidator] = [
    DataFreshnessValidator(),
    BalanceValidator(),
    DuplicateValidator(),
]


class ValidateOrderStep:
    """Step 11.5: Pre-trade validation (between SizePosition and ExecuteTrade).

    Runs all validators. Any HARD block → signal cleared.
    Validator crash → SOFT warn only (fail-open).
    """
    name = "validate_order"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not ctx.selected_signal:
            return ctx

        import os
        if os.environ.get("USE_VALIDATION_PIPELINE", "true").lower() != "true":
            return ctx

        blocked = False
        for validator in _VALIDATORS:
            try:
                result = validator.validate(ctx)
                if not result.passed:
                    if result.hard_block:
                        blocked = True
                        ctx.no_trade_reasons.append(
                            f"VALIDATOR_{validator.name.upper()}: {result.message}"
                        )
                        logger.warning(f"[VALIDATE] HARD block: {validator.name}: {result.message}")
                    else:
                        ctx.warnings.append(f"[VALIDATE] {validator.name}: {result.message}")
                elif result.message:
                    # Passed but with warning
                    ctx.warnings.append(f"[VALIDATE] {validator.name}: {result.message}")
            except Exception as e:
                # Fail-open: validator crash → warn, never block
                ctx.warnings.append(f"[VALIDATE] {validator.name} crashed: {e}")
                logger.error(f"Validator {validator.name} crashed: {e}", exc_info=True)

        if blocked:
            pair = ctx.selected_signal.pair
            ctx.selected_signal = None
            ctx.telegram_messages.append(
                f"<b>Order Blocked</b> [{pair}]\n"
                + "\n".join(r for r in ctx.no_trade_reasons if "VALIDATOR_" in r)
            )
            if ctx.verbose:
                print(f"    VALIDATE: Order blocked — {len(ctx.no_trade_reasons)} reasons")

        return ctx
