#!/usr/bin/env python3
"""
blankandyellow Deep Timing & Behavior Analysis
從 3500 trades 逆向工程：
  - 每秒/每分鐘操作量
  - 下單間隔（inter-trade gap）
  - 每個 market 內嘅操作時序
  - UP/DOWN 下單順序
  - Price 變化 pattern
  - Transaction hash 分析（同一 tx = batch order）
"""

import json
import os
from collections import defaultdict, Counter
from datetime import datetime, timezone
from statistics import median, mean, stdev, quantiles
import re

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "blankandyellow_trades.json")


def load_trades() -> list[dict]:
    with open(DATA_PATH) as f:
        trades = json.load(f)
    # Sort by timestamp
    trades.sort(key=lambda t: (t["timestamp"], t.get("outcome", "")))
    return trades


def extract_window_ts(slug: str) -> int | None:
    m = re.search(r'-(\d{10})$', slug or "")
    return int(m.group(1)) if m else None


def section(title: str):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")


def histogram(values: list[float], bins: list[tuple[str, float, float]], total: int = 0):
    total = total or len(values)
    for label, lo, hi in bins:
        c = sum(1 for v in values if lo <= v < hi)
        bar = "█" * int(c / max(total, 1) * 50)
        print(f"    {label:>14}: {c:>5} ({c/max(total,1)*100:5.1f}%) {bar}")


