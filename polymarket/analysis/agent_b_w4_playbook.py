#!/usr/bin/env python3
"""
Agent B: W4 Execution Pattern Reconstruction from 14-minute snapshot data.
Every trade, every price, every timing detail.

Data sources:
  1. wallet4_all_trades.json  (2,516 records)
  2. btc_1m_7days.json        (BTC 1-min candles)
  3. poly_ob_tape.jsonl       (Poly OB - reference only, sparse for W4 windows)
"""

import json
import math
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = Path("/Users/wai/projects/axc-trading")
TRADES_FILE  = BASE / "data/wallet4_raw/wallet4_all_trades.json"
BTC_FILE     = BASE / "polymarket/analysis/btc_1m_7days.json"
OB_FILE      = BASE / "polymarket/logs/poly_ob_tape.jsonl"
OUT_DIR      = BASE / "polymarket/analysis"
REPORT_FILE  = OUT_DIR / "agent_b_report.md"
PLOT_DIR     = OUT_DIR / "agent_b_plots"
PLOT_DIR.mkdir(exist_ok=True)

ET = timezone(timedelta(hours=-4))

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading data...")

with open(TRADES_FILE) as f:
    raw_trades = json.load(f)

with open(BTC_FILE) as f:
    btc_candles = json.load(f)

# BTC price index: ts_seconds -> candle
btc_by_ts = {}
for c in btc_candles:
    btc_by_ts[int(c["ts"] / 1000)] = c

def get_btc_price(ts_sec: int) -> float | None:
    """Get BTC close price at the 1-min candle containing ts_sec."""
    candle_ts = ts_sec - (ts_sec % 60)
    if candle_ts in btc_by_ts:
        return btc_by_ts[candle_ts]["close"]
    # Try nearby
    for offset in [-60, 60, -120, 120]:
        if (candle_ts + offset) in btc_by_ts:
            return btc_by_ts[candle_ts + offset]["close"]
    return None

def get_btc_ohlc(ts_sec: int):
    """Get full OHLC candle at ts_sec."""
    candle_ts = ts_sec - (ts_sec % 60)
    if candle_ts in btc_by_ts:
        return btc_by_ts[candle_ts]
    for offset in [-60, 60]:
        if (candle_ts + offset) in btc_by_ts:
            return btc_by_ts[candle_ts + offset]
    return None

# Load OB tape (index by slug+ts for lookup)
ob_tape = defaultdict(list)
with open(OB_FILE) as f:
    for line in f:
        rec = json.loads(line)
        ob_tape[rec["slug"]].append(rec)

# Filter to TRADE only, enrich
all_trades = [t for t in raw_trades if t["type"] == "TRADE"]
print(f"Total TRADE records: {len(all_trades)}")

# ── Classify each trade ─────────────────────────────────────────────────────
def parse_slug(slug: str):
    """
    Extract coin, timeframe, window_open from slug.
    Returns (coin, timeframe_str, window_open_ts) or None.
    """
    s = slug.lower()
    # Detect coin
    if "btc" in s or "bitcoin" in s:
        coin = "BTC"
    elif "eth" in s or "ethereum" in s:
        coin = "ETH"
    elif "xrp" in s:
        coin = "XRP"
    elif "sol" in s or "solana" in s:
        coin = "SOL"
    else:
        return None

    parts = slug.split("-")
    # Standard: btc-updown-15m-1774276200
    try:
        window_ts = int(parts[-1])
        if "15m" in slug:
            return coin, "15M", window_ts
        elif "5m" in slug:
            return coin, "5M", window_ts
        elif "1h" in slug:
            return coin, "1H", window_ts
    except ValueError:
        pass

    # Non-standard: bitcoin-up-or-down-march-23-2026-10am-et
    # 10AM ET March 23, 2026 = 1774274400 (hourly)
    if "10am" in s and "march-23" in s:
        return coin, "1H", 1774274400
    return None


# ── Group trades by market (slug = unique market) ───────────────────────────
market_trades = defaultdict(list)
for t in all_trades:
    market_trades[t["slug"]].append(t)

# Build enriched market records
markets = {}
for slug, tlist in market_trades.items():
    parsed = parse_slug(slug)
    if not parsed:
        continue
    coin, tf, window_open = parsed

    if tf == "15M":
        window_end = window_open + 900
    elif tf == "5M":
        window_end = window_open + 300
    elif tf == "1H":
        window_end = window_open + 3600
    else:
        continue

    up_trades = [t for t in tlist if t["outcome"] == "Up"]
    down_trades = [t for t in tlist if t["outcome"] == "Down"]

    up_usdc = sum(t["usdcSize"] for t in up_trades)
    down_usdc = sum(t["usdcSize"] for t in down_trades)
    up_shares = sum(t["size"] for t in up_trades)
    down_shares = sum(t["size"] for t in down_trades)

    ts_min = min(t["timestamp"] for t in tlist)
    ts_max = max(t["timestamp"] for t in tlist)

    markets[slug] = {
        "slug": slug,
        "coin": coin,
        "tf": tf,
        "window_open": window_open,
        "window_end": window_end,
        "up_usdc": up_usdc,
        "down_usdc": down_usdc,
        "up_shares": up_shares,
        "down_shares": down_shares,
        "total_usdc": up_usdc + down_usdc,
        "trades": tlist,
        "up_trades": up_trades,
        "down_trades": down_trades,
        "n_trades": len(tlist),
        "first_ts": ts_min,
        "last_ts": ts_max,
        "entry_delay_s": ts_min - window_open,
        "trading_duration_s": ts_max - ts_min,
    }

print(f"Parsed {len(markets)} markets")

