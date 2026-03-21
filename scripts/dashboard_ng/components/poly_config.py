"""Polymarket config panel — scrubbers for all strategy parameters.

Changes are written to polymarket/config/params.py which is imported
by the pipeline at each cycle start. No restart needed.
"""

import os
import re
import ast
import tempfile
import logging

from nicegui import ui, run

log = logging.getLogger('axc.poly_config')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
PARAMS_PATH = os.path.join(AXC_HOME, 'polymarket', 'config', 'params.py')

# Parameter definitions: (key, label, default, min, max, step, unit)
SCRUBBERS = [
    # Scanning
    ('SCAN_INTERVAL_SEC', 'Scan Interval', 180, 30, 600, 30, 's'),
    ('MAX_MARKETS_TO_SCAN', 'Max Markets', 300, 50, 500, 50, ''),
    ('MIN_LIQUIDITY_USDC', 'Min Liquidity', 1000, 100, 5000, 100, '$'),
    ('AI_TEMPERATURE', 'AI Temperature', 0.3, 0.0, 1.0, 0.1, ''),
    ('MAX_MARKETS_FOR_AI', 'Max AI Assess', 5, 1, 10, 1, ''),

    # Edge thresholds
    ('MIN_EDGE_PCT', 'Min Edge', 0.10, 0.01, 0.20, 0.01, ''),
    ('CRYPTO_15M_MIN_EDGE_PCT', '15M Edge', 0.065, 0.01, 0.15, 0.005, ''),
    ('CVD_MIN_EDGE_PCT', 'CVD Edge', 0.065, 0.01, 0.15, 0.005, ''),
    ('MICRO_MIN_EDGE_PCT', 'Micro Edge', 0.065, 0.01, 0.15, 0.005, ''),

    # Sizing
    ('KELLY_FRACTION', 'Kelly Fraction', 0.50, 0.1, 1.0, 0.05, ''),
    ('KELLY_MAX_BET_USDC', 'Max Bet', 50, 10, 200, 10, '$'),
    ('KELLY_MIN_BET_USDC', 'Min Bet', 1, 1, 20, 1, '$'),
    ('CRYPTO_15M_MAX_BET_USDC', '15M Max Bet', 50, 10, 100, 5, '$'),

    # Risk
    ('TAKE_PROFIT_TOKEN_PRICE', 'Take Profit', 0.93, 0.80, 0.99, 0.01, ''),
    ('MAX_TOTAL_EXPOSURE', 'Max Exposure', 0.30, 0.10, 0.50, 0.01, '%'),
    ('MAX_PER_BET', 'Max Per Bet', 0.01, 0.005, 0.05, 0.005, ''),
    ('MAX_PER_MARKET', 'Max Per Market', 0.10, 0.05, 0.30, 0.01, '%'),
    ('MAX_OPEN_POSITIONS', 'Max Positions', 5, 1, 10, 1, ''),
    ('MAX_SIGNALS_PER_CYCLE', 'Max Signals', 3, 1, 10, 1, ''),
    ('COOLDOWN_AFTER_LOSS_MIN', 'Loss Cooldown', 60, 10, 360, 10, 'm'),

    # GTO
    ('GTO_ADVERSE_BLOCK_THRESHOLD', 'GTO Adverse', 0.80, 0.5, 1.0, 0.05, ''),
    ('GTO_NASH_SKIP_THRESHOLD', 'GTO Nash', 0.90, 0.5, 1.0, 0.05, ''),
    ('GTO_UNEXPLOITABILITY_MIN', 'GTO Unexploit', 0.30, 0.1, 0.8, 0.05, ''),
]

TOGGLES = [
    ('CVD_ENABLED', 'CVD Signal', True),
    ('MICRO_ENABLED', 'Microstructure', True),
    ('HEDGE_ENABLED', 'Hyperliquid Hedge', False),
]


def _read_current_params() -> dict:
    """Read current values from params.py."""
    values = {}
    if not os.path.exists(PARAMS_PATH):
        return values
    try:
        with open(PARAMS_PATH) as f:
            content = f.read()
        for line in content.split('\n'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip()
                # Remove comments
                if '#' in val:
                    val = val[:val.index('#')].strip()
                try:
                    values[key] = ast.literal_eval(val)
                except (ValueError, SyntaxError):
                    values[key] = val
    except Exception as e:
        log.error('Failed to read params.py: %s', e)
    return values


def _write_param(key: str, value):
    """Write a single parameter to polymarket/config/params.py."""
    if not os.path.exists(PARAMS_PATH):
        with open(PARAMS_PATH, 'w') as f:
            f.write(f'# Polymarket Config Overrides\n{key} = {repr(value)}\n')
        return

    with open(PARAMS_PATH) as f:
        content = f.read()

    # Try to replace existing
    if isinstance(value, bool):
        pattern = rf'^({key}\s*=\s*).*$'
        replacement = rf'\g<1>{value}'
    elif isinstance(value, float):
        pattern = rf'^({key}\s*=\s*).*$'
        replacement = rf'\g<1>{value}'
    else:
        pattern = rf'^({key}\s*=\s*).*$'
        replacement = rf'\g<1>{repr(value)}'

    new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
    if count == 0:
        new_content = content.rstrip() + f'\n{key} = {repr(value)}\n'

    # Atomic write: tempfile + os.replace
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PARAMS_PATH), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(new_content)
        os.replace(tmp, PARAMS_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info('Poly param %s = %s', key, value)


def render_poly_config():
    """Render polymarket config panel with sliders + toggles."""
    with ui.expansion('Strategy Config', icon='tune', value=False).classes('w-full'):
        current = _read_current_params()

        # Toggles
        with ui.row().classes('gap-4 mb-2'):
            for key, label, default in TOGGLES:
                val = current.get(key, default)

                async def on_toggle(e, k=key):
                    await run.io_bound(_write_param, k, e.value)
                    ui.notify(f'{k} = {e.value}', type='info')

                ui.switch(label, value=val, on_change=on_toggle) \
                    .props('color=green dense')

        # Scrubbers in 2-column grid
        with ui.element('div').classes('w-full').style(
            'display: grid; grid-template-columns: 1fr 1fr; gap: 6px;'
        ):
            for key, label, default, mn, mx, step, unit in SCRUBBERS:
                val = current.get(key, default)

                with ui.row().classes('items-center gap-2 py-1 px-2 rounded '
                                      'border border-gray-800 bg-gray-900/50'):
                    ui.label(label).classes('text-[12px] text-gray-500 min-w-[85px]')

                    is_int = isinstance(default, int) and step >= 1
                    fmt = '%.0f' if is_int else f'%.{max(0, len(str(step).split(".")[-1]) if "." in str(step) else 0)}f'

                    slider = ui.slider(min=mn, max=mx, step=step, value=val) \
                        .props('dense color=blue-grey-6') \
                        .classes('flex-1')

                    val_label = ui.label(f'{val}{unit}').classes(
                        'text-[12px] font-mono text-gray-300 min-w-[45px] text-right')

                    async def on_change(e, k=key, u=unit, lbl=val_label, integer=is_int):
                        v = int(e.value) if integer else round(e.value, 4)
                        lbl.text = f'{v}{u}'
                        await run.io_bound(_write_param, k, v)

                    slider.on('update:model-value', on_change)

        ui.label('Changes are live — pipeline reads params.py each cycle.').classes(
            'text-[11px] text-gray-600 mt-2')
