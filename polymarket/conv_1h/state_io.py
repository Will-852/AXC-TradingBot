"""
conv_1h/state_io.py — State persistence and logging for 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
⚠️ DOWNSTREAM D1: If shared bot_state.py is built later, update imports here.
⚠️ DOWNSTREAM D5: If MMMarketState adds fields, update _to_dict field list.
"""

import json
import logging
import os
import tempfile
import time
import urllib.request
from datetime import datetime

from polymarket.conv_1h.constants import (
    _FILL_STATS_DEFAULT, _HKT, _LOG_DIR, _ORDER_LOG, _SIGNAL_TAPE_1H,
    _STATE_PATH, _TRADE_LOG, _TG_CHAT_ID, _TG_NEWS_TOKEN,
)

logger = logging.getLogger(__name__)


def bump_fill(state: dict, event: str, n: int = 1):
    fs = state.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
    fs[event] = fs.get(event, 0) + n


def load() -> dict:
    if not os.path.exists(_STATE_PATH):
        return _default_state()
    try:
        with open(_STATE_PATH) as f:
            d = json.load(f)
        d.setdefault("fill_stats", dict(_FILL_STATS_DEFAULT))
        return d
    except Exception:
        return _default_state()


def _default_state() -> dict:
    return {"markets": {}, "watchlist": {}, "daily_pnl": 0.0,
            "total_pnl": 0.0, "total_markets": 0, "bankroll": 100.0,
            "consecutive_losses": 0, "cooldown_until": "",
            "daily_pnl_date": "", "fill_stats": dict(_FILL_STATS_DEFAULT)}


def save(state: dict):
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


def to_dict(s) -> dict:
    from polymarket.strategy.market_maker import MMMarketState
    return {k: getattr(s, k) for k in [
        "condition_id", "title", "up_token_id", "down_token_id",
        "window_start_ms", "window_end_ms", "btc_open_price", "phase",
        "up_shares", "up_avg_price", "down_shares", "down_avg_price",
        "entry_cost", "payout", "realized_pnl"]}


def from_dict(d: dict):
    from polymarket.strategy.market_maker import MMMarketState
    s = MMMarketState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def log_trade(record: dict):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRADE_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def log_order(event: str, order_id: str, cid: str, **kwargs):
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


def tg_alert(msg: str):
    """Send alert via @AXCnews_bot Telegram."""
    if not _TG_NEWS_TOKEN or not _TG_CHAT_ID:
        logger.warning("TG alert skipped: no credentials")
        return
    try:
        data = json.dumps({"chat_id": _TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_TG_NEWS_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("TG alert failed: %s", e)


def record_signal_tape(coin: str, cid: str, up_tok: str, dn_tok: str,
                       start_ms: int, end_ms: int, t_elapsed: float,
                       spot_price: float, btc_open: float, vol_1m: float,
                       sig, vol_dir: str | None, h_imbal: float = 0,
                       poly_midpoint_fn=None):
    """Append one snapshot to signal_tape_1h.jsonl. Called every heavy cycle per market."""
    if poly_midpoint_fn is None:
        up_mid, dn_mid = 0, 0
    else:
        up_mid = poly_midpoint_fn(up_tok)
        dn_mid = poly_midpoint_fn(dn_tok)
    record = {
        "ts": time.time(),
        "coin": coin,
        "cid": cid[:16],
        "start_ms": start_ms,
        "end_ms": end_ms,
        "t_elapsed": round(t_elapsed, 1),
        "up_mid": round(up_mid, 4) if up_mid else None,
        "dn_mid": round(dn_mid, 4) if dn_mid else None,
        "spot": round(spot_price, 2),
        "open": round(btc_open, 2),
        "vol_1m": round(vol_1m, 6),
        "fair_up": round(sig.fair_up, 4) if sig else None,
        "conviction": round(sig.conviction, 3) if sig else None,
        "confidence": round(sig.confidence, 3) if sig else None,
        "direction": sig.direction if sig else None,
        "action": sig.action if sig else None,
        "entry_price": sig.entry_price if sig else None,
        "vol_dir": vol_dir,
        "h_imbal": round(h_imbal, 3),
    }
    try:
        with open(_SIGNAL_TAPE_1H, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass
