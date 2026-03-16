"""
test_dashboard_bt.py — Dashboard backtest endpoint validation tests.

Tests input validation, job lifecycle, and param override propagation.
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
if os.path.join(AXC_HOME, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))


class TestInputValidation:
    """Phase 5C: Bad input should return 400 with friendly error."""

    def test_invalid_symbol(self):
        from scripts.dashboard.backtest import handle_bt_run
        body = json.dumps({"symbol": "DOGEUSDT", "days": 30})
        code, data = handle_bt_run(body)
        assert code == 400
        assert "not allowed" in data["error"]

    def test_negative_days(self):
        from scripts.dashboard.backtest import handle_bt_run
        body = json.dumps({"symbol": "BTCUSDT", "days": -5})
        code, data = handle_bt_run(body)
        assert code == 400
        assert "days" in data["error"]

    def test_days_too_large(self):
        from scripts.dashboard.backtest import handle_bt_run
        body = json.dumps({"symbol": "BTCUSDT", "days": 500})
        code, data = handle_bt_run(body)
        assert code == 400
        assert "days" in data["error"]

    def test_balance_too_low(self):
        from scripts.dashboard.backtest import handle_bt_run
        body = json.dumps({"symbol": "BTCUSDT", "days": 30, "balance": 10})
        code, data = handle_bt_run(body)
        assert code == 400
        assert "balance" in data["error"]

    def test_non_numeric_param_override(self):
        from scripts.dashboard.backtest import handle_bt_run
        body = json.dumps({"symbol": "BTCUSDT", "days": 30,
                          "param_overrides": {"bb_touch_tol": "bad"}})
        code, data = handle_bt_run(body)
        assert code == 400
        assert "numeric" in data["error"]

    def test_valid_input_accepted(self):
        from scripts.dashboard.backtest import handle_bt_run
        with patch("scripts.dashboard.backtest._get_bt_pool") as mock_pool:
            mock_future = MagicMock()
            mock_pool.return_value.submit.return_value = mock_future
            mock_future.add_done_callback = MagicMock()

            body = json.dumps({"symbol": "BTCUSDT", "days": 60, "balance": 10000})
            code, data = handle_bt_run(body)
            assert code == 200
            assert data["status"] == "running"


class TestJobLifecycle:
    """Job submit → poll → done flow."""

    def test_submit_and_poll(self):
        from scripts.dashboard.backtest import handle_bt_run, handle_bt_status, _bt_jobs, _bt_lock

        with patch("scripts.dashboard.backtest._get_bt_pool") as mock_pool:
            mock_future = MagicMock()
            mock_pool.return_value.submit.return_value = mock_future
            mock_future.add_done_callback = MagicMock()

            body = json.dumps({"symbol": "ETHUSDT", "days": 14})
            code, data = handle_bt_run(body)
            assert code == 200
            job_id = data["job_id"]

            # Poll → should be running
            code2, data2 = handle_bt_status({"job_id": [job_id]})
            assert code2 == 200
            assert data2["status"] == "running"

            # Simulate completion
            with _bt_lock:
                _bt_jobs[job_id]["status"] = "done"
                _bt_jobs[job_id]["result"] = {"total_trades": 5, "indicator_series": []}

            code3, data3 = handle_bt_status({"job_id": [job_id]})
            assert code3 == 200
            assert data3["status"] == "done"
            assert "indicator_series" in data3["result"]

            # Cleanup
            with _bt_lock:
                _bt_jobs.pop(job_id, None)


class TestParamOverridePropagation:
    """Param overrides reach the worker."""

    def test_strategy_params_passed(self):
        from scripts.dashboard.backtest import handle_bt_run

        with patch("scripts.dashboard.backtest._get_bt_pool") as mock_pool:
            mock_future = MagicMock()
            mock_pool.return_value.submit.return_value = mock_future
            mock_future.add_done_callback = MagicMock()

            body = json.dumps({
                "symbol": "BTCUSDT", "days": 30,
                "strategy_params": {"range_sl": 1.5, "risk_pct": 0.03},
                "param_overrides": {"bb_touch_tol": 0.008},
                "mode_confirmation": 3,
            })
            code, data = handle_bt_run(body)
            assert code == 200

            # Verify submit was called with correct args
            call_args = mock_pool.return_value.submit.call_args
            assert call_args[1]["strategy_params"]["range_sl"] == 1.5
            assert call_args[1]["param_overrides"]["bb_touch_tol"] == 0.008
            assert call_args[1]["mode_confirmation"] == 3
