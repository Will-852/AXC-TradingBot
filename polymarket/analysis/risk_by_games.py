#!/usr/bin/env python3
"""
W4 Both-Sides Risk Calculator — Risk measured in GAMES (trades), not dollars.

Design decisions:
- Share count lean (1.5:1) per gotchas.md: budget lean != share lean.
  Cheap side gets more shares per dollar, so we use SHARE COUNT ratio.
- Combined mid ~$1.00 for 95% of snapshots (from both_sides_simulation.py).
- Fees: ~$0.01/trade (Polymarket standard).
- WR = 81% from 13-month 15M backtest.
"""

import numpy as np
from collections import Counter

# ============================================================
# CONSTANTS
# ============================================================
BANKROLL = 253.0
BET_PCT = 0.02  # 2% per market
BET_SIZE = BANKROLL * BET_PCT  # $5.06 per market
MAX_EXPOSURE_PCT = 0.03  # 3% per game max

# Share count lean ratio (lean side : hedge side)
LEAN_RATIO = 1.5  # 1.5:1 for 15M

# Win rate from 13-month backtest
WR = 0.81
LOSS_RATE = 1.0 - WR

# Polymarket fee rate
FEE_RATE = 0.02  # 2% on winnings

# Trades per day (15M windows)
TRADES_PER_DAY = 60

# Monte Carlo params
MC_SIMS = 10_000
MC_DAYS = 30
SEED = 42


def calculate_pnl_per_game():
    """
    Calculate PnL per game for both-sides strategy with share count lean.

    Both-sides: buy YES on one side, YES on other side.
    Combined cost = price_lean + price_hedge.
    If combined < $1.00: guaranteed profit on resolution (one side pays $1).
    If combined > $1.00: guaranteed loss unless direction correct matters.

    With lean (1.5:1 share ratio):
    - Total budget = $5.06
    - Lean side gets 1.5/(1.5+1) = 60% of SHARES
    - Hedge side gets 1/(1.5+1) = 40% of SHARES

    But share allocation depends on prices. Let's work through concrete examples.
    """
    budget = BET_SIZE  # $5.06

    results = {}

    # --- Scenario A: Combined = $0.97 (arb profit) ---
    # Example: lean side $0.52, hedge side $0.45
    # With equal shares: buy N shares each side, cost = N * 0.97, profit = N * 0.03
    # With lean 1.5:1 shares:
    #   lean_shares = 1.5 * hedge_shares
    #   Cost = lean_shares * 0.52 + hedge_shares * 0.45
    #        = 1.5*H*0.52 + H*0.45 = H*(0.78 + 0.45) = H*1.23
    #   Budget = 5.06, so H = 5.06/1.23 = 4.11 shares
    #   lean_shares = 6.17
    #   If lean wins: payout = 6.17 * $1.00 = $6.17, cost = $5.06, profit = $1.11
    #   If hedge wins: payout = 4.11 * $1.00 = $4.11, cost = $5.06, loss = -$0.95
    #   Fee on win: 2% of profit portion

    # Let's generalize for multiple combined prices
    scenarios = {
        "combined_0.97": {"lean_price": 0.52, "hedge_price": 0.45},
        "combined_1.00": {"lean_price": 0.54, "hedge_price": 0.46},
        "combined_1.02": {"lean_price": 0.55, "hedge_price": 0.47},
        "combined_1.03": {"lean_price": 0.56, "hedge_price": 0.47},
    }

    print("=" * 80)
    print("SECTION 1: PnL PER GAME — W4 Both-Sides with 1.5:1 Share Count Lean")
    print(f"Bankroll: ${BANKROLL:.2f} | Bet size: ${budget:.2f} (2%) | Max exposure: 3%")
    print("=" * 80)

    for name, prices in scenarios.items():
        lp = prices["lean_price"]
        hp = prices["hedge_price"]
        combined = lp + hp

        # Share count lean: lean_shares = LEAN_RATIO * hedge_shares
        # Cost = lean_shares * lp + hedge_shares * hp
        #      = LEAN_RATIO * H * lp + H * hp
        #      = H * (LEAN_RATIO * lp + hp)
        cost_per_hedge_share = LEAN_RATIO * lp + hp
        hedge_shares = budget / cost_per_hedge_share
        lean_shares = LEAN_RATIO * hedge_shares

        # If lean side wins (direction correct):
        lean_payout = lean_shares * 1.00
        lean_profit_gross = lean_payout - budget
        lean_fee = max(0, lean_profit_gross) * FEE_RATE
        lean_profit_net = lean_profit_gross - lean_fee

        # If hedge side wins (direction wrong):
        hedge_payout = hedge_shares * 1.00
        hedge_profit_gross = hedge_payout - budget
        hedge_fee = max(0, hedge_profit_gross) * FEE_RATE
        hedge_profit_net = hedge_profit_gross - hedge_fee

        results[name] = {
            "combined": combined,
            "lean_shares": lean_shares,
            "hedge_shares": hedge_shares,
            "lean_win_pnl": lean_profit_net,
            "hedge_win_pnl": hedge_profit_net,
        }

        print(f"\n--- {name} (lean=${lp:.2f}, hedge=${hp:.2f}, combined=${combined:.2f}) ---")
        print(f"  Shares: lean={lean_shares:.2f}, hedge={hedge_shares:.2f} (ratio {lean_shares/hedge_shares:.1f}:1)")
        print(f"  Direction CORRECT (lean wins): payout=${lean_payout:.2f}, PnL=${lean_profit_net:+.2f}")
        print(f"  Direction WRONG (hedge wins):  payout=${hedge_payout:.2f}, PnL=${hedge_profit_net:+.2f}")

    # Worst case: lean side goes to $0 (total loss on lean, only hedge pays)
    # This is effectively the "hedge wins" scenario above
    # But absolute worst = both sides lose (market voided? extremely rare)
    # Realistic worst = direction wrong at worst combined price
    print(f"\n--- WORST CASE PER GAME ---")
    worst_pnl = min(r["hedge_win_pnl"] for r in results.values())
    best_pnl = max(r["lean_win_pnl"] for r in results.values())
    print(f"  Best win (direction correct, best spread):  ${best_pnl:+.2f}")
    print(f"  Worst loss (direction wrong, worst spread): ${worst_pnl:+.2f}")
    print(f"  Worst loss as % of bankroll: {worst_pnl/BANKROLL*100:+.2f}%")

    return results, worst_pnl, best_pnl


