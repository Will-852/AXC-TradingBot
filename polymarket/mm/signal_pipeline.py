"""
mm/signal_pipeline.py — W4 signal computation for MM 15M bot.

Split from run_mm_live.py (2026-03-25).
🟡 VERIFY: BPS calculation formula must be exact.
"""

import math
import time

from polymarket.mm.constants import _W4_DELAY_S, _W4_RATIO_BY_TIER, _W4_THRESHOLD_BPS
from polymarket.mm.data_feeds import open_at, price


def w4_dynamic_ratio(mag_bps: float) -> float:
    """Returns R=1.0 for all tiers (v4 pure arb mode).
    Dynamic tiers preserved in _W4_RATIO_BY_TIER for future re-enable.
    """
    return 1.0  # v4: pure arb, no lean


def w4_signal(window_start_ms: int, symbol: str = "BTCUSDT",
              ws_binance=None) -> tuple:
    """W4 momentum signal: log return from window open to now.
    Returns (direction, magnitude_bps, log_return).
    direction: 'UP', 'DOWN', 'WAIT' (too early), 'SKIP' (below threshold).

    🔴 BPS formula: abs(log(p_now/p0)) * 10000 — DO NOT change.
    """
    now_ms = int(time.time() * 1000)
    elapsed_s = (now_ms - window_start_ms) / 1000
    if elapsed_s < _W4_DELAY_S:
        return "WAIT", 0.0, 0.0
    p0 = open_at(window_start_ms, symbol)
    if p0 <= 0:
        return "SKIP", 0.0, 0.0
    p_now = price(symbol, ws_binance=ws_binance)
    if p_now <= 0:
        return "SKIP", 0.0, 0.0
    log_ret = math.log(p_now / p0)
    mag_bps = abs(log_ret) * 10000
    if mag_bps < _W4_THRESHOLD_BPS:
        return "SKIP", mag_bps, log_ret
    direction = "UP" if log_ret > 0 else "DOWN"
    return direction, mag_bps, log_ret
