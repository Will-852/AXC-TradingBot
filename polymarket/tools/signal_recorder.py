"""
signal_recorder.py — Standalone data collector for BTC/ETH 15M + 1H markets

Records exchange prices + Polymarket midpoints + order book depth.
15M: signal_tape.jsonl (20s tick)
1H:  signal_tape_1h.jsonl (20s normal, 5s burst at 15M boundaries)

The 1H tape captures OB spread/depth at 15M settlement moments
to test whether boundary dislocations exist and are tradeable.

stdlib only (urllib + json). Flush after each write for crash safety.

Run: PYTHONPATH=.:scripts python3 polymarket/tools/signal_recorder.py
"""
import json, logging, signal, statistics, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT))
sys.path.insert(0, str(_PROJECT / "scripts"))
from polymarket.exchange.gamma_client import GammaClient  # noqa: E402

# ─── Constants ───
_TICK_S = 20
_BURST_TICK_S = 5       # faster tick near 15M boundaries
_BOUNDARY_WINDOW_S = 120  # ±2 min around each 15M boundary
_TIMEOUT = 5
_UA = "AXC-SignalRecorder/1.0"
_LOG_DIR = _PROJECT / "polymarket" / "logs"
_TAPE_PATH = _LOG_DIR / "signal_tape.jsonl"
_TAPE_1H_PATH = _LOG_DIR / "signal_tape_1h.jsonl"
_ET = timezone(timedelta(hours=-4))
_HKT = timezone(timedelta(hours=8))
_CLOB = "https://clob.polymarket.com"
_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE_KLINE = "https://api.binance.com/api/v3/klines"

# 1H slug construction
_COIN_SLUGS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "xrp", "DOGE": "dogecoin",
}

# Exchange endpoints — public, no auth
_EX = {
    "binance": {"BTC": "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
                "ETH": "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"},
    "okx":     {"BTC": "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
                "ETH": "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT"},
    "bybit":   {"BTC": "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
                "ETH": "https://api.bybit.com/v5/market/tickers?category=spot&symbol=ETHUSDT"},
}

logger = logging.getLogger("signal_recorder")
_running = True

def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s, finishing tick...", signum)
    _running = False

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ─── HTTP + price helpers ───
def _get_json(url: str):
    """GET JSON, returns None on any failure (non-fatal)."""
    try:
        with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        logger.debug("HTTP fail %s: %s", url, e)
        return None

def _parse_price(ex: str, data) -> float | None:
    """Extract last price from exchange-specific JSON."""
    try:
        if ex == "binance": return float(data["price"])
        if ex == "okx":     return float(data["data"][0]["last"])
        if ex == "bybit":   return float(data["result"]["list"][0]["lastPrice"])
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return None

def _fetch_prices(coin: str) -> dict:
    """Fetch price from 3 exchanges, compute median + divergence."""
    prices = {}
    for ex in ("binance", "okx", "bybit"):
        data = _get_json(_EX[ex][coin])
        if data is not None:
            p = _parse_price(ex, data)
            if p and p > 0:
                prices[ex] = round(p, 2)
    if len(prices) >= 2:
        vals = list(prices.values())
        med = round(statistics.median(vals), 2)
        prices["median"] = med
        prices["div_pct"] = round((max(vals) - min(vals)) / med * 100, 4) if med else 0
    return prices

# ─── Polymarket 15M discovery (slug-based, mirrors run_mm_live._discover) ───
def _discover_15m(gamma: GammaClient) -> list[dict]:
    """Find active BTC/ETH 15M markets via slug pattern."""
    results, now_s = [], int(time.time())
    now_et = datetime.now(tz=_ET)
    base = now_et.replace(minute=0, second=0, microsecond=0)
    slot = (now_et.minute // 15) * 15
    for i in range(3):
        ws = base + timedelta(minutes=slot + i * 15)
        we = ws + timedelta(minutes=15)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 120:
            continue
        for coin in ("btc", "eth"):
            data = _get_json(f"{_GAMMA}/markets?slug={coin}-updown-15m-{ts}")
            if not data or not isinstance(data, list) or not data[0]:
                continue
            p = gamma.parse_market(data[0])
            cid, up, dn = p.get("condition_id",""), p.get("yes_token_id",""), p.get("no_token_id","")
            if cid and up and dn:
                results.append({"cid": cid, "title": p.get("title",""),
                                "coin": coin.upper(), "up_token": up, "dn_token": dn})
    return results

# ─── 1H market discovery ───
def _build_1h_slug(coin: str, dt_et: datetime) -> str:
    """Build human-readable 1H slug. Must be all lowercase."""
    name = _COIN_SLUGS.get(coin)
    if not name:
        return ""
    month = dt_et.strftime("%B").lower()
    day = str(dt_et.day)
    year = str(dt_et.year)
    hour = dt_et.strftime("%I").lstrip("0")
    ampm = dt_et.strftime("%p").lower()
    return f"{name}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et"


def _discover_1h(gamma: GammaClient) -> list[dict]:
    """Find active BTC/ETH 1H markets via slug pattern."""
    results = []
    now_et = datetime.now(tz=_ET)
    base = now_et.replace(minute=0, second=0, microsecond=0)
    now_s = int(time.time())

    for i in range(3):  # current + next 2 hours
        ws = base + timedelta(hours=i)
        we = ws + timedelta(hours=1)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 300:
            continue
        for coin in ("BTC", "ETH"):
            slug = _build_1h_slug(coin, ws)
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
                    "coin": coin, "up_token": up, "dn_token": dn,
                    "start_s": ts, "end_s": te, "slug": slug,
                })
    return results


