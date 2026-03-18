"""
market_data.py — Public market data fetching
Imports from indicator_calc.py for indicator calculation
Routes to Aster or Binance API per pair based on params.py symbol lists.
"""

from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.error

import logging
import time as _time

from ..config.settings import (
    ASTER_FAPI, BINANCE_FAPI, API_TIMEOUT, PAIRS, PAIR_PREFIX, KLINE_LIMIT,
    PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME, ASTER_SYMBOLS,
)
from ..core.context import CycleContext, MarketSnapshot
from ..core.pipeline import RecoverableError

logger = logging.getLogger(__name__)

# ─── Data Freshness Constants ───
TICKER_MAX_AGE_SEC = 120   # ticker data older than 2min = stale
TICKER_MIN_PRICE = 0.0     # price must be > 0
TICKER_MIN_VOLUME = 0.0    # volume must be >= 0 (0 = allow, we check ratio elsewhere)

# Import from indicator_calc.py
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from indicator_calc import fetch_klines, calc_indicators, TIMEFRAME_PARAMS, PRODUCT_OVERRIDES

# ─── Indicator Cache (from indicator_engine.py) ───
_INDICATOR_CACHE_PATH = os.path.join(
    os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading")),
    "shared", "indicator_cache.json"
)
_CACHE_MAX_AGE_SEC = 600  # 10 minutes — stale after this


def _read_indicator_cache() -> dict | None:
    """Read indicator_cache.json if it exists and is fresh. Returns None if stale/missing."""
    try:
        if not os.path.exists(_INDICATOR_CACHE_PATH):
            return None
        with open(_INDICATOR_CACHE_PATH) as f:
            cache = json.load(f)
        meta = cache.get("_meta", {})
        last_update = meta.get("last_update", "")
        if not last_update:
            return None
        from datetime import datetime, timezone
        updated_at = datetime.fromisoformat(last_update)
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age > _CACHE_MAX_AGE_SEC:
            logger.info("Indicator cache stale (%.0fs old), falling back to REST", age)
            return None
        return cache
    except Exception as exc:
        logger.warning("Failed to read indicator cache: %s", exc)
        return None


def _fetch_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    """Fetch JSON from URL. Returns dict with 'error' key on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenClaw-TraderCycle/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _api_base(symbol: str) -> str:
    """Route to Aster or Binance API based on symbol's exchange."""
    if symbol in ASTER_SYMBOLS:
        return ASTER_FAPI
    return BINANCE_FAPI


def _platform(symbol: str) -> str:
    """Return platform name for fetch_klines()."""
    if symbol in ASTER_SYMBOLS:
        return "aster"
    return "binance"


def validate_ticker(symbol: str, ticker: dict) -> tuple[bool, str]:
    """
    Validate ticker data freshness and sanity.
    Returns (is_valid, reason_if_invalid).

    Checks:
      - price > 0
      - closeTime exists and is within TICKER_MAX_AGE_SEC
      - No error in response
    """
    if "error" in ticker:
        return False, f"API error: {ticker['error']}"

    price = float(ticker.get("lastPrice", 0))
    if price <= TICKER_MIN_PRICE:
        return False, f"price={price} (must be > 0)"

    # Check data age via closeTime (ms epoch from exchange)
    close_time_ms = ticker.get("closeTime", 0)
    if close_time_ms:
        age_sec = (_time.time() * 1000 - float(close_time_ms)) / 1000
        if age_sec > TICKER_MAX_AGE_SEC:
            return False, f"stale data: {age_sec:.0f}s old (max {TICKER_MAX_AGE_SEC}s)"

    return True, ""


