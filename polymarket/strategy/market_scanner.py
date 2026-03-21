"""
market_scanner.py — Market scanning + filtering logic

Extracts the scan logic from ScanMarketsStep into a reusable module.
Can be used standalone for market exploration.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone

from ..config.settings import (
    MAX_MARKETS_TO_SCAN, MIN_LIQUIDITY_USDC, MIN_VOLUME_24H,
    MIN_DAYS_TO_RESOLUTION, MAX_DAYS_TO_RESOLUTION,
    PRICE_FLOOR, PRICE_CEILING,
    CRYPTO_15M_MIN_LIQUIDITY,
    CRYPTO_15M_ENABLED_COINS,
)
from ..config.categories import match_category, CRYPTO_15M_COINS
from ..core.context import PolyMarket
from ..exchange.gamma_client import GammaClient

logger = logging.getLogger(__name__)

# ─── 15M Direct Slug Discovery ───
# Markets are continuous (24/7), slug = {coin}-updown-15m-{unix_timestamp}
# Timestamp aligned to 15-min boundaries (900s)
_15M_WINDOW_S = 900
# Coin → slug prefix mapping
_15M_SLUG_PREFIX = {
    "bitcoin": "btc",
}
# How many windows to fetch: current (running) + next (upcoming)
_15M_LOOKAHEAD = 2


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
        # Note: check "-5m-" not "5m" to avoid matching "15m"
        if market.category == "crypto_15m" and "-5m-" in market.slug:
            continue

        filtered.append(market)

    # ── 15M: direct slug scan (continuous markets, bypasses search) ──
    crypto_scanned, crypto_filtered = _scan_15m_direct(gamma, verbose)
    existing_ids = {m.condition_id for m in scanned}
    for m in crypto_scanned:
        if m.condition_id not in existing_ids:
            scanned.append(m)
    existing_ids = {m.condition_id for m in filtered}
    for m in crypto_filtered:
        if m.condition_id not in existing_ids:
            filtered.append(m)

    return scanned, filtered


# ─── 15M Direct Slug Scanner ───

def _scan_15m_direct(
    gamma: GammaClient, verbose: bool = False,
) -> tuple[list[PolyMarket], list[PolyMarket]]:
    """Fetch current + next 15M windows by direct slug calculation.

    設計決定：15M 市場係 continuous（24/7 每 15 min），slug = {coin}-updown-15m-{unix_ts}。
    唔用 search — 直接計算 timestamp → construct slug → fetch event。
    """
    scanned: list[PolyMarket] = []
    filtered: list[PolyMarket] = []

    now_ts = int(time.time())
    current_window = now_ts // _15M_WINDOW_S * _15M_WINDOW_S

    for coin in CRYPTO_15M_ENABLED_COINS:
        prefix = _15M_SLUG_PREFIX.get(coin)
        if not prefix:
            continue

        for offset in range(0, _15M_LOOKAHEAD):
            window_ts = current_window + offset * _15M_WINDOW_S
            slug = f"{prefix}-updown-15m-{window_ts}"

            try:
                event = gamma.get_event_by_slug(slug)
            except Exception as e:
                logger.debug("15M slug fetch failed (%s): %s", slug, e)
                continue

            if not event:
                continue

            # Parse markets within this event
            raw_markets = event.get("markets", [])
            if not raw_markets:
                continue

            for raw in raw_markets:
                parsed = gamma.parse_market(raw)
                category = match_category(parsed["title"])
                if category != "crypto_15m":
                    continue

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
                    slug=parsed.get("slug", slug),
                    outcomes=parsed.get("outcomes", []),
                    outcome_prices=parsed.get("outcome_prices", {}),
                    outcome_tokens=parsed.get("outcome_tokens", {}),
                    neg_risk=parsed.get("neg_risk", False),
                    event_id=parsed.get("event_id", ""),
                    event_slug=parsed.get("event_slug", slug),
                    tick_size=parsed.get("tick_size", 0.01),
                    min_order_size=parsed.get("min_order_size", 5),
                    spread=abs((parsed["yes_price"] + parsed["no_price"]) - 1.0)
                           if parsed["yes_price"] > 0 and parsed["no_price"] > 0
                           else 0.0,
                )
                scanned.append(market)

                if _passes_quality_filter(market, verbose):
                    # 5M block doesn't apply here (we only construct 15M slugs)
                    filtered.append(market)

            if verbose:
                logger.info("15M direct: %s → %d markets", slug,
                            len([m for m in scanned if slug in m.slug]))

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


def markets_from_cache(cached: list[dict]) -> list[PolyMarket]:
    """Rebuild PolyMarket list from state cache dicts.

    向後兼容：old cache entries missing new fields get safe defaults via .get().
    """
    return [
        PolyMarket(
            condition_id=m.get("condition_id", ""),
            title=m.get("title", ""),
            category=m.get("category", ""),
            yes_price=float(m.get("yes_price", 0)),
            no_price=float(m.get("no_price", 0)),
            liquidity=float(m.get("liquidity", 0)),
            volume_24h=float(m.get("volume_24h", 0)),
            end_date=m.get("end_date", ""),
            event_id=m.get("event_id", ""),
            neg_risk=m.get("neg_risk", False),
            yes_token_id=m.get("yes_token_id", ""),
            no_token_id=m.get("no_token_id", ""),
            slug=m.get("slug", ""),
            spread=float(m.get("spread", 0.0)),
            description=m.get("description", ""),
            event_slug=m.get("event_slug", ""),
            volume=float(m.get("volume", 0)),
            tick_size=float(m.get("tick_size", 0.01)),
            min_order_size=float(m.get("min_order_size", 5)),
            outcomes=m.get("outcomes", []),
            outcome_prices=m.get("outcome_prices", {}),
            outcome_tokens=m.get("outcome_tokens", {}),
        )
        for m in cached
    ]


# ─── CLI Test ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gamma = GammaClient()
    scanned, filtered = scan_markets(gamma, limit=100, verbose=True)
    print(f"\nScanned: {len(scanned)}, Filtered: {len(filtered)}")
    for m in filtered:
        print(f"  [{m.category}] {m.title[:55]}  Yes:{m.yes_price:.3f}  Liq:${m.liquidity:,.0f}")
