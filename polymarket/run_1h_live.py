#!/usr/bin/env python3
"""
run_1h_live.py — 1H Conviction Pricing Bot (slim orchestrator)

Strategy: Observe BTC vs open price → when conviction is high enough → enter
at dynamic price → hold to resolution.

No fixed wait times or thresholds. Three factors interact continuously:
  time × odds × order_book → conviction → entry_price + size

Based on blue-walnut whale analysis ($103K PnL, 4561 markets, 1H only).
Resolution: Binance 1H OHLC candle (close >= open = Up).

Module split (2026-03-26): all logic extracted to polymarket/conv_1h/.
This file is the orchestrator: run_cycle + _status + main.

Usage:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --dry-run --verbose
  PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --live --bet-pct 0.03
  PYTHONPATH=.:scripts python3 polymarket/run_1h_live.py --status
"""

import argparse
import json
import logging
import os
import signal as _signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

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

# ─── conv_1h module imports (aliased to preserve call-site compatibility) ───
from polymarket.conv_1h.constants import (
    _BINANCE, _CYCLE_S, _FILL_STATS_DEFAULT, _GAMMA, _HEAVY_INTERVAL_S,
    _HKT, _ET, _HOLDER_MILD_IMBAL, _HOLDER_STRONG_IMBAL, _LIVE_COINS,
    _LOG_DIR, _OBSERVE_LOG, _SCAN_INTERVAL_S, _STATE_PATH,
    _TOD_SKIP_HOURS_HKT, _TOTAL_LOSS_FUSE_PCT,
)
from polymarket.conv_1h.data_feeds import (
    btc_price as _btc_price,
    binance_open as _binance_open,
    holder_imbalance as _holder_imbalance,
    poly_midpoint as _poly_midpoint,
    poly_ob as _poly_ob,
    set_ws_feeds as _set_ws_feeds,
    vol_1m as _vol_1m,
    vol_imbalance as _vol_imbalance,
)
from polymarket.conv_1h.state_io import (
    bump_fill as _bump_fill,
    load as _load,
    log_order as _log_order,
    log_trade as _log_trade,
    record_signal_tape as _record_signal_tape,
    save as _save,
    tg_alert as _tg_alert,
    to_dict as _to_dict,
    from_dict as _from_dict,
)
from polymarket.conv_1h.analysis import _collect_analysis
from polymarket.conv_1h.discovery import _discover
from polymarket.conv_1h.exit_logic import (
    _check_black_swan, _check_resolutions, _try_sell_partial,
)
from polymarket.conv_1h.order_lifecycle import (
    _check_fills, _check_fills_paper, _execute_order, _reprice_1h,
    get_repricing_cid,
)
from polymarket.conv_1h.paper_trading import (
    _paper_enter, _paper_resolve, _paper_status, _paper_state,
)

logger = logging.getLogger(__name__)

# ─── Orchestrator-only state ───
_running = True
_ws_binance = None  # BinancePriceFeed instance (set in main, WS price source)
_ws_poly = None     # PolymarketBookFeed instance (set in main, WS OB source)


def _shutdown(signum, _frame):
    global _running
    logger.info("Shutdown signal %s", signum)
    _running = False


