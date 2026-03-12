#!/usr/bin/env python3
"""
compare_hmm.py — Backtest comparison: WITH vs WITHOUT HMM
Usage: python3 backtest/compare_hmm.py
"""

import os
import sys

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

import pandas as pd
from backtest.engine import BacktestEngine

DATA = os.path.join(AXC_HOME, "backtest", "data")


def _find_longest_csv(symbol, interval):
    """Find the CSV with most data for a symbol+interval."""
    import glob
    pattern = f"{DATA}/{symbol}_{interval}_*.csv"
    files = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(f"No {interval} data for {symbol} in {DATA}")
    # Pick file with earliest start date (longest range)
    files.sort()
    return files[0]


def run_comparison(symbol="BTCUSDT"):
    # Load longest available data
    f_1h = _find_longest_csv(symbol, "1h")
    f_4h = _find_longest_csv(symbol, "4h")
    df_1h = pd.read_csv(f_1h, parse_dates=["timestamp"])
    df_4h = pd.read_csv(f_4h, parse_dates=["timestamp"])
    print(f"{symbol} data: 1H={len(df_1h)} candles, 4H={len(df_4h)} candles")

    # Run 1: WITHOUT HMM
    print("\n=== Run 1: WITHOUT HMM ===")
    engine_no = BacktestEngine(
        symbol=symbol, df_1h=df_1h, df_4h=df_4h,
        initial_balance=10000.0, quiet=True,
    )
    engine_no.hmm_enabled = False
    engine_no._hmm = None
    result_no = engine_no.run()

    # Run 2: WITH HMM
    print("\n=== Run 2: WITH HMM ===")
    engine_hmm = BacktestEngine(
        symbol=symbol, df_1h=df_1h, df_4h=df_4h,
        initial_balance=10000.0, quiet=True,
    )
    result_hmm = engine_hmm.run()

    # Compare
    sep = "-" * 55
    print(f"\n{sep}")
    print(f"{'Metric':25s} {'No HMM':>13s} {'With HMM':>13s}")
    print(sep)
    for key in ["return_pct", "total_trades", "win_rate", "profit_factor",
                "expectancy", "max_drawdown_pct", "sharpe_ratio",
                "max_win_streak", "max_loss_streak"]:
        v1 = result_no.get(key, 0)
        v2 = result_hmm.get(key, 0)
        if isinstance(v1, float):
            print(f"{key:25s} {v1:>13.2f} {v2:>13.2f}")
        else:
            print(f"{key:25s} {str(v1):>13s} {str(v2):>13s}")

    # Strategy breakdown
    print(sep)
    print("Strategy breakdown:")
    for strat in ("range", "trend", "crash"):
        s1 = result_no.get("by_strategy", {}).get(strat, {})
        s2 = result_hmm.get("by_strategy", {}).get(strat, {})
        c1 = s1.get("count", 0)
        c2 = s2.get("count", 0)
        w1 = s1.get("win_rate", 0)
        w2 = s2.get("win_rate", 0)
        if c1 or c2:
            print(f"  {strat:8s}  No HMM: {c1} trades ({w1:.0f}% WR) | HMM: {c2} trades ({w2:.0f}% WR)")

    # Mode analysis
    no_modes = [e["mode"] for e in result_no.get("equity_curve", [])]
    hmm_modes = [e["mode"] for e in result_hmm.get("equity_curve", [])]

    no_switches = sum(
        1 for i in range(1, len(no_modes)) if no_modes[i] != no_modes[i - 1]
    )
    hmm_switches = sum(
        1 for i in range(1, len(hmm_modes)) if hmm_modes[i] != hmm_modes[i - 1]
    )
    crash_candles = sum(1 for m in hmm_modes if m == "CRASH")

    print(f"\nMode switches:    No HMM={no_switches} | HMM={hmm_switches}")
    print(f"CRASH candles:    HMM={crash_candles} / {len(hmm_modes)} "
          f"({100 * crash_candles / max(len(hmm_modes), 1):.1f}%)")
    print(sep)


if __name__ == "__main__":
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
    run_comparison(symbol)