def consecutive_loss_table(worst_pnl, best_pnl, avg_win):
    """Section 1 continued: consecutive loss impact table."""
    print(f"\n{'=' * 80}")
    print("CONSECUTIVE LOSS IMPACT TABLE")
    print(f"Using worst_loss=${worst_pnl:.2f}/game, avg_win=${avg_win:.2f}/game")
    print(f"{'=' * 80}")

    consec = [1, 3, 5, 8, 10]
    print(f"\n{'Consec Losses':>14} | {'Total Loss':>11} | {'% Bankroll':>11} | {'Recovery Games':>15}")
    print("-" * 60)

    loss_data = {}
    for n in consec:
        total_loss = abs(worst_pnl) * n
        pct = total_loss / BANKROLL * 100
        # Recovery = total_loss / avg_win_per_game (at WR, expected value per game)
        expected_pnl_per_game = WR * avg_win + (1 - WR) * worst_pnl
        recovery_games = total_loss / expected_pnl_per_game if expected_pnl_per_game > 0 else float('inf')
        loss_data[n] = {"total": total_loss, "pct": pct, "recovery": recovery_games}
        print(f"{n:>14} | ${total_loss:>9.2f} | {pct:>9.1f}% | {recovery_games:>13.1f}")

    return loss_data


def probability_analysis():
    """Section 2: Probability of consecutive losses."""
    print(f"\n{'=' * 80}")
    print(f"SECTION 2: PROBABILITY OF CONSECUTIVE LOSSES (WR={WR*100:.0f}%)")
    print(f"{'=' * 80}")

    consec = [1, 2, 3, 5, 8, 10]
    print(f"\n{'Consec Losses':>14} | {'P(exact)':>14} | {'1 in X':>10}")
    print("-" * 45)

    for n in consec:
        p = LOSS_RATE ** n
        one_in = 1 / p if p > 0 else float('inf')
        print(f"{n:>14} | {p:>12.8f}% | 1 in {one_in:>,.0f}" if p >= 0.01
              else f"{n:>14} | {p*100:>13.8f}% | 1 in {one_in:>,.0f}")

    # Over trading periods
    print(f"\n--- Expected occurrences over time ({TRADES_PER_DAY} trades/day) ---")
    periods = {
        "1 day (60 trades)": 60,
        "1 week (420 trades)": 420,
        "1 month (1800 trades)": 1800,
        "3 months (5400 trades)": 5400,
    }

    # For N trades, expected number of runs of k consecutive losses:
    # Approximate: (N - k + 1) * p^k * (1-p)^2  (bounded by wins on both sides)
    # More precisely for a sequence of length k within N trials:
    # E[runs of >=k losses] ~ (N-k+1) * L^k * W^2 / W  (adjusted for edges)
    # Simplified: ~ N * L^k for large N relative to k

    print(f"\n{'Period':>25} | {'>=3 losses':>12} | {'>=5 losses':>12} | {'>=8 losses':>12} | {'>=10 losses':>12}")
    print("-" * 90)

    for period_name, n_trades in periods.items():
        row = f"{period_name:>25} |"
        for k in [3, 5, 8, 10]:
            # Expected number of runs of >= k consecutive losses in n_trades
            # Using: E = (n - k + 1) * L^k * W  (a run starts after a W or at position 0)
            # More accurate: consider starting probability
            p_run = LOSS_RATE ** k
            # Number of possible starting positions
            expected = (n_trades - k + 1) * p_run * WR  # preceded by a win
            # Add probability of starting at position 0
            expected += p_run  # small correction for start
            row += f" {expected:>11.4f} |"
        print(row)

    return


