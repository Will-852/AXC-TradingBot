"""
Backtest: Squeeze-Explosion Strategy on ETHUSDT 360-day 1H data.

Runs TWO variants side-by-side:
  1. ORIGINAL params (same as SOL backtest)
  2. ADJUSTED params (relaxed thresholds + lookback squeeze state + wider SL)

Fetches data from Binance Futures API (paginated), computes indicators
from scratch (no external TA libs), runs candle-by-candle simulation,
prints full result breakdown for BOTH variants side by side.

Usage:
    python3 backtest/bt_squeeze_eth_360d.py
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ─── Config ────────────────────────────────────────────────────
SYMBOL = "ETHUSDT"
INTERVAL = "1h"
DAYS = 360
MARGIN = 300.0              # $300 margin per trade
LEVERAGE = 8
MAX_HOLD_BARS = 72          # 72 x 1H = 72 hours

# Indicator params (shared)
BB_LEN = 20
BB_MULT = 2.0
BB_PCTL_LOOKBACK = 100
ADX_PERIOD = 14
ATR_PERIOD = 14
VOL_SMA_PERIOD = 30
OBV_EMA_PERIOD = 20

# Output
PROJECT = Path("/Users/wai/projects/axc-trading")
OUT_DIR = PROJECT / "backtest" / "data"
OUT_JSONL = OUT_DIR / "bt_ETHUSDT_360d_squeeze_trades.jsonl"


@dataclass
class SqueezeParams:
    """Parameter set for a squeeze variant."""
    name: str
    bb_pctl_thresh: float    # BB width percentile threshold
    adx_thresh: float        # ADX threshold
    vol_ratio_thresh: float  # Volume ratio threshold
    sl_atr_mult: float       # SL = ATR * this
    tp_atr_mult: float       # TP = ATR * this
    lookback_bars: int       # 0 = current candle only, N = check prev N candles too


ORIGINAL = SqueezeParams(
    name="ORIGINAL",
    bb_pctl_thresh=20.0,
    adx_thresh=20.0,
    vol_ratio_thresh=0.70,
    sl_atr_mult=1.0,
    tp_atr_mult=3.0,
    lookback_bars=0,
)

ADJUSTED = SqueezeParams(
    name="ADJUSTED",
    bb_pctl_thresh=30.0,
    adx_thresh=25.0,
    vol_ratio_thresh=0.80,
    sl_atr_mult=1.5,
    tp_atr_mult=3.0,
    lookback_bars=3,
)


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


def compute_obv(df: pd.DataFrame) -> pd.DataFrame:
    """On-Balance Volume + EMA."""
    direction = np.sign(df["close"].diff())
    direction.iloc[0] = 0
    df["obv"] = (df["volume"] * direction).cumsum()
    df["obv_ema"] = df["obv"].ewm(span=OBV_EMA_PERIOD, adjust=False).mean()
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all required indicators."""
    df = compute_bb(df)
    df = compute_adx(df)
    df = compute_atr(df)
    df = compute_volume_ratio(df)
    df = compute_obv(df)
    return df


# ═══════════════════════════════════════════════════════════════
# 3. BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════

def _squeeze_conditions_met(row, params: SqueezeParams) -> bool:
    """Check if the 3 squeeze conditions are met for a single candle."""
    bb_pctl = row.get("bb_width_pctl")
    adx = row.get("adx")
    vol_ratio = row.get("volume_ratio")
    if any(pd.isna(x) for x in [bb_pctl, adx, vol_ratio]):
        return False
    return (
        bb_pctl < params.bb_pctl_thresh
        and adx < params.adx_thresh
        and vol_ratio < params.vol_ratio_thresh
    )


