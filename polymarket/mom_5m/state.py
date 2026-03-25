"""
mom_5m/state.py — State persistence, logging, kill switches, and resolution for 5M bot.

Split from run_5m_live.py (2026-03-26).
🟡 VERIFY: State mutations affect trading decisions.
_check_resolutions settles PnL and updates bankroll.
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta

from polymarket.mom_5m.config import (
    COIN_CONFIG, _FILL_STATS_DEFAULT, _HKT, _LOG_DIR,
    _ORDER_LOG, _RESOLUTION_DELAY_MS, _STATE_PATH, _TRADE_LOG, _W4_LOG,
    tg_alert,
)
from polymarket.mom_5m.data import _get_json
from polymarket.strategy.market_maker import MMMarketState, resolve_market

logger = logging.getLogger(__name__)


# ─── Fill stats ───

def bump_fill(state: dict, event: str, n: int = 1):
    """Increment fill rate counter."""
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


# ─── State persistence ───

def _default_state() -> dict:
    return {
        "markets": {}, "watchlist": {},
        "daily_pnl": 0.0, "total_pnl": 0.0,
        "total_markets": 0, "bankroll": 100.0,
        "consecutive_losses": 0, "cooldown_until": "",
        "daily_pnl_date": "",
        "fill_stats": dict(_FILL_STATS_DEFAULT),
    }


def load() -> dict:
    """Load state from disk. Returns default if missing/corrupt."""
    if not os.path.exists(_STATE_PATH):
        return _default_state()
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        d.setdefault("watchlist", {})
        return d
    except Exception:
        return _default_state()


def save(state: dict):
    """Atomic write state to disk."""
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_STATE_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ─── Serialization ───

def to_dict(s: MMMarketState) -> dict:
    """Serialize MMMarketState to dict."""
    return {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}


def from_dict(d: dict) -> MMMarketState:
    """Deserialize dict to MMMarketState."""
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


# ─── Logging ───

def log_trade(record: dict):
    """Append to trade log."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRADE_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def log_order(event: str, order_id: str, cid: str, **kwargs):
    """Per-order lifecycle log: submit/fill/cancel/expired."""
    record = {
        "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
        "event": event,
        "order_id": order_id[:16] if order_id else "",
        "cid": cid[:8] if cid else "",
    }
    record.update(kwargs)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_ORDER_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


# ─── Kill Switches ───

def check_kill_switches(state: dict) -> bool:
    """Check daily loss + cooldown. Returns True if should stop trading."""
    now_hkt = datetime.now(tz=_HKT)

    # Cooldown check
    cooldown = state.get("cooldown_until", "")
    if cooldown:
        try:
            if now_hkt.isoformat(timespec="seconds") < cooldown:
                return True
        except ValueError:
            pass
        # Cooldown expired
        state["cooldown_until"] = ""
        state["consecutive_losses"] = 0
        logger.info("Cooldown expired, resuming trading")

    # Daily loss cap: 15% of bankroll
    daily_loss_pct = abs(state["daily_pnl"]) / max(state["bankroll"], 1) \
        if state["daily_pnl"] < 0 else 0
    if daily_loss_pct > 0.15:
        logger.warning("DAILY LOSS %.1f%% > 15%% -> STOP", daily_loss_pct * 100)
        return True

    return False


# ─── Resolution (Binance 5m OHLC) ───

