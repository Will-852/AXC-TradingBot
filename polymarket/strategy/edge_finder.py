"""
edge_finder.py — Market edge detection: deterministic + AI paths

設計決定：
- Crypto 15M：triple signal (indicator + CVD + microstructure)，AI fallback
- Crypto（一般）：Claude AI 概率估算（需要推理能力）
- 追蹤 calibration（predicted vs actual）方便日後校正
"""

import json
import logging
import os
import re
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from ..config.settings import (
    AXC_HOME, SECRETS_PATH, AI_MODEL, AI_MAX_TOKENS, AI_TEMPERATURE, LOG_DIR,
)
from ..core.context import PolyMarket, EdgeAssessment

logger = logging.getLogger(__name__)

# ─── API Config (loaded once) ───
load_dotenv(SECRETS_PATH)
_PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
_PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
_PROXY2_BASE_URL = os.environ.get("PROXY2_BASE_URL", "")
_PROXY2_API_KEY = os.environ.get("PROXY2_API_KEY", "")
_FALLBACK_MODEL = "gpt-5.2"
_API_TIMEOUT = 60  # seconds per call

_HKT = ZoneInfo("Asia/Hong_Kong")


# ─── System Prompt ───
_SYSTEM_PROMPT = """You are a probability calibration expert. Your job is to estimate the TRUE probability of prediction market outcomes, independent of current market prices.

Rules:
1. Output ONLY valid JSON — no markdown, no explanation outside JSON.
2. Estimate probability as a decimal between 0.01 and 0.99.
3. Rate your confidence from 0.0 (pure guess) to 1.0 (very certain).
4. Be calibrated: when you say 70%, events should happen ~70% of the time.
5. Consider base rates, current data, and known biases.
6. Do NOT anchor to the current market price — form your own estimate first.

Output format:
{
  "probability": 0.XX,
  "confidence": 0.XX,
  "reasoning": "Brief 1-3 sentence explanation",
  "key_factors": ["factor1", "factor2"],
  "data_quality": "high|medium|low"
}"""


def _call_claude(system: str, user: str) -> dict:
    """Call LLM with fallback chain. Returns parsed JSON response.

    Chain: Claude Sonnet (PROXY1) → GPT fallback (PROXY1 → PROXY2).
    """
    if not _PROXY_API_KEY and not _PROXY2_API_KEY:
        raise RuntimeError("No proxy API key configured")

    messages = [{"role": "user", "content": user}]

    for model in [AI_MODEL, _FALLBACK_MODEL]:
        is_anthropic = model.startswith("claude-")
        proxies = [(_PROXY_BASE_URL, _PROXY_API_KEY)]
        if not is_anthropic and _PROXY2_BASE_URL and _PROXY2_API_KEY:
            proxies.append((_PROXY2_BASE_URL, _PROXY2_API_KEY))

        for proxy_url, proxy_key in proxies:
            try:
                if is_anthropic:
                    url = f"{proxy_url}/messages"
                    payload = json.dumps({
                        "model": model, "max_tokens": AI_MAX_TOKENS,
                        "temperature": AI_TEMPERATURE,
                        "system": system, "messages": messages,
                    }).encode("utf-8")
                    headers = {"Content-Type": "application/json",
                               "Authorization": f"Bearer {proxy_key}",
                               "anthropic-version": "2023-06-01"}
                else:
                    url = f"{proxy_url}/chat/completions"
                    oai_msgs = [{"role": "system", "content": system}] + messages
                    payload = json.dumps({
                        "model": model, "max_tokens": AI_MAX_TOKENS,
                        "temperature": AI_TEMPERATURE,
                        "messages": oai_msgs,
                    }).encode("utf-8")
                    headers = {"Content-Type": "application/json",
                               "Authorization": f"Bearer {proxy_key}"}

                req = urllib.request.Request(url, data=payload, method="POST",
                                             headers=headers)
                with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                    data = json.loads(resp.read().decode())

                # Extract text
                if is_anthropic:
                    content = data.get("content", [])
                    parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    text = "\n".join(parts).strip()
                else:
                    text = data["choices"][0]["message"]["content"].strip()

                logger.info("Model %s succeeded via %s", model, proxy_url)

                # Parse JSON (handle markdown code blocks)
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Model %s returned non-JSON: %s", model, text[:200])
                    continue  # try next model/proxy
            except Exception as e:
                logger.warning("Model %s via %s failed: %s", model, proxy_url, e)
                continue

    logger.error("All models in fallback chain failed")
    return {"probability": 0.5, "confidence": 0.0, "reasoning": "All models failed"}


# ─── Data Gatherers ───

