"""
conv_1h/discovery.py — 1H market discovery via Gamma API.

Split from run_1h_live.py (2026-03-26).
🟢 SAFE: Read-only API calls, no trading impact.

Discovers active BTC/ETH/SOL 1H up-or-down markets by constructing
Polymarket slugs from coin name + ET datetime.
"""

import time
from datetime import datetime, timedelta

from polymarket.conv_1h.constants import _COIN_SLUGS, _ET, _GAMMA
from polymarket.conv_1h.data_feeds import _get_json
from polymarket.exchange.gamma_client import GammaClient

# _discover uses GammaClient.parse_market for structured field extraction


def _build_slug(coin: str, dt_et: datetime) -> str:
    name = _COIN_SLUGS.get(coin, "")
    if not name:
        return ""
    month = dt_et.strftime("%B").lower()
    day = str(dt_et.day)
    year = str(dt_et.year)
    hour = dt_et.strftime("%I").lstrip("0")
    ampm = dt_et.strftime("%p").lower()
    return f"{name}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et"


def _discover(gamma: GammaClient) -> list[dict]:
    """Find active BTC/ETH/SOL 1H markets."""
    results = []
    now_et = datetime.now(tz=_ET)
    now_s = int(time.time())
    base = now_et.replace(minute=0, second=0, microsecond=0)

    for i in range(3):
        ws = base + timedelta(hours=i)
        we = ws + timedelta(hours=1)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 300:
            continue
        for coin in ("BTC", "ETH", "SOL"):
            slug = _build_slug(coin, ws)
            if not slug:
                continue
            data = _get_json(f"{_GAMMA}/markets?slug={slug}")
            if not data or not isinstance(data, list) or not data:
                continue
            p = gamma.parse_market(data[0])
            cid = p.get("condition_id", "")
            up = p.get("yes_token_id", "")
            dn = p.get("no_token_id", "")
            if cid and up and dn:
                results.append({
                    "cid": cid, "title": p.get("title", ""),
                    "coin": coin, "slug": slug,
                    "up_tok": up, "dn_tok": dn,
                    "start_ms": ts * 1000, "end_ms": te * 1000,
                })
    return results