def main():
    trades = load_trades()
    timestamps = [t["timestamp"] for t in trades]
    ts_min, ts_max = min(timestamps), max(timestamps)
    span_sec = ts_max - ts_min
    span_min = span_sec / 60
    span_hr = span_sec / 3600

    print(f"{'═'*70}")
    print(f"  blankandyellow — Deep Timing Analysis")
    print(f"  Trades: {len(trades)}  |  Span: {span_hr:.2f}h ({span_min:.0f} min)")
    print(f"  From: {datetime.fromtimestamp(ts_min, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  To:   {datetime.fromtimestamp(ts_max, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'═'*70}")

    # ═══════════════════════════════════════════════════════════════════
    section("1. GLOBAL THROUGHPUT")
    # ═══════════════════════════════════════════════════════════════════

    print(f"  Trades/hour:   {len(trades) / max(span_hr, 0.01):.1f}")
    print(f"  Trades/minute: {len(trades) / max(span_min, 0.01):.2f}")
    print(f"  Trades/second: {len(trades) / max(span_sec, 1):.3f}")
    print(f"  Avg gap:       {span_sec / max(len(trades)-1, 1):.2f}s between trades")

    # Per-second count
    sec_counts = Counter(ts for ts in timestamps)
    max_per_sec = max(sec_counts.values())
    secs_with_trades = len(sec_counts)
    print(f"\n  Seconds with ≥1 trade: {secs_with_trades} / {span_sec} ({secs_with_trades/max(span_sec,1)*100:.1f}%)")
    print(f"  Max trades in 1 second: {max_per_sec}")
    sec_dist = Counter(sec_counts.values())
    print(f"  Trades-per-second distribution:")
    for n in sorted(sec_dist.keys()):
        bar = "█" * int(sec_dist[n] / max(secs_with_trades, 1) * 40)
        print(f"    {n} trades: {sec_dist[n]:>5} seconds ({sec_dist[n]/secs_with_trades*100:5.1f}%) {bar}")

    # Per-minute count
    min_counts = Counter(ts // 60 for ts in timestamps)
    mins_with_trades = len(min_counts)
    min_values = list(min_counts.values())
    print(f"\n  Minutes with ≥1 trade: {mins_with_trades}")
    print(f"  Trades/min: mean={mean(min_values):.1f}  median={median(min_values):.1f}  max={max(min_values)}")

    # ═══════════════════════════════════════════════════════════════════
    section("2. INTER-TRADE GAPS (all trades, chronological)")
    # ═══════════════════════════════════════════════════════════════════

    gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    zero_gaps = sum(1 for g in gaps if g == 0)
    nonzero_gaps = [g for g in gaps if g > 0]

    print(f"  Total gaps: {len(gaps)}")
    print(f"  Zero-gap (same second): {zero_gaps} ({zero_gaps/len(gaps)*100:.1f}%)")
    if nonzero_gaps:
        print(f"  Non-zero gaps:")
        print(f"    Mean:   {mean(nonzero_gaps):.2f}s")
        print(f"    Median: {median(nonzero_gaps):.1f}s")
        print(f"    Min:    {min(nonzero_gaps)}s")
        print(f"    Max:    {max(nonzero_gaps)}s")
        print(f"    Stdev:  {stdev(nonzero_gaps):.2f}s")
        q = quantiles(nonzero_gaps, n=100)
        print(f"    P10={q[9]:.0f}s  P25={q[24]:.0f}s  P50={q[49]:.0f}s  P75={q[74]:.0f}s  P90={q[89]:.0f}s  P99={q[98]:.0f}s")

    print(f"\n  Gap distribution (all):")
    histogram(gaps, [
        ("0s (同秒)", 0, 0.5),
        ("1s", 0.5, 1.5),
        ("2-3s", 1.5, 3.5),
        ("4-5s", 3.5, 5.5),
        ("6-10s", 5.5, 10.5),
        ("11-30s", 10.5, 30.5),
        ("31-60s", 30.5, 60.5),
        ("1-5min", 60.5, 300.5),
        (">5min", 300.5, 999999),
    ])

    # ═══════════════════════════════════════════════════════════════════
    section("3. TRANSACTION HASH ANALYSIS (batching)")
    # ═══════════════════════════════════════════════════════════════════

    tx_groups = defaultdict(list)
    for t in trades:
        tx_groups[t.get("transactionHash", "")].append(t)

    tx_sizes = [len(v) for v in tx_groups.values()]
    print(f"  Unique transactions: {len(tx_groups)}")
    print(f"  Trades per transaction:")
    print(f"    Mean:   {mean(tx_sizes):.2f}")
    print(f"    Median: {median(tx_sizes):.1f}")
    print(f"    Max:    {max(tx_sizes)}")

    tx_size_dist = Counter(tx_sizes)
    print(f"  Distribution:")
    for n in sorted(tx_size_dist.keys()):
        bar = "█" * int(tx_size_dist[n] / len(tx_groups) * 40)
        print(f"    {n} trades/tx: {tx_size_dist[n]:>5} ({tx_size_dist[n]/len(tx_groups)*100:5.1f}%) {bar}")

    # Check if batched trades are same outcome or mixed
    mixed_tx = 0
    same_tx = 0
    for tx, tx_trades in tx_groups.items():
        if len(tx_trades) > 1:
            outcomes = set(t.get("outcome", "") for t in tx_trades)
            slugs = set(t.get("eventSlug", "") for t in tx_trades)
            if len(outcomes) > 1 or len(slugs) > 1:
                mixed_tx += 1
            else:
                same_tx += 1
    multi_tx = sum(1 for s in tx_sizes if s > 1)
    print(f"\n  Multi-trade transactions: {multi_tx}")
    print(f"    Mixed (diff outcome/market): {mixed_tx}")
    print(f"    Same (same outcome+market):  {same_tx}")

    # ═══════════════════════════════════════════════════════════════════
    section("4. PER-MARKET TIMING DEEP DIVE")
    # ═══════════════════════════════════════════════════════════════════

    markets = defaultdict(list)
    for t in trades:
        key = t.get("eventSlug") or t.get("slug") or "unknown"
        markets[key].append(t)

    # Sort trades within each market
    for slug in markets:
        markets[slug].sort(key=lambda t: t["timestamp"])

    market_durations = []    # how long they're active in a market
    market_first_gaps = []   # gap between first UP and first DOWN
    market_up_first = 0
    market_down_first = 0
    market_same_sec = 0
    intra_market_gaps = []   # all gaps within a market

    per_market_stats = []

    for slug, mtrades in markets.items():
        window_ts = extract_window_ts(slug)
        ts_list = [t["timestamp"] for t in mtrades]
        duration = max(ts_list) - min(ts_list)
        market_durations.append(duration)

        # Intra-market gaps
        for i in range(len(ts_list) - 1):
            intra_market_gaps.append(ts_list[i+1] - ts_list[i])

        # UP vs DOWN ordering
        up_ts = [t["timestamp"] for t in mtrades if t.get("outcome", "").lower() in ("up", "yes")]
        down_ts = [t["timestamp"] for t in mtrades if t.get("outcome", "").lower() in ("down", "no")]

        first_up = min(up_ts) if up_ts else None
        first_down = min(down_ts) if down_ts else None

        if first_up and first_down:
            gap = abs(first_up - first_down)
            market_first_gaps.append(gap)
            if first_up < first_down:
                market_up_first += 1
            elif first_down < first_up:
                market_down_first += 1
            else:
                market_same_sec += 1

        # Entry offset from window
        entry_offset = min(ts_list) - window_ts if window_ts else None

        # Price patterns within market
        up_prices = [float(t.get("price", 0)) for t in mtrades if t.get("outcome", "").lower() in ("up", "yes")]
        down_prices = [float(t.get("price", 0)) for t in mtrades if t.get("outcome", "").lower() in ("down", "no")]

        # Size patterns
        up_sizes = [float(t.get("size", 0)) for t in mtrades if t.get("outcome", "").lower() in ("up", "yes")]
        down_sizes = [float(t.get("size", 0)) for t in mtrades if t.get("outcome", "").lower() in ("down", "no")]

        coin = "BTC" if "btc" in slug.lower() else "ETH" if "eth" in slug.lower() else "OTHER"

        per_market_stats.append({
            "slug": slug,
            "coin": coin,
            "num_trades": len(mtrades),
            "duration_s": duration,
            "entry_offset_s": entry_offset,
            "first_up": first_up,
            "first_down": first_down,
            "up_first": first_up < first_down if (first_up and first_down) else None,
            "side_gap_s": abs(first_up - first_down) if (first_up and first_down) else None,
            "up_prices": up_prices,
            "down_prices": down_prices,
            "up_sizes": up_sizes,
            "down_sizes": down_sizes,
            "up_price_range": (min(up_prices), max(up_prices)) if up_prices else (0, 0),
            "down_price_range": (min(down_prices), max(down_prices)) if down_prices else (0, 0),
            "up_size_total": sum(up_sizes),
            "down_size_total": sum(down_sizes),
        })

    print(f"  Markets: {len(markets)}")
    print(f"\n  --- Activity Duration per Market ---")
    print(f"  Mean:   {mean(market_durations):.1f}s ({mean(market_durations)/60:.1f} min)")
    print(f"  Median: {median(market_durations):.1f}s ({median(market_durations)/60:.1f} min)")
    print(f"  Max:    {max(market_durations)}s ({max(market_durations)/60:.1f} min)")
    histogram(market_durations, [
        ("0-30s", 0, 30),
        ("30-60s", 30, 60),
        ("1-2min", 60, 120),
        ("2-3min", 120, 180),
        ("3-4min", 180, 240),
        ("4-5min", 240, 300),
    ])

    print(f"\n  --- UP vs DOWN: 邊個先落單？ ---")
    total_both = market_up_first + market_down_first + market_same_sec
    if total_both:
        print(f"  UP first:    {market_up_first} ({market_up_first/total_both*100:.1f}%)")
        print(f"  DOWN first:  {market_down_first} ({market_down_first/total_both*100:.1f}%)")
        print(f"  Same second: {market_same_sec} ({market_same_sec/total_both*100:.1f}%)")

    if market_first_gaps:
        print(f"\n  --- UP↔DOWN 第一單嘅時間差 ---")
        print(f"  Mean:   {mean(market_first_gaps):.1f}s")
        print(f"  Median: {median(market_first_gaps):.1f}s")
        histogram(market_first_gaps, [
            ("0s (同秒)", 0, 0.5),
            ("1-2s", 0.5, 2.5),
            ("3-5s", 2.5, 5.5),
            ("6-10s", 5.5, 10.5),
            ("11-30s", 10.5, 30.5),
            ("31-60s", 30.5, 60.5),
            ("1-2min", 60.5, 120.5),
            (">2min", 120.5, 999999),
        ])

    if intra_market_gaps:
        print(f"\n  --- Intra-Market Gaps (within same market) ---")
        print(f"  Mean:   {mean(intra_market_gaps):.2f}s")
        print(f"  Median: {median(intra_market_gaps):.1f}s")
        intra_zero = sum(1 for g in intra_market_gaps if g == 0)
        print(f"  Same second: {intra_zero} ({intra_zero/len(intra_market_gaps)*100:.1f}%)")
        histogram(intra_market_gaps, [
            ("0s (同秒)", 0, 0.5),
            ("1s", 0.5, 1.5),
            ("2-3s", 1.5, 3.5),
            ("4-5s", 3.5, 5.5),
            ("6-10s", 5.5, 10.5),
            ("11-30s", 10.5, 30.5),
            ("31-60s", 30.5, 60.5),
            (">1min", 60.5, 999999),
        ])

    # ═══════════════════════════════════════════════════════════════════
    section("5. ORDER SIZE PATTERNS")
    # ═══════════════════════════════════════════════════════════════════

    all_sizes = [float(t.get("size", 0)) for t in trades]
    print(f"  Mean:   {mean(all_sizes):.2f} shares")
    print(f"  Median: {median(all_sizes):.2f}")
    print(f"  Min:    {min(all_sizes):.2f}")
    print(f"  Max:    {max(all_sizes):.2f}")

    size_counter = Counter(round(s, 2) for s in all_sizes)
    print(f"\n  Top 15 order sizes:")
    for size, count in size_counter.most_common(15):
        bar = "█" * int(count / len(trades) * 50)
        print(f"    {size:>10.2f}: {count:>5} ({count/len(trades)*100:5.1f}%) {bar}")

    # Round number analysis
    round_sizes = sum(1 for s in all_sizes if s == round(s))
    print(f"\n  Round numbers (integer): {round_sizes} ({round_sizes/len(all_sizes)*100:.1f}%)")

    histogram(all_sizes, [
        ("0-10", 0, 10),
        ("10-25", 10, 25),
        ("25-50", 25, 50),
        ("50-100", 50, 100),
        ("100-200", 100, 200),
        ("200-500", 200, 500),
        (">500", 500, 999999),
    ])

    # ═══════════════════════════════════════════════════════════════════
    section("6. PRICE PATTERNS")
    # ═══════════════════════════════════════════════════════════════════

    all_prices = [float(t.get("price", 0)) for t in trades]
    up_all = [float(t.get("price", 0)) for t in trades if t.get("outcome", "").lower() in ("up", "yes")]
    down_all = [float(t.get("price", 0)) for t in trades if t.get("outcome", "").lower() in ("down", "no")]

    print(f"  All prices: mean={mean(all_prices):.4f}  median={median(all_prices):.4f}")
    print(f"  UP prices:  mean={mean(up_all):.4f}  median={median(up_all):.4f}  min={min(up_all):.4f}  max={max(up_all):.4f}")
    print(f"  DOWN prices: mean={mean(down_all):.4f}  median={median(down_all):.4f}  min={min(down_all):.4f}  max={max(down_all):.4f}")

    print(f"\n  Price distribution (all trades):")
    histogram(all_prices, [
        ("0.01-0.10", 0.01, 0.10),
        ("0.10-0.20", 0.10, 0.20),
        ("0.20-0.30", 0.20, 0.30),
        ("0.30-0.40", 0.30, 0.40),
        ("0.40-0.50", 0.40, 0.50),
        ("0.50-0.60", 0.50, 0.60),
        ("0.60-0.70", 0.60, 0.70),
        ("0.70-0.80", 0.70, 0.80),
        ("0.80-0.90", 0.80, 0.90),
        ("0.90-1.00", 0.90, 1.00),
    ])

    # Price change within market
    print(f"\n  --- Price drift within market ---")
    up_drifts = []
    down_drifts = []
    for m in per_market_stats:
        if len(m["up_prices"]) >= 2:
            drift = m["up_prices"][-1] - m["up_prices"][0]
            up_drifts.append(drift)
        if len(m["down_prices"]) >= 2:
            drift = m["down_prices"][-1] - m["down_prices"][0]
            down_drifts.append(drift)
    if up_drifts:
        print(f"  UP price drift (last - first): mean={mean(up_drifts):.4f}  median={median(up_drifts):.4f}")
    if down_drifts:
        print(f"  DOWN price drift (last - first): mean={mean(down_drifts):.4f}  median={median(down_drifts):.4f}")

    # ═══════════════════════════════════════════════════════════════════
    section("7. CONCURRENT MARKET ACTIVITY")
    # ═══════════════════════════════════════════════════════════════════

    # For each trade, count how many markets are active at that timestamp
    # A market is "active" from its first trade to last trade
    market_spans = []
    for slug, mtrades in markets.items():
        ts_list = [t["timestamp"] for t in mtrades]
        market_spans.append((min(ts_list), max(ts_list), slug))

    # Sample at each trade timestamp
    concurrent_counts = []
    for ts in sorted(set(timestamps)):
        active = sum(1 for s, e, _ in market_spans if s <= ts <= e)
        concurrent_counts.append(active)

    print(f"  Concurrent active markets:")
    print(f"    Mean:   {mean(concurrent_counts):.1f}")
    print(f"    Median: {median(concurrent_counts):.1f}")
    print(f"    Max:    {max(concurrent_counts)}")

    # ═══════════════════════════════════════════════════════════════════
    section("8. WINDOW-BY-WINDOW TIMELINE (sample: first 20 markets)")
    # ═══════════════════════════════════════════════════════════════════

    sorted_markets = sorted(per_market_stats, key=lambda m: m.get("entry_offset_s", 9999) if m.get("entry_offset_s") is not None else 9999)
    # Sort by first trade timestamp instead
    sorted_markets = sorted(per_market_stats, key=lambda m: min(m["first_up"] or 9999999999, m["first_down"] or 9999999999))

    print(f"  {'Slug':<35} {'Coin':>4} {'#Tr':>4} {'Dur':>5} {'Entry':>6} {'Gap':>5} {'UpP':>6} {'DnP':>6} {'Comb':>6} {'UpSh':>7} {'DnSh':>7} {'1st':>5}")
    print(f"  {'-'*35} {'-'*4} {'-'*4} {'-'*5} {'-'*6} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*5}")

    for m in sorted_markets[:30]:
        slug_short = m["slug"][-25:] if len(m["slug"]) > 25 else m["slug"]
        entry = f"{m['entry_offset_s']}s" if m["entry_offset_s"] is not None else "?"
        gap = f"{m['side_gap_s']}s" if m["side_gap_s"] is not None else "-"
        up_p = f"{mean(m['up_prices']):.3f}" if m["up_prices"] else "-"
        dn_p = f"{mean(m['down_prices']):.3f}" if m["down_prices"] else "-"
        comb_val = mean(m["up_prices"]) + mean(m["down_prices"]) if (m["up_prices"] and m["down_prices"]) else 0
        comb = f"{comb_val:.3f}" if comb_val else "-"
        first = "UP" if m.get("up_first") == True else "DN" if m.get("up_first") == False else "="
        print(f"  {slug_short:<35} {m['coin']:>4} {m['num_trades']:>4} {m['duration_s']:>4}s {entry:>6} {gap:>5} {up_p:>6} {dn_p:>6} {comb:>6} {m['up_size_total']:>7.0f} {m['down_size_total']:>7.0f} {first:>5}")

    # ═══════════════════════════════════════════════════════════════════
    section("9. BURST DETECTION")
    # ═══════════════════════════════════════════════════════════════════

    # Find periods with unusually high activity (>10 trades in 5 seconds)
    bursts = []
    for i in range(len(timestamps)):
        # Count trades in next 5 seconds
        count = 0
        j = i
        while j < len(timestamps) and timestamps[j] - timestamps[i] <= 5:
            count += 1
            j += 1
        if count >= 8:
            bursts.append((timestamps[i], count))

    # Deduplicate overlapping bursts
    if bursts:
        deduped = [bursts[0]]
        for ts, count in bursts[1:]:
            if ts - deduped[-1][0] > 5:
                deduped.append((ts, count))
            elif count > deduped[-1][1]:
                deduped[-1] = (ts, count)

        print(f"  Bursts (≥8 trades in 5s): {len(deduped)}")
        for ts, count in deduped[:20]:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')
            # What markets were active?
            burst_trades = [t for t in trades if ts <= t["timestamp"] <= ts + 5]
            burst_markets = set(t.get("eventSlug", "") for t in burst_trades)
            burst_outcomes = Counter(t.get("outcome", "") for t in burst_trades)
            print(f"    {dt} UTC: {count} trades in 5s | {len(burst_markets)} markets | {dict(burst_outcomes)}")
    else:
        print(f"  No bursts detected (≥8 trades in 5s)")

    # ═══════════════════════════════════════════════════════════════════
    section("10. BOT BEHAVIOR SIGNATURE")
    # ═══════════════════════════════════════════════════════════════════

    # Regularity: is the bot on a fixed cycle?
    # Check if there's a dominant gap pattern
    gap_counter = Counter(g for g in gaps if g > 0)
    print(f"  Top 10 inter-trade gaps:")
    for gap_val, count in gap_counter.most_common(10):
        print(f"    {gap_val}s: {count} times ({count/len(gaps)*100:.1f}%)")

    # Check if trades happen at fixed offsets into each second
    # (sub-second precision not available, but we can check second-level patterns)
    sec_of_minute = Counter(ts % 60 for ts in timestamps)
    print(f"\n  Second-of-minute distribution (top 10):")
    for sec, count in sec_of_minute.most_common(10):
        bar = "█" * int(count / len(trades) * 100)
        print(f"    :{sec:02d} → {count:>4} trades {bar}")

    # Minute-of-5min distribution (0-4)
    min_of_5 = Counter((ts % 300) // 60 for ts in timestamps)
    print(f"\n  Minute-of-5min-window distribution:")
    for m in range(5):
        c = min_of_5.get(m, 0)
        bar = "█" * int(c / len(trades) * 50)
        print(f"    Min {m}: {c:>5} trades ({c/len(trades)*100:5.1f}%) {bar}")

    # Is the bot sleeping between windows?
    # Find gaps > 60s and check if they align with window boundaries
    big_gaps = [(i, gaps[i]) for i in range(len(gaps)) if gaps[i] > 60]
    window_aligned = 0
    for idx, gap in big_gaps:
        ts_after = timestamps[idx + 1]
        # Check if ts_after is near a window start (within 30s)
        offset = ts_after % 300
        if offset <= 30 or offset >= 270:
            window_aligned += 1

    print(f"\n  Big gaps (>60s): {len(big_gaps)}")
    if big_gaps:
        print(f"  Window-aligned (within 30s of 5min mark): {window_aligned} ({window_aligned/len(big_gaps)*100:.1f}%)")

    print(f"\n{'═'*70}")
    print(f"  ANALYSIS COMPLETE")
    print(f"{'═'*70}")


if __name__ == "__main__":
    main()
