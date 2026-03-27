"""
conv_1h/exit_logic.py — Forced exit, profit lock, and resolution for 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
🔴 2CHECK: _try_sell_partial and _check_black_swan place REAL sell orders.
            _check_resolutions settles PnL and updates state.

Merged from two non-contiguous blocks:
  Block A (lines 634-734): _try_sell_partial, _check_black_swan
  Block B (lines 1313-1381): _check_resolutions

⚠️ DOWNSTREAM D3: _BLACK_SWAN_MID=0.95 (1H) vs 0.96 (mm) — intentionally different.
"""

import logging
import time
from datetime import datetime, timedelta

from polymarket.conv_1h.constants import (
    _BINANCE, _BLACK_SWAN_MID, _BLACK_SWAN_SELL_PCT, _HKT,
    _coin_from_title,
)
from polymarket.conv_1h.data_feeds import _get_json, poly_midpoint
from polymarket.conv_1h.paper_trading import _paper_resolve
from polymarket.conv_1h.state_io import (
    bump_fill, from_dict, log_order, log_trade, to_dict,
)
from polymarket.strategy.market_maker import resolve_market

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
#  Forced Exit — Black Swan Protection
# ═══════════════════════════════════════

def _try_sell_partial(client, state: dict, cid: str, mkt: dict,
                      up_tok: str, dn_tok: str, reason: str = "",
                      known_mid: float = 0, sell_pct: float = 1.0) -> bool:
    """Sell shares in a market. Returns True if sold.
    sell_pct: fraction to sell (0.90 = sell 90%, keep 10% free roll).
    known_mid: caller's last-known mid for pricing. Avoids re-fetch stale/fail.
    """
    if not client or not hasattr(client, "sell_shares"):
        return False
    sold = False
    for side, tok_key, shares_key, avg_key, tok in [
        ("UP", "up_token_id", "up_shares", "up_avg_price", up_tok),
        ("DOWN", "down_token_id", "down_shares", "down_avg_price", dn_tok),
    ]:
        shares = mkt.get(shares_key, 0)
        avg = mkt.get(avg_key, 0)
        if shares < 1 or not tok:
            continue
        # Use caller's known mid if available, else fetch (with NO fallback)
        mid = known_mid if known_mid > 0 else poly_midpoint(tok)
        if not mid or mid <= 0:
            logger.warning("SELL ABORT %s %s: mid unavailable, refusing to sell blind", cid[:8], side)
            continue
        # Calculate shares to sell
        _sell_shares = max(1, int(shares * sell_pct))
        _keep = shares - _sell_shares
        sell_price = 0.99  # limit sell near max — let buyers come to us
        try:
            client.sell_shares(tok, _sell_shares, price=sell_price)
            pnl = _sell_shares * (sell_price - avg)
            mkt[shares_key] = _keep
            # FIX: reduce entry_cost proportionally so resolve_market PnL is correct.
            # Without this, resolve_market computes payout - FULL_original_cost → phantom loss.
            _sold_cost = _sell_shares * avg
            mkt["entry_cost"] = max(0, mkt.get("entry_cost", 0) - _sold_cost)
            mkt["realized_pnl"] = mkt.get("realized_pnl", 0) + pnl
            if _keep > 0:
                mkt["cost_recovered"] = True  # remaining shares = free roll
            logger.info("SELL [%s] %s %s: %d/%d @ $%.3f | pnl=$%.2f | keep %d free",
                        reason, cid[:8], side, _sell_shares, int(shares), sell_price, pnl, int(_keep))
            sold = True
        except Exception as e:
            logger.warning("SELL FAILED [%s] %s %s: %s", reason, cid[:8], side, e)
    # Only mark RESOLVED if no shares remain
    remaining = mkt.get("up_shares", 0) + mkt.get("down_shares", 0)
    if sold and remaining < 1:
        mkt["phase"] = "RESOLVED"
        mkt["early_exit"] = reason
    return sold


