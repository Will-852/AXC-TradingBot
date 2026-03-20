"""Order book display — modal with bids/asks depth visualization."""

import logging

from nicegui import ui, run

log = logging.getLogger('axc.ob')


async def show_orderbook(symbol: str = 'BTCUSDT'):
    """Show order book modal for a symbol, auto-refreshing every 10s."""
    with ui.dialog() as dialog, ui.card().classes('p-4 min-w-[500px] max-w-[600px]'):
        ui.label(f'Order Book — {symbol}').classes('text-lg font-bold mb-2')

        mid_label = ui.label('').classes('text-sm font-mono text-center w-full')
        spread_label = ui.label('').classes('text-[10px] text-gray-500 text-center w-full')

        with ui.row().classes('w-full gap-4'):
            # Bids (left)
            with ui.column().classes('flex-1'):
                ui.label('BIDS').classes('text-[10px] text-green-400 uppercase tracking-wide mb-1')
                bids_container = ui.column().classes('gap-0 w-full')

            # Asks (right)
            with ui.column().classes('flex-1'):
                ui.label('ASKS').classes('text-[10px] text-red-400 uppercase tracking-wide mb-1')
                asks_container = ui.column().classes('gap-0 w-full')

        depth_label = ui.label('').classes('text-[10px] text-gray-600 mt-2')

        async def refresh_ob():
            from scripts.dashboard.handlers import handle_orderbook
            qs = {'symbol': [symbol]}
            code, data = await run.io_bound(handle_orderbook, qs)
            if code != 200:
                mid_label.text = f'Error: {data.get("error", "?")}'
                return

            bids = data.get('bids', [])[:10]
            asks = data.get('asks', [])[:10]
            mid = data.get('mid_price', 0)
            spread = data.get('spread', 0)

            mid_label.text = f'Mid: ${mid:,.4f}' if mid else 'Mid: —'
            spread_label.text = f'Spread: ${spread:,.4f}' if spread else ''

            # Find max qty for bar scaling
            all_qtys = [float(b[1]) for b in bids] + [float(a[1]) for a in asks] if bids or asks else [1]
            max_qty = max(all_qtys) if all_qtys else 1

            # Render bids
            bids_container.clear()
            with bids_container:
                for price, qty, *_ in bids:
                    pct = min(float(qty) / max_qty * 100, 100)
                    with ui.row().classes('items-center w-full gap-1 py-0.5'):
                        ui.label(f'{float(price):,.4f}').classes('text-[11px] font-mono text-green-400 min-w-[80px]')
                        ui.label(f'{float(qty):,.2f}').classes('text-[10px] font-mono text-gray-400 min-w-[60px]')
                        ui.html(f'<div style="width:{pct}%;height:4px;background:#22c55e30;border-radius:2px"></div>') \
                            .classes('flex-1')

            # Render asks
            asks_container.clear()
            with asks_container:
                for price, qty, *_ in asks:
                    pct = min(float(qty) / max_qty * 100, 100)
                    with ui.row().classes('items-center w-full gap-1 py-0.5'):
                        ui.label(f'{float(price):,.4f}').classes('text-[11px] font-mono text-red-400 min-w-[80px]')
                        ui.label(f'{float(qty):,.2f}').classes('text-[10px] font-mono text-gray-400 min-w-[60px]')
                        ui.html(f'<div style="width:{pct}%;height:4px;background:#ef444430;border-radius:2px"></div>') \
                            .classes('flex-1')

            # Depth totals
            bid_total = sum(float(b[1]) for b in bids)
            ask_total = sum(float(a[1]) for a in asks)
            depth_label.text = f'Bid depth: {bid_total:,.2f} | Ask depth: {ask_total:,.2f}'

        # Initial load + auto-refresh
        ui.timer(0.1, refresh_ob, once=True)
        ob_timer = ui.timer(10, refresh_ob)

        ui.button('Close', on_click=dialog.close).props('flat color=grey').classes('mt-2')

    dialog.open()
