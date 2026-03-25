"""
conv_1h/order_lifecycle.py — Order execution, fill tracking, and repricing for 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
🔴 2CHECK: This module directly places/cancels real orders on Polymarket CLOB.
Every function here handles real money.

Key changes from original:
- `_btc_price` / `_poly_midpoint` / `_poly_ob` → imported from data_feeds (no underscore)
- `_bump_fill` / `_log_order` → imported from state_io (no underscore)
- `_repricing_cid` module global preserved (cancel-race protection)
- `conviction_signal` imported from hourly_engine

⚠️ [REPRICE-LOCK] _repricing_cid prevents one-order guard from creating duplicates
    during cancel→submit cycle. Per feedback_reprice_race_condition.md ($9.38 loss).
⚠️ [CANCEL-SAFETY] Pre/post-cancel trade checks prevent phantom fills.
⚠️ [WR-ASSUMPTION] Repricing may reduce WR from 72% toward 55-65%.
"""

import logging
import time

from polymarket.conv_1h.constants import (
    _1H_REPRICE_COOLDOWN_S, _1H_REPRICE_ENABLED, _1H_REPRICE_MAX_PER_ORDER,
    _1H_REPRICE_MIN_AGE_S, _1H_REPRICE_STOP_BEFORE_END_S, _1H_REPRICE_THRESHOLD,
    _coin_from_title,
)
from polymarket.conv_1h.data_feeds import btc_price, poly_midpoint, poly_ob
from polymarket.conv_1h.state_io import bump_fill, log_order
from polymarket.strategy.hourly_engine import OBState, conviction_signal

logger = logging.getLogger(__name__)

# ⚠️ [REPRICE-LOCK] Global flag: prevents one-order guard from allowing
# new ENTER while a reprice cancel→submit is in progress.
_repricing_cid: str = ""  # "" = idle, non-empty = repricing this market


def get_repricing_cid() -> str:
    """Expose _repricing_cid for the orchestrator's one-order guard."""
    return _repricing_cid


# ═══════════════════════════════════════
#  Order Execution
# ═══════════════════════════════════════

def _execute_order(client, token_id: str, outcome: str,
                   price: float, size_usd: float, dry_run: bool,
                   coin: str = "BTC", cid: str = "") -> dict:
    """Submit a single limit order."""
    shares = size_usd / price if price > 0 else 0
    if shares < 5:
        # Bump to minimum 5 shares if budget allows
        min_cost = 5 * price
        if min_cost <= size_usd * 2:  # allow up to 2x bump
            size_usd = min_cost
            shares = 5
            logger.debug("Bumped to min 5 shares ($%.2f)", size_usd)
        else:
            logger.debug("Skip order: %.1f shares < 5 minimum", shares)
            return {"submitted": False, "reason": "below_min"}

    try:
        r = client.buy_shares(token_id, round(size_usd, 2), price=price)
        order_id = ""
        status = ""
        if isinstance(r, dict):
            order_id = r.get("orderID", r.get("id", ""))
            status = r.get("status", "")
            if r.get("dry_run"):
                # Paper mode: order sits on book as "live" pending.
                # Fill simulation via _check_fills_paper() each cycle.
                # Don't instant-fill — allows repricing to work on pending orders.
                pass  # keep status from mock ("live")
        # Phase 3 fix: if CLOB returns no order_id and status isn't matched,
        # treat as rejected — don't add to pending_orders.
        if not order_id and status != "matched":
            logger.warning("ORDER REJECTED %s: no order_id returned (status=%s)", outcome, status)
            log_order("rejected", "", cid, coin=coin,
                       outcome=outcome, price=price, size=shares, status=status)
            return {"outcome": outcome, "submitted": False, "reason": "rejected_no_id"}
        _now = time.time()
        _btc_now = btc_price(coin)
        logger.info("ORDER %s %.0f shares @ $%.3f ($%.2f) → %s",
                    outcome, shares, price, size_usd, status or order_id[:12])
        log_order("submit", order_id, cid, coin=coin,
                   outcome=outcome, price=price, size=shares,
                   status=status, btc=round(_btc_now, 2))
        return {"outcome": outcome, "price": price, "size": shares,
                "token_id": token_id, "order_id": order_id,
                "status": status, "submitted": True,
                "order_ts": _now, "btc_at_order": round(_btc_now, 2)}
    except Exception as e:
        logger.error("ORDER FAILED %s: %s", outcome, e)
        return {"outcome": outcome, "submitted": False, "error": str(e)}


