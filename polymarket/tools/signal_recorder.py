"""
signal_recorder.py — Standalone 20s data collector for BTC/ETH 15M markets

Records exchange prices + Polymarket midpoints to signal_tape.jsonl.
stdlib only (urllib + json). Flush after each write for crash safety.
Rate: ~8 HTTP calls per 20s tick = well within exchange limits.

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
_TIMEOUT = 5
_UA = "AXC-SignalRecorder/1.0"
_LOG_DIR = _PROJECT / "polymarket" / "logs"
_TAPE_PATH = _LOG_DIR / "signal_tape.jsonl"
_ET = timezone(timedelta(hours=-4))
_HKT = timezone(timedelta(hours=8))
_CLOB = "https://clob.polymarket.com"
_GAMMA = "https://gamma-api.polymarket.com"

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

def _sum_side(book: dict, side: str) -> float:
    total = 0.0
    for lv in book.get(side, []):
        try: total += float(lv.get("size", 0))
        except (TypeError, ValueError): pass
    return total

def _sfloat(data, key: str) -> float | None:
    try: return round(float(data[key]), 4)
    except (KeyError, TypeError, ValueError): return None

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

# ─── Main loop ───
def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    gamma = GammaClient()
    logger.info("Signal recorder started — writing to %s", _TAPE_PATH)

    last_disc, cached = 0.0, []
    with open(_TAPE_PATH, "a", encoding="utf-8") as tape:
        while _running:
            t0 = time.monotonic()
            now = datetime.now(tz=_HKT)
            # Re-discover every 60s (3 ticks)
            if time.monotonic() - last_disc > 60:
                try:
                    cached = _discover_15m(gamma)
                    logger.info("Discovered %d markets", len(cached))
                except Exception as e:
                    logger.warning("Discovery failed: %s", e)
                last_disc = time.monotonic()

            record = {"ts": now.isoformat(timespec="seconds"),
                      "btc": _fetch_prices("BTC"), "eth": _fetch_prices("ETH"),
                      "poly": _fetch_poly(cached) if cached else []}
            tape.write(json.dumps(record, separators=(",", ":")) + "\n")
            tape.flush()

            # Sleep in 1s chunks for responsive shutdown
            end = time.monotonic() + max(0, _TICK_S - (time.monotonic() - t0))
            while _running and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))
    logger.info("Signal recorder stopped cleanly.")

if __name__ == "__main__":
    main()
