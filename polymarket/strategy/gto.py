"""
gto.py — Game Theory Optimal filter for Polymarket pipeline

零 AI 成本嘅 GTO 濾鏡。每個 market 過三個問題：
1. 「點解我會 fill？」— adverse selection scoring
2. 「市場係咪已經 Nash equilibrium？」— 冇 edge = skip
3. 「我嘅 order 可唔可以被 exploit？」— unexploitability

設計決定：
- 純 deterministic（keyword + math），唔用 AI，零成本
- Soft filter + Kelly adjuster — 極端 case 先 block；中等風險 → 縮注
- Nash equilibrium 做 opportunity detector — 低分 = 市場 flux = 有機會；高分 = efficient = skip
- Dominant strategy bonus — 50/50 at 48% type plays get full Kelly
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..config.settings import (
    GTO_ADVERSE_BLOCK_THRESHOLD,
    GTO_NASH_SKIP_THRESHOLD,
    GTO_UNEXPLOITABILITY_MIN,
    GTO_LIVE_EVENT_BLOCK,
    GTO_NEWS_DRIVEN_MAX_OFFSET,
    MIN_EDGE_PCT,
)
from ..core.context import EdgeAssessment, PolyMarket

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# GTO Market Type Registry
# ════════════════════════════════════════════════════════════════

# base adverse selection risk per type + recommended order strategy
_GTO_TYPE_REGISTRY: dict[str, dict] = {
    "live_event": {
        "base_adverse": 0.95,
        "order_type": "MARKET",
        "limit_offset": 0.0,
        "keywords": [
            "nba", "nfl", "nhl", "mlb", "mls", "ufc", "pga",
            "game", "match", "score", "win", "playoff", "championship",
            "premier league", "la liga", "bundesliga", "serie a",
            "formula 1", "f1", "grand prix", "race",
            "half", "quarter", "inning", "round",
        ],
    },
    "news_driven": {
        "base_adverse": 0.75,
        "order_type": "LIMIT",
        "limit_offset": 0.03,
        "keywords": [
            "fire", "fired", "resign", "quit", "impeach",
            "war", "attack", "invade", "strike", "bomb",
            "fed rate", "interest rate", "rate cut", "rate hike",
            "earthquake", "hurricane", "tornado", "tsunami",
            "arrest", "indictment", "verdict", "guilty",
            "announce", "breaking", "emergency",
        ],
    },
    "quantifiable": {
        "base_adverse": 0.15,
        "order_type": "LIMIT",
        "limit_offset": 0.10,
        "keywords": [
            "temperature", "degrees", "celsius", "fahrenheit",
            "gas fee", "gwei", "gas price",
            "coin flip", "dice", "random",
            "population", "gdp", "cpi", "inflation rate",
            "rainfall", "inches", "millimeters",
        ],
    },
    "crypto_15m": {
        "base_adverse": 0.40,
        "order_type": "MARKET",  # FOK taker
        "limit_offset": 0.0,
    },
    "crypto": {
        "base_adverse": 0.50,
        "order_type": "LIMIT",
        "limit_offset": 0.05,
    },
}


# ════════════════════════════════════════════════════════════════
# Data Structures
# ════════════════════════════════════════════════════════════════

@dataclass
class GTOAssessment:
    """GTO analysis result for a single market."""
    condition_id: str = ""
    gto_type: str = ""
    adverse_selection_score: float = 0.0    # [0,1] — higher = more risk
    nash_equilibrium_score: float = 0.0     # [0,1] — higher = more efficient (less opportunity)
    unexploitability_score: float = 0.0     # [0,1] — higher = safer order
    fill_quality: str = ""                  # "good" / "neutral" / "bad"
    dumb_money_prob: float = 0.0
    informed_prob: float = 0.0
    order_type: str = "LIMIT"               # "MARKET" or "LIMIT"
    limit_offset: float = 0.0               # distance from mid for limit orders
    is_dominant_strategy: bool = False
    approved: bool = True
    reasoning: str = ""


# ════════════════════════════════════════════════════════════════
# Classification
# ════════════════════════════════════════════════════════════════

def classify_gto_type(market: PolyMarket) -> str:
    """Classify market into GTO type by keyword matching.

    Priority: live_event > news_driven > quantifiable (higher risk wins).
    crypto_15m matched by existing category field.
    """
    # Existing category-based types
    if market.category == "crypto_15m":
        return "crypto_15m"

    title_lower = (market.title or "").lower()
    desc_lower = (market.description or "").lower()[:300]
    text = f"{title_lower} {desc_lower}"

    # Priority order: live_event > news_driven > quantifiable
    for gto_type in ("live_event", "news_driven", "quantifiable"):
        registry = _GTO_TYPE_REGISTRY[gto_type]
        keywords = registry.get("keywords", [])
        for kw in keywords:
            if len(kw) <= 3:
                if re.search(rf"\b{re.escape(kw)}\b", text):
                    return gto_type
            else:
                if kw in text:
                    return gto_type

    # Default: crypto
    return "crypto"


# ════════════════════════════════════════════════════════════════
# Core Scoring Functions
# ════════════════════════════════════════════════════════════════

def compute_adverse_selection(
    market: PolyMarket,
    edge: float,
    gto_type: str,
) -> float:
    """Compute adverse selection risk [0,1].

    Core question: "If I get filled at a price 10% from mid,
    is it dumb money or informed flow?"

    Factors:
    - Base risk from market type (live_event=0.95, quantifiable=0.15)
    - Distance penalty: larger edge = further from mid = more likely informed
    - Volume penalty: very low volume markets attract informed traders
    """
    registry = _GTO_TYPE_REGISTRY.get(gto_type, _GTO_TYPE_REGISTRY["crypto"])
    base = registry["base_adverse"]

    # Distance penalty: edge far from mid → more likely I'm wrong side
    # Edge of 20%+ from mid is suspicious — why hasn't arb fixed it?
    abs_edge = abs(edge)
    distance_penalty = min(1.0, abs_edge / 0.20) * 0.15

    # Volume penalty: <$2k 24h volume = thin market, informed traders dominate
    vol = max(1.0, market.volume_24h)
    volume_penalty = max(0.0, (1.0 - vol / 5000.0)) * 0.10

    score = min(1.0, base + distance_penalty + volume_penalty)
    return round(score, 3)


def compute_nash_equilibrium_score(market: PolyMarket) -> float:
    """Compute how close market is to Nash equilibrium [0,1].

    0 = far from equilibrium (opportunity)
    1 = at equilibrium (no edge, skip)

    Factors:
    - Price near 50% → coin flip = equilibrium
    - Tight spread → efficient price discovery
    - Deep liquidity → many participants → consensus price
    """
    # Price proximity to 50% (coin flip = max equilibrium)
    mid_distance = abs(market.yes_price - 0.50)
    # At 50%: price_factor = 1.0 (max eq); at 5% or 95%: ~0.1
    price_factor = max(0.0, 1.0 - mid_distance * 2.0)

    # Spread tightness: tight spread = efficient market
    # spread < 2% → very tight → factor ~1.0
    spread = max(0.001, market.spread)
    spread_factor = max(0.0, 1.0 - spread / 0.08)

    # Liquidity depth: deep book = many participants = consensus
    liq = max(1.0, market.liquidity)
    # $10k+ liquidity → factor ~1.0
    liq_factor = min(1.0, liq / 10000.0)

    # Weighted combination
    score = price_factor * 0.50 + spread_factor * 0.30 + liq_factor * 0.20
    return round(min(1.0, score), 3)


def compute_unexploitability(
    edge: float,
    adverse_score: float,
    confidence: float,
    market_price: float,
) -> float:
    """Compute how unexploitable our order is [0,1].

    Sun Tzu: "Making no mistakes = certainty of victory."
    Higher = safer order. Geometric mean of protection factors.
    """
    # Factor 1: inverse adverse selection (0.95 adverse → 0.05 protection)
    f_adverse = max(0.01, 1.0 - adverse_score)

    # Factor 2: confidence in our estimate
    f_confidence = max(0.01, min(1.0, confidence))

    # Factor 3: mid proximity — orders near mid are harder to exploit
    mid_dist = abs(market_price - 0.50)
    f_mid = max(0.01, 1.0 - mid_dist * 1.5)

    # Geometric mean — all three must be decent for high score
    score = (f_adverse * f_confidence * f_mid) ** (1.0 / 3.0)
    return round(min(1.0, score), 3)


def estimate_fill_quality(
    market: PolyMarket,
    gto_type: str,
    adverse_score: float,
) -> dict:
    """Estimate fill quality: who is filling my order?

    Returns dict with dumb_money_prob, informed_prob, fill_quality.
    """
    # Base probabilities from adverse selection
    informed_prob = adverse_score
    dumb_money_prob = 1.0 - adverse_score

    # Quantifiable markets: fills are mostly noise traders
    if gto_type == "quantifiable":
        dumb_money_prob = min(1.0, dumb_money_prob + 0.15)
        informed_prob = 1.0 - dumb_money_prob

    # Live events: fills are almost always informed (someone knows score)
    elif gto_type == "live_event":
        informed_prob = min(1.0, informed_prob + 0.10)
        dumb_money_prob = 1.0 - informed_prob

    # Fill quality label
    if dumb_money_prob >= 0.60:
        quality = "good"
    elif dumb_money_prob >= 0.35:
        quality = "neutral"
    else:
        quality = "bad"

    return {
        "dumb_money_prob": round(dumb_money_prob, 3),
        "informed_prob": round(informed_prob, 3),
        "fill_quality": quality,
    }


def recommend_order_strategy(gto_type: str, unexploitability: float) -> dict:
    """Recommend order type and limit offset based on GTO analysis."""
    registry = _GTO_TYPE_REGISTRY.get(gto_type, _GTO_TYPE_REGISTRY["crypto"])
    order_type = registry["order_type"]
    base_offset = registry["limit_offset"]

    # Low unexploitability → tighten limit (closer to fair value)
    # Rationale: don't pay wide spreads when fill quality is poor
    if order_type == "LIMIT" and unexploitability < 0.40:
        base_offset = min(base_offset, GTO_NEWS_DRIVEN_MAX_OFFSET)

    rationale = f"{gto_type}: {order_type}"
    if order_type == "LIMIT":
        rationale += f" offset={base_offset:.1%}"

    return {
        "order_type": order_type,
        "limit_offset": round(base_offset, 4),
        "rationale": rationale,
    }


def is_dominant_strategy(
    edge: float,
    gto_type: str,
    market_price: float,
    confidence: float,
) -> bool:
    """Check if trade is profitable regardless of counterparty behavior.

    True examples:
    - 50/50 event at 48% (always profitable in expectation)
    - Quantifiable market with strong data and large edge
    """
    abs_edge = abs(edge)

    # Quantifiable with high confidence + decent edge → always EV+
    if gto_type == "quantifiable" and abs_edge >= 0.12 and confidence >= 0.75:
        return True

    # Near-coin-flip with mispricing: price near 50% but edge > 5%
    mid_dist = abs(market_price - 0.50)
    if mid_dist < 0.08 and abs_edge >= 0.05 and confidence >= 0.65:
        return True

    return False


# ════════════════════════════════════════════════════════════════
# GTO Decision Logic
# ════════════════════════════════════════════════════════════════

def _apply_gto_rules(
    gto_type: str,
    adverse_score: float,
    nash_score: float,
    unexploitability_score: float,
    fill_quality: str,
    edge: float,
    dominant: bool,
) -> tuple[bool, str]:
    """Apply GTO decision rules. Returns (approved, reasoning)."""
    abs_edge = abs(edge)

    # Rule 1: block live events (extreme adverse selection)
    if gto_type == "live_event" and GTO_LIVE_EVENT_BLOCK:
        return False, "BLOCKED: live event — extreme adverse selection risk"

    # Rule 2: block bad fills on non-quantifiable
    if fill_quality == "bad" and gto_type != "quantifiable":
        return False, f"BLOCKED: bad fill quality on {gto_type} market"

    # Rule 3: block high adverse selection
    if adverse_score > GTO_ADVERSE_BLOCK_THRESHOLD:
        return False, f"BLOCKED: adverse selection {adverse_score:.2f} > {GTO_ADVERSE_BLOCK_THRESHOLD}"

    # Rule 4: skip if market at equilibrium + small edge
    if nash_score > GTO_NASH_SKIP_THRESHOLD and abs_edge < MIN_EDGE_PCT:
        return False, f"BLOCKED: Nash eq {nash_score:.2f} + small edge {abs_edge:.1%}"

    # Rule 5: dominant strategy → always approve (before unexploitability check)
    if dominant:
        return True, "APPROVED: dominant strategy — full Kelly"

    # Rule 6: block if order is too exploitable
    if unexploitability_score < GTO_UNEXPLOITABILITY_MIN:
        return False, f"BLOCKED: unexploitability {unexploitability_score:.2f} < {GTO_UNEXPLOITABILITY_MIN}"

    # Rule 7: everything else → approve with Kelly scaling
    return True, f"APPROVED: adv={adverse_score:.2f} nash={nash_score:.2f}"


# ════════════════════════════════════════════════════════════════
# Orchestrator
# ════════════════════════════════════════════════════════════════

def assess_gto(market: PolyMarket, edge_assessment: EdgeAssessment) -> GTOAssessment:
    """Full GTO assessment for a single market + edge pair."""
    edge = edge_assessment.edge
    confidence = edge_assessment.confidence
    market_price = market.yes_price

    # 1. Classify
    gto_type = classify_gto_type(market)

    # 2. Adverse selection
    adverse = compute_adverse_selection(market, edge, gto_type)

    # 3. Nash equilibrium
    nash = compute_nash_equilibrium_score(market)

    # 4. Unexploitability
    unexploit = compute_unexploitability(
        edge, adverse, confidence, market_price,
    )

    # 5. Fill quality
    fill = estimate_fill_quality(market, gto_type, adverse)

    # 6. Dominant strategy
    dominant = is_dominant_strategy(edge, gto_type, market_price, confidence)

    # 7. Order strategy
    order = recommend_order_strategy(gto_type, unexploit)

    # 8. Decision
    approved, reasoning = _apply_gto_rules(
        gto_type, adverse, nash, unexploit, fill["fill_quality"], edge, dominant,
    )

    return GTOAssessment(
        condition_id=edge_assessment.condition_id,
        gto_type=gto_type,
        adverse_selection_score=adverse,
        nash_equilibrium_score=nash,
        unexploitability_score=unexploit,
        fill_quality=fill["fill_quality"],
        dumb_money_prob=fill["dumb_money_prob"],
        informed_prob=fill["informed_prob"],
        order_type=order["order_type"],
        limit_offset=order["limit_offset"],
        is_dominant_strategy=dominant,
        approved=approved,
        reasoning=reasoning,
    )


def assess_gto_batch(
    markets: list[PolyMarket],
    edge_assessments: list[EdgeAssessment],
) -> dict[str, GTOAssessment]:
    """Batch GTO assessment. Returns dict keyed by condition_id."""
    # Build market lookup
    market_map = {m.condition_id: m for m in markets}
    results: dict[str, GTOAssessment] = {}

    for ea in edge_assessments:
        market = market_map.get(ea.condition_id)
        if not market:
            logger.warning("GTO: no market found for %s ('%s')", ea.condition_id[:20], ea.title[:30])
            continue

        gto = assess_gto(market, ea)
        results[ea.condition_id] = gto

        logger.info(
            "GTO [%s] %s adv:%.2f nash:%.2f unexploit:%.2f fill:%s %s",
            gto.gto_type, ea.title[:40],
            gto.adverse_selection_score, gto.nash_equilibrium_score,
            gto.unexploitability_score, gto.fill_quality,
            "APPROVED" if gto.approved else "BLOCKED",
        )

    return results
