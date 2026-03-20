#!/usr/bin/env python3
"""
coin_shadow_test.py — 24h shadow test: BTC/ETH/SOL/XRP 15M

Runs the SAME signal pipeline as run_mm_live.py but:
- No real orders (pure observation)
- Uses live Binance data (bridge, CVD, M1, vol)
- Logs every decision + outcome to shadow_test.jsonl
- After 24h: auto-generate comparison report

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/tools/coin_shadow_test.py
"""
import json, logging, math, os, signal, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_P = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_P))
sys.path.insert(0, str(_P / "scripts"))

from polymarket.strategy.market_maker import compute_fair_up
from polymarket.exchange.gamma_client import GammaClient

logger = logging.getLogger("shadow_test")
_running = True

def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s", signum)
    _running = False

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

_ET = timezone(timedelta(hours=-4))
_HKT = timezone(timedelta(hours=8))
_LOG_DIR = _P / "polymarket" / "logs"
_TAPE = _LOG_DIR / "shadow_test.jsonl"
_BINANCE = "https://api.binance.com/api/v3"
_GAMMA = "https://gamma-api.polymarket.com"
_UA = "AXC-Shadow/1.0"

_COINS = {
    "btc": {"slug": "btc", "title_kw": "bitcoin", "symbol": "BTCUSDT"},
    "eth": {"slug": "eth", "title_kw": "ethereum", "symbol": "ETHUSDT"},
    "sol": {"slug": "sol", "title_kw": "solana", "symbol": "SOLUSDT"},
    "xrp": {"slug": "xrp", "title_kw": "xrp", "symbol": "XRPUSDT"},
}

_cache = {}


def _get_json(url):
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _price(symbol):
    key = f"p_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 5:
        return _cache[key][0]
    d = _get_json(f"{_BINANCE}/ticker/price?symbol={symbol}")
    if d:
        p = float(d.get("price", 0))
        if p > 0:
            _cache[key] = (p, now)
            return p
    return _cache.get(key, (0, 0))[0]


def _vol_1m(symbol):
    key = f"v_{symbol}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 60:
        return _cache[key][0]
    d = _get_json(f"{_BINANCE}/klines?symbol={symbol}&interval=1m&limit=60")
    if d and len(d) >= 20:
        closes = [float(k[4]) for k in d]
        rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
        if rets:
            mean = sum(rets) / len(rets)
            vol = max(0.0001, math.sqrt(sum((r-mean)**2 for r in rets) / len(rets)))
            _cache[key] = (vol, now)
            return vol
    return _cache.get(key, (0.001, 0))[0]


def _cvd_buy_ratio(symbol, minutes=3):
    key = f"cvd_{symbol}_{minutes}"
    now = time.time()
    if key in _cache and now - _cache[key][1] < 30:
        return _cache[key][0]
    d = _get_json(f"{_BINANCE}/klines?symbol={symbol}&interval=1m&limit={minutes+1}")
    if d and len(d) >= 2:
        recent = d[-minutes:]
        total_vol = sum(float(c[5]) for c in recent)
        total_buy = sum(float(c[9]) for c in recent)
        ratio = total_buy / total_vol if total_vol > 0 else 0.5
        _cache[key] = (ratio, now)
        return ratio
    return 0.5


def _open_at(start_ms, symbol):
    key = f"open_{symbol}_{start_ms}"
    if key in _cache:
        return _cache[key]
    d = _get_json(f"{_BINANCE}/klines?symbol={symbol}&interval=1h&startTime={start_ms}&limit=1")
    if d and len(d) > 0:
        p = float(d[0][1])
        _cache[key] = p
        return p
    return 0


def _m1_return(symbol):
    d = _get_json(f"{_BINANCE}/klines?symbol={symbol}&interval=1m&limit=2")
    if d and len(d) >= 2:
        c0 = float(d[0][4])
        c1 = float(d[1][4])
        if c0 > 0 and c1 > 0:
            return math.log(c1 / c0)
    return 0


