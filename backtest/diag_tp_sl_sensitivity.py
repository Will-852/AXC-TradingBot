#!/usr/bin/env python3
"""
diag_tp_sl_sensitivity.py — TP/SL sensitivity analysis for Range LONG
Tests multiple combinations:
  TP: 100% to BB mid, 75%, 50%, 25%
  SL: 1.0×ATR, 1.2×ATR (current), 1.5×ATR, 2.0×ATR
  Lookahead: 10, 15, 20 candles

Uses same data and signal detection as diag_range_entry.py.
"""

import os
import sys
import numpy as np
import pandas as pd
from collections import defaultdict

sys.path.insert(0, os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, os.path.expanduser("~/projects/axc-trading/scripts"))

import tradingview_indicators as tv
from indicator_calc import (
    calc_atr, calc_obv, evaluate_range_signal,
    TIMEFRAME_PARAMS, PRODUCT_OVERRIDES,
    BB_WIDTH_MIN, SR_PROXIMITY_TOL,
    STOCH_K_PERIOD, STOCH_K_SMOOTH, STOCH_D_SMOOTH,
    OBV_EMA_PERIOD,
)

DATA_DIR = os.path.expanduser("~/projects/axc-trading/backtest/data")
WARMUP = 200

PAIRS = {
    "BTCUSDT": "BTCUSDT_1h_20250906_20260313.csv",
    "ETHUSDT": "ETHUSDT_1h_20250903_20260311.csv",
    "SOLUSDT": "SOLUSDT_1h_20250903_20260311.csv",
    "XRPUSDT": "XRPUSDT_1h_20260103_20260312.csv",
    "XAGUSDT": "XAGUSDT_1h_20250904_20260311.csv",
    "XAUUSDT": "XAUUSDT_1h_20250904_20260311.csv",
}

# Sensitivity grid
TP_FRACTIONS = [1.0, 0.75, 0.50, 0.25]  # fraction of distance to BB mid
SL_MULTS = [1.0, 1.2, 1.5, 2.0]          # ×ATR
LOOKAHEADS = [10, 15, 20]


def load_data(filename):
    path = os.path.join(DATA_DIR, filename)
    df = pd.read_csv(path)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_all_series(df, params):
    close, high, low = df["close"], df["high"], df["low"]
    bb = tv.bollinger_bands(close, params["bb_length"], params["bb_mult"])
    bb_width = (bb["upper"] - bb["lower"]) / bb["basis"]
    rsi = tv.RSI(close, params["rsi_period"])
    dmi = tv.DMI(df, "close")
    adx_series = dmi.adx()[0]
    atr = calc_atr(df, params["atr_period"])
    try:
        stoch_result = tv.slow_stoch(close, high, low, STOCH_K_PERIOD, STOCH_K_SMOOTH, STOCH_D_SMOOTH)
        stoch_k, stoch_d = stoch_result[0], stoch_result[1]
    except Exception:
        stoch_k = stoch_d = pd.Series([np.nan] * len(close))
    obv = calc_obv(df)
    obv_ema = tv.ema(obv, OBV_EMA_PERIOD)
    rolling_low = low.rolling(params["lookback_support"]).min()
    rolling_high = high.rolling(params["lookback_support"]).max()
    return {
        "close": close, "high": high, "low": low,
        "bb_upper": bb["upper"], "bb_basis": bb["basis"], "bb_lower": bb["lower"],
        "bb_width": bb_width, "rsi": rsi, "adx": adx_series,
        "atr": atr, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "obv": obv, "obv_ema": obv_ema,
        "rolling_low": rolling_low, "rolling_high": rolling_high,
        "volume": df["volume"],
    }


def safe(series, idx):
    try:
        v = series.iloc[idx] if isinstance(idx, int) and idx < 0 else series.loc[idx]
        return None if pd.isna(v) else round(float(v), 6)
    except Exception:
        return None


def build_ind(s, i):
    return {
        "price": safe(s["close"], i), "high": safe(s["high"], i),
        "low": safe(s["low"], i), "volume": safe(s["volume"], i),
        "bb_upper": safe(s["bb_upper"], i), "bb_basis": safe(s["bb_basis"], i),
        "bb_lower": safe(s["bb_lower"], i), "bb_width": safe(s["bb_width"], i),
        "rsi": safe(s["rsi"], i), "rsi_prev": safe(s["rsi"], i - 1),
        "adx": safe(s["adx"], i), "atr": safe(s["atr"], i),
        "stoch_k": safe(s["stoch_k"], i), "stoch_d": safe(s["stoch_d"], i),
        "stoch_k_prev": safe(s["stoch_k"], i - 1), "stoch_d_prev": safe(s["stoch_d"], i - 1),
        "obv": safe(s["obv"], i), "obv_ema": safe(s["obv_ema"], i),
        "rolling_low": safe(s["rolling_low"], i), "rolling_high": safe(s["rolling_high"], i),
    }


