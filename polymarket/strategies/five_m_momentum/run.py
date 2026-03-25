#!/usr/bin/env python3
"""
5M Momentum Strategy — Runner (main loop + execution).

Three-tier decision tree:
  < 8bps  → SKIP
  8-15bps → MAKER_ARB (both sides, combined < $0.98)
  >= 15bps → TAKER_DIRECTIONAL (aggressive GTC lean + optional maker hedge)

Usage:
  PYTHONPATH=.:scripts python3 polymarket/strategies/five_m_momentum/run.py --dry-run
  PYTHONPATH=.:scripts python3 polymarket/strategies/five_m_momentum/run.py --live
"""

import argparse
import json
import logging
import os
import signal as _signal
import sys
import time
import urllib.request
from copy import copy
from datetime import datetime, timedelta, timezone

# ── Path setup ──
_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.exchange.polymarket_client import PolymarketClient
from polymarket.data.ws_binance import BinancePriceFeed

from polymarket.strategies.five_m_momentum.config import (
    # Timing
    ENTRY_DELAY_S, WINDOW_DURATION_S, ENTRY_CYCLE_S,
    SCAN_INTERVAL_S, WINDOW_GIVE_UP_PCT,
    # Thresholds
    SKIP_BELOW_BPS, ARB_UPPER_BPS,
    # Arb mode
    ARB_MAX_COMBINED, ARB_SPREAD_FROM_MID, ARB_MAX_PRICE,
    # Taker mode
    TAKER_ASK_BUFFER, TAKER_ASK_CAP, HEDGE_SPREAD_FROM_MID,
    HEDGE_MAX_PRICE, HEDGE_ENABLED,
    # Sizing
    BET_SIZE_USD, MIN_ORDER_SHARES,
    # Risk
    SESSION_MAX_LOSS, MAX_CONSECUTIVE_LOSSES,
    # Coins + experiment
    COINS, EXPERIMENT_TRADE_LIMIT,
)
from polymarket.strategies.five_m_momentum.momentum_signal import compute_signal, Mode
from polymarket.strategies.five_m_momentum.modes.maker_arb import plan_maker_arb
from polymarket.strategies.five_m_momentum.modes.single_side import (
    plan_taker_directional, plan_hedge,
)
from polymarket.strategies.five_m_momentum.modes.skip import should_skip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [5M-MOM] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ──
_HKT = timezone(timedelta(hours=8))
_LOG_DIR = os.path.join(_AXC, "polymarket", "logs")
_TRADE_LOG = os.path.join(_LOG_DIR, "5m_momentum_trades.jsonl")
_SIGNAL_LOG = os.path.join(_LOG_DIR, "5m_momentum_signals.jsonl")
_STATE_PATH = os.path.join(_LOG_DIR, "5m_momentum_state.json")
_GAMMA_URL = "https://gamma-api.polymarket.com"

# ── Shutdown ──
_running = True


def _shutdown(signum, _frame):
    global _running
    log.info("Shutdown signal %s", signum)
    _running = False


_signal.signal(_signal.SIGINT, _shutdown)
_signal.signal(_signal.SIGTERM, _shutdown)


# ═══════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════

_HTTP_TIMEOUT_S = 3  # Per-request timeout. Keep short to avoid cascade.


def _get_json(url: str, timeout: int = _HTTP_TIMEOUT_S):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AXC-5M-MOM/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug("HTTP fail %s: %s", url[:80], e)
        return None


