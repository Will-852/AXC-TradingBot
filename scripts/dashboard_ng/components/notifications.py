"""Persistent notification bell — stores alerts in app.storage.user.

24h TTL, survives page refresh, unread count shown on button text.
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
    """Push a notification. Called from state.py on alerts."""
    notifs = app.storage.user.get('notifications', [])
    notifs.append({'ts': time.time(), 'msg': msg, 'type': ntype})
    if len(notifs) > MAX_NOTIFICATIONS:
        notifs = notifs[-MAX_NOTIFICATIONS:]
    app.storage.user['notifications'] = notifs


def render_notification_bell():
    """Render bell button + dropdown panel. Call inside header row."""

    # Simple bell button — text shows unread count
    bell_btn = ui.button(icon='notifications') \
        .props('flat round color=white size=sm')

    # Panel — use dialog instead of fixed card (simpler, no positioning issues)
    _panel_open = {'value': False}

    async def toggle_panel():
        if _panel_open['value']:
            return  # already open

        _panel_open['value'] = True
        app.storage.user['notif_last_read'] = time.time()

        dlg = ui.dialog()
        dlg.move()
        with dlg, ui.card().classes('p-0 w-[380px] max-h-[420px]'):
            with ui.row().classes('items-center justify-between px-3 py-2 bg-gray-800'):
                ui.label('Notifications').classes('text-sm font-bold')

                def clear_all():
                    app.storage.user['notifications'] = []
                    app.storage.user['notif_last_read'] = time.time()
                    dlg.submit(None)

                ui.button('Clear All', on_click=clear_all) \
                    .props('flat dense size=xs color=grey-6')
                ui.button(icon='close', on_click=lambda: dlg.submit(None)) \
                    .props('flat round dense size=xs color=grey-6')

            notifs = _get_notifications()
            with ui.scroll_area().classes('w-full').style('max-height:360px'):
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
                        from datetime import datetime
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

        dlg.open()
        await dlg
        _panel_open['value'] = False
        _update_badge()

    bell_btn.on_click(toggle_panel)

    def _update_badge():
        unread = _get_unread_count()
        if unread > 0:
            bell_btn.props(f'color=amber')
        else:
            bell_btn.props('color=white')

    _update_badge()
    ui.timer(5, _update_badge)