# ═══════════════════════════════════════
#  Fill Confirmation
# ═══════════════════════════════════════

def _check_fills(state: dict, client) -> None:
    if not client or not hasattr(client, "get_orders"):
        return
    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        _coin = mkt.get("coin", _coin_from_title(mkt.get("title", "")))
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            # FIX: actually cancel orders on CLOB (was only logging, not cancelling)
            # GTC orders stay live after window close → can be adversely filled
            for _ep in pending:
                _oid = _ep.get("order_id", "")
                if _oid and client and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=_oid)
                        logger.info("CANCEL EXPIRED %s %s", cid[:8], _ep.get("outcome", ""))
                    except Exception:
                        pass
                log_order("expired", _oid, cid, coin=_coin,
                           outcome=_ep.get("outcome", ""))
            bump_fill(state, "expired", len(pending))
            mkt["pending_orders"] = []
            mkt["fills_confirmed"] = True
            continue
        try:
            trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
            trade_ids = set()
            for t in (trades or []):
                tid = t.get("taker_order_id", "")
                if tid:
                    trade_ids.add(tid)
                for mo in t.get("maker_orders", []):
                    mid = mo.get("order_id", "") if isinstance(mo, dict) else ""
                    if mid:
                        trade_ids.add(mid)
            open_orders = client.get_orders(market=cid)
            open_ids = {o.get("id", "") for o in open_orders} if open_orders else set()

            filled, still_open = [], []
            for po in pending:
                oid = po.get("order_id", "")
                if oid and oid in trade_ids:
                    filled.append(po)
                elif oid and oid in open_ids:
                    still_open.append(po)
                elif not oid:
                    # No order_id = CLOB rejected at submit (Phase 3 fix)
                    bump_fill(state, "cancelled")
                    log_order("rejected", "", cid, coin=_coin,
                               outcome=po.get("outcome", ""))
                else:
                    bump_fill(state, "cancelled")
                    log_order("cancelled_external", oid, cid, coin=_coin,
                               outcome=po.get("outcome", ""))

            # FIX: always update pending_orders — not just when fills exist.
            # Old code: `if filled: ... mkt["pending_orders"] = still_open`
            # Bug: cancelled orders stayed in pending forever when no fills,
            # making budget_remaining permanently stuck at 0 for that market.
            mkt["pending_orders"] = still_open

            if filled:
                for f in filled:
                    o = f["outcome"]
                    if o == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += f["size"]
                        mkt["up_avg_price"] = (old + f["size"] * f["price"]) / mkt["up_shares"]
                    elif o == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += f["size"]
                        mkt["down_avg_price"] = (old + f["size"] * f["price"]) / mkt["down_shares"]
                    mkt["entry_cost"] += f["size"] * f["price"]
                    bump_fill(state, "filled")
                    # AS metrics
                    _title = mkt.get("title", "").lower()
                    _fill_coin = "ETH" if "ethereum" in _title else "BTC"
                    _btc_fill = btc_price(_fill_coin)
                    _order_ts = f.get("order_ts", 0)
                    _ttf = round(time.time() - _order_ts, 1) if _order_ts > 0 else 0
                    log_order("fill", f.get("order_id", ""), cid, coin=_coin,
                               outcome=o, price=f["price"], size=f["size"],
                               btc_at_fill=round(_btc_fill, 2),
                               time_to_fill_s=_ttf)
                    logger.info("FILL %s %s: %.0f @ $%.3f ttf=%.0fs",
                                cid[:8], o, f["size"], f["price"], _ttf)

            if not still_open:
                mkt["fills_confirmed"] = True
        except Exception as e:
            logger.warning("Fill check %s: %s", cid[:8], e)


