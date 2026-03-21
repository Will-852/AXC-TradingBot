"""Per-market focused view — distinct-baguette style.

Shows one market at a time: KPI cards, PNL scenarios, countdown,
live prices chart, spread, position delta.
"""

import logging
from nicegui import ui, run

log = logging.getLogger('axc.poly_view')


def render_market_view():
    """Render per-market focused dashboard section."""
    from scripts.dashboard_ng.utils.poly_market_data import (
        get_active_markets, get_live_prices,
    )

    # State
    view_state = {'selected_cid': None, 'prices_history': [], 'pos_history': [],
                  'start_ts': None, 'window_minutes': 15}

    # ── Market selector + countdown ──
    with ui.row().classes('items-center justify-between w-full'):
        with ui.row().classes('items-center gap-3'):
            ui.label('MARKET FOCUS').classes('text-xs text-gray-500 uppercase tracking-wide')
            market_select = ui.select(
                [], value=None, label='Select Market',
            ).classes('w-80').props('dense filled dark color=indigo')

        with ui.row().classes('items-center gap-3'):
            countdown_label = ui.label('--:--').classes('text-2xl font-mono font-bold text-amber-400')
            progress_bar = ui.linear_progress(value=0).props('color=amber size=6px rounded').classes('w-32')

    # ── KPI Cards Row ──
    with ui.row().classes('gap-2 flex-wrap w-full'):
        def _kpi(label, key):
            with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[120px] flex-1'):
                ui.label(label).classes('text-[10px] text-gray-600 uppercase tracking-wider')
                lbl = ui.label('—').classes('text-lg font-mono font-bold')
                sub = ui.label('').classes('text-[11px] text-gray-500')
                return lbl, sub

        avg_sum_val, avg_sum_sub = _kpi('AVG SUM', 'avg_sum')
        delta_val, delta_sub = _kpi('POSITION Δ', 'delta')
        pnl_down_val, pnl_down_sub = _kpi('PNL IF DOWN', 'pnl_down')
        pnl_up_val, pnl_up_sub = _kpi('PNL IF UP', 'pnl_up')

    with ui.row().classes('gap-2 w-full'):
        capital_val, capital_sub = _kpi('TOTAL CAPITAL', 'capital')
        ev_val, ev_sub = _kpi('EXPECTED VALUE', 'ev')

    # ── Decision Engine (signal data) ──
    ui.label('DECISION ENGINE').classes('text-xs text-gray-500 uppercase tracking-wide')
    signal_container = ui.row().classes('gap-2 flex-wrap w-full')

    ui.separator().classes('bg-gray-700')

    # ── Price Chart (ECharts dual-line) ──
    ui.label('PRICES').classes('text-xs text-gray-500 uppercase tracking-wide')
    with ui.row().classes('items-center gap-2'):
        price_info = ui.label('').classes('text-[11px] font-mono text-gray-500')

    # Shared X axis config: elapsed minutes, fixed window
    def _x_elapsed(show_label=True):
        return {'type': 'value', 'min': 0, 'max': 15,
                'axisLabel': {'color': '#475569', 'fontSize': 11, 'show': show_label,
                              'formatter': '{value}m'},
                'axisLine': {'lineStyle': {'color': '#1e2d45'}},
                'splitLine': {'show': False}}

    price_chart = ui.echart({
        'darkMode': True, 'backgroundColor': 'transparent',
        'tooltip': {'trigger': 'axis', 'formatter': None},
        'legend': {'data': ['Up', 'Down'], 'textStyle': {'color': '#64748b', 'fontSize': 11}, 'top': 0},
        'grid': {'left': 45, 'right': 60, 'top': 25, 'bottom': 20},
        'xAxis': _x_elapsed(),
        'yAxis': {'type': 'value', 'min': 0, 'max': 1,
                  'axisLabel': {'color': '#475569', 'fontSize': 11, 'formatter': '${value}'},
                  'splitLine': {'lineStyle': {'color': '#1e2d45', 'type': 'dashed'}}},
        'series': [
            {'name': 'Up', 'type': 'line', 'data': [], 'smooth': True,
             'showSymbol': False, 'lineStyle': {'width': 2, 'color': '#34d399'},
             'markLine': {'data': [], 'silent': True}},
            {'name': 'Down', 'type': 'line', 'data': [], 'smooth': True,
             'showSymbol': False, 'lineStyle': {'width': 2, 'color': '#f87171'}},
        ],
    }).classes('h-40 w-full')

    # ── Spread Chart ──
    with ui.row().classes('items-center gap-2 mt-2'):
        ui.label('SPREAD').classes('text-xs text-gray-500 uppercase tracking-wide')
        spread_info = ui.label('').classes('text-[11px] font-mono text-amber-400')
    spread_chart = ui.echart({
        'darkMode': True, 'backgroundColor': 'transparent',
        'grid': {'left': 45, 'right': 60, 'top': 10, 'bottom': 20},
        'xAxis': _x_elapsed(show_label=False),
        'yAxis': {'type': 'value',
                  'axisLabel': {'color': '#475569', 'fontSize': 11, 'formatter': '${value}'},
                  'splitLine': {'lineStyle': {'color': '#1e2d45', 'type': 'dashed'}}},
        'series': [
            {'type': 'line', 'data': [], 'step': 'end',
             'showSymbol': False, 'lineStyle': {'width': 1.5, 'color': '#f59e0b'},
             'areaStyle': {'color': 'rgba(245,158,11,0.15)'}},
        ],
    }).classes('h-24 w-full')

    # ── Positions Chart (shares over time) ──
    with ui.row().classes('gap-2 w-full mt-2'):
        with ui.column().classes('flex-1'):
            with ui.row().classes('items-center gap-2'):
                ui.label('POSITIONS').classes('text-xs text-gray-500 uppercase tracking-wide')
                pos_info = ui.label('').classes('text-[11px] font-mono text-gray-500')
            pos_chart = ui.echart({
                'darkMode': True, 'backgroundColor': 'transparent',
                'tooltip': {'trigger': 'axis'},
                'legend': {'data': ['Up', 'Down'], 'textStyle': {'color': '#64748b', 'fontSize': 10}, 'top': 0},
                'grid': {'left': 50, 'right': 15, 'top': 25, 'bottom': 20},
                'xAxis': _x_elapsed(),
                'yAxis': {'type': 'value',
                          'axisLabel': {'color': '#475569', 'fontSize': 10},
                          'splitLine': {'lineStyle': {'color': '#1e2d45', 'type': 'dashed'}}},
                'series': [
                    {'name': 'Up', 'type': 'line', 'data': [], 'smooth': True,
                     'showSymbol': False, 'lineStyle': {'width': 2, 'color': '#34d399'},
                     'areaStyle': {'color': 'rgba(52,211,153,0.1)'}},
                    {'name': 'Down', 'type': 'line', 'data': [], 'smooth': True,
                     'showSymbol': False, 'lineStyle': {'width': 2, 'color': '#f87171'},
                     'areaStyle': {'color': 'rgba(248,113,113,0.1)'}},
                ],
            }).classes('h-32 w-full')

        with ui.column().classes('flex-1'):
            with ui.row().classes('items-center gap-2'):
                ui.label('AVG PRICES').classes('text-xs text-gray-500 uppercase tracking-wide')
                avg_info = ui.label('').classes('text-[11px] font-mono text-gray-500')
                sum_badge = ui.badge('SUM —', color='grey').classes('text-[11px] font-mono')
            avg_chart = ui.echart({
                'darkMode': True, 'backgroundColor': 'transparent',
                'tooltip': {'trigger': 'axis'},
                'legend': {'data': ['Up', 'Down'], 'textStyle': {'color': '#64748b', 'fontSize': 10}, 'top': 0},
                'grid': {'left': 45, 'right': 15, 'top': 25, 'bottom': 20},
                'xAxis': _x_elapsed(),
                'yAxis': {'type': 'value', 'min': 0, 'max': 1,
                          'axisLabel': {'color': '#475569', 'fontSize': 10, 'formatter': '${value}'},
                          'splitLine': {'lineStyle': {'color': '#1e2d45', 'type': 'dashed'}}},
                'series': [
                    {'name': 'Up', 'type': 'line', 'data': [], 'smooth': True,
                     'showSymbol': False, 'lineStyle': {'width': 2, 'color': '#34d399'}},
                    {'name': 'Down', 'type': 'line', 'data': [], 'smooth': True,
                     'showSymbol': False, 'lineStyle': {'width': 2, 'color': '#f87171'}},
                ],
            }).classes('h-32 w-full')

    # ── Update functions ──
    def update_market_list():
        markets = get_active_markets()
        options = {m['cid']: f"{m['title'][:50]} ({m['remaining_str']})" for m in markets}
        market_select.options = options
        if markets and not view_state['selected_cid']:
            # Auto-select first market with shares
            for m in markets:
                if m['up_shares'] > 0 or m['down_shares'] > 0:
                    market_select.value = m['cid']
                    view_state['selected_cid'] = m['cid']
                    break
            else:
                market_select.value = markets[0]['cid']
                view_state['selected_cid'] = markets[0]['cid']

    def on_market_change(e):
        view_state['selected_cid'] = e.value
        view_state['prices_history'] = []  # reset chart history
        view_state['pos_history'] = []
        view_state['start_ts'] = None  # reset elapsed origin
        update_kpis()

    market_select.on_value_change(on_market_change)

    def update_kpis():
        markets = get_active_markets()
        cid = view_state['selected_cid']
        if not cid:
            return

        m = next((x for x in markets if x['cid'] == cid), None)
        if not m:
            return

        # Countdown
        countdown_label.text = m['remaining_str']
        progress_bar.value = m['progress_pct'] / 100

        has_position = m['up_shares'] > 0 or m['down_shares'] > 0

        # KPI cards
        if m['avg_sum']:
            avg_sum_val.text = f"{m['avg_sum']:.4f}"
            profit_pct = (m['avg_sum'] - 1.0) * 100
            avg_sum_sub.text = f"{profit_pct:+.2f}% profit"
        else:
            avg_sum_val.text = '—'
            avg_sum_sub.text = 'no position' if not has_position else ''

        delta_val.text = f"{m['delta_pct']:+.1f}%"
        delta_sub.text = f"{m['delta_shares']:+.1f} diff" if has_position else 'watching'

        if has_position:
            pnl_down_val.text = f"${m['pnl_if_down']:+.2f}"
            pnl_down_val.classes(replace='text-lg font-mono font-bold ' +
                                 ('text-green-400' if m['pnl_if_down'] >= 0 else 'text-red-400'))
            pnl_down_sub.text = f"Capital: ${m['capital']:.2f}"

            pnl_up_val.text = f"${m['pnl_if_up']:+.2f}"
            pnl_up_val.classes(replace='text-lg font-mono font-bold ' +
                                 ('text-green-400' if m['pnl_if_up'] >= 0 else 'text-red-400'))
            pnl_up_sub.text = f"Capital: ${m['capital']:.2f}"
        else:
            pnl_down_val.text = '—'
            pnl_down_val.classes(replace='text-lg font-mono font-bold text-gray-500')
            pnl_down_sub.text = 'no position'
            pnl_up_val.text = '—'
            pnl_up_val.classes(replace='text-lg font-mono font-bold text-gray-500')
            pnl_up_sub.text = 'no position'

        capital_val.text = f"${m['capital']:.2f}" if has_position else '—'
        capital_sub.text = m.get('phase', '') if m.get('phase') else ''

        # ── Accumulate position history (every 5s, elapsed minutes X axis) ──
        import time as _time
        if not view_state['start_ts']:
            ws = m.get('window_end_ms', 0)
            remaining = m.get('remaining_s', 0)
            if ws and remaining:
                window_total = m.get('window_total_s', 15 * 60)
                view_state['start_ts'] = _time.time() - (window_total - remaining)
                view_state['window_minutes'] = window_total / 60
            else:
                view_state['start_ts'] = _time.time()
        _elapsed = (_time.time() - view_state['start_ts']) / 60
        _win = view_state['window_minutes']

        _ph = view_state['pos_history']
        _ph.append({
            'x': round(_elapsed, 2),
            'up_s': m['up_shares'], 'dn_s': m['down_shares'],
            'up_a': m['up_avg'], 'dn_a': m['down_avg'],
        })
        if len(_ph) > 180:
            _ph.pop(0)

        # Update positions chart — [x, y] pairs
        pos_chart.options['xAxis']['max'] = int(_win)
        pos_chart.options['series'][0]['data'] = [[p['x'], p['up_s']] for p in _ph]
        pos_chart.options['series'][1]['data'] = [[p['x'], p['dn_s']] for p in _ph]
        pos_chart.update()
        pos_info.text = f'▲ {m["up_shares"]:.1f}  ▼ {m["down_shares"]:.1f}'

        # Update avg prices chart — [x, y] pairs
        avg_chart.options['xAxis']['max'] = int(_win)
        avg_chart.options['series'][0]['data'] = [[p['x'], p['up_a']] for p in _ph]
        avg_chart.options['series'][1]['data'] = [[p['x'], p['dn_a']] for p in _ph]
        avg_chart.update()
        avg_info.text = f'▲ ${m["up_avg"]:.3f}  ▼ ${m["down_avg"]:.3f}'
        _sum = m['avg_sum']
        if _sum:
            _sum_color = 'green' if _sum < 1.0 else 'red' if _sum > 1.02 else 'grey'
            sum_badge._props['color'] = _sum_color
            sum_badge.text = f'SUM {_sum:.4f}'
            sum_badge.update()

        # Decision Engine signals
        from scripts.dashboard_ng.utils.poly_market_data import get_latest_signals
        signals = get_latest_signals()
        # Match signal by cid prefix (signals use truncated cid)
        sig = None
        for sig_cid, sig_data in signals.items():
            if cid.startswith(sig_cid) or sig_cid.startswith(cid[:8]):
                sig = sig_data
                break

        signal_container.clear()
        with signal_container:
            if sig:
                bridge = sig.get('bridge', 0)
                fair = sig.get('fair', 0)
                cvd = sig.get('cvd', 0)
                m1 = sig.get('m1', 0)
                m1_sigma = sig.get('m1_sigma', 0)
                ob_adj = sig.get('ob_adj', 0)
                sym = sig.get('sym', '')
                ts = sig.get('ts', '')
                if isinstance(ts, str) and len(ts) > 16:
                    ts = ts[11:19]  # HH:MM:SS

                # Direction badge
                direction = 'UP' if bridge > 0.5 else 'DOWN'
                dir_score = abs(bridge - 0.5) * 200  # 0-100 scale
                dir_color = 'green' if direction == 'UP' else 'red'
                ui.badge(f'{direction} {dir_score:.0f}', color=dir_color).classes('text-[12px] font-mono')

                # Signal strength (bridge distance from 0.5)
                strength = min(10, dir_score / 5)  # 0-10 scale
                with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[80px]'):
                    ui.label('SIGNAL').classes('text-[10px] text-gray-600 uppercase')
                    ui.label(f'{strength:.1f}/10').classes('text-sm font-mono font-bold')
                    ui.linear_progress(value=strength / 10).props(f'color={dir_color} size=4px rounded')

                # Key metrics as compact badges
                with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[90px]'):
                    ui.label('BRIDGE').classes('text-[10px] text-gray-600 uppercase')
                    b_color = 'text-green-400' if bridge > 0.5 else 'text-red-400'
                    ui.label(f'{bridge:.3f}').classes(f'text-sm font-mono font-bold {b_color}')

                with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[80px]'):
                    ui.label('CVD').classes('text-[10px] text-gray-600 uppercase')
                    c_color = 'text-green-400' if cvd > 0.5 else 'text-red-400' if cvd < 0.3 else 'text-gray-300'
                    ui.label(f'{cvd:.3f}').classes(f'text-sm font-mono font-bold {c_color}')

                with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[80px]'):
                    ui.label('M1').classes('text-[10px] text-gray-600 uppercase')
                    ui.label(f'{m1_sigma:.1f}σ').classes('text-sm font-mono font-bold')

                with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[80px]'):
                    ui.label('OB ADJ').classes('text-[10px] text-gray-600 uppercase')
                    ui.label(f'{ob_adj:+.4f}').classes('text-sm font-mono font-bold')

                with ui.card().classes('p-2 bg-gray-800 border border-gray-700 min-w-[70px]'):
                    ui.label('FAIR').classes('text-[10px] text-gray-600 uppercase')
                    ui.label(f'${fair:.3f}').classes('text-sm font-mono font-bold')

                ui.label(f'{sym} @ {ts}').classes('text-[10px] text-gray-600 font-mono self-end')
            else:
                ui.label('No signal data for this market').classes('text-gray-600 text-sm')

    async def poll_live_prices():
        """Poll live midpoint + spread for selected market."""
        cid = view_state['selected_cid']
        if not cid:
            return

        markets = get_active_markets()
        m = next((x for x in markets if x['cid'] == cid), None)
        if not m or not m.get('up_token') or not m.get('dn_token'):
            return

        prices = await run.io_bound(get_live_prices, m['up_token'], m['dn_token'])
        if not prices:
            return

        up_mid = prices.get('up_mid', 0)
        dn_mid = prices.get('dn_mid', 0)
        up_spread = prices.get('up_spread', 0)

        try:
            up_mid = float(up_mid)
            dn_mid = float(dn_mid)
            up_spread = float(up_spread)
        except (TypeError, ValueError):
            return

        # EV calculation
        pnl_up = m['pnl_if_up']
        pnl_down = m['pnl_if_down']
        ev = up_mid * pnl_up + (1 - up_mid) * pnl_down
        capital = m['capital'] if m['capital'] > 0 else 1
        roi = ev / capital * 100

        ev_val.text = f"{ev:+.2f} EV"
        ev_sub.text = f"{roi:+.1f}% ROI"
        ev_val.classes(replace='text-lg font-mono font-bold ' +
                       ('text-green-400' if ev >= 0 else 'text-red-400'))

        # Compute elapsed minutes from window start
        import time as _time
        if not view_state['start_ts']:
            # Use market window_start if available, else now
            ws = m.get('window_end_ms', 0)
            remaining = m.get('remaining_s', 0)
            if ws and remaining:
                window_total = m.get('window_total_s', 15 * 60)  # 15 min default
                view_state['start_ts'] = _time.time() - (window_total - remaining)
                view_state['window_minutes'] = window_total / 60
            else:
                view_state['start_ts'] = _time.time()
        elapsed_min = (_time.time() - view_state['start_ts']) / 60
        win_min = view_state['window_minutes']

        # Append to price history (with elapsed minutes as X)
        history = view_state['prices_history']
        history.append({'x': round(elapsed_min, 2), 'up': up_mid, 'dn': dn_mid, 'spread': up_spread})
        if len(history) > 90:
            history.pop(0)

        # Update price chart — data as [x, y] pairs for value axis
        ups = [[p['x'], p['up']] for p in history]
        dns = [[p['x'], p['dn']] for p in history]
        price_chart.options['xAxis']['max'] = int(win_min)
        price_chart.options['series'][0]['data'] = ups
        # "Now" vertical dashed line
        price_chart.options['series'][0]['markLine'] = {
            'silent': True, 'symbol': 'none',
            'lineStyle': {'type': 'dashed', 'color': '#f59e0b', 'width': 1},
            'data': [{'xAxis': round(elapsed_min, 1)}],
            'label': {'show': False},
        }
        price_chart.options['series'][1]['data'] = dns
        price_chart.update()

        # Update price info (right side values like distinct-baguette)
        price_info.text = f'▲ ${up_mid:.3f}  ▼ ${dn_mid:.3f}'

        # Update spread chart + info
        spreads = [[p['x'], p['spread']] for p in history]
        spread_chart.options['xAxis']['max'] = int(win_min)
        spread_chart.options['series'][0]['data'] = spreads
        spread_chart.update()
        spread_info.text = f'${up_spread:.4f}'

    # ── Timers ──
    ui.timer(0.5, update_market_list, once=True)
    ui.timer(1, update_kpis, once=True)
    ui.timer(5, update_kpis)           # KPI from local file: fast
    ui.timer(3, poll_live_prices, once=True)
    ui.timer(20, poll_live_prices)     # Live prices: 20s (3 API calls)
