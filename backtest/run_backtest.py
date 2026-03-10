#!/usr/bin/env python3
"""
run_backtest.py — Backtest CLI 入口 + Equity Curve

用法:
  python3 backtest/run_backtest.py --symbol BTCUSDT --days 14
  python3 backtest/run_backtest.py --symbol ETHUSDT --days 30 --platform binance
  python3 backtest/run_backtest.py --symbol BTCUSDT --days 14 --balance 5000
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES

DATA_DIR = os.path.join(AXC_HOME, "backtest", "data")


def _calc_time_range(days: int, interval: str) -> tuple[int, int]:
    """Calculate start/end ms timestamps including warmup period."""
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)

    if interval == "4h":
        warmup_hours = WARMUP_CANDLES * 4  # 800h for 200 4H candles
    else:
        warmup_hours = WARMUP_CANDLES      # 200h for 200 1H candles

    total_hours = days * 24 + warmup_hours
    start_ms = int((now - timedelta(hours=total_hours)).timestamp() * 1000)

    return start_ms, end_ms


def _print_results(result: dict, args):
    """Print formatted backtest results to terminal."""
    print(f"\n{'─' * 45}")
    print(f"  {'RESULTS':^41}")
    print(f"{'─' * 45}")
    print(f"  Final Balance:  ${result['final_balance']:>12,.2f}")
    print(f"  Return:         {result['return_pct']:>+12.2f}%")
    print(f"  Total Trades:   {result['total_trades']:>12}")

    if result["total_trades"] > 0:
        print(f"  Win Rate:       {result['win_rate']:>11.1f}%")
        adj_wr = result.get("cluster_adj_wr", 0.0)
        indep = result.get("independent_decisions", result["total_trades"])
        clusters = result.get("clusters", 0)
        print(f"  Adj Win Rate:   {adj_wr:>11.1f}%  ({indep} indep, {clusters} clusters)")
        pf = result["profit_factor"]
        pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
        print(f"  Profit Factor:  {pf_str:>12}")
        print(f"  Expectancy:     ${result['expectancy']:>+11.2f}/trade")
        print(f"  Max Drawdown:   {result['max_drawdown_pct']:>11.1f}%")
        print(f"  Avg Win:        ${result['avg_win']:>+11.2f}")
        print(f"  Avg Loss:       ${result['avg_loss']:>+11.2f}")

        # Per-strategy breakdown
        range_t = [t for t in result["trades"] if t.strategy == "range"]
        trend_t = [t for t in result["trades"] if t.strategy == "trend"]
        if range_t:
            rw = sum(1 for t in range_t if t.pnl > 0)
            print(f"  Range:          {rw}W / {len(range_t) - rw}L")
        if trend_t:
            tw = sum(1 for t in trend_t if t.pnl > 0)
            print(f"  Trend:          {tw}W / {len(trend_t) - tw}L")

    print(f"{'─' * 45}")


def _save_trades(result: dict, symbol: str, days: int) -> str:
    """Save trades to JSONL file. Returns file path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"bt_{symbol}_{days}d_trades.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for trade in result["trades"]:
            f.write(trade.to_jsonl() + "\n")
    return path


def _save_equity_chart(result: dict, symbol: str, days: int, initial_balance: float) -> str | None:
    """Save equity curve PNG. Returns file path or None if matplotlib unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    eq = result["equity_curve"]
    if not eq:
        return None

    equities = [e["equity"] for e in eq]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(range(len(equities)), equities, linewidth=1, color="#2196F3", label="Equity")
    ax.axhline(y=initial_balance, color="gray", linestyle="--", alpha=0.5, label="Initial")

    # Mark trades
    for trade in result["trades"]:
        color = "#4CAF50" if trade.pnl > 0 else "#F44336"
        marker = "^" if trade.side == "LONG" else "v"
        for idx, e in enumerate(eq):
            if e["time"] == trade.entry_time:
                ax.scatter(idx, equities[idx], color=color, marker=marker, s=40, zorder=5)
                break

    ret = result["return_pct"]
    ax.set_title(f"{symbol} Backtest — {days}d — Return: {ret:+.2f}%")
    ax.set_xlabel("1H Candles")
    ax.set_ylabel("Equity ($)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    path = os.path.join(DATA_DIR, f"bt_{symbol}_{days}d_equity.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)

    return path


def _print_trade_log(result: dict):
    """Print individual trade details."""
    if not result["trades"]:
        return

    print(f"\n  Trade Log:")
    for t in result["trades"]:
        icon = "+" if t.pnl > 0 else "-"
        print(
            f"    [{icon}] {t.side:5s} {t.strategy:5s} "
            f"entry={t.entry:.2f} exit={t.exit:.2f} "
            f"pnl=${t.pnl:+.2f} [{t.exit_reason}]"
        )


def main():
    parser = argparse.ArgumentParser(description="AXC Backtest Engine")
    parser.add_argument("--symbol", required=True, help="Trading pair (e.g. BTCUSDT)")
    parser.add_argument("--days", type=int, default=14, help="Test period in days (default 14)")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance USD (default 10000)")
    parser.add_argument("--platform", default="binance", help="Data source (default binance)")
    args = parser.parse_args()

    symbol = args.symbol.upper()

    print(f"\n{'=' * 50}")
    print(f"  AXC Backtest — {symbol} — {args.days}d")
    print(f"{'=' * 50}")

    # ── Phase 1: Fetch data ──
    print("\n[1/3] Fetching historical data...")

    start_1h, end_1h = _calc_time_range(args.days, "1h")
    start_4h, end_4h = _calc_time_range(args.days, "4h")

    df_1h = fetch_klines_range(symbol, "1h", start_1h, end_1h, args.platform)
    df_4h = fetch_klines_range(symbol, "4h", start_4h, end_4h, args.platform)

    print(f"  1H: {len(df_1h)} candles")
    print(f"  4H: {len(df_4h)} candles")

    # ── Phase 2: Run engine ──
    print("\n[2/3] Running backtest...")

    engine = BacktestEngine(
        symbol=symbol,
        df_1h=df_1h,
        df_4h=df_4h,
        initial_balance=args.balance,
    )
    result = engine.run()

    # ── Phase 3: Output ──
    print("\n[3/3] Output")

    _print_results(result, args)

    # Save trades JSONL
    trades_path = _save_trades(result, symbol, args.days)
    print(f"\n  Trades → {trades_path}")

    # Save equity curve
    chart_path = _save_equity_chart(result, symbol, args.days, args.balance)
    if chart_path:
        print(f"  Chart  → {chart_path}")
    else:
        print("  (matplotlib not installed — skipping chart)")

    # Trade log
    _print_trade_log(result)

    print()


if __name__ == "__main__":
    main()
