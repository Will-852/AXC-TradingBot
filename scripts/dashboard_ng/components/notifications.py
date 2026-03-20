"""Persistent notification bell — stores alerts in app.storage.user.

24h TTL, survives page refresh, unread badge count.
"""

import time
import logging

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
    notifs = app.storage.user.get('notifications', [])
    notifs.append({'ts': time.time(), 'msg': msg, 'type': ntype})
    if len(notifs) > MAX_NOTIFICATIONS:
        notifs = notifs[-MAX_NOTIFICATIONS:]
    app.storage.user['notifications'] = notifs


def render_notification_bell():
    """Render bell button inline (call inside header row). Panel at page root."""

    # Bell button — rendered inline where called
    bell_btn = ui.button(icon='notifications') \
        .props('flat round color=white size=sm')

    # Panel — create then move to page root so it's not inside header/sidebar
    panel = ui.card().classes(
        'w-[360px] max-h-[400px] overflow-y-auto '
        'bg-gray-900 border border-gray-700 shadow-xl'
    ).style('position:fixed; top:52px; right:60px; z-index:9999;')
    panel.set_visibility(False)
    panel.move()  # page root

    notif_container = ui.column().classes('w-full gap-0')

    with panel:
        with ui.row().classes('items-center justify-between px-3 py-2 border-b border-gray-800'):
            ui.label('Notifications').classes('text-sm font-bold')

            def clear_all():
                app.storage.user['notifications'] = []
                app.storage.user['notif_last_read'] = time.time()
                update_bell()

            ui.button('Clear', on_click=clear_all).props('flat dense size=xs color=grey-6')
        notif_container

    def toggle_panel():
        panel.set_visibility(not panel.visible)
        if panel.visible:
            app.storage.user['notif_last_read'] = time.time()
            update_bell()

    bell_btn.on_click(toggle_panel)

    def update_bell():
        notifs = _get_notifications()
        unread = _get_unread_count()

        # Update button badge via props
        if unread > 0:
            bell_btn.props(f'color=yellow-7')
            bell_btn.badge = unread
        else:
            bell_btn.props('color=white')

        notif_container.clear()
        with notif_container:
            if not notifs:
                ui.label('No notifications').classes('text-gray-600 text-sm p-3')
                return

            type_colors = {
                'trade': 'text-green-400', 'circuit_breaker': 'text-red-400',
                'news': 'text-blue-400', 'system': 'text-gray-400',
            }
            type_icons = {
                'trade': 'swap_horiz', 'circuit_breaker': 'warning',
                'news': 'article', 'system': 'info',
            }

            for n in reversed(notifs[-30:]):
                from datetime import datetime
                ts_str = datetime.fromtimestamp(n.get('ts', 0)).strftime('%H:%M')
                ntype = n.get('type', 'system')

                with ui.row().classes('items-start gap-2 px-3 py-1.5 w-full border-b border-gray-800/50'):
                    ui.icon(type_icons.get(ntype, 'info')).classes(
                        f'text-[14px] mt-0.5 {type_colors.get(ntype, "text-gray-400")}')
                    with ui.column().classes('gap-0 flex-1'):
                        ui.label(n.get('msg', '')).classes('text-[11px] text-gray-300')
                        ui.label(ts_str).classes('text-[9px] text-gray-600')

    update_bell()
    ui.timer(5, update_bell)
