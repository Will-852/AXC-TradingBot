#!/usr/bin/env python3
"""
Agent D: W4 Strategy Historical Simulation on REAL Polymarket Data
==================================================================
Simulates the W4 (momentum-weighted) strategy using:
- shadow_tape.jsonl: ground truth 15M window outcomes
- signal_tape.jsonl: BTC prices + Poly OB mids at ~20s intervals
- poly_ob_tape.jsonl: raw Poly order book snapshots
- btc_1m_7days.json: BTC 1-minute candles
- mm_trades.jsonl: our actual v15 trades for comparison
"""

import json
import re
import os
import sys
import math
import statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path

import dateutil.parser

# ── Paths ──
BASE = Path("/Users/wai/projects/axc-trading/polymarket")
SHADOW_PATH = BASE / "logs" / "shadow_tape.jsonl"
SIGNAL_PATH = BASE / "logs" / "signal_tape.jsonl"
OB_PATH = BASE / "logs" / "poly_ob_tape.jsonl"
BTC_PATH = BASE / "analysis" / "btc_1m_7days.json"
MM_PATH = BASE / "logs" / "mm_trades.jsonl"
REPORT_PATH = BASE / "analysis" / "agent_d_report.md"

EDT = timezone(timedelta(hours=-4))
HKT = timezone(timedelta(hours=8))

# ── STEP 1: Build 15M Window Database from shadow_tape ──
print("=" * 70)
print("STEP 1: Building 15M window database from shadow_tape")
print("=" * 70)


def load_jsonl(path):
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


shadow_all = load_jsonl(SHADOW_PATH)
btc_shadows = [e for e in shadow_all if e.get("coin") == "BTC" and e.get("tf") == "15M"]
print(f"  Total shadow entries: {len(shadow_all)}")
print(f"  BTC 15M entries: {len(btc_shadows)}")


def parse_window_times_from_title(title):
    """Parse ET window start/end from title.
    Returns (start_epoch, end_epoch) in UTC epoch seconds.
    Handles cross-midnight: 'March 20, 11:45PM-March 21, 12:00AM ET'
    """
    # Pattern 1: same day "March 20, 2:00PM-2:15PM ET"
    m1 = re.match(
        r"Bitcoin Up or Down - (March \d+), (\d+:\d+[AP]M)-(\d+:\d+[AP]M) ET",
        title,
    )
    # Pattern 2: cross midnight "March 20, 11:45PM-March 21, 12:00AM ET"
    m2 = re.match(
        r"Bitcoin Up or Down - (March \d+), (\d+:\d+[AP]M)-(March \d+), (\d+:\d+[AP]M) ET",
        title,
    )

    if m2:
        date1 = m2.group(1)
        time1 = m2.group(2)
        date2 = m2.group(3)
        time2 = m2.group(4)
        dt_start = datetime.strptime(f"{date1} 2026 {time1}", "%B %d %Y %I:%M%p").replace(tzinfo=EDT)
        dt_end = datetime.strptime(f"{date2} 2026 {time2}", "%B %d %Y %I:%M%p").replace(tzinfo=EDT)
        return dt_start.timestamp(), dt_end.timestamp()
    elif m1:
        date_str = m1.group(1)
        time_start = m1.group(2)
        time_end = m1.group(3)
        dt_start = datetime.strptime(f"{date_str} 2026 {time_start}", "%B %d %Y %I:%M%p").replace(tzinfo=EDT)
        dt_end = datetime.strptime(f"{date_str} 2026 {time_end}", "%B %d %Y %I:%M%p").replace(tzinfo=EDT)
        return dt_start.timestamp(), dt_end.timestamp()
    else:
        return None, None


# Build window database
windows = []
for s in btc_shadows:
    start_ts, end_ts = parse_window_times_from_title(s["title"])
    if start_ts is None:
        continue
    windows.append({
        "title": s["title"],
        "ts_shadow": s["ts"],  # when shadow recorded it
        "window_start": start_ts,
        "window_end": end_ts,
        "open_price": s["open"],
        "result": s["result"],  # UP or DOWN
        "entry": s.get("entry"),  # our actual entry if any
    })

windows.sort(key=lambda w: w["window_start"])
print(f"  Parsed windows: {len(windows)}")
if windows:
    print(f"  Time range: {datetime.fromtimestamp(windows[0]['window_start'], tz=HKT)} to {datetime.fromtimestamp(windows[-1]['window_start'], tz=HKT)}")
    up_count = sum(1 for w in windows if w["result"] == "UP")
    print(f"  Results: UP={up_count}, DOWN={len(windows) - up_count} ({up_count/len(windows)*100:.1f}% UP)")

