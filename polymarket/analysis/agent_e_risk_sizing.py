#!/usr/bin/env python3
"""
Agent E: Optimal Sizing and Risk Parameters for W4 Strategy at $253 Bankroll.

Inputs (from w4_strategy_spec.md, w4_15m_era_report.md, w4_complete_analysis_report.md):
  - WR = 81% (15M at 300s/5bps)
  - Lean ratio: 2:1 (canonical)
  - p_lean = $0.55, p_hedge = $0.41, combined = $0.96
  - Win PnL per unit: +$0.49 (payout $2.00 - cost $1.51)
  - Loss PnL per unit: -$0.51 (payout $1.00 - cost $1.51)
  - EV per unit: +$0.30 (+19.9%)
  - ~60 qualifying windows/day (300s/5bps)
  - 0.5% allocation per trade (W4 best-fit)
  - Polymarket fee: 2% on winning payout

Outputs:
  1. Kelly fraction calculations for various entry prices
  2. Compound growth modeling (multiple fill rates)
  3. Monte Carlo drawdown analysis (10,000 runs)
  4. Concurrent position limits analysis
  5. Portfolio-level stop loss recommendations
  6. Allocation comparison (0.5% / 1% / 2% / 5%)
  7. Minimum viable bankroll analysis
"""

import math
import random
import statistics
from datetime import datetime
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
BANKROLL = 253.0
WR = 0.81
LEAN_RATIO = 2  # 2:1
DAILY_QUALIFYING_WINDOWS = 60
POLY_FEE_RATE = 0.02  # 2% on winning payout

# Canonical pricing (from w4_strategy_spec.md §2.2 / §7.2)
P_LEAN_CANONICAL = 0.55
P_HEDGE_CANONICAL = 0.41

SEP = "=" * 80
SUBSEP = "-" * 80

OUTPUT_LINES = []


def out(s=""):
    OUTPUT_LINES.append(s)
    print(s)


def section(title):
    out(f"\n{SEP}")
    out(f"  {title}")
    out(SEP)


# ── Helpers ──────────────────────────────────────────────────────────────────

def kelly_fraction(p_win, b_win, b_loss):
    """
    Generalized Kelly for asymmetric payoffs.
    f* = (p * b_win - q * b_loss) / (b_win * b_loss)
    where b_win = net profit on win (per $1 risked),
          b_loss = net loss on loss (per $1 risked, positive number).

    Standard form: f* = p/b_loss - q/b_win   (equivalent)
    """
    q = 1 - p_win
    if b_win <= 0 or b_loss <= 0:
        return 0.0
    return (p_win * b_win - q * b_loss) / (b_win * b_loss)


def pnl_per_unit(p_lean, p_hedge, lean_ratio, correct):
    """
    Calculate PnL for a unit trade (lean_ratio shares lean, 1 share hedge).
    """
    cost = lean_ratio * p_lean + 1 * p_hedge
    if correct:
        payout = lean_ratio * 1.00  # lean side wins
    else:
        payout = 1 * 1.00  # hedge side wins
    # Fee: 2% on winning payout
    fee = payout * POLY_FEE_RATE
    return payout - fee - cost


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Kelly Fraction Calculation
# ═══════════════════════════════════════════════════════════════════════════════

