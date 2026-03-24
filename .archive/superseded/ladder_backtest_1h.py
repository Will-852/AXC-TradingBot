#!/usr/bin/env python3
"""
ladder_backtest_1h.py — Backtest wide ladder DCA for 1H Polymarket markets

Uses Binance 1H + 1M klines to simulate:
  1. Direction from 1H candle (close >= open = UP)
  2. Brownian Bridge fair_up at each minute within the hour
  3. Ladder fill simulation (mid drops to rung → fill)
  4. Tiered TP simulation (mid rises to TP level → sell)
  5. Defense thresholds (adverse BTC move → cancel)

Run: cd ~/projects/axc-trading && PYTHONPATH=.:scripts python3 polymarket/analysis/ladder_backtest_1h.py [--days 30]
"""

import argparse
import json
import logging
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("1h_ladder_bt")

_UA = "AXC-Backtest/1.0"
_BINANCE = "https://api.binance.com/api/v3/klines"
_CACHE_DIR = Path(__file__).parent.parent / "logs" / "bt_cache"


# ═══════════════════════════════════════
#  Data fetch (with disk cache)
# ═══════════════════════════════════════

def _get(url: str, timeout: int = 10):
    try:
        with urlopen(Request(url, headers={"User-Agent": _UA}), timeout=timeout) as r:
            return json.loads(r.read())
    except (HTTPError, URLError, TimeoutError) as e:
        log.warning("Fetch failed: %s", e)
        return None


def fetch_1h_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch 1H klines for resolution ground truth. Cached to disk."""
    cache_file = _CACHE_DIR / f"1h_{symbol}_{start_ms}_{end_ms}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    all_klines = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{_BINANCE}?symbol={symbol}&interval=1h"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        data = _get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        for k in data:
            all_klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "close": float(k[4]),
                "high": float(k[2]),
                "low": float(k[3]),
                "volume": float(k[5]),
                "result": "UP" if float(k[4]) >= float(k[1]) else "DOWN",
            })
        cursor = data[-1][0] + 3_600_000
        if len(data) < 1000:
            break
        time.sleep(0.1)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(all_klines))
    return all_klines


def fetch_1m_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch 1M klines within an hour. Cached to disk."""
    cache_file = _CACHE_DIR / f"1m_{symbol}_{start_ms}_{end_ms}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    all_klines = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{_BINANCE}?symbol={symbol}&interval=1m"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        data = _get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        for k in data:
            all_klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        cursor = data[-1][0] + 60_000
        if len(data) < 1000:
            break
        time.sleep(0.05)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(all_klines))
    return all_klines


# ═══════════════════════════════════════
#  Brownian Bridge fair_up simulation
# ═══════════════════════════════════════

_norm_cdf = statistics.NormalDist().cdf


def compute_vol_1m(klines_1m: list[dict], lookback: int = 60) -> float:
    if len(klines_1m) < 10:
        return 0.001
    returns = []
    for i in range(1, min(lookback, len(klines_1m))):
        r = math.log(klines_1m[i]["close"] / klines_1m[i - 1]["close"])
        returns.append(r)
    return max(statistics.stdev(returns), 0.0001) if returns else 0.001


def bridge_fair_up(btc_current: float, btc_open: float, vol_1m: float,
                   minutes_remaining: float) -> float:
    """Brownian Bridge P(UP) using Normal CDF + 10% fat-tail haircut (1H model)."""
    if vol_1m <= 0 or minutes_remaining <= 0:
        return 0.50
    tau = max(minutes_remaining, 0.1)
    sigma_T = vol_1m * math.sqrt(tau)
    if sigma_T < 1e-10:
        return 0.50
    z = math.log(btc_current / btc_open) / sigma_T
    raw = _norm_cdf(z)
    # 10% haircut toward 0.50 (1H uses Normal, not Student-t)
    fair = 0.50 + (raw - 0.50) * 0.90
    return max(0.02, min(0.98, fair))


