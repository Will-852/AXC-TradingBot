"""KPI stat cards — Today PnL, Total PnL, Triggers, Positions."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data
from scripts.dashboard_ng.theme import (
    CARD_DARK, SECTION_HEADER, LABEL_XS,
    DATA_VALUE_XL, PNL_POS, PNL_NEG,
    TEXT_SECONDARY, TEXT_PRIMARY, GREEN, RED,
)


def _format_pnl(val) -> tuple[str, str]:
    """Return (formatted_str, color_class)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ('$0.00', f'text-[{TEXT_SECONDARY}]')
    color = PNL_POS if v >= 0 else PNL_NEG
    sign = '+' if v > 0 else ''
    return (f'{sign}${v:.2f}', color)


def _stat_card(title: str, key: str, formatter=None, icon: str = 'info'):
    """Create a single stat card that auto-updates."""
    with ui.card().classes(f'{CARD_DARK} min-w-[150px] flex-1'):
        with ui.row().classes('items-center gap-2 mb-1'):
            ui.icon(icon).classes(f'text-[14px] text-[{TEXT_SECONDARY}]')
            ui.label(title).classes(LABEL_XS + ' uppercase tracking-wider')
        value_label = ui.label('—').classes(DATA_VALUE_XL)

        def update():
            d = get_data()
            raw = d.get(key, '—')
            if formatter:
                text, color = formatter(raw)
                value_label.text = text
                value_label.classes(replace=f'{DATA_VALUE_XL} {color}')
            else:
                value_label.text = str(raw)

        ui.timer(2, update)


def render_stats_row():
    """Render the 4-KPI stats row."""
    with ui.row().classes('gap-2 flex-wrap w-full'):
        _stat_card('Today PnL', 'today_pnl', formatter=_format_pnl, icon='trending_up')
        _stat_card('Total PnL', 'total_pnl', formatter=_format_pnl, icon='account_balance')
        _stat_card('Triggers', 'scan_count', icon='bolt')

        # Open Positions count
        with ui.card().classes(f'{CARD_DARK} min-w-[150px] flex-1'):
            with ui.row().classes('items-center gap-2 mb-1'):
                ui.icon('show_chart').classes(f'text-[14px] text-[{TEXT_SECONDARY}]')
                ui.label('POSITIONS').classes(LABEL_XS + ' uppercase tracking-wider')
            pos_label = ui.label('0').classes(DATA_VALUE_XL)

            def update_pos():
                d = get_data()
                positions = d.get('live_positions', [])
                n = len(positions)
                pos_label.text = str(n)
                color = f'text-[{GREEN}]' if n > 0 else f'text-[{TEXT_PRIMARY}]'
                pos_label.classes(replace=f'{DATA_VALUE_XL} {color}')

            ui.timer(2, update_pos)
