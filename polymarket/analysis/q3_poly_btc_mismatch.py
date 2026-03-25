"""
Q3: BTC → Polymarket Resolution Mismatch Analysis
===================================================
KEY QUESTION: How often does BTC go one direction at entry but Polymarket
resolves the OPPOSITE? Is this the source of the WR gap?

Note on lean_dir semantics:
- w4_ret < 0 (BTC down at entry) → lean_dir = "DOWN" (we buy more DOWN shares)
- w4_ret > 0 (BTC up at entry)   → lean_dir = "UP"   (we buy more UP shares)
- A few contrarian entries exist (lean flipped vs signal)
- "BTC direction" = sign of w4_ret at entry time
- "Poly result" = actual Chainlink oracle resolution
- MATCH   = BTC direction agrees with Poly result
- MISMATCH = BTC direction disagrees with Poly result (BTC down but Poly resolves UP, or vice versa)
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

# ── Paths ────────────────────────────────────────────────────────────────────
LOGS_DIR = Path("/Users/wai/projects/axc-trading/polymarket/logs")
W4_LOG   = LOGS_DIR / "mm_w4_5m.jsonl"
TR_LOG   = LOGS_DIR / "mm_trades_5m.jsonl"

# ── Load w4 entries (event == "w4_entry") ────────────────────────────────────
w4_map: dict[str, dict] = {}   # short_cid → record
with open(W4_LOG) as fh:
    for line in fh:
        rec = json.loads(line)
        if rec.get("event") == "w4_entry":
            w4_map[rec["cid"]] = rec   # short cid e.g. "0x21739f"

print(f"Loaded {len(w4_map)} w4_entry records")

# ── Load trades and join ─────────────────────────────────────────────────────
# Trade cids are full hex; w4 cids are 8-char prefixes.
# All 153 trades matched in pre-check (startswith join).

joined: list[dict] = []
unmatched = 0

with open(TR_LOG) as fh:
    for line in fh:
        t = json.loads(line)
        full_cid = t["cid"]
        match_rec = None
        for short_cid, w4r in w4_map.items():
            if full_cid.startswith(short_cid):
                match_rec = w4r
                break
        if match_rec is None:
            unmatched += 1
            continue

        w4_ret   = match_rec["w4_ret"]
        w4_bps   = abs(w4_ret) * 10_000       # magnitude in bps
        btc_dir  = "UP" if w4_ret > 0 else "DOWN"
        poly_res = t["result"]                 # "UP" or "DOWN"
        mismatch = (btc_dir != poly_res)
        contrarian = match_rec.get("contrarian", False)

        joined.append({
            "cid":        full_cid,
            "coin":       t["coin"],
            "ts":         t["ts"],
            "w4_ret":     w4_ret,
            "w4_bps":     w4_bps,
            "btc_dir":    btc_dir,
            "lean_dir":   match_rec["lean_dir"],
            "poly_res":   poly_res,
            "mismatch":   mismatch,
            "contrarian": contrarian,
            "pnl":        t["pnl"],
            "cost":       t["cost"],
        })

print(f"Joined: {len(joined)}  |  Unmatched: {unmatched}")

# ── Helper ────────────────────────────────────────────────────────────────────
def pct(n, d):
    return f"{n/d*100:.1f}%" if d else "N/A"

# ── Section 1: Overall mismatch stats ────────────────────────────────────────
total     = len(joined)
mismatches = [r for r in joined if r["mismatch"]]
matches    = [r for r in joined if not r["mismatch"]]

print("\n" + "=" * 60)
print("SECTION 1: OVERALL MISMATCH STATS")
print("=" * 60)
print(f"Total trades:      {total}")
print(f"BTC dir == Poly:   {len(matches)}  ({pct(len(matches), total)})")
print(f"BTC dir != Poly:   {len(mismatches)}  ({pct(len(mismatches), total)})")
print()

# WR if we used BTC direction as predictor
wr_btc_as_pred = len(matches) / total if total else 0
print(f"WR using BTC direction as predictor: {wr_btc_as_pred*100:.1f}%")
print(f"(i.e., if we ALWAYS bet the BTC direction, we win {wr_btc_as_pred*100:.1f}% of markets)")

# Breakdown by coin
print("\nBy coin:")
coins = sorted(set(r["coin"] for r in joined))
for coin in coins:
    sub = [r for r in joined if r["coin"] == coin]
    mm  = [r for r in sub if r["mismatch"]]
    print(f"  {coin:4s}: n={len(sub):3d}  mismatch={len(mm):3d} ({pct(len(mm), len(sub))})")

# ── Section 2: Mismatch rate by |w4_ret| magnitude ───────────────────────────
buckets = [
    ("< 5 bps",    0,    5),
    ("5-10 bps",   5,   10),
    ("10-20 bps", 10,   20),
    ("> 20 bps",  20, 9999),
]

print("\n" + "=" * 60)
print("SECTION 2: MISMATCH RATE BY SIGNAL MAGNITUDE")
print("=" * 60)
print(f"{'Bucket':<12}  {'N':>4}  {'Mismatch':>8}  {'Mismatch%':>10}  {'WR(BTC pred)':>13}")
print("-" * 60)

for label, lo, hi in buckets:
    sub = [r for r in joined if lo <= r["w4_bps"] < hi]
    if not sub:
        continue
    mm = [r for r in sub if r["mismatch"]]
    wr = (len(sub) - len(mm)) / len(sub) if sub else 0
    print(f"{label:<12}  {len(sub):>4}  {len(mm):>8}  {pct(len(mm), len(sub)):>10}  {wr*100:>12.1f}%")

# ── Section 3: Contrarian trades ─────────────────────────────────────────────
contra = [r for r in joined if r["contrarian"]]
if contra:
    print("\n" + "=" * 60)
    print("SECTION 3: CONTRARIAN ENTRIES (lean flipped vs BTC signal)")
    print("=" * 60)
    print(f"Contrarian trades: {len(contra)}")
    for r in contra:
        print(f"  w4_ret={r['w4_ret']:+.4f} ({r['w4_bps']:.1f}bps)  btc={r['btc_dir']}  lean={r['lean_dir']}  poly={r['poly_res']}  mismatch={r['mismatch']}")

# ── Section 4: Top 10 mismatches by signal strength ──────────────────────────
print("\n" + "=" * 60)
print("SECTION 4: TOP 10 STRONGEST SIGNALS THAT MISMATCHED POLY RESULT")
print("  (high |w4_ret| = strong signal, but Poly went opposite)")
print("=" * 60)

top_mismatch = sorted(mismatches, key=lambda r: r["w4_bps"], reverse=True)[:10]
if top_mismatch:
    print(f"{'#':>2}  {'coin':4}  {'w4_ret':>8}  {'bps':>7}  {'btc_dir':>7}  {'poly_res':>8}  {'pnl':>7}  {'ts'}")
    print("-" * 80)
    for i, r in enumerate(top_mismatch, 1):
        print(f"{i:>2}  {r['coin']:4}  {r['w4_ret']:+.4f}  {r['w4_bps']:>6.1f}  "
              f"{r['btc_dir']:>7}  {r['poly_res']:>8}  {r['pnl']:>+7.2f}  {r['ts']}")
else:
    print("No mismatches found.")

# ── Section 5: WR breakdown by magnitude using BTC direction ─────────────────
print("\n" + "=" * 60)
print("SECTION 5: WIN RATE BY MAGNITUDE (BTC dir as predictor)")
print("  WIN = BTC direction matched Poly resolution")
print("=" * 60)
print(f"{'Bucket':<12}  {'N':>4}  {'Wins':>5}  {'WR':>8}  {'Avg|bps|':>9}")
print("-" * 55)

for label, lo, hi in buckets:
    sub = [r for r in joined if lo <= r["w4_bps"] < hi]
    if not sub:
        continue
    wins = [r for r in sub if not r["mismatch"]]
    avg_bps = sum(r["w4_bps"] for r in sub) / len(sub)
    print(f"{label:<12}  {len(sub):>4}  {len(wins):>5}  {pct(len(wins), len(sub)):>8}  {avg_bps:>8.1f}")

# ── Section 6: Mismatch vs actual PnL ────────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 6: DOES MISMATCH EXPLAIN THE PNL PATTERN?")
print("=" * 60)

# MM trades both sides — profit when lean side wins
# lean_dir = direction we bet MORE on
# If lean=DOWN and result=DOWN: we profit (lean side won)
# If lean=DOWN and result=UP: we lose (lean side lost)

lean_wins  = [r for r in joined if r["lean_dir"] == r["poly_res"]]
lean_loses = [r for r in joined if r["lean_dir"] != r["poly_res"]]

avg_pnl_win  = sum(r["pnl"] for r in lean_wins)  / len(lean_wins)  if lean_wins  else 0
avg_pnl_lose = sum(r["pnl"] for r in lean_loses) / len(lean_loses) if lean_loses else 0

print(f"Lean side won  (lean_dir == poly_res): n={len(lean_wins):3d}  avg_pnl={avg_pnl_win:+.3f}")
print(f"Lean side lost (lean_dir != poly_res): n={len(lean_loses):3d}  avg_pnl={avg_pnl_lose:+.3f}")
print()

# BTC direction as predictor = lean wins (since lean follows BTC mostly)
# So mismatch rate ≈ lean loss rate
btc_as_pred_wr = len(lean_wins) / total if total else 0
print(f"Lean-side WR (actual, based on lean_dir vs poly_res): {btc_as_pred_wr*100:.1f}%")
print()

# Cross-tab: BTC dir vs lean_dir vs poly result
print("Cross-tab of (btc_dir, lean_dir, poly_res):")
xtab = defaultdict(int)
for r in joined:
    key = (r["btc_dir"], r["lean_dir"], r["poly_res"])
    xtab[key] += 1
for k in sorted(xtab):
    print(f"  btc={k[0]:4s}  lean={k[1]:4s}  poly={k[2]:4s}  n={xtab[k]}")

# ── Section 7: Distribution of w4_bps ────────────────────────────────────────
print("\n" + "=" * 60)
print("SECTION 7: SIGNAL MAGNITUDE DISTRIBUTION")
print("=" * 60)
bps_vals = sorted(r["w4_bps"] for r in joined)
print(f"Min: {bps_vals[0]:.1f} bps")
print(f"Max: {bps_vals[-1]:.1f} bps")
print(f"Median: {bps_vals[len(bps_vals)//2]:.1f} bps")
print(f"Mean: {sum(bps_vals)/len(bps_vals):.1f} bps")
print()
thresholds = [5, 10, 15, 20, 30, 50]
for t in thresholds:
    n = sum(1 for v in bps_vals if v >= t)
    print(f"  |w4_ret| >= {t:3d}bps: {n:3d}/{total} ({pct(n, total)})")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
overall_mm = len(mismatches) / total * 100 if total else 0
print(f"Overall BTC↔Poly mismatch rate: {overall_mm:.1f}%")
print(f"  → Meaning: BTC moved one way at entry but Poly resolved opposite {overall_mm:.1f}% of the time")
print(f"  → If mismatch rate >> 50%: BTC is ANTI-predictive (contrarian would win)")
print(f"  → If mismatch rate ~50%: BTC signal has no edge on Poly resolution")
print(f"  → If mismatch rate << 50%: BTC signal IS predictive of Poly")
print()
print(f"Lean-side WR: {btc_as_pred_wr*100:.1f}%  (how often our lean direction matched Poly)")
print(f"Backtest assumed ~60%+ WR. Actual lean WR explains actual PnL performance.")