# ── BTC return for each window ───────────────────────────────────────────────
for slug, m in markets.items():
    btc_open = get_btc_price(m["window_open"])
    btc_close = get_btc_price(m["window_end"])
    btc_at_entry = get_btc_price(m["first_ts"])

    if btc_open and btc_close:
        m["btc_return_bps"] = (btc_close - btc_open) / btc_open * 10000
    else:
        m["btc_return_bps"] = None

    if btc_open and btc_at_entry:
        m["btc_return_at_entry_bps"] = (btc_at_entry - btc_open) / btc_open * 10000
    else:
        m["btc_return_at_entry_bps"] = None

    m["btc_open"] = btc_open
    m["btc_close"] = btc_close


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 1: W4's bid price vs market mid
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 1: W4's bid price vs market mid")
print("="*70)

analysis1_lines = []
analysis1_lines.append("# ANALYSIS 1: W4's Bid Price vs Market Mid\n")
analysis1_lines.append("Since OB tape shows 0.01/0.99 (empty markets) for W4's windows,")
analysis1_lines.append("we infer the market mid from W4's own trades within each slug.\n")

# For each market, estimate mid from W4's own up/down prices
# In a binary market: up_price + down_price ≈ 1.00 (minus spread)
# If W4 buys Up at 0.60 and Down at 0.40, mid_up ≈ 0.60, mid_down ≈ 0.40
# Maker = price below the concurrent ask; Taker = hits the ask

maker_taker_stats = {"maker": 0, "taker": 0, "unknown": 0}
price_analysis = []

for slug, m in sorted(markets.items(), key=lambda x: x[1]["window_open"]):
    if m["n_trades"] < 5:
        continue

    # Compute average price for up and down
    if m["up_trades"]:
        avg_up_price = sum(t["price"] * t["usdcSize"] for t in m["up_trades"]) / m["up_usdc"] if m["up_usdc"] > 0 else 0
    else:
        avg_up_price = 0

    if m["down_trades"]:
        avg_down_price = sum(t["price"] * t["usdcSize"] for t in m["down_trades"]) / m["down_usdc"] if m["down_usdc"] > 0 else 0
    else:
        avg_down_price = 0

    # Check if prices are consistent (up + down ≈ 1.0)
    price_sum = avg_up_price + avg_down_price if avg_up_price > 0 and avg_down_price > 0 else None

    # Determine maker/taker from fill patterns:
    # Multiple fills at same price in rapid succession = likely maker (resting order getting hit)
    # Single large fill or fills at varying prices = likely taker (sweeping the book)

    # Price clustering analysis
    up_prices = [round(t["price"], 4) for t in m["up_trades"]]
    down_prices = [round(t["price"], 4) for t in m["down_trades"]]

    up_price_set = set(up_prices) if up_prices else set()
    down_price_set = set(down_prices) if down_prices else set()

    # Many fills at exact same price = MAKER (resting limit order filled in fragments)
    # Fills at different prices = TAKER (walking the book)
    up_unique_ratio = len(up_price_set) / len(up_prices) if up_prices else 1.0
    down_unique_ratio = len(down_price_set) / len(down_prices) if down_prices else 1.0

    # If most fills are at same price, it's maker behavior
    up_role = "MAKER" if up_unique_ratio < 0.3 else ("TAKER" if up_unique_ratio > 0.7 else "MIXED")
    down_role = "MAKER" if down_unique_ratio < 0.3 else ("TAKER" if down_unique_ratio > 0.7 else "MIXED")

    price_analysis.append({
        "slug": slug,
        "coin": m["coin"],
        "tf": m["tf"],
        "avg_up_price": avg_up_price,
        "avg_down_price": avg_down_price,
        "price_sum": price_sum,
        "up_unique_prices": len(up_price_set),
        "up_total_fills": len(up_prices),
        "down_unique_prices": len(down_price_set),
        "down_total_fills": len(down_prices),
        "up_role": up_role,
        "down_role": down_role,
    })

    dt_open = datetime.fromtimestamp(m["window_open"], tz=ET)
    sum_str = f"{price_sum:.4f}" if price_sum else "N/A"
    analysis1_lines.append(
        f"**{m['coin']} {m['tf']} {dt_open.strftime('%H:%M')}ET** | "
        f"Avg Up={avg_up_price:.4f} Down={avg_down_price:.4f} Sum={sum_str:>6} | "
        f"Up: {up_role}({len(up_price_set)}/{len(up_prices)} unique) "
        f"Down: {down_role}({len(down_price_set)}/{len(down_prices)} unique)"
    )

# Overall maker/taker summary
all_up_roles = [p["up_role"] for p in price_analysis]
all_down_roles = [p["down_role"] for p in price_analysis]
from collections import Counter
analysis1_lines.append(f"\n**Up side roles**: {Counter(all_up_roles)}")
analysis1_lines.append(f"**Down side roles**: {Counter(all_down_roles)}")

# Price sum analysis
valid_sums = [p["price_sum"] for p in price_analysis if p["price_sum"]]
if valid_sums:
    analysis1_lines.append(f"\n**Price sum (up+down)**: mean={statistics.mean(valid_sums):.4f}, "
                          f"min={min(valid_sums):.4f}, max={max(valid_sums):.4f}")
    analysis1_lines.append("(Sum < 1.0 means W4 buys below fair value on both sides = maker edge)")

print("\n".join(analysis1_lines[-5:]))


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 2: Lean ratio distribution
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 2: Lean ratio distribution")
print("="*70)

analysis2_lines = []
analysis2_lines.append("# ANALYSIS 2: Lean Ratio Distribution\n")
analysis2_lines.append("Lean ratio = max(UP$, DOWN$) / min(UP$, DOWN$) for each window.\n")

lean_data = []  # (btc_move_bps, lean_ratio, lean_direction, coin, tf)
lean_table = []

