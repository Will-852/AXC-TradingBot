"""
mm/exit_logic.py — Resolution, exits, endgame, hedge for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🔴 2CHECK: Every function here sells real shares or resolves real PnL.
"""

import json
import logging
import math
import os
import time
import urllib.request
from collections import deque
from copy import copy as _copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from polymarket.mm.constants import (
    _BLACK_SWAN_MID, _BLACK_SWAN_SELL_PCT, _BOTHSIDES_LOG,
    _COST_RECOVERY_MID, _ENDGAME_DAILY_CAP, _ENDGAME_ENABLED,
    _ENDGAME_FLIP_RANGE, _ENDGAME_MID_RANGE, _ENDGAME_REVERSAL,
    _ENDGAME_SHARES, _ENDGAME_TTE_START, _ENDGAME_TTE_STOP,
    _EXIT_STOP_PCT, _HEDGE_BTC_THRESHOLD, _HEDGE_PCT, _HKT,
    _LIVE_TRADE_COINS, _LOG_DIR, _MAX_ROUNDS, _REENTRY_COOLDOWN_S,
    coin_from_title,
)
from polymarket.mm.data_feeds import (
    btc_price, cross_exchange_price, m1_return, poly_midpoint, price, vol_1m,
)
from polymarket.mm.state_io import fill_rate, from_dict, log_trade, to_dict, bump_fill

logger = logging.getLogger(__name__)

# Module-level mutable state
endgame_mid_buf: dict[str, deque] = {}  # cid → deque of (ts, mid)
btc_price_buf: deque = deque(maxlen=120)  # (ts, price) for 30s BTC momentum