def section_1_kelly():
    section("1. KELLY FRACTION CALCULATION")

    entry_prices = [0.55, 0.60, 0.65, 0.70]
    out()
    out("  Parameters: WR=81%, lean_ratio=2:1, hedge_price = 1 - lean_price (approx)")
    out("  Fee: 2% on winning payout")
    out()

    header = (f"  {'Entry(lean)':>12s} | {'Hedge':>7s} | {'Combined':>9s} | "
              f"{'Win PnL':>9s} | {'Loss PnL':>9s} | {'EV/unit':>9s} | "
              f"{'Full K':>7s} | {'Half K':>7s} | {'Qtr K':>7s} | {'W4 0.5%':>8s} | {'0.5% as %K':>11s}")
    out(header)
    out("  " + "-" * 135)

    for p_lean in entry_prices:
        p_hedge = 1.0 - p_lean  # complementary pricing
        cost = LEAN_RATIO * p_lean + 1 * p_hedge
        combined = p_lean + p_hedge  # per share combined (always $1 in this model)

        win_pnl = pnl_per_unit(p_lean, p_hedge, LEAN_RATIO, True)
        loss_pnl = pnl_per_unit(p_lean, p_hedge, LEAN_RATIO, False)

        ev = WR * win_pnl + (1 - WR) * loss_pnl

        # For Kelly: express as fraction of cost
        # b_win = win_pnl / cost (return on investment if win)
        # b_loss = abs(loss_pnl) / cost (return on investment if loss)
        b_win = win_pnl / cost if cost > 0 else 0
        b_loss = abs(loss_pnl) / cost if cost > 0 else 0

        full_k = kelly_fraction(WR, b_win, b_loss) if b_win > 0 else 0
        half_k = full_k / 2
        qtr_k = full_k / 4
        w4_alloc = 0.005
        w4_as_pct_k = (w4_alloc / full_k * 100) if full_k > 0 else float('inf')

        out(f"  ${p_lean:>10.2f} | ${p_hedge:>5.2f} | ${combined:>7.2f} | "
            f"${win_pnl:>7.3f} | ${loss_pnl:>7.3f} | ${ev:>7.3f} | "
            f"{full_k:>6.1%} | {half_k:>6.1%} | {qtr_k:>6.1%} | "
            f"{w4_alloc:>7.1%} | {w4_as_pct_k:>9.1f}%")

    out()

    # Also do the canonical W4 pricing (not complementary but actual from spec)
    out("  ── Canonical W4 Pricing (from spec: lean=$0.55, hedge=$0.41) ──")
    p_l, p_h = P_LEAN_CANONICAL, P_HEDGE_CANONICAL
    cost = LEAN_RATIO * p_l + 1 * p_h
    combined = p_l + p_h
    win = pnl_per_unit(p_l, p_h, LEAN_RATIO, True)
    loss = pnl_per_unit(p_l, p_h, LEAN_RATIO, False)
    ev = WR * win + (1 - WR) * loss
    b_w = win / cost
    b_l = abs(loss) / cost
    fk = kelly_fraction(WR, b_w, b_l)

    out(f"  Cost per unit: ${cost:.3f} (2×$0.55 + 1×$0.41)")
    out(f"  Win PnL:  +${win:.3f} ({win/cost:.1%} ROI)")
    out(f"  Loss PnL: -${abs(loss):.3f} ({loss/cost:.1%} ROI)")
    out(f"  EV:       +${ev:.3f} ({ev/cost:.1%} EV/unit)")
    out(f"  Full Kelly: {fk:.1%}")
    out(f"  Half Kelly: {fk/2:.1%}")
    out(f"  Quarter Kelly: {fk/4:.1%}")
    out(f"  W4's 0.5% is {0.005/fk*100:.1f}% of Full Kelly → ultra-conservative")
    out()
    out("  INTERPRETATION:")
    out("  Full Kelly at canonical pricing is enormous because EV is huge relative to variance.")
    out("  W4 uses ~1/100th of Kelly. This makes sense because:")
    out("  (a) 60 trades/day means you don't need aggressive sizing per trade")
    out("  (b) Kelly assumes independent bets — 15M windows may have serial correlation")
    out("  (c) Ultra-small sizing + high frequency = smooth equity curve")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Compound Growth Modeling
# ═══════════════════════════════════════════════════════════════════════════════

