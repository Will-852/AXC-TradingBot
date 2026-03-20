"""
whale_1h_timing.py — Analyze blue-walnut's 1H entry timing vs 5M/15M settlement boundaries

Goal: Test hypothesis that 1H entries cluster around 5M/15M settlement moments
      (when sub-market resolution creates temporary mispricings).

Run: PYTHONPATH=.:scripts python3 polymarket/analysis/whale_1h_timing.py [--days 7] [--offset 0]
"""
import argparse
import json
import logging
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT))

# ─── Constants ───
_WALLET = "0x4b188496d1b3da1716165380999afb9b314c725f"
_DATA_API = "https://data-api.polymarket.com"
_ET = timezone(timedelta(hours=-4))
_HKT = timezone(timedelta(hours=8))
_UA = "AXC-WhaleAnalysis/1.0"
_FETCH_LIMIT = 500  # max per request
_RATE_LIMIT_S = 0.5  # be polite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("whale_1h")


# ═══════════════════════════════════════════════════════════════
# Data fetch
# ═══════════════════════════════════════════════════════════════

def _get(url: str, timeout: int = 10) -> list | dict | None:
    """Fetch JSON from URL."""
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        log.warning("Fetch failed: %s → %s", url, e)
        return None


def fetch_all_trades(max_pages: int = 40) -> list[dict]:
    """Pull all trades for the wallet, paginating until exhausted."""
    all_trades = []
    offset = 0
    for page in range(max_pages):
        url = f"{_DATA_API}/trades?user={_WALLET}&limit={_FETCH_LIMIT}&offset={offset}"
        data = _get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        all_trades.extend(data)
        log.info("Page %d: fetched %d trades (total: %d)", page + 1, len(data), len(all_trades))
        offset += _FETCH_LIMIT
        if len(data) < _FETCH_LIMIT:
            break
        time.sleep(_RATE_LIMIT_S)
    return all_trades


# ═══════════════════════════════════════════════════════════════
# Parse + classify trades
# ═══════════════════════════════════════════════════════════════

def parse_trade(raw: dict) -> dict | None:
    """Extract relevant fields from a raw trade dict."""
    try:
        ts = raw.get("timestamp") or raw.get("matchTime") or raw.get("createdAt")
        if isinstance(ts, str):
            # ISO format
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_unix = dt.timestamp()
        else:
            ts_unix = float(ts)
            # Polymarket sometimes uses ms
            if ts_unix > 1e12:
                ts_unix /= 1000
            dt = datetime.fromtimestamp(ts_unix, tz=timezone.utc)

        price = float(raw.get("price", 0))
        size = float(raw.get("size", 0))
        side = raw.get("side", "BUY")
        outcome = raw.get("outcome", raw.get("outcomeIndex", ""))
        market = raw.get("market", raw.get("title", raw.get("event", "")))
        maker = raw.get("maker_address", raw.get("makerAddress", ""))

        # Determine if maker
        is_maker = (maker.lower() == _WALLET.lower()) if maker else None

        return {
            "ts": ts_unix,
            "dt": dt,
            "price": price,
            "size": size,
            "cost": price * size,
            "side": side,
            "outcome": str(outcome),
            "market": str(market),
            "is_maker": is_maker,
        }
    except (ValueError, TypeError, AttributeError) as e:
        log.debug("Skip unparseable trade: %s", e)
        return None


def classify_market_timeframe(market_name: str) -> str:
    """Detect if market is 5M, 15M, or 1H from its name."""
    name = market_name.lower()
    if "5 min" in name or "5min" in name or "5-min" in name:
        return "5M"
    if "15 min" in name or "15min" in name or "15-min" in name:
        return "15M"
    # Default: 1H (hourly markets say "XAM ET" or "XPM ET")
    return "1H"


def extract_hour_window(trade: dict) -> tuple[int, float] | None:
    """
    For a 1H market trade, figure out:
    - which hour window it belongs to (window_start as unix ts)
    - how many minutes into the window the trade was placed

    1H windows start at the top of each hour ET.
    """
    dt_et = trade["dt"].astimezone(_ET)
    # Window start = top of the current hour
    window_start = dt_et.replace(minute=0, second=0, microsecond=0)
    minutes_in = (trade["ts"] - window_start.timestamp()) / 60.0

    # Sanity: should be 0-60
    if minutes_in < -5 or minutes_in > 65:
        return None

    return int(window_start.timestamp()), minutes_in


