#!/usr/bin/env python3
"""
swing_backtest.py — PM Token Swing Trading Backtest (Independent Pipeline)

設計決定：
- 同 binary betting pipeline 完全獨立 — 唔預測結果，炒 token 價差
- 模擬 "BTC > K by T" 預測市場，用 digital option 模型定價
- Edge 來源：vol mismatch（24h realized vol vs 7d realized vol）
  → 短期 vol > 長期 vol = OTM token 被低估 → 買入
- 退出：TP / SL / 時間 / 到期結算
- Kelly 半 Kelly 落注，5% bankroll 上限
- 目標：Sharpe ≥ 1.0（μ/σ ≥ 1 per-trade returns）

核心 thesis:
Token 價格根據 implied vol 定價。當 realized vol (短期) > implied vol (長期)，
OTM token 被系統性低估。買入後等 vol 反映 / 標的移動 → 賣出獲利。
類似 options vol trading，但用喺 prediction market tokens。

用法:
    cd ~/projects/axc-trading
    PYTHONPATH=.:scripts python3 polymarket/backtest/swing_backtest.py --days 90
"""

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

# ─── Path setup ───
_PROJECT_ROOT = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.fetch_historical import fetch_klines_range

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
LOG_DIR = os.path.join(_PROJECT_ROOT, "polymarket", "logs")
SQRT_8760 = math.sqrt(8760)  # annualize hourly vol


# ═══════════════════════════════════════
#  Digital Option Model
# ═══════════════════════════════════════

def norm_cdf(x: float) -> float:
    """Standard normal CDF (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / 1.4142135623730951))


def token_price_model(spot: float, strike: float, vol: float, tau_yr: float) -> float:
    """Binary digital option: P(S_T > K) = Φ(ln(S/K) / (σ√τ)).

    設計決定：用 log-normal model（Black-Scholes digital），唔係 linear approximation。
    忽略 risk-free rate（crypto 市場，短期影響可忽略）。
    """
    if tau_yr <= 1e-10:
        return 0.99 if spot > strike else 0.01
    if strike <= 0 or spot <= 0:
        return 0.50
    sqrt_tau = math.sqrt(tau_yr)
    d = math.log(spot / strike) / (vol * sqrt_tau)
    return max(0.01, min(0.99, norm_cdf(d)))


# ═══════════════════════════════════════
#  Volatility Precomputation
# ═══════════════════════════════════════

def precompute_vols(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rolling annualized vol: 24h (model) vs 168h (market).

    設計決定：precompute 全部避免 inner loop 重複計算。
    Floor at 10% 防止除零。
    """
    n = len(closes)
    log_p = np.log(closes)
    vol_24h = np.full(n, 0.50)
    vol_168h = np.full(n, 0.50)

    for i in range(25, n):
        lr = np.diff(log_p[i - 24: i + 1])
        vol_24h[i] = max(0.10, float(np.std(lr)) * SQRT_8760)

    for i in range(169, n):
        lr = np.diff(log_p[i - 168: i + 1])
        vol_168h[i] = max(0.10, float(np.std(lr)) * SQRT_8760)

    return vol_24h, vol_168h


# ═══════════════════════════════════════
#  Data Structures
# ═══════════════════════════════════════

@dataclass
class Market:
    strike: float
    start_idx: int
    end_idx: int
    duration: int     # total hours
    outcome: bool     # spot > strike at expiry


@dataclass
class SwingTrade:
    strike: float
    entry_idx: int
    entry_price: float      # token buy price (incl spread)
    entry_btc: float
    edge: float
    shares: float
    bet_size: float
    market_outcome: bool    # for binary comparison
    exit_idx: int = 0
    exit_price: float = 0.0
    exit_type: str = ""     # tp / sl / time / expiry_win / expiry_lose
    pnl: float = 0.0
    ret_pct: float = 0.0


# ═══════════════════════════════════════
#  Market Generation
# ═══════════════════════════════════════