def check_resolutions(state: dict):
    """Resolve markets using Binance 5m kline data.

    Resolution: close >= open = UP, close < open = DOWN.
    Wait 60s after window end for Binance data to stabilize.
    """
    now_ms = int(time.time() * 1000)

    for cid, md in list(state["markets"].items()):
        if md.get("phase") == "RESOLVED":
            continue
        end_ms = md.get("window_end_ms", 0)
        if end_ms <= 0 or now_ms < end_ms + _RESOLUTION_DELAY_MS:
            continue
        start_ms = md.get("window_start_ms", 0)
        if start_ms <= 0:
            continue

        # Resolution symbol: use stored coin key (not title parsing)
        _res_coin = md.get("coin", "btc")
        _res_cfg = COIN_CONFIG.get(_res_coin)
        sym = _res_cfg.symbol if _res_cfg else "BTCUSDT"

        # Fetch Binance 5m candle at window start
        from polymarket.mom_5m.config import _BINANCE
        data = _get_json(
            f"{_BINANCE}/klines?symbol={sym}&interval=5m"
            f"&startTime={start_ms}&limit=1")
        if not data or not isinstance(data, list) or not data:
            continue

        btc_o = float(data[0][1])   # open
        btc_c = float(data[0][4])   # close
        result = "UP" if btc_c >= btc_o else "DOWN"

        ms = from_dict(md)
        pnl = resolve_market(ms, result)
        resolved_dict = to_dict(ms)

        # Preserve runtime keys
        for _rk in ("pending_orders", "fills_confirmed", "w4_lean_dir",
                     "w4_combined", "w4_mag_bps"):
            if _rk in md:
                resolved_dict[_rk] = md[_rk]
        state["markets"][cid] = resolved_dict

        # Only count PnL for live coins (paper trades don't affect bankroll)
        _res_coin = md.get("coin", "?")
        _res_cfg = COIN_CONFIG.get(_res_coin)
        _is_live_coin = _res_cfg and _res_cfg.live

        if _is_live_coin:
            state["daily_pnl"] += pnl
            state["total_pnl"] += pnl
        state["total_markets"] = state.get("total_markets", 0) + 1

        # Consecutive loss tracking (live only)
        if _is_live_coin:
            if pnl < 0:
                state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                if state["consecutive_losses"] >= 8:
                    cd = (datetime.now(tz=_HKT) + timedelta(hours=4)).isoformat(timespec="seconds")
                    state["cooldown_until"] = cd
                    logger.warning("8 consecutive losses -> COOLDOWN until %s", cd)
                    tg_alert(f"<b>5M BOT</b> 8 consecutive losses -> cooldown 4h")
            else:
                state["consecutive_losses"] = 0

        # Both-sides resolution log
        _bs_res = {
            "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
            "event": "resolution",
            "cid": cid[:8], "coin": md.get("coin", "?"),
            "result": result, "pnl": round(pnl, 4),
            "up_shares": md.get("up_shares", 0),
            "down_shares": md.get("down_shares", 0),
            "combined": md.get("w4_combined", 0),
            "lean_dir": md.get("w4_lean_dir", "?"),
            "cost": round(ms.total_cost, 2),
            "payout": round(ms.payout, 2),
            "both_filled": md.get("up_shares", 0) > 0 and md.get("down_shares", 0) > 0,
        }
        try:
            with open(_W4_LOG, "a") as f:
                f.write(json.dumps(_bs_res) + "\n")
        except Exception:
            pass

        log_trade({
            "ts": datetime.now(tz=_HKT).isoformat(timespec="seconds"),
            "cid": cid, "coin": md.get("coin", "?"),
            "result": result, "pnl": round(pnl, 4),
            "cost": round(ms.total_cost, 2), "payout": round(ms.payout, 2),
            "total_pnl": round(state["total_pnl"], 2),
            "lean_dir": md.get("w4_lean_dir", "?"),
            "combined": md.get("w4_combined", 0),
        })

        d = "^" if result == "UP" else "v"
        print(f"  RESOLVED {cid[:8]} {md.get('coin', '?')} {d} | "
              f"PnL ${pnl:+.2f} | Total ${state['total_pnl']:.2f}")

    # ── Cleanup: remove RESOLVED markets older than 1h to prevent unbounded growth ──
    _cleanup_cutoff = now_ms - 3600_000  # 1 hour ago
    stale = [cid for cid, md in state["markets"].items()
             if md.get("phase") == "RESOLVED"
             and md.get("window_end_ms", 0) < _cleanup_cutoff]
    for cid in stale:
        del state["markets"][cid]
    if stale:
        logger.debug("Cleaned up %d resolved markets", len(stale))
