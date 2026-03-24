"""
F10 Simulation Validation Gate
===============================
BMD Session 3 found a sim bug: adjusting lean ratio changes BOTH cost AND redeem,
but the sim only adjusted cost. This module provides a hard validation gate that
any Phase 1+ simulation MUST call before reporting results.

Usage:
    from analysis.f10_validate import validate_trade, validate_sim_batch

    # Per-trade validation
    validate_trade(
        lean_shares=6, hedge_shares=4, lean_price=0.55, hedge_price=0.45,
        total_cost=5.10, win_payout=6.0, lose_payout=4.0
    )

    # Batch validation (raises on first failure)
    validate_sim_batch(trades_list)

Raises AssertionError with detailed message on ANY mismatch.
"""

import logging

logger = logging.getLogger(__name__)

# Tolerance for floating point comparison (half a cent)
_TOL = 0.005


def validate_trade(
    lean_shares: float,
    hedge_shares: float,
    lean_price: float,
    hedge_price: float,
    total_cost: float,
    win_payout: float,
    lose_payout: float,
    trade_id: str = "",
) -> None:
    """Validate that a simulated trade has consistent cost AND redeem.

    F10 rule: when lean_ratio changes, BOTH sides must adjust together.
    - total_cost = lean_shares × lean_price + hedge_shares × hedge_price
    - win_payout = lean_shares × $1 (lean direction correct → lean redeems)
    - lose_payout = hedge_shares × $1 (lean direction wrong → hedge redeems)

    Raises AssertionError if any check fails.
    """
    prefix = f"[F10 {trade_id}] " if trade_id else "[F10] "

    # Check 1: cost consistency
    expected_cost = lean_shares * lean_price + hedge_shares * hedge_price
    cost_diff = abs(total_cost - expected_cost)
    assert cost_diff < _TOL, (
        f"{prefix}COST MISMATCH: "
        f"total_cost={total_cost:.4f} != "
        f"lean({lean_shares}×{lean_price:.2f}) + hedge({hedge_shares}×{hedge_price:.2f}) "
        f"= {expected_cost:.4f} (diff={cost_diff:.4f})"
    )

    # Check 2: win payout consistency (lean correct → lean shares redeem at $1)
    expected_win = lean_shares * 1.0
    win_diff = abs(win_payout - expected_win)
    assert win_diff < _TOL, (
        f"{prefix}WIN PAYOUT MISMATCH: "
        f"win_payout={win_payout:.4f} != lean_shares({lean_shares}) × $1 "
        f"= {expected_win:.4f} (diff={win_diff:.4f})"
    )

    # Check 3: lose payout consistency (lean wrong → hedge shares redeem at $1)
    expected_lose = hedge_shares * 1.0
    lose_diff = abs(lose_payout - expected_lose)
    assert lose_diff < _TOL, (
        f"{prefix}LOSE PAYOUT MISMATCH: "
        f"lose_payout={lose_payout:.4f} != hedge_shares({hedge_shares}) × $1 "
        f"= {expected_lose:.4f} (diff={lose_diff:.4f})"
    )

    # Check 4: sanity — prices must be in (0, 1) for binary
    assert 0 < lean_price < 1, f"{prefix}lean_price={lean_price} out of (0,1)"
    assert 0 < hedge_price < 1, f"{prefix}hedge_price={hedge_price} out of (0,1)"

    # Check 5: combined price sanity — lean_price + hedge_price should be ~$1
    # (Polymarket binary: Up + Down ≈ $1, but spread means 0.97-1.03 is normal)
    combined = lean_price + hedge_price
    assert 0.90 < combined < 1.10, (
        f"{prefix}COMBINED PRICE {combined:.4f} outside 0.90-1.10 "
        f"(lean={lean_price:.2f}, hedge={hedge_price:.2f})"
    )

    # Check 6: EV direction — win profit > 0, lose profit can be negative
    win_profit = win_payout - total_cost
    lose_profit = lose_payout - total_cost
    # Win should be positive (otherwise why trade?)
    if win_profit <= 0:
        logger.warning(
            "%sWin profit %.4f <= 0 (cost=%.2f, payout=%.2f). "
            "Trade has no upside — check pricing.",
            prefix, win_profit, total_cost, win_payout,
        )


