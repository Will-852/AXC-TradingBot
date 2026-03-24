"""
Backtest: Squeeze-Explosion Strategy v2 on SOLUSDT 360-day 1H data.

ADJUSTED params vs v1:
  - BB width percentile < 30% (was 20)
  - ADX < 25 (was 20)
  - volume_ratio < 0.8 (was 0.7)
  - SL = ATR × 1.5 (was 1.0)
  - TP = ATR × 3.0 (same)
  - "Squeeze state" lookback: if squeeze conditions met in current OR any
    of previous 3 candles, AND current candle breaks BB → enter

Usage:
    python3 backtest/bt_squeeze_sol_360d_v2.py
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
MARGIN = 300.0                 # Fixed $300 margin per trade
LEVERAGE = 8
SL_ATR_MULT = 1.5             # v2: was 1.0
TP_ATR_MULT = 3.0             # same
MAX_HOLD_BARS = 72            # 72h

# Indicator params
BB_LEN = 20
BB_MULT = 2.0
BB_PCTL_LOOKBACK = 100
ADX_PERIOD = 14
ATR_PERIOD = 14
VOL_SMA_PERIOD = 30

# Entry thresholds — ADJUSTED
BB_PCTL_SQUEEZE = 30.0        # v2: was 20
ADX_LOW = 25.0                # v2: was 20
VOL_QUIET = 0.80              # v2: was 0.70
SQUEEZE_LOOKBACK = 3          # v2: check current + prev 3 candles

# Output
PROJECT = Path("/Users/wai/projects/axc-trading")
OUT_DIR = PROJECT / "backtest" / "data"
OUT_JSONL = OUT_DIR / "bt_SOLUSDT_360d_squeeze_v2_trades.jsonl"


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
        last_close_time = data[-1][6]
        current_start = last_close_time + 1
        print(f"  ... fetched {len(all_rows)} candles (up to {datetime.fromtimestamp(last_close_time/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
        if len(data) < limit:
            break
        time.sleep(0.2)

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

def compute_bb(df: pd.DataFrame) -> pd.DataFrame:
    """Bollinger Bands: basis, upper, lower, width, width_pctl."""
    df["bb_basis"] = df["close"].rolling(BB_LEN).mean()
    bb_std = df["close"].rolling(BB_LEN).std(ddof=0)
    df["bb_upper"] = df["bb_basis"] + BB_MULT * bb_std
    df["bb_lower"] = df["bb_basis"] - BB_MULT * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_basis"]

    def pctl_rank(s: pd.Series) -> float:
        if len(s) < 2 or pd.isna(s.iloc[-1]):
            return np.nan
        val = s.iloc[-1]
        return (s.iloc[:-1] < val).sum() / (len(s) - 1) * 100.0

    df["bb_width_pctl"] = df["bb_width"].rolling(
        BB_PCTL_LOOKBACK, min_periods=BB_PCTL_LOOKBACK
    ).apply(pctl_rank, raw=False)
    return df


def compute_adx(df: pd.DataFrame) -> pd.DataFrame:
    """ADX using Wilder's smoothing."""
    high, low, close = df["high"], df["low"], df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    alpha = 1.0 / ADX_PERIOD
    atr_s = tr.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean() / atr_s

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    return df


def compute_atr(df: pd.DataFrame) -> pd.DataFrame:
    """ATR using Wilder's smoothing."""
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / ATR_PERIOD, min_periods=ATR_PERIOD, adjust=False).mean()
    return df


def compute_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Volume ratio = volume / SMA(volume, period)."""
    vol_sma = df["volume"].rolling(VOL_SMA_PERIOD).mean()
    df["volume_ratio"] = df["volume"] / vol_sma.replace(0, np.nan)
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all required indicators."""
    df = compute_bb(df)
    df = compute_adx(df)
    df = compute_atr(df)
    df = compute_volume_ratio(df)
    return df


# ═══════════════════════════════════════════════════════════════
# 3. BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════

def _is_squeeze(row) -> bool:
    """Check if a single row satisfies squeeze conditions (BB pctl + ADX + vol)."""
    bb_pctl = row.get("bb_width_pctl")
    adx = row.get("adx")
    vol_ratio = row.get("volume_ratio")
    if any(pd.isna(x) for x in [bb_pctl, adx, vol_ratio]):
        return False
    return bb_pctl < BB_PCTL_SQUEEZE and adx < ADX_LOW and vol_ratio < VOL_QUIET


