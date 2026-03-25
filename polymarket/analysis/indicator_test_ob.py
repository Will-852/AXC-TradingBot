"""
indicator_test_ob.py

Tests Binance taker buy/sell ratio as a predictor for BTC 5-minute direction
on Polymarket binary options.

KEY QUESTION: Does Binance taker buy/sell ratio add predictive power
ON TOP OF momentum signal?

Data source: btc_1m_90d_raw.json (90 days, 129k candles, fetched from Binance API)
Fields: [open_time_ms, open, high, low, close, volume, close_time_ms,
         quote_vol, trades, taker_buy_base, taker_buy_quote, ignore]

Parts:
  A — Taker buy/sell ratio as standalone predictor
  B — Taker flow COMBINED with momentum (agreement vs disagreement)
  C — Within-window evolution of taker ratio (minute 1 to 5)
"""

import json
import math
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_PATH = "/Users/wai/projects/axc-trading/polymarket/analysis/btc_1m_90d_raw.json"

# Kline field indices
IDX_OPEN_TIME = 0
IDX_OPEN = 1
IDX_HIGH = 2
IDX_LOW = 3
IDX_CLOSE = 4
IDX_VOLUME = 5
IDX_TAKER_BUY_BASE = 9

THRESHOLDS = [0.50, 0.52, 0.55, 0.58, 0.60]
LOOKBACKS = [1, 2]   # in minutes (first N candles of 5M window)
MOMENTUM_BPS_THRESHOLD = 5  # bps trigger for Part B


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def bps(price_start: float, price_end: float) -> float:
    """Return move in basis points."""
    return (price_end - price_start) / price_start * 10000


def fmt_pct(n: float) -> str:
    return f"{n*100:.1f}%"


def fmt_table_row(*cols, widths=None) -> str:
    if widths is None:
        widths = [16] * len(cols)
    return "  ".join(str(c).ljust(w) for c, w in zip(cols, widths))


def print_table(headers, rows, widths=None):
    if widths is None:
        widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0)) + 2
                  for i, h in enumerate(headers)]
    print(fmt_table_row(*headers, widths=widths))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt_table_row(*row, widths=widths))


def confidence_interval_95(wr: float, n: int) -> float:
    """Wilson score interval half-width (95%)."""
    if n == 0:
        return 0.0
    z = 1.96
    p = wr
    return z * math.sqrt(p * (1 - p) / n)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
def load_klines(path: str):
    with open(path) as f:
        raw = json.load(f)
    klines = []
    for k in raw:
        volume = float(k[IDX_VOLUME])
        taker_buy = float(k[IDX_TAKER_BUY_BASE])
        klines.append({
            "ts": int(k[IDX_OPEN_TIME]),
            "open": float(k[IDX_OPEN]),
            "high": float(k[IDX_HIGH]),
            "low": float(k[IDX_LOW]),
            "close": float(k[IDX_CLOSE]),
            "volume": volume,
            "taker_buy": taker_buy,
            "taker_buy_ratio": taker_buy / volume if volume > 0 else 0.5,
        })
    return klines