for slug, m in sorted(markets.items(), key=lambda x: x[1]["window_open"]):
    if m["n_trades"] < 5 or m["up_usdc"] == 0 or m["down_usdc"] == 0:
        continue

    if m["up_usdc"] >= m["down_usdc"]:
        lean_ratio = m["up_usdc"] / m["down_usdc"]
        lean_dir = "UP"
    else:
        lean_ratio = m["down_usdc"] / m["up_usdc"]
        lean_dir = "DOWN"

    lean_pct = m["up_usdc"] / (m["up_usdc"] + m["down_usdc"])  # UP fraction

    btc_ret = m["btc_return_bps"]
    btc_at_entry = m["btc_return_at_entry_bps"]

    lean_data.append({
        "slug": slug,
        "coin": m["coin"],
        "tf": m["tf"],
        "lean_ratio": lean_ratio,
        "lean_dir": lean_dir,
        "lean_pct": lean_pct,
        "up_usdc": m["up_usdc"],
        "down_usdc": m["down_usdc"],
        "btc_return_bps": btc_ret,
        "btc_at_entry_bps": btc_at_entry,
    })

    dt_open = datetime.fromtimestamp(m["window_open"], tz=ET)
    lean_table.append(
        f"{m['coin']:3s} {m['tf']:3s} {dt_open.strftime('%H:%M')}ET | "
        f"UP=${m['up_usdc']:8.2f} DOWN=${m['down_usdc']:8.2f} | "
        f"Lean={lean_dir:4s} {lean_ratio:5.2f}x | "
        f"BTC@open→close={btc_ret:+.1f}bps" if btc_ret else
        f"{m['coin']:3s} {m['tf']:3s} {dt_open.strftime('%H:%M')}ET | "
        f"UP=${m['up_usdc']:8.2f} DOWN=${m['down_usdc']:8.2f} | "
        f"Lean={lean_dir:4s} {lean_ratio:5.2f}x | BTC=N/A"
    )

for line in lean_table:
    analysis2_lines.append(line)
    print(line)

# Statistics
ratios = [d["lean_ratio"] for d in lean_data]
analysis2_lines.append(f"\n**Lean ratio stats**: mean={statistics.mean(ratios):.2f}x, "
                      f"median={statistics.median(ratios):.2f}x, "
                      f"min={min(ratios):.2f}x, max={max(ratios):.2f}x, "
                      f"stdev={statistics.stdev(ratios):.2f}")

# Check if lean direction matches BTC move direction
matches = 0
total = 0
for d in lean_data:
    if d["btc_return_bps"] is None:
        continue
    total += 1
    btc_up = d["btc_return_bps"] > 0
    lean_up = d["lean_dir"] == "UP"
    if btc_up == lean_up:
        matches += 1

if total > 0:
    analysis2_lines.append(f"\n**Lean direction matches BTC outcome**: {matches}/{total} = {matches/total*100:.1f}%")

# Correlation: BTC move size vs lean ratio
btc_moves = []
lean_ratios = []
for d in lean_data:
    if d["btc_at_entry_bps"] is not None:
        btc_moves.append(abs(d["btc_at_entry_bps"]))
        lean_ratios.append(d["lean_ratio"])

if len(btc_moves) > 3:
    corr = np.corrcoef(btc_moves, lean_ratios)[0, 1]
    analysis2_lines.append(f"**Correlation (|BTC move at entry| vs lean ratio)**: r = {corr:.4f}")
    print(f"\nCorrelation (|BTC move| vs lean ratio): r = {corr:.4f}")

# Plot: BTC move size vs lean ratio
fig, ax = plt.subplots(figsize=(10, 6))
colors = {"BTC": "#F7931A", "ETH": "#627EEA", "SOL": "#9945FF", "XRP": "#23292F"}
for d in lean_data:
    if d["btc_at_entry_bps"] is None:
        continue
    c = colors.get(d["coin"], "gray")
    # Signed lean: positive if lean UP, negative if lean DOWN
    signed_lean = d["lean_ratio"] if d["lean_dir"] == "UP" else -d["lean_ratio"]
    ax.scatter(d["btc_at_entry_bps"], signed_lean, c=c, s=d["up_usdc"]+d["down_usdc"],
              alpha=0.6, edgecolors="black", linewidth=0.5, label=d["coin"])

# Remove duplicate legends
handles, labels = ax.get_legend_handles_labels()
by_label = dict(zip(labels, handles))
ax.legend(by_label.values(), by_label.keys())
ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
ax.axvline(0, color="gray", linestyle="--", alpha=0.5)
ax.set_xlabel("BTC Return at Entry (bps)")
ax.set_ylabel("Signed Lean Ratio (+ = UP lean, - = DOWN lean)")
ax.set_title("W4: BTC Move at Entry vs Lean Direction & Magnitude")
fig.tight_layout()
fig.savefig(PLOT_DIR / "a2_btc_move_vs_lean.png", dpi=150)
plt.close(fig)
print(f"Saved: {PLOT_DIR / 'a2_btc_move_vs_lean.png'}")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 3: Entry sequence
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 3: Entry sequence")
print("="*70)

analysis3_lines = []
analysis3_lines.append("# ANALYSIS 3: Entry Sequence\n")

