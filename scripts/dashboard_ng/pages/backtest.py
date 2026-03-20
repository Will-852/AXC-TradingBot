"""Backtest studio page — embeds original backtest.html self-contained.

Injects CSS overrides to optimize layout:
- Right panel default 250px (was 300px)
- Control bar more compact
- Sidebar starts collapsed for more chart space
- TradingView-style proportions
"""

from nicegui import ui


def render_backtest_page():
    """Render the backtest studio — full height, layout-optimized iframe."""
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

    # Inject layout fixes into iframe after load (same-origin, allowed)
    ui.add_body_html('''
        <script>
        (function() {
            var frame = document.getElementById('bt-frame');
            if (!frame) return;
            frame.addEventListener('load', function() {
                try {
                    var doc = frame.contentDocument;
                    if (!doc) return;

                    // Inject CSS overrides for better proportions
                    var style = doc.createElement('style');
                    style.textContent = `
                        /* ── AXC NiceGUI Layout Overrides ── */

                        /* Narrower right panel (250px, was 300px) */
                        .main-grid {
                            grid-template-columns: 1fr 6px var(--rp-width, 250px) !important;
                        }

                        /* More compact control bar */
                        .control-bar {
                            padding: 4px 10px !important;
                            gap: 6px !important;
                            font-size: 13px !important;
                        }
                        .control-bar select,
                        .control-bar input {
                            height: 28px !important;
                            font-size: 12px !important;
                            padding: 2px 6px !important;
                        }
                        .control-bar .btn-run {
                            height: 30px !important;
                            font-size: 12px !important;
                            padding: 4px 12px !important;
                        }

                        /* Compact topbar (hide "AXC" brand — NiceGUI header handles it) */
                        .topbar {
                            height: 32px !important;
                            padding: 0 12px !important;
                            font-size: 12px !important;
                        }
                        .topbar .brand {
                            font-size: 13px !important;
                        }

                        /* Tighter param panel */
                        .param-panel {
                            font-size: 12px !important;
                        }
                        .param-body {
                            padding: 6px 10px !important;
                            gap: 4px !important;
                        }
                        .param-body label {
                            font-size: 11px !important;
                        }
                        .param-body input, .param-body select {
                            height: 26px !important;
                            font-size: 11px !important;
                        }

                        /* Chart header bar — more compact */
                        .chart-header {
                            padding: 2px 8px !important;
                            gap: 4px !important;
                            font-size: 11px !important;
                        }
                        .itab {
                            padding: 2px 6px !important;
                            font-size: 11px !important;
                        }

                        /* Draw toolbar compact */
                        .draw-toolbar {
                            padding: 2px 8px !important;
                            gap: 4px !important;
                        }

                        /* Right panel — tighter spacing */
                        .right-panel {
                            font-size: 12px !important;
                        }
                        .right-panel .card-section {
                            padding: 8px !important;
                        }
                        .stat-grid {
                            gap: 4px !important;
                        }
                        .stat-item {
                            padding: 4px 6px !important;
                        }
                        .stat-label {
                            font-size: 10px !important;
                        }
                        .stat-value {
                            font-size: 13px !important;
                        }

                        /* Trade log compact */
                        .trade-row {
                            padding: 3px 6px !important;
                            font-size: 11px !important;
                        }

                        /* Guide panel — start closed */
                        #guide-panel .guide-body {
                            display: none !important;
                        }
                    `;
                    doc.head.appendChild(style);

                    // Collapse right sidebar by default for more chart space
                    var sidebarKey = 'bt_sidebar_open';
                    if (!localStorage.getItem(sidebarKey)) {
                        var rpWidth = doc.documentElement.style;
                        rpWidth.setProperty('--rp-width', '250px');
                    }

                    // Fix links to navigate parent
                    var links = doc.querySelectorAll('a[href="/"]');
                    links.forEach(function(a) { a.setAttribute('target', '_parent'); });

                } catch(e) {
                    console.warn('Backtest iframe CSS inject failed:', e);
                }
            });
        })();
        </script>
    ''')
