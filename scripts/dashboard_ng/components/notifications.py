"""Persistent notification bell — stores alerts in app.storage.user.

24h TTL, survives page refresh, unread badge count.
Types: trade, circuit_breaker, news, system.
"""

import time
import logging

from nicegui import app, ui

log = logging.getLogger('axc.notify')

MAX_NOTIFICATIONS = 100
TTL_HOURS = 24


def _get_notifications() -> list:
    """Get notifications from user storage, prune expired."""
    notifs = app.storage.user.get('notifications', [])
    cutoff = time.time() - TTL_HOURS * 3600
    notifs = [n for n in notifs if n.get('ts', 0) > cutoff]
    app.storage.user['notifications'] = notifs
    return notifs


def _get_unread_count() -> int:
    last_read = app.storage.user.get('notif_last_read', 0)
    return sum(1 for n in _get_notifications() if n.get('ts', 0) > last_read)


def push_notification(msg: str, ntype: str = 'system'):
    """Push a notification to user storage. Called from any component."""
    notifs = app.storage.user.get('notifications', [])
    notifs.append({
        'ts': time.time(),
        'msg': msg,
        'type': ntype,
    })
    # Trim to max
    if len(notifs) > MAX_NOTIFICATIONS:
        notifs = notifs[-MAX_NOTIFICATIONS:]
    app.storage.user['notifications'] = notifs


def render_notification_bell():
    """Render bell icon with badge + dropdown history. Place in header."""
    badge = ui.badge('0', color='red').classes('text-[9px]').style(
        'position:absolute; top:-4px; right:-4px; min-width:16px; height:16px; '
        'line-height:16px; padding:0 3px; display:none;'
    )

    # Panel must be defined before button (forward reference in closure)
    panel = ui.card().classes(
        'fixed top-[52px] right-[60px] z-[999] w-[360px] max-h-[400px] '
        'overflow-y-auto bg-gray-900 border border-gray-700 shadow-xl'
    )
    panel.set_visibility(False)

    notif_container = ui.column().classes('w-full gap-0')

    with panel:
        with ui.row().classes('items-center justify-between px-3 py-2 border-b border-gray-800'):
            ui.label('Notifications').classes('text-sm font-bold')
            ui.button('Clear', on_click=lambda: _clear_all()) \
                .props('flat dense size=xs color=grey-6')
        notif_container  # add as child of panel

    # Bell button — uses on_bell_click to mark as read
    def on_bell_click():
        panel.set_visibility(not panel.visible)
        if panel.visible:
            app.storage.user['notif_last_read'] = time.time()
            update_bell()

    ui.button(icon='notifications', on_click=on_bell_click) \
        .props('flat round color=white size=sm')

    def _clear_all():
        app.storage.user['notifications'] = []
        app.storage.user['notif_last_read'] = time.time()
        notif_container.clear()
        badge.text = '0'
        badge.style('display:none')

    def update_bell():
        notifs = _get_notifications()
        unread = _get_unread_count()

        badge.text = str(unread)
        badge.style(f'display:{"inline-block" if unread > 0 else "none"}')

        notif_container.clear()
        with notif_container:
            if not notifs:
                ui.label('No notifications').classes('text-gray-600 text-sm p-3')
                return

            for n in reversed(notifs[-30:]):  # newest first
                ts = n.get('ts', 0)
                from datetime import datetime
                time_str = datetime.fromtimestamp(ts).strftime('%H:%M')
                msg = n.get('msg', '')
                ntype = n.get('type', 'system')

                type_colors = {
                    'trade': 'text-green-400',
                    'circuit_breaker': 'text-red-400',
                    'news': 'text-blue-400',
                    'system': 'text-gray-400',
                }
                type_icons = {
                    'trade': 'swap_horiz',
                    'circuit_breaker': 'warning',
                    'news': 'article',
                    'system': 'info',
                }

                with ui.row().classes('items-start gap-2 px-3 py-1.5 w-full border-b border-gray-800/50 hover:bg-gray-800/30'):
                    ui.icon(type_icons.get(ntype, 'info')).classes(
                        f'text-[14px] mt-0.5 {type_colors.get(ntype, "text-gray-400")}')
                    with ui.column().classes('gap-0 flex-1'):
                        ui.label(msg).classes('text-[11px] text-gray-300')
                        ui.label(time_str).classes('text-[9px] text-gray-600')

    update_bell()
    ui.timer(5, update_bell)

    return panel, push_notification
