"""
crypto_15m.py — 15-minute crypto binary market edge detection

設計決定：
- 兩路並行：deterministic indicator scoring → AI fallback
  原因：15M 市場時間緊迫，indicator 快（<1s）；AI 慢（~5s）但能捕捉 indicator 漏嘅 edge
- 只用 existing 數據源（SCAN_CONFIG + indicator_calc.py subprocess）
- Outcome 映射安全檢查：assert outcomes[0] is "Up"/"Yes"，防止買反方向
- Score → P(Up) 用 tanh 壓縮，clamp [0.15, 0.85]，避免極端概率
"""

import json
import logging
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config.categories import CRYPTO_15M_COINS, _RE_CRYPTO_15M
from ..config.settings import (
    AXC_HOME,
    CRYPTO_15M_ENABLED_COINS,
    CRYPTO_15M_MIN_LEAD_MIN,
    CRYPTO_15M_MAX_LEAD_MIN,
    CRYPTO_15M_MIN_EDGE_PCT,
    CRYPTO_15M_INDICATOR_THRESHOLD,
)
from ..core.context import PolyMarket, EdgeAssessment

logger = logging.getLogger(__name__)

# ─── Constants ───
_ET_TZ = ZoneInfo("America/New_York")

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Indicator weights for _score_direction()
_WEIGHTS = {
    "rsi": 0.20,
    "macd": 0.15,
    "bb": 0.15,
    "ema": 0.10,
    "stoch": 0.10,
    "vwap": 0.10,
    "funding": 0.10,
    "sentiment": 0.10,
}

# indicator_calc.py subprocess timeout
_INDICATOR_TIMEOUT_S = 15


# ─── Title Parsing ───

def parse_crypto_15m_market(title: str) -> dict | None:
    """Parse 15M binary market title → structured info.

    Returns dict with: coin, symbol, start_time, end_time, lead_minutes.
    Returns None if parse fails or coin not enabled.
    """
    m = _RE_CRYPTO_15M.search(title)
    if not m:
        return None

    coin = m.group(1).lower()
    symbol = CRYPTO_15M_COINS.get(coin)
    if not symbol:
        logger.debug("15M coin not in mapping: %s", coin)
        return None

    if coin not in CRYPTO_15M_ENABLED_COINS:
        logger.debug("15M coin not enabled: %s", coin)
        return None

    # Parse date + times
    month_str = m.group(2).lower()
    day = int(m.group(3))
    start_hour = int(m.group(4))
    start_min = int(m.group(5))
    start_ampm = m.group(6).upper()
    end_hour = int(m.group(7))
    end_min = int(m.group(8))
    end_ampm = m.group(9).upper()

    month = _MONTHS.get(month_str)
    if month is None:
        return None

    # Convert 12h → 24h
    start_h24 = _to_24h(start_hour, start_ampm)
    end_h24 = _to_24h(end_hour, end_ampm)

    # Build ET datetime
    now = datetime.now(tz=_ET_TZ)
    year = now.year

    try:
        start_time = datetime(year, month, day, start_h24, start_min,
                              tzinfo=_ET_TZ)
        end_time = datetime(year, month, day, end_h24, end_min,
                            tzinfo=_ET_TZ)
    except ValueError:
        logger.debug("Invalid date/time in 15M title: %s", title[:60])
        return None

    # Lead time (informational — 15M markets are continuous, no strict window)
    lead_seconds = (start_time - now).total_seconds()
    lead_minutes = lead_seconds / 60

    # Skip markets that already ended (negative lead beyond window duration)
    if lead_minutes < -15:
        logger.debug("15M market already resolved: %.0f min ago", -lead_minutes)
        return None

    return {
        "coin": coin,
        "symbol": symbol,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "lead_minutes": round(lead_minutes, 1),
        "month": month,
        "day": day,
    }


def _to_24h(hour: int, ampm: str) -> int:
    """Convert 12-hour time to 24-hour."""
    if ampm == "AM":
        return 0 if hour == 12 else hour
    else:  # PM
        return hour if hour == 12 else hour + 12


# ─── Data Fetching ───

