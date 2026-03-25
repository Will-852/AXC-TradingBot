"""
mm/entry_logic.py — Entry, T2 confirmation, phased rungs, re-entry for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🔴 2CHECK: Entry logic places real buy orders on Polymarket CLOB.
Every function here spends real money.

Key extractions:
- try_entries(): the watchlist → OPEN entry loop (was inline in run_cycle lines 1644-2208)
- try_w4_t2(): T2 confirmation at T+480s (was inline lines 2210-2404)
- place_phased_rungs(): conditional rung 3-4 placement (was inline lines 2425-2517)
- try_reentry(): re-entry after stop loss (was inline lines 3505-3670)
"""

import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import copy as _copy
from datetime import datetime
from zoneinfo import ZoneInfo

from polymarket.mm.constants import (
    _BET_PCT_BY_COIN, _BOTHSIDES_LOG, _HKT, _HOLDER_CACHE_TTL,
    _LIVE_TRADE_COINS, _LOG_DIR, _MAX_ROUNDS, _MIN_VIABLE_BUDGET,
    _REENTRY_COOLDOWN_S, _SIGNAL_LOG, _W4_LEAN_RATIO, _W4_T1_PCT,
    _W4_T2_DELAY_S, _W4_T2_PCT, _W4_THRESHOLD_BPS,
)
from polymarket.mm.data_feeds import (
    cross_exchange_price, cvd_buy_ratio, holder_imbalance, m1_return,
    open_at, poly_midpoint, poly_ob_imbalance, price, vol_1m,
)
from polymarket.mm.signal_pipeline import w4_dynamic_ratio, w4_signal
from polymarket.mm.state_io import bump_fill, to_dict

logger = logging.getLogger(__name__)


# ─── Paper client classes (extracted from inline in run_cycle) ───
class PaperClient:
    """Mock client that simulates instant fills for paper trading."""
    def buy_shares(self, tid, amt, price=0):
        return {"orderID": f"paper_{tid[:8]}_{int(time.time())}",
                "status": "matched", "dry_run": True}
    def sell_shares(self, tid, amt, price=0):
        return {"orderID": f"paper_s_{tid[:8]}_{int(time.time())}",
                "status": "matched", "dry_run": True}


class T2PaperClient:
    """Mock client for T2 paper fills."""
    def buy_shares(self, tid, amt, price=0):
        return {"orderID": f"paper_t2_{tid[:8]}_{int(time.time())}",
                "status": "matched", "dry_run": True}


