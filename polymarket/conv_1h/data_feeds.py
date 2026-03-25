"""
conv_1h/data_feeds.py — Market data fetching layer for 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
⚠️ DOWNSTREAM D2: _btc_price, _vol_1m, _poly_midpoint near-identical to mm/data_feeds.py.
    Future: extract to polymarket/shared/data_feeds.py.
"""

import json
import logging
import math
import time
import urllib.request

from polymarket.conv_1h.constants import (
    _BINANCE, _COIN_SYMBOLS, _DATA_API, _HOLDER_CACHE_TTL,
    _VOL_IMBAL_CACHE_TTL,
)

logger = logging.getLogger(__name__)

# ─── Module-level caches ───
_holder_cache: dict = {}     # {cid: (ts, imbalance)}
_price_cache: dict = {}      # {coin: (ts, price)}
_vol_imbal_cache: dict = {}  # {key: (ts, direction_or_none)}


def _get_json(url: str, timeout: int = 10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-1H/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug("HTTP fail %s: %s", url[:80], e)
        return None


def holder_imbalance(cid: str) -> float:
    """Fetch holder imbalance for a market. Returns float in [-1, +1].
    Positive = UP dominant, negative = DOWN dominant. Cached 30s."""
    now = time.time()
    if cid in _holder_cache and now - _holder_cache[cid][0] < _HOLDER_CACHE_TTL:
        return _holder_cache[cid][1]
    try:
        data = _get_json(f"{_DATA_API}/holders?market={cid}&limit=10&minBalance=10", timeout=2)
        if not data or not isinstance(data, list):
            _holder_cache[cid] = (now, 0.0)
            return 0.0
        up_total, dn_total = 0.0, 0.0
        for group in data:
            hs = group.get("holders", []) if isinstance(group, dict) else []
            if not hs:
                continue
            idx = hs[0].get("outcomeIndex", -1)
            total = sum(float(h.get("amount", 0)) for h in hs)
            if idx == 0:
                up_total = total
            elif idx == 1:
                dn_total = total
        combined = up_total + dn_total
        imbal = (up_total - dn_total) / combined if combined > 0 else 0.0
        _holder_cache[cid] = (now, imbal)
        return imbal
    except Exception:
        _holder_cache[cid] = (now, 0.0)
        return 0.0


def btc_price(coin: str = "BTC", ws_binance=None) -> float:
    """Get latest price with 3s cache. WS first, REST fallback."""
    now = time.time()
    if coin in _price_cache and now - _price_cache[coin][0] < 3:
        return _price_cache[coin][1]
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    if ws_binance:
        ws_price = ws_binance.get_price(sym)
        if ws_price:
            _price_cache[coin] = (now, ws_price)
            return ws_price
    data = _get_json(f"{_BINANCE}/ticker/price?symbol={sym}")
    if data:
        p = float(data["price"])
        _price_cache[coin] = (now, p)
        return p
    return _price_cache.get(coin, (0, 0))[1]


def binance_open(coin: str, start_ms: int) -> float | None:
    """Fetch Binance 1H candle open price."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1h&startTime={start_ms}&limit=1")
    if data and isinstance(data, list) and data:
        return float(data[0][1])
    return None


def vol_imbalance(coin: str, window_start_ms: int) -> str | None:
    """Check Binance 1m kline buy/sell volume ratio since window start.
    Returns 'UP' if buy-dominant, 'DOWN' if sell-dominant, None if neutral. Cached 15s."""
    now = time.time()
    cache_key = f"{coin}_{window_start_ms}"
    if cache_key in _vol_imbal_cache and now - _vol_imbal_cache[cache_key][0] < _VOL_IMBAL_CACHE_TTL:
        return _vol_imbal_cache[cache_key][1]

    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now_ms = int(now * 1000)
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m"
                     f"&startTime={window_start_ms}&endTime={now_ms}&limit=60")
    if not data or not isinstance(data, list) or len(data) < 5:
        _vol_imbal_cache[cache_key] = (now, None)
        return None

    buy_vol, sell_vol = 0.0, 0.0
    for k in data:
        o, c, v = float(k[1]), float(k[4]), float(k[5])
        if c >= o:
            buy_vol += v
        else:
            sell_vol += v

    total = buy_vol + sell_vol
    if total < 1:
        _vol_imbal_cache[cache_key] = (now, None)
        return None

    ratio = buy_vol / total
    result = "UP" if ratio > 0.55 else ("DOWN" if ratio < 0.45 else None)
    _vol_imbal_cache[cache_key] = (now, result)
    return result


def vol_1m(coin: str = "BTC") -> float:
    """Per-minute volatility from Binance 1m klines (120 candles = 2h)."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    url = f"{_BINANCE}/klines?symbol={sym}&interval=1m&limit=120"
    data = _get_json(url)
    if not data or len(data) < 20:
        return 0.00077
    closes = [float(k[4]) for k in data]
    rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
    if len(rets) < 10:
        return 0.00077
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean)**2 for r in rets) / len(rets))
    return max(0.0001, vol)


def poly_midpoint(token_id: str, ws_poly=None) -> float | None:
    """Polymarket midpoint. WS first, REST fallback."""
    if ws_poly:
        ws_mid = ws_poly.get_midpoint(token_id)
        if ws_mid is not None:
            return ws_mid
    data = _get_json(f"https://clob.polymarket.com/midpoint?token_id={token_id}")
    if data:
        try:
            return float(data["mid"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def poly_ob(token_id: str, ws_poly=None):
    """Fetch OB and return OBState for conviction engine. WS first, REST fallback."""
    from polymarket.strategy.hourly_engine import OBState

    if ws_poly:
        ws_state = ws_poly.get_book_state(token_id)
        if ws_state is not None:
            best_bid = ws_state["bid"]
            best_ask = ws_state["ask"]
            spread = (best_ask - best_bid) if best_bid and best_ask and best_ask > best_bid else 0
            return OBState(
                spread=round(spread, 4),
                bid_depth=ws_state["bid_depth"],
                ask_depth=ws_state["ask_depth"],
                imbalance=ws_state["imbalance"],
            )
    data = _get_json(f"https://clob.polymarket.com/book?token_id={token_id}")
    if not data:
        return OBState()
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    bid_prices = [float(b["price"]) for b in bids if b.get("price")]
    ask_prices = [float(a["price"]) for a in asks if a.get("price")]
    best_bid = max(bid_prices) if bid_prices else 0
    best_ask = min(ask_prices) if ask_prices else 0
    spread = (best_ask - best_bid) if best_bid and best_ask and best_ask > best_bid else 0
    bid_vol = sum(float(b.get("size", 0)) for b in bids)
    ask_vol = sum(float(a.get("size", 0)) for a in asks)
    total = bid_vol + ask_vol
    return OBState(
        spread=round(spread, 4),
        bid_depth=round(bid_vol, 2),
        ask_depth=round(ask_vol, 2),
        imbalance=round((bid_vol - ask_vol) / total, 4) if total else 0,
    )
