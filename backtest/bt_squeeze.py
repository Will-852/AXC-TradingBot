"""
bt_squeeze.py — Unified Squeeze-Explosion Strategy Backtest

Replaces 4 per-coin scripts (bt_squeeze_{btc,eth,sol}_360d.py + _v2).
Runs ORIGINAL + ADJUSTED params side-by-side, prints full comparison.

Usage:
    python3 backtest/bt_squeeze.py --coin BTC --days 360
    python3 backtest/bt_squeeze.py --coin ETH --days 180
    python3 backtest/bt_squeeze.py --coin SOL --days 360 --margin 200
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ─── Config ────────────────────────────────────────────────────
# Per-coin defaults (overridable via CLI)
COIN_DEFAULTS = {
    "BTC": {"symbol": "BTCUSDT", "margin": 300.0},
    "ETH": {"symbol": "ETHUSDT", "margin": 300.0},
    "SOL": {"symbol": "SOLUSDT", "margin": 200.0},
    "XRP": {"symbol": "XRPUSDT", "margin": 200.0},
    "POL": {"symbol": "POLUSDT", "margin": 100.0},
}

LEVERAGE = 8
MAX_HOLD_BARS = 72           # 72 x 1H = 72 hours

# Indicator params
BB_LEN = 20
BB_MULT = 2.0
BB_PCTL_LOOKBACK = 100
ADX_PERIOD = 14
ATR_PERIOD = 14
VOL_SMA_PERIOD = 30

PROJECT = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
OUT_DIR = PROJECT / "backtest" / "data"


@dataclass
class StrategyParams:
    """Bundle of tunable strategy parameters."""
    name: str
    bb_pctl_thresh: float
    adx_thresh: float
    vol_ratio_thresh: float
    sl_atr_mult: float
    tp_atr_mult: float
    squeeze_lookback: int


ORIGINAL = StrategyParams(
    name="ORIGINAL",
    bb_pctl_thresh=20.0,
    adx_thresh=20.0,
    vol_ratio_thresh=0.70,
    sl_atr_mult=1.0,
    tp_atr_mult=3.0,
    squeeze_lookback=0,
)

ADJUSTED = StrategyParams(
    name="ADJUSTED",
    bb_pctl_thresh=30.0,
    adx_thresh=25.0,
    vol_ratio_thresh=0.80,
    sl_atr_mult=1.5,
    tp_atr_mult=3.0,
    squeeze_lookback=3,
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
        print(f"  ... fetched {len(all_rows)} candles "
              f"(up to {datetime.fromtimestamp(last_close_time/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')})")
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
# 2. INDICATOR COMPUTATIONS
# ═══════════════════════════════════════════════════════════════

def compute_bb(df: pd.DataFrame) -> pd.DataFrame:
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
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    alpha = 1.0 / ADX_PERIOD
    atr_s = tr.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean() / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=alpha, min_periods=ADX_PERIOD, adjust=False).mean()
    return df


def compute_atr(df: pd.DataFrame) -> pd.DataFrame:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / ATR_PERIOD, min_periods=ATR_PERIOD, adjust=False).mean()
    return df


def compute_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    vol_sma = df["volume"].rolling(VOL_SMA_PERIOD).mean()
    df["volume_ratio"] = df["volume"] / vol_sma.replace(0, np.nan)
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = compute_bb(df)
    df = compute_adx(df)
    df = compute_atr(df)
    df = compute_volume_ratio(df)
    return df


# ═══════════════════════════════════════════════════════════════
# 3. BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════

def _squeeze_conditions_met(row, params: StrategyParams) -> bool:
    bb_pctl = row.get("bb_width_pctl")
    adx = row.get("adx")
    vol_ratio = row.get("volume_ratio")
    if any(pd.isna(x) for x in [bb_pctl, adx, vol_ratio]):
        return False
    return (bb_pctl < params.bb_pctl_thresh
            and adx < params.adx_thresh
            and vol_ratio < params.vol_ratio_thresh)


def run_backtest(df: pd.DataFrame, params: StrategyParams, margin: float) -> list[dict]:
    """Run squeeze strategy backtest on 1H candles."""
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
                    exit_reason, exit_price = "SL", sl
                elif row["high"] >= tp:
                    exit_reason, exit_price = "TP", tp
            else:
                if row["high"] >= sl:
                    exit_reason, exit_price = "SL", sl
                elif row["low"] <= tp:
                    exit_reason, exit_price = "TP", tp

            if exit_reason is None and bars_held >= MAX_HOLD_BARS:
                exit_reason, exit_price = "MAX_HOLD", row["close"]

            if exit_reason:
                if direction == "LONG":
                    pnl_pct = ((exit_price - entry_price) / entry_price) * LEVERAGE * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * LEVERAGE * 100

                pnl_dollar = margin * (pnl_pct / 100.0)

                trades.append({
                    "entry_time": entry_info["entry_time"].isoformat()
                        if hasattr(entry_info["entry_time"], "isoformat")
                        else str(entry_info["entry_time"]),
                    "exit_time": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_dollar": round(pnl_dollar, 4),
                    "exit_reason": exit_reason,
                    "bars_held": bars_held,
                    "was_us_session": entry_info["was_us_session"],
                    "bb_width_pctl": round(entry_info["bb_width_pctl"], 2),
                    "adx": round(entry_info["adx"], 2),
                    "volume_ratio": round(entry_info["volume_ratio"], 4),
                    "atr_at_entry": round(entry_info["atr_at_entry"], 2),
                })
                in_position = False
                entry_info = {}
                bars_held = 0

        else:
            bb_upper = row.get("bb_upper")
            bb_lower = row.get("bb_lower")
            atr = row.get("atr")
            close = row["close"]

            if any(pd.isna(x) for x in [bb_upper, bb_lower, atr,
                                         row.get("bb_width_pctl"),
                                         row.get("adx"),
                                         row.get("volume_ratio")]):
                continue

            squeeze_ok = False
            lookback_start = max(0, i - params.squeeze_lookback)
            for j in range(lookback_start, i + 1):
                if _squeeze_conditions_met(df.iloc[j], params):
                    squeeze_ok = True
                    break

            if not squeeze_ok:
                continue

            direction = None
            if close > bb_upper:
                direction = "LONG"
            elif close < bb_lower:
                direction = "SHORT"

            if direction is None:
                continue

            entry_price = close
            if direction == "LONG":
                sl = entry_price - atr * params.sl_atr_mult
                tp = entry_price + atr * params.tp_atr_mult
            else:
                sl = entry_price + atr * params.sl_atr_mult
                tp = entry_price - atr * params.tp_atr_mult

            hour_utc = ts.hour if hasattr(ts, "hour") else pd.Timestamp(ts).hour
            is_us_session = 12 <= hour_utc <= 20

            in_position = True
            bars_held = 0
            entry_info = {
                "entry_time": ts,
                "direction": direction,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
                "was_us_session": is_us_session,
                "bb_width_pctl": row["bb_width_pctl"],
                "adx": row["adx"],
                "volume_ratio": row["volume_ratio"],
                "atr_at_entry": atr,
            }

    return trades


# ═══════════════════════════════════════════════════════════════
# 4. ANALYSIS & OUTPUT
# ═══════════════════════════════════════════════════════════════

def compute_stats(trades: list[dict], margin: float) -> dict:
    if not trades:
        return {"n": 0}

    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]

    wr = len(wins) / n * 100
    total_return = df["pnl_dollar"].sum()

    gross_profit = wins["pnl_dollar"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl_dollar"].sum()) if len(losses) > 0 else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    returns = df["pnl_dollar"].values / margin
    if len(returns) > 1 and returns.std() > 0:
        avg_hold = df["bars_held"].mean()
        trades_per_year = 365 * 24 / avg_hold if avg_hold > 0 else 365
        sharpe = (returns.mean() / returns.std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0

    exit_dist = {}
    for reason in ["TP", "SL", "MAX_HOLD"]:
        subset = df[df["exit_reason"] == reason]
        exit_dist[reason] = {
            "count": len(subset),
            "pct": len(subset) / n * 100,
            "avg_pnl": subset["pnl_pct"].mean() if len(subset) > 0 else 0,
        }

    session_stats = {}
    for label, mask in [("US (12-20 UTC)", df["was_us_session"] == True),
                        ("Non-US", df["was_us_session"] == False)]:
        subset = df[mask]
        if len(subset) == 0:
            session_stats[label] = {"n": 0}
            continue
        s_wins = subset[subset["pnl_pct"] > 0]
        s_losses = subset[subset["pnl_pct"] <= 0]
        s_gp = s_wins["pnl_dollar"].sum() if len(s_wins) > 0 else 0
        s_gl = abs(s_losses["pnl_dollar"].sum()) if len(s_losses) > 0 else 0
        session_stats[label] = {
            "n": len(subset),
            "wr": len(s_wins) / len(subset) * 100,
            "pf": s_gp / s_gl if s_gl > 0 else float("inf"),
            "ret": subset["pnl_dollar"].sum(),
        }

    df["entry_month"] = pd.to_datetime(df["entry_time"]).dt.to_period("M")
    monthly = {}
    for month, group in df.groupby("entry_month"):
        m_wins = len(group[group["pnl_pct"] > 0])
        m_gp = group[group["pnl_pct"] > 0]["pnl_dollar"].sum() if m_wins > 0 else 0
        m_gl = abs(group[group["pnl_pct"] <= 0]["pnl_dollar"].sum()) if len(group) - m_wins > 0 else 0
        monthly[str(month)] = {
            "trades": len(group),
            "wins": m_wins,
            "wr": m_wins / len(group) * 100,
            "pf": m_gp / m_gl if m_gl > 0 else (float("inf") if m_gp > 0 else 0),
            "ret": group["pnl_dollar"].sum(),
        }

    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "wr": wr, "pf": pf, "total_return": total_return,
        "sharpe": sharpe,
        "avg_win_pct": wins["pnl_pct"].mean() if len(wins) > 0 else 0,
        "avg_loss_pct": losses["pnl_pct"].mean() if len(losses) > 0 else 0,
        "avg_hold": df["bars_held"].mean(),
        "longs": len(df[df["direction"] == "LONG"]),
        "shorts": len(df[df["direction"] == "SHORT"]),
        "exit_dist": exit_dist,
        "session": session_stats,
        "monthly": monthly,
    }


def print_comparison(symbol: str, days: int, stats_orig: dict, stats_adj: dict) -> None:
    w = 80
    print("\n" + "=" * w)
    print(f"  SQUEEZE-EXPLOSION BACKTEST — {symbol} {days}d (1h)")
    print(f"  Side-by-Side: ORIGINAL vs ADJUSTED params")
    print("=" * w)

    if stats_orig["n"] == 0 and stats_adj["n"] == 0:
        print("\n  NO TRADES generated by either variant.")
        return

    print(f"\n{'─── Summary Stats ───':^{w}}")
    hdr = f"  {'Metric':<24} {'ORIGINAL':>18} {'ADJUSTED':>18} {'Delta':>14}"
    print(hdr)
    print(f"  {'─' * 24} {'─' * 18} {'─' * 18} {'─' * 14}")

    o, a = stats_orig, stats_adj

    def row(label, o_val, a_val, fmt=".1f", prefix="", suffix=""):
        o_str = f"{prefix}{o_val:{fmt}}{suffix}" if o["n"] > 0 else "N/A"
        a_str = f"{prefix}{a_val:{fmt}}{suffix}" if a["n"] > 0 else "N/A"
        d_str = f"{a_val - o_val:+{fmt}}" if o["n"] > 0 and a["n"] > 0 else "—"
        print(f"  {label:<24} {o_str:>18} {a_str:>18} {d_str:>14}")

    row("Total trades", o.get("n", 0), a.get("n", 0), "d")
    row("Win rate %", o.get("wr", 0), a.get("wr", 0), ".1f", suffix="%")

    def pf_str(v):
        return "inf" if v == float("inf") else f"{v:.2f}"
    print(f"  {'Profit factor':<24} {pf_str(o.get('pf', 0)) if o['n'] > 0 else 'N/A':>18} "
          f"{pf_str(a.get('pf', 0)) if a['n'] > 0 else 'N/A':>18} {'—':>14}")

    row("Total return $", o.get("total_return", 0), a.get("total_return", 0), ".2f", prefix="$")
    row("Sharpe", o.get("sharpe", 0), a.get("sharpe", 0), ".2f")
    row("Avg hold (hours)", o.get("avg_hold", 0), a.get("avg_hold", 0), ".1f")

    # Session breakdown
    print(f"\n{'─── Session Breakdown ───':^{w}}")
    for label in ["US (12-20 UTC)", "Non-US"]:
        os_ = o.get("session", {}).get(label, {"n": 0})
        as_ = a.get("session", {}).get(label, {"n": 0})
        print(f"  {label:<18} "
              f"ORIG: n={os_.get('n', 0)} WR={os_.get('wr', 0):.1f}% PF={pf_str(os_.get('pf', 0))} ${os_.get('ret', 0):+.2f}"
              f"  |  ADJ: n={as_.get('n', 0)} WR={as_.get('wr', 0):.1f}% PF={pf_str(as_.get('pf', 0))} ${as_.get('ret', 0):+.2f}")

    print("\n" + "=" * w)


def save_trades(trades: list[dict], path: Path) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    print(f"\nTrades saved to {path}  ({len(trades)} records)")


# ═══════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Squeeze-Explosion Strategy Backtest")
    parser.add_argument("--coin", required=True, choices=list(COIN_DEFAULTS.keys()),
                        help="Coin to backtest (BTC, ETH, SOL, XRP, POL)")
    parser.add_argument("--days", type=int, default=360, help="Lookback days (default: 360)")
    parser.add_argument("--margin", type=float, default=None,
                        help="Fixed margin per trade (default: per-coin)")
    parser.add_argument("--save", action="store_true", help="Save trades to JSONL")
    args = parser.parse_args()

    coin = COIN_DEFAULTS[args.coin]
    symbol = coin["symbol"]
    margin = args.margin or coin["margin"]

    df = fetch_binance_futures(symbol, "1h", args.days)
    print("\nComputing indicators...")
    df = compute_all_indicators(df)

    valid = df.dropna(subset=["bb_width_pctl", "adx", "atr", "volume_ratio"])
    print(f"Valid rows: {len(valid)} / {len(df)}")

    print(f"\nRunning ORIGINAL: pctl<{ORIGINAL.bb_pctl_thresh} ADX<{ORIGINAL.adx_thresh} "
          f"vol<{ORIGINAL.vol_ratio_thresh} SL={ORIGINAL.sl_atr_mult}x TP={ORIGINAL.tp_atr_mult}x")
    trades_orig = run_backtest(df, ORIGINAL, margin)
    print(f"  -> {len(trades_orig)} trades")

    print(f"\nRunning ADJUSTED: pctl<{ADJUSTED.bb_pctl_thresh} ADX<{ADJUSTED.adx_thresh} "
          f"vol<{ADJUSTED.vol_ratio_thresh} SL={ADJUSTED.sl_atr_mult}x TP={ADJUSTED.tp_atr_mult}x")
    trades_adj = run_backtest(df, ADJUSTED, margin)
    print(f"  -> {len(trades_adj)} trades")

    stats_orig = compute_stats(trades_orig, margin)
    stats_adj = compute_stats(trades_adj, margin)
    print_comparison(symbol, args.days, stats_orig, stats_adj)

    if args.save:
        out_path = OUT_DIR / f"bt_{symbol}_{args.days}d_squeeze_trades.jsonl"
        save_trades(trades_adj, out_path)


if __name__ == "__main__":
    main()
