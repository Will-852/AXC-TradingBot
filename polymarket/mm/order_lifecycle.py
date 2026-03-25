"""
mm/order_lifecycle.py — Order execution, fill tracking, cancel defense, repricing.

Split from run_mm_live.py (2026-03-25).
🔴 2CHECK: This module directly places/cancels real orders on Polymarket CLOB.
Every function here handles real money.

Key changes from original:
- `_ws_user` module global → `ws_user` explicit parameter
- `dry_run` closure variable → explicit parameter in check_fills
- `_bump_fill` / `_log_order` → imported from state_io
- `_price` / `_poly_midpoint` → imported from data_feeds
- Cancel defense / reprice / ratio cap extracted from run_cycle as standalone functions
"""

import json
import logging
import time

from polymarket.mm.constants import (
    _BOTHSIDES_LOG, _LEAN_PREEMPTIVE_S, _LEAN_UNFILLED_TIMEOUT_S,
    _REPRICE_COOLDOWN_S, _REPRICE_MAX_PER_ORDER, _REPRICE_THRESHOLD,
    _TAKER_CONVERT_DELAY_S, _TAKER_CONVERT_ENABLED, _TAKER_CONVERT_MAX_SPREAD,
    _W4_EFFECTIVE_R_CAP, coin_from_title, ts_hkt,
)
from polymarket.mm.data_feeds import btc_price, poly_midpoint, price
from polymarket.mm.state_io import bump_fill, log_order

logger = logging.getLogger(__name__)

# ─── Module-level mutable state ───
# Deferred post-fill checks: list of (check_time, order_id, cid, token_id)
post_fill_checks: list[tuple[float, str, str, str]] = []


def find_directional_orders(pending_list: list) -> list:
    """Find orders that are directional (not part of equal-size hedge pair).
    Hedge pair = one UP + one DN with same size. Extra orders = directional."""
    up_orders = [p for p in pending_list if p["outcome"] == "UP"]
    dn_orders = [p for p in pending_list if p["outcome"] == "DOWN"]
    hedge_up_ids = set()
    hedge_dn_ids = set()
    for u in up_orders:
        for d in dn_orders:
            if abs(u["size"] - d["size"]) < 0.1 and id(u) not in hedge_up_ids and id(d) not in hedge_dn_ids:
                hedge_up_ids.add(id(u))
                hedge_dn_ids.add(id(d))
                break
    return [p for p in pending_list
            if id(p) not in hedge_up_ids and id(p) not in hedge_dn_ids]


def execute(orders, client, cid: str = "", signal_ctx: dict | None = None, coin: str = "") -> list[dict]:
    """Submit limit orders. Returns order IDs — NOT fills.

    🔴 IMPORTANT: Limit orders (GTC) go on the book. Submit != filled.
    Fills are checked later via check_fills().

    cid: condition_id for per-order logging.
    signal_ctx: market state at submit time for AS analysis.
    """
    results = []
    _ctx = signal_ctx or {}
    for o in orders:
        try:
            amount = round(o.size * o.price, 2)
            r = client.buy_shares(o.token_id, amount, price=o.price)
            order_id = ""
            status = ""
            if isinstance(r, dict):
                order_id = r.get("orderID", r.get("id", ""))
                status = r.get("status", "")
                if r.get("dry_run"):
                    status = "matched"
            # DZ-4: Partial fill blindspot — use size_matched not size
            _size_matched = o.size
            if isinstance(r, dict):
                _taking = r.get("takingAmount", "")
                if _taking and str(_taking).strip():
                    try:
                        _size_matched = float(_taking)
                    except (ValueError, TypeError):
                        _size_matched = o.size
            logger.info("ORDER SUBMITTED %s %s: %.1f shares @ $%.3f ($%.2f) → %s [%s]",
                        o.outcome, o.token_id[:10], o.size, o.price, amount,
                        order_id[:12] if order_id else "ok", status)
            if abs(_size_matched - o.size) > 0.1:
                logger.warning("PARTIAL FILL DETECTED %s: submitted=%.1f matched=%.1f",
                               order_id[:12] if order_id else "?", o.size, _size_matched)
            results.append({"outcome": o.outcome, "price": o.price,
                           "size": o.size, "size_matched": _size_matched,
                           "token_id": o.token_id,
                           "order_id": order_id, "status": status,
                           "submitted": True, "order_ts": time.time()})
            log_order("submit", order_id, cid, coin=coin,
                      outcome=o.outcome, price=o.price, size=o.size,
                      status=status, **_ctx)
        except Exception as e:
            logger.error("ORDER FAILED %s: %s", o.outcome, e)
            results.append({"outcome": o.outcome, "submitted": False, "error": str(e)})
    return results


