#!/usr/bin/env python3
"""
diag_range_entry.py — Range LONG 入場條件 60 日 diagnostic
診斷 4 個提議改動嘅影響：
  1. C1+C3 相關性（BB lower ≈ rolling low）
  2. C2 單根 RSI 回升 false rate
  3. 低量信號比例
  4. BB_WIDTH_MIN 分佈

用法: python3 backtest/diag_range_entry.py
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, os.path.expanduser("~/projects/axc-trading/scripts"))

import tradingview_indicators as tv
from indicator_calc import (
    calc_atr, calc_obv, evaluate_range_signal,
    TIMEFRAME_PARAMS, PRODUCT_OVERRIDES,
    BB_WIDTH_MIN, SR_PROXIMITY_TOL,
    STOCH_K_PERIOD, STOCH_K_SMOOTH, STOCH_D_SMOOTH,
    STOCH_OVERSOLD, STOCH_OVERBOUGHT,
    OBV_EMA_PERIOD,
)

DATA_DIR = os.path.expanduser("~/projects/axc-trading/backtest/data")
WARMUP = 200  # candles for indicator warmup
LOOKAHEAD = 10  # candles to check outcome after signal

# Pairs and their data files (most recent ~68d+ coverage)
PAIRS = {
    "BTCUSDT":  "BTCUSDT_1h_20250906_20260313.csv",
    "ETHUSDT":  "ETHUSDT_1h_20250903_20260311.csv",
    "SOLUSDT":  "SOLUSDT_1h_20250903_20260311.csv",
    "XRPUSDT":  "XRPUSDT_1h_20260103_20260312.csv",
    "XAGUSDT":  "XAGUSDT_1h_20250904_20260311.csv",
    "XAUUSDT":  "XAUUSDT_1h_20250904_20260311.csv",
}


def load_data(filename: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, filename)
    df = pd.read_csv(path)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_all_series(df: pd.DataFrame, params: dict) -> dict:
    """Compute full indicator series (not just last candle)."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    bb = tv.bollinger_bands(close, params["bb_length"], params["bb_mult"])
    bb_upper = bb["upper"]
    bb_basis = bb["basis"]
    bb_lower = bb["lower"]
    bb_width = (bb_upper - bb_lower) / bb_basis

    rsi = tv.RSI(close, params["rsi_period"])

    dmi = tv.DMI(df, "close")
    adx_tuple = dmi.adx()
    adx_series = adx_tuple[0]

    atr = calc_atr(df, params["atr_period"])

    try:
        stoch_result = tv.slow_stoch(close, high, low, STOCH_K_PERIOD, STOCH_K_SMOOTH, STOCH_D_SMOOTH)
        stoch_k = stoch_result[0]
        stoch_d = stoch_result[1]
    except Exception:
        stoch_k = pd.Series([np.nan] * len(close))
        stoch_d = pd.Series([np.nan] * len(close))

    obv = calc_obv(df)
    obv_ema = tv.ema(obv, OBV_EMA_PERIOD)

    rolling_low = low.rolling(params["lookback_support"]).min()
    rolling_high = high.rolling(params["lookback_support"]).max()

    vol_ma = df["volume"].rolling(20).mean()

    return {
        "close": close, "high": high, "low": low,
        "bb_upper": bb_upper, "bb_basis": bb_basis, "bb_lower": bb_lower,
        "bb_width": bb_width, "rsi": rsi, "adx": adx_series,
        "atr": atr, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "obv": obv, "obv_ema": obv_ema,
        "rolling_low": rolling_low, "rolling_high": rolling_high,
        "vol_ma": vol_ma, "volume": df["volume"],
    }


def safe(series, idx):
    try:
        v = series.iloc[idx] if isinstance(idx, int) and idx < 0 else series.loc[idx]
        return None if pd.isna(v) else round(float(v), 6)
    except Exception:
        return None


def build_ind(s: dict, i: int) -> dict:
    """Build indicator dict for candle i (same structure as calc_indicators output)."""
    return {
        "price": safe(s["close"], i),
        "high": safe(s["high"], i),
        "low": safe(s["low"], i),
        "volume": safe(s["volume"], i),
        "bb_upper": safe(s["bb_upper"], i),
        "bb_basis": safe(s["bb_basis"], i),
        "bb_lower": safe(s["bb_lower"], i),
        "bb_width": safe(s["bb_width"], i),
        "rsi": safe(s["rsi"], i),
        "rsi_prev": safe(s["rsi"], i - 1),
        "adx": safe(s["adx"], i),
        "atr": safe(s["atr"], i),
        "stoch_k": safe(s["stoch_k"], i),
        "stoch_d": safe(s["stoch_d"], i),
        "stoch_k_prev": safe(s["stoch_k"], i - 1),
        "stoch_d_prev": safe(s["stoch_d"], i - 1),
        "obv": safe(s["obv"], i),
        "obv_ema": safe(s["obv_ema"], i),
        "rolling_low": safe(s["rolling_low"], i),
        "rolling_high": safe(s["rolling_high"], i),
    }