def run_backtest(df: pd.DataFrame, params: SqueezeParams) -> list[dict]:
    """Run squeeze strategy backtest on 1H candles with given params."""
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

            # SL/TP using high/low of candle
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
                    "confidence": round(entry_info["confidence"], 4),
                    "variant": params.name,
                }
                trades.append(trade)
                in_position = False
                entry_info = {}
                bars_held = 0

        else:
            # ── Check entry conditions ──
            atr = row.get("atr")
            close = row["close"]
            bb_upper = row.get("bb_upper")
            bb_lower = row.get("bb_lower")
            bb_width = row.get("bb_width")
            obv = row.get("obv")
            obv_ema = row.get("obv_ema")

            if any(pd.isna(x) for x in [atr, bb_upper, bb_lower, bb_width]):
                continue

            # Check squeeze conditions: current candle or lookback
            squeeze_met = False
            squeeze_row = row  # Row that satisfied squeeze (for logging indicators)

            if _squeeze_conditions_met(row, params):
                squeeze_met = True
                squeeze_row = row
            elif params.lookback_bars > 0:
                # Check previous N candles for squeeze state
                for lookback in range(1, params.lookback_bars + 1):
                    idx = i - lookback
                    if idx < 0:
                        break
                    prev = df.iloc[idx]
                    if _squeeze_conditions_met(prev, params):
                        squeeze_met = True
                        squeeze_row = prev
                        break

            if not squeeze_met:
                continue

            # Gate: Price breaks BB band (always on CURRENT candle)
            direction = None
            if close > bb_upper:
                direction = "LONG"
            elif close < bb_lower:
                direction = "SHORT"

            if direction is None:
                continue

            # ── All conditions met -> ENTER ──
            entry_price = close

            # Confidence scoring
            bb_pctl_val = squeeze_row.get("bb_width_pctl", 0)
            adx_val = squeeze_row.get("adx", 0)
            vol_ratio_val = squeeze_row.get("volume_ratio", 0)

            bb_pctl_score = max(0.0, 1.0 - (bb_pctl_val / params.bb_pctl_thresh))
            adx_score = max(0.0, 1.0 - (adx_val / params.adx_thresh))
            vol_score = max(0.0, 1.0 - (vol_ratio_val / params.vol_ratio_thresh))

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

            hour_utc = ts.hour if hasattr(ts, "hour") else pd.Timestamp(ts).hour
            is_us_session = 12 <= hour_utc <= 20
            if is_us_session:
                confidence += 0.10

            if not pd.isna(obv) and not pd.isna(obv_ema):
                if direction == "LONG" and obv > obv_ema:
                    confidence += 0.05
                elif direction == "SHORT" and obv < obv_ema:
                    confidence += 0.05

            confidence = min(confidence, 1.0)

            # SL / TP
            if direction == "LONG":
                sl = entry_price - atr * params.sl_atr_mult
                tp = entry_price + atr * params.tp_atr_mult
            else:
                sl = entry_price + atr * params.sl_atr_mult
                tp = entry_price - atr * params.tp_atr_mult

            in_position = True
            bars_held = 0
            entry_info = {
                "entry_time": ts,
                "direction": direction,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
                "was_us_session": is_us_session,
                "bb_width_pctl": bb_pctl_val if not pd.isna(bb_pctl_val) else 0,
                "adx": adx_val if not pd.isna(adx_val) else 0,
                "volume_ratio": vol_ratio_val if not pd.isna(vol_ratio_val) else 0,
                "confidence": confidence,
            }

    return trades


# ═══════════════════════════════════════════════════════════════
# 4. ANALYSIS & OUTPUT
# ═══════════════════════════════════════════════════════════════