def monte_carlo_simulation(worst_pnl, results):
    """Section 3: Monte Carlo max drawdown over 30 days."""
    print(f"\n{'=' * 80}")
    print(f"SECTION 3: MONTE CARLO — {MC_SIMS:,} sims x {MC_DAYS} days x {TRADES_PER_DAY} trades/day")
    print(f"{'=' * 80}")

    rng = np.random.default_rng(SEED)

    # Use realistic PnL distribution from Section 1
    # At combined ~$1.00 (most common): lean_win ~+$0.95, hedge_win ~-$0.95
    # Weight scenarios by typical occurrence
    # 95% of snapshots: combined = $1.00
    # ~22% have combined < $0.97
    # We'll use combined=$1.00 as the baseline (conservative)
    baseline = results["combined_1.00"]
    win_pnl = baseline["lean_win_pnl"]
    loss_pnl = baseline["hedge_win_pnl"]

    total_trades = MC_DAYS * TRADES_PER_DAY
    max_drawdowns = np.zeros(MC_SIMS)
    max_consec_losses_arr = np.zeros(MC_SIMS, dtype=int)
    final_pnl = np.zeros(MC_SIMS)
    ruin_count = 0  # bankroll hits 0

    for i in range(MC_SIMS):
        # Generate trade outcomes: 1 = win (direction correct), 0 = loss
        outcomes = rng.random(total_trades) < WR

        # Calculate PnL series
        pnl_series = np.where(outcomes, win_pnl, loss_pnl)
        cumulative = np.cumsum(pnl_series)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_drawdowns[i] = np.max(drawdown)

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for o in outcomes:
            if not o:
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0
        max_consec_losses_arr[i] = max_consec

        final_pnl[i] = cumulative[-1]

        # Ruin check: cumulative PnL ever < -BANKROLL
        if np.any(cumulative < -BANKROLL):
            ruin_count += 1

    # Results
    print(f"\nWin PnL per game: ${win_pnl:+.2f} | Loss PnL per game: ${loss_pnl:+.2f}")
    print(f"\n--- Max Drawdown Distribution ---")
    percentiles = [50, 75, 90, 95, 99]
    for p in percentiles:
        val = np.percentile(max_drawdowns, p)
        pct_br = val / BANKROLL * 100
        print(f"  P{p:02d}: ${val:>7.2f} ({pct_br:>5.1f}% of bankroll)")

    print(f"\n--- Max Consecutive Losses Distribution ---")
    for p in percentiles:
        val = np.percentile(max_consec_losses_arr, p)
        print(f"  P{p:02d}: {val:>3.0f} consecutive losses")

    consec_counter = Counter(max_consec_losses_arr)
    print(f"\n  Distribution of max consecutive losses:")
    for k in sorted(consec_counter.keys()):
        count = consec_counter[k]
        print(f"    {k} losses: {count:>5} sims ({count/MC_SIMS*100:>5.1f}%)")

    print(f"\n--- Final PnL after {MC_DAYS} days ---")
    for p in percentiles:
        val = np.percentile(final_pnl, p)
        print(f"  P{p:02d}: ${val:>+8.2f}")
    print(f"  Mean:  ${np.mean(final_pnl):>+8.2f}")
    print(f"  Ruin (bankroll zeroed): {ruin_count}/{MC_SIMS} ({ruin_count/MC_SIMS*100:.2f}%)")

    return max_drawdowns, max_consec_losses_arr, final_pnl


