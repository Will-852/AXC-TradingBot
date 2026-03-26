#!/usr/bin/env python3
"""
edge_calibration.py — 3-Layer Edge Verification for conviction bots.

Layer 1: Direction accuracy (does bridge predict correctly?)
Layer 2: Calibration (when model says 70%, does it win ~70%?)
Layer 3: Edge operability (at what entry price is EV maximized?)

Uses bt_cache/ (720 1H windows + 1m data) as primary calibration dataset.

Run:
  cd ~/projects/axc-trading
  PYTHONPATH=.:scripts python3 polymarket/analysis/edge_calibration.py
"""
import json
import logging
import math
import os
import sys
from collections import defaultdict

_AXC = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
for p in [_AXC, os.path.join(_AXC, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from polymarket.strategy.market_maker import compute_fair_up

log = logging.getLogger("calibration")
_BT_CACHE = os.path.join(_AXC, "polymarket", "logs", "bt_cache")


# ═══════════════════════════════════════
#  Data Loading
# ═══════════════════════════════════════

def _load_1h_windows(symbol: str = "BTCUSDT") -> list[dict]:
    """Load all 1H windows from bt_cache."""
    windows = []
    prefix = f"1h_{symbol}_"
    for f in os.listdir(_BT_CACHE):
        if f.startswith(prefix) and f.endswith(".json"):
            with open(os.path.join(_BT_CACHE, f)) as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    windows.extend(data)
    # Deduplicate by open_time
    seen = set()
    unique = []
    for w in windows:
        ot = w.get("open_time", 0)
        if ot not in seen:
            seen.add(ot)
            unique.append(w)
    unique.sort(key=lambda x: x["open_time"])
    return unique


def _load_1m_klines(symbol: str = "BTCUSDT") -> dict[int, list[dict]]:
    """Load 1m klines indexed by hour start (open_time of 1H window)."""
    all_1m = {}
    prefix = f"1m_{symbol}_"
    for f in os.listdir(_BT_CACHE):
        if f.startswith(prefix) and f.endswith(".json"):
            with open(os.path.join(_BT_CACHE, f)) as fh:
                data = json.load(fh)
                if isinstance(data, list) and data:
                    # Group by hour
                    hour_start = data[0].get("open_time", 0)
                    # Normalize to hour boundary
                    hour_start = (hour_start // 3600000) * 3600000
                    if hour_start not in all_1m:
                        all_1m[hour_start] = []
                    all_1m[hour_start].extend(data)
    # Sort each hour's 1m candles
    for k in all_1m:
        all_1m[k].sort(key=lambda x: x.get("open_time", 0))
    return all_1m


def _vol_from_1m(candles_1m: list[dict]) -> float:
    """Compute per-minute log-return volatility from 1m klines."""
    if len(candles_1m) < 5:
        return 0.001
    closes = [c.get("close", 0) for c in candles_1m if c.get("close", 0) > 0]
    if len(closes) < 5:
        return 0.001
    log_rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))
                if closes[i] > 0 and closes[i-1] > 0]
    if len(log_rets) < 3:
        return 0.001
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean)**2 for r in log_rets) / len(log_rets)
    return max(0.0001, math.sqrt(var))


# ═══════════════════════════════════════
#  Statistical Helpers
# ═══════════════════════════════════════

def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z*z / n
    centre = (p + z*z / (2*n)) / denom
    spread = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n) / denom
    return (max(0, centre - spread), min(1, centre + spread))


def _binomial_p(wins: int, n: int, p0: float = 0.5) -> float:
    """P(X >= wins) under binomial(n, p0). One-sided test."""
    from math import comb
    return sum(comb(n, k) * p0**k * (1-p0)**(n-k) for k in range(wins, n+1))


# ═══════════════════════════════════════
#  Layer 1: Direction Accuracy
# ═══════════════════════════════════════