def check_outcome_matrix(s, signal_idx):
    """For each TP/SL/lookahead combo, check outcome. Returns dict of results."""
    entry_price = safe(s["close"], signal_idx)
    atr_val = safe(s["atr"], signal_idx)
    bb_mid = safe(s["bb_basis"], signal_idx)
    max_idx = len(s["close"]) - 1

    if entry_price is None or atr_val is None or bb_mid is None or atr_val == 0:
        return None

    tp_distance = bb_mid - entry_price  # positive for LONG in range (price near lower, mid is above)

    results = {}
    for sl_mult in SL_MULTS:
        sl = entry_price - sl_mult * atr_val
        for tp_frac in TP_FRACTIONS:
            tp = entry_price + tp_distance * tp_frac
            for lookahead in LOOKAHEADS:
                key = (tp_frac, sl_mult, lookahead)
                outcome = "timeout"
                max_drawdown = 0.0
                max_profit = 0.0

                for j in range(signal_idx + 1, min(signal_idx + lookahead + 1, max_idx + 1)):
                    h = safe(s["high"], j)
                    l = safe(s["low"], j)
                    if h is None or l is None:
                        continue
                    drawdown = (entry_price - l) / entry_price
                    profit = (h - entry_price) / entry_price
                    max_drawdown = max(max_drawdown, drawdown)
                    max_profit = max(max_profit, profit)

                    if l <= sl:
                        outcome = "SL"
                        break
                    if h >= tp:
                        outcome = "TP"
                        break

                rr = (tp_distance * tp_frac) / (sl_mult * atr_val) if sl_mult * atr_val > 0 else 0
                results[key] = {
                    "outcome": outcome,
                    "rr": round(rr, 2),
                    "max_dd_pct": round(max_drawdown * 100, 2),
                    "max_profit_pct": round(max_profit * 100, 2),
                    "entry": entry_price,
                    "tp": round(tp, 4),
                    "sl": round(sl, 4),
                }
    return results


def collect_signals(symbol, filename):
    """Find all LONG signals and return their outcomes matrix."""
    df = load_data(filename)
    diag_days = int(os.environ.get("DIAG_DAYS", 60))
    target_candles = diag_days * 24 + WARMUP
    if len(df) > target_candles:
        df = df.iloc[-target_candles:].reset_index(drop=True)

    params = TIMEFRAME_PARAMS["1h"].copy()
    if symbol in PRODUCT_OVERRIDES:
        params.update(PRODUCT_OVERRIDES[symbol])

    s = compute_all_series(df, params)
    max_lookahead = max(LOOKAHEADS)
    start_idx = WARMUP
    end_idx = len(df) - max_lookahead

    signals = []
    for i in range(start_idx, end_idx):
        ind = build_ind(s, i)
        if ind["price"] is None or ind["bb_width"] is None:
            continue
        result = evaluate_range_signal(ind, params)
        if result["signal_long"] == 1:
            matrix = check_outcome_matrix(s, i)
            if matrix:
                strength = "STRONG" if any("STRONG" in r for r in result["reasons"]) else "WEAK"
                signals.append({
                    "idx": i, "symbol": symbol, "strength": strength,
                    "entry": ind["price"], "bb_mid": ind["bb_basis"],
                    "atr": ind["atr"], "bb_width": ind["bb_width"],
                    "matrix": matrix,
                })
    return signals


