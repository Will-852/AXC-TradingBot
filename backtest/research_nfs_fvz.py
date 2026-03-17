#!/usr/bin/env python3
"""
research_nfs_fvz.py — NFS + FVZ Research Backtest
版本: 2026-03-17
用途: Standalone research script — 唔動 production code。
      Grid sweep NFS/FVZ 參數，排名最佳組合。

Pipeline: Load 4H CSV → precompute_zones → simulate_trades → calc_metrics → rank → output

設計決定：
- Fill at next bar open（conservative，同 engine.py 一致）
- SL/TP same candle → SL wins（同 engine.py line 8）
- Commission 0.05%×2 + SL slippage 0.02%（mirror engine constants）
- Workers 讀 CSV path，唔傳 DataFrame（避免 pickle 慢）
- Two-stage grid：Stage 1 粗搜 → Stage 2 精搜
"""

import argparse
import itertools
import json
import logging
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from glob import glob
from typing import Optional

import numpy as np
import pandas as pd

# ── Project imports ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.indicator_calc_smc import (
    FairValueZone,
    NFS_FVZ_Trade,
    build_fvz,
    calc_atr_standalone,
    calc_entry_price,
    calc_stop_price,
    check_conflicting_zones,
    find_nfs_events,
    find_swing_points,
    regime_filter_passes,
)

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# Constants (mirror engine.py)
# ═══════════════════════════════════════════════════════

COMMISSION_RATE = 0.0005    # 0.05% per side
SL_SLIPPAGE_PCT = 0.0002   # 0.02% adverse slippage on SL
DATA_DIR = os.path.join(PROJECT_ROOT, "backtest", "data")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "backtest", "data")

# Fixed params (not swept)
ATR_MULT = 1.5
STOP_BUFFER_PCT = 0.001
MAX_ACTIVE_ZONES = 3
ATR_PERIOD = 14
MIN_TRADES = 8  # minimum trades for valid scoring


# ═══════════════════════════════════════════════════════
# Data Loader
# ═══════════════════════════════════════════════════════

def find_longest_csv(pair: str, timeframe: str = "4h") -> Optional[str]:
    """Find the longest CSV file for a pair+timeframe by date range in filename."""
    pattern = os.path.join(DATA_DIR, f"{pair}_{timeframe}_*.csv")
    files = glob(pattern)
    if not files:
        return None

    best_file = None
    best_span = 0
    for f in files:
        base = os.path.basename(f).replace(".csv", "")
        parts = base.split("_")
        if len(parts) >= 4:
            try:
                d1 = int(parts[2])
                d2 = int(parts[3])
                span = d2 - d1
                if span > best_span:
                    best_span = span
                    best_file = f
            except ValueError:
                continue
    return best_file


def load_csv(path: str) -> pd.DataFrame:
    """Load and prepare OHLCV CSV."""
    df = pd.read_csv(path)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    if "open_time" in df.columns:
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


# ═══════════════════════════════════════════════════════
# Precompute Zones
# ═══════════════════════════════════════════════════════

def precompute_zones(
    df: pd.DataFrame,
    params: dict,
    adx_series: pd.Series,
) -> list[FairValueZone]:
    """
    Chain: swings → NFS events → FVZ zones.
    Apply regime filter at NFS break point.
    """
    swings = find_swing_points(df, lookback=params["swing_lookback"])
    nfs_events = find_nfs_events(swings, df, max_gap=params["nfs_max_gap"])

    zones: list[FairValueZone] = []
    for nfs in nfs_events:
        # Regime gate at break point
        if not regime_filter_passes(adx_series, nfs.break_idx, params["regime_filter"]):
            continue

        fvz = build_fvz(
            nfs, df,
            expiry=params["zone_expiry"],
            min_width_pct=params["min_zone_width_pct"],
        )
        if fvz is not None:
            zones.append(fvz)

    return zones


# ═══════════════════════════════════════════════════════
# Trade Simulation
# ═══════════════════════════════════════════════════════