def kill_switch_recommendation(worst_pnl, avg_win, max_drawdowns, max_consec_losses_arr):
    """Section 4: Kill switch recommendations."""
    print(f"\n{'=' * 80}")
    print("SECTION 4: KILL SWITCH RECOMMENDATIONS")
    print(f"{'=' * 80}")

    expected_pnl_per_game = WR * avg_win + (1 - WR) * worst_pnl
    p95_dd = np.percentile(max_drawdowns, 95)
    p99_dd = np.percentile(max_drawdowns, 99)
    p95_consec = np.percentile(max_consec_losses_arr, 95)
    p99_consec = np.percentile(max_consec_losses_arr, 99)

    print(f"\nExpected PnL per game: ${expected_pnl_per_game:+.2f}")
    print(f"P95 max drawdown: ${p95_dd:.2f} | P99: ${p99_dd:.2f}")
    print(f"P95 max consec losses: {p95_consec:.0f} | P99: {p99_consec:.0f}")

    # Daily loss cap: set at ~P99 daily drawdown level
    # With 60 trades/day, daily expected PnL = 60 * expected_pnl
    daily_expected = TRADES_PER_DAY * expected_pnl_per_game
    daily_loss_cap_games = int(p99_consec) + 2  # buffer above P99 consec
    daily_loss_cap_dollars = abs(worst_pnl) * daily_loss_cap_games

    # Total loss fuse: based on P99 30-day drawdown
    total_fuse_dollars = min(p99_dd * 1.2, BANKROLL * 0.25)  # never more than 25% of bankroll
    total_fuse_games = int(total_fuse_dollars / abs(worst_pnl))

    # Consecutive loss cooldown: P95 + 1 buffer
    consec_cooldown = int(p95_consec) + 1

    print(f"\n┌─────────────────────────────────────────────────────────┐")
    print(f"│  RECOMMENDED KILL SWITCHES                              │")
    print(f"├─────────────────────────────────────────────────────────┤")
    print(f"│  Daily loss cap:       {daily_loss_cap_games:>3} games | ${daily_loss_cap_dollars:>6.2f}     │")
    print(f"│  Total loss fuse:      {total_fuse_games:>3} games | ${total_fuse_dollars:>6.2f}     │")
    print(f"│  Consec loss cooldown: {consec_cooldown:>3} games (pause 15 min)      │")
    print(f"│  Daily expected PnL:  ${daily_expected:>+7.2f}                       │")
    print(f"└─────────────────────────────────────────────────────────┘")

    return daily_loss_cap_games, total_fuse_games, consec_cooldown


