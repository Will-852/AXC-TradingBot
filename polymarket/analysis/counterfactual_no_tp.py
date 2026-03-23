#!/usr/bin/env python3
"""
Counterfactual analysis: What if the MM bot had NO take-profit (TP) exits?
All positions held to full resolution.

Data sources:
- mm_trades.jsonl: 168 resolved trades with actual PnL
- mm_order_log.jsonl: fill events with entry price/size

Logic:
- ACTUAL PnL = from trade log (includes TP exit profits)
- COUNTERFACTUAL PnL = if all shares held to resolution:
    - Win side: shares × $1.00
    - Lose side: $0.00
    - PnL = winning_payout - original_entry_cost

For trades WITH order log fills: use fill data for original cost/shares.
For trades WITHOUT fills: use trade log fields directly.
  - Old format (first 2): entry_cost, up_shares, down_shares, unwind_revenue
  - New format: cost = remaining cost after TP exits, payout = remaining shares payout
    Without fill data, we cannot reconstruct original position — flag as "no fill data".
"""

import json
from collections import defaultdict
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "logs"
TRADES_FILE = LOGS / "mm_trades.jsonl"
ORDERS_FILE = LOGS / "mm_order_log.jsonl"


def load_trades():
    trades = []
    with open(TRADES_FILE) as f:
        for line in f:
            trades.append(json.loads(line.strip()))
    return trades


def load_fills():
    """Group fill events by short cid. Returns {cid: {outcome: {shares, cost}}}"""
    fills = defaultdict(lambda: defaultdict(lambda: {"shares": 0.0, "cost": 0.0}))
    with open(ORDERS_FILE) as f:
        for line in f:
            d = json.loads(line.strip())
            if d.get("event") in ("fill", "fill_ws"):
                cid = d["cid"]
                outcome = d["outcome"]
                fills[cid][outcome]["shares"] += d["size"]
                fills[cid][outcome]["cost"] += d["price"] * d["size"]
    return fills


def get_short_cid(trade):
    cid = trade.get("cid") or trade.get("condition_id", "")
    return "0x" + cid[2:8]


