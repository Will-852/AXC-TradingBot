"""AXC Dashboard — Shared layout (header, sidebar, footer).

Every @ui.page calls create_layout() first for consistent chrome.
"""

from nicegui import app, ui

from . import state
from .theme import EXCHANGE_COLORS, SERVICE_STATUS

# Core services to show in sidebar
CORE_SERVICES = [
    ('ai.openclaw.scanner', 'Scanner'),
    ('ai.openclaw.tradercycle', 'Trader'),
    ('ai.openclaw.telegram', 'Telegram'),
    ('ai.openclaw.heartbeat', 'Heartbeat'),
    ('ai.openclaw.lightscan', 'LightScan'),
    ('ai.openclaw.newsbot', 'NewsBot'),
    ('ai.openclaw.report', 'Report'),
]

NAV_ITEMS = [
    ('Dashboard', '/', 'dashboard'),
    ('Backtest', '/backtest', 'candlestick_chart'),
    ('Polymarket', '/polymarket', 'casino'),
    ('Paper Trading', '/paper', 'science'),
    ('Docs', '/docs', 'description'),
]


def _exchange_badge(name: str, container):
    """Render a small exchange connection badge."""
    exchanges = state.get_exchanges()
    info = exchanges.get(name, {})
    status = info.get('status', 'disconnected')
    color = EXCHANGE_COLORS.get(status, EXCHANGE_COLORS['disconnected'])

    with container:
        with ui.row().classes('items-center gap-1'):
            ui.icon('circle').classes('text-[8px]').style(f'color: {color}')
            ui.label(name.upper()).classes('text-xs text-gray-400')


def _service_row(label: str, display_name: str, services_container):
    """Render a service status row with restart button."""
    services = state.get_services()
    info = services.get(label, {})
    is_running = info.get('pid') is not None
    color = SERVICE_STATUS['running'] if is_running else SERVICE_STATUS['stopped']
    status_text = 'Running' if is_running else 'Stopped'

    with services_container:
        with ui.row().classes('items-center justify-between w-full py-1'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('circle').classes('text-[8px]').style(f'color: {color}')
                ui.label(display_name).classes('text-sm text-gray-300')
            with ui.row().classes('items-center gap-2'):
                ui.label(status_text).classes('text-xs text-gray-500')
                ui.button(icon='refresh', on_click=lambda l=label: _restart_service(l)) \
                    .props('flat round dense size=xs color=grey-6') \
                    .tooltip(f'Restart {display_name}')


async def _restart_service(label: str):
    """Restart a LaunchAgent service."""
    from scripts.dashboard.services import handle_service_restart
    from nicegui import run
    result = await run.io_bound(handle_service_restart, {'label': label})
    if result.get('ok'):
        ui.notify(f'{label.split(".")[-1]} restarted', type='positive')
        # Refresh services state
        await state._update_services()
    else:
        ui.notify(f'Restart failed: {result.get("error")}', type='negative')


def create_layout(active_path: str = '/'):
    """Build the shared page layout. Call at the start of every @ui.page."""
    # Dark mode — default on, persist to user storage
    dark = ui.dark_mode()
    dark.bind_value(app.storage.user, 'dark_mode')
    if 'dark_mode' not in app.storage.user:
        app.storage.user['dark_mode'] = True

    # ── Header ──
    with ui.header().classes('items-center justify-between px-4 py-2 bg-[#0b1021] border-b border-gray-800'):
        with ui.row().classes('items-center gap-3'):
            ui.image('/svg/axc.svg').classes('w-8 h-8').on('click', lambda: ui.navigate.to('/'))
            ui.label('AXC').classes('text-xl font-bold text-white tracking-wide')

        # Exchange badges
        exchange_badges = ui.row().classes('items-center gap-4')
        for exch_name in ['aster', 'binance', 'hl']:
            _exchange_badge(exch_name, exchange_badges)

        with ui.row().classes('items-center gap-2'):
            ui.button(icon='brightness_6', on_click=dark.toggle) \
                .props('flat round color=white size=sm')

    # ── Sidebar ──
    with ui.left_drawer(value=True).classes('bg-[#0f1520] border-r border-gray-800 p-0') as drawer:
        # Navigation
        ui.label('NAVIGATION').classes('text-[10px] text-gray-500 font-bold tracking-widest px-4 pt-4 pb-2')
        for label_text, path, icon_name in NAV_ITEMS:
            is_active = path == active_path
            btn = ui.button(label_text, icon=icon_name,
                            on_click=lambda p=path: ui.navigate.to(p)) \
                .classes('w-full justify-start rounded-none') \
                .props('flat no-caps')
            if is_active:
                btn.classes('bg-indigo-900/30 text-indigo-400')
            else:
                btn.classes('text-gray-400 hover:text-white hover:bg-gray-800/50')

        ui.separator().classes('my-3 bg-gray-700')

        # Services status
        ui.label('SERVICES').classes('text-[10px] text-gray-500 font-bold tracking-widest px-4 pb-2')
        services_container = ui.column().classes('px-4 gap-0 w-full')
        for label, display in CORE_SERVICES:
            _service_row(label, display, services_container)

    # ── Footer ──
    with ui.footer().classes('bg-[#0b1021] border-t border-gray-800 py-1 px-4'):
        with ui.row().classes('items-center justify-between w-full'):
            ui.label('AXC Trading').classes('text-[10px] text-gray-600')
            # Data freshness indicator
            data_age = ui.label('').classes('text-[10px] text-gray-600')

            def update_freshness():
                import time
                ts = app.storage.general.get('dashboard_data_ts', 0)
                if ts:
                    age = int(time.time() - ts)
                    data_age.text = f'Data: {age}s ago'
                else:
                    data_age.text = 'Data: loading...'

            ui.timer(2, update_freshness)

    return drawer
