#!/usr/bin/env python3
"""
grid_search.py — Grid search parameter optimizer for backtest.

設計決定：
  - 指定要 sweep 嘅參數，自動跑所有組合（笛卡爾積）
  - ProcessPoolExecutor（唔係 Thread）— 每個 process 獨立 module globals
  - 評分公式 anti-overfitting：加 consistency + coverage，減 drawdown^1.5
  - 自動跑 production baseline 作對比
  - rsi_long >= rsi_short 組合自動過濾

用法:
  python3 backtest/grid_search.py --list-params
  python3 backtest/grid_search.py --params bb_touch_tol --symbols BTCUSDT --days 14 --top 3
  python3 backtest/grid_search.py --params bb_touch_tol adx_range_max --days 60 --top 5
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import numpy as np

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES

log = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(AXC_HOME, "backtest", "data")
DEFAULT_PAIRS = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT",
    "DOGEUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT",
]


# ═══════════════════════════════════════════════════════
# PARAM_REGISTRY — 所有可 sweep 參數
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class ParamSpec:
    """One sweepable parameter's definition."""
    name: str
    low: float
    high: float
    step: float
    category: str       # indicator / position / trend
    description: str


PARAM_REGISTRY: dict[str, ParamSpec] = {
    # ─── indicator（經 engine.param_overrides + TIMEFRAME_PARAMS patch）───
    "bb_touch_tol":     ParamSpec("bb_touch_tol",     0.003, 0.010, 0.001, "indicator", "BB touch tolerance"),
    "adx_range_max":    ParamSpec("adx_range_max",    15,    25,    1,     "indicator", "ADX max for range mode"),
    "bb_width_squeeze": ParamSpec("bb_width_squeeze", 0.008, 0.025, 0.002, "indicator", "BB width squeeze threshold"),
    "rsi_long":         ParamSpec("rsi_long",         25,    40,    5,     "indicator", "RSI lower bound (LONG entry)"),
    "rsi_short":        ParamSpec("rsi_short",        55,    75,    5,     "indicator", "RSI upper bound (SHORT entry)"),
    # ─── position（經 strategy_overrides + position_overrides 注入 BT 策略）───
    "sl_atr_mult_range": ParamSpec("sl_atr_mult_range", 0.8,  1.5,  0.1, "position", "Range SL = N x ATR"),
    "sl_atr_mult_trend": ParamSpec("sl_atr_mult_trend", 1.0,  2.0,  0.1, "position", "Trend SL = N x ATR"),
    "min_rr":            ParamSpec("min_rr",            1.5,  3.0,  0.5, "position", "Minimum reward:risk"),
    # ─── trend（monkey-patch trend_strategy module global）───
    "pullback_tolerance": ParamSpec("pullback_tolerance", 0.010, 0.025, 0.005, "trend", "Pullback vs MA50 tolerance"),
}


def _param_values(spec: ParamSpec) -> list[float]:
    """Generate discrete sweep values for one parameter."""
    vals = []
    v = spec.low
    while v <= spec.high + spec.step * 0.01:
        vals.append(round(v, 6))
        v += spec.step
    return vals


def generate_grid(param_names: list[str]) -> list[dict]:
    """Cartesian product of all selected parameters. Filters rsi_long >= rsi_short."""
    specs = [PARAM_REGISTRY[n] for n in param_names]
    combos = []
    for values in itertools.product(*(_param_values(s) for s in specs)):
        combo = dict(zip(param_names, values))
        if "rsi_long" in combo and "rsi_short" in combo:
            if combo["rsi_long"] >= combo["rsi_short"]:
                continue
        combos.append(combo)
    return combos


# ═══════════════════════════════════════════════════════
# Data I/O — 主進程 fetch，worker 讀 CSV
# ═══════════════════════════════════════════════════════

def fetch_all_data(pairs: list[str], days: int) -> dict:
    """Fetch 1H + 4H data for all pairs (sequential, API rate limits)."""
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    data = {}
    for pair in pairs:
        s1h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES)).timestamp() * 1000)
        s4h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES * 4)).timestamp() * 1000)
        data[pair] = (
            fetch_klines_range(pair, "1h", s1h, end_ms),
            fetch_klines_range(pair, "4h", s4h, end_ms),
        )
    return data


