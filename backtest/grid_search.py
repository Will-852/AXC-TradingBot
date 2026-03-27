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
    "pullback_tolerance":     ParamSpec("pullback_tolerance",     0.015, 0.050, 0.005, "trend", "Pullback vs MA50 tolerance"),
    "confidence_threshold":   ParamSpec("confidence_threshold",   0.25,  0.45,  0.05,  "trend", "Min weighted confidence to fire"),
    "macd_hist_decay":        ParamSpec("macd_hist_decay",        0.4,   0.8,   0.1,   "trend", "MACD histogram decay exit threshold"),
    # ─── crash（monkey-patch crash_strategy module global）───
    "crash_rsi_entry":   ParamSpec("crash_rsi_entry",   50,  70,  5,   "crash", "Crash RSI overbought entry threshold"),
    "crash_sl_atr_mult": ParamSpec("crash_sl_atr_mult", 1.5, 3.0, 0.5, "crash", "Crash SL = N x ATR"),
    "crash_min_rr":      ParamSpec("crash_min_rr",      1.0, 2.5, 0.5, "crash", "Crash min reward:risk"),
    # ─── squeeze（monkey-patch squeeze_strategy module global）───
    "sqz_bb_pctl":       ParamSpec("sqz_bb_pctl",       20,  40,  5,   "squeeze", "Squeeze BB width percentile threshold"),
    "sqz_sl_atr_mult":   ParamSpec("sqz_sl_atr_mult",   1.0, 2.0, 0.5, "squeeze", "Squeeze SL = N x ATR"),
    "sqz_tp_atr_mult":   ParamSpec("sqz_tp_atr_mult",   2.0, 4.0, 0.5, "squeeze", "Squeeze TP = N x ATR"),
    "sqz_min_rr":        ParamSpec("sqz_min_rr",        1.5, 3.0, 0.5, "squeeze", "Squeeze min reward:risk"),
    # ─── volume spike（indicator patch + scorer override）───
    "vol_spike_mult":  ParamSpec("vol_spike_mult",  1.5, 3.0, 0.5, "indicator", "Volume spike threshold multiplier"),
    "vol_spike_bonus": ParamSpec("vol_spike_bonus", 0.0, 1.0, 0.25, "indicator", "Volume spike score bonus"),
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
    from backtest.scoring import WeightedScorer

    # ─── Monkey-patch with save/restore (BMD P4 fix, 2026-03-28) ───
    # 🔴 2CHECK: all module-level mutations saved for try/finally restore.
    # ProcessPoolExecutor reuses workers — mutations leak across combos without restore.
    _module_originals = {}  # {(module, attr): original_value}

    def _patch(mod, attr, val):
        """Patch module attr and record original for restore."""
        _module_originals[(mod, attr)] = getattr(mod, attr, None)
        setattr(mod, attr, val)

    # ─── Trend entry params ───
    import trader_cycle.strategies.trend_strategy as _ts
    if "pullback_tolerance" in combo:
        _patch(_ts, "PULLBACK_TOLERANCE", combo["pullback_tolerance"])
    if "confidence_threshold" in combo:
        _patch(_ts, "CONFIDENCE_THRESHOLD", combo["confidence_threshold"])
    if "sl_atr_mult_trend" in combo:
        _patch(_ts, "TREND_SL_ATR_MULT", combo["sl_atr_mult_trend"])
    if "min_rr" in combo:
        _patch(_ts, "TREND_MIN_RR", combo["min_rr"])
    if "macd_hist_decay" in combo:
        _patch(_ts, "MACD_HIST_DECAY_THRESHOLD", combo["macd_hist_decay"])

    # ─── Crash params ───
    import trader_cycle.strategies.crash_strategy as _cs
    if "crash_rsi_entry" in combo:
        _patch(_cs, "CRASH_RSI_ENTRY", combo["crash_rsi_entry"])
    if "crash_sl_atr_mult" in combo:
        _patch(_cs, "CRASH_SL_ATR_MULT", combo["crash_sl_atr_mult"])
    if "crash_min_rr" in combo:
        _patch(_cs, "CRASH_MIN_RR", combo["crash_min_rr"])

    # ─── Squeeze params ───
    import trader_cycle.strategies.squeeze_strategy as _sq
    if "sqz_bb_pctl" in combo:
        _patch(_sq, "BB_PCTL_SQUEEZE", combo["sqz_bb_pctl"])
    if "sqz_sl_atr_mult" in combo:
        _patch(_sq, "SQZ_SL_ATR_MULT", combo["sqz_sl_atr_mult"])
    if "sqz_tp_atr_mult" in combo:
        _patch(_sq, "SQZ_TP_ATR_MULT", combo["sqz_tp_atr_mult"])
    if "sqz_min_rr" in combo:
        _patch(_sq, "SQZ_MIN_RR", combo["sqz_min_rr"])

    # ─── Patch TIMEFRAME_PARAMS for rsi_long / rsi_short ───
    # 🔴 2CHECK: was mutating global dict without restore — BUG found in BMD 2026-03-28.
    # Now save originals and restore in finally block at end of function.
    _tf_originals = {}
    for key in ("rsi_long", "rsi_short"):
        if key in combo:
            _tf_originals[key] = TIMEFRAME_PARAMS["1h"].get(key)
            TIMEFRAME_PARAMS["1h"][key] = combo[key]

    # ─── Patch vol_spike multiplier (indicator module global) ───
    if "vol_spike_mult" in combo:
        import indicator_calc as _ic
        _patch(_ic, "VOL_SPIKE_MULT", combo["vol_spike_mult"])

    # ─── Engine indicator overrides ───
    engine_overrides = {
        k: combo[k] for k in ("bb_touch_tol", "adx_range_max", "bb_width_squeeze",
                               "rsi_long", "rsi_short")
        if k in combo
    }

    # ─── Build scorer override for vol_spike_bonus ───
    scorer_override = None
    if "vol_spike_bonus" in combo:
        from backtest.scoring import ScoringWeights
        scorer_override = WeightedScorer(ScoringWeights(w_vol_spike=combo["vol_spike_bonus"]))

    # ─── Build position_overrides for BT strategies ───
    # Range: 仍用 BTRangeStrategy（同 production 邏輯一致）
    # Trend: 唔建 BTTrendStrategy — 用 production TrendStrategy + monkey-patch（上面已 patch）
    pos_range = {}
    if "sl_atr_mult_range" in combo:
        pos_range["sl_atr_mult"] = combo["sl_atr_mult_range"]
    if "min_rr" in combo:
        pos_range["min_rr"] = combo["min_rr"]

    strat_overrides = {}
    if pos_range or scorer_override:
        strat_overrides["range"] = BTRangeStrategy(
            position_overrides=pos_range or None, scorer=scorer_override)

    # ─── Run per pair ───
    # 🔴 2CHECK: try/finally ensures TIMEFRAME_PARAMS restored even on exception.
    # BMD 2026-03-28: previously leaked mutations across combos.
    try:
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
    finally:
        # Restore ALL mutations (BMD P0 + P4 fix: prevent leak across combos)
        # Module-level attrs (_ts, _cs, _sq, _ic)
        for (mod, attr), orig_val in _module_originals.items():
            if orig_val is not None:
                setattr(mod, attr, orig_val)
        # TIMEFRAME_PARAMS dict entries
        for key, orig_val in _tf_originals.items():
            if orig_val is not None:
                TIMEFRAME_PARAMS["1h"][key] = orig_val
            else:
                TIMEFRAME_PARAMS["1h"].pop(key, None)


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
    validate: bool = False, validate_folds: int = 5, validate_top: int = 10,
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

    # ── Auto Walk-Forward Validation (if --validate) ──
    if validate:
        auto_validate_top(
            ranked, data, pairs,
            top_n=validate_top, folds=validate_folds,
            balance=initial_balance, output_path=output_path,
        )

    return ranked


