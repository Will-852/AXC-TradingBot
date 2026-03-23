#!/usr/bin/env python3
"""
W4 OB Pricing Check — Does Polymarket still offer exploitable pricing after BTC moves?

Cross-references:
  - signal_tape.jsonl (BTC price + Poly mid prices, ~20s intervals)
  - poly_ob_tape.jsonl (individual token bid/ask data)
  - shadow_tape.jsonl (actual entry prices and outcomes)
  - btc_1m_7days.json (BTC 1M klines for open-price baseline)

Design decision: signal_tape is the primary source because it has BOTH BTC spot
prices and Poly mid prices in the same record. poly_ob_tape mostly has 0.01/0.99
placeholder spreads for far-future windows, so only 55/2856 BTC entries carry
real information.
"""

import json
import re
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

BASE = Path("/Users/wai/projects/axc-trading/polymarket")
LOGS = BASE / "logs"
ANALYSIS = BASE / "analysis"

# Eastern Time offset (EDT = UTC-4, March = DST)
ET_OFFSET = timedelta(hours=-4)

# Backtest WR lookup: (btc_move_bps_bucket, elapsed_bucket) -> WR
# From existing backtest data - we'll compute empirical WR from the data itself
# and also use the shadow_tape fair_up values as reference


# ─────────────────────────────────────────────────────────
# STEP 0: Load BTC 1M klines into a lookup
# ─────────────────────────────────────────────────────────
def load_btc_klines():
    """Load BTC 1M klines, return dict of unix_ts_seconds -> close price."""
    with open(ANALYSIS / "btc_1m_7days.json") as f:
        raw = json.load(f)
    # ts is in milliseconds
    klines = {}
    for k in raw:
        ts_s = k["ts"] / 1000
        klines[ts_s] = {
            "open": k["open"],
            "high": k["high"],
            "low": k["low"],
            "close": k["close"],
        }
    return klines


def get_btc_price_at(klines: dict, ts: float) -> float | None:
    """Get BTC close price at the 1M candle containing `ts`."""
    # Round down to nearest 60s
    candle_ts = (int(ts) // 60) * 60
    if candle_ts in klines:
        return klines[candle_ts]["close"]
    # Try +/- 60s
    for offset in [60, -60, 120, -120]:
        if candle_ts + offset in klines:
            return klines[candle_ts + offset]["close"]
    return None


# ─────────────────────────────────────────────────────────
# STEP 1: Parse signal_tape — extract window timing + BTC + Poly mids
# ─────────────────────────────────────────────────────────
def parse_title_to_window_start(title: str) -> float | None:
    """
    Parse 'Bitcoin Up or Down - March 20, 3:15AM-3:30AM ET'
    Return window_start as unix timestamp.
    """
    # Match: "March 20, 3:15AM-3:30AM ET" or "March 20, 10:00AM-10:15AM ET"
    m = re.search(
        r"(\w+)\s+(\d+),\s+(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)\s+ET",
        title,
    )
    if not m:
        # Try 1H format: "March 20, 2PM ET"
        m2 = re.search(r"(\w+)\s+(\d+),\s+(\d{1,2})(AM|PM)\s+ET", title)
        if not m2:
            return None
        month_str, day, hour, ampm = m2.groups()
        minute = 0
    else:
        month_str, day, hour, minute, ampm = (
            m.group(1),
            m.group(2),
            m.group(3),
            m.group(4),
            m.group(5),
        )

    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }
    month = month_map.get(month_str)
    if month is None:
        return None

    hour_int = int(hour)
    if ampm == "PM" and hour_int != 12:
        hour_int += 12
    elif ampm == "AM" and hour_int == 12:
        hour_int = 0

    # Build datetime in ET (EDT = UTC-4)
    et_tz = timezone(ET_OFFSET)
    try:
        dt = datetime(2026, month, int(day), hour_int, int(minute), tzinfo=et_tz)
    except ValueError:
        return None
    return dt.timestamp()


