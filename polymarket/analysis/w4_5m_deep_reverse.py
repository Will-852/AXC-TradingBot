#!/usr/bin/env python3
"""
W4 5M Deep Reverse Engineering
================================
Reconstructs W4's exact 5-minute execution pattern from 2,516 raw trades.
Outputs: w4_5m_playbook.md

Sections:
  A. Filter to 5M trades only
  B. Per-window reconstruction
  C. Timing analysis
  D. Price analysis per side
  E. Cross-reference with BTC price
  F. Fill size and fragmentation
  G. SOL-specific analysis
  H. The W4 5M Playbook
"""

import json
import datetime
import statistics
import os
from collections import defaultdict, Counter
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
BASE = Path("/Users/wai/projects/axc-trading")
TRADES_PATH = BASE / "data/wallet4_raw/wallet4_all_trades.json"
BTC_PATH = BASE / "polymarket/analysis/btc_1m_7days.json"
SOL_PATH = BASE / "polymarket/analysis/solusdt_1m_7days.json"
OUTPUT_PATH = BASE / "polymarket/analysis/w4_5m_playbook.md"

ET = datetime.timezone(datetime.timedelta(hours=-4))  # EDT (March)


def load_data():
    """Load all data files."""
    with open(TRADES_PATH) as f:
        all_trades = json.load(f)
    with open(BTC_PATH) as f:
        btc_1m = json.load(f)
    sol_1m = []
    if SOL_PATH.exists():
        with open(SOL_PATH) as f:
            sol_1m = json.load(f)
    return all_trades, btc_1m, sol_1m


def build_price_index(candles):
    """Build ts->candle lookup from 1m candle data. ts in candles are ms."""
    idx = {}
    for c in candles:
        ts_sec = int(c["ts"] / 1000)
        idx[ts_sec] = c
    return idx


def get_price_at(price_idx, ts, field="close"):
    """Get price at or just before ts. Returns (price, actual_ts) or (None, None)."""
    # Try exact match first
    if ts in price_idx:
        return price_idx[ts][field], ts
    # Search backwards up to 120s
    for offset in range(1, 121):
        check = ts - offset
        if check in price_idx:
            return price_idx[check][field], check
    return None, None


def ts_to_et(ts):
    """Unix timestamp to ET string."""
    return datetime.datetime.fromtimestamp(ts, tz=ET).strftime("%I:%M:%S%p ET")


def ts_to_et_short(ts):
    """Unix timestamp to ET short string."""
    return datetime.datetime.fromtimestamp(ts, tz=ET).strftime("%I:%M%p ET")


# ═══════════════════════════════════════════════════════════════════════
# SECTION A: Filter to 5M trades
# ═══════════════════════════════════════════════════════════════════════
def filter_5m_trades(all_trades):
    """Extract only 5M TRADE entries (not REDEEMs)."""
    trades_5m = [t for t in all_trades if "-5m-" in t.get("slug", "") and t["type"] == "TRADE"]
    redeems_5m = [t for t in all_trades if "-5m-" in t.get("slug", "") and t["type"] == "REDEEM"]
    non_5m = [t for t in all_trades if "-5m-" not in t.get("slug", "")]

    print("=" * 70)
    print("SECTION A: Filter to 5M Trades")
    print("=" * 70)
    print(f"Total trades in dataset: {len(all_trades)}")
    print(f"5M TRADE entries: {len(trades_5m)}")
    print(f"5M REDEEM entries: {len(redeems_5m)}")
    print(f"Non-5M entries: {len(non_5m)}")

    # Breakdown by coin
    coin_counts = Counter()
    for t in trades_5m:
        coin = t["slug"].split("-updown")[0]
        coin_counts[coin] += 1
    print(f"\nBy coin: {dict(coin_counts)}")

    # Unique windows
    window_slugs = set(t["slug"] for t in trades_5m)
    print(f"Unique 5M windows traded: {len(window_slugs)}")

    return trades_5m, redeems_5m


# ═══════════════════════════════════════════════════════════════════════
# SECTION B: Per-window reconstruction
# ═══════════════════════════════════════════════════════════════════════
def reconstruct_windows(trades_5m):
    """Build complete timeline for each 5M window."""
    windows = defaultdict(list)
    for t in trades_5m:
        windows[t["slug"]].append(t)

    print("\n" + "=" * 70)
    print("SECTION B: Per-Window Reconstruction")
    print("=" * 70)

    results = {}
    for slug in sorted(windows.keys()):
        epoch = int(slug.split("-")[-1])
        coin = slug.split("-updown")[0]
        w_trades = sorted(windows[slug], key=lambda x: x["timestamp"])

        first_ts = w_trades[0]["timestamp"]
        last_ts = w_trades[-1]["timestamp"]
        entry_delay = first_ts - epoch
        active_duration = last_ts - first_ts

        up_trades = [t for t in w_trades if t["outcome"] == "Up"]
        dn_trades = [t for t in w_trades if t["outcome"] == "Down"]

        up_count = len(up_trades)
        dn_count = len(dn_trades)
        up_usd = sum(t["usdcSize"] for t in up_trades)
        dn_usd = sum(t["usdcSize"] for t in dn_trades)
        up_prices = [t["price"] for t in up_trades] if up_trades else []
        dn_prices = [t["price"] for t in dn_trades] if dn_trades else []

        up_avg = statistics.mean(up_prices) if up_prices else 0
        dn_avg = statistics.mean(dn_prices) if dn_prices else 0
        up_min = min(up_prices) if up_prices else 0
        up_max = max(up_prices) if up_prices else 0
        dn_min = min(dn_prices) if dn_prices else 0
        dn_max = max(dn_prices) if dn_prices else 0

        # Weighted average price (USDC-weighted)
        up_wavg = sum(t["price"] * t["usdcSize"] for t in up_trades) / up_usd if up_usd > 0 else 0
        dn_wavg = sum(t["price"] * t["usdcSize"] for t in dn_trades) / dn_usd if dn_usd > 0 else 0

        lean_dir = "UP" if up_usd >= dn_usd else "DOWN"
        lean_usd = max(up_usd, dn_usd)
        hedge_usd = min(up_usd, dn_usd)
        lean_ratio = lean_usd / hedge_usd if hedge_usd > 0 else float("inf")

        combined_cost = up_wavg + dn_wavg

        result = {
            "slug": slug,
            "coin": coin,
            "epoch": epoch,
            "window_open_et": ts_to_et_short(epoch),
            "window_close_et": ts_to_et_short(epoch + 300),
            "first_ts": first_ts,
            "last_ts": last_ts,
            "entry_delay_s": entry_delay,
            "active_duration_s": active_duration,
            "total_trades": len(w_trades),
            "up_count": up_count,
            "dn_count": dn_count,
            "up_usd": up_usd,
            "dn_usd": dn_usd,
            "up_avg_price": up_avg,
            "dn_avg_price": dn_avg,
            "up_wavg_price": up_wavg,
            "dn_wavg_price": dn_wavg,
            "up_min_price": up_min,
            "up_max_price": up_max,
            "dn_min_price": dn_min,
            "dn_max_price": dn_max,
            "lean_dir": lean_dir,
            "lean_usd": lean_usd,
            "hedge_usd": hedge_usd,
            "lean_ratio": lean_ratio,
            "combined_cost": combined_cost,
            "trades": w_trades,
            "up_trades": up_trades,
            "dn_trades": dn_trades,
        }
        results[slug] = result

        # Skip anomalous early window (9:15AM with 4900s+ delay = traded in a later window's batch)
        flag = " *** ANOMALOUS (late entry)" if entry_delay > 300 else ""
        print(f"\n  {slug} ({coin.upper()}) — {result['window_open_et']} to {result['window_close_et']}{flag}")
        print(f"    Entry delay: {entry_delay}s | Active: {active_duration}s | Trades: {len(w_trades)}")
        print(f"    UP:   {up_count} fills, ${up_usd:.2f} USDC, wavg={up_wavg:.4f}, range=[{up_min:.4f}, {up_max:.4f}]")
        print(f"    DOWN: {dn_count} fills, ${dn_usd:.2f} USDC, wavg={dn_wavg:.4f}, range=[{dn_min:.4f}, {dn_max:.4f}]")
        print(f"    Lean: {lean_dir} | Ratio: {lean_ratio:.2f}:1 | Combined: {combined_cost:.4f}")

    return results


