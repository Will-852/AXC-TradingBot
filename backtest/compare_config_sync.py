#!/usr/bin/env python3
"""
compare_config_sync.py — Before/After comparison of config sync (2026-03-26).

Runs BTC 30d backtest twice:
  A) OLD params (hardcoded, pre-sync engine.py values)
  B) NEW params (imported from params.py, post-sync)
"""

import os
import sys

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from datetime import datetime, timezone, timedelta
from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES
import backtest.engine_types as et


def run_with_overrides(df_1h, df_4h, symbol, balance, overrides):
    """Run backtest with specific param overrides on engine_types."""
    import backtest.engine as eng

    # Save + override
    saved_et, saved_eng = {}, {}
    for key, val in overrides.items():
        if hasattr(et, key):
            saved_et[key] = getattr(et, key)
            setattr(et, key, val)
        if hasattr(eng, key):
            saved_eng[key] = getattr(eng, key)
            setattr(eng, key, val)
    try:
        engine = BacktestEngine(
            symbol=symbol, df_1h=df_1h, df_4h=df_4h,
            initial_balance=balance)
        return engine.run()
    finally:
        for k, v in saved_et.items():
            setattr(et, k, v)
        for k, v in saved_eng.items():
            setattr(eng, k, v)


def main():
    symbol = "BTCUSDT"
    days = 30
    balance = 10000.0

    print(f"\n{'═'*60}")
    print(f"  Config Sync Impact — {symbol} {days}d")
    print(f"{'═'*60}")

    # Fetch data
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)

    start_1h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES)).timestamp() * 1000)
    start_4h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES * 4)).timestamp() * 1000)

    print(f"  Fetching data...")
    df_1h = fetch_klines_range(symbol, "1h", start_1h, end_ms)
    df_4h = fetch_klines_range(symbol, "4h", start_4h, end_ms)
    print(f"  1H: {len(df_1h)} candles | 4H: {len(df_4h)} candles")

    # OLD params
    old = {
        "REGIME_ADJUST_ENABLED": True,
        "PERSISTENCE_THRESHOLD": {"range": 3, "trend": 4, "crash": 1, "burst": 1, "newarch": 1},
        "_STRATEGY_CONF_GATE": {"range": 0.50, "trend": 0.50, "crash": 0.50, "burst": 0.35, "newarch": 0.50},
        "_MODE_AFFINITY": {
            "TREND": {"trend": 0.0, "range": -0.20, "crash": 0.0, "burst": -0.05, "newarch": 0.0},
            "RANGE": {"range": 0.0, "trend": -0.30, "crash": 0.0, "burst": -0.05, "newarch": 0.0},
            "CRASH": {"crash": 0.0, "trend": -0.20, "range": -0.30, "burst": -0.15, "newarch": 0.0},
        },
        "_MODE_DEFAULT_PENALTY": {"trend": -0.25, "range": -0.10, "crash": 0.0, "burst": -0.05, "newarch": 0.0},
    }

    print(f"\n  [A] OLD params (pre-sync)...")
    old_r = run_with_overrides(df_1h, df_4h, symbol, balance, old)

    print(f"  [B] NEW params (synced from params.py)...")
    new_r = run_with_overrides(df_1h, df_4h, symbol, balance, {})

    # Compare
    print(f"\n{'─'*60}")
    print(f"  {'METRIC':<25} {'OLD':>15} {'NEW':>15}")
    print(f"{'─'*60}")

    keys = [
        ("Final Balance", "final_balance"),
        ("Total PnL", "total_pnl"),
        ("Return %", "return_pct"),
        ("Total Trades", "total_trades"),
        ("Win Rate %", "win_rate"),
        ("Profit Factor", "profit_factor"),
        ("Max DD %", "max_drawdown_pct"),
        ("Sharpe", "sharpe_ratio"),
    ]

    for label, key in keys:
        o = old_r.get(key, 0) or 0
        n = new_r.get(key, 0) or 0
        delta = ""
        if isinstance(o, (int, float)) and isinstance(n, (int, float)) and abs(o) > 0.001:
            pct = (n - o) / abs(o) * 100
            delta = f"  ({pct:+.1f}%)" if abs(pct) > 0.1 else ""
        o_s = f"{o:.2f}" if isinstance(o, float) else str(o)
        n_s = f"{n:.2f}" if isinstance(n, float) else str(n)
        print(f"  {label:<25} {o_s:>15} {n_s:>15}{delta}")

    # Per-strategy
    print(f"\n  {'STRATEGY':<10} {'OLD trades':>12} {'OLD PnL':>10} {'NEW trades':>12} {'NEW PnL':>10}")
    print(f"  {'─'*54}")
    for strat in ("range", "trend", "crash", "burst"):
        ot = [t for t in old_r.get("trades", []) if t.strategy == strat]
        nt = [t for t in new_r.get("trades", []) if t.strategy == strat]
        op = sum(t.pnl for t in ot)
        np_ = sum(t.pnl for t in nt)
        print(f"  {strat:<10} {len(ot):>12} {op:>+10.2f} {len(nt):>12} {np_:>+10.2f}")

    print(f"\n{'═'*60}")
    print(f"  CHANGES: REGIME_ADJUST True→False | CONF_GATE looser | PERSIST trend 4→1")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