def run_backtest(df: pd.DataFrame) -> list[dict]:
    """
    Run squeeze strategy v2 backtest.

    "Squeeze state": if squeeze conditions met in current candle OR any
    of previous SQUEEZE_LOOKBACK candles, AND current candle breaks
    BB upper/lower → enter.
    """
    trades: list[dict] = []
    in_position = False
    entry_info: dict = {}
    bars_held = 0

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

            if direction == "LONG":
                if row["low"] <= sl:
                    exit_reason = "SL"
                    exit_price = sl
                elif row["high"] >= tp:
                    exit_reason = "TP"
                    exit_price = tp
            else:
                if row["high"] >= sl:
                    exit_reason = "SL"
                    exit_price = sl
                elif row["low"] <= tp:
                    exit_reason = "TP"
                    exit_price = tp

            if exit_reason is None and bars_held >= MAX_HOLD_BARS:
                exit_reason = "MAX_HOLD"
                exit_price = row["close"]

            if exit_reason:
                if direction == "LONG":
                    pnl_pct = ((exit_price - entry_price) / entry_price) * LEVERAGE * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * LEVERAGE * 100

                pnl_dollar = MARGIN * (pnl_pct / 100.0)

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
                    "squeeze_bar_offset": entry_info["squeeze_bar_offset"],
                }
                trades.append(trade)
                in_position = False
                entry_info = {}
                bars_held = 0

        else:
            # ─── Entry logic with squeeze lookback ───
            close = row["close"]
            bb_upper = row.get("bb_upper")
            bb_lower = row.get("bb_lower")
            atr = row.get("atr")

            if any(pd.isna(x) for x in [bb_upper, bb_lower, atr, close]):
                continue

            # Gate 1: Price must break BB band on current candle
            direction = None
            if close > bb_upper:
                direction = "LONG"
            elif close < bb_lower:
                direction = "SHORT"

            if direction is None:
                continue

            # Gate 2: Squeeze state — current OR any of prev SQUEEZE_LOOKBACK candles
            squeeze_found = False
            squeeze_offset = 0  # 0 = current candle
            for offset in range(0, SQUEEZE_LOOKBACK + 1):
                idx = i - offset
                if idx < 0:
                    break
                if _is_squeeze(df.iloc[idx]):
                    squeeze_found = True
                    squeeze_offset = offset
                    break

            if not squeeze_found:
                continue

            # ─── All conditions met → ENTER ───
            entry_price = close
            squeeze_row = df.iloc[i - squeeze_offset]

            # US session
            hour_utc = ts.hour if hasattr(ts, "hour") else pd.Timestamp(ts).hour
            is_us_session = 12 <= hour_utc <= 20

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
                "bb_width_pctl": squeeze_row["bb_width_pctl"],
                "adx": squeeze_row["adx"],
                "volume_ratio": squeeze_row["volume_ratio"],
                "squeeze_bar_offset": squeeze_offset,
            }

    return trades


# ═══════════════════════════════════════════════════════════════
# 4. ANALYSIS & OUTPUT
# ═══════════════════════════════════════════════════════════════

def load_v1_trades() -> pd.DataFrame | None:
    """Load original v1 trades for comparison."""
    v1_path = OUT_DIR / "bt_SOLUSDT_360d_squeeze_trades.jsonl"
    if not v1_path.exists():
        return None
    rows = []
    with open(v1_path) as f:
        for line in f:
            rows.append(json.loads(line.strip()))
    if rows:
        return pd.DataFrame(rows)
    return None


