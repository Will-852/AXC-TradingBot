"""
edge_finder.py — Market edge detection: deterministic + AI paths

設計決定：
- Weather 市場：確定性計算（forecast + normal CDF），零 AI 成本
  原因：天氣預報本身已係校準概率來源，用 Claude 估概率多餘且唔夠準
  Reddit 研究：gopfan2 ($2M+) 同 meropi ($30K) 用 GFS ensemble，唔係 AI
- Crypto 市場：Claude AI 概率估算（需要推理能力）
- Weather parse 失敗 → fallback 到 Claude（唔會 silent failure）
- 追蹤 calibration（predicted vs actual）方便日後校正
"""

import json
import logging
import math
import os
import re
import urllib.request
import urllib.error
from datetime import date
from typing import Optional

from dotenv import load_dotenv

from ..config.categories import WEATHER_CITIES
from ..config.settings import (
    AXC_HOME, SECRETS_PATH, AI_MODEL, AI_MAX_TOKENS, AI_TEMPERATURE,
    WEATHER_SIGMA_BY_LEAD, WEATHER_CONFIDENCE_BY_LEAD,
)
from ..core.context import PolyMarket, EdgeAssessment

logger = logging.getLogger(__name__)

# ─── API Config (loaded once) ───
load_dotenv(SECRETS_PATH)
_PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
_PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
_API_TIMEOUT = 60  # seconds per call


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
    """Call Claude via proxy API. Returns parsed JSON response."""
    if not _PROXY_API_KEY:
        raise RuntimeError("PROXY_API_KEY not set — cannot call Claude")

    url = f"{_PROXY_BASE_URL}/messages"
    payload = json.dumps({
        "model": AI_MODEL,
        "max_tokens": AI_MAX_TOKENS,
        "temperature": AI_TEMPERATURE,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_PROXY_API_KEY}",
        "anthropic-version": "2023-06-01",
    })

    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500] if hasattr(e, "read") else ""
        raise RuntimeError(f"Claude API error {e.code}: {body}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Claude API connection error: {e}")

    # Extract text from Anthropic response
    content = data.get("content", [])
    parts = [block.get("text", "") for block in content if block.get("type") == "text"]
    text = "\n".join(parts).strip()

    # Parse JSON from response (handle markdown code blocks)
    if text.startswith("```"):
        # Strip ```json ... ```
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON: %s", text[:200])
        return {"probability": 0.5, "confidence": 0.0, "reasoning": "Parse error"}


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


# ─── Weather: Deterministic Forecast-Based Edge ───
# 用天氣預報 + 正態分佈 CDF 計算概率，零 AI 成本

# Regex patterns for weather market title parsing
_RE_EXACT_C = re.compile(
    r"(?:be\s+)?(\d+)\s*°?\s*[Cc]", re.IGNORECASE,
)
_RE_FLOOR_C = re.compile(
    r"(?:≤|<=|below|under|at most)\s*(\d+)\s*°?\s*[Cc]", re.IGNORECASE,
)
_RE_CEIL_C = re.compile(
    r"(?:≥|>=|above|over|at least)\s*(\d+)\s*°?\s*[Cc]", re.IGNORECASE,
)
_RE_RANGE_F = re.compile(
    r"(\d+)\s*[-–]\s*(\d+)\s*°?\s*[Ff]", re.IGNORECASE,
)
_RE_FLOOR_F = re.compile(
    r"(?:≤|<=|below|under|at most)\s*(\d+)\s*°?\s*[Ff]", re.IGNORECASE,
)
_RE_CEIL_F = re.compile(
    r"(?:≥|>=|above|over|at least)\s*(\d+)\s*°?\s*[Ff]", re.IGNORECASE,
)
_RE_EXACT_F = re.compile(
    r"(?:be\s+)?(\d+)\s*°?\s*[Ff]", re.IGNORECASE,
)