def _fetch_15m_indicators(symbol: str) -> dict | None:
    """Subprocess call to indicator_calc.py for 15m indicators.

    Returns indicators dict or None on failure. Uses python3.11 because
    tradingview_indicators requires match syntax (3.10+) and system
    python3.11 is the installed version.
    """
    script_path = os.path.join(AXC_HOME, "scripts", "indicator_calc.py")
    if not os.path.exists(script_path):
        logger.warning("indicator_calc.py not found: %s", script_path)
        return None

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3.11",
             script_path,
             "--symbol", symbol,
             "--interval", "15m",
             "--limit", "50",
             "--mode", "full"],
            capture_output=True, text=True,
            timeout=_INDICATOR_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.warning("indicator_calc.py timed out for %s 15m", symbol)
        return None
    except FileNotFoundError:
        logger.warning("python3.11 not found at /opt/homebrew/bin/python3.11")
        return None

    if result.returncode != 0:
        logger.warning("indicator_calc.py failed (rc=%d): %s",
                       result.returncode, result.stderr[:200])
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("indicator_calc.py non-JSON output: %s",
                       result.stdout[:200])
        return None

    if "error" in data:
        logger.warning("indicator_calc.py error: %s", data["error"])
        return None

    indicators = data.get("indicators")

    # Freshness check: indicator price should be close to recent market price
    # If indicator_calc returns stale data, the price would be materially different
    if indicators and indicators.get("price"):
        ind_price = indicators["price"]
        # Quick spot check via the same subprocess cache — if price is >2% off
        # from what we'd expect, data is likely stale. Log warning but don't block
        # (we can't easily get a reference price here without another API call).
        logger.debug("Indicator price: $%.0f", ind_price)

    return indicators


def _gather_btc_context() -> dict:
    """Read SCAN_CONFIG + news_sentiment + TRADE_STATE for BTC context.

    Returns dict with keys: price, atr, support, resistance, funding,
    sentiment, market_mode. Missing values → None.
    """
    ctx: dict = {
        "price": None, "atr": None, "support": None, "resistance": None,
        "funding": None, "sentiment": None, "sentiment_impact": None,
        "market_mode": None,
    }

    # SCAN_CONFIG.md
    scan_path = os.path.join(AXC_HOME, "shared", "SCAN_CONFIG.md")
    try:
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
                            val = float(v)
                        except ValueError:
                            val = v
                        if k == "BTC_price":
                            ctx["price"] = val
                        elif k == "BTC_ATR":
                            ctx["atr"] = val
                        elif k == "BTC_support":
                            ctx["support"] = val
                        elif k == "BTC_resistance":
                            ctx["resistance"] = val
                        elif k == "BTC_funding_last":
                            ctx["funding"] = val
    except OSError as e:
        logger.debug("SCAN_CONFIG read error: %s", e)

    # News sentiment
    sentiment_path = os.path.join(AXC_HOME, "shared", "news_sentiment.json")
    try:
        if os.path.exists(sentiment_path):
            with open(sentiment_path, "r") as f:
                sentiment = json.load(f)
            ctx["sentiment"] = sentiment.get("overall_sentiment")
            ctx["sentiment_impact"] = sentiment.get("overall_impact")
    except (json.JSONDecodeError, IOError) as e:
        logger.debug("news_sentiment read error: %s", e)

    # TRADE_STATE.json → market_mode
    state_path = os.path.join(AXC_HOME, "shared", "TRADE_STATE.json")
    try:
        if os.path.exists(state_path):
            with open(state_path, "r") as f:
                state = json.load(f)
            ctx["market_mode"] = state.get("system", {}).get("market_mode")
    except (json.JSONDecodeError, IOError) as e:
        logger.debug("TRADE_STATE read error: %s", e)

    return ctx


# ─── Scoring ───