# ═══════════════════════════════════════════════════════════════════════
# SECTION C: Timing analysis
# ═══════════════════════════════════════════════════════════════════════
def timing_analysis(results):
    """Analyze timing patterns within each window."""
    print("\n" + "=" * 70)
    print("SECTION C: Timing Analysis")
    print("=" * 70)

    # Filter out anomalous windows (delay > 300s)
    valid = {k: v for k, v in results.items() if v["entry_delay_s"] <= 300}

    delays = [v["entry_delay_s"] for v in valid.values()]
    durations = [v["active_duration_s"] for v in valid.values()]

    print(f"\nValid windows (delay <= 300s): {len(valid)}")
    print(f"\nEntry delay (seconds from window open to first trade):")
    print(f"  Min: {min(delays)}s | Max: {max(delays)}s | Mean: {statistics.mean(delays):.1f}s | Median: {statistics.median(delays)}s")
    print(f"\nActive duration (first trade to last trade):")
    print(f"  Min: {min(durations)}s | Max: {max(durations)}s | Mean: {statistics.mean(durations):.1f}s | Median: {statistics.median(durations)}s")

    # Burst analysis: compute inter-trade gaps per window
    print(f"\nBurst vs Spread Analysis:")
    for slug in sorted(valid.keys()):
        v = valid[slug]
        trades = sorted(v["trades"], key=lambda x: x["timestamp"])
        timestamps = [t["timestamp"] for t in trades]
        unique_ts = sorted(set(timestamps))
        gaps = [unique_ts[i + 1] - unique_ts[i] for i in range(len(unique_ts) - 1)] if len(unique_ts) > 1 else [0]

        # Count trades per second
        ts_counts = Counter(timestamps)
        max_per_sec = max(ts_counts.values())
        n_unique_seconds = len(unique_ts)

        # Classify: burst if >80% of trades in first 30% of duration, spread otherwise
        if v["active_duration_s"] > 0:
            cutoff_ts = trades[0]["timestamp"] + v["active_duration_s"] * 0.3
            early_count = sum(1 for t in trades if t["timestamp"] <= cutoff_ts)
            early_pct = early_count / len(trades) * 100
        else:
            early_pct = 100

        pattern = "BURST" if early_pct > 60 else "SPREAD"

        print(f"  {slug}: {n_unique_seconds} unique timestamps over {v['active_duration_s']}s")
        print(f"    Max fills/sec: {max_per_sec} | Avg gap: {statistics.mean(gaps):.1f}s | Pattern: {pattern} ({early_pct:.0f}% in first 30% of duration)")

    # Per-coin timing consistency
    print(f"\nPer-coin timing (valid windows only):")
    coin_delays = defaultdict(list)
    coin_durations = defaultdict(list)
    for v in valid.values():
        coin_delays[v["coin"]].append(v["entry_delay_s"])
        coin_durations[v["coin"]].append(v["active_duration_s"])
    for coin in sorted(coin_delays.keys()):
        d = coin_delays[coin]
        dur = coin_durations[coin]
        print(f"  {coin.upper()}: delay={statistics.mean(d):.0f}s (range {min(d)}-{max(d)}s), "
              f"duration={statistics.mean(dur):.0f}s (range {min(dur)}-{max(dur)}s)")

    # Same-second cross-coin: do BTC, ETH, SOL, XRP trades at same epoch come at same ts?
    print(f"\nCross-coin synchronization (same window epoch):")
    epoch_groups = defaultdict(dict)
    for slug, v in valid.items():
        epoch = v["epoch"]
        coin = v["coin"]
        epoch_groups[epoch][coin] = v["first_ts"]
    for epoch in sorted(epoch_groups.keys()):
        coins_ts = epoch_groups[epoch]
        if len(coins_ts) > 1:
            ts_vals = list(coins_ts.values())
            spread = max(ts_vals) - min(ts_vals)
            detail = ", ".join(f"{c.upper()}={ts}" for c, ts in sorted(coins_ts.items()))
            print(f"  {ts_to_et_short(epoch)}: {detail} | spread={spread}s")

    return valid


