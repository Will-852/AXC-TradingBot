"""System health display — agent status, uptime, memory, suggest mode."""

import logging

from nicegui import ui, run

from scripts.dashboard_ng.state import get_data

log = logging.getLogger('axc.health')


def render_health_panel():
    """Render system health as an expandable panel."""
    with ui.expansion('System Health', icon='monitor_heart').classes('w-full'):
        health_container = ui.column().classes('w-full gap-2')

        async def refresh():
            from scripts.dashboard.handlers import handle_api_health
            result = await run.io_bound(handle_api_health)
            if isinstance(result, tuple):
                _, data = result
            else:
                data = result
            health_container.clear()
            with health_container:
                if not data or not isinstance(data, dict):
                    ui.label('Health check failed').classes('text-red-400 text-sm')
                    return

                # Agents
                agents = data.get('agents', {})
                if isinstance(agents, dict):
                    for name, info in agents.items():
                        status = info.get('status', '?') if isinstance(info, dict) else str(info)
                        ok = status in ('ok', 'running', 'alive')
                        color = '#22c55e' if ok else '#ef4444'
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('circle').classes('text-[6px]').style(f'color: {color}')
                            ui.label(name).classes('text-xs text-gray-400 min-w-[100px]')
                            ui.label(str(status)).classes('text-[10px] font-mono text-gray-500')

                # Uptime
                uptime = data.get('uptime', {})
                if isinstance(uptime, dict):
                    ui.separator().classes('bg-gray-800 my-1')
                    for k, v in uptime.items():
                        ui.label(f'{k}: {v}').classes('text-[10px] text-gray-500 font-mono')

                # Memory count
                mem = data.get('memory_count', 0)
                if mem:
                    ui.label(f'Memory embeddings: {mem}').classes('text-[10px] text-gray-500 font-mono')

        ui.timer(0.1, refresh, once=True)
        ui.timer(60, refresh)


def render_suggest_mode():
    """Show AI-recommended trading profile based on BTC 24h change."""
    with ui.expansion('Mode Suggestion', icon='psychology').classes('w-full'):
        suggest_container = ui.column().classes('w-full gap-1')

        async def refresh():
            from scripts.dashboard.handlers import handle_suggest_mode
            result = await run.io_bound(handle_suggest_mode)
            if isinstance(result, tuple):
                _, data = result
            else:
                data = result
            suggest_container.clear()
            with suggest_container:
                if not data or not isinstance(data, dict):
                    ui.label('Could not get suggestion').classes('text-gray-600 text-sm')
                    return

                suggested = data.get('suggested', '?')
                reason = data.get('reason', '')
                btc_change = data.get('btc_change_24h', data.get('btc_24h_change', 0))

                colors = {
                    'CONSERVATIVE': 'text-blue-400',
                    'BALANCED': 'text-yellow-400',
                    'AGGRESSIVE': 'text-green-400',
                }
                with ui.row().classes('items-center gap-2'):
                    ui.label('Suggested:').classes('text-xs text-gray-500')
                    ui.label(suggested).classes(f'text-sm font-bold {colors.get(suggested, "text-gray-400")}')

                if btc_change:
                    ui.label(f'BTC 24h: {btc_change:+.2f}%').classes('text-[10px] text-gray-500 font-mono')
                if reason:
                    ui.label(reason).classes('text-[10px] text-gray-600')

        ui.timer(0.1, refresh, once=True)
        ui.timer(300, refresh)  # every 5 min
