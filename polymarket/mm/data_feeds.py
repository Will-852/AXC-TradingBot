"""
mm/data_feeds.py — Market data fetching layer for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🟡 VERIFY: _price() fallback chain order must be preserved exactly:
    WS → REST spot bookTicker → REST futures kline
"""

import json
import logging
import math
import time
import urllib.request
from datetime import datetime

from polymarket.mm.constants import (
    _API_LIMIT_PER_MIN, _BINANCE, _BINANCE_SPOT, _CROSS_EXCHANGES,
    _DATA_API, _ET, _HOLDER_CACHE_TTL, _SYM_MAP_OKX,
)

logger = logging.getLogger(__name__)

# ─── Module-level mutable state (owned by data_feeds) ───
_cache: dict = {}
_holder_cache: dict = {}  # cid → (imbalance, timestamp)
_api_calls: dict = {}     # {"binance": [(ts, count), ...]}


def _rate_ok(source: str = "binance") -> bool:
    """Check if we're within safe API call rate."""
    now = time.time()
    calls = _api_calls.get(source, [])
    calls = [(t, c) for t, c in calls if now - t < 60]
    _api_calls[source] = calls
    total = sum(c for _, c in calls)
    return total < _API_LIMIT_PER_MIN


def _track_call(source: str = "binance", n: int = 1):
    """Track an API call for rate limiting."""
    _api_calls.setdefault(source, []).append((time.time(), n))


def holder_imbalance(condition_id: str, up_token_id: str,
                     ttl_override: float = 0) -> tuple[float, float]:
    """Fetch holder position imbalance + delta from previous reading.

    Returns (imbalance, delta). imbalance in [-1, +1], delta = change since last.
    Positive imbalance = more UP shares. Negative delta = whale exit from UP.
    Cached 30s (or ttl_override for last-minute burst mode).
    Uses Data API (separate rate limit from CLOB).
    """
    key = f"holder_{condition_id}"
    now = time.time()
    ttl = ttl_override if ttl_override > 0 else _HOLDER_CACHE_TTL

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

    prev_imbalance = _holder_cache.get(condition_id, (0.0, 0))[0]
    prev_ts = _holder_cache.get(condition_id, (0.0, 0))[1]
    delta = imbalance - prev_imbalance if prev_ts > 0 else 0.0

    _holder_cache[condition_id] = (imbalance, now)
    result = (round(imbalance, 4), round(delta, 4))
    _cache[key] = (result, now)
    return result


def price(symbol: str = "BTCUSDT", ws_binance=None) -> float:
    """Latest price. Cached 1s.
    🔴 Fallback chain (DO NOT reorder):
        1. WebSocket (sub-ms, no REST call)
        2. REST spot bookTicker (fastest REST)
        3. REST futures kline (last resort)
    """
    key = f"price_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 1:
        return _cache[key][0]
    # 1. WebSocket path
    if ws_binance:
        ws_price = ws_binance.get_price(symbol)
        if ws_price:
            _cache[key] = (ws_price, now)
            return ws_price
    if not _rate_ok("binance"):
        return _cache.get(key, (0, 0))[0]
    # 2. REST spot bookTicker
    url = f"{_BINANCE_SPOT}/api/v3/ticker/bookTicker?symbol={symbol}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            data = json.loads(r.read())
            bid = float(data.get("bidPrice", 0))
            ask = float(data.get("askPrice", 0))
            p = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            if p > 0:
                _cache[key] = (p, now)
                _track_call("binance")
                return p
    except Exception:
        pass
    # 3. REST futures kline
    url = f"{_BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=1"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            p = float(json.loads(r.read())[0][4])
            _cache[key] = (p, now)
            _track_call("binance")
            return p
    except Exception as e:
        logger.warning("%s price fetch failed: %s", symbol, e)
        return _cache.get(key, (0, 0))[0]


def btc_price(ws_binance=None) -> float:
    return price("BTCUSDT", ws_binance=ws_binance)


def cross_exchange_price(symbol: str = "BTCUSDT") -> tuple[float, float]:
    """Fetch price from 3 exchanges, return (median, max_divergence_pct).
    divergence = (max - min) / median. High = anomaly.
    Falls back to Binance-only if others fail. Cached 5s."""
    key = f"xprice_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:
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


def open_at(start_ms: int, symbol: str = "BTCUSDT") -> float:
    """Price at a specific timestamp. Cached permanently (historical)."""
    key = f"open_{symbol}_{start_ms}"
    if key in _cache:
        return _cache[key][0]
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&startTime={start_ms}&limit=1"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            p = float(json.loads(r.read())[0][1])
            _cache[key] = (p, time.time())
            _track_call("binance")
            return p
    except Exception:
        return 0.0


def btc_open_at(start_ms: int) -> float:
    return open_at(start_ms, "BTCUSDT")


def vol_1m(symbol: str = "BTCUSDT") -> float:
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


def cvd_buy_ratio(symbol: str = "BTCUSDT", minutes: int = 3) -> float:
    """Taker buy ratio over last N minutes. >0.55 = buying pressure, <0.45 = selling.
    Uses Binance spot 1m klines (taker_buy_volume included). Cached 15s."""
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
        recent = candles[-minutes:]
        total_vol = sum(float(c[5]) for c in recent)
        total_buy = sum(float(c[9]) for c in recent)  # index 9 = taker_buy_volume
        ratio = total_buy / total_vol if total_vol > 0 else 0.5
        _cache[key] = (ratio, now)
        return ratio
    except Exception:
        return _cache.get(key, (0.5, 0))[0]


def m1_return(symbol: str = "BTCUSDT") -> float:
    """Last 1-minute return (log). Positive = price went up."""
    vol_key = f"vol_{symbol}"
    if vol_key in _cache and time.time() - _cache[vol_key][1] < 60:
        pass  # vol was computed recently
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
        c_prev = float(candles[0][4])
        c_now = float(candles[1][4])
        if c_prev <= 0:
            return 0.0
        return math.log(c_now / c_prev)
    except Exception:
        return 0.0


def poly_midpoint(client, token_id: str, ws_poly=None) -> float:
    """Polymarket midpoint for a token. WS first, REST fallback. Cached 5s."""
    if ws_poly:
        ws_mid = ws_poly.get_midpoint(token_id)
        if ws_mid is not None:
            return ws_mid
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


def poly_ob_imbalance(client, up_token: str, ws_poly=None) -> float:
    """Order book imbalance for UP token. WS first, REST fallback. Cached 5s. Returns -1 to +1."""
    if ws_poly:
        ws_imbal = ws_poly.get_ob_imbalance(up_token)
        if ws_imbal is not None:
            return ws_imbal
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


def discover(gamma, config) -> list:
    """Find BTC + ETH + SOL + XRP 15M markets for current + next 4 windows via slug.

    Returns list of (PolyMarket, dict) tuples.
    """
    from datetime import timedelta
    from polymarket.core.context import PolyMarket
    from polymarket.strategy.market_maker import should_enter_market

    results = []
    now_s = int(time.time())
    now_et = datetime.now(tz=_ET)
    slot = (now_et.minute // 15) * 15
    base = now_et.replace(minute=0, second=0, microsecond=0)

    _COINS = [("btc", "bitcoin"), ("eth", "ethereum"), ("sol", "solana"), ("xrp", "xrp")]

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
