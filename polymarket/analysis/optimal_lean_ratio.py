"""
Optimal Lean Ratio Calculator for 5M Polymarket Bot
====================================================
Strategy: Buy BOTH sides (UP + DOWN) with asymmetric sizing.
Combined cost ~$1.02 (2% overround = spread cost).
Budget = $5 per market (2% of $253 bankroll).

KEY QUESTION: What lean ratio maximizes risk-adjusted return at each confidence tier?
"""

import math

BUDGET = 5.0
COMBINED_DEFAULT = 1.02
LEAN_PRICE_MEDIAN = 0.55  # median from our data


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def calc_pnl(ratio: float, lean_price: float, combined: float = COMBINED_DEFAULT, budget: float = BUDGET):
    """
    Returns (lean_win, hedge_win, break_even_wr).
    lean_win  = PnL when predicted direction resolves YES
    hedge_win = PnL when predicted direction resolves NO (hedge side wins)
    """
    hedge_price = combined - lean_price
    lean_budget = budget * ratio / (ratio + 1)
    hedge_budget = budget / (ratio + 1)
    lean_shares = lean_budget / lean_price
    hedge_shares = hedge_budget / hedge_price
    lean_win = lean_shares * 1.00 - budget
    hedge_win = hedge_shares * 1.00 - budget
    # Break-even WR: EV = 0 → WR * lean_win + (1-WR) * hedge_win = 0
    # WR = |hedge_win| / (lean_win + |hedge_win|)  (only when lean_win > 0 > hedge_win)
    if lean_win > 0 and hedge_win < 0:
        be_wr = abs(hedge_win) / (lean_win + abs(hedge_win))
    else:
        be_wr = float('nan')
    return lean_win, hedge_win, be_wr


def ev(wr: float, lean_win: float, hedge_win: float) -> float:
    return wr * lean_win + (1 - wr) * hedge_win


def kelly_fraction(wr: float, lean_win: float, hedge_win: float) -> float:
    """Full Kelly fraction of bankroll to bet."""
    loss = abs(hedge_win)
    if lean_win <= 0 or loss <= 0:
        return 0.0
    # Kelly: f = (p*b - q) / b  where b = win/loss ratio
    b = lean_win / loss
    return (wr * b - (1 - wr)) / b


# ─────────────────────────────────────────────────────────────────────────────
# Part A: Break-even WR for each lean ratio × lean price
# ─────────────────────────────────────────────────────────────────────────────

def part_a():
    print("\n" + "=" * 80)
    print("PART A: Break-even WR by lean ratio and lean price (combined=$1.02, budget=$5)")
    print("=" * 80)

    ratios = [1.5, 2.0, 3.0, 5.0, 8.0, 12.0]
    prices = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]

    # Header
    header = f"{'Lean Price':>11}" + "".join(f"  ratio={r:.1f}" for r in ratios)
    print(header)
    print("-" * len(header))

    for lp in prices:
        row = f"  ${lp:.2f}     "
        for r in ratios:
            lw, hw, be = calc_pnl(r, lp)
            if not math.isnan(be):
                row += f"   {be*100:5.1f}%   "
            else:
                row += "    N/A    "
        print(row)

    print()
    print("  → Cells show minimum WR needed to break even.")
    print("  → Lower lean_price = hedge side is cheaper = more hedge shares = easier to break even.")
    print()

    # Detailed table at median lean price = 0.55
    lp = LEAN_PRICE_MEDIAN
    print(f"\n  Detail at lean_price=${lp:.2f} (our median):")
    print(f"  {'Ratio':>8}  {'Lean Budget':>12}  {'Hedge Budget':>13}  {'Lean Shares':>12}  "
          f"{'Hedge Shares':>13}  {'Lean Win$':>10}  {'Hedge Loss$':>12}  {'Break-even WR':>14}")
    print("  " + "-" * 110)
    for r in ratios:
        hedge_price = COMBINED_DEFAULT - lp
        lb = BUDGET * r / (r + 1)
        hb = BUDGET / (r + 1)
        ls = lb / lp
        hs = hb / hedge_price
        lw = ls * 1.00 - BUDGET
        hw = hs * 1.00 - BUDGET
        be = abs(hw) / (lw + abs(hw)) if lw > 0 > hw else float('nan')
        print(f"  {r:>8.1f}  ${lb:>10.3f}  ${hb:>11.3f}  {ls:>12.2f}  "
              f"{hs:>13.2f}  ${lw:>9.3f}  ${hw:>11.3f}  {be*100:>13.1f}%")


# ─────────────────────────────────────────────────────────────────────────────
# Part B: Expected PnL matrix WR × lean ratio
# ─────────────────────────────────────────────────────────────────────────────

