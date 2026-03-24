"""Trading controls — Zone selector, Regime selector, Trading toggle, Service control.

These directly modify config/params.py (same as current dashboard).
Service control uses launchctl via axc_service_control.py.
"""

import re
import os
import logging

from nicegui import ui, run

from scripts.dashboard_ng.state import get_data
from scripts.dashboard_ng.utils import axc_service_control as svc_ctl

log = logging.getLogger('axc.controls')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
PARAMS_PATH = os.path.join(AXC_HOME, 'config', 'params.py')


def _write_param(key: str, value: str):
    """Update a single parameter in params.py using line-by-line replacement.

    Safety: reads all lines, modifies only the target line, preserves everything else.
    If key doesn't exist, appends it at the end.
    """
    with open(PARAMS_PATH, 'r') as f:
        lines = f.readlines()

    # Guard: params.py should be >100 lines. If truncated, refuse to write.
    if len(lines) < 10:
        log.error('params.py truncated (%d lines) — refusing to write to avoid data loss', len(lines))
        return False

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f'{key} ') or stripped.startswith(f'{key}='):
            # Replace this line
            if value in ('True', 'False'):
                lines[i] = f'{key} = {value}\n'
            else:
                lines[i] = f'{key} = "{value}"\n'
            found = True
            break

    if not found:
        # Append
        if value in ('True', 'False'):
            lines.append(f'\n{key} = {value}\n')
        else:
            lines.append(f'\n{key} = "{value}"\n')
        log.info('Appended %s = %s to params.py', key, value)

    with open(PARAMS_PATH, 'w') as f:
        f.writelines(lines)
    log.info('Updated %s = %s', key, value)
    return True


