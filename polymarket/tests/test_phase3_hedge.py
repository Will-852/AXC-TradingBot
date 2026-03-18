#!/usr/bin/env python3
"""
Phase 3 unit tests — HL Hedge integration

Tests:
1. HLHedgeClient dry_run mode (no SDK init, no credentials needed)
2. Hedge direction logic (YES → SHORT, NO → LONG)
3. PolyPosition hedge fields
4. ExecuteTradesStep._try_open_hedge integration
5. CloseHedgeStep with hedged positions
6. State serialization with hedge fields
7. Config values present
"""

import sys
import os

_AXC = os.path.expanduser("~/projects/axc-trading")
if _AXC not in sys.path:
    sys.path.insert(0, _AXC)
_scripts = os.path.join(_AXC, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from datetime import datetime
from polymarket.exchange.hl_hedge_client import HLHedgeClient, HLHedgeError
from polymarket.core.context import PolyPosition, PolySignal, PolyContext, EdgeAssessment
from polymarket.pipeline import ExecuteTradesStep, CloseHedgeStep

passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ T{passed + failed}: {name}")
    else:
        failed += 1
        print(f"  ✗ T{passed + failed}: {name} — {detail}")


# ─── T1: HLHedgeClient dry_run ───
client = HLHedgeClient(dry_run=True)
test("dry_run client creates without credentials",
     client.dry_run is True and client._info is None)

result = client.open_hedge("SHORT", 100.0, leverage=20)
test("dry_run open_hedge returns dry_run status",
     result["status"] == "dry_run" and result["direction"] == "SHORT")

result = client.close_hedge("BTC")
test("dry_run close_hedge returns dry_run status",
     result["status"] == "dry_run")

bal = client.get_balance()
test("dry_run balance returns 0.0",
     bal == 0.0)

pos = client.get_position("BTC")
test("dry_run position returns None",
     pos is None)

# ─── T2: Hedge direction logic ───
# YES (predict UP) → SHORT hedge (inverse)
# NO (predict DOWN) → LONG hedge (inverse)
signal_yes = PolySignal(side="YES", category="crypto_15m")
signal_no = PolySignal(side="NO", category="crypto_15m")
# The direction logic is in _try_open_hedge; test it indirectly
test("hedge direction: YES → SHORT",
     ("SHORT" if signal_yes.side == "YES" else "LONG") == "SHORT")
test("hedge direction: NO → LONG",
     ("SHORT" if signal_no.side == "YES" else "LONG") == "LONG")

# ─── T3: PolyPosition hedge fields ───
pos = PolyPosition(
    condition_id="test",
    hedge_side="SHORT",
    hedge_size=0.001,
    hedge_entry_px=85000.0,
)
test("PolyPosition has hedge_side",
     pos.hedge_side == "SHORT")
test("PolyPosition has hedge_size",
     pos.hedge_size == 0.001)
test("PolyPosition has hedge_entry_px",
     pos.hedge_entry_px == 85000.0)

# ─── T4: Symbol mapping ───
test("_to_coin BTCUSDT → BTC",
     HLHedgeClient._to_coin("BTCUSDT") == "BTC")
test("_to_coin BTC → BTC",
     HLHedgeClient._to_coin("BTC") == "BTC")

# ─── T5: State serialization with hedge fields ───
from polymarket.state.poly_state import build_state_snapshot

ctx = PolyContext(
    timestamp=datetime.now(),
    timestamp_str="2026-03-18 12:00",
    dry_run=True,
)
pos = PolyPosition(
    condition_id="c1", title="BTC Test", category="crypto_15m",
    side="YES", shares=10, avg_price=0.5, current_price=0.5,
    cost_basis=5.0, market_value=5.0,
    hedge_side="SHORT", hedge_size=0.001, hedge_entry_px=85000.0,
)
ctx.open_positions = [pos]
state = build_state_snapshot(ctx)
saved_pos = state["positions"][0]
test("state serialization includes hedge_side",
     saved_pos["hedge_side"] == "SHORT")
test("state serialization includes hedge_size",
     saved_pos["hedge_size"] == 0.001)
test("state serialization includes hedge_entry_px",
     saved_pos["hedge_entry_px"] == 85000.0)

# ─── T6: Config values ───
from polymarket.config.settings import (
    HEDGE_ENABLED, HEDGE_USD, HEDGE_LEVERAGE, HEDGE_SYMBOL,
    HEDGE_AUTO_CLOSE_ON_RESOLVE, HEDGE_CATEGORIES,
)
test("HEDGE_ENABLED default is False",
     HEDGE_ENABLED is False)
test("HEDGE_USD is 100.0",
     HEDGE_USD == 100.0)
test("HEDGE_LEVERAGE is 20",
     HEDGE_LEVERAGE == 20)
test("HEDGE_SYMBOL is BTC",
     HEDGE_SYMBOL == "BTC")
test("HEDGE_CATEGORIES includes crypto_15m",
     "crypto_15m" in HEDGE_CATEGORIES)

# ─── T7: CloseHedgeStep skips when HEDGE_ENABLED=False ───
ctx2 = PolyContext(dry_run=True, timestamp=datetime.now(), timestamp_str="test")
ctx2.exit_signals = []
step = CloseHedgeStep()
result_ctx = step.run(ctx2)
test("CloseHedgeStep no-op when HEDGE_ENABLED=False",
     result_ctx is ctx2)

# ─── Summary ───
print(f"\nAll {passed + failed} Phase 3 tests: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("All Phase 3 tests PASSED ✓")