def try_entries(state: dict, client, config, dry_run: bool,
                both_sides: bool, continuous_momentum: bool,
                now_ms: int, now, is_heavy: bool,
                daily_budget_mult: float, risk_mode: str,
                ws_binance=None, ws_poly=None,
                mkt_fetcher=None, existing_markets=None,
                execute_fn=None) -> None:
    """Main entry loop: watchlist → signal pipeline → OPEN markets.

    🔴 Places real buy orders for both-sides (W4) and directional paths.
    """
    from polymarket.core.context import PolyMarket
    from polymarket.strategy.market_maker import (
        MMMarketState, PlannedOrder, apply_fill,
        calc_tranches, compute_fair_up, plan_opening,
    )
    if execute_fn is None:
        from polymarket.mm.order_lifecycle import execute as execute_fn

    if existing_markets is None:
        existing_markets = set()

    active = sum(1 for m in state["markets"].values() if m["phase"] != "RESOLVED")
    if not is_heavy:
        active = config.max_concurrent_markets  # skip entry on fast cycles

    for cid, wl in list(state.get("watchlist", {}).items()):
        if cid in state["markets"]:
            del state["watchlist"][cid]
            continue
        if cid in existing_markets:
            logger.info("SKIP %s: already have orders on CLOB (dedup)", cid[:8])
            del state["watchlist"][cid]
            continue
        if active >= config.max_concurrent_markets:
            break
        if now_ms < wl["start_ms"]:
            continue
        if now_ms > wl["end_ms"]:
            del state["watchlist"][cid]
            continue

        _elapsed_ms = now_ms - wl["start_ms"]

        # Dead Hours Gate
        _hkt_hour = datetime.now(tz=_HKT).hour
        if _hkt_hour in {4, 5}:
            continue

        # Late Gate: < 1.5 min remaining
        if now_ms > wl["end_ms"] - 90_000:
            logger.info("SKIP %s: < 1.5 min remaining, too late", cid[:8])
            del state["watchlist"][cid]
            continue

        # Detect coin
        _title_lower = wl["title"].lower()
        if "ethereum" in _title_lower:
            _sym, _coin_slug = "ETHUSDT", "eth"
        elif "solana" in _title_lower:
            _sym, _coin_slug = "SOLUSDT", "sol"
        elif "xrp" in _title_lower:
            _sym, _coin_slug = "XRPUSDT", "xrp"
        else:
            _sym, _coin_slug = "BTCUSDT", "btc"

        _observe_only = _coin_slug not in _LIVE_TRADE_COINS

        # ── Momentum Filter ──
        _m1_vol = vol_1m(_sym)
        _m1 = 0.0
        if both_sides:
            pass
        elif continuous_momentum:
            _cm_open = open_at(wl["start_ms"], _sym) or price(_sym, ws_binance=ws_binance)
            _cm_now = price(_sym, ws_binance=ws_binance)
            _cm_ret = math.log(_cm_now / _cm_open) if _cm_open > 0 and _cm_now > 0 else 0
            _cm_mins = _elapsed_ms / 60_000
            _cm_thresh = max(0.0005, _m1_vol * math.sqrt(max(1, _cm_mins)) * 0.7)
            _m1_confirmed = abs(_cm_ret) >= _cm_thresh
            _m1 = _cm_ret
            if not _m1_confirmed:
                if _elapsed_ms < 300_000:
                    continue
                logger.info("SKIP %s: CM weak |%.4f| < %.4f after 5min", cid[:8], _cm_ret, _cm_thresh)
                del state["watchlist"][cid]
                continue
            logger.info("CM confirmed %s: %+.4f (%.2f%%, %.1fσ) [%dmin elapsed]",
                        cid[:8], _cm_ret, _cm_ret * 100, abs(_cm_ret) / _cm_thresh,
                        int(_cm_mins))
        else:
            _m1 = m1_return(_sym)
            _m1_thresh = max(0.0005, _m1_vol * 1.0)
            _m1_confirmed = abs(_m1) >= _m1_thresh
            if not _m1_confirmed:
                if _elapsed_ms < 300_000:
                    continue
                logger.info("SKIP %s: M1 weak |%.4f| < %.4f after 5min", cid[:8], _m1, _m1_thresh)
                del state["watchlist"][cid]
                continue
            logger.info("M1 confirmed %s: %+.4f (%.2f%%, %.1fσ)",
                        cid[:8], _m1, _m1 * 100, abs(_m1) / _m1_thresh)

        # Cross-exchange validation
        _xprice, _xdiv = cross_exchange_price(_sym)
        _coin_price = _xprice if _xprice > 0 else price(_sym, ws_binance=ws_binance)
        if _xdiv > 0.003:
            logger.warning("SKIP %s: cross-exchange divergence %.2f%% (anomaly)",
                           cid[:8], _xdiv * 100)
            continue

        _coin_open = open_at(wl["start_ms"], _sym) or _coin_price
        _coin_vol = vol_1m(_sym)
        mins_left = max(1, (wl["end_ms"] - now_ms) / 60_000)

        # Signal pipeline
        mkt = PolyMarket(condition_id=cid, title=wl["title"], category="crypto_15m",
                         yes_token_id=wl["up_tok"], no_token_id=wl["dn_tok"],
                         liquidity=15000)

        bridge_p_up = compute_fair_up(_coin_price, _coin_open, _coin_vol, int(mins_left))

        ob_adjustment = 0.0
        _ob_best_bid = 0.0
        _ob_best_ask = 0.0
        _ob_bid_vol = 0.0
        _ob_ask_vol = 0.0
        _ob_depth = 0
        if client and hasattr(client, "get_order_book") and not dry_run:
            try:
                up_book = client.get_order_book(wl["up_tok"])
                bids = up_book.get("bids", [])
                asks = up_book.get("asks", [])
                bid_vol = sum(b["size"] for b in bids)
                ask_vol = sum(a["size"] for a in asks)
                _ob_bid_vol = bid_vol
                _ob_ask_vol = ask_vol
                _ob_depth = len(bids) + len(asks)
                if bids:
                    _ob_best_bid = max(b["price"] for b in bids)
                if asks:
                    _ob_best_ask = min(a["price"] for a in asks)
                if bid_vol + ask_vol > 0:
                    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                    ob_adjustment = imbalance * 0.05
                    logger.info("OB imbalance=%.3f (bid=%.0f ask=%.0f) → adj=%.3f",
                                imbalance, bid_vol, ask_vol, ob_adjustment)
            except Exception as e:
                logger.debug("OB fetch failed: %s", e)

        fair = bridge_p_up + ob_adjustment
        fair = max(0.05, min(0.95, fair))

        _cvd = cvd_buy_ratio(_sym, minutes=3)

        # Signal log
        try:
            _sig_record = {
                "ts": datetime.now(tz=_HKT).isoformat(), "cid": cid[:8],
                "coin": _coin_slug, "observe": _observe_only,
                "sym": _sym, "m1": round(_m1, 6),
                "m1_sigma": round(abs(_m1) / _m1_thresh, 2) if locals().get("_m1_thresh", 0) > 0 else 0,
                "bridge": round(bridge_p_up, 4),
                "fair": round(fair, 4), "xdiv": round(_xdiv, 5),
                "ob_adj": round(ob_adjustment, 4), "cvd": round(_cvd, 3),
            }
            if mkt_fetcher is not None:
                _snap = mkt_fetcher.latest()
                if _snap and _snap.price > 0 and _snap.age_ms < 30_000:
                    _sig_record["mkt"] = {
                        "price": round(_snap.price, 2),
                        "src": _snap.sources_responded,
                        "fund_agg": round(_snap.funding_agg, 8),
                        "fund_prem": round(_snap.funding_premium, 2),
                        "oi_total": round(_snap.oi_total / 1e9, 2),
                        "oi_d5m": round(_snap.oi_delta_5m / 1e9, 3),
                        "ls": round(_snap.ls_ratio, 3),
                        "ls_ext": _snap.ls_extreme,
                        "dvol": round(_snap.dvol, 1),
                        "taker": round(_snap.taker_buy_sell_ratio, 3),
                        "age_ms": round(_snap.age_ms),
                    }
            os.makedirs(_LOG_DIR, exist_ok=True)
            with open(_SIGNAL_LOG, "a") as _sf:
                _sf.write(json.dumps(_sig_record) + "\n")
        except Exception:
            pass

        _fair_up = fair > 0.50
        _m1_up = _m1 > 0

        # ── W4 Both-Sides path ──
        if both_sides:
            _win_min = datetime.utcfromtimestamp(wl["start_ms"] / 1000).minute
            if _win_min == 45:
                if not wl.get("_45_skip_logged"):
                    logger.info("W4 SKIP :45 %s: :45 window anomaly (27%% WR)", cid[:8])
                    wl["_45_skip_logged"] = True
                del state["watchlist"][cid]
                continue

            _hkt_now = datetime.now(tz=_HKT)
            _hkt_hm = _hkt_now.hour * 60 + _hkt_now.minute
            if 165 <= _hkt_hm < 450:
                if not wl.get("_dead_hour_logged"):
                    logger.info("W4 SKIP DEAD %s: HKT %02d:%02d (dead hours 02:45-07:30)", cid[:8], _hkt_now.hour, _hkt_now.minute)
                    wl["_dead_hour_logged"] = True
                del state["watchlist"][cid]
                continue

            _w4_dir, _w4_mag, _w4_ret = w4_signal(wl["start_ms"], _sym, ws_binance=ws_binance)
            if _w4_dir == "WAIT":
                continue
            if _w4_dir == "SKIP":
                if _elapsed_ms > 600_000:
                    logger.info("W4 SKIP %s: mag=%.1f bps < %d bps after 10min",
                                cid[:8], _w4_mag, _W4_THRESHOLD_BPS)
                    del state["watchlist"][cid]
                continue
            _m1 = _w4_ret
            logger.info("W4 SIGNAL %s: %s %+.1f bps", cid[:8], _w4_dir, _w4_mag)

            _up_mid = 0.0
            _dn_mid = 0.0
            if client and hasattr(client, "get_order_book") and not dry_run:
                try:
                    _up_book = client.get_order_book(wl["up_tok"])
                    _up_bids = _up_book.get("bids", [])
                    _up_asks = _up_book.get("asks", [])
                    if _up_bids and _up_asks:
                        _up_mid = (max(b["price"] for b in _up_bids) + min(a["price"] for a in _up_asks)) / 2
                    _dn_book = client.get_order_book(wl["dn_tok"])
                    _dn_bids = _dn_book.get("bids", [])
                    _dn_asks = _dn_book.get("asks", [])
                    if _dn_bids and _dn_asks:
                        _dn_mid = (max(b["price"] for b in _dn_bids) + min(a["price"] for a in _dn_asks)) / 2
                except Exception as e:
                    logger.debug("W4 OB fetch failed: %s", e)

            if _up_mid <= 0.01 or _up_mid >= 0.99:
                _up_mid = max(0.05, min(0.95, fair))
            if _dn_mid <= 0.01 or _dn_mid >= 0.99:
                _dn_mid = max(0.05, min(0.95, 1.0 - fair))

            _TICK = 0.02  # 2026-03-26: widened from 0.01 → 0.02 (EV +30%, see compare_config_sync)
            _up_bid = round(max(0.02, _up_mid - _TICK), 2)
            _dn_bid = round(max(0.02, _dn_mid - _TICK), 2)
            _bs_combined = round(_up_bid + _dn_bid, 4)

            if _bs_combined >= 0.99:
                logger.info("W4 SKIP %s: combined $%.4f >= $0.99 (arb spread too thin)",
                            cid[:8], _bs_combined)
                del state["watchlist"][cid]
                continue

            _cheap_bid = min(_up_bid, _dn_bid)
            if _cheap_bid > 0.31 or _cheap_bid < 0.02:
                logger.info("W4 SKIP %s: cheap side $%.2f outside 2¢-31¢ range",
                            cid[:8], _cheap_bid)
                del state["watchlist"][cid]
                continue

            _CHEAP_TIERS = [
                (0.17, 1.0), (0.20, 0.80), (0.23, 0.60), (0.27, 0.40), (0.31, 0.20),
            ]
            _cheap_mult = 0.20
            _cheap_tier = 1
            for _ct_i, (_ct_max, _ct_mult) in enumerate(_CHEAP_TIERS):
                if _cheap_bid <= _ct_max:
                    _cheap_mult = _ct_mult
                    _cheap_tier = 5 - _ct_i
                    break

            bankroll = state.get("bankroll", 100.0)
            _coin_bet_pct = _BET_PCT_BY_COIN.get(_coin_slug, config.bet_pct)
            _full_budget = bankroll * _coin_bet_pct * daily_budget_mult * _cheap_mult
            if _full_budget < _MIN_VIABLE_BUDGET:
                logger.info("W4 SKIP %s: budget $%.2f < $%.2f min (tier %d, mult %.0f%%)",
                            cid[:8], _full_budget, _MIN_VIABLE_BUDGET, _cheap_tier, _cheap_mult * 100)
                del state["watchlist"][cid]
                continue
            _budget = _full_budget * _W4_T1_PCT
            _lean_dir = _w4_dir

            _avg_price = (_up_bid + _dn_bid) / 2
            _total_shares = max(10, _budget / _avg_price)

            _dyn_ratio = w4_dynamic_ratio(_w4_mag)
            _lean_share_frac = _dyn_ratio / (_dyn_ratio + 1)
            _hedge_share_frac = 1.0 / (_dyn_ratio + 1)

            if _lean_dir == "UP":
                _up_shares = max(config.min_order_size, round(_total_shares * _lean_share_frac, 1))
                _dn_shares = max(config.min_order_size, round(_total_shares * _hedge_share_frac, 1))
            else:
                _up_shares = max(config.min_order_size, round(_total_shares * _hedge_share_frac, 1))
                _dn_shares = max(config.min_order_size, round(_total_shares * _lean_share_frac, 1))

            _est_cost = _up_shares * _up_bid + _dn_shares * _dn_bid
            if _est_cost > _budget * 1.5 and _budget > 0:
                _scale = _budget / _est_cost
                _up_shares = max(1, round(_up_shares * _scale, 1))
                _dn_shares = max(1, round(_dn_shares * _scale, 1))
                logger.info("W4 BUDGET CAP %s: est $%.2f > budget $%.2f → scaled to %.1f/%.1f shares",
                            cid[:8], _est_cost, _budget, _up_shares, _dn_shares)

            if cid in state.get("markets", {}):
                logger.warning("W4 DUP %s: already in markets, skip", cid[:8])
                del state["watchlist"][cid]
                continue

            orders = [
                PlannedOrder(token_id=wl["up_tok"], side="BUY",
                             price=_up_bid, size=_up_shares, outcome="UP"),
                PlannedOrder(token_id=wl["dn_tok"], side="BUY",
                             price=_dn_bid, size=_dn_shares, outcome="DOWN"),
            ]
            _cond_rungs_config = []
            n_tranches = 1
            _h_imbalance, _h_delta = 0.0, 0.0
            _whale_action = "NORMAL"
            _cvd_strong_disagree = False
            if not state.get("_w4_live"):
                _observe_only = True

            try:
                _bs_entry = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_entry",
                    "cid": cid[:8], "coin": _coin_slug,
                    "lean_dir": _lean_dir, "lean_ratio": _dyn_ratio,
                    "w4_mag_bps": round(_w4_mag, 1),
                    "up_mid": round(_up_mid, 4), "dn_mid": round(_dn_mid, 4),
                    "up_bid": _up_bid, "dn_bid": _dn_bid,
                    "combined": _bs_combined, "cheap_tier": _cheap_tier,
                    "cheap_mult": _cheap_mult, "cheap_bid": _cheap_bid,
                    "up_shares": _up_shares, "dn_shares": _dn_shares,
                    "budget": round(_budget, 2), "bankroll": round(bankroll, 2),
                    "bridge": round(bridge_p_up, 4), "live": not _observe_only,
                }
                with open(_BOTHSIDES_LOG, "a") as _bsf:
                    _bsf.write(json.dumps(_bs_entry) + "\n")
            except Exception:
                pass

            logger.info("W4 %s %s: lean=%s %+.1fbps | UP@$%.2f×%.0f + DN@$%.2f×%.0f = $%.3f %s",
                        "LIVE" if not _observe_only else "PAPER",
                        cid[:8], _lean_dir, _w4_mag,
                        _up_bid, _up_shares, _dn_bid, _dn_shares,
                        _bs_combined, "(PAPER)" if _observe_only else "")
        else:
            # ── Original directional logic ──
            if abs(_m1) >= 0.001 and _fair_up != _m1_up:
                logger.info("SKIP %s: M1/fair CONFLICT (M1=%+.4f %s, fair=%.3f %s)",
                            cid[:8], _m1, "UP" if _m1_up else "DN",
                            fair, "UP" if _fair_up else "DN")
                continue

            _cvd_agrees = (_fair_up and _cvd > 0.50) or (not _fair_up and _cvd < 0.50)
            _cvd_strong_disagree = (_fair_up and _cvd < 0.45) or (not _fair_up and _cvd > 0.55)
            if _cvd_strong_disagree:
                logger.info("CVD DISAGREE %s: fair %s but CVD %.0f%% → reduced size",
                            cid[:8], "UP" if _fair_up else "DN", _cvd * 100)

            if client and hasattr(client, "get_midpoint") and not dry_run:
                _dir_tok = wl["up_tok"] if fair > 0.50 else wl["dn_tok"]
                _mid = poly_midpoint(client, _dir_tok, ws_poly=ws_poly)
                if 0 < _mid < 0.38:
                    logger.info("SKIP %s: market mid=%.3f < 0.38 → market disagrees",
                                cid[:8], _mid)
                    continue

            if not _observe_only:
                _tte_s = (wl["end_ms"] - now_ms) / 1000
                _holder_ttl = 5 if _tte_s < 120 else _HOLDER_CACHE_TTL
                _h_imbalance, _h_delta = holder_imbalance(cid, wl["up_tok"], ttl_override=_holder_ttl)
            else:
                _h_imbalance, _h_delta = 0.0, 0.0
            _whale_action = "NORMAL"
            _whale_favors_up = _h_imbalance > 0
            if abs(_h_imbalance) > 0.30:
                _whale_agrees = (_fair_up and _whale_favors_up) or (not _fair_up and not _whale_favors_up)
                if not _whale_agrees:
                    _whale_action = "FOLLOW_LOG"

        # ── Wide Ladder DCA ──
        if not both_sides:
            _LADDER_AUTO = [0.43, 0.37]
            _LADDER_COND = [0.31, 0.26]
            _LADDER_BUDGET_PCT = config.bet_pct

            bankroll = state.get("bankroll", 100.0)
            n_tranches = calc_tranches(bankroll, config)
            _all_rungs = _LADDER_AUTO + _LADDER_COND
            _window_budget = bankroll * _LADDER_BUDGET_PCT * daily_budget_mult / max(1, n_tranches)
            _rung_budget = _window_budget / len(_all_rungs)

            _dir_tok = wl["up_tok"] if _fair_up else wl["dn_tok"]
            _dir_side = "UP" if _fair_up else "DOWN"

            orders = []
            for _rung_price in _LADDER_AUTO:
                _shares = max(config.min_order_size, _rung_budget / _rung_price)
                orders.append(PlannedOrder(
                    token_id=_dir_tok, side="BUY",
                    price=_rung_price, size=round(_shares, 1),
                    outcome=_dir_side))

            _cond_rungs_config = []
            if _whale_action not in ("FOLLOW_LOG", "EXIT"):
                for _rung_price in _LADDER_COND:
                    _shares = max(config.min_order_size, _rung_budget / _rung_price)
                    _cond_rungs_config.append({
                        "price": _rung_price, "size": round(_shares, 1),
                        "token_id": _dir_tok, "outcome": _dir_side, "placed": False,
                    })
            else:
                logger.info("CHECKPOINT %s: deep rungs disabled (whale=%s)",
                            cid[:8], _whale_action)

            if not orders:
                del state["watchlist"][cid]
                continue

        # CVD disagree → single cheap rung
        if not both_sides:
            if _cvd_strong_disagree and orders:
                _our_fair = fair if _fair_up else (1.0 - fair)
                _disagree_bid = round(max(0.25, min(0.35, _our_fair * 0.60)), 3)
                _dir_tok = orders[0].token_id
                _dir_side = orders[0].outcome
                orders = [PlannedOrder(
                    token_id=_dir_tok, side="BUY",
                    price=_disagree_bid, size=config.min_order_size, outcome=_dir_side)]
                logger.info("CVD REDUCED %s: 1 rung @ $%.3f × %.0f (was %d orders)",
                            cid[:8], _disagree_bid, config.min_order_size, len(orders) + 1)

            if abs(_h_imbalance) > 0.30:
                _whale_agrees = (_fair_up and _whale_favors_up) or (not _fair_up and not _whale_favors_up)
                if _whale_agrees:
                    _whale_action = "AGREE"
                    logger.info("WHALE AGREE %s: imbalance %+.3f confirms %s",
                                cid[:8], _h_imbalance, "UP" if _fair_up else "DOWN")
                elif _whale_action == "FOLLOW_LOG":
                    logger.warning("WHALE FOLLOW(log) %s: imbalance %+.3f — halving orders",
                                   cid[:8], _h_imbalance)
                    if orders:
                        for o in orders:
                            o.size = max(config.min_order_size, o.size * 0.5)

            if abs(_h_delta) > 0.15 and _whale_action == "NORMAL":
                _delta_against = (_fair_up and _h_delta < 0) or (not _fair_up and _h_delta > 0)
                if _delta_against:
                    _whale_action = "EXIT"
                    logger.warning("WHALE EXIT %s: imbalance Δ%+.3f AGAINST %s — halve size",
                                   cid[:8], _h_delta, "UP" if _fair_up else "DOWN")
                    if orders:
                        for o in orders:
                            o.size = max(config.min_order_size, o.size * 0.5)

        _sig_ctx = {"fair": round(fair, 4), "bridge": round(bridge_p_up, 4),
                    "cvd": round(_cvd, 3), "vol": round(_coin_vol, 6),
                    "m1": round(_m1, 6), "ob_adj": round(ob_adjustment, 4),
                    "btc": round(_coin_price, 2),
                    "ob_best_bid": round(_ob_best_bid, 4),
                    "ob_best_ask": round(_ob_best_ask, 4),
                    "ob_bid_vol": round(_ob_bid_vol, 1),
                    "ob_ask_vol": round(_ob_ask_vol, 1),
                    "ob_depth": _ob_depth,
                    "coin": _coin_slug, "observe_only": _observe_only,
                    "h_imb": _h_imbalance, "h_delta": _h_delta,
                    "whale": _whale_action}

        if _observe_only:
            _sig_ctx["paper"] = True
            _paper_client = PaperClient()
            results = execute_fn(orders, _paper_client, cid=cid, signal_ctx=_sig_ctx)
            logger.info("PAPER %s %s: fair=%.3f bridge=%.3f %d orders simulated",
                        cid[:8], _coin_slug.upper(), fair, bridge_p_up, len(orders))
        else:
            results = execute_fn(orders, client, cid=cid, signal_ctx=_sig_ctx)
        ms = MMMarketState(condition_id=cid, title=wl["title"],
                           up_token_id=wl["up_tok"], down_token_id=wl["dn_tok"],
                           window_start_ms=wl["start_ms"], window_end_ms=wl["end_ms"],
                           btc_open_price=_coin_open, phase="OPEN")

        pending = []
        for r in results:
            if not r.get("submitted"):
                continue
            bump_fill(state, "submitted")
            status = r.get("status", "")
            if status == "matched":
                _fill_sz = r.get("size_matched", r["size"])
                apply_fill(ms, r["outcome"], "BUY", r["price"], _fill_sz)
                bump_fill(state, "filled")
                logger.info("INSTANT FILL %s %s: %.1f @ $%.3f",
                            cid[:8], r["outcome"], _fill_sz, r["price"])
            else:
                pending.append(r)

        mkt_dict = to_dict(ms)
        mkt_dict["pending_orders"] = pending
        mkt_dict["fills_confirmed"] = len(pending) == 0
        mkt_dict["entry_price"] = _coin_price
        mkt_dict["entry_ts"] = int(time.time())
        mkt_dict["tranches_done"] = 1
        mkt_dict["tranches_total"] = n_tranches
        mkt_dict["original_dir"] = "UP" if fair > 0.50 else "DOWN"
        if _whale_action == "FOLLOW_LOG":
            mkt_dict["whale_disagree"] = True
        mkt_dict["rounds"] = 0
        mkt_dict["phased_rungs"] = _cond_rungs_config
        if both_sides:
            mkt_dict["both_sides"] = True
            mkt_dict["bs_combined"] = _bs_combined
            mkt_dict["_w4_t2_pending"] = True
            mkt_dict["_w4_t1_dir"] = _lean_dir
            mkt_dict["_w4_t1_ratio"] = _dyn_ratio
            mkt_dict["_w4_full_budget"] = round(_full_budget, 4)
        if _observe_only:
            mkt_dict["paper"] = True
        state["markets"][cid] = mkt_dict
        del state["watchlist"][cid]
        active += 1

        t_str = f" T1/{n_tranches}" if n_tranches > 1 else ""
        filled_str = f"UP={ms.up_shares:.0f} DN={ms.down_shares:.0f}"
        pending_str = ",".join(r["outcome"] for r in pending)
        cost_str = f"${ms.entry_cost:.2f}" if ms.entry_cost > 0 else "$0"
        print(f"  OPEN {cid[:8]} | {filled_str} | pend: {pending_str or '-'} | {cost_str}{t_str}")