class FetchMarketDataStep:
    """Step 4: Fetch live market data for all pairs."""
    name = "fetch_market_data"

    def run(self, ctx: CycleContext) -> CycleContext:
        success_count = 0

        for symbol in PAIRS:
            prefix = PAIR_PREFIX[symbol]
            base = _api_base(symbol)

            # Ticker
            ticker = _fetch_json(f"{base}/ticker/24hr?symbol={symbol}")

            # Data freshness validation (pair-level)
            valid, reason = validate_ticker(symbol, ticker)
            if not valid:
                ctx.warnings.append(f"{symbol} skipped: {reason}")
                logger.warning(f"[{symbol}] data freshness: {reason}")
                continue

            # Funding
            funding = _fetch_json(f"{base}/premiumIndex?symbol={symbol}")

            snap = MarketSnapshot(
                symbol=symbol,
                price=float(ticker.get("lastPrice", 0)),
                price_change_24h_pct=float(ticker.get("priceChangePercent", 0)),
                volume_24h=float(ticker.get("quoteVolume", 0)),
                funding_rate=float(funding.get("lastFundingRate", 0)) if "error" not in funding else 0,
                mark_price=float(funding.get("markPrice", 0)) if "error" not in funding else 0,
                index_price=float(funding.get("indexPrice", 0)) if "error" not in funding else 0,
            )
            ctx.market_data[symbol] = snap
            success_count += 1

        if success_count == 0:
            raise RecoverableError("All market data API calls failed")

        if ctx.verbose:
            for sym, snap in ctx.market_data.items():
                print(f"    {sym}: ${snap.price:.2f} ({snap.price_change_24h_pct:+.1f}%) funding={snap.funding_rate:.6f}")

        return ctx


class CalcIndicatorsStep:
    """Step 5: Calculate technical indicators for all pairs.

    Fast path: read from indicator_cache.json (written by indicator_engine.py).
    Slow path: REST fetch klines + calc_indicators (original behavior, fallback).
    """
    name = "calc_indicators"

    def run(self, ctx: CycleContext) -> CycleContext:
        # ── Fast path: read from indicator_engine cache ──
        cache = _read_indicator_cache()
        if cache:
            cache_hit = False
            for symbol in ctx.market_data:
                sym_cache = cache.get(symbol, {})
                if sym_cache:
                    ctx.indicators[symbol] = {}
                    for timeframe in [PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME]:
                        tf_data = sym_cache.get(timeframe)
                        if tf_data and tf_data.get("price") is not None:
                            ctx.indicators[symbol][timeframe] = tf_data
                            cache_hit = True

            if cache_hit and ctx.indicators:
                meta = cache.get("_meta", {})
                if ctx.verbose:
                    age = meta.get("engine_uptime_s", "?")
                    src = meta.get("source", "?")
                    for sym in ctx.indicators:
                        tfs = list(ctx.indicators[sym].keys())
                        print(f"    {sym}: indicators from cache ({src}, uptime={age}s) for {tfs}")
                logger.info("CalcIndicatorsStep: cache hit (source=%s)", meta.get("source"))
                return ctx
            else:
                logger.info("CalcIndicatorsStep: cache incomplete, falling back to REST")

        # ── Slow path: REST fetch + calc (original behavior) ──
        for symbol in ctx.market_data:
            ctx.indicators[symbol] = {}

            for timeframe in [PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME]:
                try:
                    if timeframe not in TIMEFRAME_PARAMS:
                        continue
                    params = TIMEFRAME_PARAMS[timeframe].copy()

                    if symbol in PRODUCT_OVERRIDES:
                        params.update(PRODUCT_OVERRIDES[symbol])

                    df = fetch_klines(symbol, timeframe, KLINE_LIMIT, platform=_platform(symbol))
                    indicators = calc_indicators(df, params)

                    if len(df) >= 30:
                        avg_vol = df["volume"].tail(30).mean()
                        current_vol = df["volume"].iloc[-1]
                        indicators["volume_ratio"] = (
                            current_vol / avg_vol if avg_vol > 0 else 1.0
                        )
                    else:
                        indicators["volume_ratio"] = 1.0

                    ctx.indicators[symbol][timeframe] = indicators

                except Exception as e:
                    ctx.warnings.append(f"{symbol} {timeframe} indicators failed: {e}")

        if not ctx.indicators:
            raise RecoverableError("No indicators calculated for any pair")

        if ctx.verbose:
            for sym in ctx.indicators:
                tfs = list(ctx.indicators[sym].keys())
                print(f"    {sym}: indicators for {tfs} (REST fallback)")

        return ctx
