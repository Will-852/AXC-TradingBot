"""Position cards + pending orders display."""

import logging

from nicegui import ui, run

from scripts.dashboard_ng.state import get_data

log = logging.getLogger('axc.positions')


async def _close_position(symbol: str, platform: str):
    """Market-close a position."""
    from scripts.dashboard.handlers import handle_close_position
    result = await run.io_bound(handle_close_position, {
        'symbol': symbol, 'platform': platform,
    })
    if result.get('ok'):
        ui.notify(f'Closed {symbol}', type='positive')
    else:
        ui.notify(f'Close failed: {result.get("error")}', type='negative')


async def _show_modify_dialog(pos: dict):
    """Show SL/TP modification dialog."""
    symbol = pos.get('symbol', '?')
    current_sl = pos.get('sl', '')
    current_tp = pos.get('tp', '')

    with ui.dialog() as dialog, ui.card().classes('p-6 min-w-[300px]'):
        ui.label(f'Modify SL/TP — {symbol}').classes('text-lg font-bold mb-4')

        sl_input = ui.number('Stop Loss', value=float(current_sl) if current_sl else None,
                             format='%.2f').classes('w-full')
        tp_input = ui.number('Take Profit', value=float(current_tp) if current_tp else None,
                             format='%.2f').classes('w-full')

        with ui.row().classes('gap-2 mt-4 justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat color=grey')
            ui.button('Confirm', on_click=lambda: dialog.submit({
                'sl': sl_input.value, 'tp': tp_input.value,
            })).props('color=indigo')

    result = await dialog
    if result:
        from scripts.dashboard.handlers import handle_modify_sltp
        mod_result = await run.io_bound(handle_modify_sltp, {
            'symbol': symbol,
            'platform': pos.get('platform', 'aster'),
            'sl': result['sl'],
            'tp': result['tp'],
        })
        if mod_result.get('ok'):
            ui.notify(f'SL/TP updated for {symbol}', type='positive')
        else:
            ui.notify(f'Modify failed: {mod_result.get("error")}', type='negative')


def _render_position_card(pos: dict):
    """Render a single position detail card."""
    symbol = pos.get('symbol', '?')
    side = pos.get('side', '?')
    entry = pos.get('entry_price', 0)
    mark = pos.get('mark_price', 0)
    unrealized = pos.get('unrealized_pnl', 0)
    unrealized_pct = pos.get('unrealized_pct', 0)
    sl = pos.get('sl', '—')
    tp = pos.get('tp', '—')
    platform = pos.get('platform', 'aster')
    hold_score = pos.get('hold_score', '—')

    pnl_color = 'text-green-400' if float(unrealized) >= 0 else 'text-red-400'
    side_color = 'text-green-400' if side == 'LONG' else 'text-red-400'

    with ui.card().classes('p-4 bg-gray-800 border border-gray-700 w-full'):
        with ui.row().classes('items-center justify-between mb-3'):
            with ui.row().classes('items-center gap-2'):
                ui.label(symbol).classes('text-lg font-bold')
                ui.badge(side, color='green' if side == 'LONG' else 'red').classes('text-xs')
                ui.badge(platform.upper(), color='grey').classes('text-xs')
            with ui.row().classes('gap-2'):
                ui.button('Modify', on_click=lambda p=pos: _show_modify_dialog(p)) \
                    .props('flat dense size=sm color=indigo')
                ui.button('Close', on_click=lambda s=symbol, p=platform: _close_position(s, p)) \
                    .props('flat dense size=sm color=red')

        with ui.row().classes('gap-6'):
            with ui.column().classes('gap-1'):
                ui.label('Entry').classes('text-xs text-gray-500')
                ui.label(f'${entry}').classes('text-sm')
            with ui.column().classes('gap-1'):
                ui.label('Mark').classes('text-xs text-gray-500')
                ui.label(f'${mark}').classes('text-sm')
            with ui.column().classes('gap-1'):
                ui.label('PnL').classes('text-xs text-gray-500')
                ui.label(f'${unrealized} ({unrealized_pct}%)').classes(f'text-sm {pnl_color}')
            with ui.column().classes('gap-1'):
                ui.label('SL / TP').classes('text-xs text-gray-500')
                ui.label(f'{sl} / {tp}').classes('text-sm')
            with ui.column().classes('gap-1'):
                ui.label('Hold Score').classes('text-xs text-gray-500')
                ui.label(str(hold_score)).classes('text-sm')


def render_positions():
    """Render all open positions + pending orders."""
    # Positions section
    ui.label('POSITIONS').classes('text-xs text-gray-500 uppercase tracking-wide')
    positions_container = ui.column().classes('w-full gap-3')

    # Pending orders section
    ui.label('PENDING ORDERS').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    orders_container = ui.column().classes('w-full')

    def update():
        d = get_data()
        positions = d.get('live_positions', [])
        orders = d.get('open_orders', [])

        # Positions
        positions_container.clear()
        with positions_container:
            if not positions:
                ui.label('No open positions').classes('text-gray-600 text-sm')
            else:
                for pos in positions:
                    _render_position_card(pos)

        # Pending orders
        orders_container.clear()
        with orders_container:
            if not orders:
                ui.label('No pending orders').classes('text-gray-600 text-sm')
            else:
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'symbol', 'headerName': 'Symbol', 'width': 120},
                        {'field': 'side', 'headerName': 'Side', 'width': 80},
                        {'field': 'type', 'headerName': 'Type', 'width': 80},
                        {'field': 'price', 'headerName': 'Price', 'width': 100},
                        {'field': 'qty', 'headerName': 'Qty', 'width': 80},
                        {'field': 'status', 'headerName': 'Status', 'width': 80},
                    ],
                    'rowData': orders,
                }).classes('h-40')

    ui.timer(3, update)
