#!/usr/bin/env python3
"""
shadow_observer.py — Zero-risk shadow observer for 4 coins × 2 timeframes

Watches BTC/ETH/SOL/XRP on both 15M and 1H markets.
Records what our conviction engine WOULD do, then checks actual outcomes.
NO orders placed. Pure observation.

After 24h, generates comparison report.

Run:
  PYTHONPATH=.:scripts python3 polymarket/tools/shadow_observer.py
  PYTHONPATH=.:scripts python3 polymarket/tools/shadow_observer.py --report
"""
import argparse
import json
import logging
import math
import os
import signal as _signal
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT))
sys.path.insert(0, str(_PROJECT / "scripts"))

from polymarket.strategy.hourly_engine import (
    HourlyConfig, OBState, conviction_signal,
)
from polymarket.exchange.gamma_client import GammaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("shadow")

_ET = timezone(timedelta(hours=-4))
_HKT = timezone(timedelta(hours=8))
_GAMMA = "https://gamma-api.polymarket.com"
_BINANCE = "https://api.binance.com/api/v3"
_CLOB = "https://clob.polymarket.com"
_LOG_DIR = _PROJECT / "polymarket" / "logs"
_TAPE_PATH = _LOG_DIR / "shadow_tape.jsonl"

_COINS = {
    "BTC": {"symbol": "BTCUSDT", "slug_15m": "btc", "slug_1h": "bitcoin"},
    "ETH": {"symbol": "ETHUSDT", "slug_15m": "eth", "slug_1h": "ethereum"},
    "SOL": {"symbol": "SOLUSDT", "slug_15m": "sol", "slug_1h": "solana"},
    "XRP": {"symbol": "XRPUSDT", "slug_15m": "xrp", "slug_1h": "xrp"},
}

_CYCLE_S = 30  # check every 30s
_running = True


def _shutdown(signum, _frame):
    global _running
    log.info("Shutdown signal %s", signum)
    _running = False

