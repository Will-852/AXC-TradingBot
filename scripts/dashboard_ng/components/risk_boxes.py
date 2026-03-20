"""Risk status boxes — consecutive losses, daily loss, circuit breaker, market mode."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data


def _risk_bar(label: str, current: float, limit: float, color: str = 'red'):
    """A progress bar showing current/limit ratio."""
    pct = min((current / limit * 100) if limit else 0, 100)
    with ui.column().classes('gap-1'):
        with ui.row().classes('justify-between w-full'):
            ui.label(label).classes('text-xs text-gray-400')
            ui.label(f'{current}/{limit}').classes('text-xs text-gray-500')
        ui.linear_progress(value=pct / 100).props(f'color={color} size=8px')


def render_risk_boxes():
    """Render risk status display."""
    with ui.row().classes('gap-4 flex-wrap w-full'):
        # Market mode + regime
        with ui.card().classes('p-4 bg-gray-800 border border-gray-700 flex-1 min-w-[200px]'):
            ui.label('MARKET').classes('text-xs text-gray-500 uppercase tracking-wide mb-2')
            mode_label = ui.label('—').classes('text-lg font-bold')
            regime_label = ui.label('').classes('text-xs text-gray-500')

            def update_mode():
                d = get_data()
                mode = d.get('mode', '—')
                regime = d.get('regime_engine', '—')
                mode_label.text = mode
                # Color by mode
                colors = {'TREND': 'text-green-400', 'RANGE': 'text-yellow-400',
                          'SIDEWAYS': 'text-gray-400'}
                mode_label.classes(replace=f'text-lg font-bold {colors.get(mode, "text-gray-400")}')
                regime_label.text = f'Engine: {regime}'

            ui.timer(5, update_mode)

        # Risk meters
        with ui.card().classes('p-4 bg-gray-800 border border-gray-700 flex-1 min-w-[300px]'):
            ui.label('RISK').classes('text-xs text-gray-500 uppercase tracking-wide mb-2')
            risk_container = ui.column().classes('gap-3 w-full')

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
                        'Daily Loss',
                        abs(risk.get('daily_loss', 0)),
                        risk.get('max_daily_loss', 5),
                        color='red',
                    )
                    # Circuit breaker
                    cb = risk.get('trigger_cooldown', False) or risk.get('circuit_breaker', False)
                    if cb:
                        ui.badge('CIRCUIT BREAKER', color='red').classes('mt-1')

            ui.timer(5, update_risk)

        # Drawdown
        with ui.card().classes('p-4 bg-gray-800 border border-gray-700 flex-1 min-w-[200px]'):
            ui.label('DRAWDOWN').classes('text-xs text-gray-500 uppercase tracking-wide mb-2')
            dd_current = ui.label('—').classes('text-2xl font-bold text-yellow-400')
            dd_max = ui.label('').classes('text-xs text-gray-500')

            def update_dd():
                d = get_data()
                dd = d.get('drawdown', {})
                curr = dd.get('current_dd_pct', 0)
                mx = dd.get('max_dd_pct', 0)
                dd_current.text = f'{curr:.1f}%'
                dd_max.text = f'Max: {mx:.1f}% | Peak: ${dd.get("peak_value", 0):.2f}'

            ui.timer(5, update_dd)
