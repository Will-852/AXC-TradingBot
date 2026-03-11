#!/usr/bin/env python3
"""
validate.py — 6 Validation Tools for Grid Search Results

設計決定：
  - 3 must-use (monte-carlo, walk-forward, heatmap) + 3 optional (noise, delay, dsr)
  - CLI subcommand 選擇，因為每個 method 有唔同 flags
  - Reuse BacktestEngine + fetch_all_data，唔重複造輪
  - Optional 依賴 (matplotlib, scipy, sklearn) graceful fallback
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ─── Path setup ───
AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

from backtest.engine import BacktestEngine, WARMUP_CANDLES
from backtest.grid_search import fetch_all_data

log = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(AXC_HOME, "backtest", "data")


# ═══════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════

def _parse_params(param_strs: list[str] | None) -> dict:
    """Parse 'key=value' pairs from CLI into dict."""
    if not param_strs:
        return {}
    params = {}
    for s in param_strs:
        if "=" not in s:
            raise ValueError(f"Invalid param format: '{s}' (expected key=value)")
        k, v = s.split("=", 1)
        try:
            params[k] = int(v)
        except ValueError:
            try:
                params[k] = float(v)
            except ValueError:
                params[k] = v
    return params


def _run_backtest(
    params: dict, pairs: list[str], days: int,
    balance: float = 10000.0,
    data: dict | None = None,
    signal_delay: int = 1,
    min_score: float = 0.0,
) -> tuple[dict, list[float]]:
    """
    Run backtest across pairs. Returns (summary_by_pair, all_trades_pnl).

    If data is provided, reuse it (skip fetch).
    min_score: pass through to engine for score-based signal filtering.
    """
    if data is None:
        data = fetch_all_data(pairs, days)

    summaries = {}
    all_pnl = []
    for pair in pairs:
        df_1h, df_4h = data[pair]
        eng = BacktestEngine(
            symbol=pair, df_1h=df_1h, df_4h=df_4h,
            initial_balance=balance,
            param_overrides=params,
            signal_delay=signal_delay,
            min_score=min_score,
            quiet=True,
        )
        s = eng.run()
        summaries[pair] = s
        all_pnl.extend([t.pnl for t in s["trades"]])

    return summaries, all_pnl


def _total_return_pct(summaries: dict, balance: float) -> float:
    """Average return% across all pairs."""
    rets = [(s["final_balance"] - balance) / balance * 100 for s in summaries.values()]
    return float(np.mean(rets)) if rets else 0.0


# ═══════════════════════════════════════════════════════
# 1. Monte Carlo Trade Shuffle
# ═══════════════════════════════════════════════════════

def monte_carlo(args):
    """Shuffle trade order 1000x, rebuild equity curves, check drawdown distribution."""
    params = _parse_params(args.params)
    data = fetch_all_data(args.symbols, args.days)
    min_score = getattr(args, "min_score", 0.0)
    summaries, all_pnl = _run_backtest(params, args.symbols, args.days,
                                       args.balance, data=data,
                                       min_score=min_score)

    if len(all_pnl) < 5:
        print("\n  Not enough trades for Monte Carlo analysis.")
        return False

    # Backtest max drawdown
    bt_dd = max((s["max_drawdown_pct"] for s in summaries.values()), default=0)

    n_sims = args.sims
    rng = np.random.default_rng(42)
    pnl_arr = np.array(all_pnl)
    max_dds = []

    for _ in range(n_sims):
        shuffled = rng.permutation(pnl_arr)
        equity = np.cumsum(shuffled) + args.balance
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak
        max_dds.append(float(np.max(dd)) * 100)

    max_dds = np.array(max_dds)
    median_dd = float(np.median(max_dds))
    pct_95 = float(np.percentile(max_dds, 95))
    worst_dd = float(np.max(max_dds))

    passed = pct_95 < bt_dd * 2

    print(f"\n  Monte Carlo ({n_sims} sims, {len(all_pnl)} trades):")
    print(f"    Backtest MaxDD:   {bt_dd:.1f}%")
    print(f"    MC Median MaxDD:  {median_dd:.1f}%")
    print(f"    MC 95th pct DD:   {pct_95:.1f}%")
    print(f"    MC Worst DD:      {worst_dd:.1f}%")
    print(f"    Verdict: {'PASS' if passed else 'FAIL'} "
          f"(95th {'<' if passed else '>='} 2x backtest)")
    return passed


# ═══════════════════════════════════════════════════════
# 2. Walk-Forward + WFE
# ═══════════════════════════════════════════════════════

def walk_forward(args):
    """Time-series cross-validation: compare in-sample vs out-of-sample returns."""
    params = _parse_params(args.params)
    data = fetch_all_data(args.symbols, args.days)
    n_folds = args.folds
    balance = args.balance
    min_score = getattr(args, "min_score", 0.0)

    # Collect per-fold IS/OOS results
    fold_results = []

    for pair in args.symbols:
        df_1h, df_4h = data[pair]
        total = len(df_1h)
        usable = total - WARMUP_CANDLES

        if usable < n_folds * 2:
            print(f"  {pair}: Not enough data for {n_folds} folds, skipping")
            continue

        fold_size = usable // (n_folds + 1)  # +1 for expanding window

        for fold_i in range(n_folds):
            # Expanding window: IS grows, OOS is fixed size
            is_end = WARMUP_CANDLES + fold_size * (fold_i + 1)
            oos_start = is_end - WARMUP_CANDLES  # overlap warmup for OOS
            oos_end = min(is_end + fold_size, total)

            if oos_end - oos_start < WARMUP_CANDLES + 10:
                continue

            # In-sample
            is_1h = df_1h.iloc[:is_end].copy()
            is_4h_end_ts = int(is_1h.iloc[-1]["close_time"])
            is_4h = df_4h[df_4h["close_time"].astype(int) <= is_4h_end_ts].copy()

            # Out-of-sample
            oos_1h = df_1h.iloc[oos_start:oos_end].copy()
            oos_4h_start_ts = int(oos_1h.iloc[0]["open_time"])
            oos_4h_end_ts = int(oos_1h.iloc[-1]["close_time"])
            oos_4h = df_4h[
                (df_4h["open_time"].astype(int) >= oos_4h_start_ts - WARMUP_CANDLES * 4 * 3600000)
                & (df_4h["close_time"].astype(int) <= oos_4h_end_ts)
            ].copy()

            if len(is_1h) < WARMUP_CANDLES + 10 or len(oos_1h) < WARMUP_CANDLES + 10:
                continue
            if len(is_4h) < 50 or len(oos_4h) < 50:
                continue

            # Run IS
            try:
                eng_is = BacktestEngine(
                    symbol=pair, df_1h=is_1h, df_4h=is_4h,
                    initial_balance=balance, param_overrides=params,
                    min_score=min_score, quiet=True,
                )
                s_is = eng_is.run()
            except ValueError:
                continue

            # Run OOS
            try:
                eng_oos = BacktestEngine(
                    symbol=pair, df_1h=oos_1h, df_4h=oos_4h,
                    initial_balance=balance, param_overrides=params,
                    min_score=min_score, quiet=True,
                )
                s_oos = eng_oos.run()
            except ValueError:
                continue

            fold_results.append({
                "fold": fold_i + 1,
                "pair": pair,
                "is_ret": s_is["return_pct"],
                "oos_ret": s_oos["return_pct"],
                "is_trades": s_is["total_trades"],
                "oos_trades": s_oos["total_trades"],
            })

    if not fold_results:
        print("\n  No valid folds produced. Try more --days or fewer --folds.")
        return False

    # Compute WFE
    is_rets = [f["is_ret"] for f in fold_results if f["is_ret"] != 0]
    oos_rets = [f["oos_ret"] for f in fold_results]
    mean_is = float(np.mean(is_rets)) if is_rets else 0
    mean_oos = float(np.mean(oos_rets))
    wfe = mean_oos / mean_is if mean_is != 0 else 0
    passed = wfe > 0.50

    print(f"\n  Walk-Forward ({n_folds} folds × {len(args.symbols)} pairs):")
    print(f"    {'Fold':>4}  {'Pair':<10} {'IS_ret%':>8} {'OOS_ret%':>9} "
          f"{'IS_trades':>9} {'OOS_trades':>10}")
    for f in fold_results:
        print(f"    {f['fold']:>4}  {f['pair']:<10} {f['is_ret']:>+7.1f}% "
              f"{f['oos_ret']:>+8.1f}% {f['is_trades']:>9} {f['oos_trades']:>10}")
    print(f"    WFE Ratio: {wfe:.2f} (target > 0.50)")
    print(f"    Verdict: {'PASS' if passed else 'FAIL'}")
    return passed


# ═══════════════════════════════════════════════════════
# 3. Parameter Heatmap
# ═══════════════════════════════════════════════════════

def heatmap(args):
    """Visualize 2-param grid search results + cliff-edge detection."""
    with open(args.results_file) as f:
        data = json.load(f)

    param_names = data["meta"]["params_swept"]
    if len(param_names) != 2:
        print(f"\n  Heatmap requires exactly 2 swept params, got {len(param_names)}: {param_names}")
        return False

    p1, p2 = param_names
    results = data["results"]

    # Build pivot data
    scores = {}
    for entry in results:
        v1 = entry["params"][p1]
        v2 = entry["params"][p2]
        scores[(v1, v2)] = entry["aggregate"]["score"]

    vals_1 = sorted(set(k[0] for k in scores))
    vals_2 = sorted(set(k[1] for k in scores))

    matrix = np.full((len(vals_2), len(vals_1)), np.nan)
    for i, v2 in enumerate(vals_2):
        for j, v1 in enumerate(vals_1):
            if (v1, v2) in scores:
                matrix[i, j] = scores[(v1, v2)]

    # Find best
    best_key = max(scores, key=scores.get)
    best_score = scores[best_key]
    best_i1 = vals_1.index(best_key[0])
    best_i2 = vals_2.index(best_key[1])

    # Cliff-edge detection: check ±1 step around best
    cliff_edge = False
    neighbors = []
    for di in [-1, 0, 1]:
        for dj in [-1, 0, 1]:
            if di == 0 and dj == 0:
                continue
            ni, nj = best_i2 + di, best_i1 + dj
            if 0 <= ni < len(vals_2) and 0 <= nj < len(vals_1):
                v = matrix[ni, nj]
                if not np.isnan(v):
                    neighbors.append(v)
                    if best_score > 0 and v < best_score * 0.7:
                        cliff_edge = True

    # Try matplotlib
    png_path = None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(max(8, len(vals_1) * 0.8), max(6, len(vals_2) * 0.6)))
        im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", origin="lower")
        ax.set_xticks(range(len(vals_1)))
        ax.set_xticklabels([f"{v:.4g}" for v in vals_1], rotation=45, ha="right")
        ax.set_yticks(range(len(vals_2)))
        ax.set_yticklabels([f"{v:.4g}" for v in vals_2])
        ax.set_xlabel(p1)
        ax.set_ylabel(p2)
        ax.set_title(f"Grid Search Heatmap: {p1} × {p2}")
        fig.colorbar(im, label="Score")

        # Mark best
        ax.plot(best_i1, best_i2, "k*", markersize=15)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        png_path = os.path.join(OUTPUT_DIR, f"heatmap_{p1}_x_{p2}.png")
        fig.tight_layout()
        fig.savefig(png_path, dpi=150)
        plt.close(fig)
    except ImportError:
        log.info("matplotlib not available, using text grid")

    # Output
    if png_path:
        print(f"\n  Heatmap saved: {png_path}")
    else:
        # Text grid fallback
        print(f"\n  Heatmap (text): {p1} (cols) × {p2} (rows)")
        header = "         " + " ".join(f"{v:>7.4g}" for v in vals_1)
        print(f"  {header}")
        for i, v2 in enumerate(vals_2):
            row = f"  {v2:>7.4g}  " + " ".join(
                f"{matrix[i, j]:>7.1f}" if not np.isnan(matrix[i, j]) else "      -"
                for j in range(len(vals_1))
            )
            print(row)

    print(f"  Best: {p1}={best_key[0]}, {p2}={best_key[1]}, score={best_score:.1f}")
    if cliff_edge:
        min_nb = min(neighbors) if neighbors else 0
        drop = (best_score - min_nb) / best_score * 100 if best_score > 0 else 0
        print(f"  Cliff-edge: WARNING (neighbor drops up to {drop:.0f}%)")
    else:
        pct = ((best_score - min(neighbors)) / best_score * 100) if neighbors and best_score > 0 else 0
        print(f"  Cliff-edge: NONE (surrounding cells within {pct:.0f}%)")

    return not cliff_edge


# ═══════════════════════════════════════════════════════
# 4. Noise Injection (optional)
# ═══════════════════════════════════════════════════════

def noise_test(args):
    """Add random noise to OHLC prices, check strategy robustness."""
    params = _parse_params(args.params)
    data = fetch_all_data(args.symbols, args.days)
    balance = args.balance
    min_score = getattr(args, "min_score", 0.0)

    # Baseline
    base_summaries, _ = _run_backtest(params, args.symbols, args.days, balance, data=data,
                                      min_score=min_score)
    base_ret = _total_return_pct(base_summaries, balance)

    n_sims = args.sims
    noise_pct = args.noise_pct
    rng = np.random.default_rng(42)
    noisy_rets = []

    for sim_i in range(n_sims):
        noisy_data = {}
        for pair, (df_1h, df_4h) in data.items():
            d1 = df_1h.copy()
            d4 = df_4h.copy()
            for col in ["open", "high", "low", "close"]:
                d1[col] = d1[col].astype(float) * rng.uniform(
                    1 - noise_pct, 1 + noise_pct, size=len(d1))
                d4[col] = d4[col].astype(float) * rng.uniform(
                    1 - noise_pct, 1 + noise_pct, size=len(d4))
            noisy_data[pair] = (d1, d4)

        try:
            s, _ = _run_backtest(params, args.symbols, args.days, balance, data=noisy_data,
                                min_score=min_score)
            noisy_rets.append(_total_return_pct(s, balance))
        except Exception as e:
            log.warning("Noise sim %d failed: %s", sim_i, e)

        if (sim_i + 1) % 100 == 0:
            print(f"    {sim_i + 1}/{n_sims} sims done...")

    if not noisy_rets:
        print("\n  All noise simulations failed.")
        return False

    noisy_arr = np.array(noisy_rets)
    median_ret = float(np.median(noisy_arr))
    pct_10 = float(np.percentile(noisy_arr, 10))

    if abs(base_ret) < 0.01:
        # Near-zero base return — use absolute difference as degradation proxy
        degradation = abs(median_ret) * 100
    else:
        degradation = abs(base_ret - median_ret) / abs(base_ret) * 100
    passed = degradation < 30

    print(f"\n  Noise Test ({n_sims} sims, ±{noise_pct*100:.1f}%):")
    print(f"    Base return:      {base_ret:+.1f}%")
    print(f"    Noisy median:     {median_ret:+.1f}%")
    print(f"    Noisy 10th pct:   {pct_10:+.1f}%")
    print(f"    Degradation:      {degradation:.0f}% (median vs base)")
    print(f"    Verdict: {'PASS' if passed else 'FAIL'} "
          f"(degradation {'<' if passed else '>='} 30%)")
    return passed


# ═══════════════════════════════════════════════════════
# 5. Entry Delay Test (optional)
# ═══════════════════════════════════════════════════════

def delay_test(args):
    """Test performance degradation with increasing entry delays."""
    params = _parse_params(args.params)
    data = fetch_all_data(args.symbols, args.days)
    balance = args.balance
    min_score = getattr(args, "min_score", 0.0)

    delays = list(range(args.max_delay + 1))
    results = []

    for d in delays:
        actual_delay = d + 1  # delay=0 → signal_delay=1 (baseline), delay=1 → 2, ...
        summaries, _ = _run_backtest(
            params, args.symbols, args.days, balance,
            data=data, signal_delay=actual_delay,
            min_score=min_score,
        )
        ret = _total_return_pct(summaries, balance)
        trades = sum(s["total_trades"] for s in summaries.values())
        wins = sum(s["winners"] for s in summaries.values())
        wr = wins / trades * 100 if trades > 0 else 0

        # Profit factor
        gp = sum(max(0, s["final_balance"] - balance) for s in summaries.values())
        gl = sum(max(0, balance - s["final_balance"]) for s in summaries.values())
        pf = gp / gl if gl > 0 else float("inf")

        results.append({"delay": d, "ret": ret, "trades": trades, "wr": wr, "pf": pf})

    base_ret = results[0]["ret"] if results else 0

    print(f"\n  Entry Delay Test:")
    print(f"    {'Delay':>5}  {'Return%':>8}  {'Trades':>6}  {'WR%':>5}  {'PF':>6}  {'Drop':>6}")
    for r in results:
        drop = ((base_ret - r["ret"]) / abs(base_ret) * 100) if base_ret != 0 else 0
        drop_str = f"({drop:+.0f}%)" if r["delay"] > 0 else ""
        pf_str = f"{r['pf']:.1f}" if r["pf"] != float("inf") else "inf"
        print(f"    {r['delay']:>5}  {r['ret']:>+7.1f}%  {r['trades']:>6}  "
              f"{r['wr']:>4.0f}%  {pf_str:>6}  {drop_str:>6}")

    # Pass: delay=1 (index 1 if exists, else baseline) drop < 30%
    if len(results) >= 2:
        delay1_ret = results[1]["ret"]
        delay1_drop = abs(base_ret - delay1_ret) / abs(base_ret) * 100 if base_ret != 0 else 0
        passed = delay1_drop < 30
        print(f"    Verdict: {'PASS' if passed else 'FAIL'} "
              f"(delay 1 drop {delay1_drop:.0f}% {'<' if passed else '>='} 30%)")
        return passed
    else:
        print("    Verdict: SKIP (not enough delay levels)")
        return True


# ═══════════════════════════════════════════════════════
# 6. Deflated Sharpe Ratio (optional)
# ═══════════════════════════════════════════════════════

def dsr_test(args):
    """Bailey & Lopez de Prado (2014) deflated Sharpe ratio."""
    with open(args.results_file) as f:
        data = json.load(f)

    results = data["results"]
    n_combos = len(results)

    if n_combos < 2:
        print("\n  DSR requires ≥2 combos with varying returns.")
        return False

    # Best combo stats — reconstruct Sharpe from return/drawdown
    best = results[0]
    ret_pct = best["aggregate"]["return_pct"]

    # Estimate Sharpe: annualized return / annualized vol
    # Use return std across all combos as vol proxy
    all_rets = [r["aggregate"]["return_pct"] for r in results]
    ret_std = float(np.std(all_rets))

    if ret_std <= 0:
        print("\n  DSR requires varying returns across combos (std=0).")
        return False

    days = data["meta"].get("days", 180)
    ann_factor = np.sqrt(365 / max(days, 1))
    sharpe = (ret_pct / ret_std) * ann_factor if ret_std > 0 else 0

    # Expected max Sharpe under null (Euler-Mascheroni approximation)
    # E[max(SR)] ≈ (1 - γ) * Z_{1-1/N} + γ * Z_{1-1/(N*e)}
    # Simplified: E[max(SR)] ≈ sqrt(2 * ln(N)) - (γ + ln(π/2)) / (2 * sqrt(2 * ln(N)))
    gamma = 0.5772  # Euler-Mascheroni constant
    if n_combos > 1:
        log_n = np.log(n_combos)
        e_max_sr = np.sqrt(2 * log_n) - (gamma + np.log(np.pi / 2)) / (2 * np.sqrt(2 * log_n))
    else:
        e_max_sr = 0

    # DSR probability: P(SR > E[max(SR)])
    # Using normal CDF
    try:
        from scipy.stats import norm
        dsr_prob = float(norm.cdf(sharpe - e_max_sr))
    except ImportError:
        # Approximation: simple sigmoid
        x = sharpe - e_max_sr
        dsr_prob = 1 / (1 + np.exp(-1.7 * x))
        dsr_prob = float(dsr_prob)
        log.info("scipy not available, using sigmoid approximation for DSR")

    passed = dsr_prob > 0.05

    print(f"\n  Deflated Sharpe Ratio:")
    print(f"    Observed Sharpe:    {sharpe:.2f}")
    print(f"    Combos tested:      {n_combos}")
    print(f"    E[max(SR)] null:    {e_max_sr:.2f}")
    print(f"    DSR probability:    {dsr_prob:.2f}")
    verdict_note = ""
    if passed and dsr_prob < 0.20:
        verdict_note = " — marginal, try fewer combos"
    print(f"    Verdict: {'PASS' if passed else 'FAIL'} "
          f"(DSR {'>' if passed else '<='} 0.05{verdict_note})")
    return passed


# ═══════════════════════════════════════════════════════
# "all" — run all must-use tools
# ═══════════════════════════════════════════════════════

def run_all(args):
    """Run all 3 must-use validation tools."""
    verdicts = {}

    print("\n" + "=" * 60)
    print("  VALIDATION SUITE — Must-Use Tools")
    print("=" * 60)

    # Monte Carlo
    print("\n── 1/3 Monte Carlo ──")
    verdicts["monte-carlo"] = monte_carlo(args)

    # Walk-Forward
    print("\n── 2/3 Walk-Forward ──")
    verdicts["walk-forward"] = walk_forward(args)

    # Heatmap (only if results file provided)
    if args.results_file:
        print("\n── 3/3 Heatmap ──")
        verdicts["heatmap"] = heatmap(args)
    else:
        print("\n── 3/3 Heatmap: SKIPPED (no --results-file) ──")

    # Summary
    print("\n" + "=" * 60)
    print("  VALIDATION SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, result in verdicts.items():
        status = "PASS" if result else "FAIL"
        if not result:
            all_pass = False
        print(f"    {name:<15} {status}")
    print(f"\n  Overall: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    return all_pass


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def _add_common_args(p):
    """Add shared args to a subparser."""
    p.add_argument("--params", nargs="+", help="Parameter overrides (key=value)")
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"],
                   help="Trading pairs (default: BTCUSDT ETHUSDT)")
    p.add_argument("--days", type=int, default=60, help="Backtest days (default: 60)")
    p.add_argument("--balance", type=float, default=10000, help="Initial balance (default: 10000)")
    p.add_argument("--min-score", type=float, default=0.0,
                   help="Minimum signal score to accept (default: 0.0 = no filter)")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest Validation Tools — Anti-Overfitting Checks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Must-use:
  monte-carlo   Shuffle trade order, check drawdown distribution
  walk-forward  Time-series CV with Walk-Forward Efficiency
  heatmap       2D parameter heatmap + cliff-edge detection

Optional:
  noise         Random price noise injection
  delay         Entry delay degradation test
  dsr           Deflated Sharpe Ratio (multiple testing correction)

  all           Run all 3 must-use tools
        """,
    )
    subs = parser.add_subparsers(dest="method", required=True)

    # Monte Carlo
    p_mc = subs.add_parser("monte-carlo", help="Trade shuffle drawdown analysis")
    _add_common_args(p_mc)
    p_mc.add_argument("--sims", type=int, default=1000, help="Number of simulations (default: 1000)")
    p_mc.set_defaults(func=monte_carlo)

    # Walk-Forward
    p_wf = subs.add_parser("walk-forward", help="Walk-forward cross-validation")
    _add_common_args(p_wf)
    p_wf.add_argument("--folds", type=int, default=5, help="Number of folds (default: 5)")
    p_wf.set_defaults(func=walk_forward)

    # Heatmap
    p_hm = subs.add_parser("heatmap", help="2D parameter heatmap")
    p_hm.add_argument("--results-file", required=True, help="Grid search JSON file")
    p_hm.set_defaults(func=heatmap)

    # Noise
    p_noise = subs.add_parser("noise", help="Price noise injection test")
    _add_common_args(p_noise)
    p_noise.add_argument("--sims", type=int, default=500, help="Number of simulations (default: 500)")
    p_noise.add_argument("--noise-pct", type=float, default=0.002,
                         help="Noise range as fraction (default: 0.002 = ±0.2%%)")
    p_noise.set_defaults(func=noise_test)

    # Delay
    p_delay = subs.add_parser("delay", help="Entry delay degradation test")
    _add_common_args(p_delay)
    p_delay.add_argument("--max-delay", type=int, default=3,
                         help="Max candles of delay to test (default: 3)")
    p_delay.set_defaults(func=delay_test)

    # DSR
    p_dsr = subs.add_parser("dsr", help="Deflated Sharpe Ratio")
    p_dsr.add_argument("--results-file", required=True, help="Grid search JSON file")
    p_dsr.set_defaults(func=dsr_test)

    # All
    p_all = subs.add_parser("all", help="Run all 3 must-use tools")
    _add_common_args(p_all)
    p_all.add_argument("--results-file", default=None, help="Grid search JSON (for heatmap)")
    p_all.add_argument("--sims", type=int, default=1000, help="Monte Carlo sims (default: 1000)")
    p_all.add_argument("--folds", type=int, default=5, help="Walk-forward folds (default: 5)")
    p_all.set_defaults(func=run_all)

    args = parser.parse_args()

    # Normalize symbols
    args.symbols = [
        s.upper() if "USDT" in s.upper() else s.upper() + "USDT"
        for s in getattr(args, "symbols", [])
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    result = args.func(args)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