# ═══════════════════════════════════════════════════════════════
# Analysis
# ═══════════════════════════════════════════════════════════════

def analyze_timing(trades: list[dict]) -> dict:
    """Core analysis: when within the hour does blue-walnut enter?"""

    # Minute-level histogram (0-60)
    minute_histogram = Counter()
    # Cost-weighted minute histogram
    cost_by_minute = defaultdict(float)
    # Price by phase
    phase_prices = defaultdict(list)   # phase → [prices]
    phase_costs = defaultdict(float)   # phase → total $
    phase_counts = defaultdict(int)
    # 5M/15M boundary proximity
    near_5m = []  # trades within ±90s of a 5M boundary
    near_15m = []  # trades within ±90s of a 15M boundary
    far_from_boundary = []  # trades >90s from any boundary
    # Per-window analysis
    window_trades = defaultdict(list)  # window_start → [trades]
    # Asset breakdown
    asset_stats = defaultdict(lambda: {"count": 0, "cost": 0.0, "prices": []})

    for t in trades:
        hw = extract_hour_window(t)
        if hw is None:
            continue
        window_start, min_in = hw

        # Clamp to 0-60
        min_in = max(0, min(60, min_in))
        minute_bin = int(min_in)
        minute_histogram[minute_bin] += 1
        cost_by_minute[minute_bin] += t["cost"]

        # Phase classification
        if min_in < 15:
            phase = "early_0_15"
        elif min_in < 30:
            phase = "mid_15_30"
        elif min_in < 45:
            phase = "late_30_45"
        else:
            phase = "final_45_60"

        phase_prices[phase].append(t["price"])
        phase_costs[phase] += t["cost"]
        phase_counts[phase] += 1

        # 5M boundary proximity (boundaries at 0, 5, 10, 15, ... 60)
        nearest_5m = round(min_in / 5) * 5
        dist_5m = abs(min_in - nearest_5m)
        # 15M boundary proximity (boundaries at 0, 15, 30, 45, 60)
        nearest_15m = round(min_in / 15) * 15
        dist_15m = abs(min_in - nearest_15m)

        trade_info = {**t, "min_in": min_in, "dist_5m": dist_5m, "dist_15m": dist_15m}

        if dist_15m <= 1.5:  # within 90 seconds of 15M boundary
            near_15m.append(trade_info)
        elif dist_5m <= 1.5:  # within 90 seconds of 5M boundary (but not 15M)
            near_5m.append(trade_info)
        else:
            far_from_boundary.append(trade_info)

        window_trades[window_start].append(trade_info)

        # Asset
        market = t["market"].lower()
        for coin in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp"]:
            if coin in market:
                asset = coin.upper()
                if asset in ("BITCOIN",):
                    asset = "BTC"
                elif asset in ("ETHEREUM",):
                    asset = "ETH"
                elif asset in ("SOLANA",):
                    asset = "SOL"
                asset_stats[asset]["count"] += 1
                asset_stats[asset]["cost"] += t["cost"]
                asset_stats[asset]["prices"].append(t["price"])
                break

    return {
        "total_trades": len(trades),
        "minute_histogram": dict(sorted(minute_histogram.items())),
        "cost_by_minute": dict(sorted({k: round(v, 2) for k, v in cost_by_minute.items()}.items())),
        "phases": {
            phase: {
                "count": phase_counts[phase],
                "total_cost": round(phase_costs[phase], 2),
                "avg_price": round(statistics.mean(phase_prices[phase]), 4) if phase_prices[phase] else 0,
                "median_price": round(statistics.median(phase_prices[phase]), 4) if phase_prices[phase] else 0,
                "price_range": (
                    round(min(phase_prices[phase]), 4),
                    round(max(phase_prices[phase]), 4),
                ) if phase_prices[phase] else (0, 0),
            }
            for phase in ["early_0_15", "mid_15_30", "late_30_45", "final_45_60"]
        },
        "boundary_proximity": {
            "near_15m_boundary": len(near_15m),
            "near_5m_boundary": len(near_5m),
            "far_from_boundary": len(far_from_boundary),
            "pct_near_15m": round(len(near_15m) / max(1, len(trades)) * 100, 1),
            "pct_near_5m": round(len(near_5m) / max(1, len(trades)) * 100, 1),
            "pct_far": round(len(far_from_boundary) / max(1, len(trades)) * 100, 1),
        },
        "boundary_detail": {
            "near_15m_avg_price": round(statistics.mean([t["price"] for t in near_15m]), 4) if near_15m else 0,
            "near_15m_avg_cost": round(statistics.mean([t["cost"] for t in near_15m]), 2) if near_15m else 0,
            "far_avg_price": round(statistics.mean([t["price"] for t in far_from_boundary]), 4) if far_from_boundary else 0,
            "far_avg_cost": round(statistics.mean([t["cost"] for t in far_from_boundary]), 2) if far_from_boundary else 0,
            "near_5m_avg_price": round(statistics.mean([t["price"] for t in near_5m]), 4) if near_5m else 0,
        },
        "per_window_stats": _per_window_summary(window_trades),
        "asset_stats": {
            k: {
                "count": v["count"],
                "total_cost": round(v["cost"], 2),
                "avg_price": round(statistics.mean(v["prices"]), 4) if v["prices"] else 0,
            }
            for k, v in sorted(asset_stats.items())
        },
    }


