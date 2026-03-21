#!/usr/bin/env python3
"""
Fill Probability Model for Polymarket 15-Minute BTC Binary Options
==================================================================
反推 entry price / fill rate / volatility / time 之間嘅關係，
搵出最優動態 bidding curve。

Design decisions:
- 只用 stdlib（json, math, statistics, collections, datetime, re）
- signal_tape.jsonl 一次讀入記憶體（~6K lines, <20MB）夠快
- 用 cid 分組 market，每個 cid = 一個 15min window
- σ_poly 定義：每個 market 內 up_mid 連續差分嘅 std
- Window start/end 由 title 解析，唔係用第一個 snapshot timestamp
  （因為 next-window cid 會提早出現喺 signal_tape）
- Fill simulation: 只考慮 window 期間嘅 snapshots（window_start → window_end）
"""

import json
import math
import os
import re
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ── Paths ──────────────────────────────────────────────────────────
BASE = "/Users/wai/projects/axc-trading/polymarket"
SIGNAL_TAPE = os.path.join(BASE, "logs", "signal_tape.jsonl")
ORDER_LOG = os.path.join(BASE, "logs", "mm_order_log.jsonl")
RESOLUTIONS = os.path.join(BASE, "logs", "btc_15m_resolutions.jsonl")
OUTPUT_JSON = os.path.join(BASE, "analysis", "fill_probability_results.json")

# ── Constants ──────────────────────────────────────────────────────
BID_PRICES = [0.20, 0.25, 0.30, 0.35, 0.37, 0.40, 0.42, 0.45, 0.50]
ENTRY_MINUTES = [1, 3, 5, 7, 9]
BASE_WR = 0.60  # conservative win rate assumption
MIN_RECORDS_IN_WINDOW = 5  # 至少要 5 個 in-window snapshot 先夠分析

# ── Timezone ───────────────────────────────────────────────────────
HKT = timezone(timedelta(hours=8))
ET = timezone(timedelta(hours=-4))  # EDT (March 2026 = DST active)


# ══════════════════════════════════════════════════════════════════
# Utility
# ══════════════════════════════════════════════════════════════════

def parse_ts(ts_str):
    """Parse ISO timestamp to datetime."""
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        if "+" in ts_str[10:]:
            base, tz_part = ts_str.rsplit("+", 1)
            dt = datetime.fromisoformat(base)
            h, m = tz_part.split(":")
            dt = dt.replace(tzinfo=timezone(timedelta(hours=int(h), minutes=int(m))))
            return dt
        raise


def parse_window_from_title(title):
    """
    Extract window start HKT from title like:
    'Bitcoin Up or Down - March 20, 3:15AM-3:30AM ET'
    Returns: (window_start_hkt, window_end_hkt) or (None, None)
    """
    m = re.search(
        r'(\w+)\s+(\d+),\s*(\d+):(\d+)(AM|PM)-(\d+):(\d+)(AM|PM)\s+ET',
        title
    )
    if not m:
        return None, None

    month_str = m.group(1)
    day = int(m.group(2))
    sh, smi = int(m.group(3)), int(m.group(4))
    s_ampm = m.group(5)
    eh, emi = int(m.group(6)), int(m.group(7))
    e_ampm = m.group(8)

    # Convert to 24h
    if s_ampm == "PM" and sh != 12:
        sh += 12
    if s_ampm == "AM" and sh == 12:
        sh = 0

    # Parse month
    months = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }
    month_num = months.get(month_str, 3)

    # Build ET datetime, convert to HKT
    ws_et = datetime(2026, month_num, day, sh, smi, tzinfo=ET)
    ws_hkt = ws_et.astimezone(HKT)
    we_hkt = ws_hkt + timedelta(minutes=15)

    return ws_hkt, we_hkt


