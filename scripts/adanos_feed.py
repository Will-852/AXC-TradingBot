#!/usr/bin/env python3
"""
adanos_feed.py — Adanos Social Sentiment Feed (Reddit + X/Twitter)

Free tier: 250 req/month, 100 req/min, 30d history.
Design: daily poll (3 req/day = ~90/month), store as jsonl.

Usage:
  python3 scripts/adanos_feed.py              # one-shot fetch
  python3 scripts/adanos_feed.py --backfill   # pull 30d history
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
OUT_DIR = BASE_DIR / "shared" / "sentiment"
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_BASE = "https://api.adanos.org"
API_KEY = os.environ.get("ADANOS_API_KEY", "")

CRYPTO_SYMBOLS = ["BTC", "ETH"]
REQUEST_TIMEOUT = 15

logger = logging.getLogger("adanos_feed")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)


# ─── HTTP ───────────────────────────────────────────────

def _get(path: str, params: dict | None = None) -> dict | list | None:
    """GET request with API key auth. Returns parsed JSON or None on error."""
    if not API_KEY:
        logger.error("ADANOS_API_KEY not set")
        return None

    qs = ""
    if params:
        qs = "?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{API_BASE}{path}{qs}"

    req = Request(url)
    req.add_header("X-API-Key", API_KEY)
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        logger.error("HTTP %d for %s: %s", e.code, path, e.reason)
        return None
    except (URLError, TimeoutError) as e:
        logger.error("Request failed for %s: %s", path, e)
        return None


# ─── Data Fetchers ──────────────────────────────────────

def fetch_token_sentiment(symbol: str, days: int = 7) -> dict | None:
    """Fetch detailed sentiment for a single crypto token."""
    data = _get(f"/reddit/crypto/v1/token/{symbol}", {"days": days})
    if data is None:
        return None
    return {
        "source": "adanos",
        "platform": "reddit",
        "type": "token_sentiment",
        "symbol": symbol,
        "days": days,
        "ts": int(time.time()),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def fetch_market_sentiment(days: int = 1) -> dict | None:
    """Fetch overall crypto market sentiment."""
    data = _get("/reddit/crypto/v1/market-sentiment", {"days": days})
    if data is None:
        return None
    return {
        "source": "adanos",
        "platform": "reddit",
        "type": "market_sentiment",
        "days": days,
        "ts": int(time.time()),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def fetch_trending(days: int = 1, limit: int = 10) -> dict | None:
    """Fetch trending crypto tokens."""
    data = _get("/reddit/crypto/v1/trending", {"days": days, "limit": limit})
    if data is None:
        return None
    return {
        "source": "adanos",
        "platform": "reddit",
        "type": "trending",
        "days": days,
        "ts": int(time.time()),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


# ─── Storage ────────────────────────────────────────────

def _append_jsonl(filename: str, record: dict) -> None:
    """Append record to jsonl file (atomic-ish)."""
    path = OUT_DIR / filename
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    logger.info("Wrote to %s", path.name)


def _already_fetched_today(filename: str) -> bool:
    """Check if we already have a record for today (UTC)."""
    path = OUT_DIR / filename
    if not path.exists():
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("fetched_at", "").startswith(today):
                    return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


# ─── Main ───────────────────────────────────────────────

def daily_fetch() -> dict:
    """
    Daily fetch: BTC + ETH token sentiment + market sentiment.
    Skips if already fetched today. Returns summary.
    """
    results = {}

    # Token sentiment (7d window for trend context)
    for symbol in CRYPTO_SYMBOLS:
        fname = f"token_{symbol.lower()}.jsonl"
        if _already_fetched_today(fname):
            logger.info("Already fetched %s today, skipping", symbol)
            results[symbol] = "skipped"
            continue
        rec = fetch_token_sentiment(symbol, days=7)
        if rec:
            _append_jsonl(fname, rec)
            results[symbol] = "ok"
        else:
            results[symbol] = "failed"
        time.sleep(0.5)  # polite rate limiting

    # Market sentiment (1d snapshot)
    fname = "market_sentiment.jsonl"
    if _already_fetched_today(fname):
        logger.info("Already fetched market sentiment today, skipping")
        results["market"] = "skipped"
    else:
        rec = fetch_market_sentiment(days=1)
        if rec:
            _append_jsonl(fname, rec)
            results["market"] = "ok"
        else:
            results["market"] = "failed"

    return results


def backfill() -> None:
    """
    Pull 30d history for each token. Uses 1 request per token (days=30).
    Total: 2 tokens + 1 market = 3 requests.
    """
    logger.info("Backfilling 30d sentiment history...")

    for symbol in CRYPTO_SYMBOLS:
        rec = fetch_token_sentiment(symbol, days=30)
        if rec:
            _append_jsonl(f"token_{symbol.lower()}.jsonl", rec)
            logger.info("%s 30d backfill done", symbol)
        else:
            logger.error("%s backfill failed", symbol)
        time.sleep(0.5)

    rec = fetch_market_sentiment(days=30)
    if rec:
        _append_jsonl("market_sentiment.jsonl", rec)
        logger.info("Market sentiment 30d backfill done")

    # Also grab trending for context
    rec = fetch_trending(days=7, limit=20)
    if rec:
        _append_jsonl("trending.jsonl", rec)
        logger.info("Trending 7d backfill done")

    logger.info("Backfill complete (4 requests used)")


# ─── Read helpers (for other modules) ───────────────────

def latest_sentiment(symbol: str) -> dict | None:
    """Read the most recent sentiment record for a symbol."""
    path = OUT_DIR / f"token_{symbol.lower()}.jsonl"
    if not path.exists():
        return None
    last_line = None
    with open(path) as f:
        for line in f:
            if line.strip():
                last_line = line
    if last_line:
        return json.loads(last_line)
    return None


def latest_market_sentiment() -> dict | None:
    """Read the most recent market sentiment record."""
    path = OUT_DIR / "market_sentiment.jsonl"
    if not path.exists():
        return None
    last_line = None
    with open(path) as f:
        for line in f:
            if line.strip():
                last_line = line
    if last_line:
        return json.loads(last_line)
    return None


if __name__ == "__main__":
    if not API_KEY:
        print("Set ADANOS_API_KEY environment variable first")
        print("  export ADANOS_API_KEY=sk_live_...")
        sys.exit(1)

    if "--backfill" in sys.argv:
        backfill()
    else:
        results = daily_fetch()
        print(json.dumps(results, indent=2))
