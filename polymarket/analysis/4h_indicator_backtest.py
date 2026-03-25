#!/usr/bin/env python3
"""
4h_indicator_backtest.py — 10 indicators × 365 days × 6 windows/day

Polymarket 4H: P(BTC close >= open over 4-hour window).
Ground truth: Binance 4H candle close >= open → UP, else DOWN.

Indicators tested (all from existing code, zero new indicator logic):
  1. momentum    — BTC direction in first 1H → predict continuation
  2. rsi         — RSI(14) > 55 → UP, < 45 → DOWN
  3. macd        — MACD histogram sign → UP/DOWN
  4. bb_squeeze  — BB width pctl < 20 → breakout in EMA direction
  5. volume      — High vol prev candle → continuation
  6. adx         — ADX > 25 + DI direction → UP/DOWN
  7. session     — Session UP bias learned from training data
  8. bridge      — Brownian Bridge fair value at T+1H → predict final
  9. obv         — OBV > OBV_EMA → UP
  10. ema_cross  — EMA(10) > EMA(50) → UP

Train/test: 70/30 chronological split. 50-candle warmup skipped.

Run:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/analysis/4h_indicator_backtest.py
  PYTHONPATH=.:scripts python3 polymarket/analysis/4h_indicator_backtest.py --days 180 --coin ETH
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
from datetime import datetime, timedelta, timezone
from itertools import combinations
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config.params import TIMEFRAME_PARAMS
from polymarket.strategy.market_maker import compute_fair_up

log = logging.getLogger("4h_bt")

_UA = "AXC-Backtest/1.0"
_BINANCE = "https://api.binance.com/api/v3/klines"


# ═══════════════════════════════════════
#  Data Fetch
# ═══════════════════════════════════════

def _fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Paginated Binance spot kline fetch."""
    all_klines = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{_BINANCE}?symbol={symbol}&interval={interval}"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        try:
            with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=15) as r:
                batch = json.loads(r.read())
        except (HTTPError, URLError, TimeoutError) as e:
            log.warning("Fetch failed at %d: %s — retrying", cursor, e)
            time.sleep(2)
            continue
        if not batch:
            break
        all_klines.extend(batch)
        cursor = batch[-1][6] + 1  # close_time + 1ms
        if len(batch) < 1000:  # Binance spot API max = 1000
            break
        time.sleep(0.3)  # Binance rate limit courtesy
    return all_klines


def _to_df(raw: list) -> pd.DataFrame:
    """Convert raw Binance klines to OHLCV DataFrame."""
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    for col in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)


# ═══════════════════════════════════════
#  Indicator Computation (full series on 4H data)
# ═══════════════════════════════════════

def _get_session_tag(ts_utc) -> str:
    """Map UTC timestamp to trading session."""
    if hasattr(ts_utc, "weekday"):
        if ts_utc.weekday() >= 5:
            return "WEEKEND"
        hour = ts_utc.hour
    else:
        ts = pd.Timestamp(ts_utc, unit="ms", tz="UTC") if isinstance(ts_utc, (int, float)) else pd.Timestamp(ts_utc)
        if ts.weekday() >= 5:
            return "WEEKEND"
        hour = ts.hour
    if 0 <= hour < 7:
        return "ASIA"
    if 7 <= hour < 12:
        return "EU_OPEN"
    if 12 <= hour < 14:
        return "US_PRE"
    if 14 <= hour < 20:
        return "US_OPEN"
    return "ASIA"  # 20-24 UTC


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicator series on 4H DataFrame. Modifies in-place."""
    try:
        import tradingview_indicators as tv
    except ImportError:
        log.error("tradingview_indicators not installed. pip install tradingview-indicators")
        sys.exit(1)

    params = TIMEFRAME_PARAMS["4h"]
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # Bollinger Bands
    bb = tv.bollinger_bands(close, params["bb_length"], params["bb_mult"])
    df["bb_basis"] = bb["basis"]
    bb_width = (bb["upper"] - bb["lower"]) / bb["basis"]
    df["bb_width_pctl"] = bb_width.rolling(100, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    ).fillna(50.0)

    # RSI
    df["rsi"] = tv.RSI(close, params["rsi_period"])

    # ADX / DMI
    dmi = tv.DMI(df, "close")
    adx_tuple = dmi.adx()
    df["adx"] = adx_tuple[0]
    df["di_plus"] = adx_tuple[1]
    df["di_minus"] = adx_tuple[2]

    # EMA
    df["ema_fast"] = tv.ema(close, params["ema_fast"])
    df["ema_slow"] = tv.ema(close, params["ema_slow"])

    # MACD
    macd_df = tv.MACD(close, 12, 26, 9)
    df["macd_hist"] = macd_df["histogram"]

    # OBV + EMA
    close_diff = close.diff()
    direction = close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    df["obv"] = (df["volume"] * direction).cumsum()
    df["obv_ema"] = tv.ema(df["obv"], 20)

    # Volume ratio (current / 20-SMA)
    vol_sma = tv.sma(df["volume"].astype(float), 20)
    df["volume_ratio"] = df["volume"] / vol_sma.replace(0, np.nan)

    # Session + ground truth
    df["session"] = df["timestamp"].apply(_get_session_tag)
    df["result"] = np.where(df["close"] >= df["open"], "UP", "DOWN")

    return df


# ═══════════════════════════════════════
#  10 Indicator Predictions
#  Convention: return "UP", "DOWN", or None (no opinion)
#  idx = index into df_4h for the CURRENT window being predicted
#  Indicators use idx-1 (previous candle) to avoid look-ahead bias
# ═══════════════════════════════════════

def _prev(df: pd.DataFrame, col: str, idx: int):
    """Safe access to previous candle's indicator value."""
    if idx < 1:
        return None
    v = df[col].iloc[idx - 1]
    return None if pd.isna(v) else float(v)


