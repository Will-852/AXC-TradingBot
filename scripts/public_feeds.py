"""
public_feeds.py — 公共價格數據源（9 路輪轉，無需認證）
v1 | 2026-03-10

每個 exchange 用單一 HTTP GET 取得所有 ticker，回傳統一格式。
設計決定：
- 只做讀取，唔涉及交易/認證
- 任何 exchange 失敗 → 返回空 dict，唔影響輪轉
- Symbol 正規化為 "XXXUSDT" 格式
- 每個 fetcher 用 sync HTTP，由 async wrapper 跑 thread pool
"""

import asyncio
import concurrent.futures
import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("scanner.feeds")

_TIMEOUT = 10
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="feeds",
)

TickerData = dict  # {price, change, high, low, volume}


def _get_json(url: str, timeout: int = _TIMEOUT) -> Optional[dict | list]:
    """Sync HTTP GET → JSON. Returns None on any error."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "OpenClaw-Scanner/7.0",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.debug(f"GET failed {url}: {e}")
        return None


def _post_json(url: str, body: dict, timeout: int = _TIMEOUT) -> Optional[dict | list]:
    """Sync HTTP POST → JSON. For HyperLiquid."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "OpenClaw-Scanner/7.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.debug(f"POST failed {url}: {e}")
        return None


