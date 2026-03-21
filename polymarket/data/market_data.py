"""
market_data.py — Staggered multi-exchange data fetcher

設計決定：
- 用數量壓延遲：同類數據從多個交易所 parallel fetch，最快回嘅就用
- Staggered slots：唔同類數據順序 fetch，避免 burst + 每秒都有新數據
- 每個 source 獨立 try/except，任何一個掛都唔影響其他
- MarketSnapshot = frozen dataclass，thread-safe
- SnapshotHistory = ring buffer，供 delta/trend 計算

Architecture:
  SLOT 1 (t=0):    Price       — 5 sources parallel
  SLOT 2 (t=1.5):  Funding     — 5 sources parallel
  SLOT 3 (t=3.5):  OI          — 4 sources parallel
  SLOT 4 (t=5.5):  Flow + L/S  — 5 sources parallel
  SLOT 5 (t=7.5):  Depth + Vol — 5 sources parallel
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "AXC/1.0"}
_DEFAULT_TIMEOUT = 3  # seconds per HTTP call


# ════════════════════════════════════════
#  Data Model
# ════════════════════════════════════════

@dataclass(frozen=True)
class MarketSnapshot:
    """Immutable point-in-time snapshot of all market data.

    Produced by StaggeredFetcher.run_cycle(). Consumed by MM/1H bots.
    All floats default to 0.0 (= no data). Consumers check sources_responded.
    """
    timestamp: float = 0.0
    symbol: str = "BTCUSDT"

    # ─── Price (SLOT 1) ───
    price: float = 0.0                           # median of N sources
    price_sources: dict[str, float] = field(default_factory=dict)
    price_divergence: float = 0.0                # (max - min) / median

    # ─── Funding (SLOT 2) ───
    funding_premium: float = 0.0                 # Binance mark - index (real-time)
    funding_rates: dict[str, float] = field(default_factory=dict)
    funding_agg: float = 0.0                     # median across exchanges
    funding_divergence: float = 0.0              # max - min

    # ─── Open Interest (SLOT 3) ───
    oi_usd: dict[str, float] = field(default_factory=dict)  # per-exchange in USD
    oi_total: float = 0.0                        # sum across exchanges
    oi_delta_5m: float = 0.0                     # change from 5 min ago

    # ─── Taker Flow (SLOT 4) ───
    taker_buy_sell_ratio: float = 0.0            # Binance futures
    taker_sources: dict[str, float] = field(default_factory=dict)

    # ─── Long/Short Ratio (SLOT 4) ───
    ls_ratio: float = 0.0                        # top trader L/S (Binance)
    ls_sources: dict[str, float] = field(default_factory=dict)
    ls_extreme: bool = False                     # >1.38 (58%) or <0.72 (42%)

    # ─── DVOL (SLOT 5) ───
    dvol: float = 0.0                            # Deribit volatility index
    dvol_change_5m: float = 0.0

    # ─── Book Depth (SLOT 5) ───
    book_imbalance: dict[str, float] = field(default_factory=dict)

    # ─── Meta ───
    sources_responded: int = 0
    sources_total: int = 0
    fetch_ms: float = 0.0
    slot_timings: dict[str, float] = field(default_factory=dict)

    @property
    def age_ms(self) -> float:
        return (time.time() - self.timestamp) * 1000 if self.timestamp else float("inf")


# ════════════════════════════════════════
#  HTTP Helpers
# ════════════════════════════════════════

def _http_get(url: str, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    """GET JSON from URL. Raises on failure (caller must handle)."""
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════
#  Individual Source Fetchers
# ════════════════════════════════════════
# Each returns a dict fragment that gets merged into the snapshot.
# Naming: _fetch_{slot}_{exchange}_{data}

# ─── SLOT 1: Price ───

def _fetch_price_binance_spot(symbol: str) -> dict:
    data = _http_get(f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}")
    bid = _safe_float(data.get("bidPrice"))
    ask = _safe_float(data.get("askPrice"))
    price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
    return {"binance_spot": price} if price > 0 else {}


def _fetch_price_binance_fut(symbol: str) -> dict:
    data = _http_get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
    price = _safe_float(data.get("markPrice"))
    return {"binance_fut": price} if price > 0 else {}


def _fetch_price_okx(symbol: str) -> dict:
    okx_sym = symbol.replace("USDT", "-USDT")
    data = _http_get(f"https://www.okx.com/api/v5/market/ticker?instId={okx_sym}")
    items = data.get("data", [])
    price = _safe_float(items[0].get("last")) if items else 0.0
    return {"okx": price} if price > 0 else {}


def _fetch_price_bybit(symbol: str) -> dict:
    data = _http_get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}")
    items = data.get("result", {}).get("list", [])
    price = _safe_float(items[0].get("lastPrice")) if items else 0.0
    return {"bybit": price} if price > 0 else {}


def _fetch_price_hl(symbol: str) -> dict:
    """Hyperliquid — POST to info endpoint (no SDK dependency)."""
    coin = symbol.replace("USDT", "")
    payload = json.dumps({"type": "allMids"}).encode()
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", **_UA},
    )
    with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as r:
        mids = json.loads(r.read())
    price = _safe_float(mids.get(coin))
    return {"hl": price} if price > 0 else {}


# ─── SLOT 2: Funding ───

def _fetch_funding_binance(symbol: str) -> dict:
    data = _http_get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
    rate = _safe_float(data.get("lastFundingRate"))
    mark = _safe_float(data.get("markPrice"))
    index = _safe_float(data.get("indexPrice"))
    premium = mark - index if mark > 0 and index > 0 else 0.0
    return {"funding_rate": rate, "premium": premium}


def _fetch_funding_okx(symbol: str) -> dict:
    inst = symbol.replace("USDT", "-USDT-SWAP")
    data = _http_get(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst}")
    items = data.get("data", [])
    rate = _safe_float(items[0].get("fundingRate")) if items else 0.0
    return {"funding_rate": rate}


def _fetch_funding_bybit(symbol: str) -> dict:
    data = _http_get(
        f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={symbol}&limit=1"
    )
    items = data.get("result", {}).get("list", [])
    rate = _safe_float(items[0].get("fundingRate")) if items else 0.0
    return {"funding_rate": rate}


def _fetch_funding_deribit(symbol: str) -> dict:
    coin = symbol.replace("USDT", "")  # BTCUSDT → BTC, ETHUSDT → ETH
    data = _http_get(f"https://www.deribit.com/api/v2/public/ticker?instrument_name={coin}-PERPETUAL")
    result = data.get("result", {})
    rate = _safe_float(result.get("current_funding"))
    # Deribit perp OI is already in USD
    oi_usd = _safe_float(result.get("open_interest"))
    # Note: mark_iv from perp is NOT the DVOL index — DVOL comes from _fetch_dvol_deribit
    return {"funding_rate": rate, "oi_usd": oi_usd}


def _fetch_funding_hl(symbol: str) -> dict:
    """HL funding via REST (no SDK needed)."""
    coin = symbol.replace("USDT", "")  # BTCUSDT → BTC, ETHUSDT → ETH
    payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", **_UA},
    )
    with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as r:
        data = json.loads(r.read())
    # data = [meta_dict, [asset_ctx_0, asset_ctx_1, ...]]
    if not isinstance(data, list) or len(data) < 2:
        return {}
    meta = data[0]
    ctxs = data[1]
    universe = meta.get("universe", [])
    for i, asset in enumerate(universe):
        if asset.get("name") == coin and i < len(ctxs):
            ctx = ctxs[i]
            rate = _safe_float(ctx.get("funding"))
            oi = _safe_float(ctx.get("openInterest"))
            mark = _safe_float(ctx.get("markPx"))
            return {"funding_rate": rate, "oi_coins": oi, "mark": mark}
    return {}


# ─── SLOT 3: Open Interest ───

def _fetch_oi_binance(symbol: str) -> dict:
    data = _http_get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}")
    oi = _safe_float(data.get("openInterest"))
    # OI in contracts — need price to convert to USD
    # premiumIndex gives us markPrice; fetch it if needed
    price_data = _http_get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
    mark = _safe_float(price_data.get("markPrice"))
    oi_usd = oi * mark if mark > 0 else 0.0
    return {"oi_usd": oi_usd}


def _fetch_oi_okx(symbol: str) -> dict:
    inst = symbol.replace("USDT", "-USDT-SWAP")
    data = _http_get(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst}")
    items = data.get("data", [])
    oi_usd = _safe_float(items[0].get("oiUsd")) if items else 0.0
    return {"oi_usd": oi_usd}


def _fetch_oi_bybit(symbol: str) -> dict:
    data = _http_get(
        f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=1"
    )
    items = data.get("result", {}).get("list", [])
    oi = _safe_float(items[0].get("openInterest")) if items else 0.0
    # Bybit OI is in coin units — multiply by current price for USD
    # Use Bybit's own ticker for consistency
    try:
        ticker = _http_get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")
        t_list = ticker.get("result", {}).get("list", [])
        price = _safe_float(t_list[0].get("markPrice")) if t_list else 0.0
        oi_usd = oi * price if price > 0 else 0.0
    except Exception as e:
        logger.debug("Bybit OI price fetch failed: %s", e)
        oi_usd = 0.0
    return {"oi_usd": oi_usd}


# ─── SLOT 4: Taker Flow + L/S Ratio ───

def _fetch_taker_binance(symbol: str) -> dict:
    data = _http_get(
        f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1"
    )
    if data and isinstance(data, list):
        ratio = _safe_float(data[0].get("buySellRatio"))
        return {"taker_ratio": ratio}
    return {}


def _fetch_ls_binance_top(symbol: str) -> dict:
    data = _http_get(
        f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=5m&limit=1"
    )
    if data and isinstance(data, list):
        ratio = _safe_float(data[0].get("longShortRatio"))
        return {"ls_ratio": ratio}
    return {}


def _fetch_ls_binance_global(symbol: str) -> dict:
    data = _http_get(
        f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=1"
    )
    if data and isinstance(data, list):
        ratio = _safe_float(data[0].get("longShortRatio"))
        return {"ls_ratio": ratio}
    return {}


def _fetch_ls_okx(symbol: str) -> dict:
    ccy = symbol.replace("USDT", "")
    data = _http_get(
        f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m"
    )
    items = data.get("data", [])
    if items:
        # OKX returns [timestamp, longShortRatio] pairs
        ratio = _safe_float(items[0][1]) if isinstance(items[0], list) else 0.0
        return {"ls_ratio": ratio}
    return {}


def _fetch_ls_bybit(symbol: str) -> dict:
    data = _http_get(
        f"https://api.bybit.com/v5/market/account-ratio?category=linear&symbol={symbol}&period=5min&limit=1"
    )
    items = data.get("result", {}).get("list", [])
    if items:
        ratio = _safe_float(items[0].get("buyRatio"))
        sell = _safe_float(items[0].get("sellRatio"))
        if sell > 0:
            return {"ls_ratio": ratio / sell}
    return {}


# ─── SLOT 5: Book Depth + DVOL ───

def _fetch_book_binance(symbol: str) -> dict:
    data = _http_get(f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=20")
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    bid_vol = sum(_safe_float(b[1]) for b in bids[:10])
    ask_vol = sum(_safe_float(a[1]) for a in asks[:10])
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0
    return {"book_imbalance": imbalance}


def _fetch_book_okx(symbol: str) -> dict:
    inst = symbol.replace("USDT", "-USDT-SWAP")
    data = _http_get(f"https://www.okx.com/api/v5/market/books?instId={inst}&sz=20")
    items = data.get("data", [])
    if not items:
        return {}
    bids = items[0].get("bids", [])
    asks = items[0].get("asks", [])
    bid_vol = sum(_safe_float(b[1]) for b in bids[:10])
    ask_vol = sum(_safe_float(a[1]) for a in asks[:10])
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0
    return {"book_imbalance": imbalance}


def _fetch_book_bybit(symbol: str) -> dict:
    data = _http_get(f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=20")
    result = data.get("result", {})
    bids = result.get("b", [])
    asks = result.get("a", [])
    bid_vol = sum(_safe_float(b[1]) for b in bids[:10])
    ask_vol = sum(_safe_float(a[1]) for a in asks[:10])
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0
    return {"book_imbalance": imbalance}


def _fetch_dvol_deribit(symbol: str) -> dict:
    """Fetch Deribit DVOL (volatility index)."""
    coin = symbol.replace("USDT", "")  # BTC or ETH
    try:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - 60_000  # last 60s
        dvol_data = _http_get(
            f"https://www.deribit.com/api/v2/public/get_volatility_index_data"
            f"?currency={coin}&resolution=1&start_timestamp={start_ms}&end_timestamp={now_ms}"
        )
        points = dvol_data.get("result", {}).get("data", [])
        dvol = _safe_float(points[-1][4]) if points else 0.0  # [ts, open, high, low, close]
    except Exception:
        dvol = 0.0
    return {"dvol": dvol}


# ════════════════════════════════════════
#  Slot Definitions
# ════════════════════════════════════════

# Each slot: (name, delay_after_prev_sec, [(label, fetcher_fn)])
SLOT_SCHEDULE = [
    ("price", 0.0, [
        ("binance_spot", _fetch_price_binance_spot),
        ("binance_fut", _fetch_price_binance_fut),
        ("okx", _fetch_price_okx),
        ("bybit", _fetch_price_bybit),
        ("hl", _fetch_price_hl),
    ]),
    ("funding", 0.0, [
        ("binance", _fetch_funding_binance),
        ("okx", _fetch_funding_okx),
        ("bybit", _fetch_funding_bybit),
        ("deribit", _fetch_funding_deribit),
        ("hl", _fetch_funding_hl),
    ]),
    ("oi", 0.0, [
        ("binance", _fetch_oi_binance),
        ("okx", _fetch_oi_okx),
        ("bybit", _fetch_oi_bybit),
        # HL OI piggybacks on funding fetch (same call)
    ]),
    ("flow", 0.0, [
        ("binance_taker", _fetch_taker_binance),
        ("binance_ls_top", _fetch_ls_binance_top),
        ("binance_ls_global", _fetch_ls_binance_global),
        ("okx_ls", _fetch_ls_okx),
        ("bybit_ls", _fetch_ls_bybit),
    ]),
    ("depth", 0.0, [
        ("binance", _fetch_book_binance),
        ("okx", _fetch_book_okx),
        ("bybit", _fetch_book_bybit),
        ("deribit_dvol", _fetch_dvol_deribit),
    ]),
]


# ════════════════════════════════════════
#  Snapshot History (ring buffer)
# ════════════════════════════════════════

class SnapshotHistory:
    """Thread-safe ring buffer of recent MarketSnapshots."""

    def __init__(self, maxlen: int = 60):
        self._buf: deque[MarketSnapshot] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, snap: MarketSnapshot):
        with self._lock:
            self._buf.append(snap)

    def latest(self) -> MarketSnapshot | None:
        with self._lock:
            return self._buf[-1] if self._buf else None

    def get_delta(self, field_name: str, lookback_sec: float = 300) -> float:
        """Return (latest_value - oldest_within_lookback). 0 if insufficient data."""
        with self._lock:
            if len(self._buf) < 2:
                return 0.0
            now = self._buf[-1].timestamp
            cutoff = now - lookback_sec
            latest_val = getattr(self._buf[-1], field_name, 0.0)
            # Find oldest snapshot within lookback
            for snap in self._buf:
                if snap.timestamp >= cutoff:
                    old_val = getattr(snap, field_name, 0.0)
                    return latest_val - old_val
            return 0.0


# ════════════════════════════════════════
#  Staggered Fetcher
# ════════════════════════════════════════

class StaggeredFetcher:
    """Runs all slots, staggered or parallel, producing MarketSnapshot.

    Usage:
        fetcher = StaggeredFetcher()
        snap = fetcher.run_cycle("BTCUSDT")
        # or in background:
        fetcher.start_background("BTCUSDT", interval_sec=10)
        snap = fetcher.latest()
    """

    def __init__(self, max_workers: int = 12):
        """
        Args:
            max_workers: ThreadPool size for parallel fetches.
                12 = enough for ~22 sources with some headroom.
        """
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._history = SnapshotHistory(maxlen=60)  # 10 min at 10s interval
        self._bg_thread: threading.Thread | None = None
        self._bg_stop = threading.Event()
        # Per-exchange call counter for rate limiting
        self._call_counts: dict[str, list[float]] = {}
        self._call_lock = threading.Lock()

    @property
    def history(self) -> SnapshotHistory:
        return self._history

    def latest(self) -> MarketSnapshot | None:
        return self._history.latest()

    def _track_call(self, exchange: str):
        with self._call_lock:
            now = time.time()
            calls = self._call_counts.setdefault(exchange, [])
            # Prune calls older than 60s
            self._call_counts[exchange] = [t for t in calls if now - t < 60]
            self._call_counts[exchange].append(now)

    def calls_per_min(self, exchange: str) -> int:
        """Current call count for an exchange. Used for monitoring/logging."""
        with self._call_lock:
            now = time.time()
            calls = self._call_counts.get(exchange, [])
            return sum(1 for t in calls if now - t < 60)

    def run_cycle(self, symbol: str = "BTCUSDT") -> MarketSnapshot:
        """Execute all slots, produce a MarketSnapshot.

        All fetchers fire in parallel (max_workers threads). Slot grouping is
        logical (for result aggregation) not temporal — no sequential wait.
        """
        t0 = time.time()
        results: dict[str, Any] = {}
        slot_timings: dict[str, float] = {}
        sources_ok = 0
        sources_total = 0

        # Fire ALL fetchers across ALL slots in parallel
        all_futures: dict[Any, tuple[str, str]] = {}  # future → (slot_name, label)
        slot_submit_times: dict[str, float] = {}
        for slot_name, _delay, fetchers in SLOT_SCHEDULE:
            slot_submit_times[slot_name] = time.time()
            for label, fn in fetchers:
                sources_total += 1
                fut = self._pool.submit(self._safe_fetch, fn, symbol, label)
                all_futures[fut] = (slot_name, label)

        # Collect results as they complete — keep partial on timeout
        try:
            for fut in as_completed(all_futures, timeout=_DEFAULT_TIMEOUT + 2):
                slot_name, label = all_futures[fut]
                try:
                    data = fut.result()
                    if data:
                        results[f"{slot_name}_{label}"] = data
                        sources_ok += 1
                        exchange = label.split("_")[0]
                        self._track_call(exchange)
                except Exception as e:
                    logger.debug("Future %s/%s error: %s", slot_name, label, e)
        except TimeoutError:
            logger.warning("Cycle timeout: %d/%d sources responded", sources_ok, sources_total)
            # Cancel remaining futures to free pool capacity
            for fut in all_futures:
                fut.cancel()

        # Compute per-slot timings (submit→last-complete)
        now = time.time()
        for slot_name in slot_submit_times:
            slot_timings[slot_name] = (now - slot_submit_times[slot_name]) * 1000

        # ─── Aggregate into MarketSnapshot ───
        snap = self._aggregate(symbol, results, sources_ok, sources_total,
                               (time.time() - t0) * 1000, slot_timings)
        self._history.append(snap)
        return snap

    def _safe_fetch(self, fn, symbol: str, label: str) -> dict:
        """Wrapper with per-source error isolation."""
        try:
            return fn(symbol)
        except Exception as e:
            logger.debug("Fetch %s failed: %s", label, e)
            return {}

    def _aggregate(self, symbol: str, results: dict, sources_ok: int,
                   sources_total: int, fetch_ms: float,
                   slot_timings: dict) -> MarketSnapshot:
        """Build MarketSnapshot from raw slot results."""
        now = time.time()

        # ── Price: median of all sources ──
        price_sources = {}
        for key, data in results.items():
            if key.startswith("price_"):
                for exchange, px in data.items():
                    if px > 0:
                        price_sources[exchange] = px
        prices = list(price_sources.values())
        price = statistics.median(prices) if prices else 0.0
        price_div = ((max(prices) - min(prices)) / price
                     if price > 0 and len(prices) > 1 else 0.0)

        # ── Funding: per-exchange rates + premium ──
        funding_rates = {}
        funding_premium = 0.0
        hl_oi_usd = 0.0
        deribit_oi_usd = 0.0
        for key, data in results.items():
            if key.startswith("funding_"):
                exchange = key.split("_", 1)[1]
                if "funding_rate" in data:
                    funding_rates[exchange] = data["funding_rate"]
                if "premium" in data:
                    funding_premium = data["premium"]
                # Deribit OI piggybacks on funding call (already in USD)
                if "oi_usd" in data and data["oi_usd"] > 0 and exchange == "deribit":
                    deribit_oi_usd = data["oi_usd"]
                # HL OI piggybacks on funding call (coins × mark → USD)
                if "oi_coins" in data and data.get("mark", 0) > 0:
                    hl_oi_usd = data["oi_coins"] * data["mark"]
        rates = list(funding_rates.values())
        funding_agg = statistics.median(rates) if rates else 0.0
        funding_div = (max(rates) - min(rates)) if len(rates) > 1 else 0.0

        # ── Open Interest ──
        oi_sources = {}
        for key, data in results.items():
            if key.startswith("oi_"):
                exchange = key.split("_", 1)[1]
                oi_usd = data.get("oi_usd", 0.0)
                if oi_usd > 0:
                    oi_sources[exchange] = oi_usd
        if hl_oi_usd > 0:
            oi_sources["hl"] = hl_oi_usd
        if deribit_oi_usd > 0:
            oi_sources["deribit"] = deribit_oi_usd
        oi_total = sum(oi_sources.values())
        oi_delta_5m = self._history.get_delta("oi_total", 300)

        # ── Taker Flow ──
        taker_sources = {}
        for key, data in results.items():
            if key.startswith("flow_") and "taker" in key and "taker_ratio" in data:
                label = key.split("_", 1)[1]
                taker_sources[label] = data["taker_ratio"]
        taker_vals = list(taker_sources.values())
        taker_ratio = statistics.median(taker_vals) if taker_vals else 0.0

        # ── L/S Ratio ──
        ls_sources = {}
        for key, data in results.items():
            if key.startswith("flow_") and "_ls" in key and "ls_ratio" in data:
                label = key.split("_", 1)[1]
                ls_sources[label] = data["ls_ratio"]
        ls_vals = list(ls_sources.values())
        ls_ratio = statistics.median(ls_vals) if ls_vals else 0.0
        ls_extreme = ls_ratio > 1.38 or ls_ratio < 0.72 if ls_ratio > 0 else False

        # ── Book Depth ──
        book_imbalance = {}
        for key, data in results.items():
            if key.startswith("depth_") and "book_imbalance" in data:
                exchange = key.split("_", 1)[1]
                book_imbalance[exchange] = data["book_imbalance"]

        # ── DVOL (exclusively from _fetch_dvol_deribit in depth slot) ──
        dvol = 0.0
        dvol_key = "depth_deribit_dvol"
        if dvol_key in results and results[dvol_key].get("dvol", 0) > 0:
            dvol = results[dvol_key]["dvol"]
        dvol_change_5m = self._history.get_delta("dvol", 300)

        return MarketSnapshot(
            timestamp=now,
            symbol=symbol,
            price=price,
            price_sources=price_sources,
            price_divergence=price_div,
            funding_premium=funding_premium,
            funding_rates=funding_rates,
            funding_agg=funding_agg,
            funding_divergence=funding_div,
            oi_usd=oi_sources,
            oi_total=oi_total,
            oi_delta_5m=oi_delta_5m,
            taker_buy_sell_ratio=taker_ratio,
            taker_sources=taker_sources,
            ls_ratio=ls_ratio,
            ls_sources=ls_sources,
            ls_extreme=ls_extreme,
            dvol=dvol,
            dvol_change_5m=dvol_change_5m,
            book_imbalance=book_imbalance,
            sources_responded=sources_ok,
            sources_total=sources_total,
            fetch_ms=fetch_ms,
            slot_timings=slot_timings,
        )

    # ─── Background Fetch ───

    def start_background(self, symbol: str = "BTCUSDT", interval_sec: float = 10):
        """Start background fetch loop. Non-blocking."""
        if self._bg_thread and self._bg_thread.is_alive():
            logger.warning("Background fetcher already running")
            return
        self._bg_stop.clear()
        self._bg_thread = threading.Thread(
            target=self._bg_loop, args=(symbol, interval_sec),
            daemon=True, name="market-data-fetcher",
        )
        self._bg_thread.start()
        logger.info("Background fetcher started: %s every %.1fs", symbol, interval_sec)

    def stop_background(self):
        self._bg_stop.set()
        if self._bg_thread:
            self._bg_thread.join(timeout=5)

    def _bg_loop(self, symbol: str, interval_sec: float):
        while not self._bg_stop.is_set():
            try:
                snap = self.run_cycle(symbol)
                logger.debug(
                    "Snapshot: %s $%.0f | %d/%d sources | %.0fms",
                    symbol, snap.price, snap.sources_responded,
                    snap.sources_total, snap.fetch_ms,
                )
            except Exception as e:
                logger.error("Fetch cycle error: %s", e)
            self._bg_stop.wait(interval_sec)

    def shutdown(self):
        self.stop_background()
        self._pool.shutdown(wait=False)


# ════════════════════════════════════════
#  Module-level convenience
# ════════════════════════════════════════

_default_fetcher: StaggeredFetcher | None = None
_fetcher_lock = threading.Lock()


def get_fetcher() -> StaggeredFetcher:
    """Get or create the module-level fetcher singleton (thread-safe)."""
    global _default_fetcher
    if _default_fetcher is None:
        with _fetcher_lock:
            if _default_fetcher is None:
                _default_fetcher = StaggeredFetcher()
    return _default_fetcher


# ═══════════════════════════════════════
#  CLI Test
# ═══════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    fetcher = StaggeredFetcher()

    for sym in ["BTCUSDT", "ETHUSDT"]:
        print(f"\n{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")
        snap = fetcher.run_cycle(sym)
        print(f"  Price:    ${snap.price:,.2f}  ({len(snap.price_sources)} sources)")
        print(f"  Sources:  {snap.price_sources}")
        print(f"  Diverge:  {snap.price_divergence:.4%}")
        print(f"  Funding:  premium=${snap.funding_premium:,.2f}  agg={snap.funding_agg:.6f}")
        print(f"  Funding:  {snap.funding_rates}")
        print(f"  Fund div: {snap.funding_divergence:.6f}")
        print(f"  OI total: ${snap.oi_total:,.0f}")
        print(f"  OI:       {snap.oi_usd}")
        print(f"  Taker:    {snap.taker_buy_sell_ratio:.4f}  ({snap.taker_sources})")
        print(f"  L/S:      {snap.ls_ratio:.4f}  extreme={snap.ls_extreme}")
        print(f"  L/S:      {snap.ls_sources}")
        print(f"  DVOL:     {snap.dvol:.1f}")
        print(f"  Book:     {snap.book_imbalance}")
        print(f"  Meta:     {snap.sources_responded}/{snap.sources_total} in {snap.fetch_ms:.0f}ms")
        print(f"  Slots:    {snap.slot_timings}")
