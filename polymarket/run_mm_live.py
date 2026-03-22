#!/usr/bin/env python3
"""
run_mm_live.py — v4 Dual-Layer Runner

策略：Dual-layer (hedge + directional) with signal pipeline。
- Zone 1 (0.50-0.57): pure hedge (guaranteed if both fill)
- Zone 2 (0.57-0.65): 50% hedge + 50% directional
- Zone 3 (>0.65): 25% hedge + 75% directional
- Cancel defense: spot move + TTL + window-end (layer-specific)

流程（每 30 秒）：
1. Fetch coin price + vol + indicators
2. Refresh bankroll
3. Discover markets（slug-based）→ watchlist
4. Enter with directional/asymmetric sizing
5. Cancel stale GTC 2 min before window end
6. Confirm fills via get_trades()
7. Check resolutions → PnL
8. Save state

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --live --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --status
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import time
import urllib.request
from copy import copy as _copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import (
    MMConfig, MMMarketState, PlannedOrder,
    compute_fair_up, plan_opening, apply_fill,
    resolve_market, should_enter_market, calc_tranches,
)
from polymarket.core.context import PolyMarket
from polymarket.exchange.gamma_client import GammaClient
from polymarket.config.settings import MM_DAILY_LOSS_LIMIT

logger = logging.getLogger(__name__)

_HKT = ZoneInfo("Asia/Hong_Kong")
_ET = ZoneInfo("America/New_York")
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades.jsonl")
_SIGNAL_LOG = os.path.join(_LOG_DIR, "mm_signals.jsonl")  # OB + cross-exchange data for taker research
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log.jsonl")  # per-order lifecycle: submit/fill/cancel/post_fill
_POS_LOG = os.path.join(_LOG_DIR, "mm_positions.jsonl")  # position snapshots for post-session analysis
_CYCLE_S = 5           # 5s main loop — fast reaction
_SCAN_S = 300          # discovery every 5 min (watchlist covers gaps)
_HEAVY_INTERVAL_S = 5  # heavy ops every 5s (~60 CLOB req/min, stop OB recorder if hitting limit)

# Newbie protection: first N hours of live trading, cap exposure
_PROTECTION_HOURS = 3
_PROTECTION_BET_PCT = 0.01   # 1% per market during protection
_PROTECTION_MAX_MARKETS = 1  # 1 market per cycle (= 1 per 15min window)
_MAX_ROUNDS = 3          # max scalp rounds per market window
_REENTRY_COOLDOWN_S = 30 # seconds after sell before re-entry
# Live execution gate: only these coins place real orders.
# ETH + SOL = discover + log signals but NEVER execute (observation only).
_LIVE_TRADE_COINS = {"btc"}  # lowercase slug prefix
_BINANCE = "https://fapi.binance.com"
_BINANCE_SPOT = "https://api.binance.com"

# Rate limit safety: track API calls per minute
_api_calls: dict = {}  # {"binance": [(ts, count), ...]}
_API_LIMIT_PER_MIN = 200  # conservative: 200/min out of 2400 limit
_mkt_fetcher = None  # StaggeredFetcher instance (set in main, used in run_cycle for logging)


def _rate_ok(source: str = "binance") -> bool:
    """Check if we're within safe API call rate."""
    now = time.time()
    calls = _api_calls.get(source, [])
    # Remove calls older than 60s
    calls = [(t, c) for t, c in calls if now - t < 60]
    _api_calls[source] = calls
    total = sum(c for _, c in calls)
    return total < _API_LIMIT_PER_MIN


def _track_call(source: str = "binance", n: int = 1):
    """Track an API call for rate limiting."""
    _api_calls.setdefault(source, []).append((time.time(), n))


# ═══════════════════════════════════════
#  Data — High-Frequency Layer (3-5s cache)
# ═══════════════════════════════════════

_cache: dict = {}

# ─── Holder imbalance tracking (whale exit detection) ───
_holder_cache: dict = {}  # cid → (imbalance, timestamp)
_HOLDER_CACHE_TTL = 30    # seconds — holders don't change every second
_DATA_API = "https://data-api.polymarket.com"


def _holder_imbalance(condition_id: str, up_token_id: str,
                      ttl_override: float = 0) -> tuple[float, float]:
    """Fetch holder position imbalance + delta from previous reading.

    Returns (imbalance, delta). imbalance ∈ [-1, +1], delta = change since last.
    Positive imbalance = more UP shares. Negative delta = whale exit from UP.
    Cached 30s (or ttl_override for last-minute burst mode).
    Uses Data API (separate rate limit from CLOB).
    """
    key = f"holder_{condition_id}"
    now = time.time()
    ttl = ttl_override if ttl_override > 0 else _HOLDER_CACHE_TTL

    # Check cache
    if key in _cache and now - _cache[key][1] < ttl:
        return _cache[key][0]

    try:
        url = f"{_DATA_API}/holders?market={condition_id}&limit=20"
        req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            groups = json.loads(r.read())
    except Exception as e:
        logger.debug("Holder fetch failed: %s", e)
        cached = _cache.get(key, ((0.0, 0.0), 0))[0]
        return cached

    if not groups or not isinstance(groups, list):
        return (0.0, 0.0)

    up_shares = 0.0
    down_shares = 0.0
    for group in groups:
        token = group.get("token", "")
        is_up = token == up_token_id
        for h in group.get("holders", []):
            amt = float(h.get("amount", 0)) if h.get("amount") else 0.0
            if is_up:
                up_shares += amt
            else:
                down_shares += amt

    total = up_shares + down_shares
    imbalance = (up_shares - down_shares) / total if total > 0 else 0.0

    # Delta from previous reading
    prev_imbalance = _holder_cache.get(condition_id, (0.0, 0))[0]
    prev_ts = _holder_cache.get(condition_id, (0.0, 0))[1]
    delta = imbalance - prev_imbalance if prev_ts > 0 else 0.0

    _holder_cache[condition_id] = (imbalance, now)
    result = (round(imbalance, 4), round(delta, 4))
    _cache[key] = (result, now)
    return result


def _price(symbol: str = "BTCUSDT") -> float:
    """Latest price. Cached 1s — tighter for cancel defense (was 3s, reduced 2026-03-22)."""
    key = f"price_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 1:
        return _cache[key][0]
    if not _rate_ok("binance"):
        return _cache.get(key, (0, 0))[0]
    # Use book ticker for fastest price (best bid+ask, single call)
    url = f"{_BINANCE_SPOT}/api/v3/ticker/bookTicker?symbol={symbol}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            data = json.loads(r.read())
            bid = float(data.get("bidPrice", 0))
            ask = float(data.get("askPrice", 0))
            price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            if price > 0:
                _cache[key] = (price, now)
                _track_call("binance")
                return price
    except Exception:
        pass
    # Fallback to kline
    url = f"{_BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=1"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            price = float(json.loads(r.read())[0][4])
            _cache[key] = (price, now)
            _track_call("binance")
            return price
    except Exception as e:
        logger.warning("%s price fetch failed: %s", symbol, e)
        return _cache.get(key, (0, 0))[0]


def _btc_price() -> float:
    return _price("BTCUSDT")


