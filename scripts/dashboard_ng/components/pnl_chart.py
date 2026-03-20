"""PnL chart — sparkline + cumulative, time range filter."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data


def render_pnl_chart():
    """Render PnL sparkline with ECharts."""
    ui.label('PNL HISTORY').classes('text-xs text-gray-500 uppercase tracking-wide')

    # Time range filter
    time_range = ui.toggle(['1H', '4H', '1D', '7D', 'ALL'], value='1D') \
        .props('dense no-caps size=sm color=indigo')

    chart = ui.echart({
        'backgroundColor': 'transparent',
        'tooltip': {'trigger': 'axis'},
        'legend': {'data': ['PnL'], 'textStyle': {'color': '#9ca3af'}, 'top': 5},
        'grid': {'left': 50, 'right': 20, 'top': 40, 'bottom': 30},
        'xAxis': {
            'type': 'category',
            'data': [],
            'axisLabel': {'color': '#6b7280', 'fontSize': 10},
            'axisLine': {'lineStyle': {'color': '#374151'}},
        },
        'yAxis': {
            'type': 'value',
            'axisLabel': {'color': '#6b7280', 'fontSize': 10, 'formatter': '${value}'},
            'splitLine': {'lineStyle': {'color': '#1f2937'}},
        },
        'series': [{
            'name': 'PnL',
            'type': 'line',
            'data': [],
            'smooth': True,
            'lineStyle': {'width': 2},
            'areaStyle': {
                'color': {
                    'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                    'colorStops': [
                        {'offset': 0, 'color': 'rgba(99, 102, 241, 0.3)'},
                        {'offset': 1, 'color': 'rgba(99, 102, 241, 0.02)'},
                    ],
                },
            },
            'itemStyle': {'color': '#6366f1'},
        }],
    }).classes('h-64 w-full')

    def update_chart():
        d = get_data()
        history = d.get('pnl_history', [])
        if not history:
            return

        # Apply time range filter
        range_val = time_range.value
        max_points = {
            '1H': 12,    # ~5min intervals
            '4H': 48,
            '1D': 288,
            '7D': 500,
            'ALL': len(history),
        }.get(range_val, 288)

        sliced = history[-max_points:]

        times = [p.get('time', '') for p in sliced]
        values = [p.get('pnl', 0) for p in sliced]

        chart.options['xAxis']['data'] = times
        chart.options['series'][0]['data'] = values

        # Color based on last value
        if values and values[-1] >= 0:
            chart.options['series'][0]['itemStyle']['color'] = '#22c55e'
            chart.options['series'][0]['areaStyle']['color']['colorStops'][0]['color'] = 'rgba(34, 197, 94, 0.3)'
        else:
            chart.options['series'][0]['itemStyle']['color'] = '#ef4444'
            chart.options['series'][0]['areaStyle']['color']['colorStops'][0]['color'] = 'rgba(239, 68, 68, 0.3)'

        chart.update()

    ui.timer(5, update_chart)
    time_range.on_value_change(lambda: update_chart())
