#!/usr/bin/env python3
"""
lottery_backtest.py — Validate dead market lottery thesis

Core thesis: In dead markets (low σ_poly), the cheap side (12-16¢) wins
more often than the market implies (12-16%). If true P > 12%, buying at
$0.12 is +EV with 8:1 payoff.

Uses signal_tape.jsonl which has up_mid per ~20s snapshot per market.
Each market resolves UP or DOWN. We check:
1. Which markets are "dead" (low σ_poly)?
2. In those markets, what's the cheap side win rate?
3. Is it > breakeven (12% at $0.12 entry)?

Usage: PYTHONPATH=.:scripts python3 polymarket/analysis/lottery_backtest.py
"""

import json
import logging
import os
import statistics
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_TAPE = os.path.join(_AXC, "polymarket", "logs", "signal_tape.jsonl")


def load_markets() -> dict[str, list[dict]]:
    """Load signal_tape grouped by condition_id.

    Signal tape format: each line is a snapshot with nested "poly" array
    containing per-market data: {cid, coin, title, up_mid, dn_mid, ...}
    """
    markets: dict[str, list[dict]] = defaultdict(list)
    with open(_TAPE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = r.get("ts", "")
                for m in r.get("poly", []):
                    cid = m.get("cid", "")
                    if cid and (m.get("up_mid") or 0) > 0:
                        m["_ts"] = ts  # carry timestamp
                        markets[cid].append(m)
            except json.JSONDecodeError:
                continue
    return dict(markets)


def compute_sigma(ticks: list[dict]) -> float:
    """Compute σ_poly from consecutive up_mid changes."""
    mids = [t.get("up_mid", 0) for t in ticks if t.get("up_mid", 0) > 0]
    if len(mids) < 3:
        return 0.0
    changes = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    if not changes:
        return 0.0
    return (sum(c * c for c in changes) / len(changes)) ** 0.5


def determine_outcome(ticks: list[dict]) -> str | None:
    """Determine if market resolved UP or DOWN from final mid prices.

    If last up_mid > 0.90 → UP won. If < 0.10 → DOWN won.
    Otherwise → indeterminate.
    """
    if not ticks:
        return None
    last_mid = ticks[-1].get("up_mid", 0.5)
    if last_mid > 0.90:
        return "UP"
    elif last_mid < 0.10:
        return "DOWN"
    return None  # not resolved or mid-window


def main():
    log.info("=" * 60)
    log.info("  Dead Market Lottery Backtest")
    log.info("=" * 60)

    markets = load_markets()
    log.info(f"  Loaded {len(markets)} markets from signal_tape\n")

    # ─── Compute per-market stats ───
    results = []
    for cid, ticks in markets.items():
        sigma = compute_sigma(ticks)
        outcome = determine_outcome(ticks)
        if outcome is None:
            continue  # skip unresolved

        mids = [t.get("up_mid", 0.5) for t in ticks if t.get("up_mid", 0) > 0]
        if not mids:
            continue

        # Use FIRST tick (entry-time) mid, not average — more realistic entry decision
        first_mid = mids[0]
        avg_mid = statistics.mean(mids)
        min_mid = min(mids)
        max_mid = max(mids)
        # Cheap side at entry time: whichever side is cheaper when we'd decide
        cheap_side_price = min(first_mid, 1 - first_mid)
        cheap_side = "UP" if first_mid < 0.5 else "DOWN"
        cheap_won = (cheap_side == outcome)

        title = ticks[0].get("title", "?")
        results.append({
            "cid": cid[:10],
            "title": title[:40],
            "sigma": sigma,
            "outcome": outcome,
            "avg_mid": avg_mid,
            "cheap_side": cheap_side,
            "cheap_price": cheap_side_price,
            "cheap_won": cheap_won,
            "ticks": len(ticks),
        })

    log.info(f"  Resolved markets: {len(results)}\n")

    # ─── Analyse by σ_poly regime ───
    THRESHOLDS = [0.015, 0.020, 0.025, 0.030, 0.040, 0.060, 999]
    LABELS = ["<0.015", "<0.020", "<0.025", "<0.030", "<0.040", "<0.060", "all"]

    log.info(f"{'Regime':>10s} {'N':>4s} {'Cheap WR':>9s} {'BE price':>9s} {'EV@12c':>8s} {'EV@15c':>8s} {'Cheap avg':>10s}")
    log.info("-" * 65)

    for thresh, label in zip(THRESHOLDS, LABELS):
        if label == "all":
            subset = results
        else:
            subset = [r for r in results if r["sigma"] < thresh]

        if not subset:
            log.info(f"{label:>10s} {0:>4d} {'--':>9s}")
            continue

        n = len(subset)
        wins = sum(1 for r in subset if r["cheap_won"])
        wr = wins / n
        avg_cheap = statistics.mean(r["cheap_price"] for r in subset)

        # EV at different entry prices (assuming we always buy cheap side)
        # EV = WR × (1 - entry) - (1 - WR) × entry = WR - entry
        ev_12 = wr * (1 - 0.12) - (1 - wr) * 0.12
        ev_15 = wr * (1 - 0.15) - (1 - wr) * 0.15

        # Breakeven price = WR (at this price, EV = 0)
        be_price = wr

        log.info(f"{label:>10s} {n:>4d} {wr:>8.1%} {be_price:>8.2f}¢ {ev_12:>+7.3f} {ev_15:>+7.3f} {avg_cheap:>9.3f}")

    log.info("")

    # ─── Detailed: only dead markets (σ < 0.025) ───
    dead = [r for r in results if r["sigma"] < 0.025]
    if dead:
        log.info(f"── Dead markets (σ < 0.025): {len(dead)} markets ──")
        log.info(f"{'CID':>10s} {'σ':>6s} {'Cheap':>5s} {'Price':>6s} {'Won':>4s} {'Title'}")
        for r in sorted(dead, key=lambda x: x["sigma"]):
            won_str = "✓" if r["cheap_won"] else "✗"
            log.info(f"{r['cid']:>10s} {r['sigma']:>5.3f} {r['cheap_side']:>5s} {r['cheap_price']:>5.2f} {won_str:>4s} {r['title']}")

    log.info("")

    # ─── Summary ───
    dead_wins = sum(1 for r in dead if r["cheap_won"])
    dead_wr = dead_wins / len(dead) if dead else 0
    all_wins = sum(1 for r in results if r["cheap_won"])
    all_wr = all_wins / len(results) if results else 0

    log.info("── Summary ──")
    log.info(f"  All markets:  cheap side WR = {all_wr:.1%} ({all_wins}/{len(results)})")
    log.info(f"  Dead markets: cheap side WR = {dead_wr:.1%} ({dead_wins}/{len(dead)})")
    log.info(f"  Breakeven at $0.12 entry:  need WR > 12%")
    log.info(f"  Breakeven at $0.15 entry:  need WR > 15%")
    log.info(f"  Thesis {'CONFIRMED' if dead_wr > 0.15 else 'UNCONFIRMED'}: dead market cheap side WR = {dead_wr:.1%} vs 15% threshold")


if __name__ == "__main__":
    main()
