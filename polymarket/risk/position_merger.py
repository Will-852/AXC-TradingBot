"""
position_merger.py — Detect mergeable positions on Polymarket

Phase 1: Detection only (report mergeable positions via Telegram)
Phase 2: On-chain CTF merge execution via Relayer (TODO)

When you hold both YES and NO tokens for the same market,
they can be merged (redeemed) for $1 USDC per pair, freeing capital.

Detection uses the Polymarket Data API:
  GET /positions?user=...&mergeable=true  (no auth needed)
"""

import logging
from dataclasses import dataclass, field
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote
import json

from ..config.settings import DATA_API_HOST

logger = logging.getLogger(__name__)

_TIMEOUT = 15


@dataclass
class MergeablePosition:
    """A detected mergeable position pair."""
    condition_id: str = ""
    title: str = ""
    event_slug: str = ""
    yes_shares: float = 0.0
    no_shares: float = 0.0
    mergeable_pairs: float = 0.0  # min(yes_shares, no_shares)
    reclaimable_usdc: float = 0.0  # mergeable_pairs * $1


def detect_mergeable(
    user_address: str,
    verbose: bool = False,
) -> list[MergeablePosition]:
    """Query Data API for mergeable positions.

    Args:
        user_address: Polygon wallet address (proxy wallet, not EOA)
        verbose: Print debug info

    Returns:
        List of MergeablePosition objects with reclaimable USDC amounts
    """
    if not user_address:
        logger.warning("No user address provided for merge detection")
        return []

    url = f"{DATA_API_HOST}/positions?user={quote(user_address)}&mergeable=true"

    try:
        req = Request(url, headers={"User-Agent": "AXC-Trading/1.0"})
        with urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, HTTPError, json.JSONDecodeError) as e:
        logger.warning("Data API merge check failed: %s", e)
        return []

    if not data:
        return []

    # Data API returns position objects with mergeable flag
    # Group by condition_id to find YES+NO pairs
    positions_by_cid: dict[str, dict] = {}
    for pos in data:
        cid = pos.get("conditionId", pos.get("condition_id", ""))
        if not cid:
            continue
        if cid not in positions_by_cid:
            positions_by_cid[cid] = {
                "title": pos.get("title", pos.get("question", "")),
                "event_slug": pos.get("eventSlug", ""),
                "yes": 0.0,
                "no": 0.0,
            }

        outcome = pos.get("outcome", "").upper()
        size = float(pos.get("size", 0) or 0)
        if outcome == "YES":
            positions_by_cid[cid]["yes"] = size
        elif outcome == "NO":
            positions_by_cid[cid]["no"] = size

    results = []
    for cid, info in positions_by_cid.items():
        pairs = min(info["yes"], info["no"])
        if pairs <= 0:
            continue

        mp = MergeablePosition(
            condition_id=cid,
            title=info["title"],
            event_slug=info["event_slug"],
            yes_shares=info["yes"],
            no_shares=info["no"],
            mergeable_pairs=pairs,
            reclaimable_usdc=pairs,  # $1 per merged pair
        )
        results.append(mp)

        if verbose:
            logger.info(
                "MERGEABLE: %s — %d pairs → $%.2f reclaimable",
                mp.title[:50], pairs, mp.reclaimable_usdc,
            )

    return results


def format_merge_report(mergeables: list[MergeablePosition]) -> str:
    """Format mergeable positions for Telegram reporting."""
    if not mergeables:
        return ""

    total = sum(m.reclaimable_usdc for m in mergeables)
    lines = [f"<b>💰 Mergeable Positions ({len(mergeables)})</b>"]
    lines.append(f"Total reclaimable: <b>${total:.2f}</b>\n")

    for m in mergeables:
        lines.append(
            f"• {m.title[:40]}\n"
            f"  YES: {m.yes_shares:.0f} / NO: {m.no_shares:.0f} "
            f"→ {m.mergeable_pairs:.0f} pairs (${m.reclaimable_usdc:.2f})"
        )

    lines.append("\n<i>Merge execution: manual (Phase 2 TODO)</i>")
    return "\n".join(lines)
