#!/usr/bin/env python3
"""
run_newarch_sweep.py — Parameter sensitivity sweep + walk-forward validation
for NewArch 5-Layer strategy.

設計決定：
  - Standalone script（唔改 grid_search.py）— data source pattern 唔同
    grid_search fetches live API data；呢度用 existing local CSV for reproducibility
  - ProcessPoolExecutor for parallel combos
  - Heatmap: matplotlib pivot → red/white/green color map
  - Walk-forward: train/test temporal split → sweep train → lock → run test

用法:
  # 2D heat map sweep
  python3 backtest/run_newarch_sweep.py --sweep z_pullback adx_trending

  # Long-only variant
  python3 backtest/run_newarch_sweep.py --sweep z_pullback adx_trending --long-only

  # Walk-forward validation
  python3 backtest/run_newarch_sweep.py --sweep z_pullback adx_trending --walk-forward

  # Single combo test
  python3 backtest/run_newarch_sweep.py --test z_pullback=1.5 adx_trending=25
"""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.engine import BacktestEngine, WARMUP_CANDLES
from backtest.metrics_ext import extend_summary
from backtest.strategies.bt_newarch_strategy import BTNewArchStrategy
from trader_cycle.strategies.base import StrategyBase, PositionParams
from trader_cycle.core.context import CycleContext, Signal

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(AXC_HOME, "backtest", "data")

# Same data files as run_newarch.py
DATA_4H = os.path.join(DATA_DIR, "BTCUSDT_4h_20240222_20260317.csv")
DATA_1H = os.path.join(DATA_DIR, "BTCUSDT_1h_20240318_20260317.csv")

# ─── Param Grid ───
PARAM_RANGES: dict[str, list[float]] = {
    "z_pullback":    [round(v, 2) for v in np.arange(0.75, 2.75, 0.25)],   # 8 steps
    "z_reversion":   [round(v, 2) for v in np.arange(1.0, 3.25, 0.25)],    # 9 steps
    "adx_trending":  [float(v) for v in range(15, 45, 5)],                   # 6 steps
    "adx_deep_range":[float(v) for v in range(10, 35, 5)],                   # 5 steps
    "bb_pctl_dead":  [float(v) for v in range(0, 25, 5)],                    # 5 steps
    "bb_pctl_range": [float(v) for v in range(10, 55, 5)],                   # 9 steps
}

# Walk-forward temporal split dates (ISO format for pd.Timestamp comparison)
WF_TRAIN_END = "2025-06-01"    # train: start → 2025-06-01
WF_TEST_START = "2025-06-01"   # test:  2025-06-01 → end

BALANCE = 10000.0
MAX_WORKERS = 4


class _NullStrategy(StrategyBase):
    """No-op strategy for isolating newarch."""
    name = "null"
    mode = ""
    required_timeframes = ["4h", "1h"]
    def evaluate(self, pair, indicators, ctx):
        return None
    def get_position_params(self):
        return PositionParams(risk_pct=0.02, leverage=5, sl_atr_mult=1.5, min_rr=2.0)


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


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load full dataset (cached at module level for subprocess reuse)."""
    return _load_csv(DATA_4H), _load_csv(DATA_1H)


def _slice_data(
    df_4h: pd.DataFrame, df_1h: pd.DataFrame,
    start: str | None = None, end: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Slice dataframes by timestamp range."""
    if start:
        ts = pd.Timestamp(start, tz="UTC")
        df_4h = df_4h[df_4h["timestamp"] >= ts].reset_index(drop=True)
        df_1h = df_1h[df_1h["timestamp"] >= ts].reset_index(drop=True)
    if end:
        ts = pd.Timestamp(end, tz="UTC")
        df_4h = df_4h[df_4h["timestamp"] < ts].reset_index(drop=True)
        df_1h = df_1h[df_1h["timestamp"] < ts].reset_index(drop=True)
    return df_4h, df_1h