def _log_jsonl(path: str, entry: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log.warning("Log write failed %s: %s", os.path.basename(path), e)


def _now_hkt() -> str:
    return datetime.now(tz=_HKT).isoformat(timespec="seconds")


def get_poly_mid(token_id: str) -> float | None:
    """Get Polymarket midpoint. Returns None if unavailable or placeholder."""
    data = _get_json(
        f"https://clob.polymarket.com/midpoint?token_id={token_id}", timeout=5
    )
    if data and "mid" in data:
        mid = float(data["mid"])
        if 0.02 < mid < 0.99:  # Only reject true placeholders (0.01/0.99)
            return mid
    return None


# ═══════════════════════════════════════
#  5M Window Discovery
# ═══════════════════════════════════════

def discover_windows() -> list[dict]:
    """Find current + next 5M windows for live coins."""
    results = []
    now_s = int(time.time())

    for i in range(3):
        window_start = (now_s // WINDOW_DURATION_S) * WINDOW_DURATION_S + i * WINDOW_DURATION_S
        window_end = window_start + WINDOW_DURATION_S

        if now_s > window_end + 60:
            continue

        for coin_name, coin_cfg in COINS.items():
            if not coin_cfg.live:
                continue

            slug = f"{coin_cfg.slug_prefix}-updown-5m-{window_start}"
            data = _get_json(f"{_GAMMA_URL}/markets?slug={slug}", timeout=5)

            if not data or not isinstance(data, list) or not data:
                continue

            mkt = data[0]
            cid = mkt.get("conditionId", "") or mkt.get("condition_id", "")

            # ⚠️ RISK: Gamma API returns tokens in two formats:
            #   - "tokens" list (old format, may be None)
            #   - "clobTokenIds" JSON string + "outcomes" JSON string (current format)
            up_tok = dn_tok = ""
            tokens = mkt.get("tokens") or []
            if tokens:
                for t in tokens:
                    outcome = t.get("outcome", "").upper()
                    if outcome in ("UP", "YES"):
                        up_tok = t.get("token_id", "")
                    elif outcome in ("DOWN", "NO"):
                        dn_tok = t.get("token_id", "")
            else:
                # Current Gamma format: clobTokenIds + outcomes as JSON strings
                try:
                    clob_ids = json.loads(mkt.get("clobTokenIds", "[]"))
                    outcomes = json.loads(mkt.get("outcomes", "[]"))
                    for outcome, tid in zip(outcomes, clob_ids):
                        if outcome.upper() in ("UP", "YES"):
                            up_tok = str(tid)
                        elif outcome.upper() in ("DOWN", "NO"):
                            dn_tok = str(tid)
                except (json.JSONDecodeError, TypeError):
                    pass

            if cid and up_tok and dn_tok:
                results.append({
                    "cid": cid, "coin": coin_name, "slug": slug,
                    "up_tok": up_tok, "dn_tok": dn_tok,
                    "start_ms": window_start * 1000,
                    "end_ms": window_end * 1000,
                })

    return results


# ═══════════════════════════════════════
#  Session State
# ═══════════════════════════════════════

class SessionState:
    def __init__(self):
        self.pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.entered_cids: set = set()
        self.pending_orders: dict = {}  # cid -> {mode, coin, direction, end_ms, ...}
        self.mode_a_count: int = 0
        self.mode_b_count: int = 0

    @property
    def total_trades(self) -> int:
        return self.mode_a_count + self.mode_b_count

    def record_fill(self, cid: str, pnl: float):
        """Called when a market resolves and PnL is known."""
        self.pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        log.info("RESOLVED %s: PnL=$%.2f | Session=$%.2f | Consec=%d",
                 cid[:8], pnl, self.pnl, self.consecutive_losses)

    def should_stop(self) -> str | None:
        if self.pnl <= -SESSION_MAX_LOSS:
            return f"session_loss ${self.pnl:.2f} <= -${SESSION_MAX_LOSS}"
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return f"consecutive_losses {self.consecutive_losses} >= {MAX_CONSECUTIVE_LOSSES}"
        if self.total_trades >= EXPERIMENT_TRADE_LIMIT:
            return f"experiment_limit {self.total_trades} >= {EXPERIMENT_TRADE_LIMIT}"
        return None

    def save(self):
        """Atomic state persistence for crash recovery."""
        state = {
            "entered_cids": list(self.entered_cids),
            "pnl": self.pnl,
            "consecutive_losses": self.consecutive_losses,
            "mode_a_count": self.mode_a_count,
            "mode_b_count": self.mode_b_count,
            "pending_orders": self.pending_orders,
            "saved_at": _now_hkt(),
        }
        try:
            os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
            tmp = _STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, _STATE_PATH)
        except Exception as e:
            log.warning("State save failed: %s", e)

    def load(self):
        """Restore from disk after crash."""
        if not os.path.exists(_STATE_PATH):
            return
        try:
            with open(_STATE_PATH) as f:
                state = json.load(f)
            self.entered_cids = set(state.get("entered_cids", []))
            self.pnl = state.get("pnl", 0.0)
            self.consecutive_losses = state.get("consecutive_losses", 0)
            self.mode_a_count = state.get("mode_a_count", 0)
            self.mode_b_count = state.get("mode_b_count", 0)
            self.pending_orders = state.get("pending_orders", {})
            log.info("Restored: %d cids, PnL=$%.2f, trades=%d, pending=%d",
                     len(self.entered_cids), self.pnl, self.total_trades,
                     len(self.pending_orders))
        except Exception as e:
            log.warning("State load failed: %s", e)


# ═══════════════════════════════════════
#  Resolution Check
# ═══════════════════════════════════════

def _check_resolution(session: SessionState, now_ms: int):
    """Check pending orders for resolved windows. Compute PnL from BTC price."""
    resolved = []
    for pcid, pinfo in session.pending_orders.items():
        # ⚠️ RISK: wait 90s after window end for Chainlink resolution
        if now_ms <= pinfo["end_ms"] + 90_000:
            continue

        # Fetch BTC close price at window end
        end_ms = pinfo["end_ms"]
        kline = _get_json(
            f"https://api.binance.com/api/v3/klines?"
            f"symbol=BTCUSDT&interval=1s&startTime={end_ms}&limit=1",
            timeout=5,
        )
        if not kline or not kline[0]:
            log.warning("RESOLVE SKIP %s: no BTC close data", pcid[:8])
            resolved.append(pcid)  # remove anyway to prevent infinite retry
            continue

        close_price = float(kline[0][4])  # close price at window end
        open_price = pinfo.get("open_price", 0)

        if open_price <= 0:
            log.warning("RESOLVE SKIP %s: no open price in pending", pcid[:8])
            resolved.append(pcid)
            continue

        # Determine outcome
        # Polymarket 5M: "Up if price >= open". Flat = UP.
        outcome = "UP" if close_price >= open_price else "DOWN"

        # Compute PnL based on mode
        mode = pinfo.get("mode", "?")
        direction = pinfo.get("direction", "?")
        pnl = 0.0

        if mode == "A":
            # Arb mode: PnL = matched_shares × (1.00 - combined)
            combined = pinfo.get("combined", 1.0)
            shares = pinfo.get("shares", 5)
            pnl = shares * (1.00 - combined)
            # ⚠️ RISK: assumes both sides filled. May not be true.

        elif mode == "B":
            # Taker directional: PnL depends on direction match
            price = pinfo.get("price", 0.50)
            shares = pinfo.get("shares", 5)
            if direction == outcome:
                pnl = shares * (1.00 - price)  # win: $1 per share - cost
            else:
                pnl = -shares * price           # lose: cost

        session.record_fill(pcid, pnl)
        resolved.append(pcid)

        _log_jsonl(_TRADE_LOG, {
            "ts": _now_hkt(), "event": "resolution",
            "cid": pcid[:8], "mode": mode,
            "direction": direction, "outcome": outcome,
            "pnl": round(pnl, 4),
            "session_pnl": round(session.pnl, 2),
            "open_price": open_price, "close_price": close_price,
        })

    for pcid in resolved:
        session.pending_orders.pop(pcid, None)
    if resolved:
        session.save()


# ═══════════════════════════════════════
#  Main Loop
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="5M Momentum Strategy")
    parser.add_argument("--live", action="store_true", help="Execute real orders")
    parser.add_argument("--dry-run", action="store_true", help="Paper trade (default)")
    parser.add_argument("--bet", type=float, default=BET_SIZE_USD, help="Bet size USD")
    args = parser.parse_args()

    dry_run = not args.live
    bet_size = args.bet

    log.info("=" * 60)
    log.info("  5M Momentum — Three-Tier Strategy")
    log.info("  Mode: %s | Bet: $%.2f | Entry: T+%ds | Cycle: %ds",
             "DRY-RUN" if dry_run else "LIVE", bet_size, ENTRY_DELAY_S, ENTRY_CYCLE_S)
    log.info("  SKIP < %dbps | ARB %d-%dbps (combined < $%.2f) | DIR >= %dbps (ask cap $%.2f)",
             SKIP_BELOW_BPS, SKIP_BELOW_BPS, ARB_UPPER_BPS, ARB_MAX_COMBINED,
             ARB_UPPER_BPS, TAKER_ASK_CAP)
    log.info("  Stop: -$%.0f session | %d consec losses | %d trades",
             SESSION_MAX_LOSS, MAX_CONSECUTIVE_LOSSES, EXPERIMENT_TRADE_LIMIT)
    log.info("=" * 60)

    # ── Init client ──
    client = PolymarketClient(dry_run=dry_run)
    if not dry_run:
        client.authenticate()
        balance = client.get_balance()
        log.info("Balance: $%.2f", balance)
        # ⚠️ RISK: no balance gate. live=True + --live = real money immediately.

    # ── Init Binance WS ──
    ws_binance = BinancePriceFeed()
    ws_binance.start()
    time.sleep(2)

    session = SessionState()
    session.load()
    session.last_scan_s = 0.0
    session.last_save_s = 0.0
    watchlist: dict[str, dict] = {}

    log.info("Main loop starting...")

    while _running:
        loop_start = time.time()
        try:
            _run_cycle(client, ws_binance, session, watchlist, bet_size, dry_run)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            log.error("LOOP ERROR: %s — continuing", e, exc_info=True)
            session.save()

        elapsed = time.time() - loop_start
        time.sleep(max(0.1, ENTRY_CYCLE_S - elapsed))

    # ── Shutdown ──
    ws_binance.stop()
    log.info("=" * 60)
    log.info("  Session complete: A=%d B=%d PnL=$%.2f",
             session.mode_a_count, session.mode_b_count, session.pnl)
    log.info("  Logs: %s", _TRADE_LOG)
    log.info("=" * 60)


