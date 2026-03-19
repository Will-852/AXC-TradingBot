"""
market_scanner.py — Market scanning + filtering logic

Extracts the scan logic from ScanMarketsStep into a reusable module.
Can be used standalone for market exploration.
"""

import logging
from datetime import date, datetime, timedelta

from ..config.settings import (
    MAX_MARKETS_TO_SCAN, MIN_LIQUIDITY_USDC, MIN_VOLUME_24H,
    MIN_DAYS_TO_RESOLUTION, MAX_DAYS_TO_RESOLUTION,
    PRICE_FLOOR, PRICE_CEILING,
    CRYPTO_15M_MIN_LIQUIDITY, WEATHER_MIN_LIQUIDITY,
    WEATHER_MAX_LEAD_DAYS,
)
from ..config.categories import match_category, WEATHER_CITIES
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

    # Also fetch recent markets to catch low-liquidity series (15M/5M)
    # These rank too low by liquidity (~$14K) to appear in the main scan
    recent = gamma.get_recent_markets(limit=50)
    seen_ids = {m.get("conditionId") for m in raw_markets}
    for m in recent:
        if m.get("conditionId") not in seen_ids:
            raw_markets.append(m)
            seen_ids.add(m.get("conditionId"))

    if verbose:
        logger.info("Gamma API returned %d markets (incl. recent)", len(raw_markets))

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
            event_id=parsed.get("event_id", ""),
            event_slug=parsed.get("event_slug", ""),
            tick_size=parsed.get("tick_size", 0.01),
            min_order_size=parsed.get("min_order_size", 5),
            spread=abs((parsed["yes_price"] + parsed["no_price"]) - 1.0)
                   if parsed["yes_price"] > 0 and parsed["no_price"] > 0
                   else 0.0,
        )
        scanned.append(market)

        # Quality filters
        if not _passes_quality_filter(market, verbose):
            continue

        # 5M markets: scan only (price reference), do NOT trade
        # Only 15M windows are backtested + calibrated
        if market.category == "crypto_15m" and "5m" in market.slug:
            continue

        filtered.append(market)

    # ── Weather: targeted event slug scan (bypasses liquidity ranking) ──
    weather_scanned, weather_filtered = _scan_weather_events(gamma, verbose)
    # De-duplicate by condition_id (avoid double-counting if also found in general scan)
    existing_ids = {m.condition_id for m in scanned}
    for m in weather_scanned:
        if m.condition_id not in existing_ids:
            scanned.append(m)
    existing_ids = {m.condition_id for m in filtered}
    for m in weather_filtered:
        if m.condition_id not in existing_ids:
            filtered.append(m)

    return scanned, filtered


# ─── Weather Event Slug Scanner ───

# City name → Polymarket event slug segment
# Live scope: all Polymarket weather cities (same process, zero AI cost, more diversification)
_WEATHER_SLUGS = {
    # Asia
    "tokyo": "tokyo", "hong kong": "hong-kong", "shanghai": "shanghai",
    "seoul": "seoul", "taipei": "taipei", "singapore": "singapore",
    # Europe
    "paris": "paris", "london": "london", "ankara": "ankara",
    "milan": "milan", "madrid": "madrid", "munich": "munich",
    # Americas
    "seattle": "seattle", "atlanta": "atlanta", "chicago": "chicago",
    "dallas": "dallas", "miami": "miami", "toronto": "toronto",
    "new york": "new-york-city", "sao paulo": "sao-paulo",
    "buenos aires": "buenos-aires",
    # Oceania
    "wellington": "wellington", "sydney": "sydney",
}


def _scan_weather_events(
    gamma: GammaClient, verbose: bool = False,
) -> tuple[list[PolyMarket], list[PolyMarket]]:
    """Scan weather markets via event slug pattern.

    Polymarket event slug: highest-temperature-in-{city}-on-{month}-{day}-{year}
    Each event contains ~11 bucket markets (e.g., 10°C or below, 11°C, ..., 15°C or above).
    """
    today = date.today()
    scanned = []
    filtered = []

    for city_name, slug in _WEATHER_SLUGS.items():
        if city_name not in WEATHER_CITIES:
            continue
        for delta in range(0, WEATHER_MAX_LEAD_DAYS + 1):
            d = today + timedelta(days=delta)
            month = d.strftime("%B").lower()
            event_slug = f"highest-temperature-in-{slug}-on-{month}-{d.day}-{d.year}"

            event = gamma.get_event_by_slug(event_slug)
            if not event:
                continue

            raw_markets = event.get("markets", [])
            for raw in raw_markets:
                if not raw.get("active", False):
                    continue
                parsed = gamma.parse_market(raw)
                market = PolyMarket(
                    condition_id=parsed["condition_id"],
                    title=parsed["title"],
                    description=parsed.get("description", ""),
                    category="weather",
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
                    event_id=parsed.get("event_id", ""),
                    event_slug=event_slug,
                    tick_size=parsed.get("tick_size", 0.01),
                    min_order_size=parsed.get("min_order_size", 5),
                    spread=abs((parsed["yes_price"] + parsed["no_price"]) - 1.0)
                           if parsed["yes_price"] > 0 and parsed["no_price"] > 0
                           else 0.0,
                )
                scanned.append(market)
                if _passes_quality_filter(market, verbose):
                    filtered.append(market)

    if verbose and scanned:
        logger.info("Weather slug scan: %d scanned, %d filtered", len(scanned), len(filtered))

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

    # Weather markets: low liquidity is normal (per-bucket $200-$1500)
    if market.category == "weather":
        if market.liquidity < WEATHER_MIN_LIQUIDITY:
            return False
        if not market.yes_token_id:
            return False
        return True  # skip price range + days-to-resolution for weather

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
