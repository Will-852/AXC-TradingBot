"""
ws_shared.py — Shared WebSocket singleton manager for Polymarket runners.

Created: 2026-03-28 (BMD fix P2-Step8).
Ensures MM, 1H, and 5M runners share a single BinancePriceFeed and
PolymarketBookFeed instead of creating independent connections.

Design decisions:
  - Lazy creation: first caller triggers start(), subsequent calls reuse.
  - Refcount lifecycle: stop() only when last user releases.
  - Thread-safe: all state mutations under _lock.
  - ws_user NOT included: already singleton (only MM uses it).
  - 4H/Daily NOT affected: they don't use WS feeds.

⚠️ 容易錯 #11: BinancePriceFeed has 23h preemptive reconnect — preserved.
⚠️ 容易錯 #12: Runner crash must not kill shared feed — release() in finally.
⚠️ 容易錯 #16: PolyBookFeed.subscribe() triggers reconnect → brief data gap.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# ── Binance ────────────────────────────────────────

_binance_feed = None
_binance_refs = 0


def get_binance():
    """Get or create shared BinancePriceFeed. Thread-safe, refcounted.

    Returns:
        BinancePriceFeed instance (already started).

    Usage:
        feed = ws_shared.get_binance()
        try:
            # ... use feed.get_price(), feed.get_bbo() ...
        finally:
            ws_shared.release_binance()  # ⚠️ #12: MUST be in finally
    """
    global _binance_feed, _binance_refs
    with _lock:
        if _binance_feed is None:
            from polymarket.data.ws_binance import BinancePriceFeed
            _binance_feed = BinancePriceFeed()
            _binance_feed.start()
            logger.info("SharedWS: BinancePriceFeed created and started")
        _binance_refs += 1
        logger.debug("SharedWS: Binance refcount → %d", _binance_refs)
        return _binance_feed


def release_binance():
    """Decrement Binance refcount. Stops feed when last user releases."""
    global _binance_feed, _binance_refs
    with _lock:
        if _binance_refs <= 0:
            logger.warning("SharedWS: Binance release called but refcount already %d "
                           "— possible double-release bug", _binance_refs)
            return
        _binance_refs -= 1
        logger.debug("SharedWS: Binance refcount → %d", _binance_refs)
        if _binance_refs == 0 and _binance_feed is not None:
            try:
                _binance_feed.stop()
                logger.info("SharedWS: BinancePriceFeed stopped (last user released)")
            except Exception as e:
                logger.warning("SharedWS: BinancePriceFeed stop error: %s", e)
            _binance_feed = None


# ── Polymarket Book ────────────────────────────────

_poly_feed = None
_poly_refs = 0


def get_poly():
    """Get or create shared PolymarketBookFeed. Thread-safe, refcounted.

    Returns:
        PolymarketBookFeed instance (already started).

    ⚠️ #16: subscribe() on the shared instance triggers reconnect,
    which briefly clears _data/_books for ALL users. REST fallback exists.

    Usage:
        feed = ws_shared.get_poly()
        try:
            # ... use feed.get_midpoint(), feed.subscribe() ...
        finally:
            ws_shared.release_poly()  # ⚠️ #12: MUST be in finally
    """
    global _poly_feed, _poly_refs
    with _lock:
        if _poly_feed is None:
            from polymarket.data.ws_polymarket import PolymarketBookFeed
            _poly_feed = PolymarketBookFeed()
            _poly_feed.start()
            logger.info("SharedWS: PolymarketBookFeed created and started")
        _poly_refs += 1
        logger.debug("SharedWS: Poly refcount → %d", _poly_refs)
        return _poly_feed


def release_poly():
    """Decrement Poly refcount. Stops feed when last user releases."""
    global _poly_feed, _poly_refs
    with _lock:
        if _poly_refs <= 0:
            logger.warning("SharedWS: Poly release called but refcount already %d "
                           "— possible double-release bug", _poly_refs)
            return
        _poly_refs -= 1
        logger.debug("SharedWS: Poly refcount → %d", _poly_refs)
        if _poly_refs == 0 and _poly_feed is not None:
            try:
                _poly_feed.stop()
                logger.info("SharedWS: PolymarketBookFeed stopped (last user released)")
            except Exception as e:
                logger.warning("SharedWS: PolymarketBookFeed stop error: %s", e)
            _poly_feed = None


# ── Convenience ────────────────────────────────────

def status() -> dict:
    """Return current state for diagnostics / --status output."""
    with _lock:
        return {
            "binance": {"active": _binance_feed is not None, "refs": _binance_refs},
            "poly": {"active": _poly_feed is not None, "refs": _poly_refs},
        }
