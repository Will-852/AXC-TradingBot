#!/usr/bin/env python3
"""
run_optimizer.py — CLI entry point for backtest weight optimization.

用法：
  python3 backtest/run_optimizer.py --mode stage1 --pairs BTC,ETH,XRP
  python3 backtest/run_optimizer.py --mode stage2
  python3 backtest/run_optimizer.py --mode full
  python3 backtest/run_optimizer.py --mode validate

設計決定：
  - JSON + terminal table 雙輸出
  - 進度用 logging（optuna 自帶 progress bar）
  - --samples / --trials 可 override 預設值（方便快速測試）
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.optimizer import (
    run_stage1, run_stage2, run_walk_forward,
    run_full_optimization, load_all_data, run_single_backtest,
    check_stability, apply_shrinkage,
    compute_objective,
)
from backtest.weight_config import (
    OptimizerConfig, ENTRY_DEFAULTS, WEIGHT_DEFAULTS,
    ENTRY_SEARCH_SPACE, WEIGHT_SEARCH_SPACE,
)

OUTPUT_DIR = os.path.join(AXC_HOME, "backtest", "data")
log = logging.getLogger("optimizer")


# ═══════════════════════════════════════════════════════
# Terminal Table Formatting
# ═══════════════════════════════════════════════════════

def print_stage1_table(results: list[dict]):
    """Print Stage 1 results as terminal table."""
    print(f"\n{'=' * 90}")
    print("  STAGE 1 — Entry Parameter Search (LHS)")
    print(f"{'=' * 90}")

    viable = [r for r in results if r.get("viable")]
    non_viable = [r for r in results if not r.get("viable")]

    print(f"\n  Viable: {len(viable)} / {len(results)} configs")

    if not viable:
        print("  No viable configs found!")
        if non_viable:
            print("\n  Top 5 non-viable (by trade count):")
            sorted_nv = sorted(non_viable, key=lambda r: r["total_trades"], reverse=True)[:5]
            print(f"  {'#':<4} {'Trades':>7} {'PnL':>10} {'bb_wid':>7} {'adx':>5} "
                  f"{'vol_min':>8} {'pullback':>9} {'min_keys':>9}")
            print("  " + "─" * 70)
            for i, r in enumerate(sorted_nv):
                p = r["params"]
                print(f"  {i+1:<4} {r['total_trades']:>7} ${r['total_pnl']:>+9.0f} "
                      f"{p.get('bb_width_min', '-'):>7} {p.get('adx_range_max', '-'):>5} "
                      f"{p.get('entry_volume_min', '-'):>8} {p.get('pullback_tolerance', '-'):>9} "
                      f"{p.get('trend_min_keys', '-'):>9}")
        return

    print(f"\n  {'#':<4} {'Obj':>8} {'Trades':>7} {'PnL':>10} {'bb_wid':>7} {'adx':>5} "
          f"{'vol_min':>8} {'pullback':>9} {'min_keys':>9}")
    print("  " + "─" * 80)

    for i, r in enumerate(viable[:15]):
        p = r["params"]
        print(f"  {i+1:<4} {r['objective']:>8.1f} {r['total_trades']:>7} ${r['total_pnl']:>+9.0f} "
              f"{p.get('bb_width_min', '-'):>7} {p.get('adx_range_max', '-'):>5} "
              f"{p.get('entry_volume_min', '-'):>8} {p.get('pullback_tolerance', '-'):>9} "
              f"{p.get('trend_min_keys', '-'):>9}")

    # Trade distribution
    print(f"\n  Trade distribution per pair:")
    for r in viable[:5]:
        tpp = r.get("trades_per_pair", {})
        parts = [f"{p.replace('USDT','')}: {n}" for p, n in tpp.items()]
        print(f"    Config {viable.index(r)+1}: {', '.join(parts)}")


def print_stage2_table(results: list[dict]):
    """Print Stage 2 results."""
    print(f"\n{'=' * 90}")
    print("  STAGE 2 — Scoring Weight Optimization (Bayesian)")
    print(f"{'=' * 90}")

    if not results:
        print("  No results!")
        return

    for i, r in enumerate(results):
        print(f"\n  Config {i+1} (obj={r['best_objective']:.1f}, trials={r['n_trials']}):")
        w = r["best_weights"]
        print(f"    Weights: w_vol={w.get('w_vol', '?'):.3f}  w_obv={w.get('w_obv', '?'):.3f}  "
              f"w_stoch={w.get('w_stoch', '?'):.3f}  "
              f"base_strong={w.get('base_score_strong', '?'):.2f}  "
              f"base_weak={w.get('base_score_weak', '?'):.2f}")

        if r.get("results_by_pair"):
            print(f"\n    {'Pair':<10} {'Trades':>7} {'PnL':>10} {'WR%':>6} {'MaxDD':>6} {'PF':>6}")
            print("    " + "─" * 50)
            for pair, pr in r["results_by_pair"].items():
                pf = pr.get("profit_factor", 0)
                pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) and pf != float("inf") else str(pf)
                print(f"    {pair:<10} {pr['total_trades']:>7} ${pr['pnl']:>+9.0f} "
                      f"{pr['win_rate']:>5.1f} {pr['max_drawdown_pct']:>5.1f}% {pf_str:>6}")


def print_walk_forward_table(results: list[dict]):
    """Print walk-forward validation results."""
    print(f"\n{'=' * 90}")
    print("  WALK-FORWARD VALIDATION")
    print(f"{'=' * 90}")

    if not results:
        print("  No results!")
        return

    for i, wf in enumerate(results):
        status = "PASS" if wf["passed"] else "FAIL"
        print(f"\n  Config {i+1}: {status} (degradation={wf['degradation_pct']:.1f}%)")
        print(f"    IS avg={wf['in_sample_avg']:.1f}  OOS avg={wf['out_of_sample_avg']:.1f}")

        for fold in wf.get("folds", []):
            print(f"    Fold {fold['fold']}: IS={fold['in_sample_obj']:.1f} "
                  f"OOS={fold['out_of_sample_obj']:.1f} "
                  f"(IS_trades={fold['is_trades']}, OOS_trades={fold['oos_trades']})")


def print_stability_table(stability: dict):
    """Print parameter stability analysis."""
    print(f"\n{'=' * 90}")
    print("  PARAMETER STABILITY (±10% step)")
    print(f"{'=' * 90}")

    if not stability:
        print("  No stability data!")
        return

    print(f"\n  {'Param':<28} {'−step':>8} {'center':>8} {'+step':>8} {'Δ−%':>6} {'Δ+%':>6} {'Cliff':>6}")
    print("  " + "─" * 76)

    for name, s in stability.items():
        cliff_str = "⚠️" if s["cliff"] else "OK"
        print(f"  {name:<28} {s['minus']:>8.1f} {s['center']:>8.1f} {s['plus']:>8.1f} "
              f"{s['delta_minus_pct']:>+5.1f}% {s['delta_plus_pct']:>+5.1f}% {cliff_str:>6}")


def print_best_config(best: dict):
    """Print final recommendation."""
    print(f"\n{'=' * 90}")
    print("  FINAL RECOMMENDATION")
    print(f"{'=' * 90}")

    if not best:
        print("  No best config found!")
        return

    print(f"\n  Objective: {best.get('objective', '?')}")
    print(f"  Cross-pair consistent: {best.get('cross_pair_consistent', '?')} "
          f"({best.get('positive_pairs', '?')}/8 positive)")

    if best.get("cliff_edges"):
        print(f"  ⚠️  Cliff-edge parameters: {', '.join(best['cliff_edges'])}")

    print(f"\n  Entry parameters:")
    for k, v in best.get("entry_params", {}).items():
        default = ENTRY_DEFAULTS.get(k, "?")
        changed = " ← CHANGED" if v != default else ""
        print(f"    {k}: {v} (default: {default}){changed}")

    print(f"\n  Shrunk weights (70% optimized + 30% default):")
    for k, v in best.get("shrunk_weights", {}).items():
        raw = best.get("raw_weights", {}).get(k, "?")
        default = WEIGHT_DEFAULTS.get(k, "?")
        print(f"    {k}: {v} (raw: {raw}, default: {default})")


def save_results(output: dict, filename: str):
    """Save results to JSON."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)

    # Convert non-serializable objects
    def _clean(obj):
        if hasattr(obj, "__dict__"):
            return {k: _clean(v) for k, v in obj.__dict__.items()}
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_clean(v) for v in obj]
        if isinstance(obj, float) and (obj == float("inf") or obj == float("-inf")):
            return str(obj)
        return obj

    with open(path, "w") as f:
        json.dump(_clean(output), f, indent=2, default=str)

    print(f"\n  Results saved to: {path}")


