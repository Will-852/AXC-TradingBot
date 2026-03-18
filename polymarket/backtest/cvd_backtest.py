#!/usr/bin/env python3
"""
cvd_backtest.py — CVD Divergence Backtest for BTC 15-min Up/Down

設計決定：
- 用 Binance aggTrades 計 CVD，1m klines 做 price reference
- Ground truth: 15-min candle close > open = UP（對齊 Polymarket crypto_15m 市場）
- 5m klines 做 leading indicator（更短 timeframe 嘅 signal 預測 15m 方向）
- 三個 model 對比：Indicator-only, CVD-only, Combined
- Brier score 為主要指標（0.25 = random baseline，越低越好）
- funding/sentiment 歷史冇數據 → neutral fallback
- Look-ahead 防護：CVD ref 用 15m candle open 前一分鐘；5m indicator 用最後完成嘅 5m candle
- Dollar imbalance 用前一個完成嘅 5m candle，唔係當前（未完成）

用法:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/cvd_backtest.py --days 14
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

# ─── Path setup (same pattern as other polymarket scripts) ───
_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_agg_trades import (
    fetch_agg_trades_range, aggregate_cvd, aggregate_delta_volume,
)
from backtest.fetch_historical import fetch_klines_range
from polymarket.strategy.crypto_15m import _score_direction
from polymarket.strategy.cvd_strategy import (
    detect_cvd_divergence, cvd_to_prob, compute_dollar_imbalance,
    LOOKBACK_WINDOWS, ONE_MIN_MS, FIVE_MIN_MS, FIFTEEN_MIN_MS,
)

logger = logging.getLogger(__name__)

# ─── Constants ───
SYMBOL = "BTCUSDT"
LOG_DIR = os.path.join(_PROJECT_ROOT, "polymarket", "logs")

# Combined model weights
W_INDICATOR = 0.5
W_CVD = 0.5

# Edge buckets for calibration
EDGE_BUCKETS = [
    (0.00, 0.05, "< 5%"), (0.05, 0.10, "5-10%"), (0.10, 0.15, "10-15%"),
    (0.15, 0.20, "15-20%"), (0.20, 1.00, "> 20%"),
]


# ═══════════════════════════════════════
#  Technical Indicator Computation
# ═══════════════════════════════════════

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(data, np.nan, dtype=float)
    if len(data) < period:
        return out
    out[period - 1] = np.nanmean(data[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(data)):
        out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


def _rolling_std(data: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        out[i] = np.std(data[i - period + 1: i + 1], ddof=0)
    return out


def compute_indicators(klines_5m: pd.DataFrame) -> list[dict]:
    """Compute RSI, MACD, BB, EMA, Stoch, VWAP from 5m klines.

    設計決定：直接 vectorized 計算（唔 subprocess call indicator_calc.py），
    因為 backtest 需要全量歷史，唔係即時一次。
    """
    c = klines_5m["close"].values.astype(float)
    h = klines_5m["high"].values.astype(float)
    lo = klines_5m["low"].values.astype(float)
    vol = klines_5m["volume"].values.astype(float)
    n = len(c)

    # RSI-14
    rsi = np.full(n, np.nan)
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
            rsi[i + 1] = 100 if al[i] == 0 else 100 - 100 / (1 + ag[i] / al[i])

    # MACD (12, 26, 9)
    ema12 = _ema(c, 12)
    ema26 = _ema(c, 26)
    macd_line = ema12 - ema26
    macd_hist = macd_line - _ema(macd_line, 9)

    # Bollinger Bands (20, 2)
    bb_basis = np.full(n, np.nan)
    bb_std = _rolling_std(c, 20)
    for i in range(19, n):
        bb_basis[i] = c[i - 19: i + 1].mean()
    bb_upper = bb_basis + 2 * bb_std
    bb_lower = bb_basis - 2 * bb_std

    # EMA fast(9) / slow(21)
    ema_f = _ema(c, 9)
    ema_s = _ema(c, 21)

    # Stochastic (14, 3)
    stoch_k = np.full(n, np.nan)
    for i in range(13, n):
        hi = h[i - 13: i + 1].max()
        li = lo[i - 13: i + 1].min()
        stoch_k[i] = 100 * (c[i] - li) / (hi - li) if hi != li else 50.0
    stoch_d = np.full(n, np.nan)
    for i in range(15, n):
        vals = stoch_k[i - 2: i + 1]
        if np.all(~np.isnan(vals)):
            stoch_d[i] = vals.mean()

    # VWAP (cumulative — OK for relative comparison within backtest)
    tp = (h + lo + c) / 3
    cum_tpv = np.cumsum(tp * vol)
    cum_v = np.cumsum(vol)
    vwap = np.where(cum_v > 0, cum_tpv / cum_v, c)

    def _v(arr, idx):
        v = arr[idx]
        return float(v) if not np.isnan(v) else None

    indicators = []
    for i in range(n):
        indicators.append({
            "price": float(c[i]),
            "rsi": _v(rsi, i),
            "macd_hist": _v(macd_hist, i),
            "macd_hist_prev": _v(macd_hist, i - 1) if i > 0 else None,
            "bb_upper": _v(bb_upper, i), "bb_lower": _v(bb_lower, i),
            "bb_basis": _v(bb_basis, i),
            "ema_fast": _v(ema_f, i), "ema_slow": _v(ema_s, i),
            "stoch_k": _v(stoch_k, i), "stoch_d": _v(stoch_d, i),
            "vwap": _v(vwap, i),
        })
    return indicators


# ═══════════════════════════════════════
#  Main Backtest
# ═══════════════════════════════════════

def run_backtest(days: int = 14) -> dict:
    """Run CVD backtest: indicator-only vs CVD-only vs combined.

    Returns result dict saved to polymarket/logs/cvd_backtest_results.json.
    """
    now = datetime.now(timezone.utc)
    end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000) - 1

    print(f"╔══ CVD Backtest ══════════════════════════╗")
    print(f"║ {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d} ({days}d)")
    print(f"║ Symbol: {SYMBOL}")
    print(f"╚══════════════════════════════════════════╝\n")

    # ── 1. Fetch data ──
    print("[1/5] Fetching data...")
    t0 = time.time()

    klines_1m = fetch_klines_range(SYMBOL, "1m", start_ms, end_ms)
    print(f"  1m klines: {len(klines_1m)}")

    klines_5m = fetch_klines_range(SYMBOL, "5m", start_ms, end_ms)
    print(f"  5m klines: {len(klines_5m)}")

    klines_15m = fetch_klines_range(SYMBOL, "15m", start_ms, end_ms)
    print(f"  15m klines: {len(klines_15m)}")

    print("  Fetching aggTrades (cached per-day, first run may be slow)...")
    # Retry on ConnectionError — Binance resets after heavy API usage
    for _attempt in range(3):
        try:
            trades_df = fetch_agg_trades_range(SYMBOL, start_ms, end_ms)
            break
        except (ConnectionError, OSError) as e:
            if _attempt == 2:
                raise
            logger.warning("Connection error, retrying in 30s: %s", e)
            time.sleep(30)
    print(f"  aggTrades: {len(trades_df):,}")
    print(f"  Fetch time: {time.time() - t0:.0f}s")

    # ── 2. Pre-compute CVD + delta volume ──
    print("\n[2/5] Computing CVD + delta volume...")
    ts_1m = klines_1m["open_time"].astype(int).tolist()
    minute_cvd_raw = aggregate_cvd(trades_df, ts_1m, ONE_MIN_MS)

    # Fast lookup dicts (O(1) per candle)
    price_by_ts = dict(zip(
        klines_1m["open_time"].astype(int),
        klines_1m["close"].astype(float),
    ))
    cvd_by_ts = {int(k): v["cvd"] for k, v in minute_cvd_raw.items()}

    ts_5m = klines_5m["open_time"].astype(int).tolist()
    dv_5m = aggregate_delta_volume(trades_df, ts_5m, FIVE_MIN_MS)
    ts_15m = klines_15m["open_time"].astype(int).tolist()
    dv_15m = aggregate_delta_volume(trades_df, ts_15m, FIFTEEN_MIN_MS)
    print(f"  1m CVD: {len(minute_cvd_raw)} | 5m DV: {len(dv_5m)} | 15m DV: {len(dv_15m)}")

    # ── 3. Compute indicators ──
    print("\n[3/5] Computing indicators from 5m klines...")
    ind_list = compute_indicators(klines_5m)
    print(f"  {len(ind_list)} indicator snapshots")

    # ── 4. Backtest loop ──
    print("\n[4/5] Running backtest...")
    btc_ctx_neutral = {
        "price": None, "atr": None, "support": None, "resistance": None,
        "funding": None, "sentiment": None, "market_mode": None,
    }

    models = {name: {"correct": 0, "total": 0, "brier": 0.0}
              for name in ["indicator", "cvd", "combined"]}
    cvd_signal_only = {"correct": 0, "total": 0}
    signal_types = {"BULLISH_DIV": {"c": 0, "t": 0}, "BEARISH_DIV": {"c": 0, "t": 0}}
    edge_buckets = {label: {"c": 0, "t": 0} for _, _, label in EDGE_BUCKETS}

    # Build 5m open_time → indicator index mapping (for look-up from 15m loop)
    ts_5m_arr = klines_5m["open_time"].astype(int).values
    ts_to_5m_idx = {int(t): i for i, t in enumerate(ts_5m_arr)}
    warmup_5m = 26  # EMA-26 needs 26 × 5m candles of history

    skipped = 0
    for i in range(len(klines_15m)):
        row = klines_15m.iloc[i]
        actual = 1.0 if float(row["close"]) > float(row["open"]) else 0.0
        ts_15 = int(row["open_time"])

        # Find last COMPLETED 5m candle before this 15m candle opens
        # 5m candle at (ts_15 - 5min) closes exactly at ts_15
        last_5m_ts = ts_15 - FIVE_MIN_MS
        idx_5m = ts_to_5m_idx.get(last_5m_ts)
        if idx_5m is None or idx_5m < warmup_5m:
            skipped += 1
            continue

        # ── Model A: Indicator-only (5m leading indicator for 15m direction) ──
        ind = ind_list[idx_5m]
        p_ind = _score_direction(ind, btc_ctx_neutral)[0] if ind["rsi"] is not None else 0.5

        # ── Model B: CVD-only ──
        # ref_ts = 1 minute BEFORE 15m candle open (avoid look-ahead)
        ref_ts = ts_15 - ONE_MIN_MS
        div_result = detect_cvd_divergence(price_by_ts, cvd_by_ts, ref_ts)
        # Dollar imbalance from last completed 5m candle
        imbalance = compute_dollar_imbalance(dv_5m, dv_15m, last_5m_ts)
        p_cvd = cvd_to_prob(div_result, imbalance)

        # ── Model C: Combined ──
        p_comb = max(0.15, min(0.85, W_INDICATOR * p_ind + W_CVD * p_cvd))

        # ── Record metrics ──
        # p == 0.5 = no directional call → skip accuracy (still count Brier)
        for name, p in [("indicator", p_ind), ("cvd", p_cvd), ("combined", p_comb)]:
            m = models[name]
            m["total"] += 1
            m["brier"] += (p - actual) ** 2
            if p > 0.5 and actual == 1.0:
                m["correct"] += 1
            elif p < 0.5 and actual == 0.0:
                m["correct"] += 1
            elif p == 0.5:
                m["neutral"] = m.get("neutral", 0) + 1

        # CVD signal-only (skip no-signal candles for dedicated accuracy)
        if p_cvd != 0.5:
            cvd_signal_only["total"] += 1
            if (p_cvd > 0.5 and actual == 1.0) or (p_cvd < 0.5 and actual == 0.0):
                cvd_signal_only["correct"] += 1
            st = "BULLISH_DIV" if div_result["bullish"] > div_result["bearish"] else "BEARISH_DIV"
            signal_types[st]["t"] += 1
            if (p_cvd > 0.5) == (actual == 1.0):
                signal_types[st]["c"] += 1

        # Edge bucket (CVD model)
        edge = abs(p_cvd - 0.5)
        for lo_b, hi_b, label in EDGE_BUCKETS:
            if lo_b <= edge < hi_b:
                edge_buckets[label]["t"] += 1
                if p_cvd != 0.5 and ((p_cvd > 0.5 and actual == 1.0) or (p_cvd < 0.5 and actual == 0.0)):
                    edge_buckets[label]["c"] += 1
                break

    if skipped:
        print(f"  (skipped {skipped} 15m candles — warmup / alignment)")

    # ── 5. Report ──
    print("\n[5/5] Results")
    print("=" * 65)

    print(f"\n{'Model':<15} {'Accuracy':>10} {'Brier':>10} {'N':>8} {'Neutral':>8}")
    print("-" * 55)
    for name in ["indicator", "cvd", "combined"]:
        m = models[name]
        n = m["total"]
        neutral = m.get("neutral", 0)
        directional = n - neutral
        acc = m["correct"] / directional if directional else 0
        brier = m["brier"] / n if n else 0
        print(f"  {name:<13} {acc:>9.1%} {brier:>10.4f} {n:>8} {neutral:>8}")
    print(f"  {'random':<13} {'50.0%':>10} {'0.2500':>10}")

    # CVD signal-only
    so = cvd_signal_only
    print(f"\nCVD signal-only (candles with divergence):")
    if so["total"]:
        pct = so["total"] / models["cvd"]["total"] * 100
        print(f"  Accuracy: {so['correct']/so['total']:.1%}  "
              f"({so['total']} signals / {models['cvd']['total']} candles = {pct:.1f}%)")
    else:
        print("  No CVD signals detected")

    # Signal type breakdown
    print(f"\n{'Signal Type':<18} {'Accuracy':>10} {'Count':>8}")
    print("-" * 38)
    for st, d in signal_types.items():
        if d["t"] > 0:
            print(f"  {st:<16} {d['c']/d['t']:>9.1%} {d['t']:>8}")
        else:
            print(f"  {st:<16} {'N/A':>10} {0:>8}")

    # Edge buckets
    print(f"\nCVD Edge-Bucketed Accuracy:")
    print(f"  {'Edge':>8} {'Accuracy':>10} {'Count':>8}")
    print(f"  {'-' * 28}")
    for _, _, label in EDGE_BUCKETS:
        d = edge_buckets[label]
        if d["t"] > 0:
            print(f"  {label:>8} {d['c']/d['t']:>9.1%} {d['t']:>8}")
        else:
            print(f"  {label:>8} {'N/A':>10} {d['t']:>8}")

    # ── Save results (atomic write) ──
    os.makedirs(LOG_DIR, exist_ok=True)
    result_file = os.path.join(LOG_DIR, "cvd_backtest_results.json")
    output = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "period": f"{start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "days": days,
        "symbol": SYMBOL,
        "timeframe": "15m ground truth, 5m leading indicators",
        "models": {
            name: {
                "accuracy": m["correct"] / max(1, m["total"] - m.get("neutral", 0)),
                "brier_score": m["brier"] / m["total"] if m["total"] else 0,
                "total": m["total"],
                "correct": m["correct"],
                "neutral": m.get("neutral", 0),
            }
            for name, m in models.items()
        },
        "cvd_signal_only": {
            "accuracy": so["correct"] / so["total"] if so["total"] else None,
            "total": so["total"],
            "correct": so["correct"],
        },
        "signal_types": {
            st: {
                "accuracy": d["c"] / d["t"] if d["t"] else None,
                "total": d["t"],
                "correct": d["c"],
            }
            for st, d in signal_types.items()
        },
        "edge_buckets": {
            label: {
                "accuracy": edge_buckets[label]["c"] / edge_buckets[label]["t"]
                if edge_buckets[label]["t"] else None,
                "total": edge_buckets[label]["t"],
                "correct": edge_buckets[label]["c"],
            }
            for _, _, label in EDGE_BUCKETS
        },
    }

    fd, tmp_path = tempfile.mkstemp(dir=LOG_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, result_file)
        print(f"\nResults saved → {result_file}")
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return output


def main():
    parser = argparse.ArgumentParser(description="CVD Divergence Backtest")
    parser.add_argument("--days", type=int, default=14, help="Days of history (default: 14)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run_backtest(days=args.days)


if __name__ == "__main__":
    main()
