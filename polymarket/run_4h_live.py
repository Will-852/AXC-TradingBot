#!/usr/bin/env python3
"""
run_4h_live.py — 4H Conviction Bot (Bridge + Momentum)

Strategy: Wait 60min into 4H window → compute bridge P(UP) + check momentum.
If both agree and conviction high enough → enter at cheap price → hold to resolution.

Only TWO signals (backtest-proven edge, everything else is noise):
  Bridge:    77.5% accuracy at T+1H (+25.9pp vs base)
  Momentum:  63.6% accuracy (+12.0pp vs base)

Slug: {coin}-updown-4h-{unix_ts}   |  Resolution: Chainlink
6 coins: BTC ETH SOL DOGE XRP BNB  |  6 windows/day (every 4H UTC-aligned)

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/run_4h_live.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_4h_live.py --live --bet-pct 0.03
  PYTHONPATH=.:scripts python3 polymarket/run_4h_live.py --status
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
from datetime import datetime, timedelta, timezone

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import compute_fair_up
from polymarket.exchange.gamma_client import GammaClient

logger = logging.getLogger(__name__)

_HKT = timezone(timedelta(hours=8))
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_STATE_PATH = os.path.join(_LOG_DIR, "mm_state_4h.json")


def _signal_path(coin: str) -> str:
    return os.path.join(_LOG_DIR, f"signal_tape_4h_{coin}.jsonl")


def _order_path(coin: str) -> str:
    return os.path.join(_LOG_DIR, f"mm_order_log_4h_{coin}.jsonl")


def _coin_from_title(title: str) -> str:
    t = title.lower()
    if "ethereum" in t: return "ETH"
    if "solana" in t: return "SOL"
    if "xrp" in t: return "XRP"
    return "BTC"
_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE = "https://api.binance.com/api/v3"

# ── Timing ──
_CYCLE_S = 10           # main loop: 10s
_HEAVY_INTERVAL_S = 60  # heavy ops every 60s (4H = slower pace)
_SCAN_INTERVAL_S = 600  # discovery every 10 min (4H windows are long)
_WINDOW_S = 14400       # 4H = 14400 seconds
_WINDOW_MIN = 240       # 4H = 240 minutes

# ── Entry timing: wait for momentum + bridge ──
_WAIT_MIN = 60          # wait 60 min before first entry (momentum read)
_LATE_CUTOFF_MIN = 210  # no new entries in last 30 min

# ── Pricing: relative cap (edge_calibration.py → 70% discount optimal) ──
_ENTRY_DISCOUNT = 0.70     # entry ≤ fair × 70% (was absolute $0.39)
_MIN_ENTRY_PRICE = 0.20    # floor
_MAX_ENTRY_PRICE = 0.60    # absolute ceiling (safety net)
_MIN_EDGE = 0.10           # must leave ≥10c EV per share
_MIN_FAIR_DEVIATION = 0.08 # bridge must deviate ≥8c from 0.50

# ── Sizing ──
_MAX_SIZE_FRAC = 0.03   # 3% of bankroll per window (default)
_MIN_ORDER_USD = 2.50   # Polymarket minimum

# ── Risk ──
_PROFIT_LOCK_MID = 0.96  # mid ≥ 96¢ → sell
_PROFIT_LOCK_PCT = 0.96  # sell 96% of position
_TOTAL_LOSS_FUSE_PCT = 0.22  # 22% total loss → stop live

# ── Coins ──
_COIN_SLUGS = {
    "BTC": "btc", "ETH": "eth", "SOL": "sol",
    # XRP removed: OB depth ≤$0.50 = 9 shares (2026-03-26 audit)
}
_COIN_SYMBOLS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
}
# BTC paper-only. Others observe (collect signals, no execution).
_LIVE_COINS = {"BTC", "ETH", "SOL"}  # all coins live (2/day cap protects)

_FILL_STATS_DEFAULT = {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}

_running = True

# ── TG credentials ──
_ENV_PATH = os.path.join(_AXC, "secrets", ".env")
_TG_TOKEN = ""
_TG_CHAT_ID = ""
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("TELEGRAM_NEWS_BOT_TOKEN="):
                _TG_TOKEN = _line.split("=", 1)[1]
            elif _line.startswith("TELEGRAM_CHAT_ID="):
                _TG_CHAT_ID = _line.split("=", 1)[1]


def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s", signum)
    _running = False


# ═══════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════

def _get_json(url: str, timeout: int = 8):
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    try:
        with urlopen(Request(url, headers={"User-Agent": "AXC-4H/1.0"}), timeout=timeout) as r:
            return json.loads(r.read())
    except (HTTPError, URLError, TimeoutError, OSError):
        return None


def _btc_price(coin: str = "BTC") -> float:
    """Current spot price from Binance."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    d = _get_json(f"{_BINANCE}/ticker/price?symbol={sym}")
    return float(d["price"]) if d else 0


