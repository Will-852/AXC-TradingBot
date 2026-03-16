"""exchange_clients.py — Exchange client singletons with timeout + periodic re-sync."""

import sys
import threading
import time

from scripts.dashboard.constants import CONNECT_TIMEOUT_SEC, SCRIPTS_DIR

# ── Aster ──────────────────────────────────────────────────────────
_aster_client_singleton = None
_aster_client_ts = 0
_EXCHANGE_RESYNC_INTERVAL = 300  # re-sync time offset every 5 min


def _run_with_timeout(fn, timeout=CONNECT_TIMEOUT_SEC):
    """Run fn() in a daemon thread, raise TimeoutError if exceeds timeout."""
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"連接超時（>{timeout}s），請檢查網絡或交易所狀態")
    if error[0]:
        raise error[0]
    return result[0]


def _get_aster_client():
    """Singleton AsterClient with periodic time re-sync."""
    global _aster_client_singleton, _aster_client_ts
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.aster_client import AsterClient

    now = time.time()
    if _aster_client_singleton is None:
        _aster_client_singleton = AsterClient()
        _aster_client_ts = now
    elif now - _aster_client_ts >= _EXCHANGE_RESYNC_INTERVAL:
        try:
            _aster_client_singleton._sync_time()
        except Exception:
            pass
        _aster_client_ts = now
    return _aster_client_singleton


def _reset_aster_client():
    """Force rebuild on auth/connection failure."""
    global _aster_client_singleton, _aster_client_ts
    _aster_client_singleton = None
    _aster_client_ts = 0


def _reset_binance_client():
    """Force rebuild on auth/connection failure."""
    global _binance_client_singleton, _binance_client_ts
    _binance_client_singleton = None
    _binance_client_ts = 0


def _reset_hl_client():
    """Force rebuild on auth/connection failure."""
    global _hl_client_singleton
    _hl_client_singleton = None


# ── HyperLiquid ─────────────────────────────────────────────────────
_hl_client_singleton = None


def _get_hl_client():
    """Singleton HyperLiquidClient."""
    global _hl_client_singleton
    if _hl_client_singleton is None:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from trader_cycle.exchange.hyperliquid_client import HyperLiquidClient
        _hl_client_singleton = HyperLiquidClient()
    return _hl_client_singleton


# ── Binance ──────────────────────────────────────────────────────────
_binance_client_singleton = None
_binance_client_ts = 0


def _get_binance_client():
    """Singleton BinanceClient with periodic time re-sync."""
    global _binance_client_singleton, _binance_client_ts
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from trader_cycle.exchange.binance_client import BinanceClient

    now = time.time()
    if _binance_client_singleton is None:
        _binance_client_singleton = BinanceClient()
        _binance_client_ts = now
    elif now - _binance_client_ts >= _EXCHANGE_RESYNC_INTERVAL:
        try:
            _binance_client_singleton._sync_time()
        except Exception:
            pass
        _binance_client_ts = now
    return _binance_client_singleton
