"""Backtest studio page — embeds original backtest.html.

backtest.html has target="_top" on 主控台 link already.
No JS injection needed.
"""

from nicegui import ui


def render_backtest_page():
    """Render the backtest studio — full height iframe."""
    ui.add_head_html('''
        <style>
        .backtest-frame {
            width: 100%;
            height: calc(100vh - 52px);
            border: none;
            background: #0d0d0f;
            display: block;
        }
        </style>
    ''')

    import time
    cache_bust = int(time.time())
    ui.element('iframe').props(
        f'src="/backtest.html?v={cache_bust}" id="bt-frame"'
    ).classes('backtest-frame')
