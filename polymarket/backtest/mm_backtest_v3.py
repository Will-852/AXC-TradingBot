#!/usr/bin/env python3
"""
mm_backtest_v3.py — Strategy C backtest: 兩邊買 + hold to resolution

3 個獨立 fill model，用戶自己判斷邊個最接近現實：

Model A「真實數據」: 基於 LampStore/Anon 嘅實際觀察
  - 75% 兩邊 fill → 100% win (combined < $1.00 = guaranteed)
  - 15% 只一邊 fill → 50/50
  - 10% 冇 fill

Model B「中等」: 比真實保守
  - 50% 兩邊 fill
  - 25% 只一邊 fill → 50/50
  - 25% 冇 fill

Model C「悲觀 + adverse selection」: 假設大部分 fill 係 adverse
  - 30% 兩邊 fill
  - 30% 只一邊 fill → 35% win (adverse selection)
  - 40% 冇 fill

設計決定：
- 用 Binance 1m klines 驅動 BTC 15M windows
- Import production market_maker.py code（唔自己重寫）
- 輸出完整 trade log CSV

用法:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/mm_backtest_v3.py \
        --days 180 --bankroll 20.88 --bet-pct 0.23
"""

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import NormalDist

import numpy as np
import pandas as pd

_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_ROOT, os.path.join(_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range
from polymarket.strategy.market_maker import (
    MMConfig, compute_fair_up, plan_opening,
    MMMarketState, PlannedOrder,
)
from polymarket.core.context import PolyMarket

SYMBOL = "BTCUSDT"
WINDOW_MIN = 15
ONE_MIN_MS = 60_000
LOG_DIR = os.path.join(_ROOT, "polymarket", "logs")


# ─── Fill Models ───

@dataclass
class FillModel:
    name: str
    both_pct: float      # P(both sides fill)
    one_pct: float       # P(one side fills)
    one_win_rate: float  # P(win | one side fill)
    # Implicit: no_fill_pct = 1 - both_pct - one_pct

MODELS = {
    "A_real": FillModel("A: Real data (LampStore/Anon)", 0.75, 0.15, 0.50),
    "B_moderate": FillModel("B: Moderate", 0.50, 0.25, 0.50),
    "C_pessimistic": FillModel("C: Pessimistic + adverse", 0.30, 0.30, 0.35),
}


# ─── Backtest engine ───

@dataclass
class TradeRecord:
    window_start: str
    btc_open: float
    btc_close: float
    fair_entry: float
    up_price: float
    down_price: float
    combined: float
    shares: float
    cost: float
    fill_type: str       # BOTH / UP_ONLY / DOWN_ONLY / SKIP / NO_FILL
    result: str          # UP / DOWN
    payout: float
    pnl: float
    bankroll_after: float


def load_data(days: int):
    """Fetch BTC data once, reuse across models."""
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    print(f"  Fetching {days}d BTC 1m klines...")
    df = fetch_klines_range(SYMBOL, "1m", start_ms, end_ms)
    if df.empty:
        return None, []

    df["close"] = df["close"].astype(float)
    df["open"] = df["open"].astype(float)
    df["ts"] = df["open_time"].astype(int)

    # Rolling 60m vol
    log_ret = np.log(df["close"] / df["close"].shift(1))
    df["vol_1m"] = log_ret.rolling(60, min_periods=20).std().bfill().fillna(0.001)

    # 15M window boundaries
    first_ts = int(df["ts"].iloc[0])
    last_ts = int(df["ts"].iloc[-1])
    window_ms = WINDOW_MIN * ONE_MIN_MS
    first_window = (first_ts // window_ms + 1) * window_ms
    windows = list(range(first_window, last_ts - window_ms, window_ms))

    print(f"  {len(windows)} markets over {days}d")
    return df, windows


def run_backtest(df: pd.DataFrame, windows: list, bankroll: float,
                 bet_pct: float, half_spread: float,
                 model: FillModel, seed: int = 42) -> list[TradeRecord]:
    """Run backtest with specified fill model."""
    config = MMConfig(half_spread=half_spread, bet_pct=bet_pct)
    trades: list[TradeRecord] = []
    br = bankroll
    rng = np.random.default_rng(seed)

    for w_start in windows:
        w_end = w_start + WINDOW_MIN * ONE_MIN_MS
        mask = (df["ts"] >= w_start) & (df["ts"] < w_end)
        chunk = df[mask]
        if len(chunk) < 5:
            continue

        btc_open = float(chunk.iloc[0]["open"])
        btc_close = float(chunk.iloc[-1]["close"])
        ts_str = datetime.fromtimestamp(w_start / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Fair at entry (minute 1)
        btc_1min = float(chunk.iloc[1]["close"])
        vol_1min = float(chunk.iloc[1]["vol_1m"])
        fair_entry = compute_fair_up(btc_1min, btc_open, vol_1min, WINDOW_MIN - 1)

        # Production sizing
        dummy_market = PolyMarket(
            condition_id=f"bt_{w_start}", title="BTC 15M backtest",
            category="crypto_15m", yes_token_id="UP", no_token_id="DOWN",
            liquidity=15000,
        )
        orders = plan_opening(dummy_market, fair_entry, config, bankroll=br)

        if not orders:
            trades.append(TradeRecord(
                window_start=ts_str, btc_open=btc_open, btc_close=btc_close,
                fair_entry=fair_entry, up_price=0, down_price=0,
                combined=0, shares=0, cost=0, fill_type="SKIP",
                result="", payout=0, pnl=0, bankroll_after=br,
            ))
            continue

        up_order = [o for o in orders if o.outcome == "UP"][0]
        dn_order = [o for o in orders if o.outcome == "DOWN"][0]
        combined = up_order.price + dn_order.price
        shares = up_order.size

        # Resolution
        result = "UP" if btc_close >= btc_open else "DOWN"

        # Fill model
        roll = rng.random()
        if roll < model.both_pct:
            # Both sides fill
            cost = shares * combined
            payout = shares  # winning side $1.00
            pnl = payout - cost
            fill_type = "BOTH"

        elif roll < model.both_pct + model.one_pct:
            # One side fills
            # Which side? Random (real data doesn't show consistent bias)
            if rng.random() < 0.5:
                fill_price = up_order.price
                won = (result == "UP")
                fill_type = "UP_ONLY"
            else:
                fill_price = dn_order.price
                won = (result == "DOWN")
                fill_type = "DOWN_ONLY"

            # Apply win rate (adverse selection reduces win rate below 50%)
            if rng.random() >= model.one_win_rate:
                won = False

            cost = shares * fill_price
            payout = shares if won else 0
            pnl = payout - cost
        else:
            # No fill
            cost = 0
            payout = 0
            pnl = 0
            fill_type = "NO_FILL"

        br += pnl

        trades.append(TradeRecord(
            window_start=ts_str, btc_open=btc_open, btc_close=btc_close,
            fair_entry=fair_entry, up_price=up_order.price, down_price=dn_order.price,
            combined=combined, shares=shares, cost=cost, fill_type=fill_type,
            result=result, payout=payout, pnl=pnl, bankroll_after=br,
        ))

        if br <= 1.0:
            break

    return trades


def analyze_compact(trades: list[TradeRecord], bankroll_init: float, days: int, label: str):
    """One-line summary."""
    filled = [t for t in trades if t.fill_type in ("BOTH", "UP_ONLY", "DOWN_ONLY")]
    both = [t for t in trades if t.fill_type == "BOTH"]
    one_side = [t for t in trades if t.fill_type in ("UP_ONLY", "DOWN_ONLY")]
    skips = [t for t in trades if t.fill_type == "SKIP"]

    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in filled if t.pnl > 0)
    losses = sum(1 for t in filled if t.pnl < 0)
    wr = wins / len(filled) * 100 if filled else 0
    daily = total_pnl / days if days > 0 else 0
    final = trades[-1].bankroll_after if trades else bankroll_init

    # Drawdown
    pnls = [t.pnl for t in trades]
    if pnls:
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum + bankroll_init)
        dd = ((peak - (cum + bankroll_init)) / peak).max()
    else:
        dd = 0

    return {
        "label": label,
        "filled": len(filled),
        "both": len(both),
        "one": len(one_side),
        "skips": len(skips),
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": total_pnl,
        "daily": daily,
        "dd": dd,
        "final": final,
    }


def analyze_detailed(trades: list[TradeRecord], bankroll_init: float, days: int):
    """Detailed analysis."""
    filled = [t for t in trades if t.fill_type in ("BOTH", "UP_ONLY", "DOWN_ONLY")]
    both = [t for t in trades if t.fill_type == "BOTH"]
    one_side = [t for t in trades if t.fill_type in ("UP_ONLY", "DOWN_ONLY")]

    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in filled if t.pnl > 0)
    losses = sum(1 for t in filled if t.pnl < 0)

    print(f"\n  ── Trades ──")
    print(f"  Total markets: {len(trades)} | Filled: {len(filled)} | "
          f"Both: {len(both)} | One-side: {len(one_side)}")
    print(f"  Skipped: {sum(1 for t in trades if t.fill_type == 'SKIP')} | "
          f"No-fill: {sum(1 for t in trades if t.fill_type == 'NO_FILL')}")

    if not filled:
        print("  No filled trades!")
        return

    wr = wins / len(filled) * 100
    print(f"\n  ── PnL ──")
    print(f"  Total: ${total_pnl:.2f} | W/L: {wins}/{losses} ({wr:.1f}%)")
    print(f"  $/day: ${total_pnl/days:.2f} | ROI: {total_pnl/bankroll_init*100:.1f}%")

    if both:
        bp = sum(t.pnl for t in both)
        print(f"\n  Both-fill: {len(both)} trades | PnL ${bp:.2f} | "
              f"Avg ${bp/len(both):.2f}/trade | WR {sum(1 for t in both if t.pnl>0)/len(both)*100:.0f}%")

    if one_side:
        op = sum(t.pnl for t in one_side)
        ow = sum(1 for t in one_side if t.pnl > 0)
        print(f"  One-side: {len(one_side)} trades | PnL ${op:.2f} | "
              f"Avg ${op/len(one_side):.2f}/trade | WR {ow/len(one_side)*100:.0f}%")

    # Drawdown
    pnls = [t.pnl for t in trades]
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum + bankroll_init)
    dd = ((peak - (cum + bankroll_init)) / peak).max()
    print(f"\n  Max DD: {dd:.1%} | Final: ${trades[-1].bankroll_after:.2f}")

    # Daily stats
    daily = {}
    for t in trades:
        day = t.window_start[:10]
        daily[day] = daily.get(day, 0) + t.pnl
    if daily:
        arr = np.array(list(daily.values()))
        pos = (arr > 0).sum()
        sharpe = arr.mean() / arr.std() * math.sqrt(365) if arr.std() > 0 else 0
        print(f"  Days: {len(daily)} | Positive: {pos} ({pos/len(daily)*100:.0f}%) | "
              f"Sharpe: {sharpe:.1f}")

    # Show first 10 filled trades for eyeball check
    print(f"\n  ── First 10 Filled Trades (verify yourself) ──")
    print(f"  {'Time':<17} {'BTC Open':>10} {'BTC Close':>10} {'Combined':>8} "
          f"{'Shares':>6} {'Fill':>10} {'Result':>6} {'PnL':>8} {'BR':>8}")
    count = 0
    for t in trades:
        if t.fill_type in ("BOTH", "UP_ONLY", "DOWN_ONLY"):
            print(f"  {t.window_start:<17} ${t.btc_open:>9.2f} ${t.btc_close:>9.2f} "
                  f"${t.combined:>7.3f} {t.shares:>6.1f} {t.fill_type:>10} "
                  f"{t.result:>6} ${t.pnl:>7.2f} ${t.bankroll_after:>7.2f}")
            count += 1
            if count >= 10:
                break