def _check_fills_paper(state: dict) -> None:
    """Simulate fills for paper/dry-run orders using real Polymarket mid prices.

    Each cycle: for each pending paper order, if order_price >= current market mid
    for that token, treat as filled (maker order lifted by taker).
    Tracks _repriced flag for WR comparison analysis.
    """
    now = time.time()
    now_ms = int(now * 1000)

    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        _coin = mkt.get("coin", _coin_from_title(mkt.get("title", "")))
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        # Expire orders past window end
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            for po in pending:
                log_order("paper_expired", po.get("order_id", ""), cid, coin=_coin,
                           outcome=po.get("outcome", ""),
                           repriced=po.get("_repriced", False))
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
            mid = poly_midpoint(tok) if tok else None
            if mid is None or mid <= 0:
                new_pending.append(po)
                continue

            # Simulate fill: our limit price >= current mid
            # (conservative: we'd be best bid getting lifted)
            if po["price"] >= mid:
                o = po["outcome"]
                s, p = po["size"], po["price"]
                if o == "UP":
                    old_val = mkt["up_shares"] * mkt["up_avg_price"]
                    mkt["up_shares"] += s
                    mkt["up_avg_price"] = (old_val + s * p) / mkt["up_shares"] if mkt["up_shares"] else p
                elif o == "DOWN":
                    old_val = mkt["down_shares"] * mkt["down_avg_price"]
                    mkt["down_shares"] += s
                    mkt["down_avg_price"] = (old_val + s * p) / mkt["down_shares"] if mkt["down_shares"] else p
                mkt["entry_cost"] += s * p
                bump_fill(state, "filled")

                repriced = po.get("_repriced", False)
                fill_age_s = now - po.get("order_ts", now)
                reprice_count = po.get("_reprice_count", 0)
                logger.info("PAPER FILL %s %s @ $%.3f (age=%.0fs, repriced=%s, #reprices=%d)",
                            cid[:8], o, p, fill_age_s, repriced, reprice_count)
                log_order("paper_fill", po.get("order_id", ""), cid, coin=_coin,
                           outcome=o, price=p, size=s,
                           repriced=repriced, reprice_count=reprice_count,
                           fill_age_s=round(fill_age_s))
            else:
                new_pending.append(po)

        mkt["pending_orders"] = new_pending
        if not new_pending and (mkt["up_shares"] > 0 or mkt["down_shares"] > 0):
            mkt["fills_confirmed"] = True


# ═══════════════════════════════════════
#  Repricing — Conviction-Driven (cheap zone only)
#  ⚠️ BMD markers: [CANCEL-SAFETY] [REPRICE-LOCK] [WR-ASSUMPTION]
# ═══════════════════════════════════════

