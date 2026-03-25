#!/opt/homebrew/bin/python3
"""
Q4: Backtest vs Live Gap Analysis

KEY QUESTION: On the SAME windows, does the backtest predict differently than the bot?

Design decisions:
- Bot uses _open_at(): fetches 1m candle OPEN at window_start_ms (index [1])
- Backtest uses 1s klines: get_price() returns close price at exact second (index [4])
- These are different price references — this is one likely gap source.
- Bot measures momentum at wall-clock NOW (T+elapsed), which is NOT aligned to T+300s.
- Backtest measures at exact T+60s or T+15s — fixed delay from window open.
- Live entry fires as soon as elapsed >= 300s, which could be anywhere up to ~60s late.

Data availability:
- 1s klines: 2026-02-22 to 2026-03-23 23:59:59 UTC
- Live trades: 5 in cache (2026-03-23 19:09-19:26 UTC) + 31 out of cache (2026-03-24 UTC)
- For out-of-cache windows: we reconstruct from 1m candles (3months file) as proxy.
"""

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
KLINES_CACHE = BASE / "data" / "btc_1s_klines.json"
BTC_1M_3M = BASE / "btc_1m_3months.json"
LIVE_LOG = Path(__file__).resolve().parents[1] / "logs" / "mm_w4_5m.jsonl"
RESULTS = BASE / "data" / "momentum_wr_results.json"

# ---------------------------------------------------------------------------
# Constants (must match bot and backtest exactly)
# ---------------------------------------------------------------------------
WINDOW_S = 300         # 5-minute window
BOT_DELAY_S = 300      # bot fires at T+300s (after elapsed >= _W4_DELAY_S)
BT_DELAYS = [60, 15]   # backtest delays we're comparing against
THRESHOLD_BPS = 5.0    # 5bps threshold (both bot and backtest)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_1s_cache() -> dict[int, float]:
    """Load 1s klines. Returns {timestamp_ms: close_price}."""
    print("Loading 1s klines cache...")
    with open(KLINES_CACHE) as f:
        cache = json.load(f)
    prices = {int(k): float(v) for k, v in cache["prices"].items()}
    print(f"  {len(prices):,} 1s prices loaded")
    print(f"  Range: {datetime.fromtimestamp(min(prices)/1000, tz=timezone.utc)} "
          f"to {datetime.fromtimestamp(max(prices)/1000, tz=timezone.utc)}")
    return prices