# ---------------------------------------------------------------------------
# Build 5-minute windows (groups of 5 consecutive 1M candles)
# ---------------------------------------------------------------------------
def build_5m_windows(klines):
    """
    Groups consecutive 1M candles into 5M windows.
    A valid window requires exactly 5 consecutive candles with no time gap.
    Returns list of windows, each a dict with analysis fields.
    """
    windows = []
    n = len(klines)
    i = 0
    while i + 4 < n:
        group = klines[i:i + 5]

        # Validate: all 5 candles must be consecutive 60-second intervals
        valid = True
        for j in range(1, 5):
            expected_ts = group[0]["ts"] + j * 60_000
            if group[j]["ts"] != expected_ts:
                valid = False
                break

        if not valid:
            i += 1
            continue

        # Aggregate across full window
        total_volume = sum(c["volume"] for c in group)
        total_taker_buy = sum(c["taker_buy"] for c in group)

        # Direction: close of candle 5 vs open of candle 1
        window_open = group[0]["open"]
        window_close = group[4]["close"]
        window_move_bps = bps(window_open, window_close)
        direction = "UP" if window_close > window_open else "DOWN"

        # Taker ratio for first 1 min (candle 0 only)
        v1 = group[0]["volume"]
        tb1 = group[0]["taker_buy"]
        ratio_1m = tb1 / v1 if v1 > 0 else 0.5

        # Taker ratio for first 2 min (candles 0-1)
        v2 = group[0]["volume"] + group[1]["volume"]
        tb2 = group[0]["taker_buy"] + group[1]["taker_buy"]
        ratio_2m = tb2 / v2 if v2 > 0 else 0.5

        # Taker ratio per minute (for Part C)
        per_min_ratio = []
        for c in group:
            r = c["taker_buy"] / c["volume"] if c["volume"] > 0 else 0.5
            per_min_ratio.append(r)

        # Momentum at T+60s (close of candle 1 vs open of candle 1 == open of window)
        momentum_1m_bps = bps(window_open, group[0]["close"])

        windows.append({
            "ts": group[0]["ts"],
            "open": window_open,
            "close": window_close,
            "direction": direction,
            "move_bps": window_move_bps,
            "total_volume": total_volume,
            "ratio_1m": ratio_1m,
            "ratio_2m": ratio_2m,
            "per_min_ratio": per_min_ratio,
            "momentum_1m_bps": momentum_1m_bps,
        })

        i += 5  # non-overlapping windows

    return windows


# ---------------------------------------------------------------------------
# PART A: Taker buy/sell ratio as standalone predictor
# ---------------------------------------------------------------------------
def part_a(windows):
    print("\n" + "=" * 72)
    print("PART A: Taker Buy/Sell Ratio as Standalone Predictor")
    print("=" * 72)

    total = len(windows)
    baseline_up = sum(1 for w in windows if w["direction"] == "UP")
    baseline_wr = baseline_up / total if total > 0 else 0
    print(f"\nBaseline: {baseline_up}/{total} UP = {fmt_pct(baseline_wr)} WR")
    print(f"(Any signal > baseline lift is meaningful)\n")

    # Table: lookback x threshold
    ratio_keys = {1: "ratio_1m", 2: "ratio_2m"}

    for lb in LOOKBACKS:
        key = ratio_keys[lb]
        print(f"\n--- Lookback: first {lb} minute(s) ---")

        headers = ["Threshold", "Signal_UP(N)", "WR_UP", "±95%CI", "Lift_bps",
                   "Signal_DOWN(N)", "WR_DOWN", "±95%CI", "Lift_bps"]
        widths = [12, 14, 8, 8, 10, 16, 10, 8, 10]
        rows = []

        for thresh in THRESHOLDS:
            # Signal UP: ratio > thresh
            up_candidates = [w for w in windows if w[key] > thresh]
            up_correct = sum(1 for w in up_candidates if w["direction"] == "UP")
            n_up = len(up_candidates)
            wr_up = up_correct / n_up if n_up > 0 else 0
            ci_up = confidence_interval_95(wr_up, n_up)
            lift_up = (wr_up - baseline_wr) * 10000  # in bps

            # Signal DOWN: ratio < (1 - thresh)
            down_thresh = 1 - thresh
            down_candidates = [w for w in windows if w[key] < down_thresh]
            down_correct = sum(1 for w in down_candidates if w["direction"] == "DOWN")
            n_down = len(down_candidates)
            wr_down = down_correct / n_down if n_down > 0 else 0
            ci_down = confidence_interval_95(wr_down, n_down)
            lift_down = (wr_down - (1 - baseline_wr)) * 10000

            rows.append([
                f">{thresh}",
                f"{n_up}",
                fmt_pct(wr_up),
                f"±{fmt_pct(ci_up)}",
                f"{lift_up:+.0f}",
                f"{n_down}",
                fmt_pct(wr_down),
                f"±{fmt_pct(ci_down)}",
                f"{lift_down:+.0f}",
            ])

        print_table(headers, rows, widths)

    # Volume filter: high volume windows only
    print("\n--- High Volume Filter (top 25% by total 5M volume) ---")
    vol_p75 = sorted(w["total_volume"] for w in windows)[int(len(windows) * 0.75)]
    hv_windows = [w for w in windows if w["total_volume"] >= vol_p75]

    baseline_hv = sum(1 for w in hv_windows if w["direction"] == "UP") / len(hv_windows)
    print(f"High volume subset: {len(hv_windows)} windows, baseline WR = {fmt_pct(baseline_hv)}")

    headers = ["Threshold", "N_UP", "WR_UP", "Lift_bps", "N_DOWN", "WR_DOWN", "Lift_bps"]
    widths = [12, 8, 8, 10, 8, 10, 10]
    rows = []
    for thresh in THRESHOLDS:
        up_c = [w for w in hv_windows if w["ratio_1m"] > thresh]
        wr_up = sum(1 for w in up_c if w["direction"] == "UP") / len(up_c) if up_c else 0
        lift_up = (wr_up - baseline_hv) * 10000

        dn_c = [w for w in hv_windows if w["ratio_1m"] < 1 - thresh]
        wr_dn = sum(1 for w in dn_c if w["direction"] == "DOWN") / len(dn_c) if dn_c else 0
        lift_dn = (wr_dn - (1 - baseline_hv)) * 10000

        rows.append([f">{thresh}", len(up_c), fmt_pct(wr_up), f"{lift_up:+.0f}",
                     len(dn_c), fmt_pct(wr_dn), f"{lift_dn:+.0f}"])
    print_table(headers, rows, widths)


