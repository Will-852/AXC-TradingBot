"""
conftest.py — Shared test fixtures for AXC backtest tests.

Provides synthetic OHLCV data fixtures and pre-configured engine instances.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

# Ensure imports work
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)


def _make_ohlcv(n: int, start_price: float = 50000.0,
                trend: float = 0.0, volatility: float = 0.002,
                seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data.

    Args:
        n: number of candles
        start_price: initial price
        trend: per-candle drift (e.g. 0.001 for uptrend)
        volatility: per-candle volatility
        seed: random seed for reproducibility
    """
    rng = np.random.RandomState(seed)
    timestamps = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    prices = [start_price]
    for _ in range(n - 1):
        ret = trend + volatility * rng.randn()
        prices.append(prices[-1] * (1 + ret))

    closes = np.array(prices)
    highs = closes * (1 + abs(volatility) * rng.uniform(0.2, 1.5, n))
    lows = closes * (1 - abs(volatility) * rng.uniform(0.2, 1.5, n))
    opens = closes * (1 + volatility * rng.uniform(-0.5, 0.5, n))
    volumes = rng.uniform(100, 10000, n)

    df = pd.DataFrame({
        "open_time": [int(t.timestamp() * 1000) for t in timestamps],
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "close_time": [int(t.timestamp() * 1000) + 3599999 for t in timestamps],
        "timestamp": timestamps,
    })
    return df


@pytest.fixture
def uptrend_1h():
    """400 candles of 1H uptrend data (200 warmup + 200 test)."""
    return _make_ohlcv(400, trend=0.0005, seed=1)


@pytest.fixture
def range_1h():
    """400 candles of 1H range-bound data (low trend, low volatility)."""
    return _make_ohlcv(400, trend=0.0, volatility=0.001, seed=2)


@pytest.fixture
def spike_1h():
    """400 candles with a mid-period spike."""
    df = _make_ohlcv(400, trend=0.0, volatility=0.001, seed=3)
    # Spike at candle 300
    df.loc[300, "high"] = df.loc[300, "close"] * 1.05
    df.loc[300, "close"] = df.loc[300, "close"] * 1.03
    return df


@pytest.fixture
def uptrend_4h():
    """100 candles of 4H uptrend data."""
    return _make_ohlcv(100, trend=0.002, seed=10).assign(
        close_time=lambda d: d["open_time"] + 14399999
    )


@pytest.fixture
def range_4h():
    """100 candles of 4H range-bound data."""
    return _make_ohlcv(100, trend=0.0, volatility=0.001, seed=20).assign(
        close_time=lambda d: d["open_time"] + 14399999
    )


@pytest.fixture
def spike_4h():
    """100 candles of 4H data matching spike_1h period."""
    return _make_ohlcv(100, trend=0.0, volatility=0.001, seed=30).assign(
        close_time=lambda d: d["open_time"] + 14399999
    )
