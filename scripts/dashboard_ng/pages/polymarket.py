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


def _get_running_processes() -> list[dict]:
    """Find all running polymarket-related processes."""
    try:
        result = subprocess.run(
            ['pgrep', '-fl', 'polymarket'], capture_output=True, text=True, timeout=5
        )
        procs = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split(' ', 1)
            if len(parts) == 2:
                procs.append({'pid': parts[0], 'cmd': parts[1]})
        return procs
    except Exception:
        return []


def render_polymarket_page():
    """Render the full Polymarket page content."""
    poly_data = {'data': {}}

    async def refresh():
        poly_data['data'] = await run.io_bound(_get_poly_data)
        update_all()

    # ── KPI row ──
    with ui.row().classes('gap-3 flex-wrap'):
        kpi_labels = {}
        for key, label in [
            ('usdc_balance', 'Balance'),
            ('positions_count', 'Positions'),
            ('total_exposure', 'Exposure'),
            ('exposure_pct', 'Exposure %'),
            ('last_updated', 'Last Updated'),
        ]:
            with ui.card().classes('p-3 bg-gray-800 border border-gray-700 min-w-[120px]'):
                ui.label(label).classes('text-[10px] text-gray-500 uppercase')
                kpi_labels[key] = ui.label('—').classes('text-lg font-bold font-mono')

    ui.separator().classes('bg-gray-700')

    # ── Controls row ──
    with ui.row().classes('gap-3 items-center flex-wrap'):
        async def run_cycle():
            from scripts.dashboard.polymarket import handle_polymarket_run_cycle
            run_btn.set_enabled(False)
            ui.notify('Pipeline starting...', type='info')
            result = await run.io_bound(handle_polymarket_run_cycle)
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
            result = await run.io_bound(handle_polymarket_force_scan)
            if isinstance(result, tuple):
                _, data = result
            else:
                data = result
            found = data.get('found', data.get('scanned', 0))
            arbs = data.get('arb_opportunities', [])
            ui.notify(f'Scan: {found} markets, {len(arbs)} arb opportunities', type='info')
            scan_btn.set_enabled(True)

        async def toggle_mode():
            from scripts.dashboard.polymarket import handle_polymarket_set_mode
            d = poly_data['data']
            state = d.get('state', {})
            is_dry = state.get('dry_run', True)
            new_mode = 'live' if is_dry else 'dry_run'
            await run.io_bound(handle_polymarket_set_mode, {'mode': new_mode})
            ui.notify(f'Mode → {new_mode}', type='positive')
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

    ui.separator().classes('bg-gray-700')

    # ── Running Processes (PID) ──
    with ui.expansion('Running Processes', icon='terminal').classes('w-full'):
        proc_container = ui.column().classes('w-full gap-1')

        async def refresh_procs():
            procs = await run.io_bound(_get_running_processes)
            proc_container.clear()
            with proc_container:
                if not procs:
                    ui.label('No polymarket processes running').classes('text-gray-600 text-sm')
                else:
                    for p in procs:
                        with ui.row().classes('items-center gap-3 w-full py-0.5'):
                            ui.badge(p['pid'], color='blue').classes('font-mono text-[10px]')
                            ui.label(p['cmd']).classes('text-xs text-gray-400 font-mono')

        ui.timer(0.1, refresh_procs, once=True)
        ui.timer(15, refresh_procs)

    # ── Cycle Status ──
    with ui.expansion('Pipeline Status', icon='pending_actions').classes('w-full'):
        cycle_container = ui.column().classes('w-full gap-1')

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

        ui.timer(0.1, refresh_cycle, once=True)
        ui.timer(10, refresh_cycle)

    ui.separator().classes('bg-gray-700')

    # ── Positions table ──
    ui.label('POSITIONS').classes('text-xs text-gray-500 uppercase tracking-wide')
    positions_container = ui.column().classes('w-full')

    # ── PnL chart ──
    ui.label('PNL').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    pnl_chart = ui.echart({
        'backgroundColor': 'transparent',
        'tooltip': {'trigger': 'axis'},
        'grid': {'left': 50, 'right': 20, 'top': 20, 'bottom': 30},
        'xAxis': {'type': 'category', 'data': [],
                  'axisLabel': {'color': '#6b7280', 'fontSize': 10}},
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

    # ── Strategy Breakdown ──
    ui.label('STRATEGY BREAKDOWN').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    strategy_container = ui.column().classes('w-full')

    # ── Calibration ──
    ui.label('CALIBRATION').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    cal_container = ui.row().classes('gap-4')

    # ── Trades table ──
    ui.label('RECENT TRADES').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    trades_container = ui.column().classes('w-full')

    # ── Circuit breakers (with reset) ──
    ui.label('CIRCUIT BREAKERS').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    cb_container = ui.column().classes('w-full')

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

        kpi_labels['usdc_balance'].text = f'${bal:.2f}' if isinstance(bal, (int, float)) else str(bal)
        kpi_labels['positions_count'].text = str(len(positions)) if isinstance(positions, list) else str(positions)
        kpi_labels['total_exposure'].text = f'${exposure:.2f}' if isinstance(exposure, (int, float)) else str(exposure)
        kpi_labels['exposure_pct'].text = f'{exposure_pct:.1f}%' if isinstance(exposure_pct, (int, float)) else str(exposure_pct)
        kpi_labels['last_updated'].text = str(last_updated)

        # Mode button
        is_dry = state.get('dry_run', True)
        mode_str = 'DRY RUN' if is_dry else 'LIVE'
        mode_btn.text = f'Mode: {mode_str}'
        mode_btn.props(f'color={"orange" if is_dry else "green"}')

        # Positions table
        positions_container.clear()
        with positions_container:
            if positions and isinstance(positions, list):
                rows = []
                for p in positions:
                    rows.append({
                        'title': (p.get('title', '') or '')[:45],
                        'side': p.get('side', ''),
                        'shares': p.get('shares', 0),
                        'avg_price': f"${p.get('avg_price', 0):.3f}",
                        'current': f"${p.get('current_price', 0):.3f}",
                        'cost': f"${p.get('cost_basis', 0):.2f}",
                        'value': f"${p.get('market_value', 0):.2f}",
                        'pnl': f"${p.get('unrealized_pnl', 0):.2f}",
                        'pnl_pct': f"{p.get('unrealized_pnl_pct', 0):.1f}%",
                        'end_date': p.get('end_date', ''),
                    })
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'title', 'headerName': 'Market', 'width': 280},
                        {'field': 'side', 'width': 55},
                        {'field': 'shares', 'width': 65, 'type': 'rightAligned'},
                        {'field': 'avg_price', 'headerName': 'Avg', 'width': 75, 'type': 'rightAligned'},
                        {'field': 'current', 'headerName': 'Now', 'width': 75, 'type': 'rightAligned'},
                        {'field': 'cost', 'headerName': 'Cost', 'width': 70, 'type': 'rightAligned'},
                        {'field': 'value', 'headerName': 'Value', 'width': 70, 'type': 'rightAligned'},
                        {'field': 'pnl', 'width': 70, 'type': 'rightAligned'},
                        {'field': 'pnl_pct', 'width': 60, 'type': 'rightAligned'},
                        {'field': 'end_date', 'headerName': 'Expires', 'width': 90},
                    ],
                    'rowData': rows,
                }).classes('h-48 ag-theme-balham-dark')
            else:
                ui.label('No positions').classes('text-gray-600 text-sm')

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
                    for strat, count in sorted(breakdown.items(), key=lambda x: -x[1] if isinstance(x[1], (int, float)) else 0):
                        if isinstance(count, (int, float)) and count > 0:
                            ui.badge(f'{strat}: {count}', color='grey').classes('font-mono text-[11px]')
            else:
                ui.label('No strategy data').classes('text-gray-600 text-sm')

        # Calibration
        cal = d.get('calibration', {})
        cal_container.clear()
        with cal_container:
            brier = cal.get('brier')
            edge = cal.get('edge')
            if brier is not None:
                ui.label(f'Brier: {brier:.4f}').classes('text-sm font-mono text-gray-400')
            if edge is not None:
                color = 'text-green-400' if edge > 0 else 'text-red-400'
                ui.label(f'Edge: {edge:.4f}').classes(f'text-sm font-mono {color}')
            if brier is None and edge is None:
                ui.label('No calibration data').classes('text-gray-600 text-sm')

        # Trades
        trades = d.get('trades', [])
        trades_container.clear()
        with trades_container:
            if trades:
                rows = []
                for t in trades[:30]:
                    ts = t.get('timestamp', t.get('time', ''))
                    if isinstance(ts, str) and len(ts) > 16:
                        ts = ts[5:16]
                    price = t.get('price', t.get('avg_price', 0))
                    rows.append({
                        'time': ts,
                        'market': (t.get('title', t.get('market', t.get('slug', ''))) or '')[:35],
                        'side': t.get('side', ''),
                        'price': f"${float(price):.3f}" if isinstance(price, (int, float)) else str(price),
                        'size': t.get('size', t.get('shares', t.get('amount', ''))),
                        'pnl': t.get('pnl', t.get('realized_pnl', '—')),
                    })
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'time', 'width': 130},
                        {'field': 'market', 'width': 220},
                        {'field': 'side', 'width': 55},
                        {'field': 'price', 'width': 75, 'type': 'rightAligned'},
                        {'field': 'size', 'width': 70, 'type': 'rightAligned'},
                        {'field': 'pnl', 'width': 70, 'type': 'rightAligned'},
                    ],
                    'rowData': rows,
                }).classes('h-52 ag-theme-balham-dark')
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
                        ui.icon('circle').classes('text-[8px]').style(
                            f'color: {"#ef4444" if triggered else "#22c55e"}')
                        ui.label(str(name)).classes('text-sm text-gray-300 min-w-[100px]')
                        ui.label(f'{cb_state}' if isinstance(cb, dict) else '').classes('text-[10px] font-mono text-gray-500')
                        if failures:
                            ui.label(f'({failures} failures)').classes('text-[10px] text-yellow-400')
                        if triggered:
                            async def reset_cb(n=name):
                                from scripts.dashboard.polymarket import handle_polymarket_reset_cb
                                import json as _json
                                result = await run.io_bound(
                                    handle_polymarket_reset_cb, _json.dumps({'name': n})
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

    # ── Pipeline diagram ──
    ui.separator().classes('bg-gray-700 mt-4')
    from scripts.dashboard_ng.components.diagrams import render_polymarket_pipeline
    render_polymarket_pipeline()

    # Initial load + timer
    ui.timer(0.1, refresh, once=True)
    ui.timer(30, refresh)
