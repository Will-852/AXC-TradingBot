#!/usr/bin/env python3
"""
regime_analysis.py — Regime-conditional statistical analysis of backtest trades.

Answers: "In each (vol_regime × market_mode × strategy) cell, what is the
expected trade quality? Which regimes have positive expectancy?"

Usage:
  python3 backtest/regime_analysis.py                   # all pairs, 180d
  python3 backtest/regime_analysis.py --days 360        # 360d
  python3 backtest/regime_analysis.py --pair XRPUSDT    # single pair

Output: prints analysis tables + saves JSON to backtest/data/regime_analysis.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
DATA_DIR = os.path.join(AXC_HOME, "backtest", "data")

# Minimum sample size for statistical reliability
MIN_SAMPLES_RELIABLE = 20   # full confidence in statistics
MIN_SAMPLES_USABLE = 8      # cautious use (flag as low-n)


@dataclass
class CellStats:
    """Statistics for one (vol_regime, market_mode, strategy, pair) cell."""
    vol_regime: str
    market_mode: str
    strategy: str
    pair: str
    n: int
    wins: int
    losses: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float          # win_rate × avg_win - (1-win_rate) × avg_loss
    expectancy_per_trade: float  # avg PnL per trade
    total_pnl: float
    avg_confidence: float
    confidence_reliable: bool  # n >= MIN_SAMPLES_RELIABLE
    # Confidence-vs-win-rate monotonicity (do higher confidence signals win more?)
    conf_monotonic: bool | None  # True = higher conf → higher wr; None = insufficient data

    def to_dict(self) -> dict:
        return {
            "vol_regime": self.vol_regime,
            "market_mode": self.market_mode,
            "strategy": self.strategy,
            "pair": self.pair,
            "n": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "expectancy": round(self.expectancy, 4),
            "expectancy_per_trade": round(self.expectancy_per_trade, 2),
            "total_pnl": round(self.total_pnl, 2),
            "avg_confidence": round(self.avg_confidence, 4),
            "reliable": self.confidence_reliable,
            "conf_monotonic": self.conf_monotonic,
        }


def load_trades(pairs: list[str], days: int) -> list[dict]:
    """Load trade JSONL files for specified pairs and period."""
    all_trades = []
    for pair in pairs:
        path = os.path.join(DATA_DIR, f"bt_{pair}_{days}d_trades.jsonl")
        if not os.path.exists(path):
            print(f"  SKIP: {path} not found")
            continue
        with open(path) as f:
            trades = [json.loads(line) for line in f if line.strip()]
        # Check regime fields exist
        if trades and "vol_regime" not in trades[0]:
            print(f"  SKIP: {pair} trades missing regime fields (re-run backtest)")
            continue
        all_trades.extend(trades)
        print(f"  Loaded {len(trades)} trades from {pair}")
    return all_trades


def check_conf_monotonicity(trades: list[dict]) -> bool | None:
    """Check if higher confidence correlates with higher win rate.

    Split trades at median confidence. If upper half win rate > lower half,
    confidence signal is monotonic (good calibration).
    Returns None if insufficient data (< 6 trades).
    """
    if len(trades) < 6:
        return None
    confs = sorted(trades, key=lambda t: t.get("confidence", 0))
    mid = len(confs) // 2
    lower = confs[:mid]
    upper = confs[mid:]
    wr_lower = sum(1 for t in lower if t["pnl"] > 0) / max(len(lower), 1)
    wr_upper = sum(1 for t in upper if t["pnl"] > 0) / max(len(upper), 1)
    return wr_upper >= wr_lower


def analyze_cell(trades: list[dict], vol_regime: str, market_mode: str,
                 strategy: str, pair: str) -> CellStats | None:
    """Compute statistics for one regime cell."""
    cell_trades = [
        t for t in trades
        if t["vol_regime"] == vol_regime
        and t["market_mode"] == market_mode
        and t["strategy"] == strategy
        and (pair == "ALL" or t["symbol"] == pair)
    ]
    n = len(cell_trades)
    if n == 0:
        return None

    wins_list = [t["pnl"] for t in cell_trades if t["pnl"] > 0]
    losses_list = [t["pnl"] for t in cell_trades if t["pnl"] <= 0]

    wins = len(wins_list)
    losses = len(losses_list)
    win_rate = wins / n
    avg_win = sum(wins_list) / wins if wins > 0 else 0.0
    avg_loss = abs(sum(losses_list) / losses) if losses > 0 else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    total_pnl = sum(t["pnl"] for t in cell_trades)
    avg_conf = sum(t.get("confidence", 0) for t in cell_trades) / n

    return CellStats(
        vol_regime=vol_regime,
        market_mode=market_mode,
        strategy=strategy,
        pair=pair,
        n=n,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        expectancy_per_trade=total_pnl / n,
        total_pnl=total_pnl,
        avg_confidence=avg_conf,
        confidence_reliable=n >= MIN_SAMPLES_RELIABLE,
        conf_monotonic=check_conf_monotonicity(cell_trades),
    )


def run_analysis(trades: list[dict], pairs: list[str]) -> list[CellStats]:
    """Run full regime-conditional analysis."""
    vol_regimes = sorted(set(t["vol_regime"] for t in trades))
    market_modes = sorted(set(t["market_mode"] for t in trades))
    strategies = sorted(set(t["strategy"] for t in trades))

    results = []

    # Per-pair analysis
    for pair in pairs:
        for vr in vol_regimes:
            for mm in market_modes:
                for st in strategies:
                    cell = analyze_cell(trades, vr, mm, st, pair)
                    if cell:
                        results.append(cell)

    # Aggregated (ALL pairs) — for cross-pair patterns
    for vr in vol_regimes:
        for mm in market_modes:
            for st in strategies:
                cell = analyze_cell(trades, vr, mm, st, "ALL")
                if cell:
                    results.append(cell)

    return results


def print_results(results: list[CellStats]) -> None:
    """Print formatted analysis tables."""
    # Group by pair
    pairs_seen = sorted(set(r.pair for r in results))

    for pair in pairs_seen:
        pair_results = [r for r in results if r.pair == pair]
        if not pair_results:
            continue

        header = f"\n{'='*80}\n  {pair} — Regime-Conditional Analysis\n{'='*80}"
        print(header)
        print(f"  {'Vol':>6s}  {'Mode':>5s}  {'Strat':>5s}  {'N':>4s}  "
              f"{'WR':>5s}  {'AvgW':>8s}  {'AvgL':>8s}  {'E[PnL]':>8s}  "
              f"{'Total':>9s}  {'AvgConf':>7s}  {'Mono':>4s}  {'Flag':>6s}")
        print("-" * 90)

        # Sort: positive expectancy first, then by n desc
        pair_results.sort(key=lambda r: (-r.expectancy_per_trade, -r.n))

        for r in pair_results:
            flag = ""
            if r.n < MIN_SAMPLES_USABLE:
                flag = "LOW-N"
            elif r.n < MIN_SAMPLES_RELIABLE:
                flag = "med-n"

            mono = "—" if r.conf_monotonic is None else ("Y" if r.conf_monotonic else "N")

            # Color coding via emoji
            if r.expectancy_per_trade > 0 and r.n >= MIN_SAMPLES_USABLE:
                marker = "+"
            elif r.expectancy_per_trade < 0 and r.n >= MIN_SAMPLES_USABLE:
                marker = "-"
            else:
                marker = "?"

            print(
                f"{marker} {r.vol_regime:>6s}  {r.market_mode:>5s}  {r.strategy:>5s}  "
                f"{r.n:>4d}  {r.win_rate:>5.0%}  {r.avg_win:>8.1f}  {r.avg_loss:>8.1f}  "
                f"{r.expectancy_per_trade:>+8.1f}  {r.total_pnl:>+9.1f}  "
                f"{r.avg_confidence:>7.2f}  {mono:>4s}  {flag:>6s}"
            )

    # Summary: which regimes to trade
    print(f"\n{'='*80}")
    print("  VERDICT: Positive Expectancy Regimes (n >= 8)")
    print(f"{'='*80}")
    positive = [r for r in results if r.expectancy_per_trade > 0
                and r.n >= MIN_SAMPLES_USABLE and r.pair != "ALL"]
    negative = [r for r in results if r.expectancy_per_trade <= 0
                and r.n >= MIN_SAMPLES_USABLE and r.pair != "ALL"]

    if positive:
        print("\n  TRADE (positive expectancy):")
        for r in sorted(positive, key=lambda r: -r.expectancy_per_trade):
            conf_note = f" [RELIABLE n={r.n}]" if r.confidence_reliable else f" [n={r.n}]"
            print(f"    {r.pair} {r.vol_regime}×{r.market_mode}×{r.strategy}: "
                  f"E[PnL]={r.expectancy_per_trade:+.1f} WR={r.win_rate:.0%}{conf_note}")

    if negative:
        print("\n  AVOID (negative expectancy):")
        for r in sorted(negative, key=lambda r: r.expectancy_per_trade):
            print(f"    {r.pair} {r.vol_regime}×{r.market_mode}×{r.strategy}: "
                  f"E[PnL]={r.expectancy_per_trade:+.1f} WR={r.win_rate:.0%} [n={r.n}]")

    # Confidence calibration summary
    print(f"\n{'='*80}")
    print("  CONFIDENCE CALIBRATION (is higher confidence = higher win rate?)")
    print(f"{'='*80}")
    calibrated = [r for r in results if r.conf_monotonic is True and r.n >= MIN_SAMPLES_USABLE]
    uncalibrated = [r for r in results if r.conf_monotonic is False and r.n >= MIN_SAMPLES_USABLE]
    if calibrated:
        print("\n  CALIBRATED (conf gate useful):")
        for r in calibrated:
            print(f"    {r.pair} {r.vol_regime}×{r.market_mode}×{r.strategy} [n={r.n}]")
    if uncalibrated:
        print("\n  UNCALIBRATED (conf gate may not help):")
        for r in uncalibrated:
            print(f"    {r.pair} {r.vol_regime}×{r.market_mode}×{r.strategy} [n={r.n}]")


def main():
    parser = argparse.ArgumentParser(description="Regime-conditional backtest analysis")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--pair", type=str, default=None, help="Single pair or omit for all")
    args = parser.parse_args()

    if args.pair:
        pairs = [args.pair]
    else:
        pairs = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]

    print(f"Regime Analysis — {args.days}d, pairs: {pairs}")
    trades = load_trades(pairs, args.days)
    if not trades:
        print("No trades found. Run backtest first.")
        sys.exit(1)

    print(f"Total trades: {len(trades)}")
    results = run_analysis(trades, pairs)
    print_results(results)

    # Save JSON
    output_path = os.path.join(DATA_DIR, "regime_analysis.json")
    with open(output_path, "w") as f:
        json.dump({
            "days": args.days,
            "pairs": pairs,
            "total_trades": len(trades),
            "cells": [r.to_dict() for r in results],
        }, f, indent=2)
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
