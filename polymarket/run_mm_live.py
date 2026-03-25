#!/usr/bin/env python3
"""
run_mm_live.py — v4 Dual-Layer Runner

策略：Dual-layer (hedge + directional) with signal pipeline。
- Zone 1 (0.50-0.57): pure hedge (guaranteed if both fill)
- Zone 2 (0.57-0.65): 50% hedge + 50% directional
- Zone 3 (>0.65): 25% hedge + 75% directional
- Cancel defense: spot move + TTL + window-end (layer-specific)

流程（每 30 秒）：
1. Fetch coin price + vol + indicators
2. Refresh bankroll
3. Discover markets（slug-based）→ watchlist
4. Enter with directional/asymmetric sizing
5. Cancel stale GTC 2 min before window end
6. Confirm fills via get_trades()
7. Check resolutions → PnL
8. Save state

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --live --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --status
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from copy import copy as _copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import (
    MMConfig, MMMarketState, PlannedOrder,
    compute_fair_up, plan_opening, apply_fill,
    resolve_market, should_enter_market, calc_tranches,
)
from polymarket.core.context import PolyMarket
from polymarket.exchange.gamma_client import GammaClient
from polymarket.config.settings import MM_DAILY_LOSS_LIMIT

logger = logging.getLogger(__name__)

# ─── Telegram alerts (critical events only) ───
_tg_last_alert: dict = {}  # dedupe: key → timestamp

def _tg_alert(msg: str, cooldown_min: int = 30):
    """Send Telegram alert for critical events. Deduped by message prefix."""
    key = msg[:20]
    now = time.time()
    if key in _tg_last_alert and now - _tg_last_alert[key] < cooldown_min * 60:
        return  # already sent recently
    _tg_last_alert[key] = now
    try:
        from shared_infra.telegram import send_telegram
        send_telegram(f"<b>MM 15M</b>\n{msg}")
    except Exception as e:
        logger.debug("Telegram alert failed: %s", e)

_HKT = ZoneInfo("Asia/Hong_Kong")
_ET = ZoneInfo("America/New_York")
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades.jsonl")
_SIGNAL_LOG = os.path.join(_LOG_DIR, "mm_signals.jsonl")  # OB + cross-exchange data for taker research
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log.jsonl")  # per-order lifecycle: submit/fill/cancel/post_fill
_BOTHSIDES_LOG = os.path.join(_LOG_DIR, "mm_bothsides.jsonl")  # both-sides experiment log
_POS_LOG = os.path.join(_LOG_DIR, "mm_positions.jsonl")  # position snapshots for post-session analysis
_CYCLE_S = 5           # 5s main loop — fast reaction
_SCAN_S = 120          # discovery every 2 min (was 300s; tighter scan for faster T1 entry)
_HEAVY_INTERVAL_S = 3  # heavy ops every 3s (~100 CLOB req/min, limit=200 self-imposed, 2400 CLOB max)

# Newbie protection: first N hours of live trading, cap exposure
_PROTECTION_HOURS = 3
_PROTECTION_BET_PCT = 0.01   # 1% per market during protection
_PROTECTION_MAX_MARKETS = 1  # 1 market per cycle (= 1 per 15min window)
_MAX_ROUNDS = 3          # max scalp rounds per market window
_REENTRY_COOLDOWN_S = 30 # seconds after sell before re-entry
# Live execution gate: only these coins place real orders.
# ETH + XRP = discover + log signals but NEVER execute (observation only).
_LIVE_TRADE_COINS = {"btc", "sol"}  # BTC + SOL live; ETH + XRP = observe only
# Per-coin bet sizing: SOL at 1% (smaller, less data), BTC at 3% (proven edge)
_BET_PCT_BY_COIN = {"btc": 0.03, "sol": 0.01}
# ── Endgame: 1-share data collection in undecided markets (last 2 min) ──
_ENDGAME_ENABLED = True
_ENDGAME_TTE_START = 120     # activate at T-120s (after normal cancel-all)
_ENDGAME_TTE_STOP = 30       # stop placing at T-30s
_ENDGAME_SHARES = 1          # 1 share per bet (data collection mode)
_ENDGAME_MID_RANGE = (0.17, 0.83)   # only undecided markets
_ENDGAME_FLIP_RANGE = (0.38, 0.62)  # case 1: coin flip zone
_ENDGAME_REVERSAL = 0.20     # case 2: ≥20pt mid move in 30s toward center
_ENDGAME_DAILY_CAP = 10      # max 10 bets/day → worst case $2.50 (< 2% of $300 bankroll)
# ── Reversal Research: log extreme markets in last 5min for dry-run analysis ──
_REVERSAL_LOG = os.path.join(_LOG_DIR, "reversal_research.jsonl")
_REVERSAL_TTE_START = 300    # start logging at T-5min
_REVERSAL_EXTREME_THRESH = 0.10  # log when cheap side ≤ 10¢ (90/10+)
_BINANCE = "https://fapi.binance.com"
_BINANCE_SPOT = "https://api.binance.com"

# Rate limit safety: track API calls per minute
_api_calls: dict = {}  # {"binance": [(ts, count), ...]}
_API_LIMIT_PER_MIN = 200  # conservative: 200/min out of 2400 limit
_mkt_fetcher = None  # StaggeredFetcher instance (set in main, used in run_cycle for logging)
_ws_binance = None   # BinancePriceFeed instance (set in main, WS price source)
_ws_poly = None      # PolymarketBookFeed instance (set in main, WS OB source)
_ws_user = None      # PolymarketUserFeed instance (set in main, WS fill/cancel detection)
_endgame_mid_buf: dict[str, deque] = {}  # cid → deque of (ts, mid) for case 2 reversal detection
# endgame daily count persisted in state["_eg_daily_count"] (survives restart)
_btc_price_buf: deque = deque(maxlen=120)  # (ts, price) for 30s BTC momentum (hedge trigger)
_HEDGE_BTC_THRESHOLD = 50   # $50 BTC move in 30s = hedge trigger
_HEDGE_PCT = 0.30           # hedge 30% of position


def _rate_ok(source: str = "binance") -> bool:
    """Check if we're within safe API call rate."""
    now = time.time()
    calls = _api_calls.get(source, [])
    # Remove calls older than 60s
    calls = [(t, c) for t, c in calls if now - t < 60]
    _api_calls[source] = calls
    total = sum(c for _, c in calls)
    return total < _API_LIMIT_PER_MIN


def _track_call(source: str = "binance", n: int = 1):
    """Track an API call for rate limiting."""
    _api_calls.setdefault(source, []).append((time.time(), n))


# ═══════════════════════════════════════
#  Data — High-Frequency Layer (3-5s cache)
# ═══════════════════════════════════════

_cache: dict = {}

# ─── Holder imbalance tracking (whale exit detection) ───
_holder_cache: dict = {}  # cid → (imbalance, timestamp)
_HOLDER_CACHE_TTL = 30    # seconds — holders don't change every second
_DATA_API = "https://data-api.polymarket.com"


def _holder_imbalance(condition_id: str, up_token_id: str,
                      ttl_override: float = 0) -> tuple[float, float]:
    """Fetch holder position imbalance + delta from previous reading.

    Returns (imbalance, delta). imbalance ∈ [-1, +1], delta = change since last.
    Positive imbalance = more UP shares. Negative delta = whale exit from UP.
    Cached 30s (or ttl_override for last-minute burst mode).
    Uses Data API (separate rate limit from CLOB).
    """
    key = f"holder_{condition_id}"
    now = time.time()
    ttl = ttl_override if ttl_override > 0 else _HOLDER_CACHE_TTL

    # Check cache
    if key in _cache and now - _cache[key][1] < ttl:
        return _cache[key][0]

    try:
        url = f"{_DATA_API}/holders?market={condition_id}&limit=20"
        req = urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            groups = json.loads(r.read())
    except Exception as e:
        logger.debug("Holder fetch failed: %s", e)
        cached = _cache.get(key, ((0.0, 0.0), 0))[0]
        return cached

    if not groups or not isinstance(groups, list):
        return (0.0, 0.0)

    up_shares = 0.0
    down_shares = 0.0
    for group in groups:
        token = group.get("token", "")
        is_up = token == up_token_id
        for h in group.get("holders", []):
            amt = float(h.get("amount", 0)) if h.get("amount") else 0.0
            if is_up:
                up_shares += amt
            else:
                down_shares += amt

    total = up_shares + down_shares
    imbalance = (up_shares - down_shares) / total if total > 0 else 0.0

    # Delta from previous reading
    prev_imbalance = _holder_cache.get(condition_id, (0.0, 0))[0]
    prev_ts = _holder_cache.get(condition_id, (0.0, 0))[1]
    delta = imbalance - prev_imbalance if prev_ts > 0 else 0.0

    _holder_cache[condition_id] = (imbalance, now)
    result = (round(imbalance, 4), round(delta, 4))
    _cache[key] = (result, now)
    return result


def _price(symbol: str = "BTCUSDT") -> float:
    """Latest price. Cached 1s — tighter for cancel defense (was 3s, reduced 2026-03-22)."""
    key = f"price_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 1:
        return _cache[key][0]
    # WebSocket path — sub-millisecond, no REST call needed
    if _ws_binance:
        ws_price = _ws_binance.get_price(symbol)
        if ws_price:
            _cache[key] = (ws_price, now)
            return ws_price
    if not _rate_ok("binance"):
        return _cache.get(key, (0, 0))[0]
    # REST fallback: book ticker for fastest price (best bid+ask, single call)
    url = f"{_BINANCE_SPOT}/api/v3/ticker/bookTicker?symbol={symbol}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            data = json.loads(r.read())
            bid = float(data.get("bidPrice", 0))
            ask = float(data.get("askPrice", 0))
            price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            if price > 0:
                _cache[key] = (price, now)
                _track_call("binance")
                return price
    except Exception:
        pass
    # Fallback to kline
    url = f"{_BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=1"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            price = float(json.loads(r.read())[0][4])
            _cache[key] = (price, now)
            _track_call("binance")
            return price
    except Exception as e:
        logger.warning("%s price fetch failed: %s", symbol, e)
        return _cache.get(key, (0, 0))[0]


def _btc_price() -> float:
    return _price("BTCUSDT")


# ── Cross-exchange price validation ──
# Fetch from 3 exchanges, use median. Detect anomalies.
_CROSS_EXCHANGES = {
    "binance": "https://api.binance.com/api/v3/ticker/price?symbol={sym}",
    "okx":     "https://www.okx.com/api/v5/market/ticker?instId={sym_okx}",
    "bybit":   "https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}",
}
_SYM_MAP_OKX = {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"}


def _cross_exchange_price(symbol: str = "BTCUSDT") -> tuple[float, float]:
    """Fetch price from 3 exchanges, return (median, max_divergence_pct).
    divergence = (max - min) / median. High = anomaly.
    Falls back to Binance-only if others fail.
    Cached 5s — faster flash crash detection."""
    key = f"xprice_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:  # 5s for faster flash crash detection
        return _cache[key][0]

    prices = []
    # Binance (fastest, always try)
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            p = float(json.loads(r.read()).get("price", 0))
            if p > 0:
                prices.append(p)
    except Exception:
        pass

    # OKX
    try:
        okx_sym = _SYM_MAP_OKX.get(symbol, symbol.replace("USDT", "-USDT"))
        url = f"https://www.okx.com/api/v5/market/ticker?instId={okx_sym}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            data = json.loads(r.read()).get("data", [{}])
            p = float(data[0].get("last", 0)) if data else 0
            if p > 0:
                prices.append(p)
    except Exception:
        pass

    # Bybit
    try:
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=3) as r:
            result = json.loads(r.read()).get("result", {}).get("list", [{}])
            p = float(result[0].get("lastPrice", 0)) if result else 0
            if p > 0:
                prices.append(p)
    except Exception:
        pass

    if not prices:
        cached = _cache.get(key, ((0, 0), 0))[0]
        return cached

    prices.sort()
    median = prices[len(prices) // 2]
    divergence = (prices[-1] - prices[0]) / median if median > 0 and len(prices) > 1 else 0.0

    result = (median, divergence)
    _cache[key] = (result, now)
    _track_call("binance")
    return result


def _open_at(start_ms: int, symbol: str = "BTCUSDT") -> float:
    """Price at a specific timestamp. Cached permanently (historical)."""
    key = f"open_{symbol}_{start_ms}"
    if key in _cache:
        return _cache[key][0]
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&startTime={start_ms}&limit=1"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            price = float(json.loads(r.read())[0][1])
            _cache[key] = (price, time.time())
            _track_call("binance")
            return price
    except Exception:
        return 0.0


def _btc_open_at(start_ms: int) -> float:
    return _open_at(start_ms, "BTCUSDT")


def _vol_1m(symbol: str = "BTCUSDT") -> float:
    """Per-minute vol. Cached 60s — slow-moving, no need for fast refresh."""
    key = f"vol_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 60:
        return _cache[key][0]
    if not _rate_ok("binance"):
        return _cache.get(key, (0.001, 0))[0]
    url = f"{_BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=120"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=10) as r:
            closes = [float(k[4]) for k in json.loads(r.read())]
        _track_call("binance")
        if len(closes) < 20:
            return _cache.get(key, (0.001, 0))[0]
        rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
        mean = sum(rets) / len(rets)
        vol = max(0.0001, math.sqrt(sum((r - mean)**2 for r in rets) / len(rets)))
        _cache[key] = (vol, now)
        return vol
    except Exception:
        return _cache.get(key, (0.001, 0))[0]


def _cvd_buy_ratio(symbol: str = "BTCUSDT", minutes: int = 3) -> float:
    """Taker buy ratio over last N minutes. >0.55 = buying pressure, <0.45 = selling.
    Uses Binance spot 1m klines (taker_buy_volume included). Cached 15s (was 30s)."""
    key = f"cvd_{symbol}_{minutes}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 15:
        return _cache[key][0]
    if not _rate_ok("binance"):
        return _cache.get(key, (0.5, 0))[0]
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&limit={minutes + 1}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            candles = json.loads(r.read())
        _track_call("binance")
        if len(candles) < 2:
            return 0.5
        # Use last N candles (skip first partial)
        recent = candles[-minutes:]
        total_vol = sum(float(c[5]) for c in recent)
        total_buy = sum(float(c[9]) for c in recent)  # index 9 = taker_buy_volume
        ratio = total_buy / total_vol if total_vol > 0 else 0.5
        _cache[key] = (ratio, now)
        return ratio
    except Exception:
        return _cache.get(key, (0.5, 0))[0]


# ── W4 Signal: momentum from window open (5bps threshold at T+300s for 15M) ──
_W4_DELAY_S = 300       # 5 min after window open (15M sweet spot)
_W4_THRESHOLD_BPS = 5   # 5 basis points minimum
_W4_LEAN_RATIO = 1.0    # v4: PURE ARB. Lean killed. Direction = data collection only.
# v3 had dynamic 1.2/1.0 by tier. BMD proved: (1) lean = -$14.55 drag on 26 trades,
# (2) T1+T2 amplifies to R=2.7-4.2x, (3) one bad trade wipes session.
# Keep _w4_dynamic_ratio() for logging but FORCE R=1.0.
_W4_RATIO_BY_TIER = {
    "5-10": 1.0,   # pure arb — lean killed pending 50-trade validation
    "10+":  1.0,   # pure arb
}
_W4_EFFECTIVE_R_CAP = 1.1  # runtime cap: cancel excess if actual ratio > this


def _w4_dynamic_ratio(mag_bps: float) -> float:
    """Returns R=1.0 for all tiers (v4 pure arb mode).
    Dynamic tiers preserved in _W4_RATIO_BY_TIER for future re-enable.
    """
    return 1.0  # v4: pure arb, no lean
# ── W4 Staged Entry: T1 at 5min, T2 confirmation at 8min ──
# Data: T+480s confirms T+300s → 86.1% WR (vs 76.1% overall)
#       T+480s flips → 21.5% WR. Skip rate 23.9%.
_W4_T2_DELAY_S = 480       # T2 confirmation at 8 min into window
_W4_T1_PCT = 0.60          # T1 gets 60% of budget
_W4_T2_PCT = 0.40          # T2 gets 40% (if direction confirmed)
# ── W4 Order Repricing ──
_REPRICE_COOLDOWN_S = 5     # max 1 reprice per 5s per market (was 30s, reduced 2026-03-25)
_REPRICE_THRESHOLD = 0.02   # 2¢ drift triggers reprice
_REPRICE_MAX_PER_ORDER = 3  # max 3 reprices per order lifetime
# ── W4 Lean-Unfilled Protection (Door B) ──
# Data: 30.3% of entries = lean miss (hedge fills, lean doesn't).
# These cost -$2.30/33 trades. Cancel hedge early to avoid naked hedge.
# Stage 1 (preemptive): hedge still pending → cancel for free.
# Stage 2 (reactive): hedge already filled → cancel lean, block T2, let hedge resolve.
# 90s timeout = zero false cancels on 33-trade sample (max BOTH lean gap = 87s).
_LEAN_UNFILLED_TIMEOUT_S = 90  # seconds after entry before declaring lean unfilled
_LEAN_PREEMPTIVE_S = 20        # seconds for Stage 1 preemptive hedge cancel
# ── W4 Taker Conversion: one side fills → taker the other side to complete arb ──
# When hedge fills as maker, lean is still pending → cancel lean maker → re-buy as taker.
# Cost: ~1-2¢ extra spread. Benefit: guarantees both-fill = true arb (no naked positions).
_TAKER_CONVERT_ENABLED = True
_TAKER_CONVERT_DELAY_S = 3    # wait N seconds after first fill before converting
_TAKER_CONVERT_MAX_SPREAD = 0.04  # abort if taker price > maker price + 4¢

_MIN_VIABLE_BUDGET = 3.50  # 2 sides × 5 shares × ~$0.20 = $2 min; need ≥$3.33 for T1 60% split


def _w4_signal(window_start_ms: int, symbol: str = "BTCUSDT") -> tuple:
    """W4 momentum signal: log return from window open to now.
    Returns (direction, magnitude_bps, log_return).
    direction: 'UP', 'DOWN', 'WAIT' (too early), 'SKIP' (below threshold).
    """
    now_ms = int(time.time() * 1000)
    elapsed_s = (now_ms - window_start_ms) / 1000
    if elapsed_s < _W4_DELAY_S:
        return "WAIT", 0.0, 0.0
    p0 = _open_at(window_start_ms, symbol)
    if p0 <= 0:
        return "SKIP", 0.0, 0.0
    p_now = _price(symbol)
    if p_now <= 0:
        return "SKIP", 0.0, 0.0
    log_ret = math.log(p_now / p0)
    mag_bps = abs(log_ret) * 10000
    if mag_bps < _W4_THRESHOLD_BPS:
        return "SKIP", mag_bps, log_ret
    direction = "UP" if log_ret > 0 else "DOWN"
    return direction, mag_bps, log_ret


def _m1_return(symbol: str = "BTCUSDT") -> float:
    """Last 1-minute return (log). Reuses _vol_1m cache if fresh, else fetches 2 candles.
    Returns 0.0 if unavailable. Positive = price went up."""
    # Try vol cache first — it has 60 closes, last ret = M1
    vol_key = f"vol_{symbol}"
    if vol_key in _cache and time.time() - _cache[vol_key][1] < 60:
        # Vol was computed recently — fetch fresh M1 from 2 candles (cheap)
        pass
    if not _rate_ok("binance"):
        return 0.0
    url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={symbol}&interval=1m&limit=2"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}),
                timeout=5) as r:
            candles = json.loads(r.read())
        _track_call("binance")
        if len(candles) < 2:
            return 0.0
        c_prev = float(candles[0][4])  # previous 1m close
        c_now = float(candles[1][4])   # current 1m close
        if c_prev <= 0:
            return 0.0
        return math.log(c_now / c_prev)
    except Exception:
        return 0.0


def _poly_midpoint(client, token_id: str) -> float:
    """Polymarket midpoint for a token. WS first, REST fallback. Cached 5s."""
    # WebSocket path — sub-second, no REST call needed
    if _ws_poly:
        ws_mid = _ws_poly.get_midpoint(token_id)
        if ws_mid is not None:
            return ws_mid
    # REST fallback (cached 5s)
    key = f"mid_{token_id[:16]}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:
        return _cache[key][0]
    if not client or not hasattr(client, "get_midpoint"):
        return 0.0
    try:
        mid = client.get_midpoint(token_id)
        if mid > 0:
            _cache[key] = (mid, now)
            _track_call("clob")
        return mid
    except Exception:
        return _cache.get(key, (0, 0))[0]


def _poly_ob_imbalance(client, up_token: str) -> float:
    """Order book imbalance for UP token. WS first, REST fallback. Cached 5s. Returns -1 to +1."""
    # WebSocket path — sub-second, no REST call needed
    if _ws_poly:
        ws_imbal = _ws_poly.get_ob_imbalance(up_token)
        if ws_imbal is not None:
            return ws_imbal
    # REST fallback (cached 5s)
    key = f"obi_{up_token[:16]}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:
        return _cache[key][0]
    if not client or not hasattr(client, "get_order_book"):
        return 0.0
    try:
        book = client.get_order_book(up_token)
        bid_vol = sum(b["size"] for b in book.get("bids", []))
        ask_vol = sum(a["size"] for a in book.get("asks", []))
        if bid_vol + ask_vol > 0:
            imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            _cache[key] = (imb, now)
            _track_call("clob")
            return imb
    except Exception:
        pass
    return _cache.get(key, (0, 0))[0]


# ═══════════════════════════════════════
#  Discovery（slug-based）
# ═══════════════════════════════════════

