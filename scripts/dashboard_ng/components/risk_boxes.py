"""Risk status boxes — market mode, consecutive losses, daily loss, drawdown."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data
from scripts.dashboard_ng.theme import (
    CARD_DARK, SECTION_HEADER, LABEL_XS, LABEL_SM,
    DATA_VALUE_LG, DATA_VALUE_XL,
    GREEN, RED, AMBER, TEXT_SECONDARY, TEXT_MUTED,
)


def _risk_bar(label: str, current, limit, color: str = 'red'):
    """A progress bar showing current/limit ratio."""
    try:
        current_f = float(current)
        limit_f = float(limit)
    except (TypeError, ValueError):
        current_f, limit_f = 0, 1
    pct = min((current_f / limit_f) if limit_f else 0, 1.0)

    with ui.column().classes('gap-0.5'):
        with ui.row().classes('justify-between w-full'):
            ui.label(label).classes(LABEL_SM)
            ui.label(f'{current_f:.0f} / {limit_f:.0f}').classes(LABEL_XS + ' font-mono')
        ui.linear_progress(value=pct).props(f'color={color} size=6px rounded')


def render_risk_boxes():
    """Render risk status display."""
    with ui.row().classes('gap-2 flex-wrap w-full'):
        # Market mode + regime
        with ui.card().classes(f'{CARD_DARK} flex-1 min-w-[180px]'):
            ui.label('MARKET').classes(LABEL_XS + ' uppercase tracking-wider mb-1')
            mode_label = ui.label('—').classes(DATA_VALUE_LG)
            regime_label = ui.label('').classes(LABEL_XS + ' font-mono')

            def update_mode():
                d = get_data()
                mode = d.get('mode', '—')
                regime = d.get('regime_engine', '—')
                mode_label.text = mode
                colors = {'TREND': f'text-[{GREEN}]', 'RANGE': f'text-[{AMBER}]',
                          'SIDEWAYS': f'text-[{TEXT_SECONDARY}]'}
                mode_label.classes(replace=f'{DATA_VALUE_LG} {colors.get(mode, f"text-[{TEXT_SECONDARY}]")}')
                regime_label.text = f'Engine: {regime}'

            ui.timer(5, update_mode)

        # Risk meters
        with ui.card().classes(f'{CARD_DARK} flex-1 min-w-[280px]'):
            ui.label('RISK').classes(LABEL_XS + ' uppercase tracking-wider mb-1')
            risk_container = ui.column().classes('gap-2 w-full')

            def update_risk():
                d = get_data()
                risk = d.get('risk_status', {})
                consec = d.get('consecutive_losses', 0)
                risk_container.clear()
                with risk_container:
                    _risk_bar(
                        'Consecutive Losses',
                        consec,
                        risk.get('max_consecutive_losses', 5),
                        color='orange',
                    )
                    _risk_bar(
                        'Daily Loss ($)',
                        abs(risk.get('daily_loss', 0)),
                        risk.get('max_daily_loss', 5),
                        color='red',
                    )
                    cb = risk.get('trigger_cooldown', False) or risk.get('circuit_breaker', False)
                    if cb:
                        ui.badge('CIRCUIT BREAKER ACTIVE', color='red').classes('mt-1 font-mono text-[10px]')

            ui.timer(5, update_risk)

        # Drawdown
        with ui.card().classes(f'{CARD_DARK} flex-1 min-w-[180px]'):
            ui.label('DRAWDOWN').classes(LABEL_XS + ' uppercase tracking-wider mb-1')
            dd_current = ui.label('—').classes(f'{DATA_VALUE_XL} text-[{AMBER}]')
            dd_max = ui.label('').classes(LABEL_XS + ' font-mono')

            def update_dd():
                d = get_data()
                dd = d.get('drawdown', {})
                curr = dd.get('current_dd_pct', 0)
                mx = dd.get('max_dd_pct', 0)
                peak = dd.get('peak_value', 0)
                dd_current.text = f'{curr:.1f}%'
                dd_max.text = f'Max: {mx:.1f}%  |  Peak: ${peak:.2f}'

            ui.timer(5, update_dd)