entry_seq_data = []
for slug, m in sorted(markets.items(), key=lambda x: x[1]["window_open"]):
    if m["n_trades"] < 5:
        continue

    up_first_ts = min(t["timestamp"] for t in m["up_trades"]) if m["up_trades"] else None
    down_first_ts = min(t["timestamp"] for t in m["down_trades"]) if m["down_trades"] else None
    up_last_ts = max(t["timestamp"] for t in m["up_trades"]) if m["up_trades"] else None
    down_last_ts = max(t["timestamp"] for t in m["down_trades"]) if m["down_trades"] else None

    if up_first_ts and down_first_ts:
        first_side = "UP" if up_first_ts <= down_first_ts else "DOWN"
        gap_s = abs(up_first_ts - down_first_ts)
    elif up_first_ts:
        first_side = "UP"
        gap_s = None
    elif down_first_ts:
        first_side = "DOWN"
        gap_s = None
    else:
        continue

    # Check if fills are clustered or spread out
    all_ts = sorted(t["timestamp"] for t in m["trades"])
    if len(all_ts) >= 2:
        ts_diffs = [all_ts[i+1] - all_ts[i] for i in range(len(all_ts)-1)]
        burst_count = sum(1 for d in ts_diffs if d == 0)  # same-second fills
        slow_count = sum(1 for d in ts_diffs if d > 30)   # >30s gap
    else:
        ts_diffs = []
        burst_count = 0
        slow_count = 0

    entry_seq_data.append({
        "slug": slug,
        "coin": m["coin"],
        "tf": m["tf"],
        "first_side": first_side,
        "gap_s": gap_s,
        "entry_delay_s": m["entry_delay_s"],
        "trading_duration_s": m["trading_duration_s"],
        "burst_pct": burst_count / max(len(ts_diffs), 1) * 100,
        "n_trades": m["n_trades"],
    })

    dt_open = datetime.fromtimestamp(m["window_open"], tz=ET)
    line = (
        f"{m['coin']:3s} {m['tf']:3s} {dt_open.strftime('%H:%M')}ET | "
        f"Entry delay: {m['entry_delay_s']:4d}s | Duration: {m['trading_duration_s']:4d}s | "
        f"First side: {first_side:4s} | Gap: {gap_s}s | "
        f"Same-sec fills: {burst_count}/{len(ts_diffs)} ({burst_count/max(len(ts_diffs),1)*100:.0f}%)"
    )
    analysis3_lines.append(line)
    print(line)

# Summary
first_sides = [d["first_side"] for d in entry_seq_data]
analysis3_lines.append(f"\n**First side placed**: {Counter(first_sides)}")

delays = [d["entry_delay_s"] for d in entry_seq_data]
durations = [d["trading_duration_s"] for d in entry_seq_data]
gaps = [d["gap_s"] for d in entry_seq_data if d["gap_s"] is not None]

analysis3_lines.append(f"**Entry delay**: mean={statistics.mean(delays):.0f}s, median={statistics.median(delays):.0f}s, "
                      f"min={min(delays)}s, max={max(delays)}s")
analysis3_lines.append(f"**Trading duration**: mean={statistics.mean(durations):.0f}s, median={statistics.median(durations):.0f}s")
analysis3_lines.append(f"**UP/DOWN gap**: mean={statistics.mean(gaps):.0f}s, median={statistics.median(gaps):.0f}s" if gaps else "")

burst_pcts = [d["burst_pct"] for d in entry_seq_data]
analysis3_lines.append(f"**Same-second fills**: mean={statistics.mean(burst_pcts):.0f}%")
analysis3_lines.append("(High same-second fill % = fragmented maker fills, NOT taker sweeps)")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 4: Fill size distribution
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 4: Fill size distribution")
print("="*70)

analysis4_lines = []
analysis4_lines.append("# ANALYSIS 4: Fill Size Distribution\n")

# All fill sizes in USDC
all_usdc = [t["usdcSize"] for t in all_trades]
all_shares = [t["size"] for t in all_trades]

# Separate by lean vs hedge
# First determine lean side for each market
lean_side_map = {}  # slug -> "Up" or "Down"
for slug, m in markets.items():
    if m["up_usdc"] >= m["down_usdc"]:
        lean_side_map[slug] = "Up"
    else:
        lean_side_map[slug] = "Down"

lean_fills = []
hedge_fills = []
for t in all_trades:
    slug = t["slug"]
    if slug in lean_side_map:
        if t["outcome"] == lean_side_map[slug]:
            lean_fills.append(t["usdcSize"])
        else:
            hedge_fills.append(t["usdcSize"])

def pct(arr, p):
    arr_s = sorted(arr)
    idx = int(len(arr_s) * p / 100)
    return arr_s[min(idx, len(arr_s)-1)]

print(f"ALL fills (n={len(all_usdc)}):")
print(f"  min=${min(all_usdc):.4f}, p25=${pct(all_usdc,25):.2f}, p50=${pct(all_usdc,50):.2f}, "
      f"p75=${pct(all_usdc,75):.2f}, p90=${pct(all_usdc,90):.2f}, p99=${pct(all_usdc,99):.2f}, max=${max(all_usdc):.2f}")
print(f"  mean=${statistics.mean(all_usdc):.2f}")

analysis4_lines.append("## All Fills")
analysis4_lines.append(f"- Count: {len(all_usdc)}")
analysis4_lines.append(f"- Min: ${min(all_usdc):.4f}")
analysis4_lines.append(f"- P25: ${pct(all_usdc,25):.2f}")
analysis4_lines.append(f"- **P50 (median): ${pct(all_usdc,50):.2f}**")
analysis4_lines.append(f"- P75: ${pct(all_usdc,75):.2f}")
analysis4_lines.append(f"- P90: ${pct(all_usdc,90):.2f}")
analysis4_lines.append(f"- P99: ${pct(all_usdc,99):.2f}")
analysis4_lines.append(f"- Max: ${max(all_usdc):.2f}")
analysis4_lines.append(f"- Mean: ${statistics.mean(all_usdc):.2f}")

if lean_fills:
    analysis4_lines.append(f"\n## Lean Side Fills (n={len(lean_fills)})")
    analysis4_lines.append(f"- P50: ${pct(lean_fills,50):.2f}, Mean: ${statistics.mean(lean_fills):.2f}")

if hedge_fills:
    analysis4_lines.append(f"\n## Hedge Side Fills (n={len(hedge_fills)})")
    analysis4_lines.append(f"- P50: ${pct(hedge_fills,50):.2f}, Mean: ${statistics.mean(hedge_fills):.2f}")

# Histogram
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