def safe_std(values):
    """Standard deviation, returns 0 if < 2 values."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def percentile(sorted_list, p):
    """p in [0,100]. Linear interpolation."""
    if not sorted_list:
        return 0.0
    n = len(sorted_list)
    k = (p / 100.0) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_list[int(k)]
    return sorted_list[f] * (c - k) + sorted_list[c] * (k - f)


def compute_correlation(xs, ys):
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)
    sx = safe_std(xs)
    sy = safe_std(ys)
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def fmt_pct(v, width=7):
    return f"{v*100:>{width}.1f}%"


def fmt_float(v, width=8, decimals=4):
    return f"{v:>{width}.{decimals}f}"


def print_header(title):
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def print_subheader(title):
    print(f"\n--- {title} ---")


# ══════════════════════════════════════════════════════════════════
# PHASE 1: Load & Group Data
# ══════════════════════════════════════════════════════════════════

def load_signal_tape():
    """
    讀 signal_tape.jsonl，只提取 BTC market 數據。
    Returns: dict[cid] -> {
        "title": str,
        "window_start": datetime,
        "window_end": datetime,
        "snapshots": list of {ts, up_mid, dn_mid, btc, ob_imbalance}
    }
    """
    raw = defaultdict(lambda: {"title": None, "snapshots": []})

    with open(SIGNAL_TAPE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            ts = parse_ts(record["ts"])
            btc_data = record.get("btc", {})
            if not btc_data or "median" not in btc_data:
                continue
            btc_price = btc_data["median"]

            for poly in record.get("poly", []):
                if poly["coin"] != "BTC":
                    continue
                up_mid = poly.get("up_mid")
                dn_mid = poly.get("dn_mid")
                if up_mid is None or dn_mid is None:
                    continue

                cid = poly["cid"]
                if raw[cid]["title"] is None:
                    raw[cid]["title"] = poly.get("title", "")

                raw[cid]["snapshots"].append({
                    "ts": ts,
                    "up_mid": up_mid,
                    "dn_mid": dn_mid,
                    "btc": btc_price,
                    "ob_imbalance": poly.get("ob_imbalance", 0),
                })

    # Parse window times and filter to in-window snapshots only
    markets = {}
    skipped_parse = 0
    skipped_few = 0

    for cid, data in raw.items():
        ws, we = parse_window_from_title(data["title"])
        if ws is None:
            skipped_parse += 1
            continue

        # Sort snapshots by ts
        all_snaps = sorted(data["snapshots"], key=lambda x: x["ts"])

        # Filter to only snapshots within the window [ws, we]
        in_window = [s for s in all_snaps if ws <= s["ts"] <= we]

        if len(in_window) < MIN_RECORDS_IN_WINDOW:
            skipped_few += 1
            continue

        markets[cid] = {
            "title": data["title"],
            "window_start": ws,
            "window_end": we,
            "snapshots": in_window,
            "all_snapshots": all_snaps,  # keep all for reference
        }

    return markets, skipped_parse, skipped_few


def load_order_log():
    """讀 mm_order_log.jsonl。注意 cid 係 truncated。"""
    orders = []
    with open(ORDER_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            orders.append(json.loads(line))
    return orders


# ══════════════════════════════════════════════════════════════════
# PHASE 2: Compute Poly Volatility (σ_poly)
# ══════════════════════════════════════════════════════════════════

def compute_market_volatility(markets):
    """
    每個 market 計算（只用 in-window snapshots）：
    - σ_poly: std of consecutive up_mid changes (per ~20s tick)
    - σ_poly_15m: std of up_mid over window
    - range_up_mid: max - min of up_mid
    - σ_btc: std of consecutive BTC price changes
    """
    vol_stats = {}

    for cid, mkt in markets.items():
        snaps = mkt["snapshots"]
        up_mids = [s["up_mid"] for s in snaps]
        dn_mids = [s["dn_mid"] for s in snaps]
        btc_prices = [s["btc"] for s in snaps]

        delta_up = [up_mids[i+1] - up_mids[i] for i in range(len(up_mids)-1)]
        delta_btc_pct = [
            (btc_prices[i+1] - btc_prices[i]) / btc_prices[i]
            for i in range(len(btc_prices)-1)
            if btc_prices[i] > 0
        ]
        delta_btc = [btc_prices[i+1] - btc_prices[i] for i in range(len(btc_prices)-1)]

        sigma_poly = safe_std(delta_up)
        sigma_poly_15m = safe_std(up_mids)
        sigma_btc = safe_std(delta_btc)
        sigma_btc_pct = safe_std(delta_btc_pct) if delta_btc_pct else 0.0
        range_up = max(up_mids) - min(up_mids)
        range_dn = max(dn_mids) - min(dn_mids)
        duration_s = (snaps[-1]["ts"] - snaps[0]["ts"]).total_seconds()

        vol_stats[cid] = {
            "n_snapshots": len(snaps),
            "duration_s": duration_s,
            "sigma_poly": sigma_poly,
            "sigma_poly_15m": sigma_poly_15m,
            "range_up_mid": range_up,
            "range_dn_mid": range_dn,
            "sigma_btc": sigma_btc,
            "sigma_btc_pct": sigma_btc_pct,
            "min_up_mid": min(up_mids),
            "max_up_mid": max(up_mids),
        }

    return vol_stats


def print_volatility_stats(vol_stats):
    print_header("Part 1: Polymarket Mid Volatility (σ_poly)")

    sigmas = sorted([v["sigma_poly"] for v in vol_stats.values()])
    ranges = sorted([v["range_up_mid"] for v in vol_stats.values()])
    sigma_15m = sorted([v["sigma_poly_15m"] for v in vol_stats.values()])
    sigma_btc_pct = sorted([v["sigma_btc_pct"] for v in vol_stats.values()])

    print(f"\n  分析咗 {len(vol_stats)} 個 BTC 15-min markets（只計 in-window snapshots）")

    print_subheader("σ_poly (std of Δup_mid per ~20s tick)")
    print(f"  Mean:   {fmt_float(statistics.mean(sigmas))}")
    print(f"  Median: {fmt_float(statistics.median(sigmas))}")
    print(f"  P25:    {fmt_float(percentile(sigmas, 25))}")
    print(f"  P75:    {fmt_float(percentile(sigmas, 75))}")
    print(f"  Min:    {fmt_float(min(sigmas))}")
    print(f"  Max:    {fmt_float(max(sigmas))}")

    print_subheader("σ_poly_15m (std of up_mid over full window)")
    print(f"  Mean:   {fmt_float(statistics.mean(sigma_15m))}")
    print(f"  Median: {fmt_float(statistics.median(sigma_15m))}")
    print(f"  P25:    {fmt_float(percentile(sigma_15m, 25))}")
    print(f"  P75:    {fmt_float(percentile(sigma_15m, 75))}")

    print_subheader("up_mid Range (max - min) per market")
    print(f"  Mean:   {fmt_float(statistics.mean(ranges))}")
    print(f"  Median: {fmt_float(statistics.median(ranges))}")
    print(f"  P25:    {fmt_float(percentile(ranges, 25))}")
    print(f"  P75:    {fmt_float(percentile(ranges, 75))}")

    print_subheader("σ_btc_pct (std of BTC % change per ~20s)")
    print(f"  Mean:   {fmt_float(statistics.mean(sigma_btc_pct), decimals=6)}")
    print(f"  Median: {fmt_float(statistics.median(sigma_btc_pct), decimals=6)}")

    # Correlation
    if len(vol_stats) >= 5:
        sp_list = [v["sigma_poly"] for v in vol_stats.values()]
        sb_list = [v["sigma_btc_pct"] for v in vol_stats.values()]
        corr = compute_correlation(sp_list, sb_list)
        print_subheader("σ_poly vs σ_btc_pct 相關性")
        print(f"  Pearson r: {corr:.4f}")
        if abs(corr) > 0.5:
            print("  → 強相關：BTC 波動大嘅時候，Poly mid 都跳得勁")
        elif abs(corr) > 0.3:
            print("  → 中等相關：有一定聯動")
        else:
            print("  → 弱相關：Poly mid 波動有自己嘅節奏")


# ══════════════════════════════════════════════════════════════════
# PHASE 3: Fill Probability Simulation
# ══════════════════════════════════════════════════════════════════

def assign_volatility_terciles(vol_stats):
    """分 low / medium / high volatility 三組。"""
    sorted_cids = sorted(vol_stats.keys(), key=lambda c: vol_stats[c]["sigma_poly"])
    n = len(sorted_cids)
    t1 = n // 3
    t2 = 2 * n // 3

    terciles = {}
    for i, cid in enumerate(sorted_cids):
        if i < t1:
            terciles[cid] = "low"
        elif i < t2:
            terciles[cid] = "medium"
        else:
            terciles[cid] = "high"

    sigmas_low = [vol_stats[c]["sigma_poly"] for c in sorted_cids[:t1]]
    sigmas_mid = [vol_stats[c]["sigma_poly"] for c in sorted_cids[t1:t2]]
    sigmas_hi = [vol_stats[c]["sigma_poly"] for c in sorted_cids[t2:]]

    print_subheader("Volatility Tercile 分界")
    if sigmas_low:
        print(f"  Low:    σ_poly ∈ [{min(sigmas_low):.5f}, {max(sigmas_low):.5f}]  (n={len(sigmas_low)})")
    if sigmas_mid:
        print(f"  Medium: σ_poly ∈ [{min(sigmas_mid):.5f}, {max(sigmas_mid):.5f}]  (n={len(sigmas_mid)})")
    if sigmas_hi:
        print(f"  High:   σ_poly ∈ [{min(sigmas_hi):.5f}, {max(sigmas_hi):.5f}]  (n={len(sigmas_hi)})")

    return terciles


def simulate_fills(markets, vol_stats, terciles, entry_minute=3):
    """
    對每個 market 同每個 bid price 模擬：
    - entry_minute = N minutes after WINDOW START (唔係第一個 snapshot)
    - 之後嘅 in-window snapshot 中，mid 有冇跌穿 bid?

    Returns: dict with fill probability results
    """
    results = {
        "up": {bp: {"filled": 0, "total": 0, "times": [],
                     "by_tercile": {"low": [0, 0], "medium": [0, 0], "high": [0, 0]}}
               for bp in BID_PRICES},
        "dn": {bp: {"filled": 0, "total": 0, "times": [],
                     "by_tercile": {"low": [0, 0], "medium": [0, 0], "high": [0, 0]}}
               for bp in BID_PRICES},
    }

    for cid, mkt in markets.items():
        if cid not in vol_stats:
            continue

        ws = mkt["window_start"]
        we = mkt["window_end"]
        snaps = mkt["snapshots"]
        tercile = terciles.get(cid, "medium")

        # Entry time = window_start + entry_minute minutes
        entry_ts = ws + timedelta(minutes=entry_minute)

        if entry_ts >= we:
            continue  # entry after window end

        # Find first snapshot at or after entry_ts
        entry_idx = None
        for i, s in enumerate(snaps):
            if s["ts"] >= entry_ts:
                entry_idx = i
                break

        if entry_idx is None:
            continue  # no snapshots after entry time

        # How many snapshots remain after entry?
        remaining = len(snaps) - entry_idx
        if remaining < 2:
            continue

        for bp in BID_PRICES:
            # UP token: fill when up_mid drops to <= bid
            filled_up = False
            fill_time_up = None
            for j in range(entry_idx, len(snaps)):
                if snaps[j]["up_mid"] <= bp:
                    filled_up = True
                    fill_time_up = (snaps[j]["ts"] - snaps[entry_idx]["ts"]).total_seconds()
                    break

            results["up"][bp]["total"] += 1
            results["up"][bp]["by_tercile"][tercile][1] += 1
            if filled_up:
                results["up"][bp]["filled"] += 1
                results["up"][bp]["by_tercile"][tercile][0] += 1
                if fill_time_up is not None:
                    results["up"][bp]["times"].append(fill_time_up)

            # DOWN token: fill when dn_mid drops to <= bid
            filled_dn = False
            fill_time_dn = None
            for j in range(entry_idx, len(snaps)):
                if snaps[j]["dn_mid"] <= bp:
                    filled_dn = True
                    fill_time_dn = (snaps[j]["ts"] - snaps[entry_idx]["ts"]).total_seconds()
                    break

            results["dn"][bp]["total"] += 1
            results["dn"][bp]["by_tercile"][tercile][1] += 1
            if filled_dn:
                results["dn"][bp]["filled"] += 1
                results["dn"][bp]["by_tercile"][tercile][0] += 1
                if fill_time_dn is not None:
                    results["dn"][bp]["times"].append(fill_time_dn)

    return results


def print_fill_probability(results, entry_minute=3):
    print_header(f"Part 2: Fill Probability Curve (entry at minute {entry_minute})")

    print_subheader("P(fill | bid) — 合併 UP + DOWN token")
    print(f"  {'Bid':>6}  {'P(fill)':>8}  {'Filled':>7}  {'Total':>7}  {'Avg t_fill':>11}")
    print(f"  {'─'*6}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*11}")

    for bp in BID_PRICES:
        filled = results["up"][bp]["filled"] + results["dn"][bp]["filled"]
        total = results["up"][bp]["total"] + results["dn"][bp]["total"]
        times = results["up"][bp]["times"] + results["dn"][bp]["times"]
        p_fill = filled / total if total > 0 else 0
        avg_time = statistics.mean(times) if times else 0
        print(f"  {bp:>6.2f}  {fmt_pct(p_fill)}  {filled:>7}  {total:>7}  {avg_time:>8.0f}s")

    for side, label in [("up", "UP"), ("dn", "DOWN")]:
        print_subheader(f"P(fill | bid) — {label} token only")
        print(f"  {'Bid':>6}  {'P(fill)':>8}  {'Filled':>7}  {'Total':>7}")
        print(f"  {'─'*6}  {'─'*8}  {'─'*7}  {'─'*7}")
        for bp in BID_PRICES:
            filled = results[side][bp]["filled"]
            total = results[side][bp]["total"]
            p_fill = filled / total if total > 0 else 0
            print(f"  {bp:>6.2f}  {fmt_pct(p_fill)}  {filled:>7}  {total:>7}")


def print_fill_by_volatility(results):
    print_subheader("P(fill | bid, σ_poly tercile) — 合併 UP + DOWN")
    print(f"  {'Bid':>6}  {'Low σ':>10}  {'Med σ':>10}  {'High σ':>10}  {'Hi-Lo':>8}")
    print(f"  {'─'*6}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*8}")

    for bp in BID_PRICES:
        row = {}
        for t in ["low", "medium", "high"]:
            f_up = results["up"][bp]["by_tercile"][t][0]
            n_up = results["up"][bp]["by_tercile"][t][1]
            f_dn = results["dn"][bp]["by_tercile"][t][0]
            n_dn = results["dn"][bp]["by_tercile"][t][1]
            total_f = f_up + f_dn
            total_n = n_up + n_dn
            row[t] = total_f / total_n if total_n > 0 else 0

        spread = row["high"] - row["low"]
        print(f"  {bp:>6.2f}  {fmt_pct(row['low'], 9)}  {fmt_pct(row['medium'], 9)}  {fmt_pct(row['high'], 9)}  {spread:>+7.1%}")


# ══════════════════════════════════════════════════════════════════
# PHASE 4: Optimal Bid Curve (EV maximisation)
# ══════════════════════════════════════════════════════════════════

def compute_optimal_bids(results, vol_stats, terciles):
    """
    EV(bid) = P(fill|bid) × (WR - bid)
    Token resolves $0 or $1 → Win profit = 1-bid, Loss = bid
    EV per filled = WR(1-bid) - (1-WR)bid = WR - bid
    """
    print_header("Part 3: Optimal Bid Curve")

    print(f"  假設 base_WR = {BASE_WR:.0%}")
    print(f"  EV(bid) = P(fill|bid) × (WR - bid)")
    print(f"  bid > WR → EV per fill 係負數，但 P(fill) 高可能 compensate")

    print_subheader("Overall EV curve")
    print(f"  {'Bid':>6}  {'P(fill)':>8}  {'EV/fill':>9}  {'EV':>9}  {'$EV/100':>9}")
    print(f"  {'─'*6}  {'─'*8}  {'─'*9}  {'─'*9}  {'─'*9}")

    best_ev = -999
    best_bid = 0

    for bp in BID_PRICES:
        filled = results["up"][bp]["filled"] + results["dn"][bp]["filled"]
        total = results["up"][bp]["total"] + results["dn"][bp]["total"]
        p_fill = filled / total if total > 0 else 0
        ev_per_fill = BASE_WR - bp
        ev = p_fill * ev_per_fill
        dollar_ev_100 = ev * 100

        marker = " ◀" if ev > best_ev else ""
        if ev > best_ev:
            best_ev = ev
            best_bid = bp
        print(f"  {bp:>6.2f}  {fmt_pct(p_fill)}  {ev_per_fill:>+9.4f}  {ev:>+9.4f}  ${dollar_ev_100:>+7.2f}{marker}")

    print(f"\n  → 最優 bid = {best_bid:.2f}  (EV = {best_ev:+.4f} per market)")

    # By tercile
    print_subheader("bid*(σ_poly) — 每個 volatility bucket 嘅最優 bid")
    optimal_by_tercile = {}

    for t in ["low", "medium", "high"]:
        best_ev_t = -999
        best_bid_t = 0
        print(f"\n  [{t.upper()} volatility] EV curve:")
        print(f"    {'Bid':>6}  {'P(fill)':>8}  {'EV':>9}")
        for bp in BID_PRICES:
            f_up = results["up"][bp]["by_tercile"][t][0]
            n_up = results["up"][bp]["by_tercile"][t][1]
            f_dn = results["dn"][bp]["by_tercile"][t][0]
            n_dn = results["dn"][bp]["by_tercile"][t][1]
            total_f = f_up + f_dn
            total_n = n_up + n_dn
            p_fill = total_f / total_n if total_n > 0 else 0
            ev = p_fill * (BASE_WR - bp)
            marker = " ◀" if ev > best_ev_t else ""
            if ev > best_ev_t:
                best_ev_t = ev
                best_bid_t = bp
            print(f"    {bp:>6.2f}  {fmt_pct(p_fill)}  {ev:>+9.4f}{marker}")
        optimal_by_tercile[t] = {"bid_star": best_bid_t, "ev": best_ev_t}

    print_subheader("Summary: bid*(σ_poly)")
    print(f"  {'Tercile':>8}  {'bid*':>6}  {'EV':>9}")
    print(f"  {'─'*8}  {'─'*6}  {'─'*9}")
    for t in ["low", "medium", "high"]:
        print(f"  {t:>8}  {optimal_by_tercile[t]['bid_star']:>6.2f}  {optimal_by_tercile[t]['ev']:>+9.4f}")

    return optimal_by_tercile


# ══════════════════════════════════════════════════════════════════
# PHASE 5: Validate Against Real Data
# ══════════════════════════════════════════════════════════════════

def validate_against_real_orders(markets, order_log):
    """
    用 mm_order_log 嘅 submit events 去同 signal_tape 比較。
    cid matching: order_log truncated cid → signal_tape full cid（prefix match）。
    """
    print_header("Part 4: Validation Against Real Orders")

    # Build cid lookup: truncated -> full (try multiple prefix lengths)
    cid_lookup = {}
    for full_cid in markets:
        for plen in [8, 10, 12]:
            short = full_cid[:plen]
            if short not in cid_lookup:
                cid_lookup[short] = full_cid

    # Parse submit and fill events
    submits = []
    fills = set()
    cancels = set()
    for entry in order_log:
        ev = entry.get("event")
        if ev == "submit":
            submits.append(entry)
        elif ev == "fill":
            fills.add(entry.get("order_id"))
        elif ev in ("cancel", "cancelled_external"):
            cancels.add(entry.get("order_id"))

    print(f"  Order log: {len(submits)} submits, {len(fills)} fills, {len(cancels)} cancels")

    matched = 0
    correct = 0
    details = []

    for sub in submits:
        order_id = sub.get("order_id")
        short_cid = sub.get("cid", "")
        outcome = sub.get("outcome", "UP")
        bid_price = sub.get("price", 0)
        sub_ts = parse_ts(sub["ts"])

        # Find full cid
        full_cid = cid_lookup.get(short_cid)
        if not full_cid or full_cid not in markets:
            continue

        mkt = markets[full_cid]
        snaps = mkt["snapshots"]

        # Find snapshot closest to (at or after) submission time
        entry_idx = None
        for i, s in enumerate(snaps):
            if s["ts"] >= sub_ts:
                entry_idx = i
                break
        if entry_idx is None:
            entry_idx = len(snaps) - 1

        # Simulate fill
        if outcome == "UP":
            sim_filled = any(
                snaps[j]["up_mid"] <= bid_price
                for j in range(entry_idx, len(snaps))
            )
        else:
            sim_filled = any(
                snaps[j]["dn_mid"] <= bid_price
                for j in range(entry_idx, len(snaps))
            )

        real_filled = order_id in fills
        was_cancelled = order_id in cancels

        matched += 1
        if sim_filled == real_filled:
            correct += 1

        details.append({
            "order_id": order_id[:14] + "..",
            "cid_short": short_cid,
            "outcome": outcome,
            "bid": bid_price,
            "real_filled": real_filled,
            "sim_filled": sim_filled,
            "cancelled": was_cancelled,
            "match": sim_filled == real_filled,
        })

    if matched == 0:
        print("  搵唔到 matching orders（cid 對唔上）")
        print(f"  Order log cids: {set(s.get('cid','') for s in submits)}")
        print(f"  Signal tape cid prefixes: {list(cid_lookup.keys())[:10]}")
        return {"matched": 0, "correct": 0, "accuracy": 0}

    accuracy = correct / matched
    print(f"\n  配對到 {matched} 個 submit orders")
    print(f"  模擬預測準確率: {correct}/{matched} = {accuracy:.1%}")

    print(f"\n  {'Order':>16}  {'CID':>10}  {'Side':>5}  {'Bid':>5}  {'Real':>6}  {'Sim':>6}  {'Cancel':>7}  {'OK':>4}")
    print(f"  {'─'*16}  {'─'*10}  {'─'*5}  {'─'*5}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*4}")
    for d in details:
        real_str = "FILL" if d["real_filled"] else "miss"
        sim_str = "FILL" if d["sim_filled"] else "miss"
        cancel_str = "Y" if d["cancelled"] else ""
        match_str = "✓" if d["match"] else "✗"
        print(f"  {d['order_id']:>16}  {d['cid_short']:>10}  {d['outcome']:>5}  {d['bid']:>5.2f}  {real_str:>6}  {sim_str:>6}  {cancel_str:>7}  {match_str:>4}")

    mismatches = [d for d in details if not d["match"]]
    if mismatches:
        print_subheader("Mismatch 分析")
        false_pos = sum(1 for d in mismatches if d["sim_filled"] and not d["real_filled"])
        false_neg = sum(1 for d in mismatches if not d["sim_filled"] and d["real_filled"])
        fp_cancelled = sum(1 for d in mismatches if d["sim_filled"] and not d["real_filled"] and d["cancelled"])
        print(f"  False positive (sim=fill, real=miss): {false_pos}  (其中 {fp_cancelled} 已 cancel)")
        print(f"  False negative (sim=miss, real=fill): {false_neg}")
        print("  → FP + cancelled = mid 的確跌穿，但 order 已經 cancel 咗所以冇 fill")
        print("  → FP - cancelled = mid 跌穿但 OB depth 唔夠 fill（simulation 嘅固有限制）")
        print("  → FN = 真正 fill 但 mid 冇跌穿（taker 直接 hit ask / market order fill）")

    return {"matched": matched, "correct": correct, "accuracy": accuracy}


# ══════════════════════════════════════════════════════════════════
# PHASE 6: Time Decay Effect
# ══════════════════════════════════════════════════════════════════

def simulate_time_decay(markets, vol_stats, terciles):
    """
    Part 5: 唔同入場時間對 fill rate 嘅影響。
    Entry at minute 1, 3, 5, 7, 9 of the 15-min window.
    """
    print_header("Part 5: Time Decay Effect")

    time_results = {}
    for em in ENTRY_MINUTES:
        time_results[em] = simulate_fills(markets, vol_stats, terciles, entry_minute=em)

    # Print P(fill | bid, entry_time) matrix
    print_subheader("P(fill | bid, entry_time) — 合併 UP + DOWN")
    header = f"  {'Bid':>6}"
    for em in ENTRY_MINUTES:
        header += f"  {'min'+str(em):>8}"
    print(header)
    print(f"  {'─'*6}" + f"  {'─'*8}" * len(ENTRY_MINUTES))

    for bp in BID_PRICES:
        row = f"  {bp:>6.2f}"
        for em in ENTRY_MINUTES:
            r = time_results[em]
            filled = r["up"][bp]["filled"] + r["dn"][bp]["filled"]
            total = r["up"][bp]["total"] + r["dn"][bp]["total"]
            p_fill = filled / total if total > 0 else 0
            row += f"  {fmt_pct(p_fill, 7)}"
        print(row)

    # EV surface
    print_subheader(f"EV(bid, entry_time) — WR={BASE_WR:.0%}")
    header = f"  {'Bid':>6}"
    for em in ENTRY_MINUTES:
        header += f"  {'min'+str(em):>8}"
    print(header)
    print(f"  {'─'*6}" + f"  {'─'*8}" * len(ENTRY_MINUTES))

    best_by_time = {}
    for em in ENTRY_MINUTES:
        best_ev = -999
        best_bid = 0
        for bp in BID_PRICES:
            r = time_results[em]
            filled = r["up"][bp]["filled"] + r["dn"][bp]["filled"]
            total = r["up"][bp]["total"] + r["dn"][bp]["total"]
            p_fill = filled / total if total > 0 else 0
            ev = p_fill * (BASE_WR - bp)
            if ev > best_ev:
                best_ev = ev
                best_bid = bp
        best_by_time[em] = {"bid_star": best_bid, "ev": best_ev}

    for bp in BID_PRICES:
        row = f"  {bp:>6.2f}"
        for em in ENTRY_MINUTES:
            r = time_results[em]
            filled = r["up"][bp]["filled"] + r["dn"][bp]["filled"]
            total = r["up"][bp]["total"] + r["dn"][bp]["total"]
            p_fill = filled / total if total > 0 else 0
            ev = p_fill * (BASE_WR - bp)
            row += f"  {ev:>+8.4f}"
        print(row)

    # Optimal bid by entry time
    print_subheader("bid*(entry_time) — 每個入場時間嘅最優 bid")
    print(f"  {'Entry':>6}  {'bid*':>6}  {'EV':>9}  {'N markets':>10}")
    print(f"  {'─'*6}  {'─'*6}  {'─'*9}  {'─'*10}")
    for em in ENTRY_MINUTES:
        bp = best_by_time[em]["bid_star"]
        r = time_results[em]
        total = r["up"][bp]["total"] + r["dn"][bp]["total"]
        print(f"  min {em:>2}  {bp:>6.2f}  {best_by_time[em]['ev']:>+9.4f}  {total//2:>10}")

    # Fill rate decay bars
    print_subheader("Fill rate 隨入場時間衰減（fixed bid = 0.37）")
    bp_ref = 0.37
    for em in ENTRY_MINUTES:
        r = time_results[em]
        filled = r["up"][bp_ref]["filled"] + r["dn"][bp_ref]["filled"]
        total = r["up"][bp_ref]["total"] + r["dn"][bp_ref]["total"]
        p_fill = filled / total if total > 0 else 0
        bar = "█" * int(p_fill * 50)
        print(f"  min {em:>2}: {fmt_pct(p_fill)}  {bar}")

    return time_results, best_by_time


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║  Fill Probability Model — Polymarket BTC 15-min Binary             ║")
    print("║  反推 entry price / fill rate / volatility / time 嘅關係           ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    # ── Load data ──
    print("\n⏳ Loading signal_tape.jsonl ...")
    markets, skipped_parse, skipped_few = load_signal_tape()
    total_snaps = sum(len(m["snapshots"]) for m in markets.values())
    print(f"  ✓ {len(markets)} BTC markets loaded ({total_snaps} in-window snapshots)")
    if skipped_parse:
        print(f"  ⚠ {skipped_parse} markets skipped（title parse 失敗）")
    if skipped_few:
        print(f"  ⚠ {skipped_few} markets skipped（in-window snapshots < {MIN_RECORDS_IN_WINDOW}）")

    # Print sample market for sanity check
    sample_cid = list(markets.keys())[0]
    sm = markets[sample_cid]
    print(f"\n  Sample: {sm['title']}")
    print(f"    Window: {sm['window_start'].strftime('%H:%M')} → {sm['window_end'].strftime('%H:%M')} HKT")
    print(f"    In-window snaps: {len(sm['snapshots'])}")
    print(f"    First snap: {sm['snapshots'][0]['ts'].strftime('%H:%M:%S')}  up_mid={sm['snapshots'][0]['up_mid']}")
    print(f"    Last snap:  {sm['snapshots'][-1]['ts'].strftime('%H:%M:%S')}  up_mid={sm['snapshots'][-1]['up_mid']}")

    print("\n⏳ Loading mm_order_log.jsonl ...")
    order_log = load_order_log()
    print(f"  ✓ {len(order_log)} order events loaded")

    # ── Part 1: Volatility ──
    vol_stats = compute_market_volatility(markets)
    print_volatility_stats(vol_stats)

    # ── Part 2: Fill probability ──
    terciles = assign_volatility_terciles(vol_stats)
    results_m3 = simulate_fills(markets, vol_stats, terciles, entry_minute=3)
    print_fill_probability(results_m3, entry_minute=3)
    print_fill_by_volatility(results_m3)

    # ── Part 3: Optimal bid ──
    optimal_by_tercile = compute_optimal_bids(results_m3, vol_stats, terciles)

    # ── Part 4: Validate ──
    validation = validate_against_real_orders(markets, order_log)

    # ── Part 5: Time decay ──
    time_results, best_by_time = simulate_time_decay(markets, vol_stats, terciles)

    # ══════════════════════════════════════════════════════════════
    # Summary & Recommendations
    # ══════════════════════════════════════════════════════════════
    print_header("Summary & 建議")

    # Current cap fill rate
    filled_040 = results_m3["up"][0.40]["filled"] + results_m3["dn"][0.40]["filled"]
    total_040 = results_m3["up"][0.40]["total"] + results_m3["dn"][0.40]["total"]
    p_fill_040 = filled_040 / total_040 if total_040 > 0 else 0

    filled_037 = results_m3["up"][0.37]["filled"] + results_m3["dn"][0.37]["filled"]
    total_037 = results_m3["up"][0.37]["total"] + results_m3["dn"][0.37]["total"]
    p_fill_037 = filled_037 / total_037 if total_037 > 0 else 0

    print(f"\n  現狀：固定 cap = $0.28-$0.40")
    print(f"  真實 fill rate: 28.6% (10/35)")
    print(f"  模擬 P(fill) at $0.37: {p_fill_037:.1%}  |  at $0.40: {p_fill_040:.1%}")
    print(f"  → 模擬 fill rate 遠高於真實，因為：")
    print(f"    (a) 模擬用 mid-price crossing，真實要 OB depth 配合")
    print(f"    (b) 真實有 cancel 同 TTL expiry 減少 fill 機會")
    print(f"    (c) 模擬係 passive（等 mid 跌穿），真實 fill 可能更 selective")

    # Best overall
    best_overall_ev = -999
    best_overall_bid = 0
    for bp in BID_PRICES:
        filled = results_m3["up"][bp]["filled"] + results_m3["dn"][bp]["filled"]
        total = results_m3["up"][bp]["total"] + results_m3["dn"][bp]["total"]
        p_fill = filled / total if total > 0 else 0
        ev = p_fill * (BASE_WR - bp)
        if ev > best_overall_ev:
            best_overall_ev = ev
            best_overall_bid = bp

    print(f"\n  最優固定 bid = ${best_overall_bid:.2f}  (EV = {best_overall_ev:+.4f})")

    print(f"\n  動態 bid by σ_poly:")
    for t in ["low", "medium", "high"]:
        o = optimal_by_tercile[t]
        print(f"    {t:>8} vol: bid* = ${o['bid_star']:.2f}  (EV = {o['ev']:+.4f})")

    print(f"\n  動態 bid by entry_time:")
    for em in ENTRY_MINUTES:
        o = best_by_time[em]
        print(f"    min {em}: bid* = ${o['bid_star']:.2f}  (EV = {o['ev']:+.4f})")

    # Additional insight: bid/mid ratio at fill
    print_subheader("核心發現")
    print("  1. bid 越高 → fill rate 越高，但 EV per fill 越低")
    print("     → 存在 sweet spot：P(fill) × (WR - bid) 最大化")
    print("  2. 高波動 market 唔使出高價就有較好 fill rate")
    print("     → 可以出低 bid 提高 margin（但要即時識別 vol regime）")
    print("  3. 入場越遲，剩餘時間越短，fill rate 越低")
    print("     → 遲入場可能要出更高 bid 先 fill 到")
    print("  4. 模擬 vs 真實 fill rate 差距大")
    print("     → Mid-crossing 係必要條件但非充分條件")
    print("     → Cancel policy / TTL / OB depth 係 fill rate 嘅 binding constraint")
    print("  5. σ_poly 同 σ_btc_pct 嘅相關性決定：")
    print("     → 如果弱相關 → poly mid 由 OB flow 驅動，唔係 BTC 價格")
    print("     → Dynamic bid 要 track poly OB 狀態，唔止 BTC vol")

    # ── Save JSON ──
    output = {
        "metadata": {
            "n_markets": len(vol_stats),
            "total_in_window_snapshots": total_snaps,
            "base_wr": BASE_WR,
            "bid_prices_tested": BID_PRICES,
            "entry_minutes_tested": ENTRY_MINUTES,
        },
        "volatility_stats": {
            "sigma_poly_mean": statistics.mean([v["sigma_poly"] for v in vol_stats.values()]),
            "sigma_poly_median": statistics.median([v["sigma_poly"] for v in vol_stats.values()]),
            "sigma_poly_p25": percentile(sorted([v["sigma_poly"] for v in vol_stats.values()]), 25),
            "sigma_poly_p75": percentile(sorted([v["sigma_poly"] for v in vol_stats.values()]), 75),
            "correlation_poly_btc": compute_correlation(
                [v["sigma_poly"] for v in vol_stats.values()],
                [v["sigma_btc_pct"] for v in vol_stats.values()],
            ),
        },
        "fill_curve_minute3": {
            str(bp): {
                "p_fill_combined": (results_m3["up"][bp]["filled"] + results_m3["dn"][bp]["filled"]) /
                                   max(1, results_m3["up"][bp]["total"] + results_m3["dn"][bp]["total"]),
                "p_fill_up": results_m3["up"][bp]["filled"] / max(1, results_m3["up"][bp]["total"]),
                "p_fill_dn": results_m3["dn"][bp]["filled"] / max(1, results_m3["dn"][bp]["total"]),
                "ev": ((results_m3["up"][bp]["filled"] + results_m3["dn"][bp]["filled"]) /
                       max(1, results_m3["up"][bp]["total"] + results_m3["dn"][bp]["total"])) * (BASE_WR - bp),
            }
            for bp in BID_PRICES
        },
        "fill_by_tercile_minute3": {
            str(bp): {
                t: {
                    "p_fill": (results_m3["up"][bp]["by_tercile"][t][0] + results_m3["dn"][bp]["by_tercile"][t][0]) /
                              max(1, results_m3["up"][bp]["by_tercile"][t][1] + results_m3["dn"][bp]["by_tercile"][t][1]),
                }
                for t in ["low", "medium", "high"]
            }
            for bp in BID_PRICES
        },
        "optimal_bid_overall": best_overall_bid,
        "optimal_bid_by_tercile": {
            t: optimal_by_tercile[t]["bid_star"] for t in ["low", "medium", "high"]
        },
        "optimal_bid_by_entry_time": {
            str(em): best_by_time[em]["bid_star"] for em in ENTRY_MINUTES
        },
        "validation": validation,
        "time_decay": {
            str(em): {
                str(bp): {
                    "p_fill": (time_results[em]["up"][bp]["filled"] + time_results[em]["dn"][bp]["filled"]) /
                              max(1, time_results[em]["up"][bp]["total"] + time_results[em]["dn"][bp]["total"]),
                    "n_markets": (time_results[em]["up"][bp]["total"] + time_results[em]["dn"][bp]["total"]) // 2,
                }
                for bp in BID_PRICES
            }
            for em in ENTRY_MINUTES
        },
    }

    # Atomic write
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(OUTPUT_JSON))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(output, f, indent=2)
        os.replace(tmp_path, OUTPUT_JSON)
        print(f"\n  JSON results saved to: {OUTPUT_JSON}")
    except Exception:
        os.unlink(tmp_path)
        raise

    print("\n  Done.")


if __name__ == "__main__":
    main()
