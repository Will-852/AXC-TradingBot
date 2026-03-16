"""market_data.py — ATR, kline changes, multi-interval price data."""

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from scripts.dashboard.constants import _KLINE_API, _ASTER_ONLY

# ── ATR fallback: compute from 4H klines when SCAN_CONFIG lacks data ──
_atr_fallback_cache = {}  # {symbol: {"atr": float, "ts": float}}
_ATR_FALLBACK_TTL = 300   # 5 min cache


def _compute_atr_from_klines(symbol):
    """Fetch 20x 4H klines and compute ATR(14) via Wilder's RMA."""
    base = _KLINE_API["aster"] if symbol in _ASTER_ONLY else _KLINE_API["binance"]
    url = f"{base}/fapi/v1/klines?symbol={symbol}&interval=4h&limit=20"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if len(data) < 15:
            return 0
        trs = []
        for i in range(1, len(data)):
            high, low = float(data[i][2]), float(data[i][3])
            prev_close = float(data[i - 1][4])
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        period = 14
        if len(trs) < period:
            return 0
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr
    except Exception as e:
        print(f"[ATR fallback] {symbol} fetch failed: {e}", file=sys.stderr)
        return 0


def _fetch_missing_atrs(symbols):
    """Concurrently compute ATR for symbols not in SCAN_CONFIG. Results cached 5 min."""
    now = time.time()
    to_fetch = [
        s for s in symbols
        if s not in _atr_fallback_cache
        or now - _atr_fallback_cache[s]["ts"] >= _ATR_FALLBACK_TTL
    ]
    if not to_fetch:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_compute_atr_from_klines, s): s for s in to_fetch}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                val = fut.result(timeout=10)
                _atr_fallback_cache[sym] = {"atr": val, "ts": time.time()}
            except Exception:
                _atr_fallback_cache[sym] = {"atr": 0, "ts": time.time()}


# ── Kline change ────────────────────────────────────────────────────

_4h_cache = {"data": {}, "ts": 0}
_4H_CACHE_TTL = 120  # seconds


def _fetch_kline_change(symbol, interval="4h"):
    """Fetch kline from appropriate exchange for a single symbol.
    Returns pct_change or None."""
    base = _KLINE_API["aster"] if symbol in _ASTER_ONLY else _KLINE_API["binance"]
    url = f"{base}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=2"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if len(data) >= 1:
                candle = data[-1]
                open_price = float(candle[1])
                close_price = float(candle[4])
                if open_price > 0:
                    return round((close_price - open_price) / open_price * 100, 2)
    except Exception:
        pass
    return None


def get_multi_interval_changes(symbols):
    """Get 4H + 1H change for all symbols. 120s cache, concurrent fetching.
    Returns {symbol: {"4h": pct, "1h": pct}}."""
    global _4h_cache
    now = time.time()
    if now - _4h_cache["ts"] < _4H_CACHE_TTL and _4h_cache["data"]:
        return _4h_cache["data"]

    intervals = ["1h", "4h"]
    result = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for sym in symbols:
            for iv in intervals:
                futures[pool.submit(_fetch_kline_change, sym, iv)] = (sym, iv)
        for fut in as_completed(futures):
            sym, iv = futures[fut]
            try:
                val = fut.result(timeout=8)
                if val is not None:
                    if sym not in result:
                        result[sym] = {}
                    result[sym][iv] = val
            except Exception:
                pass

    _4h_cache["data"] = result
    _4h_cache["ts"] = now
    return result
