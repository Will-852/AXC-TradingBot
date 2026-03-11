"""
optimizer.py — Backtest weight optimization core engine.

設計決定：
  - Stage 1 (LHS): Latin Hypercube Sampling 搵可行入場配置
  - Stage 2 (Bayesian): optuna TPE 搵最佳評分權重
  - Walk-forward 3-fold 防過擬合
  - 唔改 production 代碼（只用 backtest/ 下嘅可配置策略）

Stage 1 流程:
  300 LHS samples × 3 pairs → 篩選 ≥30 trades & 正 PnL → top 10 viable configs

Stage 2 流程:
  每個 viable config × 150 optuna trials × 8 pairs → 最佳權重

Walk-forward:
  3 folds × best configs → in-sample vs out-of-sample 比較
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
_scripts = os.path.join(AXC_HOME, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
if AXC_HOME not in sys.path:
    sys.path.insert(0, AXC_HOME)

from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES
from backtest.scoring import WeightedScorer, ScoringWeights
from backtest.strategies.bt_range_strategy import BTRangeStrategy
from backtest.strategies.bt_trend_strategy import BTTrendStrategy
from backtest.weight_config import (
    ENTRY_SEARCH_SPACE, WEIGHT_SEARCH_SPACE,
    ENTRY_DEFAULTS, WEIGHT_DEFAULTS,
    OBJECTIVE_WEIGHTS, OptimizerConfig,
)

log = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(AXC_HOME, "backtest", "data")


# ═══════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════

def load_pair_data(pair: str, days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch 1H + 4H data for a pair."""
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    s1h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES)).timestamp() * 1000)
    s4h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES * 4)).timestamp() * 1000)
    df_1h = fetch_klines_range(pair, "1h", s1h, end_ms)
    df_4h = fetch_klines_range(pair, "4h", s4h, end_ms)
    return df_1h, df_4h


def load_all_data(pairs: list[str], days: int) -> dict[str, tuple]:
    """Load data for all pairs (sequential — API rate limits)."""
    data = {}
    for pair in pairs:
        log.info("Fetching %s...", pair)
        data[pair] = load_pair_data(pair, days)
    return data


def _split_weights(weights: dict | None) -> tuple[dict | None, float]:
    """Separate min_score from scoring weights dict."""
    if not weights:
        return None, 0.0
    min_score = weights.get("min_score", 0.0)
    scoring_only = {k: v for k, v in weights.items() if k != "min_score"}
    return scoring_only or None, min_score


# ═══════════════════════════════════════════════════════
# Single Backtest Run
# ═══════════════════════════════════════════════════════

def run_single_backtest(
    pair: str,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    entry_params: dict,
    scoring_weights: dict | None = None,
    min_score: float = 0.0,
) -> dict:
    """Run one backtest with given entry params and scoring weights.

    Score integration:
      - Strategies compute signal.score via WeightedScorer
      - Engine filters signals below min_score
      - Engine uses scorer.risk_multiplier(score) for confidence-based position sizing
    """
    weights = ScoringWeights.from_dict(scoring_weights) if scoring_weights else ScoringWeights()
    scorer = WeightedScorer(weights)

    # Build entry overrides for strategies
    entry_overrides = {k: v for k, v in entry_params.items()}

    range_strat = BTRangeStrategy(entry_overrides=entry_overrides, scorer=scorer)
    trend_strat = BTTrendStrategy(entry_overrides=entry_overrides, scorer=scorer)

    # param_overrides for engine (BB width, ADX, bb_touch_tol — patched into indicator_calc)
    engine_overrides = {}
    for key in ("bb_width_min", "bb_touch_tol", "adx_range_max", "bb_width_squeeze"):
        if key in entry_params:
            engine_overrides[key] = entry_params[key]

    mode_conf = int(entry_params.get("mode_confirmation", 2))

    engine = BacktestEngine(
        symbol=pair,
        df_1h=df_1h.copy(),
        df_4h=df_4h.copy(),
        param_overrides=engine_overrides,
        strategy_overrides={"range": range_strat, "trend": trend_strat},
        mode_confirmation=mode_conf,
        min_score=min_score,
        scorer=scorer,
        quiet=True,
    )

    return engine.run()