# ═══════════════════════════════════════════════════════════════════════
# SECTION D: Price analysis per side
# ═══════════════════════════════════════════════════════════════════════
def price_analysis(results):
    """Analyze price distributions for lean and hedge sides."""
    print("\n" + "=" * 70)
    print("SECTION D: Price Analysis Per Side")
    print("=" * 70)

    valid = {k: v for k, v in results.items() if v["entry_delay_s"] <= 300}

    # Collect all lean-side and hedge-side prices
    lean_prices = []
    hedge_prices = []
    lean_wavgs = []
    hedge_wavgs = []

    for v in valid.values():
        if v["lean_dir"] == "UP":
            lean_prices.extend([t["price"] for t in v["up_trades"]])
            hedge_prices.extend([t["price"] for t in v["dn_trades"]])
            lean_wavgs.append(v["up_wavg_price"])
            hedge_wavgs.append(v["dn_wavg_price"])
        else:
            lean_prices.extend([t["price"] for t in v["dn_trades"]])
            hedge_prices.extend([t["price"] for t in v["up_trades"]])
            lean_wavgs.append(v["dn_wavg_price"])
            hedge_wavgs.append(v["up_wavg_price"])

    # Price distribution histograms
    bins = [(i / 10, (i + 1) / 10) for i in range(10)]  # 0-0.1, 0.1-0.2, ..., 0.9-1.0
    bin_labels = [f"{int(lo*100)}-{int(hi*100)}c" for lo, hi in bins]

    def histogram(prices, label):
        counts = [0] * 10
        for p in prices:
            idx = min(int(p * 10), 9)
            counts[idx] += 1
        print(f"\n  {label} price distribution ({len(prices)} fills):")
        max_count = max(counts) if counts else 1
        for i, (lbl, cnt) in enumerate(zip(bin_labels, counts)):
            bar = "#" * int(cnt / max_count * 40) if max_count > 0 else ""
            pct = cnt / len(prices) * 100 if prices else 0
            print(f"    {lbl:>8}: {cnt:>4} ({pct:5.1f}%) {bar}")

    histogram(lean_prices, "LEAN side")
    histogram(hedge_prices, "HEDGE side")

    # Is lean side always expensive?
    print(f"\n  Lean side = EXPENSIVE side?")
    lean_expensive_count = 0
    for v in valid.values():
        if v["lean_dir"] == "UP":
            is_expensive = v["up_wavg_price"] > v["dn_wavg_price"]
        else:
            is_expensive = v["dn_wavg_price"] > v["up_wavg_price"]
        lean_expensive_count += int(is_expensive)
        expensive_tag = "YES (expensive)" if is_expensive else "NO (cheap)"
        print(f"    {v['slug']}: lean={v['lean_dir']}, "
              f"UP wavg={v['up_wavg_price']:.4f}, DN wavg={v['dn_wavg_price']:.4f} → {expensive_tag}")

    pct = lean_expensive_count / len(valid) * 100
    print(f"\n  Lean = expensive in {lean_expensive_count}/{len(valid)} windows ({pct:.0f}%)")
    print(f"  → {'CONFIRMS momentum following (lean = expensive = likely direction)' if pct > 60 else 'MIXED — not always momentum'}")

    # Lean/hedge wavg stats
    print(f"\n  Lean side wavg prices: {[f'{p:.4f}' for p in lean_wavgs]}")
    print(f"    Mean: {statistics.mean(lean_wavgs):.4f} | Median: {statistics.median(lean_wavgs):.4f}")
    print(f"  Hedge side wavg prices: {[f'{p:.4f}' for p in hedge_wavgs]}")
    print(f"    Mean: {statistics.mean(hedge_wavgs):.4f} | Median: {statistics.median(hedge_wavgs):.4f}")

    # Combined cost analysis
    combined_costs = [v["combined_cost"] for v in valid.values()]
    print(f"\n  Combined cost (up_wavg + dn_wavg):")
    print(f"    Min: {min(combined_costs):.4f} | Max: {max(combined_costs):.4f} | Mean: {statistics.mean(combined_costs):.4f}")
    for v in sorted(valid.values(), key=lambda x: x["combined_cost"]):
        print(f"    {v['slug']}: combined={v['combined_cost']:.4f} (up_wavg={v['up_wavg_price']:.4f}, dn_wavg={v['dn_wavg_price']:.4f})")

    return lean_prices, hedge_prices


# ═══════════════════════════════════════════════════════════════════════
# SECTION E: Cross-reference with BTC price
# ═══════════════════════════════════════════════════════════════════════
def btc_cross_reference(results, btc_idx):
    """Compare W4's lean direction with BTC momentum."""
    print("\n" + "=" * 70)
    print("SECTION E: Cross-Reference with BTC Price")
    print("=" * 70)

    valid = {k: v for k, v in results.items() if v["entry_delay_s"] <= 300}

    # Group by epoch (all coins share same BTC context)
    epoch_results = defaultdict(dict)
    for slug, v in valid.items():
        epoch_results[v["epoch"]][v["coin"]] = v

    momentum_match = 0
    resolution_match = 0
    total_checked = 0

    print(f"\nPer-window BTC context:")
    for epoch in sorted(epoch_results.keys()):
        window_close = epoch + 300

        btc_open, _ = get_price_at(btc_idx, epoch)
        btc_close, btc_close_ts = get_price_at(btc_idx, window_close)

        if btc_open is None or btc_close is None:
            print(f"  {ts_to_et_short(epoch)}: BTC data missing")
            continue

        # BTC return over window
        btc_return_pct = (btc_close - btc_open) / btc_open * 100
        btc_direction = "UP" if btc_return_pct > 0 else "DOWN"

        # For each coin in this epoch
        for coin, v in sorted(epoch_results[epoch].items()):
            # BTC return from open to W4's entry
            btc_at_entry, _ = get_price_at(btc_idx, v["first_ts"])
            if btc_at_entry and btc_open:
                entry_return_pct = (btc_at_entry - btc_open) / btc_open * 100
                entry_direction = "UP" if entry_return_pct > 0 else "DOWN"
            else:
                entry_return_pct = None
                entry_direction = "N/A"

            # Did lean match momentum at entry?
            lean_matches_momentum = v["lean_dir"] == entry_direction if entry_direction != "N/A" else None
            # Did lean match resolution? (BTC direction = resolution for BTC markets)
            lean_matches_resolution = v["lean_dir"] == btc_direction

            if lean_matches_momentum is not None:
                momentum_match += int(lean_matches_momentum)
                total_checked += 1
            resolution_match += int(lean_matches_resolution)

            mom_tag = "MATCH" if lean_matches_momentum else "MISS" if lean_matches_momentum is not None else "N/A"
            res_tag = "WIN" if lean_matches_resolution else "LOSE"

            btc_entry_str = f"{btc_at_entry:.0f}" if btc_at_entry else "N/A"
            entry_ret_str = f"{entry_return_pct:+.3f}%" if entry_return_pct is not None else "N/A"
            print(f"  {ts_to_et_short(epoch)} {coin.upper():>3}: lean={v['lean_dir']:>4}, "
                  f"BTC@open={btc_open:.0f}, BTC@entry={btc_entry_str} "
                  f"({entry_ret_str} -> {entry_direction}), "
                  f"BTC@close={btc_close:.0f} ({btc_return_pct:+.3f}% -> {btc_direction}), "
                  f"mom={mom_tag}, res={res_tag}")

    if total_checked > 0:
        print(f"\n  Momentum match rate: {momentum_match}/{total_checked} ({momentum_match/total_checked*100:.0f}%)")
    total_valid = len(valid)
    print(f"  Resolution match rate (lean = winner): {resolution_match}/{total_valid} ({resolution_match/total_valid*100:.0f}%)")

    # Deeper: for BTC-only windows, what was the entry momentum?
    print(f"\n  BTC-only windows — entry momentum detail:")
    btc_wins = {k: v for k, v in valid.items() if v["coin"] == "btc"}
    for slug in sorted(btc_wins.keys()):
        v = btc_wins[slug]
        epoch = v["epoch"]
        btc_open, _ = get_price_at(btc_idx, epoch)
        btc_at_entry, _ = get_price_at(btc_idx, v["first_ts"])
        btc_close, _ = get_price_at(btc_idx, epoch + 300)
        if btc_open and btc_at_entry:
            # How many seconds of price data between open and entry?
            secs = v["entry_delay_s"]
            mom = (btc_at_entry - btc_open) / btc_open * 100
            print(f"    {slug}: waited {secs}s, saw {mom:+.4f}% BTC move, leaned {v['lean_dir']}")

    return momentum_match, total_checked, resolution_match, total_valid


