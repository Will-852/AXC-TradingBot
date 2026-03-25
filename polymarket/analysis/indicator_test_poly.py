"""
Polymarket Signal Validation — indicator_test_poly.py
=======================================================
Tests whether Polymarket-specific signals (OB depth imbalance, smart money,
trade flow) predict 15M binary market direction.

Result inference approach:
  Priority 1: shadow_tape.jsonl — ground truth (bot observed actual results)
  Priority 2: signal_tape.jsonl final up_mid — if >= 0.75 → UP, if <= 0.25 → DOWN
  Priority 3: poly_ob_tape last snapshot depth (time_to_end=0 only)

WHY this matters for 5M bot: signals physics is identical, window length differs.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime

# ─────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────
BASE = "/Users/wai/projects/axc-trading/polymarket/logs"

print("Loading data...", flush=True)

with open(f"{BASE}/poly_ob_tape.jsonl") as f:
    ob_tape = [json.loads(l) for l in f]

with open(f"{BASE}/signal_tape.jsonl") as f:
    signal_tape = [json.loads(l) for l in f]

with open(f"{BASE}/shadow_tape.jsonl") as f:
    shadow_tape = [json.loads(l) for l in f]

print(f"  poly_ob_tape: {len(ob_tape):,} rows")
print(f"  signal_tape:  {len(signal_tape):,} rows")
print(f"  shadow_tape:  {len(shadow_tape):,} rows")

# ─────────────────────────────────────────────
# BUILD RESULT LOOKUP
# ─────────────────────────────────────────────
# Step 1: title → cid mapping from signal_tape
title_to_cid = {}
for s in signal_tape:
    for p in s.get("poly", []):
        t = p.get("title")
        cid = p.get("cid")
        if t and cid:
            title_to_cid[t] = cid

# Step 2: cid → result from shadow_tape (ground truth)
result_from_shadow = {}
for row in shadow_tape:
    title = row.get("title")
    cid = title_to_cid.get(title)
    if cid and row.get("result") in ("UP", "DOWN"):
        result_from_shadow[cid] = row["result"]

# Step 3: cid → result from signal_tape final up_mid
sig_by_cid = defaultdict(list)
for s in signal_tape:
    for p in s.get("poly", []):
        cid = p.get("cid")
        um = p.get("up_mid")
        if cid and um is not None:
            sig_by_cid[cid].append({"ts": s["ts"], "up_mid": um})

result_from_signal = {}
for cid, entries in sig_by_cid.items():
    entries_sorted = sorted(entries, key=lambda x: x["ts"])
    last_up_mid = entries_sorted[-1]["up_mid"]
    if last_up_mid >= 0.75:
        result_from_signal[cid] = "UP"
    elif last_up_mid <= 0.25:
        result_from_signal[cid] = "DOWN"

# Step 4: cid → result from poly_ob_tape resolved (time_to_end=0)
ob_by_cid = defaultdict(list)
for row in ob_tape:
    ob_by_cid[row["condition_id"]].append(row)

result_from_ob = {}
for cid, snaps in ob_by_cid.items():
    last = sorted(snaps, key=lambda x: x["ts"])[-1]
    if last.get("time_to_end_s", 999) == 0:
        ub = last.get("up_bid_depth_10", 0)
        db = last.get("down_bid_depth_10", 0)
        if ub > db * 2:
            result_from_ob[cid] = "UP"
        elif db > ub * 2:
            result_from_ob[cid] = "DOWN"

# Merge: shadow > signal > ob
all_cids = set(ob_by_cid.keys())
result_map = {}
source_map = {}
for cid in all_cids:
    if cid in result_from_shadow:
        result_map[cid] = result_from_shadow[cid]
        source_map[cid] = "shadow"
    elif cid in result_from_signal:
        result_map[cid] = result_from_signal[cid]
        source_map[cid] = "signal"
    elif cid in result_from_ob:
        result_map[cid] = result_from_ob[cid]
        source_map[cid] = "ob_resolved"

print(f"\nResult coverage:")
print(f"  from shadow_tape:    {sum(1 for v in source_map.values() if v == 'shadow'):>5}")
print(f"  from signal_tape:    {sum(1 for v in source_map.values() if v == 'signal'):>5}")
print(f"  from ob_resolved:    {sum(1 for v in source_map.values() if v == 'ob_resolved'):>5}")
print(f"  no result found:     {len(all_cids) - len(result_map):>5}")
print(f"  total markets in ob: {len(all_cids):>5}")

# ─────────────────────────────────────────────
# PREPARE PER-MARKET ANALYSIS RECORDS
# ─────────────────────────────────────────────
# For each market with known result, extract:
#   - EARLIEST snapshot (first 5 minutes) for "early signal" tests
#   - LATEST snapshot for "late signal" baseline

# Filter: exclude 0.01/0.99 illiquid markets (both sides at tick min)
ILLIQUID_THRESHOLD = 500  # if up+down_bid_depth < 500 USDC combined, skip

def is_illiquid(snap):
    ub = snap.get("up_bid_depth_10", 0)
    db = snap.get("down_bid_depth_10", 0)
    return (ub + db) < ILLIQUID_THRESHOLD

records = []
for cid, snaps in ob_by_cid.items():
    result = result_map.get(cid)
    if result not in ("UP", "DOWN"):
        continue

    snaps_sorted = sorted(snaps, key=lambda x: x["ts"])
    window_end = snaps_sorted[0].get("window_end_ts", 0)
    time_span = snaps_sorted[0].get("time_to_end_s", 0)

    # Early snapshots: first 5 minutes (time_to_end > window_length - 300)
    # We know window is 15M = 900s. Early = time_to_end > 600
    early_snaps = [s for s in snaps_sorted if s.get("time_to_end_s", 0) > 600]
    if not early_snaps:
        early_snaps = [snaps_sorted[0]]  # fallback: earliest we have

    # Use the EARLIEST non-illiquid snapshot
    first_valid = None
    for s in early_snaps:
        if not is_illiquid(s):
            first_valid = s
            break

    if first_valid is None:
        # All early snapshots are illiquid — skip
        continue

    coin = first_valid.get("coin", "UNKNOWN")
    slug = first_valid.get("slug", "")
    tte = first_valid.get("time_to_end_s", 0)

    # OB depth imbalance
    ub = first_valid.get("up_bid_depth_10", 0)
    db = first_valid.get("down_bid_depth_10", 0)
    ua = first_valid.get("up_ask_depth_10", 0)
    da = first_valid.get("down_ask_depth_10", 0)
    total_bid = ub + db
    ob_imbalance = (ub - db) / total_bid if total_bid > 0 else 0

    # Smart money
    h_smart_side = first_valid.get("h_smart_side", "NONE")
    h_imbalance = first_valid.get("h_imbalance", 0.0)

    # Trade flow
    trade_taker_ratio = first_valid.get("trade_taker_ratio")
    trade_vol_5m = first_valid.get("trade_vol_5m", 0)

    # ph_price (consensus price)
    ph_price = first_valid.get("ph_price")
    ph_momentum = first_valid.get("ph_momentum", 0)

    records.append({
        "cid": cid,
        "coin": coin,
        "slug": slug,
        "result": result,
        "tte_at_snap": tte,
        "ob_imbalance": ob_imbalance,
        "ub": ub,
        "db": db,
        "h_smart_side": h_smart_side,
        "h_imbalance": h_imbalance,
        "trade_taker_ratio": trade_taker_ratio,
        "trade_vol_5m": trade_vol_5m,
        "ph_price": ph_price,
        "ph_momentum": ph_momentum,
        "result_source": source_map.get(cid, "unknown"),
    })

print(f"\nAnalysis records after liquidity filter: {len(records)}")
up_count = sum(1 for r in records if r["result"] == "UP")
down_count = sum(1 for r in records if r["result"] == "DOWN")
print(f"  UP: {up_count} ({up_count/len(records):.1%})   DOWN: {down_count} ({down_count/len(records):.1%})")


# ─────────────────────────────────────────────
# HELPER: Win Rate Table
# ─────────────────────────────────────────────
def wr_table(label, groups):
    """groups: list of (group_label, predicts_up_bool, list_of_results)"""
    header = f"\n{'Group':<35} {'N':>6} {'WR%':>7} {'UP':>6} {'DOWN':>6}"
    print(header)
    print("─" * len(header))
    for g_label, pred_up, results in groups:
        if not results:
            print(f"{g_label:<35} {'—':>6}")
            continue
        n = len(results)
        correct = sum(1 for r in results if (r == "UP") == pred_up)
        wr = correct / n
        up_n = sum(1 for r in results if r == "UP")
        dn_n = n - up_n
        bar = "▓" * int(wr * 20) + "░" * (20 - int(wr * 20))
        print(f"{g_label:<35} {n:>6} {wr:>6.1%}  {up_n:>6} {dn_n:>6}  {bar}")


# ─────────────────────────────────────────────
# BASELINE
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("BASELINE (random = 50%)")
print("=" * 65)
all_results = [r["result"] for r in records]
base_up_wr = sum(1 for r in all_results if r == "UP") / len(all_results)
print(f"  All markets: N={len(all_results)}, UP={base_up_wr:.1%}, DOWN={1-base_up_wr:.1%}")
print(f"  Naive predict-always-DOWN WR: {1-base_up_wr:.1%}")


# ─────────────────────────────────────────────
# PART A: OB DEPTH IMBALANCE
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PART A: OB Depth Imbalance → Direction Prediction")
print("=" * 65)
print("Signal: (up_bid_depth_10 - down_bid_depth_10) / total")
print("Prediction: imbalance > 0 → UP, imbalance < 0 → DOWN")

# Bucket by imbalance strength
buckets_a = [
    ("Strong DOWN  imb < -0.3",  False, [r["result"] for r in records if r["ob_imbalance"] < -0.3]),
    ("Moderate DOWN  -0.3 to -0.1", False, [r["result"] for r in records if -0.3 <= r["ob_imbalance"] < -0.1]),
    ("Neutral  -0.1 to 0.1",   True,  [r["result"] for r in records if -0.1 <= r["ob_imbalance"] <= 0.1]),
    ("Moderate UP  0.1 to 0.3",  True,  [r["result"] for r in records if 0.1 < r["ob_imbalance"] <= 0.3]),
    ("Strong UP  imb > 0.3",    True,  [r["result"] for r in records if r["ob_imbalance"] > 0.3]),
]
wr_table("OB Imbalance Bucket", buckets_a)

# Overall directional accuracy
ob_directional = [r for r in records if abs(r["ob_imbalance"]) > 0.0]
correct_ob = sum(1 for r in ob_directional
                 if (r["ob_imbalance"] > 0 and r["result"] == "UP")
                 or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
print(f"\nOverall directional accuracy (imb != 0): {correct_ob}/{len(ob_directional)} = {correct_ob/len(ob_directional):.1%}")

# Strong signal subset
strong_ob = [r for r in records if abs(r["ob_imbalance"]) > 0.2]
if strong_ob:
    correct_strong = sum(1 for r in strong_ob
                         if (r["ob_imbalance"] > 0 and r["result"] == "UP")
                         or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    print(f"Strong signal (|imb| > 0.2):             {correct_strong}/{len(strong_ob)} = {correct_strong/len(strong_ob):.1%}")

# By coin
print("\nBy coin (|imb| > 0.1):")
for coin in ["BTC", "ETH", "SOL"]:
    sub = [r for r in records if r["coin"] == coin and abs(r["ob_imbalance"]) > 0.1]
    if sub:
        c = sum(1 for r in sub
                if (r["ob_imbalance"] > 0 and r["result"] == "UP")
                or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
        print(f"  {coin}: {c}/{len(sub)} = {c/len(sub):.1%}")

# By time_to_end (how early in the market)
print("\nBy time remaining when signal taken (imb > 0.1):")
early_records = sorted(records, key=lambda x: x["tte_at_snap"], reverse=True)
time_buckets = [
    ("> 700s (very early)", [r for r in records if r["tte_at_snap"] > 700]),
    ("500-700s",            [r for r in records if 500 < r["tte_at_snap"] <= 700]),
    ("300-500s",            [r for r in records if 300 < r["tte_at_snap"] <= 500]),
    ("< 300s (late)",       [r for r in records if r["tte_at_snap"] <= 300]),
]
for label, sub in time_buckets:
    if not sub:
        print(f"  {label:<25} N=0")
        continue
    c = sum(1 for r in sub
            if (r["ob_imbalance"] > 0 and r["result"] == "UP")
            or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    print(f"  {label:<25} N={len(sub):>4}  acc={c/len(sub):.1%}")


# ─────────────────────────────────────────────
# PART B: SMART MONEY SIGNAL
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PART B: Smart Money Signal (h_smart_side)")
print("=" * 65)

smart_records = [r for r in records if r["h_smart_side"] != "NONE" and r["h_smart_side"] is not None]
print(f"Records with smart money signal: {len(smart_records)} / {len(records)}")

smart_counts = defaultdict(int)
for r in records:
    smart_counts[r.get("h_smart_side", "NONE")] += 1
print(f"h_smart_side distribution:")
for k, v in sorted(smart_counts.items()):
    print(f"  {k}: {v}")

buckets_b = []
# h_smart_side = UP: smart money bet UP → predict UP
up_smart = [r["result"] for r in records if r["h_smart_side"] == "UP"]
buckets_b.append(("smart_side=UP → predict UP", True, up_smart))

# h_smart_side = DOWN: smart money bet DOWN → predict DOWN
dn_smart = [r["result"] for r in records if r["h_smart_side"] == "DOWN"]
buckets_b.append(("smart_side=DOWN → predict DOWN", False, dn_smart))

# h_smart_side = BOTH: both sides have smart money
both_smart = [r["result"] for r in records if r["h_smart_side"] == "BOTH"]
buckets_b.append(("smart_side=BOTH (coin flip, pred UP)", True, both_smart))

# NONE: no smart money
none_smart = [r["result"] for r in records if r["h_smart_side"] in ("NONE", None)]
buckets_b.append(("smart_side=NONE (baseline)", True, none_smart))

wr_table("Smart Money", buckets_b)

# Compare: smart money correct vs ob_imbalance agrees
print("\nSmart money vs OB imbalance agreement:")
for smart_val in ("UP", "DOWN"):
    ob_dir = 1 if smart_val == "UP" else -1
    agree = [r for r in records if r["h_smart_side"] == smart_val
             and (ob_dir * r["ob_imbalance"]) > 0.1]
    disagree = [r for r in records if r["h_smart_side"] == smart_val
                and (ob_dir * r["ob_imbalance"]) < -0.1]
    if agree:
        c = sum(1 for r in agree if r["result"] == smart_val)
        print(f"  smart={smart_val} + OB agrees:    N={len(agree):>4}  WR={c/len(agree):.1%}")
    if disagree:
        c = sum(1 for r in disagree if r["result"] == smart_val)
        print(f"  smart={smart_val} + OB disagrees: N={len(disagree):>4}  WR={c/len(disagree):.1%}")


# ─────────────────────────────────────────────
# PART C: TRADE FLOW (TAKER RATIO)
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PART C: Trade Flow — Taker Ratio")
print("=" * 65)
print("trade_taker_ratio = buy_vol / (buy_vol + sell_vol)")
print("High ratio → more buying → predict UP")

flow_records = [r for r in records if r["trade_taker_ratio"] is not None]
print(f"Records with trade_taker_ratio: {len(flow_records)} / {len(records)}")

# Distribution
ratios = sorted(r["trade_taker_ratio"] for r in flow_records)
if ratios:
    p25 = ratios[len(ratios) // 4]
    p50 = ratios[len(ratios) // 2]
    p75 = ratios[3 * len(ratios) // 4]
    print(f"  Distribution: p25={p25:.3f}  median={p50:.3f}  p75={p75:.3f}")

buckets_c = [
    ("taker_ratio < 0.3 (heavy selling)", False,
     [r["result"] for r in flow_records if r["trade_taker_ratio"] < 0.3]),
    ("taker_ratio 0.3-0.45 (moderate sell)", False,
     [r["result"] for r in flow_records if 0.3 <= r["trade_taker_ratio"] < 0.45]),
    ("taker_ratio 0.45-0.55 (neutral)",     True,
     [r["result"] for r in flow_records if 0.45 <= r["trade_taker_ratio"] <= 0.55]),
    ("taker_ratio 0.55-0.7 (moderate buy)", True,
     [r["result"] for r in flow_records if 0.55 < r["trade_taker_ratio"] <= 0.7]),
    ("taker_ratio > 0.7 (heavy buying)",    True,
     [r["result"] for r in flow_records if r["trade_taker_ratio"] > 0.7]),
]
wr_table("Trade Flow (Taker Ratio)", buckets_c)

# Low vs high volume
print("\nTrade volume cut (does more flow = better signal?):")
vol_records = [r for r in flow_records if r["trade_vol_5m"] is not None]
vols = sorted(r["trade_vol_5m"] for r in vol_records)
if vols:
    med_vol = vols[len(vols) // 2]
    print(f"  Median 5m trade vol: ${med_vol:.0f}")
    high_vol = [r for r in vol_records if r["trade_vol_5m"] >= med_vol]
    low_vol = [r for r in vol_records if r["trade_vol_5m"] < med_vol]
    for label, sub in [("High vol (>= median)", high_vol), ("Low vol (< median)", low_vol)]:
        if sub:
            c = sum(1 for r in sub
                    if (r["trade_taker_ratio"] > 0.55 and r["result"] == "UP")
                    or (r["trade_taker_ratio"] < 0.45 and r["result"] == "DOWN"))
            sig_sub = [r for r in sub if abs(r["trade_taker_ratio"] - 0.5) > 0.05]
            if sig_sub:
                c2 = sum(1 for r in sig_sub
                         if (r["trade_taker_ratio"] > 0.55 and r["result"] == "UP")
                         or (r["trade_taker_ratio"] < 0.45 and r["result"] == "DOWN"))
                print(f"  {label:<25}  N={len(sig_sub):>4}  directional acc={c2/len(sig_sub):.1%}")


# ─────────────────────────────────────────────
# PART D: COMBINED SIGNALS
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PART D: Combined Signals")
print("=" * 65)

def signal_direction(r):
    """Return (+1, -1, or 0) for each signal, 0 = no opinion"""
    ob = 1 if r["ob_imbalance"] > 0.1 else (-1 if r["ob_imbalance"] < -0.1 else 0)
    smart = 1 if r["h_smart_side"] == "UP" else (-1 if r["h_smart_side"] == "DOWN" else 0)
    flow = (1 if (r["trade_taker_ratio"] or 0.5) > 0.6 else
            (-1 if (r["trade_taker_ratio"] or 0.5) < 0.4 else 0))
    return ob, smart, flow

# Score = sum of active signals (range -3 to +3)
def consensus_score(r):
    ob, smart, flow = signal_direction(r)
    return ob + smart + flow

# Add score to records
for r in records:
    r["consensus"] = consensus_score(r)

score_groups = []
for score in range(-3, 4):
    subset = [r for r in records if r["consensus"] == score]
    if not subset:
        continue
    pred_up = score >= 0
    label = f"score={score:+d} ({'↑' if score > 0 else '↓' if score < 0 else '~'})"
    score_groups.append((label, pred_up, [r["result"] for r in subset]))

wr_table("Consensus Score (OB + Smart + Flow)", score_groups)

# Strong agreement vs disagreement
all_agree_up = [r for r in records if r["consensus"] == 3]
all_agree_dn = [r for r in records if r["consensus"] == -3]
partial_up = [r for r in records if r["consensus"] == 2]
partial_dn = [r for r in records if r["consensus"] == -2]
mixed = [r for r in records if r["consensus"] in (0, 1, -1)]

print("\nKey combined buckets:")
for label, pred_up, subset in [
    ("All 3 agree UP (score=+3)",    True,  all_agree_up),
    ("All 3 agree DOWN (score=-3)",  False, all_agree_dn),
    ("2/3 agree UP (score=+2)",      True,  partial_up),
    ("2/3 agree DOWN (score=-2)",    False, partial_dn),
    ("Mixed/neutral (score -1,0,+1)",True,  mixed),
]:
    if not subset:
        continue
    n = len(subset)
    c = sum(1 for r in subset if (pred_up and r["result"] == "UP") or (not pred_up and r["result"] == "DOWN"))
    print(f"  {label:<40} N={n:>4}  WR={c/n:.1%}")


# ─────────────────────────────────────────────
# PART E: OB-ONLY DEEP DIVE (for 5M bot)
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PART E: OB Imbalance Deep Dive (5M relevance)")
print("=" * 65)

# For a 5M bot, we'll see markets even earlier (time_to_end > 200s of 300s total)
# Simulate: what's the accuracy when we look at signals early (> 600s into 900s market = first 5min)
early = [r for r in records if r["tte_at_snap"] > 600]
print(f"Early snapshots (>600s to end, first 5 min of 15M): {len(early)}")

# Imbalance thresholds
print("\nOB imbalance threshold sweep (early snapshots only):")
thresholds = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
print(f"{'Threshold':>10}  {'N':>6}  {'Acc':>7}  {'Coverage':>9}")
print("─" * 40)
for thr in thresholds:
    sub = [r for r in early if abs(r["ob_imbalance"]) >= thr]
    if not sub:
        print(f"{thr:>10.2f}  {'—':>6}")
        continue
    correct = sum(1 for r in sub
                  if (r["ob_imbalance"] > 0 and r["result"] == "UP")
                  or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    coverage = len(sub) / len(early) if early else 0
    print(f"{thr:>10.2f}  {len(sub):>6}  {correct/len(sub):>6.1%}  {coverage:>8.1%}")

# Coin breakdown at 0.2 threshold
print("\nCoin breakdown at |imb| >= 0.2 (early snapshots):")
for coin in ["BTC", "ETH", "SOL"]:
    sub = [r for r in early if r["coin"] == coin and abs(r["ob_imbalance"]) >= 0.2]
    if sub:
        c = sum(1 for r in sub
                if (r["ob_imbalance"] > 0 and r["result"] == "UP")
                or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
        print(f"  {coin}: N={len(sub):>4}  acc={c/len(sub):.1%}  (UP={sum(1 for r in sub if r['result']=='UP')}  DN={sum(1 for r in sub if r['result']=='DOWN')})")


# ─────────────────────────────────────────────
# PART F: SIGNAL QUALITY CHECKS
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PART F: Data Quality Issues & Caveats")
print("=" * 65)

total_in_ob = len(ob_by_cid)
illiquid_count = 0
for cid, snaps in ob_by_cid.items():
    snaps_sorted = sorted(snaps, key=lambda x: x["ts"])
    early_snaps = [s for s in snaps_sorted if s.get("time_to_end_s", 0) > 600]
    if not early_snaps:
        early_snaps = [snaps_sorted[0]]
    if all(is_illiquid(s) for s in early_snaps):
        illiquid_count += 1

print(f"Markets filtered out (illiquid early snapshots): {illiquid_count} / {total_in_ob}")
print(f"Markets missing result:    {total_in_ob - len(result_map)}")
print(f"Markets in analysis:       {len(records)}")

no_smart = sum(1 for r in records if r["h_smart_side"] in ("NONE", None))
no_flow = sum(1 for r in records if r["trade_taker_ratio"] is None)
print(f"Records missing smart signal:  {no_smart} ({no_smart/len(records):.1%})")
print(f"Records missing trade flow:    {no_flow} ({no_flow/len(records):.1%})")

# Check implication lookback bias
# The result might be in part CAUSED by the OB imbalance persisting
# (i.e., if we're reading OB too close to resolution, it's circular)
close_snaps = [r for r in records if r["tte_at_snap"] < 200]
print(f"\nWarning: {len(close_snaps)} records have tte < 200s (potential result-leakage)")
print("  These might show inflated accuracy if the OB already reflects the result")
if close_snaps:
    c = sum(1 for r in close_snaps
            if (r["ob_imbalance"] > 0 and r["result"] == "UP")
            or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    print(f"  Late snapshot accuracy: {c}/{len(close_snaps)} = {c/len(close_snaps):.1%} (vs early: check Part E)")


# ─────────────────────────────────────────────
# SUMMARY TABLE
# ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("SUMMARY: Signal Predictive Power")
print("=" * 65)

summary_rows = []

# Baseline
n_all = len(records)
summary_rows.append(("Baseline (always DOWN)", n_all, 1 - base_up_wr))

# OB strong signal
strong = [r for r in records if abs(r["ob_imbalance"]) > 0.3]
if strong:
    c = sum(1 for r in strong
            if (r["ob_imbalance"] > 0 and r["result"] == "UP")
            or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    summary_rows.append(("OB imbalance |>0.3|", len(strong), c / len(strong)))

# Smart money
sm = [r for r in records if r["h_smart_side"] in ("UP", "DOWN")]
if sm:
    c = sum(1 for r in sm
            if (r["h_smart_side"] == "UP" and r["result"] == "UP")
            or (r["h_smart_side"] == "DOWN" and r["result"] == "DOWN"))
    summary_rows.append(("Smart money directional", len(sm), c / len(sm)))

# Trade flow (strong)
sf = [r for r in flow_records if abs(r["trade_taker_ratio"] - 0.5) > 0.2]
if sf:
    c = sum(1 for r in sf
            if (r["trade_taker_ratio"] > 0.5 and r["result"] == "UP")
            or (r["trade_taker_ratio"] < 0.5 and r["result"] == "DOWN"))
    summary_rows.append(("Trade flow (taker |>0.7 or <0.3|)", len(sf), c / len(sf)))

# All 3 agree
all3 = [r for r in records if abs(r["consensus"]) == 3]
if all3:
    c = sum(1 for r in all3
            if (r["consensus"] == 3 and r["result"] == "UP")
            or (r["consensus"] == -3 and r["result"] == "DOWN"))
    summary_rows.append(("All 3 signals agree", len(all3), c / len(all3)))

# 2+ agree
twop = [r for r in records if abs(r["consensus"]) >= 2]
if twop:
    c = sum(1 for r in twop
            if (r["consensus"] > 0 and r["result"] == "UP")
            or (r["consensus"] < 0 and r["result"] == "DOWN"))
    summary_rows.append(("2+ signals agree", len(twop), c / len(twop)))

print(f"\n{'Signal':<40} {'N':>6}  {'WR%':>7}  {'Lift':>7}")
print("─" * 65)
baseline_wr = summary_rows[0][2]
for label, n, wr in summary_rows:
    lift = wr - baseline_wr
    lift_str = f"{lift:+.1%}" if label != "Baseline (always DOWN)" else "  —"
    print(f"{label:<40} {n:>6}  {wr:>6.1%}  {lift_str:>7}")

print("\n" + "=" * 65)
print("KEY CONCLUSIONS")
print("=" * 65)
print()

# Auto-generate key conclusions
best_signal = max(summary_rows[1:], key=lambda x: x[2], default=None)
if best_signal:
    label, n, wr = best_signal
    lift = wr - baseline_wr
    print(f"1. Best single signal: '{label}' — WR={wr:.1%}, lift={lift:+.1%}")

combined_row = next((r for r in summary_rows if r[0] == "All 3 signals agree"), None)
if combined_row:
    label, n, wr = combined_row
    lift = wr - baseline_wr
    print(f"2. Combined (all 3 agree): N={n}, WR={wr:.1%}, lift={lift:+.1%}")

# OB accuracy on early vs late
early_strong = [r for r in early if abs(r["ob_imbalance"]) > 0.2]
if early_strong:
    c = sum(1 for r in early_strong
            if (r["ob_imbalance"] > 0 and r["result"] == "UP")
            or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    print(f"3. OB imbalance EARLY (tte>600, |imb|>0.2): N={len(early_strong)}, acc={c/len(early_strong):.1%}")

late_strong = [r for r in records if r["tte_at_snap"] < 300 and abs(r["ob_imbalance"]) > 0.2]
if late_strong:
    c = sum(1 for r in late_strong
            if (r["ob_imbalance"] > 0 and r["result"] == "UP")
            or (r["ob_imbalance"] < 0 and r["result"] == "DOWN"))
    print(f"4. OB imbalance LATE (tte<300, |imb|>0.2):  N={len(late_strong)}, acc={c/len(late_strong):.1%}")

print()
print("⚠  Caveats:")
print("   - Data is 15M markets; 5M physics should be similar but unvalidated")
print("   - Result inference via signal_tape up_mid is not ground truth for all markets")
print("   - Sample size drives confidence: N<50 = unreliable, N>200 = usable")


# ─────────────────────────────────────────────
# PART G: SMART MONEY EXTENDED (all snapshots, not just earliest)
# ─────────────────────────────────────────────
# The early-snapshot filter in Part B excluded most smart money signals
# because h_smart_side fires predominantly late in the market (median TTE ~200s).
# Here we test using the FIRST qualifying smart-signal snapshot per market.
print("\n" + "=" * 65)
print("PART G: Smart Money Extended (all snapshots, tte > 200s)")
print("=" * 65)
print("NOTE: h_smart_side fires late (median TTE ~200s). Part B missed most of them.")
print("This section finds the FIRST snapshot per market where smart signal is active.\n")

records_smart_ext = []
for cid, snaps in ob_by_cid.items():
    result = result_map.get(cid)
    if result not in ("UP", "DOWN"):
        continue
    snaps_sorted = sorted(snaps, key=lambda x: x["ts"])
    # First snapshot where smart side is set AND we still have time to act
    for s in snaps_sorted:
        hs = s.get("h_smart_side")
        tte = s.get("time_to_end_s", 0)
        if hs not in ("NONE", None) and tte > 200:
            records_smart_ext.append({
                "cid": cid,
                "coin": s.get("coin"),
                "result": result,
                "h_smart_side": hs,
                "h_imbalance": s.get("h_imbalance", 0.0),
                "ob_imbalance": (lambda ub, db: (ub - db) / (ub + db) if (ub + db) > 0 else 0)(
                    s.get("up_bid_depth_10", 0), s.get("down_bid_depth_10", 0)),
                "tte": tte,
            })
            break  # earliest qualifying snapshot

print(f"Markets with actionable smart signal (tte>200s): {len(records_smart_ext)}")

if records_smart_ext:
    smart_ext_dist = defaultdict(int)
    for r in records_smart_ext:
        smart_ext_dist[r["h_smart_side"]] += 1
    for k, v in sorted(smart_ext_dist.items()):
        print(f"  {k}: {v}")

    print()
    ext_groups = []
    for smart_val in ("UP", "DOWN", "BOTH"):
        subset = [r for r in records_smart_ext if r["h_smart_side"] == smart_val]
        if not subset:
            continue
        pred_up = smart_val == "UP"
        results = [r["result"] for r in subset]
        ext_groups.append((f"smart={smart_val} → predict {smart_val if smart_val != 'BOTH' else 'UP'}", pred_up, results))
    wr_table("Smart Money Extended", ext_groups)

    # h_imbalance continuous predictor (broader test)
    print("\nh_imbalance as continuous predictor (all markets, first snap with |h_imb|>threshold):")
    himb_ext = []
    for cid, snaps in ob_by_cid.items():
        result = result_map.get(cid)
        if result not in ("UP", "DOWN"):
            continue
        for s in sorted(snaps, key=lambda x: x["ts"]):
            tte = s.get("time_to_end_s", 0)
            himb = s.get("h_imbalance")
            if himb is not None and tte > 300 and abs(himb) > 0.0:
                himb_ext.append({"result": result, "h_imbalance": himb})
                break

    print(f"  Records: {len(himb_ext)}")
    print(f"  {'Threshold':>10}  {'N':>6}  {'Acc':>7}")
    print("  " + "─" * 30)
    for thr in [0.05, 0.10, 0.15, 0.20]:
        sub = [r for r in himb_ext if abs(r["h_imbalance"]) >= thr]
        if not sub:
            continue
        c = sum(1 for r in sub
                if (r["h_imbalance"] > 0 and r["result"] == "UP")
                or (r["h_imbalance"] < 0 and r["result"] == "DOWN"))
        print(f"  {thr:>10.2f}  {len(sub):>6}  {c/len(sub):>6.1%}")

    # Anti-pattern check: does smart money predict AGAINST direction?
    print("\nAnti-pattern check (smart money as CONTRARIAN signal):")
    anti_groups = []
    for smart_val, opposite in [("UP", "DOWN"), ("DOWN", "UP")]:
        subset = [r for r in records_smart_ext if r["h_smart_side"] == smart_val]
        if not subset:
            continue
        results = [r["result"] for r in subset]
        pred_up = opposite == "UP"
        anti_groups.append((f"smart={smart_val} → predict OPPOSITE ({opposite})", pred_up, results))
    if anti_groups:
        wr_table("Contrarian Smart Money", anti_groups)

print()
print("Done.")
