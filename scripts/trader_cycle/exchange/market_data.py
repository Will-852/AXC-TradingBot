"""
market_data.py — Public market data fetching
Imports from indicator_calc.py for indicator calculation
"""

from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.error

from ..config.settings import ASTER_FAPI, API_TIMEOUT, PAIRS, PAIR_PREFIX, KLINE_LIMIT
from ..config.settings import PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME
from ..core.context import CycleContext, MarketSnapshot
from ..core.pipeline import RecoverableError

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


class FetchMarketDataStep:
    """Step 4: Fetch live market data for all pairs."""
    name = "fetch_market_data"

    def run(self, ctx: CycleContext) -> CycleContext:
        success_count = 0

        for symbol in PAIRS:
            prefix = PAIR_PREFIX[symbol]

            # Ticker
            ticker = _fetch_json(f"{ASTER_FAPI}/ticker/24hr?symbol={symbol}")
            if "error" in ticker:
                ctx.warnings.append(f"{symbol} ticker failed: {ticker['error']}")
                continue

            # Funding
            funding = _fetch_json(f"{ASTER_FAPI}/premiumIndex?symbol={symbol}")

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

                    # Fetch klines and calculate
                    df = fetch_klines(symbol, timeframe, KLINE_LIMIT)
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
