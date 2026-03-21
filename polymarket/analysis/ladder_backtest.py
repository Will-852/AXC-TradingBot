#!/usr/bin/env python3
"""
ladder_backtest.py — Backtest wide ladder DCA strategy for 15M BTC

Simulates placing orders at multiple price levels (rungs) and tracks:
- Which rungs fill (mid crosses the price)
- Average entry price across filled rungs
- Win rate per rung (cheap rungs = higher WR?)
- Total EV for different ladder configurations

Uses signal_tape.jsonl (117+ markets, ~20s mid snapshots).

Usage: PYTHONPATH=.:scripts python3 polymarket/analysis/ladder_backtest.py
"""

import json
import logging
import os
import statistics
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_TAPE = os.path.join(_AXC, "polymarket", "logs", "signal_tape.jsonl")


def load_markets() -> dict[str, list[dict]]:
    markets: dict[str, list[dict]] = defaultdict(list)
    with open(_TAPE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                for m in r.get("poly", []):
                    cid = m.get("cid", "")
                    if cid and (m.get("up_mid") or 0) > 0:
                        markets[cid].append(m)
            except json.JSONDecodeError:
                continue
    return dict(markets)


def determine_outcome(ticks: list[dict]) -> str | None:
    if not ticks:
        return None
    last_mid = ticks[-1].get("up_mid", 0.5)
    if last_mid > 0.90:
        return "UP"
    elif last_mid < 0.10:
        return "DOWN"
    return None


def simulate_ladder(ticks: list[dict], outcome: str,
                    rungs: list[float], budget_per_rung: float,
                    direction: str = "auto") -> dict:
    """Simulate a ladder of limit orders for one market.

    Args:
        ticks: list of snapshots with up_mid
        outcome: "UP" or "DOWN"
        rungs: list of bid prices (e.g. [0.37, 0.32, 0.27, 0.22, 0.17, 0.12])
        budget_per_rung: $ per rung
        direction: "UP", "DOWN", or "auto" (first tick mid > 0.5 = UP)

    Returns: dict with fills, avg_entry, pnl, etc.
    """
    mids = [(t.get("up_mid") or 0.5) for t in ticks]
    if not mids:
        return {"fills": 0}

    # Determine direction from first tick
    first_mid = mids[0]
    if direction == "auto":
        direction = "UP" if first_mid > 0.5 else "DOWN"

    # For UP direction: we buy UP token. UP token mid = up_mid.
    # Our bid is below current mid. Fill when mid drops to our bid.
    # For DOWN direction: we buy DOWN token. DOWN token mid ≈ 1 - up_mid.
    # Our bid is below current DOWN mid. Fill when DOWN mid drops = UP mid rises.

    filled_rungs = []
    for rung_price in rungs:
        shares = budget_per_rung / rung_price if rung_price > 0 else 0
        if shares <= 0:
            continue

        # Check if mid ever reaches our bid price
        filled = False
        fill_tick = -1
        for i, mid in enumerate(mids):
            if direction == "UP":
                token_mid = mid
            else:
                token_mid = 1.0 - mid

            if token_mid <= rung_price:
                filled = True
                fill_tick = i
                break

        if filled:
            filled_rungs.append({
                "price": rung_price,
                "shares": shares,
                "cost": budget_per_rung,
                "fill_tick": fill_tick,
                "fill_pct": fill_tick / len(mids) * 100 if mids else 0,
            })

    if not filled_rungs:
        return {"fills": 0, "direction": direction}

    total_shares = sum(r["shares"] for r in filled_rungs)
    total_cost = sum(r["cost"] for r in filled_rungs)
    avg_entry = total_cost / total_shares if total_shares > 0 else 0

    # PnL: if we win, each share = $1. If lose, each share = $0.
    won = (direction == outcome)
    if won:
        pnl = total_shares * 1.0 - total_cost  # shares resolve at $1
    else:
        pnl = -total_cost  # shares resolve at $0

    return {
        "fills": len(filled_rungs),
        "total_rungs": len(rungs),
        "direction": direction,
        "outcome": outcome,
        "won": won,
        "total_shares": round(total_shares, 2),
        "total_cost": round(total_cost, 2),
        "avg_entry": round(avg_entry, 4),
        "pnl": round(pnl, 2),
        "filled_prices": [r["price"] for r in filled_rungs],
        "deepest_fill": min(r["price"] for r in filled_rungs),
    }


def main():
    log.info("=" * 60)
    log.info("  Wide Ladder DCA Backtest — 15M BTC")
    log.info("=" * 60)

    markets = load_markets()
    log.info(f"  Loaded {len(markets)} markets\n")

    # Filter to resolved BTC markets
    resolved = []
    for cid, ticks in markets.items():
        outcome = determine_outcome(ticks)
        if outcome is None:
            continue
        title = ticks[0].get("title", "")
        if "Bitcoin" not in title:
            continue
        resolved.append((cid, ticks, outcome))

    log.info(f"  Resolved BTC markets: {len(resolved)}\n")

    # ─── Test different ladder configs ───
    configs = [
        # (name, rungs, budget_per_rung)
        ("Current (1 rung @0.37)", [0.37], 2.50),
        ("Current (1 rung @0.35)", [0.35], 2.50),
        ("2 rungs (0.37, 0.30)", [0.37, 0.30], 1.25),
        ("3 rungs (0.37, 0.30, 0.22)", [0.37, 0.30, 0.22], 0.83),
        ("4 rungs (0.40, 0.33, 0.26, 0.19)", [0.40, 0.33, 0.26, 0.19], 0.63),
        ("5 rungs (0.40, 0.35, 0.30, 0.25, 0.20)", [0.40, 0.35, 0.30, 0.25, 0.20], 0.50),
        ("6 rungs (0.40, 0.35, 0.30, 0.25, 0.20, 0.15)", [0.40, 0.35, 0.30, 0.25, 0.20, 0.15], 0.42),
        ("6 rungs wide (0.45, 0.38, 0.31, 0.24, 0.17, 0.10)", [0.45, 0.38, 0.31, 0.24, 0.17, 0.10], 0.42),
    ]

    log.info(f"{'Config':>45s} {'Fill%':>6s} {'AvgFills':>8s} {'AvgEntry':>9s} {'WR':>5s} {'PnL/mkt':>8s} {'TotalPnL':>9s}")
    log.info("-" * 100)

    for name, rungs, budget in configs:
        results = []
        for cid, ticks, outcome in resolved:
            r = simulate_ladder(ticks, outcome, rungs, budget)
            results.append(r)

        filled = [r for r in results if r["fills"] > 0]
        n_filled = len(filled)
        fill_pct = n_filled / len(results) * 100 if results else 0
        avg_fills = statistics.mean(r["fills"] for r in filled) if filled else 0
        avg_entry = statistics.mean(r["avg_entry"] for r in filled) if filled else 0
        wins = sum(1 for r in filled if r.get("won"))
        wr = wins / n_filled * 100 if n_filled else 0
        total_pnl = sum(r.get("pnl", 0) for r in filled)
        pnl_per_mkt = total_pnl / n_filled if n_filled else 0

        log.info(f"{name:>45s} {fill_pct:>5.1f}% {avg_fills:>7.1f} ${avg_entry:>7.4f} {wr:>4.0f}% ${pnl_per_mkt:>+7.2f} ${total_pnl:>+8.2f}")

    log.info("")

    # ─── Per-rung fill rate analysis ───
    log.info("── Per-Rung Fill Rate (how often does mid reach each price?) ──")
    test_prices = [0.45, 0.40, 0.37, 0.35, 0.30, 0.25, 0.20, 0.15, 0.12, 0.10]

    log.info(f"{'Price':>7s} {'Fill%':>7s} {'WR':>5s} {'EV/fill':>8s}")
    log.info("-" * 30)
    for price in test_prices:
        filled = 0
        wins = 0
        for cid, ticks, outcome in resolved:
            r = simulate_ladder(ticks, outcome, [price], 1.0)
            if r["fills"] > 0:
                filled += 1
                if r.get("won"):
                    wins += 1

        fill_pct = filled / len(resolved) * 100 if resolved else 0
        wr = wins / filled * 100 if filled else 0
        ev = (wr / 100) * (1.0 - price) - (1 - wr / 100) * price if filled else 0

        bar = "█" * int(fill_pct / 2)
        log.info(f"${price:>5.2f} {fill_pct:>6.1f}% {wr:>4.0f}% ${ev:>+6.3f} {bar}")

    log.info("")

    # ─── Optimal config recommendation ───
    log.info("── Recommendation ──")
    log.info("  Compare: total PnL, fill rate, avg entry price")
    log.info("  Best config = highest total PnL with reasonable fill rate")


if __name__ == "__main__":
    main()
