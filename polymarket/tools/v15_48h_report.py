#!/usr/bin/env python3
"""
v15_48h_report.py — Auto-report after 48h of v15 live data.

Reads mm_order_log.jsonl + mm_trades.jsonl and generates:
1. AS diagnostic: time_to_fill vs WR (fast fill = adverse selection?)
2. Fill rate by cancel reason
3. Time-of-day WR check
4. Per-order lifecycle funnel
5. Student-t bridge accuracy (bridge vs outcome)

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/tools/v15_48h_report.py
"""
import json, os, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_P = Path(__file__).resolve().parents[2]
_LOG_DIR = _P / "polymarket" / "logs"
_ORDER_LOG = _LOG_DIR / "mm_order_log.jsonl"
_TRADE_LOG = _LOG_DIR / "mm_trades.jsonl"


def _load_jsonl(path):
    records = []
    if not path.exists():
        return records
    with open(path) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records


def _section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def report_order_funnel(orders):
    """Per-order lifecycle funnel: submit → fill/cancel/expired."""
    _section("1. Order Lifecycle Funnel")
    events = defaultdict(int)
    for o in orders:
        events[o.get("event", "?")] += 1
    total = events.get("submit", 0)
    print(f"  Submitted:          {events.get('submit', 0)}")
    print(f"  Filled:             {events.get('fill', 0)}")
    print(f"  Cancelled (self):   {events.get('cancel', 0)}")
    print(f"  Cancelled (ext):    {events.get('cancelled_external', 0)}")
    print(f"  Expired:            {events.get('expired', 0)}")
    print(f"  Post-fill checks:   {events.get('post_fill_60s', 0)}")
    if total > 0:
        fill_rate = events.get("fill", 0) / total * 100
        cancel_rate = (events.get("cancel", 0) + events.get("cancelled_external", 0)) / total * 100
        print(f"\n  Fill rate:   {fill_rate:.0f}%")
        print(f"  Cancel rate: {cancel_rate:.0f}%")


def report_cancel_reasons(orders):
    """Cancel reason breakdown."""
    _section("2. Cancel Reasons")
    cancels = [o for o in orders if o.get("event") == "cancel"]
    if not cancels:
        print("  No cancel events yet.")
        return
    reasons = defaultdict(list)
    for c in cancels:
        r = c.get("reason", "unknown")
        # Normalize: ttl_XXXs_maxYYYs → ttl
        if r.startswith("ttl_"):
            r = "ttl_dynamic"
        elif r.startswith("adverse_move"):
            r = "adverse_move"
        reasons[r].append(c)
    for reason, items in sorted(reasons.items(), key=lambda x: -len(x[1])):
        avg_book = sum(i.get("time_on_book_s", 0) for i in items) / len(items) if items else 0
        avg_end = sum(i.get("dist_to_end_s", 0) for i in items) / len(items) if items else 0
        print(f"  {reason}: {len(items)} orders | avg book={avg_book:.0f}s | avg dist_to_end={avg_end:.0f}s")