def _run_cycle(client, ws_binance, session: SessionState,
               watchlist: dict, bet_size: float, dry_run: bool):
    """Single main-loop iteration. Extracted for top-level try/except."""
    stop_reason = session.should_stop()
    if stop_reason:
        log.info("SESSION STOP: %s | PnL=$%.2f | Trades=%d",
                 stop_reason, session.pnl, session.total_trades)
        global _running
        _running = False
        return

    now_s = time.time()
    now_ms = int(now_s * 1000)

    # ── Discovery ──
    if now_s - session.last_scan_s > SCAN_INTERVAL_S:
        windows = discover_windows()
        for w in windows:
            if w["cid"] not in watchlist and w["cid"] not in session.entered_cids:
                watchlist[w["cid"]] = w
                log.info("WATCH %s %s", w["coin"].upper(), w["slug"])
        session.last_scan_s = now_s

    # ── Process watchlist ──
    to_remove = []

    for cid, wl in watchlist.items():
        if cid in session.entered_cids:
            to_remove.append(cid)
            continue
        if now_ms > wl["end_ms"] + 60_000:
            to_remove.append(cid)
            continue

        elapsed_s = (now_ms - wl["start_ms"]) / 1000
        if elapsed_s < ENTRY_DELAY_S:
            continue
        if elapsed_s > WINDOW_DURATION_S * WINDOW_GIVE_UP_PCT:
            to_remove.append(cid)
            continue

        coin = wl["coin"]
        coin_cfg = COINS[coin]

        current_price = ws_binance.get_price(coin_cfg.symbol)
        if not current_price:
            continue

        # Open price (cached per window)
        if "open_price" not in wl:
            kline_data = _get_json(
                f"https://api.binance.com/api/v3/klines?"
                f"symbol={coin_cfg.symbol}&interval=1s"
                f"&startTime={wl['start_ms']}&limit=1",
                timeout=5,
            )
            if not kline_data or not kline_data[0]:
                continue
            wl["open_price"] = float(kline_data[0][1])

        open_price = wl["open_price"]

        # ── Signal ──
        sig = compute_signal(
            open_price=open_price,
            current_price=current_price,
            delay_s=int(elapsed_s),
            skip_below_bps=SKIP_BELOW_BPS,
            arb_upper_bps=ARB_UPPER_BPS,
        )

        _log_jsonl(_SIGNAL_LOG, {
            "ts": _now_hkt(), "cid": cid[:8], "coin": coin,
            "mode": sig.mode.value, "momentum_bps": sig.momentum_bps,
            "direction": sig.direction, "confidence": sig.confidence,
            "elapsed_s": round(elapsed_s, 1),
        })

        # ── SKIP ──
        if sig.mode == Mode.SKIP:
            skip_check = should_skip(
                abs(sig.momentum_bps), SKIP_BELOW_BPS,
                session_drawdown=abs(min(0, session.pnl)),
                session_stop_all=SESSION_MAX_LOSS,
            )
            if skip_check:
                log.info("SKIP %s %s: %s", coin.upper(), cid[:8], skip_check.reason)
            continue

        # ── Poly mids ──
        up_mid = get_poly_mid(wl["up_tok"])
        dn_mid = get_poly_mid(wl["dn_tok"])
        if up_mid is None or dn_mid is None:
            continue

        # ── MAKER_ARB (8-15bps) ──
        if sig.mode == Mode.MAKER_ARB:
            _execute_arb(client, session, sig, wl, up_mid, dn_mid,
                         bet_size, open_price, dry_run, to_remove)

        # ── TAKER_DIRECTIONAL (>= 15bps) ──
        elif sig.mode == Mode.TAKER_DIRECTIONAL:
            _execute_directional(client, session, sig, wl, up_mid, dn_mid,
                                 bet_size, open_price, dry_run, to_remove)

    # ── Cleanup ──
    for cid in to_remove:
        watchlist.pop(cid, None)

    # ── Resolution ──
    _check_resolution(session, now_ms)

    # ── Periodic save + status ──
    if now_s - session.last_save_s > SCAN_INTERVAL_S:
        session.save()
        session.last_save_s = now_s
    if int(now_s) % 60 < ENTRY_CYCLE_S:
        log.info("STATUS: A=%d B=%d PnL=$%.2f watch=%d pending=%d",
                 session.mode_a_count, session.mode_b_count,
                 session.pnl, len(watchlist), len(session.pending_orders))