def _fetch_binance_open(coin: str, start_ms: int) -> float | None:
    """Fetch the open price of the 1H Binance candle."""
    symbol = f"{coin}USDT"
    url = (f"{_BINANCE_KLINE}?symbol={symbol}&interval=1h"
           f"&startTime={start_ms}&limit=1")
    data = _get_json(url)
    if data and isinstance(data, list) and data:
        try:
            return float(data[0][1])  # [1] = open price
        except (IndexError, TypeError, ValueError):
            pass
    return None


def _minutes_to_boundary(now_et: datetime) -> float:
    """Minutes to nearest 15M boundary (0, 15, 30, 45)."""
    minute = now_et.minute + now_et.second / 60.0
    nearest = round(minute / 15) * 15
    return abs(minute - nearest)


def _is_boundary_burst(now_et: datetime) -> bool:
    """Should we use burst mode (5s tick) near 15M boundaries?"""
    return _minutes_to_boundary(now_et) <= _BOUNDARY_WINDOW_S / 60.0


def _sum_side(book: dict, side: str) -> float:
    total = 0.0
    for lv in book.get(side, []):
        try: total += float(lv.get("size", 0))
        except (TypeError, ValueError): pass
    return total

def _sfloat(data, key: str) -> float | None:
    try: return round(float(data[key]), 4)
    except (KeyError, TypeError, ValueError): return None

def _parse_ob_depth(book: dict) -> dict:
    """Extract OB depth metrics: spread, top-3 levels, total volume."""
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    bv = _sum_side(book, "bids")
    av = _sum_side(book, "asks")
    tot = bv + av

    # Best bid = highest bid price, best ask = lowest ask price
    bid_prices = [float(b["price"]) for b in bids if b.get("price")]
    ask_prices = [float(a["price"]) for a in asks if a.get("price")]
    best_bid = max(bid_prices) if bid_prices else 0.0
    best_ask = min(ask_prices) if ask_prices else 0.0
    spread = round(best_ask - best_bid, 4) if best_bid and best_ask and best_ask > best_bid else None

    # Top-3 depth (cumulative volume at top 3 price levels)
    top3_bid = sum(float(bids[i].get("size", 0)) for i in range(min(3, len(bids))))
    top3_ask = sum(float(asks[i].get("size", 0)) for i in range(min(3, len(asks))))

    return {
        "best_bid": round(best_bid, 4), "best_ask": round(best_ask, 4),
        "spread": spread,
        "bid_vol": round(bv, 2), "ask_vol": round(av, 2),
        "top3_bid": round(top3_bid, 2), "top3_ask": round(top3_ask, 2),
        "imbalance": round((bv - av) / tot, 4) if tot else 0,
    }


def _fetch_poly(markets: list[dict]) -> list[dict]:
    """Fetch midpoint + order book imbalance for discovered markets."""
    out = []
    for m in markets:
        up_d = _get_json(f"{_CLOB}/midpoint?token_id={m['up_token']}")
        dn_d = _get_json(f"{_CLOB}/midpoint?token_id={m['dn_token']}")
        bk = _get_json(f"{_CLOB}/book?token_id={m['up_token']}")
        bv, av = (_sum_side(bk, "bids"), _sum_side(bk, "asks")) if bk else (0.0, 0.0)
        tot = bv + av
        out.append({"cid": m["cid"], "coin": m["coin"], "title": m["title"],
                     "up_mid": _sfloat(up_d, "mid") if up_d else None,
                     "dn_mid": _sfloat(dn_d, "mid") if dn_d else None,
                     "ob_bid_vol": round(bv, 2), "ob_ask_vol": round(av, 2),
                     "ob_imbalance": round((bv - av) / tot, 4) if tot else 0})
    return out


