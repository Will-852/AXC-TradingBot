"""
test_indicator_calc.py — Unit tests for indicator calculations.

Tests calc_indicators returns expected keys and evaluate_range_signal logic.
"""

import os
import sys

import pandas as pd
import pytest

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if os.path.join(AXC_HOME, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from indicator_calc import calc_indicators, evaluate_range_signal, TIMEFRAME_PARAMS


class TestCalcIndicators:
    """calc_indicators() should return all expected keys."""

    def test_returns_all_keys(self, uptrend_1h):
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = calc_indicators(uptrend_1h, params)
        expected_keys = [
            "price", "high", "low", "volume",
            "bb_upper", "bb_basis", "bb_lower", "bb_width",
            "rsi", "rsi_prev",
            "adx", "di_plus", "di_minus",
            "ema_fast", "ema_slow",
            "atr",
            "stoch_k", "stoch_d",
            "ma50", "ma200",
            "macd_line", "macd_signal", "macd_hist",
            "obv", "obv_ema",
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_price_is_numeric(self, uptrend_1h):
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = calc_indicators(uptrend_1h, params)
        assert isinstance(result["price"], (int, float))
        assert result["price"] > 0

    def test_rsi_in_range(self, uptrend_1h):
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = calc_indicators(uptrend_1h, params)
        if result["rsi"] is not None:
            assert 0 <= result["rsi"] <= 100

    def test_bb_relationship(self, uptrend_1h):
        """BB upper > basis > lower."""
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = calc_indicators(uptrend_1h, params)
        if all(result[k] is not None for k in ["bb_upper", "bb_basis", "bb_lower"]):
            assert result["bb_upper"] > result["bb_basis"]
            assert result["bb_basis"] > result["bb_lower"]

    def test_atr_positive(self, uptrend_1h):
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = calc_indicators(uptrend_1h, params)
        if result["atr"] is not None:
            assert result["atr"] > 0


class TestEvaluateRangeSignal:
    """evaluate_range_signal() gate logic."""

    def test_high_bb_width_fails(self):
        """Wide BB → R0 fails (not range market)."""
        ind = {"bb_width": 0.15, "adx": 10, "price": 50000,
               "bb_lower": 49000, "bb_upper": 51000, "bb_basis": 50000,
               "rsi": 30, "rsi_prev": 28,
               "rolling_low": 49000, "rolling_high": 51000,
               "stoch_k": 15, "stoch_d": 20, "stoch_k_prev": 22, "stoch_d_prev": 20}
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = evaluate_range_signal(ind, params)
        assert result["range_valid"] is False

    def test_high_adx_fails(self):
        """High ADX → R1 fails (trending market)."""
        ind = {"bb_width": 0.02, "adx": 35, "price": 50000,
               "bb_lower": 49000, "bb_upper": 51000, "bb_basis": 50000,
               "rsi": 30, "rsi_prev": 28,
               "rolling_low": 49000, "rolling_high": 51000,
               "stoch_k": 15, "stoch_d": 20, "stoch_k_prev": 22, "stoch_d_prev": 20}
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = evaluate_range_signal(ind, params)
        assert result["range_valid"] is False

    def test_range_valid_pass(self):
        """Low BB width + low ADX → range valid."""
        ind = {"bb_width": 0.02, "adx": 15, "price": 50000,
               "bb_lower": 49500, "bb_upper": 50500, "bb_basis": 50000,
               "rsi": 30, "rsi_prev": 28,
               "rolling_low": 49800, "rolling_high": 50200,
               "stoch_k": 15, "stoch_d": 20, "stoch_k_prev": 22, "stoch_d_prev": 20}
        params = TIMEFRAME_PARAMS["1h"].copy()
        result = evaluate_range_signal(ind, params)
        assert result["range_valid"] is True
