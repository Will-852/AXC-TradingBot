#!/usr/bin/env python3
"""
run_mm_live.py — v3 Strategy C Runner

策略：兩邊買 near fair，hold to resolution，冇 management。
模仿 Anon + LampStore 真實行為。

流程（每 30 秒）：
1. Fetch BTC price + vol
2. Refresh bankroll
3. Discover markets（slug-based）→ watchlist
4. Enter active markets（maker limit bids）
5. Check resolutions → PnL
6. Save state

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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import (
    MMConfig, MMMarketState, PlannedOrder,
    compute_fair_up, plan_opening, apply_fill,
    resolve_market, should_enter_market,
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
_CYCLE_S = 30
_SCAN_S = 300
_BINANCE = "https://fapi.binance.com"


# ═══════════════════════════════════════
#  Data
# ═══════════════════════════════════════

_cache: dict = {}

def _btc_price() -> float:
    """Latest BTC price. Cached 25s."""
    now = time.time()
    if "btc" in _cache and now - _cache["btc"][1] < 25:
        return _cache["btc"][0]
    url = f"{_BINANCE}/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}), timeout=8) as r:
            price = float(json.loads(r.read())[0][4])
            _cache["btc"] = (price, now)
            return price
    except Exception as e:
        logger.warning("BTC price fetch failed: %s", e)
        return _cache.get("btc", (0, 0))[0]


def _btc_open_at(start_ms: int) -> float:
    """BTC price at a specific timestamp."""
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={start_ms}&limit=1"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}), timeout=5) as r:
            return float(json.loads(r.read())[0][1])
    except Exception:
        return 0.0


def _vol_1m() -> float:
    """Per-minute vol. Cached 5 min."""
    now = time.time()
    if "vol" in _cache and now - _cache["vol"][1] < 300:
        return _cache["vol"][0]
    url = f"{_BINANCE}/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=60"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AXC/1.0"}), timeout=10) as r:
            closes = [float(k[4]) for k in json.loads(r.read())]
        if len(closes) < 20:
            return _cache.get("vol", (0.001, 0))[0]
        rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes)) if closes[i-1] > 0]
        mean = sum(rets) / len(rets)
        vol = max(0.0001, math.sqrt(sum((r - mean)**2 for r in rets) / len(rets)))
        _cache["vol"] = (vol, now)
        return vol
    except Exception:
        return _cache.get("vol", (0.001, 0))[0]


# ═══════════════════════════════════════
#  Discovery（slug-based）
# ═══════════════════════════════════════

def _discover(gamma: GammaClient, config: MMConfig) -> list[tuple[PolyMarket, dict]]:
    """Find BTC 15M markets for current + next 4 windows via slug."""
    import requests
    results = []
    now_s = int(time.time())
    now_et = datetime.now(tz=_ET)
    slot = (now_et.minute // 15) * 15
    base = now_et.replace(minute=0, second=0, microsecond=0)

    for i in range(5):
        ws = base + timedelta(minutes=slot + i * 15)
        we = ws + timedelta(minutes=15)
        ts, te = int(ws.timestamp()), int(we.timestamp())
        if now_s > te + 120:
            continue

        slug = f"btc-updown-15m-{ts}"
        try:
            data = requests.get("https://gamma-api.polymarket.com/markets",
                               params={"slug": slug}, timeout=5).json()
        except Exception:
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
    """Submit orders. No retry (gotcha: retry = double submit)."""
    results = []
    for o in orders:
        try:
            amount = round(o.size * o.price, 2)
            r = client.buy_shares(o.token_id, amount, price=o.price)
            logger.info("ORDER BUY %s %s: %.1f shares @ $%.3f ($%.2f)",
                        o.outcome, o.token_id[:10], o.size, o.price, amount)
            results.append({"outcome": o.outcome, "price": o.price,
                           "size": o.size, "ok": True})
        except Exception as e:
            logger.error("ORDER FAILED %s: %s", o.outcome, e)
            results.append({"outcome": o.outcome, "ok": False, "error": str(e)})
    return results


# ═══════════════════════════════════════
#  State
# ═══════════════════════════════════════

def _load() -> dict:
    if not os.path.exists(_STATE_PATH):
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "last_scan": ""}
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
                "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
                "consecutive_losses": 0, "cooldown_until": "",
                "daily_pnl_date": "", "last_scan": ""}


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
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol=BTCUSDT&interval={interval}&startTime={start_ms}&limit=1")
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

        _log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                     "result": result, "pnl": round(pnl, 4),
                     "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                     "total_pnl": round(state["total_pnl"], 2)})

        d = "↑" if result == "UP" else "↓"
        print(f"  RESOLVED {cid[:8]} {d} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")


# ═══════════════════════════════════════
#  Main Cycle
# ═══════════════════════════════════════

def run_cycle(state: dict, gamma: GammaClient, client,
              config: MMConfig, dry_run: bool) -> dict:
    now = datetime.now(tz=_HKT)
    now_ms = int(time.time() * 1000)

    # Daily reset
    today = now.strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today

    # Kill switches
    if state.get("daily_pnl", 0) < -MM_DAILY_LOSS_LIMIT:
        logger.warning("KILL: daily loss $%.2f", -state["daily_pnl"])
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

    # Data
    btc = _btc_price()
    if btc <= 0:
        return state
    vol = _vol_1m()

    # Refresh bankroll
    if client and hasattr(client, "get_usdc_balance") and not dry_run:
        try:
            state["bankroll"] = client.get_usdc_balance()
        except Exception:
            pass

    # Discover → watchlist
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

    # Enter active markets from watchlist
    active = sum(1 for m in state["markets"].values() if m["phase"] != "RESOLVED")
    for cid, wl in list(state.get("watchlist", {}).items()):
        if cid in state["markets"]:
            del state["watchlist"][cid]
            continue
        if active >= config.max_concurrent_markets:
            break
        if now_ms < wl["start_ms"]:
            continue
        if now_ms > wl["end_ms"]:
            del state["watchlist"][cid]
            continue

        # Enter
        btc_open = _btc_open_at(wl["start_ms"]) or btc
        mins_left = max(1, (wl["end_ms"] - now_ms) / 60_000)

        # Fetch indicator-based P(Up) from crypto_15m pipeline
        mkt = PolyMarket(condition_id=cid, title=wl["title"], category="crypto_15m",
                         yes_token_id=wl["up_tok"], no_token_id=wl["dn_tok"],
                         liquidity=15000)
        indicator_p_up = 0.0
        try:
            from polymarket.strategy.crypto_15m import assess_crypto_15m_edge
            ind_result = assess_crypto_15m_edge(mkt)
            if ind_result is not None:
                # ind_result.probability is P(Up) from indicator scoring
                indicator_p_up = ind_result.probability or 0.0
                logger.info("15M indicator P(Up)=%.3f for %s", indicator_p_up, cid[:8])
        except Exception as e:
            logger.debug("Indicator fetch failed (using bridge only): %s", e)

        fair = compute_fair_up(btc, btc_open, vol, int(mins_left),
                               indicator_p_up=indicator_p_up)
        bankroll = state.get("bankroll", 100.0)
        orders = plan_opening(mkt, fair, config, bankroll=bankroll)
        if not orders:
            del state["watchlist"][cid]
            continue

        results = _execute(orders, client)
        ms = MMMarketState(condition_id=cid, title=wl["title"],
                           up_token_id=wl["up_tok"], down_token_id=wl["dn_tok"],
                           window_start_ms=wl["start_ms"], window_end_ms=wl["end_ms"],
                           btc_open_price=btc_open, phase="OPEN")
        for r in results:
            if r.get("ok"):
                apply_fill(ms, r["outcome"], "BUY", r["price"], r["size"])

        state["markets"][cid] = _to_dict(ms)
        del state["watchlist"][cid]
        active += 1
        print(f"  OPEN {cid[:8]} | UP@{ms.up_avg_price:.2f} DOWN@{ms.down_avg_price:.2f} "
              f"= {ms.combined_entry:.3f} | ${ms.entry_cost:.2f}")

    # Resolutions
    _check_resolutions(state)

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
    print(f"  MM v3 Status — {datetime.now(tz=_HKT):%Y-%m-%d %H:%M HKT}")
    print(f"{'='*55}")
    print(f"  Bankroll:  ${state.get('bankroll', 0):.2f}")
    print(f"  Watchlist: {len(wl)} | Active: {len(active)} | Resolved: {len(resolved)}")
    print(f"  Daily PnL: ${state.get('daily_pnl', 0):.2f} | Total: ${state.get('total_pnl', 0):.2f}")
    print(f"  Markets:   {state.get('total_markets', 0)} | Consec losses: {state.get('consecutive_losses', 0)}")
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

    br = state.get("bankroll", 100)
    bet = br * config.bet_pct
    print(f"  [{datetime.now(tz=_HKT):%H:%M HKT}] Bankroll ${br:.2f} | "
          f"Bet {config.bet_pct:.0%} = ${bet:.2f} | Spread {config.half_spread:.1%}")

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
            _save(state)
            _status(state)


if __name__ == "__main__":
    main()
