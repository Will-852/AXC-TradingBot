#!/usr/bin/env python3
"""
W4-style 5M Both-Sides Momentum Bot
Replicates Wallet 4's 5M execution: fast entry, extreme lean, hold to resolution.
Independent from run_mm_live.py (15M) -- runs as separate process.

W4 5M Playbook (from reverse engineering):
- Entry: T+15s after window open (fast, not waiting for momentum)
- Lean: 12.5:1 ratio (almost all-in on one side, tiny hedge)
- Direction: momentum following -- lean toward the side market prices as likely
- Pricing: sweep book (buy at ask, not bid) -- prioritize speed over price
- Combined: ~$1.00 (NOT arb -- directional conviction)
- Active: ~162s continuous fills
- Hold to resolution, zero management
- Per-coin independent config

Usage:
  PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --dry-run --bet-pct 0.02
  PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --live --w4-live --bet-pct 0.02
  PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --status
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import (
    MMMarketState, PlannedOrder, resolve_market, apply_fill,
)
from polymarket.exchange.gamma_client import GammaClient

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
#  Graceful Shutdown
# ═══════════════════════════════════════

_running = True
_ws_binance = None   # BinancePriceFeed instance
_ws_poly = None      # PolymarketBookFeed instance


def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s", signum)
    _running = False


_signal.signal(_signal.SIGINT, _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


# ═══════════════════════════════════════
#  Paths (independent from 15M/1H)
# ═══════════════════════════════════════

_HKT = timezone(timedelta(hours=8))
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state_5m.json")
_TRADE_LOG = os.path.join(_LOG_DIR, "mm_trades_5m.jsonl")
_ORDER_LOG = os.path.join(_LOG_DIR, "mm_order_log_5m.jsonl")
_W4_LOG = os.path.join(_LOG_DIR, "mm_w4_5m.jsonl")

_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE = "https://api.binance.com/api/v3"
_BINANCE_FUTURES = "https://fapi.binance.com"


# ═══════════════════════════════════════
#  Timing Constants
# ═══════════════════════════════════════

_WINDOW_S = 300           # 5M = 300 seconds
_CYCLE_S = 5              # 5s main loop
_HEAVY_INTERVAL_S = 5     # heavy ops every 5s (v4 port, was 10s)
_SCAN_S = 60              # discover every 60s (5M windows every 300s)
_CANCEL_BEFORE_END_S = 30 # cancel 30s before window end (not 120s like 15M)
_RESOLUTION_DELAY_MS = 60_000  # wait 60s after window end before resolving

# Total loss fuse: switch to dry-run if exceeded
_TOTAL_LOSS_FUSE_PCT = 0.20  # 20% of initial bankroll

# Bankroll fraction: 5M uses 30% of wallet balance
# (15M uses 60%, 1H uses 10% -- don't over-commit across bots)
_BANKROLL_FRACTION = 0.30


# ═══════════════════════════════════════
#  Per-Coin Config
# ═══════════════════════════════════════

@dataclass
class CoinConfig:
    """Per-coin parameters. Each coin can be tuned independently."""
    live: bool = False          # True = execute orders, False = paper only
    delay_s: int = 15           # seconds after window open before entry
    threshold_bps: int = 5      # minimum |log_return| to trigger signal
    lean_ratio: float = 12.5    # lean:hedge ratio (W4 avg = 12.5:1)
    contrarian: bool = False    # True = bet AGAINST momentum (SOL pattern)
    symbol: str = "BTCUSDT"     # Binance symbol
    slug_prefix: str = "btc"    # Polymarket slug prefix
    min_order_size: float = 5.0 # CLOB minimum shares per order


# v4 port: R=1.0 pure arb (lean killed). All dry-run.
COIN_CONFIG = {
    "btc": CoinConfig(live=False, symbol="BTCUSDT", slug_prefix="btc",
                      delay_s=15, threshold_bps=5, lean_ratio=1.0),
    "eth": CoinConfig(live=False, symbol="ETHUSDT", slug_prefix="eth",
                      delay_s=15, threshold_bps=5, lean_ratio=1.0),
    "sol": CoinConfig(live=False, symbol="SOLUSDT", slug_prefix="sol",
                      delay_s=15, threshold_bps=5, lean_ratio=1.0),
    "xrp": CoinConfig(live=False, symbol="XRPUSDT", slug_prefix="xrp",
                      delay_s=15, threshold_bps=5, lean_ratio=1.0),
}

# Binance symbol map for price lookups (matches 1H bot pattern)
_COIN_SYMBOLS = {c: cfg.symbol for c, cfg in COIN_CONFIG.items()}


# ═══════════════════════════════════════
#  Telegram Alerts (critical events only)
# ═══════════════════════════════════════

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


def _tg_alert(msg: str):
    """Send alert via Telegram. Non-blocking, errors silenced."""
    if not _TG_NEWS_TOKEN or not _TG_CHAT_ID:
        return
    try:
        data = json.dumps({"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_TG_NEWS_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.debug("TG alert failed: %s", e)


# ═══════════════════════════════════════
#  HTTP Helper
# ═══════════════════════════════════════

def _get_json(url: str, timeout: int = 10):
    """Fetch JSON from URL. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-5M/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug("HTTP fail %s: %s", url[:80], e)
        return None


# ═══════════════════════════════════════
#  Market Data Layer
# ═══════════════════════════════════════

_price_cache: dict = {}   # {symbol: (ts, price)}
_vol_cache: dict = {}     # {symbol: (ts, vol)}
_open_cache: dict = {}    # {f"{symbol}_{ms}": price} -- permanent cache


