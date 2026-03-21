#!/usr/bin/env python3
"""
ob_recorder.py — Lightweight Polymarket OB depth recorder for fill model calibration.

設計決定：
- 獨立運行，唔依賴 CLOB SDK — 純 urllib（同 market_data.py 同模式）
- 每 5s fetch active 15M BTC+ETH markets 嘅 UP + DOWN order books
- 所有 depth 數據寫入一行 JSONL per market per snapshot
- Gamma discovery 每 300s 刷新（同 run_mm_live.py 一致）
- Discovery scope: current + next window = max 4 markets (BTC+ETH × 2)
- Rate limit: sequential fetch + 0.5s delay between requests
  Shares CLOB budget with live MM bot (~100 req/min total)

用途：
- combined_best_ask (up_ask + down_ask) = arb spread indicator
- bid_depth_10 / ask_depth_10 = depth within 10¢ of best price
- time_to_end_s = seconds until window close (depth changes with proximity)
- 數據用於校準 depth-aware fill probability model

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/data/ob_recorder.py
  PYTHONPATH=.:scripts python3 polymarket/data/ob_recorder.py --once   # single snapshot then exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ════════════════════════════════════════
#  Constants
# ════════════════════════════════════════

_ET = ZoneInfo("America/New_York")
_HKT = ZoneInfo("Asia/Hong_Kong")
_UA = {"User-Agent": "AXC/1.0"}
_HTTP_TIMEOUT = 5
_CLOB_BASE = "https://clob.polymarket.com"
_GAMMA_BASE = "https://gamma-api.polymarket.com"

_CYCLE_S = 60         # target OB snapshot interval (12 markets × 3 calls ≈ 50s actual)
_DISCOVERY_S = 300    # re-discover markets every 5 min
_DISCOVERY_WINDOWS = 2  # current + next window (max 4 markets for BTC+ETH)
_INTER_REQ_DELAY = 0.5  # delay between CLOB requests (share budget with live MM bot)
_DEPTH_RANGE = 0.10   # 10¢ from best price for depth calculation

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
_TAPE_PATH = os.path.join(_LOG_DIR, "poly_ob_tape.jsonl")

# BTC + ETH + SOL (observe-only for SOL, but collect OB data for analysis)
_COINS = [("btc", "bitcoin"), ("eth", "ethereum"), ("sol", "solana")]

# ════════════════════════════════════════
#  Logging
# ════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ob_recorder")

# Graceful shutdown
_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info("Signal %d received — shutting down after current cycle", signum)
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ════════════════════════════════════════
#  HTTP helpers (same pattern as market_data.py)
# ════════════════════════════════════════

def _http_get(url: str, timeout: float = _HTTP_TIMEOUT) -> dict | list | None:
    """GET JSON from URL. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.warning("HTTP GET failed: %s — %s", url[:80], e)
        return None


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════
#  Discovery — find active 15M markets
# ════════════════════════════════════════

def _parse_gamma_market(raw: dict, slug: str, ts: int, te: int, coin_slug: str) -> dict | None:
    """Parse a single Gamma API market response into our internal format."""
    outcomes_raw = raw.get("outcomes", "[]")
    tokens_raw = raw.get("clobTokenIds", "[]")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except (json.JSONDecodeError, TypeError):
        outcomes = []
    try:
        token_ids = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
    except (json.JSONDecodeError, TypeError):
        token_ids = []

    if len(outcomes) < 2 or len(token_ids) < 2:
        logger.warning("Incomplete market %s: outcomes=%s tokens=%d", slug, outcomes, len(token_ids))
        return None

    if outcomes[0].lower() not in ("up", "yes"):
        logger.error("OUTCOME SWAPPED %s: %s — skipping", slug, outcomes)
        return None

    return {
        "condition_id": raw.get("conditionId", ""),
        "title": raw.get("question", slug),
        "slug": slug,
        "up_token_id": str(token_ids[0]),
        "down_token_id": str(token_ids[1]),
        "start_ts": ts,
        "end_ts": te,
        "coin": coin_slug.upper(),
    }


# 1H slug helpers (same pattern as run_1h_live.py)
_COIN_SLUGS_1H = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana"}


