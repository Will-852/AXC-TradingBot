#!/usr/bin/env python3
"""
run_1h_live.py — 1H Conviction Pricing Bot

Strategy: Observe BTC vs open price → when conviction is high enough → enter
at dynamic price → hold to resolution.

No fixed wait times or thresholds. Three factors interact continuously:
  time × odds × order_book → conviction → entry_price + size

Based on blue-walnut whale analysis ($103K PnL, 4561 markets, 1H only).
Resolution: Binance 1H OHLC candle (close >= open = Up).

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --live --bet-pct 0.03
  PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --status
"""

import argparse
import json
import logging
import math
import os
import signal as _signal
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import (
    MMMarketState, PlannedOrder, resolve_market,
)
from polymarket.strategy.hourly_engine import (
    HourlyConfig, OBState, ConvictionSignal, conviction_signal,
)
from polymarket.exchange.gamma_client import GammaClient

logger = logging.getLogger(__name__)

_HKT = timezone(timedelta(hours=8))
_ET = timezone(timedelta(hours=-4))
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state_1h.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades_1h.jsonl")
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log_1h.jsonl")
_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE = "https://api.binance.com/api/v3"
_DATA_API = "https://data-api.polymarket.com"
_ANALYSIS_TAPE = os.path.join(_LOG_DIR, "analysis_1h.jsonl")
_SIGNAL_TAPE_1H = os.path.join(_LOG_DIR, "signal_tape_1h.jsonl")
_PAPER_PNL_LOG = os.path.join(_LOG_DIR, "paper_pnl_1h.jsonl")

_CYCLE_S = 10           # main loop: 10s (1H is slower than 15M)
_HEAVY_INTERVAL_S = 20  # heavy ops every 20s (3x from 60s, 12 req/min, 50% total budget)
_SCAN_INTERVAL_S = 300   # discovery every 5 min
_TOTAL_LOSS_FUSE_PCT = 0.22  # 22% of initial bankroll → permanent stop live

# Load TG credentials from .env for alerts
_ENV_PATH = os.path.join(_AXC, "secrets", ".env")
_TG_NEWS_TOKEN = ""
_TG_CHAT_ID = ""
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("TELEGRAM_NEWS_BOT_TOKEN="):
                _TG_NEWS_TOKEN = _line.split("=", 1)[1]
            elif _line.startswith("TELEGRAM_CHAT_ID="):
                _TG_CHAT_ID = _line.split("=", 1)[1]
_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}

# Slug construction for 1H markets
_COIN_SLUGS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
_COIN_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}

# Coin scope: all 3 coins in dry-run for data collection
# BTC/ETH/SOL all active — live mode gated by --live flag, not coin filter
_LIVE_COINS = {"BTC", "ETH", "SOL"}
_OBSERVE_LOG = os.path.join(os.path.dirname(__file__), "logs", "observe_1h.jsonl")

_running = True
_ws_binance = None  # BinancePriceFeed instance (set in main, WS price source)
_ws_poly = None     # PolymarketBookFeed instance (set in main, WS OB source)


def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s", signum)
    _running = False