def print_report(all_signals):
    print("=" * 90)
    print("TP/SL Sensitivity Analysis — Range LONG — 60 Day / 1H")
    print("=" * 90)

    # Signal inventory
    print(f"\nTotal signals: {len(all_signals)}")
    for sig in all_signals:
        dist_to_mid = sig["bb_mid"] - sig["entry"]
        dist_pct = dist_to_mid / sig["entry"] * 100
        print(f"  {sig['symbol']:<10} entry={sig['entry']:<10.2f} BB_mid={sig['bb_mid']:<10.2f} "
              f"dist={dist_pct:>+5.2f}%  ATR={sig['atr']:<8.2f} "
              f"bb_w={sig['bb_width']:.4f}  {sig['strength']}")

    # Main heatmap: TP fraction × SL mult (fixed lookahead=10, then 15, 20)
    for lookahead in LOOKAHEADS:
        print(f"\n{'=' * 90}")
        print(f"Lookahead = {lookahead} candles ({lookahead}H)")
        print(f"{'=' * 90}")

        # Header
        print(f"\n  {'TP \\ SL':>12}", end="")
        for sl in SL_MULTS:
            print(f"  {'SL=' + str(sl) + '×ATR':>14}", end="")
        print()
        print("  " + "-" * 72)

        for tp_frac in TP_FRACTIONS:
            tp_label = f"TP={int(tp_frac*100)}%→mid"
            print(f"  {tp_label:>12}", end="")

            for sl in SL_MULTS:
                key = (tp_frac, sl, lookahead)
                tp_count = sum(1 for sig in all_signals if sig["matrix"][key]["outcome"] == "TP")
                sl_count = sum(1 for sig in all_signals if sig["matrix"][key]["outcome"] == "SL")
                to_count = sum(1 for sig in all_signals if sig["matrix"][key]["outcome"] == "timeout")
                total = len(all_signals)
                wr = (tp_count / total * 100) if total > 0 else 0

                # Average R:R for this combo
                avg_rr = np.mean([sig["matrix"][key]["rr"] for sig in all_signals]) if all_signals else 0

                cell = f"{tp_count}W/{sl_count}L/{to_count}T {wr:.0f}%"
                print(f"  {cell:>14}", end="")
            print()

        # R:R row
        print(f"\n  {'R:R ratio':>12}", end="")
        for sl in SL_MULTS:
            key = (1.0, sl, lookahead)  # use TP=100% for R:R display
            avg_rr = np.mean([sig["matrix"][key]["rr"] for sig in all_signals]) if all_signals else 0
            print(f"  {avg_rr:>14.2f}", end="")
        print()

    # Per-signal detail with best combo
    print(f"\n{'=' * 90}")
    print("Per-Signal Best Outcome (across all combos)")
    print(f"{'=' * 90}")

    for sig in all_signals:
        best_key = None
        best_outcome = None
        for key, res in sig["matrix"].items():
            if res["outcome"] == "TP":
                if best_key is None or key[0] > best_key[0]:  # prefer higher TP frac
                    best_key = key
                    best_outcome = res

        if best_outcome:
            tp_f, sl_m, la = best_key
            print(f"  {sig['symbol']:<10} entry={sig['entry']:<10.2f} → TP at {int(tp_f*100)}%→mid "
                  f"SL={sl_m}×ATR LA={la}H  R:R={best_outcome['rr']}")
        else:
            # Find combo with least drawdown
            min_dd = 999
            min_key = None
            for key, res in sig["matrix"].items():
                if res["max_dd_pct"] < min_dd:
                    min_dd = res["max_dd_pct"]
                    min_key = key
            print(f"  {sig['symbol']:<10} entry={sig['entry']:<10.2f} → NO TP in any combo. "
                  f"Max drawdown={min_dd:.2f}%  Max profit={sig['matrix'][min_key]['max_profit_pct']:.2f}%")

    # Drawdown analysis
    print(f"\n{'=' * 90}")
    print("Drawdown + Max Profit 分析（每個信號入場後 20H 內）")
    print(f"{'=' * 90}")
    for sig in all_signals:
        # Use widest SL (2.0) and longest lookahead (20) to see full picture
        key = (1.0, 2.0, 20)
        res = sig["matrix"][key]
        print(f"  {sig['symbol']:<10} entry={sig['entry']:<10.2f} "
              f"max_dd={res['max_dd_pct']:>5.2f}%  max_profit={res['max_profit_pct']:>5.2f}%  "
              f"outcome={res['outcome']}")

    # Summary recommendation
    print(f"\n{'=' * 90}")
    print("建議摘要")
    print(f"{'=' * 90}")

    # Find best combo overall
    best_wr = 0
    best_combo = None
    for tp_frac in TP_FRACTIONS:
        for sl in SL_MULTS:
            for la in LOOKAHEADS:
                key = (tp_frac, sl, la)
                tp_count = sum(1 for sig in all_signals if sig["matrix"][key]["outcome"] == "TP")
                sl_count = sum(1 for sig in all_signals if sig["matrix"][key]["outcome"] == "SL")
                total = tp_count + sl_count
                wr = (tp_count / total * 100) if total > 0 else 0
                # Weight by: WR and also consider timeout as neutral
                if wr > best_wr or (wr == best_wr and best_combo and tp_frac > best_combo[0]):
                    best_wr = wr
                    best_combo = key

    if best_combo:
        tp_f, sl_m, la = best_combo
        tp_c = sum(1 for sig in all_signals if sig["matrix"][best_combo]["outcome"] == "TP")
        sl_c = sum(1 for sig in all_signals if sig["matrix"][best_combo]["outcome"] == "SL")
        to_c = sum(1 for sig in all_signals if sig["matrix"][best_combo]["outcome"] == "timeout")
        print(f"  Best combo: TP={int(tp_f*100)}%→mid  SL={sl_m}×ATR  Lookahead={la}H")
        print(f"  Results: {tp_c}W / {sl_c}L / {to_c}T  WR={best_wr:.0f}%")
    else:
        print("  No winning combo found.")

    # Count how many signals EVER reach various profit levels
    print(f"\n  Price ever reaches (within 20H of entry):")
    for pct in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
        key = (1.0, 99.0, 20) if (1.0, 99.0, 20) in all_signals[0]["matrix"] else (1.0, 2.0, 20)
        count = sum(1 for sig in all_signals if sig["matrix"][(1.0, 2.0, 20)]["max_profit_pct"] >= pct)
        print(f"    +{pct:.2f}%: {count}/{len(all_signals)} signals ({count/len(all_signals)*100:.0f}%)")


def main():
    all_signals = []
    for symbol, filename in PAIRS.items():
        print(f"Processing {symbol}...", flush=True)
        try:
            sigs = collect_signals(symbol, filename)
            all_signals.extend(sigs)
        except Exception as e:
            print(f"  ERROR: {e}")

    if not all_signals:
        print("\nNo signals found. Cannot run sensitivity analysis.")
        return

    print_report(all_signals)


if __name__ == "__main__":
    main()
