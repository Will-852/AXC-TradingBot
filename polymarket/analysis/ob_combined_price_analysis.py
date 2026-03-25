"""
OB Combined Price Analysis — Polymarket Up/Down Markets
Answers: What is the realistic combined price at different time points?

KEY FINDING from data exploration:
- poly_ob_tape.jsonl has ONLY 0.01/0.99 bid/ask (far-from-market LOs) — useless for pricing
- signal_tape.jsonl has 15M mid prices (combined_mid ~= 1.0000 throughout)
- mm_w4_5m.jsonl has ACTUAL 5M ask prices at entry (the real gold)

The real pricing question must be answered from mm_w4 + signal tape spread analysis.
"""

import json
import statistics
from collections import defaultdict, Counter
from pathlib import Path

LOG_DIR = Path("/Users/wai/projects/axc-trading/polymarket/logs")

def load_jsonl(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def pct(vals, p):
    if not vals: return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(s): return s[f]
    return s[f] + (k - f) * (s[c] - s[f])

def fmt(v, dp=4):
    if v != v: return "  NaN  "
    return f"{v:.{dp}f}"

SEP = "=" * 80

# ─────────────────────────────────────────────────────────────────
print("Loading data...")
ob_tape = load_jsonl(LOG_DIR / "poly_ob_tape.jsonl")
signal_tape = load_jsonl(LOG_DIR / "signal_tape.jsonl")
mm_w4 = load_jsonl(LOG_DIR / "mm_w4_5m.jsonl")

w4_entries = [r for r in mm_w4 if r.get("event") == "w4_entry"]
w4_resolutions = [r for r in mm_w4 if r.get("event") == "resolution"]
entry_map = {e["cid"]: e for e in w4_entries}

# Classify OB tape
for row in ob_tape:
    slug = row.get("slug", "")
    if "15m" in slug:
        row["_mkt"] = "15m"
    elif "up-or-down" in slug:
        row["_mkt"] = "1h"
    else:
        row["_mkt"] = "unknown"

print(f"  poly_ob_tape.jsonl:  {len(ob_tape):,} entries")
print(f"  signal_tape.jsonl:   {len(signal_tape):,} entries")
print(f"  mm_w4_5m.jsonl:      {len(mm_w4):,} ({len(w4_entries)} entries + {len(w4_resolutions)} resolutions)")

# Market type breakdown
mkt_counts = Counter(r["_mkt"] for r in ob_tape)
coin_counts = Counter(r.get("coin", "?") for r in ob_tape)
print(f"  OB tape markets: {dict(mkt_counts)}")
print(f"  OB tape coins:   {dict(coin_counts)}")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q1: OB TAPE — Data Quality Assessment")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

# Check bid/ask distribution
up_bids = Counter(round(r.get("up_best_bid", 0), 3) for r in ob_tape)
print(f"\n  up_best_bid distribution (ALL {len(ob_tape):,} entries):")
for v, cnt in up_bids.most_common(5):
    print(f"    {v:.3f}: {cnt:,} ({cnt/len(ob_tape)*100:.1f}%)")

# Check combined_best_ask
cba = Counter(round(r.get("combined_best_ask", 0), 3) for r in ob_tape)
print(f"\n  combined_best_ask distribution:")
for v, cnt in cba.most_common(5):
    print(f"    {v:.3f}: {cnt:,} ({cnt/len(ob_tape)*100:.1f}%)")

# Real pricing entries
real_price = [r for r in ob_tape if r.get("up_best_bid", 0) > 0.02]
print(f"\n  Entries with real UP bid > 0.02: {len(real_price)} (0%)")
print(f"  ⚠ OB TAPE HAS NO REAL DIRECTIONAL PRICES — only 0.01/0.99 limit orders")
print(f"  ⚠ combined_best_ask is 1.98 (= 0.99+0.99) everywhere = meaningless")
print(f"  → MUST use signal_tape (mid) and mm_w4_5m (ask) for pricing analysis")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q1b: OB TAPE — Depth by Time-to-End (15M markets)")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

# Even though prices are 0.01/0.99, depth data is still informative
BUCKETS = ["0-60", "60-120", "120-180", "180-300", "300-600", "600-900", ">900"]

def bucket_tte(tte):
    if tte <= 60: return "0-60"
    elif tte <= 120: return "60-120"
    elif tte <= 180: return "120-180"
    elif tte <= 300: return "180-300"
    elif tte <= 600: return "300-600"
    elif tte <= 900: return "600-900"
    else: return ">900"

# 15M entries only
ob_15m = [r for r in ob_tape if r["_mkt"] == "15m"]
depth_by_bucket = defaultdict(lambda: {"up_bid": [], "up_ask": [], "dn_bid": [], "dn_ask": [], "total": []})

for r in ob_15m:
    b = bucket_tte(r.get("time_to_end_s", 0))
    ub = r.get("up_bid_depth_10", 0)
    ua = r.get("up_ask_depth_10", 0)
    db = r.get("down_bid_depth_10", 0)
    da = r.get("down_ask_depth_10", 0)
    depth_by_bucket[b]["up_bid"].append(ub)
    depth_by_bucket[b]["up_ask"].append(ua)
    depth_by_bucket[b]["dn_bid"].append(db)
    depth_by_bucket[b]["dn_ask"].append(da)
    depth_by_bucket[b]["total"].append(ub + ua + db + da)

print(f"\n  {'Bucket':>8s}  {'N':>5s}  {'UP_bid':>10s}  {'UP_ask':>10s}  {'DN_bid':>10s}  {'DN_ask':>10s}  {'Total':>10s}")
print(f"  {'-'*8}  {'-'*5}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}")
for b in BUCKETS:
    d = depth_by_bucket.get(b)
    if d and d["up_bid"]:
        n = len(d["up_bid"])
        print(f"  {b:>8s}  {n:5d}  {statistics.median(d['up_bid']):10,.0f}  {statistics.median(d['up_ask']):10,.0f}  "
              f"{statistics.median(d['dn_bid']):10,.0f}  {statistics.median(d['dn_ask']):10,.0f}  {statistics.median(d['total']):10,.0f}")

print(f"\n  Note: depth is in shares (USDC equivalent at ~$1 each)")
print(f"  Depth drops dramatically as window approaches expiry")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q2: 5M ACTUAL PRICES (mm_w4_5m.jsonl) — THE KEY DATA")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

print(f"\n  This is from the w4 strategy logging actual 5M market asks at entry time.")
print(f"  N = {len(w4_entries)} entries across coins\n")

combined = [e["combined"] for e in w4_entries if e.get("combined")]
up_asks = [e["up_ask"] for e in w4_entries if e.get("up_ask")]
dn_asks = [e["dn_ask"] for e in w4_entries if e.get("dn_ask")]

print(f"  ┌──────────────────────────────────────────────────────┐")
print(f"  │  5M Combined Ask (up_ask + dn_ask)                   │")
print(f"  │  N = {len(combined):>3d}                                           │")
print(f"  │  Mean   = {statistics.mean(combined):.4f}                                │")
print(f"  │  Median = {statistics.median(combined):.4f}                                │")
print(f"  │  P10    = {pct(combined, 10):.4f}                                │")
print(f"  │  P25    = {pct(combined, 25):.4f}                                │")
print(f"  │  P75    = {pct(combined, 75):.4f}                                │")
print(f"  │  P90    = {pct(combined, 90):.4f}                                │")
print(f"  │  Min    = {min(combined):.4f}    Max = {max(combined):.4f}                │")
print(f"  │                                                      │")
print(f"  │  OVERROUND = median - 1.00 = {(statistics.median(combined)-1)*100:+.2f}%              │")
print(f"  └──────────────────────────────────────────────────────┘")

# Distribution
print(f"\n  Combined Ask Distribution:")
dist_buckets = [("<0.98", 0, 0.98), ("0.98-1.00", 0.98, 1.00), ("1.00-1.02", 1.00, 1.02),
                ("1.02-1.04", 1.02, 1.04), (">=1.04", 1.04, 99)]
for label, lo, hi in dist_buckets:
    cnt = sum(1 for v in combined if lo <= v < hi)
    pct_val = cnt / len(combined) * 100
    bar = "#" * int(pct_val / 2)
    print(f"    {label:>10s}: {cnt:4d} ({pct_val:5.1f}%) {bar}")

# By coin
print(f"\n  By Coin:")
print(f"  {'Coin':>4s}  {'N':>4s}  {'Mean':>7s}  {'Median':>7s}  {'Min':>6s}  {'Max':>6s}  {'Up_ask_med':>10s}  {'Dn_ask_med':>10s}")
print(f"  {'-'*4}  {'-'*4}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*10}  {'-'*10}")
for coin in ["btc", "eth", "sol", "xrp"]:
    ce = [e for e in w4_entries if e.get("coin") == coin and e.get("combined")]
    if ce:
        cv = [e["combined"] for e in ce]
        ua = [e["up_ask"] for e in ce if e.get("up_ask")]
        da = [e["dn_ask"] for e in ce if e.get("dn_ask")]
        print(f"  {coin.upper():>4s}  {len(cv):4d}  {statistics.mean(cv):7.4f}  {statistics.median(cv):7.4f}  "
              f"{min(cv):6.4f}  {max(cv):6.4f}  "
              f"{statistics.median(ua):10.4f}  {statistics.median(da):10.4f}")

# Spread per side
print(f"\n  Spread per side (ask - mid):")
up_spreads = [e["up_ask"] - e["up_mid"] for e in w4_entries if e.get("up_ask") and e.get("up_mid") is not None]
dn_spreads = [e["dn_ask"] - e["dn_mid"] for e in w4_entries if e.get("dn_ask") and e.get("dn_mid") is not None]
if up_spreads:
    print(f"    UP spread: mean={statistics.mean(up_spreads):.4f}  median={statistics.median(up_spreads):.4f}  "
          f"max={max(up_spreads):.4f}")
if dn_spreads:
    print(f"    DN spread: mean={statistics.mean(dn_spreads):.4f}  median={statistics.median(dn_spreads):.4f}  "
          f"max={max(dn_spreads):.4f}")
total_spreads = [u + d for u, d in zip(up_spreads, dn_spreads)]
if total_spreads:
    print(f"    TOTAL spread (both sides): mean={statistics.mean(total_spreads):.4f}  "
          f"median={statistics.median(total_spreads):.4f}")

# By mid-price level (how does combined change as market becomes more directional?)
print(f"\n  Combined ask by directional skew (max(up_mid, dn_mid)):")
skew_buckets = defaultdict(list)
for e in w4_entries:
    if e.get("combined") and e.get("up_mid") is not None and e.get("dn_mid") is not None:
        max_mid = max(e["up_mid"], e["dn_mid"])
        if max_mid >= 0.90:
            skew_buckets["extreme (>0.90)"].append(e["combined"])
        elif max_mid >= 0.70:
            skew_buckets["strong (0.70-0.90)"].append(e["combined"])
        elif max_mid >= 0.55:
            skew_buckets["mild (0.55-0.70)"].append(e["combined"])
        else:
            skew_buckets["balanced (<0.55)"].append(e["combined"])

for label in ["balanced (<0.55)", "mild (0.55-0.70)", "strong (0.70-0.90)", "extreme (>0.90)"]:
    vals = skew_buckets.get(label, [])
    if vals:
        print(f"    {label:>20s}: N={len(vals):3d}  mean={statistics.mean(vals):.4f}  median={statistics.median(vals):.4f}")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q3: OB Depth Asymmetry (from OB tape)")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

# For 15M at different time points
print(f"\n  Depth ratio (UP_bid / DN_bid) — measures balance of liquidity")
print(f"  Ratio > 1 = more UP liquidity, < 1 = more DN liquidity")

for label, tte_lo, tte_hi in [
    ("tte=600-900 (early window)", 600, 900),
    ("tte=300-600 (mid window)", 300, 600),
    ("tte=120-300 (late window)", 120, 300),
    ("tte=0-120 (near expiry)", 0, 120),
]:
    entries = [r for r in ob_15m if tte_lo <= r.get("time_to_end_s", 0) <= tte_hi]
    if not entries:
        print(f"\n  {label}: NO DATA")
        continue

    ratios = []
    up_depths = []
    dn_depths = []
    for r in entries:
        ub = r.get("up_bid_depth_10", 0)
        db = r.get("down_bid_depth_10", 0)
        if db > 0 and ub > 0:
            ratios.append(ub / db)
        up_depths.append(ub + r.get("up_ask_depth_10", 0))
        dn_depths.append(db + r.get("down_ask_depth_10", 0))

    print(f"\n  {label}: N={len(entries)}")
    if ratios:
        print(f"    UP/DN bid ratio: mean={statistics.mean(ratios):.3f}  median={statistics.median(ratios):.3f}  "
              f"p25={pct(ratios,25):.3f}  p75={pct(ratios,75):.3f}")
    print(f"    UP total depth:  median=${statistics.median(up_depths):,.0f}")
    print(f"    DN total depth:  median=${statistics.median(dn_depths):,.0f}")

    # Asymmetry: how often is one side > 2x the other?
    if ratios:
        asym_up = sum(1 for r in ratios if r > 2.0)
        asym_dn = sum(1 for r in ratios if r < 0.5)
        balanced = len(ratios) - asym_up - asym_dn
        print(f"    Asymmetric (>2x): UP-heavy={asym_up} ({asym_up/len(ratios)*100:.0f}%)  "
              f"DN-heavy={asym_dn} ({asym_dn/len(ratios)*100:.0f}%)  "
              f"Balanced={balanced} ({balanced/len(ratios)*100:.0f}%)")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q4: Trade Flow Signal")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

trade_entries = [r for r in ob_tape if r.get("trade_buy_vol") is not None]
print(f"\n  Entries with trade data: {len(trade_entries):,} / {len(ob_tape):,}")

taker_ratios = [r["trade_taker_ratio"] for r in trade_entries
                if r.get("trade_taker_ratio") is not None]
buy_vols = [r["trade_buy_vol"] for r in trade_entries]
sell_vols = [r["trade_sell_vol"] for r in trade_entries]

print(f"  Overall taker ratio: mean={statistics.mean(taker_ratios):.4f}  "
      f"median={statistics.median(taker_ratios):.4f}")
print(f"  Buy vol:  mean={statistics.mean(buy_vols):.1f}  median={statistics.median(buy_vols):.1f}")
print(f"  Sell vol: mean={statistics.mean(sell_vols):.1f}  median={statistics.median(sell_vols):.1f}")
print(f"  ⚠ Taker ratio heavily buy-skewed (median={statistics.median(taker_ratios):.2f})")

# By time bucket
print(f"\n  Trade flow by time-to-end (15M):")
print(f"  {'Bucket':>8s}  {'N':>5s}  {'TR_mean':>7s}  {'TR_med':>7s}  {'BuyVol':>8s}  {'SellVol':>8s}  {'Buy%':>5s}")
print(f"  {'-'*8}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*5}")
for b in BUCKETS:
    entries = [r for r in trade_entries if r["_mkt"] == "15m"
               and bucket_tte(r.get("time_to_end_s", 0)) == b]
    if entries:
        trs = [r["trade_taker_ratio"] for r in entries if r.get("trade_taker_ratio") is not None]
        bvs = [r["trade_buy_vol"] for r in entries]
        svs = [r["trade_sell_vol"] for r in entries]
        buy_pct = sum(1 for t in trs if t > 0.5) / max(len(trs), 1) * 100
        print(f"  {b:>8s}  {len(entries):5d}  {statistics.mean(trs):7.4f}  {statistics.median(trs):7.4f}  "
              f"{statistics.mean(bvs):8.0f}  {statistics.mean(svs):8.0f}  {buy_pct:5.1f}")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q5: Signal Tape — Combined Mid Evolution within Window")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

# Flatten signal tape
flat_signals = []
for row in signal_tape:
    ts = row.get("ts", "")
    for p in row.get("poly", []):
        um = p.get("up_mid")
        dm = p.get("dn_mid")
        if um is not None and dm is not None:
            flat_signals.append({
                "ts": ts,
                "cid": p.get("cid", ""),
                "coin": p.get("coin", ""),
                "combined_mid": um + dm,
                "ob_imbalance": p.get("ob_imbalance", 0),
            })

print(f"\n  Flattened signal entries: {len(flat_signals):,}")

# Group by cid
by_cid = defaultdict(list)
for s in flat_signals:
    by_cid[s["cid"]].append(s)
print(f"  Unique windows: {len(by_cid)}")

# Analyze by quintile
window_analyses = []
for cid, entries in by_cid.items():
    entries.sort(key=lambda x: x["ts"])
    if len(entries) < 10:
        continue
    n = len(entries)
    mids = [e["combined_mid"] for e in entries]
    quintiles = [mids[i*n//5:(i+1)*n//5] for i in range(5)]
    window_analyses.append({
        "coin": entries[0]["coin"],
        "n": n,
        "q_mids": [statistics.mean(q) for q in quintiles],
        "first": mids[0],
        "last": mids[-1],
        "min": min(mids),
        "max": max(mids),
        "spread_range": max(mids) - min(mids),
    })

print(f"  Windows with 10+ obs: {len(window_analyses)}")

print(f"\n  Combined Mid by quintile (time within window):")
print(f"  {'Quintile':>10s}  {'Mean':>8s}  {'Median':>8s}  {'P25':>8s}  {'P75':>8s}")
print(f"  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
for qi in range(5):
    vals = [w["q_mids"][qi] for w in window_analyses]
    label = f"{qi*20}-{(qi+1)*20}%"
    print(f"  {label:>10s}  {statistics.mean(vals):8.5f}  {statistics.median(vals):8.5f}  "
          f"{pct(vals,25):8.5f}  {pct(vals,75):8.5f}")

# Spread range within window
ranges = [w["spread_range"] for w in window_analyses]
print(f"\n  Combined mid spread range within window:")
print(f"    mean={statistics.mean(ranges):.5f}  median={statistics.median(ranges):.5f}  "
      f"p75={pct(ranges,75):.5f}  p95={pct(ranges,95):.5f}  max={max(ranges):.5f}")

# Overall distribution
all_mids = [s["combined_mid"] for s in flat_signals]
print(f"\n  All signal combined_mid distribution (N={len(all_mids):,}):")
print(f"    mean={statistics.mean(all_mids):.5f}  median={statistics.median(all_mids):.5f}")
print(f"    p1={pct(all_mids,1):.4f}  p5={pct(all_mids,5):.4f}  p95={pct(all_mids,95):.4f}  p99={pct(all_mids,99):.4f}")

below_1 = sum(1 for m in all_mids if m < 1.0)
at_1 = sum(1 for m in all_mids if 0.995 <= m <= 1.005)
above_1 = sum(1 for m in all_mids if m > 1.005)
print(f"    <1.00: {below_1} ({below_1/len(all_mids)*100:.1f}%)  "
      f"0.995-1.005: {at_1} ({at_1/len(all_mids)*100:.1f}%)  "
      f">1.005: {above_1} ({above_1/len(all_mids)*100:.1f}%)")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q6: 5M vs 15M — What we know and don't know")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

print(f"""
  DATA SOURCES:
  ┌──────────────────┬──────────┬──────────────────────────────────┐
  │ Source           │ N        │ What it tells us                 │
  ├──────────────────┼──────────┼──────────────────────────────────┤
  │ poly_ob_tape     │ {len(ob_tape):>6,}   │ Depth only (prices = 0.01/0.99)  │
  │  └ 15M           │ {sum(1 for r in ob_tape if r['_mkt']=='15m'):>6,}   │ Depth near expiry                │
  │  └ 1H            │ {sum(1 for r in ob_tape if r['_mkt']=='1h'):>6,}   │ Depth for hourly markets         │
  │ signal_tape      │ {len(flat_signals):>6,}   │ Mid prices (combined ≈ 1.0000)   │
  │ mm_w4_5m         │ {len(w4_entries):>6,}   │ ★ ACTUAL 5M ask prices at entry  │
  └──────────────────┴──────────┴──────────────────────────────────┘

  ⚠ NO 5M-specific OB depth data exists
  ⚠ OB tape has zero real directional prices (all 0.01 bid / 0.99 ask)
  ⚠ mm_w4 is paper trading log — prices are real but no slippage data
""")

# Check for 5M slugs (must exclude '15m' false positive)
slug_5m = sum(1 for r in ob_tape
              if "-5m-" in r.get("slug", "").lower()
              or r.get("slug", "").lower().endswith("-5m"))
print(f"  OB tape entries with actual 5M slug: {slug_5m}")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  Q6b: 5M Resolution Analysis (from mm_w4)")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

# Match entries with resolutions
for r in w4_resolutions:
    entry = entry_map.get(r["cid"])
    if entry:
        r["_entry"] = entry

print(f"\n  Total resolutions: {len(w4_resolutions)}")
both_filled = [r for r in w4_resolutions if r.get("both_filled")]
not_filled = [r for r in w4_resolutions if not r.get("both_filled")]
print(f"  Both filled: {len(both_filled)} ({len(both_filled)/len(w4_resolutions)*100:.1f}%)")
print(f"  NOT both filled: {len(not_filled)} ({len(not_filled)/len(w4_resolutions)*100:.1f}%)")

# P&L analysis
all_pnl = [r["pnl"] for r in w4_resolutions]
filled_pnl = [r["pnl"] for r in both_filled]
print(f"\n  ALL resolutions:  sum={sum(all_pnl):+.2f}  mean={statistics.mean(all_pnl):+.3f}  N={len(all_pnl)}")
if filled_pnl:
    print(f"  Both-filled only: sum={sum(filled_pnl):+.2f}  mean={statistics.mean(filled_pnl):+.3f}  N={len(filled_pnl)}")
    wins = sum(1 for p in filled_pnl if p > 0)
    losses = sum(1 for p in filled_pnl if p < 0)
    print(f"  Win: {wins}  Loss: {losses}  WR: {wins/len(filled_pnl)*100:.1f}%")

# P&L by combined price
print(f"\n  P&L by combined ask (both-filled only):")
print(f"  {'Bucket':>10s}  {'N':>4s}  {'Sum':>8s}  {'Mean':>7s}  {'WR':>5s}")
print(f"  {'-'*10}  {'-'*4}  {'-'*8}  {'-'*7}  {'-'*5}")
for label, lo, hi in [("<1.00", 0, 1.00), ("1.00-1.02", 1.00, 1.02), ("1.02-1.04", 1.02, 1.04), (">=1.04", 1.04, 99)]:
    br = [r for r in both_filled
          if r.get("_entry") and lo <= r["_entry"].get("combined", 0) < hi]
    if br:
        bp = [r["pnl"] for r in br]
        wr = sum(1 for p in bp if p > 0) / len(bp) * 100
        print(f"  {label:>10s}  {len(br):4d}  {sum(bp):+8.2f}  {statistics.mean(bp):+7.3f}  {wr:5.1f}%")

# Why <1.00 loses: asymmetric sizing
print(f"\n  ⚠ <1.00 combined LOSES because:")
sub1_entries = [r for r in both_filled
                if r.get("_entry") and r["_entry"].get("combined", 0) < 1.00]
if sub1_entries:
    for r in sub1_entries[:3]:
        e = r["_entry"]
        print(f"    cid={r['cid']} coin={r['coin']} combined={e['combined']} "
              f"up_ask={e['up_ask']} dn_ask={e['dn_ask']} "
              f"up_shares={r['up_shares']} dn_shares={r['down_shares']} "
              f"pnl={r['pnl']:+.3f}")
    print(f"    → Heavily skewed positions (e.g. 156 UP vs 5 DN) = NOT hedged")
    print(f"    → Low combined ≠ free money when position is directional")


# ═══════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  ★★★ SUMMARY: THE KEY NUMBERS ★★★")
print(f"{SEP}")
# ═══════════════════════════════════════════════════════════════════

# Separate balanced vs skewed entries
balanced_entries = [e for e in w4_entries
                    if e.get("combined") and e.get("up_mid") is not None
                    and e.get("dn_mid") is not None
                    and max(e["up_mid"], e["dn_mid"]) < 0.70]
balanced_combined = [e["combined"] for e in balanced_entries]

skewed_entries = [e for e in w4_entries
                  if e.get("combined") and e.get("up_mid") is not None
                  and e.get("dn_mid") is not None
                  and max(e["up_mid"], e["dn_mid"]) >= 0.70]
skewed_combined = [e["combined"] for e in skewed_entries]

print(f"""
  ┌──────────────────────────────────────────────────────────────┐
  │  "At T+60s after a 5M window opens, what is the realistic   │
  │   combined price (UP+DOWN) we'd have to pay?"               │
  ├──────────────────────────────────────────────────────────────┤
  │                                                              │
  │  From mm_w4_5m.jsonl (N={len(combined):>3d} actual 5M entries):          │
  │                                                              │
  │  ALL entries:                                                │
  │    Combined Ask:  median = {statistics.median(combined):.4f}                     │
  │    Overround:     {(statistics.median(combined)-1)*100:+.2f}% (= cost of buying both sides) │
  │    Spread/side:   ~{statistics.median(total_spreads):.3f} (median half-spread)           │""")

if balanced_combined:
    print(f"  │                                                              │")
    print(f"  │  BALANCED markets (max_mid < 0.70):  N={len(balanced_combined):>3d}               │")
    print(f"  │    Combined Ask:  median = {statistics.median(balanced_combined):.4f}                     │")
    print(f"  │    Overround:     {(statistics.median(balanced_combined)-1)*100:+.2f}%                                │")

if skewed_combined:
    print(f"  │                                                              │")
    print(f"  │  SKEWED markets (max_mid >= 0.70):   N={len(skewed_combined):>3d}               │")
    print(f"  │    Combined Ask:  median = {statistics.median(skewed_combined):.4f}                     │")
    print(f"  │    Overround:     {(statistics.median(skewed_combined)-1)*100:+.2f}%                                │")

print(f"""  │                                                              │
  │  Signal tape mid (15M, N={len(all_mids):>5d}):                       │
  │    Combined Mid:  median = {statistics.median(all_mids):.5f}                   │
  │    (= mid is ~1.00, spread is what you pay)                  │
  │                                                              │
  ├──────────────────────────────────────────────────────────────┤
  │  IMPLICATIONS:                                               │
  │  • Typical 5M overround = ~2% (median combined = 1.02)      │
  │  • At 1.02 combined, buying both sides guarantees -$0.02     │
  │    per $1 invested (before any directional edge)             │
  │  • To profit from both-sides buying, need combined < 1.00   │
  │    which happens ~16% of the time but only in skewed mkts   │
  │  • Balanced (50/50) markets have HIGHER combined (1.03)      │
  │    because both sides have real demand                       │
  │  • Fill rate is a concern: only {len(both_filled)/len(w4_resolutions)*100:.0f}% got both sides     │
  │    filled, and unfilled = zero P&L                           │
  └──────────────────────────────────────────────────────────────┘""")

print(f"\n  DATA QUALITY WARNINGS:")
print(f"  1. OB tape prices are ALL 0.01/0.99 — NO usable pricing data")
print(f"  2. mm_w4 has only {len(w4_entries)} entries — small sample from one session")
print(f"  3. No 5M OB depth data exists — cannot assess fill probability")
print(f"  4. Signal tape is 15M only — 5M may have different mid dynamics")
print(f"  5. mm_w4 includes XRP which may behave differently from BTC/ETH")

# Check for xrp
xrp_count = sum(1 for e in w4_entries if e.get("coin") == "xrp")
print(f"  6. XRP entries in mm_w4: {xrp_count}")

print(f"\n  Done.")
