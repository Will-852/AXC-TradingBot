"""Analytics components — fees, funding, trade stats, news, activity."""

from nicegui import ui

from scripts.dashboard_ng.state import get_data


def render_fee_breakdown():
    """Fee breakdown card."""
    with ui.card().classes('p-3 bg-gray-800 border border-gray-700 flex-1 min-w-[150px]'):
        ui.label('FEES').classes('text-xs text-gray-500 uppercase tracking-wide mb-1')
        fee_container = ui.column().classes('gap-1')

        def update():
            d = get_data()
            fees = d.get('fee_breakdown', {})
            fee_container.clear()
            with fee_container:
                for key, label in [
                    ('realized_pnl', 'Realized PnL'),
                    ('funding', 'Funding'),
                    ('commission', 'Commission'),
                    ('net', 'Net'),
                ]:
                    raw = fees.get(key, 0)
                    try:
                        val = float(raw)
                    except (TypeError, ValueError):
                        val = 0.0
                    color = 'text-green-400' if val >= 0 else 'text-red-400'
                    with ui.row().classes('justify-between w-full'):
                        ui.label(label).classes('text-xs text-gray-400')
                        ui.label(f'${val:.2f}').classes(f'text-xs {color}')

        ui.timer(10, update)


def render_trade_stats():
    """Trade statistics strip."""
    with ui.card().classes('p-3 bg-gray-800 border border-gray-700 flex-1 min-w-[150px]'):
        ui.label('STATS').classes('text-xs text-gray-500 uppercase tracking-wide mb-1')
        stats_container = ui.row().classes('gap-6 flex-wrap')

        def update():
            d = get_data()
            stats = d.get('trade_stats', {})
            stats_container.clear()
            with stats_container:
                for key, label, fmt in [
                    ('win_rate', 'Win Rate', '{:.0f}%'),
                    ('avg_win', 'Avg Win', '${:.2f}'),
                    ('avg_loss', 'Avg Loss', '${:.2f}'),
                    ('profit_factor', 'PF', '{:.2f}'),
                    ('total_trades', 'Trades', '{}'),
                ]:
                    val = stats.get(key, 0)
                    with ui.column().classes('gap-0'):
                        ui.label(label).classes('text-[10px] text-gray-500')
                        try:
                            ui.label(fmt.format(val)).classes('text-sm font-bold')
                        except (ValueError, TypeError):
                            ui.label(str(val)).classes('text-sm font-bold')

        ui.timer(10, update)


def render_funding_rates():
    """Funding rates table."""
    ui.label('FUNDING RATES').classes('text-xs text-gray-500 uppercase tracking-wide')
    rates_container = ui.column().classes('w-full')

    def update():
        d = get_data()
        rates = d.get('funding_rates', {})
        rates_container.clear()
        with rates_container:
            if not rates:
                ui.label('No funding data').classes('text-gray-600 text-sm')
                return

            rows = []
            for symbol, info in rates.items():
                if isinstance(info, dict):
                    rows.append({
                        'symbol': symbol,
                        'rate': f"{info.get('rate', 0)*100:.4f}%",
                        'next': info.get('next_time', '—'),
                    })

            if rows:
                ui.aggrid({
                    'columnDefs': [
                        {'field': 'symbol', 'headerName': 'Symbol', 'width': 100},
                        {'field': 'rate', 'headerName': 'Rate', 'width': 100, 'type': 'rightAligned'},
                        {'field': 'next', 'headerName': 'Next', 'width': 120},
                    ],
                    'rowData': rows,
                    'headerHeight': 32,
                    'rowHeight': 28,
                    'domLayout': 'autoHeight',
                }).classes('w-full ag-theme-balham-dark')

    ui.timer(30, update)


