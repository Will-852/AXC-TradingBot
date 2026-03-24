"""Position cards + pending orders display."""

import logging

from nicegui import ui, run

from scripts.dashboard_ng.state import get_data

log = logging.getLogger('axc.positions')


async def _close_position(symbol: str, platform: str,
                          order_type: str = 'MARKET', limit_price: float = 0):
    """Close a position via market or limit order."""
    _pos_dialog_open['value'] = True
    try:
        import json
        from scripts.dashboard.handlers import handle_close_position
        payload = json.dumps({
            'symbol': symbol, 'platform': platform,
            'order_type': order_type, 'limit_price': limit_price,
        })
        result = await run.io_bound(handle_close_position, payload)
        if isinstance(result, tuple):
            status, data = result
            if status == 200:
                label = '市價' if order_type == 'MARKET' else f'限價 ${limit_price:,.1f}'
                ui.notify(f'{label} 平倉 {symbol} 成功', type='positive')
            else:
                ui.notify(f'Close failed: {data.get("error", "unknown")}', type='negative')
        elif isinstance(result, dict) and result.get('ok'):
            ui.notify(f'Closed {symbol}', type='positive')
        else:
            ui.notify(f'Close failed: {result}', type='negative')
    except Exception as e:
        log.exception('Close position error')
        ui.notify(f'Close error: {e}', type='negative')
    finally:
        _pos_dialog_open['value'] = False


async def _show_modify_dialog(pos: dict):
    """Show SL/TP modification dialog."""
    _pos_dialog_open['value'] = True
    symbol = pos.get('pair') or pos.get('symbol', '?')
    current_sl = pos.get('sl_price') or pos.get('sl', '')
    current_tp = pos.get('tp_price') or pos.get('tp', '')

    dialog = ui.dialog().props('persistent')
    dialog.move()  # page root
    with dialog, ui.card().classes('p-6 min-w-[300px]'):
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

    try:
        result = await dialog
        if result:
            import json
            from scripts.dashboard.handlers import handle_modify_sltp
            # handler expects JSON string body with sl_price/tp_price keys
            payload = json.dumps({
                'symbol': symbol,
                'platform': pos.get('platform', 'aster'),
                'sl_price': result['sl'] or 0,
                'tp_price': result['tp'] or 0,
            })
            mod_result = await run.io_bound(handle_modify_sltp, payload)
            if isinstance(mod_result, tuple):
                status, data = mod_result
                if status == 200:
                    ui.notify(f'SL/TP updated for {symbol}', type='positive')
                else:
                    ui.notify(f'Modify failed: {data.get("error", "unknown")}', type='negative')
            elif isinstance(mod_result, dict) and mod_result.get('ok'):
                ui.notify(f'SL/TP updated for {symbol}', type='positive')
            else:
                ui.notify(f'Modify failed: {mod_result}', type='negative')
    except Exception as e:
        log.exception('Modify SL/TP error')
        ui.notify(f'Modify error: {e}', type='negative')
    finally:
        _pos_dialog_open['value'] = False


def _fmt_price(v) -> str:
    """Format price for display: BTC-level → 1 dp, small → 4 dp."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return '—'
    if n == 0:
        return '—'
    return f'{n:,.1f}' if n > 100 else f'{n:.4f}'


def _fmt_pnl(v) -> str:
    """Format PnL: always 2 dp with sign."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return '—'
    return f'{n:+.2f}'


