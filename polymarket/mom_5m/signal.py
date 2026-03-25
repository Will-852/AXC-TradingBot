"""
mom_5m/signal.py — W4 momentum signal and 5M market discovery.

Split from run_5m_live.py (2026-03-26).
🟡 VERIFY: Signal computation affects entry decisions but doesn't place orders.
"""

import logging
import math
import time

from polymarket.mom_5m.config import (
    COIN_CONFIG, CoinConfig, _GAMMA, _WINDOW_S,
)
from polymarket.mom_5m.data import _get_json, coin_price, open_at
from polymarket.exchange.gamma_client import GammaClient

logger = logging.getLogger(__name__)


def w4_signal(window_start_ms: int, coin: str,
              cfg: CoinConfig) -> tuple[str, float, float]:
    """W4 momentum signal: log return from window open to now.

    Returns (direction, magnitude_bps, log_return).
    direction: 'UP', 'DOWN', 'WAIT' (too early), 'SKIP' (below threshold).

    Key difference from 15M: T+15s delay (not T+300s).
    """
    now_ms = int(time.time() * 1000)
    elapsed_s = (now_ms - window_start_ms) / 1000
    if elapsed_s < cfg.delay_s:
        return "WAIT", 0.0, 0.0

    p0 = open_at(window_start_ms, coin)
    if p0 <= 0:
        return "SKIP", 0.0, 0.0

    p_now = coin_price(coin)
    if p_now <= 0:
        return "SKIP", 0.0, 0.0

    log_ret = math.log(p_now / p0)
    mag_bps = abs(log_ret) * 10000

    if mag_bps < cfg.threshold_bps:
        return "SKIP", mag_bps, log_ret

    direction = "UP" if log_ret > 0 else "DOWN"

    # Contrarian mode: flip direction (SOL pattern)
    if cfg.contrarian:
        direction = "DOWN" if direction == "UP" else "UP"

    return direction, mag_bps, log_ret


def discover_5m(gamma: GammaClient) -> list[dict]:
    """Find 5M markets for current + next 4 windows via slug.

    Slug format: {coin}-updown-5m-{unix_timestamp}
    Windows: every 300s (5 min), 24/7 continuous.

    Returns list of dicts: {cid, title, coin, slug, up_tok, dn_tok, start_ms, end_ms}
    """
    results = []
    now_s = int(time.time())

    for i in range(5):
        # Floor to 5-min boundary, then offset by i windows
        window_start = (now_s // _WINDOW_S) * _WINDOW_S + i * _WINDOW_S
        window_end = window_start + _WINDOW_S

        # Skip windows that ended > 120s ago
        if now_s > window_end + 120:
            continue

        for coin, cfg in COIN_CONFIG.items():
            slug = f"{cfg.slug_prefix}-updown-5m-{window_start}"
            try:
                data = _get_json(f"{_GAMMA}/markets?slug={slug}", timeout=5)
            except Exception as e:
                logger.warning("Gamma slug fetch failed %s: %s", slug, e)
                continue

            if not data or not isinstance(data, list) or not data:
                continue

            parsed = gamma.parse_market(data[0])
            cid = parsed.get("condition_id", "")
            up_tok = parsed.get("yes_token_id", "")
            dn_tok = parsed.get("no_token_id", "")

            # Sanity: outcomes must be [UP/Yes, DOWN/No]
            outcomes = parsed.get("outcomes", [])
            if outcomes and isinstance(outcomes, list) and len(outcomes) >= 2:
                if outcomes[0].lower() not in ("up", "yes"):
                    logger.error("OUTCOME SWAPPED %s: %s", slug, outcomes)
                    continue

            if cid and up_tok and dn_tok:
                results.append({
                    "cid": cid,
                    "title": parsed.get("title", ""),
                    "coin": coin,
                    "slug": slug,
                    "up_tok": up_tok,
                    "dn_tok": dn_tok,
                    "start_ms": window_start * 1000,
                    "end_ms": window_end * 1000,
                })

    return results