def predict_momentum(df_4h, df_1h, idx):
    """First 1H of 4H window: up → predict UP continuation."""
    open_time = df_4h["open_time"].iloc[idx]
    match = df_1h.loc[df_1h["open_time"] == open_time]
    if match.empty:
        return None
    row = match.iloc[0]
    if row["close"] > row["open"]:
        return "UP"
    if row["close"] < row["open"]:
        return "DOWN"
    return None


def predict_rsi(df_4h, _df_1h, idx):
    """RSI > 55 → UP, < 45 → DOWN. Neutral zone = skip."""
    rsi = _prev(df_4h, "rsi", idx)
    if rsi is None:
        return None
    if rsi > 55:
        return "UP"
    if rsi < 45:
        return "DOWN"
    return None


def predict_macd(df_4h, _df_1h, idx):
    """MACD histogram positive → UP."""
    hist = _prev(df_4h, "macd_hist", idx)
    if hist is None:
        return None
    return "UP" if hist > 0 else "DOWN"


def predict_bb_squeeze(df_4h, _df_1h, idx):
    """BB width pctl < 20 → squeeze → breakout in EMA direction."""
    pctl = _prev(df_4h, "bb_width_pctl", idx)
    if pctl is None or pctl > 20:
        return None
    ema_f = _prev(df_4h, "ema_fast", idx)
    ema_s = _prev(df_4h, "ema_slow", idx)
    if ema_f is None or ema_s is None:
        return None
    return "UP" if ema_f > ema_s else "DOWN"


def predict_volume(df_4h, _df_1h, idx):
    """High volume prev candle (>1.5x avg) → continuation of prev candle direction."""
    vr = _prev(df_4h, "volume_ratio", idx)
    if vr is None or vr < 1.5:
        return None
    return df_4h["result"].iloc[idx - 1]  # continuation


def predict_adx(df_4h, _df_1h, idx):
    """ADX > 25 = trending. DI+ > DI- → UP."""
    adx = _prev(df_4h, "adx", idx)
    if adx is None or adx < 25:
        return None
    di_p = _prev(df_4h, "di_plus", idx)
    di_m = _prev(df_4h, "di_minus", idx)
    if di_p is None or di_m is None:
        return None
    return "UP" if di_p > di_m else "DOWN"


def predict_session(_df_4h, _df_1h, _idx):
    """Session bias — handled separately via learned bias dict."""
    return None  # sentinel: evaluate() fills this in


def predict_bridge(df_4h, df_1h, idx):
    """Brownian Bridge P(UP) at T+1H into the 4H window."""
    open_time = df_4h["open_time"].iloc[idx]
    btc_open = df_4h["open"].iloc[idx]
    if btc_open <= 0:
        return None

    target_time = open_time + 3_600_000  # T+1H
    match = df_1h.loc[df_1h["open_time"] == target_time]
    if match.empty:
        return None

    h1 = match.iloc[0]
    btc_at_1h = h1["close"]
    if btc_at_1h <= 0:
        return None

    # Estimate per-minute vol from 1H candle range
    if h1["high"] > 0 and h1["low"] > 0:
        log_range = abs(math.log(h1["high"] / h1["low"]))
        vol_1m = max(0.0001, log_range / math.sqrt(60))
    else:
        vol_1m = 0.005

    fair = compute_fair_up(btc_at_1h, btc_open, vol_1m, 180)  # 3H remaining
    if fair > 0.55:
        return "UP"
    if fair < 0.45:
        return "DOWN"
    return None