def _parse_hold_score(raw):
    """Parse hold_score regardless of whether backend sends dict or JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            import json, re
            # Python repr → JSON: single quotes, True/False/None
            s = raw.replace("'", '"')
            s = re.sub(r'\bTrue\b', 'true', s)
            s = re.sub(r'\bFalse\b', 'false', s)
            s = re.sub(r'\bNone\b', 'null', s)
            return json.loads(s)
        except Exception:
            pass
    return None


def _render_position_card(pos: dict):
    """Render a single position detail card."""
    symbol = pos.get('pair') or pos.get('symbol', '?')
    side = pos.get('direction') or pos.get('side', '?')
    entry = pos.get('entry_price', 0)
    mark = pos.get('mark_price', 0)
    unrealized = pos.get('unrealized_pnl', 0)
    unrealized_pct = pos.get('unrealized_pct', 0)
    sl = pos.get('sl_price') or pos.get('sl', '')
    tp = pos.get('tp_price') or pos.get('tp', '')
    platform = pos.get('platform', 'aster')
    hold_score_raw = pos.get('hold_score', None)

    pnl_val = float(unrealized) if unrealized else 0
    pnl_color = 'text-green-400' if pnl_val >= 0 else 'text-red-400'

    with ui.card().classes('p-4 bg-gray-800 border border-gray-700 w-full'):
        with ui.row().classes('items-center justify-between mb-3'):
            with ui.row().classes('items-center gap-2'):
                ui.label(symbol).classes('text-lg font-bold')
                ui.badge(side, color='green' if side == 'LONG' else 'red').classes('text-xs')
                ui.badge(platform.upper(), color='grey').classes('text-xs')
            with ui.row().classes('gap-2'):
                ui.button('Modify', on_click=lambda p=pos: _show_modify_dialog(p)) \
                    .props('flat dense size=sm color=indigo')
                async def _confirm_close(s=symbol, p=platform, pnl=pnl_val, mk=mark, sd=side):
                    dlg = ui.dialog().props('persistent')
                    dlg.move()
                    with dlg, ui.card().classes('p-5 min-w-[340px]'):
                        ui.label(f'確認平倉 {s}？').classes('text-lg font-bold')
                        ui.label(f'PnL: ${pnl:+.2f}').classes('text-sm text-gray-400 mt-1')

                        # Market / Limit toggle
                        order_type = {'v': 'MARKET'}
                        with ui.row().classes('mt-3 gap-2 items-center'):
                            ui.label('落單方式').classes('text-xs text-gray-500')
                            toggle = ui.toggle(
                                {'MARKET': '市價 (Taker)', 'LIMIT': '限價 (Maker)'},
                                value='MARKET',
                            ).classes('text-xs')

                        # Limit price input (hidden by default)
                        mk_f = float(mk) if mk else 0
                        # Default = mark price (user adjusts to desired maker price)
                        default_limit = round(mk_f, 1)
                        limit_row = ui.column().classes('mt-2 gap-1')
                        limit_row.set_visibility(False)
                        with limit_row:
                            limit_input = ui.number(
                                'Limit Price', value=default_limit,
                                format='%.1f',
                            ).classes('w-full')
                            # Fat-finger warning (>2% from mark)
                            limit_warn = ui.label('').classes('text-xs text-red-400')
                            limit_warn.set_visibility(False)

                            def _check_limit(e, m=mk_f):
                                if m > 0 and e.value:
                                    dev = abs(e.value - m) / m
                                    if dev > 0.02:
                                        limit_warn.text = f'⚠️ 偏離 mark price {dev:.1%} — 確認價格正確'
                                        limit_warn.set_visibility(True)
                                    else:
                                        limit_warn.set_visibility(False)
                            limit_input.on_value_change(_check_limit)

                        # Slippage warning (market only)
                        warn_row = ui.row().classes('mt-2')
                        with warn_row:
                            ui.label('⚠️ 市價落單可能有滑點（~0.01-0.05%）') \
                                .classes('text-xs text-amber-400')

                        def _on_toggle(e):
                            order_type['v'] = e.value
                            limit_row.set_visibility(e.value == 'LIMIT')
                            warn_row.set_visibility(e.value == 'MARKET')
                        toggle.on_value_change(_on_toggle)

                        with ui.row().classes('gap-2 mt-4 justify-end'):
                            ui.button('Cancel', on_click=dlg.close).props('flat color=grey')
                            ui.button('平倉', on_click=lambda: dlg.submit({
                                'type': order_type['v'],
                                'limit': limit_input.value,
                            })).props('color=red')

                    result = await dlg
                    if result:
                        await _close_position(
                            s, p,
                            order_type=result['type'],
                            limit_price=float(result['limit'] or 0) if result['type'] == 'LIMIT' else 0,
                        )
                ui.button('Close', on_click=_confirm_close) \
                    .props('flat dense size=sm color=red')

        with ui.row().classes('gap-6'):
            with ui.column().classes('gap-1'):
                ui.label('Entry').classes('text-xs text-gray-500')
                ui.label(f'${_fmt_price(entry)}').classes('text-sm font-mono')
            with ui.column().classes('gap-1'):
                ui.label('Mark').classes('text-xs text-gray-500')
                ui.label(f'${_fmt_price(mark)}').classes('text-sm font-mono')
            with ui.column().classes('gap-1'):
                ui.label('PnL').classes('text-xs text-gray-500')
                pct_val = float(unrealized_pct) if unrealized_pct else 0
                ui.label(f'${_fmt_pnl(pnl_val)} ({pct_val:+.2f}%)').classes(f'text-sm font-mono {pnl_color}')
            with ui.column().classes('gap-1'):
                ui.label('SL / TP').classes('text-xs text-gray-500')
                sl_str = _fmt_price(sl) if sl else '—'
                tp_str = _fmt_price(tp) if tp else '—'
                ui.label(f'{sl_str} / {tp_str}').classes('text-sm font-mono')

        # Hold Score — parsed + formatted as badge with factor tooltip
        hs = _parse_hold_score(hold_score_raw)
        log.debug('hold_score raw type=%s, parsed=%s', type(hold_score_raw).__name__,
                  'ok' if hs else 'FAIL')
        if hs and 'score' in hs:
            sc = float(hs['score'])
            color = ('green' if sc >= 8 else 'indigo' if sc >= 6
                     else 'amber' if sc >= 4 else 'orange' if sc >= 2 else 'red')
            factors = hs.get('factors', [])
            with ui.row().classes('mt-2 items-center gap-2 flex-wrap'):
                ui.label('Hold Score').classes('text-xs text-gray-500')
                badge = ui.badge(f'{sc:.1f}', color=color).classes('text-sm')
                if factors:
                    tip = '\n'.join(
                        f"{f.get('name', '?')}: {f.get('score', '?')}  {f.get('detail', '')}"
                        for f in factors
                    )
                    badge.tooltip(tip)
                # Factor breakdown inline
                for f in factors:
                    f_sc = float(f.get('score', 0))
                    f_color = 'green' if f_sc >= 7 else 'amber' if f_sc >= 4 else 'red'
                    ui.badge(f"{f.get('name', '?')} {f_sc:.0f}", color=f_color) \
                        .classes('text-xs').props('outline')
        elif hold_score_raw is not None:
            # Fallback: show score label even if parse fails (for debugging)
            with ui.row().classes('mt-2 items-center gap-2'):
                ui.label('Hold Score').classes('text-xs text-gray-500')
                ui.label('Parse error — check logs').classes('text-xs text-red-400')


_pos_dialog_open = {'value': False}


def render_positions():
    """Render all open positions + pending orders."""
    ui.label('POSITIONS').classes('text-xs text-gray-500 uppercase tracking-wide')
    positions_container = ui.column().classes('w-full gap-3')

    ui.label('PENDING ORDERS').classes('text-xs text-gray-500 uppercase tracking-wide mt-4')
    orders_container = ui.column().classes('w-full')

    def update():
        if _pos_dialog_open['value']:
            return
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
                for order in orders:
                    sym = order.get('symbol', '?')
                    side = order.get('side', '?')
                    otype = order.get('type', '?')
                    price = order.get('price', '?')
                    qty = order.get('qty', '?')
                    oid = order.get('orderId', order.get('order_id', ''))
                    platform = order.get('platform', 'aster')

                    with ui.row().classes('items-center gap-3 w-full py-1 border-b border-gray-800'):
                        ui.label(sym).classes('text-sm font-bold min-w-[90px]')
                        ui.badge(side, color='green' if side == 'BUY' else 'red').classes('text-xs')
                        ui.label(otype).classes('text-xs text-gray-500')
                        ui.label(f'@ {price}').classes('text-sm font-mono')
                        ui.label(f'x {qty}').classes('text-xs text-gray-400')

                        async def cancel_order(s=sym, p=platform, o=oid):
                            import json as _json
                            try:
                                from scripts.dashboard.handlers import handle_cancel_order
                                payload = _json.dumps({'symbol': s, 'platform': p, 'orderId': o})
                                result = await run.io_bound(handle_cancel_order, payload)
                                if isinstance(result, tuple):
                                    status, data = result
                                    if status == 200:
                                        ui.notify(f'Cancelled {s}', type='positive')
                                    else:
                                        ui.notify(f'Cancel failed: {data.get("error", "unknown")}', type='negative')
                                elif isinstance(result, dict) and result.get('ok'):
                                    ui.notify(f'Cancelled {s}', type='positive')
                                else:
                                    ui.notify(f'Cancel failed: {result}', type='negative')
                            except Exception as e:
                                ui.notify(f'Cancel error: {e}', type='negative')

                        ui.button('Cancel', on_click=cancel_order) \
                            .props('flat dense size=xs color=red')

    ui.timer(3, update)
