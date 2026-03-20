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

_HKT = ZoneInfo("Asia/Hong_Kong")
_ET = ZoneInfo("America/New_York")
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades.jsonl")
_CYCLE_S = 5           # 5s main loop — fast reaction
_SCAN_S = 300          # discovery every 5 min (watchlist covers gaps)
_HEAVY_INTERVAL_S = 30 # heavy ops (signal pipeline, indicator) every 30s

# Newbie protection: first N hours of live trading, cap exposure
_PROTECTION_HOURS = 3
_PROTECTION_BET_PCT = 0.01   # 1% per market during protection
_PROTECTION_MAX_MARKETS = 1  # 1 market per cycle (= 1 per 15min window)
_BINANCE = "https://fapi.binance.com"
_BINANCE_SPOT = "https://api.binance.com"

# Rate limit safety: track API calls per minute
_api_calls: dict = {}  # {"binance": [(ts, count), ...]}
_API_LIMIT_PER_MIN = 200  # conservative: 200/min out of 2400 limit


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


def _price(symbol: str = "BTCUSDT") -> float:
    """Latest price. Cached 3s — fast enough for cancel defense."""
    key = f"price_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 3:
        return _cache[key][0]
    if not _rate_ok("binance"):
        return _cache.get(key, (0, 0))[0]
    # Use book ticker for fastest price (best bid+ask, single call)
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
    url = f"{_BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m&limit=60"
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


def _poly_midpoint(client, token_id: str) -> float:
    """Polymarket midpoint for a token. Cached 5s."""
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
    """Order book imbalance for UP token. Cached 5s. Returns -1 to +1."""
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

    _COINS = [("btc", "bitcoin"), ("eth", "ethereum")]

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

def _execute(orders: list[PlannedOrder], client) -> list[dict]:
    """Submit limit orders. Returns order IDs — NOT fills.

    IMPORTANT: Limit orders (GTC) go on the book. Submit ≠ filled.
    Fills are checked later via _check_fills().
    """
    results = []
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
            logger.info("ORDER SUBMITTED %s %s: %.1f shares @ $%.3f ($%.2f) → %s [%s]",
                        o.outcome, o.token_id[:10], o.size, o.price, amount,
                        order_id[:12] if order_id else "ok", status)
            results.append({"outcome": o.outcome, "price": o.price,
                           "size": o.size, "token_id": o.token_id,
                           "order_id": order_id, "status": status,
                           "submitted": True})
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
            logger.info("Window ended %s: %d pending orders → expired (not filled)",
                        cid[:8], len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
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
                    logger.info("FILL CONFIRMED %s %s: %.1f @ $%.3f",
                                cid[:8], outcome, size, price)

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


def _log_trade(record: dict):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRADE_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _get_rolling_wr(state: dict, window: int = 30) -> tuple[float, int]:
    """Rolling win rate over last N resolved markets.
    Returns (wr, count). If count < 10, returns (0.68, count) = assume baseline.
    """
    resolved = [m for m in state["markets"].values() if m["phase"] == "RESOLVED"]
    recent = resolved[-window:] if len(resolved) > window else resolved
    if len(recent) < 10:
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

    # Thresholds — calibrated to stress test break-even
    # Break-even: WR-14% (54%) at fill=60% + adv=10%
    # HEDGE_ONLY at 54% = cut directional EXACTLY at break-even → prevent further loss
    if wr < 0.48:
        logger.warning("RISK MODE: STOPPED — rolling WR %.1f%% (%d trades) < 48%%", wr*100, count)
        return "STOPPED"
    elif wr < 0.54:
        logger.warning("RISK MODE: HEDGE_ONLY — rolling WR %.1f%% (%d trades) < 54%% (break-even)",
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

def _check_resolutions(state: dict):
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
        pnl = resolve_market(ms, result)
        state["markets"][cid] = _to_dict(ms)
        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] += 1

        if pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 5:
                cd = datetime.now(tz=_HKT) + timedelta(hours=24)
                state["cooldown_until"] = cd.isoformat()
                logger.warning("5 losses → COOLDOWN until %s", cd.strftime("%H:%M HKT"))
        else:
            state["consecutive_losses"] = 0

        fr_pct, _, _ = _fill_rate(state)
        _log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                     "result": result, "pnl": round(pnl, 4),
                     "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                     "total_pnl": round(state["total_pnl"], 2),
                     "fill_rate_pct": round(fr_pct, 1)})

        d = "↑" if result == "UP" else "↓"
        print(f"  RESOLVED {cid[:8]} {d} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")


