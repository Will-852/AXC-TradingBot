"""Exchange connect/disconnect UI — Aster, Binance, HyperLiquid.

Modal dialogs for API key input, disconnect buttons, status refresh.
"""

import json
import logging

from nicegui import ui, run

from scripts.dashboard_ng import state

log = logging.getLogger('axc.exchange')

EXCHANGES = [
    {
        'name': 'aster', 'label': 'Aster FX',
        'fields': [('api_key', 'API Key'), ('api_secret', 'API Secret')],
        'connect': 'handle_aster_connect',
        'disconnect': 'handle_aster_disconnect',
        'status': 'handle_aster_status',
    },
    {
        'name': 'binance', 'label': 'Binance',
        'fields': [('api_key', 'API Key'), ('api_secret', 'API Secret')],
        'connect': 'handle_binance_connect',
        'disconnect': 'handle_binance_disconnect',
        'status': 'handle_binance_status',
    },
    {
        'name': 'hl', 'label': 'HyperLiquid',
        'fields': [('private_key', 'Private Key'), ('account_address', 'Wallet Address')],
        'connect': 'handle_hl_connect',
        'disconnect': 'handle_hl_disconnect',
        'status': 'handle_hl_status',
    },
]


async def _show_connect_dialog(exch: dict):
    """Show exchange connect modal."""
    with ui.dialog() as dialog, ui.card().classes('p-6 min-w-[380px]'):
        ui.label(f'Connect {exch["label"]}').classes('text-lg font-bold mb-4')

        inputs = {}
        for field_key, field_label in exch['fields']:
            inputs[field_key] = ui.input(field_label).classes('w-full mb-2') \
                .props('dense outlined dark type=password')

        status_label = ui.label('').classes('text-sm mt-2')

        async def submit():
            body = {k: v.value for k, v in inputs.items()}
            if not all(body.values()):
                ui.notify('All fields required', type='warning')
                return

            status_label.text = 'Connecting...'
            from scripts.dashboard import exchange_auth
            handler = getattr(exchange_auth, exch['connect'])
            code, result = await run.io_bound(handler, json.dumps(body))

            if result.get('ok'):
                bal = result.get('balance', '?')
                ui.notify(f'{exch["label"]} connected! Balance: ${bal}', type='positive')
                await state._update_exchanges()
                dialog.close()
            else:
                status_label.text = f'Error: {result.get("error", "Unknown")}'
                status_label.classes(replace='text-sm mt-2 text-red-400')

        with ui.row().classes('gap-2 justify-end w-full mt-2'):
            ui.button('Cancel', on_click=dialog.close).props('flat color=grey')
            ui.button('Connect', icon='link', on_click=submit).props('color=indigo')

    dialog.open()


async def _disconnect(exch: dict):
    """Disconnect an exchange."""
    from scripts.dashboard import exchange_auth
    handler = getattr(exchange_auth, exch['disconnect'])
    result = await run.io_bound(handler)
    if isinstance(result, tuple):
        _, data = result
    else:
        data = result if isinstance(result, dict) else {'ok': True}

    ui.notify(f'{exch["label"]} disconnected', type='warning')
    await state._update_exchanges()


def render_exchange_panel():
    """Render exchange connection management panel."""
    with ui.expansion('Exchange Connections', icon='account_balance_wallet').classes('w-full'):
        exchange_container = ui.column().classes('w-full gap-3')

        def update():
            exchanges = state.get_exchanges()
            exchange_container.clear()
            with exchange_container:
                for exch in EXCHANGES:
                    info = exchanges.get(exch['name'], {})
                    status = info.get('status', 'disconnected')
                    balance = info.get('balance')
                    key_preview = info.get('key_preview', '')

                    is_connected = status == 'connected'
                    color = '#22c55e' if is_connected else '#6b7280'

                    with ui.row().classes('items-center justify-between w-full py-1'):
                        with ui.row().classes('items-center gap-3'):
                            ui.icon('circle').classes('text-[8px]').style(f'color: {color}')
                            ui.label(exch['label']).classes('text-sm font-bold min-w-[100px]')
                            if is_connected:
                                if balance is not None:
                                    ui.label(f'${balance:.2f}').classes('text-sm font-mono text-green-400')
                                if key_preview:
                                    ui.label(key_preview).classes('text-[10px] text-gray-600 font-mono')
                            else:
                                ui.label('Not connected').classes('text-xs text-gray-500')

                        with ui.row().classes('gap-1'):
                            if is_connected:
                                ui.button('Disconnect',
                                          on_click=lambda e=exch: _disconnect(e)) \
                                    .props('flat dense size=xs color=red')
                            else:
                                async def do_connect(e=exch):
                                    await _show_connect_dialog(e)
                                ui.button('Connect', on_click=do_connect) \
                                    .props('flat dense size=xs color=indigo')

        ui.timer(0.1, update, once=True)
        ui.timer(60, update)
