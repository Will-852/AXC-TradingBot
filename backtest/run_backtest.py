#!/usr/bin/env python3
"""
run_backtest.py — Backtest CLI 入口 + Equity Curve

用法:
  python3 backtest/run_backtest.py --symbol BTCUSDT --days 14
  python3 backtest/run_backtest.py --symbol ETHUSDT --days 30 --platform binance
  python3 backtest/run_backtest.py --symbol BTCUSDT --days 14 --balance 5000
"""

import argparse
import json
import os
import sys
import tempfile
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
        sharpe = result.get("sharpe_ratio", 0.0)
        print(f"  Sharpe:         {sharpe:>12.2f}")
        sortino = result.get("sortino_ratio", 0.0)
        print(f"  Sortino:        {sortino:>12.2f}")
        calmar = result.get("calmar_ratio", 0.0)
        print(f"  Calmar:         {calmar:>12.2f}")
        var_95 = result.get("var_95", 0.0)
        print(f"  VaR (95%):      {var_95 * 100:>+11.4f}%")
        cvar_95 = result.get("cvar_95", 0.0)
        print(f"  CVaR (95%):     {cvar_95 * 100:>+11.4f}%")
        recovery = result.get("recovery_factor", 0.0)
        print(f"  Recovery:       {recovery:>12.2f}")
        payoff = result.get("payoff_ratio", 0.0)
        print(f"  Payoff:         {payoff:>12.2f}")
        sqn = result.get("sqn", 0.0)
        sqn_grade = result.get("sqn_grade", "N/A")
        print(f"  SQN:            {sqn:>12.2f}  ({sqn_grade})")
        alpha = result.get("alpha", 0.0)
        bh = result.get("buyhold_return", 0.0)
        print(f"  Alpha:          {alpha:>+11.2f}%  (B&H: {bh:+.2f}%)")
        exposure = result.get("exposure_pct", 0.0)
        print(f"  Exposure:       {exposure:>11.1f}%")

        # Per-strategy breakdown
        for strat in ("range", "trend", "crash"):
            strat_t = [t for t in result["trades"] if t.strategy == strat]
            if strat_t:
                sw = sum(1 for t in strat_t if t.pnl > 0)
                print(f"  {strat.title():14s}  {sw}W / {len(strat_t) - sw}L")

        # Top drawdown periods
        dd_periods = result.get("drawdown_periods", [])
        if dd_periods:
            print(f"\n  Top Drawdowns:")
            for i, dd in enumerate(dd_periods, 1):
                start = dd["start"][:10]
                if dd["end"]:
                    end = dd["end"][:10]
                    status = f"{dd['duration_candles']} candles, recovered"
                else:
                    end = "ongoing"
                    status = f"{dd['duration_candles']} candles"
                print(f"    #{i}  {dd['depth_pct']:>5.1f}%  {start} → {end}  ({status})")

    print(f"{'─' * 45}")


def _save_trades(result: dict, symbol: str, days: int) -> str:
    """Save trades to JSONL file using to_dict() for complete 11-field records.
    to_jsonl() only has 7 fields — missing entry_time, exit_time, exit_reason,
    strategy, tp_price — making export/import lossy.
    Uses atomic write (tempfile + os.replace) to avoid truncated files on Ctrl+C."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"bt_{symbol}_{days}d_trades.jsonl")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for trade in result["trades"]:
            f.write(json.dumps(trade.to_dict(), ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def _save_meta(result: dict, symbol: str, days: int, balance: float) -> str:
    """Save backtest metadata as JSON sidecar for dashboard export/import."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"bt_{symbol}_{days}d_meta.json")
    meta = {
        "symbol": symbol,
        "days": days,
        "balance": balance,
        "strategy_params": {},
        "param_overrides": {},
        "stats": {
            k: result.get(k)
            for k in ("return_pct", "win_rate", "profit_factor",
                       "max_drawdown_pct", "total_trades", "sharpe_ratio",
                       "sortino_ratio", "calmar_ratio", "var_95", "cvar_95",
                       "recovery_factor", "payoff_ratio",
                       "expectancy", "sqn", "sqn_grade", "alpha",
                       "buyhold_return", "exposure_pct")
            if result.get(k) is not None
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = tempfile.NamedTemporaryFile(
        mode='w', dir=os.path.dirname(path),
        delete=False, suffix='.tmp')
    json.dump(meta, tmp, ensure_ascii=False)
    tmp.close()
    os.replace(tmp.name, path)
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


def _fmt_vol(v: float) -> str:
    """Format volume/value: 1234→1.2K, 1234567→1.2M, 1234567890→1.2B."""
    if abs(v) >= 1e9:
        return f"{v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:.0f}"


def _print_trade_log(result: dict):
    """Print trade roundtrip table with color hints and strategy breakdown."""
    if not result["trades"]:
        return

    print(f"\n  {'#':>3}  {'Side':5s} {'Strat':5s} {'Entry':>10s}  {'Exit':>10s}  {'PnL':>10s}  {'Reason':4s}")
    print(f"  {'─' * 55}")
    for i, t in enumerate(result["trades"], 1):
        pnl_str = f"${t.pnl:+.2f}"
        print(
            f"  {i:>3}  {t.side:5s} {t.strategy:5s} "
            f"{t.entry:>10.2f}  {t.exit:>10.2f}  {pnl_str:>10s}  {t.exit_reason}"
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

    # Save trades JSONL (complete 11-field records)
    trades_path = _save_trades(result, symbol, args.days)
    print(f"\n  Trades → {trades_path}")

    # Save metadata sidecar (stats + config for dashboard export)
    meta_path = _save_meta(result, symbol, args.days, args.balance)
    print(f"  Meta   → {meta_path}")

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