def check_fills_paper(state: dict, client=None, ws_poly=None) -> None:
    """Paper fill simulation: maker order fills when market mid ≤ our limit price.

    For MM bot paper testing. Simulates GTC limit order being lifted.
    """
    now = time.time()
    now_ms = int(now * 1000)

    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        _coin = mkt.get("coin", "") or coin_from_title(mkt.get("title", ""))
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            for po in pending:
                log_order("paper_expired", po.get("order_id", ""), cid, coin=_coin,
                          outcome=po.get("outcome", ""))
            bump_fill(state, "expired", len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue

        new_pending = []
        for po in pending:
            if not po.get("submitted"):
                new_pending.append(po)
                continue
            tok = po.get("token_id", "")
            # Try WS mid first, fall back to REST
            mid = None
            if ws_poly:
                mid = ws_poly.get_midpoint(tok)
            if mid is None or mid <= 0:
                mid = poly_midpoint(client, tok) if tok else None
            if mid is None or mid <= 0:
                new_pending.append(po)
                continue

            # Maker fill: our bid price >= market mid (mid dropped to our level)
            if po["price"] >= mid:
                o = po.get("outcome", "UP")
                s = po.get("size", 0)
                p = po["price"]
                if o == "UP":
                    old_val = mkt.get("up_shares", 0) * mkt.get("up_avg_price", 0)
                    mkt["up_shares"] = mkt.get("up_shares", 0) + s
                    mkt["up_avg_price"] = (old_val + s * p) / mkt["up_shares"] if mkt["up_shares"] else p
                else:
                    old_val = mkt.get("down_shares", 0) * mkt.get("down_avg_price", 0)
                    mkt["down_shares"] = mkt.get("down_shares", 0) + s
                    mkt["down_avg_price"] = (old_val + s * p) / mkt["down_shares"] if mkt["down_shares"] else p
                mkt["entry_cost"] = mkt.get("entry_cost", 0) + s * p
                bump_fill(state, "filled")
                fill_age = now - po.get("order_ts", now)
                logger.info("PAPER FILL %s %s @ $%.3f (mid=$%.3f, age=%.0fs)",
                            cid[:8], o, p, mid, fill_age)
                log_order("paper_fill", po.get("order_id", ""), cid, coin=_coin,
                          outcome=o, price=p, mid=round(mid, 3),
                          size=s, fill_age_s=round(fill_age))
            else:
                new_pending.append(po)

        mkt["pending_orders"] = new_pending
        if not new_pending and (mkt.get("up_shares", 0) > 0 or mkt.get("down_shares", 0) > 0):
            mkt["fills_confirmed"] = True


def check_fills(state: dict, client, dry_run: bool = False,
                ws_user=None, ws_poly=None) -> None:
    """Check which submitted orders actually filled on-chain.

    🔴 Updates market state to reflect actual positions.
    🔴 Includes taker conversion logic (cancel maker → re-buy as taker).
    """
    if not client or not hasattr(client, "get_orders"):
        return

    now_ms = int(time.time() * 1000)
    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        if mkt.get("fills_confirmed"):
            continue

        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        _coin = mkt.get("coin", "") or coin_from_title(mkt.get("title", ""))

        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            bump_fill(state, "expired", len(pending))
            for _ep in pending:
                log_order("expired", _ep.get("order_id", ""), cid, coin=_coin,
                          outcome=_ep.get("outcome", ""))
            logger.info("Window ended %s: %d pending orders → expired (not filled)",
                        cid[:8], len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue

        # ── WS fast path ──
        if ws_user and ws_user.connected:
            ws_resolved = []
            ws_remaining = []
            for po in pending:
                oid = po.get("order_id", "")
                if not oid:
                    ws_remaining.append(po)
                    continue
                ws_status = ws_user.get_order_status(oid)
                if ws_status == "MATCHED":
                    outcome = po["outcome"]
                    _price = po["price"]
                    size = po["size"]
                    ws_detail = ws_user.get_order_detail(oid)
                    if ws_detail and ws_detail.get("size_matched", 0) > 0:
                        actual = ws_detail["size_matched"]
                        if abs(actual - size) > 0.1:
                            logger.warning("PARTIAL FILL %s: submitted=%.1f matched=%.1f",
                                           oid[:12], size, actual)
                            size = actual
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * _price) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * _price) / mkt["down_shares"]
                    mkt["entry_cost"] += size * _price
                    bump_fill(state, "filled")
                    log_order("fill_ws", oid, cid, coin=_coin, outcome=outcome,
                              price=_price, size=size)
                    logger.info("FILL (WS) %s %s: %.1f @ $%.3f",
                                cid[:8], outcome, size, _price)
                    ws_resolved.append(po)
                elif ws_status in ("CANCELED", "CANCELLED"):
                    bump_fill(state, "cancelled")
                    log_order("cancelled_ws", oid, cid, coin=_coin,
                              outcome=po.get("outcome", ""))
                    logger.info("CANCEL (WS) %s %s", cid[:8], po.get("outcome", ""))
                    ws_resolved.append(po)
                else:
                    ws_remaining.append(po)

            if ws_resolved:
                pending = ws_remaining
                mkt["pending_orders"] = pending
                if not pending:
                    mkt["fills_confirmed"] = True
                    logger.info("ALL FILLS CONFIRMED (WS) %s: UP=%.1f DN=%.1f cost=$%.2f",
                                cid[:8], mkt["up_shares"], mkt["down_shares"],
                                mkt["entry_cost"])
                    continue

                # ── Taker Conversion ──
                if (_TAKER_CONVERT_ENABLED and mkt.get("both_sides")
                        and client and hasattr(client, "buy_shares")
                        and not dry_run and not mkt.get("_taker_converted")):
                    _has_up = mkt.get("up_shares", 0) > 0
                    _has_dn = mkt.get("down_shares", 0) > 0
                    _pend_outcomes = {p.get("outcome", "").upper() for p in pending}
                    if ((_has_up and not _has_dn and "DOWN" in _pend_outcomes)
                            or (_has_dn and not _has_up and "UP" in _pend_outcomes)):
                        _unfilled_side = "DOWN" if _has_up else "UP"
                        _unfilled_po = next(
                            (p for p in pending if p.get("outcome", "").upper() == _unfilled_side),
                            None)
                        if _unfilled_po:
                            _entry_ts = mkt.get("entry_ts", 0)
                            _age_s = time.time() - _entry_ts if _entry_ts > 0 else 999
                            if _age_s >= _TAKER_CONVERT_DELAY_S:
                                _tc_tok = _unfilled_po.get("token_id", "")
                                _tc_oid = _unfilled_po.get("order_id", "")
                                _tc_maker_px = _unfilled_po.get("price", 0)
                                _tc_size = _unfilled_po.get("size", 0)
                                try:
                                    _tc_ob = client.get_order_book(_tc_tok)
                                    _tc_asks = _tc_ob.get("asks", [])
                                    _tc_best_ask = min(a["price"] for a in _tc_asks) if _tc_asks else 0
                                except Exception:
                                    _tc_best_ask = 0
                                if _tc_best_ask > 0:
                                    _tc_spread = _tc_best_ask - _tc_maker_px
                                    if _tc_spread <= _TAKER_CONVERT_MAX_SPREAD:
                                        _filled_side_key = "up_avg_price" if _has_up else "down_avg_price"
                                        _filled_px = mkt.get(_filled_side_key, 0)
                                        _tc_combined = _filled_px + _tc_best_ask + 0.01
                                        if _tc_combined > 1.00:
                                            logger.info(
                                                "TAKER CONVERT SKIP %s %s: combined %.3f > $1.00 (guaranteed loss)",
                                                cid[:8], _unfilled_side, _tc_combined)
                                        else:
                                            _tc_cancel_ok = False
                                            _tc_ws_st = ""
                                            if ws_user and ws_user.connected:
                                                _tc_ws_st = ws_user.get_order_status(_tc_oid)
                                            if _tc_ws_st == "MATCHED":
                                                logger.info("TAKER CONVERT SKIP %s %s: already filled during wait",
                                                            cid[:8], _unfilled_side)
                                            else:
                                                try:
                                                    client.client.cancel(order_id=_tc_oid)
                                                    _tc_cancel_ok = True
                                                except Exception as _tce:
                                                    logger.warning("TAKER CONVERT cancel failed %s: %s",
                                                                   cid[:8], _tce)
                                                if _tc_cancel_ok and ws_user and ws_user.connected:
                                                    time.sleep(0.05)
                                                    if ws_user.get_order_status(_tc_oid) == "MATCHED":
                                                        _tc_det = ws_user.get_order_detail(_tc_oid)
                                                        _pc_sz = _tc_size
                                                        if _tc_det and _tc_det.get("size_matched", 0) > 0:
                                                            _pc_sz = _tc_det["size_matched"]
                                                        if _unfilled_side == "UP":
                                                            old_v = mkt["up_shares"] * mkt["up_avg_price"]
                                                            mkt["up_shares"] += _pc_sz
                                                            mkt["up_avg_price"] = (old_v + _pc_sz * _tc_maker_px) / mkt["up_shares"]
                                                        else:
                                                            old_v = mkt["down_shares"] * mkt["down_avg_price"]
                                                            mkt["down_shares"] += _pc_sz
                                                            mkt["down_avg_price"] = (old_v + _pc_sz * _tc_maker_px) / mkt["down_shares"]
                                                        mkt["entry_cost"] += _pc_sz * _tc_maker_px
                                                        bump_fill(state, "filled")
                                                        mkt["pending_orders"] = [
                                                            p for p in pending if p is not _unfilled_po]
                                                        if not mkt["pending_orders"]:
                                                            mkt["fills_confirmed"] = True
                                                        mkt["_taker_converted"] = True
                                                        logger.warning(
                                                            "TAKER CONVERT ABORT (post-cancel) %s %s: "
                                                            "maker FILLED during cancel RTT (%.1f @ $%.3f) "
                                                            "— phantom fill recovered, NO taker placed",
                                                            cid[:8], _unfilled_side, _pc_sz, _tc_maker_px)
                                                        _tc_cancel_ok = False
                                            if _tc_cancel_ok:
                                                _tc_taker_px = round(_tc_best_ask + 0.01, 2)
                                                _tc_amount = round(_tc_size * _tc_taker_px, 2)
                                                try:
                                                    _tc_r = client.buy_shares(
                                                        _tc_tok, _tc_amount, price=_tc_taker_px)
                                                    _tc_status = ""
                                                    _tc_new_oid = ""
                                                    if isinstance(_tc_r, dict):
                                                        _tc_status = _tc_r.get("status", "")
                                                        _tc_new_oid = _tc_r.get("orderID", "")
                                                    _tc_fill_sz = _tc_size
                                                    if isinstance(_tc_r, dict):
                                                        _taking = _tc_r.get("takingAmount", "")
                                                        if _taking and str(_taking).strip():
                                                            try:
                                                                _tc_fill_sz = float(_taking)
                                                            except (ValueError, TypeError):
                                                                pass
                                                    if _tc_status == "matched":
                                                        if _unfilled_side == "UP":
                                                            old_v = mkt["up_shares"] * mkt["up_avg_price"]
                                                            mkt["up_shares"] += _tc_fill_sz
                                                            mkt["up_avg_price"] = (old_v + _tc_fill_sz * _tc_taker_px) / mkt["up_shares"]
                                                        else:
                                                            old_v = mkt["down_shares"] * mkt["down_avg_price"]
                                                            mkt["down_shares"] += _tc_fill_sz
                                                            mkt["down_avg_price"] = (old_v + _tc_fill_sz * _tc_taker_px) / mkt["down_shares"]
                                                        mkt["entry_cost"] += _tc_fill_sz * _tc_taker_px
                                                        bump_fill(state, "filled")
                                                        mkt["pending_orders"] = [
                                                            p for p in pending if p is not _unfilled_po]
                                                        if not mkt["pending_orders"]:
                                                            mkt["fills_confirmed"] = True
                                                        mkt["_taker_converted"] = True
                                                        logger.info(
                                                            "TAKER CONVERT %s %s: maker@%.2f→taker@%.2f "
                                                            "(spread=%.3f, %.1f shares) cost=$%.2f | "
                                                            "UP=%.1f DN=%.1f total=$%.2f",
                                                            cid[:8], _unfilled_side,
                                                            _tc_maker_px, _tc_taker_px, _tc_spread,
                                                            _tc_fill_sz, _tc_fill_sz * _tc_taker_px,
                                                            mkt["up_shares"], mkt["down_shares"],
                                                            mkt["entry_cost"])
                                                    else:
                                                        _new_po = dict(_unfilled_po)
                                                        _new_po["order_id"] = _tc_new_oid
                                                        _new_po["price"] = _tc_taker_px
                                                        _new_po["order_ts"] = time.time()
                                                        _new_po["_taker_convert"] = True
                                                        mkt["pending_orders"] = [
                                                            p for p in pending if p is not _unfilled_po
                                                        ] + [_new_po]
                                                        mkt["_taker_converted"] = True
                                                        logger.warning(
                                                            "TAKER CONVERT PENDING %s %s: taker@%.2f "
                                                            "status=%s — still on book",
                                                            cid[:8], _unfilled_side,
                                                            _tc_taker_px, _tc_status)
                                                except Exception as _tce2:
                                                    logger.error(
                                                        "TAKER CONVERT BUY FAILED %s %s: %s — "
                                                        "maker cancelled, taker failed, ORDER LOST",
                                                        cid[:8], _unfilled_side, _tce2)
                                    else:
                                        logger.info(
                                            "TAKER CONVERT SKIP %s %s: spread %.3f > max %.3f",
                                            cid[:8], _unfilled_side, _tc_spread,
                                            _TAKER_CONVERT_MAX_SPREAD)

            if not pending:
                continue

        try:
            trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
            trade_order_ids = set()
            for t in (trades or []):
                taker_id = t.get("taker_order_id", "")
                if taker_id:
                    trade_order_ids.add(taker_id)
                for mo in t.get("maker_orders", []):
                    mid = mo.get("order_id", "") if isinstance(mo, dict) else ""
                    if mid:
                        trade_order_ids.add(mid)

            open_orders = client.get_orders(market=cid)
            open_ids = {o.get("id", "") for o in open_orders} if open_orders else set()

            filled = []
            still_open = []
            for po in pending:
                oid = po.get("order_id", "")
                if oid and oid in trade_order_ids:
                    filled.append(po)
                elif oid and oid in open_ids:
                    still_open.append(po)
                else:
                    bump_fill(state, "cancelled")
                    log_order("cancelled_external", po.get("order_id", ""), cid, coin=_coin,
                              outcome=po.get("outcome", ""))
                    logger.info("Order %s %s: not in trades or open → cancelled",
                                cid[:8], po["outcome"])

            if filled:
                for f in filled:
                    outcome = f["outcome"]
                    _fprice = f["price"]
                    size = f["size"]
                    if outcome == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += size
                        mkt["up_avg_price"] = (old + size * _fprice) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += size
                        mkt["down_avg_price"] = (old + size * _fprice) / mkt["down_shares"]
                    mkt["entry_cost"] += size * _fprice
                    bump_fill(state, "filled")
                    _fill_mid = 0.0
                    _tok = f.get("token_id", "")
                    if _tok and hasattr(client, "get_midpoint"):
                        _fill_mid = poly_midpoint(client, _tok, ws_poly=ws_poly)
                    _title = mkt.get("title", "").lower()
                    _fill_sym = "ETHUSDT" if "ethereum" in _title else "BTCUSDT"
                    _btc_fill = price(_fill_sym)
                    _order_ts = f.get("order_ts", 0)
                    _ttf = round(time.time() - _order_ts, 1) if _order_ts > 0 else 0
                    log_order("fill", f.get("order_id", ""), cid, coin=_coin,
                              outcome=outcome, price=_fprice, size=size,
                              mid_at_fill=round(_fill_mid, 4) if _fill_mid else 0,
                              btc_at_fill=round(_btc_fill, 2),
                              time_to_fill_s=_ttf)
                    if _tok:
                        post_fill_checks.append(
                            (time.time() + 60, f.get("order_id", ""), cid, _tok))
                    logger.info("FILL CONFIRMED %s %s: %.1f @ $%.3f mid=%.3f",
                                cid[:8], outcome, size, _fprice, _fill_mid)

                mkt["pending_orders"] = still_open
                if not still_open:
                    mkt["fills_confirmed"] = True
                    logger.info("ALL FILLS CONFIRMED %s: UP=%.1f DN=%.1f cost=$%.2f",
                                cid[:8], mkt["up_shares"], mkt["down_shares"],
                                mkt["entry_cost"])

        except Exception as e:
            logger.warning("Fill check failed for %s: %s", cid[:8], e)


