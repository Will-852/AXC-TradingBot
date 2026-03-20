"""Backtest studio page — embeds original backtest.html self-contained.

The original backtest.html (5220 lines) uses KLineChart v9 with 12 custom
indicators, Web Workers, Binance WebSocket, and custom Canvas draw().
None of this can run natively in NiceGUI — it MUST be browser JS.

Strategy: serve the original HTML + JS files from /canvas/* and proxy all
/api/backtest/* endpoints via backtest_api.py. The iframe points to THIS
server — no dependency on the old dashboard.
"""

from nicegui import ui


def render_backtest_page():
    """Render the backtest studio by embedding the original HTML in an iframe.

    All API endpoints are proxied by this NiceGUI server via backtest_api.py,
    so the old dashboard is NOT needed.
    """
    # Full-height iframe — backtest.html is served at /canvas/backtest.html
    ui.html('''
        <iframe
            id="bt-frame"
            src="/canvas/backtest.html"
            style="
                width: 100%;
                height: calc(100vh - 100px);
                border: none;
                border-radius: 4px;
                background: #0d0d0f;
            "
            allow="clipboard-write"
        ></iframe>
    ''').classes('w-full')

    # Make "主控台" link in iframe navigate the parent frame
    ui.add_body_html('''
        <script>
        document.getElementById('bt-frame').addEventListener('load', function() {
            try {
                var doc = this.contentDocument;
                var links = doc.querySelectorAll('a[href="/"]');
                links.forEach(function(a) { a.setAttribute('target', '_parent'); });
            } catch(e) {}
        });
        </script>
    ''')
