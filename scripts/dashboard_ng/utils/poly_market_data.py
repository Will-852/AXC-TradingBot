"""Per-market live data for Polymarket dashboard.

Reads mm_state.json (local, zero API cost) + computes PNL scenarios.
For live midpoint/spread, calls PolymarketClient via miniforge subprocess.
"""

import json
import os
import subprocess
import time
import logging

log = logging.getLogger('axc.poly_market')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
MM_STATE_PATH = os.path.join(AXC_HOME, 'polymarket', 'logs', 'mm_state.json')
MM_STATE_1H_PATH = os.path.join(AXC_HOME, 'polymarket', 'logs', 'mm_state_1h.json')
MINIFORGE_PYTHON = '/opt/homebrew/Caskroom/miniforge/base/bin/python3'

def get_active_markets() -> list[dict]:
    """Read mm_state.json and return active markets with computed fields.

    Zero API cost — reads local file written by MM bot every cycle.
    """
    if not os.path.exists(MM_STATE_PATH):
        return []

    try:
        with open(MM_STATE_PATH) as f:
            state = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    markets_raw = state.get('markets', {})
    watchlist = state.get('watchlist', {})

    # Merge 1H bot markets (independent state file, same schema)
    if os.path.exists(MM_STATE_1H_PATH):
        try:
            with open(MM_STATE_1H_PATH) as f:
                state_1h = json.load(f)
            for cid, m in state_1h.get('markets', {}).items():
                if cid not in markets_raw and cid not in watchlist:
                    m['_source'] = '1h'
                    markets_raw[cid] = m
            for cid, m in state_1h.get('watchlist', {}).items():
                if cid not in markets_raw and cid not in watchlist:
                    m['_source'] = '1h'
                    watchlist[cid] = m
        except (json.JSONDecodeError, IOError):
            pass

    markets = []
    now_ms = int(time.time() * 1000)

    for cid, m in {**markets_raw, **watchlist}.items():
        up_shares = m.get('up_shares', 0)
        down_shares = m.get('down_shares', 0)
        up_avg = m.get('up_avg_price', 0)
        down_avg = m.get('down_avg_price', 0) or m.get('dn_avg_price', 0)
        entry_cost = m.get('entry_cost', 0)
        window_end = m.get('window_end_ms', m.get('end_ms', 0))
        window_start = m.get('window_start_ms', m.get('start_ms', 0))
        title = m.get('title', '')

        # Skip expired markets
        if window_end and window_end < now_ms:
            continue

        # PNL scenarios
        pnl_if_up = up_shares - entry_cost  # if UP wins: get $1 per up share
        pnl_if_down = down_shares - entry_cost  # if DOWN wins: get $1 per down share

        # Position delta
        total_shares = up_shares + down_shares
        delta_pct = ((up_shares - down_shares) / total_shares * 100) if total_shares > 0 else 0

        # Avg sum (should ≈ 1.0 for efficient market)
        avg_sum = up_avg + down_avg if (up_avg and down_avg) else 0

        # Capital in this market
        capital = entry_cost

        # Countdown
        remaining_s = max(0, (window_end - now_ms) / 1000) if window_end else 0
        remaining_min = int(remaining_s // 60)
        remaining_sec = int(remaining_s % 60)

        # Progress through window
        if window_start and window_end and window_end > window_start:
            elapsed = now_ms - window_start
            total_window = window_end - window_start
            progress_pct = min(100, max(0, elapsed / total_window * 100))
            window_total_s = total_window / 1000
        else:
            progress_pct = 0
            window_total_s = 15 * 60  # default 15min

        up_token = m.get('up_token_id', m.get('up_tok', ''))
        dn_token = m.get('down_token_id', m.get('dn_tok', ''))

        markets.append({
            'cid': cid,
            'title': title,
            'up_shares': round(up_shares, 2),
            'down_shares': round(down_shares, 2),
            'up_avg': round(up_avg, 4),
            'down_avg': round(down_avg, 4),
            'avg_sum': round(avg_sum, 4),
            'entry_cost': round(entry_cost, 2),
            'capital': round(capital, 2),
            'pnl_if_up': round(pnl_if_up, 2),
            'pnl_if_down': round(pnl_if_down, 2),
            'delta_pct': round(delta_pct, 1),
            'delta_shares': round(up_shares - down_shares, 1),
            'remaining_s': int(remaining_s),
            'remaining_str': f'{remaining_min}:{remaining_sec:02d}',
            'progress_pct': round(progress_pct, 1),
            'window_end_ms': window_end,
            'window_total_s': int(window_total_s),
            'up_token': up_token,
            'dn_token': dn_token,
            'phase': m.get('phase', ''),
        })

    # Sort by remaining time (soonest first)
    markets.sort(key=lambda x: x.get('remaining_s', 99999))
    return markets


def get_live_prices(up_token: str, dn_token: str) -> dict:
    """Query live midpoint + spread for a specific market's tokens.

    Uses miniforge subprocess (py_clob_client). ~3s per call.
    """
    if not up_token or not dn_token:
        return {}

    script = f'''
import sys, os, json
sys.path.insert(0, {AXC_HOME!r})
sys.path.insert(0, {os.path.join(AXC_HOME, 'scripts')!r})
os.environ['AXC_HOME'] = {AXC_HOME!r}
from dotenv import load_dotenv
load_dotenv({os.path.join(AXC_HOME, 'secrets', '.env')!r})
from polymarket.exchange.polymarket_client import PolymarketClient
client = PolymarketClient()

result = dict()
try:
    result['up_mid'] = client.get_midpoint({up_token!r})
except Exception:
    result['up_mid'] = 0
try:
    result['dn_mid'] = client.get_midpoint({dn_token!r})
except Exception:
    result['dn_mid'] = 0
try:
    result['up_spread'] = client.get_spread({up_token!r})
except Exception:
    result['up_spread'] = 0
try:
    result['dn_spread'] = client.get_spread({dn_token!r})
except Exception:
    result['dn_spread'] = 0
print(json.dumps(result))
'''

    try:
        result = subprocess.run(
            [MINIFORGE_PYTHON, '-c', script],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, 'AXC_HOME': AXC_HOME},
        )
        if result.returncode != 0:
            log.error('get_live_prices failed: %s', result.stderr[:150])
            return {}
        return json.loads(result.stdout)
    except Exception as e:
        log.error('get_live_prices error: %s', e)
        return {}


def get_latest_signals() -> dict[str, dict]:
    """Read latest signal per market from mm_signals.jsonl."""
    signals_path = os.path.join(AXC_HOME, 'polymarket', 'logs', 'mm_signals.jsonl')
    if not os.path.exists(signals_path):
        return {}

    latest = {}
    try:
        with open(signals_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                    cid = s.get('cid', '')
                    latest[cid] = s  # last one wins
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return latest


def get_market_summary() -> dict:
    """Get aggregate summary from mm_state.json."""
    if not os.path.exists(MM_STATE_PATH):
        return {}
    try:
        with open(MM_STATE_PATH) as f:
            state = json.load(f)
        return {
            'bankroll': state.get('bankroll', 0),
            'total_pnl': state.get('total_pnl', 0),
            'daily_pnl': state.get('daily_pnl', 0),
            'total_markets': state.get('total_markets', 0),
            'consecutive_losses': state.get('consecutive_losses', 0),
            'fill_stats': state.get('fill_stats', {}),
            'risk_mode': state.get('_risk_mode', ''),
        }
    except Exception:
        return {}