def cancel_defense(state: dict, client, dry_run: bool,
                   ws_user, now_ms: int, ws_binance=None) -> None:
    """Cancel defense: 4 triggers for unfilled orders.

    🔴 DZ-1: Cancel race condition — cancel(matched_order) = no-op, causes double exposure.
    WS pre-check mandatory.
    """
    if not client or not hasattr(client, "client") or dry_run:
        return

    for cid, mkt in state["markets"].items():
        if mkt["phase"] != "OPEN":
            continue
        _is_bs = mkt.get("both_sides", False)
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        end_ms = mkt.get("window_end_ms", 0)
        entry_price = mkt.get("entry_price", 0)
        entry_ts = mkt.get("entry_ts", 0)
        now_s = int(time.time())

        _t = mkt.get("title", "").lower()
        _s = "ETHUSDT" if "ethereum" in _t else "BTCUSDT"
        _coin = mkt.get("coin", "") or coin_from_title(mkt.get("title", ""))

        to_cancel = []
        reason = ""

        # Trigger 1: 2 min before window end → cancel ALL pending (except endgame/hedge)
        if end_ms > 0 and now_ms > end_ms - 120_000:
            to_cancel = [p for p in pending if not p.get("endgame") and not p.get("hedge")]
            reason = "window_end"

        # Trigger 2: spot moved ADVERSELY
        _spot_thresh = 0.007 if _s == "ETHUSDT" else 0.005
        if not to_cancel and entry_price > 0 and not _is_bs:
            current = btc_price(ws_binance=ws_binance) if _s == "BTCUSDT" else price(_s, ws_binance=ws_binance)
            if current > 0:
                signed_move = (current - entry_price) / entry_price
                _dir = mkt.get("original_dir", "UP")
                is_adverse = (signed_move < 0 and _dir == "UP") or (signed_move > 0 and _dir == "DOWN")
                if is_adverse and abs(signed_move) > _spot_thresh:
                    to_cancel = [o for o in find_directional_orders(pending)
                                 if not o.get("endgame") and not o.get("hedge")]
                    if to_cancel:
                        reason = f"adverse_move_{signed_move:+.4f}"

        # Trigger 3: Dynamic TTL
        if not to_cancel and entry_ts > 0 and end_ms > 0 and not _is_bs:
            _hard_cancel_s = (end_ms - 120_000) / 1000
            _max_ttl_s = max(60, _hard_cancel_s - entry_ts)
            _time_on_book = now_s - entry_ts
            if _time_on_book > 600 and not mkt.get("_ttl_extended_logged"):
                logger.info("TTL_EXTENDED %s: on book %ds (old cap would cancel at 600s, now max=%ds)",
                            cid[:8], int(_time_on_book), int(_max_ttl_s))
                mkt["_ttl_extended_logged"] = True
            if _time_on_book > _max_ttl_s:
                to_cancel = [o for o in find_directional_orders(pending)
                             if not o.get("endgame") and not o.get("hedge")]
                if to_cancel:
                    reason = f"ttl_{int(_time_on_book)}s_max{int(_max_ttl_s)}s"

        # Trigger 4: Lean-unfilled protection (both-sides only)
        if not to_cancel and _is_bs and entry_ts > 0:
            _lean_age = now_s - entry_ts
            _lean_dir = mkt.get("_w4_t1_dir", "")
            if _lean_dir:
                _lean_ws_filled = False
                if ws_user and ws_user.connected:
                    for _db_po in pending:
                        _db_oid = _db_po.get("order_id", "")
                        if (_db_oid and _db_po.get("outcome", "").upper() == _lean_dir
                                and ws_user.get_order_status(_db_oid) == "MATCHED"):
                            _lean_ws_filled = True
                            break

                _lean_key = "up_shares" if _lean_dir == "UP" else "down_shares"
                _hedge_key = "down_shares" if _lean_dir == "UP" else "up_shares"
                _lean_sh = mkt.get(_lean_key, 0)
                _hedge_sh = mkt.get(_hedge_key, 0)
                _lean_pending = [p for p in pending if p.get("outcome", "").upper() == _lean_dir]
                _hedge_pending = [p for p in pending if p.get("outcome", "").upper() != _lean_dir]

                if not _lean_ws_filled and _lean_sh == 0:
                    # Stage 1: preemptive hedge cancel
                    if (_lean_age >= _LEAN_PREEMPTIVE_S
                            and _hedge_sh == 0 and _hedge_pending and _lean_pending):
                        to_cancel = _hedge_pending
                        reason = f"lean_unfilled_preemptive_{int(_lean_age)}s"
                        logger.info("DOOR-B STAGE1 %s: lean=%s unfilled@%ds → cancel hedge (free)",
                                    cid[:8], _lean_dir, int(_lean_age))
                        try:
                            with open(_BOTHSIDES_LOG, "a") as _bsf:
                                _bsf.write(json.dumps({"ts": ts_hkt(), "event": "door_b_stage1",
                                    "cid": cid[:8], "lean_dir": _lean_dir,
                                    "age_s": int(_lean_age)}) + "\n")
                        except Exception:
                            pass

                    # Stage 2: lean still unfilled after 90s
                    elif _lean_age >= _LEAN_UNFILLED_TIMEOUT_S and _lean_pending:
                        to_cancel = list(_lean_pending)
                        if _hedge_sh == 0 and _hedge_pending:
                            to_cancel.extend(_hedge_pending)
                        reason = f"lean_unfilled_{int(_lean_age)}s"
                        mkt["_w4_t2_pending"] = False
                        if _hedge_sh > 0:
                            logger.warning("DOOR-B STAGE2 %s: lean=%s unfilled@%ds, "
                                           "hedge=%.1f shares → cancel lean, block T2, naked hedge",
                                           cid[:8], _lean_dir, int(_lean_age), _hedge_sh)
                        else:
                            logger.info("DOOR-B STAGE2 %s: lean=%s unfilled@%ds, "
                                        "no hedge → cancel all, block T2",
                                        cid[:8], _lean_dir, int(_lean_age))
                        try:
                            with open(_BOTHSIDES_LOG, "a") as _bsf:
                                _bsf.write(json.dumps({"ts": ts_hkt(), "event": "door_b_stage2",
                                    "cid": cid[:8], "lean_dir": _lean_dir,
                                    "age_s": int(_lean_age),
                                    "hedge_shares": round(_hedge_sh, 1),
                                    "naked_hedge": _hedge_sh > 0}) + "\n")
                        except Exception:
                            pass

        # ── Execute cancels with phantom fill recovery ──
        actually_cancelled = []
        _phantom_fills = []
        _time_on_book = now_s - entry_ts if entry_ts > 0 else 0
        _dist_to_end_s = (end_ms / 1000 - now_s) if end_ms > 0 else 0
        for po in to_cancel:
            oid = po.get("order_id", "")
            if oid:
                if ws_user and ws_user.connected:
                    _ws_st = ws_user.get_order_status(oid)
                    if _ws_st == "MATCHED":
                        _fill_size = po["size"]
                        _ws_det = ws_user.get_order_detail(oid)
                        if _ws_det and _ws_det.get("size_matched", 0) > 0:
                            _fill_size = _ws_det["size_matched"]
                        _fill_price = po.get("price", 0)
                        outcome = po["outcome"]
                        if outcome == "UP":
                            old_val = mkt["up_shares"] * mkt["up_avg_price"]
                            mkt["up_shares"] += _fill_size
                            mkt["up_avg_price"] = (
                                (old_val + _fill_size * _fill_price) / mkt["up_shares"]
                            )
                        elif outcome == "DOWN":
                            old_val = mkt["down_shares"] * mkt["down_avg_price"]
                            mkt["down_shares"] += _fill_size
                            mkt["down_avg_price"] = (
                                (old_val + _fill_size * _fill_price) / mkt["down_shares"]
                            )
                        mkt["entry_cost"] += _fill_size * _fill_price
                        bump_fill(state, "filled")
                        logger.warning(
                            "CANCEL ABORT %s %s [%s]: order ALREADY MATCHED "
                            "(%.1f @ $%.3f) — phantom fill recovered",
                            cid[:8], po["outcome"], reason, _fill_size, _fill_price)
                        _phantom_fills.append(po)
                        continue

                try:
                    _cancel_t0 = time.time()
                    client.client.cancel(order_id=oid)
                    _cancel_rtt_ms = round((time.time() - _cancel_t0) * 1000, 1)
                    logger.info("CANCEL %s %s [%s] book=%ds end=%ds rtt=%dms",
                                cid[:8], po["outcome"], reason,
                                _time_on_book, _dist_to_end_s, _cancel_rtt_ms)
                    log_order("cancel", oid, cid, coin=_coin,
                              outcome=po.get("outcome", ""), reason=reason,
                              time_on_book_s=_time_on_book,
                              dist_to_end_s=int(_dist_to_end_s),
                              cancel_rtt_ms=_cancel_rtt_ms)
                    actually_cancelled.append(po)
                except Exception as e:
                    logger.warning("Cancel FAILED %s %s: %s — keeping in pending",
                                   cid[:8], po["outcome"], e)

        _removed = actually_cancelled + _phantom_fills
        if _removed:
            if actually_cancelled:
                bump_fill(state, "cancelled", len(actually_cancelled))
            mkt["pending_orders"] = [p for p in pending if p not in _removed]
            if not mkt["pending_orders"]:
                mkt["fills_confirmed"] = True