# ═══════════════════════════════════════════════════════
# CLI Modes
# ═══════════════════════════════════════════════════════

def cmd_stage1(args, config: OptimizerConfig):
    """Run Stage 1 only."""
    if args.pairs:
        config.stage1_pairs = [p.upper() + ("USDT" if "USDT" not in p.upper() else "")
                               for p in args.pairs.split(",")]

    data = load_all_data(config.stage1_pairs, config.backtest_days)
    results = run_stage1(config, data=data)

    output = [
        {"params": r.params, "total_trades": r.total_trades,
         "total_pnl": r.total_pnl, "trades_per_pair": r.trades_per_pair,
         "objective": round(r.objective, 4) if r.objective != float("-inf") else "-inf",
         "viable": r.viable}
        for r in results[:20]
    ]

    print_stage1_table(output)
    save_results({"stage1": output}, f"optimizer_stage1_{datetime.now().strftime('%Y%m%d_%H%M')}.json")


def cmd_stage2(args, config: OptimizerConfig):
    """Run Stage 2 with provided viable configs (or from Stage 1 results)."""
    # Try loading Stage 1 results
    s1_file = args.stage1_file
    if not s1_file:
        # Find latest stage1 file
        files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.startswith("optimizer_stage1_")])
        if not files:
            print("No Stage 1 results found! Run --mode stage1 first.")
            return
        s1_file = os.path.join(OUTPUT_DIR, files[-1])

    with open(s1_file) as f:
        s1_data = json.load(f)

    viable_params = [r["params"] for r in s1_data.get("stage1", s1_data) if r.get("viable")]
    if not viable_params:
        print("No viable configs in Stage 1 results!")
        return

    viable_params = viable_params[:config.max_viable_configs]
    data = load_all_data(config.stage2_pairs, config.backtest_days)
    s2_results = run_stage2(viable_params, config, data=data)

    output = [
        {"entry_params": r.entry_params, "best_weights": r.best_weights,
         "best_objective": round(r.best_objective, 4), "n_trials": r.n_trials,
         "results_by_pair": r.results_by_pair}
        for r in s2_results
    ]

    print_stage2_table(output)
    save_results({"stage2": output}, f"optimizer_stage2_{datetime.now().strftime('%Y%m%d_%H%M')}.json")