# All fills
axes[0].hist(all_usdc, bins=50, range=(0, min(max(all_usdc), 80)), color="steelblue", edgecolor="black", alpha=0.7)
axes[0].axvline(pct(all_usdc, 50), color="red", linestyle="--", label=f"Median=${pct(all_usdc,50):.2f}")
axes[0].set_title(f"All Fills (n={len(all_usdc)})")
axes[0].set_xlabel("USDC per fill")
axes[0].legend()

# Lean fills
if lean_fills:
    axes[1].hist(lean_fills, bins=50, range=(0, min(max(lean_fills), 80)), color="green", edgecolor="black", alpha=0.7)
    axes[1].axvline(pct(lean_fills, 50), color="red", linestyle="--", label=f"Median=${pct(lean_fills,50):.2f}")
    axes[1].set_title(f"Lean Side (n={len(lean_fills)})")
    axes[1].set_xlabel("USDC per fill")
    axes[1].legend()

# Hedge fills
if hedge_fills:
    axes[2].hist(hedge_fills, bins=50, range=(0, min(max(hedge_fills), 80)), color="orange", edgecolor="black", alpha=0.7)
    axes[2].axvline(pct(hedge_fills, 50), color="red", linestyle="--", label=f"Median=${pct(hedge_fills,50):.2f}")
    axes[2].set_title(f"Hedge Side (n={len(hedge_fills)})")
    axes[2].set_xlabel("USDC per fill")
    axes[2].legend()

fig.tight_layout()
fig.savefig(PLOT_DIR / "a4_fill_size_histogram.png", dpi=150)
plt.close(fig)
print(f"Saved: {PLOT_DIR / 'a4_fill_size_histogram.png'}")