def reprice_orders(state: dict, client, dry_run: bool,
                   ws_user, now_ms: int, both_sides: bool,
                   is_heavy: bool) -> None:
    """W4 Order Repricing: improve stale limit orders when OB drops.

    🔴 DZ-1: Reprice cancel race — same phantom fill risk as cancel defense.
    🔴 BMD FIX: Only reprice DOWNWARD (better price for us).
    🔴 2CHECK FIX: Split cancel+buy into separate try blocks.
    """
    if not (both_sides and client and hasattr(client, "client")
            and not dry_run and is_heavy):
        return

    _reprice_now = time.time()
    for cid, mkt in state["markets"].items():
        if not mkt.get("both_sides") or mkt["phase"] != "OPEN":
            continue
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue
        if _reprice_now - mkt.get("_last_reprice_ts", 0) < _REPRICE_COOLDOWN_S:
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms - 180_000:
            continue

        _repriced_any = False
        _new_pending = []
        for po in pending:
            if po.get("_reprice_count", 0) >= _REPRICE_MAX_PER_ORDER:
                _new_pending.append(po)
                continue
            _tok = po.get("token_id", "")
            _oid = po.get("order_id", "")
            if not _tok or not _oid:
                _new_pending.append(po)
                continue

            try:
                _ob = client.get_order_book(_tok)
                _bids = _ob.get("bids", [])
                _asks = _ob.get("asks", [])
                if not _bids or not _asks:
                    _new_pending.append(po)
                    continue
                _cur_mid = (max(b["price"] for b in _bids) + min(a["price"] for a in _asks)) / 2
            except Exception:
                _new_pending.append(po)
                continue

            _old_price = po.get("price", 0)
            # Reprice uses tighter tick (0.01) than entry (0.02) — intentional.
            # Reprice fires when market dropped ≥2¢ → order is stale → chase with tighter bid.
            _new_bid = round(max(0.02, _cur_mid - 0.01), 2)

            if _new_bid >= _old_price:
                _new_pending.append(po)
                continue

            if _old_price - _new_bid < _REPRICE_THRESHOLD:
                _new_pending.append(po)
                continue

            # Step 0: Check if old order already filled (race condition guard)
            _already_filled = False
            if ws_user and ws_user.connected:
                _pre_status = ws_user.get_order_status(_oid)
                if _pre_status == "MATCHED":
                    _already_filled = True
                    _fill_size = po["size"]
                    _ws_det = ws_user.get_order_detail(_oid)
                    if _ws_det and _ws_det.get("size_matched", 0) > 0:
                        _fill_size = _ws_det["size_matched"]
                    outcome = po["outcome"]
                    if outcome == "UP":
                        old_val = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += _fill_size
                        mkt["up_avg_price"] = (old_val + _fill_size * _old_price) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old_val = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += _fill_size
                        mkt["down_avg_price"] = (old_val + _fill_size * _old_price) / mkt["down_shares"]
                    mkt["entry_cost"] += _fill_size * _old_price
                    bump_fill(state, "filled")
                    logger.warning(
                        "W4 REPRICE ABORT %s %s: old order ALREADY MATCHED "
                        "(%.1f @ $%.3f) — phantom fill recovered, NO replacement placed",
                        cid[:8], po["outcome"], _fill_size, _old_price)
                    _repriced_any = True
                    continue

            if _already_filled:
                continue

            # Step 1: Cancel old order
            _cancel_ok = False
            try:
                client.client.cancel(order_id=_oid)
                _cancel_ok = True
            except Exception as e:
                logger.warning("W4 REPRICE CANCEL FAILED %s %s: %s", cid[:8], po["outcome"], e)
                _new_pending.append(po)
                continue

            # Step 1.5: Post-cancel verification
            if ws_user and ws_user.connected:
                time.sleep(0.05)
                _post_status = ws_user.get_order_status(_oid)
                if _post_status == "MATCHED":
                    _fill_size = po["size"]
                    _ws_det = ws_user.get_order_detail(_oid)
                    if _ws_det and _ws_det.get("size_matched", 0) > 0:
                        _fill_size = _ws_det["size_matched"]
                    outcome = po["outcome"]
                    if outcome == "UP":
                        old_val = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += _fill_size
                        mkt["up_avg_price"] = (old_val + _fill_size * _old_price) / mkt["up_shares"]
                    elif outcome == "DOWN":
                        old_val = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += _fill_size
                        mkt["down_avg_price"] = (old_val + _fill_size * _old_price) / mkt["down_shares"]
                    mkt["entry_cost"] += _fill_size * _old_price
                    bump_fill(state, "filled")
                    logger.warning(
                        "W4 REPRICE ABORT (post-cancel) %s %s: order MATCHED during cancel RTT "
                        "(%.1f @ $%.3f) — phantom fill recovered, NO replacement placed",
                        cid[:8], po["outcome"], _fill_size, _old_price)
                    _repriced_any = True
                    continue

            # Step 2: Place replacement
            try:
                _amount = round(po["size"] * _new_bid, 2)
                _r = client.buy_shares(_tok, _amount, price=_new_bid)
                _new_oid = ""
                _new_status = ""
                if isinstance(_r, dict):
                    _new_oid = _r.get("orderID", _r.get("id", ""))
                    _new_status = _r.get("status", "")

                if _new_status == "matched":
                    bump_fill(state, "filled")
                    logger.info("W4 REPRICE+FILL %s %s: $%.2f → $%.2f (mid=%.3f)",
                                cid[:8], po["outcome"], _old_price, _new_bid, _cur_mid)
                else:
                    _new_po = dict(po)
                    _new_po["order_id"] = _new_oid
                    _new_po["price"] = _new_bid
                    _new_po["_reprice_count"] = po.get("_reprice_count", 0) + 1
                    _new_po["order_ts"] = time.time()
                    _new_pending.append(_new_po)
                    logger.info("W4 REPRICE %s %s: $%.2f → $%.2f (mid=%.3f, #%d)",
                                cid[:8], po["outcome"], _old_price, _new_bid,
                                _cur_mid, _new_po["_reprice_count"])
                _repriced_any = True
            except Exception as e:
                logger.error("W4 REPRICE BUY FAILED %s %s: cancel OK but buy failed: %s — order LOST",
                             cid[:8], po["outcome"], e)
                _repriced_any = True

        if _repriced_any:
            mkt["pending_orders"] = _new_pending
            mkt["_last_reprice_ts"] = _reprice_now
            if not _new_pending:
                mkt["fills_confirmed"] = True