def _reprice_1h(state: dict, client, config, cached_markets: list,
                dry_run: bool, coin_vols: dict | None = None) -> None:
    """Mild repricing: re-evaluate conviction → bump price within cheap zone.

    ⚠️ [WR-ASSUMPTION] This may reduce WR from 72% toward 55-65%.
    The positive selection bias of cheap fills is partially traded for
    higher fill rate. Paper test must verify repriced-fill WR > 55%.
    """
    global _repricing_cid

    if not _1H_REPRICE_ENABLED or not client:
        return
    _is_paper = dry_run  # paper mode: simulate cancel+replace, no client calls

    now = time.time()
    now_ms = int(now * 1000)

    for cid, mkt in list(state.get("markets", {}).items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        end_ms = mkt.get("window_end_ms", 0)
        start_ms = mkt.get("window_start_ms", 0)

        # Don't reprice near window end
        if end_ms > 0 and now_ms > end_ms - _1H_REPRICE_STOP_BEFORE_END_S * 1000:
            continue

        # Per-market cooldown
        if now - mkt.get("_last_reprice_ts", 0) < _1H_REPRICE_COOLDOWN_S:
            continue

        # Find matching market info for token IDs
        mkt_info = None
        for m in cached_markets:
            if m["cid"] == cid:
                mkt_info = m
                break
        if not mkt_info:
            continue

        coin = mkt_info["coin"]
        btc_open = mkt.get("btc_open_price", 0)
        if btc_open <= 0:
            continue

        current_price = btc_price(coin)
        if current_price <= 0:
            continue

        t_elapsed = (now_ms - start_ms) / 60_000  # minutes
        up_tok = mkt_info["up_tok"]
        ob = poly_ob(up_tok)

        # Recompute conviction with current data
        # ⚠️ [WR-ASSUMPTION] new_sig.entry_price will be higher than original
        # because time_trust has increased. This is conviction-driven, not market-chasing.
        _filled_cost = mkt.get("entry_cost", 0)
        _pending_cost = sum(
            o.get("size", 0) * o.get("price", 0)
            for o in pending if o.get("submitted")
        )
        budget_spent = _filled_cost + _pending_cost
        window_budget = state["bankroll"] * config.max_size_fraction
        budget_remaining_frac = max(0, (window_budget - budget_spent) / window_budget) if window_budget > 0 else 0

        new_sig = conviction_signal(
            t_elapsed=t_elapsed,
            btc_current=current_price,
            btc_open=btc_open,
            vol_1m=(coin_vols or {}).get(coin, 0.01),  # use cached vol from heavy cycle
            ob=ob, config=config,
            bankroll=state["bankroll"],
            budget_remaining_frac=budget_remaining_frac,
            current_position=None,  # repricing is for unfilled orders
        )

        if new_sig.action not in ("ENTER", "ADD"):
            continue  # signal says WAIT/EXIT → don't reprice, let order sit

        # Safety: if signal direction flipped, don't reprice (let order sit or expire)
        _pending_dir = pending[0].get("outcome", "")
        if _pending_dir and new_sig.direction != _pending_dir:
            logger.debug("REPRICE SKIP %s: direction flipped (%s → %s)", cid[:8], _pending_dir, new_sig.direction)
            continue

        # Check each pending order
        _repriced_any = False
        _new_pending = []

        for po in pending:
            old_price = po.get("price", 0)
            order_age_s = now - po.get("order_ts", now)
            reprice_count = po.get("_reprice_count", 0)

            # Guard: too young, too many reprices, or price hasn't moved enough
            if order_age_s < _1H_REPRICE_MIN_AGE_S:
                _new_pending.append(po)
                continue
            if reprice_count >= _1H_REPRICE_MAX_PER_ORDER:
                _new_pending.append(po)
                continue

            new_price = new_sig.entry_price
            # ⚠️ [BOOK-DEPTH] Only bump UP, never down. If conviction drops, keep old order.
            if new_price <= old_price + _1H_REPRICE_THRESHOLD:
                _new_pending.append(po)
                continue

            _oid = po.get("order_id", "")
            if not _oid:
                _new_pending.append(po)
                continue

            # ═══ REPRICE CYCLE ═══
            # ⚠️ [REPRICE-LOCK] Set lock to prevent one-order guard from creating duplicates
            _repricing_cid = cid

            # ── Paper mode: simulate cancel+replace (no client calls) ──
            if _is_paper:
                _new_po = dict(po)
                _new_po["order_id"] = f"paper_{int(time.time()*1000)}"
                _new_po["price"] = new_price
                _new_po["_reprice_count"] = reprice_count + 1
                _new_po["order_ts"] = time.time()
                _new_po["_repriced"] = True
                _new_pending.append(_new_po)
                logger.info("PAPER REPRICE %s %s: $%.3f → $%.3f (#%d)",
                            cid[:8], po.get("outcome", ""), old_price,
                            new_price, _new_po["_reprice_count"])
                log_order("paper_reprice", _new_po["order_id"], cid, coin=coin,
                           outcome=po.get("outcome", ""),
                           old_price=old_price, new_price=new_price,
                           reprice_count=_new_po["_reprice_count"])
                _repriced_any = True
                _repricing_cid = ""
                continue

            # ── Live mode: CLOB cancel → safety checks → replace ──

            # ⚠️ [CANCEL-SAFETY] Step 1: REST double-check before cancel
            # Pattern: get_trades → confirm not filled → proceed
            try:
                _pre_trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
                _pre_trade_ids = set()
                for _t in (_pre_trades or []):
                    _tid = _t.get("taker_order_id", "")
                    if _tid:
                        _pre_trade_ids.add(_tid)
                    for _mo in _t.get("maker_orders", []):
                        _mid = _mo.get("order_id", "") if isinstance(_mo, dict) else ""
                        if _mid:
                            _pre_trade_ids.add(_mid)
                if _oid in _pre_trade_ids:
                    logger.warning("REPRICE ABORT %s: order %s ALREADY FILLED (pre-cancel check)",
                                   cid[:8], _oid[:12])
                    _repricing_cid = ""
                    _new_pending.append(po)  # keep — fill check will process it
                    continue
            except Exception as e:
                logger.warning("REPRICE pre-check failed %s: %s — keeping order", cid[:8], e)
                _repricing_cid = ""
                _new_pending.append(po)
                continue

            # Step 2: Cancel old order
            try:
                client.client.cancel(order_id=_oid)
            except Exception as e:
                logger.warning("REPRICE CANCEL FAILED %s: %s — keeping order", cid[:8], e)
                _repricing_cid = ""
                _new_pending.append(po)
                continue

            # ⚠️ [CANCEL-SAFETY] Step 3: REST double-check AFTER cancel (500ms gap)
            time.sleep(0.5)
            try:
                _post_trades = client.get_trades(market=cid) if hasattr(client, "get_trades") else []
                _post_trade_ids = set()
                for _t in (_post_trades or []):
                    _tid = _t.get("taker_order_id", "")
                    if _tid:
                        _post_trade_ids.add(_tid)
                    for _mo in _t.get("maker_orders", []):
                        _mid = _mo.get("order_id", "") if isinstance(_mo, dict) else ""
                        if _mid:
                            _post_trade_ids.add(_mid)
                if _oid in _post_trade_ids:
                    # Phantom fill: order matched during cancel RTT
                    logger.warning("REPRICE ABORT (post-cancel) %s: order %s FILLED during cancel — "
                                   "phantom fill, NO replacement", cid[:8], _oid[:12])
                    # Account for the fill (will be picked up by _check_fills next cycle)
                    _repricing_cid = ""
                    # Don't add to _new_pending — _check_fills will handle it
                    _repriced_any = True
                    continue
            except Exception as e:
                logger.warning("REPRICE post-check failed %s: %s — order cancelled, "
                               "NOT re-placing (safety)", cid[:8], e)
                _repricing_cid = ""
                _repriced_any = True
                log_order("reprice_lost", _oid, cid, coin=coin,
                           outcome=po.get("outcome", ""), old_price=old_price)
                continue

            # Step 4: Submit replacement at new price
            try:
                _amount = round(po["size"] * new_price, 2)
                _r = client.buy_shares(po["token_id"], _amount, price=new_price)
                _new_oid = ""
                _new_status = ""
                if isinstance(_r, dict):
                    _new_oid = _r.get("orderID", _r.get("id", ""))
                    _new_status = _r.get("status", "")

                _new_po = dict(po)
                _new_po["order_id"] = _new_oid
                _new_po["price"] = new_price
                _new_po["_reprice_count"] = reprice_count + 1
                _new_po["order_ts"] = time.time()
                # ⚠️ [WR-ASSUMPTION] Track repriced fills separately for WR analysis
                _new_po["_repriced"] = True

                if _new_status == "matched":
                    # Instant fill — update market state directly (won't go through _check_fills)
                    _fill_outcome = po.get("outcome", "")
                    _fill_size = po["size"]
                    if _fill_outcome == "UP":
                        _old_val = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += _fill_size
                        mkt["up_avg_price"] = (_old_val + _fill_size * new_price) / mkt["up_shares"] if mkt["up_shares"] > 0 else new_price
                    elif _fill_outcome == "DOWN":
                        _old_val = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += _fill_size
                        mkt["down_avg_price"] = (_old_val + _fill_size * new_price) / mkt["down_shares"] if mkt["down_shares"] > 0 else new_price
                    mkt["entry_cost"] += _fill_size * new_price
                    logger.info("REPRICE+FILL %s %s: $%.3f → $%.3f (%.0f shares)",
                                cid[:8], _fill_outcome, old_price, new_price, _fill_size)
                    bump_fill(state, "filled")
                else:
                    _new_pending.append(_new_po)
                    logger.info("REPRICE %s %s: $%.3f → $%.3f (#%d)",
                                cid[:8], po.get("outcome", ""), old_price,
                                new_price, _new_po["_reprice_count"])

                log_order("reprice", _new_oid or _oid, cid, coin=coin,
                           outcome=po.get("outcome", ""),
                           old_price=old_price, new_price=new_price,
                           reprice_count=_new_po["_reprice_count"])
                _repriced_any = True

            except Exception as e:
                # Cancel succeeded but buy failed → order LOST from CLOB
                logger.error("REPRICE BUY FAILED %s: cancel OK but buy failed: %s — order LOST",
                             cid[:8], e)
                log_order("reprice_lost", _oid, cid, coin=coin,
                           outcome=po.get("outcome", ""),
                           old_price=old_price, new_price=new_price)
                _repriced_any = True
                # ⚠️ [REPRICE-LOCK] Release lock (was missing — would deadlock this market)
                _repricing_cid = ""
                continue

            # ⚠️ [REPRICE-LOCK] Release lock (normal path)
            _repricing_cid = ""

        if _repriced_any:
            mkt["pending_orders"] = _new_pending
            mkt["_last_reprice_ts"] = now
