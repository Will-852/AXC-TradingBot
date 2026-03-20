"""Backtest studio page — embeds original backtest.html self-contained.

Injects CSS to force horizontal top-bar layout (TradingView style):
- Control bar: single horizontal line, no wrapping
- Parameters panel: collapsed by default
- Right panel: 240px
- Chart gets maximum space
"""

from nicegui import ui


def render_backtest_page():
    """Render the backtest studio — full height, TradingView layout."""
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

    # Inject layout overrides into iframe (same-origin)
    ui.add_body_html('''
        <script>
        (function() {
            var frame = document.getElementById('bt-frame');
            if (!frame) return;
            frame.addEventListener('load', function() {
                try {
                    var doc = frame.contentDocument;
                    if (!doc) return;

                    var style = doc.createElement('style');
                    style.textContent = `
                        /* ═══════════════════════════════════════
                           TradingView-style: controls on TOP
                           Chart fills full width below
                           ═══════════════════════════════════════ */

                        /* Control bar — force single horizontal line */
                        .control-bar {
                            flex-wrap: nowrap !important;
                            overflow-x: auto !important;
                            padding: 3px 8px !important;
                            gap: 6px !important;
                            white-space: nowrap !important;
                            min-height: 0 !important;
                        }
                        .control-bar select {
                            height: 26px !important;
                            font-size: 12px !important;
                            padding: 1px 4px !important;
                            min-width: 0 !important;
                        }
                        .control-bar input[type="number"] {
                            height: 26px !important;
                            width: 70px !important;
                            font-size: 12px !important;
                            padding: 1px 4px !important;
                        }
                        .control-bar .btn-run {
                            height: 28px !important;
                            font-size: 12px !important;
                            padding: 2px 10px !important;
                        }
                        .control-bar .btn-load,
                        .control-bar .btn-load-existing {
                            height: 26px !important;
                            font-size: 11px !important;
                            padding: 2px 8px !important;
                        }
                        .control-bar button {
                            height: 26px !important;
                            font-size: 11px !important;
                            padding: 2px 6px !important;
                        }
                        /* Hide labels that waste space */
                        .control-bar label {
                            font-size: 11px !important;
                        }

                        /* Topbar — more compact */
                        .topbar {
                            height: 30px !important;
                            padding: 0 10px !important;
                            font-size: 12px !important;
                        }

                        /* Param panel — compact, collapsed by default */
                        .param-panel {
                            font-size: 12px !important;
                        }
                        .param-toggle {
                            padding: 3px 8px !important;
                            font-size: 12px !important;
                        }
                        .param-body {
                            padding: 4px 8px !important;
                            font-size: 11px !important;
                        }
                        .param-body input,
                        .param-body select {
                            height: 24px !important;
                            font-size: 11px !important;
                        }

                        /* Right panel — narrower */
                        .main-grid {
                            grid-template-columns: 1fr 4px var(--rp-width, 240px) !important;
                        }

                        /* Chart header — TradingView compact */
                        .chart-header {
                            padding: 1px 6px !important;
                            gap: 2px !important;
                            font-size: 11px !important;
                            min-height: 0 !important;
                        }
                        .itab {
                            padding: 2px 5px !important;
                            font-size: 11px !important;
                        }
                        .interval-tabs {
                            gap: 1px !important;
                        }

                        /* Draw toolbar */
                        .draw-toolbar {
                            padding: 1px 6px !important;
                            gap: 2px !important;
                            font-size: 11px !important;
                        }
                        .draw-btn {
                            padding: 2px 5px !important;
                            font-size: 11px !important;
                        }

                        /* Right panel content — dense */
                        .right-panel {
                            font-size: 12px !important;
                            overflow-y: auto !important;
                        }
                        .card-section {
                            padding: 6px !important;
                            margin-bottom: 4px !important;
                        }
                        .stat-grid {
                            gap: 3px !important;
                        }
                        .stat-item {
                            padding: 3px 5px !important;
                        }
                        .stat-label {
                            font-size: 9px !important;
                        }
                        .stat-value {
                            font-size: 13px !important;
                        }
                        .trade-row {
                            padding: 2px 5px !important;
                            font-size: 11px !important;
                        }

                        /* Guide panel — hide by default */
                        #guide-panel {
                            display: none !important;
                        }

                        /* Live toolbar — compact */
                        #live-toolbar {
                            top: 30px !important;
                            right: 8px !important;
                        }
                        #live-toolbar button {
                            height: 26px !important;
                            font-size: 11px !important;
                            padding: 2px 8px !important;
                        }
                    `;
                    doc.head.appendChild(style);

                    // Fix links
                    var links = doc.querySelectorAll('a[href="/"]');
                    links.forEach(function(a) { a.setAttribute('target', '_parent'); });

                } catch(e) {
                    console.warn('Backtest iframe inject:', e);
                }
            });
        })();
        </script>
    ''')