def _discover_15m() -> list[dict]:
    """Find active BTC + ETH 15M markets."""
    results = []
    now_s = int(time.time())
    now_et = datetime.now(tz=_ET)
    slot = (now_et.minute // 15) * 15
    base = now_et.replace(minute=0, second=0, microsecond=0)

    for i in range(_DISCOVERY_WINDOWS):
        ws = base + timedelta(minutes=slot + i * 15)
        we = ws + timedelta(minutes=15)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 120:
            continue
        for coin_slug, _ in _COINS:
            slug = f"{coin_slug}-updown-15m-{ts}"
            data = _http_get(f"{_GAMMA_BASE}/markets?slug={slug}")
            if not data or not isinstance(data, list) or not data:
                continue
            parsed = _parse_gamma_market(data[0], slug, ts, te, coin_slug)
            if parsed:
                results.append(parsed)
    return results


def _discover_1h() -> list[dict]:
    """Find active BTC + ETH 1H markets (same slug pattern as run_1h_live.py)."""
    results = []
    now_s = int(time.time())
    now_et = datetime.now(tz=_ET)
    base = now_et.replace(minute=0, second=0, microsecond=0)

    for i in range(2):  # current + next hour
        ws = base + timedelta(hours=i)
        we = ws + timedelta(hours=1)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 300:
            continue
        for coin_slug, coin_name in _COIN_SLUGS_1H.items():
            hour = ws.strftime("%I").lstrip("0")
            ampm = ws.strftime("%p").lower()
            slug = f"{coin_name}-up-or-down-{ws.strftime('%B').lower()}-{ws.day}-{ws.year}-{hour}{ampm}-et"
            data = _http_get(f"{_GAMMA_BASE}/markets?slug={slug}")
            if not data or not isinstance(data, list) or not data:
                continue
            parsed = _parse_gamma_market(data[0], slug, ts, te, coin_slug)
            if parsed:
                results.append(parsed)
    return results


def discover_markets() -> list[dict]:
    """Find active 15M + 1H markets for BTC + ETH."""
    results_15m = _discover_15m()
    results_1h = _discover_1h()
    results = results_15m + results_1h
    now_s = int(time.time())
    logger.info("Discovery: found %d markets (%d 15M + %d 1H)",
                len(results), len(results_15m), len(results_1h))
    for m in results:
        tte = m["end_ts"] - now_s
        logger.info("  %s %s (ends in %dm%ds)", m["coin"], m["slug"], tte // 60, tte % 60)
    return results


# ════════════════════════════════════════
#  OB fetch + depth calculation
# ════════════════════════════════════════

def fetch_ob(token_id: str) -> dict | None:
    """Fetch order book for a single token from Polymarket CLOB.

    Returns parsed JSON: {"bids": [...], "asks": [...]} or None on failure.
    """
    url = f"{_CLOB_BASE}/book?token_id={token_id}"
    return _http_get(url)


def calc_depth(book: dict, side: str, range_cents: float = _DEPTH_RANGE) -> tuple[float, float]:
    """Calculate best price and depth within range_cents of best.

    Returns (best_price, depth_within_range) in shares.
    """
    entries = book.get(side, [])
    if not entries:
        return (0.0, 0.0)

    # Entries are sorted by price (bids desc, asks asc) from CLOB
    best = _safe_float(entries[0].get("price", 0))
    if best <= 0:
        return (0.0, 0.0)

    depth = 0.0
    for e in entries:
        price = _safe_float(e.get("price", 0))
        size = _safe_float(e.get("size", 0))
        if side == "bids":
            if price >= best - range_cents:
                depth += size
            else:
                break  # sorted desc, no more in range
        else:  # asks
            if price <= best + range_cents:
                depth += size
            else:
                break  # sorted asc, no more in range

    return (best, depth)


# ════════════════════════════════════════
#  Snapshot — one complete OB reading per market
# ════════════════════════════════════════

def fetch_trades(condition_id: str, limit: int = 100) -> list[dict]:
    """Fetch recent trades for a market from Polymarket Data API (public, no auth).

    CLOB /trades requires auth; Data API is public and has same data.
    Returns list of trade dicts or empty list on failure.
    """
    url = f"https://data-api.polymarket.com/trades?market={condition_id}&limit={limit}"
    data = _http_get(url)
    if data and isinstance(data, list):
        return data
    return []


def calc_trade_flow(trades: list[dict], window_sec: float = 300) -> dict:
    """Compute trade flow metrics from recent trades.

    Returns: count, volume, buy_volume, sell_volume, largest_trade, taker_ratio
    Only considers trades within window_sec (default 5 min) of now.
    """
    now = time.time()
    count = 0
    total_vol = 0.0
    buy_vol = 0.0
    sell_vol = 0.0
    largest = 0.0

    for t in trades:
        # Parse trade timestamp — Data API returns unix int
        t_ts = t.get("timestamp", t.get("match_time", 0))
        try:
            trade_time = float(t_ts)
            if trade_time < 1e9:
                continue  # invalid
        except (TypeError, ValueError):
            continue

        if now - trade_time > window_sec:
            continue

        size = _safe_float(t.get("size", 0))
        if size <= 0:
            continue

        count += 1
        total_vol += size
        if size > largest:
            largest = size

        side = t.get("side", "").upper()
        if side == "BUY":
            buy_vol += size
        else:
            sell_vol += size

    taker_ratio = buy_vol / (buy_vol + sell_vol) if (buy_vol + sell_vol) > 0 else 0.5

    return {
        "trade_count_5m": count,
        "trade_vol_5m": round(total_vol, 2),
        "trade_buy_vol": round(buy_vol, 2),
        "trade_sell_vol": round(sell_vol, 2),
        "trade_largest": round(largest, 2),
        "trade_taker_ratio": round(taker_ratio, 4),
    }


def fetch_prices_history(token_id: str, lookback_sec: int = 300, fidelity: int = 30) -> list[dict]:
    """Fetch token price history from CLOB (public, no auth).

    Returns list of {"t": unix_ts, "p": price} or empty list on failure.
    Fallback to signal_tape — provides 30s-fidelity price trajectory.
    """
    end_ts = int(time.time())
    start_ts = end_ts - lookback_sec
    url = (f"{_CLOB_BASE}/prices-history?market={token_id}"
           f"&startTs={start_ts}&endTs={end_ts}&fidelity={fidelity}")
    data = _http_get(url)
    if data and isinstance(data, dict):
        return data.get("history", [])
    return []


def calc_price_momentum(history: list[dict]) -> dict:
    """Compute momentum metrics from price history.

    Returns: price_now, price_5m_ago, momentum (change), volatility (std of changes).
    """
    if not history or len(history) < 2:
        return {"ph_price": 0.0, "ph_price_5m_ago": 0.0, "ph_momentum": 0.0, "ph_vol": 0.0, "ph_points": 0}

    prices = [_safe_float(h.get("p", 0)) for h in history if _safe_float(h.get("p", 0)) > 0]
    if len(prices) < 2:
        return {"ph_price": 0.0, "ph_price_5m_ago": 0.0, "ph_momentum": 0.0, "ph_vol": 0.0, "ph_points": 0}

    now_p = prices[-1]
    old_p = prices[0]
    momentum = now_p - old_p

    # Volatility: std of consecutive changes
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    vol = (sum(c * c for c in changes) / len(changes)) ** 0.5 if changes else 0.0

    return {
        "ph_price": round(now_p, 4),
        "ph_price_5m_ago": round(old_p, 4),
        "ph_momentum": round(momentum, 4),
        "ph_vol": round(vol, 6),
        "ph_points": len(prices),
    }


def take_snapshot(market: dict) -> dict | None:
    """Fetch UP + DOWN order books + trade flow and compute depth metrics.

    Returns a flat dict ready for JSONL, or None if both fetches failed.
    """
    ts = time.time()

    # Fetch UP book
    up_book = fetch_ob(market["up_token_id"])
    time.sleep(_INTER_REQ_DELAY)

    # Fetch DOWN book
    down_book = fetch_ob(market["down_token_id"])
    time.sleep(_INTER_REQ_DELAY)

    # Fetch recent trades (market-level, not per-token)
    trades = fetch_trades(market["condition_id"])
    time.sleep(_INTER_REQ_DELAY)

    # Fetch prices-history (UP token, 30s fidelity, last 5 min)
    # Fallback/complement to signal_tape — finer granularity from CLOB
    up_price_hist = fetch_prices_history(market["up_token_id"])

    if up_book is None and down_book is None:
        logger.warning("Both OB fetches failed for %s", market["slug"])
        return None

    # Calculate depth metrics
    up_best_bid, up_bid_depth = calc_depth(up_book, "bids") if up_book else (0.0, 0.0)
    up_best_ask, up_ask_depth = calc_depth(up_book, "asks") if up_book else (0.0, 0.0)
    down_best_bid, down_bid_depth = calc_depth(down_book, "bids") if down_book else (0.0, 0.0)
    down_best_ask, down_ask_depth = calc_depth(down_book, "asks") if down_book else (0.0, 0.0)

    combined_best_ask = 0.0
    if up_best_ask > 0 and down_best_ask > 0:
        combined_best_ask = round(up_best_ask + down_best_ask, 4)

    return {
        "ts": round(ts, 3),
        "ts_hkt": datetime.fromtimestamp(ts, tz=_HKT).strftime("%Y-%m-%d %H:%M:%S"),
        "condition_id": market["condition_id"],
        "coin": market["coin"],
        "slug": market["slug"],
        "window_end_ts": market["end_ts"],
        "time_to_end_s": max(0, market["end_ts"] - int(ts)),
        "up_token_id": market["up_token_id"],
        "down_token_id": market["down_token_id"],
        "up_best_bid": round(up_best_bid, 4),
        "up_best_ask": round(up_best_ask, 4),
        "up_bid_depth_10": round(up_bid_depth, 2),
        "up_ask_depth_10": round(up_ask_depth, 2),
        "down_best_bid": round(down_best_bid, 4),
        "down_best_ask": round(down_best_ask, 4),
        "down_bid_depth_10": round(down_bid_depth, 2),
        "down_ask_depth_10": round(down_ask_depth, 2),
        "combined_best_ask": combined_best_ask,
        **calc_trade_flow(trades, window_sec=300),  # 5-min window
        **calc_price_momentum(up_price_hist),  # price trajectory fallback
    }


def write_snapshot(record: dict) -> None:
    """Append one JSON line to the tape file."""
    os.makedirs(os.path.dirname(_TAPE_PATH), exist_ok=True)
    with open(_TAPE_PATH, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


# ════════════════════════════════════════
#  Main loop
# ════════════════════════════════════════

def run(once: bool = False) -> None:
    """Main recording loop.

    Adaptive cycle: if snapshot takes > _CYCLE_S, continues immediately.
    """
    logger.info("OB Recorder starting — tape: %s", _TAPE_PATH)
    logger.info("Cycle: %ds | Discovery: %ds | Windows: %d | Inter-req: %.1fs",
                _CYCLE_S, _DISCOVERY_S, _DISCOVERY_WINDOWS, _INTER_REQ_DELAY)

    markets: list[dict] = []
    last_discovery = 0.0

    while _running:
        cycle_start = time.time()

        # Discovery refresh
        if time.time() - last_discovery > _DISCOVERY_S or not markets:
            markets = discover_markets()
            last_discovery = time.time()
            if not markets:
                logger.info("No active markets — waiting 30s")
                if once:
                    return
                time.sleep(30)
                continue

        # Take snapshots for all active markets
        n_ok = 0
        summaries = []
        now_s = time.time()
        for mkt in markets:
            if not _running:
                break
            # Skip expired markets (>60s past end)
            if now_s > mkt["end_ts"] + 60:
                continue
            record = take_snapshot(mkt)
            if record:
                try:
                    write_snapshot(record)
                except OSError as e:
                    logger.error("Write failed (disk full?): %s — skipping", e)
                    continue
                summaries.append(record)
                n_ok += 1
            # Inter-market delay for rate limiting
            if _running:
                time.sleep(_INTER_REQ_DELAY)

        elapsed = time.time() - cycle_start
        # Compact summary log
        parts = []
        for r in summaries:
            parts.append(f"{r['coin']} cba={r['combined_best_ask']:.2f} "
                         f"tte={r['time_to_end_s']}s "
                         f"d={r['up_bid_depth_10']:.0f}/{r['up_ask_depth_10']:.0f}")
        logger.info("Snap %d/%d in %.1fs | %s",
                     n_ok, len(markets), elapsed, " | ".join(parts) if parts else "none")

        if once:
            logger.info("--once mode complete. Last records:")
            for r in summaries:
                logger.info("  %s %s cba=%.4f tte=%ds depth(up)=%.0f/%.0f depth(dn)=%.0f/%.0f",
                            r["coin"], r["ts_hkt"], r["combined_best_ask"], r["time_to_end_s"],
                            r["up_bid_depth_10"], r["up_ask_depth_10"],
                            r["down_bid_depth_10"], r["down_ask_depth_10"])
            return

        # Adaptive sleep: if cycle took longer than target, continue immediately
        sleep_time = max(0, _CYCLE_S - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)
        elif elapsed > _CYCLE_S * 2:
            logger.warning("Cycle took %.1fs (>%.1fs target) — consider reducing market count",
                           elapsed, _CYCLE_S)

    logger.info("OB Recorder stopped. Tape: %s", _TAPE_PATH)


def main():
    parser = argparse.ArgumentParser(description="Polymarket OB depth recorder")
    parser.add_argument("--once", action="store_true", help="Single snapshot then exit")
    args = parser.parse_args()
    run(once=args.once)


if __name__ == "__main__":
    main()
