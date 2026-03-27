"""KPI stat cards — Today PnL, Total PnL, Triggers, Positions.

Uses glow effects: green glow for profit, red for loss.
"""

from nicegui import ui

from scripts.dashboard_ng.state import get_data
from scripts.dashboard_ng.theme import (
    CARD_DARK, LABEL_XS, DATA_VALUE_XL, PNL_POS, PNL_NEG,
    TEXT_SECONDARY, TEXT_PRIMARY, GREEN, RED, BG_SURFACE, BORDER,
)


def _format_pnl(val) -> tuple[str, str]:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ('$0.00', f'text-[{TEXT_SECONDARY}]')
    color = PNL_POS if v >= 0 else PNL_NEG
    sign = '+' if v > 0 else ''
    return (f'{sign}${v:.2f}', color)


def _stat_card(title: str, key: str, formatter=None, icon: str = 'info'):
    """Stat card — returns update(d) function for external timer."""
    card = ui.card().classes(f'{CARD_DARK} min-w-[140px] flex-1')
    with card:
        with ui.row().classes('items-center gap-2 mb-1'):
            ui.icon(icon).classes(f'text-[14px] text-[{TEXT_SECONDARY}]')
            ui.label(title).classes(LABEL_XS)
        value_label = ui.label('—').classes(DATA_VALUE_XL)

    def update(d: dict):
        raw = d.get(key, '—')
        if formatter:
            text, color = formatter(raw)
            value_label.text = text
            value_label.classes(replace=f'{DATA_VALUE_XL} {color}')
            try:
                v = float(raw)
                if v > 0:
                    card.classes(replace=f'{CARD_DARK} min-w-[140px] flex-1 card-glow-green')
                elif v < 0:
                    card.classes(replace=f'{CARD_DARK} min-w-[140px] flex-1 card-glow-red')
                else:
                    card.classes(replace=f'{CARD_DARK} min-w-[140px] flex-1')
            except (TypeError, ValueError):
                pass
        else:
            value_label.text = str(raw)

    return update


def render_stats_row():
    """Render the 4-KPI stats row with a single shared timer."""
    updaters = []

    with ui.row().classes('gap-3 flex-wrap w-full'):
        updaters.append(_stat_card('Today PnL', 'today_pnl', formatter=_format_pnl, icon='trending_up'))
        updaters.append(_stat_card('Total PnL', 'total_pnl', formatter=_format_pnl, icon='account_balance'))
        updaters.append(_stat_card('Triggers', 'scan_count', icon='bolt'))

        # Positions card (custom logic)
        card = ui.card().classes(f'{CARD_DARK} min-w-[140px] flex-1')
        with card:
            with ui.row().classes('items-center gap-2 mb-1'):
                ui.icon('show_chart').classes(f'text-[14px] text-[{TEXT_SECONDARY}]')
                ui.label('POSITIONS').classes(LABEL_XS)
            pos_label = ui.label('0').classes(DATA_VALUE_XL)

        def update_pos(d: dict):
            positions = d.get('live_positions', [])
            n = len(positions)
            pos_label.text = str(n)
            if n > 0:
                pos_label.classes(replace=f'{DATA_VALUE_XL} text-[{GREEN}]')
                card.classes(replace=f'{CARD_DARK} min-w-[140px] flex-1 card-glow-blue')
            else:
                pos_label.classes(replace=f'{DATA_VALUE_XL} text-[{TEXT_PRIMARY}]')
                card.classes(replace=f'{CARD_DARK} min-w-[140px] flex-1')

        updaters.append(update_pos)

    # Single 2s timer — one get_data() for all 4 cards
    def _update_all_stats():
        d = get_data()
        for fn in updaters:
            fn(d)

    ui.timer(2, _update_all_stats)