def _execute_arb(client, session, sig, wl, up_mid, dn_mid,
                 bet_size, open_price, dry_run, to_remove):
    """Mode A: MAKER_ARB — both sides, guaranteed arb."""
    cid, coin = wl["cid"], wl["coin"]

    combined_estimate = (up_mid + dn_mid) - 2 * ARB_SPREAD_FROM_MID
    if combined_estimate >= ARB_MAX_COMBINED:
        log.info("ARB_SKIP %s %s: est $%.3f >= $%.2f",
                 coin.upper(), cid[:8], combined_estimate, ARB_MAX_COMBINED)
        return

    arb_sig = copy(sig)
    arb_sig.lean_ratio = 1.0

    plan = plan_maker_arb(
        signal=arb_sig, poly_mid_up=up_mid, poly_mid_down=dn_mid,
        bet_size_usd=bet_size, spread_from_mid=ARB_SPREAD_FROM_MID,
        max_combined=ARB_MAX_COMBINED, max_lean_price=ARB_MAX_PRICE,
        max_hedge_price=HEDGE_MAX_PRICE, min_shares=MIN_ORDER_SHARES,
    )
    if not plan:
        return

    log.info("MODE_A %s %s: %s@$%.2f×%d + %s@$%.2f×%d | comb=$%.3f",
             coin.upper(), cid[:8],
             plan.lean_side, plan.lean_price, plan.lean_shares,
             plan.hedge_side, plan.hedge_price, plan.hedge_shares,
             plan.combined_cost)

    up_tok, dn_tok = wl["up_tok"], wl["dn_tok"]
    lean_tok = up_tok if plan.lean_side == "UP" else dn_tok
    hedge_tok = dn_tok if plan.lean_side == "UP" else up_tok

    # ⚠️ RISK: mark BEFORE submit to prevent double-submit on crash
    session.entered_cids.add(cid)
    session.save()

    try:
        client.buy_shares(lean_tok,
                          round(plan.lean_shares * plan.lean_price, 2),
                          plan.lean_price)
    except Exception as e:
        log.warning("MODE_A lean FAIL %s: %s", cid[:8], e)
        # Don't count as trade — zero orders submitted, don't waste experiment slot
        to_remove.append(cid)
        return

    hedge_ok = False
    try:
        client.buy_shares(hedge_tok,
                          round(plan.hedge_shares * plan.hedge_price, 2),
                          plan.hedge_price)
        hedge_ok = True
    except Exception as e:
        log.warning("MODE_A hedge FAIL %s: %s → downgrade to mode B", cid[:8], e)

    session.mode_a_count += 1

    if hedge_ok:
        # Both sides submitted → track as arb
        session.pending_orders[cid] = {
            "mode": "A", "coin": coin, "direction": sig.direction,
            "combined": plan.combined_cost,
            "shares": min(plan.lean_shares, plan.hedge_shares),
            "open_price": open_price, "end_ms": wl["end_ms"],
        }
    else:
        # Only lean submitted → track as directional (correct PnL at resolution)
        session.pending_orders[cid] = {
            "mode": "B", "coin": coin, "direction": sig.direction,
            "price": plan.lean_price,
            "shares": plan.lean_shares,
            "open_price": open_price, "end_ms": wl["end_ms"],
        }
    session.save()
    to_remove.append(cid)

    _log_jsonl(_TRADE_LOG, {
        "ts": _now_hkt(), "event": "entry", "mode": "A",
        "coin": coin, "cid": cid[:8],
        "combined": plan.combined_cost, "arb_pnl": plan.expected_arb_pnl,
        "dry_run": dry_run,
    })


