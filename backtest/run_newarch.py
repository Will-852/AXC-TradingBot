#!/usr/bin/env python3
"""
run_newarch.py — 5-Layer New Architecture Backtest Validation

用法:
  python3 backtest/run_newarch.py                    # default: BTC 2yr
  python3 backtest/run_newarch.py --compare           # compare vs range baseline
  python3 backtest/run_newarch.py --balance 5000      # custom balance
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

import pandas as pd
from backtest.engine import BacktestEngine, WARMUP_CANDLES
from backtest.metrics_ext import extend_summary
from backtest.strategies.bt_newarch_strategy import BTNewArchStrategy
from backtest.strategies.bt_range_strategy import BTRangeStrategy
from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal

DATA_DIR = os.path.join(AXC_HOME, "backtest", "data")


class _NullStrategy(StrategyBase):
    """No-op strategy that never fires. Used to disable other strategy slots."""
    name = "null"
    mode = ""
    required_timeframes = ["4h", "1h"]

    def evaluate(self, pair, indicators, ctx):
        return None

    def get_position_params(self):
        return PositionParams(risk_pct=0.02, leverage=5, sl_atr_mult=1.5, min_rr=2.0)

# Data files — 2-year BTC coverage
DATA_4H = os.path.join(DATA_DIR, "BTCUSDT_4h_20240222_20260317.csv")
DATA_1H = os.path.join(DATA_DIR, "BTCUSDT_1h_20240318_20260317.csv")


def _load_csv(path: str) -> pd.DataFrame:
    """Load OHLCV CSV with standard column processing."""
    df = pd.read_csv(path)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    if "timestamp" not in df.columns:
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _run_newarch(balance: float, quiet: bool = False) -> dict:
    """Run NewArch strategy (isolated — no other strategies)."""
    df_4h = _load_csv(DATA_4H)
    df_1h = _load_csv(DATA_1H)

    strategy = BTNewArchStrategy()
    null = _NullStrategy()

    # Isolate: disable range/trend/crash, only run newarch
    engine = BacktestEngine(
        symbol="BTCUSDT",
        df_1h=df_1h,
        df_4h=df_4h,
        initial_balance=balance,
        strategy_overrides={
            "range": null,
            "trend": null,
            "crash": null,
            "newarch": strategy,
        },
        signal_delay=1,
        quiet=quiet,
    )
    result = engine.run()
    return extend_summary(result)


def _run_range_baseline(balance: float, quiet: bool = False) -> dict:
    """Run existing range strategy as baseline comparison."""
    df_4h = _load_csv(DATA_4H)
    df_1h = _load_csv(DATA_1H)

    engine = BacktestEngine(
        symbol="BTCUSDT",
        df_1h=df_1h,
        df_4h=df_4h,
        initial_balance=balance,
        quiet=quiet,
    )
    result = engine.run()
    return extend_summary(result)


def _print_results(result: dict, label: str):
    """Print formatted backtest results."""
    print(f"\n{'═' * 50}")
    print(f"  {label}")
    print(f"{'═' * 50}")
    print(f"  Final Balance:  ${result['final_balance']:>12,.2f}")
    print(f"  Return:         {result['return_pct']:>+12.2f}%")
    print(f"  Total Trades:   {result['total_trades']:>12}")

    if result["total_trades"] > 0:
        print(f"  Win Rate:       {result['win_rate']:>11.1f}%")
        pf = result["profit_factor"]
        pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else str(pf)
        print(f"  Profit Factor:  {pf_str:>12}")
        print(f"  Expectancy:     ${result['expectancy']:>+11.2f}/trade")
        print(f"  Max Drawdown:   {result['max_drawdown_pct']:>11.1f}%")
        print(f"  Avg Win:        ${result['avg_win']:>+11.2f}")
        print(f"  Avg Loss:       ${result['avg_loss']:>+11.2f}")

        sharpe = result.get("sharpe_ratio", 0.0)
        sortino = result.get("sortino_ratio", 0.0)
        calmar = result.get("calmar_ratio", 0.0)
        print(f"  Sharpe:         {sharpe:>12.2f}")
        print(f"  Sortino:        {sortino:>12.2f}")
        print(f"  Calmar:         {calmar:>12.2f}")

        sqn = result.get("sqn", 0.0)
        sqn_grade = result.get("sqn_grade", "N/A")
        print(f"  SQN:            {sqn:>12.2f}  ({sqn_grade})")

        alpha = result.get("alpha", 0.0)
        bh = result.get("buyhold_return", 0.0)
        print(f"  Alpha:          {alpha:>+11.2f}%  (B&H: {bh:+.2f}%)")
        print(f"  Exposure:       {result.get('exposure_pct', 0):>11.1f}%")

        kelly = result.get("kelly_pct", 0.0)
        cagr = result.get("cagr_pct", 0.0)
        print(f"  Kelly:          {kelly:>+11.2f}%")
        print(f"  CAGR:           {cagr:>+11.2f}%")

        # Per-strategy breakdown
        for strat in ("range", "trend", "crash", "burst", "newarch"):
            strat_t = [t for t in result["trades"] if t.strategy == strat]
            if strat_t:
                sw = sum(1 for t in strat_t if t.pnl > 0)
                print(f"  {strat.title():14s}  {sw}W / {len(strat_t) - sw}L")

        # Confidence distribution
        conf_dist = result.get("confidence_dist", {})
        if conf_dist:
            print(f"\n  Confidence Distribution:")
            for strat, stats in sorted(conf_dist.items()):
                print(f"    {strat:8s}  n={stats['count']:>3}  "
                      f"min={stats['min']:.2f}  med={stats['median']:.2f}  "
                      f"max={stats['max']:.2f}")

    print(f"{'═' * 50}")


def _print_trade_log(result: dict, limit: int = 50):
    """Print trade table."""
    trades = result.get("trades", [])
    if not trades:
        print("\n  No trades.")
        return

    print(f"\n  {'#':>3}  {'Side':5s} {'Strat':7s} {'Entry':>10s}  {'Exit':>10s}  {'PnL':>10s}  {'R':4s} {'Conf':5s}")
    print(f"  {'─' * 65}")
    for i, t in enumerate(trades[:limit], 1):
        pnl_str = f"${t.pnl:+.2f}"
        print(
            f"  {i:>3}  {t.side:5s} {t.strategy:7s} "
            f"{t.entry:>10.2f}  {t.exit:>10.2f}  {pnl_str:>10s}  "
            f"{t.exit_reason:4s} {t.confidence:.2f}"
        )
    if len(trades) > limit:
        print(f"  ... ({len(trades) - limit} more trades)")


def _save_equity_chart(result: dict, label: str) -> str | None:
    """Save equity curve PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    eq = result.get("equity_curve", [])
    if not eq:
        return None

    equities = [e["equity"] for e in eq]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(range(len(equities)), equities, linewidth=1, color="#2196F3", label="Equity")
    ax.axhline(y=result.get("initial_balance", equities[0]),
               color="gray", linestyle="--", alpha=0.5, label="Initial")

    # Mark trades
    for trade in result["trades"]:
        color = "#4CAF50" if trade.pnl > 0 else "#F44336"
        marker = "^" if trade.side == "LONG" else "v"
        for idx, e in enumerate(eq):
            if e["time"] == trade.entry_time:
                ax.scatter(idx, equities[idx], color=color, marker=marker, s=40, zorder=5)
                break

    ret = result["return_pct"]
    sharpe = result.get("sharpe_ratio", 0.0)
    ax.set_title(f"{label} — Return: {ret:+.2f}% — Sharpe: {sharpe:.2f}")
    ax.set_xlabel("1H Candles")
    ax.set_ylabel("Equity ($)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    fname = label.lower().replace(" ", "_").replace(":", "")
    path = os.path.join(DATA_DIR, f"bt_newarch_{fname}.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _success_criteria(result: dict) -> dict:
    """Check success criteria and return verdict."""
    checks = {
        "sharpe_gt_1": result.get("sharpe_ratio", 0) > 1.0,
        "winrate_gt_45": result.get("win_rate", 0) > 45,
        "mdd_lt_15": result.get("max_drawdown_pct", 100) < 15,
        "pf_gt_1.5": (result.get("profit_factor", 0) or 0) > 1.5,
        "trades_gt_30": result.get("total_trades", 0) >= 30,
    }

    bh_return = result.get("buyhold_return", 0)
    strategy_return = result.get("return_pct", 0)
    sharpe = result.get("sharpe_ratio", 0)
    # Risk-adjusted: either positive alpha or Sharpe > 1
    checks["beats_bh_risk_adj"] = (strategy_return > bh_return) or sharpe > 1.0

    return checks


def main():
    parser = argparse.ArgumentParser(description="NewArch Backtest Validation")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance (default 10000)")
    parser.add_argument("--compare", action="store_true", help="Also run range baseline for comparison")
    parser.add_argument("--quiet", action="store_true", help="Suppress engine output")
    args = parser.parse_args()

    print(f"\n{'=' * 55}")
    print(f"  NewArch 5-Layer Architecture — Backtest Validation")
    print(f"  Data: BTCUSDT 4H — 2024-02 to 2026-03 (~2 years)")
    print(f"  Balance: ${args.balance:,.0f}")
    print(f"{'=' * 55}")

    # ── Run 1: NewArch (isolated) ──
    print("\n[1] Running NewArch strategy...")
    newarch_result = _run_newarch(args.balance, quiet=args.quiet)
    _print_results(newarch_result, "NewArch 5-Layer")
    _print_trade_log(newarch_result)

    # Equity chart
    chart = _save_equity_chart(newarch_result, "NewArch_BTC_2yr")
    if chart:
        print(f"\n  Chart → {chart}")

    # ── Success criteria ──
    checks = _success_criteria(newarch_result)
    print(f"\n  {'Success Criteria':^40}")
    print(f"  {'─' * 40}")
    all_pass = True
    for name, passed in checks.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}]  {name}")
        if not passed:
            all_pass = False

    if all_pass:
        print(f"\n  ALL CRITERIA PASSED")
    else:
        print(f"\n  SOME CRITERIA FAILED — review parameters")

    # ── Run 2: Range baseline (optional) ──
    if args.compare:
        print("\n[2] Running Range baseline for comparison...")
        range_result = _run_range_baseline(args.balance, quiet=args.quiet)
        _print_results(range_result, "Range Baseline")

        chart2 = _save_equity_chart(range_result, "Range_Baseline_BTC_2yr")
        if chart2:
            print(f"\n  Chart → {chart2}")

    print()


if __name__ == "__main__":
    main()
