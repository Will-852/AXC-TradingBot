"""
Backtest: Squeeze-Explosion Strategy on SOLUSDT 360-day 1H data.

Fetches data from Binance Futures API (paginated), computes indicators
from scratch (no external TA libs), runs candle-by-candle simulation,
and prints full result breakdown.

Usage:
    python3 backtest/bt_squeeze_sol_360d.py
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ─── Config ────────────────────────────────────────────────────
SYMBOL = "SOLUSDT"
INTERVAL = "1h"
DAYS = 360
STARTING_CAPITAL = 10_000.0
MARGIN_PCT = 0.03          # 3% of account per trade
LEVERAGE = 8
SL_ATR_MULT = 1.0
TP_ATR_MULT = 3.0
MAX_HOLD_BARS = 72         # 72 × 1H = 72 hours

# Indicator params
BB_LEN = 20
BB_MULT = 2.0
BB_PCTL_LOOKBACK = 100
RSI_PERIOD = 14
ADX_PERIOD = 14
ATR_PERIOD = 14
VOL_SMA_PERIOD = 30
OBV_EMA_PERIOD = 20

# Entry thresholds
BB_PCTL_SQUEEZE = 20.0
ADX_LOW = 20.0
VOL_QUIET = 0.70

# Output
PROJECT = Path("/Users/wai/projects/axc-trading")
OUT_DIR = PROJECT / "backtest" / "data"
OUT_JSONL = OUT_DIR / "bt_SOLUSDT_360d_squeeze_trades.jsonl"


# ═══════════════════════════════════════════════════════════════
# 1. DATA FETCH
# ═══════════════════════════════════════════════════════════════

def fetch_binance_futures(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch klines from Binance Futures REST API with pagination."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = end_ts - days * 24 * 3600 * 1000

    all_rows: list = []
    current_start = start_ts
    limit = 1500

    print(f"Fetching {symbol} {interval} data for {days} days...")
    while current_start < end_ts:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ts,
            "limit": limit,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_rows.extend(data)
        # Move start past last candle
        last_close_time = data[-1][6]
        current_start = last_close_time + 1
        print(f"  ... fetched {len(all_rows)} candles (up to {datetime.fromtimestamp(last_close_time/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
        if len(data) < limit:
            break
        time.sleep(0.2)  # Rate limit courtesy

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_volume", "taker_buy_quote_volume"]:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"Total: {len(df)} candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


# ═══════════════════════════════════════════════════════════════
# 2. INDICATOR COMPUTATIONS (pure pandas/numpy)
# ═══════════════════════════════════════════════════════════════

def compute_bb(df: pd.DataFrame, length: int = BB_LEN, mult: float = BB_MULT) -> pd.DataFrame:
    """Bollinger Bands: basis, upper, lower, width, width_pctl."""
    df["bb_basis"] = df["close"].rolling(length).mean()
    bb_std = df["close"].rolling(length).std(ddof=0)
    df["bb_upper"] = df["bb_basis"] + mult * bb_std
    df["bb_lower"] = df["bb_basis"] - mult * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_basis"]

    # Percentile rank of bb_width over lookback window
    def pctl_rank(s: pd.Series) -> float:
        if len(s) < 2 or pd.isna(s.iloc[-1]):
            return np.nan
        val = s.iloc[-1]
        return (s.iloc[:-1] < val).sum() / (len(s) - 1) * 100.0

    df["bb_width_pctl"] = df["bb_width"].rolling(BB_PCTL_LOOKBACK, min_periods=BB_PCTL_LOOKBACK).apply(pctl_rank, raw=False)
    return df


def compute_rsi(df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.DataFrame:
    """RSI using Wilder's smoothing (EMA with alpha=1/period)."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))
    return df


def compute_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.DataFrame:
    """ADX using Wilder's smoothing."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    alpha = 1.0 / period
    atr_s = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    return df


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.DataFrame:
    """ATR using Wilder's smoothing."""
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return df


def compute_volume_ratio(df: pd.DataFrame, period: int = VOL_SMA_PERIOD) -> pd.DataFrame:
    """Volume ratio = volume / SMA(volume, period)."""
    vol_sma = df["volume"].rolling(period).mean()
    df["volume_ratio"] = df["volume"] / vol_sma.replace(0, np.nan)
    return df


