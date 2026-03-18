"""
cvd_strategy.py — CVD Divergence signal source for BTC 5-min markets

設計決定：
- 獨立 signal source，同 crypto_15m indicator scoring 並行
- 用 Binance aggTrades API 即時計 CVD（唔經 indicator_calc.py subprocess）
- Divergence detection 跨 5 個 lookback windows (1m-15m)
- Dollar imbalance: 5m vs 15m buy/sell ratio divergence
- Module-level cache（60s TTL）避免同一 cycle 重複 fetch aggTrades
- Core functions (detect/prob/imbalance) shared by backtest via import

依賴：
- backtest.fetch_agg_trades (aggregate_cvd, aggregate_delta_volume)
- Binance futures API (aggTrades, klines — public, 免 auth)
"""

import logging
import math
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from ..core.context import PolyMarket, EdgeAssessment
from ..config.settings import (
    CVD_ENABLED,
    CVD_MIN_EDGE_PCT,
    CVD_LOOKBACK_MINUTES,
    CVD_STRENGTH_SCALE,
    CVD_MIN_PRICE_CHANGE_USD,
)

logger = logging.getLogger(__name__)

# ─── Constants ───
BINANCE_FAPI = "https://fapi.binance.com"
ONE_MIN_MS = 60_000
FIVE_MIN_MS = 300_000
FIFTEEN_MIN_MS = 900_000

LOOKBACK_WINDOWS = {
    "1m": ONE_MIN_MS, "3m": 3 * ONE_MIN_MS, "5m": FIVE_MIN_MS,
    "10m": 10 * ONE_MIN_MS, "15m": FIFTEEN_MIN_MS,
}

# Module-level cache — avoids re-fetching for multiple markets in one cycle
_cvd_cache: dict = {}
_CVD_CACHE_TTL_S = 55  # refresh just under 60s cycle


# ═══════════════════════════════════════
#  Data Fetching (Live)
# ═══════════════════════════════════════