STRIKES_PCT = (-0.05, -0.03, -0.01, 0.01, 0.03, 0.05)


def make_markets(
    closes: np.ndarray,
    duration: int = 72,
    strikes: tuple = STRIKES_PCT,
) -> list[Market]:
    """Non-overlapping markets, each `duration` hours."""
    markets = []
    idx = 0
    while idx + duration <= len(closes):
        p0 = closes[idx]
        p_end = closes[idx + duration - 1]
        for pct in strikes:
            K = p0 * (1 + pct)
            markets.append(Market(K, idx, idx + duration - 1, duration, p_end > K))
        idx += duration
    return markets


# ═══════════════════════════════════════
#  Strategy Engine
# ═══════════════════════════════════════

def run_strategy(
    closes: np.ndarray,
    vol_s: np.ndarray,
    vol_l: np.ndarray,
    markets: list[Market],
    *,
    tp: float = 0.50,
    sl: float = 0.20,
    edge_min: float = 0.08,
    price_lo: float = 0.10,
    price_hi: float = 0.45,
    max_hold: int = 24,
    bankroll0: float = 100.0,
    kelly_f: float = 0.5,
    max_bet: float = 0.05,
    fee: float = 0.02,
    spread: float = 0.02,
    use_momentum: bool = True,
    mom_window: int = 12,
) -> tuple[list[SwingTrade], float, float]:
    """Run swing strategy. Returns (trades, final_bankroll, max_dd_pct)."""
    trades: list[SwingTrade] = []
    bank = bankroll0
    peak = bank
    max_dd = 0.0

    for mkt in markets:
        dur = mkt.end_idx - mkt.start_idx  # = duration - 1
        pos = None

        for h in range(dur + 1):
            ix = mkt.start_idx + h
            if ix >= len(closes):
                break
            S = closes[ix]
            hours_left = dur + 1 - h
            tau_yr = hours_left / 8760.0

            fair = token_price_model(S, mkt.strike, vol_s[ix], tau_yr)
            mkt_price = token_price_model(S, mkt.strike, vol_l[ix], tau_yr)

            # ── No position → entry check ──
            if pos is None:
                if hours_left < max(6, mkt.duration * 0.15):
                    continue
                if mkt_price < price_lo or mkt_price > price_hi:
                    continue

                edge = fair - mkt_price
                if edge < edge_min:
                    continue

                # Momentum filter: BTC trending toward strike
                if use_momentum and ix >= mom_window:
                    mom = (closes[ix] - closes[ix - mom_window]) / closes[ix - mom_window]
                    toward = (mom > 0 and mkt.strike > S) or (mom < 0 and mkt.strike < S)
                    if not toward:
                        continue

                buy_p = mkt_price + spread / 2.0

                # Half-Kelly sizing
                rr = tp / sl
                est_p = min(0.65, 0.50 + edge)
                k = max(0.0, (est_p * rr - (1 - est_p)) / rr) * kelly_f
                bet = min(bank * k, bank * max_bet)
                if bet < 0.50:
                    continue

                shares = (bet * (1 - fee)) / buy_p

                pos = SwingTrade(
                    strike=mkt.strike, entry_idx=ix,
                    entry_price=round(buy_p, 4), entry_btc=S,
                    edge=round(edge, 4), shares=shares,
                    bet_size=round(bet, 2), market_outcome=mkt.outcome,
                )

            # ── Have position → exit check ──
            else:
                sell_p = max(0.005, mkt_price - spread / 2.0)
                ret = (sell_p - pos.entry_price) / pos.entry_price
                held = ix - pos.entry_idx

                ex_type = None
                final_p = sell_p

                if ret >= tp:
                    ex_type = "tp"
                elif ret <= -sl:
                    ex_type = "sl"
                elif held >= max_hold:
                    ex_type = "time"
                elif h == dur:
                    # Expiry resolution
                    final_p = 0.98 if mkt.outcome else 0.02
                    ex_type = "expiry_win" if mkt.outcome else "expiry_lose"

                if ex_type:
                    sell_fee = pos.shares * abs(final_p) * fee
                    pnl = pos.shares * (final_p - pos.entry_price) - sell_fee
                    pos.exit_idx = ix
                    pos.exit_price = round(final_p, 4)
                    pos.exit_type = ex_type
                    pos.pnl = round(pnl, 4)
                    pos.ret_pct = round(pnl / pos.bet_size * 100, 2) if pos.bet_size > 0 else 0.0
                    trades.append(pos)
                    bank += pnl
                    pos = None
                    peak = max(peak, bank)
                    dd = (peak - bank) / peak if peak > 0 else 0
                    max_dd = max(max_dd, dd)

        # Orphan position at market end
        if pos is not None:
            final_p = 0.98 if mkt.outcome else 0.02
            pnl = pos.shares * (final_p - pos.entry_price)
            pos.exit_idx = mkt.end_idx
            pos.exit_price = final_p
            pos.exit_type = "expiry_win" if mkt.outcome else "expiry_lose"
            pos.pnl = round(pnl, 4)
            pos.ret_pct = round(pnl / pos.bet_size * 100, 2) if pos.bet_size > 0 else 0.0
            trades.append(pos)
            bank += pnl
            pos = None
            peak = max(peak, bank)
            dd = (peak - bank) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

    return trades, round(bank, 2), round(max_dd * 100, 2)