def report_as_diagnostic(orders):
    """AS diagnostic: time_to_fill quartiles vs WR."""
    _section("3. Adverse Selection Diagnostic (time_to_fill vs WR)")
    fills = [o for o in orders if o.get("event") == "fill"]
    if len(fills) < 4:
        print(f"  Only {len(fills)} fills — need ≥4 for quartile analysis. Wait for more data.")
        return

    # Match fills to trade outcomes via cid
    trades = _load_jsonl(_TRADE_LOG)
    trade_outcomes = {}
    for t in trades:
        cid = t.get("cid", t.get("condition_id", ""))[:8]
        if t.get("pnl", 0) != 0:
            trade_outcomes[cid] = "win" if t["pnl"] > 0 else "loss"

    # Enrich fills with outcome
    enriched = []
    for f in fills:
        cid = f.get("cid", "")[:8]
        ttf = f.get("time_to_fill_s", 0)
        outcome = trade_outcomes.get(cid, "unknown")
        enriched.append({"ttf": ttf, "outcome": outcome, "cid": cid,
                         "mid_at_fill": f.get("mid_at_fill", 0)})

    known = [e for e in enriched if e["outcome"] != "unknown"]
    if len(known) < 4:
        print(f"  {len(known)} fills with known outcome — need ≥4.")
        # Still print raw data
        for e in enriched:
            print(f"    cid={e['cid']} ttf={e['ttf']:.1f}s outcome={e['outcome']} mid={e['mid_at_fill']}")
        return

    # Sort by time_to_fill, split into quartiles
    known.sort(key=lambda x: x["ttf"])
    n = len(known)
    q_size = max(1, n // 4)
    for i in range(4):
        start = i * q_size
        end = start + q_size if i < 3 else n
        group = known[start:end]
        wins = sum(1 for g in group if g["outcome"] == "win")
        wr = wins / len(group) * 100 if group else 0
        ttf_range = f"{group[0]['ttf']:.0f}-{group[-1]['ttf']:.0f}s" if group else "?"
        print(f"  Q{i+1} (ttf {ttf_range}): {len(group)} trades, WR {wr:.0f}% ({wins}/{len(group)})")

    # Key diagnostic
    fast_q = known[:q_size]
    slow_q = known[-q_size:]
    fast_wr = sum(1 for g in fast_q if g["outcome"] == "win") / len(fast_q) * 100
    slow_wr = sum(1 for g in slow_q if g["outcome"] == "win") / len(slow_q) * 100
    delta = fast_wr - slow_wr
    print(f"\n  Fast fill WR: {fast_wr:.0f}% | Slow fill WR: {slow_wr:.0f}% | Δ: {delta:+.0f}pp")
    if delta < -5:
        print("  ⚠️ AS SIGNAL: fast fills lose more → adverse selection likely")
    elif delta > 5:
        print("  ✅ No AS: fast fills win MORE → no adverse selection")
    else:
        print("  ⚪ Inconclusive: need more data")


def report_post_fill_as(orders):
    """Post-fill midpoint movement (AS cost measurement)."""
    _section("4. Post-Fill Midpoint Movement (AS Cost)")
    fills = [o for o in orders if o.get("event") == "fill"]
    post = {o.get("order_id", ""): o for o in orders if o.get("event") == "post_fill_60s"}
    if not fills:
        print("  No fills yet.")
        return

    movements = []
    for f in fills:
        oid = f.get("order_id", "")
        mid_at = f.get("mid_at_fill", 0)
        p = post.get(oid)
        if p and mid_at > 0:
            mid_60 = p.get("mid_60s", 0)
            if mid_60 > 0:
                move = mid_60 - mid_at
                movements.append({"oid": oid[:12], "mid_at": mid_at,
                                   "mid_60": mid_60, "move": move})

    if not movements:
        print(f"  {len(fills)} fills but no post-fill data yet. Check back after 60s per fill.")
        for f in fills:
            print(f"    {f.get('cid','')} mid_at_fill={f.get('mid_at_fill', 'N/A')}")
        return

    avg_move = sum(m["move"] for m in movements) / len(movements)
    adverse = sum(1 for m in movements if m["move"] < -0.02)
    print(f"  {len(movements)} fills with post-60s data")
    print(f"  Avg mid movement: {avg_move:+.4f}")
    print(f"  Adverse (mid dropped >2¢): {adverse}/{len(movements)}")
    if avg_move < -0.03:
        print("  ⚠️ AS COST: midpoint drops after our fills → we're getting picked off")
    else:
        print("  ✅ No significant AS cost detected")


def report_time_of_day(trades):
    """WR by hour of day (HKT)."""
    _section("5. Time-of-Day WR (HKT)")
    filled = [t for t in trades if t.get("cost", 0) > 0]
    if len(filled) < 10:
        print(f"  Only {len(filled)} filled trades — need ≥10.")
        return

    by_hour = defaultdict(lambda: {"win": 0, "loss": 0})
    for t in filled:
        ts = t.get("ts", "")
        try:
            h = datetime.fromisoformat(ts).hour
        except Exception:
            continue
        if t.get("pnl", 0) > 0:
            by_hour[h]["win"] += 1
        elif t.get("pnl", 0) < 0:
            by_hour[h]["loss"] += 1

    print(f"  {'Hour':>6} {'Win':>5} {'Loss':>5} {'WR':>6} {'n':>4}")
    for h in sorted(by_hour.keys()):
        d = by_hour[h]
        n = d["win"] + d["loss"]
        wr = d["win"] / n * 100 if n > 0 else 0
        bar = "█" * d["win"] + "░" * d["loss"]
        print(f"  {h:02d}:00 {d['win']:5d} {d['loss']:5d} {wr:5.0f}% {n:4d}  {bar}")


def report_bridge_accuracy(orders, trades):
    """Bridge probability vs actual outcome."""
    _section("6. Bridge Accuracy (signal → outcome)")
    submits = [o for o in orders if o.get("event") == "submit" and o.get("bridge")]
    if not submits:
        print("  No submit events with bridge data yet.")
        return

    trade_outcomes = {}
    for t in trades:
        cid = t.get("cid", t.get("condition_id", ""))[:8]
        if t.get("cost", 0) > 0:
            trade_outcomes[cid] = t.get("result", t.get("pnl", 0))

    # Bucket bridge into ranges
    buckets = defaultdict(lambda: {"correct": 0, "wrong": 0})
    for s in submits:
        cid = s.get("cid", "")[:8]
        bridge = s.get("bridge", 0.5)
        fair = s.get("fair", bridge)
        our_dir = "UP" if fair > 0.50 else "DOWN"
        outcome = trade_outcomes.get(cid)
        if outcome is None:
            continue
        if isinstance(outcome, (int, float)):
            correct = outcome > 0
        else:
            correct = outcome == our_dir

        # Bucket by |bridge - 0.50| (confidence)
        conf = abs(bridge - 0.50)
        if conf < 0.10:
            bucket = "low (<10%)"
        elif conf < 0.25:
            bucket = "mid (10-25%)"
        else:
            bucket = "high (>25%)"

        if correct:
            buckets[bucket]["correct"] += 1
        else:
            buckets[bucket]["wrong"] += 1

    for bucket in ["low (<10%)", "mid (10-25%)", "high (>25%)"]:
        d = buckets.get(bucket, {"correct": 0, "wrong": 0})
        n = d["correct"] + d["wrong"]
        wr = d["correct"] / n * 100 if n > 0 else 0
        print(f"  {bucket:>15}: WR {wr:.0f}% ({d['correct']}/{n})")


def main():
    print(f"\n{'#'*60}")
    print(f"  v15 LIVE REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'#'*60}")

    orders = _load_jsonl(_ORDER_LOG)
    trades = _load_jsonl(_TRADE_LOG)

    print(f"\n  Data: {len(orders)} order events, {len(trades)} trade records")
    print(f"  Order log: {_ORDER_LOG}")

    report_order_funnel(orders)
    report_cancel_reasons(orders)
    report_as_diagnostic(orders)
    report_post_fill_as(orders)
    report_time_of_day(trades)
    report_bridge_accuracy(orders, trades)

    print(f"\n{'='*60}")
    print(f"  END REPORT")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
