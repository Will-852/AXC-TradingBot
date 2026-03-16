"""
test_liq_monitor.py — Liquidation monitor + signal boost tests.

Tests:
- OI delta calculation + event detection
- LiqSignalStep state reading + staleness check
- apply_liq_boost directional logic
- LiqState serialization
"""

import json
import os
import sys
import time

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import pytest
from unittest.mock import patch
from scripts.trader_cycle.core.context import CycleContext, Signal
from scripts.trader_cycle.strategies.liq_signal import LiqSignalStep, apply_liq_boost
from scripts.trader_cycle.exchange.liq_data import LiqEvent, LiqState


class TestLiqData:
    """LiqState / LiqEvent serialization."""

    def test_round_trip(self):
        event = LiqEvent(
            coin="BTC", direction="SHORT_LIQS",
            oi_delta_pct=-2.5, price_delta_pct=1.2,
            estimated_volume_usd=5_000_000, timestamp=time.time(),
        )
        state = LiqState(
            timestamp=time.time(),
            events=[event],
            oi_by_coin={"BTC": 1_000_000_000},
            oi_delta_10m={"BTC": -2.5},
            oi_delta_1h={"BTC": -1.0},
        )
        d = state.to_dict()
        restored = LiqState.from_dict(d)
        assert len(restored.events) == 1
        assert restored.events[0].coin == "BTC"
        assert restored.events[0].direction == "SHORT_LIQS"
        assert restored.oi_by_coin["BTC"] == 1_000_000_000

    def test_empty_state(self):
        state = LiqState.from_dict({})
        assert state.timestamp == 0.0
        assert state.events == []


class TestLiqSignalStep:
    """LiqSignalStep reads state file and populates context."""

    def test_reads_valid_state(self, tmp_path):
        state_file = tmp_path / "liq_state.json"
        state = {
            "timestamp": time.time(),
            "events": [
                {
                    "coin": "BTC", "direction": "SHORT_LIQS",
                    "oi_delta_pct": -2.5, "price_delta_pct": 1.2,
                    "estimated_volume_usd": 5_000_000,
                    "timestamp": time.time(), "trigger_mode": "on_liqs",
                }
            ],
            "oi_by_coin": {"BTC": 1e9},
            "oi_delta_10m": {"BTC": -2.5},
            "oi_delta_1h": {"BTC": -1.0},
        }
        state_file.write_text(json.dumps(state))

        step = LiqSignalStep()
        ctx = CycleContext()
        with patch(
            "scripts.trader_cycle.strategies.liq_signal.LIQ_STATE_PATH",
            str(state_file),
        ):
            result = step.run(ctx)

        assert len(result.liq_events) == 1
        assert result.liq_events[0]["coin"] == "BTC"

    def test_stale_state_ignored(self, tmp_path):
        state_file = tmp_path / "liq_state.json"
        state = {
            "timestamp": time.time() - 600,  # 10 min old
            "events": [{"coin": "BTC", "direction": "SHORT_LIQS"}],
        }
        state_file.write_text(json.dumps(state))

        step = LiqSignalStep()
        ctx = CycleContext()
        with patch(
            "scripts.trader_cycle.strategies.liq_signal.LIQ_STATE_PATH",
            str(state_file),
        ):
            result = step.run(ctx)

        # Stale → no events loaded
        assert len(result.liq_events) == 0

    def test_missing_file_no_crash(self):
        step = LiqSignalStep()
        ctx = CycleContext()
        with patch(
            "scripts.trader_cycle.strategies.liq_signal.LIQ_STATE_PATH",
            "/nonexistent/path.json",
        ):
            result = step.run(ctx)
        assert len(result.liq_events) == 0

    def test_disabled_skips(self):
        step = LiqSignalStep()
        ctx = CycleContext()
        with patch(
            "scripts.trader_cycle.strategies.liq_signal.LIQ_MONITOR_ENABLED",
            False,
        ):
            result = step.run(ctx)
        assert len(result.liq_events) == 0


class TestApplyLiqBoost:
    """apply_liq_boost directional logic."""

    def test_short_liqs_boosts_long(self):
        signal = Signal(pair="BTCUSDT", direction="LONG", strategy="range", strength="STRONG")
        events = [
            {
                "coin": "BTC", "direction": "SHORT_LIQS",
                "oi_delta_pct": -3.0, "price_delta_pct": 1.5,
                "estimated_volume_usd": 2_000_000,
                "timestamp": time.time(), "trigger_mode": "on_liqs",
            }
        ]
        boost = apply_liq_boost(signal, events)
        assert boost == 1.0

    def test_long_liqs_boosts_short(self):
        signal = Signal(pair="ETHUSDT", direction="SHORT", strategy="crash", strength="STRONG")
        events = [
            {
                "coin": "ETH", "direction": "LONG_LIQS",
                "oi_delta_pct": -2.0, "price_delta_pct": -1.0,
                "estimated_volume_usd": 1_500_000,
                "timestamp": time.time(), "trigger_mode": "on_liqs",
            }
        ]
        boost = apply_liq_boost(signal, events)
        assert boost == 1.0

    def test_wrong_direction_no_boost(self):
        """SHORT_LIQS should NOT boost SHORT signals."""
        signal = Signal(pair="BTCUSDT", direction="SHORT", strategy="crash", strength="STRONG")
        events = [
            {
                "coin": "BTC", "direction": "SHORT_LIQS",
                "oi_delta_pct": -3.0, "price_delta_pct": 1.5,
                "estimated_volume_usd": 2_000_000,
                "timestamp": time.time(), "trigger_mode": "on_liqs",
            }
        ]
        boost = apply_liq_boost(signal, events)
        assert boost == 0.0

    def test_different_coin_no_boost(self):
        """BTC event should not boost ETH signal."""
        signal = Signal(pair="ETHUSDT", direction="LONG", strategy="range", strength="STRONG")
        events = [
            {
                "coin": "BTC", "direction": "SHORT_LIQS",
                "oi_delta_pct": -3.0, "price_delta_pct": 1.5,
                "estimated_volume_usd": 2_000_000,
                "timestamp": time.time(), "trigger_mode": "on_liqs",
            }
        ]
        boost = apply_liq_boost(signal, events)
        assert boost == 0.0

    def test_empty_events(self):
        signal = Signal(pair="BTCUSDT", direction="LONG", strategy="range", strength="STRONG")
        assert apply_liq_boost(signal, []) == 0.0

    def test_no_events(self):
        signal = Signal(pair="BTCUSDT", direction="LONG", strategy="range", strength="STRONG")
        assert apply_liq_boost(signal, None) == 0.0