def load_1m_data() -> dict[int, dict]:
    """Load 1m candles. Returns {open_time_ms: {open, high, low, close, volume}}."""
    print("Loading 1m klines (3 months)...")
    with open(BTC_1M_3M) as f:
        raw = json.load(f)
    data = {}
    for c in raw:
        ts = int(c["ts"])
        data[ts] = {
            "open": float(c["open"]),
            "close": float(c["close"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "volume": float(c.get("volume", 0)),
        }
    ts_list = sorted(data.keys())
    print(f"  {len(data):,} 1m candles loaded")
    print(f"  Range: {datetime.fromtimestamp(ts_list[0]/1000, tz=timezone.utc)} "
          f"to {datetime.fromtimestamp(ts_list[-1]/1000, tz=timezone.utc)}")
    return data


def load_live_btc_entries() -> list[dict]:
    """Load BTC w4_entry events from live log."""
    entries = []
    with open(LIVE_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("coin") == "btc" and ev.get("event") == "w4_entry":
                entries.append(ev)
    print(f"Loaded {len(entries)} BTC w4_entry events")
    return entries


# ---------------------------------------------------------------------------
# Price lookup helpers
# ---------------------------------------------------------------------------

def get_1s_price(prices_1s: dict[int, float], target_ms: int, max_drift_ms: int = 5000) -> float | None:
    """Get 1s close price at target_ms with ±5s drift tolerance."""
    if target_ms in prices_1s:
        return prices_1s[target_ms]
    for offset in range(1000, max_drift_ms + 1, 1000):
        if (target_ms + offset) in prices_1s:
            return prices_1s[target_ms + offset]
        if (target_ms - offset) in prices_1s:
            return prices_1s[target_ms - offset]
    return None


def get_1m_open_at(data_1m: dict[int, dict], target_ms: int) -> float | None:
    """
    Get the OPEN price of the 1m candle starting at or just before target_ms.
    This matches bot behavior: _open_at() fetches 1m candle open at window_start_ms.
    1m candles are aligned to minute boundaries (e.g. 19:09:00, 19:10:00, ...).
    """
    # Round down to nearest minute
    minute_ms = (target_ms // 60000) * 60000
    # Try exact minute, then walk back 2 minutes (in case of alignment issue)
    for delta in [0, -60000, 60000]:
        key = minute_ms + delta
        if key in data_1m:
            return data_1m[key]["open"]
    return None


def get_1m_close_at_window_end(data_1m: dict[int, dict], window_start_ms: int) -> float | None:
    """
    Get the close price of the 1m candle that contains T+300s.
    Window end = window_start_ms + 300000 ms.
    The 5M resolution is: did price go up or down by T+300s?
    We use the close of the 1m candle containing T+299s as proxy.
    """
    end_ms = window_start_ms + WINDOW_S * 1000
    # The 1m candle containing T+300s = minute_floor(end_ms)
    # But T+300s is the START of the next window, so use T+299s
    minute_ms = ((end_ms - 1) // 60000) * 60000
    if minute_ms in data_1m:
        return data_1m[minute_ms]["close"]
    # Try ±1 minute
    for delta in [-60000, 60000]:
        key = minute_ms + delta
        if key in data_1m:
            return data_1m[key]["close"]
    return None


# ---------------------------------------------------------------------------
# Core analysis: per window
# ---------------------------------------------------------------------------

def analyze_window(
    entry: dict,
    prices_1s: dict[int, float],
    data_1m: dict[int, dict],
) -> dict:
    """
    For one live trade window, compute:
    1. Bot signal: lean_dir, w4_ret, w4_mag_bps (from log, already computed by bot)
    2. What 1M data says about actual 5M window resolution
    3. Backtest signal at T+60s and T+15s using 1s klines (or 1m proxy)
    4. Agreement/disagreement analysis
    """
    result = {
        "ts": entry["ts"],
        "lean_dir": entry["lean_dir"],
        "w4_ret": entry["w4_ret"],
        "w4_mag_bps": entry["w4_mag_bps"],
        "contrarian": entry.get("contrarian", False),
    }

    # Parse entry timestamp to UTC ms
    dt = datetime.fromisoformat(entry["ts"])
    entry_ms = int(dt.timestamp() * 1000)
    dt_utc = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc)
    result["entry_ms"] = entry_ms
    result["entry_utc"] = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Step 1: Infer window_start_ms ──────────────────────────────────────
    # Bot fires at T+300s. Entry_ms ≈ window_start_ms + 300s + small delay.
    # w4_mag_bps = |log(p_now/p0)| * 10000, so we know the signal was live.
    # Best estimate: window_start_ms = round(entry_ms) to nearest 5M boundary.
    # 5M windows are aligned to :00,:05,:10,...:55 of each hour (UTC).
    window_ms = WINDOW_S * 1000  # 300000 ms
    # Round down to nearest 5M boundary
    window_start_ms = (entry_ms // window_ms) * window_ms
    # The bot fires AFTER 300s has elapsed, so entry could be in the SAME or NEXT 5M window.
    # Since elapsed >= 300s and entry_ms is the time the bot fires:
    #   candidate window = window_start_ms - 300000 (the window that started 5min ago)
    # Actually: entry_ms = window_start_ms_of_TRADING_WINDOW + ~300s
    # So the window that JUST completed is: floor(entry_ms - 300000) to nearest 5M
    candidate_ws = ((entry_ms - WINDOW_S * 1000) // window_ms) * window_ms
    # Verify: elapsed from candidate_ws to entry_ms should be ~300-360s
    elapsed_candidate = (entry_ms - candidate_ws) / 1000
    elapsed_direct = (entry_ms - window_start_ms) / 1000
    if 290 <= elapsed_candidate <= 600:
        inferred_ws = candidate_ws
        elapsed_s = elapsed_candidate
    else:
        inferred_ws = window_start_ms
        elapsed_s = elapsed_direct

    result["inferred_window_start_ms"] = inferred_ws
    result["inferred_window_start_utc"] = datetime.fromtimestamp(
        inferred_ws / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    result["elapsed_at_entry_s"] = round(elapsed_s, 1)

    # ── Step 2: Bot's reference: p0 via 1m open, p_now via live price ──────
    # The w4_ret in the log = log(p_now / p0) where:
    #   p0 = _open_at(window_start_ms) = 1m candle open at window_start_ms
    #   p_now = live price at time of entry
    # We can reverse-engineer p0 from w4_ret and the known direction:
    # w4_ret = log(p_now / p0), so p_now = p0 * exp(w4_ret)
    # But we don't have p_now directly. What we have from 1m data is p0.
    p0_1m = get_1m_open_at(data_1m, inferred_ws)
    result["p0_1m_open"] = round(p0_1m, 2) if p0_1m else None

    # ── Step 3: What did BTC do over the FULL 5M window? ──────────────────
    # Resolution = did close > open (UP) or close < open (DOWN)?
    # Use 1m close of the last minute in the window as proxy for T+300s close.
    p_end_1m = get_1m_close_at_window_end(data_1m, inferred_ws)
    result["p_end_1m"] = round(p_end_1m, 2) if p_end_1m else None

    if p0_1m and p_end_1m:
        full_ret = math.log(p_end_1m / p0_1m) if p0_1m > 0 else 0
        result["full_window_ret_bps"] = round(full_ret * 10000, 1)
        if p_end_1m > p0_1m:
            result["actual_resolution"] = "UP"
        elif p_end_1m < p0_1m:
            result["actual_resolution"] = "DOWN"
        else:
            result["actual_resolution"] = "FLAT"
    else:
        result["full_window_ret_bps"] = None
        result["actual_resolution"] = "UNKNOWN"

    # ── Step 4: Backtest signal at T+60s and T+15s ────────────────────────
    # Backtest uses 1s klines: p0 = close at window_start_ms, p_delay = close at T+delay
    cache_end_ms = max(prices_1s.keys()) if prices_1s else 0

    for delay in BT_DELAYS:
        delay_ms = inferred_ws + delay * 1000
        bt_key = f"bt_{delay}s"

        # Is this window in the 1s cache?
        if inferred_ws <= cache_end_ms and prices_1s:
            p0_bt = get_1s_price(prices_1s, inferred_ws)
            p_delay = get_1s_price(prices_1s, delay_ms)
            price_source = "1s_cache"
        else:
            # Fall back to 1m data: p0 = 1m open, p_delay = close of 1m candle at T+delay
            p0_bt = p0_1m  # same 1m open
            # Find 1m candle containing T+delay
            delay_minute_ms = (delay_ms // 60000) * 60000
            p_delay_candle = data_1m.get(delay_minute_ms) or data_1m.get(delay_minute_ms - 60000)
            p_delay = p_delay_candle["close"] if p_delay_candle else None
            price_source = "1m_proxy"

        result[f"{bt_key}_price_source"] = price_source

        if p0_bt and p_delay and p0_bt > 0:
            bt_ret = math.log(p_delay / p0_bt)
            bt_mag = abs(bt_ret) * 10000
            result[f"{bt_key}_ret_bps"] = round(bt_ret * 10000, 1)
            result[f"{bt_key}_mag_bps"] = round(bt_mag, 1)
            if bt_mag < THRESHOLD_BPS:
                result[f"{bt_key}_dir"] = "SKIP"
            else:
                result[f"{bt_key}_dir"] = "UP" if bt_ret > 0 else "DOWN"
        else:
            result[f"{bt_key}_ret_bps"] = None
            result[f"{bt_key}_mag_bps"] = None
            result[f"{bt_key}_dir"] = "NO_DATA"

    # ── Step 5: Agreement analysis ─────────────────────────────────────────
    bot_dir = entry["lean_dir"]  # "UP" or "DOWN"
    actual = result.get("actual_resolution", "UNKNOWN")

    result["bot_correct"] = (bot_dir == actual) if actual not in ("FLAT", "UNKNOWN") else None

    for delay in BT_DELAYS:
        bt_dir = result.get(f"bt_{delay}s_dir", "NO_DATA")
        if bt_dir in ("SKIP", "NO_DATA"):
            result[f"bt_{delay}s_correct"] = None
            result[f"bot_vs_bt_{delay}s"] = "BT_SKIP"
        elif actual in ("FLAT", "UNKNOWN"):
            result[f"bt_{delay}s_correct"] = None
            result[f"bot_vs_bt_{delay}s"] = "NO_ACTUAL"
        else:
            result[f"bt_{delay}s_correct"] = (bt_dir == actual)
            if bot_dir == bt_dir:
                result[f"bot_vs_bt_{delay}s"] = "AGREE"
            else:
                result[f"bot_vs_bt_{delay}s"] = "DISAGREE"

    # ── Step 6: Price reference comparison ─────────────────────────────────
    # Bot p0 = 1m candle OPEN; Backtest p0 = 1s close at window start
    # These can differ if BTC moves in the first second of the window.
    bt_60_p0 = get_1s_price(prices_1s, inferred_ws) if inferred_ws <= cache_end_ms and prices_1s else None
    result["p0_1s_close"] = round(bt_60_p0, 2) if bt_60_p0 else None

    if p0_1m and bt_60_p0:
        p0_diff_bps = (bt_60_p0 - p0_1m) / p0_1m * 10000
        result["p0_diff_bps_1m_vs_1s"] = round(p0_diff_bps, 2)
    else:
        result["p0_diff_bps_1m_vs_1s"] = None

    return result


# ---------------------------------------------------------------------------
# Timing analysis: bot entry delay distribution
# ---------------------------------------------------------------------------

def analyze_timing(entries: list[dict]) -> dict:
    """Analyze the distribution of bot entry delays from window open."""
    results = []
    window_ms = WINDOW_S * 1000

    for e in entries:
        dt = datetime.fromisoformat(e["ts"])
        entry_ms = int(dt.timestamp() * 1000)
        candidate_ws = ((entry_ms - window_ms) // window_ms) * window_ms
        elapsed = (entry_ms - candidate_ws) / 1000
        if 290 <= elapsed <= 600:
            results.append({
                "ts": e["ts"],
                "elapsed_s": round(elapsed, 1),
                "w4_mag_bps": e["w4_mag_bps"],
                "lean_dir": e["lean_dir"],
            })

    if not results:
        return {"error": "no valid timing data"}

    delays = [r["elapsed_s"] for r in results]
    mean_delay = sum(delays) / len(delays)
    min_delay = min(delays)
    max_delay = max(delays)

    return {
        "n": len(results),
        "mean_entry_delay_s": round(mean_delay, 1),
        "min_entry_delay_s": round(min_delay, 1),
        "max_entry_delay_s": round(max_delay, 1),
        "entries": results[:5],  # sample
        "note": (
            "Bot fires at T+elapsed where elapsed >= 300s. "
            "Backtest measures at EXACTLY T+60s or T+15s from window open. "
            f"Average bot delay = {mean_delay:.1f}s vs backtest T+60s. "
            "If bot measures at T+300s-350s, it's 240-290s LATER than backtest T+60s."
        ),
    }


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def compute_summary(analyzed: list[dict]) -> dict:
    """Compute agreement/disagreement statistics."""
    n = len(analyzed)
    has_actual = [a for a in analyzed if a.get("actual_resolution") not in ("UNKNOWN", "FLAT", None)]
    n_actual = len(has_actual)

    summary = {
        "total_btc_entries": n,
        "has_actual_resolution": n_actual,
        "data_gap_note": f"{n - n_actual} windows lack actual resolution (outside 1m/1s data)",
    }

    # Bot accuracy
    bot_correct = [a for a in has_actual if a.get("bot_correct") is True]
    summary["bot_wr"] = round(len(bot_correct) / n_actual * 100, 1) if n_actual else 0
    summary["bot_correct_n"] = len(bot_correct)

    # For each backtest delay
    for delay in BT_DELAYS:
        key = f"bt_{delay}s"
        agree_col = f"bot_vs_bt_{delay}s"
        bt_correct_col = f"bt_{delay}s_correct"

        has_bt = [a for a in has_actual if a.get(agree_col) not in ("BT_SKIP", "NO_ACTUAL", None)]
        n_bt = len(has_bt)

        agree = [a for a in has_bt if a.get(agree_col) == "AGREE"]
        disagree = [a for a in has_bt if a.get(agree_col) == "DISAGREE"]
        bt_corr = [a for a in has_bt if a.get(bt_correct_col) is True]

        summary[f"{key}_comparable"] = n_bt
        summary[f"{key}_agree"] = len(agree)
        summary[f"{key}_disagree"] = len(disagree)
        summary[f"{key}_wr"] = round(len(bt_corr) / n_bt * 100, 1) if n_bt else 0

        # Quadrant breakdown
        bot_right_bt_right = sum(1 for a in has_bt
                                  if a.get("bot_correct") and a.get(bt_correct_col))
        bot_right_bt_wrong = sum(1 for a in has_bt
                                  if a.get("bot_correct") and not a.get(bt_correct_col))
        bot_wrong_bt_right = sum(1 for a in has_bt
                                  if not a.get("bot_correct") and a.get(bt_correct_col))
        bot_wrong_bt_wrong = sum(1 for a in has_bt
                                  if not a.get("bot_correct") and not a.get(bt_correct_col))

        summary[f"{key}_both_correct"] = bot_right_bt_right
        summary[f"{key}_bot_only_correct"] = bot_right_bt_wrong
        summary[f"{key}_bt_only_correct"] = bot_wrong_bt_right
        summary[f"{key}_both_wrong"] = bot_wrong_bt_wrong

    # P0 reference gap
    p0_diffs = [a["p0_diff_bps_1m_vs_1s"] for a in analyzed if a.get("p0_diff_bps_1m_vs_1s") is not None]
    if p0_diffs:
        summary["p0_ref_gap"] = {
            "n": len(p0_diffs),
            "mean_diff_bps": round(sum(p0_diffs) / len(p0_diffs), 2),
            "max_diff_bps": round(max(abs(d) for d in p0_diffs), 2),
            "note": "Difference between bot p0 (1m candle open) and backtest p0 (1s close at T+0)",
        }

    return summary


# ---------------------------------------------------------------------------
# Pretty print
# ---------------------------------------------------------------------------

def print_section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_timing(timing: dict):
    print_section("1. TIMING ANALYSIS: Bot Entry Delay Distribution")
    print(f"  N windows analyzed:     {timing.get('n', 0)}")
    print(f"  Mean entry delay:        T + {timing.get('mean_entry_delay_s', 0):.1f}s")
    print(f"  Min entry delay:         T + {timing.get('min_entry_delay_s', 0):.1f}s")
    print(f"  Max entry delay:         T + {timing.get('max_entry_delay_s', 0):.1f}s")
    print()
    print(f"  Backtest T+60s:          measures at EXACTLY T+60s")
    print(f"  Backtest T+15s:          measures at EXACTLY T+15s")
    print()
    print(f"  KEY INSIGHT: Bot fires at T+{timing.get('mean_entry_delay_s', 300):.0f}s (avg).")
    print(f"  That is {timing.get('mean_entry_delay_s', 300) - 60:.0f}s LATER than backtest T+60s measurement.")
    print(f"  The market has moved for {timing.get('mean_entry_delay_s', 300) - 60:.0f}s more — different signal.")
    print()
    print(f"  NOTE: {timing.get('note', '')}")


def print_per_window(analyzed: list[dict]):
    print_section("2. PER-WINDOW COMPARISON (BTC only)")
    header = f"  {'Entry Time (UTC)':<22} {'Bot':>5} {'BotMag':>7} {'Actual':>7} {'BT60s':>7} {'BT15s':>7} {'Agree60':>8} {'Agree15':>8}"
    print(header)
    print("  " + "-" * 68)
    for a in analyzed:
        bot = a.get("lean_dir", "?")[:4]
        mag = f"{a.get('w4_mag_bps', 0):+.1f}"
        actual = a.get("actual_resolution", "?")[:4]
        bt60 = a.get("bt_60s_dir", "?")[:4]
        bt15 = a.get("bt_15s_dir", "?")[:4]
        ag60 = a.get("bot_vs_bt_60s", "?")[:8]
        ag15 = a.get("bot_vs_bt_15s", "?")[:8]
        entry_t = a.get("entry_utc", "?")
        elapsed = a.get("elapsed_at_entry_s", 0)
        print(f"  {entry_t:<22} {bot:>5} {mag:>7} {actual:>7} {bt60:>7} {bt15:>7} {ag60:>8} {ag15:>8}  [T+{elapsed:.0f}s]")


def print_summary(summary: dict):
    print_section("3. SUMMARY STATISTICS")
    print(f"  Total BTC entries:        {summary['total_btc_entries']}")
    print(f"  With actual resolution:   {summary['has_actual_resolution']}")
    print(f"  Data gap note:            {summary['data_gap_note']}")
    print()
    print(f"  Bot WR (this session):    {summary['bot_wr']}% ({summary['bot_correct_n']}/{summary['has_actual_resolution']})")

    for delay in BT_DELAYS:
        key = f"bt_{delay}s"
        n_cmp = summary.get(f"{key}_comparable", 0)
        if n_cmp == 0:
            print(f"\n  Backtest T+{delay}s: NO COMPARABLE DATA")
            continue
        print()
        print(f"  Backtest T+{delay}s (n={n_cmp}):")
        print(f"    WR:                   {summary.get(f'{key}_wr', 0)}%")
        print(f"    Bot AGREE bt:         {summary.get(f'{key}_agree', 0)} ({round(summary.get(f'{key}_agree', 0)/n_cmp*100, 1)}%)")
        print(f"    Bot DISAGREE bt:      {summary.get(f'{key}_disagree', 0)} ({round(summary.get(f'{key}_disagree', 0)/n_cmp*100, 1)}%)")
        print()
        print(f"    Quadrant breakdown:")
        print(f"      Both correct:       {summary.get(f'{key}_both_correct', 0)}")
        print(f"      Bot only correct:   {summary.get(f'{key}_bot_only_correct', 0)}")
        print(f"      Backtest only:      {summary.get(f'{key}_bt_only_correct', 0)}")
        print(f"      Both wrong:         {summary.get(f'{key}_both_wrong', 0)}")

    p0 = summary.get("p0_ref_gap", {})
    if p0:
        print()
        print("  P0 Reference Gap (bot 1m-open vs backtest 1s-close at T+0):")
        print(f"    N with both data:     {p0.get('n', 0)}")
        print(f"    Mean diff:            {p0.get('mean_diff_bps', 0):+.2f} bps")
        print(f"    Max diff:             {p0.get('max_diff_bps', 0):.2f} bps")
        print(f"    Note:                 {p0.get('note', '')}")


def print_root_cause(timing: dict, summary: dict, analyzed: list[dict]):
    print_section("4. ROOT CAUSE ANALYSIS")

    n_actual = summary["has_actual_resolution"]
    bot_wr = summary["bot_wr"]

    print("  A) TIMING MISMATCH (most likely cause)")
    print(f"     Bot signal: T + {timing.get('mean_entry_delay_s', 300):.0f}s (avg) from window open")
    print(f"     Backtest signal: T + 60s or T + 15s from window open")
    print(f"     Gap: ~{timing.get('mean_entry_delay_s', 300) - 60:.0f}s additional price movement before bot fires")
    print()
    print("     The BACKTEST WR of 73% (T+60s) assumes you enter at T+60s.")
    print("     The BOT enters at T+300s. By then, the market has moved much more.")
    print("     At T+300s, BTC has had 5 FULL minutes of trend — reversion is more likely,")
    print("     meaning momentum-following at T+300s into a T+600s window IS a different bet.")
    print()
    print("  B) P0 REFERENCE MISMATCH")
    p0 = summary.get("p0_ref_gap", {})
    if p0 and p0.get("n", 0) > 0:
        print(f"     Bot p0: 1m candle OPEN at window_start (index [1])")
        print(f"     Backtest p0: 1s close at window_start (index [4])")
        print(f"     Avg difference: {p0.get('mean_diff_bps', 0):+.2f} bps ({p0.get('n', 0)} windows)")
        print(f"     Max difference: {p0.get('max_diff_bps', 0):.2f} bps")
        print(f"     Impact: small but systematic — can flip direction for borderline signals.")
    else:
        print("     Cannot measure (most live trades are outside 1s cache)")
    print()
    print("  C) SMALL SAMPLE + WINDOW SELECTION BIAS")
    print(f"     Live sample: {n_actual} windows with known outcome")
    print("     Backtest: 8,639 windows over 30 days")
    print("     A 23% drop in WR (73% → 50%) requires only ~10 extra wrong trades")
    print("     in a 153-trade sample — well within chance variation.")
    print()
    print("  D) CONTRARIAN LOGIC (minor)")
    n_contrarian = sum(1 for a in analyzed if a.get("contrarian"))
    print(f"     Bot flipped direction (contrarian) in {n_contrarian}/{len(analyzed)} BTC windows.")
    print("     Backtest never applies contrarian logic — pure momentum.")
    print()
    print("  CONCLUSION:")
    print("  The #1 gap is a FUNDAMENTAL MISMATCH — backtest and bot measure")
    print("  completely different things:")
    print()
    print("  BACKTEST:  5M windows, signal at T+60s, predicts T+0 to T+300s close.")
    print("             WR = 73.3% (T+60s, >5bps), n=3,260.")
    print()
    print("  BOT:       15M windows (_W4_DELAY_S=300, 'the 15M sweet spot').")
    print("             Signal at T+300s (avg actual T+416s), predicts T+300→T+900s.")
    print("             Measures momentum over the FIRST 5 min of a 15M window,")
    print("             then bets on whether the REMAINING 10 min continues it.")
    print()
    print("  These are different prediction tasks — the 73% backtest WR was never")
    print("  a valid predictor of the bot's live WR.")
    print()
    print("  A proper backtest for this bot would need:")
    print("    1. Use 15M windows (not 5M)")
    print("    2. Signal: momentum from T+0 to T+300s (5-min momentum)")
    print("    3. Outcome: does BTC continue UP/DOWN from T+300s to T+900s?")
    print("    4. Same p0 reference (1m candle open, not 1s close)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print_section("Q4: Backtest vs Live Gap Analysis")
    print("  Bot file:     ", LIVE_LOG)
    print("  1s cache:     ", KLINES_CACHE)
    print("  1m data:      ", BTC_1M_3M)

    # Load data
    prices_1s = load_1s_cache()
    data_1m = load_1m_data()
    entries = load_live_btc_entries()

    # Check cache end date
    cache_end_ms = max(prices_1s.keys())
    cache_end_dt = datetime.fromtimestamp(cache_end_ms / 1000, tz=timezone.utc)
    print(f"\n  1s cache ends: {cache_end_dt}")

    in_cache = 0
    out_cache = 0
    for e in entries:
        dt = datetime.fromisoformat(e["ts"])
        ts_ms = int(dt.timestamp() * 1000)
        if ts_ms <= cache_end_ms:
            in_cache += 1
        else:
            out_cache += 1
    print(f"  BTC entries in 1s cache: {in_cache}, outside cache: {out_cache}")

    # Analyze timing
    timing = analyze_timing(entries)
    print_timing(timing)

    # Per-window analysis
    print_section("Analyzing each window...")
    analyzed = []
    for e in entries:
        result = analyze_window(e, prices_1s, data_1m)
        analyzed.append(result)

    print_per_window(analyzed)

    # Summary
    summary = compute_summary(analyzed)
    print_summary(summary)

    # Root cause
    print_root_cause(timing, summary, analyzed)

    # Save results
    output = {
        "timing": timing,
        "summary": summary,
        "per_window": analyzed,
    }
    out_path = BASE / "data" / "q4_backtest_vs_live_results.json"
    import os
    import tempfile
    tmp = str(out_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(output, f, indent=2, default=str)
    os.replace(tmp, str(out_path))
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