# ---------------------------------------------------------------------------
# PART B: Taker flow COMBINED with momentum
# ---------------------------------------------------------------------------
def part_b(windows):
    print("\n" + "=" * 72)
    print("PART B: Taker Flow COMBINED with Momentum")
    print(f"         (Momentum filter: |T+60s move| > {MOMENTUM_BPS_THRESHOLD} bps)")
    print("=" * 72)

    # Filter windows where momentum signal is strong enough
    mom_windows = [w for w in windows if abs(w["momentum_1m_bps"]) >= MOMENTUM_BPS_THRESHOLD]
    total_mom = len(mom_windows)
    print(f"\nWindows with |momentum| > {MOMENTUM_BPS_THRESHOLD}bps: {total_mom}/{len(windows)} "
          f"({fmt_pct(total_mom/len(windows))})")

    if total_mom == 0:
        print("No momentum windows found.")
        return

    # Base WR of momentum-only signal
    mom_up = [w for w in mom_windows if w["momentum_1m_bps"] > 0]
    mom_dn = [w for w in mom_windows if w["momentum_1m_bps"] < 0]

    wr_mom_up = sum(1 for w in mom_up if w["direction"] == "UP") / len(mom_up) if mom_up else 0
    wr_mom_dn = sum(1 for w in mom_dn if w["direction"] == "DOWN") / len(mom_dn) if mom_dn else 0

    print(f"\nMomentum-only baseline:")
    print(f"  UP momentum:   {len(mom_up):5d} windows → WR(UP)   = {fmt_pct(wr_mom_up)}")
    print(f"  DOWN momentum: {len(mom_dn):5d} windows → WR(DOWN) = {fmt_pct(wr_mom_dn)}")

    print("\n--- Agreement Analysis (ratio_1m, threshold 0.52) ---\n")

    headers = ["Scenario", "N", "WR", "±95%CI", "vs Momentum-only Lift"]
    widths = [36, 8, 8, 8, 22]

    for thresh in [0.50, 0.52, 0.55]:
        rows = []
        # UP momentum + taker AGREES (ratio > thresh)
        agree_up = [w for w in mom_up if w["ratio_1m"] > thresh]
        wr_agree_up = sum(1 for w in agree_up if w["direction"] == "UP") / len(agree_up) if agree_up else 0
        ci_agree_up = confidence_interval_95(wr_agree_up, len(agree_up))
        lift_agree_up = (wr_agree_up - wr_mom_up) * 10000

        # UP momentum + taker DISAGREES (ratio < 1-thresh)
        disagree_up = [w for w in mom_up if w["ratio_1m"] < 1 - thresh]
        wr_disagree_up = sum(1 for w in disagree_up if w["direction"] == "UP") / len(disagree_up) if disagree_up else 0
        ci_disagree_up = confidence_interval_95(wr_disagree_up, len(disagree_up))
        lift_disagree_up = (wr_disagree_up - wr_mom_up) * 10000

        # DOWN momentum + taker AGREES (ratio < 1-thresh)
        agree_dn = [w for w in mom_dn if w["ratio_1m"] < 1 - thresh]
        wr_agree_dn = sum(1 for w in agree_dn if w["direction"] == "DOWN") / len(agree_dn) if agree_dn else 0
        ci_agree_dn = confidence_interval_95(wr_agree_dn, len(agree_dn))
        lift_agree_dn = (wr_agree_dn - wr_mom_dn) * 10000

        # DOWN momentum + taker DISAGREES (ratio > thresh)
        disagree_dn = [w for w in mom_dn if w["ratio_1m"] > thresh]
        wr_disagree_dn = sum(1 for w in disagree_dn if w["direction"] == "DOWN") / len(disagree_dn) if disagree_dn else 0
        ci_disagree_dn = confidence_interval_95(wr_disagree_dn, len(disagree_dn))
        lift_disagree_dn = (wr_disagree_dn - wr_mom_dn) * 10000

        print(f"\n  Threshold: taker_ratio > {thresh} (agree) / < {1-thresh:.2f} (disagree)\n")
        rows = [
            [f"UP mom + taker AGREES   (>{thresh})", f"{len(agree_up)}", fmt_pct(wr_agree_up),
             f"±{fmt_pct(ci_agree_up)}", f"{lift_agree_up:+.0f} bps"],
            [f"UP mom + taker DISAGREES(<{1-thresh:.2f})", f"{len(disagree_up)}", fmt_pct(wr_disagree_up),
             f"±{fmt_pct(ci_disagree_up)}", f"{lift_disagree_up:+.0f} bps"],
            [f"DOWN mom + taker AGREES  (<{1-thresh:.2f})", f"{len(agree_dn)}", fmt_pct(wr_agree_dn),
             f"±{fmt_pct(ci_agree_dn)}", f"{lift_agree_dn:+.0f} bps"],
            [f"DOWN mom + taker DISAGREES(>{thresh})", f"{len(disagree_dn)}", fmt_pct(wr_disagree_dn),
             f"±{fmt_pct(ci_disagree_dn)}", f"{lift_disagree_dn:+.0f} bps"],
        ]
        print_table(headers, rows, widths)

    # Momentum strength buckets
    print("\n--- Momentum Strength Buckets + Taker Agreement (thresh=0.52) ---\n")
    buckets = [
        (MOMENTUM_BPS_THRESHOLD, 15, "weak"),
        (15, 30, "medium"),
        (30, 999, "strong"),
    ]
    headers = ["Bucket", "Direction", "Agree_N", "WR_agree", "Disagree_N", "WR_disagree", "Gap_bps"]
    widths = [16, 10, 10, 10, 12, 14, 10]
    rows = []

    for lo, hi, label in buckets:
        for direction, sign, mom_list, thresh_dir in [
            ("UP", 1, mom_up, 0.52),
            ("DOWN", -1, mom_dn, 0.48),
        ]:
            bucket = [w for w in mom_list if lo <= abs(w["momentum_1m_bps"]) < hi]
            if not bucket:
                continue
            if direction == "UP":
                agree = [w for w in bucket if w["ratio_1m"] > 0.52]
                disagree = [w for w in bucket if w["ratio_1m"] < 0.48]
                correct_dir = "UP"
            else:
                agree = [w for w in bucket if w["ratio_1m"] < 0.48]
                disagree = [w for w in bucket if w["ratio_1m"] > 0.52]
                correct_dir = "DOWN"

            wr_a = sum(1 for w in agree if w["direction"] == correct_dir) / len(agree) if agree else 0
            wr_d = sum(1 for w in disagree if w["direction"] == correct_dir) / len(disagree) if disagree else 0
            gap = (wr_a - wr_d) * 10000

            rows.append([f"{label}({lo}-{hi}bps)", direction,
                         str(len(agree)), fmt_pct(wr_a),
                         str(len(disagree)), fmt_pct(wr_d),
                         f"{gap:+.0f}"])

    print_table(headers, rows, widths)


