#!/usr/bin/env python3
"""
Reverse engineer Female-Billing + Unlawful-Shear trading patterns.
Fetch all trades via data-api.polymarket.com, map to windows, extract:
  - Entry timing (seconds into window)
  - Entry odds/price
  - Position sizing
  - Direction lean
  - Both-side vs single-side
  - Win rate proxy

Output: JSON + summary report
"""

import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from statistics import median, mean, stdev

# ─── Config ───
WALLETS = {
    "Female-Billing": "0xd1ebe815f921b3ebbd8d9e0a4192c6ab18360f5c",
    "Unlawful-Shear": "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
}
API_BASE = "https://data-api.polymarket.com/trades"
MAX_OFFSET = 3000
PAGE_SIZE = 500
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_all_trades(address: str) -> list[dict]:
    """Fetch up to 3500 trades for a wallet."""
    all_trades = []
    for offset in range(0, MAX_OFFSET + 1, PAGE_SIZE):
        url = f"{API_BASE}?user={address}&limit={PAGE_SIZE}&offset={offset}"
        print(f"  Fetching offset={offset}...", end=" ", flush=True)
        try:
            req = Request(url, headers={"User-Agent": "AXC-Research/1.0"})
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            print(f"{len(data)} trades")
            if not data:
                break
            all_trades.extend(data)
            if len(data) < PAGE_SIZE:
                break
            time.sleep(0.3)  # polite
        except Exception as e:
            print(f"ERROR: {e}")
            break
    # Dedup by transactionHash + asset + outcome
    seen = set()
    unique = []
    for t in all_trades:
        key = (t.get("transactionHash", ""), t.get("asset", ""),
               t.get("outcome", ""), str(t.get("size", "")), str(t.get("price", "")))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    print(f"  Total: {len(all_trades)} raw, {len(unique)} unique")
    return unique


def parse_window_from_slug(slug: str) -> dict | None:
    """Extract coin, timeframe, window_start from slug."""
    # Patterns:
    #   btc-updown-5m-1774374300
    #   bitcoin-up-or-down-march-24-2026-11  (1H format)
    #   eth-updown-15m-1774374300
    #   solana-up-or-down-5-minutes-march-24-2026-...

    slug_lower = slug.lower() if slug else ""

    # Detect coin
    coin = None
    if "btc" in slug_lower or "bitcoin" in slug_lower:
        coin = "BTC"
    elif "eth" in slug_lower or "ethereum" in slug_lower:
        coin = "ETH"
    elif "sol" in slug_lower or "solana" in slug_lower:
        coin = "SOL"
    elif "xrp" in slug_lower or "ripple" in slug_lower:
        coin = "XRP"

    # Detect timeframe
    tf = None
    if "-5m-" in slug_lower or "5-minute" in slug_lower or "5-min" in slug_lower:
        tf = "5M"
    elif "-15m-" in slug_lower or "15-minute" in slug_lower or "15-min" in slug_lower:
        tf = "15M"
    elif "-1h-" in slug_lower or "hourly" in slug_lower or re.search(r'-\d{1,2}(am|pm)?$', slug_lower):
        tf = "1H"

    # Extract window start timestamp if present
    ts_match = re.search(r'-(\d{10,})$', slug)
    window_start = int(ts_match.group(1)) if ts_match else None

    # Infer window duration
    duration = {"5M": 300, "15M": 900, "1H": 3600}.get(tf, None)

    return {
        "coin": coin,
        "timeframe": tf,
        "window_start": window_start,
        "duration": duration,
    }


