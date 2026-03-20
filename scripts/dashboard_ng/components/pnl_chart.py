"""PnL chart — sparkline + cumulative, time range filter."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data


def render_pnl_chart():
    """Render PnL sparkline with ECharts."""
    from scripts.dashboard_ng.theme import SECTION_HEADER
    ui.label('PNL HISTORY').classes(SECTION_HEADER)

    time_range = ui.toggle(['1H', '4H', '1D', '7D', 'ALL'], value='1D') \
        .props('dense no-caps size=sm color=blue-grey-6')

    chart = ui.echart({
        'darkMode': True,
        'backgroundColor': 'transparent',
        'tooltip': {'trigger': 'axis', 'backgroundColor': '#1e293b',
                    'borderColor': '#334155', 'textStyle': {'color': '#e2e8f0', 'fontSize': 11}},
        'grid': {'left': 50, 'right': 16, 'top': 20, 'bottom': 24},
        'xAxis': {
            'type': 'category', 'data': [],
            'axisLabel': {'color': '#64748b', 'fontSize': 10},
            'axisLine': {'lineStyle': {'color': '#1e2d45'}},
        },
        'yAxis': {
            'type': 'value',
            'axisLabel': {'color': '#64748b', 'fontSize': 10, 'formatter': '${value}'},
            'splitLine': {'lineStyle': {'color': '#1e2d45', 'type': 'dashed'}},
        },
        'series': [{
            'name': 'PnL', 'type': 'line', 'data': [], 'smooth': True,
            'showSymbol': False, 'lineStyle': {'width': 2},
            'areaStyle': {
                'color': {'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1,
                    'colorStops': [
                        {'offset': 0, 'color': 'rgba(59, 130, 246, 0.25)'},
                        {'offset': 1, 'color': 'rgba(59, 130, 246, 0.02)'},
                    ]},
            },
            'itemStyle': {'color': '#3b82f6'},
        }],
    }).classes('h-56 w-full')

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
