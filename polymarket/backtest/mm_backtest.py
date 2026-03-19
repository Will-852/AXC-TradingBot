#!/usr/bin/env python3
"""
mm_backtest.py — Market-Making Backtest (k9q-style)

設計決定：
- 模擬 Polymarket 15M/5M/1HR "Bitcoin Up or Down" 市場
- 用 Binance 1m klines 驅動市場價格演化（digital option formula）
- k9q 三階段邏輯：開盤雙邊 → gradual unwind → lottery tickets
- Grid search 關鍵參數，搵最佳 MM 配置
- 三方比較：pure spread capture vs k9q-style vs directional

核心 thesis：
k9q 唔預測方向，靠 spread capture + 動態管理兩邊倉位。
問題：呢個策略喺 BTC 15M market 到底有幾 robust？

用法:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/mm_backtest.py --days 90
    PYTHONPATH=.:scripts python3 polymarket/backtest/mm_backtest.py --days 360 --window 5
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
LOG_DIR = os.path.join(_PROJECT_ROOT, "polymarket", "logs")
ONE_MIN_MS = 60_000

_norm = NormalDist()


# ═══════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════

@dataclass
class MMParams:
    """Strategy parameters for grid search.

    設計決定：
    - half_spread 控制開盤 entry quality（越大越安全但 skip 更多 market）
    - unwind 參數控制止損節奏（k9q 用 gradual，唔係 hard SL）
    - lottery 係 deep OTM 投注，低成本高 payoff
    - taker_fee 2% 係 Polymarket 15M 標準 fee
    """
    # Opening phase
    half_spread: float = 0.03
    lots_per_side: int = 5
    lot_size: float = 41.0

    # Unwind phase（gradual exit losing side）
    unwind_trigger_pct: float = 0.12   # start when losing side drops 12% from entry
    unwind_step_pct: float = 0.05      # sell 1 lot per additional 5% drop
    max_unwind_lots: int = 4           # keep ≥1 lot as residual

    # Lottery phase（buy deep OTM）
    lottery_threshold: float = 0.10    # buy when price < 10¢
    lottery_lots: int = 2
    lottery_after_minute: int = 8      # only buy lottery after minute 8

    # Add winner（momentum follow-through）
    add_winner_pct: float = 0.15       # add when winner up 15% from entry
    add_winner_lots: int = 2

    # Fees & limits
    taker_fee: float = 0.02
    max_cost_per_market: float = 500.0

    # Fill rate model（P0 fix: 唔再假設 100% fill）
    # fill_rate 代表成功雙邊 fill 嘅概率。
    # 設計決定：wider spread → higher fill（冇人搶），但 Polymarket
    # 平均有 ~3-5 個 active MM，所以 base fill rate ~30-50%。
    fill_rate: float = 0.40

    @property
    def label(self) -> str:
        lt = f"_lt{self.lottery_threshold:.0%}" if self.lottery_threshold > 0 else ""
        fr = f"_fr{self.fill_rate:.0%}" if self.fill_rate < 1.0 else ""
        return f"hs{self.half_spread:.0%}_ut{self.unwind_trigger_pct:.0%}_us{self.unwind_step_pct:.0%}{lt}{fr}"


@dataclass
class MarketResult:
    """Result of one market window simulation."""
    window_start: int = 0
    result: str = "SKIP"
    combined_entry: float = 0.0

    # Shares at resolution
    up_shares_final: float = 0
    down_shares_final: float = 0

    # Cost components
    entry_cost: float = 0
    unwind_revenue: float = 0
    lottery_cost: float = 0
    add_winner_cost: float = 0

    # Final
    total_cost: float = 0
    payout: float = 0
    pnl: float = 0
    roi: float = 0
    n_trades: int = 0


# ═══════════════════════════════════════
#  Market Price Model
# ═══════════════════════════════════════

def estimate_1m_vol(klines_1m: pd.DataFrame, lookback: int = 60) -> pd.Series:
    """Rolling per-minute log-return volatility.

    設計決定：用 60 分鐘 lookback（1 小時），夠穩定但唔會 lag 太多。
    """
    close = klines_1m["close"].astype(float)
    log_ret = np.log(close / close.shift(1))
    vol = log_ret.rolling(lookback, min_periods=20).std()
    return vol.bfill().fillna(0.001)


def fair_prob_up(s_current: float, s_open: float, vol_1m: float,
                 minutes_remaining: int) -> float:
    """P(S_T > S_0 | S_t) — Digital option fair probability.

    設計決定：用 Black-Scholes lognormal model。
    P(UP) = Φ(ln(S_t / S_0) / (σ √τ))
    τ = minutes remaining，σ = per-minute vol。
    """
    if minutes_remaining <= 0:
        if s_current > s_open:
            return 0.995
        if s_current < s_open:
            return 0.005
        return 0.5

    if vol_1m <= 0 or s_current <= 0 or s_open <= 0:
        return 0.5

    sigma_tau = vol_1m * math.sqrt(minutes_remaining)
    if sigma_tau < 1e-10:
        return 0.995 if s_current > s_open else 0.005

    d = math.log(s_current / s_open) / sigma_tau
    return max(0.005, min(0.995, _norm.cdf(d)))


# ═══════════════════════════════════════
#  Strategy Engine — k9q Three-Phase MM
# ═══════════════════════════════════════

def simulate_one_market(
    candles_1m: list[dict],
    vol_1m: float,
    params: MMParams,
    window_minutes: int = 15,
) -> MarketResult:
    """Simulate k9q-style MM on one market window.

    Phase A (t=0-1min): 兩邊落 limit order，以 fair ± half_spread 入場
    Phase B (t=2..N-2): 監察方向
        - 輸面跌 > trigger → 逐 lot 賣出（gradual unwind）
        - 贏面升 > threshold → 加注
    Phase C (t=8+): 輸面跌到 lottery_threshold 以下 → 買 lottery tickets
    Resolution (t=N): 贏面 → $1，輸面 → $0
    """
    if len(candles_1m) < window_minutes:
        return MarketResult()

    s_open = candles_1m[0]["open"]
    s_close = candles_1m[-1]["close"]
    actual = "UP" if s_close > s_open else "DOWN"

    mr = MarketResult(
        window_start=int(candles_1m[0].get("open_time", 0)),
        result=actual,
    )

    # ── Phase A: Opening buys (based on first candle OPEN, not close) ──
    # P1 fix: 用 open 唔用 close，避免 look-ahead bias
    s_1 = candles_1m[0]["open"]
    p_up_0 = fair_prob_up(s_1, s_open, vol_1m, window_minutes)

    up_entry = max(0.01, p_up_0 - params.half_spread)
    down_entry = max(0.01, (1 - p_up_0) - params.half_spread)
    combined = up_entry + down_entry
    mr.combined_entry = combined

    # Skip if no positive EV (combined ≥ 1.0)
    if combined >= 1.0:
        return mr

    up_shares = params.lots_per_side * params.lot_size
    down_shares = params.lots_per_side * params.lot_size
    up_cost = up_shares * up_entry * (1 + params.taker_fee)
    down_cost = down_shares * down_entry * (1 + params.taker_fee)
    entry_cost = up_cost + down_cost

    # Cap per market
    if entry_cost > params.max_cost_per_market:
        scale = params.max_cost_per_market / entry_cost
        up_shares *= scale
        down_shares *= scale
        up_cost *= scale
        down_cost *= scale
        entry_cost = up_cost + down_cost

    mr.entry_cost = entry_cost
    mr.total_cost = entry_cost
    mr.n_trades = 2

    # State tracking
    up_s = up_shares
    down_s = down_shares
    unwind_rev = 0.0
    lottery_cost = 0.0
    add_cost = 0.0
    lots_unwound = 0
    added_winner = False

    # ── Phase B + C: Manage position (minute 2 → N-1) ──
    for i in range(1, window_minutes - 1):
        s_t = candles_1m[i]["close"]
        mins_left = window_minutes - (i + 1)
        p_up = fair_prob_up(s_t, s_open, vol_1m, mins_left)

        fair_up = max(0.005, min(0.995, p_up))
        fair_down = 1.0 - fair_up

        up_winning = p_up > 0.5

        if up_winning:
            losing_fair = fair_down
            losing_entry = down_entry
            # -- Gradual unwind DOWN --
            if down_s > params.lot_size and lots_unwound < params.max_unwind_lots:
                drop_pct = (losing_entry - losing_fair) / losing_entry if losing_entry > 0 else 0
                threshold = params.unwind_trigger_pct + lots_unwound * params.unwind_step_pct
                if drop_pct >= threshold:
                    sell_qty = min(params.lot_size, down_s - params.lot_size)
                    sell_price = max(0.005, losing_fair - 0.005)
                    rev = sell_qty * sell_price * (1 - params.taker_fee)
                    down_s -= sell_qty
                    unwind_rev += rev
                    lots_unwound += 1
                    mr.n_trades += 1

            # -- Lottery on DOWN --
            if (i >= params.lottery_after_minute
                    and losing_fair < params.lottery_threshold
                    and params.lottery_lots > 0
                    and lottery_cost < params.lot_size * 0.15 * params.lottery_lots):
                buy_qty = params.lot_size * params.lottery_lots
                buy_price = losing_fair + 0.005
                cost = buy_qty * buy_price * (1 + params.taker_fee)
                down_s += buy_qty
                lottery_cost += cost
                mr.n_trades += 1

            # -- Add winner (UP) --
            if not added_winner and fair_up > up_entry * (1 + params.add_winner_pct):
                add_qty = params.lot_size * params.add_winner_lots
                add_price = fair_up + 0.005
                cost = add_qty * add_price * (1 + params.taker_fee)
                up_s += add_qty
                add_cost += cost
                added_winner = True
                mr.n_trades += 1
        else:
            losing_fair = fair_up
            losing_entry = up_entry
            # -- Gradual unwind UP --
            if up_s > params.lot_size and lots_unwound < params.max_unwind_lots:
                drop_pct = (losing_entry - losing_fair) / losing_entry if losing_entry > 0 else 0
                threshold = params.unwind_trigger_pct + lots_unwound * params.unwind_step_pct
                if drop_pct >= threshold:
                    sell_qty = min(params.lot_size, up_s - params.lot_size)
                    sell_price = max(0.005, losing_fair - 0.005)
                    rev = sell_qty * sell_price * (1 - params.taker_fee)
                    up_s -= sell_qty
                    unwind_rev += rev
                    lots_unwound += 1
                    mr.n_trades += 1

            # -- Lottery on UP --
            if (i >= params.lottery_after_minute
                    and losing_fair < params.lottery_threshold
                    and params.lottery_lots > 0
                    and lottery_cost < params.lot_size * 0.15 * params.lottery_lots):
                buy_qty = params.lot_size * params.lottery_lots
                buy_price = losing_fair + 0.005
                cost = buy_qty * buy_price * (1 + params.taker_fee)
                up_s += buy_qty
                lottery_cost += cost
                mr.n_trades += 1

            # -- Add winner (DOWN) --
            if not added_winner and fair_down > down_entry * (1 + params.add_winner_pct):
                add_qty = params.lot_size * params.add_winner_lots
                add_price = fair_down + 0.005
                cost = add_qty * add_price * (1 + params.taker_fee)
                down_s += add_qty
                add_cost += cost
                added_winner = True
                mr.n_trades += 1

    # ── Resolution ──
    mr.up_shares_final = up_s
    mr.down_shares_final = down_s
    mr.unwind_revenue = unwind_rev
    mr.lottery_cost = lottery_cost
    mr.add_winner_cost = add_cost
    mr.total_cost = entry_cost + lottery_cost + add_cost
    mr.payout = up_s if actual == "UP" else down_s

    mr.pnl = mr.payout + mr.unwind_revenue - mr.total_cost
    mr.roi = mr.pnl / mr.total_cost if mr.total_cost > 0 else 0

    return mr


# ═══════════════════════════════════════
#  Resolution Scalping (BoneReader-style)
# ═══════════════════════════════════════

@dataclass
class ScalpParams:
    """Resolution scalping parameters.

    設計決定：
    - entry_minute 控制幾時入場（越遲越 certain 但 profit 越薄）
    - min_certainty 過濾唔夠 certain 嘅 market（skip flat markets）
    - fee 用 complement model：fee = rate × (1 - price)
    - position_usd 係每注金額（BoneReader 用 $1K-$12K，我哋 backtest 用 $1K）
    """
    entry_minute: int = 12          # 第幾分鐘入場（0-indexed, 15M window）
    min_certainty: float = 0.90     # 最低 fair prob 先入場
    position_usd: float = 1000.0    # 每注金額
    fee_rate: float = 0.015         # fee = rate × (1-price)

    @property
    def label(self) -> str:
        return f"scalp_m{self.entry_minute}_c{self.min_certainty:.0%}_f{self.fee_rate:.1%}"


def simulate_resolution_scalp(
    candles_1m: list[dict],
    vol_1m: float,
    params: ScalpParams,
    window_minutes: int = 15,
) -> MarketResult:
    """BoneReader-style: buy near-certain winner late in the window.

    1. 等到 entry_minute（e.g., minute 12 of 15）
    2. 計算 fair probability
    3. 如果 P(winner) > min_certainty → 買 winning side
    4. Hold to resolution → $1.00 payout
    5. Fee = rate × (1 - price)：near $1.00 buys 幾乎零 fee
    """
    if len(candles_1m) < window_minutes:
        return MarketResult()

    s_open = candles_1m[0]["open"]
    s_close = candles_1m[-1]["close"]
    actual = "UP" if s_close > s_open else "DOWN"

    mr = MarketResult(
        window_start=int(candles_1m[0].get("open_time", 0)),
        result=actual,
    )

    # Check at entry_minute
    entry_idx = min(params.entry_minute, window_minutes - 2)
    s_entry = candles_1m[entry_idx]["close"]
    mins_left = window_minutes - (entry_idx + 1)
    p_up = fair_prob_up(s_entry, s_open, vol_1m, mins_left)

    # Determine if certain enough
    if p_up >= params.min_certainty:
        side = "UP"
        entry_price = min(0.995, p_up + 0.005)  # buy at ask (slight premium)
    elif (1 - p_up) >= params.min_certainty:
        side = "DOWN"
        entry_price = min(0.995, (1 - p_up) + 0.005)
    else:
        return mr  # not certain enough, skip

    # Fee = rate × (1 - price) — complement model
    fee_per_share = params.fee_rate * (1 - entry_price)
    cost_per_share = entry_price + fee_per_share
    shares = params.position_usd / cost_per_share

    total_cost = shares * cost_per_share
    payout = shares if side == actual else 0

    mr.entry_cost = total_cost
    mr.total_cost = total_cost
    mr.combined_entry = entry_price
    mr.payout = payout
    mr.pnl = payout - total_cost
    mr.roi = mr.pnl / total_cost if total_cost > 0 else 0
    mr.n_trades = 1

    return mr


def run_scalp_strategy(windows: list, vol_values: np.ndarray,
                       vol_index: np.ndarray, params: ScalpParams,
                       window_minutes: int = 15) -> dict:
    """Run resolution scalping across all windows."""
    results = []
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    equity_curve = []
    skipped = 0

    for w in windows:
        candles = w["candles"]
        t0 = candles[0]["open_time"]
        idx = min(np.searchsorted(vol_index, t0, side="right") - 1,
                  len(vol_values) - 1)
        idx = max(0, idx)
        v = float(vol_values[idx])

        mr = simulate_resolution_scalp(candles, v, params, window_minutes)
        if mr.result == "SKIP" or mr.total_cost == 0:
            skipped += 1
            continue

        results.append(mr)
        equity += mr.pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        equity_curve.append(equity)

    if not results:
        return {"pnl": 0, "n_markets": 0, "sharpe": 0, "skipped": skipped,
                "label": params.label}

    pnls = np.array([r.pnl for r in results])
    rois = np.array([r.roi for r in results])
    avg_pnl = float(np.mean(pnls))
    std_pnl = float(np.std(pnls))
    sharpe = (avg_pnl / std_pnl * math.sqrt(len(pnls))) if std_pnl > 0 else 0
    wins = sum(1 for p in pnls if p > 0)
    losses = [r for r in results if r.pnl < 0]

    return {
        "label": params.label,
        "n_markets": len(results),
        "skipped": skipped,
        "win_rate": round(wins / len(results) * 100, 1),
        "total_pnl": round(float(np.sum(pnls)), 2),
        "avg_pnl": round(avg_pnl, 4),
        "avg_roi": round(float(np.mean(rois)) * 100, 2),
        "median_roi": round(float(np.median(rois)) * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_dd": round(max_dd, 2),
        "total_trades": len(results),
        "avg_cost": round(float(np.mean([r.total_cost for r in results])), 2),
        "avg_entry_price": round(float(np.mean([r.combined_entry for r in results])), 4),
        "worst_loss": round(float(min(pnls)), 2) if len(pnls) > 0 else 0,
        "n_losses": len(losses),
        "avg_loss": round(float(np.mean([r.pnl for r in losses])), 2) if losses else 0,
        "equity_curve": equity_curve,
        "trade_log": results,
    }


# ═══════════════════════════════════════
#  Backtest Runner
# ═══════════════════════════════════════

def prepare_windows(klines_1m: pd.DataFrame, window_minutes: int = 15) -> list:
    """Split 1m klines into non-overlapping market windows.

    設計決定：
    - 對齊到 window_minutes 邊界（模擬 Polymarket 嘅固定時間 window）
    - 只保留完整 window（有足夠 candle 數）
    - BTC 24/7 交易，所以冇 gap
    """
    windows = []
    df = klines_1m.sort_values("open_time").reset_index(drop=True)
    open_times = df["open_time"].values.astype(np.int64)
    window_ms = window_minutes * ONE_MIN_MS

    first_t = int(open_times[0])
    boundary = first_t - (first_t % window_ms) + window_ms

    while boundary + window_ms <= int(open_times[-1]):
        mask = (open_times >= boundary) & (open_times < boundary + window_ms)
        subset = df.loc[mask]

        if len(subset) >= window_minutes:
            candles = []
            for _, row in subset.head(window_minutes).iterrows():
                candles.append({
                    "open_time": int(row["open_time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
            windows.append({"start": boundary, "candles": candles})

        boundary += window_ms

    return windows


def run_strategy(windows: list, vol_values: np.ndarray, vol_index: np.ndarray,
                 params: MMParams, window_minutes: int = 15,
                 rng: np.random.Generator | None = None) -> dict:
    """Run MM strategy across all windows.

    P0 fix: fill_rate 模型 — 用 RNG 決定每個 market 是否雙邊 fill。
    唔 fill 嘅 market = skip（冇 entry），模擬其他 MM 搶先成交。
    """
    results = []
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    equity_curve = []
    skipped = 0
    fill_skipped = 0

    if rng is None:
        rng = np.random.default_rng(42)

    for w in windows:
        # Fill rate gate: 隨機決定呢個 market 是否雙邊 fill
        if params.fill_rate < 1.0 and rng.random() > params.fill_rate:
            fill_skipped += 1
            skipped += 1
            continue

        candles = w["candles"]
        t0 = candles[0]["open_time"]
        idx = min(np.searchsorted(vol_index, t0, side="right") - 1,
                  len(vol_values) - 1)
        idx = max(0, idx)
        v = float(vol_values[idx])

        mr = simulate_one_market(candles, v, params, window_minutes)
        if mr.result == "SKIP" or mr.total_cost == 0:
            skipped += 1
            continue

        results.append(mr)
        equity += mr.pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        equity_curve.append(equity)

    if not results:
        return {"pnl": 0, "n_markets": 0, "sharpe": 0, "skipped": skipped,
                "label": params.label}

    pnls = np.array([r.pnl for r in results])
    rois = np.array([r.roi for r in results])

    avg_pnl = float(np.mean(pnls))
    std_pnl = float(np.std(pnls))
    sharpe = (avg_pnl / std_pnl * math.sqrt(len(pnls))) if std_pnl > 0 else 0

    wins = sum(1 for p in pnls if p > 0)

    return {
        "label": params.label,
        "n_markets": len(results),
        "skipped": skipped,
        "win_rate": round(wins / len(results) * 100, 1),
        "total_pnl": round(float(np.sum(pnls)), 2),
        "avg_pnl": round(avg_pnl, 4),
        "avg_roi": round(float(np.mean(rois)) * 100, 2),
        "median_roi": round(float(np.median(rois)) * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_dd": round(max_dd, 2),
        "total_trades": sum(r.n_trades for r in results),
        # Decomposition
        "entry_cost": round(sum(r.entry_cost for r in results), 2),
        "unwind_revenue": round(sum(r.unwind_revenue for r in results), 2),
        "lottery_cost": round(sum(r.lottery_cost for r in results), 2),
        "add_winner_cost": round(sum(r.add_winner_cost for r in results), 2),
        "payout": round(sum(r.payout for r in results), 2),
        # Per-market stats
        "avg_cost": round(float(np.mean([r.total_cost for r in results])), 2),
        "avg_combined_entry": round(float(np.mean([r.combined_entry for r in results])), 4),
        "fill_skipped": fill_skipped,
        "fill_rate_actual": round(len(results) / (len(results) + fill_skipped) * 100, 1) if (len(results) + fill_skipped) > 0 else 0,
        "equity_curve": equity_curve,
        "trade_log": results,
    }


# ═══════════════════════════════════════
#  Main Runner — A/B Test: MM vs Scalp
# ═══════════════════════════════════════

def run_backtest(days: int = 90, window_minutes: int = 15):
    """A/B Test: Strategy A (Market Making) vs Strategy B (Resolution Scalping).

    Both use train/test split. Grid search on train, evaluate on test.
    Final comparison on identical test set for fair A/B.
    """

    end_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days)
    split_dt = start_dt + timedelta(days=days // 2)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    split_ms = int(split_dt.timestamp() * 1000)

    print(f"\n{'='*70}")
    print(f"  A/B TEST: Market Making vs Resolution Scalping — {SYMBOL}")
    print(f"  Period: {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d} ({days}d)")
    print(f"  Train:  {start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}")
    print(f"  Test:   {split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}")
    print(f"  Window: {window_minutes}min")
    print(f"{'='*70}\n")

    # ── Fetch data ──
    print("  Fetching 1m klines...")
    klines_1m = fetch_klines_range(SYMBOL, "1m", start_ms, end_ms)
    print(f"  ✓ {len(klines_1m):,} candles")

    all_windows = prepare_windows(klines_1m, window_minutes)
    train_windows = [w for w in all_windows if w["start"] < split_ms]
    test_windows = [w for w in all_windows if w["start"] >= split_ms]
    print(f"  ✓ {len(all_windows):,} total windows "
          f"(train: {len(train_windows):,} | test: {len(test_windows):,})")

    vol = estimate_1m_vol(klines_1m, lookback=60)
    vol_values = vol.values
    vol_index = klines_1m["open_time"].values.astype(np.int64)

    # Deterministic RNG for reproducibility
    rng_train = np.random.default_rng(42)
    rng_test = np.random.default_rng(123)

    # ══════════════════════════════════════════
    #  STRATEGY A: Market Making (k9q-style)
    # ══════════════════════════════════════════
    print("  ═══ STRATEGY A: Market Making (k9q) ═══")
    print("  Grid search on TRAIN...")

    mm_grid = []
    half_spreads = [0.03, 0.04, 0.05]
    unwind_triggers = [0.12, 0.20]
    unwind_steps = [0.05, 0.08]
    fill_rates = [0.30, 0.40, 0.50]

    mm_total = len(half_spreads) * len(unwind_triggers) * len(unwind_steps) * len(fill_rates)
    print(f"  {mm_total} MM combos")
    done = 0
    for hs in half_spreads:
        for ut in unwind_triggers:
            for us in unwind_steps:
                for fr in fill_rates:
                    p = MMParams(
                        half_spread=hs, lots_per_side=5, lot_size=41.0,
                        unwind_trigger_pct=ut, unwind_step_pct=us,
                        max_unwind_lots=4, lottery_threshold=0.0,
                        lottery_lots=0, lottery_after_minute=99,
                        add_winner_pct=0.15, add_winner_lots=2,
                        fill_rate=fr,
                    )
                    r = run_strategy(train_windows, vol_values, vol_index,
                                     p, window_minutes,
                                     rng=np.random.default_rng(42))
                    mm_grid.append((r, p))
                    done += 1
                    if done % 20 == 0:
                        print(f"    {done}/{mm_total}...")

    mm_grid.sort(key=lambda x: x[0]["sharpe"], reverse=True)

    # Top 3 MM → test
    mm_test_results = []
    for train_r, params in mm_grid[:3]:
        test_r = run_strategy(test_windows, vol_values, vol_index,
                              params, window_minutes,
                              rng=np.random.default_rng(123))
        mm_test_results.append({"train": train_r, "test": test_r, "params": params})
    mm_test_results.sort(key=lambda x: x["test"]["sharpe"], reverse=True)
    best_mm = mm_test_results[0]["test"] if mm_test_results else None
    best_mm_train = mm_test_results[0]["train"] if mm_test_results else None

    # ══════════════════════════════════════════
    #  STRATEGY B: Resolution Scalping (BoneReader)
    # ══════════════════════════════════════════
    print("\n  ═══ STRATEGY B: Resolution Scalping (BoneReader) ═══")
    print("  Grid search on TRAIN...")

    scalp_grid = []
    entry_minutes_opts = [10, 11, 12, 13]
    certainties = [0.85, 0.90, 0.95, 0.97]
    fee_rates = [0.005, 0.010, 0.015, 0.020]

    scalp_total = len(entry_minutes_opts) * len(certainties) * len(fee_rates)
    print(f"  {scalp_total} scalp combos")
    done = 0
    for em in entry_minutes_opts:
        for mc in certainties:
            for fr_fee in fee_rates:
                sp = ScalpParams(
                    entry_minute=min(em, window_minutes - 2),
                    min_certainty=mc,
                    position_usd=1000.0,
                    fee_rate=fr_fee,
                )
                r = run_scalp_strategy(train_windows, vol_values, vol_index,
                                       sp, window_minutes)
                scalp_grid.append((r, sp))
                done += 1
                if done % 20 == 0:
                    print(f"    {done}/{scalp_total}...")

    scalp_grid.sort(key=lambda x: x[0]["sharpe"], reverse=True)

    # Top 3 scalp → test
    scalp_test_results = []
    for train_r, params in scalp_grid[:3]:
        test_r = run_scalp_strategy(test_windows, vol_values, vol_index,
                                     params, window_minutes)
        scalp_test_results.append({"train": train_r, "test": test_r, "params": params})
    scalp_test_results.sort(key=lambda x: x["test"]["sharpe"], reverse=True)
    best_scalp = scalp_test_results[0]["test"] if scalp_test_results else None
    best_scalp_train = scalp_test_results[0]["train"] if scalp_test_results else None

    # ═══════════════════════════════════════
    #  A/B Report
    # ═══════════════════════════════════════

    print(f"\n{'='*70}")
    print(f"  A/B TEST RESULTS — train {len(train_windows):,} | test {len(test_windows):,}")
    print(f"{'='*70}")

    # Strategy A: MM
    print(f"\n  ── A: MARKET MAKING (k9q-style, best on TEST) ──")
    if best_mm:
        print(f"  Config: {best_mm['label']}")
        print(f"  TRAIN: PnL ${best_mm_train['total_pnl']:>8.2f}  |  Sharpe {best_mm_train['sharpe']:.3f}")
        print(f"  TEST:  ", end="")
        _print_summary(best_mm)
        if best_mm.get("unwind_revenue", 0) > 0:
            print(f"  Unwind recovered: ${best_mm['unwind_revenue']:.2f}")

    # Strategy B: Scalp
    print(f"\n  ── B: RESOLUTION SCALPING (BoneReader-style, best on TEST) ──")
    if best_scalp:
        print(f"  Config: {best_scalp['label']}")
        print(f"  TRAIN: PnL ${best_scalp_train['total_pnl']:>8.2f}  |  Sharpe {best_scalp_train['sharpe']:.3f}")
        print(f"  TEST:  PnL: ${best_scalp['total_pnl']:>10.2f}  |  "
              f"WR: {best_scalp['win_rate']:.1f}%  |  "
              f"Sharpe: {best_scalp['sharpe']:.3f}  |  "
              f"Max DD: ${best_scalp['max_dd']:.2f}")
        print(f"  Markets: {best_scalp['n_markets']:,}  |  "
              f"Skipped: {best_scalp['skipped']:,}  |  "
              f"Avg entry: ${best_scalp.get('avg_entry_price', 0):.4f}  |  "
              f"Avg ROI: {best_scalp['avg_roi']:.2f}%")
        if best_scalp.get("n_losses", 0) > 0:
            print(f"  Losses: {best_scalp['n_losses']}  |  "
                  f"Worst: ${best_scalp['worst_loss']:.2f}  |  "
                  f"Avg loss: ${best_scalp['avg_loss']:.2f}")

    # Top 3 per strategy
    print(f"\n  ── Top 3 MM (TRAIN → TEST) ──")
    print(f"  {'#':<3} {'Config':<40} {'Train Sharpe':>12} {'Test Sharpe':>12} {'Test PnL':>10}")
    for i, tr in enumerate(mm_test_results[:3]):
        print(f"  {i+1:<3} {tr['train']['label']:<40} "
              f"{tr['train']['sharpe']:>11.3f} {tr['test']['sharpe']:>11.3f} "
              f"${tr['test']['total_pnl']:>8.2f}")

    print(f"\n  ── Top 3 Scalp (TRAIN → TEST) ──")
    print(f"  {'#':<3} {'Config':<40} {'Train Sharpe':>12} {'Test Sharpe':>12} {'Test PnL':>10}")
    for i, tr in enumerate(scalp_test_results[:3]):
        print(f"  {i+1:<3} {tr['train']['label']:<40} "
              f"{tr['train']['sharpe']:>11.3f} {tr['test']['sharpe']:>11.3f} "
              f"${tr['test']['total_pnl']:>8.2f}")

    # ══════════════════════════════════════
    #  HEAD-TO-HEAD
    # ══════════════════════════════════════
    if best_mm and best_scalp:
        print(f"\n{'='*70}")
        print(f"  HEAD-TO-HEAD (TEST SET — {split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d})")
        print(f"{'='*70}")
        print(f"  {'Metric':<25} {'A: MM (k9q)':>15} {'B: Scalp (Bone)':>18} {'Winner':>10}")
        print(f"  {'─'*70}")

        metrics = [
            ("Total PnL", best_mm["total_pnl"], best_scalp["total_pnl"], "$"),
            ("Sharpe", best_mm["sharpe"], best_scalp["sharpe"], ""),
            ("Win Rate", best_mm["win_rate"], best_scalp["win_rate"], "%"),
            ("Avg ROI/trade", best_mm["avg_roi"], best_scalp["avg_roi"], "%"),
            ("Max Drawdown", best_mm["max_dd"], best_scalp["max_dd"], "$"),
            ("Markets traded", best_mm["n_markets"], best_scalp["n_markets"], ""),
            ("Avg cost/trade", best_mm["avg_cost"], best_scalp["avg_cost"], "$"),
        ]

        a_wins = 0
        b_wins = 0
        for name, a_val, b_val, unit in metrics:
            if name == "Max Drawdown":
                winner = "A ✅" if a_val < b_val else "B ✅"
            else:
                winner = "A ✅" if a_val > b_val else "B ✅"
            if "A" in winner:
                a_wins += 1
            else:
                b_wins += 1
            if unit == "$":
                a_str = f"${a_val:.2f}"
                b_str = f"${b_val:.2f}"
            elif unit == "%":
                a_str = f"{a_val:.1f}%"
                b_str = f"{b_val:.1f}%"
            else:
                a_str = f"{a_val:.3f}" if isinstance(a_val, float) else str(a_val)
                b_str = f"{b_val:.3f}" if isinstance(b_val, float) else str(b_val)
            print(f"  {name:<25} {a_str:>15} {b_str:>18} {winner:>10}")

        print(f"\n  SCORE: A ({a_wins}) vs B ({b_wins})")

        # Overfit check both
        for label, train, test in [("A: MM", best_mm_train, best_mm),
                                    ("B: Scalp", best_scalp_train, best_scalp)]:
            ts = train["sharpe"]
            tt = test["sharpe"]
            decay = (ts - tt) / ts * 100 if ts > 0 else 0
            status = "✅ robust" if decay < 30 else "⚠️ decay" if decay < 50 else "❌ overfit"
            print(f"  {label}: Train {ts:.3f} → Test {tt:.3f} ({decay:+.1f}%) {status}")

        # Verdict
        print(f"\n  ══ VERDICT ══")
        if best_scalp["sharpe"] > best_mm["sharpe"] and best_scalp["total_pnl"] > best_mm["total_pnl"]:
            print(f"  → B (Resolution Scalping) WINS on both Sharpe and PnL")
        elif best_mm["sharpe"] > best_scalp["sharpe"] and best_mm["total_pnl"] > best_scalp["total_pnl"]:
            print(f"  → A (Market Making) WINS on both Sharpe and PnL")
        else:
            print(f"  → MIXED: need to weigh trade-offs")
            if best_scalp["max_dd"] < best_mm["max_dd"]:
                print(f"    Scalp has lower DD → safer")
            if best_mm["avg_cost"] < best_scalp["avg_cost"]:
                print(f"    MM needs less capital per trade → more accessible")

    # Equity curves
    if best_mm and best_mm.get("equity_curve"):
        _print_equity({"label": "A: MM", "equity_curve": best_mm["equity_curve"]})
    if best_scalp and best_scalp.get("equity_curve"):
        _print_equity({"label": "B: Scalp", "equity_curve": best_scalp["equity_curve"]})

    # ── Save ──
    os.makedirs(LOG_DIR, exist_ok=True)
    result_path = os.path.join(LOG_DIR, "mm_backtest_results.json")
    output = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "version": "v3_ab_test",
        "period": f"{start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "train_period": f"{start_dt:%Y-%m-%d} → {split_dt:%Y-%m-%d}",
        "test_period": f"{split_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "days": days,
        "symbol": SYMBOL,
        "window_minutes": window_minutes,
        "n_windows_train": len(train_windows),
        "n_windows_test": len(test_windows),
        "best_mm_train": _strip(best_mm_train) if best_mm_train else None,
        "best_mm_test": _strip(best_mm) if best_mm else None,
        "best_scalp_train": _strip(best_scalp_train) if best_scalp_train else None,
        "best_scalp_test": _strip(best_scalp) if best_scalp else None,
        "mm_top3": [{"train": _strip(t["train"]), "test": _strip(t["test"])}
                    for t in mm_test_results[:3]],
        "scalp_top3": [{"train": _strip(t["train"]), "test": _strip(t["test"])}
                       for t in scalp_test_results[:3]],
    }

    fd, tmp = tempfile.mkstemp(dir=LOG_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        os.replace(tmp, result_path)
        print(f"\n  Results → {result_path}")
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return output


def _strip(r: dict) -> dict:
    """Remove bulky fields for JSON output."""
    return {k: v for k, v in r.items()
            if k not in ("equity_curve", "trade_log")}


def _print_summary(r: dict):
    """Print strategy summary line."""
    print(f"  PnL: ${r['total_pnl']:>10.2f}  |  "
          f"WR: {r['win_rate']:.1f}%  |  "
          f"Sharpe: {r['sharpe']:.3f}  |  "
          f"Max DD: ${r['max_dd']:.2f}")
    print(f"  Markets: {r['n_markets']:,}  |  "
          f"Skipped: {r.get('skipped', 0):,}  |  "
          f"Avg ROI: {r['avg_roi']:.2f}%  |  "
          f"Avg cost: ${r['avg_cost']:.2f}")


def _print_equity(best: dict):
    """Print ASCII equity curve."""
    eq = best["equity_curve"]
    if not eq:
        return
    print(f"\n  ── Equity Curve ({best['label']}) ──")
    step = max(1, len(eq) // 15)
    for i in range(0, len(eq), step):
        v = eq[i]
        bar_w = int(v * 0.3)
        if bar_w >= 0:
            bar = "█" * min(40, bar_w)
        else:
            bar = "░" * min(20, -bar_w)
        print(f"  #{i+1:>5}  ${v:>10.2f}  {bar}")
    print(f"  #{len(eq):>5}  ${eq[-1]:>10.2f}  ← final")


def main():
    parser = argparse.ArgumentParser(
        description="MM Backtest (k9q-style market making)")
    parser.add_argument("--days", type=int, default=90,
                        help="Backtest period in days (default: 90)")
    parser.add_argument("--window", type=int, default=15,
                        help="Window minutes: 5, 15, or 60 (default: 15)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run_backtest(days=args.days, window_minutes=args.window)


if __name__ == "__main__":
    main()