def check_resolutions(state: dict, client=None) -> None:
    """Check if any open markets have resolved and compute PnL.

    🔴 Pre-resolve reconciliation: compares bot state vs on-chain trades.
    Uses chain data when bot state drifted (phantom fills, partial fills).
    """
    from polymarket.strategy.market_maker import resolve_market

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
        _title = md.get("title", "").lower()
        _sym = "ETHUSDT" if "ethereum" in _title else "BTCUSDT"
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={_sym}&interval={interval}&startTime={start_ms}&limit=1")
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

        ms = from_dict(md)
        _is_paper = md.get("paper", False)

        # ── Pre-resolve reconciliation: bot state vs on-chain trades ──
        _on_chain_up = -1.0
        _on_chain_dn = 0.0
        try:
            _trades = (
                client.get_trades(market=cid)
                if client and hasattr(client, "get_trades")
                else []
            )
            if _trades:
                _on_chain_up = 0.0
                _on_chain_dn = 0.0
                for _t in _trades:
                    _side = _t.get("side", "")
                    _asset_id = _t.get("asset_id", "")
                    _size = float(_t.get("size", 0))
                    if _asset_id == ms.up_token_id:
                        if _side == "BUY":
                            _on_chain_up += _size
                        elif _side == "SELL":
                            _on_chain_up -= _size
                    elif _asset_id == ms.down_token_id:
                        if _side == "BUY":
                            _on_chain_dn += _size
                        elif _side == "SELL":
                            _on_chain_dn -= _size
        except Exception as e:
            logger.warning("RECONCILE FAIL %s: %s — using bot state", cid[:8], e)

        if _on_chain_up >= 0:
            _bot_up = ms.up_shares
            _bot_dn = ms.down_shares
            if abs(_on_chain_up - _bot_up) > 1 or abs(_on_chain_dn - _bot_dn) > 1:
                _has_cost = (ms.up_avg_price > 0 or ms.down_avg_price > 0)
                if not _has_cost:
                    logger.error(
                        "RECONCILE MISMATCH %s: bot UP=%.1f DN=%.1f"
                        " | chain UP=%.1f DN=%.1f — BUT avg_price=0, "
                        "CANNOT reconcile cost → using BOT state (undercount)",
                        cid[:8], _bot_up, _bot_dn, _on_chain_up, _on_chain_dn)
                else:
                    logger.error(
                        "RECONCILE MISMATCH %s: bot UP=%.1f DN=%.1f"
                        " | chain UP=%.1f DN=%.1f — USING CHAIN",
                        cid[:8], _bot_up, _bot_dn, _on_chain_up, _on_chain_dn)
                    ms.up_shares = _on_chain_up
                    ms.down_shares = _on_chain_dn
                    ms.entry_cost = (
                        _on_chain_up * ms.up_avg_price
                        + _on_chain_dn * ms.down_avg_price
                    )

        pnl = resolve_market(ms, result)
        state["markets"][cid] = to_dict(ms)

        # Paper trades
        if _is_paper:
            state["paper_pnl"] = state.get("paper_pnl", 0) + pnl
            state["paper_markets"] = state.get("paper_markets", 0) + 1
            _coin_label = "ETH" if "ethereum" in md.get("title", "").lower() else ("SOL" if "solana" in md.get("title", "").lower() else "BTC")
            log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                       "result": result, "pnl": round(pnl, 4),
                       "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                       "paper_total_pnl": round(state.get("paper_pnl", 0), 2),
                       "coin": _coin_label, "paper": True},
                      log_path=os.path.join(_LOG_DIR, "mm_paper_trades.jsonl"),
                      coin=_coin_label)
            if md.get("both_sides"):
                _bs_res = {
                    "ts": datetime.now(tz=_HKT).isoformat(), "event": "resolution",
                    "cid": cid[:8], "coin": _coin_label, "result": result,
                    "pnl": round(pnl, 4),
                    "up_shares": md.get("up_shares", 0),
                    "down_shares": md.get("down_shares", 0),
                    "combined": md.get("bs_combined", 0),
                    "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                    "both_filled": md.get("up_shares", 0) > 0 and md.get("down_shares", 0) > 0,
                    "single_fill": (md.get("up_shares", 0) > 0) != (md.get("down_shares", 0) > 0),
                }
                try:
                    with open(_BOTHSIDES_LOG, "a") as _bsf:
                        _bsf.write(json.dumps(_bs_res) + "\n")
                except Exception:
                    pass
            d = "↑" if result == "UP" else "↓"
            _bs_tag = " [BS]" if md.get("both_sides") else ""
            print(f"  PAPER {_coin_label} {cid[:8]} {d}{_bs_tag} | PnL ${pnl:+.2f} | Paper Total ${state.get('paper_pnl', 0):.2f}")
            continue

        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] += 1

        if pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 8:
                cd = datetime.now(tz=_HKT) + timedelta(hours=24)
                state["cooldown_until"] = cd.isoformat()
                logger.warning("8 consecutive losses → COOLDOWN until %s", cd.strftime("%H:%M HKT"))
        else:
            state["consecutive_losses"] = 0

        # W/L Ratio Monitor
        _wins_list = [m.get("realized_pnl", 0) for m in state["markets"].values()
                      if m.get("phase") == "RESOLVED" and m.get("realized_pnl", 0) > 0 and not m.get("paper")]
        _losses_list = [abs(m.get("realized_pnl", 0)) for m in state["markets"].values()
                        if m.get("phase") == "RESOLVED" and m.get("realized_pnl", 0) < 0 and not m.get("paper")]
        _n_filled = len(_wins_list) + len(_losses_list)
        if _n_filled >= 5 and _n_filled % 5 == 0:
            _avg_win = sum(_wins_list) / len(_wins_list) if _wins_list else 0
            _avg_loss = sum(_losses_list) / len(_losses_list) if _losses_list else 1
            _wl_ratio = _avg_win / _avg_loss if _avg_loss > 0 else 999
            _wr = len(_wins_list) / _n_filled
            if _wl_ratio < 2.0:
                logger.warning("⚠️ W/L RATIO %.1fx < 2.0x (WR=%.0f%%, n=%d)",
                               _wl_ratio, _wr * 100, _n_filled)
            else:
                logger.info("W/L RATIO %.1fx (WR=%.0f%%, n=%d)",
                            _wl_ratio, _wr * 100, _n_filled)

        fr_pct, _, _ = fill_rate(state)
        _total_rounds = md.get("rounds", 0) + 1
        _coin_live = coin_from_title(md.get("title", ""))
        log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                   "result": result, "pnl": round(pnl, 4),
                   "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                   "total_pnl": round(state["total_pnl"], 2),
                   "fill_rate_pct": round(fr_pct, 1),
                   "rounds": _total_rounds,
                   "coin": _coin_live},
                  coin=_coin_live)

        d = "↑" if result == "UP" else "↓"
        _rd_str = f" R{_total_rounds}" if _total_rounds > 1 else ""
        print(f"  RESOLVED {cid[:8]} {d}{_rd_str} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")