def _discover(gamma: GammaClient, config: MMConfig) -> list[tuple[PolyMarket, dict]]:
    """Find BTC + ETH 15M markets for current + next 4 windows via slug."""
    results = []
    now_s = int(time.time())
    now_et = datetime.now(tz=_ET)
    slot = (now_et.minute // 15) * 15
    base = now_et.replace(minute=0, second=0, microsecond=0)

    _COINS = [("btc", "bitcoin"), ("eth", "ethereum"), ("sol", "solana"), ("xrp", "xrp")]

    for i in range(5):
        ws = base + timedelta(minutes=slot + i * 15)
        we = ws + timedelta(minutes=15)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 120:
            continue

        for coin_slug, coin_title_kw in _COINS:
            slug = f"{coin_slug}-updown-15m-{ts}"
            try:
                _url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
                with urllib.request.urlopen(
                        urllib.request.Request(_url, headers={"User-Agent": "AXC/1.0"}),
                        timeout=5) as _resp:
                    data = json.loads(_resp.read())
            except Exception as e:
                logger.warning("Gamma slug fetch failed for %s: %s", slug, e)
                continue
            if not data or not isinstance(data, list):
                continue

            parsed = gamma.parse_market(data[0])
            outcomes = parsed.get("outcomes", [])
            if outcomes and isinstance(outcomes, list) and len(outcomes) >= 2:
                if outcomes[0].lower() not in ("up", "yes"):
                    logger.error("OUTCOME SWAPPED %s: %s", slug, outcomes)
                    continue

            pm = PolyMarket(
                condition_id=parsed["condition_id"], title=parsed["title"],
                category="crypto_15m", end_date=we.isoformat(),
                yes_token_id=parsed.get("yes_token_id", ""),
                no_token_id=parsed.get("no_token_id", ""),
                yes_price=parsed.get("yes_price", 0.5),
                no_price=parsed.get("no_price", 0.5),
                liquidity=parsed.get("liquidity", 0),
            )
            if should_enter_market(pm, config):
                results.append((pm, {"start_ms": ts * 1000, "end_ms": te * 1000,
                                      "end_time": we.isoformat()}))
    return results


# ═══════════════════════════════════════
#  Order Execution
# ═══════════════════════════════════════

def _execute(orders: list[PlannedOrder], client,
             cid: str = "", signal_ctx: dict | None = None) -> list[dict]:
    """Submit limit orders. Returns order IDs — NOT fills.

    IMPORTANT: Limit orders (GTC) go on the book. Submit ≠ filled.
    Fills are checked later via _check_fills().

    cid: condition_id for per-order logging.
    signal_ctx: market state at submit time (fair, bridge, cvd, vol, mid) for AS analysis.
    """
    results = []
    _ctx = signal_ctx or {}
    for o in orders:
        try:
            amount = round(o.size * o.price, 2)
            r = client.buy_shares(o.token_id, amount, price=o.price)
            order_id = ""
            status = ""
            if isinstance(r, dict):
                order_id = r.get("orderID", r.get("id", ""))
                status = r.get("status", "")
                # Dry-run: simulate instant fill (no real CLOB)
                if r.get("dry_run"):
                    status = "matched"
            # ⚠️ DZ-4: Partial fill blindspot — use size_matched not size. See docs/DANGER_ZONES.md
            # Extract actual matched size from API response (partial fill detection)
            _size_matched = o.size  # default: assume full fill
            if isinstance(r, dict):
                _taking = r.get("takingAmount", "")
                if _taking and str(_taking).strip():
                    try:
                        _size_matched = float(_taking)
                    except (ValueError, TypeError):
                        _size_matched = o.size
            logger.info("ORDER SUBMITTED %s %s: %.1f shares @ $%.3f ($%.2f) → %s [%s]",
                        o.outcome, o.token_id[:10], o.size, o.price, amount,
                        order_id[:12] if order_id else "ok", status)
            if abs(_size_matched - o.size) > 0.1:
                logger.warning("PARTIAL FILL DETECTED %s: submitted=%.1f matched=%.1f",
                               order_id[:12] if order_id else "?", o.size, _size_matched)
            results.append({"outcome": o.outcome, "price": o.price,
                           "size": o.size, "size_matched": _size_matched,
                           "token_id": o.token_id,
                           "order_id": order_id, "status": status,
                           "submitted": True, "order_ts": time.time()})
            # Per-order submit log (AS analysis data)
            _log_order("submit", order_id, cid,
                       outcome=o.outcome, price=o.price, size=o.size,
                       status=status, **_ctx)
        except Exception as e:
            logger.error("ORDER FAILED %s: %s", o.outcome, e)
            results.append({"outcome": o.outcome, "submitted": False, "error": str(e)})
    return results


def _check_fills(state: dict, client) -> None:
    """Check which submitted orders actually filled on-chain.

    Queries open orders + trades to determine real fill status.
    Updates market state to reflect actual positions.
    """
    if not client or not hasattr(client, "get_orders"):
        return

    now_ms = int(time.time() * 1000)
    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        # Skip if already confirmed fills
        if mkt.get("fills_confirmed"):
            continue

        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        # Don't check after window ends — exchange may cancel unfilled orders
        # which would falsely appear as "filled"
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            # Window over — mark remaining pending as unfilled, not filled
            _bump_fill(state, "expired", len(pending))
            for _ep in pending:
                _log_order("expired", _ep.get("order_id", ""), cid,
                           outcome=_ep.get("outcome", ""))
            logger.info("Window ended %s: %d pending orders → expired (not filled)",
                        cid[:8], len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue

        # ── WS fast path: check fills via user WebSocket (instant, no REST call) ──
        if _ws_user and _ws_user.connected:
            ws_resolved = []
            ws_remaining = []
            for po in pending:
                oid = po.get("order_id", "")
                if not oid:
                    ws_remaining.append(po)
                    continue
                ws_status = _ws_user.get_order_status(oid)
                if ws_status == "MATCHED":
                    # WS confirmed fill — apply immediately
                    outcome = po["outcome"]
                    price = po["price"]
                    size = po["size"]
                    # 2check fix: use actual size_matched if available (detect partial fills)
                    ws_detail = _ws_user.get_order_detail(oid)
                    if ws_detail and ws_detail.get("size_matched", 0) > 0:
                        actual = ws_detail["size_matched"]
                        if abs(actual - size) > 0.1:
                            logger.warning("PARTIAL FILL %s: submitted=%.1f matched=%.1f",
                                           oid[:12], size, actual)
                            size = actual
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * price) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * price) / mkt["down_shares"]
                    mkt["entry_cost"] += size * price
                    _bump_fill(state, "filled")
                    _log_order("fill_ws", oid, cid, outcome=outcome,
                               price=price, size=size)
                    logger.info("FILL (WS) %s %s: %.1f @ $%.3f",
                                cid[:8], outcome, size, price)
                    ws_resolved.append(po)
                elif ws_status in ("CANCELED", "CANCELLED"):
                    # WS confirmed cancel — remove from pending
                    _bump_fill(state, "cancelled")
                    _log_order("cancelled_ws", oid, cid,
                               outcome=po.get("outcome", ""))
                    logger.info("CANCEL (WS) %s %s", cid[:8], po.get("outcome", ""))
                    ws_resolved.append(po)
                else:
                    # WS hasn't seen this order yet → fall through to REST
                    ws_remaining.append(po)

            if ws_resolved:
                pending = ws_remaining
                mkt["pending_orders"] = pending
                if not pending:
                    mkt["fills_confirmed"] = True
                    logger.info("ALL FILLS CONFIRMED (WS) %s: UP=%.1f DN=%.1f cost=$%.2f",
                                cid[:8], mkt["up_shares"], mkt["down_shares"],
                                mkt["entry_cost"])
                    continue  # all resolved via WS, skip REST

                # ── Taker Conversion: one side filled, other still pending ──
                # If hedge filled but lean is still on the book → convert lean to taker
                # to guarantee both-fill and complete the arb.
                if (_TAKER_CONVERT_ENABLED and mkt.get("both_sides")
                        and client and hasattr(client, "buy_shares")
                        and not dry_run and not mkt.get("_taker_converted")):
                    _has_up = mkt.get("up_shares", 0) > 0
                    _has_dn = mkt.get("down_shares", 0) > 0
                    _pend_outcomes = {p.get("outcome", "").upper() for p in pending}
                    # One side filled, other still pending
                    if ((_has_up and not _has_dn and "DOWN" in _pend_outcomes)
                            or (_has_dn and not _has_up and "UP" in _pend_outcomes)):
                        _unfilled_side = "DOWN" if _has_up else "UP"
                        _unfilled_po = next(
                            (p for p in pending if p.get("outcome", "").upper() == _unfilled_side),
                            None)
                        if _unfilled_po:
                            _entry_ts = mkt.get("entry_ts", 0)
                            _age_s = time.time() - _entry_ts if _entry_ts > 0 else 999
                            if _age_s >= _TAKER_CONVERT_DELAY_S:
                                _tc_tok = _unfilled_po.get("token_id", "")
                                _tc_oid = _unfilled_po.get("order_id", "")
                                _tc_maker_px = _unfilled_po.get("price", 0)
                                _tc_size = _unfilled_po.get("size", 0)
                                # Get current ask to determine taker price
                                try:
                                    _tc_ob = client.get_order_book(_tc_tok)
                                    _tc_asks = _tc_ob.get("asks", [])
                                    _tc_best_ask = min(a["price"] for a in _tc_asks) if _tc_asks else 0
                                except Exception:
                                    _tc_best_ask = 0
                                if _tc_best_ask > 0:
                                    _tc_spread = _tc_best_ask - _tc_maker_px
                                    if _tc_spread <= _TAKER_CONVERT_MAX_SPREAD:
                                        # Combined cost check: maker cost + taker cost must be ≤ $1.00
                                        # Purpose: prevent naked exposure. Even breakeven ($1.00) is better
                                        # than naked position. Only block if guaranteed loss (>$1.00).
                                        _filled_side_key = "up_avg_price" if _has_up else "down_avg_price"
                                        _filled_px = mkt.get(_filled_side_key, 0)
                                        _tc_combined = _filled_px + _tc_best_ask + 0.01  # +1¢ for taker aggression
                                        if _tc_combined > 1.00:
                                            logger.info(
                                                "TAKER CONVERT SKIP %s %s: combined %.3f > $1.00 (guaranteed loss)",
                                                cid[:8], _unfilled_side, _tc_combined)
                                        else:
                                            # Cancel maker order first
                                            _tc_cancel_ok = False
                                            # Pre-cancel: Check if already filled (race guard)
                                            _tc_ws_st = ""
                                            if _ws_user and _ws_user.connected:
                                                _tc_ws_st = _ws_user.get_order_status(_tc_oid)
                                            if _tc_ws_st == "MATCHED":
                                                logger.info("TAKER CONVERT SKIP %s %s: already filled during wait",
                                                            cid[:8], _unfilled_side)
                                            else:
                                                try:
                                                    client.client.cancel(order_id=_tc_oid)
                                                    _tc_cancel_ok = True
                                                except Exception as _tce:
                                                    logger.warning("TAKER CONVERT cancel failed %s: %s",
                                                                   cid[:8], _tce)
                                                # Post-cancel: verify order didn't fill during cancel RTT
                                                # (DZ-1 lesson: cancel(matched) = no-op success)
                                                if _tc_cancel_ok and _ws_user and _ws_user.connected:
                                                    time.sleep(0.05)  # 50ms for WS propagation
                                                    if _ws_user.get_order_status(_tc_oid) == "MATCHED":
                                                        # Order filled during cancel — account for it
                                                        _tc_det = _ws_user.get_order_detail(_tc_oid)
                                                        _pc_sz = _tc_size
                                                        if _tc_det and _tc_det.get("size_matched", 0) > 0:
                                                            _pc_sz = _tc_det["size_matched"]
                                                        if _unfilled_side == "UP":
                                                            old_v = mkt["up_shares"] * mkt["up_avg_price"]
                                                            mkt["up_shares"] += _pc_sz
                                                            mkt["up_avg_price"] = (old_v + _pc_sz * _tc_maker_px) / mkt["up_shares"]
                                                        else:
                                                            old_v = mkt["down_shares"] * mkt["down_avg_price"]
                                                            mkt["down_shares"] += _pc_sz
                                                            mkt["down_avg_price"] = (old_v + _pc_sz * _tc_maker_px) / mkt["down_shares"]
                                                        mkt["entry_cost"] += _pc_sz * _tc_maker_px
                                                        _bump_fill(state, "filled")
                                                        mkt["pending_orders"] = [
                                                            p for p in pending if p is not _unfilled_po]
                                                        if not mkt["pending_orders"]:
                                                            mkt["fills_confirmed"] = True
                                                        mkt["_taker_converted"] = True
                                                        logger.warning(
                                                            "TAKER CONVERT ABORT (post-cancel) %s %s: "
                                                            "maker FILLED during cancel RTT (%.1f @ $%.3f) "
                                                            "— phantom fill recovered, NO taker placed",
                                                            cid[:8], _unfilled_side, _pc_sz, _tc_maker_px)
                                                        _tc_cancel_ok = False  # block taker placement
                                            if _tc_cancel_ok:
                                                # Re-buy as taker: hit best ask
                                                _tc_taker_px = round(_tc_best_ask + 0.01, 2)
                                                _tc_amount = round(_tc_size * _tc_taker_px, 2)
                                                try:
                                                    _tc_r = client.buy_shares(
                                                        _tc_tok, _tc_amount, price=_tc_taker_px)
                                                    _tc_status = ""
                                                    _tc_new_oid = ""
                                                    if isinstance(_tc_r, dict):
                                                        _tc_status = _tc_r.get("status", "")
                                                        _tc_new_oid = _tc_r.get("orderID", "")
                                                    _tc_fill_sz = _tc_size
                                                    if isinstance(_tc_r, dict):
                                                        _taking = _tc_r.get("takingAmount", "")
                                                        if _taking and str(_taking).strip():
                                                            try:
                                                                _tc_fill_sz = float(_taking)
                                                            except (ValueError, TypeError):
                                                                pass
                                                    if _tc_status == "matched":
                                                        # Taker filled — update position
                                                        if _unfilled_side == "UP":
                                                            old_v = mkt["up_shares"] * mkt["up_avg_price"]
                                                            mkt["up_shares"] += _tc_fill_sz
                                                            mkt["up_avg_price"] = (old_v + _tc_fill_sz * _tc_taker_px) / mkt["up_shares"]
                                                        else:
                                                            old_v = mkt["down_shares"] * mkt["down_avg_price"]
                                                            mkt["down_shares"] += _tc_fill_sz
                                                            mkt["down_avg_price"] = (old_v + _tc_fill_sz * _tc_taker_px) / mkt["down_shares"]
                                                        mkt["entry_cost"] += _tc_fill_sz * _tc_taker_px
                                                        _bump_fill(state, "filled")
                                                        # Remove from pending
                                                        mkt["pending_orders"] = [
                                                            p for p in pending if p is not _unfilled_po]
                                                        if not mkt["pending_orders"]:
                                                            mkt["fills_confirmed"] = True
                                                        mkt["_taker_converted"] = True
                                                        logger.info(
                                                            "TAKER CONVERT %s %s: maker@%.2f→taker@%.2f "
                                                            "(spread=%.3f, %.1f shares) cost=$%.2f | "
                                                            "UP=%.1f DN=%.1f total=$%.2f",
                                                            cid[:8], _unfilled_side,
                                                            _tc_maker_px, _tc_taker_px, _tc_spread,
                                                            _tc_fill_sz, _tc_fill_sz * _tc_taker_px,
                                                            mkt["up_shares"], mkt["down_shares"],
                                                            mkt["entry_cost"])
                                                    else:
                                                        # Taker went live (unlikely with ask+1¢ pricing)
                                                        _new_po = dict(_unfilled_po)
                                                        _new_po["order_id"] = _tc_new_oid
                                                        _new_po["price"] = _tc_taker_px
                                                        _new_po["order_ts"] = time.time()
                                                        _new_po["_taker_convert"] = True
                                                        mkt["pending_orders"] = [
                                                            p for p in pending if p is not _unfilled_po
                                                        ] + [_new_po]
                                                        mkt["_taker_converted"] = True
                                                        logger.warning(
                                                            "TAKER CONVERT PENDING %s %s: taker@%.2f "
                                                            "status=%s — still on book",
                                                            cid[:8], _unfilled_side,
                                                            _tc_taker_px, _tc_status)
                                                except Exception as _tce2:
                                                    logger.error(
                                                        "TAKER CONVERT BUY FAILED %s %s: %s — "
                                                        "maker cancelled, taker failed, ORDER LOST",
                                                        cid[:8], _unfilled_side, _tce2)
                                    else:
                                        logger.info(
                                            "TAKER CONVERT SKIP %s %s: spread %.3f > max %.3f",
                                            cid[:8], _unfilled_side, _tc_spread,
                                            _TAKER_CONVERT_MAX_SPREAD)

            # If nothing remaining, skip REST entirely
            if not pending:
                continue

        try:
            # FIX #1: Use get_trades() for reliable fill confirmation
            # "not in open_orders" could mean cancelled, not filled
            trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
            trade_order_ids = set()
            for t in (trades or []):
                # Trades reference taker_order_id or maker_orders
                taker_id = t.get("taker_order_id", "")
                if taker_id:
                    trade_order_ids.add(taker_id)
                for mo in t.get("maker_orders", []):
                    mid = mo.get("order_id", "") if isinstance(mo, dict) else ""
                    if mid:
                        trade_order_ids.add(mid)

            # Also check open orders as secondary signal
            open_orders = client.get_orders(market=cid)
            open_ids = {o.get("id", "") for o in open_orders} if open_orders else set()

            filled = []
            still_open = []
            for po in pending:
                oid = po.get("order_id", "")
                if oid and oid in trade_order_ids:
                    # Confirmed by trade record — definitely filled
                    filled.append(po)
                elif oid and oid in open_ids:
                    # Still on book — not filled yet
                    still_open.append(po)
                else:
                    # Not in trades AND not in open orders → likely cancelled
                    _bump_fill(state, "cancelled")
                    _log_order("cancelled_external", po.get("order_id", ""), cid,
                               outcome=po.get("outcome", ""))
                    logger.info("Order %s %s: not in trades or open → cancelled",
                                cid[:8], po["outcome"])

            if filled:
                # FIX #7: Don't reset — instant fills from apply_fill are already correct
                # Just ADD newly confirmed fills on top
                for f in filled:
                    outcome = f["outcome"]
                    price = f["price"]
                    size = f["size"]
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * price) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * price) / mkt["down_shares"]
                    mkt["entry_cost"] += size * price
                    _bump_fill(state, "filled")
                    # Get midpoint at fill time for AS measurement
                    _fill_mid = 0.0
                    _tok = f.get("token_id", "")
                    if _tok and hasattr(client, "get_midpoint"):
                        _fill_mid = _poly_midpoint(client, _tok)
                    # AS metrics: BTC price at fill + time to fill
                    _title = mkt.get("title", "").lower()
                    _fill_sym = "ETHUSDT" if "ethereum" in _title else "BTCUSDT"
                    _btc_fill = _price(_fill_sym)
                    _order_ts = f.get("order_ts", 0)
                    _ttf = round(time.time() - _order_ts, 1) if _order_ts > 0 else 0
                    _log_order("fill", f.get("order_id", ""), cid,
                               outcome=outcome, price=price, size=size,
                               mid_at_fill=round(_fill_mid, 4) if _fill_mid else 0,
                               btc_at_fill=round(_btc_fill, 2),
                               time_to_fill_s=_ttf)
                    # Schedule post-fill check (60s later) for AS cost measurement
                    if _tok:
                        _post_fill_checks.append(
                            (time.time() + 60, f.get("order_id", ""), cid, _tok))
                    logger.info("FILL CONFIRMED %s %s: %.1f @ $%.3f mid=%.3f",
                                cid[:8], outcome, size, price, _fill_mid)

                mkt["pending_orders"] = still_open
                if not still_open:
                    mkt["fills_confirmed"] = True
                    logger.info("ALL FILLS CONFIRMED %s: UP=%.1f DN=%.1f cost=$%.2f",
                                cid[:8], mkt["up_shares"], mkt["down_shares"],
                                mkt["entry_cost"])

        except Exception as e:
            logger.warning("Fill check failed for %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  State
# ═══════════════════════════════════════

_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}


def _bump_fill(state: dict, event: str, n: int = 1):
    """Increment fill rate counter. event: submitted/filled/cancelled/expired."""
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


def _fill_rate(state: dict) -> tuple[float, int, int]:
    """Returns (fill_rate_pct, filled, submitted). 0% if no data."""
    fs = state.get("fill_stats", _FILL_STATS_DEFAULT)
    s, f = fs.get("submitted", 0), fs.get("filled", 0)
    return (f / s * 100 if s > 0 else 0.0), f, s


def _load() -> dict:
    if not os.path.exists(_STATE_PATH):
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "last_scan": "",
                "fill_stats": dict(_FILL_STATS_DEFAULT)}
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        return d
    except Exception:
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "last_scan": "",
                "fill_stats": dict(_FILL_STATS_DEFAULT)}


def _save(state: dict):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_STATE_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _log_positions(state: dict):
    """Append position snapshot to mm_positions.jsonl (post-session analysis)."""
    try:
        markets = state.get("markets", {})
        if not markets:
            return
        ts = datetime.now(_HKT).isoformat()
        for cid, m in markets.items():
            up_s = m.get("up_shares", 0)
            dn_s = m.get("down_shares", 0)
            if up_s == 0 and dn_s == 0:
                continue
            row = {"ts": ts, "cid": cid[:16], "up_s": round(up_s, 2),
                   "dn_s": round(dn_s, 2), "up_a": round(m.get("up_avg_price", 0), 4),
                   "dn_a": round(m.get("down_avg_price", m.get("dn_avg_price", 0)), 4),
                   "cost": round(m.get("entry_cost", 0), 2)}
            with open(_POS_LOG, "a") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # non-critical logging


def _to_dict(s: MMMarketState) -> dict:
    # FIX #10: base fields from dataclass
    d = {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}
    # Preserve runtime fields (pending_orders, fills_confirmed)
    # These are added by the runner, not the dataclass
    return d


def _from_dict(d: dict) -> MMMarketState:
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def _log_trade(record: dict, log_path: str = ""):
    os.makedirs(_LOG_DIR, exist_ok=True)
    _path = log_path or _TRADE_LOG
    with open(_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _log_order(event: str, order_id: str, cid: str, **kwargs):
    """Per-order lifecycle log: submit/fill/cancel/post_fill.

    Enables AS analysis: time_to_fill, mid_at_fill, mid_60s_post_fill.
    """
    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": event,  # submit | fill | cancel | post_fill
        "order_id": order_id[:16] if order_id else "",
        "cid": cid[:8] if cid else "",
    }
    record.update(kwargs)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_ORDER_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


# Deferred post-fill checks: list of (check_time, order_id, cid, token_id)
_post_fill_checks: list[tuple[float, str, str, str]] = []


def _get_rolling_wr(state: dict, window: int = 30) -> tuple[float, int]:
    """Rolling win rate over last N resolved markets that actually filled.

    Only counts markets with entry_cost > 0 (i.e., orders were filled).
    Unfilled markets (entry_cost=0, PnL=0) are excluded — no fill = no play.
    Returns (wr, count). If count < 5, returns (0.68, count) = assume baseline.
    """
    resolved = [m for m in state["markets"].values() if m["phase"] == "RESOLVED" and not m.get("paper")]
    # Only count real markets that had fills (entry_cost > 0), exclude paper
    filled = [m for m in resolved if m.get("entry_cost", 0) > 0 or m.get("realized_pnl", 0) != 0]
    recent = filled[-window:] if len(filled) > window else filled
    if len(recent) < 5:
        return 0.68, len(recent)  # not enough data, assume baseline
    wins = sum(1 for m in recent if m.get("realized_pnl", 0) > 0)
    return wins / len(recent), len(recent)


