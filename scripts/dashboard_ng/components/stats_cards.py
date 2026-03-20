"""KPI stat cards — Today PnL, Total PnL, Triggers, Positions."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data


def _format_pnl(val) -> tuple[str, str]:
    """Return (formatted_str, color_class)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ('$0.00', 'text-gray-400')
    color = 'text-green-400' if v >= 0 else 'text-red-400'
    sign = '+' if v > 0 else ''
    return (f'{sign}${v:.2f}', color)


def _stat_card(title: str, key: str, formatter=None, icon: str = 'info'):
    """Create a single stat card that auto-updates."""
    with ui.card().classes('p-4 bg-gray-800 border border-gray-700 min-w-[160px]'):
        with ui.row().classes('items-center gap-2 mb-2'):
            ui.icon(icon).classes('text-gray-500 text-lg')
            ui.label(title).classes('text-xs text-gray-500 uppercase tracking-wide')
        value_label = ui.label('—').classes('text-2xl font-bold')

        def update():
            d = get_data()
            raw = d.get(key, '—')
            if formatter:
                text, color = formatter(raw)
                value_label.text = text
                value_label.classes(replace=f'text-2xl font-bold {color}')
            else:
                value_label.text = str(raw)

        ui.timer(2, update)


def render_stats_row():
    """Render the 4-KPI stats row."""
    with ui.row().classes('gap-4 flex-wrap'):
        _stat_card('Today PnL', 'today_pnl', formatter=_format_pnl, icon='trending_up')
        _stat_card('Total PnL', 'total_pnl', formatter=_format_pnl, icon='account_balance')

        # Triggers — show scan_count
        _stat_card('Triggers', 'scan_count', icon='bolt')

        # Open Positions count
        with ui.card().classes('p-4 bg-gray-800 border border-gray-700 min-w-[160px]'):
            with ui.row().classes('items-center gap-2 mb-2'):
                ui.icon('show_chart').classes('text-gray-500 text-lg')
                ui.label('POSITIONS').classes('text-xs text-gray-500 uppercase tracking-wide')
            pos_label = ui.label('0').classes('text-2xl font-bold')

            def update_pos():
                d = get_data()
                positions = d.get('live_positions', [])
                pos_label.text = str(len(positions))

            ui.timer(2, update_pos)