def _safe_float(val, default: float = 0.0) -> float:
    """Safe float conversion."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ════════════════════════════════════════════════════
# Exchange Fetchers — each returns dict[str, TickerData]
# ════════════════════════════════════════════════════

def _fetch_aster_sync() -> dict[str, TickerData]:
    """Aster DEX — Binance fork, same API format. Bulk endpoint."""
    data = _get_json("https://fapi.asterdex.com/fapi/v1/ticker/24hr")
    if not data or not isinstance(data, list):
        return {}
    result = {}
    for t in data:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        result[sym] = {
            "price":  _safe_float(t.get("lastPrice")),
            "change": _safe_float(t.get("priceChangePercent")),
            "high":   _safe_float(t.get("highPrice")),
            "low":    _safe_float(t.get("lowPrice")),
            "volume": _safe_float(t.get("quoteVolume")),
        }
    return result


def _fetch_binance_sync() -> dict[str, TickerData]:
    """Binance Futures — all USDT perpetual tickers."""
    data = _get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
    if not data or not isinstance(data, list):
        return {}
    result = {}
    for t in data:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        result[sym] = {
            "price":  _safe_float(t.get("lastPrice")),
            "change": _safe_float(t.get("priceChangePercent")),
            "high":   _safe_float(t.get("highPrice")),
            "low":    _safe_float(t.get("lowPrice")),
            "volume": _safe_float(t.get("quoteVolume")),
        }
    return result


def _fetch_hyperliquid_sync() -> dict[str, TickerData]:
    """HyperLiquid — POST /info metaAndAssetCtxs."""
    data = _post_json(
        "https://api.hyperliquid.xyz/info",
        {"type": "metaAndAssetCtxs"},
    )
    if not isinstance(data, list) or len(data) < 2:
        return {}
    meta, ctxs = data[0], data[1]
    universe = meta.get("universe", [])
    if len(universe) != len(ctxs):
        return {}
    result = {}
    for asset_info, ctx in zip(universe, ctxs):
        name = asset_info.get("name", "")
        sym = f"{name}USDT"
        mark_px = _safe_float(ctx.get("markPx"))
        prev_px = _safe_float(ctx.get("prevDayPx"))
        change = ((mark_px - prev_px) / prev_px * 100) if prev_px else 0.0
        result[sym] = {
            "price":  mark_px,
            "change": round(change, 4),
            "high":   0.0,  # HL 無 24h high/low
            "low":    0.0,
            "volume": _safe_float(ctx.get("dayNtlVlm")),
        }
    return result


def _fetch_bybit_sync() -> dict[str, TickerData]:
    """Bybit V5 — all linear (USDT perp) tickers."""
    data = _get_json("https://api.bybit.com/v5/market/tickers?category=linear")
    if not isinstance(data, dict):
        return {}
    items = data.get("result", {}).get("list", [])
    result = {}
    for t in items:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        # Bybit price24hPcnt is decimal (0.025 = 2.5%)
        result[sym] = {
            "price":  _safe_float(t.get("lastPrice")),
            "change": round(_safe_float(t.get("price24hPcnt")) * 100, 4),
            "high":   _safe_float(t.get("highPrice24h")),
            "low":    _safe_float(t.get("lowPrice24h")),
            "volume": _safe_float(t.get("turnover24h")),
        }
    return result


def _fetch_okx_sync() -> dict[str, TickerData]:
    """OKX — all USDT perpetual swap tickers."""
    data = _get_json("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
    if not isinstance(data, dict):
        return {}
    items = data.get("data", [])
    result = {}
    for t in items:
        inst_id = t.get("instId", "")  # e.g. "BTC-USDT-SWAP"
        if "USDT" not in inst_id:
            continue
        sym = inst_id.replace("-SWAP", "").replace("-", "")  # → BTCUSDT
        last = _safe_float(t.get("last"))
        open_24h = _safe_float(t.get("open24h"))
        change = ((last - open_24h) / open_24h * 100) if open_24h else 0.0
        result[sym] = {
            "price":  last,
            "change": round(change, 4),
            "high":   _safe_float(t.get("high24h")),
            "low":    _safe_float(t.get("low24h")),
            "volume": _safe_float(t.get("volCcy24h")),
        }
    return result


def _fetch_kucoin_sync() -> dict[str, TickerData]:
    """KuCoin — spot allTickers（futures 無 bulk endpoint）。"""
    data = _get_json("https://api.kucoin.com/api/v1/market/allTickers")
    if not isinstance(data, dict):
        return {}
    items = data.get("data", {}).get("ticker", [])
    result = {}
    for t in items:
        raw_sym = t.get("symbol", "")  # e.g. "BTC-USDT"
        if "-USDT" not in raw_sym:
            continue
        sym = raw_sym.replace("-", "")  # → BTCUSDT
        # changeRate is decimal (0.025 = 2.5%)
        result[sym] = {
            "price":  _safe_float(t.get("last")),
            "change": round(_safe_float(t.get("changeRate")) * 100, 4),
            "high":   _safe_float(t.get("high")),
            "low":    _safe_float(t.get("low")),
            "volume": _safe_float(t.get("volValue")),
        }
    return result


def _fetch_gate_sync() -> dict[str, TickerData]:
    """Gate.io — USDT futures tickers."""
    data = _get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers")
    if not isinstance(data, list):
        return {}
    result = {}
    for t in data:
        contract = t.get("contract", "")  # e.g. "BTC_USDT"
        sym = contract.replace("_", "")   # → BTCUSDT
        result[sym] = {
            "price":  _safe_float(t.get("last")),
            "change": _safe_float(t.get("change_percentage")),
            "high":   _safe_float(t.get("high_24h")),
            "low":    _safe_float(t.get("low_24h")),
            "volume": _safe_float(t.get("volume_24h_quote")),
        }
    return result


def _fetch_mexc_sync() -> dict[str, TickerData]:
    """MEXC — spot 24hr tickers（Binance 格式）。"""
    data = _get_json("https://api.mexc.com/api/v3/ticker/24hr")
    if not isinstance(data, list):
        return {}
    result = {}
    for t in data:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        result[sym] = {
            "price":  _safe_float(t.get("lastPrice")),
            "change": _safe_float(t.get("priceChangePercent")),
            "high":   _safe_float(t.get("highPrice")),
            "low":    _safe_float(t.get("lowPrice")),
            "volume": _safe_float(t.get("quoteVolume")),
        }
    return result


def _fetch_bitget_sync() -> dict[str, TickerData]:
    """Bitget — USDT-FUTURES all tickers."""
    data = _get_json(
        "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
    )
    if not isinstance(data, dict):
        return {}
    items = data.get("data", [])
    result = {}
    for t in items:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        last = _safe_float(t.get("lastPr"))
        # change24h is already percentage string
        result[sym] = {
            "price":  last,
            "change": _safe_float(t.get("change24h")),
            "high":   _safe_float(t.get("high24h")),
            "low":    _safe_float(t.get("low24h")),
            "volume": _safe_float(t.get("quoteVolume")),
        }
    return result


# ── Dispatch ──

_FETCHERS = {
    "aster":       _fetch_aster_sync,
    "binance":     _fetch_binance_sync,
    "hyperliquid": _fetch_hyperliquid_sync,
    "bybit":       _fetch_bybit_sync,
    "okx":         _fetch_okx_sync,
    "kucoin":      _fetch_kucoin_sync,
    "gate":        _fetch_gate_sync,
    "mexc":        _fetch_mexc_sync,
    "bitget":      _fetch_bitget_sync,
}


async def fetch_exchange_tickers(exchange: str) -> dict[str, TickerData]:
    """Async wrapper — run sync fetcher in thread pool.
    Returns {symbol: TickerData} or empty dict on failure."""
    fn = _FETCHERS.get(exchange)
    if fn is None:
        log.warning(f"Unknown exchange: {exchange}")
        return {}
    loop = asyncio.get_running_loop()
    try:
        fut = loop.run_in_executor(_executor, fn)
        return await asyncio.wait_for(fut, timeout=15)
    except asyncio.TimeoutError:
        log.warning(f"⏱ {exchange} all-tickers 超時")
        return {}
    except Exception as e:
        log.error(f"❌ {exchange} fetch: {type(e).__name__}: {e}")
        return {}


def shutdown():
    """Cleanup thread pool."""
    _executor.shutdown(wait=False)


# ── CLI Test ──

if __name__ == "__main__":
    """逐個測試每個 exchange 嘅 public feed。"""
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    test_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

    async def test_all():
        for name in _FETCHERS:
            print(f"\n{'='*50}")
            print(f"Testing: {name}")
            print(f"{'='*50}")
            tickers = await fetch_exchange_tickers(name)
            if not tickers:
                print(f"  ❌ FAILED — empty response")
                continue
            print(f"  ✅ Got {len(tickers)} tickers")
            for sym in test_symbols:
                if sym in tickers:
                    t = tickers[sym]
                    print(f"  {sym}: ${t['price']:.2f}  chg:{t['change']:+.2f}%  vol:${t['volume']:,.0f}")
                else:
                    print(f"  {sym}: not found")

    asyncio.run(test_all())
    shutdown()