# ── STEP 2: Build BTC price timeline + Poly OB timeline from signal_tape ──
print()
print("=" * 70)
print("STEP 2: Building price timelines from signal_tape")
print("=" * 70)

signal_all = load_jsonl(SIGNAL_PATH)
print(f"  Signal tape entries: {len(signal_all)}")

# Build BTC price timeline: [(epoch, price), ...]
btc_prices = []
# Build Poly mid timeline by title: {title: [(epoch, up_mid, dn_mid), ...]}
poly_mids_by_title = defaultdict(list)

for s in signal_all:
    ts_str = s.get("ts")
    if not ts_str:
        continue
    ts_epoch = dateutil.parser.parse(ts_str).timestamp()

    btc = s.get("btc", {})
    if "median" in btc:
        btc_prices.append((ts_epoch, btc["median"]))

    for p in s.get("poly", []):
        if p.get("coin") == "BTC":
            title = p.get("title")
            if title:
                poly_mids_by_title[title].append((ts_epoch, p["up_mid"], p["dn_mid"]))

btc_prices.sort(key=lambda x: x[0])
print(f"  BTC price points: {len(btc_prices)}")
print(f"  Poly BTC title timelines: {len(poly_mids_by_title)}")

# Also load BTC 1m candles for finer resolution
btc_1m = json.load(open(BTC_PATH))
btc_1m_prices = [(c["ts"] / 1000, c["open"]) for c in btc_1m]
btc_1m_prices.sort(key=lambda x: x[0])
print(f"  BTC 1m candles: {len(btc_1m_prices)}")


def interp_btc_price(epoch, source="signal"):
    """Get BTC price at a given epoch by interpolation."""
    timeline = btc_prices if source == "signal" else btc_1m_prices
    if not timeline:
        return None
    if epoch <= timeline[0][0]:
        return timeline[0][1]
    if epoch >= timeline[-1][0]:
        return timeline[-1][1]

    # Binary search
    lo, hi = 0, len(timeline) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if timeline[mid][0] <= epoch:
            lo = mid
        else:
            hi = mid

    t0, p0 = timeline[lo]
    t1, p1 = timeline[hi]
    if t1 == t0:
        return p0
    frac = (epoch - t0) / (t1 - t0)
    return p0 + frac * (p1 - p0)


def get_poly_mid_at(title, epoch):
    """Get Poly UP/DOWN mid prices at given epoch for a title."""
    timeline = poly_mids_by_title.get(title, [])
    if not timeline:
        return None, None

    # Find closest entry
    best = min(timeline, key=lambda x: abs(x[0] - epoch))
    if abs(best[0] - epoch) > 60:  # more than 60s off
        return None, None
    return best[1], best[2]  # up_mid, dn_mid


# ── Also load OB tape for better price data ──
print()
print("Loading poly_ob_tape for additional OB data...")
ob_all = load_jsonl(OB_PATH)
btc_ob = [e for e in ob_all if e.get("coin") == "BTC"]
print(f"  BTC OB entries: {len(btc_ob)}")

# Build OB by window_end_ts for matching
ob_by_window_end = defaultdict(list)
for e in btc_ob:
    ob_by_window_end[e["window_end_ts"]].append(e)


def get_ob_mid_at(window_end_ts, target_tte):
    """Get OB mid from poly_ob_tape at a given time-to-end.
    Returns (up_mid, dn_mid) or (None, None).
    """
    entries = ob_by_window_end.get(window_end_ts, [])
    if not entries:
        return None, None

    # Find closest to target TTE
    best = min(entries, key=lambda e: abs(e["time_to_end_s"] - target_tte))
    if abs(best["time_to_end_s"] - target_tte) > 30:  # more than 30s off
        return None, None

    up_mid = (best["up_best_bid"] + best["up_best_ask"]) / 2
    dn_mid = (best["down_best_bid"] + best["down_best_ask"]) / 2
    return up_mid, dn_mid


# ── STEP 3: Simulate W4 Strategy ──
print()
print("=" * 70)
print("STEP 3: Simulating W4 strategy per window")
print("=" * 70)

ENTRY_DELAY_DEFAULT = 300  # seconds after window start
THRESHOLD_DEFAULT = 5  # bps
LEAN_RATIO_DEFAULT = 2.0  # lean:hedge ratio
TOTAL_COST_DEFAULT = 1.265  # $0.843 lean + $0.422 hedge