def compute_stats(trades: list[dict]) -> dict:
    """Compute all stats for a trade list; returns dict for display."""
    if not trades:
        return {"n": 0}

    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]

    wr = len(wins) / n * 100
    total_dollar = df["pnl_dollar"].sum()
    total_pct = total_dollar / MARGIN * 100  # return on $300 margin

    gross_profit = wins["pnl_dollar"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl_dollar"].sum()) if len(losses) > 0 else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve
    equity = [MARGIN]
    for _, t in df.iterrows():
        equity.append(equity[-1] + t["pnl_dollar"])
    equity_arr = np.array(equity)

    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak) / peak * 100
    mdd = drawdown.min()

    returns = df["pnl_dollar"].values / MARGIN
    if len(returns) > 1 and returns.std() > 0:
        avg_hold_hours = df["bars_held"].mean()
        trades_per_year = 365 * 24 / avg_hold_hours if avg_hold_hours > 0 else 365
        sharpe = (returns.mean() / returns.std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0

    avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    avg_hold = df["bars_held"].mean()

    # Exit distribution
    exit_dist = {}
    for reason in ["TP", "SL", "MAX_HOLD"]:
        subset = df[df["exit_reason"] == reason]
        exit_dist[reason] = {
            "count": len(subset),
            "pct": len(subset) / n * 100 if n > 0 else 0,
            "avg_pnl": subset["pnl_pct"].mean() if len(subset) > 0 else 0,
        }

    # US session
    session_stats = {}
    for label, mask in [("US (12-20 UTC)", df["was_us_session"] == True),
                        ("Non-US", df["was_us_session"] == False)]:
        subset = df[mask]
        if len(subset) == 0:
            session_stats[label] = {"n": 0}
            continue
        s_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        s_gp = subset[subset["pnl_pct"] > 0]["pnl_dollar"].sum() if len(subset[subset["pnl_pct"] > 0]) > 0 else 0
        s_gl = abs(subset[subset["pnl_pct"] <= 0]["pnl_dollar"].sum()) if len(subset[subset["pnl_pct"] <= 0]) > 0 else 0
        s_pf = s_gp / s_gl if s_gl > 0 else float("inf")
        session_stats[label] = {
            "n": len(subset), "wr": s_wr, "pf": s_pf,
            "ret": subset["pnl_dollar"].sum(),
        }

    # Monthly
    df["entry_month"] = pd.to_datetime(df["entry_time"]).dt.to_period("M")
    monthly = {}
    for month, group in df.groupby("entry_month"):
        m_wins = len(group[group["pnl_pct"] > 0])
        m_n = len(group)
        m_wr = m_wins / m_n * 100
        m_gp = group[group["pnl_pct"] > 0]["pnl_dollar"].sum() if m_wins > 0 else 0
        m_gl = abs(group[group["pnl_pct"] <= 0]["pnl_dollar"].sum()) if (m_n - m_wins) > 0 else 0
        m_pf = m_gp / m_gl if m_gl > 0 else (float("inf") if m_gp > 0 else 0)
        monthly[str(month)] = {"n": m_n, "wins": m_wins, "wr": m_wr, "pf": m_pf, "ret": group["pnl_dollar"].sum()}

    # Direction
    dir_stats = {}
    for d in ["LONG", "SHORT"]:
        subset = df[df["direction"] == d]
        if len(subset) == 0:
            dir_stats[d] = {"n": 0}
            continue
        d_wr = len(subset[subset["pnl_pct"] > 0]) / len(subset) * 100
        dir_stats[d] = {"n": len(subset), "wr": d_wr, "ret": subset["pnl_dollar"].sum()}

    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "wr": wr, "pf": pf,
        "total_dollar": total_dollar, "total_pct": total_pct,
        "final_equity": equity_arr[-1],
        "sharpe": sharpe, "mdd": mdd,
        "avg_win": avg_win, "avg_loss": avg_loss, "avg_hold": avg_hold,
        "exit_dist": exit_dist,
        "session_stats": session_stats,
        "monthly": monthly,
        "dir_stats": dir_stats,
    }


def print_side_by_side(s1: dict, s2: dict, p1: SqueezeParams, p2: SqueezeParams) -> None:
    """Print two result sets side by side."""
    W = 90
    COL = 38

    def header(text: str):
        print(f"\n{'=' * W}")
        print(f"  {text}")
        print(f"{'=' * W}")

    def section(text: str):
        print(f"\n{'--- ' + text + ' ':─<{W}}")

    def row(label: str, v1: str, v2: str):
        print(f"  {label:<24s} {v1:>{COL}s}  {v2:>{COL}s}")

    def pf_str(v):
        return "inf" if v == float("inf") else f"{v:.2f}"

    header(f"SQUEEZE BACKTEST -- {SYMBOL} {DAYS}d ({INTERVAL})")
    row("", p1.name, p2.name)
    row("", "-" * len(p1.name), "-" * len(p2.name))

    # ── Params ──
    section("Parameters")
    row("BB pctl thresh", f"< {p1.bb_pctl_thresh}%", f"< {p2.bb_pctl_thresh}%")
    row("ADX thresh", f"< {p1.adx_thresh}", f"< {p2.adx_thresh}")
    row("Vol ratio thresh", f"< {p1.vol_ratio_thresh}", f"< {p2.vol_ratio_thresh}")
    row("SL (ATR x)", f"{p1.sl_atr_mult}", f"{p2.sl_atr_mult}")
    row("TP (ATR x)", f"{p1.tp_atr_mult}", f"{p2.tp_atr_mult}")
    row("Lookback bars", f"{p1.lookback_bars}", f"{p2.lookback_bars}")

    if s1["n"] == 0 and s2["n"] == 0:
        print("\n  NO TRADES for either variant.\n")
        return

    # ── Summary ──
    section("Summary Stats")
    row("Total trades", f"{s1['n']}", f"{s2['n']}")
    row("Wins / Losses", f"{s1.get('wins',0)} / {s1.get('losses',0)}", f"{s2.get('wins',0)} / {s2.get('losses',0)}")
    row("Win rate", f"{s1.get('wr',0):.1f}%", f"{s2.get('wr',0):.1f}%")
    row("Profit factor", pf_str(s1.get('pf',0)), pf_str(s2.get('pf',0)))
    row("Total return ($)", f"${s1.get('total_dollar',0):+,.2f}", f"${s2.get('total_dollar',0):+,.2f}")
    row("Total return (%)", f"{s1.get('total_pct',0):+.2f}%", f"{s2.get('total_pct',0):+.2f}%")
    row("Final equity", f"${s1.get('final_equity',MARGIN):,.2f}", f"${s2.get('final_equity',MARGIN):,.2f}")
    row("Sharpe ratio", f"{s1.get('sharpe',0):.2f}", f"{s2.get('sharpe',0):.2f}")
    row("Max drawdown", f"{s1.get('mdd',0):.2f}%", f"{s2.get('mdd',0):.2f}%")
    row("Avg hold (hours)", f"{s1.get('avg_hold',0):.1f}", f"{s2.get('avg_hold',0):.1f}")
    row("Avg win %", f"{s1.get('avg_win',0):+.2f}%", f"{s2.get('avg_win',0):+.2f}%")
    row("Avg loss %", f"{s1.get('avg_loss',0):+.2f}%", f"{s2.get('avg_loss',0):+.2f}%")

    # ── Direction ──
    section("Direction Breakdown")
    for d in ["LONG", "SHORT"]:
        d1 = s1.get("dir_stats", {}).get(d, {"n": 0})
        d2 = s2.get("dir_stats", {}).get(d, {"n": 0})
        v1 = f"{d1['n']} trades" + (f"  WR={d1['wr']:.1f}%  ${d1['ret']:+,.2f}" if d1['n'] > 0 else "")
        v2 = f"{d2['n']} trades" + (f"  WR={d2['wr']:.1f}%  ${d2['ret']:+,.2f}" if d2['n'] > 0 else "")
        row(d, v1, v2)

    # ── Exit distribution ──
    section("Exit Distribution")
    for reason in ["TP", "SL", "MAX_HOLD"]:
        e1 = s1.get("exit_dist", {}).get(reason, {"count": 0, "pct": 0, "avg_pnl": 0})
        e2 = s2.get("exit_dist", {}).get(reason, {"count": 0, "pct": 0, "avg_pnl": 0})
        v1 = f"{e1['count']:3d} ({e1['pct']:5.1f}%) avg={e1['avg_pnl']:+.2f}%"
        v2 = f"{e2['count']:3d} ({e2['pct']:5.1f}%) avg={e2['avg_pnl']:+.2f}%"
        row(reason, v1, v2)

    # ── US session ──
    section("US Session Breakdown")
    for label in ["US (12-20 UTC)", "Non-US"]:
        ss1 = s1.get("session_stats", {}).get(label, {"n": 0})
        ss2 = s2.get("session_stats", {}).get(label, {"n": 0})
        if ss1["n"] > 0:
            v1 = f"{ss1['n']} trades  WR={ss1['wr']:.1f}%  PF={pf_str(ss1['pf'])}  ${ss1['ret']:+,.2f}"
        else:
            v1 = "0 trades"
        if ss2["n"] > 0:
            v2 = f"{ss2['n']} trades  WR={ss2['wr']:.1f}%  PF={pf_str(ss2['pf'])}  ${ss2['ret']:+,.2f}"
        else:
            v2 = "0 trades"
        row(label, v1, v2)

    # ── Monthly ──
    section("Monthly Trade Count")
    all_months = sorted(set(list(s1.get("monthly", {}).keys()) + list(s2.get("monthly", {}).keys())))
    header_line = f"  {'Month':<10} {'ORIGINAL':>18} {'':>4} {'ADJUSTED':>18}"
    print(header_line)
    print(f"  {'─'*10} {'─'*18} {'':>4} {'─'*18}")
    for m in all_months:
        m1 = s1.get("monthly", {}).get(m, {"n": 0, "wr": 0, "ret": 0})
        m2 = s2.get("monthly", {}).get(m, {"n": 0, "wr": 0, "ret": 0})
        v1 = f"{m1['n']:2d}t WR={m1['wr']:4.0f}% ${m1['ret']:+7.2f}" if m1["n"] > 0 else "  --"
        v2 = f"{m2['n']:2d}t WR={m2['wr']:4.0f}% ${m2['ret']:+7.2f}" if m2["n"] > 0 else "  --"
        print(f"  {m:<10} {v1:>18} {'':>4} {v2:>18}")

    print(f"\n{'=' * W}\n")