def layer1_direction(windows: list[dict], klines_1m: dict,
                     wait_times: list[int] = None,
                     thresholds: list[float] = None) -> dict:
    """For each (wait_time, p_win_threshold): compute WR."""
    wait_times = wait_times or [20, 25, 30, 35, 40]
    thresholds = thresholds or [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]

    results = {}
    for wait in wait_times:
        for thresh in thresholds:
            wins, total = 0, 0
            for w in windows:
                ot = w["open_time"]
                btc_open = w["open"]
                result = w.get("result", "")
                if not result or btc_open <= 0:
                    continue

                # Get 1m candles for this hour
                hour_key = (ot // 3600000) * 3600000
                m1 = klines_1m.get(hour_key, [])
                if len(m1) < wait:
                    continue

                # BTC price at T+wait_min
                wait_idx = min(wait - 1, len(m1) - 1)
                btc_at_wait = m1[wait_idx].get("close", 0)
                if btc_at_wait <= 0:
                    continue

                # Vol from first `wait` minutes
                vol = _vol_from_1m(m1[:wait])
                t_remaining = 60 - wait  # minutes left in 1H window

                # Bridge prediction
                fair_up = compute_fair_up(btc_at_wait, btc_open, vol, t_remaining)
                p_win = max(fair_up, 1 - fair_up)
                direction = "UP" if fair_up > 0.50 else "DOWN"

                if p_win < thresh:
                    continue  # below threshold → skip

                total += 1
                if direction == result:
                    wins += 1

            ci_lo, ci_hi = _wilson_ci(wins, total)
            p_val = _binomial_p(wins, total) if total > 0 else 1.0
            wr = wins / total if total > 0 else 0

            results[(wait, thresh)] = {
                "wins": wins, "total": total, "wr": round(wr, 4),
                "ci_lo": round(ci_lo, 4), "ci_hi": round(ci_hi, 4),
                "p_value": round(p_val, 6),
            }
    return results


# ═══════════════════════════════════════
#  Layer 2: Calibration
# ═══════════════════════════════════════

def layer2_calibration(windows: list[dict], klines_1m: dict,
                       wait: int = 30, bucket_size: float = 0.05) -> dict:
    """Bin predictions by p_win → actual WR per bucket. Compute Brier score."""
    buckets = defaultdict(lambda: {"wins": 0, "total": 0})
    all_pred, all_actual = [], []

    for w in windows:
        ot = w["open_time"]
        btc_open = w["open"]
        result = w.get("result", "")
        if not result or btc_open <= 0:
            continue

        hour_key = (ot // 3600000) * 3600000
        m1 = klines_1m.get(hour_key, [])
        if len(m1) < wait:
            continue

        btc_at_wait = m1[min(wait-1, len(m1)-1)].get("close", 0)
        if btc_at_wait <= 0:
            continue

        vol = _vol_from_1m(m1[:wait])
        fair_up = compute_fair_up(btc_at_wait, btc_open, vol, 60 - wait)
        p_win = max(fair_up, 1 - fair_up)
        direction = "UP" if fair_up > 0.50 else "DOWN"
        correct = 1 if direction == result else 0

        # Bucket by p_win
        bucket_key = round(round(p_win / bucket_size) * bucket_size, 2)
        buckets[bucket_key]["wins"] += correct
        buckets[bucket_key]["total"] += 1

        all_pred.append(p_win)
        all_actual.append(correct)

    # Brier score
    brier = sum((p - a)**2 for p, a in zip(all_pred, all_actual)) / len(all_pred) if all_pred else 1.0
    base_rate = sum(all_actual) / len(all_actual) if all_actual else 0.5
    brier_baseline = base_rate * (1 - base_rate)
    brier_skill = 1 - brier / brier_baseline if brier_baseline > 0 else 0

    bucket_results = {}
    for bk in sorted(buckets.keys()):
        b = buckets[bk]
        n = b["total"]
        w = b["wins"]
        actual_wr = w / n if n > 0 else 0
        gap = actual_wr - bk
        bucket_results[bk] = {
            "predicted": bk, "actual_wr": round(actual_wr, 4),
            "n": n, "wins": w,
            "gap": round(gap, 4),
            "tag": "calibrated" if abs(gap) < 0.08 else ("under-confident" if gap > 0 else "over-confident"),
        }

    return {
        "buckets": bucket_results,
        "brier_score": round(brier, 4),
        "brier_skill": round(brier_skill, 4),
        "base_rate": round(base_rate, 4),
        "n_total": len(all_pred),
    }


# ═══════════════════════════════════════
#  Layer 3: Edge Operability
# ═══════════════════════════════════════

def layer3_operability(windows: list[dict], klines_1m: dict,
                       calibration: dict, wait: int = 30,
                       discounts: list[float] = None) -> dict:
    """For each discount factor: compute adj_EV = fill_rate × (cal_WR - entry_price)."""
    discounts = discounts or [0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    cal_buckets = calibration.get("buckets", {})

    results = {}
    for disc in discounts:
        total_ev, total_signals, total_fills = 0, 0, 0

        for w in windows:
            ot = w["open_time"]
            btc_open = w["open"]
            result = w.get("result", "")
            if not result or btc_open <= 0:
                continue

            hour_key = (ot // 3600000) * 3600000
            m1 = klines_1m.get(hour_key, [])
            if len(m1) < wait:
                continue

            btc_at_wait = m1[min(wait-1, len(m1)-1)].get("close", 0)
            if btc_at_wait <= 0:
                continue

            vol = _vol_from_1m(m1[:wait])
            fair_up = compute_fair_up(btc_at_wait, btc_open, vol, 60 - wait)
            p_win = max(fair_up, 1 - fair_up)
            direction = "UP" if fair_up > 0.50 else "DOWN"

            if p_win < 0.60:
                continue  # minimum threshold

            entry_price = p_win * disc
            if entry_price < 0.20:
                continue

            # EV floor: need at least 10c edge
            if p_win - entry_price < 0.10:
                continue

            total_signals += 1

            # Simulate fill: check if market mid (approximated by bridge inverse)
            # drops to entry_price during remaining window
            # Approximation: check each minute's fair_up, see if our side's fair drops to entry
            filled = False
            for mi in range(wait, min(55, len(m1))):
                btc_mi = m1[mi].get("close", 0)
                if btc_mi <= 0:
                    continue
                fair_mi = compute_fair_up(btc_mi, btc_open, vol, 60 - mi)
                p_win_mi = fair_mi if direction == "UP" else (1 - fair_mi)
                # Our side's "fair price" at minute mi
                if p_win_mi <= entry_price:
                    filled = True
                    break

            if filled:
                total_fills += 1
                # Use calibrated WR for this p_win bucket
                bk = round(round(p_win / 0.05) * 0.05, 2)
                cal = cal_buckets.get(bk, {})
                cal_wr = cal.get("actual_wr", p_win)  # fallback to model
                ev = cal_wr * 1.0 - entry_price
                total_ev += ev

            # Also add unfilled cost = $0 (maker order, not taker)

        fill_rate = total_fills / total_signals if total_signals > 0 else 0
        avg_ev = total_ev / total_fills if total_fills > 0 else 0
        adj_ev_per_signal = total_ev / total_signals if total_signals > 0 else 0

        results[disc] = {
            "discount": disc,
            "signals": total_signals,
            "fills": total_fills,
            "fill_rate": round(fill_rate, 4),
            "avg_ev_per_fill": round(avg_ev, 4),
            "adj_ev_per_signal": round(adj_ev_per_signal, 4),
            "total_ev": round(total_ev, 2),
        }

    return results


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    log.info("Loading bt_cache data...")
    windows = _load_1h_windows("BTCUSDT")
    klines = _load_1m_klines("BTCUSDT")
    log.info("Loaded %d 1H windows, %d hours of 1m data", len(windows), len(klines))

    if len(windows) < 100:
        print("ERROR: Not enough windows for calibration")
        sys.exit(1)

    # ═══ LAYER 1 ═══
    print("\n" + "═" * 65)
    print("  LAYER 1: Direction Accuracy")
    print("═" * 65)

    l1 = layer1_direction(windows, klines)
    print(f"\n  {'Wait':>5} {'Thresh':>7} {'WR':>7} {'N':>5} {'95% CI':>16} {'p-value':>10}")
    print(f"  {'─'*55}")
    for (wait, thresh), r in sorted(l1.items()):
        if wait != 30:
            continue  # show wait=30 only for brevity
        sig = "***" if r["p_value"] < 0.001 else "**" if r["p_value"] < 0.01 else "*" if r["p_value"] < 0.05 else ""
        print(f"  {wait:>5} {thresh:>6.0%} {r['wr']:>6.1%} {r['total']:>5} "
              f"[{r['ci_lo']:.1%}-{r['ci_hi']:.1%}] {r['p_value']:>9.6f} {sig}")

    # ═══ LAYER 2 ═══
    print("\n" + "═" * 65)
    print("  LAYER 2: Calibration (wait=30min)")
    print("═" * 65)

    l2 = layer2_calibration(windows, klines, wait=30)
    print(f"\n  Brier Score: {l2['brier_score']:.4f} (random=0.2500, lower=better)")
    print(f"  Brier Skill: {l2['brier_skill']:.4f} (>0 = better than base rate)")
    print(f"  Base rate: {l2['base_rate']:.1%} | N={l2['n_total']}")
    print(f"\n  {'Model':>7} {'Actual':>7} {'N':>5} {'Gap':>7} {'Tag'}")
    print(f"  {'─'*45}")
    for bk, b in sorted(l2["buckets"].items()):
        if b["n"] < 5:
            continue
        tag = "✅" if b["tag"] == "calibrated" else ("📉 under" if b["tag"] == "under-confident" else "📈 OVER")
        print(f"  {b['predicted']:>6.0%} {b['actual_wr']:>6.1%} {b['n']:>5} {b['gap']:>+6.1%} {tag}")

    # ═══ LAYER 3 ═══
    print("\n" + "═" * 65)
    print("  LAYER 3: Edge Operability")
    print("═" * 65)

    l3 = layer3_operability(windows, klines, l2)
    print(f"\n  {'Discount':>9} {'Signals':>8} {'Fills':>6} {'Fill%':>7} {'EV/fill':>8} {'EV/signal':>10} {'Total EV':>9}")
    print(f"  {'─'*62}")
    best_disc = 0
    best_ev = -1
    for disc, r in sorted(l3.items()):
        marker = ""
        if r["adj_ev_per_signal"] > best_ev:
            best_ev = r["adj_ev_per_signal"]
            best_disc = disc
        print(f"  {disc:>8.0%} {r['signals']:>8} {r['fills']:>6} {r['fill_rate']:>6.0%} "
              f"${r['avg_ev_per_fill']:>7.3f} ${r['adj_ev_per_signal']:>9.4f} ${r['total_ev']:>8.2f}")

    # Mark best
    print(f"\n  ★ Optimal discount: {best_disc:.0%} (EV/signal=${best_ev:.4f})")

    # ═══ DECISION ═══
    print("\n" + "═" * 65)
    print("  DECISION: Recommended Constants")
    print("═" * 65)

    # Check Layer 2 for over-confidence risk
    over_confident = [b for b in l2["buckets"].values()
                      if b["tag"] == "over-confident" and b["n"] >= 10]
    if over_confident:
        print(f"\n  ⚠️ Over-confident buckets: {len(over_confident)}")
        for b in over_confident:
            print(f"    Model {b['predicted']:.0%} → Actual {b['actual_wr']:.0%} (n={b['n']})")
        # Use more conservative discount
        safe_disc = max(0.70, best_disc - 0.05)
        print(f"  → Adjusting discount from {best_disc:.0%} to {safe_disc:.0%} (safety margin)")
        best_disc = safe_disc

    constants = {
        "RELATIVE_CAP_DISCOUNT": best_disc,
        "MIN_EDGE": 0.10,
        "MIN_FAIR_FOR_RELATIVE": 0.65,
        "MAX_ENTRY_PRICE": 0.60,
    }

    print(f"\n  Constants for hourly_engine.py + run_4h_live.py:")
    for k, v in constants.items():
        print(f"    {k} = {v}")

    print(f"\n  Copy-paste JSON:")
    print(f"  {json.dumps(constants, indent=2)}")

    # Save results
    out = {
        "layer1": {str(k): v for k, v in l1.items()},
        "layer2": l2,
        "layer3": {str(k): v for k, v in l3.items()},
        "decision": constants,
    }
    out_path = os.path.join(_AXC, "polymarket", "analysis", "results", "edge_calibration.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    log.info("Results saved → %s", out_path)


if __name__ == "__main__":
    main()