def _get_crypto_context() -> str:
    """Gather crypto market data from existing AXC sources."""
    parts = []

    # SCAN_CONFIG: prices, ATR, funding (direct read, no trader_cycle dep)
    try:
        scan_path = os.path.join(AXC_HOME, "shared", "SCAN_CONFIG.md")
        cfg: dict = {}
        if os.path.exists(scan_path):
            with open(scan_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.match(r"^(\w+):\s*(.+)$", line)
                    if m:
                        k, v = m.group(1), m.group(2).strip()
                        try:
                            cfg[k] = float(v) if "." in v else int(v)
                        except ValueError:
                            cfg[k] = v
        if cfg:
            btc = cfg.get("BTC_price", "N/A")
            eth = cfg.get("ETH_price", "N/A")
            btc_atr = cfg.get("BTC_ATR", "N/A")
            eth_atr = cfg.get("ETH_ATR", "N/A")
            btc_fund = cfg.get("BTC_funding_last", "N/A")
            eth_fund = cfg.get("ETH_funding_last", "N/A")
            parts.append(
                f"Current prices: BTC=${btc}, ETH=${eth}\n"
                f"ATR(14,4H): BTC={btc_atr}, ETH={eth_atr}\n"
                f"Funding rates: BTC={btc_fund}, ETH={eth_fund}"
            )
    except OSError:
        pass

    # News sentiment
    try:
        sentiment_path = os.path.join(AXC_HOME, "shared", "news_sentiment.json")
        if os.path.exists(sentiment_path):
            with open(sentiment_path, "r") as f:
                sentiment = json.load(f)
            overall = sentiment.get("overall_sentiment", "unknown")
            impact = sentiment.get("overall_impact", 0)
            stale = sentiment.get("stale", True)
            summary = sentiment.get("summary", "")
            narratives = sentiment.get("key_narratives", [])[:5]

            stale_tag = " (STALE >1h)" if stale else ""
            parts.append(f"News sentiment: {overall} (impact:{impact}/100){stale_tag}")
            if summary:
                parts.append(f"Summary: {summary}")
            if narratives:
                narr_text = "\n".join(
                    f"  - [{n.get('s', '?')}] {n.get('text', '')[:80]}"
                    for n in narratives
                )
                parts.append(f"Recent narratives:\n{narr_text}")
    except (json.JSONDecodeError, IOError):
        pass

    return "\n\n".join(parts) if parts else "No crypto market data available."


def _build_user_prompt(market: PolyMarket, context_data: str) -> str:
    """Build the user prompt for Claude."""
    return f"""Evaluate this prediction market and estimate the TRUE probability of the first outcome.

MARKET: {market.title}
DESCRIPTION: {market.description[:500] if market.description else 'No description'}
OUTCOMES: {', '.join(market.outcomes) if market.outcomes else 'Yes / No'}
CURRENT MARKET PRICE (first outcome): {market.yes_price:.4f} (= {market.yes_price*100:.1f}% implied probability)
RESOLUTION DATE: {market.end_date or 'Unknown'}
VOLUME: ${market.volume:,.0f}
LIQUIDITY: ${market.liquidity:,.0f}

RELEVANT DATA:
{context_data}

Estimate the TRUE probability of the FIRST outcome. Do NOT anchor to the market price of {market.yes_price:.4f}. Form your own independent assessment based on the data provided.

IMPORTANT: If the price differs significantly from your estimate, ask yourself: WHY hasn't arbitrage corrected it? Consider who would be on the other side of this trade.

Remember: output ONLY valid JSON."""


# ─── Main Interface ───

def assess_edge(market: PolyMarket) -> EdgeAssessment:
    """Assess a single market's edge using deterministic signals or Claude AI."""
    # Crypto 15M: triple signal source (indicator + CVD + microstructure) → AI fallback
    if market.category == "crypto_15m":
        from .crypto_15m import (
            assess_crypto_15m_edge, build_15m_ai_context,
            _fetch_15m_indicators, _gather_btc_context,
            parse_crypto_15m_market,
        )
        from polymarket.config.settings import CVD_ENABLED, MICRO_ENABLED

        indicator_result = assess_crypto_15m_edge(market)
        if indicator_result is not None and not indicator_result.signal_source:
            indicator_result.signal_source = "indicator"

        cvd_result = None
        if CVD_ENABLED:
            try:
                from .cvd_strategy import assess_cvd_edge
                cvd_result = assess_cvd_edge(market)
            except Exception as e:
                logger.warning("CVD assessment failed: %s", e)

        micro_result = None
        if MICRO_ENABLED:
            try:
                from .microstructure_strategy import assess_microstructure_edge
                micro_result = assess_microstructure_edge(market)
            except Exception as e:
                logger.warning("Microstructure assessment failed: %s", e)

        # Take the candidate with highest edge (all must pass their own thresholds)
        candidates = [r for r in [indicator_result, cvd_result, micro_result] if r is not None]
        if candidates:
            best = max(candidates, key=lambda x: x.edge_pct)
            if len(candidates) > 1:
                parts = []
                sides = set()
                for label, res in [("indicator", indicator_result), ("cvd", cvd_result), ("micro", micro_result)]:
                    if res is not None:
                        parts.append(f"{label}={res.side}@{res.edge_pct:.3f}")
                        sides.add(res.side)
                # Warn if signals disagree on direction
                if len(sides) > 1:
                    logger.warning("15M SIGNAL CONFLICT: %s → picked %s (%s)",
                                   ", ".join(parts), best.signal_source, best.side)
                else:
                    logger.info("15M multi-signal: %s → picked %s",
                                ", ".join(parts), best.signal_source)
            return best

        logger.info("15M deterministic below threshold, using AI: %s",
                     market.title[:50])
        # Outcome order safety check — same guard as crypto_15m.py:477
        # Without this, reversed outcomes ("Down","Up") would cause AI to buy wrong direction
        if not market.outcomes or market.outcomes[0].lower() not in ("up", "yes"):
            logger.warning("15M AI fallback: outcome[0]=%s (expected up/yes), skipping",
                           market.outcomes[0] if market.outcomes else "empty")
            return None

        # Prepare rich context for AI fallback — use parsed symbol, not hardcoded BTC
        parsed_15m = parse_crypto_15m_market(market.title)
        symbol = parsed_15m["symbol"] if parsed_15m else "BTCUSDT"
        indicators = _fetch_15m_indicators(symbol)
        btc_ctx = _gather_btc_context()
        context_data = build_15m_ai_context(market, indicators, btc_ctx)
        # Fall through to AI call below

    # Gather context data based on category
    # (crypto_15m already set context_data above via build_15m_ai_context)
    if market.category == "crypto_15m":
        pass  # context_data already set
    elif market.category == "crypto":
        context_data = _get_crypto_context()
    else:
        context_data = "No specific data available for this category."

    # Call Claude
    try:
        result = _call_claude(_SYSTEM_PROMPT, _build_user_prompt(market, context_data))
    except RuntimeError as e:
        logger.warning("AI assessment failed for %s: %s", market.title[:40], e)
        return EdgeAssessment(
            condition_id=market.condition_id,
            title=market.title,
            category=market.category,
            market_price=market.yes_price,
            reasoning=f"AI call failed: {e}",
        )

    # Parse result
    ai_prob = float(result.get("probability", 0.5))
    ai_prob = max(0.01, min(0.99, ai_prob))
    confidence = float(result.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    reasoning = result.get("reasoning", "")
    key_factors = result.get("key_factors", [])
    data_quality = result.get("data_quality", "low")

    # Calculate edge
    # Positive edge = AI thinks Yes is more likely than market
    # Negative edge = AI thinks Yes is less likely (→ buy No)
    raw_edge = ai_prob - market.yes_price

    # Determine side
    if raw_edge > 0:
        side = "YES"
        edge = raw_edge
    else:
        side = "NO"
        edge = -raw_edge  # flip to positive

    return EdgeAssessment(
        condition_id=market.condition_id,
        title=market.title,
        category=market.category,
        market_price=market.yes_price,
        ai_probability=ai_prob,
        edge=raw_edge,
        edge_pct=edge,
        confidence=confidence,
        side=side,
        reasoning=reasoning,
        data_sources=key_factors if key_factors else [data_quality],
    )


def assess_markets(markets: list[PolyMarket], max_assessments: int = 5,
                   verbose: bool = False) -> list[EdgeAssessment]:
    """Assess multiple markets. Sorted by liquidity — high liquidity first."""
    sorted_markets = sorted(markets, key=lambda m: m.liquidity, reverse=True)
    candidates = sorted_markets[:max_assessments]

    assessments = []
    for i, market in enumerate(candidates):
        if verbose:
            print(f"      [{i+1}/{len(candidates)}] Assessing: {market.title[:50]}...")

        assessment = assess_edge(market)
        if assessment is None:
            logger.warning("assess_edge returned None for %s — skipping", market.title[:40])
            continue
        assessments.append(assessment)

        if verbose:
            if assessment.ai_probability > 0:
                print(
                    f"        AI: {assessment.ai_probability:.1%} vs Market: {assessment.market_price:.1%} "
                    f"→ edge: {assessment.edge:+.1%} ({assessment.side}) "
                    f"conf: {assessment.confidence:.2f}"
                )
            else:
                print(f"        Failed: {assessment.reasoning[:60]}")

    return assessments