def simulate_trades(
    df: pd.DataFrame,
    zones: list[FairValueZone],
    params: dict,
    pair: str,
    atr_series: pd.Series,
) -> list[NFS_FVZ_Trade]:
    """
    Candle-by-candle simulation.
    - Zones become tradeable after break_idx
    - Fill at next bar open when price touches zone
    - SL/TP on same candle → SL wins
    """
    trades: list[NFS_FVZ_Trade] = []
    pending_zones: list[FairValueZone] = []  # zones waiting for fill
    active_trades: list[NFS_FVZ_Trade] = []  # open positions

    # Sort zones by break_idx (when they become valid)
    zones_sorted = sorted(zones, key=lambda z: z.nfs.break_idx)
    zone_ptr = 0  # pointer into zones_sorted

    entry_mode = params["fvz_entry"]
    stop_mode = params["stop_mode"]
    min_rr = params["min_rr"]

    for i in range(len(df)):
        candle_high = float(df["high"].iloc[i])
        candle_low = float(df["low"].iloc[i])
        candle_open = float(df["open"].iloc[i])
        bar_time = df["timestamp"].iloc[i] if "timestamp" in df.columns else None

        # ── 1. Activate new zones that are now valid ──
        while zone_ptr < len(zones_sorted) and zones_sorted[zone_ptr].nfs.break_idx <= i:
            z = zones_sorted[zone_ptr]
            zone_ptr += 1
            if len(pending_zones) < MAX_ACTIVE_ZONES:
                # Check conflict
                conflict = check_conflicting_zones(pending_zones, z)
                z_copy = FairValueZone(
                    nfs=z.nfs, zone_high=z.zone_high, zone_low=z.zone_low,
                    zone_width=z.zone_width, zone_mid=z.zone_mid,
                    created_at_idx=z.created_at_idx, expires_at_idx=z.expires_at_idx,
                    active=True, filled=False,
                )
                pending_zones.append(z_copy)

        # ── 2. Expire old zones ──
        pending_zones = [z for z in pending_zones if z.expires_at_idx >= i and z.active and not z.filled]

        # ── 3. Check pending zones for fill ──
        newly_filled: list[tuple[FairValueZone, float]] = []  # (zone, entry_price)
        for z in pending_zones:
            if z.filled:
                continue

            entry_price = calc_entry_price(z, entry_mode)

            if z.nfs.direction == "BULL":
                # Price must dip to entry level
                if candle_low <= entry_price:
                    newly_filled.append((z, entry_price))
                    z.filled = True
            else:  # BEAR
                # Price must rise to entry level
                if candle_high >= entry_price:
                    newly_filled.append((z, entry_price))
                    z.filled = True

        # Fill at NEXT bar open (conservative)
        if newly_filled and i + 1 < len(df):
            next_open = float(df["open"].iloc[i + 1])
            next_time = df["timestamp"].iloc[i + 1] if "timestamp" in df.columns else None

            for z, _ in newly_filled:
                fill_price = next_open

                # Calculate stop
                sl_price = calc_stop_price(
                    z, df, i + 1, stop_mode,
                    atr_mult=ATR_MULT,
                    atr_series=atr_series,
                    buffer_pct=STOP_BUFFER_PCT,
                )

                # Calculate risk and TP
                if z.nfs.direction == "BULL":
                    risk = fill_price - sl_price
                    if risk <= 0:
                        continue  # invalid stop
                    tp_price = fill_price + risk * min_rr
                else:
                    risk = sl_price - fill_price
                    if risk <= 0:
                        continue
                    tp_price = fill_price - risk * min_rr

                conflict = check_conflicting_zones(
                    [pz for pz in pending_zones if pz is not z], z
                )

                trade = NFS_FVZ_Trade(
                    pair=pair,
                    direction="LONG" if z.nfs.direction == "BULL" else "SHORT",
                    entry_idx=i + 1,
                    entry_price=fill_price,
                    entry_time=next_time,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    conflict_flag=conflict,
                    zone_width=z.zone_width,
                    params=params,
                )
                active_trades.append(trade)

        # ── 4. Check SL/TP for active trades ──
        closed_indices = []
        for t_idx, trade in enumerate(active_trades):
            if trade.exit_reason:
                continue
            if i <= trade.entry_idx:
                continue  # not yet active

            if trade.direction == "LONG":
                sl_hit = candle_low <= trade.sl_price
                tp_hit = candle_high >= trade.tp_price
            else:
                sl_hit = candle_high >= trade.sl_price
                tp_hit = candle_low <= trade.tp_price

            if sl_hit and tp_hit:
                # SL wins (conservative, same as engine.py)
                exit_reason = "SL"
            elif sl_hit:
                exit_reason = "SL"
            elif tp_hit:
                exit_reason = "TP"
            else:
                continue

            if exit_reason == "SL":
                if trade.direction == "LONG":
                    exit_price = trade.sl_price * (1 - SL_SLIPPAGE_PCT)
                else:
                    exit_price = trade.sl_price * (1 + SL_SLIPPAGE_PCT)
            else:
                exit_price = trade.tp_price  # limit order, no slippage

            # Commission: entry + exit
            commission = trade.entry_price * COMMISSION_RATE * 2

            # PnL
            if trade.direction == "LONG":
                raw_pnl = exit_price - trade.entry_price
                risk = trade.entry_price - trade.sl_price
            else:
                raw_pnl = trade.entry_price - exit_price
                risk = trade.sl_price - trade.entry_price

            pnl_pct = (raw_pnl / trade.entry_price) - (COMMISSION_RATE * 2)
            pnl_r = raw_pnl / risk if risk > 0 else 0

            trade.exit_idx = i
            trade.exit_price = exit_price
            trade.exit_time = bar_time
            trade.exit_reason = exit_reason
            trade.pnl_r = pnl_r
            trade.pnl_pct = pnl_pct
            trade.commission = commission
            closed_indices.append(t_idx)

        # Remove closed trades from active
        for ci in sorted(closed_indices, reverse=True):
            trades.append(active_trades.pop(ci))

    # Close remaining trades as expired
    for trade in active_trades:
        if not trade.exit_reason:
            trade.exit_reason = "EXPIRE"
            trade.exit_idx = len(df) - 1
            trade.exit_price = float(df["close"].iloc[-1])
            if trade.direction == "LONG":
                risk = trade.entry_price - trade.sl_price
                raw_pnl = trade.exit_price - trade.entry_price
            else:
                risk = trade.sl_price - trade.entry_price
                raw_pnl = trade.entry_price - trade.exit_price
            trade.pnl_pct = (raw_pnl / trade.entry_price) - (COMMISSION_RATE * 2)
            trade.pnl_r = raw_pnl / risk if risk > 0 else 0
            trade.commission = trade.entry_price * COMMISSION_RATE * 2
            trades.append(trade)

    return trades