def simulate_w4(windows, entry_delay=300, threshold_bps=5, lean_ratio=2.0, total_cost=1.265, verbose=True):
    """
    Simulate W4 strategy.

    For each window:
    1. At T+entry_delay, check BTC return from window open
    2. If |return| > threshold_bps, signal fires
    3. Direction = BTC momentum direction
    4. Buy lean side and hedge side at Poly mid
    5. Calculate PnL from outcome

    Returns list of trade dicts.
    """
    trades = []
    skipped_no_price = 0
    skipped_no_poly = 0
    skipped_no_signal = 0

    for w in windows:
        ws = w["window_start"]
        we = w["window_end"]
        title = w["title"]

        # BTC price at window start
        btc_open = w["open_price"]  # from shadow_tape (most accurate)

        # BTC price at T+entry_delay
        check_time = ws + entry_delay
        btc_at_check = interp_btc_price(check_time, source="1m")
        if btc_at_check is None:
            btc_at_check = interp_btc_price(check_time, source="signal")
        if btc_at_check is None:
            skipped_no_price += 1
            continue

        # Calculate BTC return in bps
        btc_return_bps = (btc_at_check - btc_open) / btc_open * 10000

        # Check threshold
        if abs(btc_return_bps) < threshold_bps:
            skipped_no_signal += 1
            continue

        # Direction: if BTC went up, lean UP; if down, lean DOWN
        lean_direction = "UP" if btc_return_bps > 0 else "DOWN"

        # Get Poly mid prices at check_time
        up_mid, dn_mid = get_poly_mid_at(title, check_time)

        # If signal_tape doesn't have it, try OB tape
        if up_mid is None:
            window_end_ts = int(we)
            target_tte = int(we - check_time)
            up_mid, dn_mid = get_ob_mid_at(window_end_ts, target_tte)

        if up_mid is None or dn_mid is None:
            skipped_no_poly += 1
            continue

        # Sanity: skip if mid prices are extreme (market already decided)
        if up_mid < 0.05 or dn_mid < 0.05 or up_mid > 0.95 or dn_mid > 0.95:
            skipped_no_poly += 1
            continue

        # Calculate position sizes
        # lean_ratio:1 split of total_cost
        # lean gets lean_ratio/(lean_ratio+1) of total, hedge gets 1/(lean_ratio+1)
        lean_frac = lean_ratio / (lean_ratio + 1)
        hedge_frac = 1 / (lean_ratio + 1)
        lean_cost = total_cost * lean_frac
        hedge_cost = total_cost * hedge_frac

        if lean_direction == "UP":
            lean_price = up_mid
            hedge_price = dn_mid
        else:
            lean_price = dn_mid
            hedge_price = up_mid

        # Buy shares: cost / price
        lean_shares = lean_cost / lean_price
        hedge_shares = hedge_cost / hedge_price

        # Outcome
        result = w["result"]
        if result == lean_direction:
            # Lean side wins
            payout = lean_shares * 1.0
        else:
            # Hedge side wins
            payout = hedge_shares * 1.0

        pnl = payout - total_cost

        trades.append({
            "title": title,
            "window_start": ws,
            "btc_open": btc_open,
            "btc_at_check": btc_at_check,
            "btc_return_bps": btc_return_bps,
            "lean_direction": lean_direction,
            "up_mid": up_mid,
            "dn_mid": dn_mid,
            "lean_price": lean_price,
            "hedge_price": hedge_price,
            "lean_cost": lean_cost,
            "hedge_cost": hedge_cost,
            "lean_shares": lean_shares,
            "hedge_shares": hedge_shares,
            "total_cost": total_cost,
            "payout": payout,
            "pnl": pnl,
            "result": result,
            "won_lean": result == lean_direction,
        })

    if verbose:
        print(f"  Total windows: {len(windows)}")
        print(f"  Skipped (no BTC price): {skipped_no_price}")
        print(f"  Skipped (below threshold): {skipped_no_signal}")
        print(f"  Skipped (no Poly OB): {skipped_no_poly}")
        print(f"  Trades executed: {len(trades)}")

    return trades


trades = simulate_w4(windows)

# ── STEP 4: Equity Curve ──
print()
print("=" * 70)
print("STEP 4: Equity curve simulation")
print("=" * 70)

STARTING_BANKROLL = 253.0


