#!/usr/bin/env python3
"""
sweep_btc_sl.py — BTC-specific SL ATR mult sweep.

Monkey-patches RANGE_SL_ATR_MULT in the production RangeStrategy module
so the engine uses the same strategy logic (not BTRangeStrategy) with
different SL multipliers.

Usage:
  python3 backtest/sweep_btc_sl.py
"""
import os
import sys
from datetime import datetime, timezone, timedelta

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES

# ─── Config ───
SYMBOL = "BTCUSDT"
DAYS = 180
BALANCE = 10_000
SL_MULTS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]


def _calc_time_range(days: int) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    warmup_hours = WARMUP_CANDLES * 4  # 4H candles
    total_hours = days * 24 + warmup_hours
    start_ms = int((now - timedelta(hours=total_hours)).timestamp() * 1000)
    return start_ms, end_ms


def main():
    # Fetch data once
    start_ms, end_ms = _calc_time_range(DAYS)
    print(f"Fetching {SYMBOL} {DAYS}d data...")
    df_1h = fetch_klines_range(SYMBOL, "1h", start_ms, end_ms)
    df_4h = fetch_klines_range(SYMBOL, "4h", start_ms, end_ms)
    print(f"  1H: {len(df_1h)} candles | 4H: {len(df_4h)} candles\n")

    # Import the module we'll patch — engine uses "trader_cycle.strategies.range_strategy"
    # (via sys.path including scripts/), so we must patch THAT module object.
    import trader_cycle.strategies.range_strategy as rs_mod

    print(f"Current RANGE_SL_ATR_MULT = {rs_mod.RANGE_SL_ATR_MULT}")
    print(f"\n{'SL Mult':>8} | {'PnL':>10} | {'Trades':>6} | {'WR':>6} | {'AvgW':>8} | {'AvgL':>8} | {'PF':>6}")
    print("-" * 72)

    for sl_mult in SL_MULTS:
        # Monkey-patch the module-level constant
        rs_mod.RANGE_SL_ATR_MULT = sl_mult

        engine = BacktestEngine(
            symbol=SYMBOL,
            df_1h=df_1h.copy(),
            df_4h=df_4h.copy(),
            initial_balance=BALANCE,
        )
        # Verify patch reached the strategy
        actual = engine.range_strategy.get_position_params().sl_atr_mult
        if abs(actual - sl_mult) > 1e-6:
            print(f"  ⚠️  PATCH FAILED: wanted {sl_mult}, got {actual}")
        result = engine.run()

        pnl = result["final_balance"] - BALANCE
        trades = result["total_trades"]
        wr = result["win_rate"] if trades > 0 else 0
        avg_w = result.get("avg_win", 0)
        avg_l = result.get("avg_loss", 0)
        pf = result.get("profit_factor", 0)
        pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)

        print(f"{sl_mult:>8.1f} | ${pnl:>+9.0f} | {trades:>6} | {wr:>5.1f}% | ${avg_w:>+7.0f} | ${avg_l:>+7.0f} | {pf_str:>6}")


if __name__ == "__main__":
    main()
