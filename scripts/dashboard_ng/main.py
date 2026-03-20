"""AXC Trading Dashboard — NiceGUI Edition.

Entry point. Run: python3 scripts/dashboard_ng/main.py
"""

import sys
import os
import logging

# Ensure AXC_HOME is set and project root is in sys.path
AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
os.environ['AXC_HOME'] = AXC_HOME
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware

# Port — different from current dashboard to allow parallel running
PORT = 5567


# Relax CSP for embedded HTML pages (backtest.html has 5220 lines of inline JS)
class RelaxCSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith('/canvas/') or path == '/backtest.html':
            # MutableHeaders doesn't have pop — use del with guard
            hdrs = response.headers
            for h in ('content-security-policy', 'x-frame-options'):
                if h in hdrs:
                    del hdrs[h]
        return response

app.add_middleware(RelaxCSPMiddleware)

# Static assets
app.add_static_files('/svg', os.path.join(AXC_HOME, 'canvas', 'svg'))

# Start background collector on app startup
from scripts.dashboard_ng.state import background_collector
app.on_startup(background_collector)

# Import layout
from scripts.dashboard_ng.layout import create_layout

# Register backtest API routes (so backtest.html can talk to this server)
from scripts.dashboard_ng.utils.backtest_api import register_backtest_routes
register_backtest_routes()

# Serve canvas directory for backtest.html and its assets
app.add_static_files('/canvas', os.path.join(AXC_HOME, 'canvas'))


# ── Pages ──

@ui.page('/')
def main_page():
    create_layout(active_path='/')

    from scripts.dashboard_ng.components.stats_cards import render_stats_row
    from scripts.dashboard_ng.components.risk_boxes import render_risk_boxes
    from scripts.dashboard_ng.components.controls import render_controls
    from scripts.dashboard_ng.components.positions import render_positions
    from scripts.dashboard_ng.components.action_plan import render_action_plan
    from scripts.dashboard_ng.components.pnl_chart import render_pnl_chart
    from scripts.dashboard_ng.components.analytics import (
        render_fee_breakdown, render_trade_stats, render_funding_rates,
        render_news_sentiment, render_trade_history, render_activity_log,
        render_scan_log,
    )

    from scripts.dashboard_ng.components.chat import render_chat_toggle
    from scripts.dashboard_ng.components.exchange_connect import render_exchange_panel
    from scripts.dashboard_ng.components.health import render_health_panel, render_suggest_mode

    with ui.column().classes('w-full p-6 gap-6'):
        # Controls row
        render_controls()

        # Exchange connections + health + suggest (expandable)
        with ui.row().classes('gap-2 flex-wrap w-full'):
            with ui.column().classes('flex-1 min-w-[300px]'):
                render_exchange_panel()
            with ui.column().classes('flex-1 min-w-[300px]'):
                render_health_panel()
            with ui.column().classes('flex-1 min-w-[200px]'):
                render_suggest_mode()

        ui.separator().classes('bg-gray-700')

        # KPI stats
        render_stats_row()

        # Risk boxes
        render_risk_boxes()

        ui.separator().classes('bg-gray-700')

        # Positions + orders
        render_positions()

        ui.separator().classes('bg-gray-700')

        # Action plan table
        render_action_plan()

        ui.separator().classes('bg-gray-700')

        # PnL chart
        render_pnl_chart()

        # Analytics row
        with ui.row().classes('gap-4 flex-wrap w-full'):
            render_fee_breakdown()
            render_trade_stats()
            render_news_sentiment()

        # Funding rates
        render_funding_rates()

        # Scan log
        render_scan_log()

        # Trade history
        render_trade_history()

        # Activity log
        render_activity_log()

        ui.separator().classes('bg-gray-700')

        # System workflow diagrams
        from scripts.dashboard_ng.components.diagrams import render_all_diagrams
        render_all_diagrams()

    # Floating chat button
    render_chat_toggle()


@ui.page('/backtest')
def backtest_page():
    create_layout(active_path='/backtest')
    from scripts.dashboard_ng.pages.backtest import render_backtest_page
    with ui.column().classes('w-full p-1 gap-0'):
        render_backtest_page()


@ui.page('/polymarket')
def polymarket_page():
    create_layout(active_path='/polymarket')
    from scripts.dashboard_ng.pages.polymarket import render_polymarket_page
    with ui.column().classes('w-full p-6 gap-4'):
        ui.label('Polymarket').classes('text-2xl font-bold')
        render_polymarket_page()


@ui.page('/paper')
def paper_page():
    create_layout(active_path='/paper')
    from scripts.dashboard_ng.pages.paper import render_paper_page
    with ui.column().classes('w-full p-6 gap-4'):
        render_paper_page()


@ui.page('/docs')
def docs_page():
    create_layout(active_path='/docs')
    from scripts.dashboard_ng.pages.docs import render_docs_page
    with ui.column().classes('w-full p-6'):
        render_docs_page()


def main():
    ui.run(
        title='AXC Trading',
        port=PORT,
        host='127.0.0.1',
        reload=False,
        show=False,
        storage_secret='axc-ng-2026',
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