# ═══════════════════════════════════════════════════════
# Objective Function
# ═══════════════════════════════════════════════════════

def compute_objective(results_by_pair: dict[str, dict]) -> float:
    """
    Composite objective: 40% Calmar + 30% PF + 20% adj WR + 10% trade count.
    Higher = better. Returns negative infinity for invalid configs.
    """
    if not results_by_pair:
        return float("-inf")

    total_trades = sum(r["total_trades"] for r in results_by_pair.values())
    if total_trades == 0:
        return float("-inf")

    # Aggregate metrics
    total_pnl = sum(r["final_balance"] - 10000.0 for r in results_by_pair.values())
    total_return_pct = total_pnl / (10000.0 * len(results_by_pair)) * 100

    max_dd_pct = max(r["max_drawdown_pct"] for r in results_by_pair.values())
    if max_dd_pct <= 0:
        max_dd_pct = 0.1  # avoid division by zero

    # Calmar = annualized return / max drawdown
    # Approximate: 180d data → annualize × 2
    calmar = (total_return_pct * 2) / max_dd_pct

    # Profit factor (aggregate)
    gross_profit = sum(
        sum(t.pnl for t in r["trades"] if t.pnl > 0)
        for r in results_by_pair.values()
    )
    gross_loss = abs(sum(
        sum(t.pnl for t in r["trades"] if t.pnl <= 0)
        for r in results_by_pair.values()
    ))
    pf = gross_profit / gross_loss if gross_loss > 0 else 10.0
    pf = min(pf, 10.0)  # cap

    # Adjusted win rate (average across pairs)
    adj_wrs = [r.get("cluster_adj_wr", r["win_rate"]) for r in results_by_pair.values()
               if r["total_trades"] > 0]
    avg_adj_wr = sum(adj_wrs) / len(adj_wrs) if adj_wrs else 0

    # Trade count (diminishing returns: log scale, target ~30/pair)
    trades_per_pair = total_trades / len(results_by_pair)
    trade_score = min(np.log1p(trades_per_pair) / np.log1p(50), 1.0) * 100

    # Weighted sum
    w = OBJECTIVE_WEIGHTS
    score = (
        w["calmar"] * calmar
        + w["profit_factor"] * pf * 10  # scale PF to similar range
        + w["adj_win_rate"] * avg_adj_wr
        + w["trade_count"] * trade_score
    )

    return score


# ═══════════════════════════════════════════════════════
# Stage 1: LHS Sampling
# ═══════════════════════════════════════════════════════

def generate_lhs_samples(n_samples: int, seed: int = 42) -> list[dict]:
    """Generate Latin Hypercube Samples for entry parameters."""
    try:
        from scipy.stats.qmc import LatinHypercube
        sampler = LatinHypercube(d=len(ENTRY_SEARCH_SPACE), seed=seed)
        raw = sampler.random(n=n_samples)
    except ImportError:
        log.warning("scipy not found, falling back to random sampling")
        rng = np.random.default_rng(seed)
        raw = rng.random((n_samples, len(ENTRY_SEARCH_SPACE)))

    samples = []
    for row in raw:
        params = {}
        for i, p in enumerate(ENTRY_SEARCH_SPACE):
            val = p.low + row[i] * (p.high - p.low)
            if p.step is not None:
                val = round(val / p.step) * p.step
                val = int(val) if p.step == 1 else val
            else:
                val = round(val, 4)
            params[p.name] = val
        samples.append(params)

    return samples


