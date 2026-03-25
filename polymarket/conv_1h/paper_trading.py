"""
conv_1h/paper_trading.py — Paper PnL simulation for 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
🟡 VERIFY: Tracks simulated buy/sell with real Poly mid, cumulative PnL.
Not precise (ignores slippage, partial fills) but good enough to decide go/no-go.

Dual-track PnL: engine_price (limit order, often unfillable) vs market_price (taker reality).
BMD fix (2026-03): engine price ($0.59) never fills. Real mid ($0.70+) is what we'd actually pay.
"""

import json
import logging
import time
from datetime import datetime

from polymarket.conv_1h.constants import _HKT, _PAPER_BUDGET, _PAPER_PNL_LOG

logger = logging.getLogger(__name__)

# ─── Module-level mutable state ───
_paper_state = {
    "positions": {},      # {cid: {coin, direction, shares, entry_price, entry_mid, cost}}
    "resolved": [],       # [{coin, direction, entry_price, result, pnl, ...}]
    "total_pnl": 0.0,
    "by_coin": {},        # {coin: {trades, wins, pnl}}
}


def _paper_enter(cid: str, coin: str, direction: str, entry_price: float,
                 up_mid: float, conviction: float):
    """Record a simulated entry. Uses REAL Poly mid for our side (not engine price).
    BMD fix: engine price ($0.59) never fills. Real mid ($0.70+) is what we'd actually pay.
    Also tracks fill_feasible: whether engine price <= our side's mid (could fill)."""
    if cid in _paper_state["positions"]:
        return  # already in
    # Our side's real Poly mid (what we'd actually pay as taker)
    our_mid = up_mid if direction == "UP" else (1.0 - up_mid) if up_mid else 0
    # Fill feasibility: engine price must be >= ask (~mid+2c) to fill as limit order
    fill_feasible = entry_price >= (our_mid - 0.02) if our_mid > 0 else False
    # Paper PnL uses TWO prices: engine_price (limit order) and market_price (taker)
    # Report both so we can compare
    market_price = round(our_mid + 0.02, 4) if our_mid > 0 else entry_price
    shares_engine = _PAPER_BUDGET / entry_price if entry_price > 0 else 0
    shares_market = _PAPER_BUDGET / market_price if market_price > 0 else 0
    _paper_state["positions"][cid] = {
        "coin": coin, "direction": direction,
        "shares_engine": round(shares_engine, 1),
        "shares_market": round(shares_market, 1),
        "entry_engine": round(entry_price, 4),
        "entry_market": round(market_price, 4),
        "entry_mid": round(up_mid, 4) if up_mid else 0,
        "our_mid": round(our_mid, 4),
        "cost_engine": round(shares_engine * entry_price, 2),
        "cost_market": round(shares_market * market_price, 2),
        "fill_feasible": fill_feasible,
        "conviction": round(conviction, 3),
        "ts": time.time(),
    }
    _fill_tag = "✅FILL" if fill_feasible else "❌MISS"
    logger.info("PAPER ENTER %s %s %s: engine=$%.3f market=$%.3f mid=$%.3f %s conv=%.2f",
                coin, direction, cid[:8], entry_price, market_price, our_mid, _fill_tag, conviction)


def _paper_resolve(cid: str, result: str):
    """Resolve a paper position. Logs BOTH engine PnL and market PnL.
    BMD fix: engine PnL is fictional (0% fill rate). Market PnL is reality."""
    pos = _paper_state["positions"].pop(cid, None)
    if not pos:
        return
    coin = pos["coin"]
    won = (pos["direction"] == result)

    # Engine PnL (limit order at conviction price — likely unfillable)
    s_eng = pos.get("shares_engine", pos.get("shares", 0))
    ep_eng = pos.get("entry_engine", pos.get("entry_price", 0))
    c_eng = pos.get("cost_engine", pos.get("cost", 0))
    pnl_engine = s_eng * (1.0 - ep_eng) if won else -c_eng

    # Market PnL (taker at real Poly mid + 2c — what would actually happen)
    s_mkt = pos.get("shares_market", s_eng)
    ep_mkt = pos.get("entry_market", ep_eng)
    c_mkt = pos.get("cost_market", c_eng)
    pnl_market = s_mkt * (1.0 - ep_mkt) if won else -c_mkt

    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "coin": coin, "cid": cid[:16],
        "direction": pos["direction"], "result": result,
        "won": won,
        "entry_engine": round(ep_eng, 4), "entry_market": round(ep_mkt, 4),
        "our_mid": pos.get("our_mid", 0),
        "fill_feasible": pos.get("fill_feasible", False),
        "pnl_engine": round(pnl_engine, 2),
        "pnl_market": round(pnl_market, 2),
        "conviction": pos.get("conviction", 0),
        # Backward compat fields
        "entry_price": round(ep_eng, 4), "pnl": round(pnl_engine, 2),
        "shares": s_eng, "cost": c_eng, "entry_mid": pos.get("entry_mid", 0),
    }
    _paper_state["resolved"].append(record)
    _paper_state["total_pnl"] += pnl_engine
    # Track market PnL separately
    _paper_state.setdefault("total_pnl_market", 0.0)
    _paper_state["total_pnl_market"] += pnl_market

    # Per-coin stats
    if coin not in _paper_state["by_coin"]:
        _paper_state["by_coin"][coin] = {"trades": 0, "wins": 0, "pnl": 0.0, "pnl_market": 0.0}
    cs = _paper_state["by_coin"][coin]
    cs["trades"] += 1
    cs["wins"] += int(won)
    cs["pnl"] += pnl_engine
    cs.setdefault("pnl_market", 0.0)
    cs["pnl_market"] += pnl_market

    # Log to file
    record["total_pnl"] = round(_paper_state["total_pnl"], 2)
    record["total_pnl_market"] = round(_paper_state["total_pnl_market"], 2)
    try:
        with open(_PAPER_PNL_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    wr = cs["wins"] / cs["trades"] * 100 if cs["trades"] else 0
    tag = "✅" if won else "❌"
    _fill_tag = "FILL" if pos.get("fill_feasible") else "MISS"
    logger.info("PAPER %s %s %s %s: eng=$%+.2f mkt=$%+.2f [%s] | %s WR=%.0f%% (%d/%d) | ENG=$%+.2f MKT=$%+.2f",
                tag, coin, pos.get("direction",""), result, pnl_engine, pnl_market, _fill_tag,
                coin, wr, cs["wins"], cs["trades"], cs["pnl"],
                _paper_state.get("total_pnl_market", 0))


def _paper_status():
    """Print paper trading summary."""
    ps = _paper_state
    n = len(ps["resolved"])
    if n == 0:
        return
    wins = sum(1 for r in ps["resolved"] if r["won"])
    logger.info("PAPER SUMMARY: %d trades | WR=%.0f%% | PnL=$%+.2f | Open=%d",
                n, wins / n * 100, ps["total_pnl"], len(ps["positions"]))
    for coin, cs in sorted(ps["by_coin"].items()):
        wr = cs["wins"] / cs["trades"] * 100 if cs["trades"] else 0
        logger.info("  %s: %d trades WR=%.0f%% PnL=$%+.2f",
                    coin, cs["trades"], wr, cs["pnl"])
