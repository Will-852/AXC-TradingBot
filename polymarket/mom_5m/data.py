"""
mom_5m/data.py — Market data fetching layer for the 5M Momentum bot.

Split from run_5m_live.py (2026-03-26).
🟡 VERIFY: Price caches + WS integration. No trading impact but affects signal quality.
"""

import json
import logging
import math
import time
import urllib.request

from polymarket.mom_5m.config import _BINANCE, _BINANCE_FUTURES, _COIN_SYMBOLS

logger = logging.getLogger(__name__)

# ─── Module-level caches ───
_price_cache: dict = {}   # {symbol: (ts, price)}
_vol_cache: dict = {}     # {symbol: (ts, vol)}
_open_cache: dict = {}    # {f"{symbol}_{ms}": price} — permanent cache
_taker_cache: dict = {}   # {symbol: (ts, ratio)}

# ─── WS feed references (set by orchestrator via set_ws_feeds) ───
_ws_binance = None
_ws_poly = None


def set_ws_feeds(ws_binance=None, ws_poly=None):
    """Called by orchestrator after WS init to inject feed references."""
    global _ws_binance, _ws_poly
    _ws_binance = ws_binance
    _ws_poly = ws_poly


def _get_json(url: str, timeout: int = 10):
    """Fetch JSON from URL. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-5M/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug("HTTP fail %s: %s", url[:80], e)
        return None


def coin_price(coin: str) -> float:
    """Get latest price with 3s cache. WS first, REST fallback."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now = time.time()
    if sym in _price_cache and now - _price_cache[sym][0] < 3:
        return _price_cache[sym][1]
    # WebSocket path
    if _ws_binance:
        ws_price = _ws_binance.get_price(sym)
        if ws_price:
            _price_cache[sym] = (now, ws_price)
            return ws_price
    # REST fallback
    data = _get_json(f"{_BINANCE}/ticker/price?symbol={sym}")
    if data:
        p = float(data["price"])
        _price_cache[sym] = (now, p)
        return p
    return _price_cache.get(sym, (0, 0))[1]


def open_at(start_ms: int, coin: str) -> float:
    """Price at a specific timestamp. Cached permanently (historical)."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    key = f"{sym}_{start_ms}"
    if key in _open_cache:
        return _open_cache[key]
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m&startTime={start_ms}&limit=1")
    if data and isinstance(data, list) and data:
        price = float(data[0][1])  # open price of 1m candle
        _open_cache[key] = price
        return price
    return 0.0


def vol_1m(coin: str) -> float:
    """Per-minute volatility from Binance 1m klines (120 candles). Cached 60s."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now = time.time()
    if sym in _vol_cache and now - _vol_cache[sym][0] < 60:
        return _vol_cache[sym][1]
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m&limit=120")
    if not data or len(data) < 20:
        return 0.00077  # fallback: ~50% annual BTC vol
    closes = [float(k[4]) for k in data]
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 10:
        return 0.00077
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
    vol = max(0.0001, vol)
    _vol_cache[sym] = (now, vol)
    return vol


def binance_taker_ratio(coin: str, lookback_s: int = 120) -> float | None:
    """Binance futures taker buy ratio over last `lookback_s` seconds.

    Returns buy_qty / total_qty in [0, 1]. >0.5 = buyers dominant.
    Uses futures aggTrades API. Cached 10s per symbol.
    Returns None on failure (caller should skip veto, not block entry).
    """
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now = time.time()
    if sym in _taker_cache and now - _taker_cache[sym][0] < 10:
        return _taker_cache[sym][1]

    start_ms = int((now - lookback_s) * 1000)
    url = (f"{_BINANCE_FUTURES}/fapi/v1/aggTrades"
           f"?symbol={sym}&startTime={start_ms}&limit=1000")
    data = _get_json(url, timeout=5)
    if not data or not isinstance(data, list) or len(data) < 5:
        return None

    buy_qty = 0.0
    total_qty = 0.0
    for t in data:
        qty = float(t.get("q", 0))
        total_qty += qty
        if not t.get("m", True):  # m=False → buyer is maker → taker buy
            buy_qty += qty

    if total_qty <= 0:
        return None

    ratio = buy_qty / total_qty
    _taker_cache[sym] = (now, ratio)
    return ratio


def poly_midpoint(token_id: str) -> float | None:
    """Polymarket midpoint for a token. WS first, REST fallback."""
    if _ws_poly:
        ws_mid = _ws_poly.get_midpoint(token_id)
        if ws_mid is not None:
            return ws_mid
    data = _get_json(f"https://clob.polymarket.com/midpoint?token_id={token_id}")
    if data:
        try:
            return float(data["mid"])
        except (KeyError, TypeError, ValueError):
            pass
    return None