_signal.signal(_signal.SIGINT, _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


# ═══════════════════════════════════════
#  HTTP helpers
# ═══════════════════════════════════════

def _get_json(url: str, timeout: int = 10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-1H/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug("HTTP fail %s: %s", url[:80], e)
        return None


# ═══════════════════════════════════════
#  Analysis Data Collection (read-only, zero trading impact)
# ═══════════════════════════════════════

_ANALYSIS_INTERVAL_S = 60      # normal: collect every 60s
_ANALYSIS_BURST_S = 15         # burst: every 15s in last 4 min of window
_ANALYSIS_BURST_MIN = 57       # burst starts at minute 57 (last 3 min)
_last_analysis = 0.0


def _collect_analysis(cached_markets: list):
    """Collect Polymarket-native data for offline analysis. Zero impact on trading.
    Writes to analysis_1h.jsonl. All errors silently caught.
    Burst mode: 15s interval in last 4 min (whale exits cluster near resolution)."""
    global _last_analysis
    now = time.time()
    now_ms = int(now * 1000)

    # Adaptive interval: burst in last 3 min of any active window
    interval = _ANALYSIS_INTERVAL_S
    try:
        for _m in cached_markets:
            if _m.get("start_ms", 0) < now_ms < _m.get("end_ms", 0):
                if (now_ms - _m["start_ms"]) / 60_000 >= _ANALYSIS_BURST_MIN:
                    interval = _ANALYSIS_BURST_S
                    break
    except Exception:
        pass

    if now - _last_analysis < interval:
        return
    _last_analysis = now

    for mkt in cached_markets:
        cid = mkt["cid"]
        start_ms = mkt["start_ms"]
        end_ms = mkt["end_ms"]
        if now_ms < start_ms or now_ms > end_ms:
            continue
        coin = mkt["coin"]
        try:
            record = {"ts": time.time(), "coin": coin, "cid": cid[:12]}

            # #3: Token price history within this window (uses UP token_id, not condition_id)
            _up_tok = mkt.get("up_tok", "")
            ph = _get_json(
                f"https://clob.polymarket.com/prices-history"
                f"?market={_up_tok}&interval=1h&fidelity=1",
                timeout=2)
            if ph and isinstance(ph, dict) and "history" in ph:
                hist = ph["history"]
                record["price_hist_len"] = len(hist)
                if hist:
                    prices = [float(h.get("p", 0)) for h in hist if h.get("p")]
                    if prices:
                        record["price_first"] = prices[0]
                        record["price_last"] = prices[-1]
                        record["price_min"] = min(prices)
                        record["price_max"] = max(prices)
                        record["price_range"] = round(max(prices) - min(prices), 4)

            # #1: Recent trades (last 50) — who's trading and which direction
            trades = _get_json(
                f"{_DATA_API}/trades?market={cid}&limit=50",
                timeout=2)
            if trades and isinstance(trades, list):
                buys = sum(1 for t in trades if t.get("side", "").upper() == "BUY")
                sells = len(trades) - buys
                total_size = sum(float(t.get("size", 0)) for t in trades)
                record["trades_count"] = len(trades)
                record["trades_buys"] = buys
                record["trades_sells"] = sells
                record["trades_total_size"] = round(total_size, 2)
                record["trades_buy_ratio"] = round(buys / len(trades), 3) if trades else 0

            # #2: Top holders per side — smart money flow indicator
            holders_raw = _get_json(
                f"{_DATA_API}/holders?market={cid}&limit=10&minBalance=10",
                timeout=2)
            if holders_raw and isinstance(holders_raw, list):
                for token_group in holders_raw:
                    hs = token_group.get("holders", []) if isinstance(token_group, dict) else []
                    if not hs:
                        continue
                    idx = hs[0].get("outcomeIndex", -1) if hs else -1
                    side = "up" if idx == 0 else "down" if idx == 1 else "unk"
                    amounts = [float(h.get("amount", 0)) for h in hs]
                    record[f"holders_{side}_count"] = len(hs)
                    record[f"holders_{side}_total"] = round(sum(amounts), 2)
                    record[f"holders_{side}_top3"] = [
                        {"wallet": h.get("proxyWallet", "")[:12],
                         "name": h.get("name", "")[:20],
                         "amt": round(float(h.get("amount", 0)), 1)}
                        for h in hs[:3]
                    ]

            # #4: Open interest — total money in market
            oi = _get_json(f"{_DATA_API}/oi?market={cid}", timeout=2)
            if oi and isinstance(oi, dict):
                record["oi"] = round(float(oi.get("value", 0)), 2)

            # Metadata for delta analysis
            t_el = (now_ms - start_ms) / 60_000
            record["t_elapsed"] = round(t_el, 1)
            record["burst"] = t_el >= _ANALYSIS_BURST_MIN

            # Write
            os.makedirs(os.path.dirname(_ANALYSIS_TAPE), exist_ok=True)
            with open(_ANALYSIS_TAPE, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")

        except Exception as e:
            logger.debug("Analysis collect %s: %s", coin, e)


# ═══════════════════════════════════════
#  Holder Imbalance — smart money directional signal
#
#  Known limitations (bmd 2026-03-22):
#  - Threshold 0.20 calibrated on n=1 case (BTC +$7 window). Need 50+ windows to validate.
#  - Only top 10 holders (minBalance=10) — may miss crowd positioning. By design: track whales.
#  - Dedup guard runs BEFORE holder gate: if pending order exists, FLIP cannot fire.
#    One-order guard takes priority. Holder signal applies only to fresh entries.
#  - h_imbal logged per order for post-hoc WR analysis: grep "holder_signal" in order log.
# ═══════════════════════════════════════

_HOLDER_STRONG_IMBAL = 0.20    # >0.20 against direction → FLIP (follow smart money)
_HOLDER_MILD_IMBAL = 0.10      # 0.10-0.20 against → size × 50%
_holder_cache: dict = {}        # {cid: (ts, imbalance)}
_HOLDER_CACHE_TTL = 30          # cache 30s (don't fetch every heavy cycle)


def _holder_imbalance(cid: str) -> float:
    """Fetch holder imbalance for a market. Returns float in [-1, +1].
    Positive = UP dominant, negative = DOWN dominant. 0 = balanced or unavailable.
    Cached for 30s to save API budget."""
    now = time.time()
    if cid in _holder_cache and now - _holder_cache[cid][0] < _HOLDER_CACHE_TTL:
        return _holder_cache[cid][1]
    try:
        data = _get_json(f"{_DATA_API}/holders?market={cid}&limit=10&minBalance=10", timeout=2)
        if not data or not isinstance(data, list):
            _holder_cache[cid] = (now, 0.0)
            return 0.0
        up_total, dn_total = 0.0, 0.0
        for group in data:
            hs = group.get("holders", []) if isinstance(group, dict) else []
            if not hs:
                continue
            idx = hs[0].get("outcomeIndex", -1)
            total = sum(float(h.get("amount", 0)) for h in hs)
            if idx == 0:
                up_total = total
            elif idx == 1:
                dn_total = total
        combined = up_total + dn_total
        imbal = (up_total - dn_total) / combined if combined > 0 else 0.0
        _holder_cache[cid] = (now, imbal)
        return imbal
    except Exception:
        _holder_cache[cid] = (now, 0.0)
        return 0.0


# ═══════════════════════════════════════
#  Market Data
# ═══════════════════════════════════════

_price_cache: dict = {}  # {coin: (ts, price)}


def _btc_price(coin: str = "BTC") -> float:
    """Get latest price with 3s cache."""
    now = time.time()
    if coin in _price_cache and now - _price_cache[coin][0] < 3:
        return _price_cache[coin][1]
    # WebSocket path — sub-millisecond, no REST call needed
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    if _ws_binance:
        ws_price = _ws_binance.get_price(sym)
        if ws_price:
            _price_cache[coin] = (now, ws_price)
            return ws_price
    # REST fallback
    data = _get_json(f"{_BINANCE}/ticker/price?symbol={sym}")
    if data:
        p = float(data["price"])
        _price_cache[coin] = (now, p)
        return p
    return _price_cache.get(coin, (0, 0))[1]


def _binance_open(coin: str, start_ms: int) -> float | None:
    """Fetch Binance 1H candle open price."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1h&startTime={start_ms}&limit=1")
    if data and isinstance(data, list) and data:
        return float(data[0][1])
    return None


# ═══════════════════════════════════════
#  Volume Imbalance — multi-signal direction filter
#  Backtest: Bridge+VolImbal at t=25 → SOL 82%, ETH 83%, BTC 77% WR
# ═══════════════════════════════════════

_vol_imbal_cache: dict = {}  # {coin: (ts, direction_or_none)}
_VOL_IMBAL_CACHE_TTL = 15   # 15s cache


def _vol_imbalance(coin: str, window_start_ms: int) -> str | None:
    """Check Binance 1m kline buy/sell volume ratio since window start.
    Returns 'UP' if buy-dominant, 'DOWN' if sell-dominant, None if neutral.
    Cached 15s."""
    now = time.time()
    cache_key = f"{coin}_{window_start_ms}"
    if cache_key in _vol_imbal_cache and now - _vol_imbal_cache[cache_key][0] < _VOL_IMBAL_CACHE_TTL:
        return _vol_imbal_cache[cache_key][1]

    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now_ms = int(now * 1000)
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m"
                     f"&startTime={window_start_ms}&endTime={now_ms}&limit=60")
    if not data or not isinstance(data, list) or len(data) < 5:
        _vol_imbal_cache[cache_key] = (now, None)
        return None

    buy_vol, sell_vol = 0.0, 0.0
    for k in data:
        o, c, v = float(k[1]), float(k[4]), float(k[5])
        if c >= o:
            buy_vol += v
        else:
            sell_vol += v

    total = buy_vol + sell_vol
    if total < 1:
        _vol_imbal_cache[cache_key] = (now, None)
        return None

    ratio = buy_vol / total
    result = "UP" if ratio > 0.55 else ("DOWN" if ratio < 0.45 else None)
    _vol_imbal_cache[cache_key] = (now, result)
    return result


# ═══════════════════════════════════════
#  Time-of-Day Gate (HKT)
#  Backtest: SOL 09h=52% WR, 19h=64% WR → skip these hours
# ═══════════════════════════════════════

_TOD_SKIP_HOURS_HKT = {9, 19}  # WR < 65% in backtest


# ═══════════════════════════════════════
#  Signal Tape — record REAL Poly mid every heavy cycle for future backtest
#  Target: signal_tape_1h.jsonl (same format idea as 15M signal_tape.jsonl)
# ═══════════════════════════════════════

def _record_signal_tape(coin: str, cid: str, up_tok: str, dn_tok: str,
                        start_ms: int, end_ms: int, t_elapsed: float,
                        spot_price: float, btc_open: float, vol_1m: float,
                        sig, vol_dir: str | None, h_imbal: float = 0):
    """Append one snapshot to signal_tape_1h.jsonl. Called every heavy cycle per market."""
    up_mid = _poly_midpoint(up_tok)
    dn_mid = _poly_midpoint(dn_tok)
    record = {
        "ts": time.time(),
        "coin": coin,
        "cid": cid[:16],
        "start_ms": start_ms,
        "end_ms": end_ms,
        "t_elapsed": round(t_elapsed, 1),
        "up_mid": round(up_mid, 4) if up_mid else None,
        "dn_mid": round(dn_mid, 4) if dn_mid else None,
        "spot": round(spot_price, 2),
        "open": round(btc_open, 2),
        "vol_1m": round(vol_1m, 6),
        "fair_up": round(sig.fair_up, 4) if sig else None,
        "conviction": round(sig.conviction, 3) if sig else None,
        "confidence": round(sig.confidence, 3) if sig else None,
        "direction": sig.direction if sig else None,
        "action": sig.action if sig else None,
        "entry_price": sig.entry_price if sig else None,
        "vol_dir": vol_dir,
        "h_imbal": round(h_imbal, 3),
    }
    try:
        with open(_SIGNAL_TAPE_1H, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════
#  Paper PnL — simulate buy/sell with real Poly mid, track cumulative PnL
#  Not precise (ignores slippage, partial fills) but good enough to decide go/no-go
# ═══════════════════════════════════════

_paper_state = {
    "positions": {},      # {cid: {coin, direction, shares, entry_price, entry_mid, cost}}
    "resolved": [],       # [{coin, direction, entry_price, result, pnl, ...}]
    "total_pnl": 0.0,
    "by_coin": {},        # {coin: {trades, wins, pnl}}
}
_PAPER_BUDGET = 8.40  # simulated $8.40 per window (same as 15M backtest)


def _paper_enter(cid: str, coin: str, direction: str, entry_price: float,
                 up_mid: float, conviction: float):
    """Record a simulated entry at current market conditions."""
    if cid in _paper_state["positions"]:
        return  # already in
    shares = _PAPER_BUDGET / entry_price if entry_price > 0 else 0
    _paper_state["positions"][cid] = {
        "coin": coin, "direction": direction,
        "shares": round(shares, 1), "entry_price": round(entry_price, 4),
        "entry_mid": round(up_mid, 4) if up_mid else 0,
        "cost": round(shares * entry_price, 2),
        "conviction": round(conviction, 3),
        "ts": time.time(),
    }
    logger.info("PAPER ENTER %s %s %s: %.0f shares @ $%.3f (mid=$%.3f conv=%.2f)",
                coin, direction, cid[:8], shares, entry_price, up_mid or 0, conviction)


def _paper_resolve(cid: str, result: str):
    """Resolve a paper position. Logs PnL."""
    pos = _paper_state["positions"].pop(cid, None)
    if not pos:
        return
    coin = pos["coin"]
    won = (pos["direction"] == result)
    if won:
        pnl = pos["shares"] * (1.0 - pos["entry_price"])
    else:
        pnl = -pos["cost"]

    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "coin": coin, "cid": cid[:16],
        "direction": pos["direction"], "result": result,
        "won": won, "shares": pos["shares"],
        "entry_price": pos["entry_price"], "entry_mid": pos["entry_mid"],
        "conviction": pos["conviction"],
        "pnl": round(pnl, 2), "cost": pos["cost"],
    }
    _paper_state["resolved"].append(record)
    _paper_state["total_pnl"] += pnl

    # Per-coin stats
    if coin not in _paper_state["by_coin"]:
        _paper_state["by_coin"][coin] = {"trades": 0, "wins": 0, "pnl": 0.0}
    cs = _paper_state["by_coin"][coin]
    cs["trades"] += 1
    cs["wins"] += int(won)
    cs["pnl"] += pnl

    # Log to file
    record["total_pnl"] = round(_paper_state["total_pnl"], 2)
    try:
        with open(_PAPER_PNL_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    wr = cs["wins"] / cs["trades"] * 100 if cs["trades"] else 0
    tag = "✅" if won else "❌"
    logger.info("PAPER %s %s %s %s: pnl=$%+.2f | %s WR=%.0f%% (%d/%d) cum=$%+.2f | TOTAL=$%+.2f",
                tag, coin, pos["direction"], result, pnl,
                coin, wr, cs["wins"], cs["trades"], cs["pnl"],
                _paper_state["total_pnl"])


def _paper_status():
    """Print paper trading summary."""
    ps = _paper_state
    n = len(ps["resolved"])
    if n == 0:
        return
    wins = sum(1 for r in ps["resolved"] if r["won"])
    logger.info("PAPER SUMMARY: %d trades | WR=%.0f%% | PnL=$%+.2f | Open=%d",
                n, wins / n * 100, ps["total_pnl"], len(ps["positions"]))
    for coin, cs in sorted(ps["by_coin"].items()):
        wr = cs["wins"] / cs["trades"] * 100 if cs["trades"] else 0
        logger.info("  %s: %d trades WR=%.0f%% PnL=$%+.2f",
                    coin, cs["trades"], wr, cs["pnl"])


def _vol_1m(coin: str = "BTC") -> float:
    """Per-minute volatility from Binance 1m klines (120 candles = 2h).

    Uses 1m close-to-close log returns — consistent with backtest
    (hourly_conviction_bt.py:compute_vol_1m) and 15M bot (run_mm_live.py:_vol_1m).
    Previous version used hourly gap returns / sqrt(60) which underestimates by 10-20%.
    """
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    url = f"{_BINANCE}/klines?symbol={sym}&interval=1m&limit=120"
    data = _get_json(url)
    if not data or len(data) < 20:
        return 0.00077  # fallback: ~50% annual BTC vol
    closes = [float(k[4]) for k in data]
    rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
    if len(rets) < 10:
        return 0.00077
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean)**2 for r in rets) / len(rets))
    return max(0.0001, vol)


def _poly_midpoint(token_id: str) -> float | None:
    # WebSocket path — sub-second, no REST call needed
    if _ws_poly:
        ws_mid = _ws_poly.get_midpoint(token_id)
        if ws_mid is not None:
            return ws_mid
    # REST fallback
    data = _get_json(f"https://clob.polymarket.com/midpoint?token_id={token_id}")
    if data:
        try:
            return float(data["mid"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _poly_ob(token_id: str) -> OBState:
    """Fetch OB and return OBState for conviction engine. WS first, REST fallback."""
    # WebSocket path — sub-second, no REST call needed
    if _ws_poly:
        ws_state = _ws_poly.get_book_state(token_id)
        if ws_state is not None:
            best_bid = ws_state["bid"]
            best_ask = ws_state["ask"]
            spread = (best_ask - best_bid) if best_bid and best_ask and best_ask > best_bid else 0
            return OBState(
                spread=round(spread, 4),
                bid_depth=ws_state["bid_depth"],
                ask_depth=ws_state["ask_depth"],
                imbalance=ws_state["imbalance"],
            )
    # REST fallback
    data = _get_json(f"https://clob.polymarket.com/book?token_id={token_id}")
    if not data:
        return OBState()
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    bid_prices = [float(b["price"]) for b in bids if b.get("price")]
    ask_prices = [float(a["price"]) for a in asks if a.get("price")]
    best_bid = max(bid_prices) if bid_prices else 0
    best_ask = min(ask_prices) if ask_prices else 0
    spread = (best_ask - best_bid) if best_bid and best_ask and best_ask > best_bid else 0
    bid_vol = sum(float(b.get("size", 0)) for b in bids)
    ask_vol = sum(float(a.get("size", 0)) for a in asks)
    total = bid_vol + ask_vol
    return OBState(
        spread=round(spread, 4),
        bid_depth=round(bid_vol, 2),
        ask_depth=round(ask_vol, 2),
        imbalance=round((bid_vol - ask_vol) / total, 4) if total else 0,
    )


# ═══════════════════════════════════════
#  Forced Exit — Black Swan Protection
# ═══════════════════════════════════════

_BLACK_SWAN_MID = 0.95   # sell 95% at 95¢+ → lock profit, keep 5% free roll
_BLACK_SWAN_SELL_PCT = 0.95  # sell 95%, keep 5% as free upside


def _try_sell_partial(client, state: dict, cid: str, mkt: dict,
                      up_tok: str, dn_tok: str, reason: str = "",
                      known_mid: float = 0, sell_pct: float = 1.0) -> bool:
    """Sell shares in a market. Returns True if sold.
    sell_pct: fraction to sell (0.90 = sell 90%, keep 10% free roll).
    known_mid: caller's last-known mid for pricing. Avoids re-fetch stale/fail.
    """
    if not client or not hasattr(client, "sell_shares"):
        return False
    sold = False
    for side, tok_key, shares_key, avg_key, tok in [
        ("UP", "up_token_id", "up_shares", "up_avg_price", up_tok),
        ("DOWN", "down_token_id", "down_shares", "down_avg_price", dn_tok),
    ]:
        shares = mkt.get(shares_key, 0)
        avg = mkt.get(avg_key, 0)
        if shares < 1 or not tok:
            continue
        # Use caller's known mid if available, else fetch (with NO fallback)
        mid = known_mid if known_mid > 0 else _poly_midpoint(tok)
        if not mid or mid <= 0:
            logger.warning("SELL ABORT %s %s: mid unavailable, refusing to sell blind", cid[:8], side)
            continue
        # Calculate shares to sell
        _sell_shares = max(1, int(shares * sell_pct))
        _keep = shares - _sell_shares
        sell_price = round(max(0.01, mid * 0.96), 2)  # 4% slippage (1H OB thinner)
        try:
            client.sell_shares(tok, _sell_shares, price=sell_price)
            pnl = _sell_shares * (sell_price - avg)
            mkt[shares_key] = _keep
            # FIX: reduce entry_cost proportionally so resolve_market PnL is correct.
            # Without this, resolve_market computes payout - FULL_original_cost → phantom loss.
            _sold_cost = _sell_shares * avg
            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + pnl
            if _keep > 0:
                mkt["cost_recovered"] = True  # remaining shares = free roll
            logger.info("SELL [%s] %s %s: %d/%d @ $%.3f | pnl=$%.2f | keep %d free",
                        reason, cid[:8], side, _sell_shares, int(shares), sell_price, pnl, int(_keep))
            sold = True
        except Exception as e:
            logger.warning("SELL FAILED [%s] %s %s: %s", reason, cid[:8], side, e)
    # Only mark RESOLVED if no shares remain
    remaining = mkt.get("up_shares", 0) + mkt.get("down_shares", 0)
    if sold and remaining < 1:
        mkt["phase"] = "RESOLVED"
        mkt["early_exit"] = reason
    return sold


def _check_black_swan(client, state: dict, dry_run: bool):
    """Check all open positions — sell if mid ≥ 95¢. Runs every cycle."""
    if dry_run or not client or not hasattr(client, "sell_shares"):
        return
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN":
            continue
        if mkt.get("cost_recovered"):
            continue  # free roll shares — hold to resolution, don't re-sell
        for side, tok_key, shares_key in [
            ("UP", "up_token_id", "up_shares"),
            ("DOWN", "down_token_id", "down_shares"),
        ]:
            shares = mkt.get(shares_key, 0)
            tok = mkt.get(tok_key, "")
            if shares < 1 or not tok:
                continue
            mid = _poly_midpoint(tok)
            if mid and mid >= _BLACK_SWAN_MID:
                logger.warning("BLACK SWAN %s %s: mid $%.3f ≥ $%.2f → selling all + hedge",
                               cid[:8], side, mid, _BLACK_SWAN_MID)
                sold = _try_sell_partial(client, state, cid, mkt,
                              mkt.get("up_token_id", ""), mkt.get("down_token_id", ""),
                              reason="profit_lock_95pct", known_mid=mid,
                              sell_pct=_BLACK_SWAN_SELL_PCT)
                # Greed hedge: buy opposite side min 5 shares at MARKET price (speed > price)
                # Must execute instantly — market can reverse in seconds.
                if sold:
                    opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                    opp_side = "DOWN" if side == "UP" else "UP"
                    opp_mid = _poly_midpoint(opp_tok) if opp_tok else None
                    # Aggressive limit = pseudo market order: mid + 100% overpay
                    hedge_price = round(max(0.01, (opp_mid or 0.06) * 2.0), 2)
                    if opp_tok and hedge_price < 0.15:  # cap: don't pay more than 15¢
                        try:
                            hedge_cost = round(2 * hedge_price, 2)
                            client.buy_shares(opp_tok, hedge_cost, price=hedge_price)
                            logger.info("HEDGE %s %s: 2 shares @ $%.2f ($%.2f) — market order",
                                        cid[:8], opp_side, hedge_price, hedge_cost)
                        except Exception as e:
                            logger.warning("HEDGE FAILED %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  1H Market Discovery
# ═══════════════════════════════════════

def _build_slug(coin: str, dt_et: datetime) -> str:
    name = _COIN_SLUGS.get(coin, "")
    if not name:
        return ""
    month = dt_et.strftime("%B").lower()
    day = str(dt_et.day)
    year = str(dt_et.year)
    hour = dt_et.strftime("%I").lstrip("0")
    ampm = dt_et.strftime("%p").lower()
    return f"{name}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et"


def _discover(gamma: GammaClient) -> list[dict]:
    """Find active BTC/ETH 1H markets."""
    results = []
    now_et = datetime.now(tz=_ET)
    now_s = int(time.time())
    base = now_et.replace(minute=0, second=0, microsecond=0)

    for i in range(3):
        ws = base + timedelta(hours=i)
        we = ws + timedelta(hours=1)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 300:
            continue
        for coin in ("BTC", "ETH", "SOL"):
            slug = _build_slug(coin, ws)
            if not slug:
                continue
            data = _get_json(f"{_GAMMA}/markets?slug={slug}")
            if not data or not isinstance(data, list) or not data:
                continue
            p = gamma.parse_market(data[0])
            cid = p.get("condition_id", "")
            up = p.get("yes_token_id", "")
            dn = p.get("no_token_id", "")
            if cid and up and dn:
                results.append({
                    "cid": cid, "title": p.get("title", ""),
                    "coin": coin, "slug": slug,
                    "up_tok": up, "dn_tok": dn,
                    "start_ms": ts * 1000, "end_ms": te * 1000,
                })
    return results


# ═══════════════════════════════════════
#  Order Execution (reuse from 15M bot)
# ═══════════════════════════════════════

def _execute_order(client, token_id: str, outcome: str,
                   price: float, size_usd: float, dry_run: bool,
                   coin: str = "BTC", cid: str = "") -> dict:
    """Submit a single limit order."""
    shares = size_usd / price if price > 0 else 0
    if shares < 5:
        # Bump to minimum 5 shares if budget allows
        min_cost = 5 * price
        if min_cost <= size_usd * 2:  # allow up to 2x bump
            size_usd = min_cost
            shares = 5
            logger.debug("Bumped to min 5 shares ($%.2f)", size_usd)
        else:
            logger.debug("Skip order: %.1f shares < 5 minimum", shares)
            return {"submitted": False, "reason": "below_min"}

    try:
        r = client.buy_shares(token_id, round(size_usd, 2), price=price)
        order_id = ""
        status = ""
        if isinstance(r, dict):
            order_id = r.get("orderID", r.get("id", ""))
            status = r.get("status", "")
            if r.get("dry_run"):
                status = "matched"
        # Phase 3 fix: if CLOB returns no order_id and status isn't matched,
        # treat as rejected — don't add to pending_orders.
        if not order_id and status != "matched":
            logger.warning("ORDER REJECTED %s: no order_id returned (status=%s)", outcome, status)
            _log_order("rejected", "", cid,
                       outcome=outcome, price=price, size=shares, status=status)
            return {"outcome": outcome, "submitted": False, "reason": "rejected_no_id"}
        _now = time.time()
        _btc_now = _btc_price(coin)
        logger.info("ORDER %s %.0f shares @ $%.3f ($%.2f) → %s",
                    outcome, shares, price, size_usd, status or order_id[:12])
        _log_order("submit", order_id, cid,
                   outcome=outcome, price=price, size=shares,
                   status=status, btc=round(_btc_now, 2))
        return {"outcome": outcome, "price": price, "size": shares,
                "token_id": token_id, "order_id": order_id,
                "status": status, "submitted": True,
                "order_ts": _now, "btc_at_order": round(_btc_now, 2)}
    except Exception as e:
        logger.error("ORDER FAILED %s: %s", outcome, e)
        return {"outcome": outcome, "submitted": False, "error": str(e)}


# ═══════════════════════════════════════
#  Fill Confirmation (reuse pattern)
# ═══════════════════════════════════════

def _check_fills(state: dict, client) -> None:
    if not client or not hasattr(client, "get_orders"):
        return
    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            # FIX: actually cancel orders on CLOB (was only logging, not cancelling)
            # GTC orders stay live after window close → can be adversely filled
            for _ep in pending:
                _oid = _ep.get("order_id", "")
                if _oid and client and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=_oid)
                        logger.info("CANCEL EXPIRED %s %s", cid[:8], _ep.get("outcome", ""))
                    except Exception:
                        pass
                _log_order("expired", _oid, cid,
                           outcome=_ep.get("outcome", ""))
            _bump_fill(state, "expired", len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue
        try:
            trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
            trade_ids = set()
            for t in (trades or []):
                tid = t.get("taker_order_id", "")
                if tid:
                    trade_ids.add(tid)
                for mo in t.get("maker_orders", []):
                    mid = mo.get("order_id", "") if isinstance(mo, dict) else ""
                    if mid:
                        trade_ids.add(mid)
            open_orders = client.get_orders(market=cid)
            open_ids = {o.get("id", "") for o in open_orders} if open_orders else set()

            filled, still_open = [], []
            for po in pending:
                oid = po.get("order_id", "")
                if oid and oid in trade_ids:
                    filled.append(po)
                elif oid and oid in open_ids:
                    still_open.append(po)
                elif not oid:
                    # No order_id = CLOB rejected at submit (Phase 3 fix)
                    _bump_fill(state, "cancelled")
                    _log_order("rejected", "", cid,
                               outcome=po.get("outcome", ""))
                else:
                    _bump_fill(state, "cancelled")
                    _log_order("cancelled_external", oid, cid,
                               outcome=po.get("outcome", ""))

            # FIX: always update pending_orders — not just when fills exist.
            # Old code: `if filled: ... mkt["pending_orders"] = still_open`
            # Bug: cancelled orders stayed in pending forever when no fills,
            # making budget_remaining permanently stuck at 0 for that market.
            mkt["pending_orders"] = still_open

            if filled:
                for f in filled:
                    o = f["outcome"]
                    if o == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += f["size"]
                        mkt["up_avg_price"] = (old + f["size"] * f["price"]) / mkt["up_shares"]
                    elif o == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += f["size"]
                        mkt["down_avg_price"] = (old + f["size"] * f["price"]) / mkt["down_shares"]
                    mkt["entry_cost"] += f["size"] * f["price"]
                    _bump_fill(state, "filled")
                    # AS metrics
                    _title = mkt.get("title", "").lower()
                    _fill_coin = "ETH" if "ethereum" in _title else "BTC"
                    _btc_fill = _btc_price(_fill_coin)
                    _order_ts = f.get("order_ts", 0)
                    _ttf = round(time.time() - _order_ts, 1) if _order_ts > 0 else 0
                    _log_order("fill", f.get("order_id", ""), cid,
                               outcome=o, price=f["price"], size=f["size"],
                               btc_at_fill=round(_btc_fill, 2),
                               time_to_fill_s=_ttf)
                    logger.info("FILL %s %s: %.0f @ $%.3f ttf=%.0fs",
                                cid[:8], o, f["size"], f["price"], _ttf)

            if not still_open:
                mkt["fills_confirmed"] = True
        except Exception as e:
            logger.warning("Fill check %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  Resolution (Binance 1H OHLC)
# ═══════════════════════════════════════

def _check_resolutions(state: dict):
    now_ms = int(time.time() * 1000)
    # Batch resolutions by window_start_ms so BTC+ETH same-hour = 1 event
    # for consecutive loss counting (correlated outcomes)
    hour_pnl: dict[int, float] = {}  # {start_ms: net_pnl}

    for cid, md in list(state["markets"].items()):
        if md.get("phase") == "RESOLVED":
            continue
        end_ms = md.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + 120_000:
            continue
        start_ms = md.get("window_start_ms", 0)
        if start_ms <= 0:
            continue

        title = md.get("title", "").lower()
        if "solana" in title:
            sym = "SOLUSDT"
        elif "ethereum" in title:
            sym = "ETHUSDT"
        else:
            sym = "BTCUSDT"
        data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1h&startTime={start_ms}&limit=1")
        if not data:
            continue

        btc_o, btc_c = float(data[0][1]), float(data[0][4])
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = _from_dict(md)
        pnl = resolve_market(ms, result)
        resolved_dict = _to_dict(ms)
        # Preserve runtime keys not in MMMarketState
        for _rk in ("pending_orders", "fills_confirmed", "cost_recovered", "early_exit"):
            if _rk in md:
                resolved_dict[_rk] = md[_rk]
        state["markets"][cid] = resolved_dict
        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] = state.get("total_markets", 0) + 1

        # Accumulate per-hour PnL (BTC+ETH same hour = 1 event)
        hour_pnl[start_ms] = hour_pnl.get(start_ms, 0) + pnl

        _log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                     "result": result, "pnl": round(pnl, 4),
                     "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                     "total_pnl": round(state["total_pnl"], 2)})

        d = "↑" if result == "UP" else "↓"
        print(f"  RESOLVED {cid[:8]} {d} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")

        # Paper trade resolution
        _paper_resolve(cid, result)

    # Update consecutive losses per hour-window (not per market)
    for _start_ms, net_pnl in sorted(hour_pnl.items()):
        if net_pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 5:
                cd = (datetime.now(tz=_HKT) + timedelta(hours=4)).isoformat(timespec="seconds")
                state["cooldown_until"] = cd
                logger.warning("CIRCUIT BREAKER: %d consecutive hour-losses → cooldown until %s",
                               state["consecutive_losses"], cd)
        else:
            state["consecutive_losses"] = 0


# ═══════════════════════════════════════
#  State Management
# ═══════════════════════════════════════

def _bump_fill(state: dict, event: str, n: int = 1):
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


def _load() -> dict:
    if not os.path.exists(_STATE_PATH):
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "fill_stats": dict(_FILL_STATS_DEFAULT)}
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        return d
    except Exception:
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "fill_stats": dict(_FILL_STATS_DEFAULT)}


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


def _to_dict(s: MMMarketState) -> dict:
    return {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}


def _from_dict(d: dict) -> MMMarketState:
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def _log_trade(record: dict):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRADE_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _log_order(event: str, order_id: str, cid: str, **kwargs):
    """Per-order lifecycle log: submit/fill/cancel/expired."""
    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": event,
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


def _tg_alert(msg: str):
    """Send alert via @AXCnews_bot Telegram."""
    if not _TG_NEWS_TOKEN or not _TG_CHAT_ID:
        logger.warning("TG alert skipped: no credentials")
        return
    try:
        data = json.dumps({"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_TG_NEWS_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("TG alert failed: %s", e)


# ═══════════════════════════════════════
#  Main Cycle
# ═══════════════════════════════════════

def run_cycle(state: dict, gamma: GammaClient, client,
              config: HourlyConfig, dry_run: bool,
              last_scan: float, last_heavy: float,
              cached_markets: list, cached_vol: float) -> tuple:
    """One cycle of the 1H bot. Returns (state, last_scan, last_heavy, cached_markets, cached_vol)."""
    now = time.time()
    now_hkt = datetime.now(tz=_HKT)
    now_et = datetime.now(tz=_ET)

    # ── Daily reset ──
    today = now_hkt.strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today

    # ── Kill switches ──
    cooldown = state.get("cooldown_until", "")
    if cooldown and now_hkt.isoformat(timespec="seconds") < cooldown:
        return state, last_scan, last_heavy, cached_markets, cached_vol
    elif cooldown and now_hkt.isoformat(timespec="seconds") >= cooldown:
        # Cooldown expired — reset
        state["cooldown_until"] = ""
        state["consecutive_losses"] = 0
        logger.info("Cooldown expired, resuming trading")

    daily_loss_pct = abs(state["daily_pnl"]) / max(state["bankroll"], 1) if state["daily_pnl"] < 0 else 0
    if daily_loss_pct > 0.15:
        logger.warning("DAILY LOSS %.1f%% > 15%% → STOP", daily_loss_pct * 100)
        return state, last_scan, last_heavy, cached_markets, cached_vol

    # ── Fast ops (every cycle) ──
    _check_fills(state, client)
    _check_resolutions(state)

    # ── Heavy ops (every 60s) ──
    is_heavy = (now - last_heavy) >= _HEAVY_INTERVAL_S
    # ── Black swan check: EVERY cycle (not just heavy) — sell at 94¢ ──
    _check_black_swan(client, state, dry_run)

    if not is_heavy:
        return state, last_scan, last_heavy, cached_markets, cached_vol
    last_heavy = now

    # ── Discovery (every 5 min) ──
    if (now - last_scan) >= _SCAN_INTERVAL_S:
        try:
            cached_markets = _discover(gamma)
            if cached_markets:
                logger.info("Discovered %d 1H markets", len(cached_markets))
                # Subscribe discovered tokens to WS order book feed
                if _ws_poly:
                    for mkt_info in cached_markets:
                        _ws_poly.subscribe(
                            [mkt_info["up_tok"], mkt_info["dn_tok"]],
                            condition_id=mkt_info["cid"])
        except Exception as e:
            logger.warning("Discovery failed: %s", e)
        last_scan = now

    # ── Refresh vol (every heavy cycle) ──
    # Per-coin vol cache (refreshed per heavy cycle)
    _coin_vols = {}
    for _vc in ("BTC", "ETH", "SOL"):
        _coin_vols[_vc] = _vol_1m(_vc)

    # ── Refresh bankroll ──
    if client and hasattr(client, "get_usdc_balance") and not dry_run:
        try:
            state["bankroll"] = client.get_usdc_balance()
        except Exception:
            pass

    # ── Analysis data collection (read-only, 60s interval) ──
    _collect_analysis(cached_markets)

    # ── Evaluate each active market ──
    for mkt_info in cached_markets:
        cid = mkt_info["cid"]
        coin = mkt_info["coin"]
        start_ms = mkt_info["start_ms"]
        end_ms = mkt_info["end_ms"]
        now_ms = int(now * 1000)

        # Only trade during the window
        if now_ms < start_ms or now_ms > end_ms:
            continue

        t_elapsed = (now_ms - start_ms) / 60_000  # minutes

        # Get current price + open price
        current_price = _btc_price(coin)
        if current_price <= 0:
            continue

        # Get or cache open price
        existing = state["markets"].get(cid, {})
        btc_open = existing.get("btc_open_price", 0)
        if btc_open <= 0:
            btc_open = _binance_open(coin, start_ms)
            if not btc_open:
                continue

        # Get OB state
        up_tok = mkt_info["up_tok"]
        dn_tok = mkt_info["dn_tok"]
        ob = _poly_ob(up_tok)

        # Compute budget remaining for this window
        # FIX: count PENDING orders too — entry_cost only updates on fill confirmation,
        # but pending orders already commit wallet funds. Without this, budget_spent=0
        # for unfilled orders → infinite re-entry (51 orders / $55 exposure bug).
        _filled_cost = existing.get("entry_cost", 0)
        _pending_cost = sum(
            o.get("size", 0) * o.get("price", 0)
            for o in existing.get("pending_orders", [])
            if o.get("submitted")
        )
        budget_spent = _filled_cost + _pending_cost
        window_budget = state["bankroll"] * config.max_size_fraction
        budget_remaining_frac = max(0, (window_budget - budget_spent) / window_budget) if window_budget > 0 else 0

        # Build current position info
        current_position = None
        if existing.get("phase") == "OPEN":
            up_s = existing.get("up_shares", 0)
            dn_s = existing.get("down_shares", 0)
            if up_s > 0 or dn_s > 0:
                pos_dir = "UP" if up_s >= dn_s else "DOWN"
                cost = existing.get("entry_cost", 0)
                # Estimate current value from midpoint
                up_mid = _poly_midpoint(up_tok)
                dn_mid = _poly_midpoint(dn_tok)
                current_val = (up_s * (up_mid or 0.5)) + (dn_s * (dn_mid or 0.5))
                pnl_pct = (current_val - cost) / cost if cost > 0 else 0
                current_position = {
                    "direction": pos_dir,
                    "avg_price": existing.get(f"{pos_dir.lower()}_avg_price", 0.40),
                    "unrealized_pnl_pct": pnl_pct,
                }

        # ── Time-of-Day gate: skip low-WR hours (backtest: 09h=52%, 19h=64%) ──
        _hkt_hour = now_hkt.hour
        if _hkt_hour in _TOD_SKIP_HOURS_HKT and current_position is None:
            logger.debug("TOD SKIP %s: hour %dh HKT in skip list", coin, _hkt_hour)
            continue

        # ── THE CORE: conviction_signal() ──
        sig = conviction_signal(
            t_elapsed=t_elapsed,
            btc_current=current_price,
            btc_open=btc_open,
            vol_1m=_coin_vols.get(coin, cached_vol),
            ob=ob,
            config=config,
            bankroll=state["bankroll"],
            budget_remaining_frac=budget_remaining_frac,
            current_position=current_position,
        )

        # ── Signal tape: record EVERY market EVERY heavy cycle for future backtest ──
        _record_signal_tape(
            coin=coin, cid=cid, up_tok=up_tok, dn_tok=dn_tok,
            start_ms=start_ms, end_ms=end_ms, t_elapsed=t_elapsed,
            spot_price=current_price, btc_open=btc_open,
            vol_1m=_coin_vols.get(coin, cached_vol), sig=sig,
            vol_dir=_vol_imbalance(coin, start_ms) if sig.action in ("ENTER", "ADD") else None,
            h_imbal=0,  # populated at entry time only
        )

        # ── Observe-only coins: log signal but don't trade ──
        # EXIT passthrough: if observe coin has a live position (from before scope change),
        # allow EXIT to execute so positions don't get stranded.
        # Note: _check_black_swan + _check_resolutions already cover ALL coins regardless.
        if coin not in _LIVE_COINS:
            _has_live_pos = existing.get("phase") == "OPEN" and existing.get("entry_cost", 0) > 0
            if sig.action == "EXIT" and _has_live_pos and not dry_run:
                logger.warning("OBSERVE EXIT %s: live position exists, executing exit: %s",
                               coin, sig.reason)
                # Fall through to EXIT handler below (don't continue)
            else:
                if sig.action in ("ENTER", "ADD", "EXIT"):
                    _obs = {"ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
                            "coin": coin, "action": sig.action, "direction": sig.direction,
                            "conviction": round(sig.conviction, 3),
                            "entry_price": sig.entry_price, "fair_up": round(sig.fair_up, 3),
                            "t_elapsed": round(t_elapsed, 1), "btc": round(current_price, 2),
                            "reason": sig.reason[:80]}
                    try:
                        os.makedirs(os.path.dirname(_OBSERVE_LOG), exist_ok=True)
                        with open(_OBSERVE_LOG, "a") as _of:
                            _of.write(json.dumps(_obs) + "\n")
                    except Exception:
                        pass
                    logger.info("OBSERVE %s %s %s | conv=%.2f fair=%.3f entry=$%.2f | %s",
                                sig.action, coin, sig.direction, sig.conviction,
                                sig.fair_up, sig.entry_price, sig.reason[:60])
                continue

        # ── Act on signal ──
        if sig.action == "ENTER" or sig.action == "ADD":
            # ── Volume imbalance filter (multi-signal) ──
            # Backtest: Bridge+VolImbal → +5-8pp WR vs bridge alone.
            # If volume direction conflicts with conviction direction → skip.
            _vol_dir = _vol_imbalance(coin, start_ms)
            if _vol_dir is not None and _vol_dir != sig.direction:
                logger.info("VOL CONFLICT %s: conviction=%s but vol=%s → skip",
                            coin, sig.direction, _vol_dir)
                continue

            # ── One-order-per-market guard ──
            # Prevents re-submission loop: CLOB cancels (no balance) → budget freed
            # → bot re-submits → cancelled again → 27 orders/28 min.
            # Rule: max 1 active (unfilled) order per market. ADD only after fill.
            _has_pending = bool(existing.get("pending_orders"))
            _has_fill = existing.get("entry_cost", 0) > 0
            if sig.action == "ENTER" and _has_pending:
                logger.debug("DEDUP %s: already has pending order, skipping", coin)
                continue
            if sig.action == "ADD" and not _has_fill:
                # ADD requires at least one filled order first
                logger.debug("DEDUP %s: ADD requires prior fill, skipping", coin)
                continue
            if sig.action == "ADD" and _has_pending:
                # Don't stack ADD orders either
                logger.debug("DEDUP %s: ADD blocked, pending order exists", coin)
                continue

            # ── Holder imbalance — smart money directional signal ──
            # Positive = UP dominant, Negative = DOWN dominant.
            # Three regimes: AGREE (boost) / MILD CONFLICT (reduce) / STRONG CONFLICT (follow whale)
            h_imbal = _holder_imbalance(cid)
            # imbal_with: how much holders AGREE with our direction (>0 = agree)
            # imbal_against: how much holders DISAGREE (>0 = disagree)
            if sig.direction == "UP":
                imbal_with = max(0, h_imbal)
                imbal_against = max(0, -h_imbal)
            else:
                imbal_with = max(0, -h_imbal)
                imbal_against = max(0, h_imbal)

            _size_mult = 1.0
            _flip = False
            if imbal_with > _HOLDER_STRONG_IMBAL:
                # Whale + bridge AGREE → strongest signal, boost size 30%
                _size_mult = 1.3
                logger.info("HOLDER AGREE %s %s: imbal=%.2f with direction — whale confirms, size ×130%%",
                            coin, sig.direction, h_imbal)
            elif imbal_against > _HOLDER_STRONG_IMBAL:
                # Smart money strongly disagrees → FOLLOW them, flip direction
                _holder_dir = "UP" if h_imbal > 0 else "DOWN"
                logger.info("HOLDER FLIP %s: bridge=%s but holders=%.2f → follow smart money %s",
                            coin, sig.direction, h_imbal, _holder_dir)
                _flip = True
                sig.direction = _holder_dir
                sig.fair_up = 1.0 - sig.fair_up
                sig.p_win = max(sig.fair_up, 1.0 - sig.fair_up)
                # FIX(bmd): use base_spread not ceiling — avoid always-$0.39 entry
                sig.entry_price = round(min(sig.p_win - config.base_spread, 0.35), 2)
                sig.entry_price = max(config.min_entry_price, sig.entry_price)
                _size_mult = 0.7  # slightly reduced for holder-driven flip
            elif imbal_against > _HOLDER_MILD_IMBAL:
                _size_mult = 0.5
                logger.info("HOLDER REDUCE %s %s: imbal=%.2f mild conflict — size ×50%%",
                            coin, sig.direction, h_imbal)

            # Mid sanity check: market must somewhat agree with our direction
            our_tok = up_tok if sig.direction == "UP" else dn_tok
            market_mid = _poly_midpoint(our_tok)
            if market_mid is not None and market_mid < config.min_market_mid:
                logger.debug("SKIP %s %s: market mid $%.2f < $%.2f (market disagrees)",
                             coin, sig.direction, market_mid, config.min_market_mid)
                continue

            # Determine token and size
            token_id = our_tok

            size_usd = sig.size_fraction * state["bankroll"] * _size_mult
            budget_left = window_budget - budget_spent
            # FIX: hard block when budget exhausted. Old max(2.50, ...) bypassed budget
            # and allowed infinite $2.50 orders → 119 shares / $50 on one market.
            if budget_left < 2.50:
                logger.info("BUDGET EXHAUSTED %s: spent $%.2f / $%.2f window budget",
                            coin, budget_spent, window_budget)
                continue
            size_usd = max(2.50, min(size_usd, budget_left))

            result = _execute_order(client, token_id, sig.direction,
                                    sig.entry_price, size_usd, dry_run,
                                    coin=coin, cid=cid)

            if result.get("submitted"):
                _bump_fill(state, "submitted")
                # Enrich order log with holder signal for post-hoc analysis
                _log_order("holder_signal", result.get("order_id", ""), cid,
                           h_imbal=round(h_imbal, 3), flip=_flip,
                           imbal_with=round(imbal_with, 3),
                           imbal_against=round(imbal_against, 3),
                           size_mult=round(_size_mult, 2))

                # Initialize or update market state
                if cid not in state["markets"]:
                    state["markets"][cid] = {
                        "condition_id": cid,
                        "title": mkt_info["title"],
                        "up_token_id": up_tok,
                        "down_token_id": dn_tok,
                        "window_start_ms": start_ms,
                        "window_end_ms": end_ms,
                        "btc_open_price": btc_open,
                        "phase": "OPEN",
                        "up_shares": 0, "up_avg_price": 0,
                        "down_shares": 0, "down_avg_price": 0,
                        "entry_cost": 0, "payout": 0, "realized_pnl": 0,
                        "pending_orders": [],
                        "fills_confirmed": False,
                    }

                mkt = state["markets"][cid]
                mkt["pending_orders"] = mkt.get("pending_orders", [])
                mkt["pending_orders"].append(result)
                mkt["fills_confirmed"] = False  # re-enable fill checking for new order

                # Dry-run: simulate instant fill
                if result.get("status") == "matched":
                    o = result["outcome"]
                    s, p = result["size"], result["price"]
                    if o == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += s
                        mkt["up_avg_price"] = (old + s * p) / mkt["up_shares"] if mkt["up_shares"] else p
                    else:
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += s
                        mkt["down_avg_price"] = (old + s * p) / mkt["down_shares"] if mkt["down_shares"] else p
                    mkt["entry_cost"] += s * p
                    mkt["fills_confirmed"] = True
                    mkt["pending_orders"] = []  # clear pending on instant fill (dry-run)
                    _bump_fill(state, "filled")

                _flip_tag = " [WHALE_FLIP]" if _flip else ""
                logger.info("  %s %s %s%s | conv=%.2f fair=%.3f entry=$%.2f size=$%.2f | %s",
                            sig.action, coin, sig.direction, _flip_tag, sig.conviction,
                            sig.fair_up, sig.entry_price, size_usd, sig.reason)

                # Paper trade: record simulated entry at real Poly mid
                _paper_enter(cid, coin, sig.direction, sig.entry_price,
                             _poly_midpoint(up_tok), sig.conviction)

        elif sig.action == "EXIT" and not dry_run:
            logger.warning("EXIT signal for %s: %s", cid[:8], sig.reason)
            _try_sell_partial(client, state, cid, existing, up_tok, dn_tok,
                              reason="exit_signal", sell_pct=1.0)  # EXIT = sell all

        elif sig.action == "WAIT":
            logger.debug("WAIT %s t=%.0fm: %s", coin, t_elapsed, sig.reason)

    return state, last_scan, last_heavy, cached_markets, cached_vol


# ═══════════════════════════════════════
#  Status
# ═══════════════════════════════════════

def _status(state: dict):
    print("\n  1H CONVICTION BOT STATUS")
    print("  " + "=" * 50)
    print(f"  Bankroll: ${state.get('bankroll', 0):.2f}")
    print(f"  Daily PnL: ${state.get('daily_pnl', 0):+.2f}")
    print(f"  Total PnL: ${state.get('total_pnl', 0):+.2f}")
    print(f"  Markets traded: {state.get('total_markets', 0)}")
    fs = state.get("fill_stats", _FILL_STATS_DEFAULT)
    s, f = fs.get("submitted", 0), fs.get("filled", 0)
    fr = f / s * 100 if s > 0 else 0
    print(f"  Fill rate: {f}/{s} ({fr:.0f}%)")
    print(f"  Consecutive losses: {state.get('consecutive_losses', 0)}")

    open_markets = {k: v for k, v in state.get("markets", {}).items() if v.get("phase") == "OPEN"}
    if open_markets:
        print(f"\n  OPEN POSITIONS ({len(open_markets)}):")
        for cid, m in open_markets.items():
            up, dn = m.get("up_shares", 0), m.get("down_shares", 0)
            cost = m.get("entry_cost", 0)
            print(f"    {m.get('title', cid[:12])}")
            print(f"      UP: {up:.0f} shares | DN: {dn:.0f} shares | Cost: ${cost:.2f}")

    resolved = [v for v in state.get("markets", {}).values() if v.get("phase") == "RESOLVED"]
    if resolved:
        wins = sum(1 for m in resolved if m.get("realized_pnl", 0) > 0)
        wr = wins / len(resolved) * 100 if resolved else 0
        print(f"\n  RESOLVED: {len(resolved)} markets | WR: {wr:.0f}%")

    # Paper trading summary
    ps = _paper_state
    if ps["resolved"]:
        n = len(ps["resolved"])
        pw = sum(1 for r in ps["resolved"] if r["won"])
        print(f"\n  📊 PAPER TRADING (simulated $8.40/window):")
        print(f"    Total: {n} trades | WR: {pw/n*100:.0f}% | PnL: ${ps['total_pnl']:+.2f}")
        for coin, cs in sorted(ps["by_coin"].items()):
            cwr = cs["wins"] / cs["trades"] * 100 if cs["trades"] else 0
            print(f"    {coin}: {cs['trades']} trades | WR: {cwr:.0f}% | PnL: ${cs['pnl']:+.2f}")
    if ps["positions"]:
        print(f"    Open: {len(ps['positions'])} paper positions")

    print()


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="1H Conviction Pricing Bot")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--status", action="store_true")
    ap.add_argument("--cycle", action="store_true", help="Run 1 cycle, exit")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--bankroll", type=float, default=0)
    ap.add_argument("--bet-pct", type=float, default=0, help="Override max_size_fraction")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    if args.status:
        _status(_load())
        return

    dry_run = args.dry_run
    config = HourlyConfig()
    if args.bet_pct > 0:
        config.max_size_fraction = args.bet_pct

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
        except Exception as e:
            print(f"  CLOB failed: {e} → dry-run fallback")
            dry_run = True

    if dry_run and client is None:
        class _Mock:
            def buy_shares(self, tid, amt, price=0):
                logger.info("DRY BUY %s $%.2f @ $%.3f", tid[:10], amt, price)
                return {"dry_run": True}
            def get_usdc_balance(self):
                return 138.0
            def get_orders(self, **kw):
                return []
            def get_trades(self, **kw):
                return []
        client = _Mock()

    state = _load()
    if args.bankroll > 0:
        state["bankroll"] = args.bankroll
    elif dry_run:
        state.setdefault("bankroll", 138.0)

    # Record initial bankroll for 22% total loss fuse (set once, never changes)
    if not dry_run and "initial_bankroll" not in state:
        state["initial_bankroll"] = state["bankroll"]
        logger.info("Initial bankroll recorded: $%.2f (22%% fuse = $%.2f)",
                     state["initial_bankroll"], state["initial_bankroll"] * _TOTAL_LOSS_FUSE_PCT)

    os.makedirs(_LOG_DIR, exist_ok=True)

    last_scan, last_heavy = 0.0, 0.0
    cached_markets: list = []
    cached_vol = 0.00077
    fuse_blown = False  # 22% total loss → switch to dry-run

    mode_str = "DRY-RUN" if dry_run else "LIVE"
    print(f"\n  1H CONVICTION BOT — {mode_str}")
    print(f"  Bankroll: ${state['bankroll']:.2f}")
    print(f"  State: {_STATE_PATH}")
    print()

    if args.cycle:
        state, *_ = run_cycle(state, gamma, client, config, dry_run,
                              last_scan, last_heavy, cached_markets, cached_vol)
        _save(state)
        _status(state)
        return

    try:
        while _running:
            try:
                state, last_scan, last_heavy, cached_markets, cached_vol = run_cycle(
                    state, gamma, client, config, dry_run,
                    last_scan, last_heavy, cached_markets, cached_vol)
                _save(state)

                # ── 22% total loss fuse: live → dry-run + TG alert ──
                init_br = state.get("initial_bankroll", 0)
                if not fuse_blown and not dry_run and init_br > 0:
                    total_pnl = state.get("total_pnl", 0)
                    if total_pnl < -(init_br * _TOTAL_LOSS_FUSE_PCT):
                        fuse_blown = True
                        dry_run = True
                        # Replace live client with mock for data collection
                        class _MockPost:
                            def buy_shares(self, tid, amt, price=0):
                                logger.info("FUSE DRY BUY %s $%.2f @ $%.3f", tid[:10], amt, price)
                                return {"dry_run": True}
                            def get_usdc_balance(self):
                                return state.get("bankroll", 0)
                            def get_orders(self, **kw):
                                return []
                            def get_trades(self, **kw):
                                return []
                        client = _MockPost()
                        loss_pct = abs(total_pnl) / init_br * 100
                        msg = (f"<b>🔴 1H BOT FUSE BLOWN</b>\n"
                               f"Total loss: ${total_pnl:.2f} ({loss_pct:.1f}% of ${init_br:.0f})\n"
                               f"Threshold: {_TOTAL_LOSS_FUSE_PCT*100:.0f}%\n"
                               f"<b>Switched to DRY-RUN.</b> Say OK to resume live.")
                        logger.warning("FUSE BLOWN: total_pnl=$%.2f (%.1f%% of $%.0f) → DRY-RUN",
                                       total_pnl, loss_pct, init_br)
                        _tg_alert(msg)

            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)
            time.sleep(_CYCLE_S)
    except KeyboardInterrupt:
        pass

    _save(state)
    _status(state)
    # Shutdown WS price feed
    if _ws_binance:
        _ws_binance.stop()
    # Shutdown Polymarket WS OB feed
    if _ws_poly:
        _ws_poly.stop()
    logger.info("1H bot stopped.")


if __name__ == "__main__":
    main()