def main():
    trades = load_trades()
    fills = load_fills()

    # Deduplicate trades by full cid (some appear twice due to paired BTC+ETH logging)
    seen_cids = set()
    unique_trades = []
    for t in trades:
        full_cid = t.get("cid") or t.get("condition_id", "")
        if full_cid in seen_cids:
            continue
        seen_cids.add(full_cid)
        unique_trades.append(t)

    print(f"Total trades: {len(trades)}, unique: {len(unique_trades)}")
    print()

    results = []
    no_fill_data = 0
    has_fill_data = 0
    zero_cost_skip = 0

    for t in unique_trades:
        short_cid = get_short_cid(t)
        result = t["result"]
        actual_pnl = t.get("pnl", 0.0)

        # --- Determine original position ---
        if "condition_id" in t:
            # OLD FORMAT: has full position info including unwind_revenue
            up_shares = t["up_shares"]
            down_shares = t["down_shares"]
            original_cost = t["entry_cost"]
            source = "old_format"
        elif short_cid in fills:
            # NEW FORMAT with fill data from order log
            fill = fills[short_cid]
            up_shares = fill.get("UP", {}).get("shares", 0.0)
            down_shares = fill.get("DOWN", {}).get("shares", 0.0)
            original_cost = sum(s["cost"] for s in fill.values())
            source = "fill_data"
            has_fill_data += 1
        else:
            # NEW FORMAT without fill data — can only use trade log
            trade_cost = t.get("cost", 0.0)
            trade_payout = t.get("payout", 0.0)

            if trade_cost == 0.0 and actual_pnl == 0.0:
                # Zero-cost trade (no fills happened) — skip
                zero_cost_skip += 1
                continue

            # Without fill data, trade_cost is AFTER TP exits
            # We can't reconstruct original position — use what we have
            # This is an approximation: if no TP happened, cost = original cost
            # For most early new-format trades, they had flat $4.81 cost, $5.06 payout
            # which suggests single-side bets with no TP
            up_shares = 0.0
            down_shares = 0.0
            original_cost = trade_cost
            # Infer shares from payout: if won, payout = shares × 1.0
            if actual_pnl > 0 and trade_payout > 0:
                if result == "UP":
                    up_shares = trade_payout
                else:
                    down_shares = trade_payout
                # Original cost = payout - pnl (since pnl = payout - cost)
                original_cost = trade_payout - actual_pnl
            elif actual_pnl < 0:
                # Lost: payout was from wrong side, or zero
                # Lost all: original_cost = -actual_pnl (if payout=0)
                # Or partial: original_cost = payout - actual_pnl
                original_cost = trade_payout - actual_pnl
            else:
                # pnl=0 but cost>0 — edge case
                original_cost = trade_cost

            source = "inferred"
            no_fill_data += 1

        # --- Compute counterfactual PnL ---
        if result == "UP":
            cf_payout = up_shares * 1.0
        else:  # DOWN
            cf_payout = down_shares * 1.0

        cf_pnl = cf_payout - original_cost

        # --- Store result ---
        results.append({
            "short_cid": short_cid,
            "ts": t["ts"][:19],
            "result": result,
            "actual_pnl": round(actual_pnl, 4),
            "cf_pnl": round(cf_pnl, 4),
            "delta": round(cf_pnl - actual_pnl, 4),
            "original_cost": round(original_cost, 4),
            "up_shares": round(up_shares, 2),
            "down_shares": round(down_shares, 2),
            "cf_payout": round(cf_payout, 4),
            "source": source,
        })

    # --- Summary ---
    total_actual = sum(r["actual_pnl"] for r in results)
    total_cf = sum(r["cf_pnl"] for r in results)
    total_delta = total_cf - total_actual

    print("=" * 80)
    print("COUNTERFACTUAL: NO TAKE-PROFIT EXITS — ALL POSITIONS HELD TO RESOLUTION")
    print("=" * 80)
    print(f"\nTrades analyzed:     {len(results)}")
    print(f"  with fill data:    {has_fill_data} (from order log)")
    print(f"  old format:        2 (early trades with full position info)")
    print(f"  inferred:          {no_fill_data} (no fill data, used trade log fields)")
    print(f"  skipped (0 cost):  {zero_cost_skip}")
    print()

    wins_actual = sum(1 for r in results if r["actual_pnl"] > 0)
    wins_cf = sum(1 for r in results if r["cf_pnl"] > 0)
    losses_actual = sum(1 for r in results if r["actual_pnl"] < 0)
    losses_cf = sum(1 for r in results if r["cf_pnl"] < 0)

    print(f"{'':>30} {'ACTUAL':>12} {'NO-TP (CF)':>12} {'DELTA':>12}")
    print(f"{'─' * 30} {'─' * 12} {'─' * 12} {'─' * 12}")
    print(f"{'Total PnL':>30} ${total_actual:>10.2f} ${total_cf:>10.2f} ${total_delta:>10.2f}")
    print(f"{'Wins':>30} {wins_actual:>12d} {wins_cf:>12d}")
    print(f"{'Losses':>30} {losses_actual:>12d} {losses_cf:>12d}")

    # Average PnL per trade
    avg_actual = total_actual / len(results) if results else 0
    avg_cf = total_cf / len(results) if results else 0
    print(f"{'Avg PnL per trade':>30} ${avg_actual:>10.3f} ${avg_cf:>10.3f} ${avg_cf - avg_actual:>10.3f}")
    print()

    # --- Breakdown by source ---
    for src in ["old_format", "fill_data", "inferred"]:
        subset = [r for r in results if r["source"] == src]
        if not subset:
            continue
        s_actual = sum(r["actual_pnl"] for r in subset)
        s_cf = sum(r["cf_pnl"] for r in subset)
        print(f"  [{src:>10}] {len(subset):>3d} trades: actual=${s_actual:>8.2f}  cf=${s_cf:>8.2f}  delta=${s_cf - s_actual:>8.2f}")

    print()

    # --- Top 10 where TP HELPED most (actual > cf, i.e., delta < 0) ---
    sorted_helped = sorted(results, key=lambda r: r["delta"])
    print("─" * 100)
    print("TOP 10 TRADES WHERE TP HELPED MOST (actual PnL > counterfactual PnL)")
    print("─" * 100)
    print(f"{'cid':>10} {'ts':>20} {'result':>6} {'actual':>10} {'cf':>10} {'delta':>10} {'cost':>10} {'up_sh':>7} {'dn_sh':>7} {'src':>10}")
    for r in sorted_helped[:10]:
        print(
            f"{r['short_cid']:>10} {r['ts']:>20} {r['result']:>6} "
            f"${r['actual_pnl']:>8.2f} ${r['cf_pnl']:>8.2f} ${r['delta']:>8.2f} "
            f"${r['original_cost']:>8.2f} {r['up_shares']:>7.1f} {r['down_shares']:>7.1f} {r['source']:>10}"
        )

    print()

    # --- Top 10 where TP HURT most (actual < cf, i.e., delta > 0) ---
    sorted_hurt = sorted(results, key=lambda r: -r["delta"])
    print("─" * 100)
    print("TOP 10 TRADES WHERE TP HURT MOST (counterfactual PnL > actual PnL)")
    print("─" * 100)
    print(f"{'cid':>10} {'ts':>20} {'result':>6} {'actual':>10} {'cf':>10} {'delta':>10} {'cost':>10} {'up_sh':>7} {'dn_sh':>7} {'src':>10}")
    for r in sorted_hurt[:10]:
        print(
            f"{r['short_cid']:>10} {r['ts']:>20} {r['result']:>6} "
            f"${r['actual_pnl']:>8.2f} ${r['cf_pnl']:>8.2f} ${r['delta']:>8.2f} "
            f"${r['original_cost']:>8.2f} {r['up_shares']:>7.1f} {r['down_shares']:>7.1f} {r['source']:>10}"
        )

    print()

    # --- Additional analysis: breakdown by matched fill data ---
    fill_matched = [r for r in results if r["source"] == "fill_data"]
    if fill_matched:
        fm_actual = sum(r["actual_pnl"] for r in fill_matched)
        fm_cf = sum(r["cf_pnl"] for r in fill_matched)
        print("─" * 80)
        print("FILL-DATA SUBSET ONLY (most reliable — original cost from order log fills)")
        print("─" * 80)
        print(f"  Trades:     {len(fill_matched)}")
        print(f"  Actual PnL: ${fm_actual:.2f}")
        print(f"  CF PnL:     ${fm_cf:.2f}")
        print(f"  Delta:      ${fm_cf - fm_actual:.2f}")
        print(f"  TP {'HELPED' if fm_cf < fm_actual else 'HURT'} by ${abs(fm_cf - fm_actual):.2f} in this subset")
        print()

    # --- Deep dive: for fill_data trades, show TP recovery amounts ---
    print("─" * 80)
    print("FILL-DATA TRADES: TP EXIT RECOVERY DETAIL")
    print("─" * 80)
    print(f"{'cid':>10} {'result':>6} {'orig_cost':>10} {'trade_cost':>11} {'TP_recovered':>13} {'actual':>10} {'cf':>10} {'delta':>10}")
    fill_trades_detail = []
    for t in unique_trades:
        short_cid = get_short_cid(t)
        if short_cid not in fills or "condition_id" in t:
            continue
        fill = fills[short_cid]
        original_cost = sum(s["cost"] for s in fill.values())
        trade_cost = t.get("cost", 0.0)
        tp_recovered = original_cost - trade_cost
        r = next((x for x in results if x["short_cid"] == short_cid), None)
        if r and (tp_recovered > 0.01 or abs(r["delta"]) > 0.01):
            fill_trades_detail.append({
                "short_cid": short_cid,
                "result": t["result"],
                "original_cost": original_cost,
                "trade_cost": trade_cost,
                "tp_recovered": tp_recovered,
                "actual_pnl": r["actual_pnl"],
                "cf_pnl": r["cf_pnl"],
                "delta": r["delta"],
            })

    fill_trades_detail.sort(key=lambda x: x["delta"])
    for d in fill_trades_detail:
        print(
            f"{d['short_cid']:>10} {d['result']:>6} "
            f"${d['original_cost']:>8.2f} ${d['trade_cost']:>9.2f} ${d['tp_recovered']:>11.2f} "
            f"${d['actual_pnl']:>8.2f} ${d['cf_pnl']:>8.2f} ${d['delta']:>8.2f}"
        )

    total_tp_recovered = sum(d["tp_recovered"] for d in fill_trades_detail)
    print(f"\n  Total TP recovered across these trades: ${total_tp_recovered:.2f}")

    # --- Verdict ---
    print()
    print("=" * 80)
    if total_delta > 0:
        print(f"VERDICT: TP exits HURT overall by ${total_delta:.2f}")
        print(f"  Holding all positions to resolution would have been ${total_delta:.2f} MORE profitable.")
    elif total_delta < 0:
        print(f"VERDICT: TP exits HELPED overall by ${abs(total_delta):.2f}")
        print(f"  Holding all positions to resolution would have been ${abs(total_delta):.2f} LESS profitable.")
    else:
        print("VERDICT: TP exits had NO net effect.")
    print("=" * 80)


if __name__ == "__main__":
    main()
