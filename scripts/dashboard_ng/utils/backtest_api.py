"""Backtest API routes — proxy the old dashboard's /api/backtest/* endpoints.

These are FastAPI routes added to NiceGUI's app, so backtest.html can
talk to the NiceGUI server directly (no need for old dashboard).
"""

import sys
import os
import json
import logging
from typing import Optional

from fastapi import Request, Query
from fastapi.responses import JSONResponse
from nicegui import app

log = logging.getLogger('axc.bt_api')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

# Lazily import backend functions to avoid circular imports
_bt = None

def _get_bt():
    global _bt
    if _bt is None:
        from scripts.dashboard import backtest as bt_mod
        _bt = bt_mod
    return _bt


def register_backtest_routes():
    """Register all /api/backtest/* routes on NiceGUI's FastAPI app."""

    @app.get('/api/backtest/list')
    async def bt_list():
        bt = _get_bt()
        result = bt.handle_bt_list()
        return result

    @app.get('/api/backtest/klines')
    async def bt_klines(symbol: str = '', days: str = '60', interval: str = '1h'):
        bt = _get_bt()
        qs = {'symbol': [symbol], 'days': [days], 'interval': [interval]}
        from nicegui import run
        code, data = await run.io_bound(bt.handle_bt_klines, qs)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/results')
    async def bt_results(file: str = '', symbol: str = '', days: str = ''):
        bt = _get_bt()
        qs = {'file': [file], 'symbol': [symbol], 'days': [days]}
        from nicegui import run
        code, data = await run.io_bound(bt.handle_bt_results, qs)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/status')
    async def bt_status(job_id: str = ''):
        bt = _get_bt()
        qs = {'job_id': [job_id]}
        code, data = bt.handle_bt_status(qs)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/aggtrades')
    async def bt_aggtrades(symbol: str = '', days: str = ''):
        bt = _get_bt()
        qs = {'symbol': [symbol], 'days': [days]}
        from nicegui import run
        code, data = await run.io_bound(bt.handle_bt_aggtrades, qs)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/aggtrades/status')
    async def bt_aggtrades_status(job_id: str = ''):
        bt = _get_bt()
        qs = {'job_id': [job_id]}
        code, data = bt.handle_bt_aggtrades_status(qs)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/export')
    async def bt_export(file: str = ''):
        bt = _get_bt()
        qs = {'file': [file]}
        from nicegui import run
        code, data = await run.io_bound(bt.handle_bt_export, qs)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/shootout/list')
    async def bt_shootout_list():
        bt = _get_bt()
        from nicegui import run
        code, data = await run.io_bound(bt.handle_bt_shootout_list)
        return JSONResponse(content=data, status_code=code)

    @app.get('/api/backtest/shootout/detail')
    async def bt_shootout_detail(file: str = ''):
        bt = _get_bt()
        qs = {'file': [file]}
        from nicegui import run
        code, data = await run.io_bound(bt.handle_bt_shootout_detail, qs)
        return JSONResponse(content=data, status_code=code)

    @app.post('/api/backtest/run')
    async def bt_run(request: Request):
        bt = _get_bt()
        body = await request.body()
        from nicegui import run
        result = await run.io_bound(bt.handle_bt_run, body.decode())
        if isinstance(result, tuple):
            code, data = result
            return JSONResponse(content=data, status_code=code)
        return result

    @app.post('/api/backtest/nfs-fvz')
    async def bt_nfs_fvz(request: Request):
        bt = _get_bt()
        body = await request.body()
        from nicegui import run
        result = await run.io_bound(bt.handle_bt_nfs_fvz, body.decode())
        if isinstance(result, tuple):
            code, data = result
            return JSONResponse(content=data, status_code=code)
        return result

    @app.post('/api/backtest/import')
    async def bt_import(request: Request):
        bt = _get_bt()
        body = await request.body()
        from nicegui import run
        result = await run.io_bound(bt.handle_bt_import, body.decode())
        if isinstance(result, tuple):
            code, data = result
            return JSONResponse(content=data, status_code=code)
        return result

    # Also proxy /api/data for live position overlay
    @app.get('/api/data')
    async def api_data():
        from scripts.dashboard_ng.state import get_data
        return get_data()

    log.info('Backtest API routes registered')
