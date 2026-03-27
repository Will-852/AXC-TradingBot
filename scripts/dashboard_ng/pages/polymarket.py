"""Polymarket trading page — full feature parity with old dashboard.

Features: KPIs, positions table, PnL chart, trades, circuit breakers (with reset),
running processes (PID), cycle status polling, force scan with results,
check merge, strategy breakdown, calibration.

Split into sub-modules:
  poly_helpers.py  — pure data functions (no UI)
  poly_auth_ui.py  — auth section (connection status + credentials dialog)
  poly_display.py  — update_all() + display helpers
"""

import logging

from nicegui import ui, run

from .poly_helpers import get_poly_data, get_cycle_status, fetch_data_api
from .poly_auth_ui import build_auth_section
from .poly_display import update_all, update_cycle_status

from scripts.dashboard_ng.theme import CHART_AXIS, CHART_GRID, INDIGO

log = logging.getLogger('axc.poly')


def render_polymarket_page():
    """Render the full Polymarket page content."""

    # ── Aggregate view data (initialise early — used by both columns) ──
    poly_data = {'data': {}}

    async def refresh():
        poly_data['data'] = await run.io_bound(get_poly_data)
        # Live CLOB balance
        try:
            from scripts.dashboard_ng.utils.poly_live import query_live
            live = await run.io_bound(query_live)
            if live and live.get('balance'):
                poly_data['live'] = live
        except Exception as e:
            log.warning('refresh CLOB balance failed: %s', e)
        # Data API: true portfolio value + positions
        try:
            import os, json
            api_data = await run.io_bound(fetch_data_api)
            if api_data:
                poly_data['_positions_value'] = api_data.get('positions_value', 0)
                poly_data['_open_count'] = api_data.get('open_count', 0)
                poly_data['_closed_wins'] = api_data.get('closed_wins', 0)
                poly_data['_closed_losses'] = api_data.get('closed_losses', 0)
                mm_path = os.path.join(
                    os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading')),
                    'polymarket', 'logs', 'mm_state.json'
                )
                if os.path.exists(mm_path):
                    with open(mm_path) as f:
                        mm = json.load(f)
                    poly_data['_initial_deposit'] = mm.get('initial_bankroll', 0)
        except Exception as e:
            log.warning('refresh data API / mm_state failed: %s', e)
        update_all(ctx)

    # ── Auth section ──
    _auth_container, refresh_auth = build_auth_section()
    ui.timer(0.5, refresh_auth, once=True)

    # ── KPI row ──
    with ui.row().classes('gap-3 flex-wrap'):
        kpi_labels = {}
        for key, label in [
            ('usdc_balance', 'Balance'),
            ('total_pnl', 'Total PnL'),
            ('win_rate', 'Win Rate'),
            ('positions_count', 'Positions'),
            ('total_exposure', 'Exposure'),
            ('exposure_pct', 'Exposure %'),
            ('open_orders', 'Open Orders'),
            ('total_trades', 'Total Trades'),
            ('last_updated', 'Last Updated'),
        ]:
            with ui.card().classes('p-3 bg-gray-800 border border-gray-700 min-w-[100px]'):
                ui.label(label).classes('text-[11px] text-gray-500 uppercase')
                kpi_labels[key] = ui.label('—').classes('text-lg font-bold font-mono')

    ui.separator().classes('bg-gray-700')

    # ── Controls row ──
    # 💰 toggle_mode: controls DRY↔LIVE — wrong = real money operations
    with ui.row().classes('gap-3 items-center flex-wrap'):
        async def run_cycle():
            from scripts.dashboard.polymarket import handle_polymarket_run_cycle
            run_btn.set_enabled(False)
            ui.notify('Pipeline starting...', type='info')
            log_cmd('Run Cycle triggered')
            result = await run.io_bound(handle_polymarket_run_cycle, '{}')
            if isinstance(result, tuple):
                code, data = result
                if code == 409:
                    ui.notify(data.get('error', 'Already running'), type='warning')
                elif data.get('ok'):
                    ui.notify('Pipeline started — polling for result...', type='info')
                    await _poll_cycle()
            run_btn.set_enabled(True)
            await refresh()

        async def _poll_cycle():
            """Poll cycle_status until done."""
            import asyncio
            for _ in range(120):  # max 4 min
                await asyncio.sleep(2)
                status = await run.io_bound(get_cycle_status)
                if not status.get('running', False):
                    if status.get('last_error'):
                        ui.notify(f'Pipeline error: {status["last_error"]}', type='negative')
                    else:
                        dur = status.get('last_duration', 0)
                        ui.notify(f'Pipeline complete ({dur:.1f}s)', type='positive')
                    update_cycle_status(cycle_container, status)
                    return
            ui.notify('Pipeline poll timeout', type='warning')

        async def force_scan():
            from scripts.dashboard.polymarket import handle_polymarket_force_scan
            scan_btn.set_enabled(False)
            result = await run.io_bound(handle_polymarket_force_scan, '{}')
            if isinstance(result, tuple):
                _, data = result
            else:
                data = result
            found = data.get('found', data.get('scanned', 0))
            arbs = data.get('arb_opportunities', [])
            ui.notify(f'Scan: {found} markets, {len(arbs)} arb opportunities', type='info')
            log_cmd(f'Force Scan: {found} markets, {len(arbs)} arbs')
            scan_btn.set_enabled(True)

        async def toggle_mode():
            """💰 Switch between DRY RUN and LIVE mode."""
            from scripts.dashboard.polymarket import handle_polymarket_set_mode
            d = poly_data['data']
            st = d.get('state', {})
            is_dry = st.get('dry_run', True)
            new_mode = 'live' if is_dry else 'dry_run'

            if new_mode == 'live':
                confirm_dlg = ui.dialog().props('persistent')
                confirm_dlg.move()
                with confirm_dlg, ui.card().classes('p-6'):
                    ui.label('Switch to LIVE mode?').classes('text-lg font-bold text-red-400')
                    ui.label('Real money will be used for trading.').classes('text-sm text-gray-400')
                    with ui.row().classes('gap-3 mt-4 justify-end'):
                        ui.button('Cancel', on_click=lambda: confirm_dlg.submit(False)).props('flat color=grey')
                        ui.button('Confirm LIVE', on_click=lambda: confirm_dlg.submit(True)).props('color=red')
                confirm_dlg.open()
                confirmed = await confirm_dlg
                if not confirmed:
                    return

            import json as _json_mode
            await run.io_bound(handle_polymarket_set_mode, _json_mode.dumps({'mode': new_mode}))
            ui.notify(f'Mode → {new_mode}', type='positive' if new_mode == 'dry_run' else 'warning')
            log_cmd(f'Mode switched to {new_mode}')
            await refresh()

        async def check_merge():
            from scripts.dashboard.polymarket import handle_polymarket_check_merge
            result = await run.io_bound(handle_polymarket_check_merge, '{}')
            if isinstance(result, tuple):
                _, data = result
            else:
                data = result
            mergeables = data.get('mergeables', [])
            reclaimable = data.get('total_reclaimable', 0)
            msg = data.get('message', '')
            if mergeables:
                ui.notify(f'{len(mergeables)} mergeable pairs, ${reclaimable:.2f} reclaimable', type='positive')
            else:
                ui.notify(msg or 'No mergeable positions', type='info')

        run_btn = ui.button('Run Cycle', icon='play_arrow', on_click=run_cycle).props('color=amber')
        scan_btn = ui.button('Force Scan', icon='search', on_click=force_scan).props('color=grey-7')
        mode_btn = ui.button('Mode: —', icon='toggle_on', on_click=toggle_mode).props('color=deep-orange')
        ui.button('Check Merge', icon='merge_type', on_click=check_merge).props('flat color=grey-6')
        ui.button('Refresh', icon='refresh', on_click=refresh).props('flat color=grey')

    # ── Split layout: Market (left) | Wallet (right) ──
    with ui.row().classes('w-full gap-4 items-start'):
        with ui.column().classes('gap-2').style('flex: 55 1 0%; min-width: 0'):
            from scripts.dashboard_ng.components.poly_market_view import render_market_view
            render_market_view()

        with ui.column().classes('gap-2').style(
            'flex: 45 1 0%; min-width: 0; position: sticky; top: 0; align-self: flex-start'
        ):
            live_container = ui.column().classes('hidden')
            live_ts = ui.label('').classes('hidden')
            ui.label('OPEN ORDERS (LIVE)').classes('text-xs text-gray-500 uppercase tracking-wide')
            positions_container = ui.column().classes('w-full')
            ui.label('RECENT TRADES (LIVE)').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
            trades_container = ui.column().classes('w-full max-h-96 overflow-y-auto')

    ui.separator().classes('bg-gray-700 my-2')

    # ── Analytics / Ops tabs ──
    with ui.tabs().classes('w-full').props('dense align=left active-color=amber indicator-color=amber') as tabs:
        tab_analytics = ui.tab('Analytics', icon='analytics')
        tab_ops = ui.tab('Ops', icon='engineering')

    with ui.tab_panels(tabs, value=tab_analytics).classes('w-full'):
        with ui.tab_panel(tab_analytics):
            ui.label('PNL').classes('text-xs text-gray-500 uppercase tracking-wide')
            pnl_chart = ui.echart({
                'backgroundColor': 'transparent',
                'tooltip': {'trigger': 'axis'},
                'grid': {'left': 50, 'right': 20, 'top': 20, 'bottom': 30},
                'xAxis': {'type': 'category', 'data': [],
                          'axisLabel': {'color': CHART_AXIS, 'fontSize': 11}},
                'yAxis': {'type': 'value',
                          'axisLabel': {'color': CHART_AXIS, 'formatter': '${value}'},
                          'splitLine': {'lineStyle': {'color': CHART_GRID}}},
                'series': [{'type': 'line', 'data': [], 'smooth': True,
                            'itemStyle': {'color': INDIGO}, 'areaStyle': {
                                'color': {'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                                          'colorStops': [
                                              {'offset': 0, 'color': 'rgba(99,102,241,0.3)'},
                                              {'offset': 1, 'color': 'rgba(99,102,241,0.02)'},
                                          ]}}}],
            }).classes('h-48 w-full')

            ui.separator().classes('bg-gray-700 my-2')
            ui.label('STRATEGY BREAKDOWN').classes('text-xs text-gray-500 uppercase tracking-wide')
            strategy_container = ui.column().classes('w-full')
            ui.label('CALIBRATION').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
            cal_container = ui.row().classes('gap-4')

        with ui.tab_panel(tab_ops):
            ui.label('CIRCUIT BREAKERS').classes('text-xs text-gray-500 uppercase tracking-wide')
            cb_container = ui.column().classes('w-full')
            ui.separator().classes('bg-gray-700 my-2')
            with ui.expansion('Pipeline Status', icon='pending_actions').classes('w-full'):
                cycle_container = ui.column().classes('w-full gap-1')
            ui.separator().classes('bg-gray-700 my-2')
            from scripts.dashboard_ng.components.poly_config import render_poly_config
            render_poly_config()

    # ── Build the shared context dict for display functions ──
    ctx = {
        'poly_data': poly_data,
        'kpi_labels': kpi_labels,
        'mode_btn': mode_btn,
        'positions_container': positions_container,
        'pnl_chart': pnl_chart,
        'strategy_container': strategy_container,
        'cal_container': cal_container,
        'trades_container': trades_container,
        'cb_container': cb_container,
        'refresh_fn': refresh,
    }

    # ── Running Processes + Bot Control ──
    ui.separator().classes('bg-gray-700')
    with ui.row().classes('items-center gap-2'):
        ui.label('RUNNING PROCESSES').classes('text-xs text-gray-500 uppercase tracking-wide')
        proc_count_badge = ui.badge('0', color='amber').classes('text-[11px] font-mono')

    from scripts.dashboard_ng.utils.poly_bot_control import (
        BOT_DEFS as _BOT_DEFS, start_bot as _start, stop_bot as _stop,
        get_running_processes as _get_procs,
    )
    from scripts.dashboard_ng.scheduler import read_schedules, write_schedules

    bot_btns = {}
    sched_inputs = {}
    schedules = read_schedules()

    for bot_name, script, args, key in _BOT_DEFS:
        sched = schedules.get(key, {})
        with ui.row().classes('items-center gap-2 w-full py-1 border-b border-gray-800'):
            async def on_start(s=script, a=args, k=key, n=bot_name):
                ok = await run.io_bound(_start, s, a, k)
                ui.notify(f'{n} started' if ok else f'{n} already running',
                          type='positive' if ok else 'info')
                log_cmd(f'Started {n}' if ok else f'{n} already running')
                await run.io_bound(lambda: __import__('time').sleep(2))
                await refresh_procs()

            async def on_stop(s=script, k=key, n=bot_name):
                killed = await run.io_bound(_stop, s, k)
                if killed:
                    ui.notify(f'{n} stopped ({killed})', type='warning')
                    log_cmd(f'Stopped {n}')
                else:
                    ui.notify(f'{n} not running', type='info')
                await run.io_bound(lambda: __import__('time').sleep(1))
                await refresh_procs()

            start_b = ui.button(f'{bot_name}', icon='play_arrow', on_click=on_start) \
                .props('dense size=sm color=green-8')
            ui.button(icon='stop', on_click=on_stop) \
                .props('dense size=sm color=red-8')

            ui.label('|').classes('text-gray-700')

            async def on_sched_change(k=key):
                s = read_schedules()
                s.setdefault(k, {})
                si = sched_inputs[k]
                s[k]['start'] = si['start'].value or ''
                s[k]['stop'] = si['stop'].value or ''
                s[k]['enabled'] = si['toggle'].value
                s[k]['name'] = si['name']
                await run.io_bound(write_schedules, s)
                ui.notify('Schedule saved', type='info')

            start_input = ui.input(placeholder='Start HH:MM') \
                .props('dense filled dark mask="##:##"') \
                .classes('w-20') \
                .on('blur', lambda e, k=key: on_sched_change(k))
            start_input.value = sched.get('start', '')

            ui.label('→').classes('text-gray-600')

            stop_input = ui.input(placeholder='Stop HH:MM') \
                .props('dense filled dark mask="##:##"') \
                .classes('w-20') \
                .on('blur', lambda e, k=key: on_sched_change(k))
            stop_input.value = sched.get('stop', '')

            sched_toggle = ui.switch('', value=sched.get('enabled', False),
                                     on_change=lambda e, k=key: on_sched_change(k)) \
                .props('dense color=amber size=sm')

            sched_inputs[key] = {
                'start': start_input, 'stop': stop_input,
                'toggle': sched_toggle, 'name': bot_name,
            }
            bot_btns[key] = (start_b,)

    proc_container = ui.column().classes('w-full gap-1')

    # ── Command Log ──
    ui.separator().classes('bg-gray-700')
    with ui.row().classes('items-center gap-2'):
        ui.label('COMMAND LOG').classes('text-xs text-gray-500 uppercase tracking-wide')
    cmd_log = ui.column().classes('w-full max-h-32 overflow-y-auto gap-0')

    # ── Async refresh helpers ──

    async def refresh_live():
        from scripts.dashboard_ng.utils.poly_live import query_live
        try:
            data = await run.io_bound(query_live)
        except Exception as e:
            log.warning('refresh_live CLOB query failed: %s', e)
            return
        if not data:
            return
        bal = data.get('balance', 0)
        if isinstance(bal, (int, float)):
            kpi_labels['usdc_balance'].text = f'${bal:.2f}'
            poly_data['live'] = data
        kpi_labels['open_orders'].text = str(data.get('open_orders', 0))
        kpi_labels['total_trades'].text = str(data.get('total_trades', 0))

    async def refresh_cycle():
        status = await run.io_bound(get_cycle_status)
        update_cycle_status(cycle_container, status)

    async def refresh_procs():
        try:
            procs = await run.io_bound(_get_procs)
        except Exception as e:
            proc_container.clear()
            with proc_container:
                ui.label(f'Error: {e}').classes('text-red-400 text-sm')
            return
        proc_count_badge.text = str(len(procs))
        proc_count_badge._props['color'] = 'green' if procs else 'grey'
        proc_count_badge.update()

        for key, (start_b,) in bot_btns.items():
            matched = next(
                (p for p in procs if key in p.get('cmd', '') or key in p.get('cmd_full', '')),
                None,
            )
            base_name = start_b.text.split(' ⏱')[0]
            if matched:
                start_b.props('color=green-8 outline')
            else:
                start_b.props('color=green-8')
            start_b.text = base_name

        proc_container.clear()
        with proc_container:
            if not procs:
                ui.label('No polymarket processes running').classes('text-gray-600 text-sm')
            else:
                for p in procs:
                    with ui.row().classes('items-center gap-2 w-full py-0.5'):
                        ui.badge(f'PID {p["pid"]}', color='amber').classes('font-mono text-[12px]')
                        up = p['uptime'].strip()
                        if up and up != '00:00' and not up.startswith('00:0'):
                            ui.label(up).classes('text-[12px] font-mono text-amber-400')
                        ui.label(p['cmd']).classes('text-[12px] text-gray-400 font-mono truncate')

    def log_cmd(msg: str):
        """Append a timestamped command to the log."""
        from datetime import datetime
        ts = datetime.now().strftime('%H:%M:%S')
        with cmd_log:
            with ui.row().classes('gap-2 py-0.5'):
                ui.label(ts).classes('text-[11px] text-gray-600 font-mono min-w-[60px]')
                ui.label(msg).classes('text-[12px] text-gray-400')

    # ── Pipeline diagram ──
    ui.separator().classes('bg-gray-700 mt-4')
    from scripts.dashboard_ng.components.diagrams import render_polymarket_pipeline
    render_polymarket_pipeline()

    # ── Timers ──
    ui.timer(5, refresh_live, once=True)
    ui.timer(30, refresh_live)
    ui.timer(0.1, refresh_cycle, once=True)
    ui.timer(10, refresh_cycle)
    ui.timer(0.1, refresh_procs, once=True)
    ui.timer(15, refresh_procs)
    ui.timer(0.1, refresh, once=True)
    ui.timer(20, refresh)