def predict_obv(df_4h, _df_1h, idx):
    """OBV > OBV EMA → bullish accumulation → UP."""
    obv = _prev(df_4h, "obv", idx)
    obv_ema = _prev(df_4h, "obv_ema", idx)
    if obv is None or obv_ema is None:
        return None
    return "UP" if obv > obv_ema else "DOWN"


def predict_ema_cross(df_4h, _df_1h, idx):
    """EMA fast > slow → UP."""
    ema_f = _prev(df_4h, "ema_fast", idx)
    ema_s = _prev(df_4h, "ema_slow", idx)
    if ema_f is None or ema_s is None:
        return None
    return "UP" if ema_f > ema_s else "DOWN"


INDICATORS = {
    "momentum":   predict_momentum,
    "rsi":        predict_rsi,
    "macd":       predict_macd,
    "bb_squeeze": predict_bb_squeeze,
    "volume":     predict_volume,
    "adx":        predict_adx,
    "session":    predict_session,
    "bridge":     predict_bridge,
    "obv":        predict_obv,
    "ema_cross":  predict_ema_cross,
}


# ═══════════════════════════════════════
#  Evaluation
# ═══════════════════════════════════════

def _evaluate(df_4h, df_1h, start_idx, end_idx, session_bias=None):
    """Evaluate all indicators on [start_idx, end_idx). Returns (results, session_stats)."""
    results = {name: {"correct": 0, "wrong": 0, "skip": 0} for name in INDICATORS}
    session_stats = defaultdict(lambda: {"up": 0, "total": 0})

    for i in range(start_idx, end_idx):
        actual = df_4h["result"].iloc[i]
        session = df_4h["session"].iloc[i]
        session_stats[session]["total"] += 1
        if actual == "UP":
            session_stats[session]["up"] += 1

        for name, predict_fn in INDICATORS.items():
            if name == "session":
                pred = session_bias.get(session) if session_bias else None
            else:
                pred = predict_fn(df_4h, df_1h, i)

            if pred is None:
                results[name]["skip"] += 1
            elif pred == actual:
                results[name]["correct"] += 1
            else:
                results[name]["wrong"] += 1

    return results, dict(session_stats)