def build_equity_curve(trades, bankroll=253.0, compound=True):
    """Build equity curve from trades."""
    curve = [bankroll]
    cumulative_pnl = 0
    max_bankroll = bankroll
    max_drawdown = 0
    daily_pnl = defaultdict(float)
    win_streak = 0
    lose_streak = 0
    max_win_streak = 0
    max_lose_streak = 0

    for t in trades:
        pnl = t["pnl"]
        cumulative_pnl += pnl

        if compound:
            # Scale position by current bankroll / starting bankroll
            scale = bankroll / STARTING_BANKROLL
            scaled_pnl = pnl * scale
            bankroll += scaled_pnl
        else:
            bankroll += pnl

        curve.append(bankroll)

        if bankroll > max_bankroll:
            max_bankroll = bankroll
        dd = (max_bankroll - bankroll) / max_bankroll
        if dd > max_drawdown:
            max_drawdown = dd

        # Daily tracking
        day = datetime.fromtimestamp(t["window_start"], tz=HKT).strftime("%Y-%m-%d")
        daily_pnl[day] += pnl

        # Streaks
        if t["won_lean"]:
            win_streak += 1
            lose_streak = 0
        else:
            lose_streak += 1
            win_streak = 0
        max_win_streak = max(max_win_streak, win_streak)
        max_lose_streak = max(max_lose_streak, lose_streak)

    return {
        "curve": curve,
        "final_bankroll": bankroll,
        "cumulative_pnl": cumulative_pnl,
        "max_drawdown": max_drawdown,
        "daily_pnl": dict(daily_pnl),
        "max_win_streak": max_win_streak,
        "max_lose_streak": max_lose_streak,
    }


eq = build_equity_curve(trades)

print(f"  Starting bankroll: ${STARTING_BANKROLL:.2f}")
print(f"  Final bankroll: ${eq['final_bankroll']:.2f}")
print(f"  Cumulative PnL (unscaled): ${eq['cumulative_pnl']:.2f}")
print(f"  Max drawdown: {eq['max_drawdown']*100:.2f}%")
print(f"  Max win streak: {eq['max_win_streak']}")
print(f"  Max lose streak: {eq['max_lose_streak']}")

# ── STEP 5: Compare to Actual v15 Results ──
print()
print("=" * 70)
print("STEP 5: Compare to actual v15 results")
print("=" * 70)

mm_trades = load_jsonl(MM_PATH)
print(f"  Total mm_trades: {len(mm_trades)}")

# Our actual performance
actual_total_pnl = sum(t.get("pnl", 0) for t in mm_trades)
actual_wins = sum(1 for t in mm_trades if t.get("pnl", 0) > 0)
actual_wr = actual_wins / len(mm_trades) * 100 if mm_trades else 0
print(f"  Actual v15 total PnL: ${actual_total_pnl:.2f}")
print(f"  Actual v15 win rate: {actual_wr:.1f}%")
print(f"  Actual v15 trades: {len(mm_trades)}")

# Time overlap
if trades:
    sim_start = min(t["window_start"] for t in trades)
    sim_end = max(t["window_start"] for t in trades)
    print(f"  Sim period: {datetime.fromtimestamp(sim_start, tz=HKT).strftime('%Y-%m-%d %H:%M')} to {datetime.fromtimestamp(sim_end, tz=HKT).strftime('%Y-%m-%d %H:%M')}")

# ── STEP 6: Statistics ──
print()
print("=" * 70)
print("STEP 6: Detailed statistics")
print("=" * 70)

if trades:
    n = len(trades)
    wins = sum(1 for t in trades if t["won_lean"])
    wr = wins / n * 100
    pnls = [t["pnl"] for t in trades]
    avg_pnl = statistics.mean(pnls)
    median_pnl = statistics.median(pnls)
    std_pnl = statistics.stdev(pnls) if n > 1 else 0

    # Sharpe (annualized assuming ~96 trades/day for 15M windows running 24h)
    # Actually use per-trade Sharpe then scale
    sharpe_per_trade = avg_pnl / std_pnl if std_pnl > 0 else 0
    trades_per_day = n / max(1, len(eq["daily_pnl"]))
    sharpe_daily = sharpe_per_trade * math.sqrt(trades_per_day)
    sharpe_annual = sharpe_daily * math.sqrt(365)

    # Profit factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Expectancy per $1 risked
    avg_win = statistics.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
    avg_loss = statistics.mean([p for p in pnls if p < 0]) if any(p < 0 for p in pnls) else 0

    # Daily stats
    daily_vals = list(eq["daily_pnl"].values())
    best_day = max(daily_vals) if daily_vals else 0
    worst_day = min(daily_vals) if daily_vals else 0
    best_day_name = max(eq["daily_pnl"], key=eq["daily_pnl"].get) if eq["daily_pnl"] else "N/A"
    worst_day_name = min(eq["daily_pnl"], key=eq["daily_pnl"].get) if eq["daily_pnl"] else "N/A"

    print(f"  Total trades: {n}")
    print(f"  Win rate (lean wins): {wr:.1f}%")
    print(f"  Average PnL/trade: ${avg_pnl:.4f}")
    print(f"  Median PnL/trade: ${median_pnl:.4f}")
    print(f"  Std dev PnL: ${std_pnl:.4f}")
    print(f"  Sharpe (per-trade): {sharpe_per_trade:.3f}")
    print(f"  Sharpe (annualized): {sharpe_annual:.2f}")
    print(f"  Profit factor: {profit_factor:.2f}")
    print(f"  Average win: ${avg_win:.4f}")
    print(f"  Average loss: ${avg_loss:.4f}")
    print(f"  Max drawdown: {eq['max_drawdown']*100:.2f}%")
    print(f"  Max win streak: {eq['max_win_streak']}")
    print(f"  Max lose streak: {eq['max_lose_streak']}")
    print(f"  Best day: {best_day_name} (${best_day:.2f})")
    print(f"  Worst day: {worst_day_name} (${worst_day:.2f})")
    print(f"  Trades/day avg: {trades_per_day:.1f}")