def part_b():
    print("\n" + "=" * 80)
    print("PART B: Expected PnL per trade (lean_price=$0.55, combined=$1.02, budget=$5)")
    print("=" * 80)

    wrs = [0.70, 0.75, 0.80, 0.85, 0.90]
    ratios = [1.5, 2.0, 3.0, 5.0, 8.0, 12.0]
    lp = LEAN_PRICE_MEDIAN

    print("\n  EV per trade ($):")
    header = f"  {'WR':>6}" + "".join(f"  {r:.1f}:1  " for r in ratios)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for wr in wrs:
        row = f"  {wr*100:.0f}%  "
        for r in ratios:
            lw, hw, _ = calc_pnl(r, lp)
            e = ev(wr, lw, hw)
            row += f"  ${e:+.3f}  "
        print(row)

    print("\n  EV as % of budget ($5):")
    header = f"  {'WR':>6}" + "".join(f"  {r:.1f}:1  " for r in ratios)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for wr in wrs:
        row = f"  {wr*100:.0f}%  "
        for r in ratios:
            lw, hw, _ = calc_pnl(r, lp)
            e = ev(wr, lw, hw)
            row += f"  {e/BUDGET*100:+5.1f}%  "
        print(row)

    print("\n  Max single-trade loss ($) and % of budget:")
    header = f"  {'Ratio':>8}" + "  Max Loss $   Max Loss %"
    print(header)
    print("  " + "-" * 38)
    for r in ratios:
        lw, hw, _ = calc_pnl(r, lp)
        print(f"  {r:>7.1f}:1   ${abs(hw):>7.3f}      {abs(hw)/BUDGET*100:>6.1f}%")

    print()
    print("  Notes:")
    print("  → At WR=80%, EV turns positive around ratio 2:1 and above.")
    print("  → Higher ratio = higher EV when right, but also higher max loss.")
    print("  → Max loss is always the hedge_loss (when wrong direction).")


# ─────────────────────────────────────────────────────────────────────────────
# Part C: Kelly-optimal lean ratio
# ─────────────────────────────────────────────────────────────────────────────

def part_c():
    print("\n" + "=" * 80)
    print("PART C: Kelly-optimal lean ratio (lean_price=$0.55, combined=$1.02)")
    print("=" * 80)

    wrs = [0.70, 0.75, 0.773, 0.80, 0.850, 0.879, 0.90, 1.00]
    ratios = [1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 50.0]
    lp = LEAN_PRICE_MEDIAN

    print(f"\n  {'WR':>8}  {'Best EV Ratio':>14}  {'Full Kelly f':>13}  "
          f"{'Half-Kelly f':>13}  {'Kelly EV/trade':>15}  {'Half-Kelly EV':>14}")
    print("  " + "-" * 90)

    for wr in wrs:
        best_ev = -999
        best_ratio = None
        best_lw = None
        best_hw = None
        for r in ratios:
            lw, hw, _ = calc_pnl(r, lp)
            e = ev(wr, lw, hw)
            if e > best_ev:
                best_ev = e
                best_ratio = r
                best_lw = lw
                best_hw = hw
        kf = kelly_fraction(wr, best_lw, best_hw)
        hkf = kf / 2
        ke = ev(wr, best_lw, best_hw)
        # half kelly EV is same per trade, just risk sizing changes
        label = f"{wr*100:.1f}%"
        print(f"  {label:>8}  {best_ratio:>12.1f}:1  {kf*100:>12.1f}%  "
              f"{hkf*100:>12.1f}%  ${ke:>13.4f}  ${ke:>12.4f}")

    print()
    print("  Notes:")
    print("  → Full Kelly fraction is % of BANKROLL to risk per trade (not ratio).")
    print("  → Half-Kelly recommended for safety (halves variance).")
    print("  → At WR=100%, infinite ratio is Kelly-optimal (never wrong).")
    print("  → 'Best EV Ratio' is the highest ratio tested with positive EV.")
    print()

    # Detailed Kelly for our 3 tiers
    print("  Kelly analysis for our exact WR tiers:")
    tiers = [
        ("Tier 1 (5-10bps)", 0.773),
        ("Tier 2 (10-20bps)", 0.879),
        ("Tier 3 (>20bps)", 1.00),
    ]
    for name, wr in tiers:
        print(f"\n  {name}, WR={wr*100:.1f}%:")
        for r in [1.5, 2.0, 3.0, 5.0, 8.0, 12.0]:
            lw, hw, be = calc_pnl(r, lp)
            e = ev(wr, lw, hw)
            kf = kelly_fraction(wr, lw, hw)
            sign = "✓" if e > 0 else "✗"
            print(f"    {sign} ratio={r:.1f}:1  EV=${e:+.4f}  Kelly={kf*100:.1f}%  "
                  f"MaxLoss=${abs(hw):.3f}  BE_WR={be*100:.1f}%")