# ═══════════════════════════════════════
#  Simulation
# ═══════════════════════════════════════

def simulate_hour(hour_kline: dict, minute_klines: list[dict],
                  rungs: list[float], budget_per_rung: float,
                  tp_tiers: list[tuple[float, float]] | None = None,
                  sl_pct: float = 0.25,
                  adverse_cancel_pct: float = 0.01,
                  min_conviction: float = 0.20,
                  min_confidence: float = 0.30) -> dict:
    """
    Simulate one 1H window with conviction-gated ladder DCA + tiered TP.

    Conviction filter: only enter when conviction (confidence × time_trust) crosses
    min_conviction threshold. This matches the real hourly_engine.py behavior.

    Returns dict with fills, pnl, avg_entry, etc.
    """
    btc_open = hour_kline["open"]
    outcome = hour_kline["result"]  # UP or DOWN

    if not minute_klines or len(minute_klines) < 5:
        return {"fills": 0, "pnl": 0, "skipped": True}

    # Compute vol from prior 60 candles (approximate)
    vol_1m = compute_vol_1m(minute_klines[:60]) if len(minute_klines) >= 60 else 0.003

    # Track fair_up at each minute → simulate Polymarket mid
    mids = []  # (minute_idx, fair_up, btc_price)
    for i, k in enumerate(minute_klines):
        btc_now = k["close"]
        mins_remaining = max(0.1, 60.0 - i - 1)
        fair = bridge_fair_up(btc_now, btc_open, vol_1m, mins_remaining)
        mids.append((i, fair, btc_now))

    # ── Conviction-gated entry (matches hourly_engine.py) ──
    # Scan each minute: compute conviction = confidence × time_trust
    # Enter ONLY when conviction first crosses threshold (like real bot)
    entry_min = None
    direction = None
    for i, (_, fair, _) in enumerate(mids):
        if i < 5:
            continue  # minimum observation time
        if i > 55:
            break  # too late
        confidence = abs(fair - 0.50) * 2.0  # 0-1
        time_trust = min(i / 40.0, 1.0)  # saturates at 40 min
        conviction = confidence * time_trust
        # Dynamic threshold: starts high, drops over time (matches hourly_engine)
        threshold = max(0.12, 0.33 - i * 0.005)
        if conviction >= threshold and confidence >= min_confidence:
            entry_min = i
            direction = "UP" if fair > 0.50 else "DOWN"
            break

    if entry_min is None or direction is None:
        return {"fills": 0, "pnl": 0, "skipped": True, "reason": "no_conviction"}

    # Our side's token mid at each minute
    token_mids = []
    for i, fair, btc in mids:
        our_mid = fair if direction == "UP" else (1.0 - fair)
        token_mids.append((i, our_mid, btc))

    # Simulate ladder fills
    filled_rungs = []
    total_shares = 0
    total_cost = 0
    cancelled = False
    btc_at_entry = mids[entry_min][2]

    for rung_price in rungs:
        if cancelled:
            break
        for i, our_mid, btc_now in token_mids:
            if i < entry_min:
                continue  # haven't entered yet
            if i > 55:
                break  # window-end cancel (last 5 min)

            # Adverse BTC cancel: if BTC moved against us by > threshold
            btc_move = (btc_now - btc_at_entry) / btc_at_entry
            adverse = -btc_move if direction == "UP" else btc_move
            if adverse > adverse_cancel_pct and not filled_rungs:
                # Only cancel if no fills yet; if we have fills, SL handles it
                cancelled = True
                break

            if our_mid <= rung_price:
                shares = max(5, budget_per_rung / rung_price)
                filled_rungs.append({"rung": rung_price, "shares": shares,
                                     "fill_min": i})
                total_shares += shares
                total_cost += shares * rung_price
                break  # this rung filled, move to next

    if total_shares == 0:
        return {"fills": 0, "pnl": 0, "direction": direction, "outcome": outcome,
                "cancelled": cancelled}

    avg_entry = total_cost / total_shares
    won = (direction == outcome)

    # Simulate tiered TP
    partial_pnl = 0
    remaining_shares = total_shares
    remaining_cost = total_cost
    tp_done = 0
    sl_fired = False

    if tp_tiers:
        last_fill_min = max(r["fill_min"] for r in filled_rungs)
        for i, our_mid, btc_now in token_mids:
            if i <= last_fill_min:
                continue
            if remaining_shares <= 0:
                break

            # Stop loss check
            if our_mid > 0 and avg_entry > 0:
                current_val = remaining_shares * our_mid
                unrealized = (current_val - remaining_cost) / remaining_cost
                if unrealized < -sl_pct:
                    # SL fires: sell all at current mid
                    sl_price = our_mid * 0.97  # taker fee
                    sl_pnl = remaining_shares * sl_price - remaining_cost
                    partial_pnl += sl_pnl
                    remaining_shares = 0
                    remaining_cost = 0
                    sl_fired = True
                    break

            # Tiered TP check
            if tp_done < len(tp_tiers):
                mult, sell_pct = tp_tiers[tp_done]
                tp_target = avg_entry * mult
                if our_mid >= tp_target:
                    sell_shares = remaining_shares * sell_pct
                    sell_revenue = sell_shares * our_mid * 0.97  # taker fee
                    sell_cost_portion = sell_shares * avg_entry
                    partial_pnl += sell_revenue - sell_cost_portion
                    remaining_shares -= sell_shares
                    remaining_cost -= sell_cost_portion
                    tp_done += 1

    # Resolution: remaining shares → $1 or $0
    if remaining_shares > 0:
        if won:
            resolution_pnl = remaining_shares * 1.0 - remaining_cost
        else:
            resolution_pnl = -remaining_cost
    else:
        resolution_pnl = 0

    total_pnl = partial_pnl + resolution_pnl

    return {
        "fills": len(filled_rungs),
        "total_shares": total_shares,
        "total_cost": total_cost,
        "avg_entry": avg_entry,
        "direction": direction,
        "outcome": outcome,
        "won": won,
        "pnl": total_pnl,
        "tp_done": tp_done,
        "sl_fired": sl_fired,
        "cancelled": cancelled,
        "filled_rungs": [r["rung"] for r in filled_rungs],
    }


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbol", default="BTCUSDT")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 86_400_000
    end_ms = now_ms - 3_600_000  # exclude current hour

    log.info("Fetching %d days of 1H klines for %s...", args.days, args.symbol)
    hourly = fetch_1h_klines(args.symbol, start_ms, end_ms)
    log.info("Got %d hourly candles", len(hourly))

    # Fetch 1M klines for each hour (this takes a while, cached)
    log.info("Fetching 1M klines for each hour (cached)...")
    hour_data = []
    for i, h in enumerate(hourly):
        h_start = h["open_time"]
        h_end = h_start + 3_600_000
        mins = fetch_1m_klines(args.symbol, h_start, h_end)
        hour_data.append((h, mins))
        if (i + 1) % 100 == 0:
            log.info("  %d/%d hours fetched", i + 1, len(hourly))

    log.info("Data ready: %d hours with minute data", len(hour_data))

    # ── Test 1: Rung combinations ──
    BUDGET = 2.10
    rung_configs = [
        ("15M rungs (0.43/0.37/0.31/0.26)", [0.43, 0.37, 0.31, 0.26]),
        ("High (0.38/0.33/0.28/0.23)",      [0.38, 0.33, 0.28, 0.23]),
        ("Mid (0.36/0.30/0.25/0.20)",        [0.36, 0.30, 0.25, 0.20]),
        ("Low (0.34/0.28/0.22/0.18)",        [0.34, 0.28, 0.22, 0.18]),
        ("Wide (0.38/0.30/0.22/0.16)",       [0.38, 0.30, 0.22, 0.16]),
        ("Tight (0.37/0.34/0.31/0.28)",      [0.37, 0.34, 0.31, 0.28]),
        ("3 rungs (0.36/0.30/0.24)",         [0.36, 0.30, 0.24]),
        ("2 rungs (0.36/0.28)",              [0.36, 0.28]),
        ("Single ($0.35)",                   [0.35]),
    ]

    print(f"\n{'Config':>35s} {'Fill%':>6s} {'WR':>5s} {'AvgEntry':>9s} {'Sharpe':>7s} {'MaxDD':>7s} {'TotalPnL':>9s}")
    print("-" * 85)

    best_sharpe = -999
    best_rungs = None
    for name, rungs in rung_configs:
        results = [simulate_hour(h, m, rungs, BUDGET) for h, m in hour_data]
        filled = [r for r in results if r["fills"] > 0]
        if not filled:
            print(f"{name:>35s}  {'0%':>5s}  {'—':>4s}  {'—':>8s}  {'—':>6s}  {'—':>6s}  {'—':>8s}")
            continue
        n = len(filled)
        fill_pct = n / len(results) * 100
        wins = sum(1 for r in filled if r.get("won"))
        wr = wins / n * 100
        avg_e = statistics.mean(r["avg_entry"] for r in filled)
        pnls = [r["pnl"] for r in filled]
        total = sum(pnls)
        mean = statistics.mean(pnls)
        std = statistics.stdev(pnls) if len(pnls) > 1 else 1
        sharpe = mean / std if std > 0 else 0
        cum = 0; peak = 0; dd = 0
        for p in pnls:
            cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
        print(f"{name:>35s} {fill_pct:>5.0f}% {wr:>4.0f}% ${avg_e:>7.3f} {sharpe:>+6.3f} ${dd:>5.2f} ${total:>+7.2f}")
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_rungs = rungs

    # ── Test 2: Per-rung fill rate + WR ──
    print(f"\n{'Rung':>6s} {'Reached':>8s} {'Won':>5s} {'WR':>5s} {'EV/fill':>8s}")
    print("-" * 40)
    test_prices = [0.45, 0.40, 0.38, 0.36, 0.34, 0.32, 0.30, 0.28, 0.26, 0.24, 0.22, 0.20, 0.18, 0.16]
    for p in test_prices:
        results = [simulate_hour(h, m, [p], BUDGET) for h, m in hour_data]
        filled = [r for r in results if r["fills"] > 0]
        if not filled:
            print(f"${p:.2f}   {0:>4d}/{len(results):>3d}  {'—':>4s}  {'—':>4s}  {'—':>7s}")
            continue
        n = len(filled)
        wins = sum(1 for r in filled if r.get("won"))
        wr = wins / n * 100
        avg_pnl = statistics.mean(r["pnl"] for r in filled)
        print(f"${p:.2f}   {n:>4d}/{len(results):>3d} {wins:>4d} {wr:>4.0f}% ${avg_pnl:>+6.3f}")

    # ── Test 3: Tiered TP (using best rungs) ──
    if best_rungs:
        print(f"\nTiered TP test (rungs: {best_rungs}):")
        tp_configs = [
            ("HOLD (no TP)",                []),
            ("Single x1.5 sell 50%",        [(1.5, 0.50)]),
            ("Tiered 30/50/75",             [(1.3, 0.30), (1.5, 0.30), (1.75, 0.30)]),
            ("Tiered 30/50/80",             [(1.3, 0.25), (1.5, 0.35), (1.8, 0.35)]),
            ("Tiered 40/60/80",             [(1.4, 0.33), (1.6, 0.33), (1.8, 0.33)]),
            ("Tiered 20/40/60",             [(1.2, 0.25), (1.4, 0.35), (1.6, 0.35)]),
            ("Tiered 30/50/80 + SL25%",     [(1.3, 0.25), (1.5, 0.35), (1.8, 0.35)]),
        ]
        print(f"\n{'TP Config':>30s} {'Sharpe':>7s} {'MeanPnL':>8s} {'MaxDD':>7s} {'TotalPnL':>9s} {'SL%':>5s}")
        print("-" * 70)
        for name, tiers in tp_configs:
            sl = 0.25 if "SL" in name else 999  # 999 = no SL
            results = [simulate_hour(h, m, best_rungs, BUDGET, tp_tiers=tiers or None,
                                     sl_pct=sl) for h, m in hour_data]
            filled = [r for r in results if r["fills"] > 0]
            if not filled:
                continue
            pnls = [r["pnl"] for r in filled]
            mean = statistics.mean(pnls)
            std = statistics.stdev(pnls) if len(pnls) > 1 else 1
            sharpe = mean / std if std > 0 else 0
            cum = 0; peak = 0; dd = 0
            for p in pnls:
                cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
            sl_count = sum(1 for r in filled if r.get("sl_fired"))
            sl_rate = sl_count / len(filled) * 100
            print(f"{name:>30s} {sharpe:>+6.3f} {mean:>+7.3f} ${dd:>5.2f} ${sum(pnls):>+7.2f} {sl_rate:>4.0f}%")

    # ── Test 4: Adverse cancel threshold ──
    if best_rungs:
        print(f"\nAdverse cancel threshold test (rungs: {best_rungs}):")
        print(f"\n{'Cancel%':>8s} {'Fill%':>6s} {'Sharpe':>7s} {'TotalPnL':>9s} {'Cancelled':>10s}")
        print("-" * 45)
        for cancel_pct in [0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 999]:
            results = [simulate_hour(h, m, best_rungs, BUDGET,
                                     adverse_cancel_pct=cancel_pct) for h, m in hour_data]
            filled = [r for r in results if r["fills"] > 0]
            cancelled = sum(1 for r in results if r.get("cancelled"))
            if not filled:
                continue
            pnls = [r["pnl"] for r in filled]
            mean = statistics.mean(pnls)
            std = statistics.stdev(pnls) if len(pnls) > 1 else 1
            sharpe = mean / std if std > 0 else 0
            fill_pct = len(filled) / len(results) * 100
            label = f"{cancel_pct*100:.1f}%" if cancel_pct < 100 else "None"
            print(f"{label:>8s} {fill_pct:>5.0f}% {sharpe:>+6.3f} ${sum(pnls):>+7.2f} {cancelled:>5d}")

    # ── Test 5: Stop loss threshold ──
    if best_rungs:
        print(f"\nStop loss threshold test (rungs: {best_rungs}, best TP tiers):")
        print(f"\n{'SL%':>6s} {'Sharpe':>7s} {'MeanPnL':>8s} {'MaxDD':>7s} {'TotalPnL':>9s} {'SL fires':>9s}")
        print("-" * 55)
        best_tp = [(1.3, 0.25), (1.5, 0.35), (1.8, 0.35)]  # default
        for sl in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 999]:
            results = [simulate_hour(h, m, best_rungs, BUDGET,
                                     tp_tiers=best_tp, sl_pct=sl) for h, m in hour_data]
            filled = [r for r in results if r["fills"] > 0]
            if not filled:
                continue
            pnls = [r["pnl"] for r in filled]
            mean = statistics.mean(pnls)
            std = statistics.stdev(pnls) if len(pnls) > 1 else 1
            sharpe = mean / std if std > 0 else 0
            cum = 0; peak = 0; dd = 0
            for p in pnls:
                cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
            sl_count = sum(1 for r in filled if r.get("sl_fired"))
            label = f"-{sl*100:.0f}%" if sl < 100 else "None"
            print(f"{label:>6s} {sharpe:>+6.3f} {mean:>+7.3f} ${dd:>5.2f} ${sum(pnls):>+7.2f} {sl_count:>5d}")


if __name__ == "__main__":
    main()
