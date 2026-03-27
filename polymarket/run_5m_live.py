#!/usr/bin/env python3
"""
W4-style 5M Both-Sides Momentum Bot (slim orchestrator)

Replicates Wallet 4's 5M execution: fast entry, extreme lean, hold to resolution.
Independent from run_mm_live.py (15M) -- runs as separate process.

Module split (2026-03-26): all logic extracted to polymarket/mom_5m/.
This file is the orchestrator: run_cycle + _status + main.

Usage:
  PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --dry-run --bet-pct 0.02
  PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --live --w4-live --bet-pct 0.02
  PYTHONPATH=.:scripts python3 polymarket/run_5m_live.py --status
"""

import argparse
import json
import logging
import os
import signal as _signal
import sys
import time
from datetime import datetime

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.exchange.gamma_client import GammaClient

# ─── mom_5m module imports (aliased to preserve call-site compatibility) ───
from polymarket.mom_5m.config import (
    COIN_CONFIG, _BANKROLL_FRACTION, _CYCLE_S, _FILL_STATS_DEFAULT,
    _HEAVY_INTERVAL_S, _HKT, _LOG_DIR, _SCAN_S, _STATE_PATH,
    _TOTAL_LOSS_FUSE_PCT,
    tg_alert as _tg_alert,
)
from polymarket.mom_5m.data import (
    coin_price as _coin_price,
    open_at as _open_at,
    set_ws_feeds as _set_ws_feeds,
    vol_1m as _vol_1m,
)
from polymarket.mom_5m.signal import discover_5m as _discover_5m
from polymarket.mom_5m.state import (
    bump_fill as _bump_fill,
    check_kill_switches as _check_kill_switches,
    check_resolutions as _check_resolutions,
    load as _load,
    save as _save,
)
from polymarket.mom_5m.execution import (
    cancel_before_end as _cancel_before_end,
    check_fills as _check_fills,
    check_profit_lock as _check_profit_lock,
    w4_entry as _w4_entry,
)

logger = logging.getLogger(__name__)

# ─── Orchestrator-only state ───
_running = True
_ws_binance = None
_ws_poly = None


def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s", signum)
    _running = False


_signal.signal(_signal.SIGINT, _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


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

    # ── Fast ops (every cycle): cancel defense, fill check, profit lock, resolution ──
    _cancel_before_end(state, client, dry_run)
    _check_fills(state, client)
    _check_profit_lock(state, client, dry_run)
    _check_resolutions(state)

    # ── Heavy ops (every 5s) ──
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

        if result == "VETO":
            # Taker veto: don't remove from watchlist (signal may change)
            # but mark as vetoed to avoid re-checking this cycle
            wl["_vetoed_ts"] = time.time()
            continue

        if result == "DEAD_HOUR":
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
                "w4_lean_dir": result[0].get("_w4_dir", "?") if result else "?",
                "w4_combined": 0,
                "w4_mag_bps": result[0].get("_w4_mag_bps", 0) if result else 0,
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
              f"threshold={cfg.threshold_bps}bps | lean=tiered(T1=2:1,T2=5:1,T3=8:1)"
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

    # ─── Start shared WebSocket feeds (BMD P2 fix, 2026-03-28) ───
    # 🔴 2CHECK: SharedWSManager — shared with MM/1H runners
    global _ws_binance, _ws_poly
    try:
        from polymarket.data import ws_shared
        _ws_binance = ws_shared.get_binance()
        print("  WS PRICE: Binance bookTicker feed (shared)")
    except Exception as e:
        logger.warning("WS price feed failed: %s -- using REST fallback", e)
    try:
        _ws_poly = ws_shared.get_poly()
        print("  WS OB: Polymarket book feed (shared)")
    except Exception as e:
        logger.warning("WS OB feed failed: %s -- using REST fallback", e)

    # Inject WS feeds into data module so all price/OB calls use WS
    _set_ws_feeds(ws_binance=_ws_binance, ws_poly=_ws_poly)

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
    if client and hasattr(client, "get_orders") and not dry_run:
        try:
            existing = client.get_orders()
            known_cids = set(state.get("markets", {}).keys())
            orphans = 0
            for o in (existing or []):
                oid = o.get("id", "")
                mkt_id = o.get("market", "")
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
        try:
            state, cached_markets = run_cycle(
                state, gamma, client, dry_run, bet_pct, cached_markets)
            _save(state)
            _status(state)
        finally:
            # 💀 FATAL fix: release shared WS even in --cycle mode
            try:
                from polymarket.data import ws_shared
                ws_shared.release_binance()
                ws_shared.release_poly()
            except Exception:
                pass
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
    finally:
        _save(state)
        _status(state)
        # ⚠️ #12: release shared feeds — guaranteed cleanup path
        try:
            from polymarket.data import ws_shared
            ws_shared.release_binance()
            ws_shared.release_poly()
        except Exception:
            pass
        logger.info("5M bot stopped.")


if __name__ == "__main__":
    main()