_signal.signal(_signal.SIGINT, _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


# ═══════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════

def _get(url, timeout=8):
    try:
        with urlopen(Request(url, headers={"User-Agent": "AXC-Shadow/1.0"}), timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ═══════════════════════════════════════
#  Market Data
# ═══════════════════════════════════════

_price_cache = {}

def _price(coin: str) -> float:
    now = time.time()
    if coin in _price_cache and now - _price_cache[coin][0] < 5:
        return _price_cache[coin][1]
    sym = _COINS[coin]["symbol"]
    data = _get(f"{_BINANCE}/ticker/price?symbol={sym}")
    if data:
        p = float(data["price"])
        _price_cache[coin] = (now, p)
        return p
    return _price_cache.get(coin, (0, 0))[1]


_vol_cache = {}

def _vol_1m(coin: str) -> float:
    now = time.time()
    if coin in _vol_cache and now - _vol_cache[coin][0] < 300:
        return _vol_cache[coin][1]
    sym = _COINS[coin]["symbol"]
    end = int(now * 1000)
    start = end - 24 * 3_600_000
    data = _get(f"{_BINANCE}/klines?symbol={sym}&interval=1h&startTime={start}&endTime={end}&limit=100")
    if not data or len(data) < 5:
        return 0.001
    returns = []
    for i in range(1, len(data)):
        c_prev, o_curr = float(data[i-1][4]), float(data[i][1])
        if c_prev > 0:
            returns.append(math.log(o_curr / c_prev))
    vol = statistics.stdev(returns) / math.sqrt(60) if len(returns) > 3 else 0.001
    _vol_cache[coin] = (now, vol)
    return vol


def _binance_open(coin: str, start_ms: int, interval: str = "1h") -> float | None:
    sym = _COINS[coin]["symbol"]
    data = _get(f"{_BINANCE}/klines?symbol={sym}&interval={interval}&startTime={start_ms}&limit=1")
    if data and isinstance(data, list) and data:
        return float(data[0][1])
    return None


def _binance_result(coin: str, start_ms: int, interval: str = "1h") -> str | None:
    sym = _COINS[coin]["symbol"]
    data = _get(f"{_BINANCE}/klines?symbol={sym}&interval={interval}&startTime={start_ms}&limit=1")
    if data and isinstance(data, list) and data:
        o, c = float(data[0][1]), float(data[0][4])
        return "UP" if c >= o else "DOWN"
    return None


# ═══════════════════════════════════════
#  Discovery
# ═══════════════════════════════════════

def _discover_15m(gamma: GammaClient) -> list[dict]:
    results = []
    now_et = datetime.now(tz=_ET)
    now_s = int(time.time())
    base = now_et.replace(minute=0, second=0, microsecond=0)
    slot = (now_et.minute // 15) * 15

    for i in range(3):
        ws = base + timedelta(minutes=slot + i * 15)
        we = ws + timedelta(minutes=15)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 120:
            continue
        for coin, info in _COINS.items():
            slug = f"{info['slug_15m']}-updown-15m-{ts}"
            data = _get(f"{_GAMMA}/markets?slug={slug}")
            if not data or not isinstance(data, list) or not data:
                continue
            p = gamma.parse_market(data[0])
            cid = p.get("condition_id", "")
            if cid:
                results.append({
                    "cid": cid, "coin": coin, "timeframe": "15M",
                    "title": p.get("title", ""),
                    "up_tok": p.get("yes_token_id", ""),
                    "dn_tok": p.get("no_token_id", ""),
                    "start_ms": ts * 1000, "end_ms": te * 1000,
                })
    return results


def _discover_1h(gamma: GammaClient) -> list[dict]:
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
        for coin, info in _COINS.items():
            name = info["slug_1h"]
            month = ws.strftime("%B").lower()
            day = str(ws.day)
            year = str(ws.year)
            hour = ws.strftime("%I").lstrip("0")
            ampm = ws.strftime("%p").lower()
            slug = f"{name}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et"
            data = _get(f"{_GAMMA}/markets?slug={slug}")
            if not data or not isinstance(data, list) or not data:
                continue
            p = gamma.parse_market(data[0])
            cid = p.get("condition_id", "")
            if cid:
                results.append({
                    "cid": cid, "coin": coin, "timeframe": "1H",
                    "title": p.get("title", ""),
                    "up_tok": p.get("yes_token_id", ""),
                    "dn_tok": p.get("no_token_id", ""),
                    "start_ms": ts * 1000, "end_ms": te * 1000,
                    "slug": slug,
                })
    return results


# ═══════════════════════════════════════
#  Shadow Tracking
# ═══════════════════════════════════════

def run_shadow(gamma: GammaClient, config_1h: HourlyConfig):
    """Main loop: discover → evaluate → record → resolve."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # State: tracked windows
    tracked = {}  # key = cid → {entry_signal, resolved, ...}
    last_disc_15m, last_disc_1h = 0.0, 0.0
    cached_15m, cached_1h = [], []

    tape = open(_TAPE_PATH, "a", encoding="utf-8")
    log.info("Shadow observer started → %s", _TAPE_PATH)
    log.info("Coins: %s | Timeframes: 15M + 1H", ", ".join(_COINS.keys()))

    try:
        while _running:
            t0 = time.monotonic()
            now_s = int(time.time())
            now_ms = now_s * 1000

            # ── Discovery ──
            if time.monotonic() - last_disc_15m > 60:
                try:
                    cached_15m = _discover_15m(gamma)
                except Exception as e:
                    log.warning("15M discovery: %s", e)
                last_disc_15m = time.monotonic()

            if time.monotonic() - last_disc_1h > 300:
                try:
                    cached_1h = _discover_1h(gamma)
                except Exception as e:
                    log.warning("1H discovery: %s", e)
                last_disc_1h = time.monotonic()

            all_markets = cached_15m + cached_1h

            # ── Evaluate each active window ──
            for mkt in all_markets:
                cid = mkt["cid"]
                coin = mkt["coin"]
                tf = mkt["timeframe"]
                start_ms = mkt["start_ms"]
                end_ms = mkt["end_ms"]
                window_min = 15 if tf == "15M" else 60

                if now_ms < start_ms or now_ms > end_ms:
                    continue

                t_elapsed = (now_ms - start_ms) / 60_000

                # Get price + open + vol
                current = _price(coin)
                if current <= 0:
                    continue

                # Get or compute open price
                if cid not in tracked:
                    interval = "15m" if tf == "15M" else "1h"
                    btc_open = _binance_open(coin, start_ms, interval)
                    if not btc_open:
                        continue
                    tracked[cid] = {
                        "coin": coin, "tf": tf, "title": mkt["title"],
                        "start_ms": start_ms, "end_ms": end_ms,
                        "open": btc_open, "signals": [],
                        "first_enter": None, "resolved": False, "result": None,
                    }

                tr = tracked[cid]
                if tr["resolved"]:
                    continue

                vol = _vol_1m(coin)

                # Evaluate conviction (use 1H config for both — same formula)
                # For 15M: scale t_elapsed to 60-min equivalent
                if tf == "15M":
                    # Map 0-15 → 0-60 so the conviction formula works
                    t_scaled = t_elapsed * 4
                else:
                    t_scaled = t_elapsed

                sig = conviction_signal(
                    t_elapsed=t_scaled,
                    btc_current=current,
                    btc_open=tr["open"],
                    vol_1m=vol,
                    config=config_1h,
                )

                # Record first ENTER signal
                if sig.action in ("ENTER", "ADD") and tr["first_enter"] is None:
                    tr["first_enter"] = {
                        "t_min": round(t_elapsed, 1),
                        "direction": sig.direction,
                        "entry_price": sig.entry_price,
                        "conviction": round(sig.conviction, 3),
                        "fair_up": round(sig.fair_up, 3),
                    }
                    log.info("SHADOW %s %s %s: %s @ $%.2f conv=%.3f t=%.0fm",
                             tf, coin, sig.direction, sig.action,
                             sig.entry_price, sig.conviction, t_elapsed)

            # ── Resolve finished windows ──
            for cid, tr in tracked.items():
                if tr["resolved"]:
                    continue
                if now_ms < tr["end_ms"] + 120_000:
                    continue

                interval = "15m" if tr["tf"] == "15M" else "1h"
                result = _binance_result(tr["coin"], tr["start_ms"], interval)
                if not result:
                    continue

                tr["resolved"] = True
                tr["result"] = result

                entry = tr["first_enter"]
                if entry:
                    won = entry["direction"] == result
                    pnl = (1.0 - entry["entry_price"]) if won else -entry["entry_price"]
                    entry["won"] = won
                    entry["pnl"] = round(pnl, 4)
                    icon = "✅" if won else "❌"
                    log.info("RESOLVED %s %s %s: %s → %s PnL $%+.2f (entered %s @ $%.2f t=%sm)",
                             tr["tf"], tr["coin"], result, icon,
                             entry["direction"], pnl,
                             entry["direction"], entry["entry_price"], entry["t_min"])
                else:
                    log.info("RESOLVED %s %s %s: NO ENTRY (conviction never crossed)",
                             tr["tf"], tr["coin"], result)

                # Write to tape
                record = {
                    "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
                    "coin": tr["coin"], "tf": tr["tf"],
                    "title": tr["title"],
                    "open": tr["open"], "result": result,
                    "entry": entry,
                }
                tape.write(json.dumps(record, separators=(",", ":")) + "\n")
                tape.flush()

            # ── Sleep ──
            elapsed = time.monotonic() - t0
            sleep_time = max(1, _CYCLE_S - elapsed)
            end_t = time.monotonic() + sleep_time
            while _running and time.monotonic() < end_t:
                time.sleep(min(1.0, end_t - time.monotonic()))

    finally:
        tape.close()
        log.info("Shadow observer stopped. Tape: %s", _TAPE_PATH)


# ═══════════════════════════════════════
#  Report
# ═══════════════════════════════════════

def generate_report():
    """Read shadow_tape.jsonl and generate comparison report."""
    if not _TAPE_PATH.exists():
        print("No shadow tape found.")
        return

    records = []
    with open(_TAPE_PATH) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        print("No records in shadow tape.")
        return

    # Group by coin × timeframe
    groups = defaultdict(list)
    for r in records:
        key = f"{r['coin']} {r['tf']}"
        groups[key].append(r)

    print("\n" + "=" * 80)
    print("  SHADOW OBSERVER — COMPARISON REPORT")
    print(f"  Records: {len(records)} | Period: {records[0]['ts'][:10]} to {records[-1]['ts'][:10]}")
    print("=" * 80)

    print(f"\n  {'Market':<10s} {'Windows':>8s} {'Entered':>8s} {'Entry%':>7s} "
          f"{'Wins':>5s} {'Loss':>5s} {'WR':>6s} {'Total$':>8s} {'$/day':>7s} {'AvgT':>6s}")
    print("  " + "-" * 75)

    # Count days for $/day
    dates = set()
    for r in records:
        dates.add(r["ts"][:10])
    n_days = max(1, len(dates))

    summary_rows = []
    for key in sorted(groups.keys()):
        recs = groups[key]
        total = len(recs)
        entered = [r for r in recs if r.get("entry")]
        wins = sum(1 for r in entered if r["entry"].get("won"))
        losses = len(entered) - wins
        wr = wins / len(entered) * 100 if entered else 0
        pnl = sum(r["entry"]["pnl"] for r in entered if r["entry"].get("pnl") is not None)
        daily = pnl / n_days
        entry_rate = len(entered) / total * 100 if total else 0
        avg_t = (sum(r["entry"]["t_min"] for r in entered) / len(entered)) if entered else 0

        print(f"  {key:<10s} {total:>8d} {len(entered):>8d} {entry_rate:>6.1f}% "
              f"{wins:>5d} {losses:>5d} {wr:>5.1f}% ${pnl:>7.2f} ${daily:>6.2f} {avg_t:>5.1f}m")

        summary_rows.append({
            "market": key, "total": total, "entered": len(entered),
            "wins": wins, "losses": losses, "wr": wr, "pnl": pnl, "daily": daily,
        })

    # Grouped summary
    print(f"\n  ── By Timeframe ──")
    for tf in ["15M", "1H"]:
        tf_rows = [r for r in summary_rows if tf in r["market"]]
        if not tf_rows:
            continue
        total_pnl = sum(r["pnl"] for r in tf_rows)
        total_entered = sum(r["entered"] for r in tf_rows)
        total_wins = sum(r["wins"] for r in tf_rows)
        wr = total_wins / total_entered * 100 if total_entered else 0
        print(f"  {tf}: {total_entered} entries | WR {wr:.1f}% | ${total_pnl:.2f} total | ${total_pnl/n_days:.2f}/day")

    print(f"\n  ── By Coin ──")
    for coin in _COINS:
        coin_rows = [r for r in summary_rows if coin in r["market"]]
        if not coin_rows:
            continue
        total_pnl = sum(r["pnl"] for r in coin_rows)
        total_entered = sum(r["entered"] for r in coin_rows)
        total_wins = sum(r["wins"] for r in coin_rows)
        wr = total_wins / total_entered * 100 if total_entered else 0
        print(f"  {coin}: {total_entered} entries | WR {wr:.1f}% | ${total_pnl:.2f} total | ${total_pnl/n_days:.2f}/day")

    print("\n" + "=" * 80)


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Shadow observer — 4 coins × 2 timeframes, no trades")
    ap.add_argument("--report", action="store_true", help="Generate report from collected data")
    args = ap.parse_args()

    if args.report:
        generate_report()
        return

    gamma = GammaClient()
    config = HourlyConfig()
    run_shadow(gamma, config)


if __name__ == "__main__":
    main()