def _score_direction(indicators: dict, btc_ctx: dict) -> tuple[float, list[str]]:
    """Core scoring: indicators → P(Up).

    Returns (p_up, reasons) where:
    - p_up: probability of Up [0.15, 0.85]
    - reasons: list of factor explanations
    """
    score = 0.0  # positive = bullish, negative = bearish
    reasons = []

    def _safe(val):
        """Convert None to 0.0 for safe arithmetic."""
        return float(val) if val is not None else 0.0

    # ── RSI (weight: 0.20) ──
    rsi = indicators.get("rsi")
    if rsi is not None:
        if rsi < 30:
            s = _WEIGHTS["rsi"] * (30 - rsi) / 30  # stronger signal at lower RSI
            score += s
            reasons.append(f"RSI oversold {rsi:.1f} → bullish (+{s:.3f})")
        elif rsi > 70:
            s = _WEIGHTS["rsi"] * (rsi - 70) / 30
            score -= s
            reasons.append(f"RSI overbought {rsi:.1f} → bearish (-{s:.3f})")
        else:
            # Mild lean based on RSI position relative to 50
            s = _WEIGHTS["rsi"] * 0.3 * (rsi - 50) / 50
            score += s
            reasons.append(f"RSI neutral {rsi:.1f} ({s:+.3f})")

    # ── MACD histogram (weight: 0.15) ──
    macd_hist = indicators.get("macd_hist")
    macd_hist_prev = indicators.get("macd_hist_prev")
    if macd_hist is not None:
        # Direction + momentum
        direction = 1.0 if macd_hist > 0 else -1.0
        expanding = 1.0
        if macd_hist_prev is not None:
            expanding = 1.2 if abs(macd_hist) > abs(macd_hist_prev) else 0.8
        s = _WEIGHTS["macd"] * direction * expanding * 0.5
        s = max(-_WEIGHTS["macd"], min(_WEIGHTS["macd"], s))
        score += s
        reasons.append(f"MACD hist {macd_hist:.2f} (prev {macd_hist_prev}) ({s:+.3f})")

    # ── BB position (weight: 0.15) ──
    price = _safe(indicators.get("price"))
    bb_upper = indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lower")
    bb_basis = indicators.get("bb_basis")
    if all(v is not None for v in [bb_upper, bb_lower, bb_basis]) and bb_upper > bb_lower:
        bb_range = bb_upper - bb_lower
        bb_pos = (price - bb_lower) / bb_range  # 0=lower, 1=upper
        # Near lower → Up bias; near upper → Down bias
        s = _WEIGHTS["bb"] * (0.5 - bb_pos)
        score += s
        reasons.append(f"BB pos {bb_pos:.2f} ({s:+.3f})")

    # ── EMA trend (weight: 0.10) ──
    ema_fast = indicators.get("ema_fast")
    ema_slow = indicators.get("ema_slow")
    if ema_fast is not None and ema_slow is not None and ema_slow != 0:
        diff_pct = (ema_fast - ema_slow) / ema_slow
        s = _WEIGHTS["ema"] * max(-1, min(1, diff_pct * 100))
        score += s
        reasons.append(f"EMA fast-slow {diff_pct:+.4f} ({s:+.3f})")

    # ── Stochastic K/D (weight: 0.10) ──
    stoch_k = indicators.get("stoch_k")
    stoch_d = indicators.get("stoch_d")
    if stoch_k is not None:
        if stoch_k < 20:
            s = _WEIGHTS["stoch"]
            score += s
            reasons.append(f"Stoch oversold K={stoch_k:.1f} → bullish (+{s:.3f})")
        elif stoch_k > 80:
            s = _WEIGHTS["stoch"]
            score -= s
            reasons.append(f"Stoch overbought K={stoch_k:.1f} → bearish (-{s:.3f})")
        elif stoch_d is not None:
            # K > D = bullish momentum
            s = _WEIGHTS["stoch"] * 0.3 * (1 if stoch_k > stoch_d else -1)
            score += s
            reasons.append(f"Stoch K={stoch_k:.1f} D={stoch_d:.1f} ({s:+.3f})")

    # ── VWAP position (weight: 0.10) ──
    vwap = indicators.get("vwap")
    if price > 0 and vwap is not None and vwap > 0:
        vwap_diff = (price - vwap) / vwap
        s = _WEIGHTS["vwap"] * max(-1, min(1, vwap_diff * 50))
        score += s
        reasons.append(f"VWAP diff {vwap_diff:+.4f} ({s:+.3f})")

    # ── Funding rate (weight: 0.10) ──
    funding = btc_ctx.get("funding")
    if funding is not None:
        try:
            funding_val = float(funding)
            # Positive funding → crowded long → lean bearish
            s = _WEIGHTS["funding"] * max(-1, min(1, -funding_val * 1000))
            score += s
            reasons.append(f"Funding {funding_val:+.6f} ({s:+.3f})")
        except (ValueError, TypeError):
            pass

    # ── News sentiment (weight: 0.10) ──
    sentiment = btc_ctx.get("sentiment")
    if sentiment is not None:
        sent_map = {"very_bullish": 1.0, "bullish": 0.5, "neutral": 0.0,
                     "bearish": -0.5, "very_bearish": -1.0}
        sent_val = sent_map.get(str(sentiment).lower(), 0.0)
        s = _WEIGHTS["sentiment"] * sent_val
        score += s
        reasons.append(f"Sentiment {sentiment} ({s:+.3f})")

    # ── Market mode adjustment ──
    market_mode = btc_ctx.get("market_mode")
    if market_mode == "RANGE":
        score *= 0.7  # 收斂 — ranging market = indicators less reliable
        reasons.append("RANGE mode: score ×0.7")
    elif market_mode == "BREAKOUT":
        score *= 1.2  # 放大 — breakout amplifies directional signal
        reasons.append("BREAKOUT mode: score ×1.2")

    # ── Score → P(Up) mapping ──
    # tanh 壓縮，0.5 中心，最大偏移 ±0.3
    p_up = 0.5 + 0.3 * math.tanh(score * 2)
    p_up = max(0.15, min(0.85, p_up))

    return p_up, reasons


