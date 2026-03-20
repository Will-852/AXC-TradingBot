"""Backtest studio page — embeds original backtest.html.

backtest.html CSS has been directly modified for TradingView-style
horizontal layout. No CSS injection needed.
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

    ui.element('iframe').props(
        'src="/backtest.html" id="bt-frame"'
    ).classes('backtest-frame')

    # Fix "主控台" link to navigate parent frame
    ui.add_body_html('''
        <script>
        (function() {
            var frame = document.getElementById('bt-frame');
            if (!frame) return;
            frame.addEventListener('load', function() {
                try {
                    var doc = frame.contentDocument;
                    if (!doc) return;
                    doc.querySelectorAll('a[href="/"]').forEach(function(a) {
                        a.setAttribute('target', '_parent');
                    });
                } catch(e) {}
            });
        })();
        </script>
    ''')