def _binance_open(coin: str, start_ms: int) -> float:
    """Get the candle open price at a specific timestamp."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    d = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m&startTime={start_ms}&limit=1")
    if d and len(d) > 0:
        return float(d[0][1])  # open
    return 0


def _poly_midpoint(token_id: str) -> float | None:
    """Polymarket midpoint for a token (REST)."""
    d = _get_json(f"https://clob.polymarket.com/midpoint?token_id={token_id}")
    if d:
        try:
            return float(d["mid"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _vol_1m(coin: str = "BTC") -> float:
    """Per-minute log-return stdev from recent 1m klines."""
    sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
    d = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1m&limit=120")
    if not d or len(d) < 30:
        return 0.001  # fallback
    closes = [float(k[4]) for k in d]
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
                if closes[i] > 0 and closes[i - 1] > 0]
    if len(log_rets) < 10:
        return 0.001
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
    return max(0.0001, math.sqrt(var))


def _tg_alert(msg: str):
    """Send Telegram alert (fire-and-forget)."""
    if not _TG_TOKEN or not _TG_CHAT_ID:
        return
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


# ═══════════════════════════════════════
#  State Management
# ═══════════════════════════════════════

def _load() -> dict:
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("State file corrupted, starting fresh")
    return {
        "bankroll": 50.0,
        "daily_pnl": 0.0, "total_pnl": 0.0,
        "daily_pnl_date": "",
        "markets": {},
        "fill_stats": dict(_FILL_STATS_DEFAULT),
        "consecutive_losses": 0,
    }


def _save(state: dict):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    fd = tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(_STATE_PATH),
                                     suffix=".json", delete=False)
    try:
        json.dump(state, fd, indent=2)
        fd.close()
        os.replace(fd.name, _STATE_PATH)
    except Exception:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise


def _log_order(action: str, order_id: str, cid: str, coin: str = "BTC", **extra):
    path = _order_path(coin)
    entry = {"ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
             "action": action, "order_id": order_id, "cid": cid[:12], "coin": coin, **extra}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _bump_fill(state: dict, key: str, n: int = 1):
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[key] = fs.get(key, 0) + n


# ═══════════════════════════════════════
#  4H Market Discovery
# ═══════════════════════════════════════

def _build_4h_slug(coin: str, window_start_utc: int) -> str:
    """Build 4H slug: {coin_lower}-updown-4h-{unix_timestamp}"""
    coin_slug = _COIN_SLUGS.get(coin, "")
    if not coin_slug:
        return ""
    return f"{coin_slug}-updown-4h-{window_start_utc}"


def _discover(gamma: GammaClient) -> list[dict]:
    """Find active 4H markets across all coins."""
    results = []
    now_s = int(time.time())

    # Current + next window
    current_ws = now_s // _WINDOW_S * _WINDOW_S
    for offset in (0, _WINDOW_S):
        ws = current_ws + offset
        we = ws + _WINDOW_S
        if now_s > we + 300:
            continue  # skip expired

        for coin in _COIN_SLUGS:
            slug = _build_4h_slug(coin, ws)
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
                    "start_ms": ws * 1000, "end_ms": we * 1000,
                    "up_price": float(p.get("yes_price", 0) or 0),
                    "dn_price": float(p.get("no_price", 0) or 0),
                })

    logger.info("Discovery: %d markets (%d coins × 2 windows)",
                len(results), len(_COIN_SLUGS))
    return results


# ═══════════════════════════════════════
#  Signal Engine: Bridge + Momentum
# ═══════════════════════════════════════

def _4h_signal(
    t_elapsed: float,          # minutes into 4H window (0-240)
    coin_current: float,       # current coin price
    coin_open: float,          # coin price at window open
    vol_1m_val: float,         # per-minute volatility
    bankroll: float = 0,
    max_size_frac: float = _MAX_SIZE_FRAC,
) -> dict:
    """
    Two-signal conviction engine for 4H windows.

    Decision tree:
      T < 60min → WAIT (need momentum observation)
      T ≥ 60min → compute bridge + check momentum
        bridge deviates ≥ 8c from 0.50 AND momentum confirms → ENTER
        bridge near 0.50 or momentum conflicts → WAIT
      T > 210min → no new entries (too late)
    """
    t_remaining = _WINDOW_MIN - t_elapsed
    result = {"action": "WAIT", "direction": "", "entry_price": 0,
              "size_usd": 0, "fair_up": 0.5, "confidence": 0,
              "reason": ""}

    # Guard: too early
    if t_elapsed < _WAIT_MIN:
        result["reason"] = f"waiting ({t_elapsed:.0f}/{_WAIT_MIN}min)"
        return result

    # Guard: too late for new entries
    if t_elapsed > _LATE_CUTOFF_MIN:
        result["reason"] = f"late window ({t_elapsed:.0f}min > {_LATE_CUTOFF_MIN})"
        return result

    # Guard: bad data
    if coin_current <= 0 or coin_open <= 0 or vol_1m_val <= 0:
        result["reason"] = "bad price/vol data"
        return result

    # ── Bridge: P(close >= open) ──
    fair_up = compute_fair_up(coin_current, coin_open, vol_1m_val, int(t_remaining))
    result["fair_up"] = round(fair_up, 4)

    # ── Momentum: direction of price move so far ──
    momentum_up = coin_current > coin_open
    momentum_dir = "UP" if momentum_up else "DOWN"

    # ── Combined signal ──
    fair_deviation = abs(fair_up - 0.50)
    if fair_deviation < _MIN_FAIR_DEVIATION:
        result["reason"] = f"coin-flip zone (fair={fair_up:.3f}, dev={fair_deviation:.3f})"
        return result

    # Bridge direction
    bridge_dir = "UP" if fair_up > 0.50 else "DOWN"

    # Must agree
    if bridge_dir != momentum_dir:
        result["reason"] = f"signal conflict (bridge={bridge_dir}, momentum={momentum_dir})"
        return result

    # ── Both agree → ENTER ──
    direction = bridge_dir
    # Confidence: how far from 0.50 (normalized to 0-1 scale)
    confidence = min(1.0, fair_deviation * 2.5)  # 0.08→0.20, 0.20→0.50, 0.40→1.0
    result["confidence"] = round(confidence, 3)
    result["direction"] = direction

    # ── Entry price: relative cap = fair × discount ──
    # Model says p_win → we buy at p_win × 70% → 30% discount = our edge
    p_win = fair_up if direction == "UP" else (1 - fair_up)
    entry_price = round(p_win * _ENTRY_DISCOUNT, 2)
    # EV floor: entry must leave ≥ MIN_EDGE per share
    entry_price = min(entry_price, round(p_win - _MIN_EDGE, 2))
    entry_price = max(_MIN_ENTRY_PRICE, min(_MAX_ENTRY_PRICE, entry_price))
    result["entry_price"] = entry_price

    # ── Size: fraction of bankroll, scaled by confidence ──
    size_frac = max(0.01, min(max_size_frac, max_size_frac * confidence))
    size_usd = max(_MIN_ORDER_USD, bankroll * size_frac)
    result["size_usd"] = round(size_usd, 2)

    result["action"] = "ENTER"
    result["reason"] = (f"bridge={fair_up:.3f} momentum={momentum_dir} "
                        f"conf={confidence:.2f} entry=${entry_price:.2f}")

    return result


# ═══════════════════════════════════════
#  Order Execution
# ═══════════════════════════════════════

def _execute_order(client, token_id: str, outcome: str,
                   price: float, size_usd: float, dry_run: bool,
                   coin: str = "BTC", cid: str = "") -> dict:
    """Submit a single limit order. dry_run = hard block on real orders."""
    shares = size_usd / price if price > 0 else 0
    # Hard cap: 10 shares max (testing phase)
    if shares > 10:
        shares = 10
        size_usd = shares * price
    if shares < 5:
        min_cost = 5 * price
        if min_cost <= size_usd * 2:
            size_usd = min_cost
            shares = 5
        else:
            return {"submitted": False, "reason": "below_min"}

    try:
        r = client.buy_shares(token_id, round(size_usd, 2), price=price)
        order_id = ""
        status = ""
        if isinstance(r, dict):
            order_id = r.get("orderID", r.get("id", ""))
            status = r.get("status", "")
            if r.get("dry_run"):
                # Paper mode: order sits on book as pending
                pass  # keep status from mock ("live")
        if not order_id and status != "matched":
            _log_order("rejected", "", cid, coin=coin, outcome=outcome, price=price)
            return {"outcome": outcome, "submitted": False, "reason": "rejected"}

        logger.info("ORDER %s %.0f shares @ $%.3f ($%.2f) → %s",
                    outcome, shares, price, size_usd, status or order_id[:12])
        _log_order("submit", order_id, cid, coin=coin, outcome=outcome, price=price,
                   size=shares, status=status, btc=round(_btc_price(coin), 2))
        return {"outcome": outcome, "price": price, "size": shares,
                "token_id": token_id, "order_id": order_id,
                "status": status, "submitted": True,
                "order_ts": time.time(), "btc_at_order": round(_btc_price(coin), 2)}
    except Exception as e:
        logger.error("ORDER FAILED %s: %s", outcome, e)
        return {"outcome": outcome, "submitted": False, "error": str(e)}


# ═══════════════════════════════════════
#  Fill Confirmation
# ═══════════════════════════════════════

def _check_fills_paper(state: dict) -> None:
    """Paper fill: instant fill at real market mid when signal fires.

    Conviction bots use cheap-zone pricing ($0.20-$0.40) which sits well below
    the Polymarket mid ($0.45-$0.70). A real maker order at $0.30 rarely fills
    mid-window. For paper testing, we care about DIRECTION ACCURACY not fill
    simulation — so fill instantly at market mid to track signal quality.
    """
    now = time.time()
    now_ms = int(now * 1000)

    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        _coin = mkt.get("coin", _coin_from_title(mkt.get("title", "")))
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            for po in pending:
                _log_order("paper_expired", po.get("order_id", ""), cid,
                           coin=_coin, outcome=po.get("outcome", ""))
            _bump_fill(state, "expired", len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue

        new_pending = []
        for po in pending:
            if not po.get("submitted"):
                new_pending.append(po)
                continue
            tok = po.get("token_id", "")
            mid = _poly_midpoint(tok) if tok else None
            if mid is None or mid <= 0:
                # Fallback: compute fair value from bridge model
                coin_now = _btc_price(_coin)
                coin_open = mkt.get("coin_open_price", 0)
                vol = _vol_1m(_coin) if coin_now > 0 else 0
                t_remaining = max(1, (_WINDOW_MIN - (now * 1000 - mkt.get("window_start_ms", 0)) / 60_000))
                if coin_now > 0 and coin_open > 0 and vol > 0:
                    fair_up = compute_fair_up(coin_now, coin_open, vol, int(t_remaining))
                    o_side = po.get("outcome", "UP")
                    mid = fair_up if o_side == "UP" else (1.0 - fair_up)
            if mid is None or mid <= 0:
                new_pending.append(po)
                continue
            fill_price = mid
            o, s = po["outcome"], po["size"]
            cost = s * po["price"]
            fill_shares = cost / fill_price if fill_price > 0 else 0
            if fill_shares < 1:
                new_pending.append(po)
                continue
            if o == "UP":
                old_val = mkt["up_shares"] * mkt["up_avg_price"]
                mkt["up_shares"] += fill_shares
                mkt["up_avg_price"] = (old_val + fill_shares * fill_price) / mkt["up_shares"] if mkt["up_shares"] else fill_price
            elif o == "DOWN":
                old_val = mkt["down_shares"] * mkt["down_avg_price"]
                mkt["down_shares"] += fill_shares
                mkt["down_avg_price"] = (old_val + fill_shares * fill_price) / mkt["down_shares"] if mkt["down_shares"] else fill_price
            mkt["entry_cost"] += fill_shares * fill_price
            _bump_fill(state, "filled")
            fill_age = now - po.get("order_ts", now)
            logger.info("PAPER FILL %s %s @ $%.3f (mid, limit was $%.3f) %.0f shares (age=%.0fs)",
                        cid[:8], o, fill_price, po["price"], fill_shares, fill_age)
            _log_order("paper_fill", po.get("order_id", ""), cid, coin=_coin,
                       outcome=o, price=fill_price, limit_price=po["price"],
                       size=fill_shares, fill_age_s=round(fill_age))

        mkt["pending_orders"] = new_pending
        if not new_pending and (mkt["up_shares"] > 0 or mkt["down_shares"] > 0):
            mkt["fills_confirmed"] = True


def _check_fills_live(state: dict, client) -> None:
    """Check fills via CLOB API (live mode only)."""
    if not client or not hasattr(client, "get_trades"):
        return
    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        _coin = mkt.get("coin", _coin_from_title(mkt.get("title", "")))
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            for po in pending:
                oid = po.get("order_id", "")
                if oid and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=oid)
                    except Exception:
                        pass
                _log_order("expired", oid, cid, coin=_coin, outcome=po.get("outcome", ""))
            _bump_fill(state, "expired", len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue
        try:
            trades = client.get_trades(market=cid)
            trade_ids = set()
            for t in (trades or []):
                tid = t.get("taker_order_id", "")
                if tid:
                    trade_ids.add(tid)
                for mo in t.get("maker_orders", []):
                    mid_id = mo.get("order_id", "") if isinstance(mo, dict) else ""
                    if mid_id:
                        trade_ids.add(mid_id)
            open_orders = client.get_orders(market=cid) if hasattr(client, "get_orders") else []
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
            mkt["pending_orders"] = still_open

            for f in filled:
                o, s, p = f["outcome"], f["size"], f["price"]
                if o == "UP":
                    old_val = mkt["up_shares"] * mkt["up_avg_price"]
                    mkt["up_shares"] += s
                    mkt["up_avg_price"] = (old_val + s * p) / mkt["up_shares"]
                elif o == "DOWN":
                    old_val = mkt["down_shares"] * mkt["down_avg_price"]
                    mkt["down_shares"] += s
                    mkt["down_avg_price"] = (old_val + s * p) / mkt["down_shares"]
                mkt["entry_cost"] += s * p
                _bump_fill(state, "filled")
                _log_order("fill", f.get("order_id", ""), cid, coin=_coin,
                           outcome=o, price=p, size=s)
                logger.info("FILL %s %s: %.0f @ $%.3f", cid[:8], o, s, p)

            if not still_open:
                mkt["fills_confirmed"] = True
        except Exception as e:
            logger.warning("Fill check %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  Resolution + PnL
# ═══════════════════════════════════════

def _check_resolutions(state: dict) -> None:
    """Resolve expired 4H markets using Binance candle data."""
    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN":
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + 30_000:
            continue  # wait 30s after window end for data

        # Need Binance close price at window end
        coin = mkt.get("coin", "BTC")
        start_ms = mkt.get("window_start_ms", 0)
        sym = _COIN_SYMBOLS.get(coin, "BTCUSDT")
        d = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=4h"
                      f"&startTime={start_ms}&limit=1")
        if not d or len(d) == 0:
            continue

        candle_open = float(d[0][1])
        candle_close = float(d[0][4])
        result = "UP" if candle_close >= candle_open else "DOWN"

        up_s = mkt.get("up_shares", 0)
        dn_s = mkt.get("down_shares", 0)
        cost = mkt.get("entry_cost", 0)

        if result == "UP":
            payout = up_s * 1.0 + dn_s * 0.0
        else:
            payout = up_s * 0.0 + dn_s * 1.0

        pnl = payout - cost
        mkt["phase"] = "RESOLVED"
        mkt["result"] = result
        mkt["payout"] = round(payout, 2)
        # += not = : profit lock may have already added partial PnL
        mkt["realized_pnl"] = round(mkt.get("realized_pnl", 0) + pnl, 2)
        mkt["resolve_ts"] = datetime.now(tz=_HKT).isoformat(timespec="seconds")

        state["daily_pnl"] = state.get("daily_pnl", 0) + pnl
        state["total_pnl"] = state.get("total_pnl", 0) + pnl

        if pnl >= 0:
            state["consecutive_losses"] = 0
        else:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1

        # Determine our bet direction
        our_dir = "UP" if up_s > dn_s else ("DOWN" if dn_s > 0 else "NONE")
        win = our_dir == result if our_dir != "NONE" else None

        logger.info("RESOLVED %s | %s → %s | cost=$%.2f payout=$%.2f pnl=$%+.2f %s",
                    cid[:8], coin, result, cost, payout, pnl,
                    "✅" if win else "❌" if win is False else "⚪")

        _log_order("resolve", "", cid, coin=coin, result=result,
                   our_direction=our_dir, cost=round(cost, 2),
                   payout=round(payout, 2), pnl=round(pnl, 2),
                   open=round(candle_open, 2), close=round(candle_close, 2))

        if abs(pnl) > 1.0:
            _tg_alert(f"<b>4H {coin}</b> → {result} {'✅' if win else '❌'}\n"
                      f"PnL: ${pnl:+.2f} | Total: ${state['total_pnl']:+.2f}")


# ═══════════════════════════════════════
#  Profit Lock
# ═══════════════════════════════════════

def _check_profit_lock(client, state: dict, dry_run: bool) -> None:
    """If our side's mid ≥ 96¢ → sell 96% (EV of sell > hold)."""
    if dry_run:
        return  # paper mode: just hold to resolution
    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN":
            continue
        up_s = mkt.get("up_shares", 0)
        dn_s = mkt.get("down_shares", 0)
        if up_s == 0 and dn_s == 0:
            continue

        # Check our side's mid
        if up_s >= dn_s:
            tok = mkt.get("up_token_id", "")
            side = "UP"
            shares = up_s
            avg_price = mkt.get("up_avg_price", 0)
            shares_key = "up_shares"
        else:
            tok = mkt.get("down_token_id", "")
            side = "DOWN"
            shares = dn_s
            avg_price = mkt.get("down_avg_price", 0)
            shares_key = "down_shares"

        mid = _poly_midpoint(tok) if tok else None
        if mid is None or mid < _PROFIT_LOCK_MID:
            continue

        sell_shares = int(shares * _PROFIT_LOCK_PCT)
        if sell_shares < 1:
            continue

        try:
            sell_price = 0.99  # limit sell near max — let buyers come to us
            client.sell_shares(tok, sell_shares, price=sell_price)
            pnl = sell_shares * (sell_price - avg_price)
            mkt[shares_key] = shares - sell_shares
            # Reduce entry_cost proportionally so resolution PnL is correct
            sold_cost = sell_shares * avg_price
            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - sold_cost)
            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + pnl
            state["daily_pnl"] = state.get("daily_pnl", 0) + pnl
            state["total_pnl"] = state.get("total_pnl", 0) + pnl
            logger.info("PROFIT LOCK %s %s: sell %d/%d @ $%.2f (mid=$%.2f) pnl=$%.2f",
                        cid[:8], side, sell_shares, int(shares), sell_price, mid, pnl)
            _log_order("profit_lock", "", cid,
                       coin=mkt.get("coin", _coin_from_title(mkt.get("title", ""))),
                       side=side, mid=round(mid, 3),
                       sell_shares=sell_shares, sell_price=sell_price, pnl=round(pnl, 2))
        except Exception as e:
            logger.warning("PROFIT LOCK FAILED %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  Signal Tape (for future analysis)
# ═══════════════════════════════════════

_adanos_cache: dict = {}
_adanos_last_fetch: float = 0


def _get_adanos(coin: str) -> dict:
    """Get Adanos sentiment (cached 5min across all coins)."""
    global _adanos_last_fetch
    now = time.time()
    if now - _adanos_last_fetch > 300:
        try:
            from polymarket.data.adanos_sentiment import crypto_signal_summary
            _adanos_cache.clear()
            _adanos_cache.update(crypto_signal_summary())
            _adanos_last_fetch = now
        except Exception:
            pass
    return _adanos_cache.get(coin, {})


def _record_signal(coin, cid, t_elapsed, spot, coin_open, vol, sig,
                    up_tok: str = "", dn_tok: str = ""):
    adanos = _get_adanos(coin)
    up_mid = _poly_midpoint(up_tok) if up_tok else None
    dn_mid = _poly_midpoint(dn_tok) if dn_tok else None
    entry = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "coin": coin, "cid": cid[:12],
        "t_elapsed": round(t_elapsed, 1),
        "spot": round(spot, 2), "open": round(coin_open, 2),
        "vol_1m": round(vol, 6),
        "fair_up": sig.get("fair_up", 0),
        "up_mid": round(up_mid, 4) if up_mid is not None else None,
        "dn_mid": round(dn_mid, 4) if dn_mid is not None else None,
        "action": sig.get("action", ""),
        "direction": sig.get("direction", ""),
        "confidence": sig.get("confidence", 0),
        "entry_price": sig.get("entry_price", 0),
        "reason": sig.get("reason", "")[:80],
        "adanos_buzz": adanos.get("buzz", 0),
        "adanos_sentiment": adanos.get("sentiment"),
        "adanos_trend": adanos.get("trend", ""),
    }
    try:
        path = _signal_path(coin)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════
