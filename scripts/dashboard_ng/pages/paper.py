"""Paper trading page."""

import logging

from nicegui import ui, run

log = logging.getLogger('axc.paper')


def _get_paper_status() -> dict:
    """Fetch paper trading status (blocking)."""
    from scripts.dashboard.paper_trading import handle_paper_trading_status
    _, data = handle_paper_trading_status()
    return data


def render_paper_page():
    """Render paper trading control page."""
    paper_state = {'data': {}}

    async def refresh():
        paper_state['data'] = await run.io_bound(_get_paper_status)
        update_display()

    # Status card
    with ui.card().classes('p-6 bg-gray-800 border border-gray-700 max-w-lg'):
        ui.label('Paper Trading').classes('text-lg font-bold mb-4')

        status_label = ui.label('—').classes('text-2xl font-bold')
        pid_label = ui.label('').classes('text-xs text-gray-500')

        with ui.row().classes('gap-3 mt-4'):
            async def start_paper():
                from scripts.dashboard.paper_trading import handle_paper_trading_start
                result = await run.io_bound(handle_paper_trading_start)
                if result.get('ok'):
                    ui.notify('Paper trading started', type='positive')
                else:
                    ui.notify(f'Start failed: {result.get("error")}', type='negative')
                await refresh()

            async def stop_paper():
                from scripts.dashboard.paper_trading import handle_paper_trading_stop
                result = await run.io_bound(handle_paper_trading_stop)
                if result.get('ok'):
                    ui.notify('Paper trading stopped', type='warning')
                else:
                    ui.notify(f'Stop failed: {result.get("error")}', type='negative')
                await refresh()

            start_btn = ui.button('Start', icon='play_arrow', on_click=start_paper) \
                .props('color=green')
            stop_btn = ui.button('Stop', icon='stop', on_click=stop_paper) \
                .props('color=red')

    # Recent dry-run entries
    ui.label('RECENT DRY-RUN ENTRIES').classes('text-xs text-gray-500 uppercase tracking-wide mt-6')
    log_container = ui.column().classes('w-full')

    def update_display():
        d = paper_state['data']
        mode = d.get('mode', 'stopped')
        pid = d.get('pid', None)

        # Status
        status_label.text = mode.upper()
        colors = {'running': 'text-green-400', 'stopped': 'text-gray-400', 'dry_run': 'text-yellow-400'}
        status_label.classes(replace=f'text-2xl font-bold {colors.get(mode, "text-gray-400")}')

        if pid:
            pid_label.text = f'PID: {pid}'
        else:
            pid_label.text = ''

        # Button states
        is_running = mode in ('running', 'dry_run')
        start_btn.set_enabled(not is_running)
        stop_btn.set_enabled(is_running)

        # Log entries — structured table if dict, plain text otherwise
        entries = d.get('entries', d.get('log', []))
        log_container.clear()
        with log_container:
            if not entries:
                ui.label('No dry-run entries').classes('text-gray-600 text-sm')
            else:
                # Try to build structured table
                rows = []
                for entry in entries[:30]:
                    if isinstance(entry, dict):
                        rows.append({
                            'time': entry.get('time', ''),
                            'action': entry.get('action', entry.get('type', '')),
                            'pair': entry.get('pair', entry.get('symbol', '')),
                            'side': entry.get('direction', entry.get('side', '')),
                            'price': entry.get('price', ''),
                            'qty': entry.get('qty', entry.get('quantity', '')),
                            'sl': entry.get('sl', ''),
                            'tp': entry.get('tp', ''),
                            'lev': entry.get('leverage', ''),
                        })
                    else:
                        rows.append({'time': '', 'action': str(entry)[:80],
                                     'pair': '', 'side': '', 'price': '',
                                     'qty': '', 'sl': '', 'tp': '', 'lev': ''})

                if rows and any(r.get('pair') for r in rows):
                    ui.aggrid({
                        'columnDefs': [
                            {'field': 'time', 'width': 100},
                            {'field': 'action', 'width': 70},
                            {'field': 'pair', 'headerName': 'Symbol', 'width': 90},
                            {'field': 'side', 'width': 55},
                            {'field': 'price', 'width': 80, 'type': 'rightAligned'},
                            {'field': 'qty', 'width': 60, 'type': 'rightAligned'},
                            {'field': 'sl', 'headerName': 'SL', 'width': 75, 'type': 'rightAligned'},
                            {'field': 'tp', 'headerName': 'TP', 'width': 75, 'type': 'rightAligned'},
                            {'field': 'lev', 'headerName': 'Lev', 'width': 45, 'type': 'rightAligned'},
                        ],
                        'rowData': rows,
                        'headerHeight': 32,
                        'rowHeight': 30,
                        'domLayout': 'autoHeight',
                    }).classes('w-full ag-theme-balham-dark')
                else:
                    # Fallback: plain text display
                    for entry in entries[:20]:
                        text = entry.get('message', str(entry)) if isinstance(entry, dict) else str(entry)
                        ts = entry.get('time', '') if isinstance(entry, dict) else ''
                        with ui.row().classes('gap-2 py-0.5'):
                            if ts:
                                ui.label(ts).classes('text-[10px] text-gray-600 min-w-[60px]')
                            ui.label(text).classes('text-xs text-gray-400')

    # Initial load + timer
    ui.timer(0.1, refresh, once=True)
    ui.timer(5, refresh)
