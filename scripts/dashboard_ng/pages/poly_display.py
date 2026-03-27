"""poly_display.py — Display logic for the Polymarket page.

update_all() is the mega-function that refreshes every UI container.
Extracted so polymarket.py stays under 400 lines.

All functions receive a `ctx` dict with UI container references:
    poly_data, kpi_labels, mode_btn, positions_container, pnl_chart,
    strategy_container, cal_container, trades_container, cb_container,
    refresh_fn (async callback)
"""

import json
import logging
import os
from datetime import datetime

from nicegui import ui, run

from scripts.dashboard_ng.theme import GREEN_LIGHT, RED

log = logging.getLogger('axc.poly')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))


def update_cycle_status(cycle_container, status: dict):
    """Render pipeline cycle status into the given container."""
    cycle_container.clear()
    with cycle_container:
        running = status.get('running', False)
        with ui.row().classes('items-center gap-2'):
            if running:
                ui.spinner(size='sm')
                ui.label('Pipeline running...').classes('text-yellow-400 text-sm')
            else:
                ui.icon('check_circle').classes('text-green-400 text-sm')
                ui.label('Idle').classes('text-gray-400 text-sm')
        last_run = status.get('last_run', 0)
        if last_run:
            ts_str = datetime.fromtimestamp(last_run).strftime('%H:%M:%S')
            dur = status.get('last_duration', 0)
            ui.label(f'Last run: {ts_str} ({dur:.1f}s)').classes('text-xs text-gray-500 font-mono')
        err = status.get('last_error')
        if err:
            ui.label(f'Last error: {err}').classes('text-xs text-red-400')


