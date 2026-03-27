"""poly_helpers.py — Pure data functions for the Polymarket page.

No UI imports, no NiceGUI dependencies. Safe to call from io_bound().
"""

import json
import logging
import os
import subprocess

log = logging.getLogger('axc.poly')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))


def get_poly_data() -> dict:
    """Fetch aggregate polymarket data from old dashboard backend."""
    from scripts.dashboard.polymarket import handle_polymarket_data
    _, data = handle_polymarket_data()
    return data


def get_cycle_status() -> dict:
    """Fetch pipeline cycle status from old dashboard backend."""
    from scripts.dashboard.polymarket import handle_polymarket_cycle_status
    _, data = handle_polymarket_cycle_status()
    return data


def fetch_data_api() -> dict:
    """Fetch portfolio data from Polymarket Data API (public, no auth).

    Uses curl because urllib gets 403 from Polymarket User-Agent block.
    """
    wallet = os.getenv('POLY_WALLET_ADDRESS', '')
    if not wallet:
        return {}
    result = {}
    try:
        r = subprocess.run(
            ['curl', '-s', f'https://data-api.polymarket.com/value?user={wallet}'],
            capture_output=True, text=True, timeout=10,
        )
        resp = json.loads(r.stdout)
        if resp and isinstance(resp, list):
            result['positions_value'] = resp[0].get('value', 0)
    except Exception as e:
        log.warning('fetch_data_api portfolio value failed: %s', e)
    try:
        r = subprocess.run(
            ['curl', '-s', f'https://data-api.polymarket.com/positions?user={wallet}'],
            capture_output=True, text=True, timeout=10,
        )
        positions = json.loads(r.stdout)
        open_pos = [p for p in positions if p.get('currentValue', 0) > 0.01]
        closed = [p for p in positions if p.get('currentValue', 0) <= 0.01]
        result['open_count'] = len(open_pos)
        result['closed_wins'] = len([p for p in closed if p.get('cashPnl', 0) > 0])
        result['closed_losses'] = len([p for p in closed if p.get('cashPnl', 0) < 0])
    except Exception as e:
        log.warning('fetch_data_api positions failed: %s', e)
    return result


def check_auth() -> dict:
    """Check Polymarket auth status (local files only, no API calls).

    💰 Auth failure = user thinks they have permissions but don't.
    """
    axc = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
    secrets_env = os.path.join(axc, 'secrets', '.env')
    creds_cache = os.path.join(axc, 'secrets', '.poly_api_creds.json')

    result = {'key': False, 'wallet': '', 'l2_creds': False, 'network': 'Polygon'}

    pk = os.getenv('POLY_PRIVATE_KEY', '')
    wallet = os.getenv('POLY_WALLET_ADDRESS', '')

    # Fallback: read from .env file
    if not pk and os.path.exists(secrets_env):
        try:
            with open(secrets_env) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('POLY_PRIVATE_KEY=') and len(line) > 20:
                        pk = 'set'
                    elif line.startswith('POLY_WALLET_ADDRESS=') and len(line) > 22:
                        wallet = line.split('=', 1)[1].strip().strip('"').strip("'")
        except Exception as e:
            log.error('Auth L1: failed to read secrets/.env: %s', e)

    result['key'] = bool(pk)
    result['wallet'] = wallet

    # Check L2 cached creds
    if os.path.exists(creds_cache):
        try:
            with open(creds_cache) as f:
                c = json.load(f)
            result['l2_creds'] = bool(c.get('api_key') or c.get('apiKey'))
        except Exception as e:
            log.error('Auth L2: failed to read .poly_api_creds.json: %s', e)

    return result
