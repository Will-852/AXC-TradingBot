#!/usr/bin/env python3
"""
Multi-Timeframe Overlap Signal Analysis for Polymarket
=======================================================
Core insight: Polymarket windows of different timeframes OVERLAP in time.
When a longer timeframe's direction is "locked" (large BTC move from open),
shorter timeframe windows opening within that period inherit a free signal.

Example:
  - 15M window opened at T-10min, resolves at T+5min
  - 5M window opens at T (current price, ~50/50 market)
  - If 15M is locked (BTC moved >10bps from 15M open), the 5M
    inheriting that direction is a near-free edge vs 50/50 pricing.

Sections:
  1. Map ALL overlap points across timeframes (5M, 15M, 1H, 4H)
  2. Calculate "locked direction" signals
  3. Backtest: 5M WR when parent timeframe is locked vs not
  4. Pricing edge estimation
  5. Multi-timeframe strategy design
"""

import json
import logging
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────
BASE = Path("/Users/wai/projects/axc-trading")
BTC_PATH = BASE / "polymarket/analysis/btc_1m_7days.json"
REPORT_PATH = BASE / "polymarket/analysis/overlap_signal_report.md"

# Polymarket timeframes (seconds)
TF_5M = 300
TF_15M = 900
TF_1H = 3600
TF_4H = 14400

ALL_TFS = {
    "5M": TF_5M,
    "15M": TF_15M,
    "1H": TF_1H,
    "4H": TF_4H,
}

# Locked direction thresholds (basis points)
# Why these values: from prior backtests, >10bps at 10min into 15M = 95%+ directional lock
LOCK_THRESHOLDS_BPS = {
    "5M": {"elapsed_s": 180, "bps": 8},     # 3min into 5M, >8bps
    "15M": {"elapsed_s": 600, "bps": 10},    # 10min into 15M, >10bps
    "1H": {"elapsed_s": 2700, "bps": 20},    # 45min into 1H, >20bps
    "4H": {"elapsed_s": 10800, "bps": 40},   # 3h into 4H, >40bps
}

