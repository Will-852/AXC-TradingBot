"""
liq_signal.py — LiqSignalStep: read liq_state.json and boost/generate signals.

Pipeline step inserted after ReadSentimentStep, before DetectModeStep.
Reads the state file written by the standalone liq_monitor.py daemon.

Phase 1 (current): boost existing signals when liq events align
Phase 2 (future): generate independent signals from whale position data
"""

from __future__ import annotations

import json
import logging
import os
import time

from ..core.context import CycleContext
from ..config.settings import LIQ_STATE_PATH, LIQ_MONITOR_ENABLED

logger = logging.getLogger(__name__)

# Import thresholds from config (not settings.py — these are tunable)
try:
    import sys
    from pathlib import Path
    _base = os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
    if _base not in sys.path:
        sys.path.insert(0, _base)
    from config.liq_params import (
        ON_LIQS_SIGNAL_BOOST,
        LIQ_STATE_MAX_AGE_SEC,
    )
except ImportError:
    ON_LIQS_SIGNAL_BOOST = 1.0
    LIQ_STATE_MAX_AGE_SEC = 180


class LiqSignalStep:
    """Step 4.6: Read liquidation state and prepare signal boosts.

    Runs after ReadSentimentStep (4.5), before DetectModeStep (5).
    Stores liq events in ctx for EvaluateSignalsStep to apply boosts.
    """
    name = "liq_signal"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not LIQ_MONITOR_ENABLED:
            return ctx

        # Read liq_state.json
        state = self._read_state()
        if not state:
            if ctx.verbose:
                print("    LIQ: no state file or empty")
            return ctx

        # Check staleness
        state_ts = state.get("timestamp", 0)
        age = time.time() - state_ts
        if age > LIQ_STATE_MAX_AGE_SEC:
            if ctx.verbose:
                print(f"    LIQ: state stale ({age:.0f}s > {LIQ_STATE_MAX_AGE_SEC}s)")
            return ctx

        # Store in context for downstream steps
        ctx.liq_state = state
        ctx.liq_events = state.get("events", [])

        if ctx.verbose:
            oi_10m = state.get("oi_delta_10m", {})
            events = ctx.liq_events
            print(f"    LIQ: {len(events)} events | OI Δ10m: {oi_10m}")
            for e in events:
                print(
                    f"      {e['coin']} {e['direction']} "
                    f"OI={e['oi_delta_pct']:+.2f}% "
                    f"vol=${e['estimated_volume_usd']:,.0f}"
                )

        return ctx

    def _read_state(self) -> dict | None:
        """Read liq_state.json, return None on any error."""
        if not os.path.exists(LIQ_STATE_PATH):
            return None
        try:
            with open(LIQ_STATE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"liq_state.json read error: {e}")
            return None


def apply_liq_boost(signal, liq_events: list[dict]) -> float:
    """Apply liquidation-based score boost to a signal.

    Called by EvaluateSignalsStep after scoring.

    Returns boost amount (0.0 if no applicable event).

    Logic:
    - SHORT_LIQS (shorts liquidated, price rising) → boost LONG signals
    - LONG_LIQS (longs liquidated, price falling) → boost SHORT signals
    """
    if not liq_events:
        return 0.0

    # Map signal pair to coin: "BTCUSDT" → "BTC"
    coin = signal.pair.replace("USDT", "").replace("USDC", "")

    for event in liq_events:
        if event.get("coin") != coin:
            continue

        direction = event.get("direction", "")

        # SHORT_LIQS = shorts got rekt = bullish → boost LONG
        if direction == "SHORT_LIQS" and signal.direction == "LONG":
            logger.info(
                f"[LIQ BOOST] {signal.pair} LONG +{ON_LIQS_SIGNAL_BOOST} "
                f"(SHORT_LIQS vol=${event.get('estimated_volume_usd', 0):,.0f})"
            )
            return ON_LIQS_SIGNAL_BOOST

        # LONG_LIQS = longs got rekt = bearish → boost SHORT
        if direction == "LONG_LIQS" and signal.direction == "SHORT":
            logger.info(
                f"[LIQ BOOST] {signal.pair} SHORT +{ON_LIQS_SIGNAL_BOOST} "
                f"(LONG_LIQS vol=${event.get('estimated_volume_usd', 0):,.0f})"
            )
            return ON_LIQS_SIGNAL_BOOST

    return 0.0