else:
    print("  No trades to analyze!")

# ── STEP 7: Sensitivity Analysis ──
print()
print("=" * 70)
print("STEP 7: Sensitivity analysis")
print("=" * 70)

entry_delays = [240, 300, 360, 420]
thresholds = [3, 5, 7, 10]
lean_ratios = [1.5, 2.0, 3.0, 100.0]  # 100 = effectively all-in lean
lean_labels = ["1.5:1", "2:1", "3:1", "All-in"]

results_grid = []
best_sharpe = -999
best_config = None

print(f"\n  {'Delay':>6} {'Thresh':>7} {'Ratio':>8} {'Trades':>7} {'WR%':>6} {'AvgPnL':>8} {'TotPnL':>8} {'Sharpe':>7} {'MaxDD':>7}")
print("  " + "-" * 75)

for delay in entry_delays:
    for thresh in thresholds:
        for lr, lr_label in zip(lean_ratios, lean_labels):
            t_list = simulate_w4(windows, entry_delay=delay, threshold_bps=thresh, lean_ratio=lr, verbose=False)

            if len(t_list) < 5:
                continue

            pnl_list = [t["pnl"] for t in t_list]
            n_t = len(t_list)
            wr_t = sum(1 for t in t_list if t["won_lean"]) / n_t * 100
            avg_t = statistics.mean(pnl_list)
            tot_t = sum(pnl_list)
            std_t = statistics.stdev(pnl_list) if n_t > 1 else 0.001
            sharpe_t = avg_t / std_t if std_t > 0 else 0

            eq_t = build_equity_curve(t_list)
            dd_t = eq_t["max_drawdown"] * 100

            row = {
                "delay": delay,
                "threshold": thresh,
                "lean_ratio": lr,
                "lean_label": lr_label,
                "trades": n_t,
                "wr": wr_t,
                "avg_pnl": avg_t,
                "total_pnl": tot_t,
                "sharpe": sharpe_t,
                "max_dd": dd_t,
                "final_bankroll": eq_t["final_bankroll"],
            }
            results_grid.append(row)

            print(f"  {delay:>5}s {thresh:>5}bp {lr_label:>8} {n_t:>7} {wr_t:>5.1f}% ${avg_t:>7.4f} ${tot_t:>7.2f} {sharpe_t:>7.3f} {dd_t:>6.2f}%")

            if sharpe_t > best_sharpe:
                best_sharpe = sharpe_t
                best_config = row

print()
if best_config:
    print(f"  ** BEST CONFIG (by Sharpe): delay={best_config['delay']}s, threshold={best_config['threshold']}bp, ratio={best_config['lean_label']}")
    print(f"     Sharpe={best_config['sharpe']:.3f}, WR={best_config['wr']:.1f}%, AvgPnL=${best_config['avg_pnl']:.4f}, Trades={best_config['trades']}, MaxDD={best_config['max_dd']:.2f}%")
    print(f"     Final bankroll: ${best_config['final_bankroll']:.2f} (from $253)")

# Also find best by total PnL
best_pnl_config = max(results_grid, key=lambda r: r["total_pnl"]) if results_grid else None
if best_pnl_config:
    print(f"  ** BEST CONFIG (by Total PnL): delay={best_pnl_config['delay']}s, threshold={best_pnl_config['threshold']}bp, ratio={best_pnl_config['lean_label']}")
    print(f"     Sharpe={best_pnl_config['sharpe']:.3f}, WR={best_pnl_config['wr']:.1f}%, TotPnL=${best_pnl_config['total_pnl']:.2f}, Trades={best_pnl_config['trades']}")