# ═══════════════════════════════════════
#  Statistics
# ═══════════════════════════════════════

def calc_stats(trades: list[SwingTrade]) -> dict:
    if not trades:
        return {"n": 0, "sharpe": 0.0}

    rets = np.array([t.ret_pct / 100 for t in trades])
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)

    mu = float(np.mean(rets))
    sigma = float(np.std(rets)) if n > 1 else 1.0
    sharpe = mu / sigma if sigma > 1e-10 else 0.0

    exit_ct: dict[str, int] = {}
    for t in trades:
        exit_ct[t.exit_type] = exit_ct.get(t.exit_type, 0) + 1

    w_pnl = [t.pnl for t in trades if t.pnl > 0]
    l_pnl = [t.pnl for t in trades if t.pnl <= 0]
    total_w = sum(w_pnl) if w_pnl else 0
    total_l = sum(l_pnl) if l_pnl else 0

    return {
        "n": n, "wins": wins, "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "total_pnl": round(sum(t.pnl for t in trades), 2),
        "mean_ret": round(mu * 100, 2),
        "std_ret": round(sigma * 100, 2),
        "sharpe": round(sharpe, 3),
        "avg_win": round(np.mean(w_pnl), 2) if w_pnl else 0,
        "avg_loss": round(np.mean(l_pnl), 2) if l_pnl else 0,
        "profit_factor": round(abs(total_w / total_l), 2) if total_l != 0 else float("inf"),
        "exit_types": exit_ct,
        "mu_1sig": round((mu + sigma) * 100, 2),
        "mu_neg1sig": round((mu - sigma) * 100, 2),
    }


def binary_comparison(trades: list[SwingTrade]) -> dict:
    """Compute what-if: same entries, hold to resolution (binary outcome)."""
    if not trades:
        return {}
    binary_pnl = []
    for t in trades:
        if t.market_outcome:
            bp = t.shares * (0.98 - t.entry_price)
        else:
            bp = t.shares * (0.02 - t.entry_price)
        binary_pnl.append(round(bp, 4))

    bp_arr = np.array(binary_pnl)
    wins_b = int(np.sum(bp_arr > 0))
    return {
        "total_pnl": round(float(np.sum(bp_arr)), 2),
        "win_rate": round(wins_b / len(bp_arr), 4) if len(bp_arr) > 0 else 0,
        "avg_pnl": round(float(np.mean(bp_arr)), 4),
    }