# ── Cross-exchange price validation ──
# Fetch from 3 exchanges, use median. Detect anomalies.
_CROSS_EXCHANGES = {
    "binance": "https://api.binance.com/api/v3/ticker/price?symbol={sym}",
    "okx":     "https://www.okx.com/api/v5/market/ticker?instId={sym_okx}",
    "bybit":   "https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}",
}
_SYM_MAP_OKX = {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"}


def _cross_exchange_price(symbol: str = "BTCUSDT") -> tuple[float, float]:
    """Fetch price from 3 exchanges, return (median, max_divergence_pct).
    divergence = (max - min) / median. High = anomaly.
    Falls back to Binance-only if others fail.
    Cached 5s — faster flash crash detection."""
    key = f"xprice_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:  # 5s for faster flash crash detection
        return _cache[key][0]

    prices = []
    # Binance (fastest, always try)
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            p = float(json.loads(r.read()).get("price", 0))
            if p > 0:
                prices.append(p)
    except Exception:
        pass

    # OKX
    try:
        okx_sym = _SYM_MAP_OKX.get(symbol, symbol.replace("USDT", "-USDT"))
        url = f"https://www.okx.com/api/v5/market/ticker?instId={okx_sym}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            data = json.loads(r.read()).get("data", [{}])
            p = float(data[0].get("last", 0)) if data else 0
            if p > 0:
                prices.append(p)
    except Exception:
        pass

    # Bybit
    try:
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            result = json.loads(r.read()).get("result", {}).get("list", [{}])
            p = float(result[0].get("lastPrice", 0)) if result else 0
            if p > 0:
                prices.append(p)
    except Exception:
        pass

    if not prices:
        cached = _cache.get(key, ((0, 0), 0))[0]
        return cached

    prices.sort()
    median = prices[len(prices) // 2]
    divergence = (prices[-1] - prices[0]) / median if median > 0 and len(prices) > 1 else 0.0

    result = (median, divergence)
    _cache[key] = (result, now)
    _track_call("binance")
    return result


def _open_at(start_ms: int, symbol: str = "BTCUSDT") -> float:
    """Price at a specific timestamp. Cached permanently (historical)."""
    key = f"open_{symbol}_{start_ms}"
    if key in _cache:
        return _cache[key][0]
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&startTime={start_ms}&limit=1"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            price = float(json.loads(r.read())[0][1])
            _cache[key] = (price, time.time())
            _track_call("binance")
            return price
    except Exception:
        return 0.0


def _btc_open_at(start_ms: int) -> float:
    return _open_at(start_ms, "BTCUSDT")


def _vol_1m(symbol: str = "BTCUSDT") -> float:
    """Per-minute vol. Cached 60s — slow-moving, no need for fast refresh."""
    key = f"vol_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 60:
        return _cache[key][0]
    if not _rate_ok("binance"):
        return _cache.get(key, (0.001, 0))[0]
    url = f"{_BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=120"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=10) as r:
            closes = [float(k[4]) for k in json.loads(r.read())]
        _track_call("binance")
        if len(closes) < 20:
            return _cache.get(key, (0.001, 0))[0]
        rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
        mean = sum(rets) / len(rets)
        vol = max(0.0001, math.sqrt(sum((r - mean)**2 for r in rets) / len(rets)))
        _cache[key] = (vol, now)
        return vol
    except Exception:
        return _cache.get(key, (0.001, 0))[0]


def _cvd_buy_ratio(symbol: str = "BTCUSDT", minutes: int = 3) -> float:
    """Taker buy ratio over last N minutes. >0.55 = buying pressure, <0.45 = selling.
    Uses Binance spot 1m klines (taker_buy_volume included). Cached 15s (was 30s)."""
    key = f"cvd_{symbol}_{minutes}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 15:
        return _cache[key][0]
    if not _rate_ok("binance"):
        return _cache.get(key, (0.5, 0))[0]
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&limit={minutes + 1}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            candles = json.loads(r.read())
        _track_call("binance")
        if len(candles) < 2:
            return 0.5
        # Use last N candles (skip first partial)
        recent = candles[-minutes:]
        total_vol = sum(float(c[5]) for c in recent)
        total_buy = sum(float(c[9]) for c in recent)  # index 9 = taker_buy_volume
        ratio = total_buy / total_vol if total_vol > 0 else 0.5
        _cache[key] = (ratio, now)
        return ratio
    except Exception:
        return _cache.get(key, (0.5, 0))[0]


def _m1_return(symbol: str = "BTCUSDT") -> float:
    """Last 1-minute return (log). Reuses _vol_1m cache if fresh, else fetches 2 candles.
    Returns 0.0 if unavailable. Positive = price went up."""
    # Try vol cache first — it has 60 closes, last ret = M1
    vol_key = f"vol_{symbol}"
    if vol_key in _cache and time.time() - _cache[vol_key][1] < 60:
        # Vol was computed recently — fetch fresh M1 from 2 candles (cheap)
        pass
    if not _rate_ok("binance"):
        return 0.0
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&limit=2"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            candles = json.loads(r.read())
        _track_call("binance")
        if len(candles) < 2:
            return 0.0
        c_prev = float(candles[0][4])  # previous 1m close
        c_now = float(candles[1][4])   # current 1m close
        if c_prev <= 0:
            return 0.0
        return math.log(c_now / c_prev)
    except Exception:
        return 0.0


def _poly_midpoint(client, token_id: str) -> float:
    """Polymarket midpoint for a token. Cached 5s."""
    key = f"mid_{token_id[:16]}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:
        return _cache[key][0]
    if not client or not hasattr(client, "get_midpoint"):
        return 0.0
    try:
        mid = client.get_midpoint(token_id)
        if mid > 0:
            _cache[key] = (mid, now)
            _track_call("clob")
        return mid
    except Exception:
        return _cache.get(key, (0, 0))[0]


def _poly_ob_imbalance(client, up_token: str) -> float:
    """Order book imbalance for UP token. Cached 5s. Returns -1 to +1."""
    key = f"obi_{up_token[:16]}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:
        return _cache[key][0]
    if not client or not hasattr(client, "get_order_book"):
        return 0.0
    try:
        book = client.get_order_book(up_token)
        bid_vol = sum(b["size"] for b in book.get("bids", []))
        ask_vol = sum(a["size"] for a in book.get("asks", []))
        if bid_vol + ask_vol > 0:
            imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            _cache[key] = (imb, now)
            _track_call("clob")
            return imb
    except Exception:
        pass
    return _cache.get(key, (0, 0))[0]


# ═══════════════════════════════════════
#  Discovery（slug-based）
# ═══════════════════════════════════════

def _discover(gamma: GammaClient, config: MMConfig) -> list[tuple[PolyMarket, dict]]:
    """Find BTC + ETH 15M markets for current + next 4 windows via slug."""
    results = []
    now_s = int(time.time())
    now_et = datetime.now(tz=_ET)
    slot = (now_et.minute // 15) * 15
    base = now_et.replace(minute=0, second=0, microsecond=0)

    _COINS = [("btc", "bitcoin"), ("eth", "ethereum"), ("sol", "solana")]

    for i in range(5):
        ws = base + timedelta(minutes=slot + i * 15)
        we = ws + timedelta(minutes=15)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 120:
            continue

        for coin_slug, coin_title_kw in _COINS:
            slug = f"{coin_slug}-updown-15m-{ts}"
            try:
                _url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
                with urllib.request.urlopen(
                        urllib.request.Request(_url, headers={"User-Agent": "AXC/1.0"}),
                        timeout=5) as _resp:
                    data = json.loads(_resp.read())
            except Exception as e:
                logger.warning("Gamma slug fetch failed for %s: %s", slug, e)
                continue
            if not data or not isinstance(data, list):
                continue

            parsed = gamma.parse_market(data[0])
            outcomes = parsed.get("outcomes", [])
            if outcomes and isinstance(outcomes, list) and len(outcomes) >= 2:
                if outcomes[0].lower() not in ("up", "yes"):
                    logger.error("OUTCOME SWAPPED %s: %s", slug, outcomes)
                    continue

            pm = PolyMarket(
                condition_id=parsed["condition_id"], title=parsed["title"],
                category="crypto_15m", end_date=we.isoformat(),
                yes_token_id=parsed.get("yes_token_id", ""),
                no_token_id=parsed.get("no_token_id", ""),
                yes_price=parsed.get("yes_price", 0.5),
                no_price=parsed.get("no_price", 0.5),
                liquidity=parsed.get("liquidity", 0),
            )
            if should_enter_market(pm, config):
                results.append((pm, {"start_ms": ts * 1000, "end_ms": te * 1000,
                                      "end_time": we.isoformat()}))
    return results


# ═══════════════════════════════════════
#  Order Execution
# ═══════════════════════════════════════

def _execute(orders: list[PlannedOrder], client,
             cid: str = "", signal_ctx: dict | None = None) -> list[dict]:
    """Submit limit orders. Returns order IDs — NOT fills.

    IMPORTANT: Limit orders (GTC) go on the book. Submit ≠ filled.
    Fills are checked later via _check_fills().

    cid: condition_id for per-order logging.
    signal_ctx: market state at submit time (fair, bridge, cvd, vol, mid) for AS analysis.
    """
    results = []
    _ctx = signal_ctx or {}
    for o in orders:
        try:
            amount = round(o.size * o.price, 2)
            r = client.buy_shares(o.token_id, amount, price=o.price)
            order_id = ""
            status = ""
            if isinstance(r, dict):
                order_id = r.get("orderID", r.get("id", ""))
                status = r.get("status", "")
                # Dry-run: simulate instant fill (no real CLOB)
                if r.get("dry_run"):
                    status = "matched"
            logger.info("ORDER SUBMITTED %s %s: %.1f shares @ $%.3f ($%.2f) → %s [%s]",
                        o.outcome, o.token_id[:10], o.size, o.price, amount,
                        order_id[:12] if order_id else "ok", status)
            results.append({"outcome": o.outcome, "price": o.price,
                           "size": o.size, "token_id": o.token_id,
                           "order_id": order_id, "status": status,
                           "submitted": True, "order_ts": time.time()})
            # Per-order submit log (AS analysis data)
            _log_order("submit", order_id, cid,
                       outcome=o.outcome, price=o.price, size=o.size,
                       status=status, **_ctx)
        except Exception as e:
            logger.error("ORDER FAILED %s: %s", o.outcome, e)
            results.append({"outcome": o.outcome, "submitted": False, "error": str(e)})
    return results


def _check_fills(state: dict, client) -> None:
    """Check which submitted orders actually filled on-chain.

    Queries open orders + trades to determine real fill status.
    Updates market state to reflect actual positions.
    """
    if not client or not hasattr(client, "get_orders"):
        return

    now_ms = int(time.time() * 1000)
    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        # Skip if already confirmed fills
        if mkt.get("fills_confirmed"):
            continue

        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        # Don't check after window ends — exchange may cancel unfilled orders
        # which would falsely appear as "filled"
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            # Window over — mark remaining pending as unfilled, not filled
            _bump_fill(state, "expired", len(pending))
            for _ep in pending:
                _log_order("expired", _ep.get("order_id", ""), cid,
                           outcome=_ep.get("outcome", ""))
            logger.info("Window ended %s: %d pending orders → expired (not filled)",
                        cid[:8], len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue

        try:
            # FIX #1: Use get_trades() for reliable fill confirmation
            # "not in open_orders" could mean cancelled, not filled
            trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
            trade_order_ids = set()
            for t in (trades or []):
                # Trades reference taker_order_id or maker_orders
                taker_id = t.get("taker_order_id", "")
                if taker_id:
                    trade_order_ids.add(taker_id)
                for mo in t.get("maker_orders", []):
                    mid = mo.get("order_id", "") if isinstance(mo, dict) else ""
                    if mid:
                        trade_order_ids.add(mid)

            # Also check open orders as secondary signal
            open_orders = client.get_orders(market=cid)
            open_ids = {o.get("id", "") for o in open_orders} if open_orders else set()

            filled = []
            still_open = []
            for po in pending:
                oid = po.get("order_id", "")
                if oid and oid in trade_order_ids:
                    # Confirmed by trade record — definitely filled
                    filled.append(po)
                elif oid and oid in open_ids:
                    # Still on book — not filled yet
                    still_open.append(po)
                else:
                    # Not in trades AND not in open orders → likely cancelled
                    _bump_fill(state, "cancelled")
                    _log_order("cancelled_external", po.get("order_id", ""), cid,
                               outcome=po.get("outcome", ""))
                    logger.info("Order %s %s: not in trades or open → cancelled",
                                cid[:8], po["outcome"])

            if filled:
                # FIX #7: Don't reset — instant fills from apply_fill are already correct
                # Just ADD newly confirmed fills on top
                for f in filled:
                    outcome = f["outcome"]
                    price = f["price"]
                    size = f["size"]
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * price) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * price) / mkt["down_shares"]
                    mkt["entry_cost"] += size * price
                    _bump_fill(state, "filled")
                    # Get midpoint at fill time for AS measurement
                    _fill_mid = 0.0
                    _tok = f.get("token_id", "")
                    if _tok and hasattr(client, "get_midpoint"):
                        _fill_mid = _poly_midpoint(client, _tok)
                    # AS metrics: BTC price at fill + time to fill
                    _title = mkt.get("title", "").lower()
                    _fill_sym = "ETHUSDT" if "ethereum" in _title else "BTCUSDT"
                    _btc_fill = _price(_fill_sym)
                    _order_ts = f.get("order_ts", 0)
                    _ttf = round(time.time() - _order_ts, 1) if _order_ts > 0 else 0
                    _log_order("fill", f.get("order_id", ""), cid,
                               outcome=outcome, price=price, size=size,
                               mid_at_fill=round(_fill_mid, 4) if _fill_mid else 0,
                               btc_at_fill=round(_btc_fill, 2),
                               time_to_fill_s=_ttf)
                    # Schedule post-fill check (60s later) for AS cost measurement
                    if _tok:
                        _post_fill_checks.append(
                            (time.time() + 60, f.get("order_id", ""), cid, _tok))
                    logger.info("FILL CONFIRMED %s %s: %.1f @ $%.3f mid=%.3f",
                                cid[:8], outcome, size, price, _fill_mid)

                mkt["pending_orders"] = still_open
                if not still_open:
                    mkt["fills_confirmed"] = True
                    logger.info("ALL FILLS CONFIRMED %s: UP=%.1f DN=%.1f cost=$%.2f",
                                cid[:8], mkt["up_shares"], mkt["down_shares"],
                                mkt["entry_cost"])

        except Exception as e:
            logger.warning("Fill check failed for %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  State
# ═══════════════════════════════════════

_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}


def _bump_fill(state: dict, event: str, n: int = 1):
    """Increment fill rate counter. event: submitted/filled/cancelled/expired."""
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


def _fill_rate(state: dict) -> tuple[float, int, int]:
    """Returns (fill_rate_pct, filled, submitted). 0% if no data."""
    fs = state.get("fill_stats", _FILL_STATS_DEFAULT)
    s, f = fs.get("submitted", 0), fs.get("filled", 0)
    return (f / s * 100 if s > 0 else 0.0), f, s


def _load() -> dict:
    if not os.path.exists(_STATE_PATH):
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "last_scan": "",
                "fill_stats": dict(_FILL_STATS_DEFAULT)}
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        return d
    except Exception:
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "last_scan": "",
                "fill_stats": dict(_FILL_STATS_DEFAULT)}


def _save(state: dict):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_STATE_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _log_positions(state: dict):
    """Append position snapshot to mm_positions.jsonl (post-session analysis)."""
    try:
        markets = state.get("markets", {})
        if not markets:
            return
        ts = datetime.now(_HKT).isoformat()
        for cid, m in markets.items():
            up_s = m.get("up_shares", 0)
            dn_s = m.get("down_shares", 0)
            if up_s == 0 and dn_s == 0:
                continue
            row = {"ts": ts, "cid": cid[:16], "up_s": round(up_s, 2),
                   "dn_s": round(dn_s, 2), "up_a": round(m.get("up_avg_price", 0), 4),
                   "dn_a": round(m.get("down_avg_price", m.get("dn_avg_price", 0)), 4),
                   "cost": round(m.get("entry_cost", 0), 2)}
            with open(_POS_LOG, "a") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # non-critical logging


def _to_dict(s: MMMarketState) -> dict:
    # FIX #10: base fields from dataclass
    d = {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}
    # Preserve runtime fields (pending_orders, fills_confirmed)
    # These are added by the runner, not the dataclass
    return d


def _from_dict(d: dict) -> MMMarketState:
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def _log_trade(record: dict):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRADE_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _log_order(event: str, order_id: str, cid: str, **kwargs):
    """Per-order lifecycle log: submit/fill/cancel/post_fill.

    Enables AS analysis: time_to_fill, mid_at_fill, mid_60s_post_fill.
    """
    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": event,  # submit | fill | cancel | post_fill
        "order_id": order_id[:16] if order_id else "",
        "cid": cid[:8] if cid else "",
    }
    record.update(kwargs)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_ORDER_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


# Deferred post-fill checks: list of (check_time, order_id, cid, token_id)
_post_fill_checks: list[tuple[float, str, str, str]] = []