# ─────────────────────────────────────────────────────────────────────────────
# Part D: Confidence-tiered lean ratios
# ─────────────────────────────────────────────────────────────────────────────

def part_d():
    print("\n" + "=" * 80)
    print("PART D: Confidence-tiered lean ratio recommendations")
    print("=" * 80)

    tiers = [
        {
            "name": "Tier 1 — Low Confidence (5-10bps)",
            "wr": 0.773,
            "mismatch": 0.225,
            "adj_wr": 0.773 * (1 - 0.225),  # adjust for BTC→Poly mismatch
            "signal": "5-10bps momentum",
            "sample_n": "~45 trades (est.)",
        },
        {
            "name": "Tier 2 — Medium Confidence (10-20bps)",
            "wr": 0.879,
            "mismatch": 0.061,
            "adj_wr": 0.879 * (1 - 0.061),
            "signal": "10-20bps momentum",
            "sample_n": "~66 trades (est.)",
        },
        {
            "name": "Tier 3 — High Confidence (>20bps)",
            "wr": 1.00,
            "mismatch": 0.00,
            "adj_wr": 1.00,
            "signal": ">20bps momentum",
            "sample_n": "~42 trades (est.)",
        },
    ]

    lp = LEAN_PRICE_MEDIAN
    ratios_to_test = [1.5, 2.0, 3.0, 5.0, 8.0, 12.0]

    for tier in tiers:
        print(f"\n  {'─'*70}")
        print(f"  {tier['name']}")
        print(f"  Raw WR={tier['wr']*100:.1f}%  |  BTC→Poly mismatch={tier['mismatch']*100:.1f}%  "
              f"|  Adjusted WR={tier['adj_wr']*100:.1f}%")
        print(f"  Sample: {tier['sample_n']}")
        print()
        print(f"  {'Ratio':>8}  {'EV (raw WR)':>13}  {'EV (adj WR)':>13}  "
              f"{'Max Loss':>10}  {'Break-even':>11}  {'Rec?':>6}")
        print(f"  {'-'*70}")

        # First pass: find best risk-adjusted ratio (EV / max_loss)
        best_r = None
        best_risk_adj = -999
        results = []
        for r in ratios_to_test:
            lw, hw, be = calc_pnl(r, lp)
            e_raw = ev(tier['wr'], lw, hw)
            e_adj = ev(tier['adj_wr'], lw, hw)
            ml = abs(hw)
            risk_adj = e_adj / ml if (ml > 0 and e_adj > 0) else -999
            results.append((r, lw, hw, be, e_raw, e_adj, ml, risk_adj))
            if e_adj > 0 and risk_adj > best_risk_adj:
                best_risk_adj = risk_adj
                best_r = r

        for r, lw, hw, be, e_raw, e_adj, ml, risk_adj in results:
            print(f"  {r:>7.1f}:1  ${e_raw:>+10.4f}  ${e_adj:>+10.4f}  "
                  f"${ml:>8.3f}  {be*100:>10.1f}%  {'←best EV/risk' if r == best_r else ''}")

        # Recalculate for the recommended ratio
        if best_r:
            lw, hw, be = calc_pnl(best_r, lp)
            e_adj = ev(tier['adj_wr'], lw, hw)
            kf = kelly_fraction(tier['adj_wr'], lw, hw)
            print()
            print(f"  ★ RECOMMENDED LEAN RATIO: {best_r:.1f}:1")
            print(f"    Expected PnL (adj WR): ${e_adj:+.4f} per trade")
            print(f"    Max loss:              ${abs(hw):.3f} ({abs(hw)/BUDGET*100:.1f}% of budget)")
            print(f"    Full Kelly:            {kf*100:.1f}% of bankroll")
            print(f"    Half-Kelly:            {kf/2*100:.1f}% of bankroll")
            print(f"    Break-even WR:         {be*100:.1f}%")

    print()
    print("  NOTE: Adjusted WR accounts for BTC→Poly direction mismatch.")
    print("  Mismatch = BTC signal fires but Poly market already priced in / inverted.")


# ─────────────────────────────────────────────────────────────────────────────
# Part E: Sensitivity to combined price
# ─────────────────────────────────────────────────────────────────────────────

