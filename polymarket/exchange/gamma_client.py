"""
gamma_client.py — Gamma API wrapper for Polymarket market discovery

Gamma API 係公開 REST API，唔需要 auth。
用途：搵所有 active 市場、過濾 crypto/weather、取得 market metadata。

Gamma API 返回嘅 market 包含：
- condition_id (market ID)
- question (title)
- description
- outcomes / tokens (Yes/No token IDs)
- end_date_iso (resolution date)
- active, closed, archived flags
- volume, liquidity
- tags

Rate limit: ~60 req/min (undocumented, conservative approach)
"""

import logging
import time
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import json

from shared_infra.exchange.exceptions import TemporaryError
from shared_infra.exchange.retry import retry_quadratic

from ..config.settings import GAMMA_HOST

logger = logging.getLogger(__name__)

# ─── Constants ───
_TIMEOUT = 15  # seconds
_USER_AGENT = "AXC-Trading/1.0"


def _extract_event_id(raw: dict) -> str:
    """Extract parent event ID from raw Gamma market response."""
    events = raw.get("events", [])
    if events and isinstance(events, list):
        return str(events[0].get("id", ""))
    return ""


def _extract_event_slug(raw: dict) -> str:
    """Extract parent event slug from raw Gamma market response."""
    events = raw.get("events", [])
    if events and isinstance(events, list):
        return events[0].get("slug", "")
    return ""