def _get_risk_mode(state: dict) -> str:
    """Determine risk mode based on rolling WR.

    NORMAL (WR >= 62%):  full dual-layer (hedge + directional)
    DEFENSIVE (55-62%):  shift budget toward hedge
    HEDGE_ONLY (<55%):   no directional, pure hedge
    STOPPED (<50%):      stop trading completely
    """
    wr, count = _get_rolling_wr(state, window=30)

    if count < 10:
        return "NORMAL"  # not enough data

    # Thresholds — calibrated to W/L ratio 3.2x system
    # Breakeven WR = 1 / (1 + W/L_ratio) = 1 / 4.2 = 24%
    # STOPPED at 28% = 4pp buffer above breakeven
    if wr < 0.28:
        logger.warning("RISK MODE: STOPPED — rolling WR %.1f%% (%d trades) < 28%%", wr*100, count)
        _tg_alert(f"🛑 MM STOPPED: WR {wr*100:.0f}% < 28% ({count} trades). Manual review needed.")
        return "STOPPED"
    elif wr < 0.30:
        logger.warning("RISK MODE: HEDGE_ONLY — rolling WR %.1f%% (%d trades) < 30%%",
                        wr*100, count)
        return "HEDGE_ONLY"
    elif wr < 0.58:
        logger.info("RISK MODE: DEFENSIVE — rolling WR %.1f%% (%d trades) < 58%%", wr*100, count)
        return "DEFENSIVE"
    else:
        return "NORMAL"


# ═══════════════════════════════════════
#  Resolution
# ═══════════════════════════════════════

def _check_resolutions(state: dict, client=None):
    now_ms = int(time.time() * 1000)
    for cid, md in list(state["markets"].items()):
        if md["phase"] == "RESOLVED":
            continue
        end_ms = md.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + 120_000:
            continue
        start_ms = md.get("window_start_ms", 0)
        if start_ms <= 0:
            continue

        dur = end_ms - start_ms
        interval = "5m" if dur <= 5*60_000 else "15m" if dur <= 15*60_000 else "1h"
        # Detect symbol from market title
        _title = md.get("title", "").lower()
        _sym = "ETHUSDT" if "ethereum" in _title else "BTCUSDT"
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={_sym}&interval={interval}&startTime={start_ms}&limit=1")
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}), timeout=10) as r:
                data = json.loads(r.read())
        except Exception:
            continue
        if not data:
            continue

        btc_o, btc_c = float(data[0][1]), float(data[0][4])
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = _from_dict(md)
        _is_paper = md.get("paper", False)

        # ── Pre-resolve reconciliation: bot state vs on-chain trades ──
        # ⚠️ DZ-2: Bot state vs on-chain drift. Phantom/partial fills compound bankroll error. See docs/DANGER_ZONES.md
        _on_chain_up = -1.0  # -1 = skip reconciliation
        _on_chain_dn = 0.0
        try:
            _trades = (
                client.get_trades(market=cid)
                if client and hasattr(client, "get_trades")
                else []
            )
            if _trades:
                _on_chain_up = 0.0
                _on_chain_dn = 0.0
                for _t in _trades:
                    _side = _t.get("side", "")
                    _asset_id = _t.get("asset_id", "")
                    _size = float(_t.get("size", 0))
                    if _asset_id == ms.up_token_id:
                        if _side == "BUY":
                            _on_chain_up += _size
                        elif _side == "SELL":
                            _on_chain_up -= _size
                    elif _asset_id == ms.down_token_id:
                        if _side == "BUY":
                            _on_chain_dn += _size
                        elif _side == "SELL":
                            _on_chain_dn -= _size
        except Exception as e:
            logger.warning(
                "RECONCILE FAIL %s: %s — using bot state", cid[:8], e
            )

        if _on_chain_up >= 0:
            _bot_up = ms.up_shares
            _bot_dn = ms.down_shares
            if abs(_on_chain_up - _bot_up) > 1 or abs(_on_chain_dn - _bot_dn) > 1:
                # Safety: if bot avg_price is 0 (never tracked these fills),
                # we can't calculate cost → using chain shares would produce
                # garbage PnL (payout - $0 cost = inflated profit).
                # In this case, LOG the mismatch but use BOT state (safer: undercount > overcount).
                _has_cost = (ms.up_avg_price > 0 or ms.down_avg_price > 0)
                if not _has_cost:
                    logger.error(
                        "RECONCILE MISMATCH %s: bot UP=%.1f DN=%.1f"
                        " | chain UP=%.1f DN=%.1f — BUT avg_price=0, "
                        "CANNOT reconcile cost → using BOT state (undercount)",
                        cid[:8], _bot_up, _bot_dn, _on_chain_up, _on_chain_dn)
                else:
                    logger.error(
                        "RECONCILE MISMATCH %s: bot UP=%.1f DN=%.1f"
                        " | chain UP=%.1f DN=%.1f — USING CHAIN",
                        cid[:8], _bot_up, _bot_dn, _on_chain_up, _on_chain_dn)
                    ms.up_shares = _on_chain_up
                    ms.down_shares = _on_chain_dn
                    ms.entry_cost = (
                        _on_chain_up * ms.up_avg_price
                        + _on_chain_dn * ms.down_avg_price
                    )

        pnl = resolve_market(ms, result)
        state["markets"][cid] = _to_dict(ms)

        # Paper trades: log separately, don't affect real bankroll/risk
        if _is_paper:
            state["paper_pnl"] = state.get("paper_pnl", 0) + pnl
            state["paper_markets"] = state.get("paper_markets", 0) + 1
            _coin_label = "ETH" if "ethereum" in md.get("title", "").lower() else ("SOL" if "solana" in md.get("title", "").lower() else "BTC")
            _log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                         "result": result, "pnl": round(pnl, 4),
                         "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                         "paper_total_pnl": round(state.get("paper_pnl", 0), 2),
                         "coin": _coin_label, "paper": True},
                        log_path=os.path.join(_LOG_DIR, "mm_paper_trades.jsonl"))
            # Both-sides experiment: extra resolution log
            if md.get("both_sides"):
                _bs_res = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "resolution",
                    "cid": cid[:8], "coin": _coin_label, "result": result,
                    "pnl": round(pnl, 4),
                    "up_shares": md.get("up_shares", 0),
                    "down_shares": md.get("down_shares", 0),
                    "combined": md.get("bs_combined", 0),
                    "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                    "both_filled": md.get("up_shares", 0) > 0 and md.get("down_shares", 0) > 0,
                    "single_fill": (md.get("up_shares", 0) > 0) != (md.get("down_shares", 0) > 0),
                }
                try:
                    with open(_BOTHSIDES_LOG, "a") as _bsf:
                        _bsf.write(json.dumps(_bs_res) + "\n")
                except Exception:
                    pass
            d = "↑" if result == "UP" else "↓"
            _bs_tag = " [BS]" if md.get("both_sides") else ""
            print(f"  PAPER {_coin_label} {cid[:8]} {d}{_bs_tag} | PnL ${pnl:+.2f} | Paper Total ${state.get('paper_pnl', 0):.2f}")
            continue  # skip real PnL/risk updates

        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] += 1

        if pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 8:
                cd = datetime.now(tz=_HKT) + timedelta(hours=24)
                state["cooldown_until"] = cd.isoformat()
                logger.warning("8 consecutive losses → COOLDOWN until %s", cd.strftime("%H:%M HKT"))
        else:
            state["consecutive_losses"] = 0

        # ── W/L Ratio Monitor (every 5 resolved, warn if < 2.0x) — real trades only ──
        _wins_list = [m.get("realized_pnl", 0) for m in state["markets"].values()
                      if m.get("phase") == "RESOLVED" and m.get("realized_pnl", 0) > 0 and not m.get("paper")]
        _losses_list = [abs(m.get("realized_pnl", 0)) for m in state["markets"].values()
                        if m.get("phase") == "RESOLVED" and m.get("realized_pnl", 0) < 0 and not m.get("paper")]
        _n_filled = len(_wins_list) + len(_losses_list)
        if _n_filled >= 5 and _n_filled % 5 == 0:
            _avg_win = sum(_wins_list) / len(_wins_list) if _wins_list else 0
            _avg_loss = sum(_losses_list) / len(_losses_list) if _losses_list else 1
            _wl_ratio = _avg_win / _avg_loss if _avg_loss > 0 else 999
            _wr = len(_wins_list) / _n_filled
            if _wl_ratio < 2.0:
                logger.warning("⚠️ W/L RATIO %.1fx < 2.0x (WR=%.0f%%, n=%d)",
                               _wl_ratio, _wr * 100, _n_filled)
            else:
                logger.info("W/L RATIO %.1fx (WR=%.0f%%, n=%d)",
                            _wl_ratio, _wr * 100, _n_filled)

        fr_pct, _, _ = _fill_rate(state)
        _total_rounds = md.get("rounds", 0) + 1
        _log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                     "result": result, "pnl": round(pnl, 4),
                     "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                     "total_pnl": round(state["total_pnl"], 2),
                     "fill_rate_pct": round(fr_pct, 1),
                     "rounds": _total_rounds})

        d = "↑" if result == "UP" else "↓"
        _rd_str = f" R{_total_rounds}" if _total_rounds > 1 else ""
        print(f"  RESOLVED {cid[:8]} {d}{_rd_str} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")


# ═══════════════════════════════════════
#  Main Cycle — 5s fast loop + 30s heavy ops
# ═══════════════════════════════════════

_last_heavy_ts: float = 0  # module-level for heavy operation throttle