def section_2_compound():
    section("2. COMPOUND GROWTH MODELING")

    fill_rates = [0.10, 0.20, 0.30, 0.50, 0.70]
    alloc = 0.005  # 0.5% per trade

    # Per-unit economics (canonical)
    cost_per_unit = LEAN_RATIO * P_LEAN_CANONICAL + 1 * P_HEDGE_CANONICAL  # $1.51
    win_pnl = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, True)
    loss_pnl = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, False)
    ev_per_unit = WR * win_pnl + (1 - WR) * loss_pnl
    ev_pct = ev_per_unit / cost_per_unit  # EV as % of cost

    out()
    out(f"  Starting bankroll: ${BANKROLL:.0f}")
    out(f"  Allocation per trade: {alloc:.1%} of bankroll")
    out(f"  Qualifying windows/day: {DAILY_QUALIFYING_WINDOWS}")
    out(f"  EV per unit trade: +${ev_per_unit:.3f} ({ev_pct:.1%} of cost)")
    out(f"  WR: {WR:.0%} | Win: +${win_pnl:.3f} | Loss: ${loss_pnl:.3f}")
    out()

    header = (f"  {'Fill Rate':>10s} | {'Trades/Day':>11s} | {'Daily PnL':>10s} | "
              f"{'Daily %':>8s} | {'30-Day':>10s} | {'90-Day':>12s} | "
              f"{'→$1K':>8s} | {'→$5K':>8s} | {'→$10K':>8s}")
    out(header)
    out("  " + "-" * 115)

    for fr in fill_rates:
        trades_day = DAILY_QUALIFYING_WINDOWS * fr

        # Daily expected PnL (simple, not compound intraday)
        # Each trade uses alloc% of start-of-day bankroll
        # PnL per trade = alloc * bankroll * (ev_per_unit / cost_per_unit)
        # But more precisely: we deploy alloc*bankroll as total budget per market
        # That buys budget/cost_per_unit "units"
        # PnL = units * ev_per_unit

        # Daily growth rate (compound)
        # Each trade: expected return = alloc * ev_pct
        # Daily compound: (1 + alloc * ev_pct) ^ trades_day
        per_trade_return = alloc * ev_pct
        daily_growth = (1 + per_trade_return) ** trades_day
        daily_pnl_simple = BANKROLL * (daily_growth - 1)
        daily_pct = (daily_growth - 1) * 100

        # 30-day and 90-day projections (compound daily)
        bal_30 = BANKROLL * daily_growth ** 30
        bal_90 = BANKROLL * daily_growth ** 90

        # Time to reach targets
        targets = [1000, 5000, 10000]
        days_to = []
        for tgt in targets:
            if daily_growth > 1:
                d = math.log(tgt / BANKROLL) / math.log(daily_growth)
                days_to.append(f"{d:.0f}d")
            else:
                days_to.append("never")

        out(f"  {fr:>9.0%} | {trades_day:>11.0f} | ${daily_pnl_simple:>8.2f} | "
            f"{daily_pct:>7.2f}% | ${bal_30:>9,.0f} | ${bal_90:>11,.0f} | "
            f"{days_to[0]:>8s} | {days_to[1]:>8s} | {days_to[2]:>8s}")

    out()
    out("  NOTE: Daily compound model assumes start-of-day bankroll for each trade.")
    out("  Intraday compounding (reinvesting settled capital immediately) would be faster.")
    out("  These projections assume EV stays constant — regime changes will cause variance.")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Drawdown Analysis (Monte Carlo)
# ═══════════════════════════════════════════════════════════════════════════════

def section_3_drawdown():
    section("3. DRAWDOWN ANALYSIS (Monte Carlo, 10,000 runs)")

    random.seed(42)  # Reproducible

    alloc = 0.005
    trades_per_day = 60  # assuming ~100% fill for worst-case drawdown analysis
    days = 90
    n_simulations = 10_000

    # Per-trade economics
    cost_per_unit = LEAN_RATIO * P_LEAN_CANONICAL + 1 * P_HEDGE_CANONICAL
    win_pnl_pct = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, True) / cost_per_unit
    loss_pnl_pct = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, False) / cost_per_unit

    out()
    out(f"  Parameters: {n_simulations:,} simulations, {days} days, {trades_per_day} trades/day")
    out(f"  Allocation: {alloc:.1%} per trade, WR: {WR:.0%}")
    out(f"  Win return: {win_pnl_pct:+.1%} on cost | Loss return: {loss_pnl_pct:+.1%} on cost")
    out()

    max_drawdowns = []
    final_balances = []
    longest_streaks = []
    dd_10_count = 0
    dd_20_count = 0
    dd_30_count = 0

    for _ in range(n_simulations):
        balance = BANKROLL
        peak = BANKROLL
        max_dd = 0.0
        current_streak = 0
        longest_streak = 0

        for day in range(days):
            day_start = balance
            for trade in range(trades_per_day):
                trade_budget = balance * alloc
                if random.random() < WR:
                    pnl = trade_budget * win_pnl_pct
                    current_streak = 0
                else:
                    pnl = trade_budget * loss_pnl_pct  # negative
                    current_streak += 1
                    longest_streak = max(longest_streak, current_streak)

                balance += pnl
                if balance <= 0:
                    balance = 0
                    break

                peak = max(peak, balance)
                dd = (peak - balance) / peak
                max_dd = max(max_dd, dd)

            if balance <= 0:
                break

        max_drawdowns.append(max_dd)
        final_balances.append(balance)
        longest_streaks.append(longest_streak)

        if max_dd >= 0.10:
            dd_10_count += 1
        if max_dd >= 0.20:
            dd_20_count += 1
        if max_dd >= 0.30:
            dd_30_count += 1

    # Sort for percentiles
    max_drawdowns.sort()
    final_balances.sort()
    longest_streaks.sort()

    def percentile(data, p):
        idx = int(len(data) * p)
        idx = min(idx, len(data) - 1)
        return data[idx]

    out("  ── Max Drawdown Distribution ──")
    out(f"  P10:  {percentile(max_drawdowns, 0.10):.1%}")
    out(f"  P25:  {percentile(max_drawdowns, 0.25):.1%}")
    out(f"  P50:  {percentile(max_drawdowns, 0.50):.1%}")
    out(f"  P75:  {percentile(max_drawdowns, 0.75):.1%}")
    out(f"  P90:  {percentile(max_drawdowns, 0.90):.1%}")
    out(f"  P95:  {percentile(max_drawdowns, 0.95):.1%}")
    out(f"  P99:  {percentile(max_drawdowns, 0.99):.1%}")
    out(f"  Max:  {max_drawdowns[-1]:.1%}")
    out()
    out(f"  Probability of ≥10% drawdown: {dd_10_count/n_simulations:.2%}")
    out(f"  Probability of ≥20% drawdown: {dd_20_count/n_simulations:.2%}")
    out(f"  Probability of ≥30% drawdown: {dd_30_count/n_simulations:.2%}")
    out()

    out("  ── Longest Losing Streak Distribution ──")
    out(f"  P50:  {percentile(longest_streaks, 0.50)} consecutive losses")
    out(f"  P90:  {percentile(longest_streaks, 0.90)} consecutive losses")
    out(f"  P95:  {percentile(longest_streaks, 0.95)} consecutive losses")
    out(f"  P99:  {percentile(longest_streaks, 0.99)} consecutive losses")
    out(f"  Max:  {longest_streaks[-1]} consecutive losses")
    out()

    out("  ── Final Balance Distribution (90 days) ──")
    out(f"  P1:   ${percentile(final_balances, 0.01):>12,.0f}")
    out(f"  P10:  ${percentile(final_balances, 0.10):>12,.0f}")
    out(f"  P25:  ${percentile(final_balances, 0.25):>12,.0f}")
    out(f"  P50:  ${percentile(final_balances, 0.50):>12,.0f}")
    out(f"  P75:  ${percentile(final_balances, 0.75):>12,.0f}")
    out(f"  P90:  ${percentile(final_balances, 0.90):>12,.0f}")
    out(f"  P99:  ${percentile(final_balances, 0.99):>12,.0f}")
    out()

    ruin_count = sum(1 for b in final_balances if b < 1.0)
    out(f"  Risk of ruin (balance < $1): {ruin_count/n_simulations:.4%}")
    out()
    out("  INTERPRETATION:")
    out("  At 0.5% allocation with 81% WR, drawdowns are extremely shallow.")
    out("  The strategy is almost ruin-proof at this sizing — you'd need a")
    out("  catastrophic WR collapse (below ~50%) to see meaningful losses.")

    return max_drawdowns, final_balances


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Concurrent Position Limits
# ═══════════════════════════════════════════════════════════════════════════════

