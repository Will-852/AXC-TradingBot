"""Backtest studio page — ECharts candlestick + parameter controls + results."""

import json
import logging

from nicegui import ui, run

log = logging.getLogger('axc.backtest')


def _bt_list():
    """Fetch saved backtest list (blocking)."""
    from scripts.dashboard.backtest import handle_bt_list
    return handle_bt_list()  # returns list directly


def _bt_klines(symbol: str, days: int, interval: str = '1h'):
    """Fetch klines for chart (blocking)."""
    from scripts.dashboard.backtest import handle_bt_klines
    qs = {'symbol': [symbol], 'days': [str(days)], 'interval': [interval]}
    code, data = handle_bt_klines(qs)
    if code != 200:
        return []
    return data.get('candles', [])


def _bt_results(filename: str):
    """Fetch backtest results (blocking)."""
    from scripts.dashboard.backtest import handle_bt_results
    qs = {'file': [filename]}
    code, data = handle_bt_results(qs)
    if code != 200:
        return {}
    return data


def _bt_run(symbol: str, days: int, params: dict):
    """Run a backtest (blocking, may take seconds)."""
    from scripts.dashboard.backtest import handle_bt_run
    body = json.dumps({
        'symbol': symbol, 'days': days,
        **params,
    })
    return handle_bt_run(body)