@dataclass
class Stage1Result:
    """Result for one LHS sample."""
    params: dict
    total_trades: int = 0
    total_pnl: float = 0.0
    trades_per_pair: dict = field(default_factory=dict)
    objective: float = float("-inf")
    viable: bool = False


def run_stage1(
    config: OptimizerConfig,
    data: dict[str, tuple] | None = None,
) -> list[Stage1Result]:
    """
    Stage 1: LHS exploration of entry parameters.
    Returns results sorted by objective (best first).
    """
    log.info("=== Stage 1: LHS Sampling (%d samples × %d pairs) ===",
             config.stage1_samples, len(config.stage1_pairs))

    # Load data
    if data is None:
        data = load_all_data(config.stage1_pairs, config.backtest_days)

    samples = generate_lhs_samples(config.stage1_samples)
    results: list[Stage1Result] = []

    total = len(samples)
    for idx, params in enumerate(samples):
        if (idx + 1) % 50 == 0 or idx == 0:
            log.info("  Stage 1: %d/%d...", idx + 1, total)

        pair_results = {}
        for pair in config.stage1_pairs:
            df_1h, df_4h = data[pair]
            try:
                r = run_single_backtest(pair, df_1h, df_4h, params)
                pair_results[pair] = r
            except Exception as e:
                log.debug("Stage1 error %s params=%s: %s", pair, params, e)
                pair_results[pair] = {"total_trades": 0, "final_balance": 10000.0,
                                       "trades": [], "max_drawdown_pct": 0, "win_rate": 0,
                                       "cluster_adj_wr": 0}

        total_trades = sum(r["total_trades"] for r in pair_results.values())
        total_pnl = sum(r["final_balance"] - 10000.0 for r in pair_results.values())
        trades_per_pair = {p: r["total_trades"] for p, r in pair_results.items()}

        # Viability check
        min_per_pair = min(trades_per_pair.values()) if trades_per_pair else 0
        viable = min_per_pair >= config.stage1_min_trades
        if config.stage1_require_positive_pnl:
            viable = viable and total_pnl > 0

        obj = compute_objective(pair_results) if viable else float("-inf")

        results.append(Stage1Result(
            params=params,
            total_trades=total_trades,
            total_pnl=round(total_pnl, 2),
            trades_per_pair=trades_per_pair,
            objective=obj,
            viable=viable,
        ))

    # Sort by objective
    results.sort(key=lambda r: r.objective, reverse=True)

    viable_count = sum(1 for r in results if r.viable)
    log.info("Stage 1 complete: %d viable / %d total", viable_count, len(results))

    return results


# ═══════════════════════════════════════════════════════
# Stage 2: Bayesian Optimization (optuna)
# ═══════════════════════════════════════════════════════

@dataclass
class Stage2Result:
    """Result for one viable config's weight optimization."""
    entry_params: dict
    best_weights: dict
    best_objective: float
    n_trials: int = 0
    results_by_pair: dict = field(default_factory=dict)


