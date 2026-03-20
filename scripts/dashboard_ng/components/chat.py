"""AI Chat panel — floating, toggle show/hide."""

import logging

from nicegui import ui, run

log = logging.getLogger('axc.chat')


def render_chat_toggle():
    """Render floating chat button + panel."""
    # Chat panel (initially hidden)
    with ui.dialog().props('position=right maximized=false') as chat_dialog:
        with ui.card().classes('w-96 h-[500px] flex flex-col p-0'):
            # Header
            with ui.row().classes('items-center justify-between p-3 bg-gray-800 border-b border-gray-700'):
                ui.label('AXC AI').classes('font-bold')
                with ui.row().classes('gap-1'):
                    mode_toggle = ui.toggle(['Fast', 'Deep'], value='Fast') \
                        .props('dense no-caps size=xs color=indigo')
                    ui.button(icon='close', on_click=chat_dialog.close) \
                        .props('flat round dense size=sm')

            # Messages area
            messages_area = ui.scroll_area().classes('flex-1 p-3')
            msg_container = ui.column().classes('gap-2 w-full')

            # Chat history
            chat_history = []

            # Input area
            with ui.row().classes('p-3 border-t border-gray-700 gap-2'):
                chat_input = ui.input(placeholder='Ask anything...') \
                    .classes('flex-1').props('dense outlined dark')

                async def send_message():
                    msg = chat_input.value
                    if not msg or not msg.strip():
                        return
                    chat_input.value = ''

                    # Add user message
                    with msg_container:
                        with ui.row().classes('justify-end'):
                            ui.label(msg).classes(
                                'bg-indigo-600 text-white rounded-lg px-3 py-2 text-sm max-w-[280px]')

                    chat_history.append({'role': 'user', 'content': msg})

                    # Call AI
                    try:
                        from scripts.dashboard.chat import handle_chat
                        model = 'deep' if mode_toggle.value == 'Deep' else 'fast'
                        result = await run.io_bound(handle_chat, {
                            'message': msg,
                            'model': model,
                            'history': chat_history[-10:],
                        })

                        reply = result.get('reply', result.get('error', 'No response'))
                        chat_history.append({'role': 'assistant', 'content': reply})

                        with msg_container:
                            ui.markdown(reply).classes(
                                'bg-gray-700 rounded-lg px-3 py-2 text-sm max-w-[280px]')

                    except Exception as e:
                        log.error('Chat error: %s', e)
                        with msg_container:
                            ui.label(f'Error: {e}').classes(
                                'bg-red-900 text-red-200 rounded-lg px-3 py-2 text-sm')

                    messages_area.scroll_to(percent=1.0)

                send_btn = ui.button(icon='send', on_click=send_message) \
                    .props('flat round dense color=indigo')
                chat_input.on('keydown.enter', send_message)

    # Floating action button
    ui.button(icon='chat', on_click=chat_dialog.open) \
        .props('fab color=indigo') \
        .classes('fixed bottom-20 right-6 z-50')