def render_backtest_page():
    """Render the backtest studio."""

    # ── Controls ──
    with ui.row().classes('gap-4 items-end flex-wrap'):
        symbol_select = ui.select(
            ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'XAGUSDT'],
            value='BTCUSDT', label='Symbol',
        ).classes('w-36').props('dense filled dark color=indigo')

        interval_select = ui.select(
            ['1h', '4h', '1d'],
            value='1h', label='Interval',
        ).classes('w-28').props('dense filled dark color=indigo')

        days_input = ui.number('Days', value=30, min=1, max=365, step=1) \
            .classes('w-28').props('dense filled dark')

        async def load_chart():
            chart_status.text = 'Loading...'
            candles = await run.io_bound(
                _bt_klines, symbol_select.value, int(days_input.value), interval_select.value
            )
            if candles:
                _update_candlestick(candles)
                chart_status.text = f'{len(candles)} candles loaded'
            else:
                chart_status.text = 'No data'

        ui.button('Load Chart', icon='candlestick_chart', on_click=load_chart) \
            .props('color=indigo')

        chart_status = ui.label('').classes('text-xs text-gray-500')

    ui.separator().classes('bg-gray-700 my-2')

    # ── Candlestick Chart (ECharts) ──
    chart = ui.echart({
        'backgroundColor': 'transparent',
        'tooltip': {
            'trigger': 'axis',
            'axisPointer': {'type': 'cross'},
        },
        'grid': [
            {'left': 60, 'right': 20, 'top': 30, 'height': '55%'},
            {'left': 60, 'right': 20, 'top': '72%', 'height': '18%'},
        ],
        'xAxis': [
            {
                'type': 'category',
                'data': [],
                'axisLabel': {'color': '#6b7280', 'fontSize': 9},
                'axisLine': {'lineStyle': {'color': '#374151'}},
            },
            {
                'type': 'category',
                'data': [],
                'gridIndex': 1,
                'axisLabel': {'show': False},
            },
        ],
        'yAxis': [
            {
                'type': 'value',
                'scale': True,
                'axisLabel': {'color': '#6b7280', 'fontSize': 9},
                'splitLine': {'lineStyle': {'color': '#1f2937'}},
            },
            {
                'type': 'value',
                'gridIndex': 1,
                'scale': True,
                'axisLabel': {'show': False},
                'splitLine': {'lineStyle': {'color': '#1f2937'}},
            },
        ],
        'series': [
            {
                'name': 'Price',
                'type': 'candlestick',
                'data': [],  # [open, close, low, high]
                'itemStyle': {
                    'color': '#22c55e',
                    'color0': '#ef4444',
                    'borderColor': '#22c55e',
                    'borderColor0': '#ef4444',
                },
            },
            {
                'name': 'Volume',
                'type': 'bar',
                'xAxisIndex': 1,
                'yAxisIndex': 1,
                'data': [],
                'itemStyle': {'color': '#374151'},
            },
        ],
        'dataZoom': [
            {'type': 'inside', 'xAxisIndex': [0, 1], 'start': 70, 'end': 100},
            {'type': 'slider', 'xAxisIndex': [0, 1], 'bottom': 5, 'height': 20},
        ],
    }).classes('h-[450px] w-full')

    def _update_candlestick(candles: list):
        from datetime import datetime
        times = []
        ohlc = []
        volumes = []
        for c in candles:
            ts = c.get('timestamp', 0)
            dt = datetime.utcfromtimestamp(ts / 1000)
            times.append(dt.strftime('%m/%d %H:%M'))
            # ECharts candlestick: [open, close, low, high]
            ohlc.append([c['open'], c['close'], c['low'], c['high']])
            volumes.append(c.get('volume', 0))

        chart.options['xAxis'][0]['data'] = times
        chart.options['xAxis'][1]['data'] = times
        chart.options['series'][0]['data'] = ohlc
        chart.options['series'][1]['data'] = volumes
        chart.update()

    ui.separator().classes('bg-gray-700 my-2')

    # ── Backtest Run ──
    with ui.expansion('Run Backtest', icon='science').classes('w-full'):
        with ui.row().classes('gap-4 flex-wrap items-end'):
            sl_mult = ui.number('SL Mult', value=0.8, min=0.1, max=5, step=0.1, format='%.1f') \
                .classes('w-24').props('dense outlined dark')
            tp_mult = ui.number('TP Mult', value=1.6, min=0.1, max=10, step=0.1, format='%.1f') \
                .classes('w-24').props('dense outlined dark')
            risk_pct = ui.number('Risk %', value=2.0, min=0.1, max=10, step=0.1, format='%.1f') \
                .classes('w-24').props('dense outlined dark')
            bt_leverage = ui.number('Leverage', value=5, min=1, max=50, step=1) \
                .classes('w-24').props('dense outlined dark')

        bt_status = ui.label('').classes('text-sm text-gray-500 mt-2')

        async def run_backtest():
            bt_status.text = 'Running backtest...'
            run_btn.set_enabled(False)
            try:
                result = await run.io_bound(_bt_run, symbol_select.value, int(days_input.value), {
                    'sl_mult': sl_mult.value,
                    'tp_mult': tp_mult.value,
                    'risk_pct': risk_pct.value,
                    'leverage': int(bt_leverage.value),
                })
                if isinstance(result, dict) and result.get('job_id'):
                    bt_status.text = f'Job submitted: {result["job_id"]} — polling...'
                    # Poll for completion
                    from scripts.dashboard.backtest import handle_bt_status
                    import asyncio
                    for _ in range(60):
                        await asyncio.sleep(2)
                        qs = {'job_id': [result['job_id']]}
                        code, status = await run.io_bound(handle_bt_status, qs)
                        if code == 200 and status.get('status') == 'done':
                            bt_status.text = 'Backtest complete!'
                            _show_results(status.get('result', {}))
                            break
                        elif code == 200 and status.get('status') == 'error':
                            bt_status.text = f'Error: {status.get("error")}'
                            break
                    else:
                        bt_status.text = 'Timeout — check manually'
                else:
                    bt_status.text = f'Unexpected: {result}'
            except Exception as e:
                bt_status.text = f'Error: {e}'
            finally:
                run_btn.set_enabled(True)

        run_btn = ui.button('Run', icon='play_arrow', on_click=run_backtest) \
            .props('color=green')

    ui.separator().classes('bg-gray-700 my-2')

    # ── Results Panel ──
    results_container = ui.column().classes('w-full gap-4')

    def _show_results(data: dict):
        results_container.clear()
        with results_container:
            stats = data.get('stats', data)
            if not stats:
                ui.label('No results').classes('text-gray-500')
                return

            # Stats cards
            with ui.row().classes('gap-4 flex-wrap'):
                for key, label, fmt in [
                    ('return_pct', 'Return', '{:+.2f}%'),
                    ('win_rate', 'Win Rate', '{:.1f}%'),
                    ('total_trades', 'Trades', '{}'),
                    ('max_drawdown_pct', 'Max DD', '{:.2f}%'),
                    ('sharpe', 'Sharpe', '{:.2f}'),
                    ('sortino', 'Sortino', '{:.2f}'),
                    ('profit_factor', 'PF', '{:.2f}'),
                ]:
                    val = stats.get(key, 0)
                    with ui.card().classes('p-3 bg-gray-800 border border-gray-700 min-w-[100px]'):
                        ui.label(label).classes('text-[10px] text-gray-500 uppercase')
                        try:
                            ui.label(fmt.format(val)).classes('text-lg font-bold')
                        except (ValueError, TypeError):
                            ui.label(str(val)).classes('text-lg font-bold')

            # Trades table
            trades = data.get('trades', [])
            if trades:
                ui.label('TRADES').classes('text-xs text-gray-500 uppercase tracking-wide')
                rows = []
                for t in trades[:50]:
                    rows.append({
                        'entry_time': t.get('entry_time', ''),
                        'exit_time': t.get('exit_time', ''),
                        'side': t.get('side', ''),
                        'entry_price': f"${t.get('entry_price', 0):.2f}",
                        'exit_price': f"${t.get('exit_price', 0):.2f}",
                        'pnl': f"${t.get('pnl', 0):.2f}",
                        'pnl_pct': f"{t.get('pnl_pct', 0):.2f}%",
                    })
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'entry_time', 'headerName': 'Entry', 'width': 140},
                        {'field': 'exit_time', 'headerName': 'Exit', 'width': 140},
                        {'field': 'side', 'width': 60},
                        {'field': 'entry_price', 'width': 100, 'type': 'rightAligned'},
                        {'field': 'exit_price', 'width': 100, 'type': 'rightAligned'},
                        {'field': 'pnl', 'width': 80, 'type': 'rightAligned',
                         'cellClassRules': {
                             'text-green-400': 'x.includes("$") && !x.includes("-")',
                             'text-red-400': 'x.includes("-")',
                         }},
                        {'field': 'pnl_pct', 'width': 80, 'type': 'rightAligned'},
                    ],
                    'rowData': rows,
                }).classes('h-64 ag-theme-balham-dark')

    # ── Saved Runs ──
    with ui.expansion('Saved Runs', icon='folder').classes('w-full'):
        saved_container = ui.column().classes('w-full')

        async def load_saved():
            files = await run.io_bound(_bt_list)
            saved_container.clear()
            with saved_container:
                if not files:
                    ui.label('No saved backtests').classes('text-gray-500 text-sm')
                    return

                for f in files[:20]:
                    if isinstance(f, dict):
                        fname = f.get('file', f.get('name', '?'))
                        symbol = f.get('symbol', '?')
                        days = f.get('days', '?')
                        stats = f.get('stats', {})
                        ret = stats.get('return_pct', 0) if isinstance(stats, dict) else 0

                        async def load_result(fn=fname):
                            data = await run.io_bound(_bt_results, fn)
                            _show_results(data)

                        with ui.row().classes('items-center gap-2 w-full'):
                            ret_color = 'text-green-400' if ret >= 0 else 'text-red-400'
                            ui.button(f'{symbol} {days}d', on_click=load_result) \
                                .props('flat dense no-caps color=grey-5 size=sm')
                            ui.label(f'{ret:+.1f}%').classes(f'text-xs {ret_color}')

        ui.timer(0.1, load_saved, once=True)