def try_w4_t2(state: dict, client, dry_run: bool,
              now_ms: int, is_heavy: bool, both_sides: bool,
              config=None, ws_binance=None, execute_fn=None) -> None:
    """W4 Tranche 2: confirmation entry at T+480s.

    🔴 Data: T+480s confirms T+300s → 86.1% WR (vs 76.1% base).
    T+480s flips → 21.5% WR. Skip saves ~24% of losing trades.
    """
    from polymarket.strategy.market_maker import PlannedOrder
    if execute_fn is None:
        from polymarket.mm.order_lifecycle import execute as execute_fn

    if not (both_sides and is_heavy):
        return

    for cid, mkt_d in list(state["markets"].items()):
        if not mkt_d.get("_w4_t2_pending"):
            continue
        if mkt_d["phase"] != "OPEN":
            mkt_d["_w4_t2_pending"] = False
            continue

        _t1_has_position = (mkt_d.get("up_shares", 0) > 0 or mkt_d.get("down_shares", 0) > 0
                            or mkt_d.get("pending_orders", []))
        if not _t1_has_position:
            logger.info("W4 T2 SKIP %s: T1 has zero position (all cancelled)", cid[:8])
            mkt_d["_w4_t2_pending"] = False
            continue

        if mkt_d.get("tranches_done", 1) >= 2:
            mkt_d["_w4_t2_pending"] = False
            continue

        start_ms = mkt_d.get("window_start_ms", 0)
        end_ms = mkt_d.get("window_end_ms", 0)
        if start_ms <= 0:
            mkt_d["_w4_t2_pending"] = False
            continue
        _t2_elapsed_s = (now_ms - start_ms) / 1000
        if _t2_elapsed_s < _W4_T2_DELAY_S:
            continue

        if end_ms > 0 and now_ms > end_ms - 180_000:
            logger.info("W4 T2 SKIP %s: too close to window end (%.0fs left)",
                        cid[:8], (end_ms - now_ms) / 1000)
            mkt_d["_w4_t2_pending"] = False
            continue

        _t2_title = mkt_d.get("title", "").lower()
        if "ethereum" in _t2_title:
            _t2_sym = "ETHUSDT"
        elif "solana" in _t2_title:
            _t2_sym = "SOLUSDT"
        else:
            _t2_sym = "BTCUSDT"

        _t2_dir, _t2_mag, _t2_ret = w4_signal(start_ms, _t2_sym, ws_binance=ws_binance)
        _t1_dir = mkt_d.get("_w4_t1_dir", "")

        if _t2_dir == _t1_dir and _t2_mag >= _W4_THRESHOLD_BPS:
            _t2_budget = mkt_d.get("_w4_full_budget", 0) * _W4_T2_PCT
            if _t2_budget < 1.0:
                logger.info("W4 T2 SKIP %s: budget $%.2f too small", cid[:8], _t2_budget)
                mkt_d["_w4_t2_pending"] = False
                continue

            _t2_up_mid, _t2_dn_mid = 0.0, 0.0
            _t2_up_tok = mkt_d.get("up_token_id", "")
            _t2_dn_tok = mkt_d.get("down_token_id", "")
            if client and hasattr(client, "get_order_book") and not dry_run:
                try:
                    _t2_ub = client.get_order_book(_t2_up_tok)
                    _t2_ubids = _t2_ub.get("bids", [])
                    _t2_uasks = _t2_ub.get("asks", [])
                    if _t2_ubids and _t2_uasks:
                        _t2_up_mid = (max(b["price"] for b in _t2_ubids) + min(a["price"] for a in _t2_uasks)) / 2
                    _t2_db = client.get_order_book(_t2_dn_tok)
                    _t2_dbids = _t2_db.get("bids", [])
                    _t2_dasks = _t2_db.get("asks", [])
                    if _t2_dbids and _t2_dasks:
                        _t2_dn_mid = (max(b["price"] for b in _t2_dbids) + min(a["price"] for a in _t2_dasks)) / 2
                except Exception as e:
                    logger.debug("W4 T2 OB fetch failed: %s", e)

            if _t2_up_mid <= 0.01 or _t2_up_mid >= 0.99:
                _t2_up_mid = 0.50
            if _t2_dn_mid <= 0.01 or _t2_dn_mid >= 0.99:
                _t2_dn_mid = 0.50

            _t2_up_bid = round(max(0.02, _t2_up_mid - 0.02), 2)  # synced with T1 TICK (2026-03-26)
            _t2_dn_bid = round(max(0.02, _t2_dn_mid - 0.02), 2)
            _t2_combined = round(_t2_up_bid + _t2_dn_bid, 4)

            if _t2_combined >= 0.99:
                logger.info("W4 T2 SKIP %s: combined $%.4f >= $0.99 (arb spread too thin)",
                            cid[:8], _t2_combined)
                mkt_d["_w4_t2_pending"] = False
                continue

            _t2_ratio = mkt_d.get("_w4_t1_ratio", _W4_LEAN_RATIO)
            _t2_avg_price = (_t2_up_bid + _t2_dn_bid) / 2
            _t2_total_shares = max(6, _t2_budget / _t2_avg_price)
            _t2_lean_frac = _t2_ratio / (_t2_ratio + 1)
            _t2_hedge_frac = 1.0 / (_t2_ratio + 1)

            _min_sz = config.min_order_size if config else 5
            if _t1_dir == "UP":
                _t2_up_sh = max(_min_sz, round(_t2_total_shares * _t2_lean_frac, 1))
                _t2_dn_sh = max(_min_sz, round(_t2_total_shares * _t2_hedge_frac, 1))
            else:
                _t2_up_sh = max(_min_sz, round(_t2_total_shares * _t2_hedge_frac, 1))
                _t2_dn_sh = max(_min_sz, round(_t2_total_shares * _t2_lean_frac, 1))

            _t2_orders = [
                PlannedOrder(token_id=_t2_up_tok, side="BUY",
                             price=_t2_up_bid, size=_t2_up_sh, outcome="UP"),
                PlannedOrder(token_id=_t2_dn_tok, side="BUY",
                             price=_t2_dn_bid, size=_t2_dn_sh, outcome="DOWN"),
            ]

            _t2_observe = mkt_d.get("paper", False) or not state.get("_w4_live")
            if _t2_observe:
                _t2_results = execute_fn(_t2_orders, T2PaperClient(), cid=cid)
            else:
                _t2_results = execute_fn(_t2_orders, client, cid=cid)

            for r in _t2_results:
                if r.get("submitted"):
                    bump_fill(state, "submitted")
                    if r.get("status") == "matched":
                        bump_fill(state, "filled")
                        _t2_out = r["outcome"]
                        _t2_px = r["price"]
                        _t2_sz = r.get("size_matched", r["size"])
                        if _t2_out == "UP":
                            _old_val = mkt_d.get("up_shares", 0) * mkt_d.get("up_avg_price", 0)
                            mkt_d["up_shares"] = mkt_d.get("up_shares", 0) + _t2_sz
                            mkt_d["up_avg_price"] = (_old_val + _t2_sz * _t2_px) / mkt_d["up_shares"] if mkt_d["up_shares"] > 0 else 0
                        elif _t2_out == "DOWN":
                            _old_val = mkt_d.get("down_shares", 0) * mkt_d.get("down_avg_price", 0)
                            mkt_d["down_shares"] = mkt_d.get("down_shares", 0) + _t2_sz
                            mkt_d["down_avg_price"] = (_old_val + _t2_sz * _t2_px) / mkt_d["down_shares"] if mkt_d["down_shares"] > 0 else 0
                        mkt_d["entry_cost"] = mkt_d.get("entry_cost", 0) + _t2_sz * _t2_px
                        logger.info("W4 T2 INSTANT FILL %s %s: %.1f @ $%.3f",
                                    cid[:8], _t2_out, _t2_sz, _t2_px)
                    else:
                        mkt_d.setdefault("pending_orders", []).append(r)

            try:
                _t2_entry = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_t2_entry",
                    "cid": cid[:8], "lean_dir": _t1_dir,
                    "t2_dir": _t2_dir, "t2_mag_bps": round(_t2_mag, 1),
                    "up_bid": _t2_up_bid, "dn_bid": _t2_dn_bid,
                    "combined": _t2_combined,
                    "up_shares": _t2_up_sh, "dn_shares": _t2_dn_sh,
                    "budget": round(_t2_budget, 2), "live": not _t2_observe,
                }
                with open(_BOTHSIDES_LOG, "a") as _bsf:
                    _bsf.write(json.dumps(_t2_entry) + "\n")
            except Exception:
                pass

            logger.info("W4 T2 %s %s: CONFIRMED %s %+.1fbps | UP@$%.2f×%.0f + DN@$%.2f×%.0f = $%.3f",
                        "LIVE" if not _t2_observe else "PAPER", cid[:8],
                        _t1_dir, _t2_mag,
                        _t2_up_bid, _t2_up_sh, _t2_dn_bid, _t2_dn_sh, _t2_combined)
            mkt_d["_w4_t2_pending"] = False
            mkt_d["tranches_done"] = 2

        else:
            logger.info("W4 T2 SKIP %s: T1=%s T2=%s mag=%.1fbps (need %s >=%dbps)",
                        cid[:8], _t1_dir, _t2_dir, _t2_mag,
                        _t1_dir, _W4_THRESHOLD_BPS)
            try:
                _t2_skip = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "w4_t2_skip",
                    "cid": cid[:8], "t1_dir": _t1_dir,
                    "t2_dir": _t2_dir, "t2_mag_bps": round(_t2_mag, 1),
                    "reason": "flip" if _t2_dir != _t1_dir else "below_threshold",
                }
                with open(_BOTHSIDES_LOG, "a") as _bsf:
                    _bsf.write(json.dumps(_t2_skip) + "\n")
            except Exception:
                pass
            mkt_d["_w4_t2_pending"] = False


