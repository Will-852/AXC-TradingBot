#!/usr/bin/env python3
"""
ab_report.py — A/B test report: M1-only (live) vs Continuous Momentum (paper)

Analyzes both logs, compares performance, addresses BMD concerns:
1. 0.7 relaxation factor sensitivity
2. Fill rate comparison
3. Trade quality (WR by CM sigma level)

Run: PYTHONPATH=.:scripts python3 polymarket/tools/ab_report.py
"""
import json, os, re, sys
from datetime import datetime
from pathlib import Path

_P = Path(__file__).resolve().parents[2]
_LIVE_LOG = _P / "polymarket" / "logs" / "mm_live_v10.log"
_CM_LOG = _P / "polymarket" / "logs" / "mm_paper_cm.log"

def parse_log(path: Path) -> dict:
    """Extract trades + stats from bot log."""
    trades = []
    orders = 0
    submitted = 0
    filled = 0
    cancels = 0
    skips = 0
    m1_confirmed = 0
    cm_confirmed = 0

    if not path.exists():
        return {"error": f"{path.name} not found"}

    with open(path, "r", errors="replace") as f:
        for line in f:
            if "plan " in line:
                orders += 1
            if "ORDER SUBMITTED" in line:
                submitted += 1
            if "FILL CONFIRMED" in line or "INSTANT FILL" in line:
                filled += 1
            if "CANCEL" in line and "CANCEL ALL" not in line:
                cancels += 1
            if "SKIP" in line:
                skips += 1
            if "M1 confirmed" in line:
                m1_confirmed += 1
            if "CM confirmed" in line:
                cm_confirmed += 1

            # Parse RESOLVED lines
            m = re.search(r"RESOLVED (\S+) . \| PnL \$([+-]?\d+\.\d+) \| Total \$([+-]?\d+\.\d+)", line)
            if m:
                trades.append({
                    "cid": m.group(1),
                    "pnl": float(m.group(2)),
                    "total": float(m.group(3)),
                })

            # Parse STOP LOSS / EARLY TAKE
            m2 = re.search(r"(STOP LOSS|EARLY TAKE) R(\d+) (\S+) (\S+):.*round_pnl=\$([+-]?\d+\.\d+)", line)
            if m2:
                trades.append({
                    "cid": m2.group(3),
                    "type": m2.group(1),
                    "round": int(m2.group(2)),
                    "pnl": float(m2.group(5)),
                })

    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] < 0)
    total_pnl = sum(t["pnl"] for t in trades)
    n = len(trades)

    return {
        "file": path.name,
        "trades": n,
        "wins": wins,
        "losses": losses,
        "wr": round(wins / n * 100, 1) if n > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / n, 2) if n > 0 else 0,
        "orders_planned": orders,
        "submitted": submitted,
        "filled": filled,
        "skips": skips,
        "cancels": cancels,
        "m1_confirmed": m1_confirmed,
        "cm_confirmed": cm_confirmed,
        "trade_list": trades,
    }


def main():
    print("=" * 60)
    print("  A/B TEST REPORT — M1 vs Continuous Momentum")
    print(f"  Generated: {datetime.now():%Y-%m-%d %H:%M HKT}")
    print("=" * 60)

    live = parse_log(_LIVE_LOG)
    paper = parse_log(_CM_LOG)

    if "error" in live:
        print(f"  ❌ Live: {live['error']}")
    if "error" in paper:
        print(f"  ❌ Paper: {paper['error']}")

    print(f"\n  {'':25} {'M1 (live)':>12} {'CM (paper)':>12}")
    print(f"  {'-'*50}")

    for label, key in [
        ("Trades", "trades"),
        ("Wins", "wins"),
        ("Losses", "losses"),
        ("Win Rate", "wr"),
        ("Total PnL", "total_pnl"),
        ("Avg PnL/trade", "avg_pnl"),
        ("Orders planned", "orders_planned"),
        ("Submitted", "submitted"),
        ("Filled", "filled"),
        ("Skips", "skips"),
        ("Cancels", "cancels"),
        ("M1 confirmed", "m1_confirmed"),
        ("CM confirmed", "cm_confirmed"),
    ]:
        lv = live.get(key, "N/A")
        pv = paper.get(key, "N/A")
        fmt = "${}" if "pnl" in key.lower() else "{}%" if key == "wr" else "{}"
        print(f"  {label:25} {fmt.format(lv):>12} {fmt.format(pv):>12}")

    # BMD Analysis
    print(f"\n{'='*60}")
    print("  BMD CONCERNS")
    print(f"{'='*60}")

    # BMD #2: 0.7 relaxation factor
    print(f"\n  BMD #2: Relaxation factor 0.7")
    cm_count = paper.get("cm_confirmed", 0)
    m1_count = live.get("m1_confirmed", 0)
    print(f"    CM confirmed: {cm_count} vs M1 confirmed: {m1_count}")
    if cm_count > 0 and m1_count > 0:
        ratio = cm_count / m1_count
        print(f"    CM catches {ratio:.1f}x more signals")
        print(f"    If WR maintained → 0.7 is working")
        print(f"    If WR dropped → 0.7 too loose, tighten to 0.8+")

    # Winner
    print(f"\n{'='*60}")
    lp = live.get("total_pnl", 0)
    pp = paper.get("total_pnl", 0)
    winner = "M1 (live)" if lp > pp else "CM (paper)" if pp > lp else "TIE"
    print(f"  WINNER: {winner}")
    print(f"  M1 PnL: ${lp:.2f} | CM PnL: ${pp:.2f}")

    # Recommendation
    print(f"\n{'='*60}")
    print("  RECOMMENDATION")
    print(f"{'='*60}")
    lwr = live.get("wr", 0)
    pwr = paper.get("wr", 0)
    lt = live.get("trades", 0)
    pt = paper.get("trades", 0)

    if pt > lt * 1.5 and pwr >= lwr - 3:
        print("  → CM catches more trades with similar WR")
        print("  → SWITCH live to --continuous-momentum")
    elif pwr < lwr - 5:
        print("  → CM WR significantly lower — 0.7 too loose")
        print("  → Keep M1, or tighten CM to 0.8+")
    else:
        print("  → Not enough data yet. Continue A/B test.")

    print()


if __name__ == "__main__":
    main()