def update_all(ctx: dict):
    """Refresh every display container on the Polymarket page.

    💰 Displays balance, PnL, risk mode — wrong values = user misjudges position.
    """
    poly_data = ctx['poly_data']
    kpi_labels = ctx['kpi_labels']
    mode_btn = ctx['mode_btn']
    positions_container = ctx['positions_container']
    pnl_chart = ctx['pnl_chart']
    strategy_container = ctx['strategy_container']
    cal_container = ctx['cal_container']
    trades_container = ctx['trades_container']
    cb_container = ctx['cb_container']
    refresh_fn = ctx['refresh_fn']

    d = poly_data['data']
    state = d.get('state', {})

    # ── KPIs ──
    positions = state.get('positions', [])
    bal = state.get('usdc_balance', 0)
    exposure = state.get('total_exposure', 0)
    exposure_pct = state.get('exposure_pct', 0)

    live = poly_data.get('live', {})
    live_bal = live.get('balance')
    if live_bal and isinstance(live_bal, (int, float)):
        bal = live_bal

    positions_value = poly_data.get('_positions_value', 0)
    initial_deposit = poly_data.get('_initial_deposit', 0)
    total_account = (bal if isinstance(bal, (int, float)) else 0) + positions_value
    total_pnl = total_account - initial_deposit if initial_deposit else 0

    kpi_labels['usdc_balance'].text = f'${bal:.2f}' if isinstance(bal, (int, float)) else str(bal)
    pnl_color = 'text-green-400' if total_pnl >= 0 else 'text-red-400'
    kpi_labels['total_pnl'].text = f'${total_pnl:+.2f}'
    kpi_labels['total_pnl'].classes(replace=f'text-lg font-bold font-mono {pnl_color}')

    # Win rate from Data API positions
    n_closed = poly_data.get('_closed_wins', 0) + poly_data.get('_closed_losses', 0)
    if n_closed > 0:
        wr = poly_data.get('_closed_wins', 0) / n_closed * 100
        kpi_labels['win_rate'].text = f'{wr:.0f}% ({poly_data.get("_closed_wins", 0)}/{n_closed})'
    else:
        kpi_labels['win_rate'].text = f'{poly_data.get("_open_count", 0)} open'

    n_orders = live.get('open_orders', 0)
    if n_orders:
        kpi_labels['positions_count'].text = f'{n_orders} orders'
    else:
        kpi_labels['positions_count'].text = str(len(positions)) if isinstance(positions, list) else '0'

    kpi_labels['total_exposure'].text = f'${exposure:.2f}' if isinstance(exposure, (int, float)) else str(exposure)
    kpi_labels['exposure_pct'].text = f'{exposure_pct:.1f}%' if isinstance(exposure_pct, (int, float)) else str(exposure_pct)
    kpi_labels['last_updated'].text = datetime.now().strftime('%H:%M:%S')

    # Mode button  💰 DRY/LIVE display — wrong = user thinks they're in dry when live
    is_dry = state.get('dry_run', True)
    mode_str = 'DRY RUN' if is_dry else 'LIVE'
    mode_btn.text = f'Mode: {mode_str}'
    mode_btn.props(f'color={"deep-orange" if is_dry else "green"}')

    # Risk mode from mm_state.json
    try:
        mm_path = os.path.join(AXC_HOME, 'polymarket', 'logs', 'mm_state.json')
        if os.path.exists(mm_path):
            with open(mm_path) as f:
                mm = json.load(f)
            risk_mode = mm.get('_risk_mode', '')
            if risk_mode:
                risk_color = (
                    'text-red-400' if risk_mode == 'STOPPED'
                    else 'text-amber-400' if risk_mode == 'PROTECTION'
                    else 'text-green-400'
                )
                kpi_labels['exposure_pct'].text = f'{exposure_pct:.1f}% ({risk_mode})'
                kpi_labels['exposure_pct'].classes(replace=f'text-lg font-bold font-mono {risk_color}')
    except Exception as e:
        log.warning('risk_mode KPI update failed: %s', e)

    # ── Positions — LIVE orders from CLOB ──
    live_orders = live.get('orders', [])
    positions_container.clear()
    with positions_container:
        if live_orders:
            rows = []
            for o in live_orders:
                try:
                    sz = f"{float(o.get('size', 0)):.2f}"
                except (TypeError, ValueError):
                    sz = str(o.get('size', ''))
                ct = o.get('created', '')
                try:
                    if isinstance(ct, (int, float)):
                        ct = datetime.fromtimestamp(ct).strftime('%m-%d %H:%M')
                    elif isinstance(ct, str) and ct.isdigit():
                        ct = datetime.fromtimestamp(int(ct)).strftime('%m-%d %H:%M')
                    elif isinstance(ct, str) and len(ct) > 16:
                        ct = ct[:16]
                except (ValueError, OSError):
                    ct = str(ct)[:16]
                rows.append({
                    'time': ct,
                    'side': o.get('side', ''),
                    'outcome': o.get('outcome', ''),
                    'size': sz,
                    'price': f"${o.get('price', '?')}",
                })
            ui.aggrid({
                'columnDefs': [
                    {'field': 'time', 'headerName': 'Created', 'width': 110},
                    {'field': 'side', 'width': 50},
                    {'field': 'outcome', 'width': 55},
                    {'field': 'size', 'width': 65, 'type': 'rightAligned'},
                    {'field': 'price', 'width': 65, 'type': 'rightAligned'},
                ],
                'rowData': rows,
                'headerHeight': 30, 'rowHeight': 28,
            }).classes('h-80 w-full ag-theme-balham-dark')
        else:
            ui.label('No open orders').classes('text-gray-600 text-sm')

    # ── PnL chart ──
    pnl_series = d.get('pnl_series', [])
    if pnl_series:
        times = []
        values = []
        for p in pnl_series:
            ts = p.get('timestamp', p.get('time', ''))
            if isinstance(ts, str) and len(ts) > 16:
                ts = ts[5:16]
            times.append(ts)
            values.append(p.get('cumulative', p.get('pnl', 0)))
        pnl_chart.options['xAxis']['data'] = times
        pnl_chart.options['series'][0]['data'] = values
        pnl_chart.update()

    # ── Strategy breakdown ──
    breakdown = d.get('strategy_breakdown', {})
    strategy_container.clear()
    with strategy_container:
        if breakdown and isinstance(breakdown, dict):
            with ui.row().classes('gap-3 flex-wrap'):
                for strat, count in sorted(
                    breakdown.items(),
                    key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0),
                ):
                    if isinstance(count, (int, float)) and count > 0:
                        ui.badge(f'{strat}: {count}', color='grey').classes('font-mono text-[12px]')
        else:
            ui.label('No strategy data').classes('text-gray-600 text-sm')

    # ── Calibration ──
    cal = d.get('calibration', {})
    cal_container.clear()
    with cal_container:
        brier = cal.get('brier')
        edge = cal.get('edge')
        if isinstance(brier, (int, float)):
            ui.label(f'Brier: {brier:.4f}').classes('text-sm font-mono text-gray-400')
        if isinstance(edge, (int, float)):
            color = 'text-green-400' if edge > 0 else 'text-red-400'
            ui.label(f'Edge: {edge:.4f}').classes(f'text-sm font-mono {color}')
        elif isinstance(edge, dict):
            matched = edge.get('matched', 0)
            predictions = edge.get('edge_predictions_count', 0)
            ui.label(f'Edge: {matched} matched / {predictions} predictions').classes(
                'text-sm font-mono text-gray-400'
            )
        if brier is None and edge is None:
            ui.label('No calibration data').classes('text-gray-600 text-sm')

    # ── Trades — LIVE CLOB trades ──
    live_trades = live.get('recent_trades', [])
    trades_container.clear()
    with trades_container:
        if live_trades:
            rows = []
            for t in live_trades[:20]:
                mt = t.get('match_time', '')
                try:
                    if isinstance(mt, (int, float)) or (isinstance(mt, str) and mt.isdigit()):
                        mt = datetime.fromtimestamp(int(mt)).strftime('%m-%d %H:%M')
                    elif isinstance(mt, str) and len(mt) > 16:
                        mt = mt[:16]
                except (ValueError, OSError):
                    pass
                try:
                    sz = f"{float(t.get('size', 0)):.2f}"
                except (TypeError, ValueError):
                    sz = str(t.get('size', ''))
                rows.append({
                    'time': mt,
                    'side': t.get('side', ''),
                    'outcome': t.get('outcome', ''),
                    'size': sz,
                    'price': f"${t.get('price', '?')}",
                })
            ui.aggrid({
                'columnDefs': [
                    {'field': 'time', 'headerName': 'Time', 'width': 140},
                    {'field': 'side', 'width': 50},
                    {'field': 'outcome', 'width': 60},
                    {'field': 'size', 'width': 65, 'type': 'rightAligned'},
                    {'field': 'price', 'width': 70, 'type': 'rightAligned'},
                ],
                'rowData': rows,
                'headerHeight': 32, 'rowHeight': 30, 'domLayout': 'autoHeight',
            }).classes('w-full ag-theme-balham-dark')
        else:
            state_trades = d.get('trades', [])
            if state_trades:
                ui.label('(State file trades — pipeline stale)').classes('text-[11px] text-yellow-400')
                for t in state_trades[:5]:
                    ts = t.get('timestamp', t.get('time', ''))[:16] if t.get('timestamp') else ''
                    ui.label(f"{ts} {t.get('side', '')} ${t.get('price', '')}").classes(
                        'text-xs text-gray-500'
                    )
            else:
                ui.label('No trades').classes('text-gray-600 text-sm')

    # ── Circuit breakers (with RESET) ──
    cbs = d.get('circuit_breakers', [])
    cb_container.clear()
    with cb_container:
        if cbs:
            for cb in cbs:
                if isinstance(cb, dict):
                    name = cb.get('service', cb.get('name', '?'))
                    cb_state = cb.get('state', 'closed')
                    failures = cb.get('failure_count', 0)
                    triggered = cb_state != 'closed'
                else:
                    name = str(cb)
                    triggered = False
                    failures = 0

                with ui.row().classes('items-center gap-2 w-full'):
                    ui.icon('circle').classes('text-[9px]').style(
                        f'color: {RED if triggered else GREEN_LIGHT}')
                    ui.label(str(name)).classes('text-sm text-gray-300 min-w-[100px]')
                    ui.label(f'{cb_state}' if isinstance(cb, dict) else '').classes(
                        'text-[11px] font-mono text-gray-500'
                    )
                    if failures:
                        ui.label(f'({failures} failures)').classes('text-[11px] text-yellow-400')
                    if triggered:
                        async def reset_cb(n=name):
                            from scripts.dashboard.polymarket import handle_polymarket_reset_cb
                            result = await run.io_bound(
                                handle_polymarket_reset_cb, json.dumps({'service': n})
                            )
                            if isinstance(result, tuple):
                                _, rdata = result
                            else:
                                rdata = result
                            if rdata.get('ok'):
                                ui.notify(f'CB "{n}" reset', type='positive')
                            else:
                                ui.notify(f'Reset failed: {rdata.get("error")}', type='negative')
                            await refresh_fn()

                        ui.button('Reset', on_click=reset_cb) \
                            .props('flat dense size=xs color=red')
        else:
            ui.label('No circuit breakers').classes('text-gray-600 text-sm')