def place_phased_rungs(state: dict, client, dry_run: bool,
                       now_ms: int, ws_poly=None,
                       execute_fn=None) -> None:
    """Phased rung placement: rungs 3-4 placed LIVE with 3-cycle cooldown.

    🔴 Places real orders after checkpoint passes.
    """
    from polymarket.strategy.market_maker import PlannedOrder
    if execute_fn is None:
        from polymarket.mm.order_lifecycle import execute as execute_fn

    _PHASED_COOLDOWN_CYCLES = 3
    if not client or dry_run:
        return

    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        _phased = mkt.get("phased_rungs", [])
        if not _phased:
            continue
        _end_ms = mkt.get("window_end_ms", 0)
        if _end_ms > 0 and now_ms > _end_ms - 120_000:
            continue

        _t = mkt.get("title", "").lower()
        _sym_pr = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"

        for _pr in _phased:
            if _pr.get("placed") or _pr.get("blocked"):
                continue
            _tok_id = _pr["token_id"]
            _mid = poly_midpoint(client, _tok_id, ws_poly=ws_poly) if hasattr(client, "get_midpoint") else 0
            if _mid <= 0 or _mid > _pr["price"] + 0.05:
                _pr.pop("_approach_count", None)
                continue

            _pr["_approach_count"] = _pr.get("_approach_count", 0) + 1
            if _pr["_approach_count"] < _PHASED_COOLDOWN_CYCLES:
                if _pr["_approach_count"] == 1:
                    logger.info("PHASED OBSERVE %s $%.2f: mid=%.3f approaching, waiting %d cycles...",
                                cid[:8], _pr["price"], _mid, _PHASED_COOLDOWN_CYCLES)
                continue

            _pass = True
            _reasons = []
            _our_dir = mkt.get("original_dir", "UP")

            _h_imb, _ = holder_imbalance(cid, mkt.get("up_token_id", ""), ttl_override=5)
            _whale_up = _h_imb > 0
            _whale_against = (_our_dir == "UP" and not _whale_up and abs(_h_imb) > 0.20) or \
                             (_our_dir == "DOWN" and _whale_up and abs(_h_imb) > 0.20)
            if _whale_against:
                _pass = False
                _reasons.append(f"whale({_h_imb:+.2f})")

            _entry_px = mkt.get("entry_price", 0)
            _now_px = price(_sym_pr)
            if _entry_px > 0 and _now_px > 0:
                _move = (_now_px - _entry_px) / _entry_px
                _is_adverse = (_our_dir == "UP" and _move < -0.003) or \
                              (_our_dir == "DOWN" and _move > 0.003)
                if _is_adverse:
                    _pass = False
                    _reasons.append(f"adverse({_move:+.3%})")

            if _mid <= _pr["price"]:
                _pass = False
                _reasons.append(f"mid({_mid:.3f})<rung({_pr['price']:.2f})")

            if _pass:
                try:
                    _results = execute_fn(
                        [PlannedOrder(token_id=_tok_id, side="BUY",
                                      price=_pr["price"], size=_pr["size"],
                                      outcome=_pr["outcome"])],
                        client, cid=cid)
                    _pr["placed"] = True
                    for _r in (_results or []):
                        if _r.get("submitted"):
                            mkt.setdefault("pending_orders", []).append({
                                "order_id": _r.get("order_id", ""),
                                "outcome": _pr["outcome"],
                                "price": _pr["price"], "size": _pr["size"],
                            })
                    logger.info("PHASED RUNG %s: $%.2f × %.1f placed (mid=%.3f, 3 checks passed after %d cycles)",
                                cid[:8], _pr["price"], _pr["size"], _mid, _pr["_approach_count"])
                except Exception as e:
                    logger.warning("Phased rung failed %s: %s", cid[:8], e)
            else:
                _pr["blocked"] = True
                logger.info("PHASED BLOCKED %s $%.2f: %s (after %d cycles observation)",
                            cid[:8], _pr["price"], " + ".join(_reasons), _pr["_approach_count"])
            break