# Month name → number for date parsing
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _normal_cdf(x: float) -> float:
    """Standard normal CDF using math.erf. Zero external dependencies."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _parse_weather_market(title: str) -> dict | None:
    """Parse weather market title → structured bucket info.

    Returns dict with keys: city, lat, lon, unit, date, threshold_low,
    threshold_high, bucket_type. Returns None if parse fails.
    """
    title_lower = title.lower()

    # ── Find city ──
    city_key = None
    for name in sorted(WEATHER_CITIES, key=len, reverse=True):
        if name in title_lower:
            city_key = name
            break
    if city_key is None:
        return None

    lat, lon, unit = WEATHER_CITIES[city_key]

    # ── Parse date: "March 20", "Mar 20", "March 20, 2026" ──
    target_date = None
    date_pat = re.compile(
        r"(january|february|march|april|may|june|july|august|september|october|november|december"
        r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    )
    m = date_pat.search(title)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else date.today().year
        try:
            target_date = date(year, month, day).isoformat()
        except (ValueError, TypeError):
            return None
    else:
        return None

    # ── Parse temperature bucket ──
    # Order matters: check floor/ceiling/range before exact (more specific first)
    bucket_type = None
    threshold_low = None
    threshold_high = None

    if unit == "C":
        m_floor = _RE_FLOOR_C.search(title)
        m_ceil = _RE_CEIL_C.search(title)
        if m_floor:
            bucket_type = "floor"
            threshold_high = float(m_floor.group(1))
        elif m_ceil:
            bucket_type = "ceiling"
            threshold_low = float(m_ceil.group(1))
        else:
            m_exact = _RE_EXACT_C.search(title)
            if m_exact:
                bucket_type = "exact"
                val = float(m_exact.group(1))
                threshold_low = val - 0.5
                threshold_high = val + 0.5
    else:  # "F"
        m_range = _RE_RANGE_F.search(title)
        m_floor = _RE_FLOOR_F.search(title)
        m_ceil = _RE_CEIL_F.search(title)
        if m_floor:
            bucket_type = "floor"
            threshold_high = float(m_floor.group(1))
        elif m_ceil:
            bucket_type = "ceiling"
            threshold_low = float(m_ceil.group(1))
        elif m_range:
            bucket_type = "range"
            threshold_low = float(m_range.group(1)) - 0.5
            threshold_high = float(m_range.group(2)) + 0.5
        else:
            m_exact = _RE_EXACT_F.search(title)
            if m_exact:
                bucket_type = "exact"
                val = float(m_exact.group(1))
                threshold_low = val - 0.5
                threshold_high = val + 0.5

    if bucket_type is None:
        return None

    return {
        "city": city_key,
        "lat": lat,
        "lon": lon,
        "unit": unit,
        "date": target_date,
        "threshold_low": threshold_low,
        "threshold_high": threshold_high,
        "bucket_type": bucket_type,
    }


def _fetch_forecast(lat: float, lon: float, target_date: str,
                    fahrenheit: bool = False) -> float | None:
    """Fetch temperature_2m_max from Open-Meteo free API.

    Returns forecast high temp for target_date, or None on failure.
    API always returns °C by default; pass fahrenheit=True for °F cities.
    """
    temp_unit = "&temperature_unit=fahrenheit" if fahrenheit else ""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&timezone=auto&forecast_days=7"
        f"{temp_unit}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-Trading/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        maxes = daily.get("temperature_2m_max", [])

        for i, d in enumerate(dates):
            if d == target_date and i < len(maxes) and maxes[i] is not None:
                return float(maxes[i])

        logger.info("Target date %s not in Open-Meteo response (dates: %s)",
                     target_date, dates)
        return None

    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
        logger.warning("Open-Meteo fetch error: %s", e)
        return None


def _get_forecast_sigma(lead_days: int) -> float:
    """Forecast uncertainty σ by lead time. Falls back to max σ for >7 days."""
    clamped = max(1, min(lead_days, 7))
    return WEATHER_SIGMA_BY_LEAD.get(clamped, 3.5)


def _compute_bucket_probability(
    forecast_temp: float,
    sigma: float,
    bucket_low: float | None,
    bucket_high: float | None,
) -> float:
    """P(temp falls in bucket) using normal CDF.

    bucket_low=None → floor bucket (−∞, high]
    bucket_high=None → ceiling bucket [low, +∞)
    Both set → range/exact bucket [low, high)
    """
    if bucket_low is None and bucket_high is not None:
        # Floor: P(T ≤ high)
        return _normal_cdf((bucket_high - forecast_temp) / sigma)
    elif bucket_high is None and bucket_low is not None:
        # Ceiling: P(T ≥ low)
        return 1.0 - _normal_cdf((bucket_low - forecast_temp) / sigma)
    elif bucket_low is not None and bucket_high is not None:
        # Range/Exact: P(low ≤ T < high)
        p_high = _normal_cdf((bucket_high - forecast_temp) / sigma)
        p_low = _normal_cdf((bucket_low - forecast_temp) / sigma)
        return max(0.0, p_high - p_low)
    else:
        return 0.5  # Should never happen


def assess_weather_edge(market: PolyMarket) -> EdgeAssessment | None:
    """Deterministic weather edge assessment — no Claude API needed.

    Parse title → fetch forecast → compute probability via normal CDF.
    Returns EdgeAssessment on success, None if parse/fetch fails (→ fallback to AI).
    """
    parsed = _parse_weather_market(market.title)
    if parsed is None:
        logger.info("Weather parse failed for: %s", market.title[:60])
        return None

    # Lead time calculation
    try:
        target = date.fromisoformat(parsed["date"])
        lead_days = (target - date.today()).days
    except (ValueError, TypeError):
        logger.info("Invalid date in parsed weather market: %s", parsed["date"])
        return None

    if lead_days < 0:
        logger.info("Weather market date in past: %s", parsed["date"])
        return None

    # Fetch forecast (°F for US cities, °C otherwise)
    fahrenheit = parsed["unit"] == "F"
    forecast_temp = _fetch_forecast(
        parsed["lat"], parsed["lon"], parsed["date"], fahrenheit=fahrenheit,
    )
    if forecast_temp is None:
        return None

    # Compute probability
    sigma = _get_forecast_sigma(lead_days)
    # °F cities: σ in °C needs conversion to °F (×1.8)
    if fahrenheit:
        sigma *= 1.8

    ai_prob = _compute_bucket_probability(
        forecast_temp, sigma, parsed["threshold_low"], parsed["threshold_high"],
    )
    ai_prob = max(0.01, min(0.99, ai_prob))

    # Confidence from lookup table
    lead_clamped = max(1, min(lead_days, 7))
    confidence = WEATHER_CONFIDENCE_BY_LEAD.get(lead_clamped, 0.40)

    # Edge calculation (same logic as AI path)
    raw_edge = ai_prob - market.yes_price
    if raw_edge > 0:
        side = "YES"
        edge_pct = raw_edge
    else:
        side = "NO"
        edge_pct = -raw_edge

    reasoning = (
        f"Forecast: {forecast_temp:.1f}°{'F' if fahrenheit else 'C'} "
        f"(lead {lead_days}d, σ={sigma:.1f}). "
        f"Bucket [{parsed['threshold_low']}, {parsed['threshold_high']}] "
        f"({parsed['bucket_type']}). "
        f"P={ai_prob:.3f} vs market={market.yes_price:.3f}"
    )

    return EdgeAssessment(
        condition_id=market.condition_id,
        title=market.title,
        category=market.category,
        market_price=market.yes_price,
        ai_probability=ai_prob,
        edge=raw_edge,
        edge_pct=edge_pct,
        confidence=confidence,
        side=side,
        reasoning=reasoning,
        data_sources=["open-meteo-forecast", f"lead_{lead_days}d"],
    )


def _get_weather_context(title: str) -> str:
    """Fetch weather forecast from Open-Meteo for AI fallback path.

    Used when _parse_weather_market fails and we fall back to Claude.
    Uses WEATHER_CITIES from categories.py for city lookup.
    """
    title_lower = title.lower()
    lat, lon = None, None
    city_name = ""
    for city, (clat, clon, _unit) in WEATHER_CITIES.items():
        if city in title_lower:
            lat, lon = clat, clon
            city_name = city.title()
            break

    if lat is None:
        return "Could not determine city for weather forecast."

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
            f"&timezone=auto&forecast_days=7"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-Trading/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        maxes = daily.get("temperature_2m_max", [])
        mins = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])

        lines = [f"Weather forecast for {city_name} (next 7 days):"]
        for i, d in enumerate(dates):
            hi = maxes[i] if i < len(maxes) else "?"
            lo = mins[i] if i < len(mins) else "?"
            rain = precip[i] if i < len(precip) else "?"
            lines.append(f"  {d}: High {hi}°C, Low {lo}°C, Precip {rain}mm")

        return "\n".join(lines)

    except Exception as e:
        logger.warning("Open-Meteo fetch error for %s: %s", city_name, e)
        return f"Weather data unavailable for {city_name}: {e}"


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

Remember: output ONLY valid JSON."""