def _run_combo(params: dict[str, Any], long_only: bool = False,
               df_4h: pd.DataFrame | None = None,
               df_1h: pd.DataFrame | None = None) -> dict:
    """Run a single param combo. Returns result dict with params attached."""
    if df_4h is None or df_1h is None:
        df_4h, df_1h = _load_data()

    strategy = BTNewArchStrategy(overrides=params, long_only=long_only)
    null = _NullStrategy()

    engine = BacktestEngine(
        symbol="BTCUSDT",
        df_1h=df_1h,
        df_4h=df_4h,
        initial_balance=BALANCE,
        strategy_overrides={
            "range": null, "trend": null, "crash": null,
            "newarch": strategy,
        },
        signal_delay=1,
        quiet=True,
    )
    result = extend_summary(engine.run())
    return {
        "params": params,
        "long_only": long_only,
        "return_pct": result.get("return_pct", 0.0),
        "sharpe_ratio": result.get("sharpe_ratio", 0.0),
        "win_rate": result.get("win_rate", 0.0),
        "total_trades": result.get("total_trades", 0),
        "profit_factor": result.get("profit_factor", 0.0) or 0.0,
        "max_drawdown_pct": result.get("max_drawdown_pct", 0.0),
        "expectancy": result.get("expectancy", 0.0),
    }


# ─── Top-level wrapper for ProcessPoolExecutor (pickling requirement) ───
_sweep_long_only = False
_sweep_df_4h: pd.DataFrame | None = None
_sweep_df_1h: pd.DataFrame | None = None

def _worker_init(long_only: bool, data_4h_path: str, data_1h_path: str):
    """Initialize worker process with shared data."""
    global _sweep_long_only, _sweep_df_4h, _sweep_df_1h
    _sweep_long_only = long_only
    _sweep_df_4h = _load_csv(data_4h_path)
    _sweep_df_1h = _load_csv(data_1h_path)

def _worker_run(params: dict) -> dict:
    """Worker entry point — uses process-global data."""
    return _run_combo(params, long_only=_sweep_long_only,
                      df_4h=_sweep_df_4h, df_1h=_sweep_df_1h)

def _worker_init_sliced(long_only: bool, data_4h_path: str, data_1h_path: str,
                        start: str | None, end: str | None):
    """Initialize worker with temporally sliced data (for walk-forward)."""
    global _sweep_long_only, _sweep_df_4h, _sweep_df_1h
    _sweep_long_only = long_only
    df_4h = _load_csv(data_4h_path)
    df_1h = _load_csv(data_1h_path)
    _sweep_df_4h, _sweep_df_1h = _slice_data(df_4h, df_1h, start, end)

def _worker_init_sliced_wrapper(args):
    """Unpack args for initializer (ProcessPoolExecutor limitation)."""
    _worker_init_sliced(*args)


def _build_grid(param_a: str, param_b: str) -> list[dict]:
    """Build 2D parameter grid from two param names."""
    vals_a = PARAM_RANGES[param_a]
    vals_b = PARAM_RANGES[param_b]
    grid = []
    for va, vb in itertools.product(vals_a, vals_b):
        grid.append({param_a: va, param_b: vb})
    return grid


def _run_sweep(grid: list[dict], long_only: bool = False,
               start: str | None = None, end: str | None = None,
               workers: int = MAX_WORKERS) -> list[dict]:
    """Run sweep over param grid using ProcessPoolExecutor."""
    total = len(grid)
    print(f"  Sweep: {total} combos, {workers} workers"
          f"{', long-only' if long_only else ''}"
          f"{f', range {start} → {end}' if start or end else ''}",
          flush=True)

    results = []
    t0 = time.time()

    if start or end:
        init_args = (long_only, DATA_4H, DATA_1H, start, end)
        initializer = _worker_init_sliced
    else:
        init_args = (long_only, DATA_4H, DATA_1H)
        initializer = _worker_init

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=initializer,
        initargs=init_args,
    ) as pool:
        futures = {pool.submit(_worker_run, p): p for p in grid}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                params = futures[future]
                log.error("Combo %s failed: %s", params, e)
                results.append({
                    "params": params, "return_pct": float("nan"),
                    "sharpe_ratio": float("nan"), "win_rate": 0,
                    "total_trades": 0, "profit_factor": 0,
                    "max_drawdown_pct": 0, "expectancy": 0,
                })
            elapsed = time.time() - t0
            eta = (elapsed / done) * (total - done) if done > 0 else 0
            print(f"    [{done}/{total}] {elapsed:.0f}s elapsed, ~{eta:.0f}s ETA",
                  flush=True)

    elapsed = time.time() - t0
    print(f"  Sweep complete: {elapsed:.1f}s total", flush=True)
    return results