# ═══════════════════════════════════════
#  Main Cycle — 5s fast loop + 30s heavy ops
# ═══════════════════════════════════════

_last_heavy_ts: float = 0  # module-level for heavy operation throttle


def run_cycle(state: dict, gamma: GammaClient, client,
              config: MMConfig, dry_run: bool) -> dict:
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

    # HARD STOP: total PnL drops >20% → permanent halt, needs manual restart
    # This survives daily resets — tracks ALL-TIME cumulative loss
    _total_pnl = state.get("total_pnl", 0.0)
    if not state.get("initial_bankroll") and br > 10:
        state["initial_bankroll"] = br  # record starting bankroll once (guard: >$10)
    _initial_br = state.get("initial_bankroll", 0)
    if _initial_br <= 0:
        # initial_bankroll not set yet — skip loss checks this cycle
        _initial_br = br if br > 10 else 100.0  # safe fallback
    if _total_pnl < -_initial_br * 0.20:
        logger.critical("💀 HARD STOP: total PnL $%.2f = %.0f%% of initial $%.0f. Manual restart required.",
                        _total_pnl, _total_pnl / _initial_br * 100, _initial_br)
        state["hard_stopped"] = True
        return state
    if state.get("hard_stopped"):
        logger.warning("💀 HARD STOPPED. Clear 'hard_stopped' from state to resume.")
        return state

    daily_loss_limit = br * 0.20  # 20% of CURRENT wallet balance (floating)
    if state.get("daily_pnl", 0) < -daily_loss_limit:
        logger.warning("KILL: daily loss $%.2f > 20%% of wallet $%.0f", -state["daily_pnl"], br)
        return state
    cd = state.get("cooldown_until", "")
    if cd:
        try:
            if now < datetime.fromisoformat(cd):
                return state
        except ValueError:
            pass
        state["consecutive_losses"] = 0
        state["cooldown_until"] = ""

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

    # FIX #1: Dedup — get all open orders on CLOB to avoid duplicate submissions
    _existing_markets = set()
    if client and hasattr(client, "get_orders") and not dry_run:
        try:
            _open = client.get_orders()
            _existing_markets = {o.get("market", "") for o in (_open or [])}
        except Exception:
            pass

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

        # Enter — detect coin from title
        _title_lower = wl["title"].lower()
        _sym = "ETHUSDT" if "ethereum" in _title_lower else "BTCUSDT"
        _coin_price = _price(_sym)
        _coin_open = _open_at(wl["start_ms"], _sym) or _coin_price
        _coin_vol = _vol_1m(_sym)
        mins_left = max(1, (wl["end_ms"] - now_ms) / 60_000)

        # Signal pipeline: combine all sources for P(Up)
        mkt = PolyMarket(condition_id=cid, title=wl["title"], category="crypto_15m",
                         yes_token_id=wl["up_tok"], no_token_id=wl["dn_tok"],
                         liquidity=15000)

        # 1. Brownian Bridge (base — always available)
        bridge_p_up = compute_fair_up(_coin_price, _coin_open, _coin_vol, int(mins_left))

        # 2. Combined signal: indicator + CVD + microstructure (via assess_edge)
        signal_p_up = 0.0
        signal_source = "bridge"
        try:
            from polymarket.strategy.edge_finder import assess_edge
            edge_result = assess_edge(mkt)
            if edge_result is not None:
                signal_p_up = edge_result.ai_probability or 0.0
                signal_source = edge_result.signal_source or "combined"
                logger.info("Signal P(Up)=%.3f [%s] for %s",
                            signal_p_up, signal_source, cid[:8])
        except Exception as e:
            logger.debug("Signal pipeline failed: %s", e)

        # 3. Order book imbalance (short-term, highest priority)
        ob_adjustment = 0.0
        if client and hasattr(client, "get_order_book") and not dry_run:
            try:
                up_book = client.get_order_book(wl["up_tok"])
                bid_vol = sum(b["size"] for b in up_book.get("bids", []))
                ask_vol = sum(a["size"] for a in up_book.get("asks", []))
                if bid_vol + ask_vol > 0:
                    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                    ob_adjustment = imbalance * 0.05  # ±5% max adjustment
                    logger.info("OB imbalance=%.3f (bid=%.0f ask=%.0f) → adj=%.3f",
                                imbalance, bid_vol, ask_vol, ob_adjustment)
            except Exception as e:
                logger.debug("OB fetch failed: %s", e)

        # Blend signals: signal pipeline > bridge, with OB adjustment
        if signal_p_up > 0:
            # Check for direction conflict: signal says one way, bridge says opposite
            signal_up = signal_p_up > 0.50
            bridge_up = bridge_p_up > 0.50
            if signal_up != bridge_up and abs(signal_p_up - 0.50) > 0.03:
                logger.info("SKIP %s: signal/bridge CONFLICT (signal=%.3f %s, bridge=%.3f %s)",
                            cid[:8], signal_p_up, "UP" if signal_up else "DN",
                            bridge_p_up, "UP" if bridge_up else "DN")
                continue  # Keep in watchlist — might resolve later

            # No conflict — blend
            fair = signal_p_up * 0.70 + bridge_p_up * 0.30 + ob_adjustment
        else:
            # Bridge only + OB
            fair = bridge_p_up + ob_adjustment
        fair = max(0.05, min(0.95, fair))

        # Time cutoff: don't enter with < 5 min remaining (not enough time)
        if now_ms > wl["end_ms"] - 300_000:
            logger.info("SKIP %s: < 5 min remaining, too late to enter", cid[:8])
            del state["watchlist"][cid]
            continue

        # Market midpoint sanity: if Polymarket mid for our side < $0.35,
        # market strongly disagrees with our direction → skip
        if client and hasattr(client, "get_midpoint") and not dry_run:
            _dir_tok = wl["up_tok"] if fair > 0.50 else wl["dn_tok"]
            _mid = _poly_midpoint(client, _dir_tok)
            if 0 < _mid < 0.38:
                logger.info("SKIP %s: market mid=%.3f < 0.38 → market disagrees with our direction",
                            cid[:8], _mid)
                continue  # keep in watchlist, might recover

        # 5% bankroll cap, phased entry across tranches
        bankroll = state.get("bankroll", 100.0)
        n_tranches = calc_tranches(bankroll, config)

        orders = plan_opening(mkt, fair, config, bankroll=bankroll,
                              tranche=0, total_tranches=n_tranches,
                              risk_mode=risk_mode)
        if not orders:
            del state["watchlist"][cid]
            continue

        results = _execute(orders, client)
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
                apply_fill(ms, r["outcome"], "BUY", r["price"], r["size"])
                _bump_fill(state, "filled")
                logger.info("INSTANT FILL %s %s: %.1f @ $%.3f",
                            cid[:8], r["outcome"], r["size"], r["price"])
            else:
                pending.append(r)

        mkt_dict = _to_dict(ms)
        mkt_dict["pending_orders"] = pending
        mkt_dict["fills_confirmed"] = len(pending) == 0
        mkt_dict["entry_price"] = _coin_price
        mkt_dict["entry_ts"] = int(time.time())
        mkt_dict["tranches_done"] = 1
        mkt_dict["tranches_total"] = n_tranches
        mkt_dict["original_dir"] = "UP" if fair > 0.50 else "DOWN"
        state["markets"][cid] = mkt_dict
        del state["watchlist"][cid]
        active += 1

        t_str = f" T1/{n_tranches}" if n_tranches > 1 else ""
        filled_str = f"UP={ms.up_shares:.0f} DN={ms.down_shares:.0f}"
        pending_str = ",".join(r["outcome"] for r in pending)
        cost_str = f"${ms.entry_cost:.2f}" if ms.entry_cost > 0 else "$0"
        print(f"  OPEN {cid[:8]} | {filled_str} | pend: {pending_str or '-'} | {cost_str}{t_str}")

    # ── Phased entry: add tranches to existing markets ──
    if is_heavy:
        for cid, mkt_d in list(state["markets"].items()):
            if mkt_d["phase"] != "OPEN":
                continue
            t_done = mkt_d.get("tranches_done", 1)
            t_total = mkt_d.get("tranches_total", 1)
            if t_done >= t_total:
                continue
            # Check timing: at least tranche_interval since entry
            entry_ts = mkt_d.get("entry_ts", 0)
            if time.time() - entry_ts < config.tranche_interval_s * t_done:
                continue
            # Check: enough time left (>3 min before window end)
            end_ms = mkt_d.get("window_end_ms", 0)
            if end_ms > 0 and now_ms > end_ms - 180_000:
                continue

            # Re-evaluate fair price for this tranche
            _t2 = mkt_d.get("title", "").lower()
            _s2 = "ETHUSDT" if "ethereum" in _t2 else "BTCUSDT"
            _p2 = _price(_s2)
            _o2 = mkt_d.get("btc_open_price", _p2)
            _v2 = _vol_1m(_s2)
            _ml2 = max(1, (end_ms - now_ms) / 60_000)
            fair2 = compute_fair_up(_p2, _o2, _v2, int(_ml2))

            # Market midpoint sanity for tranches
            if client and hasattr(client, "get_midpoint") and not dry_run:
                orig_dir_tok = mkt_d.get("up_token_id", "") if mkt_d.get("original_dir") == "UP" else mkt_d.get("down_token_id", "")
                _t_mid = _poly_midpoint(client, orig_dir_tok) if orig_dir_tok else 0
                if 0 < _t_mid < 0.38:
                    logger.info("ABORT tranche %s: market mid=%.3f < 0.38 → market says we're wrong",
                                cid[:8], _t_mid)
                    mkt_d["tranches_done"] = t_total
                    continue

            # Keep original direction — only abort if REVERSED
            orig_dir = mkt_d.get("original_dir", "UP")
            if orig_dir == "UP" and fair2 < 0.45:
                logger.info("ABORT tranche %s: direction REVERSED (fair=%.3f, was UP)",
                            cid[:8], fair2)
                mkt_d["tranches_done"] = t_total
                continue
            elif orig_dir == "DOWN" and fair2 > 0.55:
                logger.info("ABORT tranche %s: direction REVERSED (fair=%.3f, was DOWN)",
                            cid[:8], fair2)
                mkt_d["tranches_done"] = t_total
                continue

            # Use original direction with current price (buy the dip)
            # Override fair to force original direction
            if orig_dir == "UP" and fair2 < 0.50:
                fair2 = max(fair2, 0.50 + 0.001)  # nudge to keep UP direction
            elif orig_dir == "DOWN" and fair2 > 0.50:
                fair2 = min(fair2, 0.50 - 0.001)

            mkt2 = PolyMarket(
                condition_id=cid, title=mkt_d.get("title", ""),
                category="crypto_15m",
                yes_token_id=mkt_d.get("up_token_id", ""),
                no_token_id=mkt_d.get("down_token_id", ""),
                liquidity=15000)
            bankroll2 = state.get("bankroll", 100.0)
            orders2 = plan_opening(mkt2, fair2, config, bankroll=bankroll2,
                                   tranche=t_done, total_tranches=t_total)
            if not orders2:
                mkt_d["tranches_done"] = t_total
                continue

            results2 = _execute(orders2, client)
            for r in results2:
                if not r.get("submitted"):
                    continue
                _bump_fill(state, "submitted")
                status = r.get("status", "")
                if status == "matched":
                    _bump_fill(state, "filled")
                    outcome = r["outcome"]
                    price = r["price"]
                    size = r["size"]
                    if outcome == "UP":
                        old = mkt_d["up_shares"] * mkt_d["up_avg_price"]
                        mkt_d["up_shares"] += size
                        mkt_d["up_avg_price"] = (old + size * price) / mkt_d["up_shares"] if mkt_d["up_shares"] > 0 else 0
                    elif outcome == "DOWN":
                        old = mkt_d["down_shares"] * mkt_d["down_avg_price"]
                        mkt_d["down_shares"] += size
                        mkt_d["down_avg_price"] = (old + size * price) / mkt_d["down_shares"] if mkt_d["down_shares"] > 0 else 0
                    mkt_d["entry_cost"] += size * price
                else:
                    mkt_d.setdefault("pending_orders", []).append(r)
                    mkt_d["fills_confirmed"] = False

            mkt_d["tranches_done"] = t_done + 1
            logger.info("TRANCHE %d/%d %s | cost=$%.2f",
                        t_done + 1, t_total, cid[:8], mkt_d["entry_cost"])

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

    # Cancel defense: 3 triggers for unfilled orders
    if client and hasattr(client, "client") and not dry_run:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
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

            # Trigger 1: 2 min before window end → cancel ALL pending
            if end_ms > 0 and now_ms > end_ms - 120_000:
                to_cancel = list(pending)
                reason = "window_end"

            # Trigger 2: spot moved ADVERSELY >0.3% since entry → cancel DIRECTIONAL
            # 0.05% was too tight — BTC moves $42 in seconds, cancelled 10/11 orders
            # 0.3% BTC (~$250), 0.5% ETH (~$10) — coin-specific thresholds
            # Only cancel on ADVERSE move (against our direction), not favorable
            _spot_thresh = 0.005 if _s == "ETHUSDT" else 0.003
            if not to_cancel and entry_price > 0:
                current = _btc_price() if _s == "BTCUSDT" else _price(_s)
                if current > 0:
                    signed_move = (current - entry_price) / entry_price
                    _dir = mkt.get("original_dir", "UP")
                    # Adverse = price went opposite to our bet direction
                    is_adverse = (signed_move < 0 and _dir == "UP") or (signed_move > 0 and _dir == "DOWN")
                    if is_adverse and abs(signed_move) > _spot_thresh:
                        to_cancel = _find_directional_orders(pending)
                        if to_cancel:
                            reason = f"adverse_move_{signed_move:+.4f}"

            # Trigger 3: TTL 5min → cancel DIRECTIONAL only (independent check)
            # 60s was too short — maker orders need time on book to fill
            if not to_cancel and entry_ts > 0 and now_s - entry_ts > 300:
                to_cancel = _find_directional_orders(pending)
                if to_cancel:
                    reason = "ttl_5m"

            actually_cancelled = []
            for po in to_cancel:
                oid = po.get("order_id", "")
                if oid:
                    try:
                        client.client.cancel(order_id=oid)
                        logger.info("CANCEL %s %s [%s]", cid[:8], po["outcome"], reason)
                        actually_cancelled.append(po)
                    except Exception as e:
                        logger.warning("Cancel FAILED %s %s: %s — keeping in pending",
                                       cid[:8], po["outcome"], e)

            # Only remove successfully cancelled orders from pending
            if actually_cancelled:
                _bump_fill(state, "cancelled", len(actually_cancelled))
                mkt["pending_orders"] = [p for p in pending if p not in actually_cancelled]
                if not mkt["pending_orders"]:
                    mkt["fills_confirmed"] = True

    # Check fills (submitted → actually filled?)
    if not dry_run:
        _check_fills(state, client)

    # Resolutions
    _check_resolutions(state)

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

    print(f"  MODE: {'DRY-RUN' if dry_run else 'LIVE'}")

    gamma = GammaClient()
    client = None
    if not dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            client = PolymarketClient(dry_run=False)
            print("  CLOB: connected")
            # Startup safety: cancel ALL existing orders (orphan protection)
            try:
                existing = client.get_orders()
                if existing:
                    cancelled = 0
                    for o in existing:
                        oid = o.get("id", "")
                        if oid:
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

    if dry_run and client is None:
        class _Mock:
            def buy_shares(self, tid, amt, price=0):
                logger.info("DRY BUY %s $%.2f @ %.3f", tid[:10], amt, price)
                return {"dry_run": True}
        client = _Mock()

    state = _load()
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
        state = run_cycle(state, gamma, client, config, dry_run)
        _save(state)
        _status(state)
    else:
        print(f"  Loop: {_CYCLE_S}s")
        try:
            while True:
                try:
                    state = run_cycle(state, gamma, client, config, dry_run)
                    _save(state)
                except Exception as e:
                    logger.error("Cycle error: %s", e, exc_info=True)
                time.sleep(_CYCLE_S)
        except KeyboardInterrupt:
            print("\n  Shutting down...")
            # Cancel all open orders on CLOB (prevent orphans)
            if client and hasattr(client, "get_orders") and not dry_run:
                try:
                    remaining = client.get_orders()
                    for o in (remaining or []):
                        oid = o.get("id", "")
                        if oid:
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


if __name__ == "__main__":
    main()
