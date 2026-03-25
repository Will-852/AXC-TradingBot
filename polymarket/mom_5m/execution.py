"""
mom_5m/execution.py — Order execution, fill tracking, cancel defense, profit lock for 5M bot.

Split from run_5m_live.py (2026-03-26).
🔴 2CHECK: This module directly places/cancels real orders on Polymarket CLOB.
Every function here handles real money.

Key functions:
- w4_entry: Both-sides entry with tiered lean (200 lines) — returns Union[None, str, list]
- execute_order: Single limit order submission
- check_fills: REST-based fill confirmation
- cancel_before_end: Cancel all pending orders at T-30s
- check_profit_lock: Sell lean shares when mid >= 99¢

⚠️ DOWNSTREAM D4: _PROFIT_LOCK_MID=0.99 (5M) vs 0.96 (mm) vs 0.95 (1H) — intentional.
"""

import json
import logging
import os
import time
from datetime import datetime

from polymarket.mom_5m.config import (
    COIN_CONFIG, CoinConfig, _CANCEL_BEFORE_END_S, _HKT, _LOG_DIR,
    _PROFIT_LOCK_BID_DISCOUNT, _PROFIT_LOCK_MID, _W4_LOG, _WINDOW_S,
    _tier_lean_ratio,
)
from polymarket.mom_5m.data import binance_taker_ratio, poly_midpoint
from polymarket.mom_5m.state import bump_fill, log_order

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
#  W4 Both-Sides Entry
# ═══════════════════════════════════════

