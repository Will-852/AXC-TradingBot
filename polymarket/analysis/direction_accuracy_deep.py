#!/usr/bin/env python3
"""
Deep analysis of MM bot direction accuracy.

Joins mm_order_log.jsonl (submit/fill events with signals) with
mm_trades.jsonl (actual results) to understand WHY direction accuracy drops
for FILLED trades (adverse selection effect).

The key insight to investigate:
  - Overall direction accuracy ~75% (all resolved markets)
  - But the BOT's rolling WR counts pnl>0 / filled trades
  - Filled trades have WORSE accuracy than unfilled = adverse selection
  - When counterparty fills us, they likely know something we don't

Key fields:
  - bridge: P(UP) from Student-t model (>0.5 = UP, <0.5 = DOWN)
  - m1: 1-minute return (positive = recent UP momentum)
  - cvd: cumulative volume delta ratio (>0.5 = buy pressure)
  - ob_adj: order book adjustment
  - outcome: which side we bought shares of (UP or DOWN)
  - result: actual market outcome (UP or DOWN)
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / "projects/axc-trading/polymarket/logs"
ORDER_LOG = LOG_DIR / "mm_order_log.jsonl"
TRADES_LOG = LOG_DIR / "mm_trades.jsonl"
STATE_FILE = LOG_DIR / "mm_state.json"

# ── 1. Load data ──────────────────────────────────────────────────────────

def load_order_log():
    """Parse order log into submit and fill events, keyed by cid."""
    submits_by_cid = defaultdict(list)
    fills_by_cid = defaultdict(list)

    with open(ORDER_LOG) as f:
        for line in f:
            d = json.loads(line)
            cid = d.get("cid")
            if not cid:
                continue
            event = d.get("event")
            if event == "submit":
                submits_by_cid[cid].append(d)
            elif event in ("fill", "fill_ws"):
                fills_by_cid[cid].append(d)

    return submits_by_cid, fills_by_cid


def load_trades():
    """Parse trades log. Returns list of trade dicts."""
    trades = []
    with open(TRADES_LOG) as f:
        for line in f:
            d = json.loads(line)
            trades.append(d)
    return trades


def load_state_wr():
    """Load mm_state.json and compute rolling WR the same way the bot does."""
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        return None, 0
    markets = state.get("markets", {})
    resolved = [(cid, m) for cid, m in markets.items()
                if m.get("phase") == "RESOLVED" and not m.get("paper")]
    filled = [(cid, m) for cid, m in resolved
              if m.get("entry_cost", 0) > 0 or m.get("realized_pnl", 0) != 0]
    recent = filled[-30:]
    if len(recent) < 5:
        return 0.68, len(recent)
    wins = sum(1 for _, m in recent if m.get("realized_pnl", 0) > 0)
    return wins / len(recent), len(recent)


def match_trades_to_signals(submits_by_cid, fills_by_cid, trades):
    """
    Join trades to their submit signals.
    Order log uses short cid (0x5f184b), trades use full cid (0x5f184b...).
    """
    signal_by_short_cid = {}
    for short_cid, submits in submits_by_cid.items():
        for s in submits:
            if s.get("bridge") is not None:
                signal_by_short_cid[short_cid] = s
                break
        else:
            signal_by_short_cid[short_cid] = submits[0]

    has_fills_by_short_cid = set(fills_by_cid.keys())

    # Also collect ALL fill events per short_cid for fill analysis
    fill_details_by_short_cid = {}
    for short_cid, flist in fills_by_cid.items():
        fill_details_by_short_cid[short_cid] = flist

    matched = []
    unmatched = 0

    for trade in trades:
        full_cid = trade.get("cid") or trade.get("condition_id")
        if not full_cid:
            unmatched += 1
            continue

        result = trade.get("result")
        cost = trade.get("cost", trade.get("entry_cost", 0))
        pnl = trade.get("pnl", 0)

        # Find matching signal by prefix
        signal = None
        matched_short_cid = None
        for short_cid, sig in signal_by_short_cid.items():
            if full_cid.startswith(short_cid):
                signal = sig
                matched_short_cid = short_cid
                break

        if signal is None or signal.get("bridge") is None:
            unmatched += 1
            continue

        predicted_dir = signal["outcome"]  # UP or DOWN
        correct = (predicted_dir == result)

        # Collect fill details
        fill_list = fill_details_by_short_cid.get(matched_short_cid, [])
        fill_prices = [f["price"] for f in fill_list if "price" in f]
        fill_sizes = [f["size"] for f in fill_list if "size" in f]

        entry = {
            "ts": trade["ts"],
            "predicted": predicted_dir,
            "result": result,
            "correct": correct,
            "bridge": signal["bridge"],
            "fair": signal.get("fair"),
            "m1": signal.get("m1"),
            "cvd": signal.get("cvd"),
            "ob_adj": signal.get("ob_adj"),
            "vol": signal.get("vol"),
            "price": signal.get("price"),
            "pnl": pnl,
            "cost": cost,
            "pnl_positive": pnl > 0,
            "num_fills": len(fill_list),
            "fill_prices": fill_prices,
            "fill_sizes": fill_sizes,
            "paper": signal.get("paper", False),
            "coin": signal.get("coin", "btc"),
            "observe_only": signal.get("observe_only", False),
        }
        matched.append(entry)

    return matched, unmatched


# ── 2. Analysis functions ─────────────────────────────────────────────────

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_subheader(title):
    print(f"\n  --- {title} ---")


def accuracy_stats(entries, label="", show_pnl_wr=False):
    """Calculate and print accuracy stats."""
    if not entries:
        print(f"  {label}: NO DATA")
        return 0, 0, 0.0

    n = len(entries)
    correct = sum(1 for e in entries if e["correct"])
    acc = correct / n if n > 0 else 0
    pnl = sum(e["pnl"] for e in entries)
    extra = ""
    if show_pnl_wr:
        pnl_wins = sum(1 for e in entries if e["pnl"] > 0)
        extra = f" | PnL WR: {pnl_wins}/{n}={pnl_wins/n:.0%}"
    print(f"  {label}: {correct}/{n} = {acc:.1%} dir acc | PnL: ${pnl:+.2f}{extra}")
    return correct, n, acc


def analyze_core_discrepancy(entries):
    """THE KEY QUESTION: Why does direction accuracy != PnL WR?"""
    print_header("CORE QUESTION: Direction Accuracy vs PnL Win Rate")

    filled = [e for e in entries if e["cost"] > 0]
    unfilled = [e for e in entries if e["cost"] == 0]

    print(f"\n  Total resolved markets with signals: {len(entries)}")
    print(f"  Filled (cost>0): {len(filled)}")
    print(f"  Unfilled (cost=0): {len(unfilled)}")

    print_subheader("Direction accuracy (did we predict the right outcome?)")
    accuracy_stats(entries, "ALL markets", show_pnl_wr=True)
    accuracy_stats(filled, "FILLED only", show_pnl_wr=True)
    accuracy_stats(unfilled, "UNFILLED only", show_pnl_wr=True)

    print_subheader("Why direction-correct doesn't always mean PnL-positive")
    # Cases where direction was right but PnL was negative
    right_dir_neg_pnl = [e for e in filled if e["correct"] and e["pnl"] < 0]
    wrong_dir_pos_pnl = [e for e in filled if not e["correct"] and e["pnl"] > 0]
    right_dir_pos_pnl = [e for e in filled if e["correct"] and e["pnl"] > 0]
    wrong_dir_neg_pnl = [e for e in filled if not e["correct"] and e["pnl"] < 0]

    print(f"  Correct direction + PnL>0: {len(right_dir_pos_pnl)} (good)")
    print(f"  Correct direction + PnL<0: {len(right_dir_neg_pnl)} (bought wrong side?)")
    print(f"  Wrong direction + PnL>0:   {len(wrong_dir_pos_pnl)} (hedge/spread saved us)")
    print(f"  Wrong direction + PnL<0:   {len(wrong_dir_neg_pnl)} (expected loss)")

    if right_dir_neg_pnl:
        print_subheader("ANOMALY: Correct direction but lost money (filled trades)")
        for e in right_dir_neg_pnl:
            print(f"    pred={e['predicted']} result={e['result']} pnl=${e['pnl']:+.2f} "
                  f"cost=${e['cost']:.2f} bridge={e['bridge']:.4f} "
                  f"fills={e['num_fills']} prices={e['fill_prices']}")

    if wrong_dir_pos_pnl:
        print_subheader("LUCKY: Wrong direction but made money (filled trades)")
        for e in wrong_dir_pos_pnl:
            print(f"    pred={e['predicted']} result={e['result']} pnl=${e['pnl']:+.2f} "
                  f"cost=${e['cost']:.2f} bridge={e['bridge']:.4f}")


def analyze_adverse_selection_deep(entries):
    """Deep dive into adverse selection."""
    print_header("ADVERSE SELECTION DEEP DIVE")

    filled = [e for e in entries if e["cost"] > 0]
    unfilled = [e for e in entries if e["cost"] == 0]

    if not filled:
        print("  No filled trades")
        return

    # The core AS hypothesis: counterparties fill us when they know something
    # Prediction: filled trades should have WORSE direction accuracy than unfilled

    filled_correct = sum(1 for e in filled if e["correct"])
    unfilled_correct = sum(1 for e in unfilled if e["correct"])
    filled_acc = filled_correct / len(filled) if filled else 0
    unfilled_acc = unfilled_correct / len(unfilled) if unfilled else 0

    print(f"\n  Filled direction accuracy:   {filled_acc:.1%} ({filled_correct}/{len(filled)})")
    print(f"  Unfilled direction accuracy: {unfilled_acc:.1%} ({unfilled_correct}/{len(unfilled)})")
    print(f"  AS gap: {unfilled_acc - filled_acc:.1%} (unfilled - filled)")

    # Cost-weighted analysis
    print_subheader("Cost analysis")
    wrong_filled = [e for e in filled if not e["correct"]]
    right_filled = [e for e in filled if e["correct"]]
    avg_cost_wrong = sum(e["cost"] for e in wrong_filled) / len(wrong_filled) if wrong_filled else 0
    avg_cost_right = sum(e["cost"] for e in right_filled) / len(right_filled) if right_filled else 0
    print(f"  Avg cost when WRONG direction: ${avg_cost_wrong:.2f} ({len(wrong_filled)} trades)")
    print(f"  Avg cost when RIGHT direction: ${avg_cost_right:.2f} ({len(right_filled)} trades)")
    if avg_cost_right > 0:
        print(f"  AS ratio (wrong/right cost): {avg_cost_wrong/avg_cost_right:.2f}x")

    # Fill count analysis
    print_subheader("Fill count analysis")
    by_fills = defaultdict(list)
    for e in filled:
        by_fills[e["num_fills"]].append(e)
    for nf in sorted(by_fills.keys()):
        accuracy_stats(by_fills[nf], f"{nf} fill(s)", show_pnl_wr=True)

    # Bridge conviction at time of fill vs unfilled
    print_subheader("Bridge conviction: filled vs unfilled")
    filled_conv = [abs(e["bridge"] - 0.5) for e in filled]
    unfilled_conv = [abs(e["bridge"] - 0.5) for e in unfilled]
    print(f"  Filled avg conviction:   {sum(filled_conv)/len(filled_conv):.4f}")
    print(f"  Unfilled avg conviction: {sum(unfilled_conv)/len(unfilled_conv):.4f}")
    print(f"  (Lower conviction in filled = AS — market-makers get picked off on uncertain calls)")


def analyze_bridge_distribution(entries, focus_filled=True):
    """Bridge value distribution vs accuracy, with filled/all split."""
    print_header("BRIDGE VALUE (P(UP)) DISTRIBUTION vs ACCURACY")

    groups = {"ALL": entries}
    filled = [e for e in entries if e["cost"] > 0]
    if filled:
        groups["FILLED"] = filled

    for group_name, group_entries in groups.items():
        print_subheader(f"{group_name} ({len(group_entries)} trades)")

        # Bucket by bridge value
        buckets = {
            "bridge < 0.20 (strong DOWN)": lambda b: b < 0.20,
            "bridge 0.20-0.35 (mod DOWN)": lambda b: 0.20 <= b < 0.35,
            "bridge 0.35-0.45 (weak DOWN)": lambda b: 0.35 <= b < 0.45,
            "bridge 0.45-0.55 (neutral)":   lambda b: 0.45 <= b < 0.55,
            "bridge 0.55-0.65 (weak UP)":   lambda b: 0.55 <= b < 0.65,
            "bridge 0.65-0.80 (mod UP)":    lambda b: 0.65 <= b < 0.80,
            "bridge >= 0.80 (strong UP)":   lambda b: b >= 0.80,
        }
        for label, test in buckets.items():
            subset = [e for e in group_entries if test(e["bridge"])]
            accuracy_stats(subset, label, show_pnl_wr=True)

    # Conviction analysis (distance from 0.5)
    print_subheader("By conviction strength |bridge - 0.5| — ALL trades")
    conviction_buckets = {
        "conviction < 0.05 (very weak)": lambda b: abs(b - 0.5) < 0.05,
        "conviction 0.05-0.15 (weak)":   lambda b: 0.05 <= abs(b - 0.5) < 0.15,
        "conviction 0.15-0.25 (medium)": lambda b: 0.15 <= abs(b - 0.5) < 0.25,
        "conviction 0.25-0.40 (strong)": lambda b: 0.25 <= abs(b - 0.5) < 0.40,
        "conviction >= 0.40 (extreme)":  lambda b: abs(b - 0.5) >= 0.40,
    }
    for label, test in conviction_buckets.items():
        subset = [e for e in entries if test(e["bridge"])]
        accuracy_stats(subset, label, show_pnl_wr=True)


def analyze_m1_signal(entries):
    """M1 momentum signal strength vs accuracy."""
    print_header("M1 MOMENTUM SIGNAL vs ACCURACY")

    valid = [e for e in entries if e["m1"] is not None]
    if not valid:
        print("  No M1 data available")
        return

    m1_vals = [abs(e["m1"]) for e in valid]
    print(f"\n  M1 stats: min={min(m1_vals):.6f}, max={max(m1_vals):.6f}, "
          f"mean={sum(m1_vals)/len(m1_vals):.6f}")

    # M1 agreement with prediction
    m1_agrees = [e for e in valid if ("UP" if e["m1"] > 0 else "DOWN") == e["predicted"]]
    m1_disagrees = [e for e in valid if ("UP" if e["m1"] > 0 else "DOWN") != e["predicted"]]

    print_subheader("M1 agreement with prediction")
    accuracy_stats(m1_agrees, "M1 agrees w/ prediction", show_pnl_wr=True)
    accuracy_stats(m1_disagrees, "M1 disagrees w/ prediction", show_pnl_wr=True)

    # M1 agreement — filled only
    filled_valid = [e for e in valid if e["cost"] > 0]
    if filled_valid:
        m1_agrees_f = [e for e in filled_valid if ("UP" if e["m1"] > 0 else "DOWN") == e["predicted"]]
        m1_disagrees_f = [e for e in filled_valid if ("UP" if e["m1"] > 0 else "DOWN") != e["predicted"]]
        print_subheader("M1 agreement — FILLED trades only")
        accuracy_stats(m1_agrees_f, "M1 agrees (filled)", show_pnl_wr=True)
        accuracy_stats(m1_disagrees_f, "M1 disagrees (filled)", show_pnl_wr=True)

    print_subheader("By M1 absolute strength")
    m1_buckets = {
        "|m1| < 0.0005 (weak)":    lambda m: abs(m) < 0.0005,
        "|m1| 0.0005-0.001 (mod)": lambda m: 0.0005 <= abs(m) < 0.001,
        "|m1| 0.001-0.002 (strong)": lambda m: 0.001 <= abs(m) < 0.002,
        "|m1| >= 0.002 (extreme)": lambda m: abs(m) >= 0.002,
    }
    for label, test in m1_buckets.items():
        subset = [e for e in valid if test(e["m1"])]
        accuracy_stats(subset, label, show_pnl_wr=True)


def analyze_cvd_signal(entries):
    """CVD agreement with bridge."""
    print_header("CVD SIGNAL vs ACCURACY")

    valid = [e for e in entries if e["cvd"] is not None]
    if not valid:
        print("  No CVD data available")
        return

    cvd_vals = [e["cvd"] for e in valid]
    print(f"\n  CVD stats: min={min(cvd_vals):.4f}, max={max(cvd_vals):.4f}, "
          f"mean={sum(cvd_vals)/len(cvd_vals):.4f}")

    # CVD agreement with bridge direction
    cvd_agrees = [e for e in valid
                  if ("UP" if e["cvd"] > 0.5 else "DOWN") == ("UP" if e["bridge"] > 0.5 else "DOWN")]
    cvd_disagrees = [e for e in valid
                     if ("UP" if e["cvd"] > 0.5 else "DOWN") != ("UP" if e["bridge"] > 0.5 else "DOWN")]

    print_subheader("CVD agreement with bridge direction")
    accuracy_stats(cvd_agrees, "CVD agrees with bridge", show_pnl_wr=True)
    accuracy_stats(cvd_disagrees, "CVD disagrees with bridge", show_pnl_wr=True)

    # CVD — filled only
    filled_valid = [e for e in valid if e["cost"] > 0]
    if filled_valid:
        ca = [e for e in filled_valid
              if ("UP" if e["cvd"] > 0.5 else "DOWN") == e["predicted"]]
        cd = [e for e in filled_valid
              if ("UP" if e["cvd"] > 0.5 else "DOWN") != e["predicted"]]
        print_subheader("CVD agreement — FILLED trades only")
        accuracy_stats(ca, "CVD agrees (filled)", show_pnl_wr=True)
        accuracy_stats(cd, "CVD disagrees (filled)", show_pnl_wr=True)


def analyze_signal_consensus(entries):
    """When ALL signals agree vs disagree."""
    print_header("SIGNAL CONSENSUS ANALYSIS")

    valid = [e for e in entries if e["m1"] is not None and e["cvd"] is not None]
    if not valid:
        print("  Insufficient data")
        return

    all_agree = []
    mixed = []

    for e in valid:
        bridge_dir = "UP" if e["bridge"] > 0.5 else "DOWN"
        m1_dir = "UP" if e["m1"] > 0 else "DOWN"
        cvd_dir = "UP" if e["cvd"] > 0.5 else "DOWN"

        if bridge_dir == e["predicted"] and m1_dir == e["predicted"] and cvd_dir == e["predicted"]:
            all_agree.append(e)
        else:
            mixed.append(e)

    print_subheader("Consensus levels — ALL trades")
    accuracy_stats(all_agree, "ALL 3 agree with prediction", show_pnl_wr=True)
    accuracy_stats(mixed, "Mixed/disagreeing signals", show_pnl_wr=True)

    # FILLED only
    filled_agree = [e for e in all_agree if e["cost"] > 0]
    filled_mixed = [e for e in mixed if e["cost"] > 0]
    print_subheader("Consensus levels — FILLED trades only")
    accuracy_stats(filled_agree, "ALL 3 agree (filled)", show_pnl_wr=True)
    accuracy_stats(filled_mixed, "Mixed signals (filled)", show_pnl_wr=True)

    # Show every combo
    print_subheader("Every signal combination (B=bridge, M=m1, C=cvd)")
    combos = defaultdict(list)
    for e in valid:
        bridge_dir = "UP" if e["bridge"] > 0.5 else "DOWN"
        m1_dir = "UP" if e["m1"] > 0 else "DOWN"
        cvd_dir = "UP" if e["cvd"] > 0.5 else "DOWN"
        key = f"B={bridge_dir[0]} M={m1_dir[0]} C={cvd_dir[0]} pred={e['predicted'][0]}"
        combos[key].append(e)

    combo_results = []
    for key, es in sorted(combos.items()):
        n = len(es)
        c = sum(1 for e in es if e["correct"])
        acc = c / n if n > 0 else 0
        pnl = sum(e["pnl"] for e in es)
        n_filled = sum(1 for e in es if e["cost"] > 0)
        combo_results.append((key, c, n, acc, pnl, n_filled))

    combo_results.sort(key=lambda x: -x[3])
    for key, c, n, acc, pnl, nf in combo_results:
        if n >= 2:
            print(f"  {key}: {c}/{n} = {acc:.0%} | PnL ${pnl:+.2f} | {nf} filled")


def analyze_false_positives(entries):
    """Which signal is most often wrong?"""
    print_header("FALSE POSITIVE ANALYSIS — Which signal lies most?")

    valid = [e for e in entries if e["m1"] is not None and e["cvd"] is not None]
    if not valid:
        print("  Insufficient data")
        return

    # vs actual result
    bridge_wrong = sum(1 for e in valid if ("UP" if e["bridge"] > 0.5 else "DOWN") != e["result"])
    m1_wrong = sum(1 for e in valid if ("UP" if e["m1"] > 0 else "DOWN") != e["result"])
    cvd_wrong = sum(1 for e in valid if ("UP" if e["cvd"] > 0.5 else "DOWN") != e["result"])
    n = len(valid)

    print(f"\n  Signal vs actual result (all {n} trades):")
    print(f"  Bridge wrong: {bridge_wrong}/{n} = {bridge_wrong/n:.1%}")
    print(f"  M1 wrong:     {m1_wrong}/{n} = {m1_wrong/n:.1%}")
    print(f"  CVD wrong:    {cvd_wrong}/{n} = {cvd_wrong/n:.1%}")

    # Filled only
    filled = [e for e in valid if e["cost"] > 0]
    if filled:
        nf = len(filled)
        bw = sum(1 for e in filled if ("UP" if e["bridge"] > 0.5 else "DOWN") != e["result"])
        mw = sum(1 for e in filled if ("UP" if e["m1"] > 0 else "DOWN") != e["result"])
        cw = sum(1 for e in filled if ("UP" if e["cvd"] > 0.5 else "DOWN") != e["result"])
        print(f"\n  Signal vs actual result (FILLED {nf} trades):")
        print(f"  Bridge wrong: {bw}/{nf} = {bw/nf:.1%}")
        print(f"  M1 wrong:     {mw}/{nf} = {mw/nf:.1%}")
        print(f"  CVD wrong:    {cw}/{nf} = {cw/nf:.1%}")

    # Strong signals that were wrong
    print_subheader("Confident but wrong")
    for name, check_strong, check_wrong in [
        ("Bridge", lambda e: abs(e["bridge"] - 0.5) > 0.15,
         lambda e: ("UP" if e["bridge"] > 0.5 else "DOWN") != e["result"]),
        ("M1", lambda e: abs(e["m1"]) > 0.001,
         lambda e: ("UP" if e["m1"] > 0 else "DOWN") != e["result"]),
        ("CVD", lambda e: abs(e["cvd"] - 0.5) > 0.1,
         lambda e: ("UP" if e["cvd"] > 0.5 else "DOWN") != e["result"]),
    ]:
        strong = [e for e in valid if check_strong(e)]
        if strong:
            wrong = sum(1 for e in strong if check_wrong(e))
            print(f"  Strong {name} wrong: {wrong}/{len(strong)} = {wrong/len(strong):.1%}")


def find_best_filter(entries):
    """Find signal thresholds that would give >50% accuracy."""
    print_header("BEST FILTER SEARCH — What thresholds beat 50%?")

    valid = [e for e in entries if e["m1"] is not None and e["cvd"] is not None]
    if not valid:
        print("  Insufficient data")
        return

    total_n = len(valid)
    results = []

    conviction_thresholds = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
    m1_thresholds = [0.0, 0.0003, 0.0005, 0.001, 0.0015, 0.002]
    cvd_thresholds = [0.0, 0.05, 0.10, 0.15, 0.20]

    # Strategy 1: All signals agree + strength thresholds
    for conv_thresh in conviction_thresholds:
        for m1_thresh in m1_thresholds:
            for cvd_thresh in cvd_thresholds:
                subset = []
                for e in valid:
                    conviction = abs(e["bridge"] - 0.5)
                    bridge_dir = "UP" if e["bridge"] > 0.5 else "DOWN"
                    m1_dir = "UP" if e["m1"] > 0 else "DOWN"
                    cvd_dir = "UP" if e["cvd"] > 0.5 else "DOWN"

                    if (conviction >= conv_thresh
                        and abs(e["m1"]) >= m1_thresh
                        and abs(e["cvd"] - 0.5) >= cvd_thresh
                        and bridge_dir == e["predicted"]
                        and m1_dir == e["predicted"]
                        and cvd_dir == e["predicted"]):
                        subset.append(e)

                if len(subset) >= 3:
                    correct = sum(1 for e in subset if e["correct"])
                    acc = correct / len(subset)
                    pnl = sum(e["pnl"] for e in subset)
                    pnl_wr = sum(1 for e in subset if e["pnl"] > 0)
                    n_filled = sum(1 for e in subset if e["cost"] > 0)
                    results.append({
                        "filter": "all_agree",
                        "conv": conv_thresh, "m1": m1_thresh, "cvd": cvd_thresh,
                        "n": len(subset), "correct": correct, "acc": acc,
                        "pnl": pnl, "pnl_wr": pnl_wr,
                        "n_filled": n_filled,
                        "skip_pct": (1 - len(subset) / total_n) * 100,
                    })

    # Strategy 2: Bridge-only filter
    for conv_thresh in conviction_thresholds:
        subset = [e for e in valid if abs(e["bridge"] - 0.5) >= conv_thresh]
        if len(subset) >= 3:
            correct = sum(1 for e in subset if e["correct"])
            acc = correct / len(subset)
            pnl = sum(e["pnl"] for e in subset)
            pnl_wr = sum(1 for e in subset if e["pnl"] > 0)
            n_filled = sum(1 for e in subset if e["cost"] > 0)
            results.append({
                "filter": "bridge_only",
                "conv": conv_thresh, "m1": "-", "cvd": "-",
                "n": len(subset), "correct": correct, "acc": acc,
                "pnl": pnl, "pnl_wr": pnl_wr,
                "n_filled": n_filled,
                "skip_pct": (1 - len(subset) / total_n) * 100,
            })

    # Strategy 3: Contrarian — take trades where signals disagree with prediction
    # (This tests if the model is systematically wrong)
    for conv_thresh in conviction_thresholds:
        subset = []
        for e in valid:
            conviction = abs(e["bridge"] - 0.5)
            bridge_dir = "UP" if e["bridge"] > 0.5 else "DOWN"
            if conviction >= conv_thresh and bridge_dir != e["predicted"]:
                subset.append(e)
        if len(subset) >= 3:
            correct = sum(1 for e in subset if e["correct"])
            acc = correct / len(subset)
            pnl = sum(e["pnl"] for e in subset)
            pnl_wr = sum(1 for e in subset if e["pnl"] > 0)
            n_filled = sum(1 for e in subset if e["cost"] > 0)
            results.append({
                "filter": "contrarian",
                "conv": conv_thresh, "m1": "-", "cvd": "-",
                "n": len(subset), "correct": correct, "acc": acc,
                "pnl": pnl, "pnl_wr": pnl_wr,
                "n_filled": n_filled,
                "skip_pct": (1 - len(subset) / total_n) * 100,
            })

    results.sort(key=lambda x: (-x["acc"], -x["n"]))

    print_subheader("Top filters with accuracy > 50% (min 3 trades)")
    shown = 0
    for r in results:
        if r["acc"] > 0.50 and shown < 15:
            print(f"  [{r['filter']:12s}] conv>={r['conv']:.2f} m1>={r['m1']} cvd>={r['cvd']} "
                  f"| {r['correct']}/{r['n']} = {r['acc']:.0%} dir "
                  f"| PnL ${r['pnl']:+.2f} "
                  f"| {r['n_filled']} filled "
                  f"| skip {r['skip_pct']:.0f}%")
            shown += 1
    if shown == 0:
        print("  ** NO filter found with >50% accuracy **")

    # Best trade-off
    print_subheader("Best trade-off (accuracy * sqrt(N))")
    for r in results:
        r["score"] = r["acc"] * (r["n"] ** 0.5)
    results.sort(key=lambda x: -x["score"])
    for r in results[:10]:
        print(f"  [{r['filter']:12s}] conv>={r['conv']:.2f} m1>={r['m1']} cvd>={r['cvd']} "
              f"| {r['correct']}/{r['n']} = {r['acc']:.0%} "
              f"| PnL ${r['pnl']:+.2f} "
              f"| {r['n_filled']} filled "
              f"| skip {r['skip_pct']:.0f}%")


def analyze_time(entries):
    """Time-based accuracy analysis."""
    print_header("ACCURACY BY HOUR (HKT)")

    by_hour = defaultdict(list)
    for e in entries:
        try:
            dt = datetime.fromisoformat(e["ts"])
            by_hour[dt.hour].append(e)
        except Exception:
            continue

    for hour in sorted(by_hour.keys()):
        accuracy_stats(by_hour[hour], f"Hour {hour:02d}", show_pnl_wr=True)

    print_subheader("By session")
    asia = [e for h, es in by_hour.items() for e in es if 0 <= h < 8]
    europe = [e for h, es in by_hour.items() for e in es if 8 <= h < 16]
    us = [e for h, es in by_hour.items() for e in es if 16 <= h < 24]
    accuracy_stats(asia, "Asia (00-08 HKT)", show_pnl_wr=True)
    accuracy_stats(europe, "Europe (08-16 HKT)", show_pnl_wr=True)
    accuracy_stats(us, "US (16-24 HKT)", show_pnl_wr=True)

    # Filled-only by hour
    print_subheader("FILLED trades only by hour")
    for hour in sorted(by_hour.keys()):
        filled = [e for e in by_hour[hour] if e["cost"] > 0]
        if filled:
            accuracy_stats(filled, f"Hour {hour:02d} (filled)", show_pnl_wr=True)


def analyze_vol(entries):
    """Accuracy by volatility level."""
    print_header("ACCURACY BY VOLATILITY")

    valid = [e for e in entries if e["vol"] is not None]
    if not valid:
        print("  No vol data available")
        return

    vol_vals = sorted([e["vol"] for e in valid])
    print(f"\n  Vol stats: min={min(vol_vals):.6f}, max={max(vol_vals):.6f}, "
          f"median={vol_vals[len(vol_vals)//2]:.6f}")

    q1 = vol_vals[len(vol_vals) // 4]
    q2 = vol_vals[len(vol_vals) // 2]
    q3 = vol_vals[3 * len(vol_vals) // 4]

    buckets = {
        f"Low vol (<{q1:.6f})":       lambda v: v < q1,
        f"Med-low ({q1:.6f}-{q2:.6f})": lambda v: q1 <= v < q2,
        f"Med-high ({q2:.6f}-{q3:.6f})": lambda v: q2 <= v < q3,
        f"High vol (>={q3:.6f})":     lambda v: v >= q3,
    }
    for label, test in buckets.items():
        subset = [e for e in valid if test(e["vol"])]
        accuracy_stats(subset, label, show_pnl_wr=True)

    # Filled only by vol
    print_subheader("FILLED trades only by vol quartile")
    for label, test in buckets.items():
        subset = [e for e in valid if test(e["vol"]) and e["cost"] > 0]
        if subset:
            accuracy_stats(subset, label + " (filled)", show_pnl_wr=True)


def analyze_ob_adj(entries):
    """OB adjustment analysis."""
    print_header("ORDER BOOK ADJUSTMENT ANALYSIS")

    valid = [e for e in entries if e["ob_adj"] is not None]
    if not valid:
        print("  No OB adj data available")
        return

    ob_vals = [e["ob_adj"] for e in valid]
    print(f"\n  OB adj stats: min={min(ob_vals):.4f}, max={max(ob_vals):.4f}, "
          f"mean={sum(ob_vals)/len(ob_vals):.4f}")

    ob_agrees = [e for e in valid if ("UP" if e["ob_adj"] > 0 else "DOWN") == e["predicted"]]
    ob_disagrees = [e for e in valid if ("UP" if e["ob_adj"] > 0 else "DOWN") != e["predicted"]]

    accuracy_stats(ob_agrees, "OB adj agrees w/ prediction", show_pnl_wr=True)
    accuracy_stats(ob_disagrees, "OB adj disagrees w/ prediction", show_pnl_wr=True)


def print_raw_data(entries):
    """Print every trade for manual inspection."""
    print_header("RAW DATA — Every matched trade")

    # Sort by filled status then time
    sorted_entries = sorted(entries, key=lambda e: (0 if e["cost"] > 0 else 1, e["ts"]))

    print(f"\n  {'#':>3} {'F':>1} {'Pred':>4} {'Result':>6} {'Dir':>3} {'PnL$':>3} "
          f"{'Bridge':>7} {'M1':>10} {'CVD':>6} {'OB_adj':>7} "
          f"{'Vol':>10} {'PnL':>7} {'Cost':>5} {'Hr':>2}")
    print("  " + "-" * 95)

    for i, e in enumerate(sorted_entries, 1):
        try:
            dt = datetime.fromisoformat(e["ts"])
            hour = dt.hour
        except Exception:
            hour = "?"

        dir_ok = "Y" if e["correct"] else "N"
        pnl_ok = "W" if e["pnl"] > 0 else ("L" if e["pnl"] < 0 else "-")
        filled = "F" if e["cost"] > 0 else "."
        m1_str = f"{e['m1']:.6f}" if e["m1"] is not None else "N/A"
        cvd_str = f"{e['cvd']:.3f}" if e["cvd"] is not None else "N/A"
        ob_str = f"{e['ob_adj']:.4f}" if e["ob_adj"] is not None else "N/A"
        vol_str = f"{e['vol']:.6f}" if e["vol"] is not None else "N/A"

        print(f"  {i:3d} {filled:>1} {e['predicted']:>4} {e['result']:>6} {dir_ok:>3} {pnl_ok:>3} "
              f"{e['bridge']:7.4f} {m1_str:>10} {cvd_str:>6} {ob_str:>7} "
              f"{vol_str:>10} {e['pnl']:+7.2f} {e['cost']:5.2f} {hour:>2}")


def summary_diagnosis(entries):
    """Final diagnosis summary."""
    print_header("DIAGNOSIS SUMMARY")

    total = len(entries)
    correct_total = sum(1 for e in entries if e["correct"])
    overall_acc = correct_total / total if total > 0 else 0
    pnl_wins = sum(1 for e in entries if e["pnl"] > 0)
    filled = [e for e in entries if e["cost"] > 0]
    filled_correct = sum(1 for e in filled if e["correct"])
    filled_pnl_wins = sum(1 for e in filled if e["pnl"] > 0)

    print(f"\n  Total matched trades: {total}")
    print(f"  Overall direction accuracy: {correct_total}/{total} = {overall_acc:.1%}")
    print(f"  Overall PnL WR: {pnl_wins}/{total} = {pnl_wins/total:.1%}")
    print(f"  Total PnL: ${sum(e['pnl'] for e in entries):+.2f}")
    print(f"")
    print(f"  FILLED trades: {len(filled)}")
    print(f"  Filled direction accuracy: {filled_correct}/{len(filled)} = "
          f"{filled_correct/len(filled):.1%}" if filled else "  No filled trades")
    print(f"  Filled PnL WR: {filled_pnl_wins}/{len(filled)} = "
          f"{filled_pnl_wins/len(filled):.1%}" if filled else "")
    print(f"  Filled PnL: ${sum(e['pnl'] for e in filled):+.2f}" if filled else "")
    print()

    up_pred = [e for e in entries if e["predicted"] == "UP"]
    dn_pred = [e for e in entries if e["predicted"] == "DOWN"]
    print(f"  UP predictions: {len(up_pred)} | acc: "
          f"{sum(1 for e in up_pred if e['correct'])}/{len(up_pred)} = "
          f"{sum(1 for e in up_pred if e['correct'])/len(up_pred):.1%}" if up_pred else "")
    print(f"  DOWN predictions: {len(dn_pred)} | acc: "
          f"{sum(1 for e in dn_pred if e['correct'])}/{len(dn_pred)} = "
          f"{sum(1 for e in dn_pred if e['correct'])/len(dn_pred):.1%}" if dn_pred else "")
    print(f"  Actual UP outcomes: {sum(1 for e in entries if e['result'] == 'UP')}")
    print(f"  Actual DOWN outcomes: {sum(1 for e in entries if e['result'] == 'DOWN')}")

    # Bot's own WR from state
    state_wr, state_n = load_state_wr()
    if state_wr is not None:
        print(f"\n  Bot's rolling WR (from mm_state.json, last {state_n}): {state_wr:.1%}")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  DIRECTION ACCURACY DEEP ANALYSIS — MM Bot")
    print(f"  Order log: {ORDER_LOG}")
    print(f"  Trades log: {TRADES_LOG}")
    print("=" * 70)

    submits, fills = load_order_log()
    trades = load_trades()

    print(f"\n  Order log: {sum(len(v) for v in submits.values())} submits across "
          f"{len(submits)} conditions")
    print(f"  Fills: {sum(len(v) for v in fills.values())} fills across "
          f"{len(fills)} conditions")
    print(f"  Trades: {len(trades)} resolution records")

    entries, unmatched = match_trades_to_signals(submits, fills, trades)

    # Filter out paper/observe
    live_entries = [e for e in entries if not e["paper"] and not e["observe_only"]]

    print(f"\n  Matched: {len(entries)} ({unmatched} unmatched)")
    print(f"  Live (non-paper, non-observe): {len(live_entries)}")

    if not live_entries:
        print("\n  ** No live entries. Using all matched. **")
        live_entries = entries

    # Run analyses
    summary_diagnosis(live_entries)
    analyze_core_discrepancy(live_entries)
    analyze_adverse_selection_deep(live_entries)
    analyze_bridge_distribution(live_entries)
    analyze_m1_signal(live_entries)
    analyze_cvd_signal(live_entries)
    analyze_signal_consensus(live_entries)
    analyze_false_positives(live_entries)
    find_best_filter(live_entries)
    analyze_time(live_entries)
    analyze_vol(live_entries)
    analyze_ob_adj(live_entries)
    print_raw_data(live_entries)

    print("\n" + "=" * 70)
    print("  ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
