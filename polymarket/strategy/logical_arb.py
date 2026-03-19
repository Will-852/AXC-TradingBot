"""
logical_arb.py — Logical Arbitrage detection for Polymarket

Detects pricing contradictions between related markets under the same event:

1. NegRisk events: all outcomes are mutually exclusive.
   Sum of YES prices should be ≤ 1.0. Deviations = arb opportunity.
   - sum > 1.0 + fee_buffer → overpriced outcomes exist (sell signal)
   - sum < 1.0 - fee_buffer → underpriced outcomes exist (buy signal)

2. Ordered outcomes: e.g. "BTC > 70k" vs "BTC > 75k"
   Higher threshold must have lower probability. Violations = contradiction.

Zero AI cost — pure math.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..core.context import PolyMarket

logger = logging.getLogger(__name__)

# Taker fee on Polymarket (~1.5% round-trip estimate)
_FEE_BUFFER = 0.025  # need > 2.5% mispricing to cover fees both sides


@dataclass
class ArbOpportunity:
    """A detected logical arbitrage opportunity."""
    event_id: str
    event_slug: str
    arb_type: str          # "neg_risk_overpriced" / "neg_risk_underpriced" / "ordering_violation"
    markets: list[PolyMarket] = field(default_factory=list)
    sum_prices: float = 0.0
    edge_pct: float = 0.0  # magnitude of mispricing after fees
    detail: str = ""


def detect_arb(
    markets: list[PolyMarket],
    gamma_client=None,
    fee_buffer: float = _FEE_BUFFER,
    verbose: bool = False,
) -> list[ArbOpportunity]:
    """Scan markets for logical arbitrage opportunities.

    Args:
        markets: All scanned markets (pre-filter, includes all categories)
        gamma_client: GammaClient instance — needed for negRisk full event fetch
        fee_buffer: Minimum price deviation to consider (covers fees)
        verbose: Print debug info

    Returns:
        List of detected ArbOpportunity objects
    """
    opportunities: list[ArbOpportunity] = []

    # Group markets by event_id (exclude weather — thin book friction ≠ arb)
    # Weather sum(YES) deviates from 100% due to illiquidity, not mispricing.
    # False arb on weather caused HK 21°C incident (2026-03-19).
    event_groups: dict[str, list[PolyMarket]] = defaultdict(list)
    for m in markets:
        if m.event_id and m.category != "weather":
            event_groups[m.event_id].append(m)

    # Pre-fetch all markets once for negRisk sibling resolution
    all_raw_markets = None
    neg_risk_events = [
        eid for eid, g in event_groups.items() if g[0].neg_risk
    ]
    if neg_risk_events and gamma_client:
        try:
            all_raw_markets = gamma_client.get_markets(limit=200, active=True)
        except Exception as e:
            logger.warning("Failed to fetch markets for arb: %s", e)

    for event_id, group in event_groups.items():
        # Check NegRisk arbitrage (need full event siblings)
        if group[0].neg_risk and all_raw_markets is not None:
            full_group = _build_event_siblings(
                event_id, group, all_raw_markets, verbose,
            )
            if len(full_group) >= 2:
                opps = _check_neg_risk(event_id, full_group, fee_buffer, verbose)
                opportunities.extend(opps)

        # Check ordering violations (only needs our scanned markets)
        if len(group) >= 2:
            opps = _check_ordering(event_id, group, fee_buffer, verbose)
            opportunities.extend(opps)

    if verbose:
        if opportunities:
            for opp in opportunities:
                logger.info(
                    "ARB DETECTED [%s]: %s — edge %.1f%% — %s",
                    opp.arb_type, opp.event_slug, opp.edge_pct * 100, opp.detail,
                )
        else:
            logger.info("No logical arb opportunities")

    return opportunities


def _build_event_siblings(
    event_id: str,
    known_markets: list[PolyMarket],
    all_raw_markets: list[dict],
    verbose: bool,
) -> list[PolyMarket]:
    """Build complete sibling list for a negRisk event from pre-fetched data.

    Our scanner only sees category-matched markets. NegRisk sum check needs
    the complete set (e.g. all 10 temperature ranges, not just the 1-2 we matched).
    """
    import json

    siblings = []
    for raw in all_raw_markets:
        raw_events = raw.get("events", [])
        for ev in raw_events:
            if str(ev.get("id", "")) == event_id:
                prices_raw = raw.get("outcomePrices", "[]")
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                except (json.JSONDecodeError, TypeError):
                    prices = []
                yes_price = float(prices[0]) if prices else 0.0
                cid = raw.get("conditionId", "")

                siblings.append(PolyMarket(
                    condition_id=cid,
                    title=raw.get("question", ""),
                    yes_price=yes_price,
                    neg_risk=True,
                    event_id=event_id,
                    event_slug=ev.get("slug", ""),
                ))
                break

    if verbose and siblings:
        logger.info(
            "NegRisk event %s: %d total siblings (we scanned %d)",
            event_id, len(siblings), len(known_markets),
        )

    return siblings if siblings else known_markets


def _check_neg_risk(
    event_id: str,
    group: list[PolyMarket],
    fee_buffer: float,
    verbose: bool,
) -> list[ArbOpportunity]:
    """Check NegRisk event for price sum violations.

    In NegRisk events, all outcomes are mutually exclusive and exhaustive.
    The sum of all YES prices should equal ~1.0 (minus the "Other" bucket).
    """
    opps = []
    sum_yes = sum(m.yes_price for m in group)
    slug = group[0].event_slug

    if sum_yes > 1.0 + fee_buffer:
        # Overpriced: market collectively values outcomes at > 100%
        edge = sum_yes - 1.0 - fee_buffer
        opps.append(ArbOpportunity(
            event_id=event_id,
            event_slug=slug,
            arb_type="neg_risk_overpriced",
            markets=group,
            sum_prices=sum_yes,
            edge_pct=edge,
            detail=f"sum(YES)={sum_yes:.3f} > 1.0 + {fee_buffer} "
                   f"({len(group)} outcomes)",
        ))

    elif sum_yes < 1.0 - fee_buffer:
        # Underpriced: market collectively undervalues outcomes
        edge = 1.0 - fee_buffer - sum_yes
        opps.append(ArbOpportunity(
            event_id=event_id,
            event_slug=slug,
            arb_type="neg_risk_underpriced",
            markets=group,
            sum_prices=sum_yes,
            edge_pct=edge,
            detail=f"sum(YES)={sum_yes:.3f} < 1.0 - {fee_buffer} "
                   f"({len(group)} outcomes)",
        ))

    return opps


# Pattern to extract numeric thresholds from market titles
_THRESHOLD_RE = re.compile(
    r"(?:above|below|over|under|>|<|≥|≤|at least|more than|less than)\s*"
    r"\$?([\d,]+(?:\.\d+)?[kKmM]?)",
    re.IGNORECASE,
)


def _parse_threshold(title: str) -> float | None:
    """Try to extract a numeric threshold from a market title."""
    match = _THRESHOLD_RE.search(title)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    multiplier = 1.0
    if raw[-1] in ("k", "K"):
        multiplier = 1_000
        raw = raw[:-1]
    elif raw[-1] in ("m", "M"):
        multiplier = 1_000_000
        raw = raw[:-1]
    try:
        return float(raw) * multiplier
    except ValueError:
        return None


def _check_ordering(
    event_id: str,
    group: list[PolyMarket],
    fee_buffer: float,
    verbose: bool,
) -> list[ArbOpportunity]:
    """Check for ordering violations in threshold-based markets.

    If market A has threshold 70k and market B has threshold 75k,
    then P(>75k) must be ≤ P(>70k). Violation = mispricing.
    """
    opps = []
    slug = group[0].event_slug

    # Extract thresholds
    with_threshold = []
    for m in group:
        t = _parse_threshold(m.title)
        if t is not None:
            with_threshold.append((t, m))

    if len(with_threshold) < 2:
        return opps

    # Sort by threshold ascending
    with_threshold.sort(key=lambda x: x[0])

    # For "above/over" markets: higher threshold → lower probability
    for i in range(len(with_threshold) - 1):
        t_low, m_low = with_threshold[i]
        t_high, m_high = with_threshold[i + 1]

        # P(above high threshold) should be ≤ P(above low threshold)
        if m_high.yes_price > m_low.yes_price + fee_buffer:
            edge = m_high.yes_price - m_low.yes_price - fee_buffer
            opps.append(ArbOpportunity(
                event_id=event_id,
                event_slug=slug,
                arb_type="ordering_violation",
                markets=[m_low, m_high],
                sum_prices=0,
                edge_pct=edge,
                detail=(
                    f"'{m_high.title[:50]}' (${t_high:,.0f}) at "
                    f"{m_high.yes_price:.3f} > "
                    f"'{m_low.title[:50]}' (${t_low:,.0f}) at "
                    f"{m_low.yes_price:.3f}"
                ),
            ))

    return opps