# ---------------------------------------------------------------------------
# PART C: Volume imbalance evolution within 5M window
# ---------------------------------------------------------------------------
def part_c(windows):
    print("\n" + "=" * 72)
    print("PART C: Taker Ratio Evolution Within 5-Minute Window")
    print("=" * 72)

    total = len(windows)
    baseline_up = sum(1 for w in windows if w["direction"] == "UP") / total

    print(f"\nHow does per-minute taker ratio evolve, and does minute-1 predict minute-5?\n")

    # Mean taker ratio by minute and direction
    print("--- Average Taker Buy Ratio by Minute and Final Direction ---\n")
    headers = ["Minute", "All_Mean", "UP_windows_mean", "DOWN_windows_mean", "Diff_bps"]
    widths = [8, 10, 18, 20, 10]

    rows = []
    for m in range(5):
        all_ratios = [w["per_min_ratio"][m] for w in windows]
        up_ratios = [w["per_min_ratio"][m] for w in windows if w["direction"] == "UP"]
        dn_ratios = [w["per_min_ratio"][m] for w in windows if w["direction"] == "DOWN"]

        mean_all = sum(all_ratios) / len(all_ratios)
        mean_up = sum(up_ratios) / len(up_ratios) if up_ratios else 0
        mean_dn = sum(dn_ratios) / len(dn_ratios) if dn_ratios else 0
        diff_bps = (mean_up - mean_dn) * 10000

        rows.append([f"Min {m+1}", f"{mean_all:.3f}", f"{mean_up:.3f}", f"{mean_dn:.3f}", f"{diff_bps:+.1f}"])

    print_table(headers, rows, widths)

    # Autocorrelation: does minute-1 ratio predict minute-5 ratio direction?
    print("\n--- Does Minute-1 Ratio Predict Minute-5 Ratio Direction? ---\n")
    consistent_up = sum(1 for w in windows if w["per_min_ratio"][0] > 0.5 and w["per_min_ratio"][4] > 0.5)
    consistent_dn = sum(1 for w in windows if w["per_min_ratio"][0] < 0.5 and w["per_min_ratio"][4] < 0.5)
    flip = total - consistent_up - consistent_dn

    print(f"  Min-1 buying → Min-5 also buying: {consistent_up} ({fmt_pct(consistent_up/total)})")
    print(f"  Min-1 buying → Min-5 also selling: {flip} ({fmt_pct(flip/total)}) [flip cases included in both]")
    print(f"  Min-1 selling → Min-5 also selling: {consistent_dn} ({fmt_pct(consistent_dn/total)})")

    # Core question: min-1 ratio → 5M direction WR
    print("\n--- Minute-1 Taker Ratio → 5M Direction WR (by quartile) ---\n")
    sorted_by_ratio = sorted(windows, key=lambda w: w["ratio_1m"])
    q_size = total // 4

    headers = ["Quartile", "Ratio_range", "N", "WR_UP", "Lift_bps"]
    widths = [12, 14, 8, 8, 10]
    rows = []
    for q in range(4):
        start = q * q_size
        end = (q + 1) * q_size if q < 3 else total
        subset = sorted_by_ratio[start:end]
        r_min = subset[0]["ratio_1m"]
        r_max = subset[-1]["ratio_1m"]
        n = len(subset)
        wr = sum(1 for w in subset if w["direction"] == "UP") / n
        lift = (wr - baseline_up) * 10000
        label = ["Q1(lowest)", "Q2", "Q3", "Q4(highest)"][q]
        rows.append([label, f"{r_min:.3f}-{r_max:.3f}", str(n), fmt_pct(wr), f"{lift:+.0f}"])
    print_table(headers, rows, widths)

    # Persistence test: is taker flow mean-reverting or persistent?
    print("\n--- Taker Flow Persistence: Correlation Min-to-Min ---\n")
    print("  (Pearson r between consecutive minutes)\n")
    headers = ["Pair", "r", "Interpretation"]
    widths = [12, 8, 30]
    rows = []

    for m in range(4):
        x = [w["per_min_ratio"][m] for w in windows]
        y = [w["per_min_ratio"][m + 1] for w in windows]
        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)
        r = cov / (std_x * std_y) if std_x > 0 and std_y > 0 else 0
        interp = "persistent" if r > 0.1 else ("mean-reverting" if r < -0.1 else "random")
        rows.append([f"Min{m+1}→Min{m+2}", f"{r:.3f}", interp])

    print_table(headers, rows, widths)


# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
def summary(windows):
    print("\n" + "=" * 72)
    print("SUMMARY: Does Taker Buy/Sell Ratio Add Edge Over Momentum Alone?")
    print("=" * 72)

    total = len(windows)
    baseline_up = sum(1 for w in windows if w["direction"] == "UP") / total

    # Best standalone threshold
    best_thresh = None
    best_lift = -999
    best_n = 0
    best_wr = 0
    for thresh in THRESHOLDS:
        up_c = [w for w in windows if w["ratio_1m"] > thresh]
        if not up_c:
            continue
        wr = sum(1 for w in up_c if w["direction"] == "UP") / len(up_c)
        lift = (wr - baseline_up) * 10000
        if lift > best_lift and len(up_c) >= 500:
            best_lift = lift
            best_thresh = thresh
            best_n = len(up_c)
            best_wr = wr

    # Momentum alone
    mom_up = [w for w in windows if w["momentum_1m_bps"] > MOMENTUM_BPS_THRESHOLD]
    wr_mom = sum(1 for w in mom_up if w["direction"] == "UP") / len(mom_up) if mom_up else 0

    # Momentum + taker agrees (thresh=0.52)
    mom_agree = [w for w in mom_up if w["ratio_1m"] > 0.52]
    wr_mom_agree = sum(1 for w in mom_agree if w["direction"] == "UP") / len(mom_agree) if mom_agree else 0

    mom_disagree = [w for w in mom_up if w["ratio_1m"] < 0.48]
    wr_mom_disagree = sum(1 for w in mom_disagree if w["direction"] == "UP") / len(mom_disagree) if mom_disagree else 0

    print(f"""
  Dataset:         {total:,} five-minute windows (90 days)
  Baseline WR:     {fmt_pct(baseline_up)} (fraction of 5M candles that close UP)

  STANDALONE TAKER RATIO:
    Best threshold: ratio_1m > {best_thresh}
    N = {best_n:,}, WR = {fmt_pct(best_wr)}, Lift = {best_lift:+.0f} bps

  MOMENTUM SIGNAL (|T+60s| > {MOMENTUM_BPS_THRESHOLD}bps, UP direction):
    N = {len(mom_up):,}, WR = {fmt_pct(wr_mom)}

  MOMENTUM + TAKER AGREE (ratio_1m > 0.52):
    N = {len(mom_agree):,}, WR = {fmt_pct(wr_mom_agree)}, Lift = {(wr_mom_agree - wr_mom)*10000:+.0f} bps vs momentum alone

  MOMENTUM + TAKER DISAGREE (ratio_1m < 0.48):
    N = {len(mom_disagree):,}, WR = {fmt_pct(wr_mom_disagree)}, Lift = {(wr_mom_disagree - wr_mom)*10000:+.0f} bps vs momentum alone
""")

    # Verdict
    combined_lift = (wr_mom_agree - wr_mom) * 10000
    if abs(combined_lift) < 20 and len(mom_agree) > 200:
        verdict = "NEUTRAL — taker ratio adds minimal lift (<20 bps) to momentum signal. Not worth the complexity."
    elif combined_lift >= 50 and len(mom_agree) > 200:
        verdict = "POSITIVE — taker ratio adds meaningful lift (>=50 bps) to momentum signal. Consider as filter."
    elif combined_lift >= 20:
        verdict = "WEAK POSITIVE — small lift detected. Sample may be too small to confirm. Monitor live."
    else:
        verdict = "NEGATIVE — taker ratio HURTS when added to momentum. Likely noise or adverse selection."

    print(f"  VERDICT: {verdict}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading kline data...")
    klines = load_klines(DATA_PATH)
    print(f"  {len(klines):,} 1-minute candles loaded")

    print("Building 5-minute windows...")
    windows = build_5m_windows(klines)
    print(f"  {len(windows):,} valid non-overlapping 5M windows")

    part_a(windows)
    part_b(windows)
    part_c(windows)
    summary(windows)


if __name__ == "__main__":
    main()
