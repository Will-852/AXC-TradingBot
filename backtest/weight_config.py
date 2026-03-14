"""
weight_config.py — Search space definitions for backtest optimization.

設計決定：
  - Phase A（入場參數）用 LHS sampling 300 組合
  - Phase B（評分權重）用 optuna Bayesian 150 trials
  - 搜索範圍基於 production 現值 ± 合理擴展
  - LHS 用 scipy.stats.qmc（M3 Max 有裝）
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════
# Phase A: Entry Parameter Search Space
# ═══════════════════════════════════════════════════════

@dataclass
class EntryParam:
    """Single entry parameter definition."""
    name: str
    low: float
    high: float
    default: float
    step: float | None = None  # for integer/discrete params
    description: str = ""


# Phase A search dimensions — ordered by expected impact
ENTRY_SEARCH_SPACE: list[EntryParam] = [
    EntryParam("bb_width_min", 0.04, 0.10, 0.05,
               description="R0 gate: BB width maximum for range mode"),
    EntryParam("trend_min_keys", 3, 4, 4, step=1,
               description="Trend: minimum KEY conditions (3 or 4)"),
    EntryParam("pullback_tolerance", 0.015, 0.04, 0.015,
               description="Trend: price-to-MA50 distance tolerance"),
    EntryParam("adx_range_max", 18, 30, 20,
               description="Range R1 gate: ADX ceiling"),
    EntryParam("entry_volume_min", 0.4, 1.0, 0.8,
               description="Volume gate for all strategies"),
    EntryParam("mode_confirmation", 1, 2, 2, step=1,
               description="Mode switch confirmation cycles"),
    EntryParam("rsi_long_low", 30, 45, 40,
               description="Trend LONG RSI lower bound"),
    EntryParam("rsi_long_high", 50, 70, 55,
               description="Trend LONG RSI upper bound"),
    EntryParam("bb_touch_tol", 0.005, 0.015, 0.005,
               description="BB band touch tolerance"),
    EntryParam("rsi_short_low", 35, 55, 45,
               description="Trend SHORT RSI lower bound"),
    EntryParam("rsi_short_high", 55, 70, 60,
               description="Trend SHORT RSI upper bound"),
    EntryParam("mode_rsi_trend_low", 30, 40, 32,
               description="Mode RSI: below this → TREND vote"),
    EntryParam("mode_rsi_trend_high", 60, 70, 68,
               description="Mode RSI: above this → TREND vote"),
]

# Production baseline for comparison
ENTRY_DEFAULTS = {p.name: p.default for p in ENTRY_SEARCH_SPACE}


# ═══════════════════════════════════════════════════════
# Phase B: Scoring Weight Search Space
# ═══════════════════════════════════════════════════════

@dataclass
class WeightParam:
    """Single scoring weight definition."""
    name: str
    low: float
    high: float
    default: float
    description: str = ""


WEIGHT_SEARCH_SPACE: list[WeightParam] = [
    WeightParam("w_vol", 0.0, 0.5, 0.3,
                description="Volume multiplier slope"),
    WeightParam("w_obv", 0.0, 1.5, 0.5,
                description="OBV confirmation strength"),
    WeightParam("w_stoch", 0.0, 1.5, 1.0,
                description="Stochastic STRONG bonus"),
    WeightParam("base_score_strong", 3.0, 5.0, 4.0,
                description="Range STRONG base score"),
    WeightParam("base_score_weak", 2.0, 4.0, 3.0,
                description="Range WEAK base score"),
    WeightParam("confidence_threshold_low", 2.5, 4.0, 3.0,
                description="Risk ramp start (score <= this → 1.0x risk)"),
    WeightParam("confidence_threshold_high", 3.5, 5.5, 4.5,
                description="Risk ramp end (score >= this → max risk multiplier)"),
    WeightParam("confidence_risk_high_mult", 1.0, 2.0, 1.25,
                description="Max risk multiplier at ramp top"),
    WeightParam("min_score", 0.0, 3.5, 0.0,
                description="Minimum signal score to accept (0 = no filter)"),
]

WEIGHT_DEFAULTS = {p.name: p.default for p in WEIGHT_SEARCH_SPACE}


# ═══════════════════════════════════════════════════════
# Objective Function Weights
# ═══════════════════════════════════════════════════════

OBJECTIVE_WEIGHTS = {
    "calmar": 0.40,         # Calmar ratio (return / max_drawdown)
    "profit_factor": 0.30,  # gross_profit / gross_loss
    "adj_win_rate": 0.20,   # cluster-adjusted win rate
    "trade_count": 0.10,    # normalized trade count (diminishing returns)
}


# ═══════════════════════════════════════════════════════
# Optimizer Config
# ═══════════════════════════════════════════════════════

@dataclass
class OptimizerConfig:
    """
    Configuration for optimizer run.

    Runtime estimates (M3 Max, 180d data):
      - Each sample × 1 pair ≈ 45s (4520 1H candles)
      - Stage 1: samples × 3 pairs × 45s = 100 samples ≈ 3.75 hrs
      - Stage 2: trials × 8 pairs × 45s per viable config
      - Quick test: --samples 20 --trials 30 (~1.5 hrs total)
    """
    # Stage 1 (LHS)
    stage1_samples: int = 100            # 300 original plan, 100 practical default
    stage1_pairs: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "XRPUSDT"])
    stage1_min_trades: int = 15          # per pair minimum (relaxed from 30)
    stage1_require_positive_pnl: bool = True

    # Stage 2 (Bayesian)
    stage2_trials: int = 80              # 150 original plan, 80 practical default
    stage2_pairs: list[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT",
        "DOGEUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT",
    ])

    # Walk-forward validation
    wf_folds: int = 3
    wf_fold_days: int = 70

    # Anti-overfit
    min_consistent_pairs: int = 5        # out of 8 must be positive
    shrinkage_factor: float = 0.70       # 70% optimized + 30% default
    stability_delta_pct: float = 0.20    # ±1 step must be within 20%

    # Data
    backtest_days: int = 180
    max_workers: int = 6                 # ProcessPool parallelism

    # Viable config selection
    max_viable_configs: int = 10