def _get_rolling_wr(state: dict, window: int = 30) -> tuple[float, int]:
    """Rolling win rate over last N resolved markets that actually filled.

    Only counts markets with entry_cost > 0 (i.e., orders were filled).
    Unfilled markets (entry_cost=0, PnL=0) are excluded — no fill = no play.
    Returns (wr, count). If count < 5, returns (0.68, count) = assume baseline.
    """
    resolved = [m for m in state["markets"].values() if m["phase"] == "RESOLVED"]
    # Only count markets that had fills (entry_cost > 0)
    filled = [m for m in resolved if m.get("entry_cost", 0) > 0 or m.get("realized_pnl", 0) != 0]
    recent = filled[-window:] if len(filled) > window else filled
    if len(recent) < 5:
        return 0.68, len(recent)  # not enough data, assume baseline
    wins = sum(1 for m in recent if m.get("realized_pnl", 0) > 0)
    return wins / len(recent), len(recent)


def _get_risk_mode(state: dict) -> str:
    """Determine risk mode based on rolling WR.

    NORMAL (WR >= 62%):  full dual-layer (hedge + directional)
    DEFENSIVE (55-62%):  shift budget toward hedge
    HEDGE_ONLY (<55%):   no directional, pure hedge
    STOPPED (<50%):      stop trading completely
    """
    wr, count = _get_rolling_wr(state, window=30)

    if count < 10:
        return "NORMAL"  # not enough data

    # Thresholds — calibrated to W/L ratio 3.2x system
    # Breakeven WR = 1 / (1 + W/L_ratio) = 1 / 4.2 = 24%
    # STOPPED at 28% = 4pp buffer above breakeven
    if wr < 0.28:
        logger.warning("RISK MODE: STOPPED — rolling WR %.1f%% (%d trades) < 28%%", wr*100, count)
        return "STOPPED"
    elif wr < 0.30:
        logger.warning("RISK MODE: HEDGE_ONLY — rolling WR %.1f%% (%d trades) < 30%%",
                        wr*100, count)
        return "HEDGE_ONLY"
    elif wr < 0.58:
        logger.info("RISK MODE: DEFENSIVE — rolling WR %.1f%% (%d trades) < 58%%", wr*100, count)
        return "DEFENSIVE"
    else:
        return "NORMAL"


# ═══════════════════════════════════════
#  Resolution
# ═══════════════════════════════════════

def _check_resolutions(state: dict):
    now_ms = int(time.time() * 1000)
    for cid, md in list(state["markets"].items()):
        if md["phase"] == "RESOLVED":
            continue
        end_ms = md.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + 120_000:
            continue
        start_ms = md.get("window_start_ms", 0)
        if start_ms <= 0:
            continue

        dur = end_ms - start_ms
        interval = "5m" if dur <= 5*60_000 else "15m" if dur <= 15*60_000 else "1h"
        # Detect symbol from market title
        _title = md.get("title", "").lower()
        _sym = "ETHUSDT" if "ethereum" in _title else "BTCUSDT"
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={_sym}&interval={interval}&startTime={start_ms}&limit=1")
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}), timeout=10) as r:
                data = json.loads(r.read())
        except Exception:
            continue
        if not data:
            continue

        btc_o, btc_c = float(data[0][1]), float(data[0][4])
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = _from_dict(md)
        pnl = resolve_market(ms, result)
        state["markets"][cid] = _to_dict(ms)
        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] += 1

        if pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 8:
                cd = datetime.now(tz=_HKT) + timedelta(hours=24)
                state["cooldown_until"] = cd.isoformat()
                logger.warning("8 consecutive losses → COOLDOWN until %s", cd.strftime("%H:%M HKT"))
        else:
            state["consecutive_losses"] = 0

        # ── W/L Ratio Monitor (every 10 resolved, log warning if < 2.0x) ──
        _wins_list = [m.get("realized_pnl", 0) for m in state["markets"].values()
                      if m.get("phase") == "RESOLVED" and m.get("realized_pnl", 0) > 0]
        _losses_list = [abs(m.get("realized_pnl", 0)) for m in state["markets"].values()
                        if m.get("phase") == "RESOLVED" and m.get("realized_pnl", 0) < 0]
        _n_filled = len(_wins_list) + len(_losses_list)
        if _n_filled >= 5 and _n_filled % 5 == 0:  # every 5 filled trades
            _avg_win = sum(_wins_list) / len(_wins_list) if _wins_list else 0
            _avg_loss = sum(_losses_list) / len(_losses_list) if _losses_list else 1
            _wl_ratio = _avg_win / _avg_loss if _avg_loss > 0 else 999
            _wr = len(_wins_list) / _n_filled
            if _wl_ratio < 2.0:
                logger.warning("⚠️ W/L RATIO %.1fx < 2.0x THRESHOLD (WR=%.0f%%, avg_win=$%.2f, avg_loss=$%.2f, n=%d)",
                               _wl_ratio, _wr * 100, _avg_win, _avg_loss, _n_filled)
            else:
                logger.info("W/L RATIO %.1fx (WR=%.0f%%, avg_win=$%.2f, avg_loss=$%.2f, n=%d)",
                            _wl_ratio, _wr * 100, _avg_win, _avg_loss, _n_filled)

        fr_pct, _, _ = _fill_rate(state)
        _total_rounds = md.get("rounds", 0) + 1  # +1 for the final hold-to-resolution round
        _log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                     "result": result, "pnl": round(pnl, 4),
                     "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                     "total_pnl": round(state["total_pnl"], 2),
                     "fill_rate_pct": round(fr_pct, 1),
                     "rounds": _total_rounds})

        d = "↑" if result == "UP" else "↓"
        _rd_str = f" R{_total_rounds}" if _total_rounds > 1 else ""
        print(f"  RESOLVED {cid[:8]} {d}{_rd_str} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")


# ═══════════════════════════════════════
#  Main Cycle — 5s fast loop + 30s heavy ops
# ═══════════════════════════════════════

_last_heavy_ts: float = 0  # module-level for heavy operation throttle


