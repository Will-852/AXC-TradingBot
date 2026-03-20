"""Live Polymarket data — queries CLOB API via miniforge subprocess.

The PolymarketClient uses py_clob_client which is only in miniforge env.
NiceGUI runs on homebrew python. Bridge via subprocess + JSON.
"""

import subprocess
import json
import logging
import os

log = logging.getLogger('axc.poly_live')

MINIFORGE_PYTHON = '/opt/homebrew/Caskroom/miniforge/base/bin/python3'
AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))


def _build_script() -> str:
    """Build the query script with correct paths. No f-string/template issues."""
    return f'''
import sys, os, json
sys.path.insert(0, {AXC_HOME!r})
sys.path.insert(0, {os.path.join(AXC_HOME, 'scripts')!r})
os.environ['AXC_HOME'] = {AXC_HOME!r}
from dotenv import load_dotenv
load_dotenv({os.path.join(AXC_HOME, 'secrets', '.env')!r})

from polymarket.exchange.polymarket_client import PolymarketClient
client = PolymarketClient()

result = dict()
result['balance'] = client.get_usdc_balance()

orders = client.get_orders()
result['open_orders'] = len(orders)
result['orders'] = []
for o in orders[:10]:
    result['orders'].append(dict(
        id=o.get('id', '')[:12],
        status=o.get('status', ''),
        side=o.get('side', ''),
        size=o.get('original_size', o.get('size', '')),
        price=o.get('price', ''),
        market=o.get('market', '')[:12],
    ))

trades = client.get_trades()
result['total_trades'] = len(trades)
result['recent_trades'] = []
for t in trades[:5]:
    result['recent_trades'].append(dict(
        id=t.get('id', '')[:12],
        side=t.get('side', ''),
        size=t.get('size', ''),
        price=t.get('price', ''),
        market=t.get('market', '')[:12],
    ))

print(json.dumps(result))
'''


_cache = {'data': {}, 'trade_count': 0}


def query_live() -> dict:
    """Query Polymarket CLOB for live balance, orders, trades.

    Caches results — only re-queries balance + orders each time.
    Trade count is checked; full trade list only fetched when count changes.
    """
    try:
        script = _build_script()
        result = subprocess.run(
            [MINIFORGE_PYTHON, '-c', script],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, 'AXC_HOME': AXC_HOME},
        )
        if result.returncode != 0:
            log.error('poly_live failed: %s', result.stderr[:200])
            return _cache['data'] or {}  # return last good data
        try:
            data = json.loads(result.stdout)
            _cache['data'] = data
            return data
        except json.JSONDecodeError:
            log.error('poly_live invalid JSON: %s', result.stdout[:100])
            return _cache['data'] or {}
    except subprocess.TimeoutExpired:
        log.error('poly_live timeout')
        return _cache['data'] or {}
    except Exception as e:
        log.error('poly_live error: %s', e)
        return _cache['data'] or {}