# ═══════════════════════════════════════
#  Grid Search
# ═══════════════════════════════════════

def grid_search(
    closes: np.ndarray,
    vol_s: np.ndarray,
    vol_l: np.ndarray,
    markets: list[Market],
) -> list[dict]:
    """Search TP × SL × Edge × Momentum for optimal parameters."""
    results = []
    tp_vals = [0.25, 0.40, 0.60, 0.80, 1.00]
    sl_vals = [0.10, 0.15, 0.20, 0.30]
    edge_vals = [0.03, 0.05, 0.08, 0.12]
    mom_vals = [True, False]

    for tp_v in tp_vals:
        for sl_v in sl_vals:
            for edge_v in edge_vals:
                for mom_v in mom_vals:
                    tds, final, dd = run_strategy(
                        closes, vol_s, vol_l, markets,
                        tp=tp_v, sl=sl_v, edge_min=edge_v,
                        use_momentum=mom_v,
                    )
                    st = calc_stats(tds)
                    if st["n"] >= 5:
                        results.append({
                            "tp": tp_v, "sl": sl_v, "edge": edge_v,
                            "mom": mom_v,
                            "rr": round(tp_v / sl_v, 1),
                            **st, "final": final, "max_dd": dd,
                        })

    return sorted(results, key=lambda r: -r["sharpe"])


# ═══════════════════════════════════════
#  Main Backtest
# ═══════════════════════════════════════

