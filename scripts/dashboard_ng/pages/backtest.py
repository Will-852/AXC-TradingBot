"""Backtest studio page — embeds original backtest.html self-contained.

Full-height iframe with minimal chrome. Sidebar can be toggled to give
more chart space (like TradingView's full-screen mode).
"""

from nicegui import ui


def render_backtest_page():
    """Render the backtest studio — full available height, no padding."""
    # Inject CSS to maximize iframe space
    ui.add_head_html('''
        <style>
        /* Remove NiceGUI page padding for backtest */
        .backtest-frame {
            width: 100%;
            height: calc(100vh - 52px);
            border: none;
            background: #0d0d0f;
            display: block;
        }
        </style>
    ''')

    ui.element('iframe').props(
        'src="/backtest.html" id="bt-frame"'
    ).classes('backtest-frame')
