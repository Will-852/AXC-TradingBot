#!/usr/bin/env python3
"""
run_mm_live.py — v4 Dual-Layer Runner (Modular)

Thin orchestrator. All logic delegated to polymarket/mm/ sub-modules:
  mm/constants.py       — all trading constants
  mm/state_io.py        — load/save/log
  mm/data_feeds.py      — market data fetching
  mm/signal_pipeline.py — W4 signal computation
  mm/order_lifecycle.py — execute/fills/cancel/reprice
  mm/risk_guards.py     — WR monitoring/kill switches
  mm/exit_logic.py      — resolution/exits/endgame/hedge
  mm/entry_logic.py     — entry/T2/rungs/re-entry

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --live --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_mm_live.py --status
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import copy as _copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import MMConfig, MMMarketState
from polymarket.exchange.gamma_client import GammaClient
from polymarket.config.settings import MM_DAILY_LOSS_LIMIT

# ─── mm/ sub-module imports ───
from polymarket.mm.constants import (
    _BOTHSIDES_LOG, _CYCLE_S, _ET, _FILL_STATS_DEFAULT,
    _HEAVY_INTERVAL_S, _HKT, _LOG_DIR, _PROTECTION_BET_PCT,
    _PROTECTION_HOURS, _PROTECTION_MAX_MARKETS, _REVERSAL_EXTREME_THRESH,
    _REVERSAL_LOG, _REVERSAL_TTE_START, _SCAN_S, _SIGNAL_LOG,
)
from polymarket.mm import state_io
from polymarket.mm import data_feeds
from polymarket.mm import signal_pipeline
from polymarket.mm import order_lifecycle
from polymarket.mm import risk_guards
from polymarket.mm import exit_logic
from polymarket.mm import entry_logic

logger = logging.getLogger(__name__)

# ─── Module-level state (owned by orchestrator) ───
_tg_last_alert: dict = {}
_mkt_fetcher = None
_ws_binance = None
_ws_poly = None
_ws_user = None
_last_heavy_ts = 0.0


def _tg_alert(msg: str, cooldown_min: int = 30):
    """Send Telegram alert for critical events. Deduped by message prefix."""
    key = msg[:20]
    now = time.time()
    if key in _tg_last_alert and now - _tg_last_alert[key] < cooldown_min * 60:
        return
    _tg_last_alert[key] = now
    try:
        from shared_infra.telegram import send_telegram
        send_telegram(f"<b>MM 15M</b>\n{msg}")
    except Exception as e:
        logger.debug("Telegram alert failed: %s", e)


# ═══════════════════════════════════════════════════════
#  run_cycle — thin dispatcher
# ═══════════════════════════════════════════════════════

def run_cycle(state: dict, gamma: GammaClient, client,
              config: MMConfig, dry_run: bool,
              continuous_momentum: bool = False,
              both_sides: bool = False) -> dict:
    global _last_heavy_ts
    now = datetime.now(tz=_HKT)
    now_ms = int(time.time() * 1000)
    now_s = time.time()

    is_heavy = (now_s - _last_heavy_ts) >= _HEAVY_INTERVAL_S

    # ── Daily reset ──
    today = now.strftime("%Y-%m-%d")
    if state.get("daily_pnl_date") != today:
        state["daily_pnl"] = 0.0
        state["daily_pnl_date"] = today

    # ── Risk guards: hard stop + daily loss ──
    if risk_guards.check_hard_stop(state, client, dry_run, tg_alert_fn=_tg_alert):
        return state

    should_halt, _daily_budget_mult = risk_guards.check_daily_loss(
        state, client, dry_run, is_heavy, tg_alert_fn=_tg_alert)
    if should_halt:
        return state

    # ── Price check ──
    btc = data_feeds.btc_price(ws_binance=_ws_binance)
    if btc <= 0:
        return state

    # ── Bankroll refresh (heavy only) ──
    if is_heavy:
        data_feeds.vol_1m()  # warm cache
        if client and hasattr(client, "get_usdc_balance") and not dry_run:
            try:
                bal = client.get_usdc_balance()
                if bal is not None and bal > 0:
                    state["bankroll"] = bal
            except Exception:
                pass

    # ── Fast monitoring: OB imbalance for active positions ──
    if not dry_run:
        for _cid, _mkt in state["markets"].items():
            if _mkt["phase"] == "OPEN" and _mkt.get("up_token_id"):
                _obi = data_feeds.poly_ob_imbalance(client, _mkt["up_token_id"], ws_poly=_ws_poly)
                _mid = data_feeds.poly_midpoint(client, _mkt["up_token_id"], ws_poly=_ws_poly)
                if abs(_obi) > 0.3 or _mid > 0:
                    logger.debug("MONITOR %s: OBI=%.2f mid=%.3f", _cid[:8], _obi, _mid)

    # ── Risk mode ──
    risk_mode = risk_guards.get_risk_mode(state, tg_alert_fn=_tg_alert) if is_heavy else state.get("_risk_mode", "NORMAL")
    if is_heavy:
        state["_risk_mode"] = risk_mode
    if risk_mode == "STOPPED":
        logger.warning("STOPPED: WR < 50%% — no trading until manual review")
        return state

    # ── Heavy ops gate ──
    if is_heavy:
        _last_heavy_ts = now_s
        if _ws_user:
            _ws_user.cleanup_old_orders(max_age_s=3600)

    # ── Discovery → watchlist ──
    last = state.get("last_scan", "")
    since = 999
    if last:
        try:
            since = (now - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            pass
    if since >= _SCAN_S:
        for mkt, winfo in data_feeds.discover(gamma, config):
            cid = mkt.condition_id
            if cid not in state["markets"] and cid not in state.get("watchlist", {}):
                state.setdefault("watchlist", {})[cid] = {
                    "cid": cid, "title": mkt.title,
                    "up_tok": mkt.yes_token_id, "dn_tok": mkt.no_token_id,
                    "start_ms": winfo["start_ms"], "end_ms": winfo["end_ms"],
                    "end_time": winfo["end_time"]}
                if _ws_poly:
                    _ws_poly.subscribe(
                        [mkt.yes_token_id, mkt.no_token_id],
                        condition_id=cid)
                lead = (winfo["start_ms"] - now_ms) / 60_000
                logger.info("watchlist + %s (%.0fm): %s", cid[:8], lead, mkt.title[:45])
        state["last_scan"] = now.isoformat()

    # ── Newbie protection ──
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
        if not state.get("_protection_ended_logged"):
            logger.info("🛡️ PROTECTION ENDED — switching to normal: bet=%.0f%% max=%d",
                        config.bet_pct * 100, config.max_concurrent_markets)
            state["_protection_ended_logged"] = True

    # ── Pre-fetch parallel (cache warming) ──
    _pf_t0 = time.time()
    _pf_wl = [(cid, wl) for cid, wl in state.get("watchlist", {}).items()
              if cid not in state["markets"]]
    _pf_syms = set()
    for _, _wl in _pf_wl:
        _t = _wl.get("title", "").lower()
        _pf_syms.add("ETHUSDT" if "ethereum" in _t else ("SOLUSDT" if "solana" in _t else "BTCUSDT"))
    _pf_open = [(cid, m) for cid, m in state["markets"].items() if m["phase"] == "OPEN"]
    for _, _m in _pf_open:
        _t = _m.get("title", "").lower()
        _pf_syms.add("ETHUSDT" if "ethereum" in _t else "BTCUSDT")
    with ThreadPoolExecutor(max_workers=6) as _pool:
        _futs = []
        for _cid, _wl in _pf_wl:
            _futs.append(_pool.submit(data_feeds.holder_imbalance, _cid, _wl["up_tok"]))
        for _cid, _m in _pf_open:
            _futs.append(_pool.submit(data_feeds.holder_imbalance, _cid, _m.get("up_token_id", "")))
        for _s in _pf_syms:
            _futs.append(_pool.submit(data_feeds.cross_exchange_price, _s))
            _futs.append(_pool.submit(data_feeds.vol_1m, _s))
            _futs.append(_pool.submit(data_feeds.cvd_buy_ratio, _s, 3))
        for _f in _futs:
            try:
                _f.result(timeout=8)
            except Exception:
                pass
    logger.debug("Pre-fetch: %d holder + %d sym in %.1fs",
                 len(_pf_wl) + len(_pf_open), len(_pf_syms), time.time() - _pf_t0)

    # ── Dedup: existing CLOB orders ──
    _existing_markets = set()
    if client and hasattr(client, "get_orders") and not dry_run:
        try:
            _open = client.get_orders()
            _existing_markets = {o.get("market", "") for o in (_open or [])}
        except Exception:
            pass

    _heavy_loop_t0 = time.time()
    _heavy_loop_n = len(state.get("watchlist", {}))

    # ══════ ENTRY ══════
    entry_logic.try_entries(
        state, client, config, dry_run, both_sides, continuous_momentum,
        now_ms, now, is_heavy, _daily_budget_mult, risk_mode,
        ws_binance=_ws_binance, ws_poly=_ws_poly,
        mkt_fetcher=_mkt_fetcher, existing_markets=_existing_markets,
        execute_fn=order_lifecycle.execute)

    # ══════ W4 T2 ══════
    entry_logic.try_w4_t2(
        state, client, dry_run, now_ms, is_heavy, both_sides,
        config=config, ws_binance=_ws_binance,
        execute_fn=order_lifecycle.execute)

    # ══════ PHASED RUNGS ══════
    entry_logic.place_phased_rungs(
        state, client, dry_run, now_ms, ws_poly=_ws_poly,
        execute_fn=order_lifecycle.execute)

    # ══════ CANCEL DEFENSE ══════
    order_lifecycle.cancel_defense(
        state, client, dry_run, _ws_user, now_ms,
        ws_binance=_ws_binance)

    # ══════ REPRICE ══════
    order_lifecycle.reprice_orders(
        state, client, dry_run, _ws_user, now_ms,
        both_sides, is_heavy)

    # ══════ CHECK FILLS ══════
    if dry_run:
        order_lifecycle.check_fills_paper(
            state, client, ws_poly=_ws_poly)
    else:
        order_lifecycle.check_fills(
            state, client, dry_run=dry_run,
            ws_user=_ws_user, ws_poly=_ws_poly)

    # ══════ RUNTIME RATIO CAP ══════
    order_lifecycle.runtime_ratio_cap(state, client, ws_user=_ws_user)

    # ══════ POST-FILL AS CHECK ══════
    order_lifecycle.post_fill_as_check(client, ws_poly=_ws_poly)

    # ── Reversal research log (inline — log-only, zero trading impact) ──
    if is_heavy:
        for cid, mkt in state["markets"].items():
            if mkt["phase"] != "OPEN":
                continue
            end_ms = mkt.get("window_end_ms", 0)
            if end_ms <= 0:
                continue
            _rv_tte = (end_ms - now_ms) / 1000
            if not (0 < _rv_tte < _REVERSAL_TTE_START):
                continue
            _rv_up_tok = mkt.get("up_token_id", "")
            if not _rv_up_tok:
                continue
            _rv_mid = data_feeds.poly_midpoint(client, _rv_up_tok, ws_poly=_ws_poly) if client else 0
            if _rv_mid <= 0:
                continue
            _rv_cheap = min(_rv_mid, 1.0 - _rv_mid)
            if _rv_cheap > _REVERSAL_EXTREME_THRESH:
                continue
            _rv_btc = 0
            if _ws_binance:
                _rv_btc = _ws_binance.get_price("BTCUSDT")
            _rv_open = mkt.get("entry_price", 0)
            _rv_gap = _rv_btc - _rv_open if _rv_btc > 0 and _rv_open > 0 else 0
            _rv_bucket = int(_rv_tte / 30) * 30
            _rv_log_key = f"_rv_logged_{_rv_bucket}"
            if not mkt.get(_rv_log_key):
                mkt[_rv_log_key] = True
                try:
                    with open(_REVERSAL_LOG, "a") as _rvf:
                        _rvf.write(json.dumps({
                            "ts": datetime.now(tz=_HKT).isoformat(),
                            "cid": cid[:8],
                            "coin": "btc" if "bitcoin" in mkt.get("title", "").lower() else "other",
                            "tte_s": round(_rv_tte),
                            "up_mid": round(_rv_mid, 4),
                            "cheap_mid": round(_rv_cheap, 4),
                            "btc_price": round(_rv_btc, 2) if _rv_btc else 0,
                            "btc_gap": round(_rv_gap, 2) if _rv_gap else 0,
                            "window_open_px": round(_rv_open, 2) if _rv_open else 0,
                        }) + "\n")
                except Exception:
                    pass

        # Reversal research (watchlist)
        for _wl_key, wl in state.get("watchlist", {}).items():
            _wl_end = wl.get("end_ms", 0)
            if _wl_end <= 0:
                continue
            _wl_tte = (_wl_end - now_ms) / 1000
            if not (0 < _wl_tte < _REVERSAL_TTE_START):
                continue
            _wl_up_tok = wl.get("up_tok", "")
            if not _wl_up_tok:
                continue
            _wl_mid = data_feeds.poly_midpoint(client, _wl_up_tok, ws_poly=_ws_poly) if client else 0
            if _wl_mid <= 0:
                continue
            _wl_cheap = min(_wl_mid, 1.0 - _wl_mid)
            if _wl_cheap > _REVERSAL_EXTREME_THRESH:
                continue
            _wl_btc = 0
            if _ws_binance:
                _wl_btc = _ws_binance.get_price("BTCUSDT")
            _wl_bucket = int(_wl_tte / 30) * 30
            _wl_log_key = f"_rv_logged_{_wl_bucket}"
            if not wl.get(_wl_log_key):
                wl[_wl_log_key] = True
                try:
                    with open(_REVERSAL_LOG, "a") as _rvf:
                        _rvf.write(json.dumps({
                            "ts": datetime.now(tz=_HKT).isoformat(),
                            "cid": _wl_key[:8],
                            "coin": "btc" if "bitcoin" in wl.get("title", "").lower() else "other",
                            "tte_s": round(_wl_tte),
                            "up_mid": round(_wl_mid, 4),
                            "cheap_mid": round(_wl_cheap, 4),
                            "btc_price": round(_wl_btc, 2) if _wl_btc else 0,
                            "btc_gap": 0,
                            "window_open_px": 0,
                            "source": "watchlist",
                        }) + "\n")
                except Exception:
                    pass

    # ══════ ENDGAME ══════
    exit_logic.run_endgame(
        state, client, dry_run, now_ms,
        execute_fn=order_lifecycle.execute, ws_poly=_ws_poly)

    # ══════ LAST-MINUTE HEDGE ══════
    exit_logic.run_last_minute_hedge(
        state, client, dry_run, now_ms,
        execute_fn=order_lifecycle.execute,
        ws_poly=_ws_poly, ws_binance=_ws_binance)

    # ══════ EXITS ══════
    exit_logic.manage_exits(
        state, client, dry_run, now_ms, ws_poly=_ws_poly)

    # ══════ RE-ENTRY ══════
    if is_heavy:
        entry_logic.try_reentry(
            state, client, config, dry_run, now_ms, is_heavy, risk_mode,
            ws_binance=_ws_binance, ws_poly=_ws_poly,
            execute_fn=order_lifecycle.execute)

    logger.info("Heavy loop: %d markets in %.1fs", _heavy_loop_n, time.time() - _heavy_loop_t0)

    # ── WS freshness monitor ──
    _ws_status = []
    if _ws_binance:
        _bp = _ws_binance.get_price("BTCUSDT")
        _ws_status.append(f"BinWS={'OK' if _bp else 'STALE'}")
    if _ws_poly:
        _poly_ok = any(_ws_poly.get_midpoint(t) is not None
                       for cid, m in state.get("markets", {}).items()
                       for t in [m.get("up_token_id", "")]
                       if m.get("phase") == "OPEN" and t)
        _ws_status.append(f"PolyWS={'OK' if _poly_ok else 'STALE'}")
    if _ws_user:
        _ws_status.append(f"UserWS={'OK' if _ws_user.connected else 'DOWN'}")
    if _ws_status:
        logger.debug("WS feeds: %s", " | ".join(_ws_status))

    # ══════ RESOLUTIONS ══════
    exit_logic.check_resolutions(state, client=client)

    # ── Fill rate log ──
    if is_heavy:
        fr, ff, fs = state_io.fill_rate(state)
        if fs > 0:
            fst = state.get("fill_stats", _FILL_STATS_DEFAULT)
            logger.info("FILL STATS: %d/%d (%.0f%%) | cancel=%d expired=%d",
                        ff, fs, fr, fst.get("cancelled", 0), fst.get("expired", 0))

    # ── Cleanup old resolved ──
    resolved = [c for c, m in state["markets"].items() if m["phase"] == "RESOLVED"]
    if len(resolved) > 50:
        for c in resolved[:-50]:
            del state["markets"][c]
            exit_logic.endgame_mid_buf.pop(c, None)

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
    fr, ff, fs = state_io.fill_rate(state)
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
    ap.add_argument("--continuous-momentum", action="store_true",
                    help="Use current_price vs open instead of M1-only")
    ap.add_argument("--both-sides", action="store_true",
                    help="Both-sides W4 strategy: buy UP+DOWN with momentum lean")
    ap.add_argument("--w4-live", action="store_true",
                    help="Enable LIVE execution for both-sides")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # ─── 💀 Startup validation (BMD P0 fix, 2026-03-28) ───
    # ⚠️ 容易錯 #8: MUST be after logging.basicConfig, before any trading logic.
    # ⚠️ 容易錯 #9: --status mode also validates (status on corrupt state = misleading).
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
        _status(state_io.load())
        return

    dry_run = args.dry_run
    config = MMConfig()
    if args.bet_pct > 0:
        config.bet_pct = args.bet_pct

    both_sides = getattr(args, 'both_sides', False)
    w4_live = getattr(args, 'w4_live', False)
    if both_sides:
        config.half_spread = 0.020
        if w4_live:
            print(f"  MODE: W4 BOTH-SIDES LIVE (half_spread={config.half_spread})")
        else:
            if not dry_run:
                print("  ⛔ --both-sides without --w4-live → forcing dry-run.")
                dry_run = True
            print(f"  MODE: W4 BOTH-SIDES PAPER (half_spread={config.half_spread})")
    else:
        print(f"  MODE: {'DRY-RUN' if dry_run else 'LIVE'}")

    # ─── Start market data fetcher ───
    global _mkt_fetcher
    try:
        from polymarket.data.market_data import StaggeredFetcher
        _mkt_fetcher = StaggeredFetcher()
        _mkt_fetcher.start_background("BTCUSDT", interval_sec=10)
        print("  MARKET DATA: background fetcher started (log-only)")
    except Exception as e:
        logger.warning("Market data fetcher failed to start: %s — continuing without", e)

    # ─── Start shared WebSocket feeds (BMD P2 fix, 2026-03-28) ───
    # 🔴 2CHECK: SharedWSManager — MM/1H/5M share one Binance + one Poly connection
    # ⚠️ #12: release MUST be in finally block below (runner crash isolation)
    global _ws_binance, _ws_poly
    try:
        from polymarket.data import ws_shared
        _ws_binance = ws_shared.get_binance()
        print("  WS PRICE: Binance bookTicker feed (shared)")
    except Exception as e:
        logger.warning("WS price feed failed to start: %s — using REST fallback", e)
    try:
        _ws_poly = ws_shared.get_poly()
        print("  WS OB: Polymarket book feed (shared)")
    except Exception as e:
        logger.warning("WS OB feed failed to start: %s — using REST fallback", e)

    gamma = GammaClient()
    client = None
    if not dry_run:
        try:
            from polymarket.exchange.polymarket_client import PolymarketClient
            client = PolymarketClient(dry_run=False)
            print("  CLOB: connected")
            # Startup safety: cancel OWN orphan orders only
            try:
                existing = client.get_orders()
                _pre_state = state_io.load()
                _own_cids = set(_pre_state.get("markets", {}).keys()) | set(_pre_state.get("watchlist", {}).keys())
                if existing:
                    cancelled = 0
                    for o in existing:
                        oid = o.get("id", "")
                        _mkt = o.get("market", "")
                        if oid and (_mkt in _own_cids or not _mkt):
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

    # ─── Start Polymarket User WebSocket feed ───
    global _ws_user
    if client and not dry_run:
        try:
            from polymarket.data.ws_user import PolymarketUserFeed
            from polymarket.config.settings import POLY_CREDS_CACHE_PATH
            import json as _json_ws
            if os.path.exists(POLY_CREDS_CACHE_PATH):
                with open(POLY_CREDS_CACHE_PATH, "r") as _f:
                    _creds = _json_ws.load(_f)
                _ws_user = PolymarketUserFeed()
                _ws_user.start(
                    _creds.get("api_key", ""),
                    _creds.get("api_secret", ""),
                    _creds.get("api_passphrase", ""),
                )
                print("  WS USER: Polymarket user feed started (fill/cancel detection)")
            else:
                logger.warning("WS USER: creds cache not found at %s — skipping",
                               POLY_CREDS_CACHE_PATH)
        except Exception as e:
            logger.warning("WS USER feed failed to start: %s — using REST fallback", e)

    if dry_run and client is None:
        class _Mock:
            def buy_shares(self, tid, amt, price=0):
                logger.info("DRY BUY %s $%.2f @ %.3f", tid[:10], amt, price)
                return {"dry_run": True}
        client = _Mock()

    state = state_io.load()
    state["_w4_live"] = bool(both_sides and w4_live)
    if args.bankroll > 0:
        state["bankroll"] = args.bankroll
    elif client and hasattr(client, "get_usdc_balance"):
        try:
            state["bankroll"] = client.get_usdc_balance()
        except Exception:
            pass

    # 💀 Bankroll sanity check (BMD P0 fix — challenger audit found orphan)
    from polymarket.mm.validate import validate_bankroll
    bankroll_warnings = validate_bankroll(state)
    for w in bankroll_warnings:
        logger.warning("BANKROLL: %s", w)

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

    # 💀 FATAL fix: release shared WS feeds in finally block (challenger audit 2026-03-28)
    # ⚠️ #12: MUST be in finally — uncaught exception / SIGTERM / --cycle all need cleanup
    try:
        if args.cycle:
            state = run_cycle(state, gamma, client, config, dry_run,
                              continuous_momentum=getattr(args, 'continuous_momentum', False),
                              both_sides=both_sides)
            state_io.save(state)
            _status(state)
        else:
            print(f"  Loop: {_CYCLE_S}s")
            try:
                while True:
                    try:
                        state = run_cycle(state, gamma, client, config, dry_run,
                                         continuous_momentum=getattr(args, 'continuous_momentum', False),
                                         both_sides=both_sides)
                        state_io.save(state)
                        state_io.log_positions(state)
                    except Exception as e:
                        logger.error("Cycle error: %s", e, exc_info=True)
                    time.sleep(_CYCLE_S)
            except KeyboardInterrupt:
                print("\n  Shutting down...")
                if client and hasattr(client, "get_orders") and not dry_run:
                    try:
                        remaining = client.get_orders()
                        _own_cids = set(state.get("markets", {}).keys()) | set(state.get("watchlist", {}).keys())
                        for o in (remaining or []):
                            oid = o.get("id", "")
                            _mkt = o.get("market", "")
                            if oid and (_mkt in _own_cids or not _mkt):
                                try:
                                    client.client.cancel(order_id=oid)
                                except Exception:
                                    pass
                        if remaining:
                            print(f"  Cancelled {len(remaining)} open orders")
                    except Exception:
                        pass
                state_io.save(state)
                _status(state)
    finally:
        # 🔴 2CHECK: guaranteed cleanup — state save + WS release
        # Double Ctrl+C fix: save state here too (idempotent atomic write)
        try:
            state_io.save(state)
        except Exception:
            pass
        if _mkt_fetcher:
            _mkt_fetcher.shutdown()
        try:
            from polymarket.data import ws_shared
            ws_shared.release_binance()
            ws_shared.release_poly()
        except Exception:
            pass
        if _ws_user:
            _ws_user.stop()


if __name__ == "__main__":
    main()
