"""
market_scanner.py — Market scanning + filtering logic

Extracts the scan logic from ScanMarketsStep into a reusable module.
Can be used standalone for market exploration.
"""

import logging
from datetime import datetime

from ..config.settings import (
    MAX_MARKETS_TO_SCAN, MIN_LIQUIDITY_USDC, MIN_VOLUME_24H,
    MIN_DAYS_TO_RESOLUTION, MAX_DAYS_TO_RESOLUTION,
    PRICE_FLOOR, PRICE_CEILING,
    CRYPTO_15M_MIN_LIQUIDITY,
)
from ..config.categories import match_category
from ..core.context import PolyMarket
from ..exchange.gamma_client import GammaClient

logger = logging.getLogger(__name__)


def scan_markets(
    gamma: GammaClient,
    limit: int = MAX_MARKETS_TO_SCAN,
    verbose: bool = False,
) -> tuple[list[PolyMarket], list[PolyMarket]]:
    """Scan Gamma API and return (all_matched, quality_filtered) markets.

    Returns:
        Tuple of (scanned_markets, filtered_markets) where:
        - scanned_markets: all markets matching a category
        - filtered_markets: markets passing quality filters
    """
    raw_markets = gamma.get_markets(
        limit=limit,
        active=True,
        order="liquidity",
        ascending=False,
    )

    if verbose:
        logger.info("Gamma API returned %d markets", len(raw_markets))

    scanned = []
    filtered = []

    for raw in raw_markets:
        parsed = gamma.parse_market(raw)

        # Category match (title only)
        category = match_category(parsed["title"])
        if not category:
            continue

        # Build PolyMarket
        market = PolyMarket(
            condition_id=parsed["condition_id"],
            title=parsed["title"],
            description=parsed.get("description", ""),
            category=category,
            end_date=parsed["end_date"],
            yes_token_id=parsed["yes_token_id"],
            no_token_id=parsed["no_token_id"],
            yes_price=parsed["yes_price"],
            no_price=parsed["no_price"],
            volume=parsed["volume"],
            volume_24h=parsed["volume_24h"],
            liquidity=parsed["liquidity"],
            slug=parsed.get("slug", ""),
            outcomes=parsed.get("outcomes", []),
            outcome_prices=parsed.get("outcome_prices", {}),
            outcome_tokens=parsed.get("outcome_tokens", {}),
            neg_risk=parsed.get("neg_risk", False),
            tick_size=parsed.get("tick_size", 0.01),
            min_order_size=parsed.get("min_order_size", 5),
        )
        scanned.append(market)

        # Quality filters
        if not _passes_quality_filter(market, verbose):
            continue

        filtered.append(market)

    return scanned, filtered


def _passes_quality_filter(market: PolyMarket, verbose: bool = False) -> bool:
    """Check if a market passes all quality filters."""
    # 15M markets: skip days-to-resolution, use lower liquidity threshold
    if market.category == "crypto_15m":
        if market.liquidity < CRYPTO_15M_MIN_LIQUIDITY:
            return False
        if not market.yes_token_id:
            return False
        return True

    # Liquidity
    if market.liquidity < MIN_LIQUIDITY_USDC:
        return False

    # Price range (avoid extreme probabilities)
    if market.yes_price < PRICE_FLOOR or market.yes_price > PRICE_CEILING:
        return False

    # Must have token ID
    if not market.yes_token_id:
        return False

    # Days to resolution
    if market.end_date:
        try:
            end = datetime.strptime(market.end_date, "%Y-%m-%d")
            days_left = (end - datetime.now()).days
            if days_left < MIN_DAYS_TO_RESOLUTION:
                return False
            if days_left > MAX_DAYS_TO_RESOLUTION:
                return False
        except (ValueError, TypeError):
            pass  # can't parse → keep

    return True


# ─── CLI Test ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gamma = GammaClient()
    scanned, filtered = scan_markets(gamma, limit=100, verbose=True)
    print(f"\nScanned: {len(scanned)}, Filtered: {len(filtered)}")
    for m in filtered:
        print(f"  [{m.category}] {m.title[:55]}  Yes:{m.yes_price:.3f}  Liq:${m.liquidity:,.0f}")