def analyze_trades(trades: list[dict]) -> None:
    """Print full analysis of backtest results."""
    if not trades:
        print("\n=== NO TRADES GENERATED ===")
        return

    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]

    wr = len(wins) / n * 100
    total_return_dollar = df["pnl_dollar"].sum()
    avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0

    gross_profit = wins["pnl_dollar"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl_dollar"].sum()) if len(losses) > 0 else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve for Sharpe & MDD
    equity = [MARGIN]  # Starting with $300 margin
    for _, t in df.iterrows():
        equity.append(equity[-1] + t["pnl_dollar"])
    equity_arr = np.array(equity)

    # MDD
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak) / peak * 100
    mdd = drawdown.min()

    # Sharpe (annualized)
    returns = df["pnl_dollar"].values / MARGIN
    if len(returns) > 1 and returns.std() > 0:
        avg_hold_hours = df["bars_held"].mean()
        trades_per_year = 365 * 24 / avg_hold_hours if avg_hold_hours > 0 else 365
        sharpe = (returns.mean() / returns.std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0

    avg_hold = df["bars_held"].mean()

    print("\n" + "=" * 70)
    print(f"  SQUEEZE v2 BACKTEST — {SYMBOL} {DAYS}d ({INTERVAL})")
    print(f"  Margin: ${MARGIN:.0f} × {LEVERAGE}x leverage")
    print("=" * 70)

    print(f"\n{'─── [1] Summary Stats ───':^70}")
    print(f"  Total trades:        {n}")
    print(f"  Wins / Losses:       {len(wins)} / {len(losses)}")
    print(f"  Win rate:            {wr:.1f}%")
    print(f"  Profit factor:       {pf:.2f}")
    print(f"  Total return:        ${total_return_dollar:,.2f} ({total_return_dollar/MARGIN*100:+.2f}% on margin)")
    print(f"  Final equity:        ${equity_arr[-1]:,.2f}")
    print(f"  Sharpe ratio:        {sharpe:.2f}")
    print(f"  Max drawdown:        {mdd:.2f}%")
    print(f"  Avg hold time:       {avg_hold:.1f} bars ({avg_hold:.1f}h)")
    print(f"  Avg win:             {avg_win:+.2f}%  |  Avg loss: {avg_loss:+.2f}%")
    print(f"  Longs / Shorts:      {len(df[df['direction']=='LONG'])} / {len(df[df['direction']=='SHORT'])}")

    # ── [2] Exit reason distribution ──
    print(f"\n{'─── [2] Exit Reason Distribution ───':^70}")
    for reason in ["TP", "SL", "MAX_HOLD"]:
        subset = df[df["exit_reason"] == reason]
        pct = len(subset) / n * 100 if n > 0 else 0
        avg_pnl = subset["pnl_pct"].mean() if len(subset) > 0 else 0
        avg_dollar = subset["pnl_dollar"].mean() if len(subset) > 0 else 0
        print(f"  {reason:10s}: {len(subset):3d} ({pct:5.1f}%)  avg PnL: {avg_pnl:+.2f}%  avg $: {avg_dollar:+.2f}")

    # ── [3] US session vs non-US ──
    print(f"\n{'─── [3] US Session vs Other ───':^70}")
    for label, mask in [("US (12-20 UTC)", df["was_us_session"] == True),
                         ("Non-US",         df["was_us_session"] == False)]:
        subset = df[mask]
        if len(subset) == 0:
            print(f"  {label:18s}: 0 trades")
            continue
        s_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        s_gp = subset[subset["pnl_pct"] > 0]["pnl_dollar"].sum() if len(subset[subset["pnl_pct"] > 0]) > 0 else 0
        s_gl = abs(subset[subset["pnl_pct"] <= 0]["pnl_dollar"].sum()) if len(subset[subset["pnl_pct"] <= 0]) > 0 else 0
        s_pf = s_gp / s_gl if s_gl > 0 else float("inf")
        s_ret = subset["pnl_dollar"].sum()
        print(f"  {label:18s}: {len(subset):3d} trades  WR={s_wr:.1f}%  PF={s_pf:.2f}  Return=${s_ret:+,.2f}")

    # ── [4] Monthly trade count ──
    print(f"\n{'─── [4] Monthly Breakdown ───':^70}")
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

    # ── Squeeze offset distribution ──
    print(f"\n{'─── Squeeze Bar Offset Distribution ───':^70}")
    for offset in range(0, SQUEEZE_LOOKBACK + 1):
        subset = df[df["squeeze_bar_offset"] == offset]
        if len(subset) == 0:
            print(f"  Offset {offset} ({'current' if offset == 0 else f'{offset} bar ago'}): 0 trades")
            continue
        o_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        o_ret = subset["pnl_dollar"].sum()
        label = "current" if offset == 0 else f"{offset} bar ago"
        print(f"  Offset {offset} ({label:>10}): {len(subset):3d} trades  WR={o_wr:.1f}%  Return=${o_ret:+,.2f}")

    # ── Top 5 best & worst ──
    print(f"\n{'─── Top 5 Best Trades ───':^70}")
    best = df.nlargest(5, "pnl_pct")
    for _, t in best.iterrows():
        print(f"  {t['direction']:5s} {t['entry_time'][:16]}  PnL: {t['pnl_pct']:+.2f}% (${t['pnl_dollar']:+.2f})  exit={t['exit_reason']}  hold={t['bars_held']}h  sqz_off={t['squeeze_bar_offset']}")

    print(f"\n{'─── Top 5 Worst Trades ───':^70}")
    worst = df.nsmallest(5, "pnl_pct")
    for _, t in worst.iterrows():
        print(f"  {t['direction']:5s} {t['entry_time'][:16]}  PnL: {t['pnl_pct']:+.2f}% (${t['pnl_dollar']:+.2f})  exit={t['exit_reason']}  hold={t['bars_held']}h  sqz_off={t['squeeze_bar_offset']}")

    # ── [5] Comparison vs v1 ──
    print(f"\n{'─── [5] Comparison: v2 vs v1 (Original) ───':^70}")
    v1_df = load_v1_trades()
    if v1_df is not None and len(v1_df) > 0:
        v1_n = len(v1_df)
        v1_wr = len(v1_df[v1_df["pnl_pct"] > 0]) / v1_n * 100
        v1_gp = v1_df[v1_df["pnl_pct"] > 0]["pnl_dollar"].sum() if len(v1_df[v1_df["pnl_pct"] > 0]) > 0 else 0
        v1_gl = abs(v1_df[v1_df["pnl_pct"] <= 0]["pnl_dollar"].sum()) if len(v1_df[v1_df["pnl_pct"] <= 0]) > 0 else 0
        v1_pf = v1_gp / v1_gl if v1_gl > 0 else float("inf")
        v1_ret = v1_df["pnl_dollar"].sum()

        print(f"  {'Metric':<20} {'v1 (Original)':>15} {'v2 (Adjusted)':>15} {'Delta':>12}")
        print(f"  {'─'*20} {'─'*15} {'─'*15} {'─'*12}")
        print(f"  {'Trades':<20} {v1_n:>15} {n:>15} {n - v1_n:>+12}")
        print(f"  {'Win Rate':<20} {v1_wr:>14.1f}% {wr:>14.1f}% {wr - v1_wr:>+11.1f}%")

        v1_pf_str = f"{v1_pf:.2f}" if v1_pf != float("inf") else "inf"
        v2_pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"  {'Profit Factor':<20} {v1_pf_str:>15} {v2_pf_str:>15}")
        print(f"  {'Total Return $':<20} {v1_ret:>+15.2f} {total_return_dollar:>+15.2f} {total_return_dollar - v1_ret:>+12.2f}")
        print(f"  {'Avg Hold (bars)':<20} {v1_df['bars_held'].mean():>15.1f} {avg_hold:>15.1f}")
    else:
        print("  v1 trades file not found — skipping comparison.")

    print("\n  v2 param changes:")
    print("    BB pctl:     20 → 30  (wider squeeze definition)")
    print("    ADX:         20 → 25  (allow slightly more trending)")
    print("    Vol ratio:  0.7 → 0.8 (accept slightly higher volume)")
    print("    SL ATR:     1.0 → 1.5 (wider stop loss)")
    print("    Lookback:     0 →  3  (squeeze in current OR prev 3 bars)")

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
    df = fetch_binance_futures(SYMBOL, INTERVAL, DAYS)

    print("\nComputing indicators...")
    df = compute_all_indicators(df)

    valid = df.dropna(subset=["bb_width_pctl", "adx", "atr", "volume_ratio"])
    print(f"Valid rows with all indicators: {len(valid)} / {len(df)}")

    # Debug: count how many candles satisfy each condition
    sq_bb = (df["bb_width_pctl"] < BB_PCTL_SQUEEZE).sum()
    sq_adx = (df["adx"] < ADX_LOW).sum()
    sq_vol = (df["volume_ratio"] < VOL_QUIET).sum()
    sq_all = ((df["bb_width_pctl"] < BB_PCTL_SQUEEZE) & (df["adx"] < ADX_LOW) & (df["volume_ratio"] < VOL_QUIET)).sum()
    bb_break_up = (df["close"] > df["bb_upper"]).sum()
    bb_break_dn = (df["close"] < df["bb_lower"]).sum()
    print(f"\nFilter diagnostics:")
    print(f"  BB pctl < {BB_PCTL_SQUEEZE}:  {sq_bb} candles")
    print(f"  ADX < {ADX_LOW}:        {sq_adx} candles")
    print(f"  Vol ratio < {VOL_QUIET}: {sq_vol} candles")
    print(f"  All 3 squeeze:    {sq_all} candles")
    print(f"  Close > BB upper: {bb_break_up} candles")
    print(f"  Close < BB lower: {bb_break_dn} candles")

    print("\nRunning backtest...")
    trades = run_backtest(df)

    analyze_trades(trades)
    save_trades(trades, OUT_JSONL)


if __name__ == "__main__":
    main()