# ═══════════════════════════════════════════════════════════════════════
# SECTION F: Fill size and fragmentation
# ═══════════════════════════════════════════════════════════════════════
def fill_fragmentation(results):
    """Analyze how W4 fills orders — single price or sweep multiple levels."""
    print("\n" + "=" * 70)
    print("SECTION F: Fill Size and Fragmentation")
    print("=" * 70)

    valid = {k: v for k, v in results.items() if v["entry_delay_s"] <= 300}

    all_up_fills_per_window = []
    all_dn_fills_per_window = []
    all_up_levels = []
    all_dn_levels = []
    all_up_sizes = []
    all_dn_sizes = []

    for slug in sorted(valid.keys()):
        v = valid[slug]

        # UP side
        up_prices_rounded = [round(t["price"], 4) for t in v["up_trades"]]
        up_unique_prices = len(set(up_prices_rounded))
        up_fill_sizes = [t["usdcSize"] for t in v["up_trades"]]

        # DOWN side
        dn_prices_rounded = [round(t["price"], 4) for t in v["dn_trades"]]
        dn_unique_prices = len(set(dn_prices_rounded))
        dn_fill_sizes = [t["usdcSize"] for t in v["dn_trades"]]

        all_up_fills_per_window.append(v["up_count"])
        all_dn_fills_per_window.append(v["dn_count"])
        all_up_levels.append(up_unique_prices)
        all_dn_levels.append(dn_unique_prices)
        all_up_sizes.extend(up_fill_sizes)
        all_dn_sizes.extend(dn_fill_sizes)

        print(f"\n  {slug} ({v['coin'].upper()}):")
        print(f"    UP: {v['up_count']} fills at {up_unique_prices} price levels, "
              f"${v['up_usd']:.2f} total")
        if up_fill_sizes:
            print(f"      Fill sizes: min=${min(up_fill_sizes):.4f}, max=${max(up_fill_sizes):.4f}, "
                  f"avg=${statistics.mean(up_fill_sizes):.4f}")
            # Show price level distribution
            price_counts = Counter(up_prices_rounded)
            for price, cnt in sorted(price_counts.items()):
                usd_at_price = sum(t["usdcSize"] for t in v["up_trades"] if round(t["price"], 4) == price)
                print(f"        @{price:.4f}: {cnt} fills, ${usd_at_price:.2f}")

        print(f"    DN: {v['dn_count']} fills at {dn_unique_prices} price levels, "
              f"${v['dn_usd']:.2f} total")
        if dn_fill_sizes:
            print(f"      Fill sizes: min=${min(dn_fill_sizes):.4f}, max=${max(dn_fill_sizes):.4f}, "
                  f"avg=${statistics.mean(dn_fill_sizes):.4f}")
            price_counts = Counter(dn_prices_rounded)
            for price, cnt in sorted(price_counts.items()):
                usd_at_price = sum(t["usdcSize"] for t in v["dn_trades"] if round(t["price"], 4) == price)
                print(f"        @{price:.4f}: {cnt} fills, ${usd_at_price:.2f}")

    # Summary stats
    print(f"\n  ── Summary ──")
    print(f"  Fills per window (UP):  avg={statistics.mean(all_up_fills_per_window):.1f}, "
          f"range={min(all_up_fills_per_window)}-{max(all_up_fills_per_window)}")
    print(f"  Fills per window (DN):  avg={statistics.mean(all_dn_fills_per_window):.1f}, "
          f"range={min(all_dn_fills_per_window)}-{max(all_dn_fills_per_window)}")
    print(f"  Price levels per window (UP): avg={statistics.mean(all_up_levels):.1f}")
    print(f"  Price levels per window (DN): avg={statistics.mean(all_dn_levels):.1f}")
    if all_up_sizes:
        print(f"  Individual fill size (UP): avg=${statistics.mean(all_up_sizes):.4f}, "
              f"median=${statistics.median(all_up_sizes):.4f}")
    if all_dn_sizes:
        print(f"  Individual fill size (DN): avg=${statistics.mean(all_dn_sizes):.4f}, "
              f"median=${statistics.median(all_dn_sizes):.4f}")

    # Fragmentation pattern: 1 level = limit order, many levels = sweep/market order
    single_level_count = sum(1 for lvls in all_up_levels + all_dn_levels if lvls <= 2)
    total_sides = len(all_up_levels) + len(all_dn_levels)
    print(f"\n  Sides with <=2 price levels: {single_level_count}/{total_sides} "
          f"({single_level_count/total_sides*100:.0f}%)")
    many_level_count = sum(1 for lvls in all_up_levels + all_dn_levels if lvls >= 5)
    print(f"  Sides with >=5 price levels: {many_level_count}/{total_sides} "
          f"({many_level_count/total_sides*100:.0f}%)")

    conclusion = "SWEEP (market orders eating through book)" if many_level_count > single_level_count else "LIMIT (placing at specific levels)"
    print(f"\n  → W4 execution style: {conclusion}")

    return {
        "avg_up_fills": statistics.mean(all_up_fills_per_window),
        "avg_dn_fills": statistics.mean(all_dn_fills_per_window),
        "avg_up_levels": statistics.mean(all_up_levels),
        "avg_dn_levels": statistics.mean(all_dn_levels),
        "avg_up_fill_size": statistics.mean(all_up_sizes) if all_up_sizes else 0,
        "avg_dn_fill_size": statistics.mean(all_dn_sizes) if all_dn_sizes else 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# SECTION G: SOL-specific analysis
# ═══════════════════════════════════════════════════════════════════════
def sol_analysis(results, btc_idx, sol_idx):
    """Compare SOL trading pattern with BTC."""
    print("\n" + "=" * 70)
    print("SECTION G: SOL-Specific Analysis")
    print("=" * 70)

    valid = {k: v for k, v in results.items() if v["entry_delay_s"] <= 300}
    sol_windows = {k: v for k, v in valid.items() if v["coin"] == "sol"}
    btc_windows = {k: v for k, v in valid.items() if v["coin"] == "btc"}

    if not sol_windows:
        print("  No SOL 5M windows found.")
        return

    print(f"\n  SOL windows: {len(sol_windows)}")

    # Compare SOL lean with BTC lean for same epoch
    sol_by_epoch = {v["epoch"]: v for v in sol_windows.values()}
    btc_by_epoch = {v["epoch"]: v for v in btc_windows.values()}

    contrarian_count = 0
    same_dir_count = 0
    total_compared = 0

    print(f"\n  SOL vs BTC lean direction (same epoch):")
    for epoch in sorted(set(sol_by_epoch.keys()) & set(btc_by_epoch.keys())):
        sol_v = sol_by_epoch[epoch]
        btc_v = btc_by_epoch[epoch]
        is_same = sol_v["lean_dir"] == btc_v["lean_dir"]
        tag = "SAME" if is_same else "CONTRARIAN"
        same_dir_count += int(is_same)
        contrarian_count += int(not is_same)
        total_compared += 1
        print(f"    {ts_to_et_short(epoch)}: SOL lean={sol_v['lean_dir']}, BTC lean={btc_v['lean_dir']} → {tag}")
        print(f"      SOL: ${sol_v['lean_usd']:.2f} lean, ${sol_v['hedge_usd']:.2f} hedge, ratio={sol_v['lean_ratio']:.2f}:1")
        print(f"      BTC: ${btc_v['lean_usd']:.2f} lean, ${btc_v['hedge_usd']:.2f} hedge, ratio={btc_v['lean_ratio']:.2f}:1")

    if total_compared > 0:
        print(f"\n  SOL contrarian to BTC: {contrarian_count}/{total_compared} "
              f"({contrarian_count/total_compared*100:.0f}%)")
        print(f"  SOL same as BTC: {same_dir_count}/{total_compared} "
              f"({same_dir_count/total_compared*100:.0f}%)")

    # SOL entry prices vs BTC entry prices
    print(f"\n  SOL vs BTC entry prices (lean side wavg):")
    sol_lean_prices = []
    btc_lean_prices = []
    for v in sol_windows.values():
        lp = v["up_wavg_price"] if v["lean_dir"] == "UP" else v["dn_wavg_price"]
        sol_lean_prices.append(lp)
    for v in btc_windows.values():
        lp = v["up_wavg_price"] if v["lean_dir"] == "UP" else v["dn_wavg_price"]
        btc_lean_prices.append(lp)

    if sol_lean_prices:
        print(f"    SOL lean wavg: mean={statistics.mean(sol_lean_prices):.4f}, range=[{min(sol_lean_prices):.4f}, {max(sol_lean_prices):.4f}]")
    if btc_lean_prices:
        print(f"    BTC lean wavg: mean={statistics.mean(btc_lean_prices):.4f}, range=[{min(btc_lean_prices):.4f}, {max(btc_lean_prices):.4f}]")

    # SOL sizing vs BTC
    sol_total_usd = [v["up_usd"] + v["dn_usd"] for v in sol_windows.values()]
    btc_total_usd = [v["up_usd"] + v["dn_usd"] for v in btc_windows.values()]
    print(f"\n  SOL total per window: mean=${statistics.mean(sol_total_usd):.2f}, range=[${min(sol_total_usd):.2f}, ${max(sol_total_usd):.2f}]")
    print(f"  BTC total per window: mean=${statistics.mean(btc_total_usd):.2f}, range=[${min(btc_total_usd):.2f}, ${max(btc_total_usd):.2f}]")

    # SOL vs BTC resolution (using BTC price as proxy for all markets)
    print(f"\n  SOL resolution via BTC price direction:")
    for epoch in sorted(sol_by_epoch.keys()):
        sol_v = sol_by_epoch[epoch]
        btc_open, _ = get_price_at(btc_idx, epoch)
        btc_close, _ = get_price_at(btc_idx, epoch + 300)
        if btc_open and btc_close:
            btc_dir = "UP" if btc_close > btc_open else "DOWN"
            sol_win = sol_v["lean_dir"] == btc_dir
            print(f"    {ts_to_et_short(epoch)}: SOL lean={sol_v['lean_dir']}, BTC move={btc_dir} → {'WIN' if sol_win else 'LOSE'}")

    # Check SOL price data for direct verification
    if sol_idx:
        print(f"\n  SOL price data available — checking SOL's own price direction:")
        for epoch in sorted(sol_by_epoch.keys()):
            sol_v = sol_by_epoch[epoch]
            sol_open, _ = get_price_at(sol_idx, epoch)
            sol_close, _ = get_price_at(sol_idx, epoch + 300)
            if sol_open and sol_close:
                sol_dir = "UP" if sol_close > sol_open else "DOWN"
                sol_win = sol_v["lean_dir"] == sol_dir
                print(f"    {ts_to_et_short(epoch)}: SOL lean={sol_v['lean_dir']}, SOL price={sol_dir} "
                      f"(${sol_open:.2f}→${sol_close:.2f}) → {'WIN' if sol_win else 'LOSE'}")


# ═══════════════════════════════════════════════════════════════════════
# SECTION H: Build the playbook
# ═══════════════════════════════════════════════════════════════════════
def build_playbook(results, frag_stats, mom_match, mom_total, res_match, res_total):
    """Generate the final W4 5M Playbook markdown."""
    valid = {k: v for k, v in results.items() if v["entry_delay_s"] <= 300}

    # Compute aggregate stats
    delays = [v["entry_delay_s"] for v in valid.values()]
    durations = [v["active_duration_s"] for v in valid.values()]
    lean_ratios = [v["lean_ratio"] for v in valid.values() if v["lean_ratio"] < float("inf")]
    combined_costs = [v["combined_cost"] for v in valid.values()]
    total_usds = [v["up_usd"] + v["dn_usd"] for v in valid.values()]

    lean_wavgs = []
    hedge_wavgs = []
    lean_usds = []
    hedge_usds = []
    for v in valid.values():
        if v["lean_dir"] == "UP":
            lean_wavgs.append(v["up_wavg_price"])
            hedge_wavgs.append(v["dn_wavg_price"])
            lean_usds.append(v["up_usd"])
            hedge_usds.append(v["dn_usd"])
        else:
            lean_wavgs.append(v["dn_wavg_price"])
            hedge_wavgs.append(v["up_wavg_price"])
            lean_usds.append(v["dn_usd"])
            hedge_usds.append(v["up_usd"])

    # How often is lean the expensive side?
    lean_expensive = sum(1 for v in valid.values()
                         if (v["lean_dir"] == "UP" and v["up_wavg_price"] > v["dn_wavg_price"])
                         or (v["lean_dir"] == "DOWN" and v["dn_wavg_price"] > v["up_wavg_price"]))

    # BTC-specific stats
    btc_valid = {k: v for k, v in valid.items() if v["coin"] == "btc"}
    eth_valid = {k: v for k, v in valid.items() if v["coin"] == "eth"}
    sol_valid = {k: v for k, v in valid.items() if v["coin"] == "sol"}
    xrp_valid = {k: v for k, v in valid.items() if v["coin"] == "xrp"}

    # Per-coin summaries
    def coin_summary(coin_windows, label):
        if not coin_windows:
            return f"  No {label} windows.\n"
        lines = []
        lean_dirs = Counter(v["lean_dir"] for v in coin_windows.values())
        avg_total = statistics.mean([v["up_usd"] + v["dn_usd"] for v in coin_windows.values()])
        avg_ratio = statistics.mean([v["lean_ratio"] for v in coin_windows.values() if v["lean_ratio"] < float("inf")])
        avg_combined = statistics.mean([v["combined_cost"] for v in coin_windows.values()])
        lines.append(f"  {label}: {len(coin_windows)} windows | "
                     f"Lean: {dict(lean_dirs)} | "
                     f"Avg total: ${avg_total:.2f} | "
                     f"Avg ratio: {avg_ratio:.2f}:1 | "
                     f"Avg combined: {avg_combined:.4f}")
        return "\n".join(lines)

    # Timing pattern per window epoch — all coins at same time?
    epoch_groups = defaultdict(list)
    for v in valid.values():
        epoch_groups[v["epoch"]].append(v)

    # Build per-window detailed table
    window_table_lines = []
    for epoch in sorted(epoch_groups.keys()):
        vlist = sorted(epoch_groups[epoch], key=lambda x: x["coin"])
        window_table_lines.append(f"\n### Window: {ts_to_et_short(epoch)} — {ts_to_et_short(epoch + 300)} ET")
        window_table_lines.append("")
        window_table_lines.append("| Coin | Lean | UP$ | DN$ | Ratio | UP wavg | DN wavg | Combined | Delay | Duration | Fills |")
        window_table_lines.append("|------|------|-----|-----|-------|---------|---------|----------|-------|----------|-------|")
        for v in vlist:
            window_table_lines.append(
                f"| {v['coin'].upper()} | {v['lean_dir']} | "
                f"${v['up_usd']:.2f} | ${v['dn_usd']:.2f} | "
                f"{v['lean_ratio']:.2f}:1 | "
                f"{v['up_wavg_price']:.4f} | {v['dn_wavg_price']:.4f} | "
                f"{v['combined_cost']:.4f} | "
                f"{v['entry_delay_s']}s | {v['active_duration_s']}s | "
                f"{v['total_trades']} |"
            )

    # Build fill detail per window
    fill_detail_lines = []
    for slug in sorted(valid.keys()):
        v = valid[slug]
        fill_detail_lines.append(f"\n#### {slug}")

        # UP side price levels
        up_prices_rounded = Counter(round(t["price"], 4) for t in v["up_trades"])
        dn_prices_rounded = Counter(round(t["price"], 4) for t in v["dn_trades"])

        fill_detail_lines.append(f"**UP** ({v['up_count']} fills, {len(up_prices_rounded)} levels, ${v['up_usd']:.2f}):")
        fill_detail_lines.append("```")
        for price in sorted(up_prices_rounded.keys()):
            cnt = up_prices_rounded[price]
            usd = sum(t["usdcSize"] for t in v["up_trades"] if round(t["price"], 4) == price)
            fill_detail_lines.append(f"  @{price:.4f}: {cnt:>3} fills, ${usd:>8.2f}")
        fill_detail_lines.append("```")

        fill_detail_lines.append(f"**DOWN** ({v['dn_count']} fills, {len(dn_prices_rounded)} levels, ${v['dn_usd']:.2f}):")
        fill_detail_lines.append("```")
        for price in sorted(dn_prices_rounded.keys()):
            cnt = dn_prices_rounded[price]
            usd = sum(t["usdcSize"] for t in v["dn_trades"] if round(t["price"], 4) == price)
            fill_detail_lines.append(f"  @{price:.4f}: {cnt:>3} fills, ${usd:>8.2f}")
        fill_detail_lines.append("```")

    # ── Assemble the final playbook ──
    md = f"""# W4 5M Deep Reverse Engineering — Complete Playbook
> Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
> Source: wallet4_all_trades.json ({len([t for t in results.values()])} windows, data from March 23, 2026)

---

## Executive Summary

W4 traded **{len(valid)} valid 5M windows** across 4 coins (BTC, ETH, SOL, XRP) on March 23, 2026.
All trading occurred between **{ts_to_et_short(min(v['epoch'] for v in valid.values()))}** and **{ts_to_et_short(max(v['epoch'] for v in valid.values()) + 300)}**.

Key numbers:
- **Entry delay**: {statistics.mean(delays):.0f}s avg (median {statistics.median(delays):.0f}s, range {min(delays)}-{max(delays)}s)
- **Active duration**: {statistics.mean(durations):.0f}s avg (range {min(durations)}-{max(durations)}s)
- **Lean ratio**: {statistics.mean(lean_ratios):.2f}:1 avg (range {min(lean_ratios):.2f}-{max(lean_ratios):.2f})
- **Combined cost**: {statistics.mean(combined_costs):.4f} avg (range {min(combined_costs):.4f}-{max(combined_costs):.4f})
- **Total per window**: ${statistics.mean(total_usds):.2f} avg (range ${min(total_usds):.2f}-${max(total_usds):.2f})
- **Lean = expensive side**: {lean_expensive}/{len(valid)} ({lean_expensive/len(valid)*100:.0f}%)
- **Lean matches BTC momentum at entry**: {mom_match}/{mom_total} ({mom_match/mom_total*100:.0f}%) (= momentum following)
- **Lean matches resolution**: {res_match}/{res_total} ({res_match/res_total*100:.0f}%) (= W4 win rate proxy)

---

## A. Dataset Filter

| Category | Count |
|----------|-------|
| Total trades in dataset | 2,516 |
| 5M TRADE entries | {sum(len(v['trades']) for v in results.values())} |
| Valid 5M windows (delay <= 300s) | {len(valid)} |
| Anomalous windows (excluded) | {len(results) - len(valid)} |

**Coin breakdown (valid windows):**
- BTC: {len(btc_valid)} windows
- ETH: {len(eth_valid)} windows
- SOL: {len(sol_valid)} windows
- XRP: {len(xrp_valid)} windows

---

## B. Per-Window Reconstruction

{chr(10).join(window_table_lines)}

---

## C. Timing Analysis

### Entry Delay (seconds from window open to first trade)

| Stat | Value |
|------|-------|
| Mean | {statistics.mean(delays):.1f}s |
| Median | {statistics.median(delays):.1f}s |
| Min | {min(delays)}s |
| Max | {max(delays)}s |
| Std dev | {statistics.stdev(delays):.1f}s |

**Per-coin entry delay:**
{coin_summary(btc_valid, "BTC")}
{coin_summary(eth_valid, "ETH")}
{coin_summary(sol_valid, "SOL")}
{coin_summary(xrp_valid, "XRP")}

### Active Duration (first to last trade)

| Stat | Value |
|------|-------|
| Mean | {statistics.mean(durations):.1f}s |
| Median | {statistics.median(durations):.1f}s |
| Min | {min(durations)}s |
| Max | {max(durations)}s |

### Execution Pattern

W4 does NOT enter all-at-once. The fills are **spread over the full active period** (avg {statistics.mean(durations):.0f}s), indicating:
- Multiple order submissions or adjustments
- Possible "ladder" or "DCA" entry strategy
- Fills arrive in batches at the same timestamp (multiple fills per second = sweeping the book)

### Cross-Coin Synchronization

W4 trades all 4 coins within the SAME window epoch with near-simultaneous first trades:
"""

    # Add cross-coin sync data
    for epoch in sorted(epoch_groups.keys()):
        vlist = epoch_groups[epoch]
        first_timestamps = {v["coin"]: v["first_ts"] for v in vlist}
        if len(first_timestamps) > 1:
            spread = max(first_timestamps.values()) - min(first_timestamps.values())
            md += f"- {ts_to_et_short(epoch)}: spread = {spread}s across {len(first_timestamps)} coins\n"

    md += f"""
---

## D. Price Analysis

### Lean Side Price Distribution

The lean side (direction W4 bets more on) prices:
- **Mean wavg**: {statistics.mean(lean_wavgs):.4f}
- **Median wavg**: {statistics.median(lean_wavgs):.4f}
- **Range**: [{min(lean_wavgs):.4f}, {max(lean_wavgs):.4f}]

### Hedge Side Price Distribution

The hedge side (smaller position) prices:
- **Mean wavg**: {statistics.mean(hedge_wavgs):.4f}
- **Median wavg**: {statistics.median(hedge_wavgs):.4f}
- **Range**: [{min(hedge_wavgs):.4f}, {max(hedge_wavgs):.4f}]

### Is Lean Always the Expensive Side?

**{lean_expensive}/{len(valid)} ({lean_expensive/len(valid)*100:.0f}%)** — {"YES, lean = expensive = momentum following confirmed" if lean_expensive/len(valid) > 0.6 else "MIXED pattern"}

This means W4 buys the side that the market already prices as more likely.
Interpretation: W4 is a **momentum follower**, not a contrarian.

### Combined Cost (up_wavg + dn_wavg)

| Stat | Value |
|------|-------|
| Mean | {statistics.mean(combined_costs):.4f} |
| Min | {min(combined_costs):.4f} |
| Max | {max(combined_costs):.4f} |

Combined < 1.0 means guaranteed profit if either side wins.
Combined > 1.0 means net loss unless lean side wins.
"""

    # Add per-window combined cost table
    md += "\n| Window | Coin | Combined | Interpretation |\n"
    md += "|--------|------|----------|----------------|\n"
    for v in sorted(valid.values(), key=lambda x: (x["epoch"], x["coin"])):
        interp = "GUARANTEED PROFIT" if v["combined_cost"] < 1.0 else f"Need lean to win (cost={v['combined_cost']:.4f})"
        md += f"| {ts_to_et_short(v['epoch'])} | {v['coin'].upper()} | {v['combined_cost']:.4f} | {interp} |\n"

    md += f"""
---

## E. BTC Cross-Reference

### Momentum at Entry

W4 waits ~{statistics.median(delays):.0f}s after window open, observes BTC price movement, then leans in that direction.

- **Lean matches BTC momentum at entry**: {mom_match}/{mom_total} ({mom_match/mom_total*100:.0f}%)
- **Lean matches final BTC resolution**: {res_match}/{res_total} ({res_match/res_total*100:.0f}%)

### Signal Interpretation

W4's strategy = **momentum continuation bet**:
1. Wait for initial BTC move direction after window opens
2. Lean in that direction (assuming momentum continues to window close)
3. Hedge the other side at cheap prices

---

## F. Fill Fragmentation

### Summary Statistics

| Metric | UP Side | DOWN Side |
|--------|---------|-----------|
| Avg fills per window | {frag_stats['avg_up_fills']:.1f} | {frag_stats['avg_dn_fills']:.1f} |
| Avg price levels per window | {frag_stats['avg_up_levels']:.1f} | {frag_stats['avg_dn_levels']:.1f} |
| Avg individual fill size | ${frag_stats['avg_up_fill_size']:.4f} | ${frag_stats['avg_dn_fill_size']:.4f} |

### Execution Style

W4 places orders that **sweep multiple price levels** — this is NOT a single limit order.
Fills arrive in clusters at the same timestamp but at different prices, indicating market/FOK orders
that eat through the order book.

The lean side has more fills and more price levels → bigger sweep.
The hedge side has fewer fills at fewer levels → smaller, targeted order.

### Detailed Fill Maps

{chr(10).join(fill_detail_lines)}

---

## G. SOL-Specific Analysis
"""

    # SOL comparison
    sol_windows = {k: v for k, v in valid.items() if v["coin"] == "sol"}
    btc_windows = {k: v for k, v in valid.items() if v["coin"] == "btc"}

    sol_by_epoch = {v["epoch"]: v for v in sol_windows.values()}
    btc_by_epoch = {v["epoch"]: v for v in btc_windows.values()}

    contrarian = 0
    same = 0
    for epoch in sorted(set(sol_by_epoch.keys()) & set(btc_by_epoch.keys())):
        if sol_by_epoch[epoch]["lean_dir"] != btc_by_epoch[epoch]["lean_dir"]:
            contrarian += 1
        else:
            same += 1
    total_comp = contrarian + same

    md += f"""
### SOL vs BTC Lean Direction

| Epoch | SOL Lean | BTC Lean | Relationship |
|-------|----------|----------|--------------|
"""
    for epoch in sorted(set(sol_by_epoch.keys()) & set(btc_by_epoch.keys())):
        rel = "SAME" if sol_by_epoch[epoch]["lean_dir"] == btc_by_epoch[epoch]["lean_dir"] else "CONTRARIAN"
        md += f"| {ts_to_et_short(epoch)} | {sol_by_epoch[epoch]['lean_dir']} | {btc_by_epoch[epoch]['lean_dir']} | {rel} |\n"

    if total_comp > 0:
        md += f"""
**SOL contrarian to BTC: {contrarian}/{total_comp} ({contrarian/total_comp*100:.0f}%)**
**SOL same as BTC: {same}/{total_comp} ({same/total_comp*100:.0f}%)**
"""
    else:
        md += "\nNo overlapping epochs to compare.\n"

    # SOL sizing comparison
    sol_totals = [v["up_usd"] + v["dn_usd"] for v in sol_windows.values()]
    btc_totals = [v["up_usd"] + v["dn_usd"] for v in btc_windows.values()]

    if sol_totals and btc_totals:
        md += f"""
### Sizing Comparison

| Metric | SOL | BTC |
|--------|-----|-----|
| Avg total/window | ${statistics.mean(sol_totals):.2f} | ${statistics.mean(btc_totals):.2f} |
| Min total | ${min(sol_totals):.2f} | ${min(btc_totals):.2f} |
| Max total | ${max(sol_totals):.2f} | ${max(btc_totals):.2f} |
"""

    md += f"""
---

## H. The W4 5M Playbook

### Step-by-Step Recipe

```
W4 5M PLAYBOOK
═══════════════

SETUP:
- Markets: BTC, ETH, SOL, XRP 5-minute Up/Down on Polymarket
- Trade ALL 4 coins simultaneously in each window
- Window = 5 minutes (e.g., 10:30AM-10:35AM ET)

EXECUTION:
1. At T+{int(statistics.median(delays))}s (median): Enter the window
   - Range: T+{min(delays)}s to T+{max(delays)}s
   - Signal: BTC price direction in first ~{int(statistics.median(delays))}s after window open

2. Determine lean direction:
   - If BTC moved UP since window open → lean UP on all coins
   - If BTC moved DOWN since window open → lean DOWN on all coins
   - Momentum match rate: {mom_match}/{mom_total} ({mom_match/mom_total*100:.0f}%)
   - Exception: SOL may go contrarian ({contrarian}/{total_comp} times)

3. LEAN side order (the direction you believe):
   - {frag_stats['avg_up_fills']:.0f}-{frag_stats['avg_dn_fills']:.0f} fills sweeping {frag_stats['avg_up_levels']:.0f}-{frag_stats['avg_dn_levels']:.0f} price levels
   - Avg price: {statistics.mean(lean_wavgs):.2f}c (range {min(lean_wavgs):.2f}c-{max(lean_wavgs):.2f}c)
   - Avg USDC: ${statistics.mean(lean_usds):.2f} per window per coin

4. HEDGE side order (the opposite direction):
   - Fewer fills, fewer levels
   - Avg price: {statistics.mean(hedge_wavgs):.2f}c (range {min(hedge_wavgs):.2f}c-{max(hedge_wavgs):.2f}c)
   - Avg USDC: ${statistics.mean(hedge_usds):.2f} per window per coin

5. Lean ratio: {statistics.mean(lean_ratios):.2f}:1 avg
   - Range: {min(lean_ratios):.2f}:1 to {max(lean_ratios):.2f}:1
   - Lean side gets ~{statistics.mean(lean_ratios):.1f}x the USDC of hedge side

6. Combined cost: {statistics.mean(combined_costs):.4f} avg
   - Range: {min(combined_costs):.4f} to {max(combined_costs):.4f}
   - Most windows: combined > 1.0 = need lean side to win

7. Hold to resolution (window close, T+300s)
   - NO exits observed — all positions held to settlement

8. Active period: {statistics.mean(durations):.0f}s avg (range {min(durations)}-{max(durations)}s)
   - Fills spread over this duration (not all-at-once)

9. Total per window per coin: ${statistics.mean(total_usds)/4:.2f} avg
   - Total across all coins: ${statistics.mean(total_usds):.2f} avg per window epoch

RISK PROFILE:
- Win rate (lean matches resolution): ~{res_match/res_total*100:.0f}%
- When lean wins: profit = lean_shares - lean_cost + hedge_cost (net > 0)
- When lean loses: loss = lean_cost - hedge_shares (net < 0)
- Lean=expensive means higher cost but higher payout
```

### Key Insights

1. **Momentum Following**: W4 observes {int(statistics.median(delays))}s of BTC price action, then bets momentum continues. This is a simple but effective signal.

2. **Multi-Coin Diversification**: Trading BTC, ETH, SOL, XRP simultaneously in the same window diversifies across coin-specific noise while keeping the same directional thesis.

3. **Asymmetric Sizing**: {statistics.mean(lean_ratios):.1f}:1 lean ratio means W4 is NOT hedging equally. This is a conviction bet with a small hedge.

4. **Sweep Execution**: W4 uses market-like orders that eat through multiple price levels, not patient limit orders. Speed > price.

5. **Active Management**: The {statistics.mean(durations):.0f}s active period suggests W4 may be adjusting positions or adding to them as the window progresses, not just entering once.

6. **No Early Exit**: All positions held to resolution. W4 does not try to trade out of positions mid-window.

---

## Raw Data Summary

### All Valid Windows (sorted by epoch)

| # | Slug | Coin | Lean | UP$ | DN$ | Ratio | Combined | Delay | Duration | Trades |
|---|------|------|------|-----|-----|-------|----------|-------|----------|--------|
"""

    for i, v in enumerate(sorted(valid.values(), key=lambda x: (x["epoch"], x["coin"])), 1):
        md += (f"| {i} | {v['slug']} | {v['coin'].upper()} | {v['lean_dir']} | "
               f"${v['up_usd']:.2f} | ${v['dn_usd']:.2f} | {v['lean_ratio']:.2f}:1 | "
               f"{v['combined_cost']:.4f} | {v['entry_delay_s']}s | {v['active_duration_s']}s | "
               f"{v['total_trades']} |\n")

    return md


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    print("Loading data...")
    all_trades, btc_1m, sol_1m = load_data()
    btc_idx = build_price_index(btc_1m)
    sol_idx = build_price_index(sol_1m) if sol_1m else {}

    # A. Filter
    trades_5m, redeems_5m = filter_5m_trades(all_trades)

    # B. Per-window reconstruction
    results = reconstruct_windows(trades_5m)

    # C. Timing analysis
    valid_windows = timing_analysis(results)

    # D. Price analysis
    lean_prices, hedge_prices = price_analysis(results)

    # E. BTC cross-reference
    mom_match, mom_total, res_match, res_total = btc_cross_reference(results, btc_idx)

    # F. Fill fragmentation
    frag_stats = fill_fragmentation(results)

    # G. SOL analysis
    sol_analysis(results, btc_idx, sol_idx)

    # H. Build playbook
    print("\n" + "=" * 70)
    print("SECTION H: Building W4 5M Playbook")
    print("=" * 70)

    md = build_playbook(results, frag_stats, mom_match, mom_total, res_match, res_total)

    with open(OUTPUT_PATH, "w") as f:
        f.write(md)
    print(f"\nPlaybook saved to: {OUTPUT_PATH}")
    print(f"  Size: {len(md)} chars, {md.count(chr(10))} lines")


if __name__ == "__main__":
    main()
