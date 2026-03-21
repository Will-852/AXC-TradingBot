"""Polymarket trading page — full feature parity with old dashboard.

Features: KPIs, positions table, PnL chart, trades, circuit breakers (with reset),
running processes (PID), cycle status polling, force scan with results,
check merge, strategy breakdown, calibration.
"""

import logging
import subprocess

from nicegui import ui, run

log = logging.getLogger('axc.poly')


def _get_poly_data() -> dict:
    from scripts.dashboard.polymarket import handle_polymarket_data
    _, data = handle_polymarket_data()
    return data


def _get_cycle_status() -> dict:
    from scripts.dashboard.polymarket import handle_polymarket_cycle_status
    _, data = handle_polymarket_cycle_status()
    return data



def render_polymarket_page():
    """Render the full Polymarket page content."""

    # ── Per-market focused view (distinct-baguette style) ──
    from scripts.dashboard_ng.components.poly_market_view import render_market_view
    render_market_view()

    ui.separator().classes('bg-gray-700 my-2')

    # ── Aggregate view (existing) ──
    poly_data = {'data': {}}

    async def refresh():
        poly_data['data'] = await run.io_bound(_get_poly_data)
        # Also fetch live balance to override stale state file
        try:
            from scripts.dashboard_ng.utils.poly_live import query_live
            live = await run.io_bound(query_live)
            if live and live.get('balance'):
                poly_data['live'] = live
        except Exception:
            pass
        update_all()

    # ── Connection Status (Polymarket auth) ──
    auth_container = ui.row().classes('items-center gap-3 w-full py-1 px-2 rounded '
                                      'border border-gray-800 bg-gray-900/50')

    def _check_auth() -> dict:
        """Check Polymarket auth status (local files only, no API calls)."""
        import os as _os
        axc = _os.environ.get('AXC_HOME', _os.path.expanduser('~/projects/axc-trading'))
        secrets_env = _os.path.join(axc, 'secrets', '.env')
        creds_cache = _os.path.join(axc, 'secrets', '.poly_api_creds.json')

        result = {'key': False, 'wallet': '', 'l2_creds': False, 'network': 'Polygon'}

        # Check env vars (loaded by dotenv at import time)
        pk = _os.getenv('POLY_PRIVATE_KEY', '')
        wallet = _os.getenv('POLY_WALLET_ADDRESS', '')

        # Fallback: read from .env file
        if not pk and _os.path.exists(secrets_env):
            try:
                with open(secrets_env) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('POLY_PRIVATE_KEY=') and len(line) > 20:
                            pk = 'set'
                        elif line.startswith('POLY_WALLET_ADDRESS=') and len(line) > 22:
                            wallet = line.split('=', 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass

        result['key'] = bool(pk)
        result['wallet'] = wallet

        # Check L2 cached creds
        if _os.path.exists(creds_cache):
            try:
                import json as _j
                with open(creds_cache) as f:
                    c = _j.load(f)
                result['l2_creds'] = bool(c.get('api_key') or c.get('apiKey'))
            except Exception:
                pass

        return result

    async def refresh_auth():
        try:
            auth = await run.io_bound(_check_auth)
        except Exception as e:
            auth_container.clear()
            with auth_container:
                ui.icon('error').classes('text-red-400')
                ui.label(f'Auth check failed: {e}').classes('text-[12px] text-red-400')
            return
        auth_container.clear()
        with auth_container:
            # Connection dot
            connected = auth['key'] and auth['l2_creds']
            dot_color = '#22c55e' if connected else '#f59e0b' if auth['key'] else '#ef4444'
            status_text = 'Connected' if connected else 'L1 Only' if auth['key'] else 'No Key'
            ui.icon('circle').classes('text-[9px]').style(f'color: {dot_color}')
            ui.label(status_text).classes('text-[12px] font-mono font-bold').style(f'color: {dot_color}')

            # Wallet address (truncated)
            if auth['wallet']:
                w = auth['wallet']
                short = f'{w[:6]}...{w[-4:]}' if len(w) > 10 else w
                ui.label(short).classes('text-[12px] font-mono text-gray-400')

            # Auth badges
            ui.badge('L1 Key', color='green' if auth['key'] else 'red').classes('text-[11px]')
            ui.badge('L2 API', color='green' if auth['l2_creds'] else 'grey').classes('text-[11px]')
            ui.badge(auth['network'], color='purple').classes('text-[11px]')

            # Spacer + Settings button
            ui.element('div').classes('flex-1')
            ui.button(icon='settings', on_click=open_settings) \
                .props('flat dense round size=sm color=grey-6') \
                .tooltip('Polymarket Credentials')

    async def open_settings():
        """Open credential settings dialog."""
        import os as _os
        axc = _os.environ.get('AXC_HOME', _os.path.expanduser('~/projects/axc-trading'))
        env_path = _os.path.join(axc, 'secrets', '.env')

        # Read current values (masked)
        current_pk = ''
        current_wallet = ''
        if _os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('POLY_PRIVATE_KEY='):
                        val = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if val:
                            current_pk = f'{val[:6]}...{val[-4:]}' if len(val) > 10 else '***'
                    elif line.startswith('POLY_WALLET_ADDRESS='):
                        current_wallet = line.split('=', 1)[1].strip().strip('"').strip("'")

        dlg = ui.dialog().props('persistent')
        dlg.move()
        with dlg, ui.card().classes('p-6 min-w-[420px]'):
            ui.label('Polymarket Credentials').classes('text-lg font-bold')
            ui.label('Saved to secrets/.env (localhost only)').classes('text-[11px] text-gray-500')

            ui.separator().classes('my-2')

            # Current status
            if current_pk:
                ui.label(f'Current Key: {current_pk}').classes('text-[12px] font-mono text-green-400')
            else:
                ui.label('No private key configured').classes('text-[12px] text-red-400')

            if current_wallet:
                ui.label(f'Wallet: {current_wallet}').classes('text-[12px] font-mono text-gray-400')

            ui.separator().classes('my-2')
            ui.label('Update Credentials').classes('text-sm font-bold text-gray-300')
            ui.label('Leave blank to keep current value.').classes('text-[11px] text-gray-600')

            pk_input = ui.input('Private Key (0x...)') \
                .props('type=password dense filled dark') \
                .classes('w-full')
            wallet_input = ui.input('Proxy Wallet Address (0x...)') \
                .props('dense filled dark') \
                .classes('w-full')
            if current_wallet:
                wallet_input.value = current_wallet

            with ui.row().classes('gap-3 mt-4 justify-end w-full'):
                ui.button('Cancel', on_click=lambda: dlg.submit(None)).props('flat color=grey')

                async def save_creds():
                    new_pk = pk_input.value.strip()
                    new_wallet = wallet_input.value.strip()

                    if new_pk and not new_pk.startswith('0x'):
                        ui.notify('Private key must start with 0x', type='negative')
                        return
                    if new_wallet and not new_wallet.startswith('0x'):
                        ui.notify('Wallet address must start with 0x', type='negative')
                        return

                    # Read existing .env, update only POLY_ lines
                    lines_out = []
                    found_pk = False
                    found_wallet = False
                    if _os.path.exists(env_path):
                        with open(env_path) as f:
                            for line in f:
                                if line.strip().startswith('POLY_PRIVATE_KEY=') and new_pk:
                                    lines_out.append(f'POLY_PRIVATE_KEY={new_pk}\n')
                                    found_pk = True
                                elif line.strip().startswith('POLY_WALLET_ADDRESS=') and new_wallet:
                                    lines_out.append(f'POLY_WALLET_ADDRESS={new_wallet}\n')
                                    found_wallet = True
                                else:
                                    lines_out.append(line)
                    if new_pk and not found_pk:
                        lines_out.append(f'POLY_PRIVATE_KEY={new_pk}\n')
                    if new_wallet and not found_wallet:
                        lines_out.append(f'POLY_WALLET_ADDRESS={new_wallet}\n')

                    import tempfile
                    fd, tmp = tempfile.mkstemp(dir=_os.path.dirname(env_path), suffix='.tmp')
                    with _os.fdopen(fd, 'w') as f:
                        f.writelines(lines_out)
                    _os.replace(tmp, env_path)

                    # Delete cached L2 creds (force re-derive on next client init)
                    creds_path = _os.path.join(axc, 'secrets', '.poly_api_creds.json')
                    if new_pk and _os.path.exists(creds_path):
                        _os.remove(creds_path)

                    ui.notify('Credentials saved. Restart dashboard to apply.', type='positive')
                    dlg.submit('saved')

                ui.button('Save', on_click=save_creds).props('color=green')

        dlg.open()
        result = await dlg
        if result == 'saved':
            await refresh_auth()

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
            ('last_updated', 'Last Updated'),
        ]:
            with ui.card().classes('p-3 bg-gray-800 border border-gray-700 min-w-[120px]'):
                ui.label(label).classes('text-[11px] text-gray-500 uppercase')
                kpi_labels[key] = ui.label('—').classes('text-lg font-bold font-mono')

    ui.separator().classes('bg-gray-700')

    # ── Controls row ──
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
                    # Start polling cycle status
                    await _poll_cycle()
            run_btn.set_enabled(True)
            await refresh()

        async def _poll_cycle():
            """Poll cycle_status until done."""
            import asyncio
            for _ in range(120):  # max 4 min
                await asyncio.sleep(2)
                status = await run.io_bound(_get_cycle_status)
                if not status.get('running', False):
                    if status.get('last_error'):
                        ui.notify(f'Pipeline error: {status["last_error"]}', type='negative')
                    else:
                        dur = status.get('last_duration', 0)
                        ui.notify(f'Pipeline complete ({dur:.1f}s)', type='positive')
                    _update_cycle_status(status)
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
            from scripts.dashboard.polymarket import handle_polymarket_set_mode
            d = poly_data['data']
            st = d.get('state', {})
            is_dry = st.get('dry_run', True)
            new_mode = 'live' if is_dry else 'dry_run'

            # Confirm before switching to LIVE
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

        run_btn = ui.button('Run Cycle', icon='play_arrow', on_click=run_cycle).props('color=indigo')
        scan_btn = ui.button('Force Scan', icon='search', on_click=force_scan).props('color=grey-7')
        mode_btn = ui.button('Mode: —', icon='toggle_on', on_click=toggle_mode).props('color=orange')
        ui.button('Check Merge', icon='merge_type', on_click=check_merge).props('flat color=grey-6')
        ui.button('Refresh', icon='refresh', on_click=refresh).props('flat color=grey')

    # ── Tabbed content (no scrolling needed) ──
    with ui.tabs().classes('w-full').props('dense align=left active-color=indigo indicator-color=indigo') as tabs:
        tab_live = ui.tab('Live', icon='account_balance_wallet')
        tab_analytics = ui.tab('Analytics', icon='analytics')
        tab_ops = ui.tab('Ops', icon='engineering')

    with ui.tab_panels(tabs, value=tab_live).classes('w-full'):

        # ━━━ TAB: Live ━━━
        with ui.tab_panel(tab_live):
            # Live Wallet Monitor
            ui.label('LIVE WALLET MONITOR').classes('text-xs text-gray-500 uppercase tracking-wide')
            live_container = ui.column().classes('w-full gap-1')
            live_ts = ui.label('').classes('text-[11px] text-gray-600 font-mono')

            ui.separator().classes('bg-gray-700 my-2')

            # Open Orders
            ui.label('OPEN ORDERS (LIVE)').classes('text-xs text-gray-500 uppercase tracking-wide')
            positions_container = ui.column().classes('w-full')

            # Recent Trades
            ui.label('RECENT TRADES (LIVE)').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
            trades_container = ui.column().classes('w-full')

        # ━━━ TAB: Analytics ━━━
        with ui.tab_panel(tab_analytics):
            # PnL chart
            ui.label('PNL').classes('text-xs text-gray-500 uppercase tracking-wide')
            pnl_chart = ui.echart({
                'backgroundColor': 'transparent',
                'tooltip': {'trigger': 'axis'},
                'grid': {'left': 50, 'right': 20, 'top': 20, 'bottom': 30},
                'xAxis': {'type': 'category', 'data': [],
                          'axisLabel': {'color': '#6b7280', 'fontSize': 11}},
                'yAxis': {'type': 'value',
                          'axisLabel': {'color': '#6b7280', 'formatter': '${value}'},
                          'splitLine': {'lineStyle': {'color': '#1f2937'}}},
                'series': [{'type': 'line', 'data': [], 'smooth': True,
                            'itemStyle': {'color': '#6366f1'}, 'areaStyle': {
                                'color': {'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                                          'colorStops': [
                                              {'offset': 0, 'color': 'rgba(99,102,241,0.3)'},
                                              {'offset': 1, 'color': 'rgba(99,102,241,0.02)'},
                                          ]}}}],
            }).classes('h-48 w-full')

            ui.separator().classes('bg-gray-700 my-2')

            # Strategy Breakdown
            ui.label('STRATEGY BREAKDOWN').classes('text-xs text-gray-500 uppercase tracking-wide')
            strategy_container = ui.column().classes('w-full')

            # Calibration
            ui.label('CALIBRATION').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
            cal_container = ui.row().classes('gap-4')

        # ━━━ TAB: Ops ━━━
        with ui.tab_panel(tab_ops):
            # Circuit breakers
            ui.label('CIRCUIT BREAKERS').classes('text-xs text-gray-500 uppercase tracking-wide')
            cb_container = ui.column().classes('w-full')

            ui.separator().classes('bg-gray-700 my-2')

            # Pipeline Status
            with ui.expansion('Pipeline Status', icon='pending_actions').classes('w-full'):
                cycle_container = ui.column().classes('w-full gap-1')

            ui.separator().classes('bg-gray-700 my-2')

            # Strategy Config
            from scripts.dashboard_ng.components.poly_config import render_poly_config
            render_poly_config()

    # ── Running Processes + Bot Control (always visible, outside tabs) ──
    ui.separator().classes('bg-gray-700')
    with ui.row().classes('items-center gap-2'):
        ui.label('RUNNING PROCESSES').classes('text-xs text-gray-500 uppercase tracking-wide')
        proc_count_badge = ui.badge('0', color='blue').classes('text-[11px] font-mono')

    from scripts.dashboard_ng.utils.poly_bot_control import (
        BOT_DEFS as _BOT_DEFS, start_bot as _start, stop_bot as _stop,
        get_running_processes as _get_procs,
    )
    from scripts.dashboard_ng.scheduler import read_schedules, write_schedules

    # Bot control: Start/Stop + Schedule per bot
    bot_btns = {}
    sched_inputs = {}
    schedules = read_schedules()

    for bot_name, script, args, key in _BOT_DEFS:
        sched = schedules.get(key, {})
        with ui.row().classes('items-center gap-2 w-full py-1 border-b border-gray-800'):
            # Start / Stop
            async def on_start(s=script, a=args, k=key, n=bot_name):
                ok = await run.io_bound(_start, s, a, k)
                ui.notify(f'{n} started' if ok else f'{n} already running', type='positive' if ok else 'info')
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

            # Schedule inputs
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
                ui.notify(f'Schedule saved', type='info')

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
                .props('dense color=indigo size=sm')

            sched_inputs[key] = {
                'start': start_input, 'stop': stop_input,
                'toggle': sched_toggle, 'name': bot_name,
            }

            bot_btns[key] = (start_b,)

    proc_container = ui.column().classes('w-full gap-1')

    # ── Command Log (always visible, shows running status) ──
    ui.separator().classes('bg-gray-700')
    with ui.row().classes('items-center gap-2'):
        ui.label('COMMAND LOG').classes('text-xs text-gray-500 uppercase tracking-wide')
    cmd_log = ui.column().classes('w-full max-h-32 overflow-y-auto gap-0')

    # ── Async refresh functions (outside tabs — closures reference containers above) ──

    async def refresh_live():
        from scripts.dashboard_ng.utils.poly_live import query_live
        from datetime import datetime
        try:
            data = await run.io_bound(query_live)
        except Exception as e:
            live_container.clear()
            with live_container:
                ui.label(f'CLOB error: {e}').classes('text-red-400 text-sm')
            return
        live_container.clear()
        with live_container:
            if not data:
                ui.label('Could not query CLOB').classes('text-gray-600 text-sm')
                return
            bal = data.get('balance', 0)
            with ui.row().classes('items-center gap-4'):
                with ui.column().classes('gap-0'):
                    ui.label('USDC BALANCE').classes('text-[11px] text-gray-600 uppercase')
                    ui.label(f'${bal:.2f}').classes('text-xl font-mono font-bold text-green-400')
                with ui.column().classes('gap-0'):
                    ui.label('OPEN ORDERS').classes('text-[11px] text-gray-600 uppercase')
                    ui.label(str(data.get('open_orders', 0))).classes('text-xl font-mono font-bold')
                with ui.column().classes('gap-0'):
                    ui.label('TOTAL TRADES').classes('text-[11px] text-gray-600 uppercase')
                    ui.label(str(data.get('total_trades', 0))).classes('text-xl font-mono font-bold')
        live_ts.text = f'Live: {datetime.now().strftime("%H:%M:%S")} | {data.get("total_trades", 0)} trades | {data.get("open_orders", 0)} orders'

    def _update_cycle_status(status: dict):
        cycle_container.clear()
        with cycle_container:
            running = status.get('running', False)
            with ui.row().classes('items-center gap-2'):
                if running:
                    ui.spinner(size='sm')
                    ui.label('Pipeline running...').classes('text-yellow-400 text-sm')
                else:
                    ui.icon('check_circle').classes('text-green-400 text-sm')
                    ui.label('Idle').classes('text-gray-400 text-sm')
            last_run = status.get('last_run', 0)
            if last_run:
                from datetime import datetime
                ts_str = datetime.fromtimestamp(last_run).strftime('%H:%M:%S')
                dur = status.get('last_duration', 0)
                ui.label(f'Last run: {ts_str} ({dur:.1f}s)').classes('text-xs text-gray-500 font-mono')
            err = status.get('last_error')
            if err:
                ui.label(f'Last error: {err}').classes('text-xs text-red-400')

    async def refresh_cycle():
        status = await run.io_bound(_get_cycle_status)
        _update_cycle_status(status)

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

        # Update bot button states (green outline + uptime when running)
        for key, (start_b,) in bot_btns.items():
            matched = next((p for p in procs if key in p.get('cmd', '') or key in p.get('cmd_full', '')), None)
            base_name = start_b.text.split(' ⏱')[0]
            if matched:
                start_b.props('color=green-8 outline')
                start_b.text = f'{base_name} ⏱{matched["uptime"]}'
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
                        ui.badge(f'PID {p["pid"]}', color='blue').classes('font-mono text-[12px]')
                        ui.label(f'⏱ {p["uptime"]}').classes('text-[12px] font-mono text-amber-400')
                        ui.label(p['cmd']).classes('text-[12px] text-gray-400 font-mono truncate')

    ui.timer(5, refresh_live, once=True)
    ui.timer(30, refresh_live)
    ui.timer(0.1, refresh_cycle, once=True)
    ui.timer(10, refresh_cycle)
    ui.timer(0.1, refresh_procs, once=True)
    ui.timer(15, refresh_procs)

    def update_all():
        d = poly_data['data']
        state = d.get('state', {})

        # KPIs
        positions = state.get('positions', [])
        bal = state.get('usdc_balance', 0)
        exposure = state.get('total_exposure', 0)
        daily_pnl = state.get('daily_pnl_pct', 0)
        exposure_pct = state.get('exposure_pct', 0)

        last_updated = state.get('last_updated', '—')

        # Calculate total PnL + win rate from mm_trades.jsonl (local file, not API)
        total_pnl = 0
        wins = 0
        resolved = 0
        try:
            import os as _os
            trades_file = _os.path.join(
                _os.environ.get('AXC_HOME', _os.path.expanduser('~/projects/axc-trading')),
                'polymarket', 'logs', 'mm_trades.jsonl'
            )
            if _os.path.exists(trades_file):
                import json as _json
                with open(trades_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            t = _json.loads(line)
                            pnl = t.get('pnl', t.get('realized_pnl', 0))
                            if isinstance(pnl, (int, float)) and pnl != 0:
                                total_pnl += pnl
                                resolved += 1
                                if pnl > 0:
                                    wins += 1
                        except _json.JSONDecodeError:
                            continue
        except Exception:
            pass
        win_rate = (wins / resolved * 100) if resolved > 0 else 0

        # Override ALL KPIs with live CLOB data when available
        live = poly_data.get('live', {})
        live_bal = live.get('balance')
        if live_bal and isinstance(live_bal, (int, float)):
            bal = live_bal

        from datetime import datetime
        kpi_labels['usdc_balance'].text = f'${bal:.2f}' if isinstance(bal, (int, float)) else str(bal)
        pnl_color = 'text-green-400' if total_pnl >= 0 else 'text-red-400'
        kpi_labels['total_pnl'].text = f'${total_pnl:+.2f}'
        kpi_labels['total_pnl'].classes(replace=f'text-lg font-bold font-mono {pnl_color}')
        kpi_labels['win_rate'].text = f'{win_rate:.0f}% ({wins}/{resolved})'

        # Positions = live open orders if available
        n_orders = live.get('open_orders', 0)
        if n_orders:
            kpi_labels['positions_count'].text = f'{n_orders} orders'
        else:
            kpi_labels['positions_count'].text = str(len(positions)) if isinstance(positions, list) else '0'

        kpi_labels['total_exposure'].text = f'${exposure:.2f}' if isinstance(exposure, (int, float)) else str(exposure)
        kpi_labels['exposure_pct'].text = f'{exposure_pct:.1f}%' if isinstance(exposure_pct, (int, float)) else str(exposure_pct)

        # Last Updated = NOW (live query time), not stale state file timestamp
        kpi_labels['last_updated'].text = datetime.now().strftime('%H:%M:%S')

        # Mode button
        is_dry = state.get('dry_run', True)
        mode_str = 'DRY RUN' if is_dry else 'LIVE'
        mode_btn.text = f'Mode: {mode_str}'
        mode_btn.props(f'color={"orange" if is_dry else "green"}')

        # Risk mode from mm_state.json (shown next to mode button)
        try:
            import os as _os
            import json as _json_rm
            _mm_path = _os.path.join(
                _os.environ.get('AXC_HOME', _os.path.expanduser('~/projects/axc-trading')),
                'polymarket', 'logs', 'mm_state.json'
            )
            if _os.path.exists(_mm_path):
                with open(_mm_path) as _f:
                    _mm = _json_rm.load(_f)
                risk_mode = _mm.get('_risk_mode', '')
                if risk_mode:
                    risk_color = 'text-red-400' if risk_mode == 'STOPPED' else 'text-amber-400' if risk_mode == 'PROTECTION' else 'text-green-400'
                    kpi_labels['exposure_pct'].text = f'{exposure_pct:.1f}% ({risk_mode})'
                    kpi_labels['exposure_pct'].classes(replace=f'text-lg font-bold font-mono {risk_color}')
        except Exception:
            pass

        # Positions — show LIVE orders from CLOB (state file positions are stale)
        live = poly_data.get('live', {})
        live_orders = live.get('orders', [])
        positions_container.clear()
        with positions_container:
            if live_orders:
                from datetime import datetime as _dt
                rows = []
                for o in live_orders:
                    try:
                        sz = f"{float(o.get('size', 0)):.2f}"
                    except (TypeError, ValueError):
                        sz = str(o.get('size', ''))
                    # Parse created_at time (can be int epoch or string)
                    ct = o.get('created', '')
                    try:
                        if isinstance(ct, (int, float)):
                            ct = _dt.fromtimestamp(ct).strftime('%m-%d %H:%M')
                        elif isinstance(ct, str) and ct.isdigit():
                            ct = _dt.fromtimestamp(int(ct)).strftime('%m-%d %H:%M')
                        elif isinstance(ct, str) and len(ct) > 16:
                            ct = ct[:16]
                    except (ValueError, OSError):
                        ct = str(ct)[:16]
                    rows.append({
                        'time': ct,
                        'side': o.get('side', ''),
                        'outcome': o.get('outcome', ''),
                        'size': sz,
                        'price': f"${o.get('price', '?')}",
                    })
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'time', 'headerName': 'Created', 'width': 110},
                        {'field': 'side', 'width': 50},
                        {'field': 'outcome', 'width': 55},
                        {'field': 'size', 'width': 65, 'type': 'rightAligned'},
                        {'field': 'price', 'width': 65, 'type': 'rightAligned'},
                    ],
                    'rowData': rows,
                    'headerHeight': 30, 'rowHeight': 28,
                }).classes('h-80 w-full ag-theme-balham-dark')
            else:
                ui.label('No open orders').classes('text-gray-600 text-sm')

        # PnL chart — use cumulative PnL, timestamp for time axis
        pnl_series = d.get('pnl_series', [])
        if pnl_series:
            times = []
            values = []
            for p in pnl_series:
                ts = p.get('timestamp', p.get('time', ''))
                if isinstance(ts, str) and len(ts) > 16:
                    ts = ts[5:16]  # "2026-03-19T14:46" → "03-19T14:46"
                times.append(ts)
                values.append(p.get('cumulative', p.get('pnl', 0)))
            pnl_chart.options['xAxis']['data'] = times
            pnl_chart.options['series'][0]['data'] = values
            pnl_chart.update()

        # Strategy breakdown
        breakdown = d.get('strategy_breakdown', {})
        strategy_container.clear()
        with strategy_container:
            if breakdown and isinstance(breakdown, dict):
                with ui.row().classes('gap-3 flex-wrap'):
                    for strat, count in sorted(breakdown.items(), key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0)):
                        if isinstance(count, (int, float)) and count > 0:
                            ui.badge(f'{strat}: {count}', color='grey').classes('font-mono text-[12px]')
            else:
                ui.label('No strategy data').classes('text-gray-600 text-sm')

        # Calibration
        cal = d.get('calibration', {})
        cal_container.clear()
        with cal_container:
            brier = cal.get('brier')
            edge = cal.get('edge')
            if isinstance(brier, (int, float)):
                ui.label(f'Brier: {brier:.4f}').classes('text-sm font-mono text-gray-400')
            if isinstance(edge, (int, float)):
                color = 'text-green-400' if edge > 0 else 'text-red-400'
                ui.label(f'Edge: {edge:.4f}').classes(f'text-sm font-mono {color}')
            elif isinstance(edge, dict):
                matched = edge.get('matched', 0)
                predictions = edge.get('edge_predictions_count', 0)
                ui.label(f'Edge: {matched} matched / {predictions} predictions').classes('text-sm font-mono text-gray-400')
            if brier is None and edge is None:
                ui.label('No calibration data').classes('text-gray-600 text-sm')

        # Trades — use LIVE CLOB trades (not stale state file)
        live_trades = live.get('recent_trades', [])
        trades_container.clear()
        with trades_container:
            if live_trades:
                from datetime import datetime as _dt
                rows = []
                for t in live_trades[:20]:
                    mt = t.get('match_time', '')
                    # Convert epoch seconds to human time
                    try:
                        if isinstance(mt, (int, float)) or (isinstance(mt, str) and mt.isdigit()):
                            mt = _dt.fromtimestamp(int(mt)).strftime('%m-%d %H:%M')
                        elif isinstance(mt, str) and len(mt) > 16:
                            mt = mt[:16]
                    except (ValueError, OSError):
                        pass
                    try:
                        sz = f"{float(t.get('size', 0)):.2f}"
                    except (TypeError, ValueError):
                        sz = str(t.get('size', ''))
                    rows.append({
                        'time': mt,
                        'side': t.get('side', ''),
                        'outcome': t.get('outcome', ''),
                        'size': sz,
                        'price': f"${t.get('price', '?')}",
                    })
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'time', 'headerName': 'Time', 'width': 140},
                        {'field': 'side', 'width': 50},
                        {'field': 'outcome', 'width': 60},
                        {'field': 'size', 'width': 65, 'type': 'rightAligned'},
                        {'field': 'price', 'width': 70, 'type': 'rightAligned'},
                    ],
                    'rowData': rows,
                    'headerHeight': 32, 'rowHeight': 30, 'domLayout': 'autoHeight',
                }).classes('w-full ag-theme-balham-dark')
            else:
                # Fallback to state file trades if live not available
                state_trades = d.get('trades', [])
                if state_trades:
                    ui.label('(State file trades — pipeline stale)').classes('text-[11px] text-yellow-400')
                    for t in state_trades[:5]:
                        ts = t.get('timestamp', t.get('time', ''))[:16] if t.get('timestamp') else ''
                        ui.label(f"{ts} {t.get('side','')} ${t.get('price','')}").classes('text-xs text-gray-500')
                else:
                    ui.label('No trades').classes('text-gray-600 text-sm')

        # Circuit breakers (with RESET button)
        # Actual shape: [{"service": "polymarket", "state": "closed", "failure_count": 0, ...}]
        cbs = d.get('circuit_breakers', [])
        cb_container.clear()
        with cb_container:
            if cbs:
                for cb in cbs:
                    if isinstance(cb, dict):
                        name = cb.get('service', cb.get('name', '?'))
                        cb_state = cb.get('state', 'closed')
                        failures = cb.get('failure_count', 0)
                        triggered = cb_state != 'closed'
                    else:
                        name = str(cb)
                        triggered = False
                        failures = 0

                    with ui.row().classes('items-center gap-2 w-full'):
                        ui.icon('circle').classes('text-[9px]').style(
                            f'color: {"#ef4444" if triggered else "#22c55e"}')
                        ui.label(str(name)).classes('text-sm text-gray-300 min-w-[100px]')
                        ui.label(f'{cb_state}' if isinstance(cb, dict) else '').classes('text-[11px] font-mono text-gray-500')
                        if failures:
                            ui.label(f'({failures} failures)').classes('text-[11px] text-yellow-400')
                        if triggered:
                            async def reset_cb(n=name):
                                from scripts.dashboard.polymarket import handle_polymarket_reset_cb
                                import json as _json
                                result = await run.io_bound(
                                    handle_polymarket_reset_cb, _json.dumps({'service': n})
                                )
                                if isinstance(result, tuple):
                                    _, rdata = result
                                else:
                                    rdata = result
                                if rdata.get('ok'):
                                    ui.notify(f'CB "{n}" reset', type='positive')
                                else:
                                    ui.notify(f'Reset failed: {rdata.get("error")}', type='negative')
                                await refresh()

                            ui.button('Reset', on_click=reset_cb) \
                                .props('flat dense size=xs color=red')
            else:
                ui.label('No circuit breakers').classes('text-gray-600 text-sm')

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

    # Initial load + timer (20s refresh — includes live balance query)
    ui.timer(0.1, refresh, once=True)
    ui.timer(20, refresh)