def _coin_price(coin: str) -> float:
    """Get latest price with 3s cache. WS first, REST fallback."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now = time.time()
    if sym in _price_cache and now - _price_cache[sym][0] < 3:
        return _price_cache[sym][1]
    # WebSocket path
    if _ws_binance:
        ws_price = _ws_binance.get_price(sym)
        if ws_price:
            _price_cache[sym] = (now, ws_price)
            return ws_price
    # REST fallback
    data = _get_json(f"{_BINANCE}/ticker/price?symbol={sym}")
    if data:
        p = float(data["price"])
        _price_cache[sym] = (now, p)
        return p
    return _price_cache.get(sym, (0, 0))[1]


def _open_at(start_ms: int, coin: str) -> float:
    """Price at a specific timestamp. Cached permanently (historical)."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    key = f"{sym}_{start_ms}"
    if key in _open_cache:
        return _open_cache[key]
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m&startTime={start_ms}&limit=1")
    if data and isinstance(data, list) and data:
        price = float(data[0][1])  # open price of 1m candle
        _open_cache[key] = price
        return price
    return 0.0


def _vol_1m(coin: str) -> float:
    """Per-minute volatility from Binance 1m klines (120 candles). Cached 60s."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    now = time.time()
    if sym in _vol_cache and now - _vol_cache[sym][0] < 60:
        return _vol_cache[sym][1]
    data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m&limit=120")
    if not data or len(data) < 20:
        return 0.00077  # fallback: ~50% annual BTC vol
    closes = [float(k[4]) for k in data]
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 10:
        return 0.00077
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
    vol = max(0.0001, vol)
    _vol_cache[sym] = (now, vol)
    return vol


def _poly_midpoint(token_id: str) -> float | None:
    """Polymarket midpoint for a token. WS first, REST fallback."""
    if _ws_poly:
        ws_mid = _ws_poly.get_midpoint(token_id)
        if ws_mid is not None:
            return ws_mid
    data = _get_json(f"https://clob.polymarket.com/midpoint?token_id={token_id}")
    if data:
        try:
            return float(data["mid"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


# ═══════════════════════════════════════
#  W4 Momentum Signal
# ═══════════════════════════════════════

def _w4_signal(window_start_ms: int, coin: str,
               cfg: CoinConfig) -> tuple[str, float, float]:
    """W4 momentum signal: log return from window open to now.

    Returns (direction, magnitude_bps, log_return).
    direction: 'UP', 'DOWN', 'WAIT' (too early), 'SKIP' (below threshold).

    Key difference from 15M: T+15s delay (not T+300s).
    """
    now_ms = int(time.time() * 1000)
    elapsed_s = (now_ms - window_start_ms) / 1000
    # 🔴 2CHECK: at T+15s, BTC may have moved <1bps -- signal may rarely fire.
    # Fallback: if still SKIP at T+60s, we miss this window. Acceptable for 5M cadence.
    if elapsed_s < cfg.delay_s:
        return "WAIT", 0.0, 0.0

    p0 = _open_at(window_start_ms, coin)
    if p0 <= 0:
        return "SKIP", 0.0, 0.0

    p_now = _coin_price(coin)
    if p_now <= 0:
        return "SKIP", 0.0, 0.0

    log_ret = math.log(p_now / p0)
    mag_bps = abs(log_ret) * 10000

    if mag_bps < cfg.threshold_bps:
        return "SKIP", mag_bps, log_ret

    direction = "UP" if log_ret > 0 else "DOWN"

    # Contrarian mode: flip direction (SOL pattern)
    if cfg.contrarian:
        direction = "DOWN" if direction == "UP" else "UP"

    return direction, mag_bps, log_ret


# ═══════════════════════════════════════
#  5M Slug Discovery
# ═══════════════════════════════════════

def _discover_5m(gamma: GammaClient) -> list[dict]:
    """Find 5M markets for current + next 4 windows via slug.

    Slug format: {coin}-updown-5m-{unix_timestamp}
    Windows: every 300s (5 min), 24/7 continuous.

    Returns list of dicts: {cid, title, coin, slug, up_tok, dn_tok, start_ms, end_ms}
    """
    # 🔴 2CHECK: slug calculation must match Polymarket's actual format.
    # Confirmed from w4_signal_detection.py:56 and analysis data:
    #   btc-updown-5m-1774276800  -> start=1774276800, duration=5m
    results = []
    now_s = int(time.time())

    for i in range(5):
        # Floor to 5-min boundary, then offset by i windows
        window_start = (now_s // _WINDOW_S) * _WINDOW_S + i * _WINDOW_S
        window_end = window_start + _WINDOW_S

        # Skip windows that ended > 120s ago
        if now_s > window_end + 120:
            continue

        for coin, cfg in COIN_CONFIG.items():
            slug = f"{cfg.slug_prefix}-updown-5m-{window_start}"
            try:
                data = _get_json(f"{_GAMMA}/markets?slug={slug}", timeout=5)
            except Exception as e:
                logger.warning("Gamma slug fetch failed %s: %s", slug, e)
                continue

            if not data or not isinstance(data, list) or not data:
                continue

            parsed = gamma.parse_market(data[0])
            cid = parsed.get("condition_id", "")
            up_tok = parsed.get("yes_token_id", "")
            dn_tok = parsed.get("no_token_id", "")

            # Sanity: outcomes must be [UP/Yes, DOWN/No]
            outcomes = parsed.get("outcomes", [])
            if outcomes and isinstance(outcomes, list) and len(outcomes) >= 2:
                if outcomes[0].lower() not in ("up", "yes"):
                    logger.error("OUTCOME SWAPPED %s: %s", slug, outcomes)
                    continue

            if cid and up_tok and dn_tok:
                results.append({
                    "cid": cid,
                    "title": parsed.get("title", ""),
                    "coin": coin,
                    "slug": slug,
                    "up_tok": up_tok,
                    "dn_tok": dn_tok,
                    "start_ms": window_start * 1000,
                    "end_ms": window_end * 1000,
                })

    return results


# ═══════════════════════════════════════
#  W4 Both-Sides Entry
# ═══════════════════════════════════════

def _w4_entry(coin: str, cfg: CoinConfig, wl: dict,
              state: dict, client, dry_run: bool,
              bankroll: float, bet_pct: float) -> list[dict] | None:
    """W4 both-sides entry: momentum lean with per-coin config.

    Returns list of order result dicts, or None if no signal/too early/already entered.

    W4 pattern:
    - Lean 12.5:1 toward momentum direction
    - Sweep book (buy at ask price for speed)
    - Combined ~$1.00 (directional conviction, NOT structural arb)
    - Hold to resolution
    """
    cid = wl["cid"]

    # 🔴 2CHECK: duplicate order prevention (same window)
    if cid in state.get("markets", {}):
        logger.debug("W4 DUP %s: already in markets, skip", cid[:8])
        return None

    # v4 port: Dead hours skip (HKT 22-06 = low liquidity)
    _hkt_hour = datetime.now(tz=_HKT).hour
    if _hkt_hour >= 22 or _hkt_hour < 6:
        return "DEAD_HOUR"

    # Signal check
    w4_dir, w4_mag, w4_ret = _w4_signal(wl["start_ms"], coin, cfg)
    if w4_dir == "WAIT":
        return None  # not yet past delay_s
    if w4_dir == "SKIP":
        elapsed_s = (time.time() * 1000 - wl["start_ms"]) / 1000
        if elapsed_s > _WINDOW_S * 0.6:
            # 60% of window passed, give up
            logger.info("W4 SKIP %s %s: mag=%.1f bps < %d bps after %.0fs",
                        coin, cid[:8], w4_mag, cfg.threshold_bps, elapsed_s)
            return "EXPIRED"  # caller removes from watchlist
        return None  # wait longer

    logger.info("W4 SIGNAL %s %s: %s %+.1f bps (ret=%+.5f)",
                coin, cid[:8], w4_dir, w4_mag, w4_ret)

    # ── OB mid pricing -- fetch real Poly OB mid for entry price ──
    # 🔴 2CHECK: W4 sweeps book = buy at ASK (taker), not BID (maker)
    # For Phase 1 (paper), use mid + 1c as proxy for ask price.
    _up_mid = _poly_midpoint(wl["up_tok"])
    _dn_mid = _poly_midpoint(wl["dn_tok"])

    # Fallback: use 0.50 if OB unavailable (common in fresh windows)
    if not _up_mid or _up_mid <= 0.01 or _up_mid >= 0.99:
        _up_mid = 0.50
    if not _dn_mid or _dn_mid <= 0.01 or _dn_mid >= 0.99:
        _dn_mid = 0.50

    # 🔴 2CHECK: Sweep pricing = buy at ask (mid + 1 tick).
    # W4 uses aggressive fills, not passive limit orders.
    # Taker fee ~1.5% at p=0.50. At 12.5:1 lean, if wrong = big loss.
    _TICK = 0.01
    _up_ask = round(min(0.95, _up_mid + _TICK), 2)
    _dn_ask = round(min(0.95, _dn_mid + _TICK), 2)
    _bs_combined = round(_up_ask + _dn_ask, 4)

    # v4: combined gate — arb requires combined < $1.00. $0.99 = thin margin.
    if _bs_combined >= 0.99:
        logger.info("W4 SKIP %s %s: combined $%.4f >= $0.99 (arb spread too thin)",
                     coin, cid[:8], _bs_combined)
        return "ABORT"

    # ── Sizing: W4 lean = SHARE COUNT ratio (not budget ratio) ──
    # FIX: budget fraction / price → cheap side gets MORE shares. Must use share fraction.
    _budget = bankroll * bet_pct
    _avg_price = (_up_ask + _dn_ask) / 2
    _total_shares = max(10, _budget / _avg_price)

    _lean_share_frac = cfg.lean_ratio / (cfg.lean_ratio + 1)  # 0.926 at 12.5:1
    _hedge_share_frac = 1.0 / (cfg.lean_ratio + 1)            # 0.074 at 12.5:1

    if w4_dir == "UP":
        _up_shares = max(cfg.min_order_size, round(_total_shares * _lean_share_frac, 1))
        _dn_shares = max(cfg.min_order_size, round(_total_shares * _hedge_share_frac, 1))
    else:
        _up_shares = max(cfg.min_order_size, round(_total_shares * _hedge_share_frac, 1))
        _dn_shares = max(cfg.min_order_size, round(_total_shares * _lean_share_frac, 1))

    # v4 port: Budget cap — min_order_size floors can inflate spend beyond budget
    _est_cost = _up_shares * _up_ask + _dn_shares * _dn_ask
    if _est_cost > _budget * 1.5 and _budget > 0:
        _scale = _budget / _est_cost
        _up_shares = max(1, round(_up_shares * _scale, 1))
        _dn_shares = max(1, round(_dn_shares * _scale, 1))
        logger.info("W4 BUDGET CAP %s: est $%.2f > budget $%.2f → scaled",
                     coin, _est_cost, _budget)

    # Actual cost check
    _actual_cost = _up_shares * _up_ask + _dn_shares * _dn_ask
    if _actual_cost > bankroll * 0.10:
        logger.warning("W4 SIZE CAP %s: $%.2f > 10%% bankroll $%.2f",
                        coin, _actual_cost, bankroll)
        # Scale down to 10%
        _scale = (bankroll * 0.10) / _actual_cost
        _up_shares = max(cfg.min_order_size, round(_up_shares * _scale, 1))
        _dn_shares = max(cfg.min_order_size, round(_dn_shares * _scale, 1))

    # ── Determine live vs paper ──
    is_live = cfg.live and not dry_run

    # ── Log W4 entry (always, regardless of live/paper) ──
    _bs_entry = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": "w4_entry",
        "cid": cid[:8], "coin": coin,
        "lean_dir": w4_dir, "lean_ratio": cfg.lean_ratio,
        "contrarian": cfg.contrarian,
        "w4_mag_bps": round(w4_mag, 1),
        "w4_ret": round(w4_ret, 6),
        "up_mid": round(_up_mid, 4), "dn_mid": round(_dn_mid, 4),
        "up_ask": _up_ask, "dn_ask": _dn_ask,
        "combined": _bs_combined,
        "up_shares": _up_shares, "dn_shares": _dn_shares,
        "budget": round(_budget, 2), "bankroll": round(bankroll, 2),
        "live": is_live,
    }
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_W4_LOG, "a") as f:
            f.write(json.dumps(_bs_entry) + "\n")
    except Exception:
        pass

    _mode = "LIVE" if is_live else "PAPER"
    logger.info("W4 %s %s %s: lean=%s %+.1fbps | UP@$%.2f*%.0f + DN@$%.2f*%.0f = $%.3f",
                _mode, coin, cid[:8], w4_dir, w4_mag,
                _up_ask, _up_shares, _dn_ask, _dn_shares, _bs_combined)

    # ── Submit orders ──
    orders = [
        {"token_id": wl["up_tok"], "outcome": "UP",
         "price": _up_ask, "size": _up_shares},
        {"token_id": wl["dn_tok"], "outcome": "DOWN",
         "price": _dn_ask, "size": _dn_shares},
    ]

    results = []
    for o in orders:
        result = _execute_order(
            client, o["token_id"], o["outcome"],
            o["price"], o["size"], dry_run=(not is_live),
            coin=coin, cid=cid)
        results.append(result)

    return results


# ═══════════════════════════════════════
#  Order Execution
# ═══════════════════════════════════════

def _execute_order(client, token_id: str, outcome: str,
                   price: float, size: float, dry_run: bool,
                   coin: str = "", cid: str = "") -> dict:
    """Submit a single limit order. Returns order result dict.

    W4 style: uses aggressive limit (at ask) = pseudo taker.
    """
    amount = round(size * price, 2)
    if size < 5:
        min_cost = 5 * price
        if min_cost <= amount * 2:
            amount = min_cost
            size = 5
        else:
            logger.debug("Skip order: %.1f shares < 5 minimum", size)
            return {"outcome": outcome, "submitted": False, "reason": "below_min"}

    try:
        # 🔴 CRITICAL FIX: dry_run gate — MUST block real orders when paper mode
        if dry_run:
            logger.info("DRY 5M %s %s: %.1f shares @ $%.3f ($%.2f)",
                        coin.upper(), outcome, size, price, amount)
            return {
                "outcome": outcome, "submitted": True, "dry_run": True,
                "price": price, "size": size, "status": "matched",
                "order_id": f"5m_dry_{token_id[:8]}_{int(time.time())}",
            }

        r = client.buy_shares(token_id, amount, price=price)
        order_id = ""
        status = ""
        if isinstance(r, dict):
            order_id = r.get("orderID", r.get("id", ""))
            status = r.get("status", "")

        # Reject if CLOB returns no order_id
        if not order_id and status != "matched":
            logger.warning("ORDER REJECTED %s: no order_id (status=%s)", outcome, status)
            _log_order("rejected", "", cid, outcome=outcome, price=price, size=size)
            return {"outcome": outcome, "submitted": False, "reason": "rejected_no_id"}

        logger.info("ORDER %s %s %.0f shares @ $%.3f ($%.2f) -> %s",
                    coin, outcome, size, price, amount, status or order_id[:12])
        _log_order("submit", order_id, cid,
                   outcome=outcome, price=price, size=size,
                   status=status, coin=coin)
        return {"outcome": outcome, "price": price, "size": size,
                "token_id": token_id, "order_id": order_id,
                "status": status, "submitted": True,
                "order_ts": time.time()}
    except Exception as e:
        logger.error("ORDER FAILED %s %s: %s", coin, outcome, e)
        return {"outcome": outcome, "submitted": False, "error": str(e)}


# ═══════════════════════════════════════
#  Fill Confirmation
# ═══════════════════════════════════════

def _check_fills(state: dict, client) -> None:
    """Check which submitted orders actually filled on-chain.

    REST-based (like 1H bot). WS user feed can be added later for live.
    """
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
            # Window over: cancel remaining orders on CLOB, mark expired
            for _ep in pending:
                _oid = _ep.get("order_id", "")
                if _oid and client and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=_oid)
                        logger.info("CANCEL EXPIRED %s %s", cid[:8], _ep.get("outcome", ""))
                    except Exception:
                        pass
                _log_order("expired", _oid, cid, outcome=_ep.get("outcome", ""))
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
                    _bump_fill(state, "cancelled")
                    _log_order("rejected", "", cid, outcome=po.get("outcome", ""))
                else:
                    _bump_fill(state, "cancelled")
                    _log_order("cancelled_external", oid, cid,
                               outcome=po.get("outcome", ""))

            # Always update pending_orders (fix: cancelled orders don't stay stuck)
            mkt["pending_orders"] = still_open

            if filled:
                for f in filled:
                    o = f["outcome"]
                    p = f["price"]
                    s = f["size"]
                    if o == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += s
                        mkt["up_avg_price"] = (old + s * p) / mkt["up_shares"]
                    elif o == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += s
                        mkt["down_avg_price"] = (old + s * p) / mkt["down_shares"]
                    mkt["entry_cost"] += s * p
                    _bump_fill(state, "filled")
                    _ttf = round(time.time() - f.get("order_ts", 0), 1) if f.get("order_ts") else 0
                    _log_order("fill", f.get("order_id", ""), cid,
                               outcome=o, price=p, size=s,
                               time_to_fill_s=_ttf)
                    logger.info("FILL %s %s: %.0f @ $%.3f ttf=%.0fs",
                                cid[:8], o, s, p, _ttf)

            if not still_open:
                mkt["fills_confirmed"] = True

        except Exception as e:
            logger.warning("Fill check %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  Cancel Defense
# ═══════════════════════════════════════

def _cancel_before_end(state: dict, client, dry_run: bool):
    """Cancel all pending orders 30s before window end.

    5M cancel is much simpler than 15M:
    - No dynamic TTL, no layer-specific cancel, no adverse spot move cancel
    - Just: cancel all open orders at T-30s
    """
    # 🔴 2CHECK: with T+15s entry, orders sit 255s. OK for fills.
    if dry_run or not client:
        return

    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN":
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms <= 0:
            continue
        tte_s = (end_ms - now_ms) / 1000
        if 0 < tte_s <= _CANCEL_BEFORE_END_S:
            pending = mkt.get("pending_orders", [])
            for po in pending:
                oid = po.get("order_id", "")
                if oid and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=oid)
                        logger.info("CANCEL T-%.0fs %s %s", tte_s, cid[:8],
                                    po.get("outcome", ""))
                    except Exception:
                        pass
            if pending:
                _bump_fill(state, "cancelled", len(pending))
                mkt["pending_orders"] = []


# ═══════════════════════════════════════
#  Resolution (Binance 5m OHLC)
# ═══════════════════════════════════════

def _check_resolutions(state: dict):
    """Resolve markets using Binance 5m kline data.

    Resolution: close >= open = UP, close < open = DOWN.
    Wait 60s after window end for Binance data to stabilize.
    """
    now_ms = int(time.time() * 1000)

    for cid, md in list(state["markets"].items()):
        if md.get("phase") == "RESOLVED":
            continue
        end_ms = md.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + _RESOLUTION_DELAY_MS:
            continue
        start_ms = md.get("window_start_ms", 0)
        if start_ms <= 0:
            continue

        # Resolution symbol: use stored coin key (not title parsing — 2check CRITICAL fix)
        _res_coin = md.get("coin", "btc")
        _res_cfg = COIN_CONFIG.get(_res_coin)
        sym = _res_cfg.symbol if _res_cfg else "BTCUSDT"

        # Fetch Binance 5m candle at window start
        data = _get_json(
            f"{_BINANCE}/klines?symbol={sym}&interval=5m"
            f"&startTime={start_ms}&limit=1")
        if not data or not isinstance(data, list) or not data:
            continue

        btc_o = float(data[0][1])   # open
        btc_c = float(data[0][4])   # close
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = _from_dict(md)
        pnl = resolve_market(ms, result)
        resolved_dict = _to_dict(ms)

        # Preserve runtime keys
        for _rk in ("pending_orders", "fills_confirmed", "w4_lean_dir",
                     "w4_combined", "w4_mag_bps"):
            if _rk in md:
                resolved_dict[_rk] = md[_rk]
        state["markets"][cid] = resolved_dict

        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] = state.get("total_markets", 0) + 1

        # Consecutive loss tracking
        if pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 8:
                cd = (datetime.now(tz=_HKT) + timedelta(hours=4)).isoformat(timespec="seconds")
                state["cooldown_until"] = cd
                logger.warning("8 consecutive losses -> COOLDOWN until %s", cd)
                _tg_alert(f"<b>5M BOT</b> 8 consecutive losses -> cooldown 4h")
        else:
            state["consecutive_losses"] = 0

        # Both-sides resolution log
        _bs_res = {
            "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
            "event": "resolution",
            "cid": cid[:8], "coin": md.get("coin", "?"),
            "result": result, "pnl": round(pnl, 4),
            "up_shares": md.get("up_shares", 0),
            "down_shares": md.get("down_shares", 0),
            "combined": md.get("w4_combined", 0),
            "lean_dir": md.get("w4_lean_dir", "?"),
            "cost": round(ms.total_cost, 2),
            "payout": round(ms.payout, 2),
            "both_filled": md.get("up_shares", 0) > 0 and md.get("down_shares", 0) > 0,
        }
        try:
            with open(_W4_LOG, "a") as f:
                f.write(json.dumps(_bs_res) + "\n")
        except Exception:
            pass

        _log_trade({
            "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
            "cid": cid, "coin": md.get("coin", "?"),
            "result": result, "pnl": round(pnl, 4),
            "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
            "total_pnl": round(state["total_pnl"], 2),
            "lean_dir": md.get("w4_lean_dir", "?"),
            "combined": md.get("w4_combined", 0),
        })

        d = "^" if result == "UP" else "v"
        print(f"  RESOLVED {cid[:8]} {md.get('coin', '?')} {d} | "
              f"PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")


# ═══════════════════════════════════════
#  State Management
# ═══════════════════════════════════════

_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}


def _bump_fill(state: dict, event: str, n: int = 1):
    """Increment fill rate counter."""
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


def _load() -> dict:
    """Load state from disk. Returns default if missing/corrupt."""
    if not os.path.exists(_STATE_PATH):
        return _default_state()
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        d.setdefault("watchlist", {})
        return d
    except Exception:
        return _default_state()


def _default_state() -> dict:
    return {
        "markets": {}, "watchlist": {},
        "daily_pnl": 0.0, "total_pnl": 0.0,
        "total_markets": 0, "bankroll": 100.0,
        "consecutive_losses": 0, "cooldown_until": "",
        "daily_pnl_date": "",
        "fill_stats": dict(_FILL_STATS_DEFAULT),
    }


def _save(state: dict):
    """Atomic write state to disk."""
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
    """Serialize MMMarketState to dict."""
    return {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}


def _from_dict(d: dict) -> MMMarketState:
    """Deserialize dict to MMMarketState."""
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def _log_trade(record: dict):
    """Append to trade log."""
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


# ═══════════════════════════════════════
#  Kill Switches + Risk
# ═══════════════════════════════════════

def _check_kill_switches(state: dict) -> bool:
    """Check daily loss + cooldown. Returns True if should stop trading."""
    now_hkt = datetime.now(tz=_HKT)

    # Cooldown check
    cooldown = state.get("cooldown_until", "")
    if cooldown:
        try:
            if now_hkt.isoformat(timespec="seconds") < cooldown:
                return True
        except ValueError:
            pass
        # Cooldown expired
        state["cooldown_until"] = ""
        state["consecutive_losses"] = 0
        logger.info("Cooldown expired, resuming trading")

    # 🔴 2CHECK: daily loss cap
    # 5M trades more frequently than 15M, so use 15% of bankroll as daily cap
    daily_loss_pct = abs(state["daily_pnl"]) / max(state["bankroll"], 1) \
        if state["daily_pnl"] < 0 else 0
    if daily_loss_pct > 0.15:
        logger.warning("DAILY LOSS %.1f%% > 15%% -> STOP", daily_loss_pct * 100)
        return True

    return False


# ═══════════════════════════════════════
#  Main Cycle
# ═══════════════════════════════════════

_last_heavy_ts: float = 0
_last_scan_ts: float = 0


def run_cycle(state: dict, gamma: GammaClient, client,
              dry_run: bool, bet_pct: float,
              cached_markets: list) -> tuple[dict, list]:
    """One cycle of the 5M bot.

    Returns (state, cached_markets).
    """
    global _last_heavy_ts, _last_scan_ts
    now = time.time()
    now_ms = int(now * 1000)
    now_hkt = datetime.now(tz=_HKT)

    # ── Daily reset ──
    today = now_hkt.strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today

    # ── Kill switches ──
    if _check_kill_switches(state):
        return state, cached_markets

    # ── Fast ops (every cycle): cancel defense, fill check, resolution ──
    _cancel_before_end(state, client, dry_run)
    _check_fills(state, client)
    _check_resolutions(state)

    # ── Heavy ops (every 10s) ──
    is_heavy = (now - _last_heavy_ts) >= _HEAVY_INTERVAL_S
    if not is_heavy:
        return state, cached_markets
    _last_heavy_ts = now

    # ── Discovery (every 60s) ──
    if (now - _last_scan_ts) >= _SCAN_S:
        try:
            discovered = _discover_5m(gamma)
            if discovered:
                # Merge into watchlist (don't overwrite existing)
                for mkt_info in discovered:
                    cid = mkt_info["cid"]
                    if cid not in state["markets"] and cid not in state.get("watchlist", {}):
                        state.setdefault("watchlist", {})[cid] = mkt_info
                        # Subscribe to WS order book feed
                        if _ws_poly:
                            _ws_poly.subscribe(
                                [mkt_info["up_tok"], mkt_info["dn_tok"]],
                                condition_id=cid)
                        logger.info("watchlist + %s %s: %s",
                                    mkt_info["coin"], cid[:8],
                                    mkt_info.get("title", mkt_info["slug"])[:40])
                logger.info("Discovered %d 5M markets, watchlist=%d",
                            len(discovered), len(state.get("watchlist", {})))
        except Exception as e:
            logger.warning("Discovery failed: %s", e)
        _last_scan_ts = now

    # ── Refresh bankroll ──
    if client and hasattr(client, "get_usdc_balance") and not dry_run:
        try:
            bal = client.get_usdc_balance()
            if bal is not None and bal > 0:
                state["bankroll"] = bal * _BANKROLL_FRACTION
        except Exception:
            pass

    # ── Refresh vol (background, for logging) ──
    for coin in COIN_CONFIG:
        _vol_1m(coin)  # populates cache

    # ── Clean expired watchlist entries ──
    for cid in list(state.get("watchlist", {}).keys()):
        wl = state["watchlist"][cid]
        end_ms = wl.get("end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            del state["watchlist"][cid]

    # ── Evaluate watchlist: W4 entry for each active market ──
    # CRITICAL FIX: concurrent position cap (prevent aggregate exposure blowup)
    _MAX_CONCURRENT = 8  # 4 coins × 2 windows max
    _open_count = sum(1 for m in state.get("markets", {}).values() if m.get("phase") == "OPEN")

    for cid in list(state.get("watchlist", {}).keys()):
        if _open_count >= _MAX_CONCURRENT:
            break  # concurrent cap reached
        wl = state["watchlist"].get(cid)
        if not wl:
            continue

        start_ms = wl.get("start_ms", 0)
        end_ms = wl.get("end_ms", 0)

        # Only process during active window
        if now_ms < start_ms or now_ms > end_ms:
            continue

        coin = wl.get("coin", "btc")
        cfg = COIN_CONFIG.get(coin)
        if not cfg:
            continue

        # Already in markets? Skip
        if cid in state.get("markets", {}):
            del state["watchlist"][cid]
            continue

        # ── W4 entry attempt ──
        result = _w4_entry(
            coin=coin, cfg=cfg, wl=wl,
            state=state, client=client,
            dry_run=dry_run, bankroll=state["bankroll"],
            bet_pct=bet_pct)

        if result is None:
            continue  # WAIT or SKIP, try again next cycle

        if result == "EXPIRED" or result == "ABORT":
            del state["watchlist"][cid]
            continue

        if isinstance(result, list):
            # Orders submitted -- create market state
            _bump_fill(state, "submitted", sum(1 for r in result if r.get("submitted")))

            # Get open price
            btc_open = _open_at(start_ms, coin)

            state["markets"][cid] = {
                "condition_id": cid,
                "title": wl.get("title", wl.get("slug", "")),
                "coin": coin,
                "up_token_id": wl["up_tok"],
                "down_token_id": wl["dn_tok"],
                "window_start_ms": start_ms,
                "window_end_ms": end_ms,
                "btc_open_price": btc_open,
                "phase": "OPEN",
                "up_shares": 0, "up_avg_price": 0,
                "down_shares": 0, "down_avg_price": 0,
                "entry_cost": 0, "payout": 0, "realized_pnl": 0,
                "pending_orders": [r for r in result if r.get("submitted")],
                "fills_confirmed": False,
                # W4-specific metadata
                "w4_lean_dir": result[0].get("outcome", "") if result else "",
                "w4_combined": 0,
                "w4_mag_bps": 0,
            }

            # Extract W4 metadata from order results
            mkt = state["markets"][cid]
            submitted = [r for r in result if r.get("submitted")]
            if len(submitted) == 2:
                p0 = submitted[0].get("price", 0)
                p1 = submitted[1].get("price", 0)
                mkt["w4_combined"] = round(p0 + p1, 4)

            # Dry-run: simulate instant fill
            for r in submitted:
                if r.get("status") == "matched":
                    o = r["outcome"]
                    s = r["size"]
                    p = r["price"]
                    if o == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += s
                        mkt["up_avg_price"] = (old + s * p) / mkt["up_shares"] if mkt["up_shares"] else p
                    else:
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += s
                        mkt["down_avg_price"] = (old + s * p) / mkt["down_shares"] if mkt["down_shares"] else p
                    mkt["entry_cost"] += s * p
                    _bump_fill(state, "filled")
                    mkt["pending_orders"] = [
                        x for x in mkt["pending_orders"]
                        if x.get("order_id") != r.get("order_id")]

            if not mkt["pending_orders"]:
                mkt["fills_confirmed"] = True

            # Remove from watchlist + increment concurrent counter
            if cid in state.get("watchlist", {}):
                del state["watchlist"][cid]
            _open_count += 1

    return state, cached_markets


# ═══════════════════════════════════════
#  Status Display
# ═══════════════════════════════════════

def _status(state: dict):
    """Print bot status summary."""
    print("\n  5M W4 MOMENTUM BOT STATUS")
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

    # Coin config
    print(f"\n  COIN CONFIG:")
    for coin, cfg in COIN_CONFIG.items():
        live_str = "LIVE" if cfg.live else "paper"
        print(f"    {coin.upper()}: {live_str} | delay={cfg.delay_s}s | "
              f"threshold={cfg.threshold_bps}bps | lean={cfg.lean_ratio}:1"
              f"{' (contrarian)' if cfg.contrarian else ''}")

    # Open positions
    open_markets = {k: v for k, v in state.get("markets", {}).items()
                    if v.get("phase") == "OPEN"}
    if open_markets:
        print(f"\n  OPEN POSITIONS ({len(open_markets)}):")
        for cid, m in open_markets.items():
            up, dn = m.get("up_shares", 0), m.get("down_shares", 0)
            cost = m.get("entry_cost", 0)
            lean = m.get("w4_lean_dir", "?")
            comb = m.get("w4_combined", 0)
            print(f"    {m.get('coin', '?')} {cid[:12]} lean={lean}")
            print(f"      UP: {up:.0f} | DN: {dn:.0f} | Cost: ${cost:.2f} | Combined: ${comb:.4f}")

    # Resolved stats
    resolved = [v for v in state.get("markets", {}).values()
                if v.get("phase") == "RESOLVED"]
    if resolved:
        wins = sum(1 for m in resolved if m.get("realized_pnl", 0) > 0)
        wr = wins / len(resolved) * 100 if resolved else 0
        print(f"\n  RESOLVED: {len(resolved)} markets | WR: {wr:.0f}%")

    # Watchlist
    wl = state.get("watchlist", {})
    if wl:
        print(f"\n  WATCHLIST: {len(wl)} pending")

    print()


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="W4-style 5M Both-Sides Momentum Bot")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Paper mode: log signals, simulate fills")
    mode.add_argument("--live", action="store_true",
                      help="Live mode: connect to CLOB (orders gated by CoinConfig.live)")
    mode.add_argument("--status", action="store_true",
                      help="Print status and exit")
    ap.add_argument("--w4-live", action="store_true",
                    help="Enable LIVE execution for coins with CoinConfig.live=True")
    ap.add_argument("--cycle", action="store_true",
                    help="Run 1 cycle, exit")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--bankroll", type=float, default=0,
                    help="Override bankroll (default: from CLOB)")
    ap.add_argument("--bet-pct", type=float, default=0.02,
                    help="Bankroll fraction per window (default: 0.02 = 2%%)")
    ap.add_argument("--coins", type=str, default="",
                    help="Override active coins, e.g. 'btc,eth'")

    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    if args.status:
        _status(_load())
        return

    dry_run = args.dry_run
    bet_pct = args.bet_pct

    # Override active coins if specified
    if args.coins:
        active_coins = set(args.coins.lower().split(","))
        for coin, cfg in COIN_CONFIG.items():
            if coin in active_coins:
                cfg.live = True
            else:
                cfg.live = False

    # Safety: --w4-live required for real execution
    if args.live and not args.w4_live:
        logger.warning("--live without --w4-live: all coins forced to paper mode")
        for cfg in COIN_CONFIG.values():
            cfg.live = False

    # ── Start Binance WebSocket price feed ──
    global _ws_binance
    try:
        from polymarket.data.ws_binance import BinancePriceFeed
        _ws_binance = BinancePriceFeed()
        _ws_binance.start()
        print("  WS PRICE: Binance bookTicker feed started")
    except Exception as e:
        logger.warning("WS price feed failed: %s -- using REST fallback", e)

    # ── Start Polymarket WebSocket order book feed ──
    global _ws_poly
    try:
        from polymarket.data.ws_polymarket import PolymarketBookFeed
        _ws_poly = PolymarketBookFeed()
        _ws_poly.start()
        print("  WS OB: Polymarket book feed started")
    except Exception as e:
        logger.warning("WS OB feed failed: %s -- using REST fallback", e)

    # ── CLOB client ──
    gamma = GammaClient()
    client = None

    if not dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            client = PolymarketClient(dry_run=False)
        except Exception as e:
            print(f"  CLOB failed: {e} -> dry-run fallback")
            dry_run = True

    if dry_run and client is None:
        class _Mock:
            """Paper trading mock client."""
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

    # ── Load state ──
    state = _load()
    if args.bankroll > 0:
        state["bankroll"] = args.bankroll
    elif dry_run:
        state.setdefault("bankroll", 138.0 * _BANKROLL_FRACTION)

    # Record initial bankroll for total loss fuse
    if not dry_run and "initial_bankroll" not in state:
        state["initial_bankroll"] = state["bankroll"]
        logger.info("Initial bankroll: $%.2f (20%% fuse = $%.2f)",
                     state["initial_bankroll"],
                     state["initial_bankroll"] * _TOTAL_LOSS_FUSE_PCT)

    os.makedirs(_LOG_DIR, exist_ok=True)

    # 🔴 v4 FIX: Startup orphan cleanup — only cancel 5M orders (not 15M/1H!)
    # Old code cancelled ALL wallet orders not in 5M state = would kill 15M bot's orders.
    # Fix: only cancel orders whose market slug contains "-5m-".
    if client and hasattr(client, "get_orders") and not dry_run:
        try:
            existing = client.get_orders()
            known_cids = set(state.get("markets", {}).keys())
            orphans = 0
            for o in (existing or []):
                oid = o.get("id", "")
                mkt_id = o.get("market", "")
                # Only cancel if (a) not in 5M state AND (b) looks like a 5M market
                # 5M condition_ids are different from 15M, but we can't check slug from order.
                # Safest: only cancel if the market IS in our known 5M cids (stale orders).
                # Skip unknown markets entirely — they belong to other bots.
                if oid and mkt_id in known_cids and state["markets"].get(mkt_id, {}).get("phase") == "DONE":
                    try:
                        client.client.cancel(order_id=oid)
                        orphans += 1
                    except Exception:
                        pass
            if orphans:
                logger.warning("STARTUP: cancelled %d stale 5M orders", orphans)
        except Exception as e:
            logger.warning("Startup orphan check failed: %s", e)

    cached_markets: list = []
    fuse_blown = False

    mode_str = "DRY-RUN" if dry_run else "LIVE"
    live_coins = [c for c, cfg in COIN_CONFIG.items() if cfg.live]
    print(f"\n  5M W4 MOMENTUM BOT -- {mode_str}")
    print(f"  Bankroll: ${state['bankroll']:.2f} (fraction={_BANKROLL_FRACTION})")
    print(f"  Bet per window: {bet_pct*100:.1f}%")
    print(f"  Live coins: {live_coins or '(all paper)'}")
    print(f"  State: {_STATE_PATH}")
    print()

    if args.cycle:
        state, cached_markets = run_cycle(
            state, gamma, client, dry_run, bet_pct, cached_markets)
        _save(state)
        _status(state)
        return

    try:
        while _running:
            try:
                state, cached_markets = run_cycle(
                    state, gamma, client, dry_run, bet_pct, cached_markets)
                _save(state)

                # ── Total loss fuse: live -> dry-run ──
                init_br = state.get("initial_bankroll", 0)
                if not fuse_blown and not dry_run and init_br > 0:
                    total_pnl = state.get("total_pnl", 0)
                    if total_pnl < -(init_br * _TOTAL_LOSS_FUSE_PCT):
                        fuse_blown = True
                        dry_run = True
                        class _MockPost:
                            def buy_shares(self, tid, amt, price=0):
                                logger.info("FUSE DRY BUY %s $%.2f @ $%.3f",
                                            tid[:10], amt, price)
                                return {"dry_run": True}
                            def get_usdc_balance(self):
                                return state.get("bankroll", 0)
                            def get_orders(self, **kw):
                                return []
                            def get_trades(self, **kw):
                                return []
                        client = _MockPost()
                        loss_pct = abs(total_pnl) / init_br * 100
                        msg = (f"<b>5M BOT FUSE BLOWN</b>\n"
                               f"Total loss: ${total_pnl:.2f} ({loss_pct:.1f}%)\n"
                               f"Switched to DRY-RUN.")
                        logger.warning("FUSE BLOWN: $%.2f (%.1f%%) -> DRY-RUN",
                                       total_pnl, loss_pct)
                        _tg_alert(msg)

            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)
            time.sleep(_CYCLE_S)
    except KeyboardInterrupt:
        pass

    _save(state)
    _status(state)
    if _ws_binance:
        _ws_binance.stop()
    if _ws_poly:
        _ws_poly.stop()
    logger.info("5M bot stopped.")


if __name__ == "__main__":
    main()