# Share size distribution
analysis4_lines.append(f"\n## Share Size Distribution")
analysis4_lines.append(f"- Min: {min(all_shares):.4f} shares, Median: {pct(all_shares,50):.2f}, Max: {max(all_shares):.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 5: Entry delay relative to window open
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 5: Entry delay vs BTC move")
print("="*70)

analysis5_lines = []
analysis5_lines.append("# ANALYSIS 5: Entry Delay Relative to Window Open\n")

# Filter to meaningful windows (>5 trades, exclude stale 09:15 entries)
delay_data = []
for slug, m in markets.items():
    if m["n_trades"] < 5 or m["entry_delay_s"] > 2000:
        continue
    delay_data.append({
        "slug": slug,
        "coin": m["coin"],
        "tf": m["tf"],
        "delay_s": m["entry_delay_s"],
        "btc_move_at_entry": abs(m["btc_return_at_entry_bps"]) if m["btc_return_at_entry_bps"] else None,
        "total_usdc": m["total_usdc"],
    })

# Sort by delay
delay_data.sort(key=lambda x: x["delay_s"])
for d in delay_data:
    line = f"  {d['coin']:3s} {d['tf']:3s} delay={d['delay_s']:4d}s | ${d['total_usdc']:8.2f} | BTC move at entry={d['btc_move_at_entry']:.1f}bps" if d["btc_move_at_entry"] else f"  {d['coin']:3s} {d['tf']:3s} delay={d['delay_s']:4d}s | ${d['total_usdc']:8.2f} | BTC=N/A"
    analysis5_lines.append(line)
    print(line)

# Group by timeframe
for tf in ["5M", "15M", "1H"]:
    tf_delays = [d["delay_s"] for d in delay_data if d["tf"] == tf]
    if tf_delays:
        analysis5_lines.append(f"\n**{tf} windows**: mean delay={statistics.mean(tf_delays):.0f}s, "
                              f"median={statistics.median(tf_delays):.0f}s, "
                              f"range=[{min(tf_delays)}, {max(tf_delays)}]s")
        print(f"{tf}: mean={statistics.mean(tf_delays):.0f}s median={statistics.median(tf_delays):.0f}s")

# Correlation: delay vs BTC move
delay_moves = [(d["delay_s"], d["btc_move_at_entry"]) for d in delay_data if d["btc_move_at_entry"] is not None]
if len(delay_moves) > 3:
    delays_arr = [x[0] for x in delay_moves]
    moves_arr = [x[1] for x in delay_moves]
    corr = np.corrcoef(delays_arr, moves_arr)[0, 1]
    analysis5_lines.append(f"\n**Correlation (delay vs |BTC move at entry|)**: r = {corr:.4f}")
    analysis5_lines.append("(Negative = enters faster on bigger moves)")

# Plot
fig, ax = plt.subplots(figsize=(10, 6))
for d in delay_data:
    if d["btc_move_at_entry"] is None:
        continue
    c = colors.get(d["coin"], "gray")
    marker = "o" if d["tf"] == "5M" else "s" if d["tf"] == "15M" else "^"
    ax.scatter(d["delay_s"], d["btc_move_at_entry"], c=c, s=d["total_usdc"]/5,
              alpha=0.6, edgecolors="black", linewidth=0.5, marker=marker)

ax.set_xlabel("Entry Delay After Window Open (seconds)")
ax.set_ylabel("|BTC Move at Entry| (bps)")
ax.set_title("W4: Entry Delay vs BTC Move Magnitude")
# Manual legend
from matplotlib.lines import Line2D
legend_elements = [
    Line2D([0], [0], marker="o", color="w", markerfacecolor=colors["BTC"], markersize=8, label="BTC"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor=colors["ETH"], markersize=8, label="ETH"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor=colors["SOL"], markersize=8, label="SOL"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor=colors["XRP"], markersize=8, label="XRP"),
    Line2D([0], [0], marker="o", color="gray", linestyle="", markersize=8, label="5M"),
    Line2D([0], [0], marker="s", color="gray", linestyle="", markersize=8, label="15M"),
    Line2D([0], [0], marker="^", color="gray", linestyle="", markersize=8, label="1H"),
]
ax.legend(handles=legend_elements, loc="upper right")
fig.tight_layout()
fig.savefig(PLOT_DIR / "a5_delay_vs_btc_move.png", dpi=150)
plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 6: W4's EXACT P&L per window
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 6: W4's P&L per window")
print("="*70)

analysis6_lines = []
analysis6_lines.append("# ANALYSIS 6: W4's Exact P&L Per Window\n")
analysis6_lines.append("Resolution: winning side shares x $1.00, losing side = $0.00")
analysis6_lines.append("Payout includes the 2% fee on winnings on Polymarket.\n")

FEE_RATE = 0.02  # Polymarket 2% fee on winnings

pnl_records = []
cumulative_pnl = 0.0
cumulative_cost = 0.0

for slug, m in sorted(markets.items(), key=lambda x: x[1]["window_open"]):
    if m["n_trades"] < 2:
        continue

    btc_ret = m["btc_return_bps"]
    if btc_ret is None:
        # Can't determine outcome
        continue

    # For BTC markets, outcome is straightforward
    # For non-BTC, we still use BTC return since that's the reference
    # Actually, each coin resolves independently based on ITS OWN price
    # But we only have BTC candles. For non-BTC coins, we can't determine outcome.
    # Let's note this limitation and only do P&L for BTC markets.

    if m["coin"] != "BTC":
        # We don't have ETH/SOL/XRP candle data to determine outcome
        continue

    # BTC went up or down?
    btc_up = btc_ret > 0
    # If btc_ret == 0, it's a toss-up (typically resolves to "Up" on Polymarket for >=)
    if btc_ret == 0:
        btc_up = True  # Polymarket: "Up" wins on flat

    # Payout
    if btc_up:
        # Up wins: up_shares * $1, down_shares * $0
        gross_payout = m["up_shares"]
        winning_profit = gross_payout - m["up_usdc"]  # profit on winning side only
        fee = max(0, winning_profit) * FEE_RATE
        net_payout = gross_payout - fee
    else:
        # Down wins
        gross_payout = m["down_shares"]
        winning_profit = gross_payout - m["down_usdc"]
        fee = max(0, winning_profit) * FEE_RATE
        net_payout = gross_payout - fee

    total_cost = m["total_usdc"]
    pnl = net_payout - total_cost
    roi = pnl / total_cost * 100 if total_cost > 0 else 0

    cumulative_cost += total_cost
    cumulative_pnl += pnl

    outcome_str = "UP" if btc_up else "DOWN"
    dt_open = datetime.fromtimestamp(m["window_open"], tz=ET)

    pnl_records.append({
        "slug": slug,
        "tf": m["tf"],
        "dt": dt_open.strftime("%H:%M"),
        "outcome": outcome_str,
        "cost": total_cost,
        "up_shares": m["up_shares"],
        "down_shares": m["down_shares"],
        "gross_payout": gross_payout,
        "fee": fee,
        "net_payout": net_payout,
        "pnl": pnl,
        "roi": roi,
        "cum_pnl": cumulative_pnl,
        "btc_ret_bps": btc_ret,
    })

    marker = "+" if pnl >= 0 else "-"
    line = (
        f"  [{marker}] BTC {m['tf']:3s} {dt_open.strftime('%H:%M')}ET | "
        f"BTC {btc_ret:+.1f}bps→{outcome_str} | "
        f"Cost=${total_cost:.2f} | Up={m['up_shares']:.1f}sh Down={m['down_shares']:.1f}sh | "
        f"Payout=${net_payout:.2f} | PnL=${pnl:+.2f} ({roi:+.1f}%) | Cum=${cumulative_pnl:+.2f}"
    )
    analysis6_lines.append(line)
    print(line)

# Summary
if pnl_records:
    wins = sum(1 for r in pnl_records if r["pnl"] > 0)
    losses = sum(1 for r in pnl_records if r["pnl"] < 0)
    flat = sum(1 for r in pnl_records if r["pnl"] == 0)
    total_invested = sum(r["cost"] for r in pnl_records)
    total_pnl = sum(r["pnl"] for r in pnl_records)

    analysis6_lines.append(f"\n**BTC Markets Summary (14-min snapshot)**:")
    analysis6_lines.append(f"- Windows: {len(pnl_records)} | W/L: {wins}/{losses} | WR: {wins/len(pnl_records)*100:.1f}%")
    analysis6_lines.append(f"- Total invested: ${total_invested:.2f}")
    analysis6_lines.append(f"- Total P&L: ${total_pnl:+.2f}")
    analysis6_lines.append(f"- ROI: {total_pnl/total_invested*100:+.1f}%")

    avg_win = statistics.mean([r["pnl"] for r in pnl_records if r["pnl"] > 0]) if wins > 0 else 0
    avg_loss = statistics.mean([r["pnl"] for r in pnl_records if r["pnl"] < 0]) if losses > 0 else 0
    analysis6_lines.append(f"- Avg win: ${avg_win:+.2f} | Avg loss: ${avg_loss:+.2f}")

    print(f"\nBTC: {wins}W/{losses}L, Total PnL=${total_pnl:+.2f}, ROI={total_pnl/total_invested*100:+.1f}%")

# Cumulative PnL plot
if pnl_records:
    fig, ax = plt.subplots(figsize=(10, 5))
    x_labels = [f"{r['tf']} {r['dt']}" for r in pnl_records]
    cum_pnls = [r["cum_pnl"] for r in pnl_records]
    bar_colors = ["green" if r["pnl"] >= 0 else "red" for r in pnl_records]

    ax.bar(range(len(pnl_records)), [r["pnl"] for r in pnl_records], color=bar_colors, alpha=0.5, label="Per-window PnL")
    ax.plot(range(len(pnl_records)), cum_pnls, "b-o", linewidth=2, markersize=5, label="Cumulative PnL")
    ax.set_xticks(range(len(pnl_records)))
    ax.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("P&L (USDC)")
    ax.set_title("W4 BTC Markets: Per-Window & Cumulative P&L")
    ax.legend()
    ax.axhline(0, color="gray", linestyle="--")
    fig.tight_layout()
    fig.savefig(PLOT_DIR / "a6_btc_pnl.png", dpi=150)
    plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 7: The W4 Playbook (comprehensive summary)
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("ANALYSIS 7: The W4 Playbook")
print("="*70)

analysis7_lines = []
analysis7_lines.append("# ANALYSIS 7: The W4 Playbook\n")

# ── Gather all key metrics ───────────────────────────────────────────────────

# Entry delay stats (excluding stale windows)
active_markets = {s: m for s, m in markets.items() if m["n_trades"] >= 5 and m["entry_delay_s"] < 2000}

delays_5m = [m["entry_delay_s"] for m in active_markets.values() if m["tf"] == "5M"]
delays_15m = [m["entry_delay_s"] for m in active_markets.values() if m["tf"] == "15M"]
delays_1h = [m["entry_delay_s"] for m in active_markets.values() if m["tf"] == "1H"]

# Lean ratios (only markets with both sides)
both_side_markets = {s: m for s, m in active_markets.items() if m["up_usdc"] > 0 and m["down_usdc"] > 0}

lean_ratios_all = []
lean_correct = 0
lean_total = 0
for s, m in both_side_markets.items():
    if m["up_usdc"] >= m["down_usdc"]:
        lr = m["up_usdc"] / m["down_usdc"]
        lean = "UP"
    else:
        lr = m["down_usdc"] / m["up_usdc"]
        lean = "DOWN"
    lean_ratios_all.append(lr)

    # Check correctness for BTC only
    if m["coin"] == "BTC" and m["btc_return_bps"] is not None:
        lean_total += 1
        btc_up = m["btc_return_bps"] > 0
        if (lean == "UP" and btc_up) or (lean == "DOWN" and not btc_up):
            lean_correct += 1

# Average prices
avg_prices_up = [sum(t["price"] * t["usdcSize"] for t in m["up_trades"]) / m["up_usdc"]
                 for m in both_side_markets.values() if m["up_usdc"] > 0]
avg_prices_down = [sum(t["price"] * t["usdcSize"] for t in m["down_trades"]) / m["down_usdc"]
                   for m in both_side_markets.values() if m["down_usdc"] > 0]

# Fill sizes
all_fill_usdc = [t["usdcSize"] for t in all_trades]

# Total per window
totals_per_window = [m["total_usdc"] for m in active_markets.values()]

# Trading duration
durations_all = [m["trading_duration_s"] for m in active_markets.values()]

# Number of coins traded simultaneously
# Group by window_open to see how many coins per time slot
window_coins = defaultdict(set)
for m in active_markets.values():
    window_coins[m["window_open"]].add(m["coin"])

# ── Build the playbook ───────────────────────────────────────────────────────

playbook_text = f"""
## The W4 Playbook: Step-by-Step Recipe

### Identity
- Wallet: `0x818f...58cb` (pseudonym: "Decent-Dune" / "livebreathevolatility")
- Trades BTC, ETH, SOL, XRP simultaneously on both 5M and 15M windows
- Average {len(active_markets)/len(set(m['window_open'] for m in active_markets.values())):.1f} markets per time slot

### Step 1: Window Discovery
- Monitors multiple time windows: 5M + 15M + 1H for BTC/ETH/SOL/XRP
- In this 14-minute snapshot: {len(active_markets)} active markets across {len(set(m['window_open'] for m in active_markets.values()))} time slots

### Step 2: Entry Timing
- **5M windows**: enters {statistics.median(delays_5m):.0f}s after open (median), range [{min(delays_5m)}-{max(delays_5m)}]s
- **15M windows**: enters {statistics.median(delays_15m):.0f}s after open (median), range [{min(delays_15m)}-{max(delays_15m)}]s
- **1H windows**: enters ~{statistics.median(delays_1h):.0f}s after open (limited data)
- **Pattern**: Very fast entry on 5M (9-17s), slightly slower on 15M (~29s for current window, ~740s for previous)

### Step 3: Determine Direction
- Checks BTC return since window open
- Lean direction = follows the BTC move direction
- **BTC lean accuracy**: {lean_correct}/{lean_total} = {lean_correct/lean_total*100:.1f}% (based on available data)

### Step 4: Place Orders — BOTH SIDES (Hedged Directional)
- **Always buys BOTH Up and Down** — not pure directional
- **Lean ratio**: median {statistics.median(lean_ratios_all):.2f}x, mean {statistics.mean(lean_ratios_all):.2f}x
  - Range: [{min(lean_ratios_all):.2f}x - {max(lean_ratios_all):.2f}x]
- Lean side gets ~60-75% of capital; hedge side gets ~25-40%

### Step 5: Pricing
- Average Up price paid: ${statistics.mean(avg_prices_up):.4f}
- Average Down price paid: ${statistics.mean(avg_prices_down):.4f}
- Sum (up + down): ~${statistics.mean(avg_prices_up) + statistics.mean(avg_prices_down):.4f}
  - Below $1.00 = guaranteed theoretical profit if buying equal shares
  - W4 exploits the spread by being a MAKER on both sides

### Step 6: Order Sizing
- Individual fill: median ${pct(all_fill_usdc, 50):.2f}, mean ${statistics.mean(all_fill_usdc):.2f}
- Fill range: ${min(all_fill_usdc):.4f} to ${max(all_fill_usdc):.2f}
- Total per window: median ${statistics.median(totals_per_window):.2f}, mean ${statistics.mean(totals_per_window):.2f}
- Total range: ${min(totals_per_window):.2f} to ${max(totals_per_window):.2f}

### Step 7: Execution Style
- **Fragmented fills**: many small fills at same price = LIMIT ORDERS (maker)
- Trading duration per window: median {statistics.median(durations_all):.0f}s, range [{min(durations_all)}-{max(durations_all)}]s
- Places orders across multiple coins simultaneously (multi-market maker)

### Step 8: Hold to Resolution
- No evidence of exit trades in this data — all BUY side
- Holds all positions to window expiry
- Collects $1.00 per winning share, $0.00 per losing share

---

## Distilled Recipe (Concrete Parameters)

```
TRIGGER:
  At T+{statistics.median([d for d in delays_5m]):.0f}s (5M) / T+{min(delays_15m):.0f}s (15M) after window open:
    Check BTC return since window open

ENTRY:
  Buy LEAN side at ~${statistics.mean(avg_prices_up):.2f}-${max(avg_prices_up):.2f} (directional bet)
  Buy HEDGE side at ~${statistics.mean(avg_prices_down):.2f}-${max(avg_prices_down):.2f} (insurance)
  Lean:Hedge ratio ≈ {statistics.median(lean_ratios_all):.1f}:1

SIZING:
  Individual fills: ~${pct(all_fill_usdc, 50):.0f}-${pct(all_fill_usdc, 75):.0f} each (limit orders, fragmented)
  Total per window: ~${statistics.median(totals_per_window):.0f}
  Run {len(set(m['coin'] for m in active_markets.values()))} coins x multiple timeframes simultaneously

EXECUTION:
  Style: MAKER (limit orders, not market orders)
  Duration: {statistics.median(durations_all):.0f}s of active trading per window

RESOLUTION:
  Hold to expiry. No early exit.
```
"""

analysis7_lines.append(playbook_text)
print(playbook_text)


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL: Per-timeframe breakdown
# ══════════════════════════════════════════════════════════════════════════════

# Per-timeframe summary table
tf_summary_lines = []
tf_summary_lines.append("\n# Appendix: Per-Timeframe Summary\n")
tf_summary_lines.append("| TF | Markets | Total USDC | Avg/Market | Median Lean | Median Delay |")
tf_summary_lines.append("|----|---------|-----------:|-----------:|------------:|-------------:|")

for tf in ["5M", "15M", "1H"]:
    tf_mkts = [m for m in active_markets.values() if m["tf"] == tf]
    if not tf_mkts:
        continue
    total = sum(m["total_usdc"] for m in tf_mkts)
    avg = total / len(tf_mkts)
    lean_rs = []
    for m in tf_mkts:
        if m["up_usdc"] > 0 and m["down_usdc"] > 0:
            lean_rs.append(max(m["up_usdc"], m["down_usdc"]) / min(m["up_usdc"], m["down_usdc"]))
    med_lean = statistics.median(lean_rs) if lean_rs else 0
    delays = [m["entry_delay_s"] for m in tf_mkts]
    med_delay = statistics.median(delays)
    tf_summary_lines.append(f"| {tf:3s} | {len(tf_mkts):7d} | ${total:9.2f} | ${avg:9.2f} | {med_lean:10.2f}x | {med_delay:10.0f}s |")


# ══════════════════════════════════════════════════════════════════════════════
# Per-slug detail table
# ══════════════════════════════════════════════════════════════════════════════

detail_lines = []
detail_lines.append("\n# Appendix: All Markets Detail\n")
detail_lines.append("| Coin | TF | Window ET | Trades | UP $ | DOWN $ | Total $ | Lean | Ratio | Delay(s) | Duration(s) |")
detail_lines.append("|------|----|-----------|-------:|-----:|-------:|--------:|------|------:|--------:|------------:|")

for slug, m in sorted(active_markets.items(), key=lambda x: x[1]["window_open"]):
    dt_open = datetime.fromtimestamp(m["window_open"], tz=ET)
    lean = "UP" if m["up_usdc"] >= m["down_usdc"] else "DOWN"
    ratio = max(m["up_usdc"], m["down_usdc"]) / min(m["up_usdc"], m["down_usdc"]) if min(m["up_usdc"], m["down_usdc"]) > 0 else float("inf")
    detail_lines.append(
        f"| {m['coin']:3s} | {m['tf']:3s} | {dt_open.strftime('%H:%M')}ET | "
        f"{m['n_trades']:5d} | ${m['up_usdc']:7.2f} | ${m['down_usdc']:8.2f} | ${m['total_usdc']:8.2f} | "
        f"{lean:4s} | {ratio:5.2f}x | {m['entry_delay_s']:6d} | {m['trading_duration_s']:10d} |"
    )


# ══════════════════════════════════════════════════════════════════════════════
# WRITE REPORT
# ══════════════════════════════════════════════════════════════════════════════

report = []
report.append("# Agent B Report: W4 Execution Pattern Reconstruction")
report.append(f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
report.append(f"> Data: 14-minute snapshot ({len(all_trades)} trades, {len(markets)} markets)")
report.append(f"> Wallet: 0x818f...58cb (Decent-Dune / livebreathevolatility)")
report.append("")
report.append("---\n")
report.extend(analysis1_lines)
report.append("\n---\n")
report.extend(analysis2_lines)
report.append("\n---\n")
report.extend(analysis3_lines)
report.append("\n---\n")
report.extend(analysis4_lines)
report.append("\n---\n")
report.extend(analysis5_lines)
report.append("\n---\n")
report.extend(analysis6_lines)
report.append("\n---\n")
report.extend(analysis7_lines)
report.append("\n---\n")
report.extend(tf_summary_lines)
report.extend(detail_lines)

with open(REPORT_FILE, "w") as f:
    f.write("\n".join(report))

print(f"\n{'='*70}")
print(f"Report saved: {REPORT_FILE}")
print(f"Plots saved: {PLOT_DIR}/")
print(f"{'='*70}")