# ═══════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════

def calc_metrics(trades: list[NFS_FVZ_Trade], total_zones: int) -> dict:
    """Calculate performance metrics for a set of trades."""
    n = len(trades)
    if n < MIN_TRADES:
        return {
            "n_trades": n, "total_zones": total_zones,
            "win_rate": 0, "profit_factor": 0, "expectancy_r": 0,
            "max_dd_r": 0, "fill_rate": 0, "score": float("-inf"),
            "avg_pnl_r": 0, "avg_win_r": 0, "avg_loss_r": 0,
            "conflict_pct": 0,
        }

    wins = [t for t in trades if t.pnl_r > 0]
    losses = [t for t in trades if t.pnl_r <= 0]
    win_rate = len(wins) / n

    gross_win = sum(t.pnl_r for t in wins)
    gross_loss = abs(sum(t.pnl_r for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 999

    expectancy_r = sum(t.pnl_r for t in trades) / n
    avg_win_r = gross_win / len(wins) if wins else 0
    avg_loss_r = gross_loss / len(losses) if losses else 0

    # Max drawdown in R
    cumulative_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in sorted(trades, key=lambda x: x.entry_idx):
        cumulative_r += t.pnl_r
        if cumulative_r > peak_r:
            peak_r = cumulative_r
        dd = peak_r - cumulative_r
        if dd > max_dd_r:
            max_dd_r = dd

    fill_rate = n / total_zones if total_zones > 0 else 0
    conflict_pct = sum(1 for t in trades if t.conflict_flag) / n

    # Composite score
    score = (
        expectancy_r * 0.35
        + win_rate * 0.25
        + min(profit_factor, 10) * 10 * 0.15  # cap PF at 10 for scoring
        + fill_rate * 0.10
        - max_dd_r * 0.15
    )

    return {
        "n_trades": n,
        "total_zones": total_zones,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3),
        "expectancy_r": round(expectancy_r, 4),
        "max_dd_r": round(max_dd_r, 3),
        "fill_rate": round(fill_rate, 4),
        "score": round(score, 4),
        "avg_pnl_r": round(expectancy_r, 4),
        "avg_win_r": round(avg_win_r, 3),
        "avg_loss_r": round(avg_loss_r, 3),
        "conflict_pct": round(conflict_pct, 4),
    }


def aggregate_multi_pair(results_by_pair: dict[str, dict]) -> dict:
    """Aggregate metrics across multiple pairs."""
    all_trades = sum(r["n_trades"] for r in results_by_pair.values())
    all_zones = sum(r["total_zones"] for r in results_by_pair.values())

    if all_trades < MIN_TRADES:
        return {
            "n_trades": all_trades, "total_zones": all_zones,
            "score": float("-inf"), "pairs": len(results_by_pair),
        }

    # Weighted average by trade count
    def wavg(key):
        total = sum(r[key] * r["n_trades"] for r in results_by_pair.values())
        return total / all_trades if all_trades > 0 else 0

    score = wavg("score")
    return {
        "n_trades": all_trades,
        "total_zones": all_zones,
        "pairs": len(results_by_pair),
        "win_rate": round(wavg("win_rate"), 4),
        "profit_factor": round(wavg("profit_factor"), 3),
        "expectancy_r": round(wavg("expectancy_r"), 4),
        "max_dd_r": round(max(r["max_dd_r"] for r in results_by_pair.values()), 3),
        "fill_rate": round(all_trades / all_zones if all_zones > 0 else 0, 4),
        "score": round(score, 4),
        "conflict_pct": round(wavg("conflict_pct"), 4),
    }


# ═══════════════════════════════════════════════════════
# Worker for ProcessPoolExecutor
# ═══════════════════════════════════════════════════════

def _worker_run(params: dict, csv_paths: dict[str, str]) -> dict:
    """
    Single worker: run one param combo across all pairs.
    Reads CSV from path (no pickle of DataFrame).
    """
    results_by_pair = {}

    for pair, path in csv_paths.items():
        try:
            df = load_csv(path)
            atr_series = calc_atr_standalone(df, period=ATR_PERIOD)

            # ADX: try tradingview_indicators, fallback to NaN
            try:
                from scripts.indicator_calc_smc import calc_adx_series
                adx_series = calc_adx_series(df)
            except Exception:
                adx_series = pd.Series(np.nan, index=df.index)

            zones = precompute_zones(df, params, adx_series)
            trades = simulate_trades(df, zones, params, pair, atr_series)
            metrics = calc_metrics(trades, len(zones))
            results_by_pair[pair] = metrics
        except Exception as e:
            log.warning("Worker error %s: %s", pair, e)
            results_by_pair[pair] = {
                "n_trades": 0, "total_zones": 0, "score": float("-inf"),
                "win_rate": 0, "profit_factor": 0, "expectancy_r": 0,
                "max_dd_r": 0, "fill_rate": 0, "conflict_pct": 0,
            }

    agg = aggregate_multi_pair(results_by_pair)
    return {"params": params, "aggregate": agg, "per_pair": results_by_pair}


# ═══════════════════════════════════════════════════════
# Parameter Grid
# ═══════════════════════════════════════════════════════

STAGE_1_GRID = {
    "swing_lookback": [1, 2],
    "nfs_max_gap": [10, 20, 30],
    "fvz_entry": ["upper", "mid", "lower"],
    "stop_mode": ["swing", "atr", "hybrid"],
    "min_rr": [2.0, 3.0, 4.0],
    # Fixed in stage 1
    "zone_expiry": [40],
    "regime_filter": ["none"],
    "min_zone_width_pct": [0.001],
}

STAGE_2_GRID = {
    "swing_lookback": [1, 2],
    "nfs_max_gap": [10, 20, 30],
    "fvz_entry": ["upper", "mid", "lower"],
    "stop_mode": ["swing", "atr", "hybrid"],
    "min_rr": [2.0, 3.0, 4.0],
    # Expanded in stage 2
    "zone_expiry": [20, 40, 80],
    "regime_filter": ["none", "adx>20", "adx>25"],
    "min_zone_width_pct": [0.0005, 0.001, 0.002],
}


def generate_grid(stage: int) -> list[dict]:
    """Generate parameter combinations for the given stage."""
    grid_def = STAGE_1_GRID if stage == 1 else STAGE_2_GRID
    keys = list(grid_def.keys())
    combos = []
    for vals in itertools.product(*grid_def.values()):
        combos.append(dict(zip(keys, vals)))
    return combos


# ═══════════════════════════════════════════════════════
# Spot Check Mode
# ═══════════════════════════════════════════════════════

def run_spot_check(pairs: list[str], n_zones: int = 10):
    """Show detailed zone + trade info for visual verification."""
    params = {
        "swing_lookback": 2, "nfs_max_gap": 20, "fvz_entry": "mid",
        "stop_mode": "hybrid", "min_rr": 3.0, "zone_expiry": 40,
        "regime_filter": "none", "min_zone_width_pct": 0.001,
    }

    for pair in pairs:
        csv_path = find_longest_csv(pair)
        if not csv_path:
            print(f"  No CSV found for {pair}")
            continue

        df = load_csv(csv_path)
        atr_series = calc_atr_standalone(df, period=ATR_PERIOD)

        try:
            from scripts.indicator_calc_smc import calc_adx_series
            adx_series = calc_adx_series(df)
        except Exception:
            adx_series = pd.Series(np.nan, index=df.index)

        swings = find_swing_points(df, lookback=params["swing_lookback"])
        nfs_events = find_nfs_events(swings, df, max_gap=params["nfs_max_gap"])
        zones = precompute_zones(df, params, adx_series)
        trades = simulate_trades(df, zones, params, pair, atr_series)
        metrics = calc_metrics(trades, len(zones))

        print(f"\n{'='*60}")
        print(f"  {pair} — {os.path.basename(csv_path)}")
        print(f"  Bars: {len(df)} | Swings: {len(swings)} | NFS: {len(nfs_events)} | Zones: {len(zones)} | Trades: {len(trades)}")
        print(f"  WR: {metrics['win_rate']:.1%} | PF: {metrics['profit_factor']:.2f} | "
              f"E[R]: {metrics['expectancy_r']:.3f} | MaxDD: {metrics['max_dd_r']:.2f}R")
        print(f"{'='*60}")

        # Show first N zones
        for j, z in enumerate(zones[:n_zones]):
            nfs = z.nfs
            print(f"\n  Zone {j+1}/{len(zones)}:")
            print(f"    NFS: {nfs.direction} origin=bar{nfs.origin_idx} break=bar{nfs.break_idx} gap={nfs.gap_bars}")
            print(f"    Price: origin={nfs.origin_price:.4f} break={nfs.break_price:.4f}")
            print(f"    FVZ: [{z.zone_low:.4f}, {z.zone_high:.4f}] width={z.zone_width:.4f} mid={z.zone_mid:.4f}")
            print(f"    Expiry: bar{z.expires_at_idx}")

            # Find matching trades
            matching = [t for t in trades if abs(t.entry_price - calc_entry_price(z, "mid")) < z.zone_width]
            if matching:
                t = matching[0]
                print(f"    Trade: {t.direction} entry={t.entry_price:.4f} SL={t.sl_price:.4f} TP={t.tp_price:.4f}")
                print(f"    Exit: {t.exit_reason} @ {t.exit_price:.4f} PnL={t.pnl_r:+.2f}R ({t.pnl_pct:+.2%})")
            else:
                print(f"    Trade: (no fill)")

        # Trade summary
        if trades:
            print(f"\n  --- Trade List ---")
            for j, t in enumerate(trades[:20]):
                time_str = str(t.entry_time)[:16] if t.entry_time else f"bar{t.entry_idx}"
                print(f"    #{j+1} {t.direction} {time_str} → {t.exit_reason} "
                      f"{t.pnl_r:+.2f}R {'⚠️conflict' if t.conflict_flag else ''}")


# ═══════════════════════════════════════════════════════
# Grid Run
# ═══════════════════════════════════════════════════════

def run_grid(
    stage: int,
    pairs: list[str],
    top_n: int = 10,
    workers: int = 4,
    output_path: Optional[str] = None,
    do_csv: bool = False,
):
    """Run parameter grid sweep."""
    grid = generate_grid(stage)
    print(f"\n  Stage {stage}: {len(grid)} combos × {len(pairs)} pairs, {workers} workers")

    # Resolve CSV paths
    csv_paths = {}
    for pair in pairs:
        path = find_longest_csv(pair)
        if path:
            csv_paths[pair] = path
            print(f"    {pair}: {os.path.basename(path)}")
        else:
            print(f"    {pair}: NO DATA — skipping")
    if not csv_paths:
        print("  No data available. Abort.")
        return

    t0 = time.time()
    all_results: list[dict] = []
    done = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_worker_run, combo, csv_paths): idx
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

    elapsed = time.time() - t0

    # Sort by score
    all_results.sort(key=lambda x: x["aggregate"].get("score", float("-inf")), reverse=True)

    # Print top N
    print(f"\n{'='*70}")
    print(f"  Top {top_n} results (stage {stage}, {elapsed:.1f}s)")
    print(f"{'='*70}")

    for rank, res in enumerate(all_results[:top_n], 1):
        agg = res["aggregate"]
        p = res["params"]
        print(f"\n  #{rank} Score={agg['score']:.4f}")
        print(f"    Params: lb={p['swing_lookback']} gap={p['nfs_max_gap']} "
              f"entry={p['fvz_entry']} stop={p['stop_mode']} RR={p['min_rr']}")
        print(f"    Expiry={p['zone_expiry']} regime={p['regime_filter']} "
              f"minW={p['min_zone_width_pct']}")
        print(f"    Trades={agg['n_trades']} Zones={agg['total_zones']} "
              f"WR={agg['win_rate']:.1%} PF={agg['profit_factor']:.2f} "
              f"E[R]={agg['expectancy_r']:.3f} MaxDD={agg['max_dd_r']:.2f}R")

    # Save JSON
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR, f"nfs_fvz_research_{ts}.json")

    # Sanitize for JSON (replace -inf/inf/nan)
    def _sanitize(obj):
        if isinstance(obj, float):
            if obj == float("-inf") or obj == float("inf") or np.isnan(obj):
                return None
            return obj
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        return obj

    output_data = {
        "stage": stage,
        "pairs": list(csv_paths.keys()),
        "grid_size": len(grid),
        "elapsed_s": round(elapsed, 1),
        "timestamp": datetime.now().isoformat(),
        "results": _sanitize(all_results[:50]),  # top 50
    }

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=OUTPUT_DIR)
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        os.replace(tmp_path, output_path)
        print(f"\n  Saved: {output_path}")
    except Exception:
        os.unlink(tmp_path)
        raise

    # Optional CSV
    if do_csv:
        csv_path = output_path.replace(".json", ".csv")
        rows = []
        for res in all_results:
            row = {**res["params"], **res["aggregate"]}
            rows.append(row)
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        print(f"  CSV: {csv_path}")

    return all_results


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="NFS+FVZ Research Backtest")
    parser.add_argument("--stage", type=int, default=1, choices=[1, 2],
                        help="Grid stage (1=coarse, 2=fine)")
    parser.add_argument("--pairs", nargs="+", default=["BTCUSDT", "ETHUSDT", "XRPUSDT"],
                        help="Trading pairs")
    parser.add_argument("--top", type=int, default=10, help="Show top N results")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers")
    parser.add_argument("--spot-check", action="store_true", help="Spot check mode")
    parser.add_argument("--csv", action="store_true", help="Also save CSV output")
    parser.add_argument("--output", type=str, default=None, help="Output path")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"\n  NFS+FVZ Research Backtest")
    print(f"  Pairs: {args.pairs}")

    if args.spot_check:
        run_spot_check(args.pairs)
    else:
        run_grid(
            stage=args.stage,
            pairs=args.pairs,
            top_n=args.top,
            workers=args.workers,
            output_path=args.output,
            do_csv=args.csv,
        )


if __name__ == "__main__":
    main()