def try_reentry(state: dict, client, config, dry_run: bool,
                now_ms: int, is_heavy: bool, risk_mode: str,
                ws_binance=None, ws_poly=None,
                execute_fn=None) -> None:
    """Re-entry: scalp again in same window after early exit.

    🔴 Places real orders with round-dependent pricing discount.
    R2: bid × 0.90 (10% cheaper), R3: bid × 0.80 (20% cheaper).
    """
    from polymarket.core.context import PolyMarket
    from polymarket.strategy.market_maker import (
        PlannedOrder, calc_tranches, compute_fair_up, plan_opening,
    )
    if execute_fn is None:
        from polymarket.mm.order_lifecycle import execute as execute_fn

    if not (is_heavy and client and not dry_run):
        return

    for cid, mkt in list(state["markets"].items()):
        if mkt["phase"] != "OPEN":
            continue
        _rd = mkt.get("rounds", 0)
        if _rd < 1 or _rd >= _MAX_ROUNDS:
            continue
        if mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0:
            continue
        _last_sell = mkt.get("last_sell_ts", 0)
        if time.time() - _last_sell < _REENTRY_COOLDOWN_S:
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms - 90_000:
            logger.info("REENTRY SKIP %s R%d: < 1.5 min remaining", cid[:8], _rd + 1)
            mkt["phase"] = "RESOLVED"
            mkt["early_exit"] = f"window_end_r{_rd}"
            continue

        _title_lower = mkt.get("title", "").lower()
        _sym = "ETHUSDT" if "ethereum" in _title_lower else "BTCUSDT"
        _m1 = m1_return(_sym)
        _m1_vol = vol_1m(_sym)
        _m1_thresh = max(0.0005, _m1_vol * 1.0)
        if abs(_m1) < _m1_thresh:
            logger.debug("REENTRY WAIT %s R%d: M1 weak |%.4f| < %.4f",
                         cid[:8], _rd + 1, _m1, _m1_thresh)
            continue

        _xprice, _xdiv = cross_exchange_price(_sym)
        _coin_price = _xprice if _xprice > 0 else price(_sym, ws_binance=ws_binance)
        if _xdiv > 0.003:
            logger.debug("REENTRY WAIT %s R%d: cross-exchange divergence %.2f%%",
                         cid[:8], _rd + 1, _xdiv * 100)
            continue

        _coin_open = mkt.get("btc_open_price") or _coin_price
        if _coin_open > 0:
            _window_move = abs(_coin_price - _coin_open) / _coin_open
            if _window_move > 0.003:
                logger.info("REENTRY SKIP %s R%d: window move %.2f%% > 0.3%% (regime change)",
                            cid[:8], _rd + 1, _window_move * 100)
                mkt["phase"] = "RESOLVED"
                mkt["early_exit"] = f"regime_change_r{_rd}"
                continue

        start_ms = mkt.get("window_start_ms", 0)
        mins_left = max(1, (end_ms - now_ms) / 60_000)

        bridge_p_up = compute_fair_up(_coin_price, _coin_open, _m1_vol, int(mins_left))

        ob_adjustment = 0.0
        if hasattr(client, "get_order_book"):
            try:
                up_book = client.get_order_book(mkt.get("up_token_id", ""))
                bid_vol = sum(b["size"] for b in up_book.get("bids", []))
                ask_vol = sum(a["size"] for a in up_book.get("asks", []))
                if bid_vol + ask_vol > 0:
                    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                    ob_adjustment = imbalance * 0.05
            except Exception:
                pass

        fair = bridge_p_up + ob_adjustment
        fair = max(0.05, min(0.95, fair))

        _fair_up = fair > 0.50
        _m1_up = _m1 > 0
        if abs(_m1) >= 0.001 and _fair_up != _m1_up:
            logger.info("REENTRY SKIP %s R%d: M1/fair conflict", cid[:8], _rd + 1)
            continue

        if hasattr(client, "get_midpoint"):
            _dir_tok = mkt.get("up_token_id", "") if fair > 0.50 else mkt.get("down_token_id", "")
            _mid = poly_midpoint(client, _dir_tok, ws_poly=ws_poly)
            if 0 < _mid < 0.38:
                logger.info("REENTRY SKIP %s R%d: market mid=%.3f < 0.38",
                            cid[:8], _rd + 1, _mid)
                continue

        _round_discount = {1: 0.90, 2: 0.80}.get(_rd, 0.80)
        _re_config = _copy(config)
        _re_config.max_directional_bid = round(config.max_directional_bid * _round_discount, 3)
        _re_config.max_hedge_bid = round(config.max_hedge_bid * _round_discount, 3)
        logger.info("REENTRY R%d %s: bid cap $%.3f (%.0f%% of R1 $%.3f)",
                    _rd + 1, cid[:8], _re_config.max_directional_bid,
                    _round_discount * 100, config.max_directional_bid)
        _re_mkt = PolyMarket(
            condition_id=cid, title=mkt.get("title", ""),
            category="crypto_15m",
            yes_token_id=mkt.get("up_token_id", ""),
            no_token_id=mkt.get("down_token_id", ""),
            liquidity=15000)
        bankroll = state.get("bankroll", 100.0)
        n_tranches = calc_tranches(bankroll, _re_config)
        orders = plan_opening(_re_mkt, fair, _re_config, bankroll=bankroll,
                              tranche=0, total_tranches=n_tranches,
                              risk_mode=risk_mode)
        if not orders:
            continue

        results = execute_fn(orders, client, cid=cid,
                             signal_ctx={"fair": round(fair, 4), "round": _rd + 1,
                                         "bridge": round(bridge_p_up, 4)})
        mkt["phased_rungs"] = []
        mkt["entry_price"] = _coin_price
        mkt["entry_ts"] = int(time.time())
        mkt["up_avg_price"] = 0
        mkt["down_avg_price"] = 0
        mkt["entry_cost"] = 0
        mkt["fills_confirmed"] = True
        mkt["original_dir"] = "UP" if fair > 0.50 else "DOWN"
        mkt["_tp_tier_UP"] = 0
        mkt["_tp_tier_DOWN"] = 0
        mkt["cost_recovered"] = False
        mkt["tranches_done"] = 1
        mkt["tranches_total"] = n_tranches
        pending = []
        for r in results:
            if not r.get("submitted"):
                continue
            bump_fill(state, "submitted")
            status = r.get("status", "")
            if status == "matched":
                outcome = r["outcome"]
                _rprice = r["price"]
                size = r.get("size_matched", r["size"])
                if outcome == "UP":
                    old = mkt["up_shares"] * mkt["up_avg_price"]
                    mkt["up_shares"] += size
                    mkt["up_avg_price"] = (old + size * _rprice) / mkt["up_shares"] if mkt["up_shares"] > 0 else 0
                elif outcome == "DOWN":
                    old = mkt["down_shares"] * mkt["down_avg_price"]
                    mkt["down_shares"] += size
                    mkt["down_avg_price"] = (old + size * _rprice) / mkt["down_shares"] if mkt["down_shares"] > 0 else 0
                mkt["entry_cost"] += size * _rprice
                bump_fill(state, "filled")
                logger.info("REENTRY FILL R%d %s %s: %.1f @ $%.3f",
                            _rd + 1, cid[:8], outcome, size, _rprice)
            else:
                pending.append(r)
        if pending:
            mkt["pending_orders"] = pending
            mkt["fills_confirmed"] = False

        _new_dir = "UP" if fair > 0.50 else "DOWN"
        logger.info("REENTRY R%d %s dir=%s fair=%.3f (prev_dir=%s)",
                    _rd + 1, cid[:8], _new_dir, fair,
                    mkt.get("_prev_dir", mkt.get("original_dir", "?")))
        mkt["_prev_dir"] = _new_dir