#  Main Loop
# ═══════════════════════════════════════

def run_cycle(state, gamma, client, dry_run, max_size_frac,
              last_scan, last_heavy, cached_markets, cached_vols):
    now = time.time()
    now_hkt = datetime.now(tz=_HKT)
    today = now_hkt.strftime("%Y-%m-%d")

    # Daily PnL reset
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today
        state["daily_entries"] = 0

    # ── Fast ops (every cycle) ──
    if dry_run:
        _check_fills_paper(state)
    else:
        _check_fills_live(state, client)
    _check_resolutions(state)

    # ── Heavy ops ──
    is_heavy = (now - last_heavy) >= _HEAVY_INTERVAL_S
    if not dry_run:
        _check_profit_lock(client, state, dry_run)

    if not is_heavy:
        return state, last_scan, last_heavy, cached_markets, cached_vols

    last_heavy = now

    # ── Discovery ──
    if (now - last_scan) >= _SCAN_INTERVAL_S:
        try:
            cached_markets = _discover(gamma)
        except Exception as e:
            logger.warning("Discovery failed: %s", e)
        last_scan = now

    # ── Refresh vol per coin ──
    for coin in _COIN_SLUGS:
        cached_vols[coin] = _vol_1m(coin)

    # ── Refresh bankroll ──
    if client and hasattr(client, "get_usdc_balance") and not dry_run:
        try:
            _4H_BANKROLL_FRAC = 0.20  # 15M stopped, more room for conviction
            state["bankroll"] = client.get_usdc_balance() * _4H_BANKROLL_FRAC
        except Exception:
            pass

    # ── Evaluate each market ──
    now_ms = int(now * 1000)
    for mkt_info in cached_markets:
        cid = mkt_info["cid"]
        coin = mkt_info["coin"]
        start_ms = mkt_info["start_ms"]
        end_ms = mkt_info["end_ms"]

        if now_ms < start_ms or now_ms > end_ms:
            continue

        t_elapsed = (now_ms - start_ms) / 60_000  # minutes

        # Get current + open price
        current_price = _btc_price(coin)
        if current_price <= 0:
            continue

        existing = state["markets"].get(cid, {})
        coin_open = existing.get("coin_open_price", 0)
        if coin_open <= 0:
            coin_open = _binance_open(coin, start_ms)
            if not coin_open:
                continue

        vol = cached_vols.get(coin, 0.001)

        # ── Compute signal ──
        sig = _4h_signal(
            t_elapsed=t_elapsed,
            coin_current=current_price,
            coin_open=coin_open,
            vol_1m_val=vol,
            bankroll=state["bankroll"],
            max_size_frac=max_size_frac,
        )

        # Record to signal tape (every heavy cycle for every market)
        _record_signal(coin, cid, t_elapsed, current_price, coin_open, vol, sig,
                       up_tok=mkt_info.get("up_tok", ""),
                       dn_tok=mkt_info.get("dn_tok", ""))

        if sig["action"] != "ENTER":
            if sig["action"] == "WAIT" and sig.get("direction"):
                logger.debug("WAIT %s t=%.0fm: %s", coin, t_elapsed, sig["reason"])
            continue

        # ── One-order-per-market guard ──
        if existing.get("pending_orders"):
            logger.debug("DEDUP %s: pending order exists", coin)
            continue
        if existing.get("entry_cost", 0) > 0:
            logger.debug("DEDUP %s: already has position", coin)
            continue

        # ── Observe-only guard ──
        if coin not in _LIVE_COINS and not dry_run:
            logger.info("OBSERVE %s %s conf=%.2f fair=%.3f entry=$%.2f | %s",
                        coin, sig["direction"], sig["confidence"],
                        sig["fair_up"], sig["entry_price"], sig["reason"][:60])
            continue

        # ── Market mid sanity check ──
        up_tok = mkt_info["up_tok"]
        dn_tok = mkt_info["dn_tok"]
        our_tok = up_tok if sig["direction"] == "UP" else dn_tok
        market_mid = _poly_midpoint(our_tok)
        if market_mid is not None and market_mid < 0.15:
            logger.debug("SKIP %s: market mid $%.2f too low (disagrees)", coin, market_mid)
            continue

        # ── Daily entry cap (testing phase) ──
        _MAX_DAILY_ENTRIES = 2
        if state.get("daily_entries", 0) >= _MAX_DAILY_ENTRIES:
            logger.info("DAILY CAP %s: %d/%d entries today — skip",
                        coin, state["daily_entries"], _MAX_DAILY_ENTRIES)
            continue

        # ── Execute ──
        token_id = our_tok
        result = _execute_order(client, token_id, sig["direction"],
                                sig["entry_price"], sig["size_usd"], dry_run,
                                coin=coin, cid=cid)

        if result.get("submitted"):
            _bump_fill(state, "submitted")
            state["daily_entries"] = state.get("daily_entries", 0) + 1

            if cid not in state["markets"]:
                state["markets"][cid] = {
                    "condition_id": cid, "title": mkt_info["title"],
                    "coin": coin,
                    "up_token_id": up_tok, "down_token_id": dn_tok,
                    "window_start_ms": start_ms, "window_end_ms": end_ms,
                    "coin_open_price": coin_open,
                    "up_price": mkt_info.get("up_price", 0),
                    "dn_price": mkt_info.get("dn_price", 0),
                    "phase": "OPEN",
                    "up_shares": 0, "up_avg_price": 0,
                    "down_shares": 0, "down_avg_price": 0,
                    "entry_cost": 0, "payout": 0, "realized_pnl": 0,
                    "pending_orders": [], "fills_confirmed": False,
                }

            mkt = state["markets"][cid]
            mkt.setdefault("pending_orders", []).append(result)
            mkt["fills_confirmed"] = False

            # Instant fill (live matched orders)
            if result.get("status") == "matched":
                o, s, p = result["outcome"], result["size"], result["price"]
                if o == "UP":
                    old_val = mkt["up_shares"] * mkt["up_avg_price"]
                    mkt["up_shares"] += s
                    mkt["up_avg_price"] = (old_val + s * p) / mkt["up_shares"] if mkt["up_shares"] else p
                else:
                    old_val = mkt["down_shares"] * mkt["down_avg_price"]
                    mkt["down_shares"] += s
                    mkt["down_avg_price"] = (old_val + s * p) / mkt["down_shares"] if mkt["down_shares"] else p
                mkt["entry_cost"] += s * p
                mkt["fills_confirmed"] = True
                mkt["pending_orders"] = []
                _bump_fill(state, "filled")

            logger.info("  ENTER %s %s | conf=%.2f fair=%.3f entry=$%.2f $%.2f | %s",
                        coin, sig["direction"], sig["confidence"],
                        sig["fair_up"], sig["entry_price"], sig["size_usd"],
                        sig["reason"][:60])

    # ── Prune old resolved markets (keep last 50) ──
    resolved = [c for c, m in state["markets"].items() if m.get("phase") == "RESOLVED"]
    if len(resolved) > 50:
        for c in resolved[:-50]:
            del state["markets"][c]

    return state, last_scan, last_heavy, cached_markets, cached_vols


