"""
conv_1h/analysis.py — Offline analysis data collection for 1H Conviction bot.

Split from run_1h_live.py (2026-03-26).
🟢 SAFE: Pure data collection to jsonl. Zero trading impact. All errors silently caught.

Collects Polymarket-native data (trades, holders, OI, price history) for offline analysis.
Burst mode: 15s interval in last 3 min of window (whale exits cluster near resolution).
"""

import json
import logging
import os
import time

from polymarket.conv_1h.constants import (
    _ANALYSIS_BURST_MIN, _ANALYSIS_BURST_S, _ANALYSIS_INTERVAL_S,
    _ANALYSIS_TAPE, _DATA_API,
)
from polymarket.conv_1h.data_feeds import _get_json

logger = logging.getLogger(__name__)

# ─── Module-level mutable state ───
_last_analysis = 0.0


def _collect_analysis(cached_markets: list):
    """Collect Polymarket-native data for offline analysis. Zero impact on trading.
    Writes to analysis_1h.jsonl. All errors silently caught.
    Burst mode: 15s interval in last 4 min (whale exits cluster near resolution)."""
    global _last_analysis
    now = time.time()
    now_ms = int(now * 1000)

    # Adaptive interval: burst in last 3 min of any active window
    interval = _ANALYSIS_INTERVAL_S
    try:
        for _m in cached_markets:
            if _m.get("start_ms", 0) < now_ms < _m.get("end_ms", 0):
                if (now_ms - _m["start_ms"]) / 60_000 >= _ANALYSIS_BURST_MIN:
                    interval = _ANALYSIS_BURST_S
                    break
    except Exception:
        pass

    if now - _last_analysis < interval:
        return
    _last_analysis = now

    for mkt in cached_markets:
        cid = mkt["cid"]
        start_ms = mkt["start_ms"]
        end_ms = mkt["end_ms"]
        if now_ms < start_ms or now_ms > end_ms:
            continue
        coin = mkt["coin"]
        try:
            record = {"ts": time.time(), "coin": coin, "cid": cid[:12]}

            # #3: Token price history within this window (uses UP token_id, not condition_id)
            _up_tok = mkt.get("up_tok", "")
            ph = _get_json(
                f"https://clob.polymarket.com/prices-history"
                f"?market={_up_tok}&interval=1h&fidelity=1",
                timeout=2)
            if ph and isinstance(ph, dict) and "history" in ph:
                hist = ph["history"]
                record["price_hist_len"] = len(hist)
                if hist:
                    prices = [float(h.get("p", 0)) for h in hist if h.get("p")]
                    if prices:
                        record["price_first"] = prices[0]
                        record["price_last"] = prices[-1]
                        record["price_min"] = min(prices)
                        record["price_max"] = max(prices)
                        record["price_range"] = round(max(prices) - min(prices), 4)

            # #1: Recent trades (last 50) — who's trading and which direction
            trades = _get_json(
                f"{_DATA_API}/trades?market={cid}&limit=50",
                timeout=2)
            if trades and isinstance(trades, list):
                buys = sum(1 for t in trades if t.get("side", "").upper() == "BUY")
                sells = len(trades) - buys
                total_size = sum(float(t.get("size", 0)) for t in trades)
                record["trades_count"] = len(trades)
                record["trades_buys"] = buys
                record["trades_sells"] = sells
                record["trades_total_size"] = round(total_size, 2)
                record["trades_buy_ratio"] = round(buys / len(trades), 3) if trades else 0

            # #2: Top holders per side — smart money flow indicator
            holders_raw = _get_json(
                f"{_DATA_API}/holders?market={cid}&limit=10&minBalance=10",
                timeout=2)
            if holders_raw and isinstance(holders_raw, list):
                for token_group in holders_raw:
                    hs = token_group.get("holders", []) if isinstance(token_group, dict) else []
                    if not hs:
                        continue
                    idx = hs[0].get("outcomeIndex", -1) if hs else -1
                    side = "up" if idx == 0 else "down" if idx == 1 else "unk"
                    amounts = [float(h.get("amount", 0)) for h in hs]
                    record[f"holders_{side}_count"] = len(hs)
                    record[f"holders_{side}_total"] = round(sum(amounts), 2)
                    record[f"holders_{side}_top3"] = [
                        {"wallet": h.get("proxyWallet", "")[:12],
                         "name": h.get("name", "")[:20],
                         "amt": round(float(h.get("amount", 0)), 1)}
                        for h in hs[:3]
                    ]

            # #4: Open interest — total money in market
            oi = _get_json(f"{_DATA_API}/oi?market={cid}", timeout=2)
            if oi and isinstance(oi, dict):
                record["oi"] = round(float(oi.get("value", 0)), 2)

            # Metadata for delta analysis
            t_el = (now_ms - start_ms) / 60_000
            record["t_elapsed"] = round(t_el, 1)
            record["burst"] = t_el >= _ANALYSIS_BURST_MIN

            # Write
            os.makedirs(os.path.dirname(_ANALYSIS_TAPE), exist_ok=True)
            with open(_ANALYSIS_TAPE, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")

        except Exception as e:
            logger.debug("Analysis collect %s: %s", coin, e)