def load_signal_tape():
    """
    Parse signal_tape.jsonl.
    Return list of dicts with: snapshot_ts, btc_price, window_start, elapsed_s, up_mid, dn_mid
    """
    records = []
    with open(LOGS / "signal_tape.jsonl") as f:
        for line in f:
            d = json.loads(line)
            # Parse snapshot timestamp
            ts_str = d["ts"]
            snap_dt = datetime.fromisoformat(ts_str)
            snap_ts = snap_dt.timestamp()

            btc_price = d.get("btc", {}).get("median")
            if btc_price is None:
                continue  # Skip entries with missing BTC price

            # Process each BTC poly entry
            for p in d["poly"]:
                if p["coin"] != "BTC":
                    continue
                # Skip entries with null mid prices
                if p.get("up_mid") is None or p.get("dn_mid") is None:
                    continue
                # Only 15M windows (title has "HH:MMAM-HH:MMAM" pattern)
                if not re.search(
                    r"\d{1,2}:\d{2}[AP]M-\d{1,2}:\d{2}[AP]M", p.get("title", "")
                ):
                    continue  # Skip 1H and unknown formats

                window_start = parse_title_to_window_start(p["title"])
                if window_start is None:
                    continue

                elapsed = snap_ts - window_start
                # Only care about snapshots during the window (0 to 900s)
                if elapsed < -10 or elapsed > 910:
                    continue

                records.append({
                    "snap_ts": snap_ts,
                    "btc_price": btc_price,
                    "window_start": window_start,
                    "elapsed_s": elapsed,
                    "up_mid": p["up_mid"],
                    "dn_mid": p["dn_mid"],
                    "title": p["title"],
                    "ob_bid_vol": p.get("ob_bid_vol", 0),
                    "ob_ask_vol": p.get("ob_ask_vol", 0),
                })

    return records


# ─────────────────────────────────────────────────────────
# STEP 2: Cross-reference with BTC open price
# ─────────────────────────────────────────────────────────
def enrich_with_btc_return(records: list, klines: dict):
    """Add btc_open_price, btc_return_bps to each record."""
    enriched = []
    for r in records:
        # Get BTC price at window open
        btc_open = get_btc_price_at(klines, r["window_start"])
        if btc_open is None:
            continue

        btc_return_bps = (r["btc_price"] - btc_open) / btc_open * 10000
        r["btc_open"] = btc_open
        r["btc_return_bps"] = btc_return_bps
        r["btc_direction"] = "UP" if btc_return_bps > 0 else "DOWN"
        enriched.append(r)

    return enriched


# ─────────────────────────────────────────────────────────
# STEP 3: Build the pricing matrix
# ─────────────────────────────────────────────────────────
def bucket_elapsed(elapsed_s: float) -> str:
    """Bucket elapsed time into meaningful intervals."""
    if elapsed_s < 30:
        return "0-30s"
    elif elapsed_s < 60:
        return "30-60s"
    elif elapsed_s < 120:
        return "60-120s"
    elif elapsed_s < 180:
        return "120-180s"
    elif elapsed_s < 300:
        return "180-300s"
    elif elapsed_s < 450:
        return "300-450s"
    elif elapsed_s < 600:
        return "450-600s"
    elif elapsed_s < 750:
        return "600-750s"
    else:
        return "750-900s"


def bucket_btc_move(bps: float) -> str:
    """Bucket BTC move magnitude."""
    abs_bps = abs(bps)
    if abs_bps < 2:
        return "0-2bps"
    elif abs_bps < 5:
        return "2-5bps"
    elif abs_bps < 10:
        return "5-10bps"
    elif abs_bps < 20:
        return "10-20bps"
    elif abs_bps < 30:
        return "20-30bps"
    else:
        return "30+bps"


def build_pricing_matrix(records: list):
    """
    Group by (elapsed_bucket, btc_move_bucket).
    For each group, compute:
    - Mean UP mid price (when BTC is UP)
    - Count of observations
    """
    # We look at it from the MOMENTUM side:
    # If BTC is UP, the UP token should be priced higher
    # The "momentum_mid" is the mid price of the token aligned with BTC direction
    groups = defaultdict(list)

    for r in records:
        elapsed_bkt = bucket_elapsed(r["elapsed_s"])
        move_bkt = bucket_btc_move(r["btc_return_bps"])

        # Momentum-aligned mid: if BTC UP, use up_mid; if BTC DOWN, use dn_mid
        if r["btc_direction"] == "UP":
            momentum_mid = r["up_mid"]
        else:
            momentum_mid = r["dn_mid"]

        groups[(elapsed_bkt, move_bkt)].append({
            "momentum_mid": momentum_mid,
            "up_mid": r["up_mid"],
            "btc_return_bps": r["btc_return_bps"],
            "elapsed_s": r["elapsed_s"],
        })

    return groups


# ─────────────────────────────────────────────────────────
# STEP 4: Load shadow_tape for actual entry analysis
# ─────────────────────────────────────────────────────────
def load_shadow_tape():
    """Load shadow_tape entries with actual trades."""
    entries = []
    with open(LOGS / "shadow_tape.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d["coin"] != "BTC" or d.get("entry") is None:
                continue
            if d.get("tf") != "15M":
                continue
            entries.append(d)
    return entries


# ─────────────────────────────────────────────────────────
# STEP 5: Load poly_ob_tape for spread analysis
# ─────────────────────────────────────────────────────────
def load_ob_tape_btc():
    """Load poly_ob_tape BTC entries for the current window."""
    records = []
    with open(LOGS / "poly_ob_tape.jsonl") as f:
        for line in f:
            d = json.loads(line)
            if d["coin"] != "BTC":
                continue

            # Parse slug: "btc-updown-15m-1774115100" format
            # Skip non-standard slugs (e.g., 1H "bitcoin-up-or-down-march-21-...")
            slug_parts = d["slug"].split("-")
            try:
                slug_ts = int(slug_parts[-1])
            except ValueError:
                continue  # Skip 1H or non-standard format slugs
            elapsed = d["ts"] - slug_ts

            # Only entries within the active window
            if elapsed < -10 or elapsed > 910:
                continue

            combined_ask = d["up_best_ask"] + d["down_best_ask"]
            combined_bid = d["up_best_bid"] + d["down_best_bid"]
            mid_up = (d["up_best_bid"] + d["up_best_ask"]) / 2

            records.append({
                "ts": d["ts"],
                "slug": d["slug"],
                "window_start": slug_ts,
                "elapsed_s": elapsed,
                "up_best_bid": d["up_best_bid"],
                "up_best_ask": d["up_best_ask"],
                "down_best_bid": d["down_best_bid"],
                "down_best_ask": d["down_best_ask"],
                "combined_ask": combined_ask,
                "combined_bid": combined_bid,
                "mid_up": mid_up,
                "up_bid_depth": d["up_bid_depth_10"],
                "up_ask_depth": d["up_ask_depth_10"],
                "spread_up": d["up_best_ask"] - d["up_best_bid"],
                "spread_dn": d["down_best_ask"] - d["down_best_bid"],
                "time_to_end": d["time_to_end_s"],
            })

    return records


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    log.info("Loading BTC 1M klines...")
    klines = load_btc_klines()
    log.info(f"Loaded {len(klines)} klines")

    # ── PART A: Signal tape analysis ──
    log.info("Loading signal_tape...")
    records = load_signal_tape()
    log.info(f"Parsed {len(records)} BTC 15M signal_tape snapshots")

    log.info("Enriching with BTC returns...")
    records = enrich_with_btc_return(records, klines)
    log.info(f"Enriched {len(records)} records (with kline match)")

    # Unique windows
    windows = set(r["window_start"] for r in records)
    log.info(f"Covering {len(windows)} unique 15M windows")

    # ── Build pricing matrix ──
    groups = build_pricing_matrix(records)

    elapsed_order = [
        "0-30s", "30-60s", "60-120s", "120-180s", "180-300s",
        "300-450s", "450-600s", "600-750s", "750-900s",
    ]
    move_order = ["0-2bps", "2-5bps", "5-10bps", "10-20bps", "20-30bps", "30+bps"]

    print("\n" + "=" * 120)
    print("PART A: POLYMARKET MOMENTUM-ALIGNED MID PRICE vs BTC MOVE + ELAPSED TIME")
    print("(momentum_mid = UP mid when BTC is UP, DOWN mid when BTC is DOWN)")
    print("=" * 120)

    # Print header
    header = f"{'Elapsed':<12}"
    for m in move_order:
        header += f"{'|':>2} {m:>18}"
    print(header)
    print("-" * 120)

    for e_bkt in elapsed_order:
        row = f"{e_bkt:<12}"
        for m_bkt in move_order:
            data = groups.get((e_bkt, m_bkt), [])
            if len(data) < 3:
                row += f"{'|':>2} {'n/a':>18}"
            else:
                mids = [d["momentum_mid"] for d in data]
                avg_mid = sum(mids) / len(mids)
                row += f"{'|':>2} {avg_mid:>6.3f} (n={len(data):>4})"
            pass
        print(row)

    # ── Detailed view for the KEY scenario: elapsed > 60s, BTC move > 5bps ──
    print("\n" + "=" * 120)
    print("PART B: DETAILED — SNAPSHOTS WHERE BTC MOVED >5bps, ELAPSED 60-300s")
    print("This is the ENTRY ZONE for conviction trading")
    print("=" * 120)

    entry_zone = [
        r for r in records
        if abs(r["btc_return_bps"]) > 5 and 60 <= r["elapsed_s"] <= 300
    ]
    log.info(f"Entry zone snapshots (>5bps, 60-300s): {len(entry_zone)}")

    if entry_zone:
        # Group by move bucket
        by_move = defaultdict(list)
        for r in entry_zone:
            bkt = bucket_btc_move(r["btc_return_bps"])
            if r["btc_direction"] == "UP":
                by_move[bkt].append(r["up_mid"])
            else:
                by_move[bkt].append(r["dn_mid"])

        print(f"\n{'BTC Move':<12} {'Avg Momentum Mid':>18} {'Min':>8} {'Max':>8} {'Median':>8} {'N':>6}")
        print("-" * 65)
        for m_bkt in move_order:
            data = by_move.get(m_bkt, [])
            if not data:
                continue
            data.sort()
            avg = sum(data) / len(data)
            median = data[len(data) // 2]
            print(f"{m_bkt:<12} {avg:>18.3f} {min(data):>8.3f} {max(data):>8.3f} {median:>8.3f} {len(data):>6}")

    # ── EDGE QUANTIFICATION TABLE ──
    print("\n" + "=" * 120)
    print("PART C: EDGE QUANTIFICATION — DISCOUNT vs BACKTEST FAIR VALUE")
    print("Backtest WR for >5bps BTC move after 60-300s ≈ empirical from shadow_tape")
    print("=" * 120)

    # We use the shadow_tape fair_up values as our best estimate of WR
    shadow = load_shadow_tape()
    log.info(f"Shadow tape BTC 15M entries: {len(shadow)}")

    # Shadow tape stats
    if shadow:
        won = sum(1 for e in shadow if e["entry"]["won"])
        total_pnl = sum(e["entry"]["pnl"] for e in shadow)
        avg_entry = sum(e["entry"]["entry_price"] for e in shadow) / len(shadow)
        avg_fair = sum(
            e["entry"]["fair_up"] if e["entry"]["direction"] == "UP"
            else (1 - e["entry"]["fair_up"])
            for e in shadow
        ) / len(shadow)

        print(f"\nShadow Tape Summary (BTC 15M):")
        print(f"  Entries: {len(shadow)}")
        print(f"  Win Rate: {won}/{len(shadow)} = {won/len(shadow)*100:.1f}%")
        print(f"  Avg Entry Price: {avg_entry:.3f}")
        print(f"  Avg Fair Value (momentum-aligned): {avg_fair:.3f}")
        print(f"  Avg Discount (fair - entry): {avg_fair - avg_entry:.3f}")
        print(f"  Total PnL: {total_pnl:.2f}")
        print(f"  Avg PnL/trade: {total_pnl/len(shadow):.4f}")

    # ── EV calculation at different Poly mid prices ──
    # Using backtest WR from shadow_tape fair values
    print("\n" + "-" * 80)
    print("EV PER SHARE AT DIFFERENT POLY MID PRICES")
    print("(Assumes WR from backtest = fair_up for the momentum direction)")
    print("-" * 80)

    # Group shadow entries by t_min (≈ elapsed / 60)
    t_min_groups = defaultdict(list)
    for e in shadow:
        t_min = e["entry"]["t_min"]
        t_min_groups[round(t_min)].append(e)

    print(f"\n{'t_min':<8} {'N':>5} {'WR%':>7} {'AvgEntry':>10} {'AvgFair':>10} {'Discount':>10} {'EV/share':>10}")
    print("-" * 65)
    for t in sorted(t_min_groups.keys()):
        grp = t_min_groups[t]
        n = len(grp)
        if n < 5:
            continue
        wr = sum(1 for e in grp if e["entry"]["won"]) / n
        avg_entry_p = sum(e["entry"]["entry_price"] for e in grp) / n
        avg_fair_p = sum(
            e["entry"]["fair_up"] if e["entry"]["direction"] == "UP"
            else (1 - e["entry"]["fair_up"])
            for e in grp
        ) / n
        discount = avg_fair_p - avg_entry_p
        # EV = WR * (1 - entry) - (1-WR) * entry
        ev = wr * (1 - avg_entry_p) - (1 - wr) * avg_entry_p
        print(f"{t:<8} {n:>5} {wr*100:>6.1f}% {avg_entry_p:>10.3f} {avg_fair_p:>10.3f} {discount:>10.3f} {ev:>10.4f}")

    # ── THE KEY QUESTION: What is the Poly mid at different moments? ──
    print("\n" + "=" * 120)
    print("PART D: POLY MID PRICE EVOLUTION AFTER BTC MOVE (TIME SERIES)")
    print("Tracks how Poly mid responds to BTC move over the 15M window")
    print("=" * 120)

    # For each window, find the BTC return trajectory and Poly mid trajectory
    window_data = defaultdict(list)
    for r in records:
        window_data[r["window_start"]].append(r)

    # Only windows where BTC eventually moves >5bps
    print(f"\n{'Elapsed':<12} {'BTC >5bps':>12} {'BTC >10bps':>12} {'BTC >20bps':>12}")
    print(f"{'':12} {'Poly Mid':>12} {'Poly Mid':>12} {'Poly Mid':>12}")
    print("-" * 55)

    for e_bkt in elapsed_order:
        row_5bps = []
        row_10bps = []
        row_20bps = []
        for r in records:
            if bucket_elapsed(r["elapsed_s"]) != e_bkt:
                continue
            abs_bps = abs(r["btc_return_bps"])
            momentum_mid = r["up_mid"] if r["btc_direction"] == "UP" else r["dn_mid"]

            if abs_bps > 5:
                row_5bps.append(momentum_mid)
            if abs_bps > 10:
                row_10bps.append(momentum_mid)
            if abs_bps > 20:
                row_20bps.append(momentum_mid)

        def fmt(data):
            if len(data) < 3:
                return f"{'n/a':>12}"
            avg = sum(data) / len(data)
            return f"{avg:>6.3f} n={len(data):>3}"

        print(f"{e_bkt:<12} {fmt(row_5bps):>12} {fmt(row_10bps):>12} {fmt(row_20bps):>12}")

    # ── PART E: Per-window tracking ──
    # Track individual windows where BTC makes a strong move early
    print("\n" + "=" * 120)
    print("PART E: INDIVIDUAL WINDOW DEEP-DIVE — BTC MOVED >10bps WITHIN 120s")
    print("Shows exact Poly mid at each timestamp for real case studies")
    print("=" * 120)

    # Find windows where BTC moved >10bps within first 120s
    strong_windows = []
    for ws, snaps in window_data.items():
        # Sort by elapsed
        snaps.sort(key=lambda x: x["elapsed_s"])
        for s in snaps:
            if 90 <= s["elapsed_s"] <= 150 and abs(s["btc_return_bps"]) > 10:
                strong_windows.append(ws)
                break

    strong_windows = list(set(strong_windows))
    strong_windows.sort()
    log.info(f"Found {len(strong_windows)} windows with BTC >10bps within 120s")

    for ws in strong_windows[:15]:  # Show up to 15
        snaps = sorted(window_data[ws], key=lambda x: x["elapsed_s"])
        # Determine dominant direction
        late_snaps = [s for s in snaps if s["elapsed_s"] > 60]
        if not late_snaps:
            continue
        btc_dirs = [s["btc_direction"] for s in late_snaps]
        dominant = max(set(btc_dirs), key=btc_dirs.count)

        title = snaps[0]["title"]
        print(f"\n  {title}")
        print(f"  BTC open: {snaps[0]['btc_open']:.2f}, Dominant direction: {dominant}")
        print(f"  {'Elapsed':>8} {'BTC Price':>11} {'BTC Ret bps':>12} {'UP Mid':>8} {'DN Mid':>8} {'Momentum Mid':>13}")
        print(f"  {'-'*65}")

        for s in snaps:
            if s["elapsed_s"] < 0:
                continue
            momentum_mid = s["up_mid"] if s["btc_direction"] == "UP" else s["dn_mid"]
            flag = " <<<" if 60 <= s["elapsed_s"] <= 300 and abs(s["btc_return_bps"]) > 5 else ""
            print(
                f"  {s['elapsed_s']:>7.0f}s {s['btc_price']:>11.2f} "
                f"{s['btc_return_bps']:>+11.1f} {s['up_mid']:>8.3f} "
                f"{s['dn_mid']:>8.3f} {momentum_mid:>13.3f}{flag}"
            )

    # ── PART F: OB Tape spread analysis ──
    print("\n" + "=" * 120)
    print("PART F: ORDER BOOK SPREAD ANALYSIS (poly_ob_tape)")
    print("Bid-ask spreads and depths for BTC tokens in active windows")
    print("=" * 120)

    ob_records = load_ob_tape_btc()
    log.info(f"OB tape BTC in-window entries: {len(ob_records)}")

    # Filter to entries with real spreads
    real_ob = [r for r in ob_records if r["up_best_bid"] > 0.05 or r["up_best_ask"] < 0.95]
    log.info(f"OB entries with real spreads: {len(real_ob)}")

    if real_ob:
        by_elapsed = defaultdict(list)
        for r in real_ob:
            bkt = bucket_elapsed(r["elapsed_s"])
            by_elapsed[bkt].append(r)

        print(f"\n{'Elapsed':<12} {'N':>5} {'Avg UP Spread':>14} {'Avg DN Spread':>14} {'Combined Ask':>14} {'Avg UP Bid Depth':>17}")
        print("-" * 80)
        for e_bkt in elapsed_order:
            data = by_elapsed.get(e_bkt, [])
            if not data:
                continue
            avg_up_spread = sum(d["spread_up"] for d in data) / len(data)
            avg_dn_spread = sum(d["spread_dn"] for d in data) / len(data)
            avg_combined = sum(d["combined_ask"] for d in data) / len(data)
            avg_depth = sum(d["up_bid_depth"] for d in data) / len(data)
            print(
                f"{e_bkt:<12} {len(data):>5} {avg_up_spread:>14.3f} "
                f"{avg_dn_spread:>14.3f} {avg_combined:>14.3f} {avg_depth:>17.0f}"
            )

    # ── PART G: Shadow tape entry price analysis ──
    print("\n" + "=" * 120)
    print("PART G: SHADOW TAPE — ACTUAL ENTRY PRICES vs FAIR VALUES")
    print("=" * 120)

    if shadow:
        # Distribution of entry prices
        entry_prices = [e["entry"]["entry_price"] for e in shadow]
        fair_values = [
            e["entry"]["fair_up"] if e["entry"]["direction"] == "UP"
            else (1 - e["entry"]["fair_up"])
            for e in shadow
        ]
        discounts = [f - p for f, p in zip(fair_values, entry_prices)]

        print(f"\n  Entry Price Distribution:")
        print(f"    Min: {min(entry_prices):.3f}")
        print(f"    Max: {max(entry_prices):.3f}")
        print(f"    Mean: {sum(entry_prices)/len(entry_prices):.3f}")
        print(f"    Median: {sorted(entry_prices)[len(entry_prices)//2]:.3f}")

        print(f"\n  Fair Value Distribution:")
        print(f"    Min: {min(fair_values):.3f}")
        print(f"    Max: {max(fair_values):.3f}")
        print(f"    Mean: {sum(fair_values)/len(fair_values):.3f}")

        print(f"\n  Discount (Fair - Entry):")
        print(f"    Min: {min(discounts):.3f}")
        print(f"    Max: {max(discounts):.3f}")
        print(f"    Mean: {sum(discounts)/len(discounts):.3f}")
        print(f"    % of entries with discount >0.10: {sum(1 for d in discounts if d>0.10)/len(discounts)*100:.1f}%")
        print(f"    % of entries with discount >0.30: {sum(1 for d in discounts if d>0.30)/len(discounts)*100:.1f}%")
        print(f"    % of entries with discount >0.50: {sum(1 for d in discounts if d>0.50)/len(discounts)*100:.1f}%")

        # By conviction level
        print(f"\n  By Conviction Level:")
        conv_groups = defaultdict(list)
        for e in shadow:
            conv = e["entry"]["conviction"]
            if conv < 0.3:
                conv_groups["<0.30"].append(e)
            elif conv < 0.4:
                conv_groups["0.30-0.40"].append(e)
            elif conv < 0.5:
                conv_groups["0.40-0.50"].append(e)
            else:
                conv_groups["0.50+"].append(e)

        print(f"    {'Conviction':<14} {'N':>5} {'WR%':>7} {'AvgEntry':>10} {'AvgFair':>10} {'AvgDiscount':>12} {'TotalPnL':>10}")
        print(f"    {'-'*70}")
        for c_bkt in ["<0.30", "0.30-0.40", "0.40-0.50", "0.50+"]:
            grp = conv_groups.get(c_bkt, [])
            if not grp:
                continue
            wr = sum(1 for e in grp if e["entry"]["won"]) / len(grp)
            avg_e = sum(e["entry"]["entry_price"] for e in grp) / len(grp)
            avg_f = sum(
                e["entry"]["fair_up"] if e["entry"]["direction"] == "UP"
                else (1 - e["entry"]["fair_up"])
                for e in grp
            ) / len(grp)
            total_p = sum(e["entry"]["pnl"] for e in grp)
            print(
                f"    {c_bkt:<14} {len(grp):>5} {wr*100:>6.1f}% "
                f"{avg_e:>10.3f} {avg_f:>10.3f} {avg_f-avg_e:>12.3f} {total_p:>10.2f}"
            )

    # ── VERDICT ──
    print("\n" + "=" * 120)
    print("VERDICT: IS THERE EXPLOITABLE PRICING?")
    print("=" * 120)

    # Compute the key metric: when BTC moves >10bps at T+120s, what is Poly mid?
    key_snaps = [
        r for r in records
        if 90 <= r["elapsed_s"] <= 150 and abs(r["btc_return_bps"]) > 10
    ]
    if key_snaps:
        momentum_mids = [
            r["up_mid"] if r["btc_direction"] == "UP" else r["dn_mid"]
            for r in key_snaps
        ]
        avg_key_mid = sum(momentum_mids) / len(momentum_mids)
        min_key_mid = min(momentum_mids)
        max_key_mid = max(momentum_mids)
        median_key_mid = sorted(momentum_mids)[len(momentum_mids) // 2]

        # Distribution buckets
        below_60 = sum(1 for m in momentum_mids if m < 0.60)
        b60_70 = sum(1 for m in momentum_mids if 0.60 <= m < 0.70)
        b70_80 = sum(1 for m in momentum_mids if 0.70 <= m < 0.80)
        b80_90 = sum(1 for m in momentum_mids if 0.80 <= m < 0.90)
        above_90 = sum(1 for m in momentum_mids if m >= 0.90)

        print(f"\n  KEY SCENARIO: BTC moved >10bps, elapsed 90-150s (around T+120s)")
        print(f"  Observations: {len(key_snaps)}")
        print(f"  Momentum-aligned Poly mid:")
        print(f"    Mean:   {avg_key_mid:.3f}")
        print(f"    Median: {median_key_mid:.3f}")
        print(f"    Min:    {min_key_mid:.3f}")
        print(f"    Max:    {max_key_mid:.3f}")
        print(f"  Distribution:")
        print(f"    <0.60: {below_60:>4} ({below_60/len(key_snaps)*100:>5.1f}%)")
        print(f"    0.60-0.70: {b60_70:>4} ({b60_70/len(key_snaps)*100:>5.1f}%)")
        print(f"    0.70-0.80: {b70_80:>4} ({b70_80/len(key_snaps)*100:>5.1f}%)")
        print(f"    0.80-0.90: {b80_90:>4} ({b80_90/len(key_snaps)*100:>5.1f}%)")
        print(f"    >=0.90: {above_90:>4} ({above_90/len(key_snaps)*100:>5.1f}%)")

        # If our backtest says WR=89.1% at this point
        backtest_wr = 0.891
        print(f"\n  EDGE CALCULATION (assuming backtest WR = {backtest_wr*100:.1f}%):")
        print(f"    Fair value = {backtest_wr:.3f}")
        print(f"    Avg Poly mid = {avg_key_mid:.3f}")
        print(f"    Discount = {backtest_wr - avg_key_mid:.3f}")
        if avg_key_mid < backtest_wr:
            ev = backtest_wr * (1 - avg_key_mid) - (1 - backtest_wr) * avg_key_mid
            print(f"    EV per share = {ev:.4f} (= {ev*100:.2f} cents)")
            print(f"    → YES, exploitable pricing exists")
        else:
            print(f"    → NO edge: market has priced in the move")

        # Also check what happens with TAKER pricing (best ask instead of mid)
        # For taker, we'd pay up_best_ask (higher than mid)
        # We can estimate taker ask ≈ mid + half_spread
        # From signal_tape we only have mid, but from OB tape we saw real spreads
        print(f"\n  TAKER vs MAKER:")
        print(f"    If entering as MAKER at mid = {avg_key_mid:.3f}:")
        ev_maker = backtest_wr * (1 - avg_key_mid) - (1 - backtest_wr) * avg_key_mid
        print(f"      EV = {ev_maker:.4f}")
        # Typical spread from real OB data
        print(f"    If entering as TAKER (assume 2-5c worse):")
        for taker_penalty in [0.02, 0.03, 0.05]:
            taker_price = avg_key_mid + taker_penalty
            ev_taker = backtest_wr * (1 - taker_price) - (1 - backtest_wr) * taker_price
            print(f"      At mid+{taker_penalty:.0%}: price={taker_price:.3f}, EV={ev_taker:.4f}")
    else:
        print("\n  NO DATA: No snapshots found with BTC >10bps at T+120s")
        print("  This could mean:")
        print("  1. BTC doesn't move that fast in 120s (check kline data)")
        print("  2. Signal tape doesn't capture the current window well")

    # ── Also check wider entry windows ──
    for label, min_e, max_e, min_bps in [
        ("T+60-120s, >5bps", 60, 120, 5),
        ("T+60-120s, >10bps", 60, 120, 10),
        ("T+120-300s, >5bps", 120, 300, 5),
        ("T+120-300s, >10bps", 120, 300, 10),
        ("T+120-300s, >20bps", 120, 300, 20),
    ]:
        snaps = [
            r for r in records
            if min_e <= r["elapsed_s"] <= max_e and abs(r["btc_return_bps"]) > min_bps
        ]
        if len(snaps) < 3:
            continue
        mids = [
            r["up_mid"] if r["btc_direction"] == "UP" else r["dn_mid"]
            for r in snaps
        ]
        avg = sum(mids) / len(mids)
        median = sorted(mids)[len(mids) // 2]
        ev = 0.891 * (1 - avg) - 0.109 * avg
        print(f"\n  {label}: n={len(snaps)}, avg_mid={avg:.3f}, median={median:.3f}, EV={ev:.4f}")

    print()


if __name__ == "__main__":
    main()