_signal.signal(_signal.SIGINT, _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


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
        state["daily_entries"] = 0

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
    if dry_run:
        _check_fills_paper(state)
    else:
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
                # Subscribe discovered tokens to WS order book feed
                if _ws_poly:
                    for mkt_info in cached_markets:
                        _ws_poly.subscribe(
                            [mkt_info["up_tok"], mkt_info["dn_tok"]],
                            condition_id=mkt_info["cid"])
        except Exception as e:
            logger.warning("Discovery failed: %s", e)
        last_scan = now

    # ── Refresh vol (every heavy cycle) ──
    # Per-coin vol cache (refreshed per heavy cycle)
    _coin_vols = {}
    for _vc in ("BTC", "ETH", "SOL"):
        _coin_vols[_vc] = _vol_1m(_vc)

    # ── Refresh bankroll (30% of wallet — 15M stopped, more room for conviction) ──
    _1H_BANKROLL_FRACTION = 0.30
    if client and hasattr(client, "get_usdc_balance") and not dry_run:
        try:
            state["bankroll"] = client.get_usdc_balance() * _1H_BANKROLL_FRACTION
        except Exception:
            pass

    # ── Analysis data collection (read-only, 60s interval) ──
    _collect_analysis(cached_markets)

    # ── Phase 4: parallel pre-fetch slow REST data (read-only, cache warming) ──
    # Holder imbalance (Data API, 200-500ms) + vol imbalance — both cached 15-30s.
    _pf_t0 = time.time()
    _pf_now_ms = int(time.time() * 1000)
    _pf_active = [m for m in cached_markets
                  if m["start_ms"] <= _pf_now_ms <= m["end_ms"]]
    with ThreadPoolExecutor(max_workers=6) as _pool:
        _futs = []
        for _m in _pf_active:
            _futs.append(_pool.submit(_holder_imbalance, _m["cid"]))
            _futs.append(_pool.submit(_vol_imbalance, _m["coin"], _m["start_ms"]))
        for _f in _futs:
            try:
                _f.result(timeout=8)
            except Exception:
                pass
    logger.debug("Pre-fetch: %d markets in %.1fs", len(_pf_active), time.time() - _pf_t0)

    # ── Repricing: bump unfilled orders if conviction grew ──
    _reprice_1h(state, client, config, cached_markets, dry_run, coin_vols=_coin_vols)

    # ── Evaluate each active market ──
    _heavy_loop_t0 = time.time()
    _heavy_loop_n = len(cached_markets)
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

        # ── Time-of-Day gate: skip low-WR hours (backtest: 09h=52%, 19h=64%) ──
        _hkt_hour = now_hkt.hour
        if _hkt_hour in _TOD_SKIP_HOURS_HKT and current_position is None:
            logger.debug("TOD SKIP %s: hour %dh HKT in skip list", coin, _hkt_hour)
            continue

        # ── THE CORE: conviction_signal() ──
        sig = conviction_signal(
            t_elapsed=t_elapsed,
            btc_current=current_price,
            btc_open=btc_open,
            vol_1m=_coin_vols.get(coin, cached_vol),
            ob=ob,
            config=config,
            bankroll=state["bankroll"],
            budget_remaining_frac=budget_remaining_frac,
            current_position=current_position,
        )

        # ── Signal tape: record EVERY market EVERY heavy cycle for future backtest ──
        _record_signal_tape(
            coin=coin, cid=cid, up_tok=up_tok, dn_tok=dn_tok,
            start_ms=start_ms, end_ms=end_ms, t_elapsed=t_elapsed,
            spot_price=current_price, btc_open=btc_open,
            vol_1m=_coin_vols.get(coin, cached_vol), sig=sig,
            vol_dir=_vol_imbalance(coin, start_ms) if sig.action in ("ENTER", "ADD") else None,
            h_imbal=0,  # populated at entry time only
            poly_midpoint_fn=_poly_midpoint,
        )

        # ── Observe-only coins: log signal but don't trade ──
        # EXIT passthrough: if observe coin has a live position (from before scope change),
        # allow EXIT to execute so positions don't get stranded.
        # Note: _check_black_swan + _check_resolutions already cover ALL coins regardless.
        if coin not in _LIVE_COINS:
            _has_live_pos = existing.get("phase") == "OPEN" and existing.get("entry_cost", 0) > 0
            if sig.action == "EXIT" and _has_live_pos and not dry_run:
                logger.warning("OBSERVE EXIT %s: live position exists, executing exit: %s",
                               coin, sig.reason)
                # Fall through to EXIT handler below (don't continue)
            else:
                if sig.action in ("ENTER", "ADD", "EXIT"):
                    _obs = {"ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
                            "coin": coin, "action": sig.action, "direction": sig.direction,
                            "conviction": round(sig.conviction, 3),
                            "entry_price": sig.entry_price, "fair_up": round(sig.fair_up, 3),
                            "t_elapsed": round(t_elapsed, 1), "btc": round(current_price, 2),
                            "reason": sig.reason[:80]}
                    try:
                        os.makedirs(os.path.dirname(_OBSERVE_LOG), exist_ok=True)
                        with open(_OBSERVE_LOG, "a") as _of:
                            _of.write(json.dumps(_obs) + "\n")
                    except Exception:
                        pass
                    logger.info("OBSERVE %s %s %s | conv=%.2f fair=%.3f entry=$%.2f | %s",
                                sig.action, coin, sig.direction, sig.conviction,
                                sig.fair_up, sig.entry_price, sig.reason[:60])
                continue

        # ── Act on signal ──
        if sig.action == "ENTER" or sig.action == "ADD":
            # ── Volume imbalance filter (multi-signal) ──
            # Backtest: Bridge+VolImbal → +5-8pp WR vs bridge alone.
            # If volume direction conflicts with conviction direction → skip.
            _vol_dir = _vol_imbalance(coin, start_ms)
            if _vol_dir is not None and _vol_dir != sig.direction:
                logger.info("VOL CONFLICT %s: conviction=%s but vol=%s → skip",
                            coin, sig.direction, _vol_dir)
                continue

            # ── One-order-per-market guard ──
            # Prevents re-submission loop: CLOB cancels (no balance) → budget freed
            # → bot re-submits → cancelled again → 27 orders/28 min.
            # Rule: max 1 active (unfilled) order per market. ADD only after fill.
            # ⚠️ [REPRICE-LOCK] Also block if repricing is in progress for this market
            _has_pending = bool(existing.get("pending_orders"))
            _has_fill = existing.get("entry_cost", 0) > 0
            if get_repricing_cid() == cid:
                logger.debug("DEDUP %s: repricing in progress, skipping", coin)
                continue
            if sig.action == "ENTER" and _has_pending:
                logger.debug("DEDUP %s: already has pending order, skipping", coin)
                continue
            if sig.action == "ADD" and not _has_fill:
                # ADD requires at least one filled order first
                logger.debug("DEDUP %s: ADD requires prior fill, skipping", coin)
                continue
            if sig.action == "ADD" and _has_pending:
                # Don't stack ADD orders either
                logger.debug("DEDUP %s: ADD blocked, pending order exists", coin)
                continue

            # ── Holder imbalance — smart money directional signal ──
            # Positive = UP dominant, Negative = DOWN dominant.
            # Three regimes: AGREE (boost) / MILD CONFLICT (reduce) / STRONG CONFLICT (follow whale)
            h_imbal = _holder_imbalance(cid)
            # imbal_with: how much holders AGREE with our direction (>0 = agree)
            # imbal_against: how much holders DISAGREE (>0 = disagree)
            if sig.direction == "UP":
                imbal_with = max(0, h_imbal)
                imbal_against = max(0, -h_imbal)
            else:
                imbal_with = max(0, -h_imbal)
                imbal_against = max(0, h_imbal)

            _size_mult = 1.0
            _flip = False
            if imbal_with > _HOLDER_STRONG_IMBAL:
                # Whale + bridge AGREE → strongest signal, boost size 30%
                _size_mult = 1.3
                logger.info("HOLDER AGREE %s %s: imbal=%.2f with direction — whale confirms, size ×130%%",
                            coin, sig.direction, h_imbal)
            elif imbal_against > _HOLDER_STRONG_IMBAL:
                # Smart money strongly disagrees → FOLLOW them, flip direction
                _holder_dir = "UP" if h_imbal > 0 else "DOWN"
                logger.info("HOLDER FLIP %s: bridge=%s but holders=%.2f → follow smart money %s",
                            coin, sig.direction, h_imbal, _holder_dir)
                _flip = True
                sig.direction = _holder_dir
                sig.fair_up = 1.0 - sig.fair_up
                sig.p_win = max(sig.fair_up, 1.0 - sig.fair_up)
                # FIX(bmd): use base_spread not ceiling — avoid always-$0.39 entry
                sig.entry_price = round(min(sig.p_win - config.base_spread, 0.35), 2)
                sig.entry_price = max(config.min_entry_price, sig.entry_price)
                _size_mult = 0.7  # slightly reduced for holder-driven flip
            elif imbal_against > _HOLDER_MILD_IMBAL:
                _size_mult = 0.5
                logger.info("HOLDER REDUCE %s %s: imbal=%.2f mild conflict — size ×50%%",
                            coin, sig.direction, h_imbal)

            # Mid sanity check: market must somewhat agree with our direction
            our_tok = up_tok if sig.direction == "UP" else dn_tok
            market_mid = _poly_midpoint(our_tok)
            if market_mid is not None and market_mid < config.min_market_mid:
                logger.debug("SKIP %s %s: market mid $%.2f < $%.2f (market disagrees)",
                             coin, sig.direction, market_mid, config.min_market_mid)
                continue

            # Determine token and size
            token_id = our_tok

            size_usd = sig.size_fraction * state["bankroll"] * _size_mult
            budget_left = window_budget - budget_spent
            # FIX: hard block when budget exhausted. Old max(2.50, ...) bypassed budget
            # and allowed infinite $2.50 orders → 119 shares / $50 on one market.
            if budget_left < 2.50:
                logger.info("BUDGET EXHAUSTED %s: spent $%.2f / $%.2f window budget",
                            coin, budget_spent, window_budget)
                continue
            size_usd = max(2.50, min(size_usd, budget_left))

            # ── Daily entry cap (testing phase) ──
            _MAX_DAILY_ENTRIES = 2
            if state.get("daily_entries", 0) >= _MAX_DAILY_ENTRIES:
                logger.info("DAILY CAP %s: %d/%d entries today — skip",
                            coin, state["daily_entries"], _MAX_DAILY_ENTRIES)
                continue

            result = _execute_order(client, token_id, sig.direction,
                                    sig.entry_price, size_usd, dry_run,
                                    coin=coin, cid=cid)

            if result.get("submitted"):
                _bump_fill(state, "submitted")
                state["daily_entries"] = state.get("daily_entries", 0) + 1
                # Enrich order log with holder signal for post-hoc analysis
                _log_order("holder_signal", result.get("order_id", ""), cid, coin=coin,
                           h_imbal=round(h_imbal, 3), flip=_flip,
                           imbal_with=round(imbal_with, 3),
                           imbal_against=round(imbal_against, 3),
                           size_mult=round(_size_mult, 2))

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
                mkt["fills_confirmed"] = False  # re-enable fill checking for new order

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
                    mkt["pending_orders"] = []  # clear pending on instant fill (dry-run)
                    _bump_fill(state, "filled")

                _flip_tag = " [WHALE_FLIP]" if _flip else ""
                logger.info("  %s %s %s%s | conv=%.2f fair=%.3f entry=$%.2f size=$%.2f | %s",
                            sig.action, coin, sig.direction, _flip_tag, sig.conviction,
                            sig.fair_up, sig.entry_price, size_usd, sig.reason)

                # Paper trade: record simulated entry at real Poly mid
                _paper_enter(cid, coin, sig.direction, sig.entry_price,
                             _poly_midpoint(up_tok), sig.conviction)

        elif sig.action == "EXIT" and not dry_run:
            logger.warning("EXIT signal for %s: %s", cid[:8], sig.reason)
            _try_sell_partial(client, state, cid, existing, up_tok, dn_tok,
                              reason="exit_signal", sell_pct=1.0)  # EXIT = sell all

        elif sig.action == "WAIT":
            logger.debug("WAIT %s t=%.0fm: %s", coin, t_elapsed, sig.reason)

    logger.info("Heavy loop: %d markets in %.1fs", _heavy_loop_n, time.time() - _heavy_loop_t0)

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

    # Paper trading summary
    ps = _paper_state
    if ps["resolved"]:
        n = len(ps["resolved"])
        pw = sum(1 for r in ps["resolved"] if r["won"])
        print(f"\n  📊 PAPER TRADING (simulated $8.40/window):")
        print(f"    Total: {n} trades | WR: {pw/n*100:.0f}% | PnL: ${ps['total_pnl']:+.2f}")
        for coin, cs in sorted(ps["by_coin"].items()):
            cwr = cs["wins"] / cs["trades"] * 100 if cs["trades"] else 0
            print(f"    {coin}: {cs['trades']} trades | WR: {cwr:.0f}% | PnL: ${cs['pnl']:+.2f}")
    if ps["positions"]:
        print(f"    Open: {len(ps['positions'])} paper positions")

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

    # ─── 💀 Startup validation (BMD P0 fix, 2026-03-28) ───
    try:
        from polymarket.mm.validate import run_all
        run_all()
    except RuntimeError as e:
        logging.getLogger(__name__).critical("STARTUP BLOCKED: %s", e)
        raise SystemExit(1)

    if args.status:
        _status(_load())
        return

    dry_run = args.dry_run
    config = HourlyConfig()
    if args.bet_pct > 0:
        config.max_size_fraction = args.bet_pct

    # ─── Start shared WebSocket feeds (BMD P2 fix, 2026-03-28) ───
    # 🔴 2CHECK: SharedWSManager — shared with MM/5M runners
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

    # Inject WS feeds into data_feeds module so all price/OB calls use WS
    _set_ws_feeds(ws_binance=_ws_binance, ws_poly=_ws_poly)

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
    # 💀 2CHECK fix: was cancelling ALL orders (including MM's).
    # Now filters by own CIDs (same pattern as run_mm_live.py).
    # ⚠️ #21: state markets key = condition_id, Poly order market field = condition_id. Match.
    # ⚠️ #22: orders without market field (or empty) = also cancel (likely orphan).
    if not dry_run and client is not None:
        try:
            existing = client.get_orders()
            _pre_state = _load()
            _own_cids = set(_pre_state.get("markets", {}).keys()) | set(
                _pre_state.get("watchlist", {}).keys())
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
                oid = f"paper_{int(time.time()*1000)}_{_Mock._counter}"
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
        try:
            state, *_ = run_cycle(state, gamma, client, config, dry_run,
                                  last_scan, last_heavy, cached_markets, cached_vol)
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

    # 💀 FATAL fix: release shared WS feeds in finally block (challenger audit 2026-03-28)
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
                            _counter = 0
                            def buy_shares(self, tid, amt, price=0):
                                _MockPost._counter += 1
                                oid = f"fuse_{int(time.time()*1000)}_{_MockPost._counter}"
                                logger.info("FUSE DRY BUY %s $%.2f @ $%.3f → %s", tid[:10], amt, price, oid)
                                return {"orderID": oid, "status": "live", "dry_run": True}
                            def sell_shares(self, tok, shares, price=0):
                                logger.info("FUSE DRY SELL %s %d @ $%.3f", tok[:10], shares, price)
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


if __name__ == "__main__":
    main()
