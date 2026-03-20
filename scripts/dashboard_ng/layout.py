"""AXC Dashboard — Shared layout (header, sidebar, footer).

Every @ui.page calls create_layout() first for consistent chrome.
Design system: Data-Dense OLED Dark + Fira Code/Sans typography.
"""

from nicegui import app, ui

from . import state
from .theme import (
    FONTS_CSS, GLOBAL_CSS, BG_PRIMARY, BG_SURFACE, BORDER,
    TEXT_SECONDARY, TEXT_MUTED, TEXT_FAINT,
    GREEN, RED, ACCENT, SIDEBAR_WIDTH,
    HEADER_CLS, SIDEBAR_CLS, FOOTER_CLS, SECTION_HEADER,
)

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
    colors = {'connected': GREEN, 'disconnected': TEXT_MUTED, 'error': RED}
    color = colors.get(status, TEXT_MUTED)

    with container:
        with ui.row().classes('items-center gap-1'):
            ui.icon('circle').classes('text-[8px]').style(f'color: {color}')
            ui.label(name.upper()).classes(f'text-[11px] font-mono text-[{TEXT_SECONDARY}]')


def _service_row(label: str, display_name: str, services_container):
    """Render a service status row with restart button."""
    services = state.get_services()
    info = services.get(label, {})
    is_running = info.get('pid') is not None
    color = GREEN if is_running else RED
    status_text = 'ON' if is_running else 'OFF'

    with services_container:
        with ui.row().classes('items-center justify-between w-full py-0.5'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('circle').classes('text-[6px]').style(f'color: {color}')
                ui.label(display_name).classes(f'text-[12px] text-[{TEXT_SECONDARY}]')
            with ui.row().classes('items-center gap-1'):
                ui.label(status_text).classes(f'text-[10px] font-mono text-[{TEXT_MUTED}]')
                ui.button(icon='refresh', on_click=lambda l=label: _restart_service(l)) \
                    .props('flat round dense size=xs color=grey-7') \
                    .tooltip(f'Restart {display_name}')


async def _restart_service(label: str):
    """Restart a LaunchAgent service."""
    from scripts.dashboard.services import handle_service_restart
    from nicegui import run
    result = await run.io_bound(handle_service_restart, {'label': label})
    if result.get('ok'):
        ui.notify(f'{label.split(".")[-1]} restarted', type='positive')
        await state._update_services()
    else:
        ui.notify(f'Restart failed: {result.get("error")}', type='negative')


def create_layout(active_path: str = '/'):
    """Build the shared page layout. Call at the start of every @ui.page."""
    # Inject fonts + global CSS
    ui.add_head_html(FONTS_CSS)
    ui.add_css(GLOBAL_CSS)

    # Dark mode — default on, persist to user storage
    dark = ui.dark_mode()
    dark.bind_value(app.storage.user, 'dark_mode')
    if 'dark_mode' not in app.storage.user:
        app.storage.user['dark_mode'] = True

    # ── Sidebar (220px — IBKR standard, toggleable) ──
    drawer = ui.left_drawer(value=True, fixed=True) \
        .classes(f'{SIDEBAR_CLS} p-0') \
        .props(f'width={SIDEBAR_WIDTH} breakpoint=0')

    # ── Header (48px) ──
    with ui.header().classes(f'items-center justify-between px-3 py-0 h-[48px] {HEADER_CLS}'):
        with ui.row().classes('items-center gap-2'):
            ui.button(icon='menu', on_click=drawer.toggle) \
                .props('flat round color=white size=sm')
            ui.image('/svg/axc.svg').classes('w-5 h-5 cursor-pointer') \
                .on('click', lambda: ui.navigate.to('/'))
            ui.label('AXC').classes('text-[14px] font-bold text-white tracking-wider font-mono')

        # Exchange badges (clickable — opens connect dialog)
        exchange_badges = ui.row().classes('items-center gap-4')
        for exch_name in ['aster', 'binance', 'hl']:
            _exchange_badge(exch_name, exchange_badges)

        async def show_exchange_dialog():
            from scripts.dashboard_ng.components.exchange_connect import _show_connect_dialog, _disconnect, EXCHANGES
            with ui.dialog() as dlg, ui.card().classes('p-4 min-w-[400px]'):
                ui.label('Exchange Connections').classes('text-lg font-bold mb-3')
                exchanges = state.get_exchanges()
                for exch in EXCHANGES:
                    info = exchanges.get(exch['name'], {})
                    st = info.get('status', 'disconnected')
                    is_conn = st == 'connected'
                    bal = info.get('balance')
                    with ui.row().classes('items-center justify-between w-full py-2 border-b border-gray-800'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('circle').classes('text-[8px]').style(
                                f'color: {"#10b981" if is_conn else "#64748b"}')
                            ui.label(exch['label']).classes('text-sm font-bold')
                            if is_conn and bal is not None:
                                ui.label(f'${bal:.2f}').classes('text-sm font-mono text-emerald-400')
                        if is_conn:
                            ui.button('Disconnect', on_click=lambda e=exch: _disconnect(e)) \
                                .props('flat dense size=xs color=red')
                        else:
                            async def connect(e=exch, d=dlg):
                                d.close()
                                await _show_connect_dialog(e)
                            ui.button('Connect', on_click=connect) \
                                .props('flat dense size=xs color=blue')
                ui.button('Close', on_click=dlg.close).props('flat color=grey').classes('mt-2')
            dlg.open()

        ui.button(icon='settings', on_click=show_exchange_dialog) \
            .props('flat round color=white size=xs').tooltip('Exchange Connections')
        ui.button(icon='brightness_6', on_click=dark.toggle) \
            .props('flat round color=white size=xs')

    with drawer:
        # Navigation
        ui.label('NAVIGATION').classes(f'{SECTION_HEADER} px-3 pt-3 pb-1')
        for label_text, path, icon_name in NAV_ITEMS:
            is_active = path == active_path
            btn = ui.button(label_text, icon=icon_name,
                            on_click=lambda p=path: ui.navigate.to(p)) \
                .classes('w-full justify-start rounded-none text-[12px] h-[36px]') \
                .props('flat no-caps dense')
            if is_active:
                btn.classes(f'bg-[{ACCENT}]/12 text-[{ACCENT}]')
            else:
                btn.classes(f'text-[{TEXT_SECONDARY}] hover:text-white hover:bg-white/5')

        ui.separator().classes(f'my-2 bg-[{BORDER}]')

        # Services status
        ui.label('SERVICES').classes(f'{SECTION_HEADER} px-3 pb-0.5')
        services_container = ui.column().classes('px-3 gap-0 w-full')
        for label, display in CORE_SERVICES:
            _service_row(label, display, services_container)

    # ── Footer (compact) ──
    with ui.footer().classes(f'{FOOTER_CLS} py-0.5 px-4 h-[24px]'):
        with ui.row().classes('items-center justify-between w-full'):
            ui.label('AXC Trading').classes(f'text-[10px] text-[{TEXT_FAINT}] font-mono')
            data_age = ui.label('').classes(f'text-[10px] text-[{TEXT_FAINT}] font-mono')

            def update_freshness():
                import time
                ts = app.storage.general.get('dashboard_data_ts', 0)
                if ts:
                    age = int(time.time() - ts)
                    data_age.text = f'Data: {age}s ago'
                else:
                    data_age.text = 'Loading...'

            ui.timer(2, update_freshness)

    return drawer