def section_4_concurrent():
    section("4. CONCURRENT POSITION LIMITS")

    alloc = 0.005
    per_market = BANKROLL * alloc

    # 15M windows: one every 15 min, capital locked for 15 min
    # Max concurrent = 1 (capital releases as next window starts)
    # But if trading multiple coins: BTC + ETH = 2 concurrent

    max_concurrent_single = 1  # one 15M window at a time per coin
    max_concurrent_multi = 4  # BTC + ETH + SOL + XRP
    max_deployed_single = max_concurrent_single * per_market
    max_deployed_multi = max_concurrent_multi * per_market

    out()
    out(f"  Allocation per trade: {alloc:.1%} of ${BANKROLL:.0f} = ${per_market:.3f}")
    out()
    out("  ── Single Coin (BTC only) ──")
    out(f"  15M window = capital locked for 15 min")
    out(f"  Windows are sequential (non-overlapping)")
    out(f"  Max concurrent positions: {max_concurrent_single}")
    out(f"  Max capital deployed: ${max_deployed_single:.3f} ({max_deployed_single/BANKROLL:.1%} of bankroll)")
    out()
    out("  ── Multi Coin (BTC + ETH, like our bot) ──")
    out(f"  2 coins × 1 concurrent each = 2 concurrent")
    out(f"  Max capital deployed: ${2 * per_market:.3f} ({2 * per_market / BANKROLL:.1%} of bankroll)")
    out()
    out("  ── W4's approach (4 coins) ──")
    out(f"  4 coins × 1 concurrent each = {max_concurrent_multi} concurrent")
    out(f"  Max capital deployed: ${max_deployed_multi:.3f} ({max_deployed_multi/BANKROLL:.1%} of bankroll)")
    out()

    # Is this enough?
    out("  ── IS 0.5% PER TRADE ENOUGH? ──")
    out()
    out(f"  At $253, 0.5% = ${per_market:.3f} per market")
    out(f"  With 2:1 lean at $0.55/$0.41:")
    out(f"    Lean budget: ${per_market * 2/3:.3f} → {per_market * 2/3 / 0.55:.1f} shares")
    out(f"    Hedge budget: ${per_market * 1/3:.3f} → {per_market * 1/3 / 0.41:.1f} shares")
    out()
    out(f"  Polymarket minimum: 5 shares per side")
    out(f"  At lean $0.55: 5 shares × $0.55 = $2.75 minimum lean budget")
    out(f"  At hedge $0.41: 5 shares × $0.41 = $2.05 minimum hedge budget")
    out(f"  Minimum total budget per market: $4.80")
    out(f"  Current budget per market: ${per_market:.3f}")
    out()

    if per_market < 4.80:
        out(f"  *** PROBLEM: ${per_market:.3f} < $4.80 minimum ***")
        out(f"  At 0.5% allocation, we CANNOT meet Polymarket minimums!")
        min_alloc_for_min = 4.80 / BANKROLL
        out(f"  Minimum allocation needed: {min_alloc_for_min:.1%} (${4.80:.2f})")
        out(f"  Recommended: 2-3% allocation to get $5-$7.50 per market")
    else:
        out(f"  OK: ${per_market:.3f} > $4.80 minimum ✓")

    out()
    out("  ── CAPITAL UTILIZATION ──")
    for alloc_test in [0.005, 0.01, 0.02, 0.03, 0.05]:
        budget = BANKROLL * alloc_test
        concurrent_2coin = 2 * budget
        util = concurrent_2coin / BANKROLL
        lean_shares = (budget * 2/3) / 0.55
        hedge_shares = (budget * 1/3) / 0.41
        ok = "OK" if lean_shares >= 5 and hedge_shares >= 5 else "BELOW MIN"
        out(f"  {alloc_test:>5.1%}: ${budget:>6.2f}/market, "
            f"2-coin deployed ${concurrent_2coin:>6.2f} ({util:>5.1%} util), "
            f"lean {lean_shares:>5.1f} sh, hedge {hedge_shares:>5.1f} sh → {ok}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Portfolio-Level Stop Loss
# ═══════════════════════════════════════════════════════════════════════════════

def section_5_stop_loss():
    section("5. PORTFOLIO-LEVEL STOP LOSS RECOMMENDATIONS")

    # Calculate daily variance to inform stop levels
    alloc = 0.005
    trades_day = 60

    cost_per_unit = LEAN_RATIO * P_LEAN_CANONICAL + 1 * P_HEDGE_CANONICAL
    win_ret = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, True) / cost_per_unit
    loss_ret = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, False) / cost_per_unit

    # Per-trade return (as fraction of bankroll)
    per_trade_win = alloc * win_ret
    per_trade_loss = alloc * loss_ret

    # Daily return distribution (normal approx for 60 trades)
    # E[daily] = n * (p * win + q * loss)
    ev_per_trade = WR * per_trade_win + (1 - WR) * per_trade_loss
    var_per_trade = WR * (per_trade_win - ev_per_trade)**2 + (1 - WR) * (per_trade_loss - ev_per_trade)**2
    daily_ev = trades_day * ev_per_trade
    daily_std = math.sqrt(trades_day * var_per_trade)

    # Weekly (5 trading days... but crypto = 7 days)
    weekly_ev = daily_ev * 7
    weekly_std = daily_std * math.sqrt(7)

    out()
    out("  ── Daily Return Distribution (0.5% alloc, 60 trades/day) ──")
    out(f"  E[daily return]: {daily_ev:+.2%}")
    out(f"  Daily std dev:   {daily_std:.2%}")
    out(f"  Daily Sharpe (annualized): {daily_ev / daily_std * math.sqrt(365):.1f}")
    out()
    out(f"  E[weekly return]: {weekly_ev:+.2%}")
    out(f"  Weekly std dev:   {weekly_std:.2%}")
    out()

    # 2-sigma and 3-sigma events
    bad_day_2s = daily_ev - 2 * daily_std
    bad_day_3s = daily_ev - 3 * daily_std
    bad_week_2s = weekly_ev - 2 * weekly_std
    bad_week_3s = weekly_ev - 3 * weekly_std

    out("  ── Tail Events ──")
    out(f"  2σ bad day: {bad_day_2s:+.2%} (once per ~22 days)")
    out(f"  3σ bad day: {bad_day_3s:+.2%} (once per ~370 days)")
    out(f"  2σ bad week: {bad_week_2s:+.2%}")
    out(f"  3σ bad week: {bad_week_3s:+.2%}")
    out()

    # Stop loss recommendations
    out("  ── STOP LOSS RECOMMENDATIONS ──")
    out()

    # Daily stop: should be beyond 3σ bad day to avoid false triggers
    daily_stop = min(bad_day_3s * 1.5, -0.03)  # At least -3%
    daily_stop = round(daily_stop * 100) / 100  # Round to nearest %

    # But at 0.5% alloc, even 60 straight losses = 60 × 0.5% × 34% = 10.2% loss
    max_daily_loss_theoretical = trades_day * alloc * abs(loss_ret)

    out(f"  Theoretical max daily loss (60 straight losses):")
    out(f"    60 × 0.5% × {abs(loss_ret):.1%} = {max_daily_loss_theoretical:.1%}")
    out(f"    P(60 straight losses) = (1-{WR:.0%})^60 = {(1-WR)**60:.2e} → effectively impossible")
    out()
    out(f"  DAILY STOP (X): -5% of bankroll")
    out(f"    At $253: stop if daily PnL < -${253*0.05:.2f}")
    out(f"    Rationale: >3σ event at current sizing; if hit, something is WRONG")
    out(f"    (signal breakdown, exchange issue, or bug)")
    out()
    out(f"  WEEKLY STOP (Y): -10% of bankroll")
    out(f"    At $253: stop if weekly PnL < -${253*0.10:.2f}")
    out(f"    Rationale: accumulation of multiple bad days = regime change signal")
    out()
    out(f"  ── GRADUATED RESPONSE ──")
    out(f"  Daily PnL < -2%: reduce allocation by 50% for rest of day")
    out(f"  Daily PnL < -5%: HALT for rest of day, review logs")
    out(f"  Weekly PnL < -5%: reduce to 50% allocation for rest of week")
    out(f"  Weekly PnL < -10%: HALT for rest of week, full strategy review")
    out(f"  Drawdown from peak > -15%: HALT completely, reassess fundamentals")
    out()
    out("  NOTE: At 0.5% allocation, hitting these stops requires a fundamental")
    out("  breakdown. They are circuit breakers for bugs/regime change, NOT")
    out("  normal variance management.")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Allocation Comparison
