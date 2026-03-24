#!/usr/bin/env python3
"""
Co-Resolution Pricing Edge Analysis
====================================
Core insight: At co-resolution moments (where 5M+15M or 5M+15M+1H resolve
simultaneously), the LONGER timeframe direction may already be "locked"
while the SHORTER timeframe is still uncertain.

This is different from the previous overlap analysis (overlap_signal_analysis.py)
which looked at parent locked direction predicting child direction at child OPEN.
HERE we look at the final minutes before SIMULTANEOUS resolution.

Key questions:
1. At T-60s/T-120s before co-resolution, how often is 15M locked while 5M is uncertain?
2. When 15M IS locked, does the 5M tend to resolve in the SAME direction?
3. What's the theoretical PnL of trading 5M with 15M's locked direction?
4. Does 1H locked direction predict 15M at the hourly co-resolution?

Data: BTC 1M klines, 7 days (2026-03-16 to 2026-03-23), 10080 candles.
"""

import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────
BASE = Path("/Users/wai/projects/axc-trading")
BTC_PATH = BASE / "polymarket/analysis/btc_1m_7days.json"

# Polymarket window durations (seconds)
TF_5M = 300
TF_15M = 900
TF_1H = 3600

# Lock thresholds (basis points) — from prior analysis, calibrated
LOCK_BPS_THRESHOLDS = [5, 8, 10, 15, 20, 30, 50]

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ── Data Loading ───────────────────────────────────────────────────────────

def load_btc_data():
    """Load BTC 1M klines. Returns dict: timestamp_s -> candle."""
    with open(BTC_PATH) as f:
        raw = json.load(f)
    candles = {}
    for c in raw:
        ts_s = c["ts"] // 1000
        candles[ts_s] = {
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": c["volume"],
        }
    return candles