def analyze_wallet(name: str, trades: list[dict]) -> dict:
    """Deep analysis of trade patterns."""
    if not trades:
        return {"name": name, "error": "No trades"}

    # ─── Basic stats ───
    total_volume = sum(float(t.get("size", 0)) * float(t.get("price", 0)) for t in trades)
    total_shares = sum(float(t.get("size", 0)) for t in trades)
    timestamps = [int(t["timestamp"]) for t in trades if t.get("timestamp")]
    ts_min, ts_max = min(timestamps), max(timestamps)
    span_hours = (ts_max - ts_min) / 3600

    # ─── Map trades to windows ───
    windows = defaultdict(lambda: {"up_trades": [], "down_trades": [], "sells": []})

    for t in trades:
        slug = t.get("slug") or t.get("eventSlug") or ""
        parsed = parse_window_from_slug(slug)
        outcome = (t.get("outcome") or "").lower()
        side = (t.get("side") or "").upper()
        price = float(t.get("price", 0))
        size = float(t.get("size", 0))
        ts = int(t.get("timestamp", 0))

        window_key = slug  # unique per market
        w = windows[window_key]
        w["slug"] = slug
        w["coin"] = parsed["coin"]
        w["timeframe"] = parsed["timeframe"]
        w["window_start"] = parsed["window_start"]
        w["duration"] = parsed["duration"]

        trade_rec = {"price": price, "size": size, "ts": ts, "side": side, "outcome": outcome}

        if side == "SELL":
            w["sells"].append(trade_rec)
        elif outcome == "up":
            w["up_trades"].append(trade_rec)
        elif outcome == "down":
            w["down_trades"].append(trade_rec)

    # ─── Per-window analysis ───
    window_stats = []
    for wkey, w in windows.items():
        up_buys = [t for t in w["up_trades"] if t["side"] == "BUY"]
        dn_buys = [t for t in w["down_trades"] if t["side"] == "BUY"]

        up_vol = sum(t["size"] * t["price"] for t in up_buys)
        dn_vol = sum(t["size"] * t["price"] for t in dn_buys)
        up_shares = sum(t["size"] for t in up_buys)
        dn_shares = sum(t["size"] for t in dn_buys)

        avg_up_price = (sum(t["price"] * t["size"] for t in up_buys) / up_shares) if up_shares > 0 else 0
        avg_dn_price = (sum(t["price"] * t["size"] for t in dn_buys) / dn_shares) if dn_shares > 0 else 0

        combined = avg_up_price + avg_dn_price if (up_shares > 0 and dn_shares > 0) else None

        both_sides = up_shares > 0 and dn_shares > 0

        # Lean direction + ratio
        if up_shares > 0 and dn_shares > 0:
            lean_dir = "UP" if up_shares >= dn_shares else "DOWN"
            lean_ratio = max(up_shares, dn_shares) / min(up_shares, dn_shares)
        elif up_shares > 0:
            lean_dir = "UP"
            lean_ratio = float('inf')
        elif dn_shares > 0:
            lean_dir = "DOWN"
            lean_ratio = float('inf')
        else:
            lean_dir = None
            lean_ratio = None

        # Entry timing (seconds into window)
        all_ts = [t["ts"] for t in up_buys + dn_buys]
        first_entry = min(all_ts) if all_ts else None
        entry_offset = None
        if first_entry and w["window_start"]:
            entry_offset = first_entry - w["window_start"]

        # Number of trades
        n_trades = len(up_buys) + len(dn_buys) + len(w["sells"])

        ws = {
            "slug": wkey,
            "coin": w["coin"],
            "timeframe": w["timeframe"],
            "window_start": w["window_start"],
            "duration": w["duration"],
            "up_shares": round(up_shares, 1),
            "dn_shares": round(dn_shares, 1),
            "up_vol": round(up_vol, 2),
            "dn_vol": round(dn_vol, 2),
            "avg_up_price": round(avg_up_price, 4),
            "avg_dn_price": round(avg_dn_price, 4),
            "combined": round(combined, 4) if combined else None,
            "both_sides": both_sides,
            "lean_dir": lean_dir,
            "lean_ratio": round(lean_ratio, 2) if lean_ratio and lean_ratio != float('inf') else ("INF" if lean_ratio == float('inf') else None),
            "entry_offset_s": entry_offset,
            "n_trades": n_trades,
            "n_sells": len(w["sells"]),
            "first_ts": min(all_ts) if all_ts else None,
        }
        window_stats.append(ws)

    # Sort by timestamp
    window_stats.sort(key=lambda x: x["first_ts"] or 0)

    # ─── Aggregations ───

    # By coin
    coin_stats = defaultdict(lambda: {"windows": 0, "shares": 0, "volume": 0})
    for ws in window_stats:
        c = ws["coin"] or "UNKNOWN"
        coin_stats[c]["windows"] += 1
        coin_stats[c]["shares"] += ws["up_shares"] + ws["dn_shares"]
        coin_stats[c]["volume"] += ws["up_vol"] + ws["dn_vol"]

    # By timeframe
    tf_stats = defaultdict(lambda: {"windows": 0, "shares": 0})
    for ws in window_stats:
        tf = ws["timeframe"] or "UNKNOWN"
        tf_stats[tf]["windows"] += 1
        tf_stats[tf]["shares"] += ws["up_shares"] + ws["dn_shares"]

    # Both-side rate
    both_count = sum(1 for ws in window_stats if ws["both_sides"])
    single_count = sum(1 for ws in window_stats if not ws["both_sides"])

    # Combined price distribution (both-side windows only)
    combined_prices = [ws["combined"] for ws in window_stats if ws["combined"] is not None]
    combined_lt_1 = sum(1 for c in combined_prices if c < 1.0)

    # Entry timing distribution
    entry_offsets = [ws["entry_offset_s"] for ws in window_stats if ws["entry_offset_s"] is not None]

    # Lean analysis
    lean_ratios = [ws["lean_ratio"] for ws in window_stats
                   if ws["lean_ratio"] is not None and ws["lean_ratio"] != "INF"]
    lean_dirs = [ws["lean_dir"] for ws in window_stats if ws["lean_dir"]]
    up_lean_pct = lean_dirs.count("UP") / len(lean_dirs) * 100 if lean_dirs else 0

    # Size per trade
    all_sizes = [float(t.get("size", 0)) for t in trades if t.get("side") == "BUY"]
    all_prices = [float(t.get("price", 0)) for t in trades if t.get("side") == "BUY"]

    # Price buckets
    price_buckets = defaultdict(int)
    for p in all_prices:
        bucket = f"{int(p*10)/10:.1f}-{int(p*10)/10+0.1:.1f}"
        price_buckets[bucket] += 1

    # Hourly distribution
    hour_dist = defaultdict(int)
    for ts in timestamps:
        h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        hour_dist[h] += 1

    # Daily PnL proxy (can't know outcome, but can show daily activity)
    daily_stats = defaultdict(lambda: {"trades": 0, "volume": 0, "windows": set()})
    for ws in window_stats:
        if ws["first_ts"]:
            day = datetime.fromtimestamp(ws["first_ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            daily_stats[day]["trades"] += ws["n_trades"]
            daily_stats[day]["volume"] += ws["up_vol"] + ws["dn_vol"]
            daily_stats[day]["windows"].add(ws["slug"])

    # Top 10 best combined (lowest) and worst (highest)
    both_side_ws = [ws for ws in window_stats if ws["combined"] is not None]
    best_arb = sorted(both_side_ws, key=lambda x: x["combined"])[:10]
    worst_arb = sorted(both_side_ws, key=lambda x: -x["combined"])[:10]

    return {
        "name": name,
        "total_trades": len(trades),
        "total_unique_windows": len(window_stats),
        "total_volume": round(total_volume, 2),
        "total_shares": round(total_shares, 1),
        "time_span_hours": round(span_hours, 2),
        "ts_range": [ts_min, ts_max],
        "coin_breakdown": dict(coin_stats),
        "tf_breakdown": dict(tf_stats),
        "both_side_rate": round(both_count / len(window_stats) * 100, 1) if window_stats else 0,
        "both_count": both_count,
        "single_count": single_count,
        "combined_prices": {
            "count": len(combined_prices),
            "mean": round(mean(combined_prices), 4) if combined_prices else None,
            "median": round(median(combined_prices), 4) if combined_prices else None,
            "min": round(min(combined_prices), 4) if combined_prices else None,
            "max": round(max(combined_prices), 4) if combined_prices else None,
            "stdev": round(stdev(combined_prices), 4) if len(combined_prices) > 1 else None,
            "pct_below_1": round(combined_lt_1 / len(combined_prices) * 100, 1) if combined_prices else 0,
        },
        "entry_timing": {
            "count": len(entry_offsets),
            "mean_s": round(mean(entry_offsets), 1) if entry_offsets else None,
            "median_s": round(median(entry_offsets), 1) if entry_offsets else None,
            "min_s": min(entry_offsets) if entry_offsets else None,
            "max_s": max(entry_offsets) if entry_offsets else None,
            "p25_s": round(sorted(entry_offsets)[len(entry_offsets)//4], 1) if len(entry_offsets) > 3 else None,
            "p75_s": round(sorted(entry_offsets)[3*len(entry_offsets)//4], 1) if len(entry_offsets) > 3 else None,
        },
        "lean": {
            "up_lean_pct": round(up_lean_pct, 1),
            "down_lean_pct": round(100 - up_lean_pct, 1) if lean_dirs else 0,
            "mean_ratio": round(mean(lean_ratios), 2) if lean_ratios else None,
            "median_ratio": round(median(lean_ratios), 2) if lean_ratios else None,
            "max_ratio": round(max(lean_ratios), 2) if lean_ratios else None,
        },
        "sizing": {
            "mean_shares": round(mean(all_sizes), 1) if all_sizes else None,
            "median_shares": round(median(all_sizes), 1) if all_sizes else None,
            "min_shares": round(min(all_sizes), 1) if all_sizes else None,
            "max_shares": round(max(all_sizes), 1) if all_sizes else None,
            "p25": round(sorted(all_sizes)[len(all_sizes)//4], 1) if len(all_sizes) > 3 else None,
            "p75": round(sorted(all_sizes)[3*len(all_sizes)//4], 1) if len(all_sizes) > 3 else None,
        },
        "price_distribution": dict(sorted(price_buckets.items())),
        "hourly_distribution": dict(sorted(hour_dist.items())),
        "daily_activity": {
            day: {"trades": v["trades"], "volume": round(v["volume"], 2), "windows": len(v["windows"])}
            for day, v in sorted(daily_stats.items())
        },
        "best_arb_windows": [
            {"slug": w["slug"], "coin": w["coin"], "tf": w["timeframe"],
             "combined": w["combined"], "up_price": w["avg_up_price"], "dn_price": w["avg_dn_price"],
             "up_shares": w["up_shares"], "dn_shares": w["dn_shares"], "lean": w["lean_dir"],
             "entry_offset": w["entry_offset_s"]}
            for w in best_arb
        ],
        "worst_arb_windows": [
            {"slug": w["slug"], "coin": w["coin"], "tf": w["timeframe"],
             "combined": w["combined"], "up_price": w["avg_up_price"], "dn_price": w["avg_dn_price"],
             "up_shares": w["up_shares"], "dn_shares": w["dn_shares"], "lean": w["lean_dir"],
             "entry_offset": w["entry_offset_s"]}
            for w in worst_arb
        ],
        "sell_activity": {
            "total_sells": sum(ws["n_sells"] for ws in window_stats),
            "windows_with_sells": sum(1 for ws in window_stats if ws["n_sells"] > 0),
        },
        "all_windows": window_stats,  # full detail
    }


def generate_report(results: dict[str, dict]) -> str:
    """Generate markdown comparison report."""
    lines = [
        "# Wallet Reverse Engineering — Entry Timing / Odds / Sizing",
        f"> Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"> Wallets: {', '.join(results.keys())}",
        f"> Method: data-api.polymarket.com/trades (max 3500 per wallet)",
        "",
        "---",
        "",
        "## 1. Head-to-Head Comparison",
        "",
        "| Metric | " + " | ".join(results.keys()) + " |",
        "|--------|" + "|".join(["--------"] * len(results)) + "|",
    ]

    metrics = [
        ("Total trades", lambda r: f"{r['total_trades']:,}"),
        ("Unique windows", lambda r: f"{r['total_unique_windows']:,}"),
        ("Volume ($)", lambda r: f"${r['total_volume']:,.0f}"),
        ("Total shares", lambda r: f"{r['total_shares']:,.0f}"),
        ("Time span", lambda r: f"{r['time_span_hours']:.1f}h"),
        ("Both-side rate", lambda r: f"{r['both_side_rate']:.1f}%"),
        ("Combined (mean)", lambda r: f"{r['combined_prices']['mean']}" if r['combined_prices']['mean'] else "N/A"),
        ("Combined (median)", lambda r: f"{r['combined_prices']['median']}" if r['combined_prices']['median'] else "N/A"),
        ("Combined < $1.00", lambda r: f"{r['combined_prices']['pct_below_1']:.0f}%"),
        ("Entry timing (median)", lambda r: f"{r['entry_timing']['median_s']}s" if r['entry_timing']['median_s'] else "N/A"),
        ("Entry timing (p25-p75)", lambda r: f"{r['entry_timing']['p25_s']}-{r['entry_timing']['p75_s']}s" if r['entry_timing']['p25_s'] else "N/A"),
        ("Lean: Up%", lambda r: f"{r['lean']['up_lean_pct']:.0f}%"),
        ("Lean ratio (median)", lambda r: f"{r['lean']['median_ratio']}" if r['lean']['median_ratio'] else "N/A"),
        ("Size (median shares)", lambda r: f"{r['sizing']['median_shares']}"),
        ("Size (p25-p75)", lambda r: f"{r['sizing']['p25']}-{r['sizing']['p75']}"),
        ("Sells", lambda r: f"{r['sell_activity']['total_sells']}"),
    ]

    for label, fn in metrics:
        vals = " | ".join(fn(r) for r in results.values())
        lines.append(f"| **{label}** | {vals} |")

    # Coin breakdown
    lines.extend(["", "## 2. Coin Breakdown", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        lines.append("| Coin | Windows | Shares | Volume |")
        lines.append("|------|---------|--------|--------|")
        for coin, stats in sorted(r["coin_breakdown"].items(), key=lambda x: -x[1]["shares"]):
            lines.append(f"| {coin} | {stats['windows']} | {stats['shares']:,.0f} | ${stats['volume']:,.0f} |")
        lines.append("")

    # Timeframe breakdown
    lines.extend(["## 3. Timeframe Breakdown", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        lines.append("| TF | Windows | Shares |")
        lines.append("|----|---------|--------|")
        for tf, stats in sorted(r["tf_breakdown"].items(), key=lambda x: -x[1]["windows"]):
            lines.append(f"| {tf} | {stats['windows']} | {stats['shares']:,.0f} |")
        lines.append("")

    # Price distribution
    lines.extend(["## 4. Entry Price Distribution (BUY trades)", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        lines.append("| Price Range | Count | Bar |")
        lines.append("|-------------|-------|-----|")
        max_count = max(r["price_distribution"].values()) if r["price_distribution"] else 1
        for bucket, count in sorted(r["price_distribution"].items()):
            bar = "█" * max(1, int(count / max_count * 30))
            lines.append(f"| {bucket} | {count} | {bar} |")
        lines.append("")

    # Entry timing
    lines.extend(["## 5. Entry Timing (seconds after window open)", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        et = r["entry_timing"]
        lines.append(f"- Min: {et['min_s']}s | P25: {et['p25_s']}s | Median: {et['median_s']}s | P75: {et['p75_s']}s | Max: {et['max_s']}s")
        lines.append("")

    # Combined price analysis
    lines.extend(["## 6. Combined Price Analysis (both-side windows only)", ""])
    for wname, r in results.items():
        cp = r["combined_prices"]
        lines.append(f"### {wname}")
        lines.append(f"- N = {cp['count']} windows")
        lines.append(f"- Mean: {cp['mean']} | Median: {cp['median']} | Stdev: {cp['stdev']}")
        lines.append(f"- Min: {cp['min']} | Max: {cp['max']}")
        lines.append(f"- Below $1.00: {cp['pct_below_1']:.0f}%")
        lines.append("")

        lines.append("#### Top 10 Best (lowest combined)")
        lines.append("| Slug | Coin | TF | Combined | Up$ | Dn$ | Up# | Dn# | Lean | Entry |")
        lines.append("|------|------|----|----------|-----|-----|-----|-----|------|-------|")
        for w in r["best_arb_windows"]:
            lines.append(f"| ...{w['slug'][-30:]} | {w['coin']} | {w['tf']} | **{w['combined']}** | {w['up_price']} | {w['dn_price']} | {w['up_shares']} | {w['dn_shares']} | {w['lean']} | {w['entry_offset']}s |")
        lines.append("")

        lines.append("#### Top 10 Worst (highest combined)")
        lines.append("| Slug | Coin | TF | Combined | Up$ | Dn$ | Up# | Dn# | Lean | Entry |")
        lines.append("|------|------|----|----------|-----|-----|-----|-----|------|-------|")
        for w in r["worst_arb_windows"]:
            lines.append(f"| ...{w['slug'][-30:]} | {w['coin']} | {w['tf']} | **{w['combined']}** | {w['up_price']} | {w['dn_price']} | {w['up_shares']} | {w['dn_shares']} | {w['lean']} | {w['entry_offset']}s |")
        lines.append("")

    # Lean analysis
    lines.extend(["## 7. Directional Lean Analysis", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        ln = r["lean"]
        lines.append(f"- Up lean: {ln['up_lean_pct']:.0f}% | Down lean: {ln['down_lean_pct']:.0f}%")
        lines.append(f"- Lean ratio — Mean: {ln['mean_ratio']} | Median: {ln['median_ratio']} | Max: {ln['max_ratio']}")
        lines.append("")

    # Daily activity
    lines.extend(["## 8. Daily Activity", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        lines.append("| Date | Trades | Windows | Volume |")
        lines.append("|------|--------|---------|--------|")
        for day, stats in r["daily_activity"].items():
            lines.append(f"| {day} | {stats['trades']} | {stats['windows']} | ${stats['volume']:,.0f} |")
        lines.append("")

    # Hourly distribution
    lines.extend(["## 9. Hourly Distribution (UTC)", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        lines.append("| Hour | Trades | Bar |")
        lines.append("|------|--------|-----|")
        max_h = max(r["hourly_distribution"].values()) if r["hourly_distribution"] else 1
        for h in range(24):
            count = r["hourly_distribution"].get(h, 0)
            bar = "█" * max(0, int(count / max_h * 30)) if count > 0 else ""
            lines.append(f"| {h:02d}:00 | {count} | {bar} |")
        lines.append("")

    # Sizing
    lines.extend(["## 10. Position Sizing", ""])
    for wname, r in results.items():
        lines.append(f"### {wname}")
        sz = r["sizing"]
        lines.append(f"- Mean: {sz['mean_shares']} | Median: {sz['median_shares']} | Min: {sz['min_shares']} | Max: {sz['max_shares']}")
        lines.append(f"- P25: {sz['p25']} | P75: {sz['p75']}")
        lines.append("")

    # Actionable takeaways
    lines.extend([
        "## 11. Actionable Patterns for Our Bot",
        "",
        "### Entry Timing",
        "- When do they enter relative to window start?",
        "- Can we replicate this timing?",
        "",
        "### Price/Odds Selection",
        "- What price range do they target?",
        "- Combined < $1.00 or paying spread for direction?",
        "",
        "### Sizing Rules",
        "- Fixed lot or variable?",
        "- How does sizing relate to confidence/lean?",
        "",
        "### Direction Selection",
        "- Pure arb (50:50) or directional lean?",
        "- Lean ratio correlate with combined price?",
        "",
    ])

    return "\n".join(lines)


def main():
    results = {}

    for name, addr in WALLETS.items():
        print(f"\n{'='*60}")
        print(f"Fetching {name} ({addr[:10]}...)")
        print(f"{'='*60}")
        trades = fetch_all_trades(addr)

        # Save raw trades
        raw_path = os.path.join(OUTPUT_DIR, f"raw_{name.lower().replace('-', '_')}.json")
        with open(raw_path, "w") as f:
            json.dump(trades, f, indent=2)
        print(f"  Raw trades saved: {raw_path}")

        # Analyze
        print(f"  Analyzing...")
        results[name] = analyze_wallet(name, trades)

    # Save analysis JSON
    json_path = os.path.join(OUTPUT_DIR, "reverse_engineering_data.json")
    # Convert sets to lists for JSON
    for r in results.values():
        if "all_windows" in r:
            del r["all_windows"]  # too large for JSON, saved separately
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nAnalysis JSON saved: {json_path}")

    # Generate report
    report = generate_report(results)
    report_path = os.path.join(OUTPUT_DIR, "reverse_engineering_report.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved: {report_path}")

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for name, r in results.items():
        print(f"\n{name}:")
        print(f"  Trades: {r['total_trades']:,} | Windows: {r['total_unique_windows']} | Vol: ${r['total_volume']:,.0f}")
        print(f"  Both-side: {r['both_side_rate']:.0f}% | Combined median: {r['combined_prices']['median']}")
        print(f"  Entry timing median: {r['entry_timing']['median_s']}s")
        print(f"  Lean: Up {r['lean']['up_lean_pct']:.0f}% / Down {r['lean']['down_lean_pct']:.0f}% | Ratio median: {r['lean']['median_ratio']}")
        print(f"  Size median: {r['sizing']['median_shares']} shares")


if __name__ == "__main__":
    main()