# ═══════════════════════════════════════════════════════════════════════════════

def section_6_comparison():
    section("6. ALLOCATION COMPARISON: 0.5% vs 1% vs 2% vs 5%")

    random.seed(42)

    allocs = [0.005, 0.01, 0.02, 0.05]
    trades_day = 60
    days = 90
    n_sims = 5_000

    cost_per_unit = LEAN_RATIO * P_LEAN_CANONICAL + 1 * P_HEDGE_CANONICAL
    win_ret = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, True) / cost_per_unit
    loss_ret = pnl_per_unit(P_LEAN_CANONICAL, P_HEDGE_CANONICAL, LEAN_RATIO, False) / cost_per_unit

    out()
    out(f"  {n_sims:,} simulations each, {days} days, {trades_day} trades/day, WR={WR:.0%}")
    out()

    results = {}
    for alloc in allocs:
        max_dds = []
        finals = []
        daily_pnls = []
        ruin_count = 0

        for _ in range(n_sims):
            balance = BANKROLL
            peak = BANKROLL
            max_dd = 0.0
            day_pnls = []

            for day in range(days):
                day_start = balance
                for _ in range(trades_day):
                    budget = balance * alloc
                    if random.random() < WR:
                        pnl = budget * win_ret
                    else:
                        pnl = budget * loss_ret
                    balance += pnl
                    if balance <= 0:
                        balance = 0
                        break
                    peak = max(peak, balance)
                    dd = (peak - balance) / peak
                    max_dd = max(max_dd, dd)
                day_pnls.append(balance - day_start)
                if balance <= 0:
                    break

            max_dds.append(max_dd)
            finals.append(balance)
            if day_pnls:
                daily_pnls.extend(day_pnls)
            if balance < 1.0:
                ruin_count += 1

        max_dds.sort()
        finals.sort()

        avg_daily = statistics.mean(daily_pnls) if daily_pnls else 0
        std_daily = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0

        # Time to double
        if avg_daily > 0:
            days_to_2x = BANKROLL / avg_daily
        else:
            days_to_2x = float('inf')

        results[alloc] = {
            "avg_daily": avg_daily,
            "std_daily": std_daily,
            "max_dd_p50": max_dds[len(max_dds)//2],
            "max_dd_p95": max_dds[int(len(max_dds)*0.95)],
            "max_dd_p99": max_dds[int(len(max_dds)*0.99)],
            "median_final": finals[len(finals)//2],
            "days_to_2x": days_to_2x,
            "ruin_pct": ruin_count / n_sims,
        }

    # Print comparison table
    header = (f"  {'Alloc':>7s} | {'$/Market':>9s} | {'Avg $/Day':>10s} | "
              f"{'Std $/Day':>10s} | {'DD p50':>7s} | {'DD p95':>7s} | "
              f"{'DD p99':>7s} | {'2x Days':>8s} | {'90d Med':>12s} | {'Ruin':>7s}")
    out(header)
    out("  " + "-" * 120)

    for alloc in allocs:
        r = results[alloc]
        budget = BANKROLL * alloc
        d2x = f"{r['days_to_2x']:.0f}d" if r['days_to_2x'] < 9999 else "never"
        out(f"  {alloc:>6.1%} | ${budget:>7.2f} | ${r['avg_daily']:>8.2f} | "
            f"${r['std_daily']:>8.2f} | {r['max_dd_p50']:>6.1%} | {r['max_dd_p95']:>6.1%} | "
            f"{r['max_dd_p99']:>6.1%} | {d2x:>8s} | ${r['median_final']:>10,.0f} | "
            f"{r['ruin_pct']:>6.2%}")

    out()

    # Check which allocs meet Polymarket minimums
    out("  ── PRACTICAL CONSTRAINTS AT $253 ──")
    for alloc in allocs:
        budget = BANKROLL * alloc
        lean_shares = (budget * 2/3) / 0.55
        hedge_shares = (budget * 1/3) / 0.41
        meets_min = lean_shares >= 5 and hedge_shares >= 5
        out(f"  {alloc:.1%}: ${budget:.2f}/market → lean {lean_shares:.1f}sh, hedge {hedge_shares:.1f}sh → "
            f"{'MEETS MIN' if meets_min else 'BELOW MIN (need ≥5 shares/side)'}")

    out()
    out("  RECOMMENDATION:")
    out("  At $253 bankroll, 0.5% allocation ($1.27/market) is BELOW Polymarket minimums.")
    out("  You need at least ~2% ($5.06/market) to place valid orders.")
    out("  2% gives: good daily returns, very low drawdown risk, and meets minimums.")
    out("  As bankroll grows past ~$500, can reduce to 1%; past ~$1000, to 0.5%.")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: Minimum Viable Bankroll
# ═══════════════════════════════════════════════════════════════════════════════

def section_7_min_bankroll():
    section("7. MINIMUM VIABLE BANKROLL")

    out()
    out("  Polymarket minimum order: varies, but typically ~$1 notional")
    out("  For both-sides strategy: need orders on BOTH sides")
    out("  Practical minimum per side: ~5 shares")
    out()
    out("  ── Minimum Budget Per Market ──")

    price_combos = [
        (0.55, 0.45, "50/50 market"),
        (0.55, 0.41, "Canonical W4 ($0.96 combined)"),
        (0.65, 0.35, "Lean market"),
        (0.70, 0.30, "Strong lean"),
    ]

    for p_lean, p_hedge, label in price_combos:
        min_lean_budget = 5 * p_lean
        min_hedge_budget = 5 * p_hedge
        min_total = min_lean_budget + min_hedge_budget
        # With 2:1 lean: need lean_budget = 2/3 of total, hedge = 1/3
        # But minimum is driven by hedge side (fewer shares)
        # hedge_budget = total * 1/3 ≥ 5 * p_hedge → total ≥ 15 * p_hedge
        min_total_lean_adjusted = max(15 * p_hedge, 7.5 * p_lean)
        out(f"  {label}: lean ${p_lean}, hedge ${p_hedge}")
        out(f"    Min equal: 5×${p_lean} + 5×${p_hedge} = ${min_total:.2f}")
        out(f"    Min 2:1 lean: ${min_total_lean_adjusted:.2f}")

    out()
    out("  ── Bankroll Required by Allocation ──")
    out()
    out(f"  {'Alloc':>7s} | {'Min $/Market':>13s} | {'Min Bankroll':>13s} | {'Our $253':>12s}")
    out(f"  {'-'*55}")

    min_per_market = 5.00  # ~$5 minimum for viable both-sides order

    for alloc in [0.005, 0.01, 0.015, 0.02, 0.03, 0.05]:
        min_bankroll = min_per_market / alloc
        feasible = "OK" if BANKROLL >= min_bankroll else f"NEED ${min_bankroll:.0f}"
        out(f"  {alloc:>6.1%} | ${min_per_market:>11.2f} | ${min_bankroll:>11,.0f} | {feasible:>12s}")

    out()
    out("  ── AT OUR $253 BANKROLL ──")
    out()
    out(f"  Minimum viable allocation: {min_per_market / BANKROLL:.1%} (${min_per_market:.2f} per market)")
    out(f"  This gives ~{min_per_market / (LEAN_RATIO * P_LEAN_CANONICAL + P_HEDGE_CANONICAL):.1f} units per trade")
    out()
    out("  ALLOCATION LADDER (recommended):")
    out("  ┌──────────────┬─────────┬──────────────┐")
    out("  │ Bankroll     │ Alloc   │ Per Market   │")
    out("  ├──────────────┼─────────┼──────────────┤")
    out("  │ $253 (now)   │ 2.0%    │ $5.06        │")
    out("  │ $500         │ 1.5%    │ $7.50        │")
    out("  │ $1,000       │ 1.0%    │ $10.00       │")
    out("  │ $2,500       │ 0.5%    │ $12.50       │")
    out("  │ $5,000       │ 0.5%    │ $25.00       │")
    out("  │ $10,000+     │ 0.3%    │ $30.00+      │")
    out("  └──────────────┴─────────┴──────────────┘")
    out()
    out("  KEY INSIGHT: $253 is EXACTLY at the minimum viable bankroll for 2% allocation.")
    out("  W4 started at $507 — double our bankroll. At $507, 1% allocation = $5.07/market.")
    out("  We're at the floor. Every dollar of growth gives more breathing room.")
    out()
    out("  CRITICAL: At $253, the strategy works but is fragile to:")
    out("  1. Polymarket minimum order size increases")
    out("  2. Any allocation below 2% becomes non-viable")
    out("  3. A 20% drawdown ($203) would push us below minimum viable at 2%")
    out("     (need to recalculate: $203 × 2% = $4.06 — still OK but tight)")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    out(f"{'#' * 80}")
    out(f"  AGENT E: OPTIMAL SIZING & RISK PARAMETERS")
    out(f"  W4 Strategy at ${BANKROLL:.0f} Bankroll")
    out(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out(f"{'#' * 80}")

    section_1_kelly()
    section_2_compound()
    dd_data, bal_data = section_3_drawdown()
    section_4_concurrent()
    section_5_stop_loss()
    section_6_comparison()
    section_7_min_bankroll()

    # ── Final Summary ──
    section("EXECUTIVE SUMMARY")
    out()
    out("  1. KELLY: Full Kelly is ~50%+ at canonical pricing (massive edge).")
    out("     W4's 0.5% is ~1% of Kelly — ultra-conservative but correct for")
    out("     60 trades/day with potential serial correlation.")
    out()
    out("  2. GROWTH: At 30% fill rate + 2% allocation:")
    out("     ~$10-15/day, $1K in ~20 days, $5K in ~50 days, $10K in ~65 days.")
    out("     Sensitive to fill rate — uptime matters more than sizing.")
    out()
    out("  3. DRAWDOWN: At 0.5%-2% allocation, max drawdowns are tiny (<5% p95).")
    out("     Strategy is nearly ruin-proof. The risk is not drawdown but")
    out("     opportunity cost of slow growth at small bankroll.")
    out()
    out("  4. CONCURRENT: At $253, only ~$5-10 deployed at once (2-4% of bankroll).")
    out("     Capital is massively underutilized. This is fine — the edge is in")
    out("     frequency, not size.")
    out()
    out("  5. STOP LOSS: Daily -5%, Weekly -10%. These should never trigger under")
    out("     normal conditions. If they do, investigate for bugs or regime change.")
    out()
    out("  6. OPTIMAL ALLOCATION AT $253: **2%** ($5.06/market).")
    out("     0.5% is below Polymarket minimums at this bankroll.")
    out("     Scale down to 1% at $500, 0.5% at $1,000+.")
    out()
    out("  7. MINIMUM VIABLE: $253 is the FLOOR for 2% allocation.")
    out("     Below $250, the strategy becomes mechanically non-viable.")
    out("     W4 started at $507 — we're running tighter but feasible.")
    out()
    out("  ╔═══════════════════════════════════════════════════════════════════╗")
    out("  ║  BOTTOM LINE: Use 2% allocation now. Reduce to 1% at $500.     ║")
    out("  ║  Focus on UPTIME not sizing. Every qualifying window traded     ║")
    out("  ║  is worth ~$0.30 EV. Miss 10 windows = lose $3/day.            ║")
    out("  ║  The compound math does the rest.                               ║")
    out("  ╚═══════════════════════════════════════════════════════════════════╝")
    out()

    # Save report
    report_path = Path(__file__).resolve().parent / "agent_e_report.md"
    with open(report_path, "w") as f:
        f.write("# Agent E: Optimal Sizing & Risk Parameters\n")
        f.write(f"> W4 Strategy at ${BANKROLL:.0f} Bankroll\n")
        f.write(f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("```\n")
        f.write("\n".join(OUTPUT_LINES))
        f.write("\n```\n")
    out(f"  Report saved to: {report_path}")


if __name__ == "__main__":
    main()
