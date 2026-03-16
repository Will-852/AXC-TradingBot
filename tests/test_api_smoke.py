"""
test_api_smoke.py — Smoke tests for backtest API endpoints.

These tests verify the API contract without running a full backtest.
They mock the worker to avoid network calls.
"""

import json
import os
import sys
import threading
from unittest.mock import patch, MagicMock

import pytest

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
if os.path.join(AXC_HOME, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))


# Minimal mock result simulating engine output
MOCK_RESULT = {
    "symbol": "BTCUSDT",
    "total_trades": 2,
    "winners": 1,
    "losers": 1,
    "final_balance": 10100.0,
    "return_pct": 1.0,
    "win_rate": 50.0,
    "profit_factor": 2.0,
    "expectancy": 50.0,
    "max_drawdown_pct": 1.0,
    "avg_win": 200.0,
    "avg_loss": -100.0,
    "sharpe_ratio": 0.5,
    "max_win_streak": 1,
    "max_loss_streak": 1,
    "by_strategy": {"range": {"count": 1, "wins": 1, "win_rate": 100.0, "avg_pnl": 200.0}},
    "trades": [
        {"symbol": "BTCUSDT", "side": "LONG", "entry": 50000, "exit": 51000,
         "pnl": 200, "sl_price": 49000, "tp_price": 52000,
         "entry_time": "2025-01-01 00:00:00", "exit_time": "2025-01-01 12:00:00",
         "exit_reason": "TP", "strategy": "range"},
    ],
    "equity_curve": [
        {"time": "2025-01-01 00:00:00", "equity": 10000, "balance": 10000, "positions": 0, "mode": "RANGE"},
    ],
    "indicator_series": [
        {"time": "2025-01-01 00:00:00", "bb_upper": 51000, "bb_lower": 49000,
         "bb_basis": 50000, "rsi": 45, "adx": 18, "atr": 500,
         "ema_fast": 50100, "ema_slow": 49900, "ma50": 50000, "ma200": 49500,
         "macd_line": 50, "macd_signal": 30, "macd_hist": 20,
         "stoch_k": 55, "stoch_d": 50, "volume_ratio": 1.2, "mode": "RANGE"},
    ],
    "clusters": 0,
    "independent_decisions": 2,
    "cluster_adj_wr": 50.0,
}


class TestBacktestAPIContract:
    """Verify backtest API returns expected structure."""

    def test_bt_run_returns_job_id(self):
        """POST /api/backtest/run should return job_id."""
        from scripts.dashboard.backtest import handle_bt_run, _bt_jobs, _bt_lock

        with patch("scripts.dashboard.backtest._get_bt_pool") as mock_pool:
            mock_future = MagicMock()
            mock_pool.return_value.submit.return_value = mock_future
            mock_future.add_done_callback = MagicMock()

            body = json.dumps({"symbol": "BTCUSDT", "days": 30, "balance": 10000})
            code, data = handle_bt_run(body)

            assert code == 200
            assert "job_id" in data
            assert data["status"] == "running"

    def test_bt_run_invalid_json(self):
        """POST with bad JSON should return 400."""
        from scripts.dashboard.backtest import handle_bt_run
        code, data = handle_bt_run("not json{{{")
        assert code == 400

    def test_bt_status_not_found(self):
        """GET status for non-existent job should return 404."""
        from scripts.dashboard.backtest import handle_bt_status
        code, data = handle_bt_status({"job_id": ["nonexistent_job_123"]})
        assert code == 404

    def test_bt_status_done_has_indicator_series(self):
        """When job is done, result should include indicator_series."""
        from scripts.dashboard.backtest import handle_bt_status, _bt_jobs, _bt_lock

        test_job_id = "TEST_SMOKE_001"
        with _bt_lock:
            _bt_jobs[test_job_id] = {
                "status": "done",
                "symbol": "BTCUSDT",
                "days": 30,
                "result": MOCK_RESULT,
                "error": None,
            }

        try:
            code, data = handle_bt_status({"job_id": [test_job_id]})
            assert code == 200
            assert data["status"] == "done"
            assert "indicator_series" in data["result"]
            assert "sharpe_ratio" in data["result"]
            assert "by_strategy" in data["result"]
        finally:
            with _bt_lock:
                _bt_jobs.pop(test_job_id, None)

    def test_bt_results_missing_params(self):
        """GET results without symbol should return 400."""
        from scripts.dashboard.backtest import handle_bt_results
        code, data = handle_bt_results({"symbol": [""], "days": [""]})
        assert code == 400
