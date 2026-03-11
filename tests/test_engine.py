"""
test_engine.py — BacktestEngine unit tests.

Tests engine mechanics: PnL math, cluster detection, summary stats,
indicator_series output, and basic run completion.
"""

import os
import sys

import pytest

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
if os.path.join(AXC_HOME, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.engine import BacktestEngine, BTPosition, BTTrade, COMMISSION_RATE, WARMUP_CANDLES


class TestBuildParams:
    """_build_params() merges TIMEFRAME_PARAMS + PRODUCT_OVERRIDES + overrides."""

    def test_default_params(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        assert "bb_length" in engine.params_1h
        assert "rsi_period" in engine.params_1h

    def test_override_applied(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine(
            "BTCUSDT", uptrend_1h, uptrend_4h, quiet=True,
            param_overrides={"bb_touch_tol": 0.01}
        )
        assert engine.params_1h["bb_touch_tol"] == 0.01

    def test_product_override(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("ETHUSDT", uptrend_1h, uptrend_4h, quiet=True)
        # ETHUSDT has rsi_long=32 in PRODUCT_OVERRIDES
        assert engine.params_1h["rsi_long"] == 32


class TestClosePosition:
    """_close_position() PnL calculation with commission."""

    def test_long_win(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        pos = BTPosition("LONG", 50000, 49000, 52000, 10000, "2025-01-01", "range")
        engine.positions.append(pos)
        engine._close_position(pos, 51000, "2025-01-02", "TP")
        trade = engine.trades[-1]
        # raw_pnl_pct = (51000-50000)/50000 = 0.02
        # pnl = 10000 * (0.02 - 0.001) = 190.0
        assert trade.pnl == 190.0
        assert trade.exit_reason == "TP"
        assert trade.side == "LONG"

    def test_long_loss(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        pos = BTPosition("LONG", 50000, 49000, 52000, 10000, "2025-01-01", "range")
        engine.positions.append(pos)
        engine._close_position(pos, 49500, "2025-01-02", "SL")
        trade = engine.trades[-1]
        # raw_pnl_pct = (49500-50000)/50000 = -0.01
        # pnl = 10000 * (-0.01 - 0.001) = -110.0
        assert trade.pnl == -110.0

    def test_short_win(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        pos = BTPosition("SHORT", 50000, 51000, 48000, 10000, "2025-01-01", "trend")
        engine.positions.append(pos)
        engine._close_position(pos, 49000, "2025-01-02", "TP")
        trade = engine.trades[-1]
        # raw_pnl_pct = (50000-49000)/50000 = 0.02
        # pnl = 10000 * (0.02 - 0.001) = 190.0
        assert trade.pnl == 190.0

    def test_short_loss(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        pos = BTPosition("SHORT", 50000, 51000, 48000, 10000, "2025-01-01", "trend")
        engine.positions.append(pos)
        engine._close_position(pos, 51000, "2025-01-02", "SL")
        trade = engine.trades[-1]
        # raw_pnl_pct = (50000-51000)/50000 = -0.02
        # pnl = 10000 * (-0.02 - 0.001) = -210.0
        assert trade.pnl == -210.0


class TestDetectClusters:
    """_detect_clusters() groups trades by time/direction/symbol."""

    def test_no_clusters_sparse(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        engine.trades = [
            BTTrade("BTCUSDT", "LONG", 50000, 51000, 100, 49000, 52000,
                    "2025-01-01 00:00:00", "2025-01-01 12:00:00", "TP", "range"),
            BTTrade("BTCUSDT", "LONG", 50000, 51000, 100, 49000, 52000,
                    "2025-01-02 00:00:00", "2025-01-02 12:00:00", "TP", "range"),
        ]
        clusters = engine._detect_clusters()
        assert len(clusters) == 0  # 24h gap → no cluster

    def test_cluster_detected(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        engine.trades = [
            BTTrade("BTCUSDT", "LONG", 50000, 51000, 100, 49000, 52000,
                    "2025-01-01 00:00:00", "2025-01-01 01:00:00", "TP", "range"),
            BTTrade("BTCUSDT", "LONG", 50000, 51000, 100, 49000, 52000,
                    "2025-01-01 02:00:00", "2025-01-01 03:00:00", "TP", "range"),
        ]
        clusters = engine._detect_clusters()
        assert len(clusters) == 1
        assert len(clusters[0]) == 2


class TestSummary:
    """_summary() stats formulas."""

    def test_empty_trades(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        result = engine._summary()
        assert result["total_trades"] == 0
        assert result["indicator_series"] == []

    def test_stats_with_trades(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        engine.trades = [
            BTTrade("BTCUSDT", "LONG", 50000, 51000, 200, 49000, 52000,
                    "2025-01-01 00:00:00", "2025-01-01 12:00:00", "TP", "range"),
            BTTrade("BTCUSDT", "SHORT", 50000, 49500, -100, 51000, 48000,
                    "2025-01-02 00:00:00", "2025-01-02 12:00:00", "SL", "trend"),
        ]
        engine.balance = engine.initial_balance + 100  # net +100
        engine.equity_curve = [
            {"time": "2025-01-01", "equity": 10000, "balance": 10000, "positions": 0, "mode": "RANGE"},
            {"time": "2025-01-02", "equity": 10200, "balance": 10200, "positions": 0, "mode": "RANGE"},
            {"time": "2025-01-03", "equity": 10100, "balance": 10100, "positions": 0, "mode": "RANGE"},
        ]
        result = engine._summary()
        assert result["total_trades"] == 2
        assert result["winners"] == 1
        assert result["losers"] == 1
        assert result["win_rate"] == 50.0
        assert result["sharpe_ratio"] != 0
        assert result["max_win_streak"] == 1
        assert result["max_loss_streak"] == 1
        assert "range" in result["by_strategy"]
        assert "trend" in result["by_strategy"]
        assert result["by_strategy"]["range"]["count"] == 1
        assert result["by_strategy"]["trend"]["count"] == 1


class TestRun:
    """Engine.run() basic integration — produces trades and indicator_series."""

    def test_run_completes(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        result = engine.run()
        assert "total_trades" in result
        assert "indicator_series" in result
        assert "equity_curve" in result
        assert "sharpe_ratio" in result
        assert isinstance(result["equity_curve"], list)
        assert isinstance(result["indicator_series"], list)
        # indicator_series should have entries for candles with enough data
        assert len(result["indicator_series"]) > 0

    def test_indicator_series_keys(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        result = engine.run()
        if result["indicator_series"]:
            entry = result["indicator_series"][0]
            expected_keys = {
                "time", "bb_upper", "bb_lower", "bb_basis",
                "rsi", "adx", "atr", "ema_fast", "ema_slow",
                "ma50", "ma200", "macd_line", "macd_signal", "macd_hist",
                "stoch_k", "stoch_d", "volume_ratio", "mode",
            }
            assert expected_keys.issubset(set(entry.keys()))

    def test_range_only_mode(self, range_1h, range_4h):
        engine = BacktestEngine(
            "BTCUSDT", range_1h, range_4h, quiet=True,
            allowed_modes=["RANGE"]
        )
        result = engine.run()
        assert "total_trades" in result
        # All trades should be range strategy (or no trades)
        for t in result["trades"]:
            if hasattr(t, "strategy"):
                assert t.strategy == "range"

    def test_equity_curve_length(self, uptrend_1h, uptrend_4h):
        engine = BacktestEngine("BTCUSDT", uptrend_1h, uptrend_4h, quiet=True)
        result = engine.run()
        # equity_curve should have one entry per test candle
        expected = len(uptrend_1h) - WARMUP_CANDLES
        assert len(result["equity_curve"]) == expected
