"""Polymarket trading page."""

import logging

from nicegui import ui, run

log = logging.getLogger('axc.poly')


def _get_poly_data() -> dict:
    """Fetch polymarket data (blocking — wrap in run.io_bound)."""
    from scripts.dashboard.polymarket import handle_polymarket_data
    _, data = handle_polymarket_data()
    return data


def render_polymarket_page():
    """Render the full Polymarket page content."""
    poly_data = {'data': {}}

    async def refresh():
        poly_data['data'] = await run.io_bound(_get_poly_data)
        update_all()

    # KPI row
    with ui.row().classes('gap-4 flex-wrap'):
        kpi_labels = {}
        for key, label in [
            ('usdc_balance', 'Balance'),
            ('positions_count', 'Positions'),
            ('daily_pnl_pct', 'Daily PnL %'),
            ('total_exposure', 'Exposure'),
        ]:
            with ui.card().classes('p-3 bg-gray-800 border border-gray-700 min-w-[130px]'):
                ui.label(label).classes('text-[10px] text-gray-500 uppercase')
                kpi_labels[key] = ui.label('—').classes('text-xl font-bold')

    ui.separator().classes('bg-gray-700')

    # Controls row
    with ui.row().classes('gap-3 items-center'):
        async def run_cycle():
            from scripts.dashboard.polymarket import handle_polymarket_run_cycle
            ui.notify('Pipeline started...', type='info')
            result = await run.io_bound(handle_polymarket_run_cycle)
            if result.get('ok'):
                ui.notify('Pipeline complete', type='positive')
            else:
                ui.notify(f'Pipeline error: {result.get("error")}', type='negative')

        async def force_scan():
            from scripts.dashboard.polymarket import handle_polymarket_force_scan
            result = await run.io_bound(handle_polymarket_force_scan)
            ui.notify(f'Scan: {result.get("found", 0)} markets', type='info')

        async def toggle_mode():
            from scripts.dashboard.polymarket import handle_polymarket_set_mode
            d = poly_data['data']
            state = d.get('state', {})
            current = 'dry_run' if state.get('dry_run', True) else 'live'
            new_mode = 'live' if current == 'dry_run' else 'dry_run'
            await run.io_bound(handle_polymarket_set_mode, {'mode': new_mode})
            ui.notify(f'Mode → {new_mode}', type='positive')
            await refresh()

        ui.button('Run Cycle', icon='play_arrow', on_click=run_cycle).props('color=indigo')
        ui.button('Force Scan', icon='search', on_click=force_scan).props('color=grey-7')
        mode_btn = ui.button('Mode: —', icon='toggle_on', on_click=toggle_mode).props('color=orange')
        ui.button('Refresh', icon='refresh', on_click=refresh).props('flat color=grey')

    ui.separator().classes('bg-gray-700')

    # Positions table
    ui.label('POSITIONS').classes('text-xs text-gray-500 uppercase tracking-wide')
    positions_container = ui.column().classes('w-full')

    # PnL chart
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

    # Trades table
    ui.label('RECENT TRADES').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    trades_container = ui.column().classes('w-full')

    # Circuit breakers
    ui.label('CIRCUIT BREAKERS').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    cb_container = ui.column().classes('w-full')

    def update_all():
        d = poly_data['data']
        state = d.get('state', {})

        # KPIs — actual keys: usdc_balance, positions (list), total_exposure, daily_pnl_pct
        positions = state.get('positions', [])
        bal = state.get('usdc_balance', 0)
        exposure = state.get('total_exposure', 0)
        daily_pnl = state.get('daily_pnl_pct', 0)

        kpi_labels['usdc_balance'].text = f'${bal:.2f}' if isinstance(bal, (int, float)) else str(bal)
        kpi_labels['positions_count'].text = str(len(positions)) if isinstance(positions, list) else str(positions)
        kpi_labels['daily_pnl_pct'].text = f'{daily_pnl:.1f}%' if isinstance(daily_pnl, (int, float)) else str(daily_pnl)
        kpi_labels['total_exposure'].text = f'${exposure:.2f}' if isinstance(exposure, (int, float)) else str(exposure)

        # Mode button — actual key: dry_run (bool)
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
                        'title': (p.get('title', '') or '')[:40],
                        'side': p.get('side', ''),
                        'shares': p.get('shares', 0),
                        'avg_price': f"${p.get('avg_price', 0):.2f}",
                        'current': f"${p.get('current_price', 0):.2f}",
                        'pnl': f"${p.get('unrealized_pnl', 0):.2f}",
                        'pnl_pct': f"{p.get('unrealized_pnl_pct', 0):.1f}%",
                    })
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'title', 'headerName': 'Market', 'width': 250},
                        {'field': 'side', 'width': 60},
                        {'field': 'shares', 'width': 70, 'type': 'rightAligned'},
                        {'field': 'avg_price', 'headerName': 'Avg', 'width': 80, 'type': 'rightAligned'},
                        {'field': 'current', 'headerName': 'Now', 'width': 80, 'type': 'rightAligned'},
                        {'field': 'pnl', 'width': 80, 'type': 'rightAligned'},
                        {'field': 'pnl_pct', 'width': 70, 'type': 'rightAligned'},
                    ],
                    'rowData': rows,
                }).classes('h-40 ag-theme-balham-dark')
            else:
                ui.label('No positions').classes('text-gray-600 text-sm')

        # PnL chart
        pnl_series = d.get('pnl_series', [])
        if pnl_series:
            times = [p.get('time', '') for p in pnl_series]
            values = [p.get('pnl', 0) for p in pnl_series]
            pnl_chart.options['xAxis']['data'] = times
            pnl_chart.options['series'][0]['data'] = values
            pnl_chart.update()

        # Trades
        trades = d.get('trades', [])
        trades_container.clear()
        with trades_container:
            if trades:
                rows = [{
                    'time': t.get('time', ''),
                    'market': (t.get('market', t.get('slug', t.get('title', ''))) or '')[:35],
                    'side': t.get('side', ''),
                    'price': f"${t.get('price', 0):.2f}" if isinstance(t.get('price'), (int, float)) else str(t.get('price', '')),
                    'size': t.get('size', t.get('shares', '')),
                    'pnl': t.get('pnl', '—'),
                } for t in trades[:20]]
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'time', 'width': 140},
                        {'field': 'market', 'width': 220},
                        {'field': 'side', 'width': 60},
                        {'field': 'price', 'width': 80, 'type': 'rightAligned'},
                        {'field': 'size', 'width': 80, 'type': 'rightAligned'},
                        {'field': 'pnl', 'width': 80, 'type': 'rightAligned'},
                    ],
                    'rowData': rows,
                }).classes('h-48 ag-theme-balham-dark')
            else:
                ui.label('No trades').classes('text-gray-600 text-sm')

        # Circuit breakers
        cbs = d.get('circuit_breakers', [])
        cb_container.clear()
        with cb_container:
            if cbs:
                for cb in cbs:
                    name = cb.get('name', '?') if isinstance(cb, dict) else str(cb)
                    triggered = cb.get('triggered', False) if isinstance(cb, dict) else False
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('circle').classes('text-[8px]').style(
                            f'color: {"#ef4444" if triggered else "#22c55e"}')
                        ui.label(str(name)).classes('text-sm text-gray-300')
            else:
                ui.label('No circuit breakers').classes('text-gray-600 text-sm')

    # Initial load + timer
    ui.timer(0.1, refresh, once=True)
    ui.timer(30, refresh)
