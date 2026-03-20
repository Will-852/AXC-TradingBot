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
_COIN_SLUGS = {"BTC": "bitcoin", "ETH": "ethereum"}
_COIN_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

_running = True


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
#  Market Data
# ═══════════════════════════════════════

_price_cache: dict = {}  # {coin: (ts, price)}


def _btc_price(coin: str = "BTC") -> float:
    """Get latest price with 3s cache."""
    now = time.time()
    if coin in _price_cache and now - _price_cache[coin][0] < 3:
        return _price_cache[coin][1]
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
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
    data = _get_json(f"https://clob.polymarket.com/midpoint?token_id={token_id}")
    if data:
        try:
            return float(data["mid"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _poly_ob(token_id: str) -> OBState:
    """Fetch OB and return OBState for conviction engine."""
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

_BLACK_SWAN_MID = 0.95   # sell 90% at 95¢+ → lock profit, keep 10% free roll
_BLACK_SWAN_SELL_PCT = 0.90  # sell 90%, keep 10% as free upside


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
        sell_price = round(max(0.01, mid * 0.98), 2)  # 2% slippage
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
    """Check all open positions — sell if mid ≥ 94¢. Runs every cycle."""
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
                              reason="profit_lock_93pct", known_mid=mid,
                              sell_pct=_BLACK_SWAN_SELL_PCT)
                # Greed hedge: buy opposite side min 5 shares at MARKET price (speed > price)
                # Must execute instantly — market can reverse in seconds.
                if sold:
                    opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                    opp_side = "DOWN" if side == "UP" else "UP"
                    opp_mid = _poly_midpoint(opp_tok) if opp_tok else None
                    # Aggressive limit = pseudo market order: mid + 50% overpay
                    hedge_price = round(max(0.01, (opp_mid or 0.06) * 1.50), 2)
                    if opp_tok and hedge_price < 0.15:  # cap: don't pay more than 15¢
                        try:
                            hedge_cost = round(5 * hedge_price, 2)
                            client.buy_shares(opp_tok, hedge_cost, price=hedge_price)
                            logger.info("HEDGE %s %s: 5 shares @ $%.2f ($%.2f) — market order",
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
        for coin in ("BTC", "ETH"):
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
    for cid, mkt in state["markets"].items():
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
                else:
                    _bump_fill(state, "cancelled")
                    _log_order("cancelled_external", po.get("order_id", ""), cid,
                               outcome=po.get("outcome", ""))

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
                mkt["pending_orders"] = still_open
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
        sym = "ETHUSDT" if "ethereum" in title else "BTCUSDT"
        data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1h&startTime={start_ms}&limit=1")
        if not data:
            continue

        btc_o, btc_c = float(data[0][1]), float(data[0][4])
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = _from_dict(md)
        pnl = resolve_market(ms, result)
        state["markets"][cid] = _to_dict(ms)
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
        except Exception as e:
            logger.warning("Discovery failed: %s", e)
        last_scan = now

    # ── Refresh vol (every heavy cycle) ──
    cached_vol = _vol_1m("BTC")

    # ── Refresh bankroll ──
    if client and hasattr(client, "get_usdc_balance") and not dry_run:
        try:
            state["bankroll"] = client.get_usdc_balance()
        except Exception:
            pass

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

        # ── THE CORE: conviction_signal() ──
        sig = conviction_signal(
            t_elapsed=t_elapsed,
            btc_current=current_price,
            btc_open=btc_open,
            vol_1m=cached_vol,
            ob=ob,
            config=config,
            bankroll=state["bankroll"],
            budget_remaining_frac=budget_remaining_frac,
            current_position=current_position,
        )

        # ── Act on signal ──
        if sig.action == "ENTER" or sig.action == "ADD":
            # Mid sanity check: market must somewhat agree with our direction
            our_tok = up_tok if sig.direction == "UP" else dn_tok
            market_mid = _poly_midpoint(our_tok)
            if market_mid is not None and market_mid < config.min_market_mid:
                logger.debug("SKIP %s %s: market mid $%.2f < $%.2f (market disagrees)",
                             coin, sig.direction, market_mid, config.min_market_mid)
                continue

            # Determine token and size
            token_id = our_tok

            size_usd = sig.size_fraction * state["bankroll"]
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
                    _bump_fill(state, "filled")

                logger.info("  %s %s %s | conv=%.2f fair=%.3f entry=$%.2f size=$%.2f | %s",
                            sig.action, coin, sig.direction, sig.conviction,
                            sig.fair_up, sig.entry_price, size_usd, sig.reason)

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
    logger.info("1H bot stopped.")


if __name__ == "__main__":
    main()