def _per_window_summary(window_trades: dict) -> dict:
    """Summarize per-window patterns."""
    windows = []
    for ws, trades in sorted(window_trades.items()):
        if len(trades) < 3:
            continue
        prices = [t["price"] for t in trades]
        costs = [t["cost"] for t in trades]
        minutes = [t["min_in"] for t in trades]
        first_min = min(minutes)
        last_min = max(minutes)

        # Does price increase over time? (correlation)
        if len(prices) > 3:
            # Simple: compare avg price of first half vs second half
            mid = len(trades) // 2
            sorted_by_time = sorted(trades, key=lambda x: x["min_in"])
            early_avg = statistics.mean([t["price"] for t in sorted_by_time[:mid]])
            late_avg = statistics.mean([t["price"] for t in sorted_by_time[mid:]])
            price_trend = "rising" if late_avg > early_avg + 0.05 else "falling" if late_avg < early_avg - 0.05 else "flat"
        else:
            price_trend = "insufficient"

        windows.append({
            "window_start_et": datetime.fromtimestamp(ws, tz=_ET).strftime("%Y-%m-%d %I%p"),
            "n_trades": len(trades),
            "total_cost": round(sum(costs), 2),
            "first_entry_min": round(first_min, 1),
            "last_entry_min": round(last_min, 1),
            "avg_price": round(statistics.mean(prices), 4),
            "price_trend": price_trend,
        })

    return {
        "n_windows": len(windows),
        "avg_trades_per_window": round(statistics.mean([w["n_trades"] for w in windows]), 1) if windows else 0,
        "avg_first_entry_min": round(statistics.mean([w["first_entry_min"] for w in windows]), 1) if windows else 0,
        "avg_cost_per_window": round(statistics.mean([w["total_cost"] for w in windows]), 2) if windows else 0,
        "price_trend_counts": dict(Counter(w["price_trend"] for w in windows)),
        "sample_windows": windows[:10],  # first 10
    }


# ═══════════════════════════════════════════════════════════════
# 15M boundary deep-dive
# ═══════════════════════════════════════════════════════════════

