"""Trading controls — Profile selector, Regime selector, Trading toggle.

These directly modify config/params.py (same as current dashboard).
"""

import re
import os
import logging

from nicegui import ui, run

from scripts.dashboard_ng.state import get_data

log = logging.getLogger('axc.controls')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
PARAMS_PATH = os.path.join(AXC_HOME, 'config', 'params.py')


def _write_param(key: str, value: str):
    """Update a single parameter in params.py (string replacement).

    If key doesn't exist, appends it (matches current dashboard behavior).
    """
    with open(PARAMS_PATH, 'r') as f:
        content = f.read()

    # Match: KEY = "VALUE" or KEY = 'VALUE' or KEY = True/False/word
    if value in ('True', 'False'):
        pattern = rf'^({key}\s*=\s*)\w+'
        replacement = rf'\g<1>{value}'
    else:
        pattern = rf'^({key}\s*=\s*["\']).*?(["\'])'
        replacement = rf'\g<1>{value}\g<2>'

    new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
    if count == 0:
        # Key doesn't exist — append it
        if value in ('True', 'False'):
            content += f'\n{key} = {value}\n'
        else:
            content += f'\n{key} = "{value}"\n'
        new_content = content
        log.info('Appended %s = %s to params.py', key, value)

    with open(PARAMS_PATH, 'w') as f:
        f.write(new_content)
    log.info('Updated %s = %s', key, value)
    return True


def render_controls():
    """Render profile/regime/trading controls."""
    # Flag to suppress notifications during sync
    syncing = {'active': True}

    with ui.row().classes('gap-4 flex-wrap items-end'):
        # Profile selector
        with ui.column().classes('gap-1'):
            ui.label('Profile').classes('text-xs text-gray-500 uppercase tracking-wide')
            profile_select = ui.toggle(
                ['CONSERVATIVE', 'BALANCED', 'AGGRESSIVE'],
                value='AGGRESSIVE',
            ).props('dense no-caps color=indigo')

            async def on_profile(e):
                if syncing['active']:
                    return
                success = await run.io_bound(_write_param, 'ACTIVE_PROFILE', e.value)
                if success:
                    ui.notify(f'Profile → {e.value}', type='positive')

            profile_select.on_value_change(on_profile)

        # Regime selector
        with ui.column().classes('gap-1'):
            ui.label('Regime').classes('text-xs text-gray-500 uppercase tracking-wide')
            regime_select = ui.toggle(
                ['classic', 'classic_cp', 'bocpd', 'full'],
                value='full',
            ).props('dense no-caps color=indigo')

            async def on_regime(e):
                if syncing['active']:
                    return
                success = await run.io_bound(_write_param, 'ACTIVE_REGIME_PRESET', e.value)
                if success:
                    ui.notify(f'Regime → {e.value}', type='positive')

            regime_select.on_value_change(on_regime)

        # Trading toggle
        with ui.column().classes('gap-1'):
            ui.label('Trading').classes('text-xs text-gray-500 uppercase tracking-wide')
            trading_switch = ui.switch('Enabled').props('color=green')

            async def on_trading(e):
                if syncing['active']:
                    return
                val = 'True' if e.value else 'False'
                success = await run.io_bound(_write_param, 'TRADING_ENABLED', val)
                if success:
                    state = 'ON' if e.value else 'OFF'
                    ui.notify(f'Trading {state}', type='positive' if e.value else 'warning')

            trading_switch.on_value_change(on_trading)

        # Sync current values from data
        def sync_controls():
            d = get_data()
            if not d:
                return
            syncing['active'] = True
            profile = d.get('active_profile', 'AGGRESSIVE')
            regime = d.get('active_regime_preset', 'full')
            trading = d.get('params', {}).get('TRADING_ENABLED', d.get('trading_enabled', True))

            if profile_select.value != profile:
                profile_select.set_value(profile)
            if regime_select.value != regime:
                regime_select.set_value(regime)
            if trading_switch.value != trading:
                trading_switch.set_value(trading)
            syncing['active'] = False

        # Sync once on load (delayed to let data arrive), then every 10s
        ui.timer(2, sync_controls, once=True)
        ui.timer(10, sync_controls)
