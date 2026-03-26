"""Trade entry modal — enhanced order entry with real-time calculations.

Sequence: margin mode → leverage → entry → SL (critical) → TP (best-effort).
BMD fix #2: debounce lock prevents double-submit.

Enhancements (2026-03-26):
  - Margin mode badge (CROSSED)
  - Real-time notional + margin % display
  - Bidirectional USDT ↔ Qty calculation
  - Validation warnings (min qty, min notional, insufficient balance)
"""

import logging
import math
import time

from nicegui import ui, run

log = logging.getLogger('axc.trade')


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


def _get_price(symbol: str) -> float:
    """Get current price from dashboard state."""
    try:
        from scripts.dashboard_ng.state import get_data
        prices = get_data().get('prices', {})
        coin = symbol.replace('USDT', '')
        return float(prices.get(coin, 0))
    except (TypeError, ValueError):
        return 0


async def show_trade_modal(symbol: str = 'BTCUSDT', platform: str = 'aster'):
    """Show the trade entry dialog."""
    dialog = ui.dialog().props('persistent')
    dialog.move()
    with dialog, ui.card().classes('p-6 min-w-[420px] max-w-[520px]'):
        # ── Header ──
        with ui.row().classes('items-center justify-between w-full mb-3'):
            ui.label('New Order').classes('text-xl font-bold')
            ui.badge('CROSS MARGIN', color='teal', outline=True).classes('text-[10px]')

        # ── Symbol + Platform ──
        with ui.row().classes('gap-4 w-full'):
            symbol_input = ui.input('Symbol', value=symbol).classes('flex-1') \
                .props('dense outlined dark')
            platform_select = ui.select(
                ['aster', 'binance', 'hyperliquid'],
                value=platform, label='Exchange',
            ).classes('w-36').props('dense outlined dark')

        # ── Side ──
        side_toggle = ui.toggle(['BUY', 'SELL'], value='BUY').props('no-caps color=teal spread')

        # ── Order type ──
        type_toggle = ui.toggle(['MARKET', 'LIMIT'], value='MARKET').props('dense no-caps color=grey-7')
        limit_price_input = ui.number('Limit Price', format='%.4f').classes('w-full') \
            .props('dense outlined dark')
        limit_price_input.set_visibility(False)

        def on_type_change(e):
            limit_price_input.set_visibility(e.value == 'LIMIT')

        type_toggle.on_value_change(on_type_change)

        # ── Balance + Margin info ──
        with ui.row().classes('gap-4 w-full items-center'):
            balance_label = ui.label('Balance: loading...').classes('text-xs text-gray-500')
            margin_pct_label = ui.label('').classes('text-xs text-gray-500')

        # ── USDT Amount + Quantity (bidirectional) ──
        with ui.row().classes('gap-4 w-full items-end'):
            notional_input = ui.number('USDT Margin', value=10.0, min=0.1, format='%.2f') \
                .classes('flex-1').props('dense outlined dark')
            qty_input = ui.number('Quantity', format='%.6f') \
                .classes('flex-1').props('dense outlined dark')

        # ── Leverage ──
        with ui.row().classes('gap-4 w-full items-end'):
            leverage_input = ui.number('Leverage', value=5, min=1, max=125, step=1) \
                .classes('w-32').props('dense outlined dark')
            notional_label = ui.label('').classes('text-xs text-teal-400 font-mono')

        # ── SL / TP ──
        with ui.row().classes('gap-4 w-full'):
            sl_input = ui.number('Stop Loss', format='%.4f').classes('flex-1') \
                .props('dense outlined dark')
            tp_input = ui.number('Take Profit', format='%.4f').classes('flex-1') \
                .props('dense outlined dark')

        # ── Info + Validation ──
        info_label = ui.label('').classes('text-[10px] text-gray-600')
        warn_label = ui.label('').classes('text-[11px] text-orange-400')

        # ── State ──
        symbol_info = {'data': {}}
        _balance = {'value': 0.0}
        _calc_lock = {'from_usdt': False, 'from_qty': False}

        async def load_info():
            try:
                sym = (symbol_input.value or '').upper().strip()
                plat = platform_select.value or 'aster'

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
                bal_raw = balances.get(plat, {}).get('balance', 0)
                try:
                    _balance['value'] = float(bal_raw)
                except (TypeError, ValueError):
                    _balance['value'] = 0
                balance_label.text = f'Balance: ${_balance["value"]:.2f}'
            except Exception as e:
                log.error('load_info failed: %s', e)
                info_label.text = f'Error: {e}'

        def _update_display():
            """Update notional, margin %, and validation warnings."""
            price = _get_price((symbol_input.value or '').upper().strip())
            qty = qty_input.value or 0
            usdt = notional_input.value or 0
            info = symbol_info['data']

            # Notional value
            if price > 0 and qty > 0:
                notional_val = qty * price
                notional_label.text = f'Notional: ${notional_val:,.1f}'
            else:
                notional_label.text = ''

            # Margin % of balance
            bal = _balance['value']
            if bal > 0 and usdt > 0:
                pct = (usdt / bal) * 100
                color = 'text-green-400' if pct < 50 else 'text-orange-400' if pct < 80 else 'text-red-400'
                margin_pct_label.text = f'({pct:.0f}% of balance)'
                margin_pct_label.classes(replace=f'text-xs {color}')
            else:
                margin_pct_label.text = ''

            # Validation warnings
            warnings = []
            min_qty = info.get('min_qty', 0)
            min_notional = info.get('min_notional', 5.0)
            step = info.get('step_size', 0.001)

            if qty > 0 and min_qty > 0 and qty < min_qty:
                warnings.append(f'Qty {qty} < min {min_qty}')
            if price > 0 and qty > 0 and (qty * price) < min_notional:
                warnings.append(f'Notional ${qty * price:.1f} < min ${min_notional}')
            if usdt > 0 and bal > 0 and usdt > bal:
                warnings.append(f'Margin ${usdt:.1f} > balance ${bal:.1f}')

            warn_label.text = ' | '.join(warnings) if warnings else ''

        def calc_qty_from_usdt():
            """USDT → Qty calculation."""
            if _calc_lock['from_qty']:
                return
            _calc_lock['from_usdt'] = True
            try:
                price = _get_price((symbol_input.value or '').upper().strip())
                if price <= 0:
                    return
                usdt = notional_input.value or 0
                lev = leverage_input.value or 1
                if usdt <= 0 or lev <= 0:
                    return
                raw_qty = (usdt * lev) / price
                step = symbol_info['data'].get('step_size', 0.001)
                if step > 0:
                    raw_qty = math.floor(raw_qty / step) * step
                qty_input.value = round(raw_qty, 8)
            except Exception as e:
                log.warning('calc_qty error: %s', e)
            finally:
                _calc_lock['from_usdt'] = False
                _update_display()

        def calc_usdt_from_qty():
            """Qty → USDT reverse calculation."""
            if _calc_lock['from_usdt']:
                return
            _calc_lock['from_qty'] = True
            try:
                price = _get_price((symbol_input.value or '').upper().strip())
                if price <= 0:
                    return
                qty = qty_input.value or 0
                lev = leverage_input.value or 1
                if qty <= 0 or lev <= 0:
                    return
                usdt = (qty * price) / lev
                notional_input.value = round(usdt, 2)
            except Exception as e:
                log.warning('calc_usdt error: %s', e)
            finally:
                _calc_lock['from_qty'] = False
                _update_display()

        notional_input.on('update:model-value', lambda: calc_qty_from_usdt())
        leverage_input.on('update:model-value', lambda: calc_qty_from_usdt())
        qty_input.on('update:model-value', lambda: calc_usdt_from_qty())

        ui.separator().classes('bg-gray-700 my-2')

        # ── Status ──
        status_label = ui.label('').classes('text-sm')

        # ── Submit ──
        async def submit_order():
            if not submit_btn.enabled:
                return
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

                    import asyncio
                    await asyncio.sleep(1.5)
                    dialog.submit('done')
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
                submit_btn.set_enabled(True)

        with ui.row().classes('gap-3 justify-end w-full'):
            ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat color=grey')
            submit_btn = ui.button('Place Order', icon='send', on_click=submit_order) \
                .props('color=teal')

        # ── Init ──
        async def init_dialog():
            await load_info()
            calc_qty_from_usdt()

        ui.timer(0.1, init_dialog, once=True)

    dialog.open()
    await dialog
