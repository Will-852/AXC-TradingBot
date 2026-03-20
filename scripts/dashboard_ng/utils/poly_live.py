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

_QUERY_SCRIPT = '''
import sys, os, json
sys.path.insert(0, '{axc}')
sys.path.insert(0, '{axc}/scripts')
os.environ['AXC_HOME'] = '{axc}'
from dotenv import load_dotenv
load_dotenv('{axc}/secrets/.env')

from polymarket.exchange.polymarket_client import PolymarketClient
client = PolymarketClient()

result = {{}}
result['balance'] = client.get_usdc_balance()

orders = client.get_orders()
result['open_orders'] = len(orders)
result['orders'] = [{{
    'id': o.get('id', '')[:12],
    'status': o.get('status', ''),
    'side': o.get('side', ''),
    'size': o.get('original_size', o.get('size', '')),
    'price': o.get('price', ''),
    'market': o.get('market', '')[:12],
}} for o in orders[:10]]

trades = client.get_trades()
result['total_trades'] = len(trades)
result['recent_trades'] = [{{
    'id': t.get('id', '')[:12],
    'side': t.get('side', ''),
    'size': t.get('size', ''),
    'price': t.get('price', ''),
    'market': t.get('market', '')[:12],
}} for t in trades[:5]]

print(json.dumps(result))
'''.replace('{axc}', AXC_HOME)


def query_live() -> dict:
    """Query Polymarket CLOB for live balance, orders, trades."""
    try:
        result = subprocess.run(
            [MINIFORGE_PYTHON, '-c', _QUERY_SCRIPT],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, 'AXC_HOME': AXC_HOME},
        )
        if result.returncode != 0:
            log.error('poly_live query failed: %s', result.stderr[:200])
            return {}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log.error('poly_live query timeout')
        return {}
    except Exception as e:
        log.error('poly_live error: %s', e)
        return {}
