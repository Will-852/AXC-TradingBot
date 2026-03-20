"""Action plan table — per-symbol price, change, threshold, SL/TP levels.

Click a row to open trade modal for that symbol.
"""

from nicegui import ui

from scripts.dashboard_ng.state import get_data


def _status_color(status: str) -> str:
    """Return color for distance status."""
    return {
        'near': 'orange',
        'active': 'green',
        'far': 'grey',
        'blocked': 'red',
    }.get(status, 'grey')


def render_action_plan():
    """Render the action plan table with AG Grid."""
    ui.label('ACTION PLAN').classes('text-xs text-gray-500 uppercase tracking-wide')

    # Order book buttons (per symbol)
    ob_row = ui.row().classes('gap-1 flex-wrap')

    grid_container = ui.column().classes('w-full')

    def update():
        d = get_data()
        plan = d.get('action_plan', [])

        rows = []
        for item in plan:
            changes = item.get('changes', {})
            rows.append({
                'symbol': item.get('symbol', '?'),
                'price': f"${item.get('price', 0):,.2f}",
                'change_24h': f"{item.get('change_pct', 0):+.2f}%",
                'change_4h': f"{changes.get('4h', 0):+.2f}%",
                'change_1h': f"{changes.get('1h', 0):+.2f}%",
                'threshold': f"{item.get('threshold_pct', 0):.1f}%",
                'distance': f"{item.get('distance', 0):.2f}%",
                'status': item.get('status', '—'),
                'sl_long': f"${item.get('sl_long', 0):,.2f}",
                'tp_long': f"${item.get('tp_long', 0):,.2f}",
                'atr': f"${item.get('atr', 0):,.1f}",
                'tradeable': item.get('tradeable', False),
            })

        # Update OB buttons
        ob_row.clear()
        with ob_row:
            for item in plan:
                sym = item.get('symbol', '')
                if sym:
                    async def show_ob(s=sym):
                        from scripts.dashboard_ng.components.orderbook import show_orderbook
                        await show_orderbook(symbol=s)
                    ui.button(f'OB {sym.replace("USDT","")}', on_click=show_ob) \
                        .props('flat dense size=xs color=grey-6').tooltip(f'Order Book {sym}')

        grid_container.clear()
        with grid_container:
            if not rows:
                ui.label('No action plan data').classes('text-gray-600 text-sm')
                return

            async def on_row_click(e):
                row = e.args.get('data', {}) if isinstance(e.args, dict) else {}
                symbol = row.get('symbol', '')
                if symbol:
                    from scripts.dashboard_ng.components.trade_modal import show_trade_modal
                    await show_trade_modal(symbol=symbol)

            grid = ui.aggrid({
                'columnDefs': [
                    {'field': 'symbol', 'headerName': 'Symbol', 'width': 110,
                     'pinned': 'left'},
                    {'field': 'price', 'headerName': 'Price', 'width': 120,
                     'type': 'rightAligned'},
                    {'field': 'change_24h', 'headerName': '24h', 'width': 85,
                     'type': 'rightAligned',
                     'cellClassRules': {
                         'text-green-400': 'x.startsWith("+")',
                         'text-red-400': 'x.startsWith("-")',
                     }},
                    {'field': 'change_4h', 'headerName': '4h', 'width': 80,
                     'type': 'rightAligned',
                     'cellClassRules': {
                         'text-green-400': 'x.startsWith("+")',
                         'text-red-400': 'x.startsWith("-")',
                     }},
                    {'field': 'change_1h', 'headerName': '1h', 'width': 80,
                     'type': 'rightAligned',
                     'cellClassRules': {
                         'text-green-400': 'x.startsWith("+")',
                         'text-red-400': 'x.startsWith("-")',
                     }},
                    {'field': 'threshold', 'headerName': 'Thresh', 'width': 80,
                     'type': 'rightAligned'},
                    {'field': 'distance', 'headerName': 'Dist', 'width': 80,
                     'type': 'rightAligned'},
                    {'field': 'status', 'headerName': 'Status', 'width': 80},
                    {'field': 'sl_long', 'headerName': 'SL (L)', 'width': 110,
                     'type': 'rightAligned'},
                    {'field': 'tp_long', 'headerName': 'TP (L)', 'width': 110,
                     'type': 'rightAligned'},
                    {'field': 'atr', 'headerName': 'ATR', 'width': 90,
                     'type': 'rightAligned'},
                ],
                'rowData': rows,
                'defaultColDef': {
                    'sortable': True,
                    'resizable': True,
                },
                ':getRowStyle': '''params => {
                    if (params.data.status === 'near') return {background: 'rgba(234, 179, 8, 0.08)'};
                    if (params.data.status === 'active') return {background: 'rgba(34, 197, 94, 0.08)'};
                }''',
            }).classes('h-64 w-full ag-theme-balham-dark')
            grid.on('cellClicked', on_row_click)

    ui.timer(5, update)
