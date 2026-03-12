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
    """Step 5: Calculate technical indicators for all pairs."""
    name = "calc_indicators"

    def run(self, ctx: CycleContext) -> CycleContext:
        for symbol in ctx.market_data:
            ctx.indicators[symbol] = {}

            for timeframe in [PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME]:
                try:
                    # Get params for this timeframe
                    if timeframe not in TIMEFRAME_PARAMS:
                        continue
                    params = TIMEFRAME_PARAMS[timeframe].copy()

                    # Apply product overrides
                    if symbol in PRODUCT_OVERRIDES:
                        params.update(PRODUCT_OVERRIDES[symbol])

                    # Fetch klines and calculate — route to correct exchange
                    df = fetch_klines(symbol, timeframe, KLINE_LIMIT, platform=_platform(symbol))
                    indicators = calc_indicators(df, params)

                    # Also get volume average (last 30 candles vs last candle)
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
                print(f"    {sym}: indicators for {tfs}")

        return ctx