def w4_entry(coin: str, cfg: CoinConfig, wl: dict,
             state: dict, client, dry_run: bool,
             bankroll: float, bet_pct: float) -> list[dict] | None:
    """W4 both-sides entry: momentum lean with per-coin config.

    Returns list of order result dicts, or None if no signal/too early/already entered.
    Also returns str literals: "EXPIRED", "ABORT", "VETO", "DEAD_HOUR" for caller dispatch.

    W4 pattern:
    - Lean 12.5:1 toward momentum direction
    - Sweep book (buy at ask price for speed)
    - Combined ~$1.00 (directional conviction, NOT structural arb)
    - Hold to resolution
    """
    from polymarket.mom_5m.signal import w4_signal

    cid = wl["cid"]

    # 🔴 2CHECK: duplicate order prevention (same window)
    if cid in state.get("markets", {}):
        logger.debug("W4 DUP %s: already in markets, skip", cid[:8])
        return None

    # Signal check
    w4_dir, w4_mag, w4_ret = w4_signal(wl["start_ms"], coin, cfg)
    if w4_dir == "WAIT":
        return None  # not yet past delay_s
    if w4_dir == "SKIP":
        elapsed_s = (time.time() * 1000 - wl["start_ms"]) / 1000
        if elapsed_s > _WINDOW_S * 0.6:
            # 60% of window passed, give up
            logger.info("W4 SKIP %s %s: mag=%.1f bps < %d bps after %.0fs",
                        coin, cid[:8], w4_mag, cfg.threshold_bps, elapsed_s)
            return "EXPIRED"  # caller removes from watchlist
        return None  # wait longer

    # ── Confidence tier → lean ratio ──
    _lean_ratio, _tier = _tier_lean_ratio(w4_mag)

    # ── Taker flow veto: skip if exchange flow disagrees with momentum ──
    # Data-validated: disagree = -500bps WR drag. Agree = +60bps.
    # Uses 2min lookback (includes pre-window data for T+15s entry).
    _taker_r = binance_taker_ratio(coin, lookback_s=120)
    _taker_veto = False
    if _taker_r is not None:
        if w4_dir == "UP" and _taker_r < 0.45:
            _taker_veto = True
        elif w4_dir == "DOWN" and _taker_r > 0.55:
            _taker_veto = True

    if _taker_veto:
        logger.info("W4 VETO %s %s: %s but taker_ratio=%.3f (disagrees) → skip",
                     coin, cid[:8], w4_dir, _taker_r)
        # Log vetoed entry for analysis
        try:
            with open(_W4_LOG, "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
                    "event": "w4_veto",
                    "cid": cid[:8], "coin": coin,
                    "lean_dir": w4_dir, "tier": _tier,
                    "w4_mag_bps": round(w4_mag, 1),
                    "taker_ratio": round(_taker_r, 4),
                    "reason": "taker_disagree",
                }) + "\n")
        except Exception:
            pass
        return "VETO"

    logger.info("W4 SIGNAL %s %s: %s %+.1f bps %s (lean=%.1f, taker=%.3f)",
                coin, cid[:8], w4_dir, w4_mag, _tier, _lean_ratio,
                _taker_r if _taker_r is not None else -1)

    # ── OB mid pricing -- fetch real Poly OB mid for entry price ──
    _up_mid = poly_midpoint(wl["up_tok"])
    _dn_mid = poly_midpoint(wl["dn_tok"])

    # Fallback: use 0.50 if OB unavailable (common in fresh windows)
    if not _up_mid or _up_mid <= 0.01 or _up_mid >= 0.99:
        _up_mid = 0.50
    if not _dn_mid or _dn_mid <= 0.01 or _dn_mid >= 0.99:
        _dn_mid = 0.50

    # Sweep pricing = buy at ask (mid + 1 tick).
    _TICK = 0.01
    _up_ask = round(min(0.95, _up_mid + _TICK), 2)
    _dn_ask = round(min(0.95, _dn_mid + _TICK), 2)
    _bs_combined = round(_up_ask + _dn_ask, 4)

    # Combined gate: directional lean mode, not pure arb.
    _COMBINED_MAX = 1.06
    if _bs_combined >= _COMBINED_MAX:
        logger.info("W4 SKIP %s %s: combined $%.4f >= $%.2f (spread too wide)",
                     coin, cid[:8], _bs_combined, _COMBINED_MAX)
        return "ABORT"

    # ── Sizing: share count ratio from tiered lean ──
    _budget = bankroll * bet_pct
    _avg_price = (_up_ask + _dn_ask) / 2
    _total_shares = max(10, _budget / _avg_price)

    _lean_share_frac = _lean_ratio / (_lean_ratio + 1)
    _hedge_share_frac = 1.0 / (_lean_ratio + 1)

    if w4_dir == "UP":
        _up_shares = max(cfg.min_order_size, round(_total_shares * _lean_share_frac, 1))
        _dn_shares = max(cfg.min_order_size, round(_total_shares * _hedge_share_frac, 1))
    else:
        _up_shares = max(cfg.min_order_size, round(_total_shares * _hedge_share_frac, 1))
        _dn_shares = max(cfg.min_order_size, round(_total_shares * _lean_share_frac, 1))

    # v4 port: Budget cap — min_order_size floors can inflate spend beyond budget
    _est_cost = _up_shares * _up_ask + _dn_shares * _dn_ask
    if _est_cost > _budget * 1.5 and _budget > 0:
        _scale = _budget / _est_cost
        _up_shares = max(1, round(_up_shares * _scale, 1))
        _dn_shares = max(1, round(_dn_shares * _scale, 1))
        logger.info("W4 BUDGET CAP %s: est $%.2f > budget $%.2f → scaled",
                     coin, _est_cost, _budget)

    # Actual cost check
    _actual_cost = _up_shares * _up_ask + _dn_shares * _dn_ask
    if _actual_cost > bankroll * 0.10:
        logger.warning("W4 SIZE CAP %s: $%.2f > 10%% bankroll $%.2f",
                        coin, _actual_cost, bankroll)
        # Scale down to 10%
        _scale = (bankroll * 0.10) / _actual_cost
        _up_shares = max(cfg.min_order_size, round(_up_shares * _scale, 1))
        _dn_shares = max(cfg.min_order_size, round(_dn_shares * _scale, 1))

    # ── Determine live vs paper ──
    is_live = cfg.live and not dry_run

    # ── Log W4 entry (always, regardless of live/paper) ──
    _bs_entry = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": "w4_entry",
        "cid": cid[:8], "coin": coin,
        "lean_dir": w4_dir, "lean_ratio": _lean_ratio, "tier": _tier,
        "contrarian": cfg.contrarian,
        "w4_mag_bps": round(w4_mag, 1),
        "w4_ret": round(w4_ret, 6),
        "taker_ratio": round(_taker_r, 4) if _taker_r is not None else None,
        "up_mid": round(_up_mid, 4), "dn_mid": round(_dn_mid, 4),
        "up_ask": _up_ask, "dn_ask": _dn_ask,
        "combined": _bs_combined,
        "up_shares": _up_shares, "dn_shares": _dn_shares,
        "budget": round(_budget, 2), "bankroll": round(bankroll, 2),
        "live": is_live,
    }
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_W4_LOG, "a") as f:
            f.write(json.dumps(_bs_entry) + "\n")
    except Exception:
        pass

    _mode = "LIVE" if is_live else "PAPER"
    logger.info("W4 %s %s %s: lean=%s %+.1fbps | UP@$%.2f*%.0f + DN@$%.2f*%.0f = $%.3f",
                _mode, coin, cid[:8], w4_dir, w4_mag,
                _up_ask, _up_shares, _dn_ask, _dn_shares, _bs_combined)

    # ── Submit orders ──
    orders = [
        {"token_id": wl["up_tok"], "outcome": "UP",
         "price": _up_ask, "size": _up_shares},
        {"token_id": wl["dn_tok"], "outcome": "DOWN",
         "price": _dn_ask, "size": _dn_shares},
    ]

    results = []
    for i, o in enumerate(orders):
        # Safety: if first order failed, abort second to prevent naked position
        if i > 0 and results and not results[0].get("submitted"):
            logger.warning("W4 ABORT 2nd order %s: first order failed → skip to avoid naked position",
                           o["outcome"])
            results.append({"outcome": o["outcome"], "submitted": False, "reason": "first_failed"})
            continue

        result = execute_order(
            client, o["token_id"], o["outcome"],
            o["price"], o["size"], dry_run=(not is_live),
            coin=coin, cid=cid)
        results.append(result)

    # Attach entry metadata to results for caller (market state creation)
    for r in results:
        r["_w4_dir"] = w4_dir
        r["_w4_mag_bps"] = round(w4_mag, 1)
        r["_w4_tier"] = _tier
        r["_w4_lean_ratio"] = _lean_ratio

    return results