def _check_black_swan(client, state: dict, dry_run: bool):
    """Check all open positions — sell if mid ≥ 95¢. Runs every cycle."""
    if dry_run or not client or not hasattr(client, "sell_shares"):
        return
    for cid, mkt in list(state["markets"].items()):
        if mkt.get("phase") != "OPEN":
            continue
        if mkt.get("cost_recovered"):
            continue  # free roll shares — hold to resolution, don't re-sell
        for side, tok_key, shares_key in [
            ("UP", "up_token_id", "up_shares"),
            ("DOWN", "down_token_id", "down_shares"),
        ]:
            shares = mkt.get(shares_key, 0)
            tok = mkt.get(tok_key, "")
            if shares < 1 or not tok:
                continue
            mid = poly_midpoint(tok)
            if mid and mid >= _BLACK_SWAN_MID:
                logger.warning("BLACK SWAN %s %s: mid $%.3f ≥ $%.2f → selling all + hedge",
                               cid[:8], side, mid, _BLACK_SWAN_MID)
                sold = _try_sell_partial(client, state, cid, mkt,
                              mkt.get("up_token_id", ""), mkt.get("down_token_id", ""),
                              reason="profit_lock_95pct", known_mid=mid,
                              sell_pct=_BLACK_SWAN_SELL_PCT)
                # Greed hedge: buy opposite side min 5 shares at MARKET price (speed > price)
                # Must execute instantly — market can reverse in seconds.
                # NOTE: dry_run guard at line 90 protects this path — _check_black_swan returns early if dry_run.
                if sold:
                    opp_tok = mkt.get("down_token_id", "") if side == "UP" else mkt.get("up_token_id", "")
                    opp_side = "DOWN" if side == "UP" else "UP"
                    opp_mid = poly_midpoint(opp_tok) if opp_tok else None
                    # Aggressive limit = pseudo market order: mid + 100% overpay
                    hedge_price = round(max(0.01, (opp_mid or 0.06) * 2.0), 2)
                    if opp_tok and hedge_price < 0.15:  # cap: don't pay more than 15¢
                        try:
                            hedge_cost = round(2 * hedge_price, 2)
                            client.buy_shares(opp_tok, hedge_cost, price=hedge_price)
                            logger.info("HEDGE %s %s: 2 shares @ $%.2f ($%.2f) — market order",
                                        cid[:8], opp_side, hedge_price, hedge_cost)
                        except Exception as e:
                            logger.warning("HEDGE FAILED %s: %s", cid[:8], e)


# ═══════════════════════════════════════
#  Resolution (Binance 1H OHLC)
# ═══════════════════════════════════════

def _check_resolutions(state: dict):
    now_ms = int(time.time() * 1000)
    # Batch resolutions by window_start_ms so BTC+ETH same-hour = 1 event
    # for consecutive loss counting (correlated outcomes)
    hour_pnl: dict[int, float] = {}  # {start_ms: net_pnl}

    for cid, md in list(state["markets"].items()):
        if md.get("phase") == "RESOLVED":
            continue
        end_ms = md.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + 120_000:
            continue
        start_ms = md.get("window_start_ms", 0)
        if start_ms <= 0:
            continue

        title = md.get("title", "").lower()
        _res_coin = _coin_from_title(md.get("title", ""))
        if "solana" in title:
            sym = "SOLUSDT"
        elif "ethereum" in title:
            sym = "ETHUSDT"
        else:
            sym = "BTCUSDT"
        data = _get_json(f"{_BINANCE}/klines?symbol={sym}&interval=1h&startTime={start_ms}&limit=1")
        if not data:
            continue

        btc_o, btc_c = float(data[0][1]), float(data[0][4])
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = from_dict(md)
        pnl = resolve_market(ms, result)
        resolved_dict = to_dict(ms)
        # Preserve runtime keys not in MMMarketState
        for _rk in ("pending_orders", "fills_confirmed", "cost_recovered", "early_exit"):
            if _rk in md:
                resolved_dict[_rk] = md[_rk]
        state["markets"][cid] = resolved_dict
        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["total_markets"] = state.get("total_markets", 0) + 1

        # Accumulate per-hour PnL (BTC+ETH same hour = 1 event)
        hour_pnl[start_ms] = hour_pnl.get(start_ms, 0) + pnl

        log_trade({"ts": datetime.now(tz=_HKT).isoformat(), "cid": cid,
                     "result": result, "pnl": round(pnl, 4),
                     "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
                     "total_pnl": round(state["total_pnl"], 2)}, coin=_res_coin)

        d = "↑" if result == "UP" else "↓"
        print(f"  RESOLVED {cid[:8]} {d} | PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")

        # Paper trade resolution
        _paper_resolve(cid, result)

    # Update consecutive losses per hour-window (not per market)
    for _start_ms, net_pnl in sorted(hour_pnl.items()):
        if net_pnl < 0:
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            if state["consecutive_losses"] >= 5:
                cd = (datetime.now(tz=_HKT) + timedelta(hours=4)).isoformat(timespec="seconds")
                state["cooldown_until"] = cd
                logger.warning("CIRCUIT BREAKER: %d consecutive hour-losses → cooldown until %s",
                               state["consecutive_losses"], cd)
        else:
            state["consecutive_losses"] = 0