# ═══════════════════════════════════════
#  Status
# ═══════════════════════════════════════

def _status(state: dict):
    print("\n  4H CONVICTION BOT STATUS")
    print("  " + "=" * 50)
    print(f"  Bankroll: ${state.get('bankroll', 0):.2f}")
    print(f"  Daily PnL: ${state.get('daily_pnl', 0):+.2f}")
    print(f"  Total PnL: ${state.get('total_pnl', 0):+.2f}")
    fs = state.get("fill_stats", _FILL_STATS_DEFAULT)
    s, f = fs.get("submitted", 0), fs.get("filled", 0)
    fr = f / s * 100 if s > 0 else 0
    print(f"  Fill rate: {f}/{s} ({fr:.0f}%)")
    print(f"  Consecutive losses: {state.get('consecutive_losses', 0)}")

    open_mkts = {k: v for k, v in state.get("markets", {}).items() if v.get("phase") == "OPEN"}
    if open_mkts:
        print(f"\n  OPEN ({len(open_mkts)}):")
        for cid, m in open_mkts.items():
            up, dn = m.get("up_shares", 0), m.get("down_shares", 0)
            print(f"    {m.get('title', cid[:12])}")
            print(f"      UP: {up:.0f} | DN: {dn:.0f} | Cost: ${m.get('entry_cost', 0):.2f}")

    resolved = [v for v in state.get("markets", {}).values() if v.get("phase") == "RESOLVED"]
    if resolved:
        wins = sum(1 for r in resolved if r.get("realized_pnl", 0) > 0)
        total = len(resolved)
        wr = wins / total * 100 if total > 0 else 0
        print(f"\n  RESOLVED: {total} markets, WR={wins}/{total} ({wr:.0f}%)")
    print()


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="4H Conviction Bot")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--cycle", action="store_true", help="Run 1 cycle, exit")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--bankroll", type=float, default=0)
    parser.add_argument("--bet-pct", type=float, default=_MAX_SIZE_FRAC,
                        help=f"Max size fraction (default {_MAX_SIZE_FRAC})")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    # ─── 💀 Startup validation (BMD P0 fix, 2026-03-28) ───
    try:
        from polymarket.mm.validate import run_all
        run_all()
    except RuntimeError as e:
        logging.getLogger(__name__).critical("STARTUP BLOCKED: %s", e)
        raise SystemExit(1)
    except (ImportError, ModuleNotFoundError) as e:
        # Non-fatal: validate may fail under launchd due to import path differences.
        # Other errors (AttributeError, ValueError etc.) should still crash — they're real bugs.
        logging.getLogger(__name__).warning("Startup validation skipped (import): %s", e)

    if args.status:
        _status(_load())
        return

    _signal.signal(_signal.SIGINT, _shutdown)
    _signal.signal(_signal.SIGTERM, _shutdown)

    dry_run = args.dry_run
    gamma = GammaClient()
    client = None

    if not dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            client = PolymarketClient(dry_run=False)
        except Exception as e:
            print(f"  CLOB failed: {e} → dry-run fallback")
            dry_run = True

    # --- Startup orphan cancel (live only) ---
    # 💀 2CHECK fix: was cancelling ALL orders (including MM/1H's).
    # Now filters by own CIDs only.
    if not dry_run and client is not None:
        try:
            existing = client.get_orders()
            _pre_state = _load()
            _own_cids = set(_pre_state.get("markets", {}).keys())
            if existing:
                cancelled = 0
                for o in existing:
                    oid = o.get("id", "")
                    _mkt = o.get("market", "")
                    if oid and (_mkt in _own_cids or not _mkt):
                        try:
                            client.client.cancel(order_id=oid)
                            cancelled += 1
                        except Exception:
                            pass
                if cancelled:
                    logger.warning("STARTUP: cancelled %d/%d orphan orders (own CIDs only)",
                                   cancelled, len(existing))
        except Exception as e:
            logger.warning("Startup orphan check failed: %s", e)

    if dry_run and client is None:
        class _Mock:
            _counter = 0
            def buy_shares(self, tid, amt, price=0):
                _Mock._counter += 1
                oid = f"paper4h_{int(time.time()*1000)}_{_Mock._counter}"
                logger.info("PAPER BUY %s $%.2f @ $%.3f → %s", tid[:10], amt, price, oid)
                return {"orderID": oid, "status": "live", "dry_run": True}
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
        state.setdefault("bankroll", 50.0)

    if not dry_run and "initial_bankroll" not in state:
        state["initial_bankroll"] = state["bankroll"]

    os.makedirs(_LOG_DIR, exist_ok=True)
    max_size_frac = args.bet_pct

    last_scan, last_heavy = 0.0, 0.0
    cached_markets: list = []
    cached_vols: dict = {}

    mode_str = "DRY-RUN" if dry_run else "LIVE"
    print(f"\n  4H CONVICTION BOT — {mode_str}")
    print(f"  Bankroll: ${state['bankroll']:.2f}")
    print(f"  Signals: Bridge + Momentum (backtest: 72.1% combined)")
    print(f"  Coins: {', '.join(_COIN_SLUGS.keys())}")
    print(f"  State: {_STATE_PATH}")
    print()

    if args.cycle:
        state, *_ = run_cycle(state, gamma, client, dry_run, max_size_frac,
                              last_scan, last_heavy, cached_markets, cached_vols)
        _save(state)
        _status(state)
        return

    try:
        while _running:
            try:
                state, last_scan, last_heavy, cached_markets, cached_vols = run_cycle(
                    state, gamma, client, dry_run, max_size_frac,
                    last_scan, last_heavy, cached_markets, cached_vols)
                _save(state)
                # ── Fuse check: total loss > 22% of initial bankroll → stop ──
                if not dry_run:
                    _init_br = state.get("initial_bankroll", 0)
                    _total_pnl = state.get("total_pnl", 0)
                    if _init_br > 0 and _total_pnl < -(_init_br * _TOTAL_LOSS_FUSE_PCT):
                        logger.critical("FUSE BLOWN: pnl $%.2f < -%.0f%% of $%.2f",
                                        _total_pnl, _TOTAL_LOSS_FUSE_PCT * 100, _init_br)
                        _tg_alert(f"<b>🔴 4H FUSE BLOWN</b>\nPnL: ${_total_pnl:+.2f} / limit: ${-_init_br * _TOTAL_LOSS_FUSE_PCT:+.2f}")
                        dry_run = True
                        break
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)
            time.sleep(_CYCLE_S)
    finally:
        _save(state)
        print(f"\n  Saved state. Total PnL: ${state.get('total_pnl', 0):+.2f}")


if __name__ == "__main__":
    main()
