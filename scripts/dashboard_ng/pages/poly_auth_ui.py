"""poly_auth_ui.py — Polymarket auth section (connection status + credentials dialog).

Self-contained: creates its own UI containers and manages the auth ↔ settings
mutual dependency via internal closures.

💰 Auth check failure = user operates without realising auth is broken.
"""

import logging
import os
import tempfile

from nicegui import ui, run

from .poly_helpers import check_auth

log = logging.getLogger('axc.poly')


def build_auth_section() -> tuple:
    """Build the auth status bar and credentials dialog.

    Returns (auth_container, refresh_auth_fn) so the caller can wire timers.
    """
    auth_container = ui.row().classes(
        'items-center gap-3 w-full py-1 px-2 rounded '
        'border border-gray-800 bg-gray-900/50'
    )

    # Forward reference for mutual dependency
    _open_settings_ref = [None]

    async def refresh_auth():
        try:
            auth = await run.io_bound(check_auth)
        except Exception as e:
            auth_container.clear()
            with auth_container:
                ui.icon('error').classes('text-red-400')
                ui.label(f'Auth check failed: {e}').classes('text-[12px] text-red-400')
            return
        auth_container.clear()
        with auth_container:
            connected = auth['key'] and auth['l2_creds']
            dot_color = '#22c55e' if connected else '#f59e0b' if auth['key'] else '#ef4444'
            status_text = 'Connected' if connected else 'L1 Only' if auth['key'] else 'No Key'
            ui.icon('circle').classes('text-[9px]').style(f'color: {dot_color}')
            ui.label(status_text).classes('text-[12px] font-mono font-bold').style(f'color: {dot_color}')

            if auth['wallet']:
                w = auth['wallet']
                short = f'{w[:6]}...{w[-4:]}' if len(w) > 10 else w
                ui.label(short).classes('text-[12px] font-mono text-gray-400')

            ui.badge('L1 Key', color='green' if auth['key'] else 'red').classes('text-[11px]')
            ui.badge('L2 API', color='green' if auth['l2_creds'] else 'grey').classes('text-[11px]')
            ui.badge(auth['network'], color='grey-7').classes('text-[11px]')

            ui.element('div').classes('flex-1')
            ui.button(icon='settings', on_click=lambda: _open_settings_ref[0]()) \
                .props('flat dense round size=sm color=grey-6') \
                .tooltip('Polymarket Credentials')

    async def open_settings():
        """Open credential settings dialog.

        💰 Writes to secrets/.env — atomic write via tempfile + os.replace.
        """
        axc = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
        env_path = os.path.join(axc, 'secrets', '.env')

        # Read current values (masked)
        current_pk = ''
        current_wallet = ''
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('POLY_PRIVATE_KEY='):
                        val = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if val:
                            current_pk = f'{val[:6]}...{val[-4:]}' if len(val) > 10 else '***'
                    elif line.startswith('POLY_WALLET_ADDRESS='):
                        current_wallet = line.split('=', 1)[1].strip().strip('"').strip("'")

        dlg = ui.dialog().props('persistent')
        dlg.move()
        with dlg, ui.card().classes('p-6 min-w-[420px]'):
            ui.label('Polymarket Credentials').classes('text-lg font-bold')
            ui.label('Saved to secrets/.env (localhost only)').classes('text-[11px] text-gray-500')

            ui.separator().classes('my-2')

            if current_pk:
                ui.label(f'Current Key: {current_pk}').classes('text-[12px] font-mono text-green-400')
            else:
                ui.label('No private key configured').classes('text-[12px] text-red-400')

            if current_wallet:
                ui.label(f'Wallet: {current_wallet}').classes('text-[12px] font-mono text-gray-400')

            ui.separator().classes('my-2')
            ui.label('Update Credentials').classes('text-sm font-bold text-gray-300')
            ui.label('Leave blank to keep current value.').classes('text-[11px] text-gray-600')

            pk_input = ui.input('Private Key (0x...)') \
                .props('type=password dense filled dark') \
                .classes('w-full')
            wallet_input = ui.input('Proxy Wallet Address (0x...)') \
                .props('dense filled dark') \
                .classes('w-full')
            if current_wallet:
                wallet_input.value = current_wallet

            with ui.row().classes('gap-3 mt-4 justify-end w-full'):
                ui.button('Cancel', on_click=lambda: dlg.submit(None)).props('flat color=grey')

                async def save_creds():
                    new_pk = pk_input.value.strip()
                    new_wallet = wallet_input.value.strip()

                    if new_pk and not new_pk.startswith('0x'):
                        ui.notify('Private key must start with 0x', type='negative')
                        return
                    if new_wallet and not new_wallet.startswith('0x'):
                        ui.notify('Wallet address must start with 0x', type='negative')
                        return

                    # Read existing .env, update only POLY_ lines
                    lines_out = []
                    found_pk = False
                    found_wallet = False
                    if os.path.exists(env_path):
                        with open(env_path) as f:
                            for line in f:
                                if line.strip().startswith('POLY_PRIVATE_KEY=') and new_pk:
                                    lines_out.append(f'POLY_PRIVATE_KEY={new_pk}\n')
                                    found_pk = True
                                elif line.strip().startswith('POLY_WALLET_ADDRESS=') and new_wallet:
                                    lines_out.append(f'POLY_WALLET_ADDRESS={new_wallet}\n')
                                    found_wallet = True
                                else:
                                    lines_out.append(line)
                    if new_pk and not found_pk:
                        lines_out.append(f'POLY_PRIVATE_KEY={new_pk}\n')
                    if new_wallet and not found_wallet:
                        lines_out.append(f'POLY_WALLET_ADDRESS={new_wallet}\n')

                    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(env_path), suffix='.tmp')
                    with os.fdopen(fd, 'w') as f:
                        f.writelines(lines_out)
                    os.replace(tmp, env_path)

                    # Delete cached L2 creds (force re-derive on next client init)
                    creds_path = os.path.join(axc, 'secrets', '.poly_api_creds.json')
                    if new_pk and os.path.exists(creds_path):
                        os.remove(creds_path)

                    ui.notify('Credentials saved. Restart dashboard to apply.', type='positive')
                    dlg.submit('saved')

                ui.button('Save', on_click=save_creds).props('color=green')

        dlg.open()
        result = await dlg
        if result == 'saved':
            await refresh_auth()

    # Wire the forward reference
    _open_settings_ref[0] = open_settings

    return auth_container, refresh_auth