def run_stage2(
    viable_configs: list[dict],
    config: OptimizerConfig,
    data: dict[str, tuple] | None = None,
) -> list[Stage2Result]:
    """
    Stage 2: Bayesian optimization of scoring weights per viable config.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        use_optuna = True
    except ImportError:
        log.warning("optuna not installed, falling back to random search")
        use_optuna = False

    if data is None:
        data = load_all_data(config.stage2_pairs, config.backtest_days)

    results: list[Stage2Result] = []

    for cfg_idx, entry_params in enumerate(viable_configs):
        log.info("=== Stage 2: Config %d/%d — %d trials ===",
                 cfg_idx + 1, len(viable_configs), config.stage2_trials)

        if use_optuna:
            best_weights, best_obj, n_trials = _optuna_optimize(
                entry_params, data, config,
            )
        else:
            best_weights, best_obj, n_trials = _random_optimize(
                entry_params, data, config,
            )

        # Run final eval with best weights
        best_min_score = best_weights.get("min_score", 0.0)
        best_scoring_weights = {k: v for k, v in best_weights.items() if k != "min_score"}
        final_results = {}
        for pair in config.stage2_pairs:
            df_1h, df_4h = data[pair]
            try:
                r = run_single_backtest(
                    pair, df_1h, df_4h, entry_params, best_scoring_weights, min_score=best_min_score,
                )
                final_results[pair] = {
                    "total_trades": r["total_trades"],
                    "pnl": round(r["final_balance"] - 10000.0, 2),
                    "win_rate": r["win_rate"],
                    "max_drawdown_pct": r["max_drawdown_pct"],
                    "profit_factor": r["profit_factor"],
                }
            except Exception as e:
                log.error("Stage2 final eval error %s: %s", pair, e)

        results.append(Stage2Result(
            entry_params=entry_params,
            best_weights=best_weights,
            best_objective=best_obj,
            n_trials=n_trials,
            results_by_pair=final_results,
        ))

    results.sort(key=lambda r: r.best_objective, reverse=True)
    return results


def _optuna_optimize(
    entry_params: dict,
    data: dict[str, tuple],
    config: OptimizerConfig,
) -> tuple[dict, float, int]:
    """Bayesian optimization using optuna TPE sampler."""
    import optuna

    def objective(trial: optuna.Trial) -> float:
        all_params = {}
        for wp in WEIGHT_SEARCH_SPACE:
            all_params[wp.name] = trial.suggest_float(wp.name, wp.low, wp.high)

        # Constraint: ramp low must be < ramp high (otherwise linear ramp degenerates)
        if all_params.get("confidence_threshold_low", 3.0) >= all_params.get("confidence_threshold_high", 4.5):
            return float("-inf")

        # Separate engine-level min_score from scoring weights
        min_score = all_params.get("min_score", 0.0)
        scoring_weights = {k: v for k, v in all_params.items() if k != "min_score"}

        pair_results = {}
        for pair in config.stage2_pairs:
            df_1h, df_4h = data[pair]
            try:
                r = run_single_backtest(
                    pair, df_1h, df_4h, entry_params, scoring_weights, min_score=min_score,
                )
                pair_results[pair] = r
            except Exception:
                return float("-inf")

        return compute_objective(pair_results)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(objective, n_trials=config.stage2_trials, show_progress_bar=True)

    best = study.best_params
    return best, study.best_value, len(study.trials)


def _random_optimize(
    entry_params: dict,
    data: dict[str, tuple],
    config: OptimizerConfig,
) -> tuple[dict, float, int]:
    """Fallback random search when optuna unavailable."""
    rng = np.random.default_rng(42)
    best_weights = WEIGHT_DEFAULTS.copy()
    best_obj = float("-inf")

    for trial in range(config.stage2_trials):
        if (trial + 1) % 30 == 0:
            log.info("  Random search: %d/%d...", trial + 1, config.stage2_trials)

        all_params = {}
        for wp in WEIGHT_SEARCH_SPACE:
            all_params[wp.name] = round(rng.uniform(wp.low, wp.high), 4)

        # Constraint: ramp low must be < ramp high
        if all_params.get("confidence_threshold_low", 3.0) >= all_params.get("confidence_threshold_high", 4.5):
            continue

        min_score = all_params.get("min_score", 0.0)
        scoring_weights = {k: v for k, v in all_params.items() if k != "min_score"}

        pair_results = {}
        for pair in config.stage2_pairs:
            df_1h, df_4h = data[pair]
            try:
                r = run_single_backtest(
                    pair, df_1h, df_4h, entry_params, scoring_weights, min_score=min_score,
                )
                pair_results[pair] = r
            except Exception:
                break
        else:
            obj = compute_objective(pair_results)
            if obj > best_obj:
                best_obj = obj
                best_weights = all_params.copy()

    return best_weights, best_obj, config.stage2_trials


# ═══════════════════════════════════════════════════════
# Walk-Forward Validation
# ═══════════════════════════════════════════════════════

@dataclass
class WalkForwardResult:
    """Walk-forward validation result."""
    entry_params: dict
    weights: dict
    folds: list[dict] = field(default_factory=list)
    in_sample_avg: float = 0.0
    out_of_sample_avg: float = 0.0
    degradation_pct: float = 0.0
    passed: bool = False


def run_walk_forward(
    configs: list[Stage2Result],
    all_data: dict[str, tuple],
    config: OptimizerConfig,
) -> list[WalkForwardResult]:
    """
    Walk-forward validation: split data into folds, optimize on train, validate on test.
    """
    log.info("=== Walk-Forward Validation (%d folds × %d configs) ===",
             config.wf_folds, len(configs))

    results = []

    for cfg in configs:
        wf = WalkForwardResult(
            entry_params=cfg.entry_params,
            weights=cfg.best_weights,
        )

        fold_is_scores = []
        fold_oos_scores = []

        for fold_idx in range(config.wf_folds):
            # Split data into train/test by time
            fold_data_train, fold_data_test = _split_fold(
                all_data, fold_idx, config.wf_folds, config.wf_fold_days,
            )

            # In-sample
            wf_scoring, wf_min_score = _split_weights(cfg.best_weights)
            is_results = {}
            for pair in config.stage2_pairs:
                if pair not in fold_data_train:
                    continue
                df_1h, df_4h = fold_data_train[pair]
                try:
                    r = run_single_backtest(
                        pair, df_1h, df_4h, cfg.entry_params,
                        wf_scoring, min_score=wf_min_score,
                    )
                    is_results[pair] = r
                except Exception:
                    pass

            # Out-of-sample
            oos_results = {}
            for pair in config.stage2_pairs:
                if pair not in fold_data_test:
                    continue
                df_1h, df_4h = fold_data_test[pair]
                try:
                    r = run_single_backtest(
                        pair, df_1h, df_4h, cfg.entry_params,
                        wf_scoring, min_score=wf_min_score,
                    )
                    oos_results[pair] = r
                except Exception:
                    pass

            is_obj = compute_objective(is_results) if is_results else 0
            oos_obj = compute_objective(oos_results) if oos_results else 0

            fold_is_scores.append(is_obj)
            fold_oos_scores.append(oos_obj)

            wf.folds.append({
                "fold": fold_idx,
                "in_sample_obj": round(is_obj, 4),
                "out_of_sample_obj": round(oos_obj, 4),
                "is_trades": sum(r["total_trades"] for r in is_results.values()),
                "oos_trades": sum(r["total_trades"] for r in oos_results.values()),
            })

        wf.in_sample_avg = sum(fold_is_scores) / len(fold_is_scores) if fold_is_scores else 0
        wf.out_of_sample_avg = sum(fold_oos_scores) / len(fold_oos_scores) if fold_oos_scores else 0

        if wf.in_sample_avg > 0:
            wf.degradation_pct = (1 - wf.out_of_sample_avg / wf.in_sample_avg) * 100
        else:
            wf.degradation_pct = 100.0

        # Pass if OOS is at least 50% of IS
        wf.passed = wf.degradation_pct < 50

        results.append(wf)

    return results


def _split_fold(
    all_data: dict[str, tuple],
    fold_idx: int,
    n_folds: int,
    fold_days: int,
) -> tuple[dict, dict]:
    """Split data into train/test for a fold. Last fold = test."""
    train_data = {}
    test_data = {}

    for pair, (df_1h, df_4h) in all_data.items():
        total_1h = len(df_1h)
        candles_per_fold = (fold_days * 24)  # 1H candles per fold

        # test = fold_idx chunk, train = everything else
        test_start = fold_idx * candles_per_fold + WARMUP_CANDLES
        test_end = min(test_start + candles_per_fold, total_1h)

        if test_end <= test_start + 100:
            continue

        # Train: use data BEFORE the test fold (walk-forward = train on past, test on future)
        if fold_idx == 0:
            # First fold: train on later data, test on first chunk
            train_1h = df_1h.iloc[test_end:].reset_index(drop=True)
            # Need warmup before train
            warmup_1h = df_1h.iloc[max(0, test_end - WARMUP_CANDLES):test_end]
            train_1h = pd.concat([warmup_1h, train_1h]).reset_index(drop=True)
        else:
            # Train on data before test fold
            train_1h = df_1h.iloc[:test_start].reset_index(drop=True)

        test_1h = df_1h.iloc[max(0, test_start - WARMUP_CANDLES):test_end].reset_index(drop=True)

        if len(train_1h) < WARMUP_CANDLES + 50 or len(test_1h) < WARMUP_CANDLES + 50:
            continue

        # 4H: proportional split
        ratio_start = max(0, test_start - WARMUP_CANDLES) / total_1h if total_1h > 0 else 0
        ratio_end = test_end / total_1h if total_1h > 0 else 1
        total_4h = len(df_4h)

        test_4h_start = max(0, int(ratio_start * total_4h) - WARMUP_CANDLES)
        test_4h_end = min(int(ratio_end * total_4h), total_4h)
        test_4h = df_4h.iloc[test_4h_start:test_4h_end].reset_index(drop=True)

        if fold_idx == 0:
            train_4h = df_4h.iloc[int(ratio_end * total_4h):].reset_index(drop=True)
            warmup_4h = df_4h.iloc[max(0, int(ratio_end * total_4h) - WARMUP_CANDLES):int(ratio_end * total_4h)]
            train_4h = pd.concat([warmup_4h, train_4h]).reset_index(drop=True)
        else:
            train_4h = df_4h.iloc[:int(ratio_start * total_4h)].reset_index(drop=True)

        if len(train_4h) < 50 or len(test_4h) < 50:
            continue

        train_data[pair] = (train_1h, train_4h)
        test_data[pair] = (test_1h, test_4h)

    return train_data, test_data


# ═══════════════════════════════════════════════════════
# Stability Check
# ═══════════════════════════════════════════════════════

def check_stability(
    entry_params: dict,
    weights: dict,
    data: dict[str, tuple],
    pairs: list[str],
) -> dict:
    """
    Check parameter stability: ±1 step for each weight dimension.
    Returns dict of {param_name: {"minus": obj, "center": obj, "plus": obj, "cliff": bool}}.
    """
    # Baseline
    base_scoring, base_min_score = _split_weights(weights)
    baseline_results = {}
    for pair in pairs:
        df_1h, df_4h = data[pair]
        try:
            r = run_single_backtest(pair, df_1h, df_4h, entry_params, base_scoring, min_score=base_min_score)
            baseline_results[pair] = r
        except Exception:
            pass

    center_obj = compute_objective(baseline_results)
    stability = {}

    for wp in WEIGHT_SEARCH_SPACE:
        step = (wp.high - wp.low) * 0.1  # 10% of range as step
        results_minus = {}
        results_plus = {}

        # Minus step
        w_minus = weights.copy()
        w_minus[wp.name] = max(wp.low, weights.get(wp.name, wp.default) - step)
        sw_minus, ms_minus = _split_weights(w_minus)

        for pair in pairs:
            df_1h, df_4h = data[pair]
            try:
                r = run_single_backtest(pair, df_1h, df_4h, entry_params, sw_minus, min_score=ms_minus)
                results_minus[pair] = r
            except Exception:
                pass

        # Plus step
        w_plus = weights.copy()
        w_plus[wp.name] = min(wp.high, weights.get(wp.name, wp.default) + step)
        sw_plus, ms_plus = _split_weights(w_plus)

        for pair in pairs:
            df_1h, df_4h = data[pair]
            try:
                r = run_single_backtest(pair, df_1h, df_4h, entry_params, sw_plus, min_score=ms_plus)
                results_plus[pair] = r
            except Exception:
                pass

        obj_minus = compute_objective(results_minus) if results_minus else 0
        obj_plus = compute_objective(results_plus) if results_plus else 0

        # Cliff-edge detection: >20% performance drop in either direction
        cliff = False
        if center_obj > 0:
            if obj_minus < center_obj * (1 - 0.20):
                cliff = True
            if obj_plus < center_obj * (1 - 0.20):
                cliff = True

        stability[wp.name] = {
            "minus": round(obj_minus, 4),
            "center": round(center_obj, 4),
            "plus": round(obj_plus, 4),
            "cliff": cliff,
            "delta_minus_pct": round((1 - obj_minus / center_obj) * 100, 1) if center_obj > 0 else 0,
            "delta_plus_pct": round((1 - obj_plus / center_obj) * 100, 1) if center_obj > 0 else 0,
        }

    return stability


# ═══════════════════════════════════════════════════════
# Shrinkage (Anti-Overfit)
# ═══════════════════════════════════════════════════════

# Params where shrinkage would be counterproductive
# (min_score default=0 → shrinkage always pulls to 0, negating the optimization)
_SHRINKAGE_SKIP = {"min_score"}


def apply_shrinkage(
    optimized_weights: dict,
    shrinkage_factor: float = 0.70,
) -> dict:
    """Blend optimized weights with defaults: shrinkage% optimized + (1-shrinkage)% default.

    Skips shrinkage for params in _SHRINKAGE_SKIP where blending toward default
    would negate the optimization (e.g. min_score default=0).
    """
    blended = {}
    for wp in WEIGHT_SEARCH_SPACE:
        opt_val = optimized_weights.get(wp.name, wp.default)
        if wp.name in _SHRINKAGE_SKIP:
            blended[wp.name] = round(opt_val, 4)
        else:
            blended[wp.name] = round(
                shrinkage_factor * opt_val + (1 - shrinkage_factor) * wp.default, 4
            )
    return blended


# ═══════════════════════════════════════════════════════
# Cross-Pair Consistency
# ═══════════════════════════════════════════════════════

def check_cross_pair_consistency(
    results_by_pair: dict[str, dict],
    min_positive: int = 5,
) -> tuple[bool, int]:
    """Check if at least min_positive pairs have positive PnL."""
    positive = sum(
        1 for r in results_by_pair.values()
        if r.get("pnl", r.get("final_balance", 10000) - 10000) > 0
    )
    return positive >= min_positive, positive


# ═══════════════════════════════════════════════════════
# Full Pipeline
# ═══════════════════════════════════════════════════════

@dataclass
class OptimizationResult:
    """Complete optimization output."""
    stage1_results: list[dict] = field(default_factory=list)
    stage2_results: list[dict] = field(default_factory=list)
    walk_forward: list[dict] = field(default_factory=list)
    stability: dict = field(default_factory=dict)
    best_config: dict = field(default_factory=dict)
    baseline_comparison: dict = field(default_factory=dict)


def run_full_optimization(config: OptimizerConfig | None = None) -> OptimizationResult:
    """Run the complete Stage1 → Stage2 → Walk-Forward pipeline."""
    config = config or OptimizerConfig()
    result = OptimizationResult()
    start_time = time.time()

    # Load all data once
    all_pairs = list(set(config.stage1_pairs + config.stage2_pairs))
    log.info("Loading data for %d pairs × %dd...", len(all_pairs), config.backtest_days)
    all_data = load_all_data(all_pairs, config.backtest_days)

    # ─── Stage 1 ───
    stage1_data = {p: all_data[p] for p in config.stage1_pairs if p in all_data}
    s1_results = run_stage1(config, data=stage1_data)

    viable = [r for r in s1_results if r.viable][:config.max_viable_configs]
    result.stage1_results = [
        {"params": r.params, "total_trades": r.total_trades,
         "total_pnl": r.total_pnl, "trades_per_pair": r.trades_per_pair,
         "objective": round(r.objective, 4), "viable": r.viable}
        for r in s1_results[:20]  # top 20 for report
    ]

    if not viable:
        log.warning("No viable configs found in Stage 1! Try relaxing stage1_min_trades.")
        elapsed = time.time() - start_time
        log.info("Total time: %.1f minutes", elapsed / 60)
        return result

    log.info("Found %d viable configs, proceeding to Stage 2", len(viable))

    # ─── Stage 2 ───
    viable_params = [v.params for v in viable]
    stage2_data = {p: all_data[p] for p in config.stage2_pairs if p in all_data}
    s2_results = run_stage2(viable_params, config, data=stage2_data)

    result.stage2_results = [
        {"entry_params": r.entry_params, "best_weights": r.best_weights,
         "best_objective": round(r.best_objective, 4), "n_trials": r.n_trials,
         "results_by_pair": r.results_by_pair}
        for r in s2_results
    ]

    if not s2_results:
        log.warning("No Stage 2 results!")
        return result

    # ─── Walk-Forward ───
    wf_results = run_walk_forward(s2_results, all_data, config)

    result.walk_forward = [
        {"entry_params": wf.entry_params, "weights": wf.weights,
         "folds": wf.folds, "in_sample_avg": round(wf.in_sample_avg, 4),
         "out_of_sample_avg": round(wf.out_of_sample_avg, 4),
         "degradation_pct": round(wf.degradation_pct, 1), "passed": wf.passed}
        for wf in wf_results
    ]

    # Pick best config that passed walk-forward
    passed_configs = [(s2, wf) for s2, wf in zip(s2_results, wf_results) if wf.passed]

    if passed_configs:
        best_s2, best_wf = max(passed_configs, key=lambda x: x[0].best_objective)
    else:
        log.warning("No config passed walk-forward — using best overall")
        best_s2 = s2_results[0]

    # Apply shrinkage
    shrunk_weights = apply_shrinkage(best_s2.best_weights, config.shrinkage_factor)

    # Stability check
    stability = check_stability(
        best_s2.entry_params, shrunk_weights, stage2_data, config.stage2_pairs,
    )
    result.stability = stability

    # Cross-pair consistency
    consistent, n_positive = check_cross_pair_consistency(best_s2.results_by_pair)

    result.best_config = {
        "entry_params": best_s2.entry_params,
        "raw_weights": best_s2.best_weights,
        "shrunk_weights": shrunk_weights,
        "objective": round(best_s2.best_objective, 4),
        "cross_pair_consistent": consistent,
        "positive_pairs": n_positive,
        "cliff_edges": [k for k, v in stability.items() if v["cliff"]],
    }

    # ─── Baseline comparison ───
    baseline_results = {}
    for pair in config.stage2_pairs:
        if pair not in all_data:
            continue
        df_1h, df_4h = all_data[pair]
        try:
            r = run_single_backtest(pair, df_1h, df_4h, ENTRY_DEFAULTS)
            baseline_results[pair] = {
                "total_trades": r["total_trades"],
                "pnl": round(r["final_balance"] - 10000.0, 2),
                "win_rate": r["win_rate"],
                "max_drawdown_pct": r["max_drawdown_pct"],
            }
        except Exception:
            pass

    result.baseline_comparison = {
        "baseline": baseline_results,
        "baseline_objective": round(compute_objective(
            {p: run_single_backtest(p, all_data[p][0], all_data[p][1], ENTRY_DEFAULTS)
             for p in config.stage2_pairs if p in all_data}
        ), 4),
    }

    elapsed = time.time() - start_time
    log.info("=== Optimization complete in %.1f minutes ===", elapsed / 60)

    return result