def run_cycle(state: dict, gamma: GammaClient, client,
              config: MMConfig, dry_run: bool,
              continuous_momentum: bool = False,
              both_sides: bool = False) -> dict:
    global _last_heavy_ts
    now = datetime.now(tz=_HKT)
    now_ms = int(time.time() * 1000)
    now_s = time.time()

    # Is this a heavy cycle? (every 30s: discovery, signal pipeline, new entries)
    is_heavy = (now_s - _last_heavy_ts) >= _HEAVY_INTERVAL_S

    # Daily reset
    today = now.strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today

    # Kill switches — FIX #11: use % of bankroll, not absolute $50
    br = state.get("bankroll", 100.0)

    # ⚠️ DZ-6: Hard stop + daily kill cancel ALL wallet orders — includes other bots. See docs/DANGER_ZONES.md
    # HARD STOP: total PnL drops >20% of HIGH WATER MARK → permanent halt
    # Auto-updates on deposit detection (balance > previous high water mark)
    _total_pnl = state.get("total_pnl", 0.0)
    _hwm = state.get("high_water_mark", 0)
    if br > _hwm and br > 10:
        if _hwm > 0 and br > _hwm * 1.3:
            logger.info("DEPOSIT DETECTED: balance $%.2f >> HWM $%.2f → updating", br, _hwm)
        state["high_water_mark"] = br
        _hwm = br
    if _hwm <= 0:
        _hwm = br if br > 10 else 100.0
    if _total_pnl < -_hwm * 0.20:
        logger.critical("💀 HARD STOP: total PnL $%.2f = %.0f%% of HWM $%.0f. Manual restart required.",
                        _total_pnl, _total_pnl / _hwm * 100, _hwm)
        _tg_alert(f"💀 HARD STOP: PnL ${_total_pnl:.2f} ({_total_pnl/_hwm*100:.0f}% of ${_hwm:.0f}). Manual restart needed.")
        state["hard_stopped"] = True
        # Cancel all open orders before halting
        if client and hasattr(client, "get_orders") and not dry_run:
            try:
                _all_orders = client.get_orders()
                for _o in (_all_orders or []):
                    try:
                        client.client.cancel(order_id=_o.get("id", ""))
                    except Exception:
                        pass
                logger.info("HARD STOP: cancelled %d open orders", len(_all_orders or []))
            except Exception:
                pass
        return state
    if state.get("hard_stopped"):
        logger.warning("💀 HARD STOPPED. Clear 'hard_stopped' from state to resume.")
        return state

    # Daily loss: graduated response (Option B)
    # Tier 1: >$10 loss → sizing 3%→2% (DEFENSIVE)
    # Tier 2: >$20 loss → sizing 2%→1% (HEDGE_ONLY)
    # Tier 3: >$30 loss → STOP 2 hours (cooldown)
    # Tier 4: >$45 loss → STOP until tomorrow (hard kill)
    # Cooldown check (must be ABOVE daily loss tiers — otherwise Tier 3 return blocks expiry)
    cd = state.get("cooldown_until", "")
    if cd:
        try:
            if now < datetime.fromisoformat(cd):
                return state  # still in cooldown
        except ValueError:
            pass
        state["consecutive_losses"] = 0
        state["cooldown_until"] = ""
        logger.info("COOLDOWN EXPIRED — resuming trading")

    # Daily loss graduated response (Option B)
    _daily_loss = -state.get("daily_pnl", 0)  # positive = loss amount
    _daily_budget_mult = 1.0  # sizing multiplier (1.0 = normal)
    if _daily_loss > 45:
        logger.warning("DAILY KILL: loss $%.2f > $45. Halted until tomorrow.", _daily_loss)
        _tg_alert(f"🔴 DAILY KILL: loss ${_daily_loss:.2f} > $45. Halted until tomorrow.")
        # Cancel all open orders before halting (prevent orphans on CLOB)
        if client and hasattr(client, "client") and not dry_run:
            try:
                _all = client.get_orders()
                for _o in (_all or []):
                    client.client.cancel(order_id=_o.get("id", ""))
                logger.info("DAILY KILL: cancelled %d orphan orders", len(_all or []))
            except Exception as e:
                logger.warning("DAILY KILL cancel failed: %s", e)
        return state
    elif _daily_loss > 30:
        _cd_end = (now + timedelta(hours=2)).isoformat()
        if not state.get("cooldown_until"):
            state["cooldown_until"] = _cd_end
            logger.warning("DAILY COOLDOWN: loss $%.2f > $30. Pausing 2 hours.", _daily_loss)
            _tg_alert(f"🟡 DAILY COOLDOWN: loss ${_daily_loss:.2f} > $30. 2h pause.")
            # Cancel all open orders before cooldown
            if client and hasattr(client, "client") and not dry_run:
                try:
                    _all = client.get_orders()
                    for _o in (_all or []):
                        client.client.cancel(order_id=_o.get("id", ""))
                    logger.info("COOLDOWN: cancelled %d orphan orders", len(_all or []))
                except Exception as e:
                    logger.warning("COOLDOWN cancel failed: %s", e)
        return state
    elif _daily_loss > 20:
        _daily_budget_mult = 0.33  # 3% → 1%
        if is_heavy:
            logger.info("DAILY DEFENSIVE-2: loss $%.2f > $20, sizing ×0.33", _daily_loss)
    elif _daily_loss > 10:
        _daily_budget_mult = 0.67  # 3% → 2%
        if is_heavy:
            logger.info("DAILY DEFENSIVE-1: loss $%.2f > $10, sizing ×0.67", _daily_loss)

    # ── FAST OPS (every 5s): price, cancel defense, fill check, resolution ──

    # Price (3s cache — always fresh)
    btc = _btc_price()
    if btc <= 0:
        return state

    # Refresh bankroll (every heavy cycle only — CLOB call)
    if is_heavy:
        vol = _vol_1m()  # 60s cache
        if client and hasattr(client, "get_usdc_balance") and not dry_run:
            try:
                bal = client.get_usdc_balance()
                if bal is not None and bal > 0:
                    state["bankroll"] = bal
            except Exception:
                pass

    # ── FAST MONITORING (every 5s): OB imbalance for active positions ──
    if not dry_run:
        for _cid, _mkt in state["markets"].items():
            if _mkt["phase"] == "OPEN" and _mkt.get("up_token_id"):
                _obi = _poly_ob_imbalance(client, _mkt["up_token_id"])
                _mid = _poly_midpoint(client, _mkt["up_token_id"])
                if abs(_obi) > 0.3 or _mid > 0:
                    logger.debug("MONITOR %s: OBI=%.2f mid=%.3f", _cid[:8], _obi, _mid)

    # ── Risk mode check (rolling WR adaptive) ──
    risk_mode = _get_risk_mode(state) if is_heavy else state.get("_risk_mode", "NORMAL")
    if is_heavy:
        state["_risk_mode"] = risk_mode
    if risk_mode == "STOPPED":
        logger.warning("STOPPED: WR < 50%% — no trading until manual review")
        return state

    # ── HEAVY OPS (every 30s): discovery, signal pipeline, new entries ──
    if is_heavy:
        _last_heavy_ts = now_s
        # 2check fix: periodic cleanup of ws_user order cache (prevent unbounded growth)
        if _ws_user:
            _ws_user.cleanup_old_orders(max_age_s=3600)
    else:
        # Fast cycle: skip discovery + entry, go to cancel/fill/resolve
        pass

    # Discover → watchlist (gated by is_heavy via _SCAN_S check)
    last = state.get("last_scan", "")
    since = 999
    if last:
        try:
            since = (now - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            pass
    if since >= _SCAN_S:
        for mkt, winfo in _discover(gamma, config):
            cid = mkt.condition_id
            if cid not in state["markets"] and cid not in state.get("watchlist", {}):
                state.setdefault("watchlist", {})[cid] = {
                    "cid": cid, "title": mkt.title,
                    "up_tok": mkt.yes_token_id, "dn_tok": mkt.no_token_id,
                    "start_ms": winfo["start_ms"], "end_ms": winfo["end_ms"],
                    "end_time": winfo["end_time"]}
                # Subscribe to WS order book feed for these tokens
                if _ws_poly:
                    _ws_poly.subscribe(
                        [mkt.yes_token_id, mkt.no_token_id],
                        condition_id=cid)
                lead = (winfo["start_ms"] - now_ms) / 60_000
                logger.info("watchlist + %s (%.0fm): %s", cid[:8], lead, mkt.title[:45])
        state["last_scan"] = now.isoformat()

    # ── Newbie protection: override bet_pct + max_concurrent for first N hours ──
    _live_start = state.get("live_start_ts", 0)
    _in_protection = (_live_start > 0
                      and time.time() - _live_start < _PROTECTION_HOURS * 3600)
    if _in_protection:
        config = _copy(config)
        config.bet_pct = _PROTECTION_BET_PCT
        config.max_concurrent_markets = _PROTECTION_MAX_MARKETS
        remaining_h = _PROTECTION_HOURS - (time.time() - _live_start) / 3600
        if is_heavy:
            logger.info("🛡️ PROTECTION: bet=%.0f%% max=%d mkt | %.1fh remaining",
                        config.bet_pct * 100, config.max_concurrent_markets, remaining_h)
    elif _live_start > 0 and is_heavy:
        # Protection just ended — log once
        if not state.get("_protection_ended_logged"):
            logger.info("🛡️ PROTECTION ENDED — switching to normal: bet=%.0f%% max=%d",
                        config.bet_pct * 100, config.max_concurrent_markets)
            state["_protection_ended_logged"] = True

    # Enter active markets from watchlist (heavy cycle only — signal pipeline is slow)
    active = sum(1 for m in state["markets"].values() if m["phase"] != "RESOLVED")
    if not is_heavy:
        active = config.max_concurrent_markets  # skip entry on fast cycles

    # ── Phase 4: parallel pre-fetch slow REST data (read-only, cache warming) ──
    # Holder imbalance (Data API, 200-500ms per call) + cross-exchange price +
    # vol_1m + CVD — all cached, so the sequential loop below hits cache.
    _pf_t0 = time.time()
    _pf_wl = [(cid, wl) for cid, wl in state.get("watchlist", {}).items()
              if cid not in state["markets"]]
    _pf_syms = set()
    for _, _wl in _pf_wl:
        _t = _wl.get("title", "").lower()
        _pf_syms.add("ETHUSDT" if "ethereum" in _t else ("SOLUSDT" if "solana" in _t else "BTCUSDT"))
    # Also pre-fetch for open markets (phased rung checkpoint + re-entry use holder/xprice)
    _pf_open = [(cid, m) for cid, m in state["markets"].items() if m["phase"] == "OPEN"]
    for _, _m in _pf_open:
        _t = _m.get("title", "").lower()
        _pf_syms.add("ETHUSDT" if "ethereum" in _t else "BTCUSDT")
    with ThreadPoolExecutor(max_workers=6) as _pool:
        _futs = []
        for _cid, _wl in _pf_wl:
            _futs.append(_pool.submit(_holder_imbalance, _cid, _wl["up_tok"]))
        for _cid, _m in _pf_open:
            _futs.append(_pool.submit(_holder_imbalance, _cid, _m.get("up_token_id", "")))
        for _s in _pf_syms:
            _futs.append(_pool.submit(_cross_exchange_price, _s))
            _futs.append(_pool.submit(_vol_1m, _s))
            _futs.append(_pool.submit(_cvd_buy_ratio, _s, 3))
        for _f in _futs:
            try:
                _f.result(timeout=8)
            except Exception:
                pass  # cache miss is fine — loop's own call will handle it
    logger.debug("Pre-fetch: %d holder + %d sym in %.1fs",
                 len(_pf_wl) + len(_pf_open), len(_pf_syms), time.time() - _pf_t0)

    # FIX #1: Dedup — get all open orders on CLOB to avoid duplicate submissions
    _existing_markets = set()
    if client and hasattr(client, "get_orders") and not dry_run:
        try:
            _open = client.get_orders()
            _existing_markets = {o.get("market", "") for o in (_open or [])}
        except Exception:
            pass

    _heavy_loop_t0 = time.time()
    _heavy_loop_n = len(state.get("watchlist", {}))
    for cid, wl in list(state.get("watchlist", {}).items()):
        if cid in state["markets"]:
            del state["watchlist"][cid]
            continue
        # FIX #1: Skip if we already have orders on CLOB for this market
        if cid in _existing_markets:
            logger.info("SKIP %s: already have orders on CLOB (dedup)", cid[:8])
            del state["watchlist"][cid]
            continue
        if active >= config.max_concurrent_markets:
            break
        if now_ms < wl["start_ms"]:
            continue
        if now_ms > wl["end_ms"]:
            del state["watchlist"][cid]
            continue

        _elapsed_ms = now_ms - wl["start_ms"]

        # ── Dead Hours Gate: skip low-σ hours (σ_poly analysis: 04-06 HKT worst) ──
        _hkt_hour = datetime.now(tz=_HKT).hour
        if _hkt_hour in {4, 5}:
            continue  # stay in watchlist, check next cycle (hour changes)

        # ── Late Gate: don't enter with < 1.5 min remaining ──
        if now_ms > wl["end_ms"] - 90_000:
            logger.info("SKIP %s: < 1.5 min remaining, too late", cid[:8])
            del state["watchlist"][cid]
            continue

        # Enter — detect coin from title
        _title_lower = wl["title"].lower()
        if "ethereum" in _title_lower:
            _sym, _coin_slug = "ETHUSDT", "eth"
        elif "solana" in _title_lower:
            _sym, _coin_slug = "SOLUSDT", "sol"
        elif "xrp" in _title_lower:
            _sym, _coin_slug = "XRPUSDT", "xrp"
        else:
            _sym, _coin_slug = "BTCUSDT", "btc"

        # Observation gate: non-live coins → log signals but skip execution
        _observe_only = _coin_slug not in _LIVE_TRADE_COINS

        # ── Momentum Filter (SKIP for both-sides — W4 has its own signal at line 1531) ──
        _m1_vol = _vol_1m(_sym)
        _m1 = 0.0  # default for both-sides path
        if both_sides:
            pass  # W4 signal handles momentum check at line 1531, skip M1 filter
        elif continuous_momentum:
            # Continuous: current price vs window open (catches late moves)
            _cm_open = _open_at(wl["start_ms"], _sym) or _price(_sym)
            _cm_now = _price(_sym)
            _cm_ret = math.log(_cm_now / _cm_open) if _cm_open > 0 and _cm_now > 0 else 0
            _cm_mins = _elapsed_ms / 60_000
            _cm_thresh = max(0.0005, _m1_vol * math.sqrt(max(1, _cm_mins)) * 0.7)
            _m1_confirmed = abs(_cm_ret) >= _cm_thresh
            _m1 = _cm_ret  # use continuous return for direction
            if not _m1_confirmed:
                if _elapsed_ms < 300_000:
                    continue  # wait up to 5min for momentum (was 3min — +6pp WR per 2688-window study)
                logger.info("SKIP %s: CM weak |%.4f| < %.4f after 5min", cid[:8], _cm_ret, _cm_thresh)
                del state["watchlist"][cid]
                continue
            logger.info("CM confirmed %s: %+.4f (%.2f%%, %.1fσ) [%dmin elapsed]",
                        cid[:8], _cm_ret, _cm_ret * 100, abs(_cm_ret) / _cm_thresh,
                        int(_cm_mins))
        else:
            # M1 only: minute 0→1 return
            _m1 = _m1_return(_sym)
            _m1_thresh = max(0.0005, _m1_vol * 1.0)
            _m1_confirmed = abs(_m1) >= _m1_thresh
            if not _m1_confirmed:
                if _elapsed_ms < 300_000:
                    continue  # wait up to 5min (was 3min — +6pp WR per 2688-window study)
                logger.info("SKIP %s: M1 weak |%.4f| < %.4f after 5min", cid[:8], _m1, _m1_thresh)
                del state["watchlist"][cid]
                continue
            logger.info("M1 confirmed %s: %+.4f (%.2f%%, %.1fσ)",
                        cid[:8], _m1, _m1 * 100, abs(_m1) / _m1_thresh)

        # ── Cross-exchange price validation (防 flash crash / anomaly) ──
        _xprice, _xdiv = _cross_exchange_price(_sym)
        _coin_price = _xprice if _xprice > 0 else _price(_sym)
        if _xdiv > 0.003:  # >0.3% divergence across exchanges → anomaly
            logger.warning("SKIP %s: cross-exchange divergence %.2f%% (anomaly)",
                           cid[:8], _xdiv * 100)
            continue  # keep in watchlist, re-check next cycle

        _coin_open = _open_at(wl["start_ms"], _sym) or _coin_price
        _coin_vol = _vol_1m(_sym)
        mins_left = max(1, (wl["end_ms"] - now_ms) / 60_000)

        # Signal pipeline: combine all sources for P(Up)
        mkt = PolyMarket(condition_id=cid, title=wl["title"], category="crypto_15m",
                         yes_token_id=wl["up_tok"], no_token_id=wl["dn_tok"],
                         liquidity=15000)

        # 1. Brownian Bridge (base — always available)
        bridge_p_up = compute_fair_up(_coin_price, _coin_open, _coin_vol, int(mins_left))
        # Fat-tail correction now built into compute_fair_up() via Student-t(ν=5)

        # 2. Order book imbalance (short-term, forward-looking)
        # Removed: assess_edge() — traditional indicators (RSI/MACD/BB/EMA) are
        # backward-looking, cause mean-reversion bias in trending markets.
        # Bridge + OB + cross-exchange = sufficient for 15M binary.
        ob_adjustment = 0.0
        _ob_best_bid = 0.0   # instrument: OB snapshot at submit time
        _ob_best_ask = 0.0
        _ob_bid_vol = 0.0
        _ob_ask_vol = 0.0
        _ob_depth = 0
        if client and hasattr(client, "get_order_book") and not dry_run:
            try:
                up_book = client.get_order_book(wl["up_tok"])
                bids = up_book.get("bids", [])
                asks = up_book.get("asks", [])
                bid_vol = sum(b["size"] for b in bids)
                ask_vol = sum(a["size"] for a in asks)
                _ob_bid_vol = bid_vol
                _ob_ask_vol = ask_vol
                _ob_depth = len(bids) + len(asks)
                if bids:
                    _ob_best_bid = max(b["price"] for b in bids)
                if asks:
                    _ob_best_ask = min(a["price"] for a in asks)
                if bid_vol + ask_vol > 0:
                    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                    ob_adjustment = imbalance * 0.05  # ±5% max adjustment
                    logger.info("OB imbalance=%.3f (bid=%.0f ask=%.0f) → adj=%.3f",
                                imbalance, bid_vol, ask_vol, ob_adjustment)
            except Exception as e:
                logger.debug("OB fetch failed: %s", e)

        # Fair = bridge + OB (no indicator signal)
        fair = bridge_p_up + ob_adjustment
        fair = max(0.05, min(0.95, fair))

        # CVD: taker buy ratio (3 min) — computed here for logging + gate
        _cvd = _cvd_buy_ratio(_sym, minutes=3)

        # ── Log signal data + market snapshot (log-only, no decision impact) ──
        try:
            _sig_record = {
                "ts": datetime.now(tz=_HKT).isoformat(), "cid": cid[:8],
                "coin": _coin_slug, "observe": _observe_only,
                "sym": _sym, "m1": round(_m1, 6),
                "m1_sigma": round(abs(_m1) / _m1_thresh, 2) if locals().get("_m1_thresh", 0) > 0 else 0,
                "bridge": round(bridge_p_up, 4),
                "fair": round(fair, 4), "xdiv": round(_xdiv, 5),
                "ob_adj": round(ob_adjustment, 4), "cvd": round(_cvd, 3),
            }
            # Append market_data snapshot if available (log-only)
            if _mkt_fetcher is not None:
                _snap = _mkt_fetcher.latest()
                if _snap and _snap.price > 0 and _snap.age_ms < 30_000:
                    _sig_record["mkt"] = {
                        "price": round(_snap.price, 2),
                        "src": _snap.sources_responded,
                        "fund_agg": round(_snap.funding_agg, 8),
                        "fund_prem": round(_snap.funding_premium, 2),
                        "oi_total": round(_snap.oi_total / 1e9, 2),  # billions
                        "oi_d5m": round(_snap.oi_delta_5m / 1e9, 3),
                        "ls": round(_snap.ls_ratio, 3),
                        "ls_ext": _snap.ls_extreme,
                        "dvol": round(_snap.dvol, 1),
                        "taker": round(_snap.taker_buy_sell_ratio, 3),
                        "age_ms": round(_snap.age_ms),
                    }
            os.makedirs(_LOG_DIR, exist_ok=True)
            with open(_SIGNAL_LOG, "a") as _sf:
                _sf.write(json.dumps(_sig_record) + "\n")
        except Exception:
            pass

        # M1 vs fair direction conflict
        _fair_up = fair > 0.50
        _m1_up = _m1 > 0

        # ── W4 Both-Sides: buy UP + DOWN with momentum lean ──
        if both_sides:
            # Y1: Skip :45 windows (27% WR vs 44-47% for :00/:15/:30, n=52)
            _win_min = datetime.utcfromtimestamp(wl["start_ms"] / 1000).minute
            if _win_min == 45:
                if not wl.get("_45_skip_logged"):
                    logger.info("W4 SKIP :45 %s: :45 window anomaly (27%% WR)", cid[:8])
                    wl["_45_skip_logged"] = True
                del state["watchlist"][cid]
                continue

            # v4: Skip dead hours (HKT 02:45-07:30 = US late night, low liquidity)
            _hkt_now = datetime.now(tz=_HKT)
            _hkt_hm = _hkt_now.hour * 60 + _hkt_now.minute  # minutes since midnight
            if 165 <= _hkt_hm < 450:  # 02:45 (165min) to 07:30 (450min)
                if not wl.get("_dead_hour_logged"):
                    logger.info("W4 SKIP DEAD %s: HKT %02d:%02d (dead hours 02:45-07:30)", cid[:8], _hkt_now.hour, _hkt_now.minute)
                    wl["_dead_hour_logged"] = True
                del state["watchlist"][cid]
                continue

            # W4 signal: log return from window open at T+300s
            _w4_dir, _w4_mag, _w4_ret = _w4_signal(wl["start_ms"], _sym)
            if _w4_dir == "WAIT":
                continue  # not yet T+300s, keep in watchlist
            if _w4_dir == "SKIP":
                if _elapsed_ms > 600_000:  # 10 min → give up
                    logger.info("W4 SKIP %s: mag=%.1f bps < %d bps after 10min",
                                cid[:8], _w4_mag, _W4_THRESHOLD_BPS)
                    del state["watchlist"][cid]
                continue  # wait longer
            _m1 = _w4_ret  # reuse for downstream logging
            logger.info("W4 SIGNAL %s: %s %+.1f bps", cid[:8], _w4_dir, _w4_mag)

            # 🔴 2CHECK: Poly OB mid pricing — wrong mid = wrong entry price
            _up_mid = 0.0
            _dn_mid = 0.0
            if client and hasattr(client, "get_order_book") and not dry_run:
                try:
                    _up_book = client.get_order_book(wl["up_tok"])
                    _up_bids = _up_book.get("bids", [])
                    _up_asks = _up_book.get("asks", [])
                    if _up_bids and _up_asks:
                        _up_mid = (max(b["price"] for b in _up_bids) + min(a["price"] for a in _up_asks)) / 2
                    _dn_book = client.get_order_book(wl["dn_tok"])
                    _dn_bids = _dn_book.get("bids", [])
                    _dn_asks = _dn_book.get("asks", [])
                    if _dn_bids and _dn_asks:
                        _dn_mid = (max(b["price"] for b in _dn_bids) + min(a["price"] for a in _dn_asks)) / 2
                except Exception as e:
                    logger.debug("W4 OB fetch failed: %s", e)

            # Fallback: use bridge fair if OB unavailable (dry-run / API fail)
            if _up_mid <= 0.01 or _up_mid >= 0.99:
                _up_mid = max(0.05, min(0.95, fair))
            if _dn_mid <= 0.01 or _dn_mid >= 0.99:
                _dn_mid = max(0.05, min(0.95, 1.0 - fair))

            # 🔴 2CHECK: Bid below mid (maker) — 1¢ tick below to ensure maker status
            _TICK = 0.01
            _up_bid = round(max(0.02, _up_mid - _TICK), 2)
            _dn_bid = round(max(0.02, _dn_mid - _TICK), 2)
            _bs_combined = round(_up_bid + _dn_bid, 4)

            # v4: Combined gate — arb requires combined < $1.00.
            # At R=1.0 pure arb, combined >= $0.99 = margin too thin ($0.01/pair).
            # Old cap was $1.05 (allowed losing entries). Now strict $0.99.
            if _bs_combined >= 0.99:
                logger.info("W4 SKIP %s: combined $%.4f >= $0.99 (arb spread too thin)",
                            cid[:8], _bs_combined)
                del state["watchlist"][cid]
                continue

            # v4: Cheap side tiered gate — 17¢-31¢, cheaper = bigger position
            # Tier 1: 25¢-31¢ → 1/3 sizing (test the water)
            # Tier 2: 20¢-25¢ → 2/3 sizing (add)
            # Tier 3: 17¢-20¢ → full sizing (best R/R)
            _cheap_bid = min(_up_bid, _dn_bid)
            if _cheap_bid > 0.31 or _cheap_bid < 0.02:
                logger.info("W4 SKIP %s: cheap side $%.2f outside 2¢-31¢ range",
                            cid[:8], _cheap_bid)
                del state["watchlist"][cid]
                continue
            # Graduated sizing: cheaper = better R/R = bigger position
            # 5 tiers, bankroll-friendly (smallest = 20% of normal)
            _CHEAP_TIERS = [
                (0.17, 1.0),   # T5: ≤17¢ → 100% (R/R 4.9:1)
                (0.20, 0.80),  # T4: 18-20¢ → 80%  (R/R 4.0:1)
                (0.23, 0.60),  # T3: 21-23¢ → 60%  (R/R 3.3:1)
                (0.27, 0.40),  # T2: 24-27¢ → 40%  (R/R 2.7:1)
                (0.31, 0.20),  # T1: 28-31¢ → 20%  (R/R 2.2:1)
            ]
            _cheap_mult = 0.20  # default: smallest
            _cheap_tier = 1
            for _ct_i, (_ct_max, _ct_mult) in enumerate(_CHEAP_TIERS):
                if _cheap_bid <= _ct_max:
                    _cheap_mult = _ct_mult
                    _cheap_tier = 5 - _ct_i  # T5=best, T1=smallest
                    break

            # 🔴 FIX: Lean = SHARE COUNT ratio, not budget ratio
            # Bug was: budget fraction / price → cheap side gets MORE shares than lean side
            # W4 lean R:1 means lean side has R× more SHARES (R = dynamic, see _w4_dynamic_ratio)
            bankroll = state.get("bankroll", 100.0)
            _coin_bet_pct = _BET_PCT_BY_COIN.get(_coin_slug, config.bet_pct)
            _full_budget = bankroll * _coin_bet_pct * _daily_budget_mult * _cheap_mult
            if _full_budget < _MIN_VIABLE_BUDGET:
                logger.info("W4 SKIP %s: budget $%.2f < $%.2f min (tier %d, mult %.0f%%)",
                            cid[:8], _full_budget, _MIN_VIABLE_BUDGET, _cheap_tier, _cheap_mult * 100)
                del state["watchlist"][cid]
                continue
            _budget = _full_budget * _W4_T1_PCT  # T1 = 60%, T2 adds 40% at T+480s if confirmed
            _lean_dir = _w4_dir  # "UP" or "DOWN"

            # Calculate base shares from budget, then apply lean ratio to SHARES
            # Total shares = budget / avg_price_per_share
            _avg_price = (_up_bid + _dn_bid) / 2  # rough avg for sizing
            _total_shares = max(10, _budget / _avg_price)  # at least 10 shares total

            # Dynamic lean ratio by signal magnitude (Q1 adverse selection fix)
            _dyn_ratio = _w4_dynamic_ratio(_w4_mag)
            _lean_share_frac = _dyn_ratio / (_dyn_ratio + 1)    # 0.545 at 1.2:1, 0.500 at 1.0
            _hedge_share_frac = 1.0 / (_dyn_ratio + 1)          # 0.455 at 1.2:1, 0.500 at 1.0

            if _lean_dir == "UP":
                _up_shares = max(config.min_order_size, round(_total_shares * _lean_share_frac, 1))
                _dn_shares = max(config.min_order_size, round(_total_shares * _hedge_share_frac, 1))
            else:
                _up_shares = max(config.min_order_size, round(_total_shares * _hedge_share_frac, 1))
                _dn_shares = max(config.min_order_size, round(_total_shares * _lean_share_frac, 1))

            # 🔴 Budget cap: min_order_size floors can inflate spend beyond budget (bug #6)
            # SOL 1% = $1.25 budget but floors force ~$5 spend. Cap to 1.5x budget max.
            _est_cost = _up_shares * _up_bid + _dn_shares * _dn_bid
            if _est_cost > _budget * 1.5 and _budget > 0:
                _scale = _budget / _est_cost
                _up_shares = max(1, round(_up_shares * _scale, 1))
                _dn_shares = max(1, round(_dn_shares * _scale, 1))
                logger.info("W4 BUDGET CAP %s: est $%.2f > budget $%.2f → scaled to %.1f/%.1f shares",
                            cid[:8], _est_cost, _budget, _up_shares, _dn_shares)

            # 🔴 2CHECK: Double order prevention — only enter if NOT already in state["markets"]
            if cid in state.get("markets", {}):
                logger.warning("W4 DUP %s: already in markets, skip", cid[:8])
                del state["watchlist"][cid]
                continue

            orders = [
                PlannedOrder(token_id=wl["up_tok"], side="BUY",
                             price=_up_bid, size=_up_shares, outcome="UP"),
                PlannedOrder(token_id=wl["dn_tok"], side="BUY",
                             price=_dn_bid, size=_dn_shares, outcome="DOWN"),
            ]
            _cond_rungs_config = []
            n_tranches = 1
            _h_imbalance, _h_delta = 0.0, 0.0
            _whale_action = "NORMAL"
            _cvd_strong_disagree = False
            # 🔴 2CHECK: Live gate — only paper unless --w4-live flag
            if not state.get("_w4_live"):
                _observe_only = True

            # Log W4 entry
            try:
                _bs_entry = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_entry",
                    "cid": cid[:8], "coin": _coin_slug,
                    "lean_dir": _lean_dir, "lean_ratio": _dyn_ratio,
                    "w4_mag_bps": round(_w4_mag, 1),
                    "up_mid": round(_up_mid, 4), "dn_mid": round(_dn_mid, 4),
                    "up_bid": _up_bid, "dn_bid": _dn_bid,
                    "combined": _bs_combined, "cheap_tier": _cheap_tier,
                    "cheap_mult": _cheap_mult, "cheap_bid": _cheap_bid,
                    "up_shares": _up_shares, "dn_shares": _dn_shares,
                    "budget": round(_budget, 2), "bankroll": round(bankroll, 2),
                    "bridge": round(bridge_p_up, 4), "live": not _observe_only,
                }
                with open(_BOTHSIDES_LOG, "a") as _bsf:
                    _bsf.write(json.dumps(_bs_entry) + "\n")
            except Exception:
                pass

            logger.info("W4 %s %s: lean=%s %+.1fbps | UP@$%.2f×%.0f + DN@$%.2f×%.0f = $%.3f %s",
                        "LIVE" if not _observe_only else "PAPER",
                        cid[:8], _lean_dir, _w4_mag,
                        _up_bid, _up_shares, _dn_bid, _dn_shares,
                        _bs_combined, "(PAPER)" if _observe_only else "")
        else:
            # ── Original directional logic ──
            if abs(_m1) >= 0.001 and _fair_up != _m1_up:
                logger.info("SKIP %s: M1/fair CONFLICT (M1=%+.4f %s, fair=%.3f %s)",
                            cid[:8], _m1, "UP" if _m1_up else "DN",
                            fair, "UP" if _fair_up else "DN")
                continue  # keep in watchlist

            # CVD sizing: 3/3 agree → full, 2/3 agree → reduced
            _cvd_agrees = (_fair_up and _cvd > 0.50) or (not _fair_up and _cvd < 0.50)
            _cvd_strong_disagree = (_fair_up and _cvd < 0.45) or (not _fair_up and _cvd > 0.55)
            if _cvd_strong_disagree:
                logger.info("CVD DISAGREE %s: fair %s but CVD %.0f%% → reduced size",
                            cid[:8], "UP" if _fair_up else "DN", _cvd * 100)

            # Market midpoint sanity
            if client and hasattr(client, "get_midpoint") and not dry_run:
                _dir_tok = wl["up_tok"] if fair > 0.50 else wl["dn_tok"]
                _mid = _poly_midpoint(client, _dir_tok)
                if 0 < _mid < 0.38:
                    logger.info("SKIP %s: market mid=%.3f < 0.38 → market disagrees",
                                cid[:8], _mid)
                    continue

            # Whale
            if not _observe_only:
                _tte_s = (wl["end_ms"] - now_ms) / 1000
                _holder_ttl = 5 if _tte_s < 120 else _HOLDER_CACHE_TTL
                _h_imbalance, _h_delta = _holder_imbalance(cid, wl["up_tok"], ttl_override=_holder_ttl)
            else:
                _h_imbalance, _h_delta = 0.0, 0.0
            _whale_action = "NORMAL"
            _whale_favors_up = _h_imbalance > 0
            if abs(_h_imbalance) > 0.30:
                _whale_agrees = (_fair_up and _whale_favors_up) or (not _fair_up and not _whale_favors_up)
                if not _whale_agrees:
                    _whale_action = "FOLLOW_LOG"

        # ── Wide Ladder DCA: 2 auto rungs + 2 conditional (checkpoint) ──
        # Backtest: 0.43/0.37/0.31/0.26, tiered TP at x1.3/1.5/1.8 → Sharpe 0.544
        # Rungs 1-2: auto-place. Rungs 3-4: only if checkpoint passes.
        if not both_sides:  # both-sides already created orders above
            _LADDER_AUTO = [0.43, 0.37]         # always place
            _LADDER_COND = [0.31, 0.26]         # place ONLY if checkpoint passes
            _LADDER_BUDGET_PCT = config.bet_pct

            bankroll = state.get("bankroll", 100.0)
            n_tranches = calc_tranches(bankroll, config)
            _all_rungs = _LADDER_AUTO + _LADDER_COND
            _window_budget = bankroll * _LADDER_BUDGET_PCT * _daily_budget_mult / max(1, n_tranches)
            _rung_budget = _window_budget / len(_all_rungs)

            _dir_tok = wl["up_tok"] if _fair_up else wl["dn_tok"]
            _dir_side = "UP" if _fair_up else "DOWN"

            orders = []
            for _rung_price in _LADDER_AUTO:
                _shares = max(config.min_order_size, _rung_budget / _rung_price)
                orders.append(PlannedOrder(
                    token_id=_dir_tok, side="BUY",
                    price=_rung_price, size=round(_shares, 1),
                    outcome=_dir_side))

            _cond_rungs_config = []
            if _whale_action not in ("FOLLOW_LOG", "EXIT"):
                for _rung_price in _LADDER_COND:
                    _shares = max(config.min_order_size, _rung_budget / _rung_price)
                    _cond_rungs_config.append({
                        "price": _rung_price, "size": round(_shares, 1),
                        "token_id": _dir_tok, "outcome": _dir_side, "placed": False,
                    })
            else:
                logger.info("CHECKPOINT %s: deep rungs disabled (whale=%s)",
                            cid[:8], _whale_action)

            if not orders:
                del state["watchlist"][cid]
                continue

        # CVD disagree → override to single cheap rung (dynamic price)
        # 3/3 agree: keep full ladder. 2/3: reduce to 1 rung at discounted price.
        if not both_sides:  # directional-only post-processing
            if _cvd_strong_disagree and orders:
                _our_fair = fair if _fair_up else (1.0 - fair)
                _disagree_bid = round(max(0.25, min(0.35, _our_fair * 0.60)), 3)
                _dir_tok = orders[0].token_id
                _dir_side = orders[0].outcome
                orders = [PlannedOrder(
                    token_id=_dir_tok, side="BUY",
                    price=_disagree_bid, size=config.min_order_size, outcome=_dir_side)]
                logger.info("CVD REDUCED %s: 1 rung @ $%.3f × %.0f (was %d orders)",
                            cid[:8], _disagree_bid, config.min_order_size, len(orders) + 1)

            if abs(_h_imbalance) > 0.30:
                _whale_agrees = (_fair_up and _whale_favors_up) or (not _fair_up and not _whale_favors_up)
                if _whale_agrees:
                    _whale_action = "AGREE"
                    logger.info("WHALE AGREE %s: imbalance %+.3f confirms %s",
                                cid[:8], _h_imbalance, "UP" if _fair_up else "DOWN")
                elif _whale_action == "FOLLOW_LOG":
                    logger.warning("WHALE FOLLOW(log) %s: imbalance %+.3f — halving orders",
                                   cid[:8], _h_imbalance)
                    if orders:
                        for o in orders:
                            o.size = max(config.min_order_size, o.size * 0.5)

            if abs(_h_delta) > 0.15 and _whale_action == "NORMAL":
                _delta_against = (_fair_up and _h_delta < 0) or (not _fair_up and _h_delta > 0)
                if _delta_against:
                    _whale_action = "EXIT"
                    logger.warning("WHALE EXIT %s: imbalance Δ%+.3f AGAINST %s — halve size",
                                   cid[:8], _h_delta, "UP" if _fair_up else "DOWN")
                    if orders:
                        for o in orders:
                            o.size = max(config.min_order_size, o.size * 0.5)

        _sig_ctx = {"fair": round(fair, 4), "bridge": round(bridge_p_up, 4),
                    "cvd": round(_cvd, 3), "vol": round(_coin_vol, 6),
                    "m1": round(_m1, 6), "ob_adj": round(ob_adjustment, 4),
                    "btc": round(_coin_price, 2),
                    # OB snapshot at submit — answers "why didn't this fill?"
                    "ob_best_bid": round(_ob_best_bid, 4),
                    "ob_best_ask": round(_ob_best_ask, 4),
                    "ob_bid_vol": round(_ob_bid_vol, 1),
                    "ob_ask_vol": round(_ob_ask_vol, 1),
                    "ob_depth": _ob_depth,
                    "coin": _coin_slug, "observe_only": _observe_only,
                    "h_imb": _h_imbalance, "h_delta": _h_delta,
                    "whale": _whale_action}

        # Observation gate: paper trade for non-live coins (same strategy, mock client, no real money)
        if _observe_only:
            _sig_ctx["paper"] = True
            class _PaperClient:
                """Mock client that simulates instant fills for paper trading."""
                def buy_shares(self, tid, amt, price=0):
                    return {"orderID": f"paper_{tid[:8]}_{int(time.time())}",
                            "status": "matched", "dry_run": True}
                def sell_shares(self, tid, amt, price=0):
                    return {"orderID": f"paper_s_{tid[:8]}_{int(time.time())}",
                            "status": "matched", "dry_run": True}
            _paper_client = _PaperClient()
            results = _execute(orders, _paper_client, cid=cid, signal_ctx=_sig_ctx)
            logger.info("PAPER %s %s: fair=%.3f bridge=%.3f %d orders simulated",
                        cid[:8], _coin_slug.upper(), fair, bridge_p_up, len(orders))
        else:
            results = _execute(orders, client, cid=cid, signal_ctx=_sig_ctx)
        ms = MMMarketState(condition_id=cid, title=wl["title"],
                           up_token_id=wl["up_tok"], down_token_id=wl["dn_tok"],
                           window_start_ms=wl["start_ms"], window_end_ms=wl["end_ms"],
                           btc_open_price=_coin_open, phase="OPEN")

        # Use API response status to determine immediate fills
        pending = []
        for r in results:
            if not r.get("submitted"):
                continue
            _bump_fill(state, "submitted")
            status = r.get("status", "")
            if status == "matched":
                _fill_sz = r.get("size_matched", r["size"])
                apply_fill(ms, r["outcome"], "BUY", r["price"], _fill_sz)
                _bump_fill(state, "filled")
                logger.info("INSTANT FILL %s %s: %.1f @ $%.3f",
                            cid[:8], r["outcome"], _fill_sz, r["price"])
            else:
                pending.append(r)

        mkt_dict = _to_dict(ms)
        mkt_dict["pending_orders"] = pending
        mkt_dict["fills_confirmed"] = len(pending) == 0
        mkt_dict["entry_price"] = _coin_price
        mkt_dict["entry_ts"] = int(time.time())
        mkt_dict["tranches_done"] = 1
        mkt_dict["tranches_total"] = n_tranches
        # FOLLOW_LOG = log only, bridge direction kept. No flip.
        mkt_dict["original_dir"] = "UP" if fair > 0.50 else "DOWN"
        if _whale_action == "FOLLOW_LOG":
            mkt_dict["whale_disagree"] = True  # track for offline analysis
        mkt_dict["rounds"] = 0  # scalp round counter (0 = first entry, no sells yet)
        mkt_dict["phased_rungs"] = _cond_rungs_config  # rung 3-4 config for live placement
        if both_sides:
            mkt_dict["both_sides"] = True
            mkt_dict["bs_combined"] = _bs_combined
            mkt_dict["_w4_t2_pending"] = True  # T2 confirmation at T+480s
            mkt_dict["_w4_t1_dir"] = _lean_dir
            mkt_dict["_w4_t1_ratio"] = _dyn_ratio  # carry T1 ratio to T2
            mkt_dict["_w4_full_budget"] = round(_full_budget, 4)
        if _observe_only:
            mkt_dict["paper"] = True  # paper trade — no real money, don't affect bankroll/risk
        state["markets"][cid] = mkt_dict
        del state["watchlist"][cid]
        active += 1

        t_str = f" T1/{n_tranches}" if n_tranches > 1 else ""
        filled_str = f"UP={ms.up_shares:.0f} DN={ms.down_shares:.0f}"
        pending_str = ",".join(r["outcome"] for r in pending)
        cost_str = f"${ms.entry_cost:.2f}" if ms.entry_cost > 0 else "$0"
        print(f"  OPEN {cid[:8]} | {filled_str} | pend: {pending_str or '-'} | {cost_str}{t_str}")

    # ── W4 Tranche 2: confirmation entry at T+480s ──
    # Data: T+480s confirms T+300s direction → 86.1% WR (vs 76.1% base)
    #        T+480s flips → 21.5% WR. Skip saves ~24% of losing trades.
    if both_sides and is_heavy:
        for cid, mkt_d in list(state["markets"].items()):
            if not mkt_d.get("_w4_t2_pending"):
                continue
            if mkt_d["phase"] != "OPEN":
                mkt_d["_w4_t2_pending"] = False
                continue

            # 🔴 2CHECK FIX: Don't fire T2 if T1 had zero fills AND zero pending
            # (all T1 orders were cancelled = adverse market, don't re-enter)
            _t1_has_position = (mkt_d.get("up_shares", 0) > 0 or mkt_d.get("down_shares", 0) > 0
                                or mkt_d.get("pending_orders", []))
            if not _t1_has_position:
                logger.info("W4 T2 SKIP %s: T1 has zero position (all cancelled)", cid[:8])
                mkt_d["_w4_t2_pending"] = False
                continue

            # 🟡 BMD FIX: Cross-check tranches_done to prevent duplicate T2
            if mkt_d.get("tranches_done", 1) >= 2:
                mkt_d["_w4_t2_pending"] = False
                continue

            start_ms = mkt_d.get("window_start_ms", 0)
            end_ms = mkt_d.get("window_end_ms", 0)
            if start_ms <= 0:
                mkt_d["_w4_t2_pending"] = False
                continue
            _t2_elapsed_s = (now_ms - start_ms) / 1000
            if _t2_elapsed_s < _W4_T2_DELAY_S:
                continue  # not yet T+480s, keep waiting

            # Too close to window end (< 3 min) → skip T2
            if end_ms > 0 and now_ms > end_ms - 180_000:
                logger.info("W4 T2 SKIP %s: too close to window end (%.0fs left)",
                            cid[:8], (end_ms - now_ms) / 1000)
                mkt_d["_w4_t2_pending"] = False
                continue

            # Detect coin symbol
            _t2_title = mkt_d.get("title", "").lower()
            if "ethereum" in _t2_title:
                _t2_sym = "ETHUSDT"
            elif "solana" in _t2_title:
                _t2_sym = "SOLUSDT"
            else:
                _t2_sym = "BTCUSDT"

            # Re-check W4 signal at current time
            _t2_dir, _t2_mag, _t2_ret = _w4_signal(start_ms, _t2_sym)
            _t1_dir = mkt_d.get("_w4_t1_dir", "")

            # T2 confirmation: direction must match T1 AND magnitude still above threshold
            if _t2_dir == _t1_dir and _t2_mag >= _W4_THRESHOLD_BPS:
                # ⚠️ DZ-7: T2 has no budget cap (T1 does). Oversizing risk. See docs/DANGER_ZONES.md
                # CONFIRMED — place T2 orders (40% of full budget)
                _t2_budget = mkt_d.get("_w4_full_budget", 0) * _W4_T2_PCT
                if _t2_budget < 1.0:
                    logger.info("W4 T2 SKIP %s: budget $%.2f too small", cid[:8], _t2_budget)
                    mkt_d["_w4_t2_pending"] = False
                    continue

                # Fetch OB mid for pricing (same as T1 logic)
                _t2_up_mid, _t2_dn_mid = 0.0, 0.0
                _t2_up_tok = mkt_d.get("up_token_id", "")
                _t2_dn_tok = mkt_d.get("down_token_id", "")
                if client and hasattr(client, "get_order_book") and not dry_run:
                    try:
                        _t2_ub = client.get_order_book(_t2_up_tok)
                        _t2_ubids = _t2_ub.get("bids", [])
                        _t2_uasks = _t2_ub.get("asks", [])
                        if _t2_ubids and _t2_uasks:
                            _t2_up_mid = (max(b["price"] for b in _t2_ubids) + min(a["price"] for a in _t2_uasks)) / 2
                        _t2_db = client.get_order_book(_t2_dn_tok)
                        _t2_dbids = _t2_db.get("bids", [])
                        _t2_dasks = _t2_db.get("asks", [])
                        if _t2_dbids and _t2_dasks:
                            _t2_dn_mid = (max(b["price"] for b in _t2_dbids) + min(a["price"] for a in _t2_dasks)) / 2
                    except Exception as e:
                        logger.debug("W4 T2 OB fetch failed: %s", e)

                # Fallback if OB unavailable
                if _t2_up_mid <= 0.01 or _t2_up_mid >= 0.99:
                    _t2_up_mid = 0.50
                if _t2_dn_mid <= 0.01 or _t2_dn_mid >= 0.99:
                    _t2_dn_mid = 0.50

                _t2_up_bid = round(max(0.02, _t2_up_mid - 0.01), 2)
                _t2_dn_bid = round(max(0.02, _t2_dn_mid - 0.01), 2)
                _t2_combined = round(_t2_up_bid + _t2_dn_bid, 4)

                if _t2_combined >= 0.99:
                    logger.info("W4 T2 SKIP %s: combined $%.4f >= $0.99 (arb spread too thin)",
                                cid[:8], _t2_combined)
                    mkt_d["_w4_t2_pending"] = False
                    continue

                # Calculate T2 shares (same lean ratio as T1 — stored in market state)
                _t2_ratio = mkt_d.get("_w4_t1_ratio", _W4_LEAN_RATIO)  # fallback to constant
                _t2_avg_price = (_t2_up_bid + _t2_dn_bid) / 2
                _t2_total_shares = max(6, _t2_budget / _t2_avg_price)
                _t2_lean_frac = _t2_ratio / (_t2_ratio + 1)
                _t2_hedge_frac = 1.0 / (_t2_ratio + 1)

                if _t1_dir == "UP":
                    _t2_up_sh = max(config.min_order_size, round(_t2_total_shares * _t2_lean_frac, 1))
                    _t2_dn_sh = max(config.min_order_size, round(_t2_total_shares * _t2_hedge_frac, 1))
                else:
                    _t2_up_sh = max(config.min_order_size, round(_t2_total_shares * _t2_hedge_frac, 1))
                    _t2_dn_sh = max(config.min_order_size, round(_t2_total_shares * _t2_lean_frac, 1))

                _t2_orders = [
                    PlannedOrder(token_id=_t2_up_tok, side="BUY",
                                 price=_t2_up_bid, size=_t2_up_sh, outcome="UP"),
                    PlannedOrder(token_id=_t2_dn_tok, side="BUY",
                                 price=_t2_dn_bid, size=_t2_dn_sh, outcome="DOWN"),
                ]

                # Execute T2 orders (respect live gate)
                _t2_observe = mkt_d.get("paper", False) or not state.get("_w4_live")
                if _t2_observe:
                    class _T2Paper:
                        def buy_shares(self, tid, amt, price=0):
                            return {"orderID": f"paper_t2_{tid[:8]}_{int(time.time())}",
                                    "status": "matched", "dry_run": True}
                    _t2_results = _execute(_t2_orders, _T2Paper(), cid=cid)
                else:
                    _t2_results = _execute(_t2_orders, client, cid=cid)

                # Merge T2 results into existing market state
                for r in _t2_results:
                    if r.get("submitted"):
                        _bump_fill(state, "submitted")
                        if r.get("status") == "matched":
                            _bump_fill(state, "filled")
                            # 🔴 2CHECK FIX: Track T2 instant fills in position state
                            _t2_out = r["outcome"]
                            _t2_px = r["price"]
                            _t2_sz = r.get("size_matched", r["size"])
                            if _t2_out == "UP":
                                _old_val = mkt_d.get("up_shares", 0) * mkt_d.get("up_avg_price", 0)
                                mkt_d["up_shares"] = mkt_d.get("up_shares", 0) + _t2_sz
                                mkt_d["up_avg_price"] = (_old_val + _t2_sz * _t2_px) / mkt_d["up_shares"] if mkt_d["up_shares"] > 0 else 0
                            elif _t2_out == "DOWN":
                                _old_val = mkt_d.get("down_shares", 0) * mkt_d.get("down_avg_price", 0)
                                mkt_d["down_shares"] = mkt_d.get("down_shares", 0) + _t2_sz
                                mkt_d["down_avg_price"] = (_old_val + _t2_sz * _t2_px) / mkt_d["down_shares"] if mkt_d["down_shares"] > 0 else 0
                            mkt_d["entry_cost"] = mkt_d.get("entry_cost", 0) + _t2_sz * _t2_px
                            logger.info("W4 T2 INSTANT FILL %s %s: %.1f @ $%.3f",
                                        cid[:8], _t2_out, _t2_sz, _t2_px)
                        else:
                            mkt_d.setdefault("pending_orders", []).append(r)

                # Log T2
                try:
                    _t2_entry = {
                        "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_t2_entry",
                        "cid": cid[:8], "lean_dir": _t1_dir,
                        "t2_dir": _t2_dir, "t2_mag_bps": round(_t2_mag, 1),
                        "up_bid": _t2_up_bid, "dn_bid": _t2_dn_bid,
                        "combined": _t2_combined,
                        "up_shares": _t2_up_sh, "dn_shares": _t2_dn_sh,
                        "budget": round(_t2_budget, 2), "live": not _t2_observe,
                    }
                    with open(_BOTHSIDES_LOG, "a") as _bsf:
                        _bsf.write(json.dumps(_t2_entry) + "\n")
                except Exception:
                    pass

                logger.info("W4 T2 %s %s: CONFIRMED %s %+.1fbps | UP@$%.2f×%.0f + DN@$%.2f×%.0f = $%.3f",
                            "LIVE" if not _t2_observe else "PAPER", cid[:8],
                            _t1_dir, _t2_mag,
                            _t2_up_bid, _t2_up_sh, _t2_dn_bid, _t2_dn_sh, _t2_combined)
                mkt_d["_w4_t2_pending"] = False
                mkt_d["tranches_done"] = 2

            else:
                # FLIPPED or below threshold → skip T2
                logger.info("W4 T2 SKIP %s: T1=%s T2=%s mag=%.1fbps (need %s >=%dbps)",
                            cid[:8], _t1_dir, _t2_dir, _t2_mag,
                            _t1_dir, _W4_THRESHOLD_BPS)
                try:
                    _t2_skip = {
                        "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_t2_skip",
                        "cid": cid[:8], "t1_dir": _t1_dir,
                        "t2_dir": _t2_dir, "t2_mag_bps": round(_t2_mag, 1),
                        "reason": "flip" if _t2_dir != _t1_dir else "below_threshold",
                    }
                    with open(_BOTHSIDES_LOG, "a") as _bsf:
                        _bsf.write(json.dumps(_t2_skip) + "\n")
                except Exception:
                    pass
                mkt_d["_w4_t2_pending"] = False

    # ── Helper: identify directional vs hedge orders ──
    def _find_directional_orders(pending_list):
        """Find orders that are directional (not part of equal-size hedge pair).
        Hedge pair = one UP + one DN with same size. Extra orders = directional."""
        up_orders = [p for p in pending_list if p["outcome"] == "UP"]
        dn_orders = [p for p in pending_list if p["outcome"] == "DOWN"]
        # Find hedge pairs (matching size)
        hedge_up_ids = set()
        hedge_dn_ids = set()
        for u in up_orders:
            for d in dn_orders:
                if abs(u["size"] - d["size"]) < 0.1 and id(u) not in hedge_up_ids and id(d) not in hedge_dn_ids:
                    hedge_up_ids.add(id(u))
                    hedge_dn_ids.add(id(d))
                    break
        # Everything not in a hedge pair = directional
        return [p for p in pending_list
                if id(p) not in hedge_up_ids and id(p) not in hedge_dn_ids]

    # ── Phased rung placement: rungs 3-4 placed LIVE with 3-cycle cooldown ──
    # When mid approaches rung price: wait 3 cycles (15s), observe market reaction,
    # then run 3 checks. If ALL pass → place. If ANY fail → block permanently.
    _PHASED_COOLDOWN_CYCLES = 3  # observe for 15s (3 × 5s) before deciding
    if client and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            _phased = mkt.get("phased_rungs", [])
            if not _phased:
                continue
            # No _has_fills gate: allow rung 3-4 even if 1-2 missed
            # (price may skip through 0.43/0.37 on flash drop → 0.31 is still valid entry)
            # 3-cycle cooldown + checkpoint provides sufficient safety
            _end_ms = mkt.get("window_end_ms", 0)
            if _end_ms > 0 and now_ms > _end_ms - 120_000:
                continue

            _t = mkt.get("title", "").lower()
            _sym_pr = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"

            for _pr in _phased:
                if _pr.get("placed") or _pr.get("blocked"):
                    continue
                _tok_id = _pr["token_id"]
                _mid = _poly_midpoint(client, _tok_id) if hasattr(client, "get_midpoint") else 0
                if _mid <= 0 or _mid > _pr["price"] + 0.05:
                    _pr.pop("_approach_count", None)  # reset if mid moved away
                    continue

                # ── Cooldown: count cycles since mid approached ──
                _pr["_approach_count"] = _pr.get("_approach_count", 0) + 1
                if _pr["_approach_count"] < _PHASED_COOLDOWN_CYCLES:
                    if _pr["_approach_count"] == 1:
                        logger.info("PHASED OBSERVE %s $%.2f: mid=%.3f approaching, waiting %d cycles...",
                                    cid[:8], _pr["price"], _mid, _PHASED_COOLDOWN_CYCLES)
                    continue  # keep observing

                # ═══ LIVE CHECKPOINT (after cooldown) ═══
                _pass = True
                _reasons = []
                _our_dir = mkt.get("original_dir", "UP")

                # Check 1: Whale not against us
                _h_imb, _ = _holder_imbalance(cid, mkt.get("up_token_id", ""), ttl_override=5)
                _whale_up = _h_imb > 0
                _whale_against = (_our_dir == "UP" and not _whale_up and abs(_h_imb) > 0.20) or \
                                 (_our_dir == "DOWN" and _whale_up and abs(_h_imb) > 0.20)
                if _whale_against:
                    _pass = False
                    _reasons.append(f"whale({_h_imb:+.2f})")

                # Check 2: BTC adverse move < 0.3% since entry
                _entry_px = mkt.get("entry_price", 0)
                _now_px = _price(_sym_pr)
                if _entry_px > 0 and _now_px > 0:
                    _move = (_now_px - _entry_px) / _entry_px
                    _is_adverse = (_our_dir == "UP" and _move < -0.003) or \
                                  (_our_dir == "DOWN" and _move > 0.003)
                    if _is_adverse:
                        _pass = False
                        _reasons.append(f"adverse({_move:+.3%})")

                # Check 3: Mid recovering? (mid > rung price = market bouncing back)
                if _mid <= _pr["price"]:
                    _pass = False
                    _reasons.append(f"mid({_mid:.3f})<rung({_pr['price']:.2f})")

                if _pass:
                    try:
                        _results = _execute(
                            [PlannedOrder(token_id=_tok_id, side="BUY",
                                          price=_pr["price"], size=_pr["size"],
                                          outcome=_pr["outcome"])],
                            client, cid=cid)
                        _pr["placed"] = True
                        # Add to pending_orders for cancel defense visibility
                        for _r in (_results or []):
                            if _r.get("submitted"):
                                mkt.setdefault("pending_orders", []).append({
                                    "order_id": _r.get("order_id", ""),
                                    "outcome": _pr["outcome"],
                                    "price": _pr["price"], "size": _pr["size"],
                                })
                        logger.info("PHASED RUNG %s: $%.2f × %.1f placed (mid=%.3f, 3 checks passed after %d cycles)",
                                    cid[:8], _pr["price"], _pr["size"], _mid, _pr["_approach_count"])
                    except Exception as e:
                        logger.warning("Phased rung failed %s: %s", cid[:8], e)
                else:
                    _pr["blocked"] = True
                    logger.info("PHASED BLOCKED %s $%.2f: %s (after %d cycles observation)",
                                cid[:8], _pr["price"], " + ".join(_reasons), _pr["_approach_count"])
                break

    # ⚠️ DZ-1: Cancel race condition — cancel(matched_order) = no-op, causes double exposure. WS pre-check mandatory. See docs/DANGER_ZONES.md
    # Cancel defense: 3 triggers for unfilled orders
    if client and hasattr(client, "client") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            # 🔴 2CHECK FIX #3: Both-sides markets — only cancel at window-end, skip adverse/TTL
            # W4 wants BOTH sides to fill; cancelling on adverse move defeats the hedge
            _is_bs = mkt.get("both_sides", False)
            pending = mkt.get("pending_orders", [])
            if not pending:
                continue

            end_ms = mkt.get("window_end_ms", 0)
            entry_price = mkt.get("entry_price", 0)
            entry_ts = mkt.get("entry_ts", 0)
            now_s = int(time.time())

            # Detect coin symbol for spot price check
            _t = mkt.get("title", "").lower()
            _s = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"

            to_cancel = []
            reason = ""

            # Trigger 1: 2 min before window end → cancel ALL pending (except endgame/hedge)
            if end_ms > 0 and now_ms > end_ms - 120_000:
                to_cancel = [p for p in pending if not p.get("endgame") and not p.get("hedge")]
                reason = "window_end"

            # Trigger 2: spot moved ADVERSELY >0.5% since entry → cancel DIRECTIONAL
            # Skip for both-sides (2check fix #3: adverse cancel kills hedge purpose)
            _spot_thresh = 0.007 if _s == "ETHUSDT" else 0.005
            if not to_cancel and entry_price > 0 and not _is_bs:
                current = _btc_price() if _s == "BTCUSDT" else _price(_s)
                if current > 0:
                    signed_move = (current - entry_price) / entry_price
                    _dir = mkt.get("original_dir", "UP")
                    # Adverse = price went opposite to our bet direction
                    is_adverse = (signed_move < 0 and _dir == "UP") or (signed_move > 0 and _dir == "DOWN")
                    if is_adverse and abs(signed_move) > _spot_thresh:
                        to_cancel = [o for o in _find_directional_orders(pending)
                                     if not o.get("endgame") and not o.get("hedge")]
                        if to_cancel:
                            reason = f"adverse_move_{signed_move:+.4f}"

            # Trigger 3: Dynamic TTL → cancel DIRECTIONAL only (skip both-sides + endgame/hedge)
            if not to_cancel and entry_ts > 0 and end_ms > 0 and not _is_bs:
                _hard_cancel_s = (end_ms - 120_000) / 1000
                _max_ttl_s = max(60, _hard_cancel_s - entry_ts)
                _time_on_book = now_s - entry_ts
                if _time_on_book > 600 and not mkt.get("_ttl_extended_logged"):
                    logger.info("TTL_EXTENDED %s: on book %ds (old cap would cancel at 600s, now max=%ds)",
                                cid[:8], int(_time_on_book), int(_max_ttl_s))
                    mkt["_ttl_extended_logged"] = True
                if _time_on_book > _max_ttl_s:
                    to_cancel = [o for o in _find_directional_orders(pending)
                                 if not o.get("endgame") and not o.get("hedge")]
                    if to_cancel:
                        reason = f"ttl_{int(_time_on_book)}s_max{int(_max_ttl_s)}s"

            # Trigger 4: Lean-unfilled protection (both-sides only)
            # Stage 1: preemptive — if lean & hedge both unfilled after 20s → cancel hedge (free)
            # Stage 2: reactive  — if lean unfilled after 90s → cancel lean+orphan hedge, block T2
            if not to_cancel and _is_bs and entry_ts > 0:
                _lean_age = now_s - entry_ts
                _lean_dir = mkt.get("_w4_t1_dir", "")
                if _lean_dir:
                    # 🔴 BMD FIX: WS pre-check to prevent stale-state cancel.
                    # _check_fills runs AFTER cancel defense, so shares may be stale.
                    # Quick WS check: if lean order actually matched, skip Door B.
                    _lean_ws_filled = False
                    if _ws_user and _ws_user.connected:
                        for _db_po in pending:
                            _db_oid = _db_po.get("order_id", "")
                            if (_db_oid and _db_po.get("outcome", "").upper() == _lean_dir
                                    and _ws_user.get_order_status(_db_oid) == "MATCHED"):
                                _lean_ws_filled = True
                                break

                    _lean_key = "up_shares" if _lean_dir == "UP" else "down_shares"
                    _hedge_key = "down_shares" if _lean_dir == "UP" else "up_shares"
                    _lean_sh = mkt.get(_lean_key, 0)
                    _hedge_sh = mkt.get(_hedge_key, 0)
                    _lean_pending = [p for p in pending if p.get("outcome", "").upper() == _lean_dir]
                    _hedge_pending = [p for p in pending if p.get("outcome", "").upper() != _lean_dir]

                    # Skip Door B if lean actually filled (WS says MATCHED but shares not yet updated)
                    if not _lean_ws_filled and _lean_sh == 0:
                        # Stage 1: preemptive hedge cancel (both sides still pending)
                        if (_lean_age >= _LEAN_PREEMPTIVE_S
                                and _hedge_sh == 0 and _hedge_pending and _lean_pending):
                            to_cancel = _hedge_pending
                            reason = f"lean_unfilled_preemptive_{int(_lean_age)}s"
                            logger.info("DOOR-B STAGE1 %s: lean=%s unfilled@%ds → cancel hedge (free)",
                                        cid[:8], _lean_dir, int(_lean_age))
                            try:
                                with open(_BOTHSIDES_LOG, "a") as _bsf:
                                    _bsf.write(json.dumps({"ts": _ts_hkt(), "event": "door_b_stage1",
                                        "cid": cid[:8], "lean_dir": _lean_dir,
                                        "age_s": int(_lean_age)}) + "\n")
                            except Exception:
                                pass

                        # Stage 2: lean still unfilled after 90s → cancel lean + orphan hedge
                        elif _lean_age >= _LEAN_UNFILLED_TIMEOUT_S and _lean_pending:
                            to_cancel = list(_lean_pending)  # cancel stale lean orders
                            # 🟡 2CHECK FIX: also cancel orphan hedge pending if hedge unfilled
                            if _hedge_sh == 0 and _hedge_pending:
                                to_cancel.extend(_hedge_pending)
                            reason = f"lean_unfilled_{int(_lean_age)}s"
                            mkt["_w4_t2_pending"] = False  # block T2
                            if _hedge_sh > 0:
                                logger.warning("DOOR-B STAGE2 %s: lean=%s unfilled@%ds, "
                                               "hedge=%.1f shares → cancel lean, block T2, naked hedge",
                                               cid[:8], _lean_dir, int(_lean_age), _hedge_sh)
                            else:
                                logger.info("DOOR-B STAGE2 %s: lean=%s unfilled@%ds, "
                                            "no hedge → cancel all, block T2",
                                            cid[:8], _lean_dir, int(_lean_age))
                            try:
                                with open(_BOTHSIDES_LOG, "a") as _bsf:
                                    _bsf.write(json.dumps({"ts": _ts_hkt(), "event": "door_b_stage2",
                                        "cid": cid[:8], "lean_dir": _lean_dir,
                                        "age_s": int(_lean_age),
                                        "hedge_shares": round(_hedge_sh, 1),
                                        "naked_hedge": _hedge_sh > 0}) + "\n")
                            except Exception:
                                pass

            actually_cancelled = []
            _phantom_fills = []
            _time_on_book = now_s - entry_ts if entry_ts > 0 else 0
            _dist_to_end_s = (end_ms / 1000 - now_s) if end_ms > 0 else 0
            for po in to_cancel:
                oid = po.get("order_id", "")
                if oid:
                    # ── Race condition guard: check if order already filled ──
                    # If matched on-chain, cancel is no-op. We must account
                    # for the fill instead of silently dropping it.
                    if _ws_user and _ws_user.connected:
                        _ws_st = _ws_user.get_order_status(oid)
                        if _ws_st == "MATCHED":
                            _fill_size = po["size"]
                            _ws_det = _ws_user.get_order_detail(oid)
                            if _ws_det and _ws_det.get("size_matched", 0) > 0:
                                _fill_size = _ws_det["size_matched"]
                            _fill_price = po.get("price", 0)
                            outcome = po["outcome"]
                            if outcome == "UP":
                                old_val = mkt["up_shares"] * mkt["up_avg_price"]
                                mkt["up_shares"] += _fill_size
                                mkt["up_avg_price"] = (
                                    (old_val + _fill_size * _fill_price) / mkt["up_shares"]
                                )
                            elif outcome == "DOWN":
                                old_val = mkt["down_shares"] * mkt["down_avg_price"]
                                mkt["down_shares"] += _fill_size
                                mkt["down_avg_price"] = (
                                    (old_val + _fill_size * _fill_price) / mkt["down_shares"]
                                )
                            mkt["entry_cost"] += _fill_size * _fill_price
                            _bump_fill(state, "filled")
                            logger.warning(
                                "CANCEL ABORT %s %s [%s]: order ALREADY MATCHED "
                                "(%.1f @ $%.3f) — phantom fill recovered",
                                cid[:8], po["outcome"], reason, _fill_size, _fill_price)
                            _phantom_fills.append(po)
                            continue

                    try:
                        _cancel_t0 = time.time()
                        client.client.cancel(order_id=oid)
                        _cancel_rtt_ms = round((time.time() - _cancel_t0) * 1000, 1)
                        logger.info("CANCEL %s %s [%s] book=%ds end=%ds rtt=%dms",
                                    cid[:8], po["outcome"], reason,
                                    _time_on_book, _dist_to_end_s, _cancel_rtt_ms)
                        _log_order("cancel", oid, cid,
                                   outcome=po.get("outcome", ""), reason=reason,
                                   time_on_book_s=_time_on_book,
                                   dist_to_end_s=int(_dist_to_end_s),
                                   cancel_rtt_ms=_cancel_rtt_ms)
                        actually_cancelled.append(po)
                    except Exception as e:
                        logger.warning("Cancel FAILED %s %s: %s — keeping in pending",
                                       cid[:8], po["outcome"], e)

            # Only remove successfully cancelled orders from pending
            # Phantom fills are also removed from pending (already accounted for above)
            _removed = actually_cancelled + _phantom_fills
            if _removed:
                if actually_cancelled:
                    _bump_fill(state, "cancelled", len(actually_cancelled))
                mkt["pending_orders"] = [p for p in pending if p not in _removed]
                if not mkt["pending_orders"]:
                    mkt["fills_confirmed"] = True

    # ⚠️ DZ-1: Reprice cancel race — same phantom fill risk as cancel defense. WS pre-check mandatory. See docs/DANGER_ZONES.md
    # ── W4 Order Repricing: improve stale limit orders when OB drops ──
    # 🔴 BMD FIX: Only reprice DOWNWARD (OB mid dropped → we get a better price).
    # Do NOT chase rising OB mid — W4 places at mid-1¢ and waits. Patience is the edge.
    # 🔴 2CHECK FIX: Split cancel+buy into separate try blocks to prevent ghost orders.
    if both_sides and client and hasattr(client, "client") and not dry_run and is_heavy:
        _reprice_now = time.time()
        for cid, mkt in state["markets"].items():
            if not mkt.get("both_sides") or mkt["phase"] != "OPEN":
                continue
            pending = mkt.get("pending_orders", [])
            if not pending:
                continue
            # Per-market cooldown
            if _reprice_now - mkt.get("_last_reprice_ts", 0) < _REPRICE_COOLDOWN_S:
                continue
            # Don't reprice if window ending soon (< 3 min)
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 180_000:
                continue

            _repriced_any = False
            _new_pending = []
            for po in pending:
                # Skip if already repriced too many times
                if po.get("_reprice_count", 0) >= _REPRICE_MAX_PER_ORDER:
                    _new_pending.append(po)
                    continue
                _tok = po.get("token_id", "")
                _oid = po.get("order_id", "")
                if not _tok or not _oid:
                    _new_pending.append(po)
                    continue

                # Fetch current OB mid
                try:
                    _ob = client.get_order_book(_tok)
                    _bids = _ob.get("bids", [])
                    _asks = _ob.get("asks", [])
                    if not _bids or not _asks:
                        _new_pending.append(po)
                        continue
                    _cur_mid = (max(b["price"] for b in _bids) + min(a["price"] for a in _asks)) / 2
                except Exception:
                    _new_pending.append(po)
                    continue

                _old_price = po.get("price", 0)
                _new_bid = round(max(0.02, _cur_mid - 0.01), 2)

                # 🔴 BMD: Only reprice if new bid is LOWER (better price for us).
                # If OB mid rose, our old bid is already good — just wait for fill.
                if _new_bid >= _old_price:
                    _new_pending.append(po)
                    continue

                # Check threshold: only reprice if improvement > 2¢
                if _old_price - _new_bid < _REPRICE_THRESHOLD:
                    _new_pending.append(po)
                    continue

                # ── Step 0: Check if old order already filled (race condition guard) ──
                # If the order was matched on-chain before we cancel, cancel is a
                # no-op but the fill is real. We MUST detect this BEFORE placing
                # a replacement, otherwise we double our exposure.
                # Bug discovered 2026-03-24: caused 25 UP / 10 DOWN when bot
                # only tracked 10/5 → -$9.38 real loss vs +$0.55 tracked.
                _already_filled = False
                if _ws_user and _ws_user.connected:
                    _pre_status = _ws_user.get_order_status(_oid)
                    if _pre_status == "MATCHED":
                        _already_filled = True
                        # Account for the phantom fill
                        _fill_size = po["size"]
                        _ws_det = _ws_user.get_order_detail(_oid)
                        if _ws_det and _ws_det.get("size_matched", 0) > 0:
                            _fill_size = _ws_det["size_matched"]
                        outcome = po["outcome"]
                        if outcome == "UP":
                            old_val = mkt["up_shares"] * mkt["up_avg_price"]
                            mkt["up_shares"] += _fill_size
                            mkt["up_avg_price"] = (old_val + _fill_size * _old_price) / mkt["up_shares"]
                        elif outcome == "DOWN":
                            old_val = mkt["down_shares"] * mkt["down_avg_price"]
                            mkt["down_shares"] += _fill_size
                            mkt["down_avg_price"] = (old_val + _fill_size * _old_price) / mkt["down_shares"]
                        mkt["entry_cost"] += _fill_size * _old_price
                        _bump_fill(state, "filled")
                        logger.warning(
                            "W4 REPRICE ABORT %s %s: old order ALREADY MATCHED "
                            "(%.1f @ $%.3f) — phantom fill recovered, NO replacement placed",
                            cid[:8], po["outcome"], _fill_size, _old_price)
                        _repriced_any = True
                        continue  # do NOT place replacement order

                if _already_filled:
                    continue

                # Step 1: Cancel old order (separate try block)
                _cancel_ok = False
                try:
                    client.client.cancel(order_id=_oid)
                    _cancel_ok = True
                except Exception as e:
                    logger.warning("W4 REPRICE CANCEL FAILED %s %s: %s", cid[:8], po["outcome"], e)
                    _new_pending.append(po)  # keep original order
                    continue

                # Step 1.5: Post-cancel verification — did order fill during cancel RTT?
                # Small window but real: order could match between our check and cancel.
                if _ws_user and _ws_user.connected:
                    time.sleep(0.05)  # 50ms for WS to propagate match event
                    _post_status = _ws_user.get_order_status(_oid)
                    if _post_status == "MATCHED":
                        # Order filled DURING our cancel call — cancel was no-op
                        _fill_size = po["size"]
                        _ws_det = _ws_user.get_order_detail(_oid)
                        if _ws_det and _ws_det.get("size_matched", 0) > 0:
                            _fill_size = _ws_det["size_matched"]
                        outcome = po["outcome"]
                        if outcome == "UP":
                            old_val = mkt["up_shares"] * mkt["up_avg_price"]
                            mkt["up_shares"] += _fill_size
                            mkt["up_avg_price"] = (old_val + _fill_size * _old_price) / mkt["up_shares"]
                        elif outcome == "DOWN":
                            old_val = mkt["down_shares"] * mkt["down_avg_price"]
                            mkt["down_shares"] += _fill_size
                            mkt["down_avg_price"] = (old_val + _fill_size * _old_price) / mkt["down_shares"]
                        mkt["entry_cost"] += _fill_size * _old_price
                        _bump_fill(state, "filled")
                        logger.warning(
                            "W4 REPRICE ABORT (post-cancel) %s %s: order MATCHED during cancel RTT "
                            "(%.1f @ $%.3f) — phantom fill recovered, NO replacement placed",
                            cid[:8], po["outcome"], _fill_size, _old_price)
                        _repriced_any = True
                        continue  # do NOT place replacement

                # Step 2: Place replacement (only if cancel verified clean)
                try:
                    _amount = round(po["size"] * _new_bid, 2)
                    _r = client.buy_shares(_tok, _amount, price=_new_bid)
                    _new_oid = ""
                    _new_status = ""
                    if isinstance(_r, dict):
                        _new_oid = _r.get("orderID", _r.get("id", ""))
                        _new_status = _r.get("status", "")

                    if _new_status == "matched":
                        _bump_fill(state, "filled")
                        logger.info("W4 REPRICE+FILL %s %s: $%.2f → $%.2f (mid=%.3f)",
                                    cid[:8], po["outcome"], _old_price, _new_bid, _cur_mid)
                    else:
                        _new_po = dict(po)
                        _new_po["order_id"] = _new_oid
                        _new_po["price"] = _new_bid
                        _new_po["_reprice_count"] = po.get("_reprice_count", 0) + 1
                        _new_po["order_ts"] = time.time()
                        _new_pending.append(_new_po)
                        logger.info("W4 REPRICE %s %s: $%.2f → $%.2f (mid=%.3f, #%d)",
                                    cid[:8], po["outcome"], _old_price, _new_bid,
                                    _cur_mid, _new_po["_reprice_count"])
                    _repriced_any = True
                except Exception as e:
                    # Cancel succeeded but buy failed → order is GONE from CLOB
                    # Log as lost order, do NOT add stale order_id back
                    logger.error("W4 REPRICE BUY FAILED %s %s: cancel OK but buy failed: %s — order LOST",
                                 cid[:8], po["outcome"], e)
                    _repriced_any = True  # trigger pending update to remove old entry

            if _repriced_any:
                mkt["pending_orders"] = _new_pending
                mkt["_last_reprice_ts"] = _reprice_now
                if not _new_pending:
                    mkt["fills_confirmed"] = True

    # Check fills (submitted → actually filled?)
    if not dry_run:
        _check_fills(state, client)

    # ── v4: Runtime ratio cap — cancel excess lean if effective R > cap ──
    # T1+T2 can amplify ratio to 2.7-4.2x. One R=2.7 trade lost $10.44.
    for _rc_cid, _rc_mkt in list(state.get("markets", {}).items()):
        if not _rc_mkt.get("both_sides"):
            continue
        _rc_up = _rc_mkt.get("up_shares", 0)
        _rc_dn = _rc_mkt.get("down_shares", 0)
        if _rc_up > 0 and _rc_dn > 0:
            _rc_ratio = max(_rc_up, _rc_dn) / min(_rc_up, _rc_dn)
            if _rc_ratio > _W4_EFFECTIVE_R_CAP:
                _rc_excess = abs(_rc_up - _rc_dn)
                _rc_excess_side = "UP" if _rc_up > _rc_dn else "DOWN"
                # Cancel pending orders on the excess side
                _rc_pending = _rc_mkt.get("pending_orders", [])
                _rc_cancel = [p for p in _rc_pending
                              if p.get("outcome", "").upper() == _rc_excess_side]
                if _rc_cancel and client and hasattr(client, "client"):
                    for _rc_o in _rc_cancel:
                        _rc_oid = _rc_o.get("order_id", "")
                        if _rc_oid:
                            try:
                                client.client.cancel(_rc_oid)
                                logger.warning("RATIO CAP %s: R=%.1f > %.1f cap → cancelled %s %s",
                                               _rc_cid[:8], _rc_ratio, _W4_EFFECTIVE_R_CAP,
                                               _rc_excess_side, _rc_oid[:12])
                            except Exception:
                                pass
                else:
                    # Design flaw: ratio exceeded but all orders already filled — can't undo
                    logger.warning("RATIO CAP WARN %s: R=%.1f > %.1f cap, UP=%.0f DN=%.0f, "
                                   "but no pending to cancel (all filled)",
                                   _rc_cid[:8], _rc_ratio, _W4_EFFECTIVE_R_CAP, _rc_up, _rc_dn)

    # ── Post-fill AS measurement: check midpoint 60s after fill ──
    if _post_fill_checks and client and hasattr(client, "get_midpoint"):
        _now = time.time()
        _remaining = []
        for _pf_time, _pf_oid, _pf_cid, _pf_tok in _post_fill_checks:
            if _now >= _pf_time:
                _pf_mid = _poly_midpoint(client, _pf_tok)
                _log_order("post_fill_60s", _pf_oid, _pf_cid,
                           mid_60s=round(_pf_mid, 4) if _pf_mid else 0)
            else:
                _remaining.append((_pf_time, _pf_oid, _pf_cid, _pf_tok))
        _post_fill_checks.clear()
        _post_fill_checks.extend(_remaining)

    # ── ENDGAME: 1-share bets in undecided markets (T-120s to T-30s) ──
    # ── Reversal Research: snapshot extreme markets every heavy cycle (dry-run data) ──
    # Records: time, mid, BTC price, gap to target, tte — for offline analysis
    # Goal: 50 samples to evaluate reversal strategy viability
    if is_heavy:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms <= 0:
                continue
            _rv_tte = (end_ms - now_ms) / 1000
            if not (0 < _rv_tte < _REVERSAL_TTE_START):
                continue
            _rv_up_tok = mkt.get("up_token_id", "")
            if not _rv_up_tok:
                continue
            _rv_mid = _poly_midpoint(client, _rv_up_tok) if client else 0
            if _rv_mid <= 0:
                continue
            _rv_cheap = min(_rv_mid, 1.0 - _rv_mid)
            if _rv_cheap > _REVERSAL_EXTREME_THRESH:
                continue  # not extreme enough
            # Get BTC price for gap calculation
            _rv_btc = 0
            if _ws_binance:
                _rv_btc = _ws_binance.get_price("BTCUSDT")
            _rv_open = mkt.get("entry_price", 0)  # BTC price at window open
            _rv_gap = _rv_btc - _rv_open if _rv_btc > 0 and _rv_open > 0 else 0
            # Log every 30s (use tte buckets to avoid flooding)
            _rv_bucket = int(_rv_tte / 30) * 30
            _rv_log_key = f"_rv_logged_{_rv_bucket}"
            if not mkt.get(_rv_log_key):
                mkt[_rv_log_key] = True
                try:
                    with open(_REVERSAL_LOG, "a") as _rvf:
                        _rvf.write(json.dumps({
                            "ts": datetime.now(tz=_HKT).isoformat(),
                            "cid": cid[:8],
                            "coin": "btc" if "bitcoin" in mkt.get("title", "").lower() else "other",
                            "tte_s": round(_rv_tte),
                            "up_mid": round(_rv_mid, 4),
                            "cheap_mid": round(_rv_cheap, 4),
                            "btc_price": round(_rv_btc, 2) if _rv_btc else 0,
                            "btc_gap": round(_rv_gap, 2) if _rv_gap else 0,
                            "window_open_px": round(_rv_open, 2) if _rv_open else 0,
                        }) + "\n")
                except Exception:
                    pass

        # ── Reversal Research (watchlist): scan markets we SKIPPED at entry ──
        # Watchlist items have different field names; no entry_price available.
        for _wl_key, wl in state.get("watchlist", {}).items():
            _wl_end = wl.get("end_ms", 0)
            if _wl_end <= 0:
                continue
            _wl_tte = (_wl_end - now_ms) / 1000
            if not (0 < _wl_tte < _REVERSAL_TTE_START):
                continue
            _wl_up_tok = wl.get("up_tok", "")
            if not _wl_up_tok:
                continue
            _wl_mid = _poly_midpoint(client, _wl_up_tok) if client else 0
            if _wl_mid <= 0:
                continue
            _wl_cheap = min(_wl_mid, 1.0 - _wl_mid)
            if _wl_cheap > _REVERSAL_EXTREME_THRESH:
                continue  # not extreme enough
            # BTC price (no entry_price in watchlist, so gap = 0)
            _wl_btc = 0
            if _ws_binance:
                _wl_btc = _ws_binance.get_price("BTCUSDT")
            # 30s bucket dedup (flag stored on wl dict)
            _wl_bucket = int(_wl_tte / 30) * 30
            _wl_log_key = f"_rv_logged_{_wl_bucket}"
            if not wl.get(_wl_log_key):
                wl[_wl_log_key] = True
                try:
                    with open(_REVERSAL_LOG, "a") as _rvf:
                        _rvf.write(json.dumps({
                            "ts": datetime.now(tz=_HKT).isoformat(),
                            "cid": _wl_key[:8],
                            "coin": "btc" if "bitcoin" in wl.get("title", "").lower() else "other",
                            "tte_s": round(_wl_tte),
                            "up_mid": round(_wl_mid, 4),
                            "cheap_mid": round(_wl_cheap, 4),
                            "btc_price": round(_wl_btc, 2) if _wl_btc else 0,
                            "btc_gap": 0,
                            "window_open_px": 0,
                            "source": "watchlist",
                        }) + "\n")
                except Exception:
                    pass

    # Three cases:
    #   1: Coin flip (mid 0.38-0.62) → bet bridge direction
    #   2: Reversal (20pt move in 30s toward center) → follow the move
    #   3: Decided / outside range → skip
    # Guard: one order per market (endgame_placed flag)
    if _ENDGAME_ENABLED and client and not dry_run:
        # Persist daily count in state (survives restart, unlike module global)
        _eg_today = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d")
        if state.get("_eg_daily_date") != _eg_today:
            state["_eg_daily_count"] = 0
            state["_eg_daily_date"] = _eg_today
        _eg_count = state.get("_eg_daily_count", 0)
        for cid, mkt in state["markets"].items():
            if _eg_count >= _ENDGAME_DAILY_CAP:
                break
            if mkt["phase"] != "OPEN":
                continue
            if mkt.get("endgame_placed"):
                continue
            if mkt.get("both_sides"):
                continue  # W4: already holds both sides, skip endgame
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms <= 0:
                continue
            tte_s = (end_ms - now_ms) / 1000
            if not (_ENDGAME_TTE_STOP < tte_s < _ENDGAME_TTE_START):
                continue

            # Execution gate: BTC only
            _t = mkt.get("title", "").lower()
            _eg_coin = "btc" if "bitcoin" in _t else ("eth" if "ethereum" in _t else "sol")
            if _eg_coin not in _LIVE_TRADE_COINS:
                continue

            # Get current mid (prefer WebSocket, fallback REST)
            up_tok = mkt.get("up_token_id", "")
            dn_tok = mkt.get("down_token_id", "")
            if not up_tok or not dn_tok:
                continue
            _eg_mid = _poly_midpoint(client, up_tok)
            if _eg_mid <= 0:
                continue

            # Track mid history for case 2 detection
            _buf = _endgame_mid_buf.setdefault(cid, deque(maxlen=60))
            _buf.append((time.time(), _eg_mid))

            # ── Classify case ──
            _eg_case = 0
            _eg_dir = "DOWN" if _eg_mid > 0.50 else "UP"  # default: buy cheap side (underdog)
            _mid_30s_ago = 0.0
            _delta_30s = 0.0

            if _ENDGAME_MID_RANGE[0] <= _eg_mid <= _ENDGAME_MID_RANGE[1]:
                # Case 1: Coin flip zone — buy the underdog (cheap side)
                if _ENDGAME_FLIP_RANGE[0] <= _eg_mid <= _ENDGAME_FLIP_RANGE[1]:
                    _eg_case = 1
                    # _eg_dir already set to cheap side above

                # Case 2: Reversal detection — 20pt move in ~30s toward 0.50
                _old_entries = [(t, m) for t, m in _buf if time.time() - t >= 25]
                if _old_entries:
                    _mid_30s_ago = _old_entries[-1][1]
                    _delta_30s = _eg_mid - _mid_30s_ago
                    _moved_toward_center = abs(_eg_mid - 0.5) < abs(_mid_30s_ago - 0.5)
                    if abs(_delta_30s) >= _ENDGAME_REVERSAL and _moved_toward_center:
                        _eg_case = 2
                        # Follow the reversal: if mid dropped (UP losing steam), bet DOWN
                        _eg_dir = "DOWN" if _delta_30s < 0 else "UP"

            if _eg_case == 0:
                continue  # Case 3 or outside range → skip

            # Token + aggressive taker price (mid + 2¢ to ensure fill)
            _eg_tok = up_tok if _eg_dir == "UP" else dn_tok
            _eg_our_mid = _eg_mid if _eg_dir == "UP" else (1.0 - _eg_mid)
            _eg_price = round(min(_eg_our_mid + 0.02, 0.95), 2)

            # Signal context for offline analysis
            _eg_ctx = {
                "endgame": True, "case": _eg_case,
                "mid": round(_eg_mid, 4), "mid_30s_ago": round(_mid_30s_ago, 4),
                "delta_30s": round(_delta_30s, 4), "direction": _eg_dir,
                "fair": round(_eg_mid if mkt.get("original_dir") == "UP" else 1.0 - _eg_mid, 4),
                "original_dir": mkt.get("original_dir", ""),
                "tte_s": int(tte_s), "coin": _eg_coin,
            }

            orders = [PlannedOrder(token_id=_eg_tok, side="BUY",
                                   price=_eg_price, size=_ENDGAME_SHARES,
                                   outcome=_eg_dir)]

            # Set guard BEFORE execute — crash between submit and save won't double-bet
            # (worst case: market skipped, not double-submitted — safe side of gotchas)
            mkt["endgame_placed"] = True
            mkt["endgame_dir"] = _eg_dir
            mkt["endgame_case"] = _eg_case

            results = _execute(orders, client, cid=cid, signal_ctx=_eg_ctx)

            for r in results:
                if not r.get("submitted"):
                    continue
                if r.get("status") == "matched":
                    # Instant fill — update shares + entry_cost
                    _sk = "up_shares" if _eg_dir == "UP" else "down_shares"
                    _ak = "up_avg_price" if _eg_dir == "UP" else "down_avg_price"
                    _fill_sz = r.get("size_matched", r["size"])
                    _old_s = mkt.get(_sk, 0)
                    _old_a = mkt.get(_ak, 0)
                    _new_s = _old_s + _fill_sz
                    mkt[_sk] = _new_s
                    mkt[_ak] = (_old_a * _old_s + r["price"] * _fill_sz) / _new_s if _new_s > 0 else r["price"]
                    mkt["entry_cost"] = mkt.get("entry_cost", 0) + _fill_sz * r["price"]
                    logger.info("ENDGAME FILL C%d %s %s mid=%.2f @$%.2f tte=%ds",
                                _eg_case, _eg_dir, cid[:8], _eg_mid, r["price"], int(tte_s))
                else:
                    # Pending — add to pending_orders with endgame tag
                    r["endgame"] = True
                    mkt.setdefault("pending_orders", []).append(r)

            _eg_count += 1
            state["_eg_daily_count"] = _eg_count
            logger.info("ENDGAME C%d %s %s mid=%.2f price=$%.2f tte=%ds [%d/%d today]",
                        _eg_case, _eg_dir, cid[:8], _eg_mid, _eg_price, int(tte_s),
                        _eg_count, _ENDGAME_DAILY_CAP)

    # ── LAST-MINUTE HEDGE: buy opposite if BTC 30s momentum against position ──
    # Structural fix: "signal wrong 34% → bot does nothing → full loss"
    # Now: if BTC moves $50+ against us in 30s near window end → buy opposite token as insurance
    # Guard: hedge_placed flag (one per market), BTC-only execution gate
    _btc_for_buf = _btc_price()
    if _btc_for_buf > 0:
        _btc_price_buf.append((time.time(), _btc_for_buf))

    if client and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            if mkt.get("hedge_placed"):
                continue
            if mkt.get("both_sides"):
                continue  # W4: already holds both sides, skip hedge
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms <= 0:
                continue
            tte_s = (end_ms - now_ms) / 1000
            if not (30 < tte_s < 120):
                continue

            # Only for positions with filled shares
            _our_dir = mkt.get("original_dir", "")
            if not _our_dir:
                continue
            _our_shares = mkt.get("up_shares", 0) if _our_dir == "UP" else mkt.get("down_shares", 0)
            if _our_shares < 1:
                continue

            # Execution gate: BTC only
            _t = mkt.get("title", "").lower()
            if "bitcoin" not in _t:
                continue

            # Check BTC 30s momentum
            _old_btc = [(t, p) for t, p in _btc_price_buf if time.time() - t >= 25]
            if not _old_btc:
                continue
            _btc_30s_ago = _old_btc[-1][1]
            _btc_now = _btc_price_buf[-1][1] if _btc_price_buf else 0
            if _btc_now <= 0:
                continue
            _btc_30s_move = _btc_now - _btc_30s_ago

            # Adverse = BTC moved against our direction
            _is_adverse = (_btc_30s_move < 0 and _our_dir == "UP") or (_btc_30s_move > 0 and _our_dir == "DOWN")
            if not (_is_adverse and abs(_btc_30s_move) >= _HEDGE_BTC_THRESHOLD):
                continue

            # Hedge: buy opposite token, 30% of position
            _opp_dir = "DOWN" if _our_dir == "UP" else "UP"
            _opp_tok = mkt.get("down_token_id", "") if _our_dir == "UP" else mkt.get("up_token_id", "")
            if not _opp_tok:
                continue
            _hedge_shares = max(1, int(_our_shares * _HEDGE_PCT))
            _opp_mid = _poly_midpoint(client, _opp_tok)
            if _opp_mid <= 0:
                # Fallback: use 1 - UP mid
                _up_mid = _poly_midpoint(client, mkt.get("up_token_id", ""))
                _opp_mid = 1.0 - _up_mid if _up_mid > 0 else 0.5
            _hedge_price = round(min(_opp_mid + 0.02, 0.95), 2)

            _hedge_ctx = {
                "hedge": True, "our_dir": _our_dir, "our_shares": _our_shares,
                "btc_30s_move": round(_btc_30s_move, 2), "tte_s": int(tte_s),
            }

            # Guard BEFORE execute (crash safety — same pattern as endgame)
            mkt["hedge_placed"] = True

            orders = [PlannedOrder(token_id=_opp_tok, side="BUY",
                                   price=_hedge_price, size=_hedge_shares,
                                   outcome=_opp_dir)]
            results = _execute(orders, client, cid=cid, signal_ctx=_hedge_ctx)

            for r in results:
                if not r.get("submitted"):
                    continue
                if r.get("status") == "matched":
                    _fill_sz = r.get("size_matched", r["size"])
                    _sk = "up_shares" if _opp_dir == "UP" else "down_shares"
                    _ak = "up_avg_price" if _opp_dir == "UP" else "down_avg_price"
                    _old_s = mkt.get(_sk, 0)
                    _new_s = _old_s + _fill_sz
                    mkt[_sk] = _new_s
                    mkt[_ak] = (mkt.get(_ak, 0) * _old_s + r["price"] * _fill_sz) / _new_s if _new_s > 0 else r["price"]
                    mkt["entry_cost"] = mkt.get("entry_cost", 0) + _fill_sz * r["price"]
                else:
                    r["hedge"] = True
                    mkt.setdefault("pending_orders", []).append(r)

            logger.info("HEDGE %s: %d shares %s @$%.2f (BTC moved $%.0f against %s, tte=%ds)",
                        cid[:8], _hedge_shares, _opp_dir, _hedge_price,
                        abs(_btc_30s_move), _our_dir, int(tte_s))

    # ── Exit: Profit Lock + Cost Recovery + Stop Loss ──
    # Layer 1: PROFIT LOCK (mid ≥ 96¢) → sell 96%, keep 4% free roll + 2-share hedge
    # Layer 2: COST RECOVERY (mid ≥ 64¢, early) → sell enough to recover cost → free roll
    # Layer 3: STOP LOSS (-25%, pre-recovery only) → cut losses
    # Layer 4: HOLD → default (free shares or waiting)
    _EXIT_STOP_PCT = 0.25       # -25% → stop loss (pre-recovery only)
    _BLACK_SWAN_MID = 0.96      # sell 96% at 96¢+ → lock profit, keep 4% free roll
    _BLACK_SWAN_SELL_PCT = 0.96 # sell 96%, keep 4% as free upside
    _COST_RECOVERY_MID = 0.64   # recover cost when mid ≥ 64¢ (keep 3 free shares vs 2 at 55¢)
    if client and hasattr(client, "sell_shares") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            if mkt.get("paper"):
                continue  # paper trades: hold to resolution, no real sells
            if mkt.get("both_sides"):
                continue  # W4: ZERO management, hold to resolution (BMD fix #5)
            _has_any_fill = (mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0)
            if not mkt.get("fills_confirmed") and not _has_any_fill:
                continue
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 300_000:
                continue  # last 5 min — self-imposed guard (market accepts orders until T+50s)

            _cost_recovered = mkt.get("cost_recovered", False)

            for side, tok_key, shares_key, avg_key in [
                ("UP", "up_token_id", "up_shares", "up_avg_price"),
                ("DOWN", "down_token_id", "down_shares", "down_avg_price"),
            ]:
                shares = mkt.get(shares_key, 0)
                avg = mkt.get(avg_key, 0)
                tok = mkt.get(tok_key, "")
                if shares < 1 or avg <= 0 or not tok:
                    continue
                mid = _poly_midpoint(client, tok)
                if mid <= 0:
                    continue

                # ── Layer 0.5: TIERED PARTIAL TP ──
                # Only TP on our directional side — never sell hedge shares
                if side != mkt.get("original_dir", side):
                    continue  # skip opposite side (hedge/endgame shares)
                # mm_trades.jsonl shows 76.5% WR → HOLD better
                # But on-chain data may differ — keeping TP active until verified
                _PARTIAL_TP_TIERS = [
                    (1.3, 0.14),   # mid ≥ entry×1.3 → sell 14%
                    (1.5, 0.48),   # mid ≥ entry×1.5 → sell 48%
                    (1.8, 0.33),   # mid ≥ entry×1.8 → sell 33%
                ]
                _tp_key = f"_tp_tier_{side}"
                _tp_done = mkt.get(_tp_key, 0)  # how many tiers already executed
                # ⚠️ DZ-3: All exit sells below — must check fill status before updating shares. See docs/DANGER_ZONES.md
                if _tp_done < len(_PARTIAL_TP_TIERS) and not _cost_recovered:
                    _mult, _sell_pct = _PARTIAL_TP_TIERS[_tp_done]
                    _tp_target = avg * _mult
                    if mid >= _tp_target:
                        _tp_sell = max(1, int(shares * _sell_pct))
                        try:
                            _tp_price = round(max(0.01, mid * 0.97), 2)
                            _tp_r = client.sell_shares(tok, _tp_sell, price=_tp_price)
                            _tp_sell_status = _tp_r.get("status", "") if isinstance(_tp_r, dict) else ""
                            if _tp_sell_status != "matched":
                                # Sell is pending on CLOB — do NOT reduce shares until fill confirmed
                                mkt.setdefault("pending_sells", []).append({
                                    "side": side, "shares": _tp_sell, "price": _tp_price,
                                    "order_id": _tp_r.get("orderID", "") if isinstance(_tp_r, dict) else "",
                                    "order_ts": time.time(), "type": "partial_tp",
                                })
                                logger.warning("PARTIAL TP PENDING %s %s: %d shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                               cid[:8], side, _tp_sell, _tp_price, _tp_sell_status)
                            else:
                                _tp_pnl = _tp_sell * (_tp_price - avg)
                                mkt[shares_key] = shares - _tp_sell
                                mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _tp_sell * avg)
                                mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _tp_pnl
                                mkt[_tp_key] = _tp_done + 1
                                logger.info("PARTIAL TP T%d %s %s: sell %d/%d @ $%.3f (target $%.3f, x%.1f) pnl=$%.2f",
                                            _tp_done + 1, cid[:8], side, _tp_sell, int(shares),
                                            _tp_price, _tp_target, _mult, _tp_pnl)
                            # Free roll hedge: only on LAST tier (T3) — only if sell confirmed
                            if _tp_sell_status == "matched" and _tp_done + 1 == len(_PARTIAL_TP_TIERS):
                                _tte_exit = (end_ms - now_ms) / 1000 if end_ms > 0 else 999
                                if _tte_exit > 60:
                                    _opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                                    if _opp_tok:
                                        _fr_budget = _tp_sell * _tp_price * 0.05  # 5% of T3 sold value
                                        _opp_mid = _poly_midpoint(client, _opp_tok)
                                        _fr_price = round(max(0.01, (_opp_mid if _opp_mid > 0 else 0.10) * 2.0), 2)
                                        _fr_price = min(_fr_price, 0.15)
                                        if _fr_budget >= _fr_price:
                                            try:
                                                client.buy_shares(_opp_tok, round(_fr_budget, 2), price=_fr_price)
                                                logger.info("FREE ROLL %s: buy opp @ $%.2f ($%.2f) — %ds left",
                                                            cid[:8], _fr_price, _fr_budget, int(_tte_exit))
                                            except Exception as _fre:
                                                logger.debug("Free roll buy failed: %s", _fre)
                        except Exception as e:
                            logger.warning("Partial TP failed %s: %s", cid[:8], e)
                        continue  # re-check next cycle for next tier

                # ── Layer 1: PROFIT LOCK (96¢+) → sell 96%, keep 4% free roll + hedge ──
                if mid >= _BLACK_SWAN_MID:
                    # Sell 96% to lock profit, keep 4% as free roll ($0 risk)
                    _sell_shares = max(1, int(shares * _BLACK_SWAN_SELL_PCT))
                    _keep = shares - _sell_shares
                    try:
                        # Aggressive taker: hit best bid (mid × 0.97) to guarantee fill
                        # Last 2-3s bots can move price — speed > price
                        _sell_price = round(max(0.01, mid * 0.97), 2)
                        _pl_r = client.sell_shares(tok, _sell_shares, price=_sell_price)
                        _pl_status = _pl_r.get("status", "") if isinstance(_pl_r, dict) else ""
                        if _pl_status == "matched":
                            _pnl = _sell_shares * (_sell_price - avg)
                            _remaining_cost = _keep * avg
                            logger.info("PROFIT LOCK %s %s: sell %d/%d @ $%.2f | pnl=$%.2f | keep %d free (cost=$%.2f covered)",
                                        cid[:8], side, _sell_shares, int(shares), _sell_price,
                                        _pnl, int(_keep), _remaining_cost)
                            mkt[shares_key] = _keep
                            # FIX: reduce entry_cost so resolve_market PnL is correct
                            _sold_cost = _sell_shares * avg
                            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
                            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _pnl
                            mkt["cost_recovered"] = True  # remaining shares = free roll
                            # Don't set RESOLVED — keep shares alive for resolution payout
                        else:
                            # Sell is pending on CLOB — do NOT reduce shares until fill confirmed
                            mkt.setdefault("pending_sells", []).append({
                                "side": side, "shares": _sell_shares, "price": _sell_price,
                                "order_id": _pl_r.get("orderID", "") if isinstance(_pl_r, dict) else "",
                                "order_ts": time.time(), "type": "profit_lock",
                            })
                            logger.warning("PROFIT LOCK PENDING %s %s: %d shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                           cid[:8], side, _sell_shares, _sell_price, _pl_status)
                            continue  # don't proceed to hedge if sell not confirmed
                    except Exception as e:
                        logger.warning("Profit lock sell failed %s: %s", cid[:8], e)
                        continue
                    # Mini hedge: buy 2 shares opposite at market (insurance, not sizing)
                    # At 95¢ our side, opposite ≈ 5¢. Cost = 2 × 0.10 = $0.20 max.
                    # If we're wrong: 2 × $1 = $2 recovery. 100 trades: 5 wrong × $2 = $10.
                    _HEDGE_SHARES = 2
                    _opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                    _opp_side = "DOWN" if side == "UP" else "UP"
                    if _opp_tok:
                        _opp_mid = _poly_midpoint(client, _opp_tok)
                        # Aggressive taker: 2x mid to guarantee instant fill
                        _hedge_price = round(max(0.01, (_opp_mid if _opp_mid > 0 else 0.06) * 2.0), 2)
                        _hedge_price = min(_hedge_price, 0.15)  # cap at 15¢
                        try:
                            _hedge_cost = round(_HEDGE_SHARES * _hedge_price, 2)
                            client.buy_shares(_opp_tok, _hedge_cost, price=_hedge_price)
                            logger.info("HEDGE %s %s: %d shares @ $%.2f ($%.2f)",
                                        cid[:8], _opp_side, _HEDGE_SHARES, _hedge_price, _hedge_cost)
                        except Exception as e:
                            logger.warning("HEDGE FAILED %s: %s", cid[:8], e)
                    break  # exit inner for-side loop — market is RESOLVED

                if _cost_recovered:
                    # ── Post recovery: FREE ROLL — just hold, $0 risk ──
                    continue

                # ── Layer 2: COST RECOVERY (mid ≥ 55¢) ──
                if mid >= _COST_RECOVERY_MID:
                    _original_cost = mkt.get("entry_cost", shares * avg)
                    if _original_cost <= 0:
                        continue
                    _sell_price = round(max(0.01, mid * 0.98), 2)
                    _shares_to_sell = min(shares - 1, math.ceil(_original_cost / _sell_price))
                    if _shares_to_sell < 1:
                        continue
                    try:
                        _cr_r = client.sell_shares(tok, _shares_to_sell, price=_sell_price)
                        _cr_status = _cr_r.get("status", "") if isinstance(_cr_r, dict) else ""
                        if _cr_status == "matched":
                            _recovered = _shares_to_sell * _sell_price
                            _remaining = shares - _shares_to_sell
                            logger.info("COST RECOVERY %s %s: sell %.0f/%.0f @ %.3f = $%.2f recovered | %.1f free shares",
                                        cid[:8], side, _shares_to_sell, shares, _sell_price,
                                        _recovered, _remaining)
                            mkt[shares_key] = _remaining
                            # FIX: reduce entry_cost so resolve_market PnL is correct
                            _sold_cost = _shares_to_sell * avg
                            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
                            mkt["cost_recovered"] = True
                            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + (_recovered - _sold_cost)
                        else:
                            # Sell is pending on CLOB — do NOT reduce shares until fill confirmed
                            mkt.setdefault("pending_sells", []).append({
                                "side": side, "shares": _shares_to_sell, "price": _sell_price,
                                "order_id": _cr_r.get("orderID", "") if isinstance(_cr_r, dict) else "",
                                "order_ts": time.time(), "type": "cost_recovery",
                            })
                            logger.warning("COST RECOVERY PENDING %s %s: %d shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                           cid[:8], side, _shares_to_sell, _sell_price, _cr_status)
                    except Exception as e:
                        logger.warning("Cost recovery sell failed %s: %s", cid[:8], e)
                    continue

                # ── Layer 3: STOP LOSS (pre-recovery, -25%) ──
                pnl_pct = (mid - avg) / avg
                if pnl_pct < -_EXIT_STOP_PCT:
                    try:
                        _sell_price = round(max(0.01, mid * 0.97), 2)
                        _sl_r = client.sell_shares(tok, shares, price=_sell_price)
                        _sl_status = _sl_r.get("status", "") if isinstance(_sl_r, dict) else ""
                        if _sl_status == "matched":
                            _round_pnl = shares * (_sell_price - avg)
                            mkt[shares_key] = 0
                            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _round_pnl
                            mkt["rounds"] = mkt.get("rounds", 0) + 1
                            mkt["last_sell_ts"] = int(time.time())
                            _rd = mkt["rounds"]
                            logger.info("STOP LOSS R%d %s %s: sell %.1f @ %.3f (entry %.3f, %.0f%%) pnl=$%.2f",
                                        _rd, cid[:8], side, shares, mid, avg, pnl_pct * 100, _round_pnl)
                            # Cancel remaining unfilled rungs (prevent DCA into losing position)
                            try:
                                _open_orders = client.get_orders(market=cid) if hasattr(client, "get_orders") else []
                                for _oo in (_open_orders or []):
                                    _oid = _oo.get("id", "")
                                    if _oid:
                                        client.client.cancel(order_id=_oid)
                                if _open_orders:
                                    logger.info("SL CANCEL %s: cancelled %d remaining orders after stop loss",
                                                cid[:8], len(_open_orders))
                            except Exception as _ce:
                                logger.warning("SL cancel remaining failed %s: %s", cid[:8], _ce)
                            # Clear phased rungs to prevent DCA into stopped-out position
                            mkt["phased_rungs"] = []
                            mkt["pending_orders"] = []
                            if _rd >= _MAX_ROUNDS:
                                mkt["phase"] = "RESOLVED"
                                mkt["early_exit"] = "stop_loss"
                        else:
                            # Sell is pending on CLOB — do NOT zero shares until fill confirmed
                            mkt.setdefault("pending_sells", []).append({
                                "side": side, "shares": shares, "price": _sell_price,
                                "order_id": _sl_r.get("orderID", "") if isinstance(_sl_r, dict) else "",
                                "order_ts": time.time(), "type": "stop_loss",
                            })
                            logger.warning("STOP LOSS PENDING %s %s: %.1f shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                           cid[:8], side, shares, _sell_price, _sl_status)
                    except Exception as e:
                        logger.warning("Stop loss failed %s %s: %s", cid[:8], side, e)

    # ── Re-entry: scalp again in same window after early exit ──
    if is_heavy and client and not dry_run:
        for cid, mkt in list(state["markets"].items()):
            if mkt["phase"] != "OPEN":
                continue
            _rd = mkt.get("rounds", 0)
            if _rd < 1 or _rd >= _MAX_ROUNDS:
                continue  # no sell yet, or max rounds reached
            # Must be sold out (both sides zero)
            if mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0:
                continue
            # Cooldown after last sell
            _last_sell = mkt.get("last_sell_ts", 0)
            if time.time() - _last_sell < _REENTRY_COOLDOWN_S:
                continue
            # Enough time left in window (>4 min)
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 90_000:
                logger.info("REENTRY SKIP %s R%d: < 1.5 min remaining", cid[:8], _rd + 1)
                mkt["phase"] = "RESOLVED"
                mkt["early_exit"] = f"window_end_r{_rd}"
                continue

            # Re-run M1 + signal pipeline for fresh direction
            _title_lower = mkt.get("title", "").lower()
            _sym = "ETHUSDT" if "ethereum" in _title_lower else "BTCUSDT"
            _m1 = _m1_return(_sym)
            _m1_vol = _vol_1m(_sym)
            _m1_thresh = max(0.0005, _m1_vol * 1.0)
            if abs(_m1) < _m1_thresh:
                logger.debug("REENTRY WAIT %s R%d: M1 weak |%.4f| < %.4f",
                             cid[:8], _rd + 1, _m1, _m1_thresh)
                continue  # keep waiting, re-check next heavy cycle

            # Cross-exchange validation
            _xprice, _xdiv = _cross_exchange_price(_sym)
            _coin_price = _xprice if _xprice > 0 else _price(_sym)
            if _xdiv > 0.003:
                logger.debug("REENTRY WAIT %s R%d: cross-exchange divergence %.2f%%",
                             cid[:8], _rd + 1, _xdiv * 100)
                continue

            # BTC move since window open > 0.3% → skip re-entry (regime change)
            _coin_open = mkt.get("btc_open_price") or _coin_price
            if _coin_open > 0:
                _window_move = abs(_coin_price - _coin_open) / _coin_open
                if _window_move > 0.003:
                    logger.info("REENTRY SKIP %s R%d: window move %.2f%% > 0.3%% (regime change)",
                                cid[:8], _rd + 1, _window_move * 100)
                    mkt["phase"] = "RESOLVED"
                    mkt["early_exit"] = f"regime_change_r{_rd}"
                    continue

            start_ms = mkt.get("window_start_ms", 0)
            mins_left = max(1, (end_ms - now_ms) / 60_000)

            # Bridge + OB (no indicator signal — same as initial entry)
            bridge_p_up = compute_fair_up(_coin_price, _coin_open, _m1_vol, int(mins_left))
            # Fat-tail correction built into compute_fair_up() via Student-t(ν=5)

            ob_adjustment = 0.0
            if hasattr(client, "get_order_book"):
                try:
                    up_book = client.get_order_book(mkt.get("up_token_id", ""))
                    bid_vol = sum(b["size"] for b in up_book.get("bids", []))
                    ask_vol = sum(a["size"] for a in up_book.get("asks", []))
                    if bid_vol + ask_vol > 0:
                        imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                        ob_adjustment = imbalance * 0.05
                except Exception:
                    pass

            fair = bridge_p_up + ob_adjustment
            fair = max(0.05, min(0.95, fair))

            # M1 vs fair direction conflict
            _fair_up = fair > 0.50
            _m1_up = _m1 > 0
            if abs(_m1) >= 0.001 and _fair_up != _m1_up:
                logger.info("REENTRY SKIP %s R%d: M1/fair conflict", cid[:8], _rd + 1)
                continue

            # Market mid sanity
            if hasattr(client, "get_midpoint"):
                _dir_tok = mkt.get("up_token_id", "") if fair > 0.50 else mkt.get("down_token_id", "")
                _mid = _poly_midpoint(client, _dir_tok)
                if 0 < _mid < 0.38:
                    logger.info("REENTRY SKIP %s R%d: market mid=%.3f < 0.38",
                                cid[:8], _rd + 1, _mid)
                    continue

            # Place re-entry order — round-dependent pricing discount
            # R2: bid × 0.90 (10% cheaper), R3: bid × 0.80 (20% cheaper)
            # Rationale: stop loss already triggered → regime may have changed → demand better price
            _round_discount = {1: 0.90, 2: 0.80}.get(_rd, 0.80)
            _re_config = _copy(config)
            _re_config.max_directional_bid = round(config.max_directional_bid * _round_discount, 3)
            _re_config.max_hedge_bid = round(config.max_hedge_bid * _round_discount, 3)
            logger.info("REENTRY R%d %s: bid cap $%.3f (%.0f%% of R1 $%.3f)",
                        _rd + 1, cid[:8], _re_config.max_directional_bid,
                        _round_discount * 100, config.max_directional_bid)
            _re_mkt = PolyMarket(
                condition_id=cid, title=mkt.get("title", ""),
                category="crypto_15m",
                yes_token_id=mkt.get("up_token_id", ""),
                no_token_id=mkt.get("down_token_id", ""),
                liquidity=15000)
            bankroll = state.get("bankroll", 100.0)
            n_tranches = calc_tranches(bankroll, _re_config)
            orders = plan_opening(_re_mkt, fair, _re_config, bankroll=bankroll,
                                  tranche=0, total_tranches=n_tranches,
                                  risk_mode=risk_mode)
            if not orders:
                continue

            results = _execute(orders, client, cid=cid,
                               signal_ctx={"fair": round(fair, 4), "round": _rd + 1,
                                           "bridge": round(bridge_p_up, 4)})
            # Reset entry fields for new round
            mkt["phased_rungs"] = []  # clear stale rungs from previous round
            mkt["entry_price"] = _coin_price
            mkt["entry_ts"] = int(time.time())
            mkt["up_avg_price"] = 0
            mkt["down_avg_price"] = 0
            mkt["entry_cost"] = 0
            mkt["fills_confirmed"] = True
            mkt["original_dir"] = "UP" if fair > 0.50 else "DOWN"
            # Reset TP + cost recovery state for new round (prevent stale flags)
            mkt["_tp_tier_UP"] = 0
            mkt["_tp_tier_DOWN"] = 0
            mkt["cost_recovered"] = False
            mkt["tranches_done"] = 1
            mkt["tranches_total"] = n_tranches
            pending = []
            for r in results:
                if not r.get("submitted"):
                    continue
                _bump_fill(state, "submitted")
                status = r.get("status", "")
                if status == "matched":
                    outcome = r["outcome"]
                    price = r["price"]
                    size = r.get("size_matched", r["size"])
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * price) / mkt["up_shares"] if mkt["up_shares"] > 0 else 0
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * price) / mkt["down_shares"] if mkt["down_shares"] > 0 else 0
                    mkt["entry_cost"] += size * price
                    _bump_fill(state, "filled")
                    logger.info("REENTRY FILL R%d %s %s: %.1f @ $%.3f",
                                _rd + 1, cid[:8], outcome, size, price)
                else:
                    pending.append(r)
            if pending:
                mkt["pending_orders"] = pending
                mkt["fills_confirmed"] = False

            _new_dir = "UP" if fair > 0.50 else "DOWN"
            logger.info("REENTRY R%d %s dir=%s fair=%.3f (prev_dir=%s)",
                        _rd + 1, cid[:8], _new_dir, fair,
                        mkt.get("_prev_dir", mkt.get("original_dir", "?")))
            mkt["_prev_dir"] = _new_dir

    logger.info("Heavy loop: %d markets in %.1fs", _heavy_loop_n, time.time() - _heavy_loop_t0)

    # BMD fix: WS data freshness monitor (detect partial disconnects)
    _ws_status = []
    if _ws_binance:
        _bp = _ws_binance.get_price("BTCUSDT")
        _ws_status.append(f"BinWS={'OK' if _bp else 'STALE'}")
    if _ws_poly:
        # Check if any subscribed token has fresh data
        _poly_ok = any(_ws_poly.get_midpoint(t) is not None
                       for cid, m in state.get("markets", {}).items()
                       for t in [m.get("up_token_id", "")]
                       if m.get("phase") == "OPEN" and t)
        _ws_status.append(f"PolyWS={'OK' if _poly_ok else 'STALE'}")
    if _ws_user:
        _ws_status.append(f"UserWS={'OK' if _ws_user.connected else 'DOWN'}")
    if _ws_status:
        logger.debug("WS feeds: %s", " | ".join(_ws_status))

    # Resolutions
    _check_resolutions(state, client=client)

    # Periodic fill rate log (every heavy cycle)
    if is_heavy:
        fr, ff, fs = _fill_rate(state)
        if fs > 0:
            fst = state.get("fill_stats", _FILL_STATS_DEFAULT)
            logger.info("FILL STATS: %d/%d (%.0f%%) | cancel=%d expired=%d",
                        ff, fs, fr, fst.get("cancelled", 0), fst.get("expired", 0))

    # Cleanup old resolved
    resolved = [c for c, m in state["markets"].items() if m["phase"] == "RESOLVED"]
    if len(resolved) > 50:
        for c in resolved[:-50]:
            del state["markets"][c]
            _endgame_mid_buf.pop(c, None)

    return state


# ═══════════════════════════════════════
#  Status
# ═══════════════════════════════════════

def _status(state: dict):
    wl = state.get("watchlist", {})
    active = {c: m for c, m in state["markets"].items() if m["phase"] != "RESOLVED"}
    resolved = {c: m for c, m in state["markets"].items() if m["phase"] == "RESOLVED"}
    print(f"\n{'='*55}")
    print(f"  MM v4 Status — {datetime.now(tz=_HKT):%Y-%m-%d %H:%M HKT}")
    print(f"{'='*55}")
    print(f"  Bankroll:  ${state.get('bankroll', 0):.2f}")
    print(f"  Watchlist: {len(wl)} | Active: {len(active)} | Resolved: {len(resolved)}")
    print(f"  Daily PnL: ${state.get('daily_pnl', 0):.2f} | Total: ${state.get('total_pnl', 0):.2f}")
    print(f"  Markets:   {state.get('total_markets', 0)} | Consec losses: {state.get('consecutive_losses', 0)}")
    _ibr = state.get("initial_bankroll", state.get("bankroll", 0))
    _tpnl = state.get("total_pnl", 0)
    _pct = _tpnl / _ibr * 100 if _ibr > 0 else 0
    _stop = " 💀 HARD STOPPED" if state.get("hard_stopped") else ""
    print(f"  Drawdown:  ${_tpnl:.2f} ({_pct:+.1f}% of ${_ibr:.0f}) | limit -20%{_stop}")
    fr, ff, fs = _fill_rate(state)
    fstats = state.get("fill_stats", _FILL_STATS_DEFAULT)
    print(f"  Fill Rate: {fr:.0f}% ({ff}/{fs}) | Cancel: {fstats.get('cancelled',0)} | Expired: {fstats.get('expired',0)}")
    _ls = state.get("live_start_ts", 0)
    if _ls > 0:
        elapsed_h = (time.time() - _ls) / 3600
        if elapsed_h < _PROTECTION_HOURS:
            print(f"  🛡️ PROTECTION: {elapsed_h:.1f}/{_PROTECTION_HOURS}h | bet={_PROTECTION_BET_PCT:.0%} | max {_PROTECTION_MAX_MARKETS} mkt")
        else:
            print(f"  Protection: ended ({elapsed_h:.1f}h elapsed)")
    if wl:
        print(f"\n  ── Watchlist ──")
        for c, w in wl.items():
            lead = (w["start_ms"] - int(time.time() * 1000)) / 60_000
            print(f"  {c[:8]} | {lead:+.0f}m | {w['title'][:40]}")
    if active:
        print(f"\n  ── Active ──")
        for c, m in active.items():
            comb = m.get("up_avg_price", 0) + m.get("down_avg_price", 0)
            print(f"  {c[:8]} | combined={comb:.3f} | ${m.get('entry_cost',0):.2f}")
    if resolved:
        for m in list(resolved.values())[-3:]:
            print(f"  {m.get('condition_id','')[:8]} | PnL ${m.get('realized_pnl', 0):.2f}")
    print()


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="MM v3 — Strategy C")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--status", action="store_true")
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--bankroll", type=float, default=0,
                    help="Override bankroll (for dry-run simulation)")
    ap.add_argument("--bet-pct", type=float, default=0,
                    help="Override bet_pct (e.g. 0.23 for 23%%)")
    ap.add_argument("--continuous-momentum", action="store_true",
                    help="Use current_price vs open instead of M1-only")
    ap.add_argument("--both-sides", action="store_true",
                    help="Both-sides W4 strategy: buy UP+DOWN with momentum lean, hold to resolution")
    ap.add_argument("--w4-live", action="store_true",
                    help="Enable LIVE execution for both-sides (without this, both-sides = paper only)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if args.status:
        _status(_load())
        return

    dry_run = args.dry_run
    config = MMConfig()
    if args.bet_pct > 0:
        config.bet_pct = args.bet_pct

    both_sides = getattr(args, 'both_sides', False)
    w4_live = getattr(args, 'w4_live', False)
    if both_sides:
        config.half_spread = 0.020
        if w4_live:
            # 🔴 2CHECK: Live gate — only allow live with explicit --w4-live flag
            print(f"  MODE: W4 BOTH-SIDES LIVE (half_spread={config.half_spread})")
        else:
            if not dry_run:
                print("  ⛔ --both-sides without --w4-live → forcing dry-run.")
                dry_run = True
            print(f"  MODE: W4 BOTH-SIDES PAPER (half_spread={config.half_spread})")
    else:
        print(f"  MODE: {'DRY-RUN' if dry_run else 'LIVE'}")

    # ─── Start market data fetcher (background, log-only for now) ───
    global _mkt_fetcher
    try:
        from polymarket.data.market_data import StaggeredFetcher
        _mkt_fetcher = StaggeredFetcher()
        _mkt_fetcher.start_background("BTCUSDT", interval_sec=10)
        print("  MARKET DATA: background fetcher started (log-only)")
    except Exception as e:
        logger.warning("Market data fetcher failed to start: %s — continuing without", e)

    # ─── Start Binance WebSocket price feed (replaces REST polling) ───
    global _ws_binance
    try:
        from polymarket.data.ws_binance import BinancePriceFeed
        _ws_binance = BinancePriceFeed()
        _ws_binance.start()
        print("  WS PRICE: Binance bookTicker feed started")
    except Exception as e:
        logger.warning("WS price feed failed to start: %s — using REST fallback", e)

    # ─── Start Polymarket WebSocket order book feed (replaces REST OB polling) ───
    global _ws_poly
    try:
        from polymarket.data.ws_polymarket import PolymarketBookFeed
        _ws_poly = PolymarketBookFeed()
        _ws_poly.start()
        print("  WS OB: Polymarket book feed started")
    except Exception as e:
        logger.warning("WS OB feed failed to start: %s — using REST fallback", e)

    gamma = GammaClient()
    client = None
    if not dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            client = PolymarketClient(dry_run=False)
            print("  CLOB: connected")
            # ⚠️ DZ-9: Startup orphan cancel — does NOT check if orders were filled before cancelling. See docs/DANGER_ZONES.md
            # Startup safety: cancel OWN orphan orders only (not 1H bot's orders)
            try:
                existing = client.get_orders()
                _pre_state = _load()  # load state early for orphan filter
                _own_cids = set(_pre_state.get("markets", {}).keys()) | set(_pre_state.get("watchlist", {}).keys())
                if existing:
                    cancelled = 0
                    for o in existing:
                        oid = o.get("id", "")
                        _mkt = o.get("market", "")
                        if oid and (_mkt in _own_cids or not _mkt):
                            try:
                                client.client.cancel(order_id=oid)
                                cancelled += 1
                            except Exception as ce:
                                logger.warning("Startup cancel failed for %s: %s", oid[:12], ce)
                    print(f"  STARTUP: cancelled {cancelled}/{len(existing)} orphan orders")
            except Exception as e:
                logger.warning("Startup orphan check failed: %s", e)
        except Exception as e:
            print(f"  CLOB failed: {e} → dry-run")
            dry_run = True

    # ─── Start Polymarket User WebSocket feed (instant fill/cancel detection) ───
    global _ws_user
    if client and not dry_run:
        try:
            from polymarket.data.ws_user import PolymarketUserFeed
            from polymarket.config.settings import POLY_CREDS_CACHE_PATH
            import json as _json_ws
            # Load API creds from the same cache file as polymarket_client.py
            if os.path.exists(POLY_CREDS_CACHE_PATH):
                with open(POLY_CREDS_CACHE_PATH, "r") as _f:
                    _creds = _json_ws.load(_f)
                _ws_user = PolymarketUserFeed()
                _ws_user.start(
                    _creds.get("api_key", ""),
                    _creds.get("api_secret", ""),
                    _creds.get("api_passphrase", ""),
                )
                print("  WS USER: Polymarket user feed started (fill/cancel detection)")
            else:
                logger.warning("WS USER: creds cache not found at %s — skipping",
                               POLY_CREDS_CACHE_PATH)
        except Exception as e:
            logger.warning("WS USER feed failed to start: %s — using REST fallback", e)

    if dry_run and client is None:
        class _Mock:
            def buy_shares(self, tid, amt, price=0):
                logger.info("DRY BUY %s $%.2f @ %.3f", tid[:10], amt, price)
                return {"dry_run": True}
        client = _Mock()

    state = _load()
    # W4 live flag: ephemeral, always reset from CLI args (2check fix #2)
    state["_w4_live"] = bool(both_sides and w4_live)
    if args.bankroll > 0:
        state["bankroll"] = args.bankroll
    elif client and hasattr(client, "get_usdc_balance"):
        try:
            state["bankroll"] = client.get_usdc_balance()
        except Exception:
            pass

    # Newbie protection: record first live start time
    if not dry_run and not state.get("live_start_ts"):
        state["live_start_ts"] = time.time()
        logger.info("PROTECTION: live_start_ts set — %.0fh protection active", _PROTECTION_HOURS)

    br = state.get("bankroll", 100)
    bet = br * config.bet_pct
    _prot_active = (not dry_run and state.get("live_start_ts", 0) > 0
                    and time.time() - state["live_start_ts"] < _PROTECTION_HOURS * 3600)
    _prot_str = f" | 🛡️ PROTECTION ({_PROTECTION_BET_PCT:.0%}, {_PROTECTION_MAX_MARKETS} mkt)" if _prot_active else ""
    print(f"  [{datetime.now(tz=_HKT):%H:%M HKT}] Bankroll ${br:.2f} | "
          f"Bet {config.bet_pct:.0%} = ${bet:.2f} | Spread {config.half_spread:.1%}{_prot_str}")

    if args.cycle:
        state = run_cycle(state, gamma, client, config, dry_run,
                                  continuous_momentum=getattr(args, 'continuous_momentum', False),
                                  both_sides=both_sides)
        _save(state)
        _status(state)
    else:
        print(f"  Loop: {_CYCLE_S}s")
        try:
            while True:
                try:
                    state = run_cycle(state, gamma, client, config, dry_run,
                                  continuous_momentum=getattr(args, 'continuous_momentum', False),
                                  both_sides=both_sides)
                    _save(state)
                    _log_positions(state)
                except Exception as e:
                    logger.error("Cycle error: %s", e, exc_info=True)
                time.sleep(_CYCLE_S)
        except KeyboardInterrupt:
            print("\n  Shutting down...")
            # Cancel OWN open orders on CLOB (prevent orphans, don't touch 1H bot)
            if client and hasattr(client, "get_orders") and not dry_run:
                try:
                    remaining = client.get_orders()
                    _own_cids = set(state.get("markets", {}).keys()) | set(state.get("watchlist", {}).keys())
                    for o in (remaining or []):
                        oid = o.get("id", "")
                        _mkt = o.get("market", "")
                        if oid and (_mkt in _own_cids or not _mkt):
                            try:
                                client.client.cancel(order_id=oid)
                            except Exception:
                                pass
                    if remaining:
                        print(f"  Cancelled {len(remaining)} open orders")
                except Exception:
                    pass
            _save(state)
            _status(state)
            # Shutdown market data fetcher
            if _mkt_fetcher:
                _mkt_fetcher.shutdown()
            # Shutdown WS price feed
            if _ws_binance:
                _ws_binance.stop()
            # Shutdown Polymarket WS OB feed
            if _ws_poly:
                _ws_poly.stop()
            # Shutdown Polymarket User WS feed
            if _ws_user:
                _ws_user.stop()


if __name__ == "__main__":
    main()