def save_trades(trades: list[dict], path: Path) -> None:
    """Save trades to JSONL (atomic write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")
    os.replace(tmp, path)
    print(f"Trades saved to {path}  ({len(trades)} records)")


# ═══════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    # Fetch data (once, shared by both variants)
    df = fetch_binance_futures(SYMBOL, INTERVAL, DAYS)

    print("\nComputing indicators...")
    df = compute_all_indicators(df)

    valid = df.dropna(subset=["bb_width_pctl", "adx", "atr", "volume_ratio"])
    print(f"Valid rows with all indicators: {len(valid)} / {len(df)}")

    # ── Diagnostic: how often are individual squeeze conditions met? ──
    print("\n--- Squeeze condition frequency (on valid rows) ---")
    for label, params in [("ORIGINAL", ORIGINAL), ("ADJUSTED", ADJUSTED)]:
        bb_ok = (valid["bb_width_pctl"] < params.bb_pctl_thresh).sum()
        adx_ok = (valid["adx"] < params.adx_thresh).sum()
        vol_ok = (valid["volume_ratio"] < params.vol_ratio_thresh).sum()
        all_ok = (
            (valid["bb_width_pctl"] < params.bb_pctl_thresh)
            & (valid["adx"] < params.adx_thresh)
            & (valid["volume_ratio"] < params.vol_ratio_thresh)
        ).sum()
        n_valid = len(valid)
        print(f"  [{label}] BB_pctl<{params.bb_pctl_thresh}: {bb_ok}/{n_valid} ({bb_ok/n_valid*100:.1f}%)  "
              f"ADX<{params.adx_thresh}: {adx_ok}/{n_valid} ({adx_ok/n_valid*100:.1f}%)  "
              f"VolR<{params.vol_ratio_thresh}: {vol_ok}/{n_valid} ({vol_ok/n_valid*100:.1f}%)  "
              f"ALL 3: {all_ok}/{n_valid} ({all_ok/n_valid*100:.1f}%)")

    # Run both variants
    print("\nRunning ORIGINAL backtest...")
    trades_orig = run_backtest(df, ORIGINAL)
    print(f"  -> {len(trades_orig)} trades")

    print("Running ADJUSTED backtest...")
    trades_adj = run_backtest(df, ADJUSTED)
    print(f"  -> {len(trades_adj)} trades")

    # Compute stats
    stats_orig = compute_stats(trades_orig)
    stats_adj = compute_stats(trades_adj)

    # Side-by-side output
    print_side_by_side(stats_orig, stats_adj, ORIGINAL, ADJUSTED)

    # Save adjusted trades
    save_trades(trades_adj, OUT_JSONL)

    # Also print top trades for adjusted variant
    if trades_adj:
        tdf = pd.DataFrame(trades_adj)
        print("\n--- Top 5 Best Trades (ADJUSTED) ---")
        best = tdf.nlargest(5, "pnl_pct")
        for _, t in best.iterrows():
            print(f"  {t['direction']:5s} {t['entry_time'][:16]}  PnL: {t['pnl_pct']:+.2f}% (${t['pnl_dollar']:+.2f})  exit={t['exit_reason']}  hold={t['bars_held']}h")

        print("\n--- Top 5 Worst Trades (ADJUSTED) ---")
        worst = tdf.nsmallest(5, "pnl_pct")
        for _, t in worst.iterrows():
            print(f"  {t['direction']:5s} {t['entry_time'][:16]}  PnL: {t['pnl_pct']:+.2f}% (${t['pnl_dollar']:+.2f})  exit={t['exit_reason']}  hold={t['bars_held']}h")


if __name__ == "__main__":
    main()
