"""Persistent notification bell — stores alerts in app.storage.user.

24h TTL, survives page refresh, unread count shown on button text.
"""

import time
import logging
from datetime import datetime

from nicegui import app, ui

log = logging.getLogger('axc.notify')

MAX_NOTIFICATIONS = 100
TTL_HOURS = 24


def _get_notifications() -> list:
    notifs = app.storage.user.get('notifications', [])
    cutoff = time.time() - TTL_HOURS * 3600
    notifs = [n for n in notifs if n.get('ts', 0) > cutoff]
    app.storage.user['notifications'] = notifs
    return notifs


def _get_unread_count() -> int:
    last_read = app.storage.user.get('notif_last_read', 0)
    return sum(1 for n in _get_notifications() if n.get('ts', 0) > last_read)


def push_notification(msg: str, ntype: str = 'system'):
    """Push a notification. Called from state.py on alerts."""
    notifs = app.storage.user.get('notifications', [])
    notifs.append({'ts': time.time(), 'msg': msg, 'type': ntype})
    if len(notifs) > MAX_NOTIFICATIONS:
        notifs = notifs[-MAX_NOTIFICATIONS:]
    app.storage.user['notifications'] = notifs


def render_notification_bell():
    """Render bell button + dropdown menu. Call inside header row."""

    bell_btn = ui.button(icon='notifications') \
        .props('flat round color=white size=sm')

    # Dropdown menu anchored to bell button (replaces full-screen dialog)
    with ui.menu().props('anchor="bottom right" self="top right"') as menu:
        with ui.card().classes('p-0 w-[380px] max-h-[420px]'):
            with ui.row().classes('items-center justify-between px-3 py-2 bg-gray-800'):
                ui.label('Notifications').classes('text-sm font-bold')

                def clear_all():
                    app.storage.user['notifications'] = []
                    app.storage.user['notif_last_read'] = time.time()
                    menu.close()
                    _rebuild_list()

                ui.button('Clear All', on_click=clear_all) \
                    .props('flat dense size=xs color=grey-6')
                ui.button(icon='close', on_click=menu.close) \
                    .props('flat round dense size=xs color=grey-6')

            _list_container = ui.scroll_area().classes('w-full').style('max-height:360px')

    def _rebuild_list():
        """Rebuild notification list inside the scroll area."""
        _list_container.clear()
        with _list_container:
            notifs = _get_notifications()
            if not notifs:
                ui.label('No notifications').classes('text-gray-600 text-sm p-4')
            else:
                type_colors = {
                    'trade': 'text-green-400', 'circuit_breaker': 'text-red-400',
                    'news': 'text-blue-400', 'system': 'text-gray-400',
                }
                type_icons = {
                    'trade': 'swap_horiz', 'circuit_breaker': 'warning',
                    'news': 'article', 'system': 'info',
                }
                for n in reversed(notifs[-50:]):
                    ts_str = datetime.fromtimestamp(n.get('ts', 0)).strftime('%m-%d %H:%M')
                    ntype = n.get('type', 'system')
                    with ui.row().classes(
                        'items-start gap-2 px-3 py-2 w-full border-b border-gray-800/50'
                    ):
                        ui.icon(type_icons.get(ntype, 'info')).classes(
                            f'text-[14px] mt-0.5 {type_colors.get(ntype, "text-gray-400")}')
                        with ui.column().classes('gap-0 flex-1'):
                            ui.label(n.get('msg', '')).classes('text-[11px] text-gray-300')
                            ui.label(ts_str).classes('text-[9px] text-gray-600 font-mono')

    def on_bell_click():
        app.storage.user['notif_last_read'] = time.time()
        _rebuild_list()
        _update_badge()
        menu.open()

    bell_btn.on_click(on_bell_click)

    def _update_badge():
        unread = _get_unread_count()
        if unread > 0:
            bell_btn.props('color=amber')
        else:
            bell_btn.props('color=white')

    _update_badge()
    ui.timer(5, _update_badge)
