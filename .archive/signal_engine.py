#!/usr/bin/env python3
"""
signal_engine.py — Fast-poll signal engine for real-time trading decisions

Polls WS caches every 100ms, computes signals, fires callbacks when conditions met.
Designed for the 5M arb bot but usable by any bot.

Architecture:
  WS feeds (Phase 1-3) -> cached data -> SignalEngine (100ms poll) -> callbacks -> order queue

Design decision: 100ms polling loop reading from existing WS caches rather than
modifying WS feeds to add callbacks. Keeps Phase 1-4 code untouched while
achieving <200ms reaction time.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Callable

logger = logging.getLogger(__name__)

_norm = NormalDist()


# ═══════════════════════════════════════
#  Bridge Helper (standalone)
# ═══════════════════════════════════════

def _bridge_fair(btc_current: float, btc_open: float,
                 vol_1m: float, minutes_remaining: float) -> float:
    """Brownian Bridge P(UP). Same logic as market_maker.py compute_fair_up but standalone.

    Uses Normal CDF (lightweight). The full Student-t correction in market_maker.py
    is for precision pricing; here we only need conviction threshold detection.
    """
    if vol_1m <= 0 or btc_current <= 0 or btc_open <= 0 or minutes_remaining <= 0:
        return 0.5
    sigma = vol_1m * math.sqrt(max(0.1, minutes_remaining))
    if sigma < 1e-10:
        return 0.995 if btc_current >= btc_open else 0.005
    z = math.log(btc_current / btc_open) / sigma
    return max(0.005, min(0.995, _norm.cdf(z)))


# ═══════════════════════════════════════
#  Watch Entry
# ═══════════════════════════════════════

@dataclass
class WatchEntry:
    """A registered watch — describes what to monitor and how to react."""
    cid: str                    # condition_id (market identifier)
    type: str                   # "ARB" | "DIRECTION" | "EXIT"
    callback: Callable | None = None

    # Token IDs
    up_token: str = ""
    dn_token: str = ""
    token_id: str = ""          # for EXIT watches (single token)

    # ARB params
    max_combined: float = 0.96

    # DIRECTION params
    coin_symbol: str = ""       # e.g. "BTCUSDT"
    coin_open: float = 0.0
    vol_1m: float = 0.0
    mins_remaining: float = 0.0
    min_conviction: float = 0.30

    # EXIT params
    direction: str = ""         # "UP" or "DOWN"
    entry_price: float = 0.0
    tp_mult: float = 1.3
    sl_pct: float = 0.25

    # Cooldown — prevent trigger spam
    cooldown_ms: float = 5000.0
    last_triggered: float = 0.0


# ═══════════════════════════════════════
#  Signal Engine
# ═══════════════════════════════════════

class SignalEngine:
    """Fast-poll signal engine — monitors WS caches, fires callbacks on conditions.

    Usage:
        engine = SignalEngine(binance_feed, poly_feed)
        engine.watch_arb(cid, up_tok, dn_tok, callback=my_handler)
        engine.start()
        ...
        engine.stop()
    """

    def __init__(self, binance_feed, poly_feed,
                 user_feed=None, poll_interval_ms: int = 100):
        """
        Args:
            binance_feed: BinancePriceFeed instance (get_price)
            poly_feed: PolymarketBookFeed instance (get_midpoint, get_ob_imbalance)
            user_feed: PolymarketUserFeed instance (optional, for future use)
            poll_interval_ms: poll interval in milliseconds (default 100)
        """
        self._binance_feed = binance_feed
        self._poly_feed = poly_feed
        self._user_feed = user_feed
        self._poll_interval = poll_interval_ms / 1000.0  # convert to seconds

        # {condition_id: WatchEntry}
        self._watches: dict[str, list[WatchEntry]] = {}
        self._watch_lock = threading.Lock()

        # Event log (bounded)
        self._events: list[dict] = []
        self._event_lock = threading.Lock()

        # Poll loop stats
        self._poll_count: int = 0
        self._total_poll_time: float = 0.0
        self._fires_count: int = 0

        # Thread control
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Watch Registration ────────────────────────

    def watch_arb(self, condition_id: str, up_token: str, dn_token: str,
                  max_combined: float = 0.96, cooldown_ms: float = 5000.0,
                  callback: Callable | None = None) -> None:
        """Watch for arb opportunity: UP_mid + DOWN_mid < max_combined."""
        entry = WatchEntry(
            cid=condition_id, type="ARB",
            up_token=up_token, dn_token=dn_token,
            max_combined=max_combined,
            cooldown_ms=cooldown_ms, callback=callback,
        )
        self._add_watch(condition_id, entry)
        logger.info("SignalEngine: watch_arb cid=%s max_combined=%.3f",
                     condition_id[:16], max_combined)

    def watch_direction(self, condition_id: str, up_token: str, dn_token: str,
                        coin_symbol: str, coin_open: float, vol_1m: float,
                        mins_remaining: float = 15.0,
                        min_conviction: float = 0.30, cooldown_ms: float = 5000.0,
                        callback: Callable | None = None) -> None:
        """Watch for directional entry: bridge conviction crosses threshold."""
        entry = WatchEntry(
            cid=condition_id, type="DIRECTION",
            up_token=up_token, dn_token=dn_token,
            coin_symbol=coin_symbol, coin_open=coin_open,
            vol_1m=vol_1m, mins_remaining=mins_remaining,
            min_conviction=min_conviction,
            cooldown_ms=cooldown_ms, callback=callback,
        )
        self._add_watch(condition_id, entry)
        logger.info("SignalEngine: watch_direction cid=%s coin=%s min_conv=%.2f",
                     condition_id[:16], coin_symbol, min_conviction)

    def watch_exit(self, condition_id: str, token_id: str, direction: str,
                   entry_price: float, tp_mult: float = 1.3, sl_pct: float = 0.25,
                   cooldown_ms: float = 5000.0,
                   callback: Callable | None = None) -> None:
        """Watch for exit: mid >= entry*tp_mult OR mid <= entry*(1-sl_pct)."""
        entry = WatchEntry(
            cid=condition_id, type="EXIT",
            token_id=token_id, direction=direction,
            entry_price=entry_price, tp_mult=tp_mult, sl_pct=sl_pct,
            cooldown_ms=cooldown_ms, callback=callback,
        )
        self._add_watch(condition_id, entry)
        logger.info("SignalEngine: watch_exit cid=%s dir=%s entry=%.3f tp=%.2fx sl=%.0f%%",
                     condition_id[:16], direction, entry_price, tp_mult, sl_pct * 100)

    def unwatch(self, condition_id: str) -> None:
        """Remove all watches for a market."""
        with self._watch_lock:
            removed = self._watches.pop(condition_id, [])
        if removed:
            logger.info("SignalEngine: unwatch cid=%s (%d watches removed)",
                         condition_id[:16], len(removed))

    def update_direction_params(self, condition_id: str,
                                coin_open: float | None = None,
                                vol_1m: float | None = None,
                                mins_remaining: float | None = None) -> None:
        """Update dynamic params for DIRECTION watches (e.g. new candle open, updated vol)."""
        with self._watch_lock:
            watches = self._watches.get(condition_id, [])
            for w in watches:
                if w.type != "DIRECTION":
                    continue
                if coin_open is not None:
                    w.coin_open = coin_open
                if vol_1m is not None:
                    w.vol_1m = vol_1m
                if mins_remaining is not None:
                    w.mins_remaining = mins_remaining

    def _add_watch(self, condition_id: str, entry: WatchEntry) -> None:
        with self._watch_lock:
            if condition_id not in self._watches:
                self._watches[condition_id] = []
            self._watches[condition_id].append(entry)

    # ── Start / Stop ──────────────────────────────

    def start(self) -> None:
        """Start the poll loop in a daemon thread. Non-blocking."""
        if self._thread and self._thread.is_alive():
            logger.warning("SignalEngine already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="signal-engine", daemon=True
        )
        self._thread.start()
        logger.info("SignalEngine started (poll_interval=%dms)", int(self._poll_interval * 1000))

    def stop(self) -> None:
        """Stop the poll loop. Non-blocking."""
        self._stop.set()
        logger.info("SignalEngine stop requested")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Main Poll Loop ────────────────────────────

    def _poll_loop(self) -> None:
        """Main poll loop — check all watches every poll_interval."""
        logger.info("SignalEngine poll loop started")
        while not self._stop.is_set():
            start = time.monotonic()
            now = time.time()

            with self._watch_lock:
                # Snapshot watch list to avoid holding lock during evaluation
                all_watches = []
                for watch_list in self._watches.values():
                    all_watches.extend(watch_list)

            for watch in all_watches:
                # Cooldown check
                if watch.last_triggered > 0:
                    elapsed_ms = (now - watch.last_triggered) * 1000
                    if elapsed_ms < watch.cooldown_ms:
                        continue

                try:
                    self._evaluate(watch, now)
                except Exception as exc:
                    logger.warning("SignalEngine eval error cid=%s type=%s: %s",
                                    watch.cid[:16], watch.type, exc)

            elapsed = time.monotonic() - start
            self._poll_count += 1
            self._total_poll_time += elapsed

            sleep_time = max(0, self._poll_interval - elapsed)
            self._stop.wait(sleep_time)  # interruptible sleep

        logger.info("SignalEngine poll loop stopped (polls=%d, fires=%d)",
                     self._poll_count, self._fires_count)

    def _evaluate(self, watch: WatchEntry, now: float) -> None:
        """Evaluate a single watch entry against current market data."""
        if watch.type == "ARB":
            self._eval_arb(watch, now)
        elif watch.type == "DIRECTION":
            self._eval_direction(watch, now)
        elif watch.type == "EXIT":
            self._eval_exit(watch, now)

    def _eval_arb(self, watch: WatchEntry, now: float) -> None:
        up_mid = self._poly_feed.get_midpoint(watch.up_token)
        dn_mid = self._poly_feed.get_midpoint(watch.dn_token)
        if up_mid is None or dn_mid is None:
            return
        combined = up_mid + dn_mid
        if combined < watch.max_combined:
            watch.last_triggered = now
            self._fire(watch, {
                "combined": round(combined, 4),
                "up_mid": round(up_mid, 4),
                "dn_mid": round(dn_mid, 4),
                "spread": round(1.0 - combined, 4),
            })

    def _eval_direction(self, watch: WatchEntry, now: float) -> None:
        btc_price = self._binance_feed.get_price(watch.coin_symbol)
        if btc_price is None:
            return
        if watch.coin_open <= 0 or watch.vol_1m <= 0:
            return
        fair_up = _bridge_fair(btc_price, watch.coin_open, watch.vol_1m, watch.mins_remaining)
        conviction = abs(fair_up - 0.5) * 2.0
        if conviction >= watch.min_conviction:
            watch.last_triggered = now
            direction = "UP" if fair_up > 0.5 else "DOWN"
            self._fire(watch, {
                "fair_up": round(fair_up, 4),
                "conviction": round(conviction, 4),
                "direction": direction,
                "btc_price": round(btc_price, 2),
            })

    def _eval_exit(self, watch: WatchEntry, now: float) -> None:
        mid = self._poly_feed.get_midpoint(watch.token_id)
        if mid is None:
            return
        tp_price = watch.entry_price * watch.tp_mult
        sl_price = watch.entry_price * (1 - watch.sl_pct)
        if mid >= tp_price:
            watch.last_triggered = now
            self._fire(watch, {
                "reason": "TP",
                "mid": round(mid, 4),
                "entry": round(watch.entry_price, 4),
                "target": round(tp_price, 4),
            })
        elif mid <= sl_price:
            watch.last_triggered = now
            self._fire(watch, {
                "reason": "SL",
                "mid": round(mid, 4),
                "entry": round(watch.entry_price, 4),
                "stop": round(sl_price, 4),
            })

    # ── Callback Firing ───────────────────────────

    def _fire(self, watch: WatchEntry, data: dict) -> None:
        """Fire callback in a separate thread (don't block poll loop).

        Also appends to the internal event log for monitoring.
        """
        self._fires_count += 1
        event = {
            "ts": time.time(),
            "type": watch.type,
            "cid": watch.cid,
            **data,
        }

        # Append to event log (bounded)
        with self._event_lock:
            self._events.append(event)
            if len(self._events) > 1000:
                self._events = self._events[-500:]

        logger.info("SignalEngine FIRE %s cid=%s %s", watch.type, watch.cid[:16], data)

        # Fire callback in separate daemon thread — never block poll loop
        if watch.callback:
            threading.Thread(
                target=self._safe_callback,
                args=(watch.callback, watch, data),
                daemon=True,
            ).start()

    @staticmethod
    def _safe_callback(callback: Callable, watch: WatchEntry, data: dict) -> None:
        """Execute callback with error handling."""
        try:
            callback(watch, data)
        except Exception as exc:
            logger.error("SignalEngine callback error cid=%s: %s", watch.cid[:16], exc)

    # ── Stats + Monitoring ────────────────────────

    def get_stats(self) -> dict:
        """Return engine stats: watches count, events fired, avg poll time."""
        with self._watch_lock:
            total_watches = sum(len(wl) for wl in self._watches.values())
            market_count = len(self._watches)
        avg_poll_ms = (
            (self._total_poll_time / self._poll_count * 1000)
            if self._poll_count > 0 else 0.0
        )
        with self._event_lock:
            event_count = len(self._events)
        return {
            "running": self.running,
            "markets": market_count,
            "watches": total_watches,
            "polls": self._poll_count,
            "fires": self._fires_count,
            "events_buffered": event_count,
            "avg_poll_ms": round(avg_poll_ms, 3),
            "poll_interval_ms": int(self._poll_interval * 1000),
        }

    def get_events(self, since_ts: float = 0) -> list[dict]:
        """Return recent events since timestamp."""
        with self._event_lock:
            if since_ts <= 0:
                return list(self._events)
            return [e for e in self._events if e["ts"] >= since_ts]

    def get_watches(self) -> dict[str, list[dict]]:
        """Return summary of all active watches (for monitoring)."""
        with self._watch_lock:
            result = {}
            for cid, watch_list in self._watches.items():
                result[cid] = [
                    {"type": w.type, "last_triggered": w.last_triggered}
                    for w in watch_list
                ]
            return result


# ═══════════════════════════════════════
#  Quick Test
# ═══════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Minimal smoke test — verify construction and poll loop start/stop
    # without needing live WS connections

    class _MockBinance:
        """Mock BinancePriceFeed for testing."""
        def get_price(self, symbol):
            # Simulate BTC at 87500 (slightly above a hypothetical open of 87000)
            if symbol == "BTCUSDT":
                return 87500.0
            return None

    class _MockPoly:
        """Mock PolymarketBookFeed for testing."""
        def __init__(self):
            self._mids = {}
        def set_mid(self, token_id, mid):
            self._mids[token_id] = mid
        def get_midpoint(self, token_id):
            return self._mids.get(token_id)
        def get_ob_imbalance(self, token_id):
            return 0.0

    print("=== SignalEngine Smoke Test ===\n")

    bf = _MockBinance()
    pf = _MockPoly()

    engine = SignalEngine(bf, pf, poll_interval_ms=50)

    # Track fired events
    fired = []
    def on_arb(watch, data):
        fired.append(("ARB", data))
    def on_dir(watch, data):
        fired.append(("DIR", data))
    def on_exit(watch, data):
        fired.append(("EXIT", data))

    # Set up mock data — arb opportunity: 0.45 + 0.48 = 0.93 < 0.96
    pf.set_mid("tok_up_1", 0.45)
    pf.set_mid("tok_dn_1", 0.48)

    engine.watch_arb("cid_arb", "tok_up_1", "tok_dn_1", callback=on_arb)
    engine.watch_direction(
        "cid_dir", "tok_up_2", "tok_dn_2",
        coin_symbol="BTCUSDT", coin_open=87000.0, vol_1m=0.002,
        mins_remaining=10.0, min_conviction=0.10, callback=on_dir,
    )

    # EXIT: entry at 0.40, tp_mult=1.3 -> tp=0.52, sl_pct=0.25 -> sl=0.30
    pf.set_mid("tok_exit", 0.55)  # above TP
    engine.watch_exit("cid_exit", "tok_exit", "UP", entry_price=0.40, callback=on_exit)

    engine.start()
    time.sleep(0.5)  # let a few polls run

    stats = engine.get_stats()
    events = engine.get_events()

    print(f"Stats: {stats}")
    print(f"Events ({len(events)}):")
    for e in events:
        print(f"  {e['type']}: {e}")
    print(f"\nCallbacks fired: {len(fired)}")
    for typ, data in fired:
        print(f"  {typ}: {data}")

    # Verify basics
    assert stats["running"], "Engine should be running"
    assert stats["watches"] == 3, f"Expected 3 watches, got {stats['watches']}"
    assert stats["fires"] >= 3, f"Expected >= 3 fires, got {stats['fires']}"
    assert len(fired) >= 3, f"Expected >= 3 callbacks, got {len(fired)}"

    # Verify cooldown — no re-trigger within 5s
    fires_before = stats["fires"]
    time.sleep(0.3)
    stats2 = engine.get_stats()
    # With 5s cooldown, fires should not increase (only 0.3s elapsed)
    assert stats2["fires"] == fires_before, \
        f"Cooldown failed: fires went from {fires_before} to {stats2['fires']}"

    # Test unwatch
    engine.unwatch("cid_arb")
    stats3 = engine.get_stats()
    assert stats3["watches"] == 2, f"Expected 2 watches after unwatch, got {stats3['watches']}"

    engine.stop()
    time.sleep(0.2)

    print(f"\nFinal stats: {engine.get_stats()}")
    print("\n=== ALL TESTS PASSED ===")