def _fetch_live_agg_trades(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch aggTrades for a specific time window (paginated).

    Lighter than fetch_agg_trades_day() — only fetches the exact window.
    BTC 20 min ≈ 5K-20K trades ≈ 5-20 API calls.
    """
    url = f"{BINANCE_FAPI}/fapi/v1/aggTrades"
    all_rows = []
    cursor_ms = start_ms

    for _ in range(100):  # safety cap
        params = {"symbol": symbol, "startTime": cursor_ms, "endTime": end_ms, "limit": 1000}
        data = None
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.RequestException, ConnectionError) as e:
                if attempt == 2:
                    logger.warning("aggTrades fetch failed after 3 attempts: %s", e)
                    return pd.DataFrame()
                time.sleep(2 ** attempt)

        if not data:
            break

        for t in data:
            all_rows.append({
                "agg_id": t["a"],
                "price": float(t["p"]),
                "qty": float(t["q"]),
                "timestamp": int(t["T"]),
                "is_buyer_maker": t["m"],
            })

        if len(data) < 1000:
            break
        cursor_ms = int(data[-1]["T"]) + 1
        time.sleep(0.2)  # rate limit

    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


def _fetch_live_klines_1m(symbol: str, limit: int = 25) -> pd.DataFrame:
    """Fetch last N 1-minute klines (single API call)."""
    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": "1m", "limit": limit}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("1m klines fetch failed: %s", e)
        return pd.DataFrame()

    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore",
    ]
    df = pd.DataFrame(data, columns=columns)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ═══════════════════════════════════════
#  CVD Core Logic (shared with backtest)
# ═══════════════════════════════════════

def detect_cvd_divergence(
    price_by_ts: dict[int, float],
    cvd_by_ts: dict[int, float],
    ref_ts: int,
    min_price_change: float = CVD_MIN_PRICE_CHANGE_USD,
) -> dict:
    """Detect CVD divergence across lookback windows from a reference point.

    Bullish div: price down + CVD up → buying pressure despite drop → predict UP
    Bearish div: price up + CVD down → selling pressure despite rise → predict DOWN

    ref_ts: 1m timestamp to use as "now" (should be last COMPLETE 1m candle).
    Returns: {bullish: int, bearish: int, score: float}
    """
    cur_price = price_by_ts.get(ref_ts)
    cur_cvd = cvd_by_ts.get(ref_ts)
    if cur_price is None or cur_cvd is None:
        return {"bullish": 0, "bearish": 0, "score": 0.0}

    bullish = 0
    bearish = 0
    for _name, lb_ms in LOOKBACK_WINDOWS.items():
        lb_ts = ref_ts - lb_ms
        lb_price = price_by_ts.get(lb_ts)
        lb_cvd = cvd_by_ts.get(lb_ts)
        if lb_price is None or lb_cvd is None:
            continue

        dp = cur_price - lb_price
        dc = cur_cvd - lb_cvd
        if abs(dp) < min_price_change:
            continue

        if dp < 0 and dc > 0:
            bullish += 1
        elif dp > 0 and dc < 0:
            bearish += 1

    net = bullish - bearish
    return {"bullish": bullish, "bearish": bearish, "score": float(net)}


def cvd_to_prob(
    div_result: dict,
    dollar_imbalance: float = 0.0,
    strength_scale: float = CVD_STRENGTH_SCALE,
) -> float:
    """Map CVD divergence → P(Up) in [0.15, 0.85].

    Same tanh + clamp as crypto_15m._score_direction() for comparable scales.
    """
    score = div_result["score"]
    if score == 0:
        return 0.5

    norm = score / len(LOOKBACK_WINDOWS)  # [-1, +1]

    # Dollar imbalance confirmation (same direction boosts signal 30%)
    if abs(dollar_imbalance) > 0.05:
        if (norm > 0 and dollar_imbalance > 0) or (norm < 0 and dollar_imbalance < 0):
            norm *= 1.3

    p_up = 0.5 + 0.3 * math.tanh(norm * strength_scale)
    return max(0.15, min(0.85, p_up))


def compute_dollar_imbalance(dv_5m: dict, dv_15m: dict, prev_5m_ts: int) -> float:
    """5m buy_ratio - 15m buy_ratio for a completed 5m candle.

    Positive = short-term more bullish than longer-term context.
    """
    d5 = dv_5m.get(str(prev_5m_ts))
    if not d5:
        return 0.0
    total5 = d5["buy_usd"] + d5["sell_usd"]
    if total5 == 0:
        return 0.0
    r5 = d5["buy_usd"] / total5

    ts_15 = prev_5m_ts - (prev_5m_ts % FIFTEEN_MIN_MS)
    d15 = dv_15m.get(str(ts_15))
    if not d15:
        return 0.0
    total15 = d15["buy_usd"] + d15["sell_usd"]
    if total15 == 0:
        return 0.0
    r15 = d15["buy_usd"] / total15

    return r5 - r15


# ═══════════════════════════════════════
#  Live Edge Assessment
# ═══════════════════════════════════════

def _get_cached_cvd_data(symbol: str) -> dict | None:
    """Return cached CVD data if fresh, else None."""
    global _cvd_cache
    if (
        _cvd_cache.get("symbol") == symbol
        and time.time() - _cvd_cache.get("ts", 0) < _CVD_CACHE_TTL_S
    ):
        return _cvd_cache
    return None


def assess_cvd_edge(market: PolyMarket) -> EdgeAssessment | None:
    """Live CVD divergence assessment for crypto 15M markets.

    1. Fetch last 20 min of 1m klines + aggTrades (cached per cycle)
    2. Compute 1m CVD + divergence detection
    3. Dollar imbalance (5m vs 15m buy/sell ratio)
    4. CVD signals → P(Up) → EdgeAssessment

    Returns EdgeAssessment if CVD edge > threshold, None otherwise.
    """
    global _cvd_cache

    if not CVD_ENABLED:
        return None

    # Outcome safety check
    if not market.outcomes or market.outcomes[0].lower() not in ("up", "yes"):
        return None

    symbol = "BTCUSDT"  # Only BTC for now

    # ── Try cache first ──
    cached = _get_cached_cvd_data(symbol)
    if cached:
        price_by_ts = cached["price_by_ts"]
        cvd_by_ts = cached["cvd_by_ts"]
        dv_5m = cached["dv_5m"]
        dv_15m = cached["dv_15m"]
        ref_ts = cached["ref_ts"]
    else:
        # ── Fetch live data ──
        from backtest.fetch_agg_trades import aggregate_cvd, aggregate_delta_volume

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - CVD_LOOKBACK_MINUTES * ONE_MIN_MS

        klines_1m = _fetch_live_klines_1m(symbol, limit=CVD_LOOKBACK_MINUTES + 5)
        if klines_1m.empty:
            logger.warning("CVD: failed to fetch 1m klines")
            return None

        trades_df = _fetch_live_agg_trades(symbol, start_ms, now_ms)
        if trades_df.empty:
            logger.warning("CVD: failed to fetch aggTrades")
            return None

        logger.info("CVD: fetched %d aggTrades for last %d min", len(trades_df), CVD_LOOKBACK_MINUTES)

        # ── Compute CVD ──
        ts_1m = klines_1m["open_time"].astype(int).tolist()
        minute_cvd_raw = aggregate_cvd(trades_df, ts_1m, ONE_MIN_MS)

        price_by_ts = dict(zip(
            klines_1m["open_time"].astype(int),
            klines_1m["close"].astype(float),
        ))
        cvd_by_ts = {int(k): v["cvd"] for k, v in minute_cvd_raw.items()}

        # Use second-to-last 1m kline (last one may be incomplete)
        if len(ts_1m) < 3:
            return None
        ref_ts = ts_1m[-2]

        # Delta volume for dollar imbalance
        prev_5m_ts = (ref_ts - (ref_ts % FIVE_MIN_MS)) - FIVE_MIN_MS
        dv_5m = aggregate_delta_volume(trades_df, [prev_5m_ts], FIVE_MIN_MS)
        ts_15_val = prev_5m_ts - (prev_5m_ts % FIFTEEN_MIN_MS)
        dv_15m = aggregate_delta_volume(trades_df, [ts_15_val], FIFTEEN_MIN_MS)

        # Cache for other markets in same cycle
        _cvd_cache = {
            "symbol": symbol, "ts": time.time(),
            "price_by_ts": price_by_ts, "cvd_by_ts": cvd_by_ts,
            "dv_5m": dv_5m, "dv_15m": dv_15m,
            "ref_ts": ref_ts, "prev_5m_ts": prev_5m_ts,
        }

    # ── Detect divergence ──
    div_result = detect_cvd_divergence(price_by_ts, cvd_by_ts, ref_ts)

    # ── Dollar imbalance ──
    prev_5m_ts = _cvd_cache.get("prev_5m_ts", 0)
    imbalance = compute_dollar_imbalance(dv_5m, dv_15m, prev_5m_ts)

    # ── CVD → P(Up) ──
    p_up = cvd_to_prob(div_result, imbalance)

    if p_up == 0.5:
        logger.info("CVD: no divergence signal for %s", market.title[:50])
        return None

    # ── Edge calculation (same pattern as all other strategies) ──
    raw_edge = p_up - market.yes_price
    if raw_edge > 0:
        side = "YES"
        edge_pct = raw_edge
    else:
        side = "NO"
        edge_pct = -raw_edge

    if edge_pct < CVD_MIN_EDGE_PCT:
        logger.info("CVD edge too small: %.3f < %.3f", edge_pct, CVD_MIN_EDGE_PCT)
        return None

    # ── Confidence (strength-based) ──
    total_divs = div_result["bullish"] + div_result["bearish"]
    strength = total_divs / len(LOOKBACK_WINDOWS) if total_divs > 0 else 0.0
    confidence = min(0.8, 0.4 + strength * 0.3)

    signal_type = "BULLISH_DIV" if div_result["bullish"] > div_result["bearish"] else "BEARISH_DIV"
    reasoning = (
        f"CVD divergence ({signal_type}): "
        f"bull={div_result['bullish']}, bear={div_result['bearish']}. "
        f"P(Up)={p_up:.3f} vs market={market.yes_price:.3f}. "
        f"Dollar imbalance: {imbalance:+.3f}"
    )

    # ── Log prediction (same logger as indicator path for unified calibration) ──
    try:
        from .crypto_15m import log_15m_prediction, parse_crypto_15m_market
        parsed = parse_crypto_15m_market(market.title)
        if parsed:
            log_15m_prediction(
                p_up=p_up, market_price=market.yes_price,
                edge_pct=edge_pct, confidence=confidence,
                acted=True, skip_reason=None, source="cvd",
                title=market.title, condition_id=market.condition_id,
                coin=parsed["coin"], window_start=parsed["start_time"],
                window_end=parsed["end_time"],
                indicators={"cvd_bull": div_result["bullish"],
                            "cvd_bear": div_result["bearish"],
                            "imbalance": round(imbalance, 3)},
                market_mode=None,
            )
    except Exception as e:
        logger.debug("CVD prediction log failed: %s", e)

    return EdgeAssessment(
        condition_id=market.condition_id,
        title=market.title,
        category="crypto_15m",
        market_price=market.yes_price,
        ai_probability=p_up,
        edge=raw_edge,
        edge_pct=edge_pct,
        confidence=confidence,
        side=side,
        reasoning=reasoning,
        data_sources=["cvd_divergence", "binance_aggtrades", symbol],
        signal_source="cvd",
    )
