"""Trade entry modal — OKX-style order entry with 5-step execution.

Sequence: margin mode → leverage → entry → SL (critical) → TP (best-effort).
BMD fix #2: debounce lock prevents double-submit.
"""

import logging
import time

from nicegui import ui, run

log = logging.getLogger('axc.trade')

# Debounce lock — prevents double-submit (BMD fix #2)
_trade_lock = False


async def _fetch_balance() -> dict:
    """Fetch balances from all exchanges."""
    from scripts.dashboard.handlers import handle_exchange_balance
    return await run.io_bound(handle_exchange_balance)


async def _fetch_symbol_info(symbol: str, platform: str) -> dict:
    """Fetch symbol precision info."""
    from scripts.dashboard.handlers import handle_symbol_info
    qs = {'symbol': [symbol], 'platform': [platform]}
    code, data = await run.io_bound(handle_symbol_info, qs)
    if code != 200:
        return {}
    return data


async def show_trade_modal(symbol: str = 'BTCUSDT', platform: str = 'aster'):
    """Show the trade entry dialog. Awaitable — returns on close."""
    global _trade_lock

    with ui.dialog() as dialog, ui.card().classes('p-6 min-w-[400px] max-w-[500px]'):
        ui.label('New Order').classes('text-xl font-bold mb-4')

        # Symbol + Platform
        with ui.row().classes('gap-4 w-full'):
            symbol_input = ui.input('Symbol', value=symbol).classes('flex-1') \
                .props('dense outlined dark')
            platform_select = ui.select(
                ['aster', 'binance', 'hyperliquid'],
                value=platform, label='Exchange',
            ).classes('w-36').props('dense outlined dark')

        # Side
        side_toggle = ui.toggle(['BUY', 'SELL'], value='BUY').props('no-caps color=indigo spread')

        # Order type
        type_toggle = ui.toggle(['MARKET', 'LIMIT'], value='MARKET').props('dense no-caps color=grey-7')
        limit_price_input = ui.number('Limit Price', format='%.4f').classes('w-full') \
            .props('dense outlined dark')
        limit_price_input.set_visibility(False)

        def on_type_change(e):
            limit_price_input.set_visibility(e.value == 'LIMIT')

        type_toggle.on_value_change(on_type_change)

        # Balance display
        balance_label = ui.label('Balance: loading...').classes('text-xs text-gray-500')

        # Qty inputs
        with ui.row().classes('gap-4 w-full items-end'):
            notional_input = ui.number('USDT Amount', value=10.0, min=1, format='%.2f') \
                .classes('flex-1').props('dense outlined dark')
            qty_input = ui.number('Quantity', format='%.6f') \
                .classes('flex-1').props('dense outlined dark')

        # Leverage
        leverage_input = ui.number('Leverage', value=5, min=1, max=125, step=1) \
            .classes('w-32').props('dense outlined dark')

        # SL / TP
        with ui.row().classes('gap-4 w-full'):
            sl_input = ui.number('Stop Loss', format='%.4f').classes('flex-1') \
                .props('dense outlined dark')
            tp_input = ui.number('Take Profit', format='%.4f').classes('flex-1') \
                .props('dense outlined dark')

        # Symbol info display
        info_label = ui.label('').classes('text-[10px] text-gray-600')

        # Auto-calculate qty from notional
        symbol_info = {'data': {}}

        async def load_info():
            sym = (symbol_input.value or '').upper().strip()
            plat = platform_select.value or 'aster'

            # Fetch balance + symbol info in parallel
            info = await _fetch_symbol_info(sym, plat)
            symbol_info['data'] = info

            if info:
                step = info.get('step_size', 0.001)
                min_qty = info.get('min_qty', 0.001)
                min_notional = info.get('min_notional', 5.0)
                info_label.text = f'Step: {step} | Min qty: {min_qty} | Min notional: ${min_notional}'
            else:
                info_label.text = 'Could not fetch symbol info'

            balances = await _fetch_balance()
            bal = balances.get(plat, {}).get('balance', '?')
            balance_label.text = f'Balance: ${bal}'

        def calc_qty():
            from scripts.dashboard_ng.state import get_data
            d = get_data()
            prices = d.get('prices', {})
            sym = (symbol_input.value or '').upper().strip()

            # Get current price
            coin = sym.replace('USDT', '')
            price_str = prices.get(coin, '0')
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                price = 0

            if price <= 0:
                return

            notional = notional_input.value or 0
            lev = leverage_input.value or 1
            raw_qty = (notional * lev) / price

            # Round to step size
            info = symbol_info['data']
            step = info.get('step_size', 0.001)
            if step > 0:
                import math
                raw_qty = math.floor(raw_qty / step) * step

            qty_input.value = round(raw_qty, 8)

        notional_input.on('update:model-value', lambda: calc_qty())
        leverage_input.on('update:model-value', lambda: calc_qty())

        ui.separator().classes('bg-gray-700 my-2')

        # Submit button with debounce
        status_label = ui.label('').classes('text-sm')

        async def submit_order():
            global _trade_lock
            if _trade_lock:
                ui.notify('Order already submitting...', type='warning')
                return

            _trade_lock = True
            submit_btn.set_enabled(False)
            status_label.text = 'Submitting...'
            status_label.classes(replace='text-sm text-yellow-400')

            try:
                import json
                body = json.dumps({
                    'symbol': (symbol_input.value or '').upper().strip(),
                    'platform': platform_select.value,
                    'side': side_toggle.value,
                    'order_type': type_toggle.value,
                    'qty': qty_input.value,
                    'leverage': int(leverage_input.value or 5),
                    'limit_price': limit_price_input.value if type_toggle.value == 'LIMIT' else None,
                    'sl_price': sl_input.value,
                    'tp_price': tp_input.value,
                })

                from scripts.dashboard.handlers import handle_place_order
                code, result = await run.io_bound(handle_place_order, body)

                if code == 200 and result.get('ok'):
                    entry = result.get('entry', {})
                    timing = result.get('timing', {})
                    warnings = result.get('warnings', [])

                    msg = f"Filled @ ${entry.get('avgPrice', '?')} ({timing.get('fill_ms', '?')}ms)"
                    if result.get('pending'):
                        msg = 'Limit order placed (pending fill)'
                    if result.get('sltp_queued'):
                        msg += ' | SL/TP queued for fill'

                    status_label.text = msg
                    status_label.classes(replace='text-sm text-green-400')
                    ui.notify(msg, type='positive')

                    for w in warnings:
                        ui.notify(w, type='warning')

                    # Auto-close after success
                    await ui.run_javascript('setTimeout(() => {}, 1500)')
                    dialog.close()
                else:
                    error = result.get('error', 'Unknown error')
                    status_label.text = f'Error: {error}'
                    status_label.classes(replace='text-sm text-red-400')
                    ui.notify(f'Order failed: {error}', type='negative')

            except Exception as e:
                log.error('Place order error: %s', e)
                status_label.text = f'Error: {e}'
                status_label.classes(replace='text-sm text-red-400')
                ui.notify(f'Error: {e}', type='negative')
            finally:
                _trade_lock = False
                submit_btn.set_enabled(True)

        with ui.row().classes('gap-3 justify-end w-full'):
            ui.button('Cancel', on_click=dialog.close).props('flat color=grey')
            submit_btn = ui.button('Place Order', icon='send', on_click=submit_order) \
                .props('color=indigo')

        # Load info on open
        ui.timer(0.1, load_info, once=True)
        ui.timer(0.1, calc_qty, once=True)

    dialog.open()