def run_cycle(state: dict, gamma: GammaClient, client,
              config: MMConfig, dry_run: bool,
              continuous_momentum: bool = False) -> dict:
    global _last_heavy_ts
    now = datetime.now(tz=_HKT)
    now_ms = int(time.time() * 1000)
    now_s = time.time()

    # Is this a heavy cycle? (every 30s: discovery, signal pipeline, new entries)
    is_heavy = (now_s - _last_heavy_ts) >= _HEAVY_INTERVAL_S

    # Daily reset
    today = now.strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today

    # Kill switches — FIX #11: use % of bankroll, not absolute $50
    br = state.get("bankroll", 100.0)

    # HARD STOP: total PnL drops >20% of HIGH WATER MARK → permanent halt
    # Auto-updates on deposit detection (balance > previous high water mark)
    _total_pnl = state.get("total_pnl", 0.0)
    _hwm = state.get("high_water_mark", 0)
    if br > _hwm and br > 10:
        if _hwm > 0 and br > _hwm * 1.3:
            logger.info("DEPOSIT DETECTED: balance $%.2f >> HWM $%.2f → updating", br, _hwm)
        state["high_water_mark"] = br
        _hwm = br
    if _hwm <= 0:
        _hwm = br if br > 10 else 100.0
    if _total_pnl < -_hwm * 0.20:
        logger.critical("💀 HARD STOP: total PnL $%.2f = %.0f%% of HWM $%.0f. Manual restart required.",
                        _total_pnl, _total_pnl / _hwm * 100, _hwm)
        state["hard_stopped"] = True
        # Cancel all open orders before halting
        if client and hasattr(client, "get_orders") and not dry_run:
            try:
                _all_orders = client.get_orders()
                for _o in (_all_orders or []):
                    try:
                        client.client.cancel(order_id=_o.get("id", ""))
                    except Exception:
                        pass
                logger.info("HARD STOP: cancelled %d open orders", len(_all_orders or []))
            except Exception:
                pass
        return state
    if state.get("hard_stopped"):
        logger.warning("💀 HARD STOPPED. Clear 'hard_stopped' from state to resume.")
        return state

    # Daily loss: 20% of DAY-START balance (fixed, not floating)
    _day_start_key = "day_start_balance"
    if state.get("daily_pnl_date") != today:
        state[_day_start_key] = br  # capture balance at day start
    _day_start_br = state.get(_day_start_key, br)
    daily_loss_limit = _day_start_br * 0.20
    if state.get("daily_pnl", 0) < -daily_loss_limit:
        logger.warning("KILL: daily loss $%.2f > 20%% of day-start $%.0f", -state["daily_pnl"], _day_start_br)
        return state
    cd = state.get("cooldown_until", "")
    if cd:
        try:
            if now < datetime.fromisoformat(cd):
                return state
        except ValueError:
            pass
        state["consecutive_losses"] = 0
        state["cooldown_until"] = ""

    # ── FAST OPS (every 5s): price, cancel defense, fill check, resolution ──

    # Price (3s cache — always fresh)
    btc = _btc_price()
    if btc <= 0:
        return state

    # Refresh bankroll (every heavy cycle only — CLOB call)
    if is_heavy:
        vol = _vol_1m()  # 60s cache
        if client and hasattr(client, "get_usdc_balance") and not dry_run:
            try:
                bal = client.get_usdc_balance()
                if bal is not None and bal > 0:
                    state["bankroll"] = bal
            except Exception:
                pass

    # ── FAST MONITORING (every 5s): OB imbalance for active positions ──
    if not dry_run:
        for _cid, _mkt in state["markets"].items():
            if _mkt["phase"] == "OPEN" and _mkt.get("up_token_id"):
                _obi = _poly_ob_imbalance(client, _mkt["up_token_id"])
                _mid = _poly_midpoint(client, _mkt["up_token_id"])
                if abs(_obi) > 0.3 or _mid > 0:
                    logger.debug("MONITOR %s: OBI=%.2f mid=%.3f", _cid[:8], _obi, _mid)

    # ── Risk mode check (rolling WR adaptive) ──
    risk_mode = _get_risk_mode(state) if is_heavy else state.get("_risk_mode", "NORMAL")
    if is_heavy:
        state["_risk_mode"] = risk_mode
    if risk_mode == "STOPPED":
        logger.warning("STOPPED: WR < 50%% — no trading until manual review")
        return state

    # ── HEAVY OPS (every 30s): discovery, signal pipeline, new entries ──
    if is_heavy:
        _last_heavy_ts = now_s
    else:
        # Fast cycle: skip discovery + entry, go to cancel/fill/resolve
        pass

    # Discover → watchlist (gated by is_heavy via _SCAN_S check)
    last = state.get("last_scan", "")
    since = 999
    if last:
        try:
            since = (now - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            pass
    if since >= _SCAN_S:
        for mkt, winfo in _discover(gamma, config):
            cid = mkt.condition_id
            if cid not in state["markets"] and cid not in state.get("watchlist", {}):
                state.setdefault("watchlist", {})[cid] = {
                    "cid": cid, "title": mkt.title,
                    "up_tok": mkt.yes_token_id, "dn_tok": mkt.no_token_id,
                    "start_ms": winfo["start_ms"], "end_ms": winfo["end_ms"],
                    "end_time": winfo["end_time"]}
                lead = (winfo["start_ms"] - now_ms) / 60_000
                logger.info("watchlist + %s (%.0fm): %s", cid[:8], lead, mkt.title[:45])
        state["last_scan"] = now.isoformat()

    # ── Newbie protection: override bet_pct + max_concurrent for first N hours ──
    _live_start = state.get("live_start_ts", 0)
    _in_protection = (_live_start > 0
                      and time.time() - _live_start < _PROTECTION_HOURS * 3600)
    if _in_protection:
        config = _copy(config)
        config.bet_pct = _PROTECTION_BET_PCT
        config.max_concurrent_markets = _PROTECTION_MAX_MARKETS
        remaining_h = _PROTECTION_HOURS - (time.time() - _live_start) / 3600
        if is_heavy:
            logger.info("🛡️ PROTECTION: bet=%.0f%% max=%d mkt | %.1fh remaining",
                        config.bet_pct * 100, config.max_concurrent_markets, remaining_h)
    elif _live_start > 0 and is_heavy:
        # Protection just ended — log once
        if not state.get("_protection_ended_logged"):
            logger.info("🛡️ PROTECTION ENDED — switching to normal: bet=%.0f%% max=%d",
                        config.bet_pct * 100, config.max_concurrent_markets)
            state["_protection_ended_logged"] = True

    # Enter active markets from watchlist (heavy cycle only — signal pipeline is slow)
    active = sum(1 for m in state["markets"].values() if m["phase"] != "RESOLVED")
    if not is_heavy:
        active = config.max_concurrent_markets  # skip entry on fast cycles

    # FIX #1: Dedup — get all open orders on CLOB to avoid duplicate submissions
    _existing_markets = set()
    if client and hasattr(client, "get_orders") and not dry_run:
        try:
            _open = client.get_orders()
            _existing_markets = {o.get("market", "") for o in (_open or [])}
        except Exception:
            pass

    for cid, wl in list(state.get("watchlist", {}).items()):
        if cid in state["markets"]:
            del state["watchlist"][cid]
            continue
        # FIX #1: Skip if we already have orders on CLOB for this market
        if cid in _existing_markets:
            logger.info("SKIP %s: already have orders on CLOB (dedup)", cid[:8])
            del state["watchlist"][cid]
            continue
        if active >= config.max_concurrent_markets:
            break
        if now_ms < wl["start_ms"]:
            continue
        if now_ms > wl["end_ms"]:
            del state["watchlist"][cid]
            continue

        _elapsed_ms = now_ms - wl["start_ms"]

        # ── Dead Hours Gate: skip low-σ hours (σ_poly analysis: 04-06 HKT worst) ──
        _hkt_hour = datetime.now(tz=_HKT).hour
        if _hkt_hour in {4, 5}:
            continue  # stay in watchlist, check next cycle (hour changes)

        # ── Late Gate: don't enter with < 1.5 min remaining ──
        if now_ms > wl["end_ms"] - 90_000:
            logger.info("SKIP %s: < 1.5 min remaining, too late", cid[:8])
            del state["watchlist"][cid]
            continue

        # Enter — detect coin from title
        _title_lower = wl["title"].lower()
        if "ethereum" in _title_lower:
            _sym, _coin_slug = "ETHUSDT", "eth"
        elif "solana" in _title_lower:
            _sym, _coin_slug = "SOLUSDT", "sol"
        else:
            _sym, _coin_slug = "BTCUSDT", "btc"

        # Observation gate: non-live coins → log signals but skip execution
        _observe_only = _coin_slug not in _LIVE_TRADE_COINS

        # ── Momentum Filter ──
        _m1_vol = _vol_1m(_sym)
        if continuous_momentum:
            # Continuous: current price vs window open (catches late moves)
            _cm_open = _open_at(wl["start_ms"], _sym) or _price(_sym)
            _cm_now = _price(_sym)
            _cm_ret = math.log(_cm_now / _cm_open) if _cm_open > 0 and _cm_now > 0 else 0
            _cm_mins = _elapsed_ms / 60_000
            _cm_thresh = max(0.0005, _m1_vol * math.sqrt(max(1, _cm_mins)) * 0.7)
            _m1_confirmed = abs(_cm_ret) >= _cm_thresh
            _m1 = _cm_ret  # use continuous return for direction
            if not _m1_confirmed:
                if _elapsed_ms < 180_000:
                    continue
                logger.info("SKIP %s: CM weak |%.4f| < %.4f after 3min", cid[:8], _cm_ret, _cm_thresh)
                del state["watchlist"][cid]
                continue
            logger.info("CM confirmed %s: %+.4f (%.2f%%, %.1fσ) [%dmin elapsed]",
                        cid[:8], _cm_ret, _cm_ret * 100, abs(_cm_ret) / _cm_thresh,
                        int(_cm_mins))
        else:
            # M1 only: minute 0→1 return
            _m1 = _m1_return(_sym)
            _m1_thresh = max(0.0005, _m1_vol * 1.0)
            _m1_confirmed = abs(_m1) >= _m1_thresh
            if not _m1_confirmed:
                if _elapsed_ms < 180_000:
                    continue
                logger.info("SKIP %s: M1 weak |%.4f| < %.4f after 3min", cid[:8], _m1, _m1_thresh)
                del state["watchlist"][cid]
                continue
            logger.info("M1 confirmed %s: %+.4f (%.2f%%, %.1fσ)",
                        cid[:8], _m1, _m1 * 100, abs(_m1) / _m1_thresh)

        # ── Cross-exchange price validation (防 flash crash / anomaly) ──
        _xprice, _xdiv = _cross_exchange_price(_sym)
        _coin_price = _xprice if _xprice > 0 else _price(_sym)
        if _xdiv > 0.003:  # >0.3% divergence across exchanges → anomaly
            logger.warning("SKIP %s: cross-exchange divergence %.2f%% (anomaly)",
                           cid[:8], _xdiv * 100)
            continue  # keep in watchlist, re-check next cycle

        _coin_open = _open_at(wl["start_ms"], _sym) or _coin_price
        _coin_vol = _vol_1m(_sym)
        mins_left = max(1, (wl["end_ms"] - now_ms) / 60_000)

        # Signal pipeline: combine all sources for P(Up)
        mkt = PolyMarket(condition_id=cid, title=wl["title"], category="crypto_15m",
                         yes_token_id=wl["up_tok"], no_token_id=wl["dn_tok"],
                         liquidity=15000)

        # 1. Brownian Bridge (base — always available)
        bridge_p_up = compute_fair_up(_coin_price, _coin_open, _coin_vol, int(mins_left))
        # Fat-tail correction now built into compute_fair_up() via Student-t(ν=5)

        # 2. Order book imbalance (short-term, forward-looking)
        # Removed: assess_edge() — traditional indicators (RSI/MACD/BB/EMA) are
        # backward-looking, cause mean-reversion bias in trending markets.
        # Bridge + OB + cross-exchange = sufficient for 15M binary.
        ob_adjustment = 0.0
        _ob_best_bid = 0.0   # instrument: OB snapshot at submit time
        _ob_best_ask = 0.0
        _ob_bid_vol = 0.0
        _ob_ask_vol = 0.0
        _ob_depth = 0
        if client and hasattr(client, "get_order_book") and not dry_run:
            try:
                up_book = client.get_order_book(wl["up_tok"])
                bids = up_book.get("bids", [])
                asks = up_book.get("asks", [])
                bid_vol = sum(b["size"] for b in bids)
                ask_vol = sum(a["size"] for a in asks)
                _ob_bid_vol = bid_vol
                _ob_ask_vol = ask_vol
                _ob_depth = len(bids) + len(asks)
                if bids:
                    _ob_best_bid = max(b["price"] for b in bids)
                if asks:
                    _ob_best_ask = min(a["price"] for a in asks)
                if bid_vol + ask_vol > 0:
                    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                    ob_adjustment = imbalance * 0.05  # ±5% max adjustment
                    logger.info("OB imbalance=%.3f (bid=%.0f ask=%.0f) → adj=%.3f",
                                imbalance, bid_vol, ask_vol, ob_adjustment)
            except Exception as e:
                logger.debug("OB fetch failed: %s", e)

        # Fair = bridge + OB (no indicator signal)
        fair = bridge_p_up + ob_adjustment
        fair = max(0.05, min(0.95, fair))

        # CVD: taker buy ratio (3 min) — computed here for logging + gate
        _cvd = _cvd_buy_ratio(_sym, minutes=3)

        # ── Log signal data + market snapshot (log-only, no decision impact) ──
        try:
            _sig_record = {
                "ts": datetime.now(tz=_HKT).isoformat(), "cid": cid[:8],
                "coin": _coin_slug, "observe": _observe_only,
                "sym": _sym, "m1": round(_m1, 6),
                "m1_sigma": round(abs(_m1) / _m1_thresh, 2),
                "bridge": round(bridge_p_up, 4),
                "fair": round(fair, 4), "xdiv": round(_xdiv, 5),
                "ob_adj": round(ob_adjustment, 4), "cvd": round(_cvd, 3),
            }
            # Append market_data snapshot if available (log-only)
            if _mkt_fetcher is not None:
                _snap = _mkt_fetcher.latest()
                if _snap and _snap.price > 0 and _snap.age_ms < 30_000:
                    _sig_record["mkt"] = {
                        "price": round(_snap.price, 2),
                        "src": _snap.sources_responded,
                        "fund_agg": round(_snap.funding_agg, 8),
                        "fund_prem": round(_snap.funding_premium, 2),
                        "oi_total": round(_snap.oi_total / 1e9, 2),  # billions
                        "oi_d5m": round(_snap.oi_delta_5m / 1e9, 3),
                        "ls": round(_snap.ls_ratio, 3),
                        "ls_ext": _snap.ls_extreme,
                        "dvol": round(_snap.dvol, 1),
                        "taker": round(_snap.taker_buy_sell_ratio, 3),
                        "age_ms": round(_snap.age_ms),
                    }
            os.makedirs(_LOG_DIR, exist_ok=True)
            with open(_SIGNAL_LOG, "a") as _sf:
                _sf.write(json.dumps(_sig_record) + "\n")
        except Exception:
            pass

        # M1 vs fair direction conflict
        _fair_up = fair > 0.50
        _m1_up = _m1 > 0
        if abs(_m1) >= 0.001 and _fair_up != _m1_up:
            logger.info("SKIP %s: M1/fair CONFLICT (M1=%+.4f %s, fair=%.3f %s)",
                        cid[:8], _m1, "UP" if _m1_up else "DN",
                        fair, "UP" if _fair_up else "DN")
            continue  # keep in watchlist

        # CVD sizing: 3/3 agree → full, 2/3 agree → reduced
        # CVD no longer has veto power (weak signal shouldn't cancel strong bridge)
        _cvd_agrees = (_fair_up and _cvd > 0.50) or (not _fair_up and _cvd < 0.50)
        _cvd_strong_disagree = (_fair_up and _cvd < 0.45) or (not _fair_up and _cvd > 0.55)
        if _cvd_strong_disagree:
            logger.info("CVD DISAGREE %s: fair %s but CVD %.0f%% → reduced size",
                        cid[:8], "UP" if _fair_up else "DN", _cvd * 100)

        # Market midpoint sanity: if Polymarket mid for our side < $0.35,
        # market strongly disagrees with our direction → skip
        if client and hasattr(client, "get_midpoint") and not dry_run:
            _dir_tok = wl["up_tok"] if fair > 0.50 else wl["dn_tok"]
            _mid = _poly_midpoint(client, _dir_tok)
            if 0 < _mid < 0.38:
                logger.info("SKIP %s: market mid=%.3f < 0.38 → market disagrees with our direction",
                            cid[:8], _mid)
                continue  # keep in watchlist, might recover

        # Initialize whale_action before ladder uses it (whale block runs after ladder)
        if not _observe_only:
            _tte_s = (wl["end_ms"] - now_ms) / 1000
            _holder_ttl = 5 if _tte_s < 120 else _HOLDER_CACHE_TTL
            _h_imbalance, _h_delta = _holder_imbalance(cid, wl["up_tok"], ttl_override=_holder_ttl)
        else:
            _h_imbalance, _h_delta = 0.0, 0.0
        # Pre-compute whale action for checkpoint gate
        _whale_action = "NORMAL"
        _whale_favors_up = _h_imbalance > 0
        if abs(_h_imbalance) > 0.30:
            _whale_agrees = (_fair_up and _whale_favors_up) or (not _fair_up and not _whale_favors_up)
            if not _whale_agrees:
                _whale_action = "FOLLOW_LOG"

        # ── Wide Ladder DCA: 2 auto rungs + 2 conditional (checkpoint) ──
        # Backtest: 0.43/0.37/0.31/0.26, tiered TP at x1.3/1.5/1.8 → Sharpe 0.544
        # Rungs 1-2: auto-place. Rungs 3-4: only if checkpoint passes.
        _LADDER_AUTO = [0.43, 0.37]         # always place
        _LADDER_COND = [0.31, 0.26]         # place ONLY if checkpoint passes
        _LADDER_BUDGET_PCT = 0.03           # 3% of bankroll per window (conservative: hard stop recovery ~5 days)

        bankroll = state.get("bankroll", 100.0)
        n_tranches = calc_tranches(bankroll, config)
        _all_rungs = _LADDER_AUTO + _LADDER_COND
        _window_budget = bankroll * _LADDER_BUDGET_PCT / max(1, n_tranches)
        _rung_budget = _window_budget / len(_all_rungs)

        _dir_tok = wl["up_tok"] if _fair_up else wl["dn_tok"]
        _dir_side = "UP" if _fair_up else "DOWN"

        orders = []
        # Auto rungs (always place)
        for _rung_price in _LADDER_AUTO:
            _shares = max(config.min_order_size, _rung_budget / _rung_price)
            orders.append(PlannedOrder(
                token_id=_dir_tok, side="BUY",
                price=_rung_price, size=round(_shares, 1),
                outcome=_dir_side))

        # Conditional rungs 3-4: NOT placed at entry.
        # Placed live by phased_rung_check() every 5s when price approaches.
        # Store rung config in market state for the monitor to use.
        _cond_rungs_config = []
        if _whale_action not in ("FOLLOW_LOG", "EXIT"):
            for _rung_price in _LADDER_COND:
                _shares = max(config.min_order_size, _rung_budget / _rung_price)
                _cond_rungs_config.append({
                    "price": _rung_price, "size": round(_shares, 1),
                    "token_id": _dir_tok, "outcome": _dir_side, "placed": False,
                })
        else:
            logger.info("CHECKPOINT %s: deep rungs disabled (whale=%s)",
                        cid[:8], _whale_action)

        if not orders:
            del state["watchlist"][cid]
            continue

        # CVD disagree → override to single cheap rung (dynamic price)
        # 3/3 agree: keep full ladder. 2/3: reduce to 1 rung at discounted price.
        if _cvd_strong_disagree and orders:
            _our_fair = fair if _fair_up else (1.0 - fair)
            _disagree_bid = round(max(0.25, min(0.35, _our_fair * 0.60)), 3)
            _dir_tok = orders[0].token_id
            _dir_side = orders[0].outcome
            orders = [PlannedOrder(
                token_id=_dir_tok, side="BUY",
                price=_disagree_bid, size=config.min_order_size, outcome=_dir_side)]
            logger.info("CVD REDUCED %s: 1 rung @ $%.3f × %.0f (was %d orders)",
                        cid[:8], _disagree_bid, config.min_order_size, len(orders) + 1)

        # ── Whale signal: AGREE / FOLLOW_LOG / EXIT (applied to orders) ──
        # Holder data already fetched + _whale_action pre-computed above ladder block
        if abs(_h_imbalance) > 0.30:
            _whale_agrees = (_fair_up and _whale_favors_up) or (not _fair_up and not _whale_favors_up)
            if _whale_agrees:
                _whale_action = "AGREE"
                logger.info("WHALE AGREE %s: imbalance %+.3f confirms %s",
                            cid[:8], _h_imbalance, "UP" if _fair_up else "DOWN")
            elif _whale_action == "FOLLOW_LOG":
                # Already set above — apply size halving to orders
                logger.warning("WHALE FOLLOW(log) %s: imbalance %+.3f — halving orders (validation pending)",
                               cid[:8], _h_imbalance)
                if orders:
                    for o in orders:
                        o.size = max(config.min_order_size, o.size * 0.5)

        # Check delta — whale exit (rapid shift against us)
        if abs(_h_delta) > 0.15 and _whale_action == "NORMAL":
            _delta_against = (_fair_up and _h_delta < 0) or (not _fair_up and _h_delta > 0)
            if _delta_against:
                _whale_action = "EXIT"
                logger.warning("WHALE EXIT %s: imbalance Δ%+.3f AGAINST %s — halve size",
                               cid[:8], _h_delta, "UP" if _fair_up else "DOWN")
                if orders:
                    for o in orders:
                        o.size = max(config.min_order_size, o.size * 0.5)

        _sig_ctx = {"fair": round(fair, 4), "bridge": round(bridge_p_up, 4),
                    "cvd": round(_cvd, 3), "vol": round(_coin_vol, 6),
                    "m1": round(_m1, 6), "ob_adj": round(ob_adjustment, 4),
                    "btc": round(_coin_price, 2),
                    # OB snapshot at submit — answers "why didn't this fill?"
                    "ob_best_bid": round(_ob_best_bid, 4),
                    "ob_best_ask": round(_ob_best_ask, 4),
                    "ob_bid_vol": round(_ob_bid_vol, 1),
                    "ob_ask_vol": round(_ob_ask_vol, 1),
                    "ob_depth": _ob_depth,
                    "coin": _coin_slug, "observe_only": _observe_only,
                    "h_imb": _h_imbalance, "h_delta": _h_delta,
                    "whale": _whale_action}

        # Observation gate: log everything but don't place orders for non-live coins
        if _observe_only:
            logger.info("OBSERVE %s %s: fair=%.3f bridge=%.3f cvd=%.3f (no execution — %s not in _LIVE_TRADE_COINS)",
                        cid[:8], _coin_slug.upper(), fair, bridge_p_up, _cvd, _coin_slug)
            del state["watchlist"][cid]
            continue

        results = _execute(orders, client, cid=cid, signal_ctx=_sig_ctx)
        ms = MMMarketState(condition_id=cid, title=wl["title"],
                           up_token_id=wl["up_tok"], down_token_id=wl["dn_tok"],
                           window_start_ms=wl["start_ms"], window_end_ms=wl["end_ms"],
                           btc_open_price=_coin_open, phase="OPEN")

        # Use API response status to determine immediate fills
        pending = []
        for r in results:
            if not r.get("submitted"):
                continue
            _bump_fill(state, "submitted")
            status = r.get("status", "")
            if status == "matched":
                apply_fill(ms, r["outcome"], "BUY", r["price"], r["size"])
                _bump_fill(state, "filled")
                logger.info("INSTANT FILL %s %s: %.1f @ $%.3f",
                            cid[:8], r["outcome"], r["size"], r["price"])
            else:
                pending.append(r)

        mkt_dict = _to_dict(ms)
        mkt_dict["pending_orders"] = pending
        mkt_dict["fills_confirmed"] = len(pending) == 0
        mkt_dict["entry_price"] = _coin_price
        mkt_dict["entry_ts"] = int(time.time())
        mkt_dict["tranches_done"] = 1
        mkt_dict["tranches_total"] = n_tranches
        # FOLLOW_LOG = log only, bridge direction kept. No flip.
        mkt_dict["original_dir"] = "UP" if fair > 0.50 else "DOWN"
        if _whale_action == "FOLLOW_LOG":
            mkt_dict["whale_disagree"] = True  # track for offline analysis
        mkt_dict["rounds"] = 0  # scalp round counter (0 = first entry, no sells yet)
        mkt_dict["phased_rungs"] = _cond_rungs_config  # rung 3-4 config for live placement
        state["markets"][cid] = mkt_dict
        del state["watchlist"][cid]
        active += 1

        t_str = f" T1/{n_tranches}" if n_tranches > 1 else ""
        filled_str = f"UP={ms.up_shares:.0f} DN={ms.down_shares:.0f}"
        pending_str = ",".join(r["outcome"] for r in pending)
        cost_str = f"${ms.entry_cost:.2f}" if ms.entry_cost > 0 else "$0"
        print(f"  OPEN {cid[:8]} | {filled_str} | pend: {pending_str or '-'} | {cost_str}{t_str}")

    # ── Phased entry: add tranches to existing markets ──
    if is_heavy:
        for cid, mkt_d in list(state["markets"].items()):
            if mkt_d["phase"] != "OPEN":
                continue
            t_done = mkt_d.get("tranches_done", 1)
            t_total = mkt_d.get("tranches_total", 1)
            if t_done >= t_total:
                continue
            # Check timing: at least tranche_interval since entry
            entry_ts = mkt_d.get("entry_ts", 0)
            if time.time() - entry_ts < config.tranche_interval_s * t_done:
                continue
            # Check: enough time left (>3 min before window end)
            end_ms = mkt_d.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 180_000:
                continue

            # Re-evaluate fair price for this tranche
            _t2 = mkt_d.get("title", "").lower()
            _s2 = "ETHUSDT" if "ethereum" in _t2 else "BTCUSDT"
            _p2 = _price(_s2)
            _o2 = mkt_d.get("btc_open_price", _p2)
            _v2 = _vol_1m(_s2)
            _ml2 = max(1, (end_ms - now_ms) / 60_000)
            fair2 = compute_fair_up(_p2, _o2, _v2, int(_ml2))

            # Market midpoint sanity for tranches
            if client and hasattr(client, "get_midpoint") and not dry_run:
                orig_dir_tok = mkt_d.get("up_token_id", "") if mkt_d.get("original_dir") == "UP" else mkt_d.get("down_token_id", "")
                _t_mid = _poly_midpoint(client, orig_dir_tok) if orig_dir_tok else 0
                if 0 < _t_mid < 0.38:
                    logger.info("ABORT tranche %s: market mid=%.3f < 0.38 → market says we're wrong",
                                cid[:8], _t_mid)
                    mkt_d["tranches_done"] = t_total
                    continue

            # Keep original direction — only abort if REVERSED
            orig_dir = mkt_d.get("original_dir", "UP")
            if orig_dir == "UP" and fair2 < 0.45:
                logger.info("ABORT tranche %s: direction REVERSED (fair=%.3f, was UP)",
                            cid[:8], fair2)
                mkt_d["tranches_done"] = t_total
                continue
            elif orig_dir == "DOWN" and fair2 > 0.55:
                logger.info("ABORT tranche %s: direction REVERSED (fair=%.3f, was DOWN)",
                            cid[:8], fair2)
                mkt_d["tranches_done"] = t_total
                continue

            # Use original direction with current price (buy the dip)
            # Override fair to force original direction
            if orig_dir == "UP" and fair2 < 0.50:
                fair2 = max(fair2, 0.50 + 0.001)  # nudge to keep UP direction
            elif orig_dir == "DOWN" and fair2 > 0.50:
                fair2 = min(fair2, 0.50 - 0.001)

            mkt2 = PolyMarket(
                condition_id=cid, title=mkt_d.get("title", ""),
                category="crypto_15m",
                yes_token_id=mkt_d.get("up_token_id", ""),
                no_token_id=mkt_d.get("down_token_id", ""),
                liquidity=15000)
            bankroll2 = state.get("bankroll", 100.0)
            orders2 = plan_opening(mkt2, fair2, config, bankroll=bankroll2,
                                   tranche=t_done, total_tranches=t_total)
            if not orders2:
                mkt_d["tranches_done"] = t_total
                continue

            results2 = _execute(orders2, client, cid=cid,
                                signal_ctx={"fair": round(fair2, 4), "tranche": t_done + 1})
            for r in results2:
                if not r.get("submitted"):
                    continue
                _bump_fill(state, "submitted")
                status = r.get("status", "")
                if status == "matched":
                    _bump_fill(state, "filled")
                    outcome = r["outcome"]
                    price = r["price"]
                    size = r["size"]
                    if outcome == "UP":
                        old = mkt_d["up_shares"] * mkt_d["up_avg_price"]
                        mkt_d["up_shares"] += size
                        mkt_d["up_avg_price"] = (old + size * price) / mkt_d["up_shares"] if mkt_d["up_shares"] > 0 else 0
                    elif outcome == "DOWN":
                        old = mkt_d["down_shares"] * mkt_d["down_avg_price"]
                        mkt_d["down_shares"] += size
                        mkt_d["down_avg_price"] = (old + size * price) / mkt_d["down_shares"] if mkt_d["down_shares"] > 0 else 0
                    mkt_d["entry_cost"] += size * price
                else:
                    mkt_d.setdefault("pending_orders", []).append(r)
                    mkt_d["fills_confirmed"] = False

            mkt_d["tranches_done"] = t_done + 1
            logger.info("TRANCHE %d/%d %s | cost=$%.2f",
                        t_done + 1, t_total, cid[:8], mkt_d["entry_cost"])

    # ── Helper: identify directional vs hedge orders ──
    def _find_directional_orders(pending_list):
        """Find orders that are directional (not part of equal-size hedge pair).
        Hedge pair = one UP + one DN with same size. Extra orders = directional."""
        up_orders = [p for p in pending_list if p["outcome"] == "UP"]
        dn_orders = [p for p in pending_list if p["outcome"] == "DOWN"]
        # Find hedge pairs (matching size)
        hedge_up_ids = set()
        hedge_dn_ids = set()
        for u in up_orders:
            for d in dn_orders:
                if abs(u["size"] - d["size"]) < 0.1 and id(u) not in hedge_up_ids and id(d) not in hedge_dn_ids:
                    hedge_up_ids.add(id(u))
                    hedge_dn_ids.add(id(d))
                    break
        # Everything not in a hedge pair = directional
        return [p for p in pending_list
                if id(p) not in hedge_up_ids and id(p) not in hedge_dn_ids]

    # ── Phased rung placement: rungs 3-4 placed LIVE with 3-cycle cooldown ──
    # When mid approaches rung price: wait 3 cycles (15s), observe market reaction,
    # then run 3 checks. If ALL pass → place. If ANY fail → block permanently.
    _PHASED_COOLDOWN_CYCLES = 3  # observe for 15s (3 × 5s) before deciding
    if client and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            _phased = mkt.get("phased_rungs", [])
            if not _phased:
                continue
            # No _has_fills gate: allow rung 3-4 even if 1-2 missed
            # (price may skip through 0.43/0.37 on flash drop → 0.31 is still valid entry)
            # 3-cycle cooldown + checkpoint provides sufficient safety
            _end_ms = mkt.get("window_end_ms", 0)
            if _end_ms > 0 and now_ms > _end_ms - 120_000:
                continue

            _t = mkt.get("title", "").lower()
            _sym_pr = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"

            for _pr in _phased:
                if _pr.get("placed") or _pr.get("blocked"):
                    continue
                _tok_id = _pr["token_id"]
                _mid = _poly_midpoint(client, _tok_id) if hasattr(client, "get_midpoint") else 0
                if _mid <= 0 or _mid > _pr["price"] + 0.05:
                    _pr.pop("_approach_count", None)  # reset if mid moved away
                    continue

                # ── Cooldown: count cycles since mid approached ──
                _pr["_approach_count"] = _pr.get("_approach_count", 0) + 1
                if _pr["_approach_count"] < _PHASED_COOLDOWN_CYCLES:
                    if _pr["_approach_count"] == 1:
                        logger.info("PHASED OBSERVE %s $%.2f: mid=%.3f approaching, waiting %d cycles...",
                                    cid[:8], _pr["price"], _mid, _PHASED_COOLDOWN_CYCLES)
                    continue  # keep observing

                # ═══ LIVE CHECKPOINT (after cooldown) ═══
                _pass = True
                _reasons = []
                _our_dir = mkt.get("original_dir", "UP")

                # Check 1: Whale not against us
                _h_imb, _ = _holder_imbalance(cid, mkt.get("up_token_id", ""), ttl_override=5)
                _whale_up = _h_imb > 0
                _whale_against = (_our_dir == "UP" and not _whale_up and abs(_h_imb) > 0.20) or \
                                 (_our_dir == "DOWN" and _whale_up and abs(_h_imb) > 0.20)
                if _whale_against:
                    _pass = False
                    _reasons.append(f"whale({_h_imb:+.2f})")

                # Check 2: BTC adverse move < 0.3% since entry
                _entry_px = mkt.get("entry_price", 0)
                _now_px = _price(_sym_pr)
                if _entry_px > 0 and _now_px > 0:
                    _move = (_now_px - _entry_px) / _entry_px
                    _is_adverse = (_our_dir == "UP" and _move < -0.003) or \
                                  (_our_dir == "DOWN" and _move > 0.003)
                    if _is_adverse:
                        _pass = False
                        _reasons.append(f"adverse({_move:+.3%})")

                # Check 3: Mid recovering? (mid > rung price = market bouncing back)
                if _mid <= _pr["price"]:
                    _pass = False
                    _reasons.append(f"mid({_mid:.3f})<rung({_pr['price']:.2f})")

                if _pass:
                    try:
                        _results = _execute(
                            [PlannedOrder(token_id=_tok_id, side="BUY",
                                          price=_pr["price"], size=_pr["size"],
                                          outcome=_pr["outcome"])],
                            client, cid=cid)
                        _pr["placed"] = True
                        # Add to pending_orders for cancel defense visibility
                        for _r in (_results or []):
                            if _r.get("submitted"):
                                mkt.setdefault("pending_orders", []).append({
                                    "order_id": _r.get("order_id", ""),
                                    "outcome": _pr["outcome"],
                                    "price": _pr["price"], "size": _pr["size"],
                                })
                        logger.info("PHASED RUNG %s: $%.2f × %.1f placed (mid=%.3f, 3 checks passed after %d cycles)",
                                    cid[:8], _pr["price"], _pr["size"], _mid, _pr["_approach_count"])
                    except Exception as e:
                        logger.warning("Phased rung failed %s: %s", cid[:8], e)
                else:
                    _pr["blocked"] = True
                    logger.info("PHASED BLOCKED %s $%.2f: %s (after %d cycles observation)",
                                cid[:8], _pr["price"], " + ".join(_reasons), _pr["_approach_count"])
                break

    # Cancel defense: 3 triggers for unfilled orders
    if client and hasattr(client, "client") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            pending = mkt.get("pending_orders", [])
            if not pending:
                continue

            end_ms = mkt.get("window_end_ms", 0)
            entry_price = mkt.get("entry_price", 0)
            entry_ts = mkt.get("entry_ts", 0)
            now_s = int(time.time())

            # Detect coin symbol for spot price check
            _t = mkt.get("title", "").lower()
            _s = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"

            to_cancel = []
            reason = ""

            # Trigger 1: 2 min before window end → cancel ALL pending
            if end_ms > 0 and now_ms > end_ms - 120_000:
                to_cancel = list(pending)
                reason = "window_end"

            # Trigger 2: spot moved ADVERSELY >0.5% since entry → cancel DIRECTIONAL
            # 0.05% was too tight — BTC moves $42 in seconds, cancelled 10/11 orders
            # 0.3% also too tight — cancelled 6/6 orders in v14 live
            # 0.5% BTC (~$350), 0.7% ETH (~$14) — generous to let orders sit
            # Only cancel on ADVERSE move (against our direction), not favorable
            _spot_thresh = 0.007 if _s == "ETHUSDT" else 0.005
            if not to_cancel and entry_price > 0:
                current = _btc_price() if _s == "BTCUSDT" else _price(_s)
                if current > 0:
                    signed_move = (current - entry_price) / entry_price
                    _dir = mkt.get("original_dir", "UP")
                    # Adverse = price went opposite to our bet direction
                    is_adverse = (signed_move < 0 and _dir == "UP") or (signed_move > 0 and _dir == "DOWN")
                    if is_adverse and abs(signed_move) > _spot_thresh:
                        to_cancel = _find_directional_orders(pending)
                        if to_cancel:
                            reason = f"adverse_move_{signed_move:+.4f}"

            # Trigger 3: Dynamic TTL → cancel DIRECTIONAL only
            # Fixed 5min was too short — v14 cancelled 6/6 orders (0% fill).
            # Dynamic TTL: window_end - 2min - entry_ts (no 600s cap)
            # BMD finding: 600s cap killed 43pp of fill rate (27/27 cancels were TTL).
            # Now: order lives until 2 min before window end (matches T1 cancel).
            if not to_cancel and entry_ts > 0 and end_ms > 0:
                _hard_cancel_s = (end_ms - 120_000) / 1000  # 2 min before window end (was 3)
                _max_ttl_s = max(60, _hard_cancel_s - entry_ts)  # no cap — full window
                _time_on_book = now_s - entry_ts
                # Log when order exceeds old 600s cap (diagnostic — n=35 not enough to validate removal)
                if _time_on_book > 600 and not mkt.get("_ttl_extended_logged"):
                    logger.info("TTL_EXTENDED %s: on book %ds (old cap would cancel at 600s, now max=%ds)",
                                cid[:8], int(_time_on_book), int(_max_ttl_s))
                    mkt["_ttl_extended_logged"] = True
                if _time_on_book > _max_ttl_s:
                    to_cancel = _find_directional_orders(pending)
                    if to_cancel:
                        reason = f"ttl_{int(_time_on_book)}s_max{int(_max_ttl_s)}s"

            actually_cancelled = []
            _time_on_book = now_s - entry_ts if entry_ts > 0 else 0
            _dist_to_end_s = (end_ms / 1000 - now_s) if end_ms > 0 else 0
            for po in to_cancel:
                oid = po.get("order_id", "")
                if oid:
                    try:
                        _cancel_t0 = time.time()
                        client.client.cancel(order_id=oid)
                        _cancel_rtt_ms = round((time.time() - _cancel_t0) * 1000, 1)
                        logger.info("CANCEL %s %s [%s] book=%ds end=%ds rtt=%dms",
                                    cid[:8], po["outcome"], reason,
                                    _time_on_book, _dist_to_end_s, _cancel_rtt_ms)
                        _log_order("cancel", oid, cid,
                                   outcome=po.get("outcome", ""), reason=reason,
                                   time_on_book_s=_time_on_book,
                                   dist_to_end_s=int(_dist_to_end_s),
                                   cancel_rtt_ms=_cancel_rtt_ms)
                        actually_cancelled.append(po)
                    except Exception as e:
                        logger.warning("Cancel FAILED %s %s: %s — keeping in pending",
                                       cid[:8], po["outcome"], e)

            # Only remove successfully cancelled orders from pending
            if actually_cancelled:
                _bump_fill(state, "cancelled", len(actually_cancelled))
                mkt["pending_orders"] = [p for p in pending if p not in actually_cancelled]
                if not mkt["pending_orders"]:
                    mkt["fills_confirmed"] = True

    # Check fills (submitted → actually filled?)
    if not dry_run:
        _check_fills(state, client)

    # ── Post-fill AS measurement: check midpoint 60s after fill ──
    if _post_fill_checks and client and hasattr(client, "get_midpoint"):
        _now = time.time()
        _remaining = []
        for _pf_time, _pf_oid, _pf_cid, _pf_tok in _post_fill_checks:
            if _now >= _pf_time:
                _pf_mid = _poly_midpoint(client, _pf_tok)
                _log_order("post_fill_60s", _pf_oid, _pf_cid,
                           mid_60s=round(_pf_mid, 4) if _pf_mid else 0)
            else:
                _remaining.append((_pf_time, _pf_oid, _pf_cid, _pf_tok))
        _post_fill_checks.clear()
        _post_fill_checks.extend(_remaining)

    # ── Exit: Profit Lock + Cost Recovery + Stop Loss ──
    # Layer 1: PROFIT LOCK (mid ≥ 96¢) → sell 96%, keep 4% free roll + 2-share hedge
    # Layer 2: COST RECOVERY (mid ≥ 64¢, early) → sell enough to recover cost → free roll
    # Layer 3: STOP LOSS (-25%, pre-recovery only) → cut losses
    # Layer 4: HOLD → default (free shares or waiting)
    _EXIT_STOP_PCT = 0.25       # -25% → stop loss (pre-recovery only)
    _BLACK_SWAN_MID = 0.96      # sell 96% at 96¢+ → lock profit, keep 4% free roll
    _BLACK_SWAN_SELL_PCT = 0.96 # sell 96%, keep 4% as free upside
    _COST_RECOVERY_MID = 0.64   # recover cost when mid ≥ 64¢ (keep 3 free shares vs 2 at 55¢)
    if client and hasattr(client, "sell_shares") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            # Stop loss can fire even with pending rungs (wide ladder defense)
            # Other exits (profit lock, cost recovery) still need fills_confirmed
            _has_any_fill = (mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0)
            if not mkt.get("fills_confirmed") and not _has_any_fill:
                continue
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 300_000:
                continue  # last 5 min, can't sell (market rejects at ~4 min)

            _cost_recovered = mkt.get("cost_recovered", False)

            for side, tok_key, shares_key, avg_key in [
                ("UP", "up_token_id", "up_shares", "up_avg_price"),
                ("DOWN", "down_token_id", "down_shares", "down_avg_price"),
            ]:
                shares = mkt.get(shares_key, 0)
                avg = mkt.get(avg_key, 0)
                tok = mkt.get(tok_key, "")
                if shares < 1 or avg <= 0 or not tok:
                    continue
                mid = _poly_midpoint(client, tok)
                if mid <= 0:
                    continue

                # ── Layer 0.5: TIERED PARTIAL TP (Sharpe-optimal) ──
                # Sell portions as mid rises: lock profit progressively
                # Backtest: tiered 30/50/80 → Sharpe 0.544 (29x vs HOLD)
                _PARTIAL_TP_TIERS = [
                    (1.3, 0.14),   # mid ≥ entry×1.3 → sell 14% (keep max upside)
                    (1.5, 0.48),   # mid ≥ entry×1.5 → sell 48% (lock biggest chunk)
                    (1.8, 0.33),   # mid ≥ entry×1.8 → sell 33% + free roll hedge
                ]
                _tp_key = f"_tp_tier_{side}"
                _tp_done = mkt.get(_tp_key, 0)  # how many tiers already executed
                if _tp_done < len(_PARTIAL_TP_TIERS) and not _cost_recovered:
                    _mult, _sell_pct = _PARTIAL_TP_TIERS[_tp_done]
                    _tp_target = avg * _mult
                    if mid >= _tp_target:
                        _tp_sell = max(1, int(shares * _sell_pct))
                        try:
                            _tp_price = round(max(0.01, mid * 0.97), 2)
                            client.sell_shares(tok, _tp_sell, price=_tp_price)
                            _tp_pnl = _tp_sell * (_tp_price - avg)
                            mkt[shares_key] = shares - _tp_sell
                            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _tp_sell * avg)
                            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _tp_pnl
                            mkt[_tp_key] = _tp_done + 1
                            logger.info("PARTIAL TP T%d %s %s: sell %d/%d @ $%.3f (target $%.3f, x%.1f) pnl=$%.2f",
                                        _tp_done + 1, cid[:8], side, _tp_sell, int(shares),
                                        _tp_price, _tp_target, _mult, _tp_pnl)
                            # Free roll hedge: only on LAST tier (T3)
                            if _tp_done + 1 == len(_PARTIAL_TP_TIERS):
                                _tte_exit = (end_ms - now_ms) / 1000 if end_ms > 0 else 999
                                if _tte_exit > 60:
                                    _opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                                    if _opp_tok:
                                        _fr_budget = _tp_sell * _tp_price * 0.05  # 5% of T3 sold value
                                        _opp_mid = _poly_midpoint(client, _opp_tok)
                                        _fr_price = round(max(0.01, (_opp_mid if _opp_mid > 0 else 0.10) * 2.0), 2)
                                        _fr_price = min(_fr_price, 0.15)
                                        if _fr_budget >= _fr_price:
                                            try:
                                                client.buy_shares(_opp_tok, round(_fr_budget, 2), price=_fr_price)
                                                logger.info("FREE ROLL %s: buy opp @ $%.2f ($%.2f) — %ds left",
                                                            cid[:8], _fr_price, _fr_budget, int(_tte_exit))
                                            except Exception as _fre:
                                                logger.debug("Free roll buy failed: %s", _fre)
                        except Exception as e:
                            logger.warning("Partial TP failed %s: %s", cid[:8], e)
                        continue  # re-check next cycle for next tier

                # ── Layer 1: PROFIT LOCK (96¢+) → sell 96%, keep 4% free roll + hedge ──
                if mid >= _BLACK_SWAN_MID:
                    # Sell 96% to lock profit, keep 4% as free roll ($0 risk)
                    _sell_shares = max(1, int(shares * _BLACK_SWAN_SELL_PCT))
                    _keep = shares - _sell_shares
                    try:
                        # Aggressive taker: hit best bid (mid × 0.97) to guarantee fill
                        # Last 2-3s bots can move price — speed > price
                        _sell_price = round(max(0.01, mid * 0.97), 2)
                        client.sell_shares(tok, _sell_shares, price=_sell_price)
                        _pnl = _sell_shares * (_sell_price - avg)
                        _remaining_cost = _keep * avg
                        logger.info("PROFIT LOCK %s %s: sell %d/%d @ $%.2f | pnl=$%.2f | keep %d free (cost=$%.2f covered)",
                                    cid[:8], side, _sell_shares, int(shares), _sell_price,
                                    _pnl, int(_keep), _remaining_cost)
                        mkt[shares_key] = _keep
                        # FIX: reduce entry_cost so resolve_market PnL is correct
                        _sold_cost = _sell_shares * avg
                        mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
                        mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _pnl
                        mkt["cost_recovered"] = True  # remaining shares = free roll
                        # Don't set RESOLVED — keep shares alive for resolution payout
                    except Exception as e:
                        logger.warning("Profit lock sell failed %s: %s", cid[:8], e)
                        continue
                    # Mini hedge: buy 2 shares opposite at market (insurance, not sizing)
                    # At 95¢ our side, opposite ≈ 5¢. Cost = 2 × 0.10 = $0.20 max.
                    # If we're wrong: 2 × $1 = $2 recovery. 100 trades: 5 wrong × $2 = $10.
                    _HEDGE_SHARES = 2
                    _opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                    _opp_side = "DOWN" if side == "UP" else "UP"
                    if _opp_tok:
                        _opp_mid = _poly_midpoint(client, _opp_tok)
                        # Aggressive taker: 2x mid to guarantee instant fill
                        _hedge_price = round(max(0.01, (_opp_mid if _opp_mid > 0 else 0.06) * 2.0), 2)
                        _hedge_price = min(_hedge_price, 0.15)  # cap at 15¢
                        try:
                            _hedge_cost = round(_HEDGE_SHARES * _hedge_price, 2)
                            client.buy_shares(_opp_tok, _hedge_cost, price=_hedge_price)
                            logger.info("HEDGE %s %s: %d shares @ $%.2f ($%.2f)",
                                        cid[:8], _opp_side, _HEDGE_SHARES, _hedge_price, _hedge_cost)
                        except Exception as e:
                            logger.warning("HEDGE FAILED %s: %s", cid[:8], e)
                    break  # exit inner for-side loop — market is RESOLVED

                if _cost_recovered:
                    # ── Post recovery: FREE ROLL — just hold, $0 risk ──
                    continue

                # ── Layer 2: COST RECOVERY (mid ≥ 55¢) ──
                if mid >= _COST_RECOVERY_MID:
                    _original_cost = mkt.get("entry_cost", shares * avg)
                    if _original_cost <= 0:
                        continue
                    _sell_price = round(max(0.01, mid * 0.98), 2)
                    _shares_to_sell = min(shares - 1, math.ceil(_original_cost / _sell_price))
                    if _shares_to_sell < 1:
                        continue
                    try:
                        client.sell_shares(tok, _shares_to_sell, price=_sell_price)
                        _recovered = _shares_to_sell * _sell_price
                        _remaining = shares - _shares_to_sell
                        logger.info("COST RECOVERY %s %s: sell %.0f/%.0f @ %.3f = $%.2f recovered | %.1f free shares",
                                    cid[:8], side, _shares_to_sell, shares, _sell_price,
                                    _recovered, _remaining)
                        mkt[shares_key] = _remaining
                        # FIX: reduce entry_cost so resolve_market PnL is correct
                        _sold_cost = _shares_to_sell * avg
                        mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
                        mkt["cost_recovered"] = True
                        mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + (_recovered - _original_cost)
                    except Exception as e:
                        logger.warning("Cost recovery sell failed %s: %s", cid[:8], e)
                    continue

                # ── Layer 3: STOP LOSS (pre-recovery, -25%) ──
                pnl_pct = (mid - avg) / avg
                if pnl_pct < -_EXIT_STOP_PCT:
                    try:
                        _sell_price = round(max(0.01, mid * 0.97), 2)
                        client.sell_shares(tok, shares, price=_sell_price)
                        _round_pnl = shares * (_sell_price - avg)
                        mkt[shares_key] = 0
                        mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _round_pnl
                        mkt["rounds"] = mkt.get("rounds", 0) + 1
                        mkt["last_sell_ts"] = int(time.time())
                        _rd = mkt["rounds"]
                        logger.info("STOP LOSS R%d %s %s: sell %.1f @ %.3f (entry %.3f, %.0f%%) pnl=$%.2f",
                                    _rd, cid[:8], side, shares, mid, avg, pnl_pct * 100, _round_pnl)
                        # Cancel remaining unfilled rungs (prevent DCA into losing position)
                        try:
                            _open_orders = client.get_orders(market=cid) if hasattr(client, "get_orders") else []
                            for _oo in (_open_orders or []):
                                _oid = _oo.get("id", "")
                                if _oid:
                                    client.client.cancel(order_id=_oid)
                            if _open_orders:
                                logger.info("SL CANCEL %s: cancelled %d remaining orders after stop loss",
                                            cid[:8], len(_open_orders))
                        except Exception as _ce:
                            logger.warning("SL cancel remaining failed %s: %s", cid[:8], _ce)
                        # Clear phased rungs to prevent DCA into stopped-out position
                        mkt["phased_rungs"] = []
                        mkt["pending_orders"] = []
                        if _rd >= _MAX_ROUNDS:
                            mkt["phase"] = "RESOLVED"
                            mkt["early_exit"] = "stop_loss"
                    except Exception as e:
                        logger.warning("Stop loss failed %s %s: %s", cid[:8], side, e)

    # ── Re-entry: scalp again in same window after early exit ──
    if is_heavy and client and not dry_run:
        for cid, mkt in list(state["markets"].items()):
            if mkt["phase"] != "OPEN":
                continue
            _rd = mkt.get("rounds", 0)
            if _rd < 1 or _rd >= _MAX_ROUNDS:
                continue  # no sell yet, or max rounds reached
            # Must be sold out (both sides zero)
            if mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0:
                continue
            # Cooldown after last sell
            _last_sell = mkt.get("last_sell_ts", 0)
            if time.time() - _last_sell < _REENTRY_COOLDOWN_S:
                continue
            # Enough time left in window (>4 min)
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 90_000:
                logger.info("REENTRY SKIP %s R%d: < 1.5 min remaining", cid[:8], _rd + 1)
                mkt["phase"] = "RESOLVED"
                mkt["early_exit"] = f"window_end_r{_rd}"
                continue

            # Re-run M1 + signal pipeline for fresh direction
            _title_lower = mkt.get("title", "").lower()
            _sym = "ETHUSDT" if "ethereum" in _title_lower else "BTCUSDT"
            _m1 = _m1_return(_sym)
            _m1_vol = _vol_1m(_sym)
            _m1_thresh = max(0.0005, _m1_vol * 1.0)
            if abs(_m1) < _m1_thresh:
                logger.debug("REENTRY WAIT %s R%d: M1 weak |%.4f| < %.4f",
                             cid[:8], _rd + 1, _m1, _m1_thresh)
                continue  # keep waiting, re-check next heavy cycle

            # Cross-exchange validation
            _xprice, _xdiv = _cross_exchange_price(_sym)
            _coin_price = _xprice if _xprice > 0 else _price(_sym)
            if _xdiv > 0.003:
                logger.debug("REENTRY WAIT %s R%d: cross-exchange divergence %.2f%%",
                             cid[:8], _rd + 1, _xdiv * 100)
                continue

            # BTC move since window open > 0.3% → skip re-entry (regime change)
            _coin_open = mkt.get("btc_open_price") or _coin_price
            if _coin_open > 0:
                _window_move = abs(_coin_price - _coin_open) / _coin_open
                if _window_move > 0.003:
                    logger.info("REENTRY SKIP %s R%d: window move %.2f%% > 0.3%% (regime change)",
                                cid[:8], _rd + 1, _window_move * 100)
                    mkt["phase"] = "RESOLVED"
                    mkt["early_exit"] = f"regime_change_r{_rd}"
                    continue

            start_ms = mkt.get("window_start_ms", 0)
            mins_left = max(1, (end_ms - now_ms) / 60_000)

            # Bridge + OB (no indicator signal — same as initial entry)
            bridge_p_up = compute_fair_up(_coin_price, _coin_open, _m1_vol, int(mins_left))
            # Fat-tail correction built into compute_fair_up() via Student-t(ν=5)

            ob_adjustment = 0.0
            if hasattr(client, "get_order_book"):
                try:
                    up_book = client.get_order_book(mkt.get("up_token_id", ""))
                    bid_vol = sum(b["size"] for b in up_book.get("bids", []))
                    ask_vol = sum(a["size"] for a in up_book.get("asks", []))
                    if bid_vol + ask_vol > 0:
                        imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                        ob_adjustment = imbalance * 0.05
                except Exception:
                    pass

            fair = bridge_p_up + ob_adjustment
            fair = max(0.05, min(0.95, fair))

            # M1 vs fair direction conflict
            _fair_up = fair > 0.50
            _m1_up = _m1 > 0
            if abs(_m1) >= 0.001 and _fair_up != _m1_up:
                logger.info("REENTRY SKIP %s R%d: M1/fair conflict", cid[:8], _rd + 1)
                continue

            # Market mid sanity
            if hasattr(client, "get_midpoint"):
                _dir_tok = mkt.get("up_token_id", "") if fair > 0.50 else mkt.get("down_token_id", "")
                _mid = _poly_midpoint(client, _dir_tok)
                if 0 < _mid < 0.38:
                    logger.info("REENTRY SKIP %s R%d: market mid=%.3f < 0.38",
                                cid[:8], _rd + 1, _mid)
                    continue

            # Place re-entry order — round-dependent pricing discount
            # R2: bid × 0.90 (10% cheaper), R3: bid × 0.80 (20% cheaper)
            # Rationale: stop loss already triggered → regime may have changed → demand better price
            _round_discount = {1: 0.90, 2: 0.80}.get(_rd, 0.80)
            _re_config = _copy(config)
            _re_config.max_directional_bid = round(config.max_directional_bid * _round_discount, 3)
            _re_config.max_hedge_bid = round(config.max_hedge_bid * _round_discount, 3)
            logger.info("REENTRY R%d %s: bid cap $%.3f (%.0f%% of R1 $%.3f)",
                        _rd + 1, cid[:8], _re_config.max_directional_bid,
                        _round_discount * 100, config.max_directional_bid)
            _re_mkt = PolyMarket(
                condition_id=cid, title=mkt.get("title", ""),
                category="crypto_15m",
                yes_token_id=mkt.get("up_token_id", ""),
                no_token_id=mkt.get("down_token_id", ""),
                liquidity=15000)
            bankroll = state.get("bankroll", 100.0)
            n_tranches = calc_tranches(bankroll, _re_config)
            orders = plan_opening(_re_mkt, fair, _re_config, bankroll=bankroll,
                                  tranche=0, total_tranches=n_tranches,
                                  risk_mode=risk_mode)
            if not orders:
                continue

            results = _execute(orders, client, cid=cid,
                               signal_ctx={"fair": round(fair, 4), "round": _rd + 1,
                                           "bridge": round(bridge_p_up, 4)})
            # Reset entry fields for new round
            mkt["phased_rungs"] = []  # clear stale rungs from previous round
            mkt["entry_price"] = _coin_price
            mkt["entry_ts"] = int(time.time())
            mkt["up_avg_price"] = 0
            mkt["down_avg_price"] = 0
            mkt["entry_cost"] = 0
            mkt["fills_confirmed"] = True
            mkt["original_dir"] = "UP" if fair > 0.50 else "DOWN"
            mkt["tranches_done"] = 1
            mkt["tranches_total"] = n_tranches
            pending = []
            for r in results:
                if not r.get("submitted"):
                    continue
                _bump_fill(state, "submitted")
                status = r.get("status", "")
                if status == "matched":
                    outcome = r["outcome"]
                    price = r["price"]
                    size = r["size"]
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * price) / mkt["up_shares"] if mkt["up_shares"] > 0 else 0
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * price) / mkt["down_shares"] if mkt["down_shares"] > 0 else 0
                    mkt["entry_cost"] += size * price
                    _bump_fill(state, "filled")
                    logger.info("REENTRY FILL R%d %s %s: %.1f @ $%.3f",
                                _rd + 1, cid[:8], outcome, size, price)
                else:
                    pending.append(r)
            if pending:
                mkt["pending_orders"] = pending
                mkt["fills_confirmed"] = False

            _new_dir = "UP" if fair > 0.50 else "DOWN"
            logger.info("REENTRY R%d %s dir=%s fair=%.3f (prev_dir=%s)",
                        _rd + 1, cid[:8], _new_dir, fair,
                        mkt.get("_prev_dir", mkt.get("original_dir", "?")))
            mkt["_prev_dir"] = _new_dir

    # Resolutions
    _check_resolutions(state)

    # Periodic fill rate log (every heavy cycle)
    if is_heavy:
        fr, ff, fs = _fill_rate(state)
        if fs > 0:
            fst = state.get("fill_stats", _FILL_STATS_DEFAULT)
            logger.info("FILL STATS: %d/%d (%.0f%%) | cancel=%d expired=%d",
                        ff, fs, fr, fst.get("cancelled", 0), fst.get("expired", 0))

    # Cleanup old resolved
    resolved = [c for c, m in state["markets"].items() if m["phase"] == "RESOLVED"]
    if len(resolved) > 50:
        for c in resolved[:-50]:
            del state["markets"][c]

    return state