def analyze_15m_boundaries(trades: list[dict]) -> dict:
    """
    Deep analysis: do trades cluster JUST AFTER 15M boundaries?
    If spillover hypothesis is correct, we'd see:
    - More trades at minutes 15-17, 30-32, 45-47 (post-15M settlement)
    - Better prices (lower avg entry) near those boundaries
    """
    # 2-minute bins around each 15M boundary
    boundary_zones = {
        "pre_15m": (13, 15),    # 2 min before 15M settle
        "post_15m": (15, 17),   # 2 min after
        "pre_30m": (28, 30),
        "post_30m": (30, 32),
        "pre_45m": (43, 45),
        "post_45m": (45, 47),
        "baseline_8_12": (8, 12),     # control: far from any 15M
        "baseline_20_24": (20, 24),   # control
        "baseline_35_39": (35, 39),   # control
    }

    zone_data = {}
    for zone_name, (lo, hi) in boundary_zones.items():
        zone_trades = []
        for t in trades:
            hw = extract_hour_window(t)
            if hw is None:
                continue
            _, min_in = hw
            if lo <= min_in < hi:
                zone_trades.append(t)

        if zone_trades:
            prices = [t["price"] for t in zone_trades]
            costs = [t["cost"] for t in zone_trades]
            zone_data[zone_name] = {
                "count": len(zone_trades),
                "total_cost": round(sum(costs), 2),
                "avg_price": round(statistics.mean(prices), 4),
                "median_price": round(statistics.median(prices), 4),
                "avg_cost_per_trade": round(statistics.mean(costs), 2),
            }
        else:
            zone_data[zone_name] = {"count": 0}

    # Statistical test: is post-boundary density higher than baseline?
    post_counts = sum(
        zone_data.get(z, {}).get("count", 0)
        for z in ["post_15m", "post_30m", "post_45m"]
    )
    baseline_counts = sum(
        zone_data.get(z, {}).get("count", 0)
        for z in ["baseline_8_12", "baseline_20_24", "baseline_35_39"]
    )
    # Both cover 6 minutes total (3 zones × 2 min)
    # Same total width → direct comparison
    post_density = post_counts / 6.0  # trades per minute
    baseline_density = baseline_counts / 12.0  # 3 zones × 4 min each

    return {
        "zones": zone_data,
        "summary": {
            "post_15m_trades": post_counts,
            "baseline_trades": baseline_counts,
            "post_15m_density_per_min": round(post_density, 2),
            "baseline_density_per_min": round(baseline_density, 2),
            "density_ratio": round(post_density / max(0.01, baseline_density), 2),
            "verdict": (
                "CLUSTERING detected — post-15M density significantly higher"
                if post_density > baseline_density * 1.3
                else "NO clustering — trades evenly distributed"
                if abs(post_density - baseline_density) / max(0.01, baseline_density) < 0.3
                else "MILD clustering — slightly higher near boundaries"
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════
# Price phase analysis (the blue-walnut scaling pattern)
# ═══════════════════════════════════════════════════════════════

def analyze_scaling_pattern(trades: list[dict]) -> dict:
    """
    How does blue-walnut scale in?
    - Dollar clips by phase
    - Price ladder pattern
    - Conviction sizing (more $ at higher odds?)
    """
    by_phase = defaultdict(list)  # phase → [(min_in, price, cost)]

    for t in trades:
        hw = extract_hour_window(t)
        if hw is None:
            continue
        _, min_in = hw
        by_phase["all"].append((min_in, t["price"], t["cost"]))

        if min_in < 15:
            by_phase["early"].append((min_in, t["price"], t["cost"]))
        elif min_in < 30:
            by_phase["mid"].append((min_in, t["price"], t["cost"]))
        elif min_in < 45:
            by_phase["late"].append((min_in, t["price"], t["cost"]))
        else:
            by_phase["final"].append((min_in, t["price"], t["cost"]))

    result = {}
    for phase, entries in by_phase.items():
        if not entries:
            continue
        prices = [e[1] for e in entries]
        costs = [e[2] for e in entries]

        # Price buckets
        low = [c for p, c in zip(prices, costs) if p < 0.30]
        mid = [c for p, c in zip(prices, costs) if 0.30 <= p < 0.70]
        high = [c for p, c in zip(prices, costs) if p >= 0.70]

        result[phase] = {
            "n_trades": len(entries),
            "total_cost": round(sum(costs), 2),
            "avg_clip_size": round(statistics.mean(costs), 2),
            "median_clip_size": round(statistics.median(costs), 2),
            "price_buckets": {
                "low_lt30": {"n": len(low), "total": round(sum(low), 2)},
                "mid_30_70": {"n": len(mid), "total": round(sum(mid), 2)},
                "high_gt70": {"n": len(high), "total": round(sum(high), 2)},
            },
        }

    return result


# ═══════════════════════════════════════════════════════════════
# Boundary dislocation (proper apples-to-apples comparison)
# ═══════════════════════════════════════════════════════════════

def _t_stat(values: list[float]) -> tuple[float, float, float, int]:
    """Compute mean, std, t-statistic for a list of values.

    Returns (mean, std, t_stat, n).
    t = mean / (std / sqrt(n)) — tests whether mean differs from zero.
    """
    n = len(values)
    if n < 2:
        return (values[0] if values else 0.0, 0.0, 0.0, n)
    m = statistics.mean(values)
    s = statistics.stdev(values)
    t = m / (s / math.sqrt(n)) if s > 0 else 0.0
    return (m, s, t, n)


def analyze_boundary_dislocation(trades: list[dict]) -> dict:
    """
    PROPER pre-vs-post boundary comparison (apples-to-apples).

    For each 15M boundary (minute 15, 30, 45), within each individual 1H window:
    - pre  = trades in [boundary-2, boundary)   e.g. minutes 13-14.999
    - post = trades in [boundary, boundary+2)   e.g. minutes 15-16.999
    - delta = post_metric - pre_metric

    Aggregated across all windows → mean delta, std, t-stat.
    A positive price delta means prices went UP after the boundary.

    Also produces a minute-over-minute profile (avg price + trade count
    for each minute 0-59).
    """

    # ── Step 1: bucket trades by (window_start, boundary) ──
    # boundary_data[boundary][window_start] = {"pre": [...], "post": [...]}
    BOUNDARIES = [15, 30, 45]
    PRE_WINDOW = 2   # minutes before boundary
    POST_WINDOW = 2  # minutes after boundary

    window_trades: dict[int, list[dict]] = defaultdict(list)
    for t in trades:
        hw = extract_hour_window(t)
        if hw is None:
            continue
        ws, min_in = hw
        window_trades[ws].append({**t, "min_in": min_in})

    # Per-boundary, per-window deltas
    boundary_results = {}
    for boundary in BOUNDARIES:
        pre_lo = boundary - PRE_WINDOW
        pre_hi = boundary
        post_lo = boundary
        post_hi = boundary + POST_WINDOW

        price_deltas = []
        count_deltas = []
        clip_deltas = []
        cost_deltas = []
        window_details = []

        for ws, wtrades in sorted(window_trades.items()):
            pre = [t for t in wtrades if pre_lo <= t["min_in"] < pre_hi]
            post = [t for t in wtrades if post_lo <= t["min_in"] < post_hi]

            # Need at least 1 trade on each side to compute a delta
            if not pre or not post:
                continue

            pre_avg_price = statistics.mean([t["price"] for t in pre])
            post_avg_price = statistics.mean([t["price"] for t in post])
            pre_avg_clip = statistics.mean([t["cost"] for t in pre])
            post_avg_clip = statistics.mean([t["cost"] for t in post])
            pre_total_cost = sum(t["cost"] for t in pre)
            post_total_cost = sum(t["cost"] for t in post)

            dp = post_avg_price - pre_avg_price
            dc = len(post) - len(pre)
            dclip = post_avg_clip - pre_avg_clip
            dcost = post_total_cost - pre_total_cost

            price_deltas.append(dp)
            count_deltas.append(float(dc))
            clip_deltas.append(dclip)
            cost_deltas.append(dcost)

            window_details.append({
                "window": datetime.fromtimestamp(ws, tz=_ET).strftime("%Y-%m-%d %I%p"),
                "pre_n": len(pre),
                "post_n": len(post),
                "pre_avg_price": round(pre_avg_price, 4),
                "post_avg_price": round(post_avg_price, 4),
                "delta_price": round(dp, 4),
            })

        pm, ps, pt, pn = _t_stat(price_deltas)
        cm, cs, ct, cn = _t_stat(count_deltas)
        clm, cls_, clt, cln = _t_stat(clip_deltas)
        costm, costs, costt, costn = _t_stat(cost_deltas)

        boundary_results[f"min_{boundary}"] = {
            "n_windows": pn,
            "price_delta": {"mean": round(pm, 5), "std": round(ps, 5), "t_stat": round(pt, 3)},
            "count_delta": {"mean": round(cm, 2), "std": round(cs, 2), "t_stat": round(ct, 3)},
            "clip_delta": {"mean": round(clm, 4), "std": round(cls_, 4), "t_stat": round(clt, 3)},
            "cost_delta": {"mean": round(costm, 2), "std": round(costs, 2), "t_stat": round(costt, 3)},
            "sample_windows": window_details[:8],
        }

    # ── Step 2: minute-over-minute profile ──
    minute_prices: dict[int, list[float]] = defaultdict(list)
    minute_counts: dict[int, int] = Counter()

    for t in trades:
        hw = extract_hour_window(t)
        if hw is None:
            continue
        _, min_in = hw
        m = int(max(0, min(59, min_in)))
        minute_prices[m].append(t["price"])
        minute_counts[m] += 1

    minute_profile = {}
    for m in range(60):
        prices = minute_prices.get(m, [])
        minute_profile[m] = {
            "avg_price": round(statistics.mean(prices), 4) if prices else None,
            "trade_count": minute_counts.get(m, 0),
        }

    return {
        "boundaries": boundary_results,
        "minute_profile": minute_profile,
    }


# ═══════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════

def print_report(timing: dict, boundaries: dict, scaling: dict, dislocation: dict | None = None):
    """Print formatted analysis report."""
    print("\n" + "=" * 72)
    print("  WHALE ANALYSIS: blue-walnut 1H Entry Timing vs 15M Boundaries")
    print("=" * 72)

    # ── Profile ──
    print(f"\n📊 Total trades analyzed: {timing['total_trades']}")
    print(f"   Windows analyzed: {timing['per_window_stats']['n_windows']}")
    print(f"   Avg trades/window: {timing['per_window_stats']['avg_trades_per_window']}")
    print(f"   Avg first entry: minute {timing['per_window_stats']['avg_first_entry_min']}")

    # ── Asset breakdown ──
    print("\n── Asset Breakdown ──")
    for asset, stats in timing["asset_stats"].items():
        print(f"   {asset:4s}: {stats['count']:4d} trades | ${stats['total_cost']:>8.2f} | avg price {stats['avg_price']:.4f}")

    # ── Phase analysis ──
    print("\n── Phase Analysis (when within the hour) ──")
    print(f"   {'Phase':<14s} {'Count':>6s} {'Total $':>10s} {'Avg Price':>10s} {'Med Price':>10s} {'Range':>16s}")
    print("   " + "-" * 68)
    for phase in ["early_0_15", "mid_15_30", "late_30_45", "final_45_60"]:
        p = timing["phases"][phase]
        lo, hi = p["price_range"]
        print(f"   {phase:<14s} {p['count']:>6d} ${p['total_cost']:>9.2f} {p['avg_price']:>10.4f} {p['median_price']:>10.4f} {lo:.2f}-{hi:.2f}")

    # ── Minute histogram (grouped by 5) ──
    print("\n── Minute Histogram (trade count per 5-min bin) ──")
    hist = timing["minute_histogram"]
    cost_hist = timing["cost_by_minute"]
    for start in range(0, 60, 5):
        count = sum(hist.get(m, 0) for m in range(start, start + 5))
        cost = sum(cost_hist.get(m, 0) for m in range(start, start + 5))
        bar = "█" * (count // 2)
        marker = ""
        if start in (0, 15, 30, 45):
            marker = " ◀ 15M boundary"
        elif start == 5:
            marker = " ◀ 5M only"
        print(f"   {start:2d}-{start+4:2d} min: {count:>4d} trades ${cost:>8.2f} {bar}{marker}")

    # ── 15M boundary analysis ──
    print("\n── 15M Boundary Proximity ──")
    bp = timing["boundary_proximity"]
    print(f"   Near 15M boundary (±90s): {bp['near_15m_boundary']} ({bp['pct_near_15m']}%)")
    print(f"   Near 5M boundary (±90s):  {bp['near_5m_boundary']} ({bp['pct_near_5m']}%)")
    print(f"   Far from any boundary:    {bp['far_from_boundary']} ({bp['pct_far']}%)")

    bd = timing["boundary_detail"]
    print(f"\n   Avg price near 15M: {bd['near_15m_avg_price']:.4f} (avg clip ${bd['near_15m_avg_cost']:.2f})")
    print(f"   Avg price far:      {bd['far_avg_price']:.4f} (avg clip ${bd['far_avg_cost']:.2f})")

    # ── Deep 15M boundary zones ──
    print("\n── 15M Boundary Zone Deep-Dive ──")
    print(f"   {'Zone':<20s} {'Count':>6s} {'Total $':>10s} {'Avg Price':>10s} {'$/trade':>10s}")
    print("   " + "-" * 58)
    for zone, data in boundaries["zones"].items():
        if data["count"] == 0:
            print(f"   {zone:<20s} {0:>6d}")
            continue
        print(f"   {zone:<20s} {data['count']:>6d} ${data['total_cost']:>9.2f} {data['avg_price']:>10.4f} ${data['avg_cost_per_trade']:>9.2f}")

    s = boundaries["summary"]
    print(f"\n   Post-15M density:  {s['post_15m_density_per_min']:.2f} trades/min")
    print(f"   Baseline density:  {s['baseline_density_per_min']:.2f} trades/min")
    print(f"   Density ratio:     {s['density_ratio']:.2f}x")
    print(f"   ➜ {s['verdict']}")

    # ── Scaling pattern ──
    print("\n── Scaling Pattern (dollar sizing by phase) ──")
    print(f"   {'Phase':<10s} {'N':>5s} {'Total $':>10s} {'Avg Clip':>10s} {'Med Clip':>10s} {'Low%':>6s} {'Mid%':>6s} {'High%':>6s}")
    print("   " + "-" * 65)
    for phase in ["early", "mid", "late", "final"]:
        if phase not in scaling:
            continue
        sc = scaling[phase]
        total = sc["total_cost"] or 1
        lo_pct = sc["price_buckets"]["low_lt30"]["total"] / total * 100
        mid_pct = sc["price_buckets"]["mid_30_70"]["total"] / total * 100
        hi_pct = sc["price_buckets"]["high_gt70"]["total"] / total * 100
        print(f"   {phase:<10s} {sc['n_trades']:>5d} ${sc['total_cost']:>9.2f} ${sc['avg_clip_size']:>9.2f} ${sc['median_clip_size']:>9.2f} {lo_pct:>5.1f}% {mid_pct:>5.1f}% {hi_pct:>5.1f}%")

    # ── Per-window trend ──
    print("\n── Price Trend Within Windows ──")
    trend_counts = timing["per_window_stats"]["price_trend_counts"]
    for trend, count in sorted(trend_counts.items(), key=lambda x: -x[1]):
        print(f"   {trend}: {count} windows")

    # ── Sample windows ──
    print("\n── Sample Windows (first 10) ──")
    for w in timing["per_window_stats"]["sample_windows"]:
        print(f"   {w['window_start_et']:>16s} | {w['n_trades']:>3d} trades | "
              f"${w['total_cost']:>7.2f} | entry min {w['first_entry_min']:>4.1f}-{w['last_entry_min']:>4.1f} | "
              f"avg ${w['avg_price']:.3f} | trend: {w['price_trend']}")

    # ── Boundary dislocation (proper pre vs post) ──
    if dislocation:
        print("\n" + "=" * 72)
        print("  BOUNDARY DISLOCATION (proper pre vs post, same window)")
        print("=" * 72)
        print("  Compares [N-2, N) vs [N, N+2) within each 1H window.")
        print("  Positive delta = price/count/cost INCREASED after boundary.")
        print("  |t| > 2.0 suggests statistical significance.\n")

        bd = dislocation.get("boundaries", {})
        for bkey in ["min_15", "min_30", "min_45"]:
            info = bd.get(bkey)
            if not info:
                continue
            label = bkey.replace("min_", "Minute ")
            print(f"  ── {label} Boundary (n={info['n_windows']} windows) ──")
            for metric in ["price_delta", "count_delta", "clip_delta", "cost_delta"]:
                d = info[metric]
                sig = " **" if abs(d["t_stat"]) >= 2.0 else ""
                sign = "+" if d["mean"] >= 0 else ""
                fmt = ".5f" if "price" in metric else ".4f" if "clip" in metric else ".2f"
                print(f"   {metric:<14s}  mean={sign}{d['mean']:{fmt}}  std={d['std']:{fmt}}  t={d['t_stat']:>+6.3f}{sig}")
            # Show a few sample windows
            samples = info.get("sample_windows", [])
            if samples:
                print(f"   Sample windows:")
                for sw in samples[:4]:
                    sign = "+" if sw["delta_price"] >= 0 else ""
                    print(f"     {sw['window']}: pre({sw['pre_n']}@{sw['pre_avg_price']:.4f}) "
                          f"post({sw['post_n']}@{sw['post_avg_price']:.4f}) "
                          f"delta={sign}{sw['delta_price']:.4f}")
            print()

        # ── Minute-over-minute profile ──
        mp = dislocation.get("minute_profile", {})
        if mp:
            print("  ── Minute-over-Minute Profile ──")
            print(f"   {'Min':>3s} {'Trades':>7s} {'Avg Price':>10s}  {'Bar'}")
            print("   " + "-" * 50)
            for m in range(60):
                entry = mp.get(m) or mp.get(str(m))
                if not entry:
                    continue
                cnt = entry["trade_count"]
                avg_p = entry["avg_price"]
                bar = "#" * (cnt // 2) if cnt else ""
                price_str = f"{avg_p:.4f}" if avg_p is not None else "   n/a "
                marker = ""
                if m in (15, 30, 45):
                    marker = " <<< 15M boundary"
                elif m == 0:
                    marker = " <<< hour start"
                print(f"   {m:>3d} {cnt:>7d} {price_str:>10s}  {bar}{marker}")

    print("\n" + "=" * 72)


def save_raw(timing: dict, boundaries: dict, scaling: dict, path: Path, dislocation: dict | None = None):
    """Save raw results as JSON for further analysis."""
    # Make serializable
    out = {
        "wallet": _WALLET,
        "generated_at": datetime.now(_HKT).isoformat(),
        "timing": timing,
        "boundaries": boundaries,
        "scaling": scaling,
    }
    if dislocation:
        out["dislocation"] = dislocation
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    log.info("Raw data saved to %s", path)


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Analyze blue-walnut 1H timing vs 15M boundaries")
    ap.add_argument("--max-pages", type=int, default=40, help="Max API pages to fetch (500 trades each)")
    ap.add_argument("--save", action="store_true", help="Save raw JSON results")
    args = ap.parse_args()

    log.info("Fetching trades for %s...", _WALLET[:10])
    raw_trades = fetch_all_trades(max_pages=args.max_pages)
    if not raw_trades:
        log.error("No trades fetched. Check network / wallet address.")
        sys.exit(1)

    log.info("Parsing %d raw trades...", len(raw_trades))
    trades = []
    timeframe_counts = Counter()
    for rt in raw_trades:
        t = parse_trade(rt)
        if t is None:
            continue
        tf = classify_market_timeframe(t["market"])
        timeframe_counts[tf] += 1
        if tf == "1H":
            trades.append(t)

    log.info("Timeframe distribution: %s", dict(timeframe_counts))
    log.info("1H trades for analysis: %d", len(trades))

    if len(trades) < 10:
        log.error("Too few 1H trades (%d). Cannot analyze.", len(trades))
        sys.exit(1)

    timing = analyze_timing(trades)
    boundaries = analyze_15m_boundaries(trades)
    scaling = analyze_scaling_pattern(trades)
    dislocation = analyze_boundary_dislocation(trades)

    print_report(timing, boundaries, scaling, dislocation)

    if args.save:
        out_path = _PROJECT / "polymarket" / "logs" / "whale_1h_analysis.json"
        save_raw(timing, boundaries, scaling, out_path, dislocation)


if __name__ == "__main__":
    main()