class GammaClient:
    """Gamma API client for Polymarket market discovery.

    免 auth，pure REST。所有方法返回 parsed JSON。
    設計上同 PolymarketClient 分開：
    - GammaClient: 讀 market metadata（公開）
    - PolymarketClient: 落盤 / 查 balance（需 auth）
    """

    def __init__(self, host: str = GAMMA_HOST):
        self.host = host.rstrip("/")

    def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        """HTTP GET with error handling."""
        url = f"{self.host}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if query:
                url = f"{url}?{query}"

        req = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code == 429:
                raise TemporaryError(f"Gamma API rate limited: {e}")
            elif e.code >= 500:
                raise TemporaryError(f"Gamma API server error {e.code}: {e}")
            raise
        except (URLError, TimeoutError) as e:
            raise TemporaryError(f"Gamma API connection error: {e}")

    # ─── Markets ───

    @retry_quadratic()
    def get_markets(
        self,
        limit: int = 50,
        active: bool = True,
        closed: bool = False,
        order: str = "volume",
        ascending: bool = False,
        tag: str = "",
    ) -> list[dict]:
        """Fetch markets from Gamma API.

        Args:
            limit: Max number of markets to return
            active: Only active markets
            closed: Include closed markets
            order: Sort field (volume, liquidity, start_date, end_date)
            ascending: Sort direction
            tag: Filter by tag slug (e.g., "crypto")
        """
        params = {
            "limit": str(limit),
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if tag:
            params["tag"] = tag

        result = self._get("/markets", params)
        if isinstance(result, list):
            return result
        # Some API versions wrap in {"data": [...]}
        return result.get("data", result.get("markets", []))

    @retry_quadratic()
    def get_market(self, condition_id: str) -> dict:
        """Get single market details by condition_id."""
        result = self._get(f"/markets/{condition_id}")
        return result if isinstance(result, dict) else {}

    @retry_quadratic()
    def get_events(
        self,
        limit: int = 20,
        active: bool = True,
        closed: bool = False,
        tag: str = "",
    ) -> list[dict]:
        """Fetch events (group of related markets).

        Events can contain multiple markets (e.g., "BTC price at end of month"
        might have markets for different price ranges).
        """
        params = {
            "limit": str(limit),
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        if tag:
            params["tag"] = tag

        result = self._get("/events", params)
        if isinstance(result, list):
            return result
        return result.get("data", result.get("events", []))

    @retry_quadratic()
    def get_event_by_slug(self, slug: str) -> dict | None:
        """Fetch a single event by slug. Returns event dict with nested markets, or None."""
        result = self._get("/events", {"slug": slug})
        if isinstance(result, list) and result:
            return result[0]
        if isinstance(result, dict) and result.get("id"):
            return result
        return None

    # ─── Convenience ───

    def get_markets_by_tag(self, tag: str, limit: int = 50) -> list[dict]:
        """Get active markets filtered by tag."""
        return self.get_markets(limit=limit, active=True, tag=tag)

    def get_recent_markets(self, limit: int = 50, **kwargs) -> list[dict]:
        """Fetch most recently created markets (catches low-volume series like 15M/5M).

        設計決定：15M/5M 市場 liquidity ~$14K，排唔入 top-N by volume/liquidity。
        Sort by startDate desc 保證最新嘅 slot 一定出現。
        """
        return self.get_markets(limit=limit, active=True, order="startDate",
                                ascending=False, **kwargs)

    def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        """Search markets by text query.

        搜索兩個來源再合併：
        1. Top markets by volume（大市場）
        2. Recent markets by startDate（低流動性 series 如 15M/5M）
        """
        sources = [
            self.get_markets(limit=100, active=True),
            self.get_recent_markets(limit=100),
        ]
        seen: set[str] = set()
        all_markets: list[dict] = []
        for batch in sources:
            for m in batch:
                cid = m.get("conditionId", "")
                if cid and cid not in seen:
                    seen.add(cid)
                    all_markets.append(m)

        query_lower = query.lower()
        matched = []
        for m in all_markets:
            title = (m.get("question", "") or "").lower()
            slug = (m.get("slug", "") or "").lower()
            if query_lower in title or query_lower in slug:
                matched.append(m)
                if len(matched) >= limit:
                    break
        return matched

    @staticmethod
    def extract_token_ids(market: dict) -> dict[str, str]:
        """Extract outcome token IDs from a market dict.

        Gamma API stores token IDs in 'clobTokenIds' as a JSON string array.
        Outcomes stored in 'outcomes' as JSON string array.
        First token = first outcome, second token = second outcome.

        Returns: {"outcome_0": token_id, "outcome_1": token_id}
        with lowercase outcome names as keys (e.g., "yes", "no", "over 2.5").
        """
        result = {}
        try:
            outcomes_raw = market.get("outcomes", "[]")
            if isinstance(outcomes_raw, str):
                outcomes = json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw

            tokens_raw = market.get("clobTokenIds", "[]")
            if isinstance(tokens_raw, str):
                token_ids = json.loads(tokens_raw)
            else:
                token_ids = tokens_raw

            for i, token_id in enumerate(token_ids):
                if i < len(outcomes):
                    key = str(outcomes[i]).lower().strip()
                else:
                    key = f"outcome_{i}"
                result[key] = str(token_id)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning("Failed to parse token IDs: %s", e)

        return result

    @staticmethod
    def parse_market(raw: dict) -> dict:
        """Parse raw Gamma API market into normalized format.

        Gamma API 返回 camelCase field names，呢度轉成 snake_case。
        outcomePrices 同 clobTokenIds 係 JSON string arrays。
        """
        tokens = GammaClient.extract_token_ids(raw)

        # Parse outcomes and prices from JSON strings
        outcomes_raw = raw.get("outcomes", "[]")
        prices_raw = raw.get("outcomePrices", "[]")
        try:
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except (json.JSONDecodeError, TypeError):
            outcomes = []
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except (json.JSONDecodeError, TypeError):
            prices = []

        # Map outcome names to prices
        outcome_prices = {}
        for i, name in enumerate(outcomes):
            key = str(name).lower().strip()
            price = float(prices[i]) if i < len(prices) else 0.0
            outcome_prices[key] = price

        # For binary markets, identify yes/no prices
        yes_price = outcome_prices.get("yes", 0.0)
        no_price = outcome_prices.get("no", 0.0)
        # If not yes/no, use first two outcomes
        if yes_price == 0 and no_price == 0 and len(prices) >= 2:
            yes_price = float(prices[0])
            no_price = float(prices[1])

        # Get first/second token IDs (for binary markets)
        token_list = list(tokens.values())
        yes_token = token_list[0] if len(token_list) > 0 else ""
        no_token = token_list[1] if len(token_list) > 1 else ""

        return {
            "condition_id": raw.get("conditionId", ""),
            "question_id": raw.get("questionID", ""),
            "title": raw.get("question", ""),
            "description": raw.get("description", ""),
            "end_date": raw.get("endDateIso", ""),
            "active": raw.get("active", False),
            "closed": raw.get("closed", False),
            "volume": float(raw.get("volumeNum", 0) or raw.get("volume", 0) or 0),
            "liquidity": float(raw.get("liquidityNum", 0) or raw.get("liquidity", 0) or 0),
            "volume_24h": float(raw.get("volume24hr", 0) or 0),
            "yes_token_id": yes_token,
            "no_token_id": no_token,
            "yes_price": yes_price,
            "no_price": no_price,
            "outcomes": outcomes,
            "outcome_prices": outcome_prices,
            "outcome_tokens": tokens,
            "tags": raw.get("tags", []),
            "image": raw.get("image", ""),
            "icon": raw.get("icon", ""),
            "slug": raw.get("slug", ""),
            "neg_risk": raw.get("negRisk", False),
            "event_id": _extract_event_id(raw),
            "event_slug": _extract_event_slug(raw),
            "min_order_size": raw.get("orderMinSize", 5),
            "tick_size": raw.get("orderPriceMinTickSize", 0.01),
            "accepting_orders": raw.get("acceptingOrders", True),
        }


# ─── CLI Test ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gamma = GammaClient()

    print("=== Top markets by volume ===")
    markets = gamma.get_markets(limit=5)
    for m in markets:
        parsed = gamma.parse_market(m)
        print(f"  {parsed['title'][:60]}")
        print(f"    Vol: ${parsed['volume']:,.0f}  Liq: ${parsed['liquidity']:,.0f}")
        print(f"    Yes: {parsed['yes_price']:.2f}  No: {parsed['no_price']:.2f}")
        print()

    print("=== Crypto markets ===")
    crypto = gamma.get_markets_by_tag("crypto", limit=3)
    for m in crypto:
        parsed = gamma.parse_market(m)
        print(f"  {parsed['title'][:60]}")