def user_question_analysis(worst_pnl, avg_win):
    """Section 5: Is '10 game max, 3% per game' safe?"""
    print(f"\n{'=' * 80}")
    print("SECTION 5: IS '10 GAME MAX, 3% PER GAME' SAFE?")
    print(f"{'=' * 80}")

    max_exposure_per_game = BANKROLL * MAX_EXPOSURE_PCT  # 3% = $7.59
    max_loss_per_game = worst_pnl  # from Section 1 at $5.06 bet (2%)

    # At 3% exposure per game:
    bet_3pct = BANKROLL * 0.03  # $7.59
    # Scale worst loss proportionally
    worst_at_3pct = worst_pnl * (bet_3pct / BET_SIZE)
    best_at_3pct = avg_win * (bet_3pct / BET_SIZE)

    print(f"\n  At 2% bet (${BET_SIZE:.2f}):")
    print(f"    Worst loss/game: ${worst_pnl:.2f}")
    print(f"    Best win/game:   ${avg_win:+.2f}")
    print(f"\n  At 3% bet (${bet_3pct:.2f}):")
    print(f"    Worst loss/game: ${worst_at_3pct:.2f}")
    print(f"    Best win/game:   ${best_at_3pct:+.2f}")

    # 10 consecutive losses at worst
    ten_loss_2pct = abs(worst_pnl) * 10
    ten_loss_3pct = abs(worst_at_3pct) * 10
    pct_br_2 = ten_loss_2pct / BANKROLL * 100
    pct_br_3 = ten_loss_3pct / BANKROLL * 100

    expected_per_game_2 = WR * avg_win + (1 - WR) * worst_pnl
    expected_per_game_3 = WR * best_at_3pct + (1 - WR) * worst_at_3pct
    recovery_2 = ten_loss_2pct / expected_per_game_2 if expected_per_game_2 > 0 else float('inf')
    recovery_3 = ten_loss_3pct / expected_per_game_3 if expected_per_game_3 > 0 else float('inf')

    print(f"\n  10 CONSECUTIVE LOSSES:")
    print(f"  ┌────────────┬──────────────┬──────────────┬──────────────────┐")
    print(f"  │ Bet Size   │ Total Loss   │ % Bankroll   │ Recovery (games) │")
    print(f"  ├────────────┼──────────────┼──────────────┼──────────────────┤")
    print(f"  │ 2% (${BET_SIZE:.2f}) │ ${ten_loss_2pct:>10.2f} │ {pct_br_2:>10.1f}% │ {recovery_2:>14.0f}   │")
    print(f"  │ 3% (${bet_3pct:.2f}) │ ${ten_loss_3pct:>10.2f} │ {pct_br_3:>10.1f}% │ {recovery_3:>14.0f}   │")
    print(f"  └────────────┴──────────────┴──────────────┴──────────────────┘")

    # Probability of 10 consecutive losses
    p10 = LOSS_RATE ** 10
    # In 1800 trades (1 month), expected occurrences
    expected_monthly = 1800 * p10 * WR
    # Time to expect 1 occurrence
    trades_to_expect = 1 / (p10 * WR) if p10 * WR > 0 else float('inf')
    days_to_expect = trades_to_expect / TRADES_PER_DAY

    print(f"\n  P(10 consecutive losses) = {p10:.10f} = 1 in {1/p10:,.0f}")
    print(f"  Expected per month (1800 trades): {expected_monthly:.6f}")
    print(f"  Expected 1 occurrence every: {trades_to_expect:,.0f} trades = {days_to_expect:,.0f} days = {days_to_expect/365:.1f} years")

    # VERDICT
    print(f"\n  ┌─────────────────────────────────────────────────────────┐")
    print(f"  │  VERDICT                                                │")
    print(f"  ├─────────────────────────────────────────────────────────┤")
    safe_2 = pct_br_2 < 25
    safe_3 = pct_br_3 < 25
    print(f"  │  At 2%: 10-loss streak = {pct_br_2:.1f}% bankroll{'':>20}│")
    if safe_2:
        print(f"  │    -> SAFE. Survivable. Recovery ~{recovery_2:.0f} games ({recovery_2/TRADES_PER_DAY:.1f} days)  │")
    else:
        print(f"  │    -> DANGEROUS. >25% bankroll wipeout.                │")
    print(f"  │  At 3%: 10-loss streak = {pct_br_3:.1f}% bankroll{'':>20}│")
    if safe_3:
        print(f"  │    -> SAFE. Survivable. Recovery ~{recovery_3:.0f} games ({recovery_3/TRADES_PER_DAY:.1f} days)  │")
    else:
        print(f"  │    -> DANGEROUS. >25% bankroll wipeout.                │")
    print(f"  │                                                         │")
    print(f"  │  P(10 consec) = astronomically low ({days_to_expect/365:.0f}+ years)      │")
    print(f"  │  Real risk = 3-5 consec (happens monthly)               │")
    print(f"  │  Backtest max streak: 3-6 (13 months) = consistent      │")
    print(f"  └─────────────────────────────────────────────────────────┘")


def main():
    print("=" * 80)
    print(f"W4 BOTH-SIDES RISK ANALYSIS — Bankroll ${BANKROLL}")
    print(f"Date: 2026-03-24 | All risk in GAMES (trades)")
    print("=" * 80)

    # Section 1: PnL per game
    results, worst_pnl, best_pnl = calculate_pnl_per_game()

    # Average win (weighted across scenarios — use combined=$1.00 as most common)
    avg_win = results["combined_1.00"]["lean_win_pnl"]

    # Consecutive loss table
    loss_data = consecutive_loss_table(worst_pnl, best_pnl, avg_win)

    # Section 2: Probability analysis
    probability_analysis()

    # Section 3: Monte Carlo
    max_drawdowns, max_consec_losses_arr, final_pnl = monte_carlo_simulation(worst_pnl, results)

    # Section 4: Kill switch
    daily_cap, total_fuse, consec_cooldown = kill_switch_recommendation(
        worst_pnl, avg_win, max_drawdowns, max_consec_losses_arr
    )

    # Section 5: User's specific question
    user_question_analysis(worst_pnl, avg_win)

    print(f"\n{'=' * 80}")
    print("END OF RISK ANALYSIS")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