# ═══════════════════════════════════════
#  Order Execution
# ═══════════════════════════════════════

def execute_order(client, token_id: str, outcome: str,
                  price: float, size: float, dry_run: bool,
                  coin: str = "", cid: str = "") -> dict:
    """Submit a single limit order. Returns order result dict.

    W4 style: uses aggressive limit (at ask) = pseudo taker.
    """
    amount = round(size * price, 2)
    if size < 5:
        min_cost = 5 * price
        if min_cost <= amount * 2:
            amount = min_cost
            size = 5
        else:
            logger.debug("Skip order: %.1f shares < 5 minimum", size)
            return {"outcome": outcome, "submitted": False, "reason": "below_min"}

    try:
        # 🔴 CRITICAL FIX: dry_run gate — MUST block real orders when paper mode
        if dry_run:
            logger.info("DRY 5M %s %s: %.1f shares @ $%.3f ($%.2f)",
                        coin.upper(), outcome, size, price, amount)
            return {
                "outcome": outcome, "submitted": True, "dry_run": True,
                "price": price, "size": size, "status": "matched",
                "order_id": f"5m_dry_{token_id[:8]}_{int(time.time())}",
            }

        r = client.buy_shares(token_id, amount, price=price)
        order_id = ""
        status = ""
        if isinstance(r, dict):
            order_id = r.get("orderID", r.get("id", ""))
            status = r.get("status", "")

        # Reject if CLOB returns no order_id
        if not order_id and status != "matched":
            logger.warning("ORDER REJECTED %s: no order_id (status=%s)", outcome, status)
            log_order("rejected", "", cid, outcome=outcome, price=price, size=size)
            return {"outcome": outcome, "submitted": False, "reason": "rejected_no_id"}

        logger.info("ORDER %s %s %.0f shares @ $%.3f ($%.2f) -> %s",
                    coin, outcome, size, price, amount, status or order_id[:12])
        log_order("submit", order_id, cid,
                   outcome=outcome, price=price, size=size,
                   status=status, coin=coin)
        return {"outcome": outcome, "price": price, "size": size,
                "token_id": token_id, "order_id": order_id,
                "status": status, "submitted": True,
                "order_ts": time.time()}
    except Exception as e:
        logger.error("ORDER FAILED %s %s: %s", coin, outcome, e)
        return {"outcome": outcome, "submitted": False, "error": str(e)}


