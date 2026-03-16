#!/usr/bin/env python3
"""
liq_monitor.py — Liquidation event monitor daemon.

Polls HyperLiquid OI data every 60s, detects liquidation events via OI delta,
writes liq_state.json for trader_cycle pipeline consumption.

OI proxy logic:
- OI drops + price rises = shorts liquidated (bullish)
- OI drops + price drops  = longs liquidated (bearish)
- Estimated volume = abs(OI_delta) in USD

Standalone daemon — runs via LaunchAgent (ai.openclaw.liqmonitor).
Does NOT import trader_cycle pipeline (avoids circular deps).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

# ─── Setup paths ───
AXC_HOME = os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

from config.liq_params import (
    ON_LIQS_OI_DROP_PCT,
    ON_LIQS_THRESHOLD_USD,
    ON_LIQS_WINDOW_MIN,
    LIQ_POLL_INTERVAL_SEC,
    LIQ_COINS,
    LIQ_HISTORY_MAXLEN,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("liq_monitor")

SHARED_DIR = os.path.join(AXC_HOME, "shared")
LIQ_STATE_PATH = os.path.join(SHARED_DIR, "liq_state.json")


class LiqMonitor:
    """Monitors OI changes as a proxy for liquidation events."""

    def __init__(self, poll_interval: int = LIQ_POLL_INTERVAL_SEC):
        self._poll_interval = poll_interval
        self._coins = LIQ_COINS
        # Rolling window: list of (timestamp, {coin: oi_usd}, {coin: mid_price})
        self._history: deque = deque(maxlen=LIQ_HISTORY_MAXLEN)
        self._info = None  # lazy init SDK

    def _init_sdk(self):
        """Lazy-init HL Info SDK (read-only, no wallet needed)."""
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        self._info = Info(constants.MAINNET_API_URL, skip_ws=True)
        logger.info("HyperLiquid Info SDK initialized")

    def _fetch_oi_and_prices(self) -> tuple[dict[str, float], dict[str, float]]:
        """Fetch current OI and mid prices from HL.

        Returns (oi_by_coin, price_by_coin) both in USD.
        """
        if self._info is None:
            self._init_sdk()

        data = self._info.meta_and_asset_ctxs()
        meta = data[0]
        ctxs = data[1]
        universe = meta.get("universe", [])

        oi_map: dict[str, float] = {}
        price_map: dict[str, float] = {}

        for i, asset in enumerate(universe):
            coin = asset.get("name", "")
            if coin not in self._coins:
                continue
            if i < len(ctxs):
                ctx = ctxs[i]
                mark_px = float(ctx.get("markPx", 0))
                oi = float(ctx.get("openInterest", 0))
                oi_map[coin] = oi * mark_px
                price_map[coin] = mark_px

        return oi_map, price_map

    def poll_once(self) -> dict:
        """Single poll: fetch OI, compute deltas, detect events.

        Returns liq_state dict ready for JSON output.
        """
        now = time.time()
        oi_map, price_map = self._fetch_oi_and_prices()

        self._history.append((now, oi_map, price_map))

        # Compute deltas against window
        events = []
        oi_delta_10m: dict[str, float] = {}
        oi_delta_1h: dict[str, float] = {}

        window_10m = ON_LIQS_WINDOW_MIN * 60  # seconds

        for coin in self._coins:
            current_oi = oi_map.get(coin, 0)
            current_price = price_map.get(coin, 0)
            if current_oi <= 0:
                continue

            # Find oldest entry within 10-min window
            ref_oi_10m = None
            ref_price_10m = None
            ref_oi_1h = None

            for ts, hist_oi, hist_price in self._history:
                age = now - ts
                if age <= window_10m and ref_oi_10m is None:
                    ref_oi_10m = hist_oi.get(coin)
                    ref_price_10m = hist_price.get(coin)
                if ref_oi_1h is None:
                    # Oldest entry = best 1h proxy we have
                    ref_oi_1h = hist_oi.get(coin)

            # 10-min OI delta
            if ref_oi_10m and ref_oi_10m > 0:
                delta_pct = (current_oi - ref_oi_10m) / ref_oi_10m * 100
                oi_delta_10m[coin] = round(delta_pct, 4)

                # Detect liquidation event
                if delta_pct < -ON_LIQS_OI_DROP_PCT:
                    est_volume = abs(current_oi - ref_oi_10m)
                    price_delta = 0.0
                    if ref_price_10m and ref_price_10m > 0:
                        price_delta = (current_price - ref_price_10m) / ref_price_10m * 100

                    # Determine direction
                    if price_delta > 0:
                        direction = "SHORT_LIQS"  # shorts got liquidated → price up
                    else:
                        direction = "LONG_LIQS"   # longs got liquidated → price down

                    if est_volume >= ON_LIQS_THRESHOLD_USD:
                        events.append({
                            "coin": coin,
                            "direction": direction,
                            "oi_delta_pct": round(delta_pct, 4),
                            "price_delta_pct": round(price_delta, 4),
                            "estimated_volume_usd": round(est_volume, 2),
                            "timestamp": now,
                            "trigger_mode": "on_liqs",
                        })
                        logger.info(
                            f"LIQ EVENT: {coin} {direction} "
                            f"OI={delta_pct:+.2f}% price={price_delta:+.2f}% "
                            f"vol=${est_volume:,.0f}"
                        )

            # 1h OI delta (best effort with available history)
            if ref_oi_1h and ref_oi_1h > 0:
                delta_1h = (current_oi - ref_oi_1h) / ref_oi_1h * 100
                oi_delta_1h[coin] = round(delta_1h, 4)

        state = {
            "timestamp": now,
            "events": events,
            "oi_by_coin": {k: round(v, 2) for k, v in oi_map.items()},
            "oi_delta_10m": oi_delta_10m,
            "oi_delta_1h": oi_delta_1h,
        }
        return state

    def _write_state(self, state: dict) -> None:
        """Atomic write to liq_state.json."""
        os.makedirs(SHARED_DIR, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=SHARED_DIR, suffix=".liq.tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, LIQ_STATE_PATH)
        except OSError as e:
            logger.error(f"State write error: {e}")

    def run(self) -> None:
        """Main loop: poll → write state → sleep."""
        logger.info(
            f"Liq monitor started — coins={self._coins} "
            f"interval={self._poll_interval}s"
        )
        while True:
            try:
                state = self.poll_once()
                self._write_state(state)
                event_count = len(state.get("events", []))
                if event_count > 0:
                    logger.info(f"Poll complete: {event_count} events detected")
                else:
                    logger.debug("Poll complete: no events")
            except KeyboardInterrupt:
                logger.info("Shutting down")
                break
            except Exception as e:
                logger.error(f"Poll error: {e}", exc_info=True)

            time.sleep(self._poll_interval)


if __name__ == "__main__":
    monitor = LiqMonitor()
    monitor.run()