def run_backtest(days: int = 90, market_hours: int = 72) -> dict:
    now = datetime.now(timezone.utc)
    end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Extra 8 days for vol warmup (168h window needs history)
    fetch_start = end_dt - timedelta(days=days + 8)
    market_start = end_dt - timedelta(days=days)
    start_ms = int(fetch_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000) - 1
    warmup = 8 * 24  # warmup hours

    print(f"╔══ Swing Trading Backtest ═══════════════════════════╗")
    print(f"║ {market_start:%Y-%m-%d} → {end_dt:%Y-%m-%d} ({days}d)")
    print(f"║ Market: BTC > K ({market_hours}h, 6 strikes)")
    print(f"║ Model: Vol-edge (σ_24h vs σ_7d digital option)")
    print(f"║ Sizing: Half-Kelly, 5% bankroll cap")
    print(f"║ Target: Sharpe ≥ 1.0")
    print(f"╚════════════════════════════════════════════════════╝\n")

    # ── 1. Fetch ──
    print("[1/5] Fetching BTC 1h klines...")
    t0 = time.time()
    klines = fetch_klines_range(SYMBOL, "1h", start_ms, end_ms)
    closes = klines["close"].astype(float).values
    print(f"  {len(klines)} candles | {time.time() - t0:.1f}s")

    # ── 2. Vol surface ──
    print("\n[2/5] Computing volatility...")
    vol_s, vol_l = precompute_vols(closes)
    active = closes[warmup:]
    vs_active = vol_s[warmup:]
    vl_active = vol_l[warmup:]
    rising_pct = np.sum(vs_active > vl_active) / max(1, len(vs_active))
    print(f"  σ_24h > σ_7d: {rising_pct:.1%} (vol rising periods)")
    print(f"  σ_24h mean: {np.mean(vs_active):.1%} | σ_7d mean: {np.mean(vl_active):.1%}")

    # ── 3. Markets ──
    print(f"\n[3/5] Generating {market_hours}h markets...")
    # Generate from warmup onwards
    sub_closes = closes[warmup:]
    markets = make_markets(sub_closes, duration=market_hours)
    # Shift indices to global
    for m in markets:
        m.start_idx += warmup
        m.end_idx += warmup

    yes_n = sum(1 for m in markets if m.outcome)
    print(f"  {len(markets)} markets | YES: {yes_n} ({yes_n / max(1, len(markets)):.1%})")

    # ── 4. Grid search ──
    total_combos = 5 * 4 * 4 * 2
    print(f"\n[4/5] Grid search ({total_combos} combos)...")
    grid = grid_search(closes, vol_s, vol_l, markets)

    if not grid:
        print("  ❌ No parameter combo generated ≥5 trades.")
        print("     Try: --days 180 or --market-hours 24")
        return {"error": "insufficient trades"}

    print(f"\n  Top 8 by Sharpe:")
    print(f"  {'TP':>5} {'SL':>5} {'Edge':>6} {'Mom':>4} {'R:R':>5} {'Sharpe':>7} {'WR':>6} {'N':>5} {'PnL':>8} {'DD':>6}")
    print(f"  {'─' * 62}")
    for r in grid[:8]:
        m_str = "Y" if r.get("mom", True) else "N"
        print(f"  {r['tp']:>4.0%} {r['sl']:>4.0%} {r['edge']:>5.0%} {m_str:>4} {r['rr']:>5.1f}"
              f" {r['sharpe']:>7.3f} {r['win_rate']:>5.1%} {r['n']:>5}"
              f" ${r['total_pnl']:>+7.2f} {r['max_dd']:>5.1f}%")

    if len(grid) > 8:
        print(f"\n  Worst 3:")
        for r in grid[-3:]:
            m_str = "Y" if r.get("mom", True) else "N"
            print(f"  {r['tp']:>4.0%} {r['sl']:>4.0%} {r['edge']:>5.0%} {m_str:>4} {r['rr']:>5.1f}"
                  f" {r['sharpe']:>7.3f} {r['win_rate']:>5.1%} {r['n']:>5}"
                  f" ${r['total_pnl']:>+7.2f} {r['max_dd']:>5.1f}%")

    # ── 5. Best params detailed run ──
    best = grid[0]
    best_mom = best.get("mom", True)
    print(f"\n[5/5] Best: TP={best['tp']:.0%} SL={best['sl']:.0%} Edge={best['edge']:.0%}"
          f" Mom={'Y' if best_mom else 'N'} (R:R={best['rr']:.1f})")
    trades, final, max_dd = run_strategy(
        closes, vol_s, vol_l, markets,
        tp=best["tp"], sl=best["sl"], edge_min=best["edge"],
        use_momentum=best_mom,
    )
    stats = calc_stats(trades)
    binary = binary_comparison(trades)

    # ═══ Report ═══
    print(f"\n{'═' * 60}")
    print(f"  RESULTS")
    print(f"{'═' * 60}")

    print(f"\n  Bankroll: $100.00 → ${final:.2f} ({final - 100:+.2f})")
    print(f"  Trades:   {stats['n']} ({stats['wins']}W / {stats['losses']}L)")
    print(f"  Win rate: {stats['win_rate']:.1%}")
    print(f"  Avg win:  ${stats['avg_win']:+.2f} | Avg loss: ${stats['avg_loss']:+.2f}")
    print(f"  Profit factor: {stats['profit_factor']:.2f}")
    print(f"  Max DD:   {max_dd:.1f}%")

    print(f"\n  ── Returns Distribution ──")
    print(f"  μ  = {stats['mean_ret']:+.2f}%")
    print(f"  σ  = {stats['std_ret']:.2f}%")
    print(f"  Sharpe = {stats['sharpe']:.3f}  {'✅ ≥ 1.0' if stats['sharpe'] >= 1.0 else '❌ < 1.0'}")
    print(f"  μ+1σ = {stats['mu_1sig']:+.2f}%  {'✅ > 0' if stats['mu_1sig'] > 0 else '❌ ≤ 0'}")
    print(f"  μ-1σ = {stats['mu_neg1sig']:+.2f}%  (downside per trade)")

    # ── Swing vs Binary ──
    if binary:
        print(f"\n  ── Swing vs Binary (same entries) ──")
        print(f"  {'Method':<12} {'PnL':>10} {'WR':>8}")
        print(f"  {'─' * 32}")
        print(f"  {'Swing':<12} ${stats['total_pnl']:>+8.2f} {stats['win_rate']:>7.1%}")
        print(f"  {'Binary':<12} ${binary['total_pnl']:>+8.2f} {binary['win_rate']:>7.1%}")
        delta = stats['total_pnl'] - binary['total_pnl']
        print(f"  {'Delta':<12} ${delta:>+8.2f}  ← {'swing 贏' if delta > 0 else 'binary 贏'}")

    # ── Exit types ──
    print(f"\n  ── Exit Types ──")
    for et, ct in sorted(stats["exit_types"].items(), key=lambda x: -x[1]):
        print(f"  {et:<15} {ct:>4} ({ct / stats['n']:.1%})")

    # ── Histogram ──
    if trades:
        rets = [t.ret_pct for t in trades]
        print(f"\n  ── Histogram ──")
        bins = [(-100, -40), (-40, -20), (-20, -10), (-10, 0),
                (0, 10), (10, 20), (20, 40), (40, 100)]
        for lo_b, hi_b in bins:
            ct = sum(1 for r in rets if lo_b <= r < hi_b)
            bar = "█" * min(50, ct * 2)
            print(f"  {lo_b:>+4}% ~ {hi_b:>+4}%: {ct:>3} {bar}")

    # ── Equity curve ──
    if trades:
        print(f"\n  ── Equity ──")
        eq = 100.0
        step = max(1, len(trades) // 12)
        for i, t in enumerate(trades):
            eq += t.pnl
            if i % step == 0 or i == len(trades) - 1:
                bar_w = int((eq - 90) * 1.5)
                bar = "█" * max(0, min(60, bar_w)) if bar_w >= 0 else "░" * min(15, -bar_w)
                print(f"  #{i + 1:>4}  ${eq:>7.2f}  {bar}")

    # ── Kelly analysis ──
    if stats["n"] >= 10:
        wr = stats["win_rate"]
        avg_w = abs(stats["avg_win"])
        avg_l = abs(stats["avg_loss"])
        if avg_l > 0:
            b = avg_w / avg_l
            kelly_opt = (wr * b - (1 - wr)) / b
            print(f"\n  ── Kelly Analysis ──")
            print(f"  Empirical: WR={wr:.1%}, W/L ratio={b:.2f}")
            print(f"  Full Kelly: {kelly_opt:.1%} of bankroll")
            print(f"  Half Kelly: {kelly_opt / 2:.1%} (recommended)")
            print(f"  Quarter Kelly: {kelly_opt / 4:.1%} (conservative)")

    # ── Save ──
    os.makedirs(LOG_DIR, exist_ok=True)
    result_path = os.path.join(LOG_DIR, "swing_backtest_results.json")
    output = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "version": "v1_vol_edge",
        "period": f"{market_start:%Y-%m-%d} → {end_dt:%Y-%m-%d}",
        "days": days,
        "symbol": SYMBOL,
        "market_hours": market_hours,
        "best_params": {
            "tp": best["tp"], "sl": best["sl"],
            "edge": best["edge"], "rr": best["rr"],
        },
        "stats": stats,
        "binary_comparison": binary,
        "final_bankroll": final,
        "max_drawdown": max_dd,
        "grid_top10": grid[:10],
        "sharpe_target_met": stats["sharpe"] >= 1.0,
    }

    fd, tmp = tempfile.mkstemp(dir=LOG_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        os.replace(tmp, result_path)
        print(f"\nResults → {result_path}")
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return output


def main():
    parser = argparse.ArgumentParser(description="PM Token Swing Backtest")
    parser.add_argument("--days", type=int, default=90,
                        help="Backtest period in days (default: 90)")
    parser.add_argument("--market-hours", type=int, default=72,
                        help="Market duration in hours (default: 72 = 3 days)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run_backtest(days=args.days, market_hours=args.market_hours)


if __name__ == "__main__":
    main()