# ═══════════════════════════════════════
#  Fill Confirmation
# ═══════════════════════════════════════

def check_fills(state: dict, client) -> None:
    """Check which submitted orders actually filled on-chain.

    REST-based (like 1H bot). WS user feed can be added later for live.
    """
    if not client or not hasattr(client, "get_orders"):
        return

    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN" or mkt.get("fills_confirmed"):
            continue
        pending = mkt.get("pending_orders", [])
        if not pending:
            continue

        end_ms = mkt.get("window_end_ms", 0)
        if end_ms > 0 and now_ms > end_ms:
            # Window over: cancel remaining orders on CLOB, mark expired
            for _ep in pending:
                _oid = _ep.get("order_id", "")
                if _oid and client and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=_oid)
                        logger.info("CANCEL EXPIRED %s %s", cid[:8], _ep.get("outcome", ""))
                    except Exception:
                        pass
                log_order("expired", _oid, cid, outcome=_ep.get("outcome", ""))
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
                    bump_fill(state, "cancelled")
                    log_order("rejected", "", cid, outcome=po.get("outcome", ""))
                else:
                    bump_fill(state, "cancelled")
                    log_order("cancelled_external", oid, cid,
                               outcome=po.get("outcome", ""))

            # Always update pending_orders (fix: cancelled orders don't stay stuck)
            mkt["pending_orders"] = still_open

            if filled:
                for f in filled:
                    o = f["outcome"]
                    p = f["price"]
                    s = f["size"]
                    if o == "UP":
                        old = mkt["up_shares"] * mkt["up_avg_price"]
                        mkt["up_shares"] += s
                        mkt["up_avg_price"] = (old + s * p) / mkt["up_shares"]
                    elif o == "DOWN":
                        old = mkt["down_shares"] * mkt["down_avg_price"]
                        mkt["down_shares"] += s
                        mkt["down_avg_price"] = (old + s * p) / mkt["down_shares"]
                    mkt["entry_cost"] += s * p
                    bump_fill(state, "filled")
                    _ttf = round(time.time() - f.get("order_ts", 0), 1) if f.get("order_ts") else 0
                    log_order("fill", f.get("order_id", ""), cid,
                               outcome=o, price=p, size=s,
                               time_to_fill_s=_ttf)
                    logger.info("FILL %s %s: %.0f @ $%.3f ttf=%.0fs",
                                cid[:8], o, s, p, _ttf)

            if not still_open:
                mkt["fills_confirmed"] = True

        except Exception as e:
            logger.warning("Fill check %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  Cancel Defense
# ═══════════════════════════════════════

def cancel_before_end(state: dict, client, dry_run: bool):
    """Cancel all pending orders 30s before window end.

    5M cancel is much simpler than 15M:
    - No dynamic TTL, no layer-specific cancel, no adverse spot move cancel
    - Just: cancel all open orders at T-30s
    """
    if dry_run or not client:
        return

    now_ms = int(time.time() * 1000)
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN":
            continue
        end_ms = mkt.get("window_end_ms", 0)
        if end_ms <= 0:
            continue
        tte_s = (end_ms - now_ms) / 1000
        if 0 < tte_s <= _CANCEL_BEFORE_END_S:
            pending = mkt.get("pending_orders", [])
            for po in pending:
                oid = po.get("order_id", "")
                if oid and hasattr(client, "client"):
                    try:
                        client.client.cancel(order_id=oid)
                        logger.info("CANCEL T-%.0fs %s %s", tte_s, cid[:8],
                                    po.get("outcome", ""))
                    except Exception:
                        pass
            if pending:
                bump_fill(state, "cancelled", len(pending))
                mkt["pending_orders"] = []


# ═══════════════════════════════════════
#  Profit Lock — sell lean side when mid >= threshold
# ═══════════════════════════════════════

def check_profit_lock(state: dict, client, dry_run: bool):
    """Sell lean shares when lean side price reaches 99¢+.

    ⚠️ DOWNSTREAM D4: _PROFIT_LOCK_MID=0.99 (5M) vs 0.96 (mm) vs 0.95 (1H) — intentional.
    """
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN":
            continue
        if mkt.get("_profit_locked"):
            continue

        # Check BOTH sides — sell whichever hits 99¢+
        for side in ("UP", "DOWN"):
            if side == "UP":
                tok = mkt.get("up_token_id", "")
                shares = mkt.get("up_shares", 0)
            else:
                tok = mkt.get("down_token_id", "")
                shares = mkt.get("down_shares", 0)

            if shares <= 0 or not tok:
                continue

            mid = poly_midpoint(tok)
            if mid is None or mid < _PROFIT_LOCK_MID:
                continue

            # ── PROFIT LOCK TRIGGERED — sell winning side ──
            lean_dir = side
            lean_tok = tok
            lean_shares = shares
            lean_mid = mid
            break
        else:
            continue  # neither side hit threshold

        sell_price = round(lean_mid - _PROFIT_LOCK_BID_DISCOUNT, 2)
        sell_price = max(0.01, sell_price)

        coin = mkt.get("coin", "?")
        logger.info("PROFIT LOCK %s %s: lean=%s mid=$%.2f → sell %.0f shares @ $%.2f",
                     coin, cid[:8], lean_dir, lean_mid, lean_shares, sell_price)

        # Check if this coin is actually live (not paper)
        _coin_cfg = COIN_CONFIG.get(coin)
        _is_paper = dry_run or not client or (_coin_cfg and not _coin_cfg.live)

        if _is_paper:
            # Paper: simulate sell
            revenue = lean_shares * sell_price
            mkt["payout"] += revenue
            if lean_dir == "UP":
                mkt["up_shares"] = 0
            else:
                mkt["down_shares"] = 0
            mkt["_profit_locked"] = True
            logger.info("PROFIT LOCK DRY %s: sold %.0f shares @ $%.2f = $%.2f",
                         coin, lean_shares, sell_price, revenue)
        else:
            # Live: submit sell order
            try:
                sell_amount = round(lean_shares * sell_price, 2)
                r = client.sell_shares(lean_tok, sell_amount, price=sell_price)
                status = r.get("status", "") if isinstance(r, dict) else ""
                logger.info("PROFIT LOCK LIVE %s: sell %.0f @ $%.2f → %s",
                             coin, lean_shares, sell_price, status)
                if status == "matched":
                    revenue = lean_shares * sell_price
                    mkt["payout"] += revenue
                    if lean_dir == "UP":
                        mkt["up_shares"] = 0
                    else:
                        mkt["down_shares"] = 0
                    mkt["_profit_locked"] = True
                else:
                    # Order pending — track for later
                    mkt["_profit_lock_pending"] = {
                        "order_id": r.get("orderID", "") if isinstance(r, dict) else "",
                        "sell_price": sell_price,
                        "shares": lean_shares,
                        "token_id": lean_tok,
                    }
                    mkt["_profit_locked"] = True  # don't retry
            except Exception as e:
                logger.error("PROFIT LOCK FAILED %s: %s", coin, e)
                mkt["_profit_locked"] = True  # don't retry on error

        # Log to W4 log
        try:
            with open(_W4_LOG, "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
                    "event": "profit_lock",
                    "cid": cid[:8], "coin": coin,
                    "lean_dir": lean_dir,
                    "lean_mid": round(lean_mid, 4),
                    "sell_price": sell_price,
                    "shares": lean_shares,
                    "dry_run": dry_run,
                }) + "\n")
        except Exception:
            pass