def get_price_at(candles, ts_s):
    """Get close price at a given second-timestamp (floored to minute)."""
    minute_ts = (ts_s // 60) * 60
    if minute_ts in candles:
        return candles[minute_ts]["close"]
    return None


def get_open_at(candles, ts_s):
    """Get open price at a given second-timestamp (floored to minute)."""
    minute_ts = (ts_s // 60) * 60
    if minute_ts in candles:
        return candles[minute_ts]["open"]
    return None


def bps(price_now, price_open):
    """Return basis points change: (now - open) / open * 10000."""
    if price_open == 0:
        return 0
    return (price_now - price_open) / price_open * 10000


# ── Co-Resolution Finder ──────────────────────────────────────────────────

def find_co_resolution_moments(candles):
    """
    Find all co-resolution moments within the data range.

    Returns:
        list of dicts with:
            - ts: resolution timestamp (seconds)
            - type: '5M+15M' or '5M+15M+1H'
            - dt: datetime (UTC)
    """
    timestamps = sorted(candles.keys())
    ts_min, ts_max = timestamps[0], timestamps[-1]

    # Align to first 15-min boundary after data start
    first_15m = ((ts_min // TF_15M) + 1) * TF_15M
    # Align to last 15-min boundary before data end
    last_15m = (ts_max // TF_15M) * TF_15M

    moments = []
    ts = first_15m
    while ts <= last_15m:
        # Check we have enough data before this moment
        # Need 15M window open (ts - 900) and ideally 1H window open (ts - 3600)
        open_15m_ts = ts - TF_15M
        open_5m_ts = ts - TF_5M
        open_1h_ts = ts - TF_1H

        # Verify we have candle data for the key points
        if get_price_at(candles, open_5m_ts) is None or get_price_at(candles, open_15m_ts) is None:
            ts += TF_15M
            continue

        is_1h = (ts % TF_1H == 0)
        has_1h_data = get_price_at(candles, open_1h_ts) is not None if is_1h else False

        moment = {
            "ts": ts,
            "type": "5M+15M+1H" if (is_1h and has_1h_data) else "5M+15M",
            "dt": datetime.fromtimestamp(ts, tz=timezone.utc),
        }
        moments.append(moment)
        ts += TF_15M

    return moments


# ── Section 1 & 2: Snapshot at each co-resolution ─────────────────────────

def build_resolution_snapshots(candles, moments):
    """
    At each co-resolution moment, capture prices and directions at
    T-0, T-10s, T-30s, T-60s, T-120s.
    """
    snapshots = []

    for m in moments:
        ts = m["ts"]
        open_15m = get_open_at(candles, ts - TF_15M)
        open_5m = get_open_at(candles, ts - TF_5M)
        open_1h = get_open_at(candles, ts - TF_1H) if "1H" in m["type"] else None

        # Resolution price = close of the candle ending at ts
        # The candle at ts-60 has close = price at ts (approximately)
        # Actually: candle at ts_s has open=price at ts_s, close=price at ts_s+60
        # So resolution price = close of candle at (ts - 60)
        price_resolve = get_price_at(candles, ts - 60)  # close of last candle before resolution
        if price_resolve is None:
            continue

        # Prices at lead times before resolution
        # T-60s: close of candle at (ts - 120), i.e., price at ~T-60
        # T-120s: close of candle at (ts - 180)
        # More precisely: candle at time X has close = price at X+60
        # So price at T-Ns = close of candle at T-N-60... no.
        # Actually candle at ts has: open=price at ts, close=price at ts+60
        # So to get price at time T: we need candle where ts <= T < ts+60
        # = candle at floor(T/60)*60, and we use close if T is at the end,
        #   open if at the start. For simplicity, use close of (T-60) candle.
        # But our candles are keyed by their START time (the ts field).
        # candle[X].close = price at X+60.
        # So price at time T = candle[T-60].close = candle[(T//60 -1)*60].close
        # OR candle[T].open if T is on a minute boundary.

        # Let's use a cleaner approach:
        # price_at(T) = candle[floor(T/60)*60].open (price at start of that minute)
        # This works because open of minute X = close of minute X-60 (no gaps)

        def price_at_second(t):
            """Best estimate of BTC price at second t."""
            minute = (t // 60) * 60
            if minute in candles:
                # Interpolate: if t is at minute start, use open
                # For simplicity, use open of the minute (= close of prev minute)
                return candles[minute]["open"]
            return None

        price_t0 = price_at_second(ts)  # resolution moment
        if price_t0 is None:
            # Use close of previous candle
            price_t0 = price_resolve

        price_t10 = price_at_second(ts - 10)  # use same minute's open
        price_t30 = price_at_second(ts - 30)
        price_t60 = price_at_second(ts - 60)
        price_t120 = price_at_second(ts - 120)

        if any(p is None for p in [price_t60, price_t120]):
            continue

        # Direction at resolution
        dir_15m = "UP" if price_t0 > open_15m else "DOWN" if price_t0 < open_15m else "FLAT"
        dir_5m = "UP" if price_t0 > open_5m else "DOWN" if price_t0 < open_5m else "FLAT"
        dir_1h = None
        if open_1h is not None:
            dir_1h = "UP" if price_t0 > open_1h else "DOWN" if price_t0 < open_1h else "FLAT"

        snap = {
            "ts": ts,
            "dt": m["dt"],
            "type": m["type"],
            # Open prices
            "open_15m": open_15m,
            "open_5m": open_5m,
            "open_1h": open_1h,
            # Prices at various lead times
            "price_t0": price_t0,
            "price_t10": price_t10,
            "price_t30": price_t30,
            "price_t60": price_t60,
            "price_t120": price_t120,
            # Returns at resolution (bps)
            "ret_15m_t0": bps(price_t0, open_15m),
            "ret_5m_t0": bps(price_t0, open_5m),
            "ret_1h_t0": bps(price_t0, open_1h) if open_1h else None,
            # Returns at T-60s (bps from respective opens)
            "ret_15m_t60": bps(price_t60, open_15m),
            "ret_5m_t60": bps(price_t60, open_5m),
            "ret_1h_t60": bps(price_t60, open_1h) if open_1h else None,
            # Returns at T-120s
            "ret_15m_t120": bps(price_t120, open_15m),
            "ret_5m_t120": bps(price_t120, open_5m),
            "ret_1h_t120": bps(price_t120, open_1h) if open_1h else None,
            # Resolution directions
            "dir_15m": dir_15m,
            "dir_5m": dir_5m,
            "dir_1h": dir_1h,
        }
        snapshots.append(snap)

    return snapshots


# ── Section 3: "Known Direction" Edge ──────────────────────────────────────

def analyze_locked_direction_edge(snapshots):
    """
    When 15M is "locked" at T-60s or T-120s, what % of 5M resolve in
    the SAME direction?

    Also: when 1H is locked, what % of 15M resolve same direction?
    """
    results = {}

    for lead_label, lead_key in [("T-60s", "t60"), ("T-120s", "t120")]:
        for threshold_bps in LOCK_BPS_THRESHOLDS:
            # ── 15M locked → 5M same direction? ──
            key_15m_ret = f"ret_15m_{lead_key}"
            key_5m_dir = "dir_5m"
            key_5m_ret_at_lead = f"ret_5m_{lead_key}"

            total = 0
            locked_15m = 0
            same_dir = 0
            opposite_dir = 0
            # Track 5M return at lead time when 15M is locked
            fiveM_uncertain_count = 0  # 5M |return| < threshold at lead time

            for s in snapshots:
                if s["dir_5m"] == "FLAT" or s["dir_15m"] == "FLAT":
                    continue
                total += 1

                ret_15m = s[key_15m_ret]
                if abs(ret_15m) >= threshold_bps:
                    locked_15m += 1
                    locked_dir = "UP" if ret_15m > 0 else "DOWN"
                    if s[key_5m_dir] == locked_dir:
                        same_dir += 1
                    else:
                        opposite_dir += 1

                    # Check if 5M was uncertain at lead time
                    ret_5m_lead = abs(s[key_5m_ret_at_lead])
                    if ret_5m_lead < 5:  # <5bps = uncertain
                        fiveM_uncertain_count += 1

            wr = same_dir / locked_15m * 100 if locked_15m > 0 else 0

            results[f"15M→5M|{lead_label}|>{threshold_bps}bps"] = {
                "lead": lead_label,
                "threshold_bps": threshold_bps,
                "parent": "15M",
                "child": "5M",
                "total": total,
                "locked": locked_15m,
                "same_dir": same_dir,
                "opposite_dir": opposite_dir,
                "wr_same": wr,
                "pct_locked": locked_15m / total * 100 if total > 0 else 0,
                "uncertain_5m_count": fiveM_uncertain_count,
            }

            # ── 1H locked → 15M same direction? (only at 1H marks) ──
            key_1h_ret = f"ret_1h_{lead_key}"
            total_1h = 0
            locked_1h = 0
            same_dir_1h = 0

            for s in snapshots:
                if s["dir_1h"] is None or s["dir_15m"] == "FLAT" or s["dir_1h"] == "FLAT":
                    continue
                total_1h += 1

                ret_1h = s[key_1h_ret]
                if abs(ret_1h) >= threshold_bps:
                    locked_1h += 1
                    locked_dir = "UP" if ret_1h > 0 else "DOWN"
                    if s["dir_15m"] == locked_dir:
                        same_dir_1h += 1

            wr_1h = same_dir_1h / locked_1h * 100 if locked_1h > 0 else 0

            results[f"1H→15M|{lead_label}|>{threshold_bps}bps"] = {
                "lead": lead_label,
                "threshold_bps": threshold_bps,
                "parent": "1H",
                "child": "15M",
                "total": total_1h,
                "locked": locked_1h,
                "same_dir": same_dir_1h,
                "opposite_dir": locked_1h - same_dir_1h,
                "wr_same": wr_1h,
                "pct_locked": locked_1h / total_1h * 100 if total_1h > 0 else 0,
            }

            # ── 1H locked → 5M same direction? (only at 1H marks) ──
            total_1h_5m = 0
            locked_1h_5m = 0
            same_dir_1h_5m = 0

            for s in snapshots:
                if s["dir_1h"] is None or s["dir_5m"] == "FLAT" or s["dir_1h"] == "FLAT":
                    continue
                total_1h_5m += 1

                ret_1h = s[key_1h_ret]
                if abs(ret_1h) >= threshold_bps:
                    locked_1h_5m += 1
                    locked_dir = "UP" if ret_1h > 0 else "DOWN"
                    if s["dir_5m"] == locked_dir:
                        same_dir_1h_5m += 1

            wr_1h_5m = same_dir_1h_5m / locked_1h_5m * 100 if locked_1h_5m > 0 else 0

            results[f"1H→5M|{lead_label}|>{threshold_bps}bps"] = {
                "lead": lead_label,
                "threshold_bps": threshold_bps,
                "parent": "1H",
                "child": "5M",
                "total": total_1h_5m,
                "locked": locked_1h_5m,
                "same_dir": same_dir_1h_5m,
                "opposite_dir": locked_1h_5m - same_dir_1h_5m,
                "wr_same": wr_1h_5m,
                "pct_locked": locked_1h_5m / total_1h_5m * 100 if total_1h_5m > 0 else 0,
            }

    return results


# ── Section 3b: Conditional — 15M locked AND 5M uncertain ─────────────────

def analyze_conditional_edge(snapshots):
    """
    The real trade: at T-60s, 15M is locked (>N bps) AND 5M is uncertain
    (|return| < M bps). What's the 5M resolution WR in 15M's direction?
    """
    results = []
    uncertain_thresholds = [3, 5, 8, 10]

    for lead_label, lead_key in [("T-60s", "t60"), ("T-120s", "t120")]:
        for lock_bps in [8, 10, 15, 20, 30]:
            for uncertain_bps in uncertain_thresholds:
                key_15m_ret = f"ret_15m_{lead_key}"
                key_5m_ret = f"ret_5m_{lead_key}"

                locked_and_uncertain = 0
                same_dir = 0

                for s in snapshots:
                    if s["dir_5m"] == "FLAT" or s["dir_15m"] == "FLAT":
                        continue

                    ret_15m = s[key_15m_ret]
                    ret_5m = s[key_5m_ret]

                    if abs(ret_15m) >= lock_bps and abs(ret_5m) < uncertain_bps:
                        locked_and_uncertain += 1
                        locked_dir = "UP" if ret_15m > 0 else "DOWN"
                        if s["dir_5m"] == locked_dir:
                            same_dir += 1

                wr = same_dir / locked_and_uncertain * 100 if locked_and_uncertain > 0 else 0

                results.append({
                    "lead": lead_label,
                    "lock_bps": lock_bps,
                    "uncertain_bps": uncertain_bps,
                    "n": locked_and_uncertain,
                    "same_dir": same_dir,
                    "wr": wr,
                })

    return results


# ── Section 4: Theoretical PnL ────────────────────────────────────────────

def calculate_theoretical_pnl(snapshots, edge_results):
    """
    If we buy 5M in 15M's locked direction at T-60s:
    - Entry price: assume ~$0.50 (fair 50/50) or slightly adjusted
    - Win pays $1, lose pays $0
    - EV = WR * ($1 - entry) - (1-WR) * entry
    """
    log.info("\n" + "=" * 80)
    log.info("SECTION 4: THEORETICAL PnL")
    log.info("=" * 80)

    # Market price assumptions
    entry_prices = [0.50, 0.52, 0.55, 0.58, 0.60]

    log.info("\n### Scenario: Buy 5M in 15M locked direction at T-60s")
    log.info(f"{'Lock threshold':>15} {'N':>5} {'WR':>7} {'Entry':>7} {'EV/trade':>10} {'Daily N':>8} {'Daily EV':>10}")
    log.info("-" * 70)

    for threshold_bps in [10, 15, 20, 30]:
        key = f"15M→5M|T-60s|>{threshold_bps}bps"
        if key not in edge_results:
            continue
        r = edge_results[key]
        wr = r["wr_same"] / 100
        n = r["locked"]
        daily_n = n / 7

        for entry in entry_prices:
            ev_per_trade = wr * (1 - entry) - (1 - wr) * entry
            daily_ev = ev_per_trade * daily_n
            log.info(f">{threshold_bps:>3}bps        {n:>5} {wr:>6.1%} {entry:>6.2f}  {ev_per_trade:>+9.4f}  {daily_n:>7.1f}  ${daily_ev:>+8.2f}")

    # Conditional PnL: 15M locked AND 5M uncertain
    log.info("\n### Scenario: Buy 5M in 15M locked direction, ONLY when 5M is uncertain")

    cond_results = analyze_conditional_edge(snapshots)
    log.info(f"\n{'Lead':>6} {'15M lock':>9} {'5M unc.':>8} {'N':>5} {'Same':>5} {'WR':>7} {'EV@50c':>8} {'EV@55c':>8}")
    log.info("-" * 65)

    for r in cond_results:
        if r["n"] < 10:  # skip tiny samples
            continue
        wr = r["wr"] / 100
        ev_50 = wr * 0.50 - (1 - wr) * 0.50
        ev_55 = wr * 0.45 - (1 - wr) * 0.55
        log.info(
            f"{r['lead']:>6} >{r['lock_bps']:>3}bps  <{r['uncertain_bps']:>3}bps"
            f" {r['n']:>5} {r['same_dir']:>5} {wr:>6.1%} {ev_50:>+7.4f} {ev_55:>+7.4f}"
        )

    return cond_results


# ── Section 5: The BIG question — combo with momentum ─────────────────────

def analyze_momentum_combo(snapshots):
    """
    Can we combine 15M locked direction with short-term momentum?

    At T-60s, if:
    1. 15M is locked (|return| > 10bps)
    2. BTC has moved in 15M's direction in the last 60s (momentum confirms)

    What's the WR?
    """
    log.info("\n" + "=" * 80)
    log.info("SECTION 5: MOMENTUM COMBO (15M locked + short-term momentum)")
    log.info("=" * 80)

    for lead_label, lead_key, lead_s in [("T-60s", "t60", 60), ("T-120s", "t120", 120)]:
        log.info(f"\n### At {lead_label}:")
        log.info(f"{'Lock bps':>10} {'Condition':>25} {'N':>5} {'Same':>5} {'WR':>7}")
        log.info("-" * 60)

        for lock_bps in [10, 15, 20]:
            # Baseline: just locked
            baseline_n = 0
            baseline_same = 0

            # Momentum confirms
            confirm_n = 0
            confirm_same = 0

            # Momentum opposes
            oppose_n = 0
            oppose_same = 0

            for s in snapshots:
                if s["dir_5m"] == "FLAT" or s["dir_15m"] == "FLAT":
                    continue

                ret_15m = s[f"ret_15m_{lead_key}"]
                if abs(ret_15m) < lock_bps:
                    continue

                locked_dir = "UP" if ret_15m > 0 else "DOWN"
                is_same = s["dir_5m"] == locked_dir

                baseline_n += 1
                if is_same:
                    baseline_same += 1

                # Short-term momentum: price change from T-120 to T-60
                # (or T-180 to T-120 for T-120s lead)
                if lead_key == "t60":
                    p_now = s["price_t60"]
                    p_prev = s["price_t120"]
                elif lead_key == "t120":
                    p_now = s["price_t120"]
                    # Need T-180s price — approximate from candle
                    ts_180 = s["ts"] - 180
                    minute_ts = (ts_180 // 60) * 60
                    p_prev = None  # we don't have this easily, skip
                    # Use price_t120 vs open_5m direction as proxy
                    p_prev = s["open_5m"]

                if p_now and p_prev:
                    mom_dir = "UP" if p_now > p_prev else "DOWN"
                    if mom_dir == locked_dir:
                        confirm_n += 1
                        if is_same:
                            confirm_same += 1
                    else:
                        oppose_n += 1
                        if is_same:
                            oppose_same += 1

            wr_base = baseline_same / baseline_n if baseline_n else 0
            wr_confirm = confirm_same / confirm_n if confirm_n else 0
            wr_oppose = oppose_same / oppose_n if oppose_n else 0

            log.info(f">{lock_bps:>4}bps  {'baseline (locked only)':>25} {baseline_n:>5} {baseline_same:>5} {wr_base:>6.1%}")
            log.info(f"{'':>10} {'+ momentum confirms':>25} {confirm_n:>5} {confirm_same:>5} {wr_confirm:>6.1%}")
            log.info(f"{'':>10} {'+ momentum opposes':>25} {oppose_n:>5} {oppose_same:>5} {wr_oppose:>6.1%}")
            log.info("")


# ── Section 6: 1H locked → next 15M prediction ───────────────────────────

def analyze_1h_next_window(candles, snapshots):
    """
    At the 1H mark, if 1H resolves DOWN (locked):
    - Does the NEXT 15M window (starting right after) tend to go DOWN?
    - This is a "next window prediction" using momentum carry.
    """
    log.info("\n" + "=" * 80)
    log.info("SECTION 6: 1H LOCKED → NEXT 15M WINDOW PREDICTION")
    log.info("=" * 80)

    timestamps = sorted(candles.keys())

    for lock_bps in [10, 15, 20, 30, 50]:
        total = 0
        same_dir = 0
        events = []

        for s in snapshots:
            if s["type"] != "5M+15M+1H" or s["dir_1h"] is None or s["dir_1h"] == "FLAT":
                continue

            ret_1h = s["ret_1h_t0"]  # 1H return at resolution
            if abs(ret_1h) < lock_bps:
                continue

            locked_dir = "UP" if ret_1h > 0 else "DOWN"

            # Next 15M window: opens at ts, resolves at ts + 900
            next_15m_open_ts = s["ts"]
            next_15m_close_ts = s["ts"] + TF_15M

            # Get prices
            p_open = get_open_at(candles, next_15m_open_ts)
            p_close = get_price_at(candles, next_15m_close_ts - 60)  # close of last candle

            if p_open is None or p_close is None:
                continue

            next_dir = "UP" if p_close > p_open else "DOWN" if p_close < p_open else "FLAT"
            if next_dir == "FLAT":
                continue

            total += 1
            is_same = next_dir == locked_dir
            if is_same:
                same_dir += 1

            events.append({
                "dt": s["dt"],
                "ret_1h": ret_1h,
                "locked_dir": locked_dir,
                "next_15m_dir": next_dir,
                "next_15m_ret_bps": bps(p_close, p_open),
                "correct": is_same,
            })

        wr = same_dir / total * 100 if total > 0 else 0
        log.info(f"\n1H locked >{lock_bps}bps → next 15M same dir: {same_dir}/{total} = {wr:.1f}%")

        if lock_bps == 20 and events:
            log.info(f"\n  Sample events (1H locked >{lock_bps}bps):")
            log.info(f"  {'Time (UTC)':>20} {'1H ret':>8} {'1H dir':>8} {'Next 15M':>10} {'15M ret':>8} {'Hit':>5}")
            for e in events[:20]:
                dt_str = e["dt"].strftime("%m-%d %H:%M")
                log.info(
                    f"  {dt_str:>20} {e['ret_1h']:>+7.1f} {e['locked_dir']:>8}"
                    f" {e['next_15m_dir']:>10} {e['next_15m_ret_bps']:>+7.1f} {'Y' if e['correct'] else 'N':>5}"
                )


# ── Section: Concrete Examples ────────────────────────────────────────────

def print_concrete_examples(snapshots):
    """Print a few vivid co-resolution snapshots to make the numbers tangible."""
    log.info("\n" + "=" * 80)
    log.info("CONCRETE EXAMPLES: Co-Resolution Snapshots")
    log.info("=" * 80)

    # Find examples where 15M is strongly locked but 5M is uncertain
    good_examples = []
    for s in snapshots:
        ret_15m_t60 = abs(s["ret_15m_t60"])
        ret_5m_t60 = abs(s["ret_5m_t60"])
        if ret_15m_t60 > 15 and ret_5m_t60 < 5:
            good_examples.append(s)

    good_examples.sort(key=lambda x: abs(x["ret_15m_t60"]), reverse=True)

    log.info(f"\nFound {len(good_examples)} moments where 15M locked >15bps but 5M uncertain <5bps at T-60s")
    log.info("")

    for i, s in enumerate(good_examples[:10]):
        dt_str = s["dt"].strftime("%Y-%m-%d %H:%M UTC")
        log.info(f"── Example {i+1}: {dt_str} ({s['type']}) ──")
        log.info(f"  15M window: open=${s['open_15m']:,.1f}  →  resolve=${s['price_t0']:,.1f}")
        log.info(f"    @T-60s: ${s['price_t60']:,.1f} vs open=${s['open_15m']:,.1f} = {s['ret_15m_t60']:+.1f}bps → {'LOCKED ' + ('UP' if s['ret_15m_t60']>0 else 'DOWN')}")
        log.info(f"    Resolution: {s['dir_15m']} ({s['ret_15m_t0']:+.1f}bps)")
        log.info(f"  5M window:  open=${s['open_5m']:,.1f}  →  resolve=${s['price_t0']:,.1f}")
        log.info(f"    @T-60s: ${s['price_t60']:,.1f} vs open=${s['open_5m']:,.1f} = {s['ret_5m_t60']:+.1f}bps → UNCERTAIN")
        log.info(f"    Resolution: {s['dir_5m']} ({s['ret_5m_t0']:+.1f}bps)")
        if s["dir_1h"] is not None:
            log.info(f"  1H window:  open=${s['open_1h']:,.1f}  →  resolve=${s['price_t0']:,.1f}")
            log.info(f"    @T-60s: {s['ret_1h_t60']:+.1f}bps  Resolution: {s['dir_1h']} ({s['ret_1h_t0']:+.1f}bps)")

        # Did 5M resolve in 15M's locked direction?
        locked_dir = "UP" if s["ret_15m_t60"] > 0 else "DOWN"
        hit = "YES" if s["dir_5m"] == locked_dir else "NO"
        log.info(f"  → 5M resolved in 15M's locked direction? {hit}")
        log.info("")

    # Also show triple co-resolution examples
    triple = [s for s in snapshots if s["type"] == "5M+15M+1H" and s["ret_1h_t0"] is not None]
    log.info(f"\n── Triple Co-Resolution Examples (5M+15M+1H, showing first 8) ──")
    log.info(f"{'Time':>18} {'1H ret':>8} {'1H dir':>7} {'15M ret':>8} {'15M dir':>7} {'5M ret':>8} {'5M dir':>7}")
    log.info("-" * 70)

    for s in triple[:8]:
        dt_str = s["dt"].strftime("%m-%d %H:%M")
        log.info(
            f"{dt_str:>18}"
            f" {s['ret_1h_t0']:>+7.1f} {s['dir_1h']:>7}"
            f" {s['ret_15m_t0']:>+7.1f} {s['dir_15m']:>7}"
            f" {s['ret_5m_t0']:>+7.1f} {s['dir_5m']:>7}"
        )


# ── Section: Divergence Analysis ──────────────────────────────────────────

def analyze_divergence(snapshots):
    """
    How often do different timeframes resolve in DIFFERENT directions
    at the same co-resolution moment? This is the key insight:
    three markets, three different results.
    """
    log.info("\n" + "=" * 80)
    log.info("DIVERGENCE ANALYSIS: Different directions at same resolution")
    log.info("=" * 80)

    # 5M vs 15M at all 15-min marks
    same_5_15 = 0
    diff_5_15 = 0
    total_5_15 = 0

    for s in snapshots:
        if s["dir_5m"] == "FLAT" or s["dir_15m"] == "FLAT":
            continue
        total_5_15 += 1
        if s["dir_5m"] == s["dir_15m"]:
            same_5_15 += 1
        else:
            diff_5_15 += 1

    log.info(f"\n5M vs 15M at co-resolution (n={total_5_15}):")
    log.info(f"  Same direction: {same_5_15} ({same_5_15/total_5_15*100:.1f}%)")
    log.info(f"  Different direction: {diff_5_15} ({diff_5_15/total_5_15*100:.1f}%)")

    # At 1H marks: all three
    same_all = 0
    two_same = 0
    all_diff = 0  # can't have 3 truly different in binary UP/DOWN
    total_triple = 0

    patterns = defaultdict(int)
    for s in snapshots:
        if s["type"] != "5M+15M+1H":
            continue
        if s["dir_5m"] == "FLAT" or s["dir_15m"] == "FLAT" or s["dir_1h"] == "FLAT":
            continue
        total_triple += 1
        pat = f"1H={s['dir_1h']},15M={s['dir_15m']},5M={s['dir_5m']}"
        patterns[pat] += 1

        dirs = [s["dir_1h"], s["dir_15m"], s["dir_5m"]]
        unique = len(set(dirs))
        if unique == 1:
            same_all += 1
        else:
            two_same += 1

    if total_triple > 0:
        log.info(f"\nTriple co-resolution patterns (n={total_triple}):")
        log.info(f"  All 3 same direction: {same_all} ({same_all/total_triple*100:.1f}%)")
        log.info(f"  Mixed directions: {two_same} ({two_same/total_triple*100:.1f}%)")
        log.info(f"\n  Pattern breakdown:")
        for pat, count in sorted(patterns.items(), key=lambda x: -x[1]):
            log.info(f"    {pat}: {count} ({count/total_triple*100:.1f}%)")

    # Conditional: when 1H locked, how often do 15M and 5M disagree?
    log.info(f"\n### When 1H is locked (>20bps), 15M vs 5M divergence:")
    for lock_bps in [10, 20, 30]:
        locked = 0
        m15_same_1h = 0
        m5_same_1h = 0
        both_same = 0

        for s in snapshots:
            if s["type"] != "5M+15M+1H":
                continue
            if any(s[f"dir_{tf}"] == "FLAT" for tf in ["1h", "15m", "5m"]):
                continue
            if abs(s["ret_1h_t60"]) < lock_bps:
                continue

            locked += 1
            locked_dir = "UP" if s["ret_1h_t60"] > 0 else "DOWN"
            if s["dir_15m"] == locked_dir:
                m15_same_1h += 1
            if s["dir_5m"] == locked_dir:
                m5_same_1h += 1
            if s["dir_15m"] == locked_dir and s["dir_5m"] == locked_dir:
                both_same += 1

        if locked > 0:
            log.info(
                f"  1H>{lock_bps}bps (n={locked}): "
                f"15M same={m15_same_1h} ({m15_same_1h/locked*100:.1f}%), "
                f"5M same={m5_same_1h} ({m5_same_1h/locked*100:.1f}%), "
                f"both same={both_same} ({both_same/locked*100:.1f}%)"
            )


# ── Section: Price path analysis in final minutes ─────────────────────────

def analyze_final_minute_dynamics(candles, snapshots):
    """
    In the final 2 minutes before co-resolution, what happens to BTC?
    - Does it tend to continue in the 15M direction (momentum)?
    - Or mean-revert toward 5M open?
    """
    log.info("\n" + "=" * 80)
    log.info("FINAL MINUTE DYNAMICS: Price behavior T-120s to T-0s")
    log.info("=" * 80)

    # Track price changes in final 2 minutes
    # Split by whether 15M is locked or not
    locked_final_move = []  # bps move in last 60s when 15M locked
    unlocked_final_move = []

    # Does the final move tend to continue or reverse?
    locked_continue = 0  # final 60s move in 15M direction
    locked_reverse = 0
    unlocked_continue = 0
    unlocked_reverse = 0

    for s in snapshots:
        if s["dir_15m"] == "FLAT":
            continue

        # Final 60s move
        if s["price_t0"] and s["price_t60"]:
            final_move = bps(s["price_t0"], s["price_t60"])
            ret_15m = s["ret_15m_t60"]

            if abs(ret_15m) >= 10:
                locked_final_move.append(final_move)
                locked_dir_sign = 1 if ret_15m > 0 else -1
                if final_move * locked_dir_sign > 0:
                    locked_continue += 1
                elif final_move * locked_dir_sign < 0:
                    locked_reverse += 1
            else:
                unlocked_final_move.append(final_move)

    if locked_final_move:
        n = len(locked_final_move)
        mean_move = statistics.mean(locked_final_move)
        median_move = statistics.median(locked_final_move)
        total_dir = locked_continue + locked_reverse
        log.info(f"\nWhen 15M locked >10bps at T-60s (n={n}):")
        log.info(f"  Final 60s move: mean={mean_move:+.2f}bps, median={median_move:+.2f}bps")
        log.info(f"  Continues in 15M dir: {locked_continue}/{total_dir} ({locked_continue/total_dir*100:.1f}%)")
        log.info(f"  Reverses: {locked_reverse}/{total_dir} ({locked_reverse/total_dir*100:.1f}%)")
        log.info(f"  (Note: 'continues' doesn't mean 5M resolves same way — 5M has different open)")

    if unlocked_final_move:
        n = len(unlocked_final_move)
        mean_move = statistics.mean(unlocked_final_move)
        median_move = statistics.median(unlocked_final_move)
        log.info(f"\nWhen 15M NOT locked at T-60s (n={n}):")
        log.info(f"  Final 60s move: mean={mean_move:+.2f}bps, median={median_move:+.2f}bps")

    # Absolute move in last 60s — how much does BTC typically move?
    all_final_moves = [abs(m) for m in locked_final_move + unlocked_final_move]
    if all_final_moves:
        log.info(f"\nAbsolute BTC move in final 60s (all co-resolutions, n={len(all_final_moves)}):")
        log.info(f"  Mean: {statistics.mean(all_final_moves):.2f}bps")
        log.info(f"  Median: {statistics.median(all_final_moves):.2f}bps")
        log.info(f"  P75: {sorted(all_final_moves)[int(len(all_final_moves)*0.75)]:.2f}bps")
        log.info(f"  P90: {sorted(all_final_moves)[int(len(all_final_moves)*0.90)]:.2f}bps")
        log.info(f"  P95: {sorted(all_final_moves)[int(len(all_final_moves)*0.95)]:.2f}bps")

    # How often does the final 60s move FLIP the 5M direction?
    flip_count = 0
    total_check = 0
    for s in snapshots:
        if s["dir_5m"] == "FLAT":
            continue
        ret_5m_t60 = s["ret_5m_t60"]
        ret_5m_t0 = s["ret_5m_t0"]
        if ret_5m_t60 == 0:
            continue
        total_check += 1
        dir_at_t60 = "UP" if ret_5m_t60 > 0 else "DOWN"
        if dir_at_t60 != s["dir_5m"]:
            flip_count += 1

    if total_check > 0:
        log.info(f"\n5M direction FLIP in final 60s: {flip_count}/{total_check} ({flip_count/total_check*100:.1f}%)")
        log.info("  (5M was pointing one way at T-60s but resolved the other way)")


# ── MAIN ──────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 80)
    log.info("CO-RESOLUTION PRICING EDGE ANALYSIS")
    log.info("=" * 80)

    # Load data
    candles = load_btc_data()
    log.info(f"Loaded {len(candles)} 1M candles")

    # Find co-resolution moments
    moments = find_co_resolution_moments(candles)
    type_counts = defaultdict(int)
    for m in moments:
        type_counts[m["type"]] += 1
    log.info(f"\nCo-resolution moments found:")
    for t, c in sorted(type_counts.items()):
        log.info(f"  {t}: {c}")
    log.info(f"  Total: {len(moments)}")

    # Build snapshots
    snapshots = build_resolution_snapshots(candles, moments)
    log.info(f"\nSnapshots with complete data: {len(snapshots)}")

    # ── Section 1 & 2: Print summary stats ──
    log.info("\n" + "=" * 80)
    log.info("SECTION 1-2: Co-Resolution Snapshots Summary")
    log.info("=" * 80)

    # Distribution of 15M returns at resolution
    rets_15m = [s["ret_15m_t0"] for s in snapshots if s["dir_15m"] != "FLAT"]
    rets_5m = [s["ret_5m_t0"] for s in snapshots if s["dir_5m"] != "FLAT"]
    log.info(f"\n15M return at resolution (n={len(rets_15m)}):")
    log.info(f"  Mean: {statistics.mean(rets_15m):+.2f}bps, Median: {statistics.median(rets_15m):+.2f}bps")
    log.info(f"  Abs mean: {statistics.mean([abs(r) for r in rets_15m]):.2f}bps")
    log.info(f"\n5M return at resolution (n={len(rets_5m)}):")
    log.info(f"  Mean: {statistics.mean(rets_5m):+.2f}bps, Median: {statistics.median(rets_5m):+.2f}bps")
    log.info(f"  Abs mean: {statistics.mean([abs(r) for r in rets_5m]):.2f}bps")

    # ── Section 3: Locked direction edge ──
    log.info("\n" + "=" * 80)
    log.info("SECTION 3: LOCKED DIRECTION EDGE (co-resolution timing)")
    log.info("=" * 80)

    edge_results = analyze_locked_direction_edge(snapshots)

    # Print 15M → 5M results
    log.info("\n### 15M locked → 5M same direction at co-resolution")
    log.info(f"{'Lead':>6} {'Threshold':>10} {'Locked':>7} {'Same':>5} {'Opp':>5} {'WR':>7} {'% locked':>9}")
    log.info("-" * 55)

    for key, r in sorted(edge_results.items()):
        if r["parent"] == "15M" and r["child"] == "5M":
            log.info(
                f"{r['lead']:>6} >{r['threshold_bps']:>4}bps"
                f" {r['locked']:>7} {r['same_dir']:>5} {r['opposite_dir']:>5}"
                f" {r['wr_same']:>6.1f}% {r['pct_locked']:>8.1f}%"
            )

    # Print 1H → 15M results
    log.info("\n### 1H locked → 15M same direction at co-resolution")
    log.info(f"{'Lead':>6} {'Threshold':>10} {'Locked':>7} {'Same':>5} {'Opp':>5} {'WR':>7} {'% locked':>9}")
    log.info("-" * 55)

    for key, r in sorted(edge_results.items()):
        if r["parent"] == "1H" and r["child"] == "15M":
            log.info(
                f"{r['lead']:>6} >{r['threshold_bps']:>4}bps"
                f" {r['locked']:>7} {r['same_dir']:>5} {r['opposite_dir']:>5}"
                f" {r['wr_same']:>6.1f}% {r['pct_locked']:>8.1f}%"
            )

    # Print 1H → 5M results
    log.info("\n### 1H locked → 5M same direction at co-resolution")
    log.info(f"{'Lead':>6} {'Threshold':>10} {'Locked':>7} {'Same':>5} {'Opp':>5} {'WR':>7} {'% locked':>9}")
    log.info("-" * 55)

    for key, r in sorted(edge_results.items()):
        if r["parent"] == "1H" and r["child"] == "5M":
            log.info(
                f"{r['lead']:>6} >{r['threshold_bps']:>4}bps"
                f" {r['locked']:>7} {r['same_dir']:>5} {r['opposite_dir']:>5}"
                f" {r['wr_same']:>6.1f}% {r['pct_locked']:>8.1f}%"
            )

    # ── Section 3b: Conditional edge ──
    log.info("\n" + "=" * 80)
    log.info("SECTION 3b: CONDITIONAL EDGE (15M locked AND 5M uncertain)")
    log.info("=" * 80)

    # This is printed inside calculate_theoretical_pnl
    # But let's print a focused table here
    cond = analyze_conditional_edge(snapshots)
    log.info(f"\n{'Lead':>6} {'15M lock':>9} {'5M unc':>7} {'N':>5} {'Same':>5} {'WR':>7} {'Edge vs 50%':>12}")
    log.info("-" * 60)
    for r in cond:
        if r["n"] >= 10:
            edge = r["wr"] - 50
            log.info(
                f"{r['lead']:>6} >{r['lock_bps']:>3}bps  <{r['uncertain_bps']:>2}bps"
                f" {r['n']:>5} {r['same_dir']:>5} {r['wr']:>6.1f}% {edge:>+10.1f}pp"
            )

    # ── Section 4: PnL ──
    calculate_theoretical_pnl(snapshots, edge_results)

    # ── Section 5: Momentum combo ──
    analyze_momentum_combo(snapshots)

    # ── Section 6: 1H → next 15M ──
    analyze_1h_next_window(candles, snapshots)

    # ── Divergence ──
    analyze_divergence(snapshots)

    # ── Final minute dynamics ──
    analyze_final_minute_dynamics(candles, snapshots)

    # ── Concrete examples ──
    print_concrete_examples(snapshots)

    # ── SUMMARY ──
    log.info("\n" + "=" * 80)
    log.info("EXECUTIVE SUMMARY")
    log.info("=" * 80)

    # Find best conditional edge
    best_cond = max([r for r in cond if r["n"] >= 20], key=lambda x: x["wr"], default=None)
    if best_cond:
        log.info(f"\nBest conditional edge (n>=20):")
        log.info(f"  {best_cond['lead']}, 15M locked >{best_cond['lock_bps']}bps, 5M uncertain <{best_cond['uncertain_bps']}bps")
        log.info(f"  N={best_cond['n']}, WR={best_cond['wr']:.1f}%, edge vs 50% = {best_cond['wr']-50:+.1f}pp")
        ev_50 = best_cond["wr"]/100 * 0.50 - (1 - best_cond["wr"]/100) * 0.50
        log.info(f"  EV per $1 bet at 50c: ${ev_50:+.4f}")
        log.info(f"  Daily opportunities: {best_cond['n']/7:.1f}")
        log.info(f"  Daily EV per $1/trade: ${ev_50 * best_cond['n']/7:+.2f}")

    # Key finding: 5M vs 15M direction agreement
    total_nf = sum(1 for s in snapshots if s["dir_5m"] != "FLAT" and s["dir_15m"] != "FLAT")
    same_nf = sum(1 for s in snapshots if s["dir_5m"] != "FLAT" and s["dir_15m"] != "FLAT" and s["dir_5m"] == s["dir_15m"])
    log.info(f"\nBaseline: 5M and 15M resolve same direction {same_nf}/{total_nf} = {same_nf/total_nf*100:.1f}% of the time")

    log.info("\nDone.")


if __name__ == "__main__":
    main()