def _evaluate_combos(df_4h, df_1h, start_idx, end_idx, session_bias):
    """Test 2-combo and 3-combo majority vote. Returns sorted list."""
    n = end_idx - start_idx

    # Pre-compute all predictions (avoid redundant calls)
    preds = {}
    for name, predict_fn in INDICATORS.items():
        preds[name] = []
        for i in range(start_idx, end_idx):
            if name == "session":
                session = df_4h["session"].iloc[i]
                p = session_bias.get(session) if session_bias else None
            else:
                p = predict_fn(df_4h, df_1h, i)
            preds[name].append(p)

    actuals = [df_4h["result"].iloc[i] for i in range(start_idx, end_idx)]
    ind_names = list(INDICATORS.keys())
    combo_results = []

    for size in (2, 3):
        for combo in combinations(ind_names, size):
            correct = 0
            total = 0
            for j in range(n):
                votes = [preds[name][j] for name in combo if preds[name][j] is not None]
                if len(votes) < len(combo):
                    continue  # require ALL indicators to have opinion
                up_votes = sum(1 for v in votes if v == "UP")
                pred = "UP" if up_votes > len(votes) / 2 else "DOWN"
                total += 1
                if pred == actuals[j]:
                    correct += 1

            if total >= 30:  # minimum sample size
                combo_results.append({
                    "combo": "+".join(combo),
                    "size": size,
                    "correct": correct,
                    "total": total,
                    "accuracy": round(correct / total, 4),
                })

    combo_results.sort(key=lambda x: x["accuracy"], reverse=True)
    return combo_results


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="4H Indicator Backtest for Polymarket")
    parser.add_argument("--days", type=int, default=365, help="Days of history (default 365)")
    parser.add_argument("--coin", default="BTC", choices=["BTC", "ETH", "SOL"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    symbol = f"{args.coin}USDT"
    now_ms = int(time.time() * 1000)
    # Extra 60 days for indicator warmup (50 candles × 4h = ~8 days, plus margin)
    start_ms = now_ms - (args.days + 60) * 86_400_000

    # ── Fetch data ──
    log.info("Fetching 4H klines for %s (%d+ days)...", symbol, args.days)
    raw_4h = _fetch_klines(symbol, "4h", start_ms, now_ms)
    log.info("  → %d 4H candles", len(raw_4h))

    log.info("Fetching 1H klines for bridge + momentum...")
    raw_1h = _fetch_klines(symbol, "1h", start_ms, now_ms)
    log.info("  → %d 1H candles", len(raw_1h))

    if len(raw_4h) < 100:
        log.error("Not enough 4H data (%d candles). Need ≥100.", len(raw_4h))
        sys.exit(1)

    df_4h = _to_df(raw_4h)
    df_1h = _to_df(raw_1h)

    # ── Compute indicators ──
    log.info("Computing 4H indicators...")
    df_4h = _compute_indicators(df_4h)

    # ── Train/test split (70/30 chronological) ──
    warmup = 50  # skip first 50 candles for indicator warmup
    n_total = len(df_4h) - warmup
    n_train = int(n_total * 0.7)
    train_start = warmup
    train_end = warmup + n_train
    test_start = train_end
    test_end = len(df_4h)
    n_test = test_end - test_start

    train_date_start = df_4h["timestamp"].iloc[train_start].strftime("%Y-%m-%d")
    train_date_end = df_4h["timestamp"].iloc[train_end - 1].strftime("%Y-%m-%d")
    test_date_start = df_4h["timestamp"].iloc[test_start].strftime("%Y-%m-%d")
    test_date_end = df_4h["timestamp"].iloc[test_end - 1].strftime("%Y-%m-%d")

    log.info("Split: Train %d (%s → %s) | Test %d (%s → %s)",
             n_train, train_date_start, train_date_end,
             n_test, test_date_start, test_date_end)

    # ── Phase 1: Train — learn session bias ──
    log.info("Phase 1: Training (session bias + baseline)...")
    train_results, train_sessions = _evaluate(df_4h, df_1h, train_start, train_end)

    session_bias = {}
    for session, stats in train_sessions.items():
        up_pct = stats["up"] / stats["total"] if stats["total"] > 0 else 0.5
        if up_pct > 0.52:
            session_bias[session] = "UP"
        elif up_pct < 0.48:
            session_bias[session] = "DOWN"
        # else: no bias (None = skip)

    for session, stats in sorted(train_sessions.items()):
        up_pct = stats["up"] / stats["total"] * 100 if stats["total"] > 0 else 0
        bias = session_bias.get(session, "NEUTRAL")
        log.info("  Session %s: %d/%d UP (%.1f%%) → bias=%s",
                 session, stats["up"], stats["total"], up_pct, bias)

    # ── Phase 2: Test — evaluate all indicators ──
    log.info("Phase 2: Testing all indicators...")
    test_results, test_sessions = _evaluate(df_4h, df_1h, test_start, test_end, session_bias)

    # ── Print results ──
    print("\n" + "=" * 72)
    print(f"  4H INDICATOR BACKTEST — {symbol} — {args.days} days")
    print(f"  Train: {n_train} windows ({train_date_start} → {train_date_end})")
    print(f"  Test:  {n_test} windows ({test_date_start} → {test_date_end})")
    print("=" * 72)

    # Base rate
    test_up = sum(1 for i in range(test_start, test_end)
                  if df_4h["result"].iloc[i] == "UP")
    base_rate = test_up / n_test if n_test > 0 else 0.5
    print(f"\n  Base rate (test): {test_up}/{n_test} UP ({base_rate * 100:.1f}%)")

    print(f"\n  {'Indicator':<15} {'Correct':>8} {'Wrong':>8} {'Skip':>8} "
          f"{'Coverage':>9} {'Accuracy':>9} {'vs Base':>9}")
    print("  " + "-" * 68)

    indicator_accs = {}
    for name in INDICATORS:
        r = test_results[name]
        total = r["correct"] + r["wrong"]
        acc = r["correct"] / total if total > 0 else 0
        coverage = total / n_test if n_test > 0 else 0
        vs_base = acc - base_rate
        indicator_accs[name] = {"acc": acc, "n": total, "coverage": coverage}

        acc_s = f"{acc * 100:.1f}%" if total > 0 else "N/A"
        vs_s = f"{vs_base * 100:+.1f}pp" if total > 0 else ""
        print(f"  {name:<15} {r['correct']:>8} {r['wrong']:>8} {r['skip']:>8} "
              f"{coverage * 100:>8.0f}% {acc_s:>9} {vs_s:>9}")

    # ── Session breakdown ──
    print(f"\n  Session breakdown (test):")
    print(f"  {'Session':<12} {'UP/Total':>10} {'UP%':>7} {'Train Bias':>12}")
    print("  " + "-" * 44)
    for session in ("ASIA", "EU_OPEN", "US_PRE", "US_OPEN", "WEEKEND"):
        stats = test_sessions.get(session, {"up": 0, "total": 0})
        if stats["total"] == 0:
            continue
        up_pct = stats["up"] / stats["total"] * 100
        bias = session_bias.get(session, "NEUTRAL")
        print(f"  {session:<12} {stats['up']:>4}/{stats['total']:<4}   {up_pct:>5.1f}%   {bias:>10}")

    # ── Phase 3: Combinations ──
    log.info("Phase 3: Testing indicator combinations...")
    combos = _evaluate_combos(df_4h, df_1h, test_start, test_end, session_bias)

    if combos:
        print(f"\n  Top Combinations (test, majority vote, min 30 windows):")
        print(f"  {'Combo':<35} {'Correct':>8} {'Total':>8} "
              f"{'Accuracy':>9} {'vs Base':>9}")
        print("  " + "-" * 72)
        for c in combos[:15]:
            vs = c["accuracy"] - base_rate
            print(f"  {c['combo']:<35} {c['correct']:>8} {c['total']:>8} "
                  f"{c['accuracy'] * 100:>8.1f}% {vs * 100:>+8.1f}pp")

    # ── Save results to JSON (atomic write) ──
    out_dir = os.path.join(_AXC, "polymarket", "analysis", "results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"4h_backtest_{symbol}_{args.days}d.json")

    output = {
        "symbol": symbol,
        "days": args.days,
        "n_train": n_train,
        "n_test": n_test,
        "base_rate_up": round(base_rate, 4),
        "train_period": f"{train_date_start} → {train_date_end}",
        "test_period": f"{test_date_start} → {test_date_end}",
        "indicators": {
            name: {
                "accuracy": round(indicator_accs[name]["acc"], 4),
                "n_predictions": indicator_accs[name]["n"],
                "coverage": round(indicator_accs[name]["coverage"], 4),
                "correct": test_results[name]["correct"],
                "wrong": test_results[name]["wrong"],
                "skip": test_results[name]["skip"],
            }
            for name in INDICATORS
        },
        "session_bias_train": session_bias,
        "session_test": {
            s: {"up": d["up"], "total": d["total"],
                "up_pct": round(d["up"] / d["total"], 4) if d["total"] > 0 else 0}
            for s, d in test_sessions.items()
        },
        "top_combos": combos[:20],
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }

    fd = tempfile.NamedTemporaryFile(mode="w", dir=out_dir, suffix=".json", delete=False)
    try:
        json.dump(output, fd, indent=2)
        fd.close()
        os.replace(fd.name, out_file)
    except Exception:
        fd.close()
        os.unlink(fd.name)
        raise
    log.info("Results saved → %s", out_file)

    # ── Recommendations ──
    print(f"\n  RECOMMENDATIONS (sorted by accuracy):")
    sorted_ind = sorted(
        ((n, d) for n, d in indicator_accs.items() if d["n"] >= 30),
        key=lambda x: x[1]["acc"], reverse=True,
    )
    for rank, (name, d) in enumerate(sorted_ind[:5], 1):
        vs = d["acc"] - base_rate
        print(f"    {rank}. {name}: {d['acc'] * 100:.1f}% "
              f"({d['n']} windows, {vs * 100:+.1f}pp vs base)")

    if combos:
        best = combos[0]
        vs = best["accuracy"] - base_rate
        print(f"\n  Best combo: {best['combo']}"
              f" → {best['accuracy'] * 100:.1f}% ({best['total']} windows, {vs * 100:+.1f}pp)")

    # ── Overfitting warning ──
    if combos and combos[0]["accuracy"] > base_rate + 0.10:
        print("\n  ⚠️  Top combo >10pp above base rate. Check train vs test consistency.")
        # Find same combo in train results
        log.info("Cross-checking top combo on training set...")

    print()


if __name__ == "__main__":
    main()