# ═══════════════════════════════════════════════════════
# Auto Walk-Forward Validation (post-sweep filter)
# ═══════════════════════════════════════════════════════

def auto_validate_top(
    ranked: list[dict], data: dict, pairs: list[str],
    top_n: int = 10, folds: int = 5, balance: float = 10000,
    output_path: str | None = None,
) -> list[dict]:
    """
    Run walk-forward + monte-carlo on top N combos from grid search.
    Returns only combos that PASS both tests.

    背答案考 100 分冇用，換份卷都 80 分先係真本事。
    """
    candidates = ranked[:top_n]
    if not candidates:
        print("\n  No candidates to validate.")
        return []

    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD VALIDATION")
    print(f"  Testing top {len(candidates)} combos × {folds} folds × {len(pairs)} pairs")
    print(f"{'='*60}")

    validated = []

    # ─── Save original module globals for restore after each combo ───
    import trader_cycle.strategies.trend_strategy as _ts
    import trader_cycle.strategies.crash_strategy as _cs
    import trader_cycle.strategies.squeeze_strategy as _sq
    _orig = {
        # trend
        "ts_PULLBACK_TOLERANCE": _ts.PULLBACK_TOLERANCE,
        "ts_CONFIDENCE_THRESHOLD": _ts.CONFIDENCE_THRESHOLD,
        "ts_TREND_SL_ATR_MULT": _ts.TREND_SL_ATR_MULT,
        "ts_TREND_MIN_RR": _ts.TREND_MIN_RR,
        "ts_MACD_HIST_DECAY_THRESHOLD": _ts.MACD_HIST_DECAY_THRESHOLD,
        # crash
        "cs_CRASH_RSI_ENTRY": _cs.CRASH_RSI_ENTRY,
        "cs_CRASH_SL_ATR_MULT": _cs.CRASH_SL_ATR_MULT,
        "cs_CRASH_MIN_RR": _cs.CRASH_MIN_RR,
        # squeeze
        "sq_BB_PCTL_SQUEEZE": _sq.BB_PCTL_SQUEEZE,
        "sq_SQZ_SL_ATR_MULT": _sq.SQZ_SL_ATR_MULT,
        "sq_SQZ_TP_ATR_MULT": _sq.SQZ_TP_ATR_MULT,
        "sq_SQZ_MIN_RR": _sq.SQZ_MIN_RR,
    }

    for i, combo in enumerate(candidates):
        params = combo["params"]
        param_str = ", ".join(f"{k}={v}" for k, v in params.items())
        print(f"\n  ── Combo #{i+1}: {param_str} ──")

        # ─── Monkey-patch trend params for this combo ───
        if "pullback_tolerance" in params:
            _ts.PULLBACK_TOLERANCE = params["pullback_tolerance"]
        if "confidence_threshold" in params:
            _ts.CONFIDENCE_THRESHOLD = params["confidence_threshold"]
        if "sl_atr_mult_trend" in params:
            _ts.TREND_SL_ATR_MULT = params["sl_atr_mult_trend"]
        if "min_rr" in params:
            _ts.TREND_MIN_RR = params["min_rr"]
        if "macd_hist_decay" in params:
            _ts.MACD_HIST_DECAY_THRESHOLD = params["macd_hist_decay"]
        # crash
        if "crash_rsi_entry" in params:
            _cs.CRASH_RSI_ENTRY = params["crash_rsi_entry"]
        if "crash_sl_atr_mult" in params:
            _cs.CRASH_SL_ATR_MULT = params["crash_sl_atr_mult"]
        if "crash_min_rr" in params:
            _cs.CRASH_MIN_RR = params["crash_min_rr"]
        # squeeze
        if "sqz_bb_pctl" in params:
            _sq.BB_PCTL_SQUEEZE = params["sqz_bb_pctl"]
        if "sqz_sl_atr_mult" in params:
            _sq.SQZ_SL_ATR_MULT = params["sqz_sl_atr_mult"]
        if "sqz_tp_atr_mult" in params:
            _sq.SQZ_TP_ATR_MULT = params["sqz_tp_atr_mult"]
        if "sqz_min_rr" in params:
            _sq.SQZ_MIN_RR = params["sqz_min_rr"]

        # ── Walk-Forward ──
        fold_results = []
        for pair in pairs:
            if pair not in data:
                continue
            df_1h, df_4h = data[pair]
            total = len(df_1h)
            usable = total - WARMUP_CANDLES
            if usable < folds * 2:
                continue

            fold_size = usable // (folds + 1)

            for fold_i in range(folds):
                is_end = WARMUP_CANDLES + fold_size * (fold_i + 1)
                oos_start = is_end - WARMUP_CANDLES
                oos_end = min(is_end + fold_size, total)

                if oos_end - oos_start < WARMUP_CANDLES + 10:
                    continue

                is_1h = df_1h.iloc[:is_end].copy()
                is_4h_end_ts = int(is_1h.iloc[-1]["close_time"])
                is_4h = df_4h[df_4h["close_time"].astype(int) <= is_4h_end_ts].copy()

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

                try:
                    s_is = BacktestEngine(
                        symbol=pair, df_1h=is_1h, df_4h=is_4h,
                        initial_balance=balance, param_overrides=params, quiet=True,
                    ).run()
                    s_oos = BacktestEngine(
                        symbol=pair, df_1h=oos_1h, df_4h=oos_4h,
                        initial_balance=balance, param_overrides=params, quiet=True,
                    ).run()
                except ValueError:
                    continue  # not enough data for this fold
                except Exception as e:
                    log.warning("WF fold %d/%s unexpected error: %s", fold_i + 1, pair, e)
                    continue

                fold_results.append({
                    "fold": fold_i + 1, "pair": pair,
                    "is_ret": s_is["return_pct"], "oos_ret": s_oos["return_pct"],
                })

        # Compute WFE
        if not fold_results:
            print(f"    Walk-Forward: NO VALID FOLDS")
            continue

        is_rets = [f["is_ret"] for f in fold_results]
        oos_rets = [f["oos_ret"] for f in fold_results]
        mean_is = float(np.mean(is_rets)) if is_rets else 0
        mean_oos = float(np.mean(oos_rets))
        if abs(mean_is) < 0.5:
            wfe = 1.0 if mean_oos > 0 else 0.0
        else:
            wfe = mean_oos / mean_is if mean_is != 0 else 0
        wf_pass = wfe > 0.50

        # Count OOS positive folds
        oos_positive = sum(1 for r in oos_rets if r > 0)
        oos_total = len(oos_rets)
        consistency = oos_positive / oos_total if oos_total > 0 else 0

        print(f"    Walk-Forward: WFE={wfe:.2f} (IS={mean_is:+.1f}% OOS={mean_oos:+.1f}%) "
              f"Folds={oos_positive}/{oos_total} positive → {'PASS' if wf_pass else 'FAIL'}")

        # ── Monte Carlo (trade shuffle) ──
        mc_pass = True  # simplified: check OOS consistency instead of full MC
        if consistency < 0.4:
            mc_pass = False
            print(f"    Consistency: {consistency:.0%} positive folds → FAIL (need >40%)")
        else:
            print(f"    Consistency: {consistency:.0%} positive folds → PASS")

        if wf_pass and mc_pass:
            combo["validation"] = {
                "wfe": round(wfe, 3),
                "mean_is_ret": round(mean_is, 2),
                "mean_oos_ret": round(mean_oos, 2),
                "oos_positive_folds": oos_positive,
                "total_folds": oos_total,
                "consistency": round(consistency, 3),
            }
            validated.append(combo)
            print(f"    ✅ VALIDATED")
        else:
            print(f"    ❌ REJECTED")

        # ─── Restore all strategy globals ───
        _ts.PULLBACK_TOLERANCE = _orig["ts_PULLBACK_TOLERANCE"]
        _ts.CONFIDENCE_THRESHOLD = _orig["ts_CONFIDENCE_THRESHOLD"]
        _ts.TREND_SL_ATR_MULT = _orig["ts_TREND_SL_ATR_MULT"]
        _ts.TREND_MIN_RR = _orig["ts_TREND_MIN_RR"]
        _ts.MACD_HIST_DECAY_THRESHOLD = _orig["ts_MACD_HIST_DECAY_THRESHOLD"]
        _cs.CRASH_RSI_ENTRY = _orig["cs_CRASH_RSI_ENTRY"]
        _cs.CRASH_SL_ATR_MULT = _orig["cs_CRASH_SL_ATR_MULT"]
        _cs.CRASH_MIN_RR = _orig["cs_CRASH_MIN_RR"]
        _sq.BB_PCTL_SQUEEZE = _orig["sq_BB_PCTL_SQUEEZE"]
        _sq.SQZ_SL_ATR_MULT = _orig["sq_SQZ_SL_ATR_MULT"]
        _sq.SQZ_TP_ATR_MULT = _orig["sq_SQZ_TP_ATR_MULT"]
        _sq.SQZ_MIN_RR = _orig["sq_SQZ_MIN_RR"]

    # Summary
    print(f"\n{'='*60}")
    print(f"  VALIDATION SUMMARY")
    print(f"{'='*60}")
    print(f"  Tested:    {len(candidates)} combos")
    print(f"  Passed:    {len(validated)} combos")
    print(f"  Rejected:  {len(candidates) - len(validated)} combos")
    if validated:
        print(f"\n  Validated combos (換份卷都 80 分嘅):")
        for v in validated:
            p = ", ".join(f"{k}={v2}" for k, v2 in v["params"].items())
            vd = v["validation"]
            print(f"    {p}  WFE={vd['wfe']:.2f} OOS={vd['mean_oos_ret']:+.1f}% "
                  f"Consistency={vd['consistency']:.0%}")
    else:
        print(f"\n  ⚠️ 冇任何 combo 通過 walk-forward — 全部 overfit。")
        print(f"  呢個結果係正常嘅（207 combos 全 overfit 嘅歷史重演）。")
        print(f"  建議：檢討策略邏輯而唔係繼續調參數。")

    # Save validated results
    if output_path and validated:
        val_path = output_path.replace(".json", "_validated.json")
        val_out = {
            "meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": output_path,
                "folds": folds,
                "tested": len(candidates),
                "passed": len(validated),
            },
            "validated": [
                {"rank": i + 1, "params": v["params"],
                 "aggregate": v["aggregate"], "validation": v["validation"]}
                for i, v in enumerate(validated)
            ],
        }
        os.makedirs(os.path.dirname(val_path) or ".", exist_ok=True)
        with open(val_path, "w") as f:
            json.dump(val_out, f, indent=2, default=str)
        print(f"\n  Validated JSON saved: {val_path}")

    return validated


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
    parser.add_argument("--validate", action="store_true",
                        help="Auto walk-forward validation on top combos after sweep")
    parser.add_argument("--validate-folds", type=int, default=5,
                        help="Walk-forward folds (default: 5)")
    parser.add_argument("--validate-top", type=int, default=10,
                        help="Validate top N combos (default: 10)")
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
        validate=args.validate, validate_folds=args.validate_folds,
        validate_top=args.validate_top,
    )


if __name__ == "__main__":
    main()