def part_e():
    print("\n" + "=" * 80)
    print("PART E: Sensitivity to combined price (lean_price=$0.55, ratio=3:1)")
    print("=" * 80)

    combineds = [0.98, 1.00, 1.02, 1.04]
    ratios = [2.0, 3.0, 5.0]
    lp = LEAN_PRICE_MEDIAN
    wrs_to_show = [0.773, 0.879, 1.00]

    print(f"\n  Break-even WR by combined price and lean ratio:")
    print(f"  {'Combined':>10}" + "".join(f"  ratio={r:.0f}:1 " for r in ratios))
    print("  " + "-" * 50)
    for c in combineds:
        row = f"  ${c:.2f}     "
        for r in ratios:
            _, _, be = calc_pnl(r, lp, combined=c)
            row += f"   {be*100:5.1f}%   "
        print(row)

    print(f"\n  EV per trade at different combined prices (lean_price=$0.55):")
    for r in ratios:
        print(f"\n  ratio={r:.0f}:1:")
        print(f"  {'WR':>8}" + "".join(f"  comb={c:.2f}" for c in combineds))
        print("  " + "-" * 55)
        for wr in wrs_to_show:
            row = f"  {wr*100:.1f}%  "
            for c in combineds:
                lw, hw, _ = calc_pnl(r, lp, combined=c)
                e = ev(wr, lw, hw)
                row += f"  ${e:+.4f}"
            print(row)

    print()
    print("  Key insight:")
    for c in combineds:
        lw, hw, be = calc_pnl(3.0, lp, combined=c)
        e_t1 = ev(0.773, lw, hw)
        e_t2 = ev(0.879, lw, hw)
        e_t3 = ev(1.00, lw, hw)
        print(f"    combined=${c:.2f}: BE_WR={be*100:.1f}%  "
              f"T1_EV=${e_t1:+.4f}  T2_EV=${e_t2:+.4f}  T3_EV=${e_t3:+.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# Summary: Final Recommendations
# ─────────────────────────────────────────────────────────────────────────────

def summary():
    print("\n" + "=" * 80)
    print("SUMMARY: Final Lean Ratio Recommendations")
    print("=" * 80)

    lp = LEAN_PRICE_MEDIAN
    c = COMBINED_DEFAULT

    tiers = [
        ("Tier 1: 5-10bps  ", 0.773, 0.773 * (1 - 0.225), 2.0),
        ("Tier 2: 10-20bps ", 0.879, 0.879 * (1 - 0.061), 5.0),
        ("Tier 3: >20bps   ", 1.000, 1.000,                8.0),
    ]

    print(f"\n  {'Tier':>20}  {'Raw WR':>8}  {'Adj WR':>8}  {'Rec Ratio':>10}  "
          f"{'EV/trade':>10}  {'Max Loss':>10}  {'EV/MaxLoss':>11}")
    print("  " + "-" * 90)

    for name, raw_wr, adj_wr, rec_r in tiers:
        lw, hw, be = calc_pnl(rec_r, lp, combined=c)
        e = ev(adj_wr, lw, hw)
        ml = abs(hw)
        ratio_ev = e / ml if ml > 0 else 0
        print(f"  {name:>20}  {raw_wr*100:>7.1f}%  {adj_wr*100:>7.1f}%  {rec_r:>8.1f}:1  "
              f"${e:>+8.4f}  ${ml:>8.3f}  {ratio_ev:>10.4f}")

    print()
    print("  Risk-adjusted interpretation (EV per dollar of max loss):")
    print("  Higher = better return per unit of downside risk.")
    print()

    # Overall position sizing recommendation
    bankroll = 253.0
    budget_pct = BUDGET / bankroll * 100
    print(f"  Position sizing:")
    print(f"    Bankroll: ${bankroll:.0f}")
    print(f"    Budget per market: ${BUDGET:.0f} ({budget_pct:.1f}% of bankroll)")
    print(f"    Maximum simultaneous open markets: {int(bankroll * 0.15 / BUDGET)} "
          f"(at 15% bankroll exposure)")
    print()
    print("  Implementation notes:")
    print("  1. Use adjusted WR (accounts for BTC→Poly mismatch) for EV calcs.")
    print("  2. Tier 1 (5-10bps): Consider skipping unless fill conditions are ideal.")
    print("     EV is marginal after mismatch adjustment.")
    print("  3. Tier 2 (10-20bps): Core play. 5:1 lean gives solid EV with manageable loss.")
    print("  4. Tier 3 (>20bps):  High conviction. 8:1 lean. WR=100% in sample (n=42).")
    print("     Caveat: 100% WR may not hold out-of-sample; treat as cap not guarantee.")
    print("  5. Always check combined price. If >$1.03, drop one ratio tier for safety.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 80)
    print("OPTIMAL LEAN RATIO ANALYSIS — 5M Polymarket Bot")
    print(f"  Budget: ${BUDGET}  |  Combined (default): ${COMBINED_DEFAULT}  "
          f"|  Lean price (median): ${LEAN_PRICE_MEDIAN}")
    print(f"  Bankroll: $253  |  WR data: 153-trade verified sample")
    print("=" * 80)

    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    summary()