def manage_exits(state: dict, client, dry_run: bool,
                 now_ms: int, ws_poly=None) -> None:
    """Exit management: Profit Lock + Cost Recovery + Stop Loss + Partial TP.

    🔴 DZ-3: All exit sells must check fill status before updating shares.
    🔴 BMD FIX #5: W4 both-sides markets = ZERO management, hold to resolution.
    """
    from polymarket.mm.constants import _MAX_ROUNDS

    if not client or not hasattr(client, "sell_shares") or dry_run:
        return

    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        if mkt.get("paper"):
            continue
        if mkt.get("both_sides"):
            continue  # W4: ZERO management
        _has_any_fill = (mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0)
        if not mkt.get("fills_confirmed") and not _has_any_fill:
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms - 300_000:
            continue  # last 5 min guard

        _cost_recovered = mkt.get("cost_recovered", False)

        for side, tok_key, shares_key, avg_key in [
            ("UP", "up_token_id", "up_shares", "up_avg_price"),
            ("DOWN", "down_token_id", "down_shares", "down_avg_price"),
        ]:
            shares = mkt.get(shares_key, 0)
            avg = mkt.get(avg_key, 0)
            tok = mkt.get(tok_key, "")
            if shares < 1 or avg <= 0 or not tok:
                continue
            mid = poly_midpoint(client, tok, ws_poly=ws_poly)
            if mid <= 0:
                continue

            # Only TP on our directional side
            if side != mkt.get("original_dir", side):
                continue

            # ── Layer 0.5: TIERED PARTIAL TP ──
            _PARTIAL_TP_TIERS = [
                (1.3, 0.14),
                (1.5, 0.48),
                (1.8, 0.33),
            ]
            _tp_key = f"_tp_tier_{side}"
            _tp_done = mkt.get(_tp_key, 0)
            if _tp_done < len(_PARTIAL_TP_TIERS) and not _cost_recovered:
                _mult, _sell_pct = _PARTIAL_TP_TIERS[_tp_done]
                _tp_target = avg * _mult
                if mid >= _tp_target:
                    _tp_sell = max(1, int(shares * _sell_pct))
                    try:
                        _tp_price = round(max(0.01, mid * 0.97), 2)
                        _tp_r = client.sell_shares(tok, _tp_sell, price=_tp_price)
                        _tp_sell_status = _tp_r.get("status", "") if isinstance(_tp_r, dict) else ""
                        if _tp_sell_status != "matched":
                            mkt.setdefault("pending_sells", []).append({
                                "side": side, "shares": _tp_sell, "price": _tp_price,
                                "order_id": _tp_r.get("orderID", "") if isinstance(_tp_r, dict) else "",
                                "order_ts": time.time(), "type": "partial_tp",
                            })
                            logger.warning("PARTIAL TP PENDING %s %s: %d shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                           cid[:8], side, _tp_sell, _tp_price, _tp_sell_status)
                        else:
                            _tp_pnl = _tp_sell * (_tp_price - avg)
                            mkt[shares_key] = shares - _tp_sell
                            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _tp_sell * avg)
                            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _tp_pnl
                            mkt[_tp_key] = _tp_done + 1
                            logger.info("PARTIAL TP T%d %s %s: sell %d/%d @ $%.3f (target $%.3f, x%.1f) pnl=$%.2f",
                                        _tp_done + 1, cid[:8], side, _tp_sell, int(shares),
                                        _tp_price, _tp_target, _mult, _tp_pnl)
                        # Free roll hedge on LAST tier
                        if _tp_sell_status == "matched" and _tp_done + 1 == len(_PARTIAL_TP_TIERS):
                            _tte_exit = (end_ms - now_ms) / 1000 if end_ms > 0 else 999
                            if _tte_exit > 60:
                                _opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                                if _opp_tok:
                                    _fr_budget = _tp_sell * _tp_price * 0.05
                                    _opp_mid = poly_midpoint(client, _opp_tok, ws_poly=ws_poly)
                                    _fr_price = round(max(0.01, (_opp_mid if _opp_mid > 0 else 0.10) * 2.0), 2)
                                    _fr_price = min(_fr_price, 0.15)
                                    if _fr_budget >= _fr_price:
                                        try:
                                            client.buy_shares(_opp_tok, round(_fr_budget, 2), price=_fr_price)
                                            logger.info("FREE ROLL %s: buy opp @ $%.2f ($%.2f) — %ds left",
                                                        cid[:8], _fr_price, _fr_budget, int(_tte_exit))
                                        except Exception as _fre:
                                            logger.debug("Free roll buy failed: %s", _fre)
                    except Exception as e:
                        logger.warning("Partial TP failed %s: %s", cid[:8], e)
                    continue

            # ── Layer 1: PROFIT LOCK (96¢+) ──
            if mid >= _BLACK_SWAN_MID:
                _sell_shares = max(1, int(shares * _BLACK_SWAN_SELL_PCT))
                _keep = shares - _sell_shares
                try:
                    _sell_price = round(max(0.01, mid * 0.97), 2)
                    _pl_r = client.sell_shares(tok, _sell_shares, price=_sell_price)
                    _pl_status = _pl_r.get("status", "") if isinstance(_pl_r, dict) else ""
                    if _pl_status == "matched":
                        _pnl = _sell_shares * (_sell_price - avg)
                        _remaining_cost = _keep * avg
                        logger.info("PROFIT LOCK %s %s: sell %d/%d @ $%.2f | pnl=$%.2f | keep %d free (cost=$%.2f covered)",
                                    cid[:8], side, _sell_shares, int(shares), _sell_price,
                                    _pnl, int(_keep), _remaining_cost)
                        mkt[shares_key] = _keep
                        _sold_cost = _sell_shares * avg
                        mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
                        mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _pnl
                        mkt["cost_recovered"] = True
                    else:
                        mkt.setdefault("pending_sells", []).append({
                            "side": side, "shares": _sell_shares, "price": _sell_price,
                            "order_id": _pl_r.get("orderID", "") if isinstance(_pl_r, dict) else "",
                            "order_ts": time.time(), "type": "profit_lock",
                        })
                        logger.warning("PROFIT LOCK PENDING %s %s: %d shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                       cid[:8], side, _sell_shares, _sell_price, _pl_status)
                        continue
                except Exception as e:
                    logger.warning("Profit lock sell failed %s: %s", cid[:8], e)
                    continue
                # Mini hedge
                _HEDGE_SHARES = 2
                _opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                _opp_side = "DOWN" if side == "UP" else "UP"
                if _opp_tok:
                    _opp_mid = poly_midpoint(client, _opp_tok, ws_poly=ws_poly)
                    _hedge_price = round(max(0.01, (_opp_mid if _opp_mid > 0 else 0.06) * 2.0), 2)
                    _hedge_price = min(_hedge_price, 0.15)
                    try:
                        _hedge_cost = round(_HEDGE_SHARES * _hedge_price, 2)
                        client.buy_shares(_opp_tok, _hedge_cost, price=_hedge_price)
                        logger.info("HEDGE %s %s: %d shares @ $%.2f ($%.2f)",
                                    cid[:8], _opp_side, _HEDGE_SHARES, _hedge_price, _hedge_cost)
                    except Exception as e:
                        logger.warning("HEDGE FAILED %s: %s", cid[:8], e)
                break  # exit inner for-side loop

            if _cost_recovered:
                continue

            # ── Layer 2: COST RECOVERY ──
            if mid >= _COST_RECOVERY_MID:
                _original_cost = mkt.get("entry_cost", shares * avg)
                if _original_cost <= 0:
                    continue
                _sell_price = round(max(0.01, mid * 0.98), 2)
                _shares_to_sell = min(shares - 1, math.ceil(_original_cost / _sell_price))
                if _shares_to_sell < 1:
                    continue
                try:
                    _cr_r = client.sell_shares(tok, _shares_to_sell, price=_sell_price)
                    _cr_status = _cr_r.get("status", "") if isinstance(_cr_r, dict) else ""
                    if _cr_status == "matched":
                        _recovered = _shares_to_sell * _sell_price
                        _remaining = shares - _shares_to_sell
                        logger.info("COST RECOVERY %s %s: sell %.0f/%.0f @ %.3f = $%.2f recovered | %.1f free shares",
                                    cid[:8], side, _shares_to_sell, shares, _sell_price,
                                    _recovered, _remaining)
                        mkt[shares_key] = _remaining
                        _sold_cost = _shares_to_sell * avg
                        mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
                        mkt["cost_recovered"] = True
                        mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + (_recovered - _sold_cost)
                    else:
                        mkt.setdefault("pending_sells", []).append({
                            "side": side, "shares": _shares_to_sell, "price": _sell_price,
                            "order_id": _cr_r.get("orderID", "") if isinstance(_cr_r, dict) else "",
                            "order_ts": time.time(), "type": "cost_recovery",
                        })
                        logger.warning("COST RECOVERY PENDING %s %s: %d shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                       cid[:8], side, _shares_to_sell, _sell_price, _cr_status)
                except Exception as e:
                    logger.warning("Cost recovery sell failed %s: %s", cid[:8], e)
                continue

            # ── Layer 3: STOP LOSS ──
            pnl_pct = (mid - avg) / avg
            if pnl_pct < -_EXIT_STOP_PCT:
                try:
                    _sell_price = round(max(0.01, mid * 0.97), 2)
                    _sl_r = client.sell_shares(tok, shares, price=_sell_price)
                    _sl_status = _sl_r.get("status", "") if isinstance(_sl_r, dict) else ""
                    if _sl_status == "matched":
                        _round_pnl = shares * (_sell_price - avg)
                        mkt[shares_key] = 0
                        mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + _round_pnl
                        mkt["rounds"] = mkt.get("rounds", 0) + 1
                        mkt["last_sell_ts"] = int(time.time())
                        _rd = mkt["rounds"]
                        logger.info("STOP LOSS R%d %s %s: sell %.1f @ %.3f (entry %.3f, %.0f%%) pnl=$%.2f",
                                    _rd, cid[:8], side, shares, mid, avg, pnl_pct * 100, _round_pnl)
                        try:
                            _open_orders = client.get_orders(market=cid) if hasattr(client, "get_orders") else []
                            for _oo in (_open_orders or []):
                                _oid = _oo.get("id", "")
                                if _oid:
                                    client.client.cancel(order_id=_oid)
                            if _open_orders:
                                logger.info("SL CANCEL %s: cancelled %d remaining orders after stop loss",
                                            cid[:8], len(_open_orders))
                        except Exception as _ce:
                            logger.warning("SL cancel remaining failed %s: %s", cid[:8], _ce)
                        mkt["phased_rungs"] = []
                        mkt["pending_orders"] = []
                        if _rd >= _MAX_ROUNDS:
                            mkt["phase"] = "RESOLVED"
                            mkt["early_exit"] = "stop_loss"
                    else:
                        mkt.setdefault("pending_sells", []).append({
                            "side": side, "shares": shares, "price": _sell_price,
                            "order_id": _sl_r.get("orderID", "") if isinstance(_sl_r, dict) else "",
                            "order_ts": time.time(), "type": "stop_loss",
                        })
                        logger.warning("STOP LOSS PENDING %s %s: %.1f shares @ $%.3f — shares NOT reduced until fill confirmed (status=%s)",
                                       cid[:8], side, shares, _sell_price, _sl_status)
                except Exception as e:
                    logger.warning("Stop loss failed %s %s: %s", cid[:8], side, e)


def run_endgame(state: dict, client, dry_run: bool,
                now_ms: int, execute_fn=None, ws_poly=None) -> None:
    """Endgame: 1-share bets in undecided markets (T-120s to T-30s).

    🔴 Places real orders. Daily cap enforced.
    execute_fn: the execute() function to call (avoids circular import).
    """
    from polymarket.strategy.market_maker import PlannedOrder

    if not _ENDGAME_ENABLED or not client or dry_run:
        return
    if execute_fn is None:
        from polymarket.mm.order_lifecycle import execute as execute_fn

    _eg_today = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d")
    if state.get("_eg_daily_date") != _eg_today:
        state["_eg_daily_count"] = 0
        state["_eg_daily_date"] = _eg_today
    _eg_count = state.get("_eg_daily_count", 0)

    for cid, mkt in state["markets"].items():
        if _eg_count >= _ENDGAME_DAILY_CAP:
            break
        if mkt["phase"] != "OPEN":
            continue
        if mkt.get("endgame_placed"):
            continue
        if mkt.get("both_sides"):
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms <= 0:
            continue
        tte_s = (end_ms - now_ms) / 1000
        if not (_ENDGAME_TTE_STOP < tte_s < _ENDGAME_TTE_START):
            continue

        _t = mkt.get("title", "").lower()
        _eg_coin = "btc" if "bitcoin" in _t else ("eth" if "ethereum" in _t else "sol")
        if _eg_coin not in _LIVE_TRADE_COINS:
            continue

        up_tok = mkt.get("up_token_id", "")
        dn_tok = mkt.get("down_token_id", "")
        if not up_tok or not dn_tok:
            continue
        _eg_mid = poly_midpoint(client, up_tok, ws_poly=ws_poly)
        if _eg_mid <= 0:
            continue

        _buf = endgame_mid_buf.setdefault(cid, deque(maxlen=60))
        _buf.append((time.time(), _eg_mid))

        _eg_case = 0
        _eg_dir = "DOWN" if _eg_mid > 0.50 else "UP"
        _mid_30s_ago = 0.0
        _delta_30s = 0.0

        if _ENDGAME_MID_RANGE[0] <= _eg_mid <= _ENDGAME_MID_RANGE[1]:
            if _ENDGAME_FLIP_RANGE[0] <= _eg_mid <= _ENDGAME_FLIP_RANGE[1]:
                _eg_case = 1

            _old_entries = [(t, m) for t, m in _buf if time.time() - t >= 25]
            if _old_entries:
                _mid_30s_ago = _old_entries[-1][1]
                _delta_30s = _eg_mid - _mid_30s_ago
                _moved_toward_center = abs(_eg_mid - 0.5) < abs(_mid_30s_ago - 0.5)
                if abs(_delta_30s) >= _ENDGAME_REVERSAL and _moved_toward_center:
                    _eg_case = 2
                    _eg_dir = "DOWN" if _delta_30s < 0 else "UP"

        if _eg_case == 0:
            continue

        _eg_tok = up_tok if _eg_dir == "UP" else dn_tok
        _eg_our_mid = _eg_mid if _eg_dir == "UP" else (1.0 - _eg_mid)
        _eg_price = round(min(_eg_our_mid + 0.02, 0.95), 2)

        _eg_ctx = {
            "endgame": True, "case": _eg_case,
            "mid": round(_eg_mid, 4), "mid_30s_ago": round(_mid_30s_ago, 4),
            "delta_30s": round(_delta_30s, 4), "direction": _eg_dir,
            "fair": round(_eg_mid if mkt.get("original_dir") == "UP" else 1.0 - _eg_mid, 4),
            "original_dir": mkt.get("original_dir", ""),
            "tte_s": int(tte_s), "coin": _eg_coin,
        }

        orders = [PlannedOrder(token_id=_eg_tok, side="BUY",
                               price=_eg_price, size=_ENDGAME_SHARES,
                               outcome=_eg_dir)]

        mkt["endgame_placed"] = True
        mkt["endgame_dir"] = _eg_dir
        mkt["endgame_case"] = _eg_case

        results = execute_fn(orders, client, cid=cid, signal_ctx=_eg_ctx, coin=_eg_coin.upper())

        for r in results:
            if not r.get("submitted"):
                continue
            if r.get("status") == "matched":
                _sk = "up_shares" if _eg_dir == "UP" else "down_shares"
                _ak = "up_avg_price" if _eg_dir == "UP" else "down_avg_price"
                _fill_sz = r.get("size_matched", r["size"])
                _old_s = mkt.get(_sk, 0)
                _old_a = mkt.get(_ak, 0)
                _new_s = _old_s + _fill_sz
                mkt[_sk] = _new_s
                mkt[_ak] = (_old_a * _old_s + r["price"] * _fill_sz) / _new_s if _new_s > 0 else r["price"]
                mkt["entry_cost"] = mkt.get("entry_cost", 0) + _fill_sz * r["price"]
                logger.info("ENDGAME FILL C%d %s %s mid=%.2f @$%.2f tte=%ds",
                            _eg_case, _eg_dir, cid[:8], _eg_mid, r["price"], int(tte_s))
            else:
                r["endgame"] = True
                mkt.setdefault("pending_orders", []).append(r)

        _eg_count += 1
        state["_eg_daily_count"] = _eg_count
        logger.info("ENDGAME C%d %s %s mid=%.2f price=$%.2f tte=%ds [%d/%d today]",
                    _eg_case, _eg_dir, cid[:8], _eg_mid, _eg_price, int(tte_s),
                    _eg_count, _ENDGAME_DAILY_CAP)


def run_last_minute_hedge(state: dict, client, dry_run: bool,
                          now_ms: int, execute_fn=None, ws_poly=None,
                          ws_binance=None) -> None:
    """Last-minute hedge: buy opposite if BTC 30s momentum against position.

    🔴 Guard: hedge_placed flag, BTC-only execution gate.
    execute_fn: the execute() function to call.
    """
    from polymarket.strategy.market_maker import PlannedOrder

    if not client or dry_run:
        return
    if execute_fn is None:
        from polymarket.mm.order_lifecycle import execute as execute_fn

    # Update BTC price buffer
    _btc_for_buf = btc_price(ws_binance=ws_binance)
    if _btc_for_buf > 0:
        btc_price_buf.append((time.time(), _btc_for_buf))

    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        if mkt.get("hedge_placed"):
            continue
        if mkt.get("both_sides"):
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms <= 0:
            continue
        tte_s = (end_ms - now_ms) / 1000
        if not (30 < tte_s < 120):
            continue

        _our_dir = mkt.get("original_dir", "")
        if not _our_dir:
            continue
        _our_shares = mkt.get("up_shares", 0) if _our_dir == "UP" else mkt.get("down_shares", 0)
        if _our_shares < 1:
            continue

        _t = mkt.get("title", "").lower()
        if "bitcoin" not in _t:
            continue

        _old_btc = [(t, p) for t, p in btc_price_buf if time.time() - t >= 25]
        if not _old_btc:
            continue
        _btc_30s_ago = _old_btc[-1][1]
        _btc_now = btc_price_buf[-1][1] if btc_price_buf else 0
        if _btc_now <= 0:
            continue
        _btc_30s_move = _btc_now - _btc_30s_ago

        _is_adverse = (_btc_30s_move < 0 and _our_dir == "UP") or (_btc_30s_move > 0 and _our_dir == "DOWN")
        if not (_is_adverse and abs(_btc_30s_move) >= _HEDGE_BTC_THRESHOLD):
            continue

        _opp_dir = "DOWN" if _our_dir == "UP" else "UP"
        _opp_tok = mkt.get("down_token_id", "") if _our_dir == "UP" else mkt.get("up_token_id", "")
        if not _opp_tok:
            continue
        _hedge_shares = max(1, int(_our_shares * _HEDGE_PCT))
        _opp_mid = poly_midpoint(client, _opp_tok, ws_poly=ws_poly)
        if _opp_mid <= 0:
            _up_mid = poly_midpoint(client, mkt.get("up_token_id", ""), ws_poly=ws_poly)
            _opp_mid = 1.0 - _up_mid if _up_mid > 0 else 0.5
        _hedge_price = round(min(_opp_mid + 0.02, 0.95), 2)

        _hedge_ctx = {
            "hedge": True, "our_dir": _our_dir, "our_shares": _our_shares,
            "btc_30s_move": round(_btc_30s_move, 2), "tte_s": int(tte_s),
        }

        mkt["hedge_placed"] = True

        orders = [PlannedOrder(token_id=_opp_tok, side="BUY",
                               price=_hedge_price, size=_hedge_shares,
                               outcome=_opp_dir)]
        results = execute_fn(orders, client, cid=cid, signal_ctx=_hedge_ctx, coin="BTC")

        for r in results:
            if not r.get("submitted"):
                continue
            if r.get("status") == "matched":
                _fill_sz = r.get("size_matched", r["size"])
                _sk = "up_shares" if _opp_dir == "UP" else "down_shares"
                _ak = "up_avg_price" if _opp_dir == "UP" else "down_avg_price"
                _old_s = mkt.get(_sk, 0)
                _new_s = _old_s + _fill_sz
                mkt[_sk] = _new_s
                mkt[_ak] = (mkt.get(_ak, 0) * _old_s + r["price"] * _fill_sz) / _new_s if _new_s > 0 else r["price"]
                mkt["entry_cost"] = mkt.get("entry_cost", 0) + _fill_sz * r["price"]
            else:
                r["hedge"] = True
                mkt.setdefault("pending_orders", []).append(r)

        logger.info("HEDGE %s: %d shares %s @$%.2f (BTC moved $%.0f against %s, tte=%ds)",
                    cid[:8], _hedge_shares, _opp_dir, _hedge_price,
                    abs(_btc_30s_move), _our_dir, int(tte_s))