# ── GENERATE REPORT ──
print()
print("=" * 70)
print("Generating report...")
print("=" * 70)

report_lines = []
report_lines.append("# Agent D: W4 Strategy Historical Simulation Report")
report_lines.append(f"**Generated:** {datetime.now(tz=HKT).strftime('%Y-%m-%d %H:%M HKT')}")
report_lines.append(f"**Data period:** {datetime.fromtimestamp(windows[0]['window_start'], tz=HKT).strftime('%Y-%m-%d %H:%M')} to {datetime.fromtimestamp(windows[-1]['window_start'], tz=HKT).strftime('%Y-%m-%d %H:%M')} HKT")
report_lines.append("")
report_lines.append("---")
report_lines.append("")

# Section 1: Data Summary
report_lines.append("## 1. Data Summary")
report_lines.append("")
report_lines.append("| Source | Entries | Used |")
report_lines.append("|--------|---------|------|")
report_lines.append(f"| shadow_tape (BTC 15M) | {len(btc_shadows)} | Ground truth outcomes |")
report_lines.append(f"| signal_tape | {len(signal_all)} | BTC prices + Poly mids |")
report_lines.append(f"| poly_ob_tape (BTC) | {len(btc_ob)} | Fallback OB data |")
report_lines.append(f"| btc_1m_7days | {len(btc_1m)} | Fine BTC price resolution |")
report_lines.append(f"| mm_trades | {len(mm_trades)} | Actual v15 comparison |")
report_lines.append("")
report_lines.append(f"**Total BTC 15M windows:** {len(windows)}")
report_lines.append(f"**UP/DOWN split:** {up_count} UP / {len(windows)-up_count} DOWN ({up_count/len(windows)*100:.1f}% UP)")
report_lines.append("")

# Section 2: Default W4 Results
report_lines.append("## 2. W4 Strategy Results (Default: 300s delay, 5bp threshold, 2:1 ratio)")
report_lines.append("")
if trades:
    report_lines.append(f"| Metric | Value |")
    report_lines.append(f"|--------|-------|")
    report_lines.append(f"| Total trades | {n} |")
    report_lines.append(f"| Win rate (lean wins) | {wr:.1f}% |")
    report_lines.append(f"| Average PnL/trade | ${avg_pnl:.4f} |")
    report_lines.append(f"| Median PnL/trade | ${median_pnl:.4f} |")
    report_lines.append(f"| Std dev PnL | ${std_pnl:.4f} |")
    report_lines.append(f"| Total PnL (unscaled) | ${eq['cumulative_pnl']:.2f} |")
    report_lines.append(f"| Sharpe (per-trade) | {sharpe_per_trade:.3f} |")
    report_lines.append(f"| Sharpe (annualized) | {sharpe_annual:.2f} |")
    report_lines.append(f"| Profit factor | {profit_factor:.2f} |")
    report_lines.append(f"| Average win | ${avg_win:.4f} |")
    report_lines.append(f"| Average loss | ${avg_loss:.4f} |")
    report_lines.append(f"| Max drawdown | {eq['max_drawdown']*100:.2f}% |")
    report_lines.append(f"| Max win streak | {eq['max_win_streak']} |")
    report_lines.append(f"| Max lose streak | {eq['max_lose_streak']} |")
    report_lines.append(f"| Best day | {best_day_name} (${best_day:.2f}) |")
    report_lines.append(f"| Worst day | {worst_day_name} (${worst_day:.2f}) |")
    report_lines.append(f"| Trades/day | {trades_per_day:.1f} |")
    report_lines.append("")

# Section 3: Equity Curve
report_lines.append("## 3. Equity Curve")
report_lines.append("")
report_lines.append(f"Starting bankroll: **${STARTING_BANKROLL:.2f}**")
report_lines.append(f"Final bankroll: **${eq['final_bankroll']:.2f}**")
report_lines.append(f"Return: **{(eq['final_bankroll']/STARTING_BANKROLL - 1)*100:.2f}%**")
report_lines.append("")

