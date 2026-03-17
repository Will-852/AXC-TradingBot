"""
spread_analyzer.py — Order book spread + liquidity analysis

Before trading, check that:
1. Bid-ask spread is acceptable (唔會食太多 slippage)
2. Order book has enough depth for our intended bet size
3. Market is actively traded (唔係 stale book)

設計決定：
- 用 PolymarketClient.get_order_book() 拉 live book
- Dry-run 用 Gamma API 嘅 liquidity 數字做估計
"""

import logging
from typing import Optional

from ..core.context import PolyMarket, PolySignal

logger = logging.getLogger(__name__)


def analyze_spread(
    market: PolyMarket,
    exchange_client=None,
    max_spread_pct: float = 0.08,
    min_book_depth: float = 500.0,
) -> dict:
    """Analyze order book spread and depth for a market.

    Returns dict with:
        - spread: float (bid-ask spread)
        - spread_ok: bool
        - depth: float (total book depth in USDC)
        - depth_ok: bool
        - best_bid: float
        - best_ask: float
        - tradeable: bool (spread_ok AND depth_ok)
        - reason: str (if not tradeable)
    """
    token_id = market.yes_token_id
    if not token_id:
        return {
            "spread": 1.0, "spread_ok": False,
            "depth": 0, "depth_ok": False,
            "best_bid": 0, "best_ask": 0,
            "tradeable": False, "reason": "No token ID",
        }

    # Try live order book if exchange client available
    if exchange_client:
        try:
            return _analyze_live_book(
                token_id, exchange_client, max_spread_pct, min_book_depth
            )
        except Exception as e:
            logger.warning("Live book fetch failed for %s: %s", token_id[:16], e)

    # Fallback: estimate from Gamma API metadata
    return _estimate_from_metadata(market, max_spread_pct, min_book_depth)


def _analyze_live_book(
    token_id: str,
    exchange_client,
    max_spread_pct: float,
    min_book_depth: float,
) -> dict:
    """Analyze live order book from PolymarketClient."""
    book = exchange_client.get_order_book(token_id)
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    best_bid = float(bids[0]["price"]) if bids else 0.0
    best_ask = float(asks[0]["price"]) if asks else 1.0

    # Spread
    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2 if (best_bid + best_ask) > 0 else 0.5
    spread_pct = spread / mid if mid > 0 else 1.0
    spread_ok = spread_pct <= max_spread_pct

    # Depth (sum of size × price on both sides, top 5 levels)
    bid_depth = sum(float(b["price"]) * float(b["size"]) for b in bids[:5])
    ask_depth = sum(float(a["price"]) * float(a["size"]) for a in asks[:5])
    total_depth = bid_depth + ask_depth
    depth_ok = total_depth >= min_book_depth

    tradeable = spread_ok and depth_ok
    reason = ""
    if not spread_ok:
        reason = f"Spread {spread_pct:.1%} > {max_spread_pct:.1%}"
    if not depth_ok:
        reason += f"{'; ' if reason else ''}Depth ${total_depth:.0f} < ${min_book_depth:.0f}"

    return {
        "spread": spread_pct,
        "spread_ok": spread_ok,
        "depth": total_depth,
        "depth_ok": depth_ok,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "tradeable": tradeable,
        "reason": reason,
    }


def _estimate_from_metadata(
    market: PolyMarket,
    max_spread_pct: float,
    min_book_depth: float,
) -> dict:
    """Estimate spread/depth from Gamma API metadata (no live book).

    Used in dry-run mode or when exchange client unavailable.
    Gamma 唔提供 order book，所以用 liquidity 做粗略估計。
    """
    # Estimate spread from yes/no price gap
    if market.yes_price > 0 and market.no_price > 0:
        implied_sum = market.yes_price + market.no_price
        # In perfect market, yes + no = 1.0
        # Deviation from 1.0 suggests spread/vig
        spread_est = abs(implied_sum - 1.0)
    else:
        spread_est = 0.10  # conservative default

    spread_ok = spread_est <= max_spread_pct
    depth_ok = market.liquidity >= min_book_depth

    tradeable = spread_ok and depth_ok
    reason = ""
    if not spread_ok:
        reason = f"Est spread {spread_est:.1%} > {max_spread_pct:.1%}"
    if not depth_ok:
        reason += f"{'; ' if reason else ''}Liq ${market.liquidity:.0f} < ${min_book_depth:.0f}"

    return {
        "spread": spread_est,
        "spread_ok": spread_ok,
        "depth": market.liquidity,
        "depth_ok": depth_ok,
        "best_bid": market.yes_price - spread_est / 2,
        "best_ask": market.yes_price + spread_est / 2,
        "tradeable": tradeable,
        "reason": reason,
    }


def check_signal_tradeable(
    signal: PolySignal,
    market: PolyMarket,
    exchange_client=None,
    max_spread_pct: float = 0.08,
    min_book_depth: float = 500.0,
) -> tuple[bool, str]:
    """Quick check if a signal's market is tradeable.

    Returns (tradeable, reason).
    """
    analysis = analyze_spread(market, exchange_client, max_spread_pct, min_book_depth)
    return analysis["tradeable"], analysis.get("reason", "")