def compute_obv(df: pd.DataFrame, ema_period: int = OBV_EMA_PERIOD) -> pd.DataFrame:
    """On-Balance Volume + EMA."""
    direction = np.sign(df["close"].diff())
    direction.iloc[0] = 0
    df["obv"] = (df["volume"] * direction).cumsum()
    df["obv_ema"] = df["obv"].ewm(span=ema_period, adjust=False).mean()
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all required indicators."""
    df = compute_bb(df)
    df = compute_rsi(df)
    df = compute_adx(df)
    df = compute_atr(df)
    df = compute_volume_ratio(df)
    df = compute_obv(df)
    return df


# ═══════════════════════════════════════════════════════════════
# 3. BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame) -> list[dict]:
    """Run squeeze strategy backtest on 1H candles."""
    trades: list[dict] = []
    in_position = False
    entry_info: dict = {}
    bars_held = 0

    margin = STARTING_CAPITAL * MARGIN_PCT  # Fixed $300

    for i in range(1, len(df)):
        row = df.iloc[i]
        ts = row["timestamp"]

        if in_position:
            bars_held += 1
            direction = entry_info["direction"]
            entry_price = entry_info["entry_price"]
            sl = entry_info["sl"]
            tp = entry_info["tp"]

            exit_reason = None
            exit_price = None

            # Check SL/TP using high/low of candle
            if direction == "LONG":
                if row["low"] <= sl:
                    exit_reason = "SL"
                    exit_price = sl
                elif row["high"] >= tp:
                    exit_reason = "TP"
                    exit_price = tp
            else:  # SHORT
                if row["high"] >= sl:
                    exit_reason = "SL"
                    exit_price = sl
                elif row["low"] <= tp:
                    exit_reason = "TP"
                    exit_price = tp

            # Max hold exit
            if exit_reason is None and bars_held >= MAX_HOLD_BARS:
                exit_reason = "MAX_HOLD"
                exit_price = row["close"]

            if exit_reason:
                # Calculate PnL
                if direction == "LONG":
                    pnl_pct = ((exit_price - entry_price) / entry_price) * LEVERAGE * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * LEVERAGE * 100

                pnl_dollar = margin * (pnl_pct / 100.0)

                trade = {
                    "entry_time": entry_info["entry_time"].isoformat() if hasattr(entry_info["entry_time"], "isoformat") else str(entry_info["entry_time"]),
                    "exit_time": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "direction": direction,
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(exit_price, 4),
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_dollar": round(pnl_dollar, 4),
                    "exit_reason": exit_reason,
                    "bars_held": bars_held,
                    "was_us_session": entry_info["was_us_session"],
                    "bb_width_pctl": round(entry_info["bb_width_pctl"], 2),
                    "adx": round(entry_info["adx"], 2),
                    "volume_ratio": round(entry_info["volume_ratio"], 4),
                    "confidence": round(entry_info["confidence"], 4),
                }
                trades.append(trade)
                in_position = False
                entry_info = {}
                bars_held = 0

        else:
            # Check entry conditions
            bb_width_pctl = row.get("bb_width_pctl")
            adx = row.get("adx")
            volume_ratio = row.get("volume_ratio")
            atr = row.get("atr")
            close = row["close"]
            bb_upper = row.get("bb_upper")
            bb_lower = row.get("bb_lower")
            bb_width = row.get("bb_width")
            obv = row.get("obv")
            obv_ema = row.get("obv_ema")

            # Skip if indicators not ready
            if any(pd.isna(x) for x in [bb_width_pctl, adx, volume_ratio, atr, bb_upper, bb_lower, bb_width]):
                continue

            # Gate 1: BB width percentile < 20%
            if bb_width_pctl >= BB_PCTL_SQUEEZE:
                continue

            # Gate 2: ADX < 20
            if adx >= ADX_LOW:
                continue

            # Gate 3: Volume ratio < 0.7
            if volume_ratio >= VOL_QUIET:
                continue

            # Gate 4: Price breaks BB band
            direction = None
            if close > bb_upper:
                direction = "LONG"
            elif close < bb_lower:
                direction = "SHORT"

            if direction is None:
                continue

            # ─── All conditions met → ENTER ───
            entry_price = close

            # Compute confidence (matching squeeze_strategy.py)
            bb_pctl_score = max(0.0, 1.0 - (bb_width_pctl / BB_PCTL_SQUEEZE))
            adx_score = max(0.0, 1.0 - (adx / ADX_LOW))

            vol_score = 0.0
            if volume_ratio < VOL_QUIET:
                vol_score = 1.0 - (volume_ratio / VOL_QUIET)

            if bb_width > 0 and entry_price > 0:
                if direction == "LONG":
                    overshoot = (entry_price - bb_upper) / (bb_width * entry_price)
                else:
                    overshoot = (bb_lower - entry_price) / (bb_width * entry_price)
                break_score = min(overshoot * 5.0, 1.0)
            else:
                break_score = 0.0

            confidence = (
                0.30 * bb_pctl_score
                + 0.25 * adx_score
                + 0.25 * vol_score
                + 0.20 * break_score
            )

            # US session bonus
            hour_utc = ts.hour if hasattr(ts, "hour") else pd.Timestamp(ts).hour
            is_us_session = 12 <= hour_utc <= 20
            if is_us_session:
                confidence += 0.10

            # OBV divergence bonus
            if not pd.isna(obv) and not pd.isna(obv_ema):
                if direction == "LONG" and obv > obv_ema:
                    confidence += 0.05
                elif direction == "SHORT" and obv < obv_ema:
                    confidence += 0.05

            confidence = min(confidence, 1.0)

            # SL / TP
            if direction == "LONG":
                sl = entry_price - atr * SL_ATR_MULT
                tp = entry_price + atr * TP_ATR_MULT
            else:
                sl = entry_price + atr * SL_ATR_MULT
                tp = entry_price - atr * TP_ATR_MULT

            in_position = True
            bars_held = 0
            entry_info = {
                "entry_time": ts,
                "direction": direction,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
                "was_us_session": is_us_session,
                "bb_width_pctl": bb_width_pctl,
                "adx": adx,
                "volume_ratio": volume_ratio,
                "confidence": confidence,
            }

    return trades


# ═══════════════════════════════════════════════════════════════
# 4. ANALYSIS & OUTPUT
# ═══════════════════════════════════════════════════════════════

def analyze_trades(trades: list[dict], starting_capital: float = STARTING_CAPITAL) -> None:
    """Print full analysis of backtest results."""
    if not trades:
        print("\n=== NO TRADES GENERATED ===")
        return

    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]

    # ── Summary Stats ──
    wr = len(wins) / n * 100
    total_return_pct = df["pnl_dollar"].sum() / starting_capital * 100
    total_return_dollar = df["pnl_dollar"].sum()
    avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0

    gross_profit = wins["pnl_dollar"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl_dollar"].sum()) if len(losses) > 0 else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve for Sharpe & MDD
    equity = [starting_capital]
    for _, t in df.iterrows():
        equity.append(equity[-1] + t["pnl_dollar"])
    equity_arr = np.array(equity)

    # MDD
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak) / peak * 100
    mdd = drawdown.min()

    # Sharpe (annualized, assuming ~1 trade per few days)
    returns = df["pnl_dollar"].values / starting_capital
    if len(returns) > 1 and returns.std() > 0:
        # Annualize: assume avg hold ~ 24h, so ~365 trades/year potential
        avg_hold_hours = df["bars_held"].mean()
        trades_per_year = 365 * 24 / avg_hold_hours if avg_hold_hours > 0 else 365
        sharpe = (returns.mean() / returns.std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0

    avg_hold = df["bars_held"].mean()

    print("\n" + "=" * 70)
    print(f"  SQUEEZE-EXPLOSION BACKTEST — {SYMBOL} {DAYS}d ({INTERVAL})")
    print("=" * 70)

    print(f"\n{'─── Summary Stats ───':^70}")
    print(f"  Total trades:        {n}")
    print(f"  Wins / Losses:       {len(wins)} / {len(losses)}")
    print(f"  Win rate:            {wr:.1f}%")
    print(f"  Profit factor:       {pf:.2f}")
    print(f"  Total return:        ${total_return_dollar:,.2f} ({total_return_pct:+.2f}%)")
    print(f"  Final equity:        ${equity_arr[-1]:,.2f}")
    print(f"  Sharpe ratio:        {sharpe:.2f}")
    print(f"  Max drawdown:        {mdd:.2f}%")
    print(f"  Avg hold time:       {avg_hold:.1f} bars ({avg_hold:.1f}h)")
    print(f"  Avg win:             {avg_win:+.2f}%  |  Avg loss: {avg_loss:+.2f}%")
    print(f"  Longs / Shorts:      {len(df[df['direction']=='LONG'])} / {len(df[df['direction']=='SHORT'])}")

    # ── Exit reason distribution ──
    print(f"\n{'─── Exit Reason Distribution ───':^70}")
    for reason in ["TP", "SL", "MAX_HOLD"]:
        subset = df[df["exit_reason"] == reason]
        pct = len(subset) / n * 100 if n > 0 else 0
        avg_pnl = subset["pnl_pct"].mean() if len(subset) > 0 else 0
        print(f"  {reason:10s}: {len(subset):3d} ({pct:5.1f}%)  avg PnL: {avg_pnl:+.2f}%")

    # ── US session vs non-US ──
    print(f"\n{'─── US Session Breakdown ───':^70}")
    for label, mask in [("US (12-20 UTC)", df["was_us_session"] == True),
                         ("Non-US",         df["was_us_session"] == False)]:
        subset = df[mask]
        if len(subset) == 0:
            print(f"  {label:18s}: 0 trades")
            continue
        s_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        s_pf_win = subset[subset["pnl_pct"] > 0]["pnl_dollar"].sum() if len(subset[subset["pnl_pct"] > 0]) > 0 else 0
        s_pf_loss = abs(subset[subset["pnl_pct"] <= 0]["pnl_dollar"].sum()) if len(subset[subset["pnl_pct"] <= 0]) > 0 else 0
        s_pf = s_pf_win / s_pf_loss if s_pf_loss > 0 else float("inf")
        s_ret = subset["pnl_dollar"].sum()
        print(f"  {label:18s}: {len(subset):3d} trades  WR={s_wr:.1f}%  PF={s_pf:.2f}  Return=${s_ret:+,.2f}")

    # ── Direction breakdown ──
    print(f"\n{'─── Direction Breakdown ───':^70}")
    for d in ["LONG", "SHORT"]:
        subset = df[df["direction"] == d]
        if len(subset) == 0:
            print(f"  {d:6s}: 0 trades")
            continue
        d_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        d_ret = subset["pnl_dollar"].sum()
        print(f"  {d:6s}: {len(subset):3d} trades  WR={d_wr:.1f}%  Return=${d_ret:+,.2f}")

    # ── Per-month breakdown ──
    print(f"\n{'─── Monthly Breakdown ───':^70}")
    df["entry_month"] = pd.to_datetime(df["entry_time"]).dt.to_period("M")
    months = df.groupby("entry_month")
    print(f"  {'Month':<10} {'Trades':>6} {'Wins':>5} {'WR%':>6} {'PF':>7} {'Return$':>10}")
    print(f"  {'─'*10} {'─'*6} {'─'*5} {'─'*6} {'─'*7} {'─'*10}")
    for month, group in months:
        m_wins = len(group[group["pnl_pct"] > 0])
        m_wr = m_wins / len(group) * 100
        m_gp = group[group["pnl_pct"] > 0]["pnl_dollar"].sum() if m_wins > 0 else 0
        m_gl = abs(group[group["pnl_pct"] <= 0]["pnl_dollar"].sum()) if len(group[group["pnl_pct"] <= 0]) > 0 else 0
        m_pf = m_gp / m_gl if m_gl > 0 else float("inf") if m_gp > 0 else 0
        m_ret = group["pnl_dollar"].sum()
        pf_str = f"{m_pf:.2f}" if m_pf != float("inf") else "inf"
        print(f"  {str(month):<10} {len(group):>6} {m_wins:>5} {m_wr:>5.1f}% {pf_str:>7} {m_ret:>+10.2f}")

    # ── Top 5 best & worst ──
    print(f"\n{'─── Top 5 Best Trades ───':^70}")
    best = df.nlargest(5, "pnl_pct")
    for _, t in best.iterrows():
        print(f"  {t['direction']:5s} {t['entry_time'][:16]}  PnL: {t['pnl_pct']:+.2f}% (${t['pnl_dollar']:+.2f})  exit={t['exit_reason']}  hold={t['bars_held']}h")

    print(f"\n{'─── Top 5 Worst Trades ───':^70}")
    worst = df.nsmallest(5, "pnl_pct")
    for _, t in worst.iterrows():
        print(f"  {t['direction']:5s} {t['entry_time'][:16]}  PnL: {t['pnl_pct']:+.2f}% (${t['pnl_dollar']:+.2f})  exit={t['exit_reason']}  hold={t['bars_held']}h")

    # ── Confidence analysis ──
    print(f"\n{'─── Confidence Score Analysis ───':^70}")
    bins = [(0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in bins:
        subset = df[(df["confidence"] >= lo) & (df["confidence"] < hi)]
        if len(subset) == 0:
            continue
        c_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        c_ret = subset["pnl_dollar"].sum()
        print(f"  Conf [{lo:.1f}-{hi:.1f}): {len(subset):3d} trades  WR={c_wr:.1f}%  Return=${c_ret:+,.2f}")

    print("\n" + "=" * 70)


def save_trades(trades: list[dict], path: Path) -> None:
    """Save trades to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")
    print(f"\nTrades saved to {path}  ({len(trades)} records)")


# ═══════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    # Fetch data
    df = fetch_binance_futures(SYMBOL, INTERVAL, DAYS)

    # Compute indicators
    print("\nComputing indicators...")
    df = compute_all_indicators(df)

    # Count how many rows have valid indicators
    valid = df.dropna(subset=["bb_width_pctl", "adx", "atr", "volume_ratio"])
    print(f"Valid rows with all indicators: {len(valid)} / {len(df)}")

    # Run backtest
    print("\nRunning backtest...")
    trades = run_backtest(df)

    # Output
    analyze_trades(trades)
    save_trades(trades, OUT_JSONL)


if __name__ == "__main__":
    main()