def runtime_ratio_cap(state: dict, client) -> None:
    """Cancel excess lean orders if effective ratio > cap.

    🔴 T1+T2 can amplify ratio to 2.7-4.2x. One R=2.7 trade lost $10.44.
    """
    for _rc_cid, _rc_mkt in list(state.get("markets", {}).items()):
        if not _rc_mkt.get("both_sides"):
            continue
        _rc_up = _rc_mkt.get("up_shares", 0)
        _rc_dn = _rc_mkt.get("down_shares", 0)
        if _rc_up > 0 and _rc_dn > 0:
            _rc_ratio = max(_rc_up, _rc_dn) / min(_rc_up, _rc_dn)
            if _rc_ratio > _W4_EFFECTIVE_R_CAP:
                _rc_excess = abs(_rc_up - _rc_dn)
                _rc_excess_side = "UP" if _rc_up > _rc_dn else "DOWN"
                _rc_pending = _rc_mkt.get("pending_orders", [])
                _rc_cancel = [p for p in _rc_pending
                              if p.get("outcome", "").upper() == _rc_excess_side]
                if _rc_cancel and client and hasattr(client, "client"):
                    for _rc_o in _rc_cancel:
                        _rc_oid = _rc_o.get("order_id", "")
                        if _rc_oid:
                            try:
                                client.client.cancel(_rc_oid)
                                logger.warning("RATIO CAP %s: R=%.1f > %.1f cap → cancelled %s %s",
                                               _rc_cid[:8], _rc_ratio, _W4_EFFECTIVE_R_CAP,
                                               _rc_excess_side, _rc_oid[:12])
                            except Exception:
                                pass
                else:
                    logger.warning("RATIO CAP WARN %s: R=%.1f > %.1f cap, UP=%.0f DN=%.0f, "
                                   "but no pending to cancel (all filled)",
                                   _rc_cid[:8], _rc_ratio, _W4_EFFECTIVE_R_CAP, _rc_up, _rc_dn)


def post_fill_as_check(client, ws_poly=None) -> None:
    """Post-fill AS measurement: check midpoint 60s after fill."""
    if not post_fill_checks or not client or not hasattr(client, "get_midpoint"):
        return
    _now = time.time()
    _remaining = []
    for _pf_time, _pf_oid, _pf_cid, _pf_tok in post_fill_checks:
        if _now >= _pf_time:
            _pf_mid = poly_midpoint(client, _pf_tok, ws_poly=ws_poly)
            log_order("post_fill_60s", _pf_oid, _pf_cid, coin="",
                      mid_60s=round(_pf_mid, 4) if _pf_mid else 0)
        else:
            _remaining.append((_pf_time, _pf_oid, _pf_cid, _pf_tok))
    post_fill_checks.clear()
    post_fill_checks.extend(_remaining)