def _fetch_poly_1h(markets: list[dict]) -> list[dict]:
    """Fetch 1H market data: midpoint + detailed OB depth (spread, top-3)."""
    out = []
    for m in markets:
        up_d = _get_json(f"{_CLOB}/midpoint?token_id={m['up_token']}")
        dn_d = _get_json(f"{_CLOB}/midpoint?token_id={m['dn_token']}")
        bk_up = _get_json(f"{_CLOB}/book?token_id={m['up_token']}")
        bk_dn = _get_json(f"{_CLOB}/book?token_id={m['dn_token']}")

        ob_up = _parse_ob_depth(bk_up) if bk_up else {}
        ob_dn = _parse_ob_depth(bk_dn) if bk_dn else {}

        # Fetch Binance open price for fair value reference
        btc_open = None
        if m.get("start_s"):
            btc_open = _fetch_binance_open(m["coin"], m["start_s"] * 1000)

        entry = {
            "cid": m["cid"], "coin": m["coin"], "title": m["title"],
            "slug": m.get("slug", ""),
            "up_mid": _sfloat(up_d, "mid") if up_d else None,
            "dn_mid": _sfloat(dn_d, "mid") if dn_d else None,
            "ob_up": ob_up, "ob_dn": ob_dn,
            "btc_open": btc_open,
        }

        # Minutes into the 1H window
        if m.get("start_s"):
            elapsed_s = int(time.time()) - m["start_s"]
            entry["elapsed_min"] = round(elapsed_s / 60, 1)
            entry["boundary_dist_min"] = round(_minutes_to_boundary(
                datetime.now(tz=_ET)), 2)

        out.append(entry)
    return out

# ─── Main loop ───
def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    gamma = GammaClient()
    logger.info("Signal recorder started — 15M: %s | 1H: %s", _TAPE_PATH, _TAPE_1H_PATH)

    last_disc_15m, cached_15m = 0.0, []
    last_disc_1h, cached_1h = 0.0, []

    with (open(_TAPE_PATH, "a", encoding="utf-8") as tape_15m,
          open(_TAPE_1H_PATH, "a", encoding="utf-8") as tape_1h):
        while _running:
            t0 = time.monotonic()
            now = datetime.now(tz=_HKT)
            now_et = datetime.now(tz=_ET)

            # ── Discovery: 15M every 60s ──
            if time.monotonic() - last_disc_15m > 60:
                try:
                    cached_15m = _discover_15m(gamma)
                except Exception as e:
                    logger.warning("15M discovery failed: %s", e)
                last_disc_15m = time.monotonic()

            # ── Discovery: 1H every 300s (5 min) ──
            if time.monotonic() - last_disc_1h > 300:
                try:
                    cached_1h = _discover_1h(gamma)
                    if cached_1h:
                        logger.info("Discovered %d 1H markets: %s",
                                    len(cached_1h),
                                    [m["slug"] for m in cached_1h])
                except Exception as e:
                    logger.warning("1H discovery failed: %s", e)
                last_disc_1h = time.monotonic()

            # ── Fetch exchange prices (shared) ──
            btc_prices = _fetch_prices("BTC")
            eth_prices = _fetch_prices("ETH")

            # ── 15M tape (always) ──
            record_15m = {
                "ts": now.isoformat(timespec="seconds"),
                "btc": btc_prices, "eth": eth_prices,
                "poly": _fetch_poly(cached_15m) if cached_15m else [],
            }
            tape_15m.write(json.dumps(record_15m, separators=(",", ":")) + "\n")
            tape_15m.flush()

            # ── 1H tape (always, detailed OB) ──
            is_burst = _is_boundary_burst(now_et)
            record_1h = {
                "ts": now.isoformat(timespec="seconds"),
                "mode": "burst" if is_burst else "normal",
                "boundary_dist_min": round(_minutes_to_boundary(now_et), 2),
                "minute_in_hour": now_et.minute + now_et.second / 60.0,
                "btc": btc_prices, "eth": eth_prices,
                "poly_1h": _fetch_poly_1h(cached_1h) if cached_1h else [],
            }
            tape_1h.write(json.dumps(record_1h, separators=(",", ":")) + "\n")
            tape_1h.flush()

            if is_burst:
                logger.debug("BURST mode — %.1f min from 15M boundary",
                             _minutes_to_boundary(now_et))

            # ── Adaptive tick: 5s near boundaries, 20s otherwise ──
            tick = _BURST_TICK_S if is_burst else _TICK_S
            end = time.monotonic() + max(0, tick - (time.monotonic() - t0))
            while _running and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))

    logger.info("Signal recorder stopped cleanly.")

if __name__ == "__main__":
    main()