def save_data_for_workers(data: dict) -> tuple[str, dict[str, tuple[str, str]]]:
    """Save DataFrames to temp CSVs, return (tmp_dir, {pair: (1h_path, 4h_path)})."""
    tmp_dir = tempfile.mkdtemp(prefix="grid_search_")
    paths = {}
    for pair, (df_1h, df_4h) in data.items():
        p1 = os.path.join(tmp_dir, f"{pair}_1h.csv")
        p4 = os.path.join(tmp_dir, f"{pair}_4h.csv")
        df_1h.to_csv(p1, index=False)
        df_4h.to_csv(p4, index=False)
        paths[pair] = (p1, p4)
    return tmp_dir, paths


# ═══════════════════════════════════════════════════════
# Worker — 獨立進程，安全 monkey-patch
# ═══════════════════════════════════════════════════════

def _worker_run(
    combo: dict,
    combo_idx: int,
    pairs: list[str],
    data_paths: dict[str, tuple[str, str]],
    initial_balance: float,
) -> dict:
    """Run one combo across all pairs. Uses strategy_overrides for position params."""
    import pandas as pd
    from indicator_calc import TIMEFRAME_PARAMS
    from backtest.strategies.bt_range_strategy import BTRangeStrategy
    from backtest.strategies.bt_trend_strategy import BTTrendStrategy

    # ─── Monkey-patch trend entry params (live strategy module globals) ───
    if "pullback_tolerance" in combo:
        import trader_cycle.strategies.trend_strategy as _ts
        _ts.PULLBACK_TOLERANCE = combo["pullback_tolerance"]

    # ─── Patch TIMEFRAME_PARAMS for rsi_long / rsi_short ───
    for key in ("rsi_long", "rsi_short"):
        if key in combo:
            TIMEFRAME_PARAMS["1h"][key] = combo[key]

    # ─── Engine indicator overrides ───
    engine_overrides = {
        k: combo[k] for k in ("bb_touch_tol", "adx_range_max", "bb_width_squeeze",
                               "rsi_long", "rsi_short")
        if k in combo
    }

    # ─── Build position_overrides for BT strategies ───
    pos_range = {}
    pos_trend = {}
    if "sl_atr_mult_range" in combo:
        pos_range["sl_atr_mult"] = combo["sl_atr_mult_range"]
    if "sl_atr_mult_trend" in combo:
        pos_trend["sl_atr_mult"] = combo["sl_atr_mult_trend"]
    if "min_rr" in combo:
        pos_range["min_rr"] = combo["min_rr"]
        pos_trend["min_rr"] = combo["min_rr"]

    strat_overrides = {}
    if pos_range:
        strat_overrides["range"] = BTRangeStrategy(position_overrides=pos_range)
    if pos_trend:
        strat_overrides["trend"] = BTTrendStrategy(position_overrides=pos_trend)

    # ─── Run per pair ───
    results = {}
    for pair in pairs:
        csv_1h, csv_4h = data_paths[pair]
        df_1h = pd.read_csv(csv_1h)
        df_4h = pd.read_csv(csv_4h)
        for col in ("open", "high", "low", "close", "volume"):
            df_1h[col] = df_1h[col].astype(float)
            df_4h[col] = df_4h[col].astype(float)
        df_1h["timestamp"] = pd.to_datetime(df_1h["open_time"], unit="ms")
        df_4h["timestamp"] = pd.to_datetime(df_4h["open_time"], unit="ms")

        try:
            engine = BacktestEngine(
                symbol=pair, df_1h=df_1h, df_4h=df_4h,
                initial_balance=initial_balance,
                param_overrides=engine_overrides,
                strategy_overrides=strat_overrides if strat_overrides else None,
                quiet=True,
            )
            r = engine.run()
            results[pair] = {
                "total_trades": r["total_trades"], "winners": r["winners"],
                "losers": r["losers"], "return_pct": r["return_pct"],
                "win_rate": r["win_rate"],
                "cluster_adj_wr": r.get("cluster_adj_wr", 0.0),
                "profit_factor": r["profit_factor"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "final_balance": r["final_balance"],
            }
        except Exception as e:
            results[pair] = {
                "total_trades": 0, "winners": 0, "losers": 0,
                "return_pct": 0.0, "win_rate": 0.0, "cluster_adj_wr": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0,
                "final_balance": initial_balance, "error": str(e),
            }

    return {"combo_idx": combo_idx, "params": combo, "results": results}


# ═══════════════════════════════════════════════════════
# Scoring — Anti-Overfitting 複合評分
# ═══════════════════════════════════════════════════════

def score_combo(results: dict[str, dict], balance: float) -> float:
    """
    score = return% x 0.25 + cluster_adj_wr x 0.25 + profit_factor x 0.15
          + consistency x 0.15 + coverage x 0.10 - drawdown_penalty x 0.10
    Minimum 10 trades across all pairs.
    """
    total_trades = sum(r["total_trades"] for r in results.values())
    if total_trades < 10:
        return float("-inf")

    rets = [(r["final_balance"] - balance) / balance * 100 for r in results.values()]
    avg_ret = float(np.mean(rets))

    active_wrs = [r["cluster_adj_wr"] for r in results.values() if r["total_trades"] > 0]
    avg_wr = float(np.mean(active_wrs)) if active_wrs else 0.0

    gp = sum(max(0, r["final_balance"] - balance) for r in results.values())
    gl = sum(max(0, balance - r["final_balance"]) for r in results.values())
    pf = min(gp / gl if gl > 0 else 5.0, 5.0)

    consistency = max(0, 100 - float(np.std(rets)) * 2) if len(rets) > 1 else 50.0
    coverage = sum(1 for r in results.values() if r["total_trades"] > 0) / len(results) * 100
    max_dd = max((r["max_drawdown_pct"] for r in results.values()), default=0)
    dd_penalty = max_dd ** 1.5

    return round(
        avg_ret * 0.25 + avg_wr * 0.25 + pf * 10 * 0.15
        + consistency * 0.15 + coverage * 0.10 - dd_penalty * 0.10,
        4,
    )


# ═══════════════════════════════════════════════════════
# Aggregate helper
# ═══════════════════════════════════════════════════════

def _aggregate(results: dict[str, dict], balance: float) -> dict:
    """Build aggregate metrics dict for one combo."""
    rets = [(r["final_balance"] - balance) / balance * 100 for r in results.values()]
    wrs = [r["cluster_adj_wr"] for r in results.values() if r["total_trades"] > 0]
    gp = sum(max(0, r["final_balance"] - balance) for r in results.values())
    gl = sum(max(0, balance - r["final_balance"]) for r in results.values())
    pf = min(gp / gl if gl > 0 else float("inf"), 5.0)
    return {
        "score": score_combo(results, balance),
        "trades": sum(r["total_trades"] for r in results.values()),
        "return_pct": round(float(np.mean(rets)), 2) if rets else 0,
        "adj_wr": round(float(np.mean(wrs)), 1) if wrs else 0,
        "pf": round(pf, 2),
        "max_dd": round(max((r["max_drawdown_pct"] for r in results.values()), default=0), 1),
        "coverage": round(sum(1 for r in results.values() if r["total_trades"] > 0)
                          / len(results) * 100, 0),
    }


# ═══════════════════════════════════════════════════════
# Output
# ═══════════════════════════════════════════════════════

def _fmt_pf(pf) -> str:
    if isinstance(pf, (int, float)) and pf not in (float("inf"), float("-inf")):
        return f"{pf:.2f}"
    return str(pf)


def print_results(ranked: list[dict], param_names: list[str], pairs: list[str],
                  days: int, top_n: int, baseline: dict | None = None):
    """Terminal table: top N + baseline + best per-pair breakdown."""
    ph = "  ".join(f"{n:>12}" for n in param_names)
    sep_len = 14 + len(param_names) * 14 + 58

    print(f"\n{'=' * sep_len}")
    print(f"  GRID SEARCH — {len(ranked)} combos x {len(pairs)} pairs x {days}d")
    print(f"  Swept: {', '.join(param_names)}")
    print(f"{'=' * sep_len}")
    print(f"\n  {'#':>3}  {ph}  {'Score':>8} {'Trades':>6} {'Ret%':>8} "
          f"{'AdjWR':>6} {'PF':>6} {'MaxDD':>6} {'Cov':>4}")
    print("  " + "-" * (sep_len - 4))

    if baseline:
        bl = baseline
        bp = "  ".join(f"{'[prod]':>12}" for _ in param_names)
        print(f"  {'BL':>3}  {bp}  {bl['score']:>8.1f} {bl['trades']:>6} "
              f"{bl['return_pct']:>+7.1f}% {bl['adj_wr']:>5.1f} {_fmt_pf(bl['pf']):>6} "
              f"{bl['max_dd']:>5.1f}% {bl['coverage']:>3.0f}%")
        print("  " + "-" * (sep_len - 4))

    for i, entry in enumerate(ranked[:top_n]):
        p = entry["params"]
        a = entry["aggregate"]
        pv = "  ".join(f"{p.get(n, '-'):>12}" for n in param_names)
        print(f"  {i+1:>3}  {pv}  {a['score']:>8.1f} {a['trades']:>6} "
              f"{a['return_pct']:>+7.1f}% {a['adj_wr']:>5.1f} {_fmt_pf(a['pf']):>6} "
              f"{a['max_dd']:>5.1f}% {a['coverage']:>3.0f}%")

    if ranked:
        best = ranked[0]
        print(f"\n  #1 per-pair breakdown:")
        print(f"  {'Pair':<10} {'Trades':>6} {'Ret%':>8} {'WR':>5} {'AdjWR':>6} {'PF':>6} {'MaxDD':>6}")
        print("  " + "-" * 55)
        for pair in pairs:
            r = best["per_symbol"].get(pair)
            if not r:
                continue
            print(f"  {pair:<10} {r['total_trades']:>6} {r['return_pct']:>+7.1f}% "
                  f"{r['win_rate']:>4.0f} {r['cluster_adj_wr']:>5.1f} "
                  f"{_fmt_pf(r['profit_factor']):>6} {r['max_drawdown_pct']:>5.1f}%")


def save_json(ranked: list[dict], param_names: list[str], pairs: list[str],
              days: int, baseline: dict | None, path: str):
    """Full JSON output."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _c(o):
        if isinstance(o, float) and (o != o or o == float("inf") or o == float("-inf")):
            return str(o)
        if isinstance(o, (np.floating, np.integer)):
            return float(o) if isinstance(o, np.floating) else int(o)
        return o

    out = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "params_swept": param_names, "pairs": pairs,
            "days": days, "total_combos": len(ranked),
        },
        "baseline": baseline,
        "results": [
            {"rank": i + 1, "params": e["params"],
             "aggregate": {k: _c(v) for k, v in e["aggregate"].items()},
             "per_symbol": {p: {k: _c(v) for k, v in pr.items()} for p, pr in e["per_symbol"].items()}}
            for i, e in enumerate(ranked)
        ],
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  JSON saved: {path}")


def save_csv_output(ranked: list[dict], param_names: list[str], path: str):
    """Flat CSV for spreadsheet."""
    fields = ["rank"] + param_names + ["score", "trades", "return_pct", "adj_wr", "pf", "max_dd", "coverage"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, e in enumerate(ranked):
            row = {"rank": i + 1, **e["params"], **e["aggregate"]}
            w.writerow(row)
    print(f"  CSV saved: {path}")


# ═══════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════

def run_grid_search(
    param_names: list[str], pairs: list[str], days: int = 180,
    initial_balance: float = 10000, top_n: int = 10, workers: int = 4,
    output_path: str | None = None, do_csv: bool = False,
) -> list[dict]:
    """Run full grid search: fetch data -> generate grid -> parallel backtest -> rank."""
    grid = generate_grid(param_names)
    if not grid:
        print("  No valid parameter combinations!")
        return []

    # ─── Fetch data (main process only) ───
    print(f"\n  Fetching {days}d data for {', '.join(pairs)}...")
    data = fetch_all_data(pairs, days)
    tmp_dir, data_paths = save_data_for_workers(data)

    try:
        # ─── Production baseline (main process) ───
        print("  Running production baseline...")
        bl_results = {}
        for pair in pairs:
            df_1h, df_4h = data[pair]
            try:
                eng = BacktestEngine(symbol=pair, df_1h=df_1h.copy(), df_4h=df_4h.copy(),
                                     initial_balance=initial_balance, quiet=True)
                r = eng.run()
                bl_results[pair] = {
                    "total_trades": r["total_trades"], "winners": r["winners"],
                    "losers": r["losers"], "return_pct": r["return_pct"],
                    "win_rate": r["win_rate"], "cluster_adj_wr": r.get("cluster_adj_wr", 0.0),
                    "profit_factor": r["profit_factor"],
                    "max_drawdown_pct": r["max_drawdown_pct"],
                    "final_balance": r["final_balance"],
                }
            except Exception as e:
                log.warning("Baseline error %s: %s", pair, e)

        baseline = {**_aggregate(bl_results, initial_balance), "per_symbol": bl_results}

        # ─── Parallel grid execution ───
        print(f"  Running {len(grid)} combos with {workers} workers...")
        t0 = time.time()
        all_results: list[dict] = []
        done = 0

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(_worker_run, combo, idx, pairs, data_paths, initial_balance): idx
                for idx, combo in enumerate(grid)
            }
            for fut in as_completed(futs):
                done += 1
                if done % max(1, len(grid) // 10) == 0 or done == len(grid):
                    el = time.time() - t0
                    rate = done / el if el > 0 else 0
                    eta = (len(grid) - done) / rate if rate > 0 else 0
                    print(f"    {done}/{len(grid)} ({rate:.1f}/s, ETA {eta:.0f}s)")
                try:
                    all_results.append(fut.result())
                except Exception as e:
                    log.warning("Combo %d failed: %s", futs[fut], e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ─── Score & rank ───
    ranked = []
    for res in all_results:
        agg = _aggregate(res["results"], initial_balance)
        ranked.append({"params": res["params"], "aggregate": agg, "per_symbol": res["results"]})
    ranked.sort(key=lambda x: x["aggregate"]["score"], reverse=True)

    elapsed = time.time() - t0
    print(f"\n  Completed in {elapsed:.1f}s ({len(grid)} combos, {workers} workers)")

    # ─── Output ───
    print_results(ranked, param_names, pairs, days, top_n, baseline)

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR, f"grid_search_{ts}.json")
    save_json(ranked, param_names, pairs, days, baseline, output_path)

    if do_csv:
        save_csv_output(ranked, param_names, output_path.replace(".json", ".csv"))

    return ranked


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Grid Search Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 backtest/grid_search.py --list-params
  python3 backtest/grid_search.py --params bb_touch_tol --symbols BTCUSDT --days 14 --top 3
  python3 backtest/grid_search.py --params bb_touch_tol adx_range_max --symbols BTCUSDT ETHUSDT SOLUSDT --days 60 --top 5
        """,
    )
    parser.add_argument("--params", nargs="+", help="Parameters to sweep (from PARAM_REGISTRY)")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help=f"Trading pairs (default: {len(DEFAULT_PAIRS)} pairs)")
    parser.add_argument("--days", type=int, default=180, help="Backtest days (default: 180)")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance (default: 10000)")
    parser.add_argument("--top", type=int, default=10, help="Show top N results (default: 10)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--output", type=str, default=None, help="JSON output path (auto if omitted)")
    parser.add_argument("--csv", action="store_true", help="Also output CSV")
    parser.add_argument("--force", action="store_true", help="Allow >3 params (combinatorial explosion)")
    parser.add_argument("--list-params", action="store_true", help="List sweepable parameters and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S",
    )

    if args.list_params:
        print(f"\n  {'Name':<20} {'Range':>15} {'Step':>6} {'Cat':>10}  Description")
        print("  " + "-" * 80)
        for name, s in PARAM_REGISTRY.items():
            n = len(_param_values(s))
            print(f"  {name:<20} {s.low:>6g} - {s.high:<6g} {s.step:>6g} "
                  f"{s.category:>10}  {s.description} ({n} values)")
        return

    if not args.params:
        parser.error("--params required (or use --list-params)")
    for p in args.params:
        if p not in PARAM_REGISTRY:
            parser.error(f"Unknown param '{p}'. Use --list-params to see options.")

    grid = generate_grid(args.params)
    if len(args.params) > 3 and not args.force:
        parser.error(f"{len(args.params)} params = {len(grid)} combos. Use --force to allow.")

    pairs = args.symbols or DEFAULT_PAIRS
    pairs = [p.upper() if "USDT" in p.upper() else p.upper() + "USDT" for p in pairs]

    print(f"\n  Grid Search Optimizer")
    print(f"  Params:  {', '.join(args.params)}")
    print(f"  Combos:  {len(grid)}")
    print(f"  Pairs:   {', '.join(pairs)}")
    print(f"  Days:    {args.days}")
    print(f"  Workers: {args.workers}")

    run_grid_search(
        param_names=args.params, pairs=pairs, days=args.days,
        initial_balance=args.balance, top_n=args.top, workers=args.workers,
        output_path=args.output, do_csv=args.csv,
    )


if __name__ == "__main__":
    main()