# ─── Main Interface ───

def assess_edge(market: PolyMarket) -> EdgeAssessment:
    """Assess a single market. Weather uses deterministic forecast; others use Claude AI.

    Weather dispatch: parse title → forecast → normal CDF → EdgeAssessment.
    Falls back to Claude AI if weather parse fails or forecast unavailable.
    """
    # Crypto 15M: deterministic indicator path → AI fallback
    if market.category == "crypto_15m":
        from .crypto_15m import (
            assess_crypto_15m_edge, build_15m_ai_context,
            _fetch_15m_indicators, _gather_btc_context,
        )
        result = assess_crypto_15m_edge(market)
        if result is not None:
            return result
        logger.info("15M deterministic below threshold, using AI: %s",
                     market.title[:50])
        # Prepare rich context for AI fallback
        indicators = _fetch_15m_indicators("BTCUSDT")
        btc_ctx = _gather_btc_context()
        context_data = build_15m_ai_context(market, indicators, btc_ctx)
        # Fall through to AI call below

    # Weather: try deterministic path first (zero AI cost)
    elif market.category == "weather":
        result = assess_weather_edge(market)
        if result is not None:
            return result
        logger.info("Weather deterministic failed, falling back to AI: %s",
                     market.title[:50])

    # Gather context data based on category
    # (crypto_15m already set context_data above via build_15m_ai_context)
    if market.category == "crypto_15m":
        pass  # context_data already set
    elif market.category == "crypto":
        context_data = _get_crypto_context()
    elif market.category == "weather":
        context_data = _get_weather_context(market.title)
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
    """Assess multiple markets. Respects max_assessments limit.

    Markets sorted by liquidity before assessment — high liquidity first.
    """
    # Sort by liquidity (best markets first, since we have an API call budget)
    sorted_markets = sorted(markets, key=lambda m: m.liquidity, reverse=True)
    candidates = sorted_markets[:max_assessments]

    assessments = []
    for i, market in enumerate(candidates):
        if verbose:
            print(f"      [{i+1}/{len(candidates)}] Assessing: {market.title[:50]}...")

        assessment = assess_edge(market)
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