def _plot_heatmap(results: list[dict], param_a: str, param_b: str,
                  long_only: bool = False, suffix: str = "") -> str | None:
    """Generate heatmap PNG from sweep results."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import TwoSlopeNorm
    except ImportError:
        print("  matplotlib not available — skipping heatmap")
        return None

    # Build pivot table
    rows = []
    for r in results:
        rows.append({
            param_a: r["params"][param_a],
            param_b: r["params"][param_b],
            "return_pct": r["return_pct"],
            "sharpe_ratio": r["sharpe_ratio"],
        })
    df = pd.DataFrame(rows)

    for metric, cmap_label in [("return_pct", "Return %"), ("sharpe_ratio", "Sharpe")]:
        pivot = df.pivot_table(index=param_b, columns=param_a, values=metric, aggfunc="first")
        pivot = pivot.sort_index(ascending=False)  # high values on top

        fig, ax = plt.subplots(figsize=(max(10, len(pivot.columns) * 1.2),
                                        max(6, len(pivot.index) * 0.8)))

        # Red-white-green colormap centered at zero
        vmin = pivot.min().min()
        vmax = pivot.max().max()
        if vmin >= 0:
            vcenter = vmin + (vmax - vmin) * 0.01
        elif vmax <= 0:
            vcenter = vmax - (vmax - vmin) * 0.01
        else:
            vcenter = 0

        norm = TwoSlopeNorm(vmin=vmin, vcenter=vcenter, vmax=vmax)

        im = ax.imshow(pivot.values, cmap="RdYlGn", norm=norm, aspect="auto")

        # Annotate cells
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.values[i, j]
                if np.isnan(val):
                    txt = "ERR"
                elif metric == "return_pct":
                    txt = f"{val:+.1f}%"
                else:
                    txt = f"{val:.2f}"
                color = "white" if abs(val) > (vmax - vmin) * 0.4 else "black"
                ax.text(j, i, txt, ha="center", va="center", fontsize=8, color=color)

        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([f"{v:.2f}" if isinstance(v, float) and v != int(v) else str(int(v))
                           for v in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([f"{v:.2f}" if isinstance(v, float) and v != int(v) else str(int(v))
                           for v in pivot.index])
        ax.set_xlabel(param_a)
        ax.set_ylabel(param_b)

        mode_tag = " (LONG-ONLY)" if long_only else ""
        ax.set_title(f"NewArch {cmap_label}{mode_tag}: {param_a} × {param_b}")
        fig.colorbar(im, ax=ax, label=cmap_label)
        fig.tight_layout()

        tag = f"{'lo_' if long_only else ''}{suffix}" if suffix or long_only else ""
        fname = f"newarch_sweep_{param_a}_{param_b}_{metric}{f'_{tag}' if tag else ''}.png"
        path = os.path.join(DATA_DIR, fname)
        fig.savefig(path, dpi=150)
        plt.close(fig)
        print(f"  Heatmap → {path}")

    return path


def _walk_forward(param_a: str, param_b: str, long_only: bool = False,
                  workers: int = MAX_WORKERS):
    """Walk-forward validation: sweep on train period, validate on test period."""
    print(f"\n{'═' * 60}")
    print(f"  Walk-Forward Validation")
    print(f"  Train: start → {WF_TRAIN_END}")
    print(f"  Test:  {WF_TEST_START} → end")
    print(f"{'═' * 60}")

    grid = _build_grid(param_a, param_b)

    # ── Train sweep ──
    print(f"\n[Train] Sweeping {len(grid)} combos...")
    train_results = _run_sweep(grid, long_only=long_only, end=WF_TRAIN_END, workers=workers)
    _plot_heatmap(train_results, param_a, param_b, long_only=long_only, suffix="train")

    # Sort by return_pct, pick top 3
    valid = [r for r in train_results if not np.isnan(r["return_pct"])]
    valid.sort(key=lambda r: r["return_pct"], reverse=True)
    top3 = valid[:3]

    print(f"\n  Top-3 on train period:")
    print(f"  {'Rank':>4}  {param_a:>12}  {param_b:>12}  {'Return':>8}  {'Sharpe':>7}  {'Trades':>6}  {'WR':>5}")
    print(f"  {'─' * 60}")
    for i, r in enumerate(top3, 1):
        print(f"  {i:>4}  {r['params'][param_a]:>12}  {r['params'][param_b]:>12}  "
              f"{r['return_pct']:>+7.2f}%  {r['sharpe_ratio']:>7.2f}  "
              f"{r['total_trades']:>6}  {r['win_rate']:>4.1f}%")

    # ── Test: run top-3 combos on test period ──
    print(f"\n[Test] Running top-3 combos on test period ({WF_TEST_START} → end)...")
    print(f"  {'Rank':>4}  {param_a:>12}  {param_b:>12}  {'Train':>8}  {'Test':>8}  {'Trades':>6}  {'Overfit?':>8}")
    print(f"  {'─' * 70}")

    for i, r in enumerate(top3, 1):
        test_result = _run_combo_sliced(r["params"], long_only=long_only,
                                         start=WF_TEST_START, end=None)
        train_ret = r["return_pct"]
        test_ret = test_result["return_pct"]
        test_trades = test_result["total_trades"]

        # Overfit heuristic: test return < 50% of train return, or test is negative while train positive
        if train_ret > 0 and test_ret < 0:
            overfit = "YES"
        elif train_ret > 0 and test_ret < train_ret * 0.5:
            overfit = "LIKELY"
        else:
            overfit = "NO"

        print(f"  {i:>4}  {r['params'][param_a]:>12}  {r['params'][param_b]:>12}  "
              f"{train_ret:>+7.2f}%  {test_ret:>+7.2f}%  {test_trades:>6}  {overfit:>8}")


def _run_combo_sliced(params: dict, long_only: bool = False,
                      start: str | None = None, end: str | None = None) -> dict:
    """Run single combo on a time-sliced dataset (for walk-forward test)."""
    df_4h, df_1h = _load_data()
    df_4h, df_1h = _slice_data(df_4h, df_1h, start, end)
    return _run_combo(params, long_only=long_only, df_4h=df_4h, df_1h=df_1h)


def _print_summary_table(results: list[dict], param_a: str, param_b: str):
    """Print top-10 results sorted by return."""
    valid = [r for r in results if not np.isnan(r["return_pct"])]
    valid.sort(key=lambda r: r["return_pct"], reverse=True)

    print(f"\n  Top-10 Parameter Combos (by return):")
    print(f"  {'#':>3}  {param_a:>12}  {param_b:>12}  {'Return':>8}  {'Sharpe':>7}  "
          f"{'Trades':>6}  {'WR':>5}  {'PF':>5}  {'MDD':>6}")
    print(f"  {'─' * 75}")
    for i, r in enumerate(valid[:10], 1):
        print(f"  {i:>3}  {r['params'][param_a]:>12}  {r['params'][param_b]:>12}  "
              f"{r['return_pct']:>+7.2f}%  {r['sharpe_ratio']:>7.2f}  "
              f"{r['total_trades']:>6}  {r['win_rate']:>4.1f}%  "
              f"{r['profit_factor']:>5.2f}  {r['max_drawdown_pct']:>5.1f}%")

    # Diagnosis
    best = valid[0] if valid else None
    all_negative = all(r["return_pct"] < 0 for r in valid)
    green_count = sum(1 for r in valid if r["return_pct"] > 0)

    print(f"\n  Diagnosis:")
    if all_negative:
        print(f"  ALL RED — No edge exists in any param combo. Architecture broken for BTC.")
    elif green_count == 1:
        print(f"  ONE GREEN SPIKE — Likely overfitting. Walk-forward will confirm.")
    elif green_count <= 3:
        print(f"  FEW GREEN — Fragile edge, needs walk-forward confirmation.")
    else:
        pct = green_count / len(valid) * 100
        print(f"  {green_count}/{len(valid)} ({pct:.0f}%) positive — "
              f"{'Green plateau — real edge candidate.' if pct > 30 else 'Narrow edge region.'}")


def _save_results_json(results: list[dict], param_a: str, param_b: str,
                       long_only: bool = False, suffix: str = ""):
    """Persist sweep results to JSON for later analysis."""
    import json
    tag = f"{'lo_' if long_only else ''}{suffix}" if suffix or long_only else ""
    fname = f"newarch_sweep_{param_a}_{param_b}{f'_{tag}' if tag else ''}.json"
    path = os.path.join(DATA_DIR, fname)
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Results JSON → {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="NewArch Parameter Sensitivity Sweep")
    parser.add_argument("--sweep", nargs=2, metavar=("PARAM_A", "PARAM_B"),
                        help="Two params to sweep (e.g., z_pullback adx_trending)")
    parser.add_argument("--test", nargs="+", metavar="PARAM=VAL",
                        help="Single combo test (e.g., z_pullback=1.5 adx_trending=25)")
    parser.add_argument("--long-only", action="store_true",
                        help="Discard SHORT signals (bull market hypothesis)")
    parser.add_argument("--walk-forward", action="store_true",
                        help="Run walk-forward validation after sweep")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Parallel workers (default {MAX_WORKERS})")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit grid to first N combos (for quick testing)")
    args = parser.parse_args()

    if not args.sweep and not args.test:
        parser.error("Specify --sweep PARAM_A PARAM_B or --test PARAM=VAL")

    # ── Single combo test ──
    if args.test:
        params = {}
        for kv in args.test:
            key, val = kv.split("=")
            params[key] = float(val)

        print(f"\n{'═' * 50}")
        print(f"  Single Combo Test")
        print(f"  Params: {params}")
        print(f"  Long-only: {args.long_only}")
        print(f"{'═' * 50}")

        result = _run_combo(params, long_only=args.long_only)
        print(f"\n  Return:       {result['return_pct']:>+8.2f}%")
        print(f"  Sharpe:       {result['sharpe_ratio']:>8.2f}")
        print(f"  Win Rate:     {result['win_rate']:>7.1f}%")
        print(f"  Trades:       {result['total_trades']:>8}")
        print(f"  PF:           {result['profit_factor']:>8.2f}")
        print(f"  Max DD:       {result['max_drawdown_pct']:>7.1f}%")
        print(f"  Expectancy:   ${result['expectancy']:>+7.2f}/trade")
        return

    # ── 2D Sweep ──
    param_a, param_b = args.sweep
    for p in (param_a, param_b):
        if p not in PARAM_RANGES:
            parser.error(f"Unknown param '{p}'. Available: {list(PARAM_RANGES.keys())}")

    grid = _build_grid(param_a, param_b)
    if args.limit > 0:
        grid = grid[:args.limit]

    print(f"\n{'═' * 60}")
    print(f"  NewArch Parameter Sensitivity Sweep")
    print(f"  Params: {param_a} × {param_b}")
    print(f"  Grid: {len(grid)} combos{' (limited)' if args.limit else ''}")
    print(f"  Long-only: {args.long_only}")
    print(f"  Workers: {args.workers}")
    print(f"{'═' * 60}\n", flush=True)

    results = _run_sweep(grid, long_only=args.long_only, workers=args.workers)
    _save_results_json(results, param_a, param_b, long_only=args.long_only)
    _plot_heatmap(results, param_a, param_b, long_only=args.long_only)
    _print_summary_table(results, param_a, param_b)

    # ── Walk-forward (optional) ──
    if args.walk_forward:
        _walk_forward(param_a, param_b, long_only=args.long_only, workers=args.workers)


if __name__ == "__main__":
    main()