def render_news_sentiment():
    """News sentiment — full scrollable card with all data."""
    with ui.card().classes('p-3 bg-gray-800 border border-gray-700 w-full'):
        ui.label('NEWS SENTIMENT').classes('text-xs text-gray-500 uppercase tracking-wide mb-1')

        # Scrollable content area
        with ui.scroll_area().classes('w-full').style('max-height: 400px;'):
            news_container = ui.column().classes('gap-2 w-full pr-2')

        def update():
            d = get_data()
            news = d.get('news_sentiment', {})
            news_container.clear()
            with news_container:
                # Overall sentiment header
                overall = news.get('overall_sentiment', news.get('overall', '—'))
                confidence = news.get('confidence', 0)
                color = {'bullish': 'text-green-400', 'bearish': 'text-red-400'}.get(
                    str(overall).lower(), 'text-gray-400')

                with ui.row().classes('items-center gap-2'):
                    ui.label(str(overall).upper()).classes(f'text-lg font-bold {color}')
                    if confidence:
                        pct = confidence * 100 if confidence < 1 else confidence
                        ui.label(f'({pct:.0f}%)').classes('text-xs text-gray-500')

                # Overall impact
                impact = news.get('overall_impact', '')
                if impact:
                    ui.label(f'Impact: {impact}').classes('text-xs text-gray-400')

                ui.separator().classes('bg-gray-700 my-1')

                # Per-symbol sentiments
                per_symbol = news.get('sentiment_by_symbol', news.get('per_symbol', {}))
                if per_symbol and isinstance(per_symbol, dict):
                    ui.label('BY SYMBOL').classes('text-[10px] text-gray-600 uppercase tracking-wide')
                    for sym, sent in per_symbol.items():
                        if isinstance(sent, dict):
                            s = sent.get('sentiment', '?')
                            imp = sent.get('impact', '')
                            s_color = 'text-green-400' if s == 'bullish' else 'text-red-400' if s == 'bearish' else 'text-gray-400'
                            with ui.row().classes('items-center gap-2'):
                                ui.label(sym.replace('USDT', '')).classes('text-xs font-bold text-gray-300 min-w-[35px]')
                                ui.label(s).classes(f'text-xs {s_color} min-w-[50px]')
                                if imp:
                                    ui.label(f'{imp}%').classes('text-[10px] text-gray-500 font-mono')
                        else:
                            ui.label(f'{sym}: {sent}').classes('text-xs text-gray-400')

                # Key narratives
                narratives = news.get('key_narratives', [])
                if narratives:
                    ui.separator().classes('bg-gray-700 my-1')
                    ui.label('KEY NARRATIVES').classes('text-[10px] text-gray-600 uppercase tracking-wide')
                    for n in narratives[:5]:
                        if isinstance(n, dict):
                            ui.label(f'• {n.get("text", n.get("narrative", str(n)))}').classes('text-xs text-gray-400')
                        else:
                            ui.label(f'• {n}').classes('text-xs text-gray-400')

                # Risk events (ALL, not just 3)
                risk_events = news.get('risk_events', [])
                if risk_events:
                    ui.separator().classes('bg-gray-700 my-1')
                    ui.label('RISK EVENTS').classes('text-[10px] text-red-500 uppercase tracking-wide')
                    for evt in risk_events:
                        if isinstance(evt, dict):
                            text = evt.get('text', '')
                            src = evt.get('src', '')
                            time_str = evt.get('time', '')
                            with ui.column().classes('gap-0 py-0.5'):
                                ui.label(text).classes('text-xs text-gray-400')
                                meta = []
                                if src:
                                    meta.append(src)
                                if time_str:
                                    meta.append(time_str)
                                if meta:
                                    ui.label(' · '.join(meta)).classes('text-[10px] text-gray-600')
                        else:
                            ui.label(f'• {evt}').classes('text-xs text-gray-500')

                # Summary
                summary = news.get('summary', '')
                if summary:
                    ui.separator().classes('bg-gray-700 my-1')
                    ui.label('SUMMARY').classes('text-[10px] text-gray-600 uppercase tracking-wide')
                    ui.label(summary).classes('text-xs text-gray-400')

                # Meta
                updated = news.get('updated_at', '')
                articles = news.get('articles_analyzed', 0)
                stale = news.get('stale', False)
                if updated or articles:
                    ui.separator().classes('bg-gray-700 my-1')
                    meta_parts = []
                    if articles:
                        meta_parts.append(f'{articles} articles')
                    if updated:
                        meta_parts.append(f'Updated: {str(updated)[:16]}')
                    if stale:
                        meta_parts.append('⚠ STALE')
                    ui.label(' · '.join(meta_parts)).classes('text-[10px] text-gray-600')

        ui.timer(30, update)