def validate_sim_batch(trades: list[dict], label: str = "") -> int:
    """Validate a batch of simulated trades. Returns count validated.

    Each trade dict must have keys:
        lean_shares, hedge_shares, lean_price, hedge_price,
        total_cost, win_payout, lose_payout

    Optional: trade_id (str)
    """
    prefix = f"[{label}] " if label else ""
    for i, t in enumerate(trades):
        tid = t.get("trade_id", f"#{i}")
        validate_trade(
            lean_shares=t["lean_shares"],
            hedge_shares=t["hedge_shares"],
            lean_price=t["lean_price"],
            hedge_price=t["hedge_price"],
            total_cost=t["total_cost"],
            win_payout=t["win_payout"],
            lose_payout=t["lose_payout"],
            trade_id=f"{prefix}{tid}",
        )
    count = len(trades)
    logger.info("F10 validation PASSED: %d trades (%s)", count, label or "no label")
    return count


# ── Quick self-test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("F10 Validation Self-Test")
    print("=" * 50)

    # Good trade: 6 lean at $0.55, 4 hedge at $0.45
    print("\n1. Valid trade (6:4 at 0.55/0.45)...")
    validate_trade(
        lean_shares=6, hedge_shares=4, lean_price=0.55, hedge_price=0.45,
        total_cost=5.10, win_payout=6.0, lose_payout=4.0,
        trade_id="good_1",
    )
    print("   PASS ✅")

    # Good trade: dynamic ratio 8:3 at 0.60/0.40
    print("\n2. Valid trade (8:3 at 0.60/0.40)...")
    validate_trade(
        lean_shares=8, hedge_shares=3, lean_price=0.60, hedge_price=0.40,
        total_cost=6.0, win_payout=8.0, lose_payout=3.0,
        trade_id="good_2",
    )
    print("   PASS ✅")

    # BAD trade: cost adjusted but redeem NOT adjusted (the F10 bug)
    print("\n3. F10 BUG: cost=5.10 but win_payout=8.0 (ratio changed, redeem not)...")
    try:
        validate_trade(
            lean_shares=6, hedge_shares=4, lean_price=0.55, hedge_price=0.45,
            total_cost=5.10, win_payout=8.0, lose_payout=4.0,  # win_payout wrong!
            trade_id="f10_bug",
        )
        print("   FAIL ❌ (should have raised)")
    except AssertionError as e:
        print(f"   CAUGHT ✅: {e}")

    # BAD trade: redeem adjusted but cost NOT adjusted
    print("\n4. Reverse F10 BUG: lean=8 shares but cost still 5.10...")
    try:
        validate_trade(
            lean_shares=8, hedge_shares=3, lean_price=0.60, hedge_price=0.40,
            total_cost=5.10, win_payout=8.0, lose_payout=3.0,  # cost wrong!
            trade_id="reverse_f10",
        )
        print("   FAIL ❌ (should have raised)")
    except AssertionError as e:
        print(f"   CAUGHT ✅: {e}")

    # Batch test
    print("\n5. Batch validation (2 good trades)...")
    n = validate_sim_batch([
        {"lean_shares": 6, "hedge_shares": 4, "lean_price": 0.55, "hedge_price": 0.45,
         "total_cost": 5.10, "win_payout": 6.0, "lose_payout": 4.0},
        {"lean_shares": 7, "hedge_shares": 3, "lean_price": 0.58, "hedge_price": 0.42,
         "total_cost": 5.32, "win_payout": 7.0, "lose_payout": 3.0},
    ], label="batch_test")
    print(f"   PASS ✅ ({n} trades validated)")

    print("\n" + "=" * 50)
    print("All self-tests passed. F10 gate is operational.")