def render_controls():
    """Render profile/regime/trading controls."""
    # Flag to suppress notifications during sync
    syncing = {'active': True}

    with ui.row().classes('gap-4 flex-wrap items-end'):
        # Profile selector
        with ui.column().classes('gap-1'):
            with ui.row().classes('items-center gap-1'):
                ui.label('Profile').classes('text-xs text-gray-500 uppercase tracking-wide')
                with ui.icon('help_outline').classes('text-[12px] text-gray-600 cursor-help'):
                    ui.tooltip(
                        'Risk profile — controls leverage.\n'
                        'Zone A: Conservative (7-8x)\n'
                        'Zone B: Aggressive (15-18x)\n'
                        'Auto-selected by signal confidence.'
                    ).classes('text-[11px]')
            profile_select = ui.toggle(
                {
                    'ZONE_A': 'A · 7-8x',
                    'ZONE_B': 'B · 15-18x',
                },
                value='ZONE_A',
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
            with ui.row().classes('items-center gap-1'):
                ui.label('Regime').classes('text-xs text-gray-500 uppercase tracking-wide')
                with ui.icon('help_outline').classes('text-[12px] text-gray-600 cursor-help'):
                    ui.tooltip(
                        'Volatility detection method.\n'
                        'classic: ATR percentile\n'
                        'classic_cp: + change point\n'
                        'bocpd: Bayesian change point\n'
                        'full: HMM + BOCPD vote (recommended)'
                    ).classes('text-[11px]')
            regime_select = ui.toggle(
                {
                    'classic': 'ATR',
                    'classic_cp': 'ATR+CP',
                    'bocpd': 'BOCPD',
                    'full': 'Full',
                },
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
            with ui.row().classes('items-center gap-1'):
                ui.label('Trading').classes('text-xs text-gray-500 uppercase tracking-wide')
                with ui.icon('help_outline').classes('text-[12px] text-gray-600 cursor-help'):
                    ui.tooltip(
                        'Master switch for live trading.\n'
                        'OFF = signals evaluate but no orders placed.'
                    ).classes('text-[11px]')
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
            profile = d.get('active_profile', 'ZONE_A')
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


def render_service_panel():
    """Render AXC service control panel — start/stop/status + run once."""

    # State holders for UI elements
    status_labels = {}
    run_output = {'ref': None}

    with ui.expansion('Services', icon='dns').classes('w-full').props('dense header-class="text-[11px] p-1"'):
        with ui.column().classes('gap-1 w-full p-1'):
            for key, svc in svc_ctl.SERVICE_DEFS.items():
                with ui.row().classes('items-center gap-1 w-full'):
                    status_badge = ui.badge('...', color='grey').props('outline')
                    status_labels[key] = status_badge
                    ui.label(svc['display']).classes('text-[11px] min-w-[100px]')

                    # Start button
                    async def on_start(k=key):
                        ui.notify(f'Starting {svc_ctl.SERVICE_DEFS[k]["display"]}...', type='info')
                        ok = await run.io_bound(svc_ctl.start_service, k)
                        if ok:
                            ui.notify(f'{svc_ctl.SERVICE_DEFS[k]["display"]} started', type='positive')
                        else:
                            ui.notify(f'Failed to start {svc_ctl.SERVICE_DEFS[k]["display"]}', type='negative')
                        await refresh_status()

                    ui.button(icon='play_arrow', on_click=on_start).props(
                        'flat dense round color=green size=sm'
                    ).tooltip('Start')

                    # Stop button
                    async def on_stop(k=key):
                        ok = await run.io_bound(svc_ctl.stop_service, k)
                        if ok:
                            ui.notify(f'{svc_ctl.SERVICE_DEFS[k]["display"]} stopped', type='warning')
                        await refresh_status()

                    ui.button(icon='stop', on_click=on_stop).props(
                        'flat dense round color=red size=sm'
                    ).tooltip('Stop')

                    # Restart button
                    async def on_restart(k=key):
                        ui.notify(f'Restarting {svc_ctl.SERVICE_DEFS[k]["display"]}...', type='info')
                        ok = await run.io_bound(svc_ctl.restart_service, k)
                        if ok:
                            ui.notify(f'{svc_ctl.SERVICE_DEFS[k]["display"]} restarted', type='positive')
                        await refresh_status()

                    ui.button(icon='refresh', on_click=on_restart).props(
                        'flat dense round color=blue size=sm'
                    ).tooltip('Restart')

        ui.separator()

        # Run Once button
        with ui.row().classes('items-center gap-2'):
            ui.label('Run Cycle').classes('text-sm font-bold')

            async def on_run_dry():
                ui.notify('Running dry cycle...', type='info')
                output = await run.io_bound(svc_ctl.run_trader_cycle_once, True)
                if run_output['ref']:
                    run_output['ref'].set_content(f'```\n{output}\n```')

            async def on_run_live():
                # Confirmation dialog — live run places real orders
                with ui.dialog() as dlg, ui.card():
                    ui.label('Confirm LIVE Run').classes('text-lg font-bold')
                    ui.label('This will execute a real trading cycle with actual orders.').classes('text-sm')
                    with ui.row().classes('gap-2 justify-end'):
                        ui.button('Cancel', on_click=dlg.close).props('flat')

                        async def confirm_live():
                            dlg.close()
                            ui.notify('Running LIVE cycle...', type='warning')
                            output = await run.io_bound(svc_ctl.run_trader_cycle_once, False)
                            if run_output['ref']:
                                run_output['ref'].set_content(f'```\n{output}\n```')

                        ui.button('Confirm', on_click=confirm_live).props('color=deep-orange')
                dlg.open()

            ui.button('Dry Run', icon='science', on_click=on_run_dry).props(
                'dense no-caps color=blue-grey'
            )
            ui.button('Live Run', icon='bolt', on_click=on_run_live).props(
                'dense no-caps color=deep-orange'
            )

        # Output area
        run_output['ref'] = ui.markdown('').classes('text-xs max-h-[300px] overflow-auto w-full')

    # Status refresh function
    async def refresh_status():
        statuses = await run.io_bound(svc_ctl.get_all_status)
        for key, st in statuses.items():
            badge = status_labels.get(key)
            if not badge:
                continue
            if st['running']:
                badge.set_text(f'PID {st["pid"]}')
                badge._props['color'] = 'green'
            elif st.get('loaded'):
                badge.set_text(f'exit {st["exit_code"] or 0}')
                badge._props['color'] = 'orange'
            else:
                badge.set_text('unloaded')
                badge._props['color'] = 'grey'
            badge.update()

    # Refresh on load and every 15s
    ui.timer(1, refresh_status, once=True)
    ui.timer(15, refresh_status)