def _execute_directional(client, session, sig, wl, up_mid, dn_mid,
                         bet_size, open_price, dry_run, to_remove):
    """Mode B: TAKER_DIRECTIONAL — aggressive lean + optional hedge."""
    cid, coin = wl["cid"], wl["coin"]

    # ⚠️ RISK: directional cap = 1/3 of session cap
    if session.pnl <= -(SESSION_MAX_LOSS * 0.33):
        log.info("DIR_STOP %s: PnL $%.2f", coin.upper(), session.pnl)
        to_remove.append(cid)
        return
    if session.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
        log.info("DIR_STOP %s: %d consec losses", coin.upper(), session.consecutive_losses)
        to_remove.append(cid)
        return

    lean_mid = up_mid if sig.direction == "UP" else dn_mid

    plan = plan_taker_directional(
        signal=sig, poly_ask=lean_mid,  # pass MID — function adds buffer once
        bet_size_usd=bet_size, ask_buffer=TAKER_ASK_BUFFER,
        ask_cap=TAKER_ASK_CAP, min_shares=MIN_ORDER_SHARES,
    )
    if not plan:
        log.info("DIR_SKIP %s %s: mid $%.2f + buffer > cap $%.2f",
                 coin.upper(), cid[:8], lean_mid, TAKER_ASK_CAP)
        to_remove.append(cid)
        return

    log.info("MODE_B %s %s: %s @$%.2f×%d | mom=%+.1fbps",
             coin.upper(), cid[:8], plan.lean_side,
             plan.lean_price, plan.lean_shares, sig.momentum_bps)

    # ⚠️ RISK: mark BEFORE submit
    session.entered_cids.add(cid)
    session.save()

    lean_tok = wl["up_tok"] if sig.direction == "UP" else wl["dn_tok"]
    try:
        client.buy_shares(lean_tok,
                          round(plan.lean_shares * plan.lean_price, 2),
                          plan.lean_price)
    except Exception as e:
        log.warning("MODE_B lean FAIL %s: %s", cid[:8], e)
        # Don't count as trade — zero orders submitted
        to_remove.append(cid)
        return

    # Optional hedge
    if HEDGE_ENABLED:
        hedge_mid = dn_mid if sig.direction == "UP" else up_mid
        h = plan_hedge(signal=sig, poly_mid_hedge=hedge_mid,
                       spread_from_mid=HEDGE_SPREAD_FROM_MID,
                       max_price=HEDGE_MAX_PRICE, min_shares=MIN_ORDER_SHARES)
        if h:
            hedge_tok = wl["dn_tok"] if sig.direction == "UP" else wl["up_tok"]
            try:
                client.buy_shares(hedge_tok, round(h.shares * h.price, 2), h.price)
                log.info("  HEDGE %s @$%.2f×%d", h.side, h.price, h.shares)
            except Exception as e:
                log.warning("  HEDGE FAIL %s: %s", cid[:8], e)

    session.mode_b_count += 1
    session.pending_orders[cid] = {
        "mode": "B", "coin": coin, "direction": sig.direction,
        "price": plan.lean_price, "shares": plan.lean_shares,
        "open_price": open_price, "end_ms": wl["end_ms"],
    }
    session.save()
    to_remove.append(cid)

    _log_jsonl(_TRADE_LOG, {
        "ts": _now_hkt(), "event": "entry", "mode": "B",
        "coin": coin, "cid": cid[:8],
        "direction": sig.direction, "momentum_bps": sig.momentum_bps,
        "lean_price": plan.lean_price, "dry_run": dry_run,
    })


if __name__ == "__main__":
    main()