def cmd_full(args, config: OptimizerConfig):
    """Run full pipeline."""
    if args.pairs:
        pairs = [p.upper() + ("USDT" if "USDT" not in p.upper() else "")
                 for p in args.pairs.split(",")]
        config.stage1_pairs = pairs[:3]  # Stage 1 uses 3

    result = run_full_optimization(config)

    # Print all tables
    print_stage1_table(result.stage1_results)
    print_stage2_table(result.stage2_results)
    print_walk_forward_table(result.walk_forward)
    print_stability_table(result.stability)
    print_best_config(result.best_config)

    # Save
    output = {
        "stage1": result.stage1_results,
        "stage2": result.stage2_results,
        "walk_forward": result.walk_forward,
        "stability": result.stability,
        "best_config": result.best_config,
        "baseline": result.baseline_comparison,
    }
    save_results(output, f"optimizer_full_{datetime.now().strftime('%Y%m%d_%H%M')}.json")


def cmd_validate(args, config: OptimizerConfig):
    """Run walk-forward validation on a Stage 2 result."""
    s2_file = args.stage2_file
    if not s2_file:
        files = sorted([f for f in os.listdir(OUTPUT_DIR)
                       if f.startswith("optimizer_stage2_") or f.startswith("optimizer_full_")])
        if not files:
            print("No Stage 2 results found!")
            return
        s2_file = os.path.join(OUTPUT_DIR, files[-1])

    with open(s2_file) as f:
        s2_data = json.load(f)

    s2_list = s2_data.get("stage2", [])
    if not s2_list:
        print("No Stage 2 configs to validate!")
        return

    data = load_all_data(config.stage2_pairs, config.backtest_days)

    from backtest.optimizer import Stage2Result
    configs = [
        Stage2Result(
            entry_params=r["entry_params"],
            best_weights=r["best_weights"],
            best_objective=r.get("best_objective", 0),
            results_by_pair=r.get("results_by_pair", {}),
        )
        for r in s2_list
    ]

    wf_results = run_walk_forward(configs, data, config)

    output = [
        {"entry_params": wf.entry_params, "weights": wf.weights,
         "folds": wf.folds, "in_sample_avg": round(wf.in_sample_avg, 4),
         "out_of_sample_avg": round(wf.out_of_sample_avg, 4),
         "degradation_pct": round(wf.degradation_pct, 1), "passed": wf.passed}
        for wf in wf_results
    ]

    print_walk_forward_table(output)

    # Stability for best
    if wf_results:
        best_idx = 0
        for i, wf in enumerate(wf_results):
            if wf.passed:
                best_idx = i
                break

        shrunk = apply_shrinkage(configs[best_idx].best_weights, config.shrinkage_factor)
        stability = check_stability(
            configs[best_idx].entry_params, shrunk, data, config.stage2_pairs,
        )
        print_stability_table(stability)

    save_results({"walk_forward": output},
                 f"optimizer_validate_{datetime.now().strftime('%Y%m%d_%H%M')}.json")


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Backtest Weight Optimization System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Stage 1 only (quick scan):
    python3 backtest/run_optimizer.py --mode stage1 --samples 50 --pairs BTC,ETH,XRP

  Full pipeline:
    python3 backtest/run_optimizer.py --mode full

  Validate existing results:
    python3 backtest/run_optimizer.py --mode validate
        """,
    )
    parser.add_argument("--mode", choices=["stage1", "stage2", "full", "validate"],
                        default="full", help="Which stage to run")
    parser.add_argument("--pairs", type=str, default=None,
                        help="Comma-separated pairs (e.g. BTC,ETH,XRP)")
    parser.add_argument("--samples", type=int, default=None,
                        help="Override Stage 1 sample count")
    parser.add_argument("--trials", type=int, default=None,
                        help="Override Stage 2 trial count")
    parser.add_argument("--days", type=int, default=None,
                        help="Override backtest days")
    parser.add_argument("--min-trades", type=int, default=None,
                        help="Override minimum trades per pair for viability")
    parser.add_argument("--workers", type=int, default=None,
                        help="Override max parallel workers")
    parser.add_argument("--stage1-file", type=str, default=None,
                        help="Stage 1 results file (for --mode stage2)")
    parser.add_argument("--stage2-file", type=str, default=None,
                        help="Stage 2 results file (for --mode validate)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging")

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Build config
    config = OptimizerConfig()
    if args.samples:
        config.stage1_samples = args.samples
    if args.trials:
        config.stage2_trials = args.trials
    if args.days:
        config.backtest_days = args.days
    if args.min_trades is not None:
        config.stage1_min_trades = args.min_trades
    if args.workers:
        config.max_workers = args.workers

    print(f"\n  Backtest Weight Optimizer")
    print(f"  Mode: {args.mode}")
    print(f"  Stage 1: {config.stage1_samples} samples × {len(config.stage1_pairs)} pairs")
    print(f"  Stage 2: {config.stage2_trials} trials × {len(config.stage2_pairs)} pairs")
    print(f"  Backtest: {config.backtest_days} days")

    start = time.time()

    if args.mode == "stage1":
        cmd_stage1(args, config)
    elif args.mode == "stage2":
        cmd_stage2(args, config)
    elif args.mode == "full":
        cmd_full(args, config)
    elif args.mode == "validate":
        cmd_validate(args, config)

    elapsed = time.time() - start
    print(f"\n  Total time: {elapsed/60:.1f} minutes")


if __name__ == "__main__":
    main()