def check_outcome(s: dict, signal_idx: int, direction: str) -> dict:
    """Check if price reached TP (BB mid) before SL (1.2×ATR) within LOOKAHEAD candles."""
    entry_price = safe(s["close"], signal_idx)
    atr_val = safe(s["atr"], signal_idx)
    bb_mid = safe(s["bb_basis"], signal_idx)
    if entry_price is None or atr_val is None or bb_mid is None or atr_val == 0:
        return {"outcome": "unknown"}

    sl_dist = 1.2 * atr_val
    sl = entry_price - sl_dist if direction == "LONG" else entry_price + sl_dist
    tp = bb_mid

    max_idx = len(s["close"]) - 1
    for j in range(signal_idx + 1, min(signal_idx + LOOKAHEAD + 1, max_idx + 1)):
        h = safe(s["high"], j)
        l = safe(s["low"], j)
        if h is None or l is None:
            continue
        if direction == "LONG":
            if l <= sl:
                return {"outcome": "SL", "bars": j - signal_idx}
            if h >= tp:
                return {"outcome": "TP", "bars": j - signal_idx}
        else:
            if h >= sl:
                return {"outcome": "SL", "bars": j - signal_idx}
            if l <= tp:
                return {"outcome": "TP", "bars": j - signal_idx}

    return {"outcome": "timeout"}


def run_diagnostic(symbol: str, filename: str):
    """Run full diagnostic for one pair."""
    df = load_data(filename)

    # Take last N days + warmup (default 60, override via env DIAG_DAYS)
    diag_days = int(os.environ.get("DIAG_DAYS", 60))
    target_candles = diag_days * 24 + WARMUP
    if len(df) > target_candles:
        df = df.iloc[-target_candles:].reset_index(drop=True)

    params = TIMEFRAME_PARAMS["1h"].copy()
    if symbol in PRODUCT_OVERRIDES:
        params.update(PRODUCT_OVERRIDES[symbol])

    s = compute_all_series(df, params)
    start_idx = WARMUP
    end_idx = len(df) - LOOKAHEAD

    # Stats
    stats = {
        "total_candles": end_idx - start_idx,
        "r0_blocked": 0, "r0_squeeze": 0, "r1_blocked": 0,
        "range_valid": 0,
        "long_signals": 0, "long_weak": 0, "long_strong": 0,
        "short_signals": 0,
        # C1+C3 correlation
        "c1_pass": 0, "c3_pass": 0, "c1_and_c3": 0,
        # C2 false reversal (RSI went back down next bar)
        "c2_pass": 0, "c2_false_next": 0,
        # Volume
        "low_vol_signals": 0,  # vol_ratio < 0.5
        # BB width at signal time
        "bb_widths_at_signal": [],
        # Outcomes
        "tp_hit": 0, "sl_hit": 0, "timeout": 0, "unknown": 0,
        # 2-bar RSI test
        "c2_2bar_pass": 0,
        "long_signals_2bar": 0,
    }

    for i in range(start_idx, end_idx):
        ind = build_ind(s, i)
        if ind["price"] is None or ind["bb_width"] is None:
            continue

        tol = params["bb_touch_tol"]

        # Gate tracking
        if ind["bb_width"] >= BB_WIDTH_MIN:
            stats["r0_blocked"] += 1
            continue
        squeeze_min = params.get("bb_width_squeeze", 0)
        if squeeze_min and ind["bb_width"] <= squeeze_min:
            stats["r0_squeeze"] += 1
            continue
        if ind["adx"] is None or ind["adx"] >= params["adx_range_max"]:
            stats["r1_blocked"] += 1
            continue

        stats["range_valid"] += 1

        # Individual condition tracking (LONG side)
        price = ind["price"]
        c1 = price <= ind["bb_lower"] * (1 + tol) if ind["bb_lower"] else False
        c2 = (ind["rsi"] is not None and ind["rsi_prev"] is not None and
              ind["rsi"] < params["rsi_long"] and ind["rsi"] > ind["rsi_prev"])
        c3 = (ind["rolling_low"] is not None and
              price <= ind["rolling_low"] * (1 + SR_PROXIMITY_TOL)) if ind["rolling_low"] else False

        if c1:
            stats["c1_pass"] += 1
        if c3:
            stats["c3_pass"] += 1
        if c1 and c3:
            stats["c1_and_c3"] += 1

        # C2 false reversal check
        if c2:
            stats["c2_pass"] += 1
            rsi_next = safe(s["rsi"], i + 1)
            if rsi_next is not None and ind["rsi"] is not None and rsi_next < ind["rsi"]:
                stats["c2_false_next"] += 1

        # 2-bar RSI test: rsi > rsi_prev AND rsi_prev > rsi_prev2
        rsi_prev2 = safe(s["rsi"], i - 2)
        c2_2bar = (c2 and ind["rsi_prev"] is not None and rsi_prev2 is not None and
                   ind["rsi_prev"] > rsi_prev2)
        if c2_2bar:
            stats["c2_2bar_pass"] += 1

        # Run actual signal evaluation
        result = evaluate_range_signal(ind, params)

        if result["signal_long"] == 1:
            stats["long_signals"] += 1
            if any("STRONG" in r for r in result["reasons"]):
                stats["long_strong"] += 1
            else:
                stats["long_weak"] += 1

            stats["bb_widths_at_signal"].append(ind["bb_width"])

            # Volume check
            vol_ma = safe(s["vol_ma"], i)
            vol = ind["volume"]
            if vol_ma and vol and vol_ma > 0:
                vol_ratio = vol / vol_ma
                if vol_ratio < 0.5:
                    stats["low_vol_signals"] += 1

            # Outcome
            oc = check_outcome(s, i, "LONG")
            stats[oc["outcome"] + ("_hit" if oc["outcome"] in ("tp", "sl") else "")] = \
                stats.get(oc["outcome"] + ("_hit" if oc["outcome"] in ("tp", "sl") else ""), 0)
            if oc["outcome"] == "TP":
                stats["tp_hit"] += 1
            elif oc["outcome"] == "SL":
                stats["sl_hit"] += 1
            elif oc["outcome"] == "timeout":
                stats["timeout"] += 1
            else:
                stats["unknown"] += 1

            # Would 2-bar RSI filter have caught this?
            if c1 and c2_2bar and c3:
                stats["long_signals_2bar"] += 1

        if result["signal_short"] == -1:
            stats["short_signals"] += 1

    return stats