# ASCII equity curve
if trades:
    curve = eq["curve"]
    min_c = min(curve)
    max_c = max(curve)
    width = 60
    height = 15
    report_lines.append("```")
    report_lines.append(f"Equity Curve (${min_c:.0f} to ${max_c:.0f})")
    for row in range(height, -1, -1):
        threshold = min_c + (max_c - min_c) * row / height
        line = ""
        step = max(1, len(curve) // width)
        for i in range(0, min(len(curve), width * step), step):
            if curve[i] >= threshold:
                line += "#"
            else:
                line += " "
        price_label = f"${threshold:>7.0f}" if row % 3 == 0 else "        "
        report_lines.append(f"{price_label} |{line}")
    report_lines.append(f"         +{'-' * width}")
    report_lines.append(f"          Trade 1{' ' * (width - 20)}Trade {len(trades)}")
    report_lines.append("```")
    report_lines.append("")

# Section 4: Daily PnL
report_lines.append("## 4. Daily PnL Breakdown")
report_lines.append("")
report_lines.append("| Date | PnL | Trades |")
report_lines.append("|------|-----|--------|")
for day in sorted(eq["daily_pnl"].keys()):
    day_trades = sum(1 for t in trades if datetime.fromtimestamp(t["window_start"], tz=HKT).strftime("%Y-%m-%d") == day)
    pnl_val = eq["daily_pnl"][day]
    marker = "+" if pnl_val >= 0 else ""
    report_lines.append(f"| {day} | {marker}${pnl_val:.2f} | {day_trades} |")
report_lines.append("")

# Section 5: Comparison to v15
report_lines.append("## 5. Comparison: W4 Simulated vs Actual v15")
report_lines.append("")
report_lines.append("| Metric | W4 Simulated | Actual v15 |")
report_lines.append("|--------|--------------|------------|")
report_lines.append(f"| Total trades | {len(trades)} | {len(mm_trades)} |")
report_lines.append(f"| Win rate | {wr:.1f}% | {actual_wr:.1f}% |")
report_lines.append(f"| Total PnL | ${eq['cumulative_pnl']:.2f} | ${actual_total_pnl:.2f} |")
if trades:
    report_lines.append(f"| Avg PnL/trade | ${avg_pnl:.4f} | ${actual_total_pnl/len(mm_trades):.4f} |")
report_lines.append(f"| Max drawdown | {eq['max_drawdown']*100:.2f}% | N/A |")
report_lines.append(f"| Final bankroll | ${eq['final_bankroll']:.2f} | N/A |")
report_lines.append("")

report_lines.append("> **Note:** W4 only trades BTC when momentum signal fires (selective).")
report_lines.append("> v15 trades more broadly across coins and conditions.")
report_lines.append("")

# Section 6: Sensitivity
report_lines.append("## 6. Sensitivity Analysis")
report_lines.append("")
report_lines.append("### Full Grid (sorted by Sharpe)")
report_lines.append("")
report_lines.append("| Delay | Threshold | Ratio | Trades | WR% | AvgPnL | TotPnL | Sharpe | MaxDD% |")
report_lines.append("|-------|-----------|-------|--------|-----|--------|--------|--------|--------|")
for r in sorted(results_grid, key=lambda x: -x["sharpe"])[:30]:
    report_lines.append(
        f"| {r['delay']}s | {r['threshold']}bp | {r['lean_label']} | {r['trades']} | {r['wr']:.1f}% | ${r['avg_pnl']:.4f} | ${r['total_pnl']:.2f} | {r['sharpe']:.3f} | {r['max_dd']:.1f}% |"
    )
report_lines.append("")

if best_config:
    report_lines.append("### Best Configuration (by Sharpe)")
    report_lines.append("")
    report_lines.append(f"- **Delay:** {best_config['delay']}s")
    report_lines.append(f"- **Threshold:** {best_config['threshold']}bp")
    report_lines.append(f"- **Lean ratio:** {best_config['lean_label']}")
    report_lines.append(f"- **Sharpe:** {best_config['sharpe']:.3f}")
    report_lines.append(f"- **Win rate:** {best_config['wr']:.1f}%")
    report_lines.append(f"- **Avg PnL:** ${best_config['avg_pnl']:.4f}")
    report_lines.append(f"- **Total PnL:** ${best_config['total_pnl']:.2f}")
    report_lines.append(f"- **Trades:** {best_config['trades']}")
    report_lines.append(f"- **Max drawdown:** {best_config['max_dd']:.2f}%")
    report_lines.append(f"- **Final bankroll:** ${best_config['final_bankroll']:.2f}")
    report_lines.append("")

if best_pnl_config and best_pnl_config != best_config:
    report_lines.append("### Best Configuration (by Total PnL)")
    report_lines.append("")
    report_lines.append(f"- **Delay:** {best_pnl_config['delay']}s")
    report_lines.append(f"- **Threshold:** {best_pnl_config['threshold']}bp")
    report_lines.append(f"- **Lean ratio:** {best_pnl_config['lean_label']}")
    report_lines.append(f"- **Sharpe:** {best_pnl_config['sharpe']:.3f}")
    report_lines.append(f"- **Total PnL:** ${best_pnl_config['total_pnl']:.2f}")
    report_lines.append(f"- **Trades:** {best_pnl_config['trades']}")
    report_lines.append("")

# Section 7: Trade-by-trade log (first 20)
report_lines.append("## 7. Sample Trades (first 20)")
report_lines.append("")
report_lines.append("| # | Window | BTC Move | Lean | UP Mid | DN Mid | PnL | Result |")
report_lines.append("|---|--------|----------|------|--------|--------|-----|--------|")
for i, t in enumerate(trades[:20]):
    win_mark = "W" if t["won_lean"] else "L"
    ts_str = datetime.fromtimestamp(t["window_start"], tz=HKT).strftime("%m/%d %H:%M")
    report_lines.append(
        f"| {i+1} | {ts_str} | {t['btc_return_bps']:+.1f}bp | {t['lean_direction']} | {t['up_mid']:.3f} | {t['dn_mid']:.3f} | ${t['pnl']:+.4f} | {win_mark} |"
    )
report_lines.append("")

# Section 8: Key Findings
report_lines.append("## 8. Key Findings")
report_lines.append("")

if trades:
    # Analyze by direction
    up_trades = [t for t in trades if t["lean_direction"] == "UP"]
    dn_trades = [t for t in trades if t["lean_direction"] == "DOWN"]
    up_wr = sum(1 for t in up_trades if t["won_lean"]) / len(up_trades) * 100 if up_trades else 0
    dn_wr = sum(1 for t in dn_trades if t["won_lean"]) / len(dn_trades) * 100 if dn_trades else 0

    report_lines.append(f"- **Directional bias:** UP lean WR={up_wr:.1f}% ({len(up_trades)} trades), DOWN lean WR={dn_wr:.1f}% ({len(dn_trades)} trades)")

    # Analyze by signal strength
    strong_trades = [t for t in trades if abs(t["btc_return_bps"]) > 10]
    weak_trades = [t for t in trades if abs(t["btc_return_bps"]) <= 10]
    strong_wr = sum(1 for t in strong_trades if t["won_lean"]) / len(strong_trades) * 100 if strong_trades else 0
    weak_wr = sum(1 for t in weak_trades if t["won_lean"]) / len(weak_trades) * 100 if weak_trades else 0
    report_lines.append(f"- **Signal strength:** Strong (>10bp) WR={strong_wr:.1f}% ({len(strong_trades)} trades), Weak (5-10bp) WR={weak_wr:.1f}% ({len(weak_trades)} trades)")

    # Analyze by time of day (ET)
    morning = [t for t in trades if 9 <= datetime.fromtimestamp(t["window_start"], tz=EDT).hour < 16]
    evening = [t for t in trades if not (9 <= datetime.fromtimestamp(t["window_start"], tz=EDT).hour < 16)]
    morning_wr = sum(1 for t in morning if t["won_lean"]) / len(morning) * 100 if morning else 0
    evening_wr = sum(1 for t in evening if t["won_lean"]) / len(evening) * 100 if evening else 0
    report_lines.append(f"- **Time of day (ET):** Market hours WR={morning_wr:.1f}% ({len(morning)}), Off-hours WR={evening_wr:.1f}% ({len(evening)})")

    # Is equity curve linear or exponential?
    if len(eq["curve"]) > 10:
        mid_point = len(eq["curve"]) // 2
        first_half_gain = eq["curve"][mid_point] - eq["curve"][0]
        second_half_gain = eq["curve"][-1] - eq["curve"][mid_point]
        if first_half_gain != 0:
            ratio = second_half_gain / first_half_gain
            shape = "accelerating (exponential)" if ratio > 1.3 else "decelerating" if ratio < 0.7 else "roughly linear"
        else:
            shape = "flat first half"
        report_lines.append(f"- **Equity curve shape:** {shape} (2nd half / 1st half gain ratio: {ratio:.2f})")

    # Edge analysis
    if avg_pnl > 0:
        edge_per_dollar = avg_pnl / TOTAL_COST_DEFAULT * 100
        report_lines.append(f"- **Edge per dollar risked:** {edge_per_dollar:.2f}%")
    else:
        report_lines.append(f"- **Edge per dollar risked:** NEGATIVE ({avg_pnl/TOTAL_COST_DEFAULT*100:.2f}%)")

report_lines.append("")

# Write report
with open(REPORT_PATH, "w") as f:
    f.write("\n".join(report_lines))

print(f"Report saved to: {REPORT_PATH}")
print()
print("=" * 70)
print("DONE")
print("=" * 70)