# Additional lock check points for sensitivity analysis
MULTI_CHECK_POINTS = {
    "15M": [
        {"elapsed_s": 300, "label": "5min"},
        {"elapsed_s": 600, "label": "10min"},
        {"elapsed_s": 720, "label": "12min"},
    ],
    "1H": [
        {"elapsed_s": 1800, "label": "30min"},
        {"elapsed_s": 2700, "label": "45min"},
        {"elapsed_s": 3300, "label": "55min"},
    ],
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ── Data Loading ───────────────────────────────────────────────────────────

def load_btc_data():
    """Load BTC 1M klines. Returns list of dicts with ts (ms), open, close, etc."""
    with open(BTC_PATH) as f:
        return json.load(f)


def build_price_index(candles):
    """
    Build ts_sec -> candle lookup.
    Why dict instead of interpolation: 1M data has no gaps in our dataset,
    exact lookup is reliable and fast.
    """
    idx = {}
    for c in candles:
        ts_sec = int(c["ts"] / 1000)
        idx[ts_sec] = c
    return idx


def get_price_at(price_idx, ts_sec, field="open"):
    """Get price at or near ts_sec. Searches +-5 candles if exact miss."""
    if ts_sec in price_idx:
        return price_idx[ts_sec][field]
    # Search nearby (1M data might have small gaps)
    for offset in range(1, 6):
        for direction in [1, -1]:
            check = ts_sec + direction * 60 * offset
            if check in price_idx:
                return price_idx[check][field]
    return None


# ── Section 1: Map ALL Overlap Points ──────────────────────────────────────

def find_overlap_points(start_ts, end_ts):
    """
    For a 24h period, find every moment where multiple timeframes
    share the same resolution time.

    Resolution time = window_start + window_duration.
    Windows start at multiples of their duration (aligned to epoch).
    """
    overlaps = defaultdict(list)  # resolution_ts -> list of (tf_name, window_start)

    for tf_name, tf_seconds in ALL_TFS.items():
        # First window that starts at or after start_ts
        first_window = (start_ts // tf_seconds) * tf_seconds
        if first_window < start_ts:
            first_window += tf_seconds

        ts = first_window
        while ts + tf_seconds <= end_ts + tf_seconds:
            resolution_ts = ts + tf_seconds
            overlaps[resolution_ts].append((tf_name, ts))
            ts += tf_seconds

    # Filter to only times with 2+ timeframes resolving
    multi_overlaps = {
        res_ts: tfs for res_ts, tfs in overlaps.items()
        if len(tfs) >= 2 and start_ts <= res_ts <= end_ts + tf_seconds
    }

    return multi_overlaps


def analyze_overlap_structure():
    """
    Calculate overlap frequency for a full 24h period.
    Returns structured data about overlap patterns.
    """
    # Use a clean 24h period (midnight to midnight UTC)
    day_start = 0  # epoch-aligned, any 24h works due to periodicity
    day_end = 86400

    overlaps = find_overlap_points(day_start, day_end)

    # Categorize by overlap type
    categories = defaultdict(int)
    for res_ts, tfs in overlaps.items():
        tf_names = sorted([t[0] for t in tfs], key=lambda x: ALL_TFS[x])
        key = " + ".join(tf_names)
        categories[key] += 1

    # Count per combination
    results = {
        "total_5m_windows": 288,       # 24h / 5min = 288
        "total_15m_windows": 96,       # 24h / 15min = 96
        "total_1h_windows": 24,        # 24h / 1h = 24
        "total_4h_windows": 6,         # 24h / 4h = 6
        "overlap_categories": dict(categories),
        "5m_15m_simultaneous": 96,     # every 15min = every 3rd 5M
        "5m_15m_1h_simultaneous": 24,  # every 1h = every 4th 15M
        "5m_15m_1h_4h_simultaneous": 6, # every 4h
    }

    return results


# ── Section 2: Calculate Locked Direction Signals ──────────────────────────

def calculate_locked_signals(candles, price_idx):
    """
    For each timeframe window in the dataset, determine if the direction
    becomes "locked" before the window ends.

    Locked = BTC has moved enough bps from window open that the resolution
    direction is >95% certain.
    """
    results = {}

    for tf_name, tf_seconds in ALL_TFS.items():
        if tf_name not in LOCK_THRESHOLDS_BPS:
            continue

        config = LOCK_THRESHOLDS_BPS[tf_name]
        check_elapsed = config["elapsed_s"]
        threshold_bps = config["bps"]

        windows = []
        first_ts = int(candles[0]["ts"] / 1000)
        last_ts = int(candles[-1]["ts"] / 1000)

        # Find all windows in data range
        window_start = (first_ts // tf_seconds) * tf_seconds
        while window_start + tf_seconds <= last_ts:
            open_price = get_price_at(price_idx, window_start, "open")
            check_price = get_price_at(price_idx, window_start + check_elapsed, "close")
            close_price = get_price_at(price_idx, window_start + tf_seconds - 60, "close")

            if open_price and check_price and close_price:
                return_at_check_bps = (check_price - open_price) / open_price * 10000
                final_return_bps = (close_price - open_price) / open_price * 10000

                is_locked = abs(return_at_check_bps) > threshold_bps
                locked_dir = "UP" if return_at_check_bps > 0 else "DOWN"
                actual_dir = "UP" if final_return_bps > 0 else "DOWN"
                correct = locked_dir == actual_dir if is_locked else None

                windows.append({
                    "window_start": window_start,
                    "open_price": open_price,
                    "check_price": check_price,
                    "close_price": close_price,
                    "return_at_check_bps": return_at_check_bps,
                    "final_return_bps": final_return_bps,
                    "is_locked": is_locked,
                    "locked_dir": locked_dir if is_locked else None,
                    "actual_dir": actual_dir,
                    "correct": correct,
                })

            window_start += tf_seconds

        # Stats
        total = len(windows)
        locked = [w for w in windows if w["is_locked"]]
        correct = [w for w in locked if w["correct"]]

        results[tf_name] = {
            "total_windows": total,
            "locked_count": len(locked),
            "locked_pct": len(locked) / total * 100 if total else 0,
            "locked_correct": len(correct),
            "locked_wr": len(correct) / len(locked) * 100 if locked else 0,
            "check_elapsed_s": check_elapsed,
            "threshold_bps": threshold_bps,
            "windows": windows,
        }

    return results


def multi_threshold_sensitivity(candles, price_idx):
    """
    Test multiple lock thresholds for sensitivity analysis.
    Shows how WR changes with different bps thresholds.
    """
    results = {}

    for tf_name, checkpoints in MULTI_CHECK_POINTS.items():
        tf_seconds = ALL_TFS[tf_name]
        tf_results = []

        first_ts = int(candles[0]["ts"] / 1000)
        last_ts = int(candles[-1]["ts"] / 1000)

        for cp in checkpoints:
            check_elapsed = cp["elapsed_s"]
            for threshold_bps in [5, 8, 10, 15, 20, 30]:
                windows_checked = 0
                locked_count = 0
                correct_count = 0

                window_start = (first_ts // tf_seconds) * tf_seconds
                while window_start + tf_seconds <= last_ts:
                    open_price = get_price_at(price_idx, window_start, "open")
                    check_price = get_price_at(price_idx, window_start + check_elapsed, "close")
                    close_price = get_price_at(price_idx, window_start + tf_seconds - 60, "close")

                    if open_price and check_price and close_price:
                        windows_checked += 1
                        ret_bps = (check_price - open_price) / open_price * 10000
                        final_bps = (close_price - open_price) / open_price * 10000

                        if abs(ret_bps) > threshold_bps:
                            locked_count += 1
                            if (ret_bps > 0) == (final_bps > 0):
                                correct_count += 1

                    window_start += tf_seconds

                tf_results.append({
                    "check_label": cp["label"],
                    "threshold_bps": threshold_bps,
                    "total_windows": windows_checked,
                    "locked_count": locked_count,
                    "locked_pct": locked_count / windows_checked * 100 if windows_checked else 0,
                    "correct": correct_count,
                    "wr": correct_count / locked_count * 100 if locked_count else 0,
                })

        results[tf_name] = tf_results

    return results


# ── Section 3: Backtest the Overlap Signal ─────────────────────────────────

def backtest_overlap_signal(candles, price_idx):
    """
    Core backtest: For every 5M window that starts during the last 5 min
    of a 15M window, check if the parent's locked direction predicts 5M.

    Also tests: 5M windows within locked 1H windows.
    """
    first_ts = int(candles[0]["ts"] / 1000)
    last_ts = int(candles[-1]["ts"] / 1000)

    results = {}

    # ── 15M -> 5M overlap ──
    parent_tf = TF_15M
    child_tf = TF_5M

    # For multiple elapsed checkpoints in the parent
    for parent_elapsed_label, parent_elapsed_s, parent_threshold_bps in [
        ("10min", 600, 10),
        ("10min", 600, 15),
        ("10min", 600, 20),
        ("12min", 720, 10),
        ("12min", 720, 15),
    ]:
        with_signal = []   # 5M windows where parent is locked
        without_signal = []  # 5M windows where parent is NOT locked

        window_5m = (first_ts // child_tf) * child_tf
        while window_5m + child_tf <= last_ts:
            # Find the parent 15M window this 5M resolves within
            # A 5M window at time T resolves at T+300
            # The 15M window containing T+300 started at floor(T+300, 900) - 900... no.
            # The 15M window that ALSO resolves at T+300:
            #   15M resolution = 15M_start + 900
            #   We need 15M_start + 900 == window_5m + 300
            #   => 15M_start = window_5m + 300 - 900 = window_5m - 600
            # This is only valid if window_5m - 600 is aligned to 15M grid
            parent_start_candidate = window_5m + child_tf - parent_tf
            if parent_start_candidate % parent_tf == 0 and parent_start_candidate >= first_ts:
                # This 5M's resolution aligns with a 15M resolution
                # At this point, the 15M has been running for:
                # window_5m - parent_start_candidate = 600 seconds (10 min)

                parent_open = get_price_at(price_idx, parent_start_candidate, "open")
                child_open = get_price_at(price_idx, window_5m, "open")
                child_close = get_price_at(price_idx, window_5m + child_tf - 60, "close")

                # Check parent state at the moment the child opens
                parent_check_price = get_price_at(price_idx, window_5m, "close")

                if parent_open and child_open and child_close and parent_check_price:
                    parent_ret_bps = (parent_check_price - parent_open) / parent_open * 10000
                    child_ret_bps = (child_close - child_open) / child_open * 10000
                    child_dir = "UP" if child_ret_bps > 0 else "DOWN"
                    parent_dir = "UP" if parent_ret_bps > 0 else "DOWN"

                    entry = {
                        "window_5m": window_5m,
                        "parent_start": parent_start_candidate,
                        "parent_ret_bps": parent_ret_bps,
                        "child_ret_bps": child_ret_bps,
                        "child_dir": child_dir,
                        "parent_dir": parent_dir,
                        "match": child_dir == parent_dir,
                    }

                    if abs(parent_ret_bps) > parent_threshold_bps:
                        with_signal.append(entry)
                    else:
                        without_signal.append(entry)

            window_5m += child_tf

        key = f"15M->{{'5M'}} @{parent_elapsed_label} >{parent_threshold_bps}bps"
        ws_correct = sum(1 for e in with_signal if e["match"])
        wo_correct = sum(1 for e in without_signal if e["match"])

        results[key] = {
            "with_signal_total": len(with_signal),
            "with_signal_correct": ws_correct,
            "with_signal_wr": ws_correct / len(with_signal) * 100 if with_signal else 0,
            "without_signal_total": len(without_signal),
            "without_signal_correct": wo_correct,
            "without_signal_wr": wo_correct / len(without_signal) * 100 if without_signal else 0,
            "wr_lift": (
                (ws_correct / len(with_signal) * 100 if with_signal else 0)
                - (wo_correct / len(without_signal) * 100 if without_signal else 0)
            ),
            "details_with": with_signal,
            "details_without": without_signal,
        }

    # ── ALL 5M windows within a locked 15M (not just co-resolving) ──
    for parent_threshold_bps in [10, 15, 20]:
        with_signal = []
        without_signal = []

        window_5m = (first_ts // child_tf) * child_tf
        while window_5m + child_tf <= last_ts:
            # Find the 15M window that CONTAINS this 5M window's start
            parent_start = (window_5m // parent_tf) * parent_tf

            # How far into the 15M window is this 5M window?
            elapsed_in_parent = window_5m - parent_start

            # Only consider 5M windows that start in the LAST 5 min of a 15M
            # (i.e., the 15M has been running 10+ min, direction more certain)
            if elapsed_in_parent >= 600:  # 10 min into 15M
                parent_open = get_price_at(price_idx, parent_start, "open")
                parent_now = get_price_at(price_idx, window_5m, "close")
                child_open = get_price_at(price_idx, window_5m, "open")
                child_close = get_price_at(price_idx, window_5m + child_tf - 60, "close")

                if parent_open and parent_now and child_open and child_close:
                    parent_ret_bps = (parent_now - parent_open) / parent_open * 10000
                    child_ret_bps = (child_close - child_open) / child_open * 10000
                    child_dir = "UP" if child_ret_bps > 0 else "DOWN"
                    parent_dir = "UP" if parent_ret_bps > 0 else "DOWN"

                    entry = {
                        "window_5m": window_5m,
                        "parent_start": parent_start,
                        "elapsed_in_parent": elapsed_in_parent,
                        "parent_ret_bps": parent_ret_bps,
                        "child_ret_bps": child_ret_bps,
                        "child_dir": child_dir,
                        "parent_dir": parent_dir,
                        "match": child_dir == parent_dir,
                    }

                    if abs(parent_ret_bps) > parent_threshold_bps:
                        with_signal.append(entry)
                    else:
                        without_signal.append(entry)

            window_5m += child_tf

        key = f"15M_any->5M (last5min) >{parent_threshold_bps}bps"
        ws_correct = sum(1 for e in with_signal if e["match"])
        wo_correct = sum(1 for e in without_signal if e["match"])

        results[key] = {
            "with_signal_total": len(with_signal),
            "with_signal_correct": ws_correct,
            "with_signal_wr": ws_correct / len(with_signal) * 100 if with_signal else 0,
            "without_signal_total": len(without_signal),
            "without_signal_correct": wo_correct,
            "without_signal_wr": wo_correct / len(without_signal) * 100 if without_signal else 0,
            "wr_lift": (
                (ws_correct / len(with_signal) * 100 if with_signal else 0)
                - (wo_correct / len(without_signal) * 100 if without_signal else 0)
            ),
        }

    # ── 1H -> 15M overlap ──
    parent_tf_1h = TF_1H
    child_tf_15m = TF_15M

    for parent_threshold_bps in [15, 20, 30]:
        with_signal = []
        without_signal = []

        window_15m = (first_ts // child_tf_15m) * child_tf_15m
        while window_15m + child_tf_15m <= last_ts:
            # Find the 1H window containing this 15M
            parent_start = (window_15m // parent_tf_1h) * parent_tf_1h
            elapsed_in_parent = window_15m - parent_start

            # 15M windows that start 45+ min into 1H (last 15 min of 1H)
            if elapsed_in_parent >= 2700:
                parent_open = get_price_at(price_idx, parent_start, "open")
                parent_now = get_price_at(price_idx, window_15m, "close")
                child_open = get_price_at(price_idx, window_15m, "open")
                child_close = get_price_at(price_idx, window_15m + child_tf_15m - 60, "close")

                if parent_open and parent_now and child_open and child_close:
                    parent_ret_bps = (parent_now - parent_open) / parent_open * 10000
                    child_ret_bps = (child_close - child_open) / child_open * 10000
                    child_dir = "UP" if child_ret_bps > 0 else "DOWN"
                    parent_dir = "UP" if parent_ret_bps > 0 else "DOWN"

                    entry = {
                        "window_15m": window_15m,
                        "parent_start": parent_start,
                        "parent_ret_bps": parent_ret_bps,
                        "child_ret_bps": child_ret_bps,
                        "match": child_dir == parent_dir,
                    }

                    if abs(parent_ret_bps) > parent_threshold_bps:
                        with_signal.append(entry)
                    else:
                        without_signal.append(entry)

            window_15m += child_tf_15m

        key = f"1H->15M (last15min) >{parent_threshold_bps}bps"
        ws_correct = sum(1 for e in with_signal if e["match"])
        wo_correct = sum(1 for e in without_signal if e["match"])

        results[key] = {
            "with_signal_total": len(with_signal),
            "with_signal_correct": ws_correct,
            "with_signal_wr": ws_correct / len(with_signal) * 100 if with_signal else 0,
            "without_signal_total": len(without_signal),
            "without_signal_correct": wo_correct,
            "without_signal_wr": wo_correct / len(without_signal) * 100 if without_signal else 0,
            "wr_lift": (
                (ws_correct / len(with_signal) * 100 if with_signal else 0)
                - (wo_correct / len(without_signal) * 100 if without_signal else 0)
            ),
        }

    # ── 1H -> 5M overlap (deep cascade) ──
    for parent_threshold_bps in [20, 30]:
        with_signal = []
        without_signal = []

        window_5m = (first_ts // TF_5M) * TF_5M
        while window_5m + TF_5M <= last_ts:
            parent_start = (window_5m // TF_1H) * TF_1H
            elapsed_in_parent = window_5m - parent_start

            # 5M windows in last 15 min of 1H
            if elapsed_in_parent >= 2700:
                parent_open = get_price_at(price_idx, parent_start, "open")
                parent_now = get_price_at(price_idx, window_5m, "close")
                child_open = get_price_at(price_idx, window_5m, "open")
                child_close = get_price_at(price_idx, window_5m + TF_5M - 60, "close")

                if parent_open and parent_now and child_open and child_close:
                    parent_ret_bps = (parent_now - parent_open) / parent_open * 10000
                    child_ret_bps = (child_close - child_open) / child_open * 10000
                    child_dir = "UP" if child_ret_bps > 0 else "DOWN"
                    parent_dir = "UP" if parent_ret_bps > 0 else "DOWN"

                    entry = {
                        "window_5m": window_5m,
                        "parent_ret_bps": parent_ret_bps,
                        "child_ret_bps": child_ret_bps,
                        "match": child_dir == parent_dir,
                    }

                    if abs(parent_ret_bps) > parent_threshold_bps:
                        with_signal.append(entry)
                    else:
                        without_signal.append(entry)

            window_5m += TF_5M

        key = f"1H->5M (last15min) >{parent_threshold_bps}bps"
        ws_correct = sum(1 for e in with_signal if e["match"])
        wo_correct = sum(1 for e in without_signal if e["match"])

        results[key] = {
            "with_signal_total": len(with_signal),
            "with_signal_correct": ws_correct,
            "with_signal_wr": ws_correct / len(with_signal) * 100 if with_signal else 0,
            "without_signal_total": len(without_signal),
            "without_signal_correct": wo_correct,
            "without_signal_wr": wo_correct / len(without_signal) * 100 if without_signal else 0,
            "wr_lift": (
                (ws_correct / len(with_signal) * 100 if with_signal else 0)
                - (wo_correct / len(without_signal) * 100 if without_signal else 0)
            ),
        }

    return results


# ── Section 4: Pricing Edge ────────────────────────────────────────────────

def calculate_pricing_edge(candles, price_idx):
    """
    When parent timeframe is locked, the child opens at ~50/50.
    True probability is higher -> the gap = edge.

    For each locked 15M window, simulate:
    - 5M opens at T (within locked 15M)
    - Market prices 5M at 50/50
    - True probability of 5M matching 15M direction = our backtest WR
    - Edge = true_prob - market_price (50%)
    - Expected value per $1 bet = edge * 2 (binary payout)
    """
    first_ts = int(candles[0]["ts"] / 1000)
    last_ts = int(candles[-1]["ts"] / 1000)

    # Collect all 5M outcomes within locked 15M windows
    edges = []
    for threshold_bps in [10, 15, 20, 25, 30]:
        match_count = 0
        total_count = 0

        window_5m = (first_ts // TF_5M) * TF_5M
        while window_5m + TF_5M <= last_ts:
            parent_start = (window_5m // TF_15M) * TF_15M
            elapsed_in_parent = window_5m - parent_start

            if elapsed_in_parent >= 600:  # last 5 min of 15M
                parent_open = get_price_at(price_idx, parent_start, "open")
                parent_now = get_price_at(price_idx, window_5m, "close")
                child_open = get_price_at(price_idx, window_5m, "open")
                child_close = get_price_at(price_idx, window_5m + TF_5M - 60, "close")

                if parent_open and parent_now and child_open and child_close:
                    parent_ret_bps = (parent_now - parent_open) / parent_open * 10000

                    if abs(parent_ret_bps) > threshold_bps:
                        total_count += 1
                        child_ret_bps = (child_close - child_open) / child_open * 10000
                        parent_dir = "UP" if parent_ret_bps > 0 else "DOWN"
                        child_dir = "UP" if child_ret_bps > 0 else "DOWN"
                        if parent_dir == child_dir:
                            match_count += 1

            window_5m += TF_5M

        if total_count > 0:
            true_prob = match_count / total_count
            market_price = 0.50  # assumption: fresh 5M opens at ~50/50
            edge = true_prob - market_price
            # EV per $1 bet on the locked direction
            # Win: pay market_price, receive $1 -> profit = 1 - market_price
            # Lose: pay market_price, receive $0 -> loss = market_price
            ev_per_dollar = true_prob * (1 - market_price) - (1 - true_prob) * market_price
            # Equivalent: ev_per_dollar = true_prob - market_price = edge

            edges.append({
                "threshold_bps": threshold_bps,
                "total_signals": total_count,
                "match_count": match_count,
                "true_prob": true_prob * 100,
                "market_price_cents": 50,
                "edge_pct": edge * 100,
                "ev_per_dollar": ev_per_dollar,
                "daily_signals_est": total_count / 7,  # 7 day dataset
                "daily_ev_est": total_count / 7 * ev_per_dollar,
            })

    return edges


# ── Section 5: Multi-Timeframe Cascade ─────────────────────────────────────

def cascade_signal_analysis(candles, price_idx):
    """
    The ultimate signal: when MULTIPLE parent timeframes are locked
    in the same direction, the child window gets compounding confirmation.

    Example: 1H locked DOWN + 15M locked DOWN -> 5M DOWN signal is very strong.
    """
    first_ts = int(candles[0]["ts"] / 1000)
    last_ts = int(candles[-1]["ts"] / 1000)

    # For each 5M window, check how many parent timeframes are locked
    cascade_results = {
        0: {"total": 0, "correct": 0},  # no parents locked
        1: {"total": 0, "correct": 0},  # 1 parent locked
        2: {"total": 0, "correct": 0},  # 2 parents locked (15M + 1H)
        3: {"total": 0, "correct": 0},  # 3 parents locked (15M + 1H + 4H)
    }

    # Also track which directions match
    details = []

    window_5m = (first_ts // TF_5M) * TF_5M
    while window_5m + TF_5M <= last_ts:
        child_open = get_price_at(price_idx, window_5m, "open")
        child_close = get_price_at(price_idx, window_5m + TF_5M - 60, "close")

        if not (child_open and child_close):
            window_5m += TF_5M
            continue

        child_ret_bps = (child_close - child_open) / child_open * 10000
        child_dir = "UP" if child_ret_bps > 0 else "DOWN"

        locked_parents = []
        consensus_dir = None

        # Check each parent timeframe
        for parent_name, parent_tf, min_elapsed, threshold_bps in [
            ("15M", TF_15M, 600, 10),
            ("1H", TF_1H, 2700, 20),
            ("4H", TF_4H, 10800, 40),
        ]:
            parent_start = (window_5m // parent_tf) * parent_tf
            elapsed = window_5m - parent_start

            if elapsed >= min_elapsed:
                parent_open = get_price_at(price_idx, parent_start, "open")
                parent_now = get_price_at(price_idx, window_5m, "close")

                if parent_open and parent_now:
                    parent_ret = (parent_now - parent_open) / parent_open * 10000
                    if abs(parent_ret) > threshold_bps:
                        parent_dir = "UP" if parent_ret > 0 else "DOWN"
                        locked_parents.append((parent_name, parent_dir, parent_ret))

        # Determine consensus
        n_locked = len(locked_parents)
        if n_locked > 0:
            dirs = [lp[1] for lp in locked_parents]
            if all(d == dirs[0] for d in dirs):
                consensus_dir = dirs[0]
                correct = child_dir == consensus_dir
            else:
                # Conflicting signals -- count as 0 for safety
                n_locked = 0
                correct = False
        else:
            correct = False  # no signal

        cascade_results[n_locked]["total"] += 1
        if n_locked > 0 and correct:
            cascade_results[n_locked]["correct"] += 1

        if n_locked >= 2:
            details.append({
                "window_5m": window_5m,
                "child_dir": child_dir,
                "locked_parents": locked_parents,
                "consensus_dir": consensus_dir,
                "correct": correct,
            })

        window_5m += TF_5M

    # Compute WRs
    for k in cascade_results:
        r = cascade_results[k]
        r["wr"] = r["correct"] / r["total"] * 100 if r["total"] else 0

    return cascade_results, details


# ── Section 6: Concrete Strategy Parameters ────────────────────────────────

def design_strategy(overlap_bt, pricing_edges, cascade_results):
    """
    Synthesize all findings into actionable strategy parameters.
    Returns strategy spec dict.
    """
    # Find best edge configuration
    best_edge = max(pricing_edges, key=lambda e: e["ev_per_dollar"] * e["total_signals"])

    strategy = {
        "name": "Multi-Timeframe Overlap Signal",
        "entry_rules": [
            {
                "rule": "15M LOCKED -> 5M LEAN",
                "condition": f"15M return > {best_edge['threshold_bps']}bps at T+600s",
                "action": "Lean 5M in same direction as 15M",
                "sizing": "5:1 ratio (80% locked dir, 20% opposite)",
                "expected_wr": f"{best_edge['true_prob']:.1f}%",
                "edge_vs_50": f"{best_edge['edge_pct']:.1f}%",
            },
            {
                "rule": "CASCADE (15M + 1H LOCKED) -> 5M STRONG LEAN",
                "condition": "Both 15M (>10bps@10min) AND 1H (>20bps@45min) locked same dir",
                "action": "Lean 5M heavily in consensus direction",
                "sizing": "8:1 ratio or skip opposite side entirely",
                "expected_wr": f"{cascade_results[2]['wr']:.1f}%",
                "frequency": f"~{cascade_results[2]['total']} in 7 days",
            },
        ],
        "timing": {
            "5m_within_15m": "Only enter 5M in last 5 min of 15M (T+600 to T+900)",
            "15m_within_1h": "Only enter 15M in last 15 min of 1H (T+2700 to T+3600)",
            "cascade_window": "When multiple parents locked, ANY child in window gets signal",
        },
        "risk": {
            "max_exposure_per_signal": "3% of bankroll",
            "daily_cap": "15 signals/day (limited by overlap windows)",
            "stop_loss": "None (binary hold to resolution)",
        },
        "best_edge_config": best_edge,
    }

    return strategy


# ── Report Generation ──────────────────────────────────────────────────────

def generate_report(
    overlap_structure,
    locked_signals,
    sensitivity,
    overlap_bt,
    pricing_edges,
    cascade_results,
    cascade_details,
    strategy,
):
    """Generate comprehensive markdown report."""
    lines = []
    lines.append("# Multi-Timeframe Overlap Signal Analysis")
    lines.append(f"> Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"> Data: BTC 1M klines, 7 days (2026-03-16 to 2026-03-23)")
    lines.append(f"> Total candles: 10,080")
    lines.append("")

    # ── Section 1: Overlap Map ──
    lines.append("---")
    lines.append("## 1. Overlap Point Map (24h period)")
    lines.append("")
    lines.append("### Window counts per day")
    lines.append("| Timeframe | Windows/Day | Resolution Interval |")
    lines.append("|-----------|-------------|---------------------|")
    lines.append(f"| 5M  | {overlap_structure['total_5m_windows']} | Every 5 min |")
    lines.append(f"| 15M | {overlap_structure['total_15m_windows']} | Every 15 min |")
    lines.append(f"| 1H  | {overlap_structure['total_1h_windows']} | Every 60 min |")
    lines.append(f"| 4H  | {overlap_structure['total_4h_windows']} | Every 240 min |")
    lines.append("")
    lines.append("### Simultaneous resolution points per day")
    lines.append("| Overlap | Count/Day | Pattern |")
    lines.append("|---------|-----------|---------|")
    lines.append(f"| 5M + 15M | {overlap_structure['5m_15m_simultaneous']} | Every 3rd 5M = every 15 min |")
    lines.append(f"| 5M + 15M + 1H | {overlap_structure['5m_15m_1h_simultaneous']} | Every 12th 5M = every 60 min |")
    lines.append(f"| 5M + 15M + 1H + 4H | {overlap_structure['5m_15m_1h_4h_simultaneous']} | Every 48th 5M = every 240 min |")
    lines.append("")
    lines.append("### Key insight: Exploitable windows per day")
    lines.append("- **5M windows starting in last 5min of a 15M**: 96/day (every 15M has exactly one such 5M)")
    lines.append("- **5M windows starting in last 15min of a 1H**: 72/day (3 x 5M per 1H tail)")
    lines.append("- **15M windows starting in last 15min of a 1H**: 24/day (1 x 15M per 1H tail)")
    lines.append("")

    # ── Section 2: Locked Signals ──
    lines.append("---")
    lines.append("## 2. Locked Direction Signals")
    lines.append("")
    lines.append("### Primary lock check")
    lines.append("| TF | Check Point | Threshold | Total Windows | Locked | Locked % | Correct | WR |")
    lines.append("|----|-----------  |-----------|---------------|--------|----------|---------|-----|")
    for tf_name in ["15M", "1H"]:
        if tf_name in locked_signals:
            ls = locked_signals[tf_name]
            lines.append(
                f"| {tf_name} | T+{ls['check_elapsed_s']}s "
                f"| >{ls['threshold_bps']}bps "
                f"| {ls['total_windows']} "
                f"| {ls['locked_count']} "
                f"| {ls['locked_pct']:.1f}% "
                f"| {ls['locked_correct']} "
                f"| **{ls['locked_wr']:.1f}%** |"
            )
    lines.append("")

    lines.append("### Sensitivity analysis: 15M lock accuracy by threshold")
    lines.append("| Check Point | Threshold (bps) | Locked Count | Locked % | WR |")
    lines.append("|-------------|-----------------|--------------|----------|-----|")
    if "15M" in sensitivity:
        for row in sensitivity["15M"]:
            lines.append(
                f"| {row['check_label']} | >{row['threshold_bps']} "
                f"| {row['locked_count']} "
                f"| {row['locked_pct']:.1f}% "
                f"| **{row['wr']:.1f}%** |"
            )
    lines.append("")

    lines.append("### Sensitivity analysis: 1H lock accuracy by threshold")
    lines.append("| Check Point | Threshold (bps) | Locked Count | Locked % | WR |")
    lines.append("|-------------|-----------------|--------------|----------|-----|")
    if "1H" in sensitivity:
        for row in sensitivity["1H"]:
            lines.append(
                f"| {row['check_label']} | >{row['threshold_bps']} "
                f"| {row['locked_count']} "
                f"| {row['locked_pct']:.1f}% "
                f"| **{row['wr']:.1f}%** |"
            )
    lines.append("")

    # ── Section 3: Overlap Backtest ──
    lines.append("---")
    lines.append("## 3. Overlap Signal Backtest")
    lines.append("")
    lines.append("### Core question: Does parent locked direction predict child direction?")
    lines.append("")
    lines.append("| Signal | With Signal (N) | WR (with) | Without Signal (N) | WR (without) | WR Lift |")
    lines.append("|--------|-----------------|-----------|-------------------|--------------|---------|")
    for key, bt in sorted(overlap_bt.items()):
        if "details_with" in bt:
            # Skip detail keys for table
            pass
        lines.append(
            f"| {key} "
            f"| {bt['with_signal_total']} "
            f"| **{bt['with_signal_wr']:.1f}%** "
            f"| {bt['without_signal_total']} "
            f"| {bt['without_signal_wr']:.1f}% "
            f"| **{bt['wr_lift']:+.1f}pp** |"
        )
    lines.append("")

    # ── Section 4: Pricing Edge ──
    lines.append("---")
    lines.append("## 4. Pricing Edge Estimation")
    lines.append("")
    lines.append("Assumption: Fresh 5M window opens at ~50c (50/50 pricing).")
    lines.append("If parent is locked, true probability != 50%. The gap = edge.")
    lines.append("")
    lines.append("| Parent Threshold | Signals (7d) | True Prob | Market Price | Edge | EV/$1 | Daily Signals | Daily EV |")
    lines.append("|-----------------|-------------|-----------|--------------|------|-------|---------------|----------|")
    for e in pricing_edges:
        lines.append(
            f"| >{e['threshold_bps']}bps "
            f"| {e['total_signals']} "
            f"| {e['true_prob']:.1f}% "
            f"| {e['market_price_cents']}c "
            f"| **{e['edge_pct']:.1f}%** "
            f"| ${e['ev_per_dollar']:.3f} "
            f"| {e['daily_signals_est']:.1f} "
            f"| **${e['daily_ev_est']:.2f}** |"
        )
    lines.append("")
    lines.append("*Note: Daily EV assumes $1 bet per signal. Scale linearly with bet size.*")
    lines.append("")

    # ── Section 5: Cascade ──
    lines.append("---")
    lines.append("## 5. Multi-Timeframe Cascade Signal")
    lines.append("")
    lines.append("How many parent timeframes are locked at the same time?")
    lines.append("")
    lines.append("| Parents Locked | Count (7d) | Correct | WR | Note |")
    lines.append("|---------------|-----------|---------|-----|------|")
    notes = {
        0: "Baseline (no signal)",
        1: "Single parent locked (15M or 1H)",
        2: "Double lock (15M + 1H same dir)",
        3: "Triple lock (15M + 1H + 4H)",
    }
    for k in sorted(cascade_results.keys()):
        r = cascade_results[k]
        lines.append(
            f"| {k} "
            f"| {r['total']} "
            f"| {r['correct']} "
            f"| **{r['wr']:.1f}%** "
            f"| {notes.get(k, '')} |"
        )
    lines.append("")

    if cascade_details:
        lines.append("### Sample cascade events (2+ parents locked)")
        lines.append("| Time (UTC) | 5M Dir | Parents | Consensus | Correct |")
        lines.append("|-----------|--------|---------|-----------|---------|")
        for d in cascade_details[:20]:  # show first 20
            ts_str = datetime.fromtimestamp(d["window_5m"], tz=timezone.utc).strftime("%m-%d %H:%M")
            parents_str = ", ".join(
                f"{p[0]}={p[1]}({p[2]:+.0f}bps)" for p in d["locked_parents"]
            )
            lines.append(
                f"| {ts_str} "
                f"| {d['child_dir']} "
                f"| {parents_str} "
                f"| {d['consensus_dir']} "
                f"| {'Y' if d['correct'] else 'N'} |"
            )
        lines.append("")

    # ── Section 6: Strategy ──
    lines.append("---")
    lines.append("## 6. Concrete Strategy: Multi-Timeframe Overlap")
    lines.append("")
    lines.append("### Entry Rules")
    for i, rule in enumerate(strategy["entry_rules"], 1):
        lines.append(f"**Rule {i}: {rule['rule']}**")
        lines.append(f"- Condition: {rule['condition']}")
        lines.append(f"- Action: {rule['action']}")
        lines.append(f"- Sizing: {rule['sizing']}")
        lines.append(f"- Expected WR: {rule['expected_wr']}")
        if "edge_vs_50" in rule:
            lines.append(f"- Edge vs 50/50: {rule['edge_vs_50']}")
        if "frequency" in rule:
            lines.append(f"- Frequency: {rule['frequency']}")
        lines.append("")

    lines.append("### Timing Windows")
    for k, v in strategy["timing"].items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")

    lines.append("### Risk Parameters")
    for k, v in strategy["risk"].items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")

    lines.append("### Implementation Pseudocode")
    lines.append("```python")
    lines.append("# Every 5 seconds (main loop):")
    lines.append("now = time.time()")
    lines.append("")
    lines.append("# Check parent timeframe locks")
    lines.append("parent_15m_start = (now // 900) * 900")
    lines.append("elapsed_15m = now - parent_15m_start")
    lines.append("if elapsed_15m >= 600:  # 10+ min into 15M")
    lines.append("    btc_return_15m = (btc_now - btc_at_15m_open) / btc_at_15m_open * 10000")
    lines.append(f"    if abs(btc_return_15m) > {strategy['best_edge_config']['threshold_bps']}:  # locked")
    lines.append("        locked_dir = 'UP' if btc_return_15m > 0 else 'DOWN'")
    lines.append("")
    lines.append("        # Find any 5M window opening now")
    lines.append("        window_5m_start = (now // 300) * 300")
    lines.append("        if now - window_5m_start < 30:  # just opened")
    lines.append("            # SIGNAL: lean 5M in locked_dir")
    lines.append("            lean_ratio = 5.0  # 5:1 in locked direction")
    lines.append("            place_5m_order(locked_dir, lean_ratio)")
    lines.append("```")
    lines.append("")

    # ── Summary ──
    lines.append("---")
    lines.append("## Summary")
    lines.append("")

    best = strategy["best_edge_config"]
    lines.append(f"- **Best edge config**: >{best['threshold_bps']}bps threshold")
    lines.append(f"- **True probability when locked**: {best['true_prob']:.1f}%")
    lines.append(f"- **Edge vs 50/50 market**: {best['edge_pct']:.1f}%")
    lines.append(f"- **Daily signal count**: ~{best['daily_signals_est']:.0f}")
    lines.append(f"- **Daily EV per $1/signal**: ${best['daily_ev_est']:.2f}")
    lines.append(f"- **Cascade (2+ parents)**: {cascade_results[2]['total']} events, "
                 f"{cascade_results[2]['wr']:.1f}% WR")
    lines.append("")

    # Critical caveats
    lines.append("### Caveats")
    lines.append("1. **Market price assumption**: Fresh 5M may NOT be exactly 50c. "
                 "Smart money may already price in the parent signal.")
    lines.append("2. **Sample size**: 7 days is small. Need 30+ days for statistical significance.")
    lines.append("3. **Execution**: Fill probability on 5M is unknown. "
                 "If market already adjusts, our limit orders won't fill.")
    lines.append("4. **Fee drag**: Polymarket fees (1-2%) eat into thin edges.")
    lines.append("5. **Regime dependency**: Works in trending markets, "
                 "may fail in choppy/reversal regimes.")
    lines.append("")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 70)
    log.info("Multi-Timeframe Overlap Signal Analysis")
    log.info("=" * 70)

    # Load data
    log.info("\nLoading BTC 1M data...")
    candles = load_btc_data()
    price_idx = build_price_index(candles)
    log.info(f"  Loaded {len(candles)} candles")
    first_dt = datetime.fromtimestamp(candles[0]["ts"] / 1000, tz=timezone.utc)
    last_dt = datetime.fromtimestamp(candles[-1]["ts"] / 1000, tz=timezone.utc)
    log.info(f"  Range: {first_dt} to {last_dt}")

    # Section 1: Overlap structure
    log.info("\n── Section 1: Overlap Point Map ──")
    overlap_structure = analyze_overlap_structure()
    log.info(f"  5M windows/day: {overlap_structure['total_5m_windows']}")
    log.info(f"  5M+15M simultaneous: {overlap_structure['5m_15m_simultaneous']}/day")
    log.info(f"  5M+15M+1H simultaneous: {overlap_structure['5m_15m_1h_simultaneous']}/day")
    log.info(f"  All 4 TF simultaneous: {overlap_structure['5m_15m_1h_4h_simultaneous']}/day")

    # Section 2: Locked signals
    log.info("\n── Section 2: Locked Direction Signals ──")
    locked_signals = calculate_locked_signals(candles, price_idx)
    for tf_name, ls in locked_signals.items():
        log.info(
            f"  {tf_name}: {ls['locked_count']}/{ls['total_windows']} locked "
            f"({ls['locked_pct']:.1f}%), WR={ls['locked_wr']:.1f}%"
        )

    log.info("\n  Sensitivity analysis...")
    sensitivity = multi_threshold_sensitivity(candles, price_idx)
    for tf_name, rows in sensitivity.items():
        log.info(f"  {tf_name}:")
        for r in rows:
            log.info(
                f"    @{r['check_label']} >{r['threshold_bps']}bps: "
                f"{r['locked_count']}/{r['total_windows']} locked, WR={r['wr']:.1f}%"
            )

    # Section 3: Overlap backtest
    log.info("\n── Section 3: Overlap Signal Backtest ──")
    overlap_bt = backtest_overlap_signal(candles, price_idx)
    for key, bt in sorted(overlap_bt.items()):
        log.info(
            f"  {key}: "
            f"with={bt['with_signal_total']}@{bt['with_signal_wr']:.1f}% "
            f"without={bt['without_signal_total']}@{bt['without_signal_wr']:.1f}% "
            f"lift={bt['wr_lift']:+.1f}pp"
        )

    # Section 4: Pricing edge
    log.info("\n── Section 4: Pricing Edge ──")
    pricing_edges = calculate_pricing_edge(candles, price_idx)
    for e in pricing_edges:
        log.info(
            f"  >{e['threshold_bps']}bps: "
            f"true_prob={e['true_prob']:.1f}%, "
            f"edge={e['edge_pct']:.1f}%, "
            f"EV/day=${e['daily_ev_est']:.2f} "
            f"({e['total_signals']} signals/7d)"
        )

    # Section 5: Cascade
    log.info("\n── Section 5: Multi-Timeframe Cascade ──")
    cascade_results, cascade_details = cascade_signal_analysis(candles, price_idx)
    for k, r in sorted(cascade_results.items()):
        log.info(
            f"  {k} parents locked: "
            f"{r['total']} windows, {r['correct']} correct, WR={r['wr']:.1f}%"
        )

    # Section 6: Strategy
    log.info("\n── Section 6: Strategy Design ──")
    strategy = design_strategy(overlap_bt, pricing_edges, cascade_results)
    for rule in strategy["entry_rules"]:
        log.info(f"  {rule['rule']}: WR={rule['expected_wr']}, sizing={rule['sizing']}")

    # Generate report
    log.info("\n── Generating Report ──")
    report = generate_report(
        overlap_structure,
        locked_signals,
        sensitivity,
        overlap_bt,
        pricing_edges,
        cascade_results,
        cascade_details,
        strategy,
    )

    with open(REPORT_PATH, "w") as f:
        f.write(report)
    log.info(f"  Report saved to: {REPORT_PATH}")

    log.info("\n" + "=" * 70)
    log.info("DONE")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