def print_report(all_stats: dict):
    print("=" * 80)
    print("Range LONG Entry Diagnostic — 60 Day / 1H")
    print("=" * 80)

    # Per-pair summary
    print(f"\n{'Pair':<10} {'Candles':>8} {'R0 Blk':>7} {'Sqz':>5} {'R1 Blk':>7} "
          f"{'Valid':>6} {'LONG':>5} {'W/S':>6} {'TP':>4} {'SL':>4} {'T/O':>4} {'WR%':>6}")
    print("-" * 80)

    totals = {}
    for sym, st in all_stats.items():
        total_sig = st["long_signals"]
        wr = (st["tp_hit"] / total_sig * 100) if total_sig > 0 else 0
        print(f"{sym:<10} {st['total_candles']:>8} {st['r0_blocked']:>7} {st['r0_squeeze']:>5} "
              f"{st['r1_blocked']:>7} {st['range_valid']:>6} {total_sig:>5} "
              f"{st['long_weak']}/{st['long_strong']:>2} {st['tp_hit']:>4} {st['sl_hit']:>4} "
              f"{st['timeout']:>4} {wr:>5.1f}%")
        for k, v in st.items():
            if k != "bb_widths_at_signal":
                totals[k] = totals.get(k, 0) + v

    print("-" * 80)
    total_sig = totals.get("long_signals", 0)
    wr = (totals.get("tp_hit", 0) / total_sig * 100) if total_sig > 0 else 0
    print(f"{'TOTAL':<10} {totals.get('total_candles',0):>8} {totals.get('r0_blocked',0):>7} "
          f"{totals.get('r0_squeeze',0):>5} {totals.get('r1_blocked',0):>7} "
          f"{totals.get('range_valid',0):>6} {total_sig:>5} "
          f"{totals.get('long_weak',0)}/{totals.get('long_strong',0):>2} "
          f"{totals.get('tp_hit',0):>4} {totals.get('sl_hit',0):>4} "
          f"{totals.get('timeout',0):>4} {wr:>5.1f}%")

    # === Diagnostic 1: C1+C3 correlation ===
    print("\n" + "=" * 80)
    print("Diagnostic 1: C1 + C3 相關性（BB lower ≈ Rolling Low）")
    print("-" * 80)
    for sym, st in all_stats.items():
        c1 = st["c1_pass"]
        c3 = st["c3_pass"]
        both = st["c1_and_c3"]
        overlap = (both / c1 * 100) if c1 > 0 else 0
        print(f"  {sym:<10} C1={c1:>4}  C3={c3:>4}  Both={both:>4}  "
              f"Overlap(C1→C3)={overlap:>5.1f}%")

    # === Diagnostic 2: C2 single-bar RSI false reversal ===
    print("\n" + "=" * 80)
    print("Diagnostic 2: C2 單根 RSI 回升 false rate（下一根又跌）")
    print("-" * 80)
    for sym, st in all_stats.items():
        c2 = st["c2_pass"]
        false_r = st["c2_false_next"]
        rate = (false_r / c2 * 100) if c2 > 0 else 0
        print(f"  {sym:<10} C2 pass={c2:>4}  Next bar down={false_r:>4}  False rate={rate:>5.1f}%")

    # === Diagnostic 3: 2-bar RSI filter impact ===
    print("\n" + "=" * 80)
    print("Diagnostic 3: 2-bar RSI 過濾器影響（C2 改成連續 2 根回升）")
    print("-" * 80)
    for sym, st in all_stats.items():
        orig = st["long_signals"]
        filtered = st["long_signals_2bar"]
        removed = orig - filtered
        pct = (removed / orig * 100) if orig > 0 else 0
        print(f"  {sym:<10} Original={orig:>3}  2-bar={filtered:>3}  "
              f"Removed={removed:>3} ({pct:.0f}%)")

    # === Diagnostic 4: Low volume signals ===
    print("\n" + "=" * 80)
    print("Diagnostic 4: 低量信號（volume_ratio < 0.5）")
    print("-" * 80)
    for sym, st in all_stats.items():
        total = st["long_signals"]
        low_v = st["low_vol_signals"]
        pct = (low_v / total * 100) if total > 0 else 0
        print(f"  {sym:<10} Signals={total:>3}  Low vol={low_v:>3}  ({pct:.0f}%)")

    # === Diagnostic 5: BB Width distribution at signal time ===
    print("\n" + "=" * 80)
    print("Diagnostic 5: BB Width 分佈 @ 信號時刻")
    print("-" * 80)
    all_widths = []
    for sym, st in all_stats.items():
        ws = st["bb_widths_at_signal"]
        all_widths.extend(ws)
        if ws:
            arr = np.array(ws)
            print(f"  {sym:<10} n={len(ws):>3}  "
                  f"min={arr.min():.4f}  p25={np.percentile(arr,25):.4f}  "
                  f"med={np.median(arr):.4f}  p75={np.percentile(arr,75):.4f}  "
                  f"max={arr.max():.4f}")
        else:
            print(f"  {sym:<10} n=  0  (no signals)")

    if all_widths:
        arr = np.array(all_widths)
        print(f"  {'ALL':<10} n={len(arr):>3}  "
              f"min={arr.min():.4f}  p25={np.percentile(arr,25):.4f}  "
              f"med={np.median(arr):.4f}  p75={np.percentile(arr,75):.4f}  "
              f"max={arr.max():.4f}")

    # === Gate analysis ===
    print("\n" + "=" * 80)
    print("Gate 阻擋率分析")
    print("-" * 80)
    for sym, st in all_stats.items():
        total = st["total_candles"]
        r0 = st["r0_blocked"]
        sqz = st["r0_squeeze"]
        r1 = st["r1_blocked"]
        valid = st["range_valid"]
        print(f"  {sym:<10} R0_wide={r0/total*100:>5.1f}%  R0_squeeze={sqz/total*100:>4.1f}%  "
              f"R1_adx={r1/total*100:>5.1f}%  Valid={valid/total*100:>5.1f}%")


def main():
    all_stats = {}
    for symbol, filename in PAIRS.items():
        print(f"Processing {symbol}...", flush=True)
        try:
            all_stats[symbol] = run_diagnostic(symbol, filename)
        except Exception as e:
            print(f"  ERROR: {e}")

    print_report(all_stats)


if __name__ == "__main__":
    main()