# Track windows: key = (coin, start_ts) → entry decision + eventual outcome
_windows = {}


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    gamma = GammaClient()
    logger.info("Shadow test started — coins: %s", list(_COINS.keys()))

    last_scan = 0
    watchlist = {}  # key = condition_id → {coin, slug, start_ms, end_ms, ...}

    with open(_TAPE, "a", encoding="utf-8") as tape:
        while _running:
            t0 = time.monotonic()
            now_s = int(time.time())
            now_ms = now_s * 1000
            now_et = datetime.now(tz=_ET)

            # Discovery every 5 min
            if time.monotonic() - last_scan > 300:
                slot = (now_et.minute // 15) * 15
                base = now_et.replace(minute=0, second=0, microsecond=0)
                for i in range(5):
                    ws = base + timedelta(minutes=slot + i * 15)
                    we = ws + timedelta(minutes=15)
                    ts, te = int(ws.timestamp()), int(we.timestamp())
                    if now_s > te + 120:
                        continue
                    for coin_key, coin_info in _COINS.items():
                        slug = f"{coin_info['slug']}-updown-15m-{ts}"
                        cid_key = f"{coin_key}_{ts}"
                        if cid_key in watchlist or cid_key in _windows:
                            continue
                        d = _get_json(f"{_GAMMA}/markets?slug={slug}")
                        if d and isinstance(d, list) and d:
                            p = gamma.parse_market(d[0])
                            cid = p.get("condition_id", "")
                            if cid:
                                watchlist[cid_key] = {
                                    "coin": coin_key, "symbol": coin_info["symbol"],
                                    "cid": cid, "title": p.get("title", ""),
                                    "start_ms": ts * 1000, "end_ms": te * 1000,
                                }
                last_scan = time.monotonic()
                logger.info("Watchlist: %d markets", len(watchlist))

            # Evaluate each watched market
            for wk, wl in list(watchlist.items()):
                if now_ms > wl["end_ms"] + 120_000:
                    # Window ended — resolve
                    sym = wl["symbol"]
                    s_open = _open_at(wl["start_ms"], sym)
                    s_close = _price(sym)  # approximate close
                    actual = "UP" if s_close > s_open else "DOWN" if s_open > 0 else "UNKNOWN"

                    entry = _windows.get(wk)
                    if entry:
                        won = entry.get("direction") == actual
                        entry["actual"] = actual
                        entry["won"] = won
                        entry["resolved"] = True
                        tape.write(json.dumps(entry, default=str) + "\n")
                        tape.flush()
                        logger.info("RESOLVED %s %s: %s → %s (%s)",
                                    wl["coin"], wl["cid"][:8], entry.get("direction"), actual,
                                    "WIN" if won else "LOSS")
                    del watchlist[wk]
                    continue

                elapsed_ms = now_ms - wl["start_ms"]
                if elapsed_ms < 60_000:
                    continue  # M1 wait
                if now_ms > wl["end_ms"] - 240_000:
                    if wk not in _windows:
                        _windows[wk] = {"coin": wl["coin"], "cid": wl["cid"][:8],
                                         "action": "SKIP", "reason": "too_late"}
                    del watchlist[wk]
                    continue

                if wk in _windows:
                    continue  # already decided

                sym = wl["symbol"]

                # M1
                m1 = _m1_return(sym)
                vol = _vol_1m(sym)
                m1_thresh = max(0.0005, vol * 1.0)
                if abs(m1) < m1_thresh:
                    if elapsed_ms < 180_000:
                        continue  # wait
                    _windows[wk] = {"coin": wl["coin"], "cid": wl["cid"][:8],
                                     "action": "SKIP", "reason": "m1_weak",
                                     "m1": round(m1, 6), "thresh": round(m1_thresh, 6)}
                    del watchlist[wk]
                    continue

                # Bridge + fat-tail
                coin_price = _price(sym)
                coin_open = _open_at(wl["start_ms"], sym) or coin_price
                mins_left = max(1, (wl["end_ms"] - now_ms) / 60_000)
                bridge = compute_fair_up(coin_price, coin_open, vol, int(mins_left))
                # Fat-tail correction built into compute_fair_up() via Student-t(ν=5)

                fair = bridge  # no OB in shadow mode
                fair = max(0.05, min(0.95, fair))

                # M1 vs fair conflict
                fair_up = fair > 0.50
                m1_up = m1 > 0
                if abs(m1) >= 0.001 and fair_up != m1_up:
                    _windows[wk] = {"coin": wl["coin"], "cid": wl["cid"][:8],
                                     "action": "SKIP", "reason": "m1_fair_conflict",
                                     "m1": round(m1, 6), "fair": round(fair, 4)}
                    del watchlist[wk]
                    continue

                # CVD gate
                cvd = _cvd_buy_ratio(sym, 3)
                if fair_up and cvd < 0.45:
                    _windows[wk] = {"coin": wl["coin"], "cid": wl["cid"][:8],
                                     "action": "SKIP", "reason": "cvd_conflict",
                                     "fair": round(fair, 4), "cvd": round(cvd, 3)}
                    del watchlist[wk]
                    continue
                if not fair_up and cvd > 0.55:
                    _windows[wk] = {"coin": wl["coin"], "cid": wl["cid"][:8],
                                     "action": "SKIP", "reason": "cvd_conflict",
                                     "fair": round(fair, 4), "cvd": round(cvd, 3)}
                    del watchlist[wk]
                    continue

                # ENTER (shadow — no real order)
                direction = "UP" if fair > 0.50 else "DOWN"
                _windows[wk] = {
                    "coin": wl["coin"], "cid": wl["cid"][:8],
                    "action": "ENTER", "direction": direction,
                    "fair": round(fair, 4), "bridge": round(bridge, 4),
                    "m1": round(m1, 6), "cvd": round(cvd, 3),
                    "vol": round(vol, 6), "elapsed_min": round(elapsed_ms/60000, 1),
                    "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
                }
                logger.info("SHADOW ENTER %s %s: %s fair=%.3f cvd=%.2f m1=%+.4f",
                            wl["coin"], wl["cid"][:8], direction, fair, cvd, m1)
                del watchlist[wk]

            # Sleep
            elapsed = time.monotonic() - t0
            sleep_s = max(1, 30 - elapsed)  # 30s cycle
            end = time.monotonic() + sleep_s
            while _running and time.monotonic() < end:
                time.sleep(min(1, end - time.monotonic()))

    # Final report
    _generate_report()
    logger.info("Shadow test stopped.")


def _generate_report():
    if not _TAPE.exists():
        return
    entries = []
    with open(_TAPE) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except:
                pass

    trades = [e for e in entries if e.get("action") == "ENTER" and e.get("resolved")]
    skips = [e for e in entries if e.get("action") == "SKIP"]

    print("\n" + "="*60)
    print("  SHADOW TEST REPORT — 15M BTC/ETH/SOL/XRP")
    print("="*60)

    for coin in ["btc", "eth", "sol", "xrp"]:
        ct = [t for t in trades if t.get("coin") == coin]
        cs = [s for s in skips if s.get("coin") == coin]
        n = len(ct)
        wins = sum(1 for t in ct if t.get("won"))
        wr = wins / n * 100 if n > 0 else 0
        print(f"\n  {coin.upper()}: {n} trades | WR {wr:.0f}% | skipped {len(cs)}")
        if n > 0:
            print(f"    Wins: {wins} | Losses: {n - wins}")

    total = len(trades)
    total_wins = sum(1 for t in trades if t.get("won"))
    if total > 0:
        print(f"\n  TOTAL: {total} trades | WR {total_wins/total*100:.0f}%")

    # Per-coin skip reasons
    print(f"\n  ── Skip reasons ──")
    for coin in ["btc", "eth", "sol", "xrp"]:
        cs = [s for s in skips if s.get("coin") == coin]
        reasons = {}
        for s in cs:
            r = s.get("reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
        if reasons:
            print(f"  {coin.upper()}: {dict(sorted(reasons.items(), key=lambda x: -x[1]))}")

    print()


if __name__ == "__main__":
    main()