def save_trade_log(trades: list[TradeRecord], path: str):
    """Save CSV for user to verify every trade."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "window", "btc_open", "btc_close", "fair_entry",
            "up_price", "dn_price", "combined", "shares", "cost",
            "fill_type", "result", "payout", "pnl", "bankroll",
        ])
        for t in trades:
            if t.fill_type == "SKIP":
                continue  # don't flood CSV with skips
            writer.writerow([
                t.window_start, f"{t.btc_open:.2f}", f"{t.btc_close:.2f}",
                f"{t.fair_entry:.4f}",
                f"{t.up_price:.2f}", f"{t.down_price:.2f}", f"{t.combined:.3f}",
                f"{t.shares:.2f}", f"{t.cost:.2f}",
                t.fill_type, t.result, f"{t.payout:.2f}", f"{t.pnl:.2f}",
                f"{t.bankroll_after:.2f}",
            ])


def main():
    ap = argparse.ArgumentParser(description="MM v3 Backtest — Strategy C")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--bankroll", type=float, default=20.88)
    ap.add_argument("--bet-pct", type=float, default=0.23)
    ap.add_argument("--half-spread", type=float, default=0.025)
    args = ap.parse_args()

    print(f"\n{'='*70}")
    print(f"  MM v3 Backtest — Strategy C")
    print(f"  {args.days}d | ${args.bankroll} bankroll | {args.bet_pct:.0%} bet | "
          f"spread {args.half_spread:.1%}")
    print(f"{'='*70}")

    # Load data once
    df, windows = load_data(args.days)
    if df is None:
        return

    # ═══════════════════════════════════════
    # Part 1: Compare 3 models at user's bankroll
    # ═══════════════════════════════════════
    print(f"\n  ── Part 1: ${args.bankroll} bankroll × {args.bet_pct:.0%} bet ──")
    print(f"  {'Model':<40} {'Filled':>6} {'Both':>5} {'1-side':>6} "
          f"{'W/L':>7} {'WR':>5} {'PnL':>9} {'$/day':>7} {'DD':>6} {'Final':>8}")
    print(f"  {'-'*100}")

    for key, model in MODELS.items():
        trades = run_backtest(df, windows, args.bankroll, args.bet_pct,
                              args.half_spread, model)
        r = analyze_compact(trades, args.bankroll, args.days, model.name)
        print(f"  {r['label']:<40} {r['filled']:>6} {r['both']:>5} {r['one']:>6} "
              f"{r['wins']:>3}/{r['losses']:<3} {r['wr']:>4.0f}% "
              f"${r['pnl']:>8.2f} ${r['daily']:>6.2f} {r['dd']:>5.0%} ${r['final']:>7.2f}")

    # ═══════════════════════════════════════
    # Part 2: Compare bankrolls (Model A — real data)
    # ═══════════════════════════════════════
    print(f"\n  ── Part 2: Different bankrolls (Model A: real data) ──")
    print(f"  {'Config':<25} {'Filled':>6} {'Both':>5} {'1-side':>6} "
          f"{'W/L':>7} {'PnL':>9} {'$/day':>7} {'DD':>6} {'Final':>8}")
    print(f"  {'-'*85}")

    model_a = MODELS["A_real"]
    configs = [
        (20.88, 0.23, "Your $20.88 × 23%"),
        (50, 0.10, "$50 × 10%"),
        (50, 0.23, "$50 × 23%"),
        (100, 0.05, "$100 × 5%"),
        (100, 0.10, "$100 × 10%"),
        (200, 0.05, "$200 × 5%"),
    ]

    for br, pct, label in configs:
        trades = run_backtest(df, windows, br, pct, args.half_spread, model_a)
        r = analyze_compact(trades, br, args.days, label)
        print(f"  {label:<25} {r['filled']:>6} {r['both']:>5} {r['one']:>6} "
              f"{r['wins']:>3}/{r['losses']:<3} ${r['pnl']:>8.2f} "
              f"${r['daily']:>6.2f} {r['dd']:>5.0%} ${r['final']:>7.2f}")

    # ═══════════════════════════════════════
    # Part 3: Detailed Model A at user's bankroll
    # ═══════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  Part 3: Detailed — Model A at ${args.bankroll}")
    print(f"{'='*70}")

    trades = run_backtest(df, windows, args.bankroll, args.bet_pct,
                          args.half_spread, model_a)
    analyze_detailed(trades, args.bankroll, args.days)

    # Save trade log
    log_path = os.path.join(LOG_DIR, "mm_backtest_v3_trades.csv")
    save_trade_log(trades, log_path)
    print(f"\n  Trade log: {log_path}")

    # ═══════════════════════════════════════
    # Part 4: Mathematical expectation (no bankroll constraint)
    # ═══════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  Part 4: Pure Math (no bankroll constraint, fixed 5 shares)")
    print(f"{'='*70}")

    for key, model in MODELS.items():
        combined = 2 * (0.50 - args.half_spread)  # $0.95 at fair=0.50
        shares = 5.0
        both_profit = shares * (1.0 - combined)  # $0.25
        one_win = shares * (1.0 - (0.50 - args.half_spread))  # ~$2.63
        one_lose = -shares * (0.50 - args.half_spread)  # ~-$2.38

        ev_both = model.both_pct * both_profit
        ev_one_win = model.one_pct * model.one_win_rate * one_win
        ev_one_lose = model.one_pct * (1 - model.one_win_rate) * one_lose
        ev_total = ev_both + ev_one_win + ev_one_lose

        print(f"\n  {model.name}")
        print(f"    Both fill (+${both_profit:.2f}):  {model.both_pct:.0%} × ${both_profit:.2f} = ${ev_both:.3f}")
        print(f"    One win  (+${one_win:.2f}): {model.one_pct:.0%} × {model.one_win_rate:.0%} × ${one_win:.2f} = ${ev_one_win:.3f}")
        print(f"    One lose (-${abs(one_lose):.2f}): {model.one_pct:.0%} × {1-model.one_win_rate:.0%} × ${abs(one_lose):.2f} = ${ev_one_lose:.3f}")
        print(f"    → EV/market: ${ev_total:.3f}")
        print(f"    → 96 markets/day × ${ev_total:.3f} = ${ev_total * 96:.2f}/day = ${ev_total * 96 * 30:.0f}/month")


if __name__ == "__main__":
    main()