# ═══════════════════════════════════════
#  Status
# ═══════════════════════════════════════

def _status(state: dict):
    wl = state.get("watchlist", {})
    active = {c: m for c, m in state["markets"].items() if m["phase"] != "RESOLVED"}
    resolved = {c: m for c, m in state["markets"].items() if m["phase"] == "RESOLVED"}
    print(f"\n{'='*55}")
    print(f"  MM v4 Status — {datetime.now(tz=_HKT):%Y-%m-%d %H:%M HKT}")
    print(f"{'='*55}")
    print(f"  Bankroll:  ${state.get('bankroll', 0):.2f}")
    print(f"  Watchlist: {len(wl)} | Active: {len(active)} | Resolved: {len(resolved)}")
    print(f"  Daily PnL: ${state.get('daily_pnl', 0):.2f} | Total: ${state.get('total_pnl', 0):.2f}")
    print(f"  Markets:   {state.get('total_markets', 0)} | Consec losses: {state.get('consecutive_losses', 0)}")
    _ibr = state.get("initial_bankroll", state.get("bankroll", 0))
    _tpnl = state.get("total_pnl", 0)
    _pct = _tpnl / _ibr * 100 if _ibr > 0 else 0
    _stop = " 💀 HARD STOPPED" if state.get("hard_stopped") else ""
    print(f"  Drawdown:  ${_tpnl:.2f} ({_pct:+.1f}% of ${_ibr:.0f}) | limit -20%{_stop}")
    fr, ff, fs = _fill_rate(state)
    fstats = state.get("fill_stats", _FILL_STATS_DEFAULT)
    print(f"  Fill Rate: {fr:.0f}% ({ff}/{fs}) | Cancel: {fstats.get('cancelled',0)} | Expired: {fstats.get('expired',0)}")
    _ls = state.get("live_start_ts", 0)
    if _ls > 0:
        elapsed_h = (time.time() - _ls) / 3600
        if elapsed_h < _PROTECTION_HOURS:
            print(f"  🛡️ PROTECTION: {elapsed_h:.1f}/{_PROTECTION_HOURS}h | bet={_PROTECTION_BET_PCT:.0%} | max {_PROTECTION_MAX_MARKETS} mkt")
        else:
            print(f"  Protection: ended ({elapsed_h:.1f}h elapsed)")
    if wl:
        print(f"\n  ── Watchlist ──")
        for c, w in wl.items():
            lead = (w["start_ms"] - int(time.time() * 1000)) / 60_000
            print(f"  {c[:8]} | {lead:+.0f}m | {w['title'][:40]}")
    if active:
        print(f"\n  ── Active ──")
        for c, m in active.items():
            comb = m.get("up_avg_price", 0) + m.get("down_avg_price", 0)
            print(f"  {c[:8]} | combined={comb:.3f} | ${m.get('entry_cost',0):.2f}")
    if resolved:
        for m in list(resolved.values())[-3:]:
            print(f"  {m.get('condition_id','')[:8]} | PnL ${m.get('realized_pnl', 0):.2f}")
    print()


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="MM v3 — Strategy C")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--status", action="store_true")
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--bankroll", type=float, default=0,
                    help="Override bankroll (for dry-run simulation)")
    ap.add_argument("--bet-pct", type=float, default=0,
                    help="Override bet_pct (e.g. 0.23 for 23%%)")
    ap.add_argument("--continuous-momentum", action="store_true",
                    help="Use current_price vs open instead of M1-only")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if args.status:
        _status(_load())
        return

    dry_run = args.dry_run
    config = MMConfig()
    if args.bet_pct > 0:
        config.bet_pct = args.bet_pct

    print(f"  MODE: {'DRY-RUN' if dry_run else 'LIVE'}")

    # ─── Start market data fetcher (background, log-only for now) ───
    global _mkt_fetcher
    try:
        from polymarket.data.market_data import StaggeredFetcher
        _mkt_fetcher = StaggeredFetcher()
        _mkt_fetcher.start_background("BTCUSDT", interval_sec=10)
        print("  MARKET DATA: background fetcher started (log-only)")
    except Exception as e:
        logger.warning("Market data fetcher failed to start: %s — continuing without", e)

    gamma = GammaClient()
    client = None
    if not dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            client = PolymarketClient(dry_run=False)
            print("  CLOB: connected")
            # Startup safety: cancel OWN orphan orders only (not 1H bot's orders)
            try:
                existing = client.get_orders()
                _pre_state = _load()  # load state early for orphan filter
                _own_cids = set(_pre_state.get("markets", {}).keys()) | set(_pre_state.get("watchlist", {}).keys())
                if existing:
                    cancelled = 0
                    for o in existing:
                        oid = o.get("id", "")
                        _mkt = o.get("market", "")
                        if oid and (_mkt in _own_cids or not _mkt):
                            try:
                                client.client.cancel(order_id=oid)
                                cancelled += 1
                            except Exception as ce:
                                logger.warning("Startup cancel failed for %s: %s", oid[:12], ce)
                    print(f"  STARTUP: cancelled {cancelled}/{len(existing)} orphan orders")
            except Exception as e:
                logger.warning("Startup orphan check failed: %s", e)
        except Exception as e:
            print(f"  CLOB failed: {e} → dry-run")
            dry_run = True

    if dry_run and client is None:
        class _Mock:
            def buy_shares(self, tid, amt, price=0):
                logger.info("DRY BUY %s $%.2f @ %.3f", tid[:10], amt, price)
                return {"dry_run": True}
        client = _Mock()

    state = _load()
    if args.bankroll > 0:
        state["bankroll"] = args.bankroll
    elif client and hasattr(client, "get_usdc_balance"):
        try:
            state["bankroll"] = client.get_usdc_balance()
        except Exception:
            pass

    # Newbie protection: record first live start time
    if not dry_run and not state.get("live_start_ts"):
        state["live_start_ts"] = time.time()
        logger.info("PROTECTION: live_start_ts set — %.0fh protection active", _PROTECTION_HOURS)

    br = state.get("bankroll", 100)
    bet = br * config.bet_pct
    _prot_active = (not dry_run and state.get("live_start_ts", 0) > 0
                    and time.time() - state["live_start_ts"] < _PROTECTION_HOURS * 3600)
    _prot_str = f" | 🛡️ PROTECTION ({_PROTECTION_BET_PCT:.0%}, {_PROTECTION_MAX_MARKETS} mkt)" if _prot_active else ""
    print(f"  [{datetime.now(tz=_HKT):%H:%M HKT}] Bankroll ${br:.2f} | "
          f"Bet {config.bet_pct:.0%} = ${bet:.2f} | Spread {config.half_spread:.1%}{_prot_str}")

    if args.cycle:
        state = run_cycle(state, gamma, client, config, dry_run,
                                  continuous_momentum=getattr(args, 'continuous_momentum', False))
        _save(state)
        _status(state)
    else:
        print(f"  Loop: {_CYCLE_S}s")
        try:
            while True:
                try:
                    state = run_cycle(state, gamma, client, config, dry_run,
                                  continuous_momentum=getattr(args, 'continuous_momentum', False))
                    _save(state)
                    _log_positions(state)
                except Exception as e:
                    logger.error("Cycle error: %s", e, exc_info=True)
                time.sleep(_CYCLE_S)
        except KeyboardInterrupt:
            print("\n  Shutting down...")
            # Cancel OWN open orders on CLOB (prevent orphans, don't touch 1H bot)
            if client and hasattr(client, "get_orders") and not dry_run:
                try:
                    remaining = client.get_orders()
                    _own_cids = set(state.get("markets", {}).keys()) | set(state.get("watchlist", {}).keys())
                    for o in (remaining or []):
                        oid = o.get("id", "")
                        _mkt = o.get("market", "")
                        if oid and (_mkt in _own_cids or not _mkt):
                            try:
                                client.client.cancel(order_id=oid)
                            except Exception:
                                pass
                    if remaining:
                        print(f"  Cancelled {len(remaining)} open orders")
                except Exception:
                    pass
            _save(state)
            _status(state)
            # Shutdown market data fetcher
            if _mkt_fetcher:
                _mkt_fetcher.shutdown()


if __name__ == "__main__":
    main()
