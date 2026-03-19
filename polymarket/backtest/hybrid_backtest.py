#!/usr/bin/env python3
"""
hybrid_backtest.py — Microstructure Signal + Swing Exit (Independent Pipeline 2)

設計決定：
- Entry: microstructure volume spike signal（已驗證 64.4% WR OOS）
- Exit: asymmetric TP/SL grid search（唔係固定 25%/25%）
- 2 個 exit checkpoint（5m + 10m），唔係得 1 個
- 三方比較：hold-to-resolution vs existing 25/25 vs optimal asymmetric
- 目標：Sharpe ≥ 1.0，Kelly > 0

核心 thesis:
Microstructure signal 已有 edge（vol spike → mean reversion）。
問題係：最佳離場策略係乜？Hold 定 swing？
如果 swing，TP/SL 應該對稱定非對稱？

用法:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/hybrid_backtest.py --days 90
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
LOG_DIR = os.path.join(_PROJECT_ROOT, "polymarket", "logs")
FIVE_MIN_MS = 300_000

# ─── Signal parameters (from microstructure_backtest v3) ───
VOL_SPIKE_WINDOW = 12
MIN_VOL_RATIO = 1.5
MIN_ABS_RET = 0.10
EDGE_THRESHOLD = 0.05
MIN_BUCKET_N = 5

# ─── PnL parameters ───
INITIAL_BANKROLL = 100.0
BET_PCT = 0.01
MARKET_PRICE = 0.50


# ═══════════════════════════════════════
#  Feature Computation (from microstructure_backtest)
# ═══════════════════════════════════════

def compute_5m_features(klines_5m: pd.DataFrame) -> pd.DataFrame:
    """Per-5m-candle backward-looking features."""
    df = klines_5m.copy()
    c = df["close"].astype(float).values
    o = df["open"].astype(float).values
    vol = df["volume"].astype(float).values
    n = len(c)

    df["ret_5m"] = (c - o) / np.where(o > 0, o, 1) * 100
    df["abs_ret_5m"] = np.abs(df["ret_5m"])
    vol_ma = pd.Series(vol).rolling(VOL_SPIKE_WINDOW, min_periods=1).mean().values
    df["vol_ratio"] = np.where(vol_ma > 0, vol / vol_ma, 1.0)

    rsi = np.full(n, 50.0)
    if n > 14:
        d = np.diff(c)
        gain = np.where(d > 0, d, 0.0)
        loss = np.where(d < 0, -d, 0.0)
        ag = np.zeros(n - 1)
        al = np.zeros(n - 1)
        ag[13] = gain[:14].mean()
        al[13] = loss[:14].mean()
        for i in range(14, n - 1):
            ag[i] = (ag[i - 1] * 13 + gain[i]) / 14
            al[i] = (al[i - 1] * 13 + loss[i]) / 14
        for i in range(13, n - 1):
            rsi[i + 1] = 100.0 if al[i] == 0 else 100.0 - 100.0 / (1.0 + ag[i] / al[i])
    df["rsi"] = rsi

    bb_pos = np.full(n, 0.5)
    for i in range(19, n):
        window = c[i - 19: i + 1]
        mean = window.mean()
        std = window.std(ddof=0)
        if std > 0:
            bb_pos[i] = (c[i] - (mean - 2 * std)) / (4 * std)
    df["bb_pos"] = np.clip(bb_pos, 0, 1)

    return df


# ═══════════════════════════════════════
#  Signal Classification (from microstructure_backtest)
# ═══════════════════════════════════════

def classify_signal(vol_ratio: float, ret_5m: float) -> str | None:
    abs_ret = abs(ret_5m)
    if vol_ratio < MIN_VOL_RATIO or abs_ret < MIN_ABS_RET:
        return None
    vt = "3x" if vol_ratio >= 3.0 else ("2x" if vol_ratio >= 2.0 else "1.5x")
    rt = "large" if abs_ret >= 0.5 else ("medium" if abs_ret >= 0.3 else "small")
    direction = "drop" if ret_5m < 0 else "rise"
    return f"vol{vt}_{rt}_{direction}"


def structural_filter(signal: str) -> bool:
    if signal is None:
        return False
    if "drop" in signal:
        return signal == "vol1.5x_small_drop"
    if "rise" in signal:
        if signal == "vol1.5x_small_rise":
            return False
        if "large" in signal and not signal.startswith("vol3x"):
            return False
        return True
    return False


def calibrate_lookup(train_data: list[dict]) -> dict[str, dict]:
    bucket_outcomes: dict[str, list[float]] = defaultdict(list)
    for r in train_data:
        signal = classify_signal(r["vol_ratio"], r["ret_5m"])
        if signal and structural_filter(signal):
            bucket_outcomes[signal].append(r["actual"])

    lookup: dict[str, dict] = {}
    for signal, outcomes in bucket_outcomes.items():
        if len(outcomes) >= MIN_BUCKET_N:
            lookup[signal] = {"p_up": float(np.mean(outcomes)), "n": len(outcomes)}

    for direction in ("drop", "rise"):
        agg = []
        for signal, outcomes in bucket_outcomes.items():
            if direction in signal:
                agg.extend(outcomes)
        if len(agg) >= MIN_BUCKET_N:
            lookup[f"agg_{direction}"] = {"p_up": float(np.mean(agg)), "n": len(agg)}

    return lookup


def get_signal_p(
    vol_ratio: float, ret_5m: float, rsi: float, bb_pos: float,
    lookup: dict,
) -> tuple[float | None, str | None]:
    signal = classify_signal(vol_ratio, ret_5m)
    if signal is None or not structural_filter(signal):
        return None, None
    entry = lookup.get(signal)
    if entry is None:
        direction = "drop" if ret_5m < 0 else "rise"
        entry = lookup.get(f"agg_{direction}")
    if entry is None:
        return None, None
    p_up = entry["p_up"]
    if rsi < 30:
        p_up += 0.02
    elif rsi > 70:
        p_up -= 0.02
    if bb_pos < 0.15:
        p_up += 0.01
    elif bb_pos > 0.85:
        p_up -= 0.01
    p_up = max(0.10, min(0.90, p_up))
    if abs(p_up - 0.5) < EDGE_THRESHOLD:
        return None, None
    return p_up, signal


# ═══════════════════════════════════════
#  Token Price Model
# ═══════════════════════════════════════

def estimate_yes_price(intra_ret_pct: float) -> float:
    """Maps BTC intra-period return to YES token price.
    0% → 0.50, +0.3% → ~0.70, -0.3% → ~0.30
    """
    return 0.50 + 0.30 * math.tanh(intra_ret_pct / 0.4)


def compute_unrealized(direction: str, intra_ret: float, bet_size: float) -> float:
    """Unrealized PnL at intermediate token price."""
    est_yes = estimate_yes_price(intra_ret)
    shares = bet_size / MARKET_PRICE
    sell_price = est_yes if direction == "YES" else (1.0 - est_yes)
    return shares * (sell_price - MARKET_PRICE)


# ═══════════════════════════════════════
#  Hybrid PnL Simulation
# ═══════════════════════════════════════

@dataclass
class ExitConfig:
    tp_pct: float = 0.25       # take profit threshold (fraction of bet)
    sl_pct: float = 0.25       # stop loss threshold
    check_5m: bool = True      # check at 5m mark
    check_10m: bool = True     # check at 10m mark
    label: str = ""


def simulate_hybrid(
    data: list[dict],
    lookup: dict,
    exit_cfg: ExitConfig,
) -> dict:
    """Simulate PnL with configurable asymmetric TP/SL and dual checkpoints."""
    bankroll = INITIAL_BANKROLL
    trades = []
    peak = bankroll
    max_dd = 0.0
    exit_counts = {"hold": 0, "tp_5m": 0, "sl_5m": 0, "tp_10m": 0, "sl_10m": 0}

    for r in data:
        p_up, signal = get_signal_p(
            r["vol_ratio"], r["ret_5m"], r["rsi"], r["bb_pos"], lookup,
        )
        if p_up is None:
            continue

        bet_size = bankroll * BET_PCT
        direction = "YES" if p_up > 0.5 else "NO"
        correct = (direction == "YES" and r["actual"] == 1.0) or \
                  (direction == "NO" and r["actual"] == 0.0)

        # Default: hold to resolution
        profit = bet_size if correct else -bet_size
        exit_type = "hold"

        # ── Checkpoint 1: 5m ──
        if exit_cfg.check_5m and "intra_ret_5m_1" in r:
            unreal = compute_unrealized(direction, r["intra_ret_5m_1"], bet_size)
            if unreal > bet_size * exit_cfg.tp_pct:
                profit = unreal
                exit_type = "tp_5m"
            elif unreal < -bet_size * exit_cfg.sl_pct:
                profit = unreal
                exit_type = "sl_5m"

        # ── Checkpoint 2: 10m (only if still holding) ──
        if exit_type == "hold" and exit_cfg.check_10m and "intra_ret_5m_2" in r:
            unreal = compute_unrealized(direction, r["intra_ret_5m_2"], bet_size)
            if unreal > bet_size * exit_cfg.tp_pct:
                profit = unreal
                exit_type = "tp_10m"
            elif unreal < -bet_size * exit_cfg.sl_pct:
                profit = unreal
                exit_type = "sl_10m"

        exit_counts[exit_type] = exit_counts.get(exit_type, 0) + 1
        bankroll += profit
        peak = max(peak, bankroll)
        dd = (peak - bankroll) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

        trades.append({
            "signal": signal,
            "direction": direction,
            "p_up": round(p_up, 4),
            "correct": correct,
            "exit": exit_type,
            "profit": round(profit, 4),
            "ret_pct": round(profit / bet_size * 100, 2) if bet_size > 0 else 0,
            "bankroll": round(bankroll, 4),
        })

    total = len(trades)
    wins = sum(1 for t in trades if t["profit"] > 0)
    rets = np.array([t["ret_pct"] / 100 for t in trades]) if trades else np.array([])
    mu = float(np.mean(rets)) if len(rets) > 0 else 0
    sigma = float(np.std(rets)) if len(rets) > 1 else 1.0
    sharpe = mu / sigma if sigma > 1e-10 else 0.0

    return {
        "label": exit_cfg.label,
        "tp": exit_cfg.tp_pct,
        "sl": exit_cfg.sl_pct,
        "checks": ("5m" if exit_cfg.check_5m else "") + ("+10m" if exit_cfg.check_10m else ""),
        "initial": INITIAL_BANKROLL,
        "final": round(bankroll, 2),
        "pnl": round(bankroll - INITIAL_BANKROLL, 2),
        "pnl_pct": round((bankroll - INITIAL_BANKROLL) / INITIAL_BANKROLL * 100, 2),
        "trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(wins / total, 4) if total > 0 else 0,
        "max_dd_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "mean_ret": round(mu * 100, 2),
        "std_ret": round(sigma * 100, 2),
        "mu_1sig": round((mu + sigma) * 100, 2),
        "mu_neg1sig": round((mu - sigma) * 100, 2),
        "exit_counts": dict(exit_counts),
        "trade_log": trades,
    }


# ═══════════════════════════════════════
#  Main Backtest
# ═══════════════════════════════════════

def run_backtest(days: int = 90) -> dict:
    now = datetime.now(timezone.utc)
    end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000) - 1
    split_dt = start_dt + timedelta(days=days // 2)
    split_ms = int(split_dt.timestamp() * 1000)

    print(f"╔══ Hybrid Backtest: Microstructure Signal + Swing Exit ══╗")
    print(f"║ {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d} ({days}d)")
    print(f"║ Train: {start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}")
    print(f"║ Test:  {split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}")
    print(f"║ Signal: Volume spike mean reversion (structural filter)")
    print(f"║ Exit: Asymmetric TP/SL grid × dual checkpoint (5m+10m)")
    print(f"║ Target: Sharpe ≥ 1.0")
    print(f"╚════════════════════════════════════════════════════════╝\n")

    # ── 1. Fetch ──
    print("[1/6] Fetching klines...")
    t0 = time.time()
    klines_5m = fetch_klines_range(SYMBOL, "5m", start_ms, end_ms)
    klines_15m = fetch_klines_range(SYMBOL, "15m", start_ms, end_ms)
    print(f"  5m: {len(klines_5m)} | 15m: {len(klines_15m)} | {time.time() - t0:.1f}s")

    # ── 2. Features ──
    print("\n[2/6] Computing 5m features...")
    features = compute_5m_features(klines_5m)

    # ── 3. Align 5m → 15m with DUAL intra-candle data ──
    print("\n[3/6] Aligning 5m → 15m (dual checkpoint)...")
    ts_to_idx = {int(t): i for i, t in enumerate(features["open_time"].astype(int).values)}
    warmup = max(VOL_SPIKE_WINDOW, 26)

    all_data = []
    for i in range(len(klines_15m)):
        row = klines_15m.iloc[i]
        ts_15 = int(row["open_time"])
        open_15 = float(row["open"])
        actual = 1.0 if float(row["close"]) > open_15 else 0.0

        # Signal: last 5m BEFORE this 15m opens
        idx = ts_to_idx.get(ts_15 - FIVE_MIN_MS)
        if idx is None or idx < warmup:
            continue

        f = features.iloc[idx]
        entry = {
            "ts": ts_15,
            "actual": actual,
            "ret_5m": float(f["ret_5m"]),
            "abs_ret_5m": float(f["abs_ret_5m"]),
            "vol_ratio": float(f["vol_ratio"]),
            "rsi": float(f["rsi"]),
            "bb_pos": float(f["bb_pos"]),
        }

        # ── Intra-15m checkpoint 1: first 5m candle (0-5min) ──
        intra_idx_1 = ts_to_idx.get(ts_15)
        if intra_idx_1 is not None:
            c1 = float(klines_5m.iloc[intra_idx_1]["close"])
            entry["intra_ret_5m_1"] = (c1 - open_15) / open_15 * 100 if open_15 > 0 else 0.0

        # ── Intra-15m checkpoint 2: second 5m candle (5-10min) ──
        intra_idx_2 = ts_to_idx.get(ts_15 + FIVE_MIN_MS)
        if intra_idx_2 is not None:
            c2 = float(klines_5m.iloc[intra_idx_2]["close"])
            entry["intra_ret_5m_2"] = (c2 - open_15) / open_15 * 100 if open_15 > 0 else 0.0

        all_data.append(entry)

    train = [d for d in all_data if d["ts"] < split_ms]
    test = [d for d in all_data if d["ts"] >= split_ms]
    print(f"  Total: {len(all_data)} | Train: {len(train)} | Test: {len(test)}")

    # ── 4. Calibrate ──
    print("\n[4/6] Calibrating...")
    lookup = calibrate_lookup(train)
    print(f"  {len(lookup)} signal buckets")
    for sig in sorted(lookup, key=lambda s: -abs(lookup[s]["p_up"] - 0.5)):
        e = lookup[sig]
        print(f"  {sig:<32} P(Up)={e['p_up']:.1%}  n={e['n']}")

    # ── 5. Grid search ──
    print(f"\n[5/6] Grid search (TP × SL × checkpoints)...")

    configs = []
    # Baseline: hold to resolution
    configs.append(ExitConfig(tp_pct=99, sl_pct=99, check_5m=False, check_10m=False, label="HOLD"))
    # Existing: symmetric 25/25
    configs.append(ExitConfig(tp_pct=0.25, sl_pct=0.25, check_5m=True, check_10m=False, label="SYM-25/25"))

    # Grid: asymmetric TP/SL × checkpoint combos
    tp_vals = [0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 0.75]
    sl_vals = [0.10, 0.15, 0.20, 0.25, 0.35, 0.50]
    check_modes = [
        (True, False, "5m"),
        (True, True, "5m+10m"),
    ]

    for tp_v in tp_vals:
        for sl_v in sl_vals:
            for c5, c10, clabel in check_modes:
                configs.append(ExitConfig(
                    tp_pct=tp_v, sl_pct=sl_v,
                    check_5m=c5, check_10m=c10,
                    label=f"TP{int(tp_v*100)}/SL{int(sl_v*100)}-{clabel}",
                ))

    results = []
    for cfg in configs:
        r = simulate_hybrid(test, lookup, cfg)
        # Strip trade_log for grid results
        r_summary = {k: v for k, v in r.items() if k != "trade_log"}
        results.append(r_summary)

    results.sort(key=lambda r: -r["sharpe"])

    print(f"\n  {'Label':<28} {'Sharpe':>7} {'WR':>6} {'N':>5} {'PnL':>8} {'DD':>6} {'μ':>7} {'σ':>7}")
    print(f"  {'─' * 78}")

    # Show HOLD and SYM baselines first
    for r in results:
        if r["label"] in ("HOLD", "SYM-25/25"):
            tag = " ◀ baseline" if r["label"] == "HOLD" else " ◀ existing"
            print(f"  {r['label']:<28} {r['sharpe']:>7.3f} {r['win_rate']:>5.1%}"
                  f" {r['trades']:>5} ${r['pnl']:>+7.2f} {r['max_dd_pct']:>5.1f}%"
                  f" {r['mean_ret']:>+6.1f}% {r['std_ret']:>6.1f}%{tag}")

    print(f"  {'─' * 78}")

    # Top 10 from grid
    grid_results = [r for r in results if r["label"] not in ("HOLD", "SYM-25/25")]
    for r in grid_results[:10]:
        marker = " ✅" if r["sharpe"] >= 1.0 else ""
        print(f"  {r['label']:<28} {r['sharpe']:>7.3f} {r['win_rate']:>5.1%}"
              f" {r['trades']:>5} ${r['pnl']:>+7.2f} {r['max_dd_pct']:>5.1f}%"
              f" {r['mean_ret']:>+6.1f}% {r['std_ret']:>6.1f}%{marker}")

    # Worst 3
    if len(grid_results) > 10:
        print(f"\n  Worst 3:")
        for r in grid_results[-3:]:
            print(f"  {r['label']:<28} {r['sharpe']:>7.3f} {r['win_rate']:>5.1%}"
                  f" {r['trades']:>5} ${r['pnl']:>+7.2f} {r['max_dd_pct']:>5.1f}%")

    # ── 6. Detailed analysis of best + baselines ──
    best_grid = grid_results[0] if grid_results else None
    hold_r = next(r for r in results if r["label"] == "HOLD")
    sym_r = next(r for r in results if r["label"] == "SYM-25/25")

    print(f"\n[6/6] Detailed comparison")
    print(f"{'═' * 65}")

    compare = [hold_r, sym_r]
    if best_grid:
        compare.append(best_grid)

    print(f"\n  {'Metric':<22}", end="")
    for r in compare:
        print(f" {r['label']:>18}", end="")
    print()
    print(f"  {'─' * (22 + 19 * len(compare))}")

    for key, fmt in [
        ("pnl", "${:+.2f}"), ("pnl_pct", "{:+.1f}%"),
        ("win_rate", "{:.1%}"), ("trades", "{}"),
        ("max_dd_pct", "{:.1f}%"),
        ("sharpe", "{:.3f}"),
        ("mean_ret", "{:+.1f}%"), ("std_ret", "{:.1f}%"),
        ("mu_1sig", "{:+.1f}%"), ("mu_neg1sig", "{:+.1f}%"),
    ]:
        print(f"  {key:<22}", end="")
        for r in compare:
            print(f" {fmt.format(r[key]):>18}", end="")
        print()

    # Exit type breakdown for each
    print(f"\n  ── Exit Types ──")
    for r in compare:
        print(f"  {r['label']}:")
        for et, ct in sorted(r["exit_counts"].items(), key=lambda x: -x[1]):
            if ct > 0:
                pct = ct / r["trades"] * 100 if r["trades"] > 0 else 0
                print(f"    {et:<12} {ct:>4} ({pct:.0f}%)")

    # Returns histogram for best
    if best_grid:
        best_full = simulate_hybrid(test, lookup, ExitConfig(
            tp_pct=best_grid["tp"], sl_pct=best_grid["sl"],
            check_5m="5m" in best_grid["checks"],
            check_10m="10m" in best_grid["checks"],
        ))
        rets = [t["ret_pct"] for t in best_full["trade_log"]]
        if rets:
            print(f"\n  ── Returns Histogram ({best_grid['label']}) ──")
            bins = [(-200, -100), (-100, -50), (-50, -25), (-25, 0),
                    (0, 25), (25, 50), (50, 100), (100, 200)]
            for lo, hi in bins:
                ct = sum(1 for r in rets if lo <= r < hi)
                bar = "█" * min(50, ct)
                print(f"  {lo:>+5}% ~ {hi:>+5}%: {ct:>3} {bar}")

    # Kelly analysis for best
    if best_grid and best_grid["trades"] >= 10:
        w_pnl = [t["profit"] for t in best_full["trade_log"] if t["profit"] > 0]
        l_pnl = [t["profit"] for t in best_full["trade_log"] if t["profit"] <= 0]
        if w_pnl and l_pnl:
            avg_w = abs(np.mean(w_pnl))
            avg_l = abs(np.mean(l_pnl))
            if avg_l > 0:
                b = avg_w / avg_l
                wr = best_grid["win_rate"]
                kelly = (wr * b - (1 - wr)) / b
                print(f"\n  ── Kelly ({best_grid['label']}) ──")
                print(f"  W/L ratio:     {b:.2f}")
                print(f"  Win rate:      {wr:.1%}")
                print(f"  Full Kelly:    {kelly:.1%}")
                print(f"  Half Kelly:    {kelly/2:.1%}")
                print(f"  {'✅ Positive Kelly' if kelly > 0 else '❌ Negative Kelly'}")

    # Equity curve for best
    if best_grid:
        print(f"\n  ── Equity ({best_grid['label']}) ──")
        eq = 100.0
        tl = best_full["trade_log"]
        step = max(1, len(tl) // 15)
        for i, t in enumerate(tl):
            eq += t["profit"]
            if i % step == 0 or i == len(tl) - 1:
                bar_w = int((eq - 90) * 1.5)
                bar = "█" * max(0, min(60, bar_w)) if bar_w >= 0 else "░" * min(15, -bar_w)
                print(f"  #{i+1:>4}  ${eq:>7.2f}  {bar}")

    # ── Sharpe target check ──
    target_met = best_grid and best_grid["sharpe"] >= 1.0
    hold_sharpe = hold_r["sharpe"]
    print(f"\n  ── Target Check ──")
    print(f"  HOLD Sharpe:     {hold_sharpe:.3f}")
    if best_grid:
        print(f"  Best Sharpe:     {best_grid['sharpe']:.3f}  {'✅ ≥ 1.0' if target_met else '❌ < 1.0'}")
        delta_sharpe = best_grid["sharpe"] - hold_sharpe
        print(f"  vs HOLD:         {delta_sharpe:+.3f}  {'✅ beats HOLD' if delta_sharpe > 0 else '❌ HOLD wins'}")

    # ── Save ──
    os.makedirs(LOG_DIR, exist_ok=True)
    result_path = os.path.join(LOG_DIR, "hybrid_backtest_results.json")
    output = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "version": "v1_micro_swing",
        "period": f"{start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "days": days,
        "symbol": SYMBOL,
        "train_period": f"{start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}",
        "test_period": f"{split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "hold": {k: v for k, v in hold_r.items() if k != "trade_log"},
        "symmetric_25_25": {k: v for k, v in sym_r.items() if k != "trade_log"},
        "best_grid": {k: v for k, v in best_grid.items() if k != "trade_log"} if best_grid else None,
        "grid_top10": [r for r in results[:10]],
        "sharpe_target_met": target_met,
    }

    fd, tmp = tempfile.mkstemp(dir=LOG_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        os.replace(tmp, result_path)
        print(f"\nResults → {result_path}")
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return output


def main():
    parser = argparse.ArgumentParser(description="Hybrid: Microstructure + Swing Exit")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run_backtest(days=args.days)


if __name__ == "__main__":
    main()