# ─── Prediction Logger ───

_PREDICTION_LOG_PATH = os.path.join(
    os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading")),
    "logs", "poly_15m_predictions.jsonl",
)
_HKT_TZ = ZoneInfo("Asia/Hong_Kong")


def log_15m_prediction(
    *,
    p_up: float,
    market_price: float,
    edge_pct: float,
    confidence: float,
    acted: bool,
    skip_reason: str | None,
    source: str,
    title: str,
    condition_id: str,
    coin: str,
    window_start: str,
    window_end: str,
    indicators: dict,
    market_mode: str | None,
) -> None:
    """Append prediction record to JSONL for calibration tracking.

    Logs every scored 15M market (acted or not) so paper-trade
    calibration data accumulates before going live.
    Atomic write: tempfile + os.replace() not needed for append-only JSONL,
    but we catch IOError to avoid crashing the pipeline.
    """
    now = datetime.now(tz=_HKT_TZ)
    side = "YES" if p_up > market_price else "NO"

    record = {
        "ts": now.isoformat(),
        "condition_id": condition_id,
        "title": title,
        "coin": coin,
        "window_start": window_start,
        "window_end": window_end,
        "p_up": round(p_up, 4),
        "side": side,
        "market_price": round(market_price, 4),
        "edge_pct": round(edge_pct, 4),
        "confidence": round(confidence, 4),
        "acted": acted,
        "skip_reason": skip_reason,
        "source": source,
        "indicators": {k: round(v, 2) if isinstance(v, float) else v
                       for k, v in indicators.items()},
        "market_mode": market_mode,
    }

    try:
        os.makedirs(os.path.dirname(_PREDICTION_LOG_PATH), exist_ok=True)
        with open(_PREDICTION_LOG_PATH, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except IOError as e:
        logger.warning("15M prediction log write failed: %s", e)


# ─── Main Entry ───

def assess_crypto_15m_edge(market: PolyMarket) -> EdgeAssessment | None:
    """Deterministic 15M crypto edge: parse → fetch indicators → score.

    Returns EdgeAssessment if score exceeds threshold, None otherwise
    (caller should fallback to AI).
    """
    # Parse title
    parsed = parse_crypto_15m_market(market.title)
    if parsed is None:
        logger.info("15M parse failed: %s", market.title[:60])
        return None

    # Outcome safety check — "Up"/"Yes" must be first outcome
    if not market.outcomes:
        logger.warning("15M no outcomes — skipping: %s", market.title[:50])
        return None
    first = market.outcomes[0].lower()
    if first not in ("up", "yes"):
        logger.warning("15M outcomes[0]='%s' not 'Up'/'Yes' — skipping: %s",
                       market.outcomes[0], market.title[:50])
        return None

    # Fetch indicators
    indicators = _fetch_15m_indicators(parsed["symbol"])
    if indicators is None:
        logger.info("15M indicators unavailable for %s", parsed["symbol"])
        return None

    # Gather BTC context
    btc_ctx = _gather_btc_context()

    # Score
    p_up, reasons = _score_direction(indicators, btc_ctx)

    # Common kwargs for prediction logger
    _log_kw = dict(
        title=market.title, condition_id=market.condition_id,
        coin=parsed["coin"], window_start=parsed["start_time"],
        window_end=parsed["end_time"], source="deterministic",
        market_price=market.yes_price,
        indicators={k: indicators.get(k) for k in ("rsi", "macd_hist") if indicators.get(k) is not None},
        market_mode=btc_ctx.get("market_mode"),
    )

    # Check threshold — P(Up) needs to be sufficiently far from 0.5
    deviation = abs(p_up - 0.5)
    if deviation < (CRYPTO_15M_INDICATOR_THRESHOLD - 0.5):
        logger.info("15M score too weak: P(Up)=%.3f, deviation=%.3f < %.3f",
                     p_up, deviation, CRYPTO_15M_INDICATOR_THRESHOLD - 0.5)
        log_15m_prediction(p_up=p_up, edge_pct=deviation, confidence=0.0,
                           acted=False,
                           skip_reason=f"deviation {deviation:.2f} < {CRYPTO_15M_INDICATOR_THRESHOLD - 0.5:.2f}",
                           **_log_kw)
        return None

    # Calculate edge vs market price — side determined by edge direction
    # (same pattern as generic crypto path in edge_finder.py)
    raw_edge = p_up - market.yes_price
    if raw_edge > 0:
        side = "YES"
        edge_pct = raw_edge
    else:
        side = "NO"
        edge_pct = -raw_edge

    if edge_pct < CRYPTO_15M_MIN_EDGE_PCT:
        logger.info("15M edge too small: %.3f < %.3f", edge_pct, CRYPTO_15M_MIN_EDGE_PCT)
        log_15m_prediction(p_up=p_up, edge_pct=edge_pct, confidence=0.0,
                           acted=False,
                           skip_reason=f"edge {edge_pct:.3f} < {CRYPTO_15M_MIN_EDGE_PCT}",
                           **_log_kw)
        return None

    # Confidence: based on how many indicators contributed + lead time
    active_indicators = sum(1 for r in reasons if "→" in r or "diff" in r)
    conf_base = min(0.8, 0.4 + active_indicators * 0.05)

    reasoning = (
        f"15M {parsed['coin'].upper()} indicator scoring. "
        f"P(Up)={p_up:.3f} vs market={market.yes_price:.3f}. "
        f"Lead {parsed['lead_minutes']:.0f}min. "
        f"Factors: {'; '.join(reasons[:5])}"
    )

    log_15m_prediction(p_up=p_up, edge_pct=edge_pct, confidence=conf_base,
                       acted=True, skip_reason=None, **_log_kw)

    return EdgeAssessment(
        condition_id=market.condition_id,
        title=market.title,
        category="crypto_15m",
        market_price=market.yes_price,
        ai_probability=p_up,
        edge=raw_edge,
        edge_pct=edge_pct,
        confidence=conf_base,
        side=side,
        reasoning=reasoning,
        data_sources=["indicator_calc_15m", "scan_config", parsed["symbol"]],
        signal_source="indicator",
    )


def build_15m_ai_context(
    market: PolyMarket,
    indicators: dict | None,
    btc_ctx: dict,
) -> str:
    """Build rich context string for AI fallback when deterministic path is inconclusive."""
    parts = []

    parts.append(f"Market: {market.title}")
    parts.append(f"Market price (Up): {market.yes_price:.3f}")

    if indicators:
        ind_lines = []
        for k in ["price", "rsi", "macd_hist", "bb_upper", "bb_lower", "bb_basis",
                   "stoch_k", "stoch_d", "ema_fast", "ema_slow", "vwap", "adx"]:
            v = indicators.get(k)
            if v is not None:
                ind_lines.append(f"  {k}: {v}")
        if ind_lines:
            parts.append("15m Indicators:\n" + "\n".join(ind_lines))

    if btc_ctx.get("price"):
        ctx_lines = [f"BTC price: ${btc_ctx['price']}"]
        if btc_ctx.get("atr"):
            ctx_lines.append(f"ATR: {btc_ctx['atr']}")
        if btc_ctx.get("funding"):
            ctx_lines.append(f"Funding: {btc_ctx['funding']}")
        if btc_ctx.get("market_mode"):
            ctx_lines.append(f"Market mode: {btc_ctx['market_mode']}")
        if btc_ctx.get("sentiment"):
            ctx_lines.append(f"News sentiment: {btc_ctx['sentiment']}")
        parts.append("BTC Context:\n" + "\n".join(f"  {l}" for l in ctx_lines))

    return "\n\n".join(parts)
