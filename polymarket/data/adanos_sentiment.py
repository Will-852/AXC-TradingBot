"""
adanos_sentiment.py — Adanos Finance Sentiment API client.

Provides Reddit crypto sentiment + buzz scores as a soft directional signal.
API docs: https://api.adanos.org/docs

Key data:
  buzz_score (0-100): attention/popularity, higher = more active
  sentiment_score (-1 to +1): directional bullish/bearish
  trend: rising / falling / stable
  bullish_pct / bearish_pct: share of directional mentions

Usage:
  from polymarket.data.adanos_sentiment import get_crypto_sentiment, get_crypto_trending

  btc = get_crypto_sentiment("BTC", days=1)
  # → {"buzz_score": 78.1, "sentiment_score": -0.003, "trend": "falling", ...}

  trending = get_crypto_trending(limit=10)
  # → [{"symbol": "BTC", "buzz_score": 78.1, ...}, ...]
"""
import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_BASE = "https://api.adanos.org"
_API_KEY = ""
_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 300  # 5 min cache (sentiment doesn't change fast)

_ENV_PATH = os.path.join(
    os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading")),
    "secrets", ".env"
)


def _load_key() -> str:
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ADANOS_API_KEY="):
                    _API_KEY = line.split("=", 1)[1]
                    return _API_KEY
    return ""


def _get(path: str, params: dict | None = None, timeout: int = 10) -> dict | None:
    """GET request with auth + caching."""
    key = _load_key()
    if not key:
        logger.warning("Adanos API key not found in secrets/.env")
        return None

    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{_BASE}{path}" + (f"?{query}" if query else "")

    cache_key = url
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key][1] < _CACHE_TTL:
        return _CACHE[cache_key][0]

    # Monthly limit exhausted → stop trying until next month
    if _CACHE.get("_monthly_exhausted", (False, 0))[0]:
        exhaust_ts = _CACHE["_monthly_exhausted"][1]
        if now - exhaust_ts < 86400:  # retry once per day
            return None

    try:
        req = Request(url, headers={
            "X-API-Key": key,
            "User-Agent": "AXC-Trading/1.0",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        _CACHE[cache_key] = (data, now)
        return data
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        if "limit exceeded" in body.lower() or e.code == 429:
            logger.warning("Adanos monthly limit exhausted — pausing until reset")
            _CACHE["_monthly_exhausted"] = (True, now)
        elif e.code == 403:
            logger.warning("Adanos 403: %s", body[:100])
        else:
            logger.debug("Adanos HTTP %d: %s", e.code, path)
        return None
    except (URLError, TimeoutError, OSError) as e:
        logger.debug("Adanos fetch failed: %s", e)
        return None


# ═══════════════════════════════════════
#  Reddit Crypto Endpoints
# ═══════════════════════════════════════

def get_crypto_sentiment(symbol: str, days: int = 1) -> dict | None:
    """Get sentiment for a specific crypto token.

    Returns: {buzz_score, sentiment_score, trend, bullish_pct, bearish_pct,
              trend_history, daily_trend, ...}
    """
    return _get(f"/reddit/crypto/v1/token/{symbol.upper()}", {"days": days})


def get_crypto_trending(limit: int = 10, days: int = 1) -> list | None:
    """Get trending crypto tokens by buzz score.

    Returns: [{symbol, buzz_score, sentiment_score, trend, ...}, ...]
    """
    data = _get("/reddit/crypto/v1/trending", {"limit": limit, "days": days})
    if data and isinstance(data, dict):
        return data.get("trending", data.get("data", []))
    return data if isinstance(data, list) else None


def get_crypto_market_sentiment(days: int = 1) -> dict | None:
    """Get overall crypto market sentiment from Reddit.

    Returns: {overall_sentiment, buzz_index, bullish_pct, bearish_pct,
              top_drivers, ...}
    """
    return _get("/reddit/crypto/v1/market-sentiment", {"days": days})


def get_crypto_compare(symbols: list[str], days: int = 7) -> dict | None:
    """Compare multiple crypto tokens side by side (max 10).

    Returns: {assets: [{symbol, buzz_score, sentiment_score, ...}, ...]}
    """
    tickers = ",".join(s.upper() for s in symbols[:10])
    return _get("/reddit/crypto/v1/compare", {"tickers": tickers, "days": days})


# ═══════════════════════════════════════
#  Polymarket Stocks Endpoints (stocks only, no crypto)
# ═══════════════════════════════════════

def get_polymarket_trending(limit: int = 10) -> list | None:
    """Get trending stocks on Polymarket by activity.

    Returns: [{symbol, buzz_score, sentiment_score, trade_count, liquidity, ...}]
    """
    data = _get("/polymarket/stocks/v1/trending", {"limit": limit})
    if data and isinstance(data, dict):
        return data.get("trending", data.get("data", []))
    return data if isinstance(data, list) else None


def get_polymarket_sentiment() -> dict | None:
    """Get aggregate Polymarket directional positioning across stocks."""
    return _get("/polymarket/stocks/v1/market-sentiment")


# ═══════════════════════════════════════
#  Convenience: signal-ready summary
# ═══════════════════════════════════════

def crypto_signal_summary(coins: list[str] | None = None) -> dict:
    """Return a dict of {coin: {buzz, sentiment, trend}} for use as signal input.

    >>> crypto_signal_summary(["BTC", "ETH", "SOL"])
    {"BTC": {"buzz": 78.1, "sentiment": -0.003, "trend": "falling"}, ...}
    """
    coins = coins or ["BTC", "ETH", "SOL", "XRP"]
    result = {}
    for coin in coins:
        data = get_crypto_sentiment(coin, days=1)
        if data:
            result[coin] = {
                "buzz": data.get("buzz_score", 0),
                "sentiment": data.get("sentiment_score"),
                "trend": data.get("trend", "unknown"),
                "bullish_pct": data.get("bullish_pct", 0),
                "bearish_pct": data.get("bearish_pct", 0),
            }
        else:
            result[coin] = {"buzz": 0, "sentiment": None, "trend": "unknown",
                            "bullish_pct": 0, "bearish_pct": 0}
    return result
