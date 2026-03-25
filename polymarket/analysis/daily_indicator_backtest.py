#!/usr/bin/env python3
"""
daily_indicator_backtest.py — 10 indicators × 365 days for Polymarket Daily markets

Polymarket Daily: P(BTC close >= open) over 24H window, noon ET → noon ET.
Ground truth: Binance 1H klines, custom 24H windows aligned to noon ET (16:00 UTC EDT).

Key difference from 4H backtest:
  - 24H = news/macro driven (not just TA momentum)
  - Window: noon ET → noon ET (= 16:00 UTC EDT or 17:00 UTC EST)
  - Momentum: first 4H of window (not 1H)
  - Bridge: computed at T+4H with 20H remaining
  - Session: always spans all sessions → not useful as indicator

Indicators tested (same 10 as 4H for baseline comparison):
  1. momentum    — BTC direction in first 4H → continuation
  2. rsi         — RSI(14) on 1D timeframe at window start
  3. macd        — MACD histogram on 1D at window start
  4. bb_squeeze  — BB width pctl on 1D at window start
  5. volume      — Prev day volume vs avg
  6. adx         — ADX + DI direction on 1D
  7. day_of_week — Mon-Sun UP bias (learned from training)
  8. bridge      — Brownian Bridge at T+4H → 20H remaining
  9. obv         — OBV vs OBV_EMA on 1D
  10. ema_cross  — EMA(10) > EMA(50) on 1D

Run:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/analysis/daily_indicator_backtest.py
  PYTHONPATH=.:scripts python3 polymarket/analysis/daily_indicator_backtest.py --days 365 --coin ETH
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

log = logging.getLogger("daily_bt")

_UA = "AXC-Backtest/1.0"
_BINANCE = "https://api.binance.com/api/v3/klines"

# Noon ET = 16:00 UTC (EDT) or 17:00 UTC (EST)
# Polymarket daily crypto markets run noon ET → noon ET.
# Using 16:00 UTC (EDT, Mar-Nov). Winter months (EST) = 17:00 UTC — minor backtest skew.
_WINDOW_START_UTC_HOUR = 16  # noon ET (EDT) = 16:00 UTC


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
        cursor = batch[-1][6] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.3)
    return all_klines


def _to_df(raw: list) -> pd.DataFrame:
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
#  Build 24H Windows from 1H klines
# ═══════════════════════════════════════

def _build_daily_windows(df_1h: pd.DataFrame) -> list[dict]:
    """Build 24H windows aligned to noon ET (16:00 UTC EDT).

    Each window: open = 1H candle open at 16:00 UTC, close = 1H candle close at 15:00 UTC next day.
    Returns list of {start_ms, end_ms, open, close, high, low, volume, result, date, klines_in_window}.
    """
    windows = []
    # Group 1H candles by their daily window
    df_1h = df_1h.sort_values("open_time").reset_index(drop=True)

    # Find all 16:00 UTC candles (= noon ET window starts)
    start_candles = df_1h[df_1h["timestamp"].dt.hour == _WINDOW_START_UTC_HOUR]

    for idx in start_candles.index:
        start_ms = int(df_1h.loc[idx, "open_time"])
        end_ms = start_ms + 24 * 3600 * 1000  # 24H later

        # Get all 1H candles in this window
        mask = (df_1h["open_time"] >= start_ms) & (df_1h["open_time"] < end_ms)
        window_candles = df_1h[mask]

        if len(window_candles) < 20:  # need at least 20 of 24 candles
            continue

        w_open = window_candles.iloc[0]["open"]
        w_close = window_candles.iloc[-1]["close"]
        w_high = window_candles["high"].max()
        w_low = window_candles["low"].min()
        w_volume = window_candles["volume"].sum()
        result = "UP" if w_close >= w_open else "DOWN"
        date = window_candles.iloc[0]["timestamp"].strftime("%Y-%m-%d")
        dow = window_candles.iloc[0]["timestamp"].dayofweek  # 0=Mon, 6=Sun

        windows.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "open": w_open,
            "close": w_close,
            "high": w_high,
            "low": w_low,
            "volume": w_volume,
            "result": result,
            "date": date,
            "dow": dow,
            "dow_name": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dow],
        })

    return windows


# ═══════════════════════════════════════
#  Build "daily" indicators from 1D klines
# ═══════════════════════════════════════

def _compute_daily_indicators(df_1d: pd.DataFrame) -> pd.DataFrame:
    """Compute indicator series on 1D data."""
    try:
        import tradingview_indicators as tv
    except ImportError:
        log.error("tradingview_indicators not installed")
        sys.exit(1)

    # Use 4h params but with adjusted lookback
    close = df_1d["close"]
    high = df_1d["high"]
    low = df_1d["low"]

    # BB
    bb = tv.bollinger_bands(close, 20, 2)
    df_1d["bb_basis"] = bb["basis"]
    bb_width = (bb["upper"] - bb["lower"]) / bb["basis"]
    df_1d["bb_width_pctl"] = bb_width.rolling(50, min_periods=10).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    ).fillna(50.0)

    # RSI
    df_1d["rsi"] = tv.RSI(close, 14)

    # ADX/DMI
    dmi = tv.DMI(df_1d, "close")
    adx_tuple = dmi.adx()
    df_1d["adx"] = adx_tuple[0]
    df_1d["di_plus"] = adx_tuple[1]
    df_1d["di_minus"] = adx_tuple[2]

    # EMA
    df_1d["ema_fast"] = tv.ema(close, 10)
    df_1d["ema_slow"] = tv.ema(close, 50)

    # MACD
    macd_df = tv.MACD(close, 12, 26, 9)
    df_1d["macd_hist"] = macd_df["histogram"]

    # OBV + EMA
    close_diff = close.diff()
    direction = close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    df_1d["obv"] = (df_1d["volume"] * direction).cumsum()
    df_1d["obv_ema"] = tv.ema(df_1d["obv"], 20)

    # Volume ratio
    vol_sma = tv.sma(df_1d["volume"].astype(float), 20)
    df_1d["volume_ratio"] = df_1d["volume"] / vol_sma.replace(0, np.nan)

    # Ground truth
    df_1d["result"] = np.where(df_1d["close"] >= df_1d["open"], "UP", "DOWN")

    return df_1d


# ═══════════════════════════════════════
#  Indicator Predictions
# ═══════════════════════════════════════

def _prev_1d(df_1d, col, window_start_ms):
    """Get the most recent 1D indicator value BEFORE this window started."""
    mask = df_1d["open_time"] < window_start_ms
    if mask.sum() == 0:
        return None
    v = df_1d.loc[mask, col].iloc[-1]
    return None if pd.isna(v) else float(v)


def predict_momentum(window, df_4h):
    """First 4H of 24H window: aggregate 1H candles for momentum.
    Uses df_4h which is actually 1H data passed in — we aggregate 4 candles."""
    start_ms = window["start_ms"]
    end_4h_ms = start_ms + 4 * 3600_000
    # Get 1H candles in first 4 hours of window
    mask = (df_4h["open_time"] >= start_ms) & (df_4h["open_time"] < end_4h_ms)
    candles = df_4h[mask]
    if len(candles) < 3:  # need at least 3 of 4 candles
        return None
    first_open = candles.iloc[0]["open"]
    last_close = candles.iloc[-1]["close"]
    if last_close > first_open:
        return "UP"
    if last_close < first_open:
        return "DOWN"
    return None


def predict_rsi(df_1d, window):
    rsi = _prev_1d(df_1d, "rsi", window["start_ms"])
    if rsi is None:
        return None
    if rsi > 55:
        return "UP"
    if rsi < 45:
        return "DOWN"
    return None


def predict_macd(df_1d, window):
    hist = _prev_1d(df_1d, "macd_hist", window["start_ms"])
    if hist is None:
        return None
    return "UP" if hist > 0 else "DOWN"


def predict_bb_squeeze(df_1d, window):
    pctl = _prev_1d(df_1d, "bb_width_pctl", window["start_ms"])
    if pctl is None or pctl > 20:
        return None
    ema_f = _prev_1d(df_1d, "ema_fast", window["start_ms"])
    ema_s = _prev_1d(df_1d, "ema_slow", window["start_ms"])
    if ema_f is None or ema_s is None:
        return None
    return "UP" if ema_f > ema_s else "DOWN"


def predict_volume(df_1d, window):
    vr = _prev_1d(df_1d, "volume_ratio", window["start_ms"])
    if vr is None or vr < 1.5:
        return None
    mask = df_1d["open_time"] < window["start_ms"]
    if mask.sum() == 0:
        return None
    return df_1d.loc[mask, "result"].iloc[-1]


def predict_adx(df_1d, window):
    adx = _prev_1d(df_1d, "adx", window["start_ms"])
    if adx is None or adx < 25:
        return None
    di_p = _prev_1d(df_1d, "di_plus", window["start_ms"])
    di_m = _prev_1d(df_1d, "di_minus", window["start_ms"])
    if di_p is None or di_m is None:
        return None
    return "UP" if di_p > di_m else "DOWN"


def predict_dow(_df_1d, _window):
    """Day of week — handled via learned bias in evaluate."""
    return None


def predict_bridge(window, df_4h):
    """Bridge at T+4H into the 24H window. Uses 1H klines (df_4h = 1H data)."""
    start_ms = window["start_ms"]
    btc_open = window["open"]
    if btc_open <= 0:
        return None

    # Get 1H candle at T+4H (the 4th hour's close)
    target_ms = start_ms + 3 * 3600_000  # 1H candle starting at T+3H, close = T+4H
    match = df_4h[df_4h["open_time"] == target_ms]
    if match.empty:
        return None
    btc_at_4h = match.iloc[0]["close"]
    if btc_at_4h <= 0:
        return None

    # Estimate vol from recent 1H candles before target
    recent = df_4h[(df_4h["open_time"] >= start_ms) & (df_4h["open_time"] <= target_ms)]
    if len(recent) >= 2:
        closes = recent["close"].values
        log_rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))
                    if closes[i] > 0 and closes[i-1] > 0]
        if log_rets:
            vol_1h = max(0.0001, (sum(r**2 for r in log_rets) / len(log_rets)) ** 0.5)
            vol_1m = vol_1h / math.sqrt(60)  # convert 1H vol to 1min vol
        else:
            vol_1m = 0.005
    else:
        vol_1m = 0.005

    fair = compute_fair_up(btc_at_4h, btc_open, vol_1m, 20 * 60)  # 20H remaining
    if fair > 0.55:
        return "UP"
    if fair < 0.45:
        return "DOWN"
    return None


def predict_obv(df_1d, window):
    obv = _prev_1d(df_1d, "obv", window["start_ms"])
    obv_ema = _prev_1d(df_1d, "obv_ema", window["start_ms"])
    if obv is None or obv_ema is None:
        return None
    return "UP" if obv > obv_ema else "DOWN"


def predict_ema_cross(df_1d, window):
    ema_f = _prev_1d(df_1d, "ema_fast", window["start_ms"])
    ema_s = _prev_1d(df_1d, "ema_slow", window["start_ms"])
    if ema_f is None or ema_s is None:
        return None
    return "UP" if ema_f > ema_s else "DOWN"


# Maps indicator name → (needs_4h, predict_fn)
INDICATORS = {
    "momentum":   ("4h", lambda w, d1, d4: predict_momentum(w, d4)),
    "rsi":        ("1d", lambda w, d1, d4: predict_rsi(d1, w)),
    "macd":       ("1d", lambda w, d1, d4: predict_macd(d1, w)),
    "bb_squeeze": ("1d", lambda w, d1, d4: predict_bb_squeeze(d1, w)),
    "volume":     ("1d", lambda w, d1, d4: predict_volume(d1, w)),
    "adx":        ("1d", lambda w, d1, d4: predict_adx(d1, w)),
    "day_of_week": ("none", lambda w, d1, d4: predict_dow(d1, w)),
    "bridge":     ("4h", lambda w, d1, d4: predict_bridge(w, d4)),
    "obv":        ("1d", lambda w, d1, d4: predict_obv(d1, w)),
    "ema_cross":  ("1d", lambda w, d1, d4: predict_ema_cross(d1, w)),
}


# ═══════════════════════════════════════
#  Evaluation
# ═══════════════════════════════════════

def _evaluate(windows, df_1d, df_4h, start_idx, end_idx, dow_bias=None):
    results = {name: {"correct": 0, "wrong": 0, "skip": 0} for name in INDICATORS}
    dow_stats = defaultdict(lambda: {"up": 0, "total": 0})

    for i in range(start_idx, end_idx):
        w = windows[i]
        actual = w["result"]
        dow_name = w["dow_name"]
        dow_stats[dow_name]["total"] += 1
        if actual == "UP":
            dow_stats[dow_name]["up"] += 1

        for name, (_, predict_fn) in INDICATORS.items():
            if name == "day_of_week":
                pred = dow_bias.get(dow_name) if dow_bias else None
            else:
                pred = predict_fn(w, df_1d, df_4h)

            if pred is None:
                results[name]["skip"] += 1
            elif pred == actual:
                results[name]["correct"] += 1
            else:
                results[name]["wrong"] += 1

    return results, dict(dow_stats)


def _evaluate_combos(windows, df_1d, df_4h, start_idx, end_idx, dow_bias):
    n = end_idx - start_idx
    preds = {}
    for name, (_, predict_fn) in INDICATORS.items():
        preds[name] = []
        for i in range(start_idx, end_idx):
            w = windows[i]
            if name == "day_of_week":
                p = dow_bias.get(w["dow_name"]) if dow_bias else None
            else:
                p = predict_fn(w, df_1d, df_4h)
            preds[name].append(p)

    actuals = [windows[i]["result"] for i in range(start_idx, end_idx)]
    ind_names = list(INDICATORS.keys())
    combo_results = []

    for size in (2, 3):
        for combo in combinations(ind_names, size):
            correct = total = 0
            for j in range(n):
                votes = [preds[name][j] for name in combo if preds[name][j] is not None]
                if len(votes) < len(combo):
                    continue
                up_votes = sum(1 for v in votes if v == "UP")
                pred = "UP" if up_votes > len(votes) / 2 else "DOWN"
                total += 1
                if pred == actuals[j]:
                    correct += 1
            if total >= 20:
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
    parser = argparse.ArgumentParser(description="Daily (24H) Indicator Backtest for Polymarket")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--coin", default="BTC", choices=["BTC", "ETH", "SOL"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    )

    symbol = f"{args.coin}USDT"
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (args.days + 90) * 86_400_000  # extra for warmup

    # ── Fetch data ──
    log.info("Fetching 1H klines for %s (%d+ days)...", symbol, args.days)
    raw_1h = _fetch_klines(symbol, "1h", start_ms, now_ms)
    log.info("  → %d 1H candles", len(raw_1h))

    # Use 1H klines for momentum + bridge (4H candles don't align to 9am ET windows)
    log.info("(momentum + bridge use 1H data — 4H candles don't align to noon ET)")
    raw_4h = raw_1h  # reuse 1H data for momentum/bridge calculations

    log.info("Fetching 1D klines for TA indicators...")
    raw_1d = _fetch_klines(symbol, "1d", start_ms, now_ms)
    log.info("  → %d 1D candles", len(raw_1d))

    df_1h = _to_df(raw_1h)
    df_4h = _to_df(raw_4h)
    df_1d = _to_df(raw_1d)

    # ── Build 24H windows (9am ET aligned) ──
    log.info("Building 24H windows (noon ET = 16:00 UTC EDT)...")
    windows = _build_daily_windows(df_1h)
    log.info("  → %d daily windows", len(windows))

    if len(windows) < 50:
        log.error("Not enough windows (%d). Need ≥50.", len(windows))
        sys.exit(1)

    # ── Compute 1D indicators ──
    log.info("Computing 1D indicators...")
    df_1d = _compute_daily_indicators(df_1d)

    # ── Train/test split ──
    warmup = 30
    n_total = len(windows) - warmup
    n_train = int(n_total * 0.7)
    train_start = warmup
    train_end = warmup + n_train
    test_start = train_end
    test_end = len(windows)
    n_test = test_end - test_start

    log.info("Split: Train %d (%s → %s) | Test %d (%s → %s)",
             n_train, windows[train_start]["date"], windows[train_end - 1]["date"],
             n_test, windows[test_start]["date"], windows[test_end - 1]["date"])

    # ── Train: learn DOW bias ──
    train_results, train_dows = _evaluate(windows, df_1d, df_4h, train_start, train_end)

    dow_bias = {}
    for dow, stats in train_dows.items():
        up_pct = stats["up"] / stats["total"] if stats["total"] > 0 else 0.5
        if up_pct > 0.54:
            dow_bias[dow] = "UP"
        elif up_pct < 0.46:
            dow_bias[dow] = "DOWN"

    for dow in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        stats = train_dows.get(dow, {"up": 0, "total": 0})
        up_pct = stats["up"] / stats["total"] * 100 if stats["total"] > 0 else 0
        bias = dow_bias.get(dow, "NEUTRAL")
        log.info("  %s: %d/%d UP (%.1f%%) → %s", dow, stats["up"], stats["total"], up_pct, bias)

    # ── Test ──
    log.info("Testing all indicators...")
    test_results, test_dows = _evaluate(windows, df_1d, df_4h, test_start, test_end, dow_bias)

    # ── Print ──
    print("\n" + "=" * 72)
    print(f"  DAILY (24H) INDICATOR BACKTEST — {symbol} — {args.days} days")
    print(f"  Window: noon ET → noon ET (16:00 UTC EDT aligned)")
    print(f"  Train: {n_train} days ({windows[train_start]['date']} → {windows[train_end-1]['date']})")
    print(f"  Test:  {n_test} days ({windows[test_start]['date']} → {windows[test_end-1]['date']})")
    print("=" * 72)

    test_up = sum(1 for i in range(test_start, test_end) if windows[i]["result"] == "UP")
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

    # ── DOW breakdown ──
    print(f"\n  Day-of-week breakdown (test):")
    print(f"  {'Day':<8} {'UP/Total':>10} {'UP%':>7} {'Train Bias':>12}")
    print("  " + "-" * 40)
    for dow in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        stats = test_dows.get(dow, {"up": 0, "total": 0})
        if stats["total"] == 0:
            continue
        up_pct = stats["up"] / stats["total"] * 100
        bias = dow_bias.get(dow, "NEUTRAL")
        print(f"  {dow:<8} {stats['up']:>4}/{stats['total']:<4}   {up_pct:>5.1f}%   {bias:>10}")

    # ── Combos ──
    log.info("Testing combinations...")
    combos = _evaluate_combos(windows, df_1d, df_4h, test_start, test_end, dow_bias)

    if combos:
        print(f"\n  Top Combinations (test, majority vote, min 20 windows):")
        print(f"  {'Combo':<35} {'Correct':>8} {'Total':>8} "
              f"{'Accuracy':>9} {'vs Base':>9}")
        print("  " + "-" * 72)
        for c in combos[:15]:
            vs = c["accuracy"] - base_rate
            print(f"  {c['combo']:<35} {c['correct']:>8} {c['total']:>8} "
                  f"{c['accuracy'] * 100:>8.1f}% {vs * 100:>+8.1f}pp")

    # ── Save ──
    out_dir = os.path.join(_AXC, "polymarket", "analysis", "results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"daily_backtest_{symbol}_{args.days}d.json")

    output = {
        "symbol": symbol, "days": args.days,
        "window_type": "24H (9am ET → 9am ET)",
        "n_train": n_train, "n_test": n_test,
        "base_rate_up": round(base_rate, 4),
        "indicators": {
            name: {
                "accuracy": round(indicator_accs[name]["acc"], 4),
                "n_predictions": indicator_accs[name]["n"],
                "coverage": round(indicator_accs[name]["coverage"], 4),
            } for name in INDICATORS
        },
        "dow_bias_train": dow_bias,
        "top_combos": combos[:20] if combos else [],
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
        ((n, d) for n, d in indicator_accs.items() if d["n"] >= 20),
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

    print()


if __name__ == "__main__":
    main()