def render_trade_history():
    """Recent trade history table."""
    ui.label('RECENT TRADES').classes('text-xs text-gray-500 uppercase tracking-wide')
    trades_container = ui.column().classes('w-full')

    def update():
        d = get_data()
        trades = d.get('exchange_trades', [])
        trades_container.clear()
        with trades_container:
            if not trades:
                ui.label('No recent trades').classes('text-gray-600 text-sm')
                return

            rows = []
            for t in trades[:20]:
                rows.append({
                    'symbol': t.get('symbol', '?'),
                    'side': t.get('side', '?'),
                    'price': t.get('price', '?'),
                    'qty': t.get('qty', '?'),
                    'time': t.get('time', '?'),
                    'pnl': t.get('realizedPnl', '—'),
                })

            ui.aggrid({
                'columnDefs': [
                    {'field': 'time', 'headerName': 'Time', 'width': 110},
                    {'field': 'symbol', 'headerName': 'Symbol', 'width': 90},
                    {'field': 'side', 'headerName': 'Side', 'width': 55,
                     'cellClassRules': {
                         'text-green-400': 'x === "BUY"',
                         'text-red-400': 'x === "SELL"',
                     }},
                    {'field': 'price', 'headerName': 'Price', 'width': 90, 'type': 'rightAligned'},
                    {'field': 'qty', 'headerName': 'Qty', 'width': 60, 'type': 'rightAligned'},
                    {'field': 'pnl', 'headerName': 'PnL', 'width': 65, 'type': 'rightAligned'},
                ],
                'rowData': rows,
                'headerHeight': 32,
                'rowHeight': 28,
            }).classes('h-52 ag-theme-balham-dark')

    ui.timer(10, update)


def render_scan_log():
    """Scan log — last scanner entries."""
    ui.label('SCAN LOG').classes('text-xs text-gray-500 uppercase tracking-wide')
    scan_container = ui.column().classes('w-full max-h-36 overflow-y-auto')

    def update():
        d = get_data()
        scan_log = d.get('scan_log', [])
        scan_container.clear()
        with scan_container:
            if not scan_log:
                ui.label('No scan entries').classes('text-gray-600 text-sm')
                return
            for entry in scan_log[:10]:
                if isinstance(entry, dict):
                    ts = entry.get('time', '')
                    msg = entry.get('msg', entry.get('message', str(entry)))
                elif isinstance(entry, str):
                    ts = ''
                    msg = entry
                else:
                    continue
                with ui.row().classes('gap-2 py-0.5'):
                    if ts:
                        ui.label(ts).classes('text-[10px] text-gray-600 font-mono min-w-[80px]')
                    ui.label(str(msg)[:120]).classes('text-xs text-gray-400')

    ui.timer(10, update)


def render_activity_log():
    """Activity timeline."""
    ui.label('ACTIVITY').classes('text-xs text-gray-500 uppercase tracking-wide')
    log_container = ui.column().classes('w-full max-h-36 overflow-y-auto')

    def update():
        d = get_data()
        activity = d.get('activity_log', [])
        log_container.clear()
        with log_container:
            if not activity:
                ui.label('No activity').classes('text-gray-600 text-sm')
                return

            for item in activity[:20]:
                if isinstance(item, dict):
                    ts = item.get('time', '')
                    msg = item.get('msg', item.get('message', item.get('event', '')))
                    level = item.get('type', item.get('level', 'info'))
                elif isinstance(item, str):
                    ts = ''
                    msg = item
                    level = 'info'
                else:
                    continue

                color = {
                    'error': 'text-red-400',
                    'warn': 'text-yellow-400',
                    'warning': 'text-yellow-400',
                    'heartbeat': 'text-gray-600',
                }.get(level, 'text-gray-400')

                with ui.row().classes('gap-2 py-0.5'):
                    if ts:
                        ui.label(ts).classes('text-[10px] text-gray-600 min-w-[60px]')
                    ui.label(str(msg)).classes(f'text-xs {color}')

    ui.timer(5, update)
