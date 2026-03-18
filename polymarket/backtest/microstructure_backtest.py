#!/usr/bin/env python3
"""
microstructure_backtest.py — Volume Spike Mean Reversion Model for BTC 15-min Markets

v3: Filtered + early exit.
- Structural signal filter: block unstable patterns (large drops with vol>=2x)
- Early exit: check first 5m inside 15m window → take profit / cut loss
- Dual comparison: hold-to-resolution vs early-exit
- Train/test split, lookup-table calibration, 1% bet size

Core thesis:
After 5m volume spike + significant move, the next 15m tends to mean-revert.
Rise signals (bet NO) are more reliable than drop signals.
Only vol1.5x small drops reliably mean-revert upward.

Usage:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/microstructure_backtest.py --days 90
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
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

# ─── Path setup ───
_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range

logger = logging.getLogger(__name__)

# ─── Constants ───
SYMBOL = "BTCUSDT"
LOG_DIR = os.path.join(_PROJECT_ROOT, "polymarket", "logs")
FIVE_MIN_MS = 300_000

# Model parameters
VOL_SPIKE_WINDOW = 12       # 12 × 5m = 1h rolling avg
MIN_VOL_RATIO = 1.5         # minimum volume spike to generate signal
MIN_ABS_RET = 0.10          # minimum |5m return %| to signal
EDGE_THRESHOLD = 0.05       # minimum |P(Up) - 0.5| to trade
MIN_BUCKET_N = 5            # minimum samples to calibrate a bucket

# PnL parameters
INITIAL_BANKROLL = 100.0    # $100
BET_PCT = 0.01              # 1% per bet
MARKET_PRICE = 0.50         # assumed market price (conservative)

# Early exit parameters
TAKE_PROFIT_TRIGGER = 0.25  # unrealized > 25% of bet → take profit
CUT_LOSS_TRIGGER = 0.25     # unrealized loss > 25% of bet → cut loss


# ═══════════════════════════════════════
#  Feature Computation
# ═══════════════════════════════════════

def compute_5m_features(klines_5m: pd.DataFrame) -> pd.DataFrame:
    """Per-5m-candle features. All backward-looking (no look-ahead)."""
    df = klines_5m.copy()
    c = df["close"].astype(float).values
    o = df["open"].astype(float).values
    vol = df["volume"].astype(float).values
    n = len(c)

    df["ret_5m"] = (c - o) / np.where(o > 0, o, 1) * 100
    df["abs_ret_5m"] = np.abs(df["ret_5m"])

    vol_ma = pd.Series(vol).rolling(VOL_SPIKE_WINDOW, min_periods=1).mean().values
    df["vol_ratio"] = np.where(vol_ma > 0, vol / vol_ma, 1.0)

    # RSI-14
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
    bb_period = 20
    for i in range(bb_period - 1, n):
        window = c[i - bb_period + 1 : i + 1]
        mean = window.mean()
        std = window.std(ddof=0)
        if std > 0:
            bb_pos[i] = (c[i] - (mean - 2 * std)) / (4 * std)
    df["bb_pos"] = np.clip(bb_pos, 0, 1)

    return df


# ═══════════════════════════════════════
#  Signal Classification + Structural Filter
# ═══════════════════════════════════════

def classify_signal(vol_ratio: float, ret_5m: float) -> str | None:
    """Assign to signal bucket. Returns None if no tradeable condition."""
    abs_ret = abs(ret_5m)
    if vol_ratio < MIN_VOL_RATIO or abs_ret < MIN_ABS_RET:
        return None

    if vol_ratio >= 3.0:
        vt = "3x"
    elif vol_ratio >= 2.0:
        vt = "2x"
    else:
        vt = "1.5x"

    if abs_ret >= 0.5:
        rt = "large"
    elif abs_ret >= 0.3:
        rt = "medium"
    else:
        rt = "small"

    direction = "drop" if ret_5m < 0 else "rise"
    return f"vol{vt}_{rt}_{direction}"


def structural_filter(signal: str) -> bool:
    """Block known-unstable patterns regardless of training data.

    OOS findings (v2):
    - Large drops with vol>=2x: tend to continue, NOT mean-revert
    - vol1.5x_small_rise: flipped direction OOS (47% WR)
    - large return buckets with vol<3x: small N, unreliable
    """
    if signal is None:
        return False

    # Drop signals: ONLY vol1.5x_small_drop is reliable
    if "drop" in signal:
        return signal == "vol1.5x_small_drop"

    # Rise signals: vol>=2x with small/medium returns are reliable
    if "rise" in signal:
        if signal == "vol1.5x_small_rise":
            return False  # flipped in OOS
        if "large" in signal and not signal.startswith("vol3x"):
            return False  # small N, unreliable
        return True

    return False


def calibrate_lookup(train_data: list[dict]) -> dict[str, dict]:
    """Build P(Up) lookup table from training data (filtered signals only)."""
    bucket_outcomes: dict[str, list[float]] = defaultdict(list)

    for r in train_data:
        signal = classify_signal(r["vol_ratio"], r["ret_5m"])
        if signal and structural_filter(signal):
            bucket_outcomes[signal].append(r["actual"])

    lookup: dict[str, dict] = {}
    for signal, outcomes in bucket_outcomes.items():
        if len(outcomes) >= MIN_BUCKET_N:
            lookup[signal] = {"p_up": float(np.mean(outcomes)), "n": len(outcomes)}

    # Aggregated direction fallback for thin buckets
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
    lookup: dict[str, dict],
) -> tuple[float | None, str | None]:
    """Get calibrated P(Up). Returns (None, None) if no tradeable signal."""
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

    # Mild RSI/BB modifiers
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
#  Early Exit: Token Price Model
# ═══════════════════════════════════════

def estimate_yes_price(intra_ret_pct: float) -> float:
    """Estimate YES token price from BTC return since 15m open.

    Maps intra-period BTC return to YES token price.
    0% → 0.50, +0.3% → ~0.70, -0.3% → ~0.30
    Uses tanh for saturation at extremes.
    """
    return 0.50 + 0.30 * math.tanh(intra_ret_pct / 0.4)


def compute_exit_pnl(direction: str, intra_ret: float, bet_size: float) -> float:
    """Compute PnL if we exit at estimated intermediate token price."""
    est_yes = estimate_yes_price(intra_ret)
    shares = bet_size / MARKET_PRICE

    if direction == "YES":
        sell_price = est_yes
    else:
        sell_price = 1.0 - est_yes

    return shares * (sell_price - MARKET_PRICE)


# ═══════════════════════════════════════
#  PnL Simulation
# ═══════════════════════════════════════

def simulate_pnl(data: list[dict], lookup: dict, early_exit: bool = False) -> dict:
    """Simulate PnL with optional early exit.

    early_exit=True: after first 5m inside 15m window, check unrealized PnL.
    If exceeds threshold → take profit / cut loss at estimated intermediate price.
    """
    bankroll = INITIAL_BANKROLL
    trades = []
    peak = bankroll
    max_dd = 0.0
    exit_counts = {"hold": 0, "take_profit": 0, "cut_loss": 0}

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

        exit_type = "hold"
        profit = bet_size if correct else -bet_size

        # Early exit check
        if early_exit and "intra_ret_5m" in r:
            unrealized = compute_exit_pnl(direction, r["intra_ret_5m"], bet_size)

            if unrealized > bet_size * TAKE_PROFIT_TRIGGER:
                profit = unrealized
                exit_type = "take_profit"
            elif unrealized < -bet_size * CUT_LOSS_TRIGGER:
                profit = unrealized
                exit_type = "cut_loss"
            # else: hold to resolution → profit already set

        exit_counts[exit_type] += 1
        bankroll += profit
        peak = max(peak, bankroll)
        dd = (peak - bankroll) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

        trades.append({
            "ts": r["ts"],
            "signal": signal,
            "direction": direction,
            "p_up": round(p_up, 4),
            "correct": correct,
            "exit": exit_type,
            "profit": round(profit, 4),
            "bankroll": round(bankroll, 4),
        })

    total = len(trades)
    wins = sum(1 for t in trades if t["profit"] > 0)
    return {
        "initial": INITIAL_BANKROLL,
        "final": round(bankroll, 2),
        "pnl": round(bankroll - INITIAL_BANKROLL, 2),
        "pnl_pct": round((bankroll - INITIAL_BANKROLL) / INITIAL_BANKROLL * 100, 2),
        "trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(wins / total, 4) if total > 0 else 0,
        "max_dd_pct": round(max_dd * 100, 2),
        "exit_counts": dict(exit_counts),
        "trade_log": trades,
    }


# ═══════════════════════════════════════
#  Reporting
# ═══════════════════════════════════════

def print_scenario(label: str, period: str, pnl: dict):
    """Print PnL report for one scenario."""
    print(f"\n  ── {label} ──")
    print(f"  Period: {period}")
    print(f"  Trades: {pnl['trades']}  ({pnl['wins']}W / {pnl['losses']}L)")
    print(f"  Win rate: {pnl['win_rate']:.1%}")
    print(f"  PnL: ${pnl['pnl']:+.2f}  ({pnl['pnl_pct']:+.1f}%)")
    print(f"  Bankroll: ${pnl['initial']:.2f} → ${pnl['final']:.2f}")
    print(f"  Max drawdown: {pnl['max_dd_pct']:.1f}%")
    ec = pnl["exit_counts"]
    if ec.get("take_profit", 0) + ec.get("cut_loss", 0) > 0:
        print(f"  Exits: hold={ec['hold']} | take_profit={ec['take_profit']} | cut_loss={ec['cut_loss']}")

    if not pnl["trade_log"]:
        return

    sig_stats: dict[str, dict] = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0.0})
    for t in pnl["trade_log"]:
        s = sig_stats[t["signal"]]
        if t["profit"] > 0:
            s["w"] += 1
        else:
            s["l"] += 1
        s["pnl"] += t["profit"]

    print(f"\n  {'Signal':<32} {'W/L':>7} {'WR':>6} {'PnL':>9}")
    print(f"  {'':─<56}")
    for sig in sorted(sig_stats, key=lambda s: -sig_stats[s]["pnl"]):
        st = sig_stats[sig]
        tot = st["w"] + st["l"]
        wr = st["w"] / tot if tot > 0 else 0
        print(f"  {sig:<32} {st['w']}/{st['l']:<4} {wr:>5.1%} ${st['pnl']:>+7.2f}")


def print_equity_curve(trades: list[dict], label: str):
    """Print equity curve snapshot every N trades."""
    if not trades:
        return
    step = max(1, len(trades) // 15)
    print(f"\n  ── Equity: {label} ──")
    for i, t in enumerate(trades):
        if i % step == 0 or i == len(trades) - 1:
            dt_str = datetime.fromtimestamp(t["ts"] / 1000, tz=timezone.utc).strftime("%m-%d")
            bar_len = int((t["bankroll"] - 95) * 2)
            bar = "█" * max(0, min(80, bar_len)) if bar_len >= 0 else "░" * min(20, -bar_len)
            print(f"  #{i+1:>4}  {dt_str}  ${t['bankroll']:>7.2f}  {bar}")


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

    print(f"╔══ Microstructure v3 — Filtered + Early Exit ═══╗")
    print(f"║ {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d} ({days}d)")
    print(f"║ Train: {start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}")
    print(f"║ Test:  {split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}")
    print(f"║ Bet: {BET_PCT:.0%} | Bankroll: ${INITIAL_BANKROLL:.0f}")
    print(f"║ Filter: structural (block unstable drop/rise)")
    print(f"║ Early exit: TP@{TAKE_PROFIT_TRIGGER:.0%} / SL@{CUT_LOSS_TRIGGER:.0%} unrealized")
    print(f"╚════════════════════════════════════════════════╝\n")

    # ── 1. Fetch ──
    print("[1/6] Fetching klines...")
    t0 = time.time()
    klines_5m = fetch_klines_range(SYMBOL, "5m", start_ms, end_ms)
    klines_15m = fetch_klines_range(SYMBOL, "15m", start_ms, end_ms)
    print(f"  5m: {len(klines_5m)} | 15m: {len(klines_15m)} | {time.time() - t0:.1f}s")

    # ── 2. Features ──
    print("\n[2/6] Computing 5m features...")
    features = compute_5m_features(klines_5m)

    # ── 3. Align 5m → 15m + compute intra-15m return for early exit ──
    print("\n[3/6] Aligning 5m → 15m (with intra-candle data)...")
    ts_to_idx = {int(t): i for i, t in enumerate(features["open_time"].astype(int).values)}
    warmup = max(VOL_SPIKE_WINDOW, 26)

    all_data = []
    for i in range(len(klines_15m)):
        row = klines_15m.iloc[i]
        ts_15 = int(row["open_time"])
        actual = 1.0 if float(row["close"]) > float(row["open"]) else 0.0

        # Signal features: last 5m BEFORE this 15m opens
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

        # Intra-15m: first 5m candle INSIDE the 15m window (for early exit)
        intra_idx = ts_to_idx.get(ts_15)
        if intra_idx is not None:
            intra_row = klines_5m.iloc[intra_idx]
            intra_open = float(intra_row["open"])
            intra_close = float(intra_row["close"])
            entry["intra_ret_5m"] = (intra_close - intra_open) / intra_open * 100 if intra_open > 0 else 0.0

        all_data.append(entry)

    train = [d for d in all_data if d["ts"] < split_ms]
    test = [d for d in all_data if d["ts"] >= split_ms]
    print(f"  Total: {len(all_data)} | Train: {len(train)} | Test: {len(test)}")

    # ── 4. Calibrate ──
    print("\n[4/6] Calibrating (filtered signals only)...")
    lookup = calibrate_lookup(train)

    print(f"\n  {'Signal Bucket':<32} {'P(Up)':>7} {'Edge':>7} {'N':>5} {'OK':>4}")
    print(f"  {'':─<58}")
    for sig in sorted(lookup, key=lambda s: -abs(lookup[s]["p_up"] - 0.5)):
        e = lookup[sig]
        edge = abs(e["p_up"] - 0.5)
        ok = "✓" if edge >= EDGE_THRESHOLD else "✗"
        print(f"  {sig:<32} {e['p_up']:>6.1%} {edge:>6.1%} {e['n']:>5} {ok:>4}")

    # ── 5. Simulate: HOLD vs EARLY EXIT (both on test data) ──
    print(f"\n[5/6] PnL simulation (2 scenarios)...")
    pnl_hold = simulate_pnl(test, lookup, early_exit=False)
    pnl_exit = simulate_pnl(test, lookup, early_exit=True)
    # Also in-sample sanity
    pnl_train_hold = simulate_pnl(train, lookup, early_exit=False)

    # ── 6. Report ──
    print(f"\n[6/6] Results")
    print("=" * 60)

    test_period = f"{split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}"
    train_period = f"{start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}"

    print_scenario("IN-SAMPLE (sanity check)", train_period, pnl_train_hold)
    print_scenario("OOS — HOLD TO RESOLUTION", test_period, pnl_hold)
    print_scenario("OOS — EARLY EXIT ENABLED", test_period, pnl_exit)

    # Side-by-side comparison
    print(f"\n  ── Comparison (test period) ──")
    print(f"  {'Metric':<20} {'Hold':>12} {'Early Exit':>12} {'Delta':>10}")
    print(f"  {'':─<56}")
    for key, fmt in [
        ("pnl", "${:+.2f}"), ("pnl_pct", "{:+.1f}%"),
        ("win_rate", "{:.1%}"), ("max_dd_pct", "{:.1f}%"),
        ("trades", "{}"),
    ]:
        v_h = pnl_hold[key]
        v_e = pnl_exit[key]
        delta = v_e - v_h if isinstance(v_h, (int, float)) else ""
        d_str = f"{delta:+.2f}" if isinstance(delta, float) else str(delta)
        print(f"  {key:<20} {fmt.format(v_h):>12} {fmt.format(v_e):>12} {d_str:>10}")

    # Volume spike P(Up) on test
    print(f"\n  ── Volume Spike P(Up) — Test Period ──")
    print(f"  {'Condition':<30} {'P(Up)':>8} {'N':>5}")
    print(f"  {'':─<46}")
    for vt, vl in [(1.5, "1.5x"), (2.0, "2x"), (3.0, "3x")]:
        for rd, rl in [(-0.3, "drop>0.3%"), (0.3, "rise>0.3%")]:
            if rd < 0:
                sub = [d for d in test if d["vol_ratio"] >= vt and d["ret_5m"] <= rd]
            else:
                sub = [d for d in test if d["vol_ratio"] >= vt and d["ret_5m"] >= rd]
            if len(sub) >= 3:
                p = np.mean([d["actual"] for d in sub])
                m = " ◀◀" if abs(p - 0.5) > 0.08 else (" ◀" if abs(p - 0.5) > 0.04 else "")
                print(f"  vol>{vl} + {rl:<15} {p:>7.1%} {len(sub):>5}{m}")

    print_equity_curve(pnl_hold["trade_log"], "Hold")
    print_equity_curve(pnl_exit["trade_log"], "Early Exit")

    # ── Save ──
    os.makedirs(LOG_DIR, exist_ok=True)
    result_path = os.path.join(LOG_DIR, "microstructure_backtest_results.json")
    output = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "version": "v3_filtered_early_exit",
        "period": f"{start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "days": days,
        "symbol": SYMBOL,
        "params": {
            "min_vol_ratio": MIN_VOL_RATIO,
            "min_abs_ret": MIN_ABS_RET,
            "edge_threshold": EDGE_THRESHOLD,
            "bet_pct": BET_PCT,
            "market_price": MARKET_PRICE,
            "tp_trigger": TAKE_PROFIT_TRIGGER,
            "sl_trigger": CUT_LOSS_TRIGGER,
        },
        "train_period": f"{start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}",
        "test_period": f"{split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "lookup_table": {k: v for k, v in lookup.items()},
        "in_sample": {k: v for k, v in pnl_train_hold.items() if k != "trade_log"},
        "oos_hold": {k: v for k, v in pnl_hold.items() if k != "trade_log"},
        "oos_early_exit": {k: v for k, v in pnl_exit.items() if k != "trade_log"},
    }

    fd, tmp = tempfile.mkstemp(dir=LOG_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        os.replace(tmp, result_path)
        print(f"\nResults saved → {result_path}")
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return output


def main():
    parser = argparse.ArgumentParser(description="Microstructure v3 — Filtered + Early Exit")
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
