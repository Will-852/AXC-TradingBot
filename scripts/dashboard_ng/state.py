"""AXC Dashboard — Shared state management.

Single background collector updates app.storage.general.
UI timers read from storage only — never call backend directly.
This prevents concurrent collect_data() race conditions (BMD #3).
"""

import asyncio
import logging
import sys
import os
import time

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

from nicegui import app, run

log = logging.getLogger('axc.state')

# Import backend modules
from scripts.dashboard.collectors import collect_data
from scripts.dashboard.services import get_launchagents
from scripts.dashboard.exchange_auth import (
    handle_aster_status,
    handle_binance_status,
    handle_hl_status,
)

# Refresh intervals (seconds)
DATA_INTERVAL = 5
SERVICES_INTERVAL = 30
EXCHANGE_INTERVAL = 60


async def _update_data():
    """Fetch main dashboard data in a thread (blocking calls)."""
    try:
        data = await run.io_bound(collect_data)
        old_data = app.storage.general.get('dashboard_data') or {}
        app.storage.general['dashboard_data'] = data
        app.storage.general['dashboard_data_ts'] = time.time()

        # Auto-push notifications on state changes
        _check_for_alerts(old_data, data)
    except Exception as e:
        log.error('collect_data failed: %s', e)


def _check_for_alerts(old: dict, new: dict):
    """Compare old vs new data, push notifications for important changes."""
    try:
        from scripts.dashboard_ng.components.notifications import push_notification

        # Circuit breaker triggered
        old_risk = old.get('risk_status', {})
        new_risk = new.get('risk_status', {})
        if new_risk.get('trigger_cooldown') and not old_risk.get('trigger_cooldown'):
            push_notification('Circuit breaker triggered!', 'circuit_breaker')

        # Position opened/closed
        old_pos = old.get('live_positions', [])
        new_pos = new.get('live_positions', [])
        if len(new_pos) > len(old_pos):
            diff = len(new_pos) - len(old_pos)
            push_notification(f'{diff} new position(s) opened', 'trade')
        elif len(new_pos) < len(old_pos):
            diff = len(old_pos) - len(new_pos)
            push_notification(f'{diff} position(s) closed', 'trade')

        # Consecutive losses increased
        old_losses = old.get('consecutive_losses', 0)
        new_losses = new.get('consecutive_losses', 0)
        if new_losses > old_losses and new_losses >= 2:
            push_notification(f'Consecutive losses: {new_losses}', 'circuit_breaker')

    except Exception:
        pass  # notification system is optional


async def _update_services():
    """Fetch LaunchAgent statuses."""
    try:
        agents = await run.io_bound(get_launchagents)
        app.storage.general['services'] = agents
    except Exception as e:
        log.error('get_launchagents failed: %s', e)


async def _update_exchanges():
    """Fetch exchange connection statuses."""
    try:
        results = {}
        for name, handler in [
            ('aster', handle_aster_status),
            ('binance', handle_binance_status),
            ('hl', handle_hl_status),
        ]:
            try:
                result = await run.io_bound(handler)
                results[name] = result[1] if isinstance(result, tuple) else result
            except Exception as e:
                log.warning('Exchange %s status failed: %s', name, e)
                results[name] = {'status': 'error', 'error': str(e)}
        app.storage.general['exchanges'] = results
    except Exception as e:
        log.error('exchange status failed: %s', e)


async def background_collector():
    """Main background loop — single collector, no race conditions."""
    log.info('Background collector started')
    # Initial fetch
    await asyncio.gather(
        _update_data(),
        _update_services(),
        _update_exchanges(),
    )
    while True:
        await asyncio.sleep(DATA_INTERVAL)
        await _update_data()

        # Services + exchanges at their own intervals
        ts = time.time()
        svc_ts = app.storage.general.get('_svc_ts', 0)
        exch_ts = app.storage.general.get('_exch_ts', 0)

        if ts - svc_ts >= SERVICES_INTERVAL:
            await _update_services()
            app.storage.general['_svc_ts'] = ts

        if ts - exch_ts >= EXCHANGE_INTERVAL:
            await _update_exchanges()
            app.storage.general['_exch_ts'] = ts


def get_data() -> dict:
    """Read cached dashboard data from storage. Never blocks. Always returns dict."""
    return app.storage.general.get('dashboard_data') or {}


def get_services() -> dict:
    """Read cached service statuses."""
    return app.storage.general.get('services', {})


def get_exchanges() -> dict:
    """Read cached exchange statuses."""
    return app.storage.general.get('exchanges', {})
