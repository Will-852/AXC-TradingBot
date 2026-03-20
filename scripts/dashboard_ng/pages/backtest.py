"""Backtest studio page — embeds original backtest.html self-contained.

Uses iframe with CSP relaxation (middleware in main.py) to allow
the 5220 lines of inline KLineChart JS to execute.
"""

from nicegui import ui


def render_backtest_page():
    """Render the backtest studio by embedding the original HTML in an iframe."""
    # Use ui.element('iframe') instead of ui.html() to avoid HTML sanitisation
    ui.element('iframe').props(
        'src="/backtest.html" id="bt-frame"'
    ).style(
        'width:100%; height:calc(100vh - 100px); border:none; '
        'border-radius:4px; background:#0d0d0f;'
    )
