"""
Staged Entry Simulation — Polymarket BTC 15M Binary Options
===========================================================
Compares 1-shot vs 2-tranche vs 3-tranche entry strategies.

Design decision: fetch Binance BTCUSDT 1m futures klines for 3+ months,
build 15M windows aligned to :00/:15/:30/:45, then simulate three strategies.

No pandas — plain lists + dicts for speed.
"""

import urllib.request
import json
import time
import math
import datetime
import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYMBOL = "BTCUSDT"
INTERVAL = "1m"
LIMIT = 1500           # max per Binance request
SIGNAL_THRESHOLD = 5   # bps = 0.0005 in decimal
T_OPEN = 0             # window open
T1 = 300               # 5 min — first tranche
T2 = 480               # 8 min — second tranche
T3 = 600               # 10 min — third tranche
T_CLOSE = 900          # 15 min — window close

# Cost model: buy $0.99 side, win pays $1.00  → net win = +$0.01 per $1 spent
# Net loss = −$0.99 per $1 spent
WIN_PAYOUT  = 1.00     # receive $1 on win
ENTRY_COST  = 0.99     # pay $0.99 per $1 notional

# For PnL: on a $100 budget:
#   Win:  +$100 * (WIN_PAYOUT - ENTRY_COST) / ENTRY_COST  ≈ +$1.01 per $100 deployed
#   Loss: −$100 (lose entire deployed stake)
# We'll compute per-window PnL relative to $100 notional budget

DATA_CACHE = os.path.join(os.path.dirname(__file__), "btc_1m_3months.json")

# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_klines(start_ms: int, end_ms: int) -> list:
    """Fetch 1m klines from Binance futures between start_ms and end_ms (UTC epoch ms)."""
    base = "https://fapi.binance.com/fapi/v1/klines"
    all_candles = []
    current = start_ms

    while current < end_ms:
        url = f"{base}?symbol={SYMBOL}&interval={INTERVAL}&limit={LIMIT}&startTime={current}&endTime={end_ms}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                batch = json.loads(resp.read())
        except Exception as e:
            print(f"  [WARN] fetch error at {current}: {e}, retrying in 3s")
            time.sleep(3)
            continue

        if not batch:
            break

        all_candles.extend(batch)
        last_ts = batch[-1][0]
        if last_ts >= end_ms or len(batch) < LIMIT:
            break
        current = last_ts + 60_000  # next minute
        time.sleep(0.1)  # gentle rate limit

    return all_candles


def load_or_fetch_data() -> list:
    """
    Load cached 1m data if fresh enough, else fetch ~3 months.
    Returns list of dicts: {ts, open, high, low, close}
    """
    # Check cache
    if os.path.exists(DATA_CACHE):
        with open(DATA_CACHE) as f:
            cached = json.load(f)
        if cached:
            first_ts = cached[0]["ts"]
            last_ts  = cached[-1]["ts"]
            now_ms   = int(time.time() * 1000)
            # Accept if covers 85+ days and ends within last 2 hours
            span_days = (last_ts - first_ts) / 86_400_000
            staleness_h = (now_ms - last_ts) / 3_600_000
            if span_days >= 85 and staleness_h < 2:
                print(f"[DATA] Using cache: {span_days:.0f} days, last point {staleness_h:.1f}h ago")
                return cached

    # Fetch 3 months (approx 90 days * 1440 min = 129,600 candles)
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - 90 * 24 * 3600 * 1000

    start_dt = datetime.datetime.fromtimestamp(start_ms / 1000, datetime.timezone.utc)
    end_dt   = datetime.datetime.fromtimestamp(now_ms / 1000, datetime.timezone.utc)
    print(f"[DATA] Fetching {SYMBOL} 1m from {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}")

    raw = fetch_klines(start_ms, now_ms)
    print(f"[DATA] Fetched {len(raw):,} candles")

    # Normalise
    candles = []
    for c in raw:
        candles.append({
            "ts":    c[0],
            "open":  float(c[1]),
            "high":  float(c[2]),
            "low":   float(c[3]),
            "close": float(c[4]),
        })

    # Save cache
    with open(DATA_CACHE, "w") as f:
        json.dump(candles, f)
    print(f"[DATA] Saved to cache: {DATA_CACHE}")

    return candles


# ---------------------------------------------------------------------------
# Build 15M Windows
# ---------------------------------------------------------------------------

def build_windows(candles: list) -> list:
    """
    Build 15M windows aligned to :00/:15/:30/:45.

    Each window dict:
      {
        window_start_ms,
        p_open,   p_300,  p_480,  p_600,  p_close,
        complete   (bool — all 15 candles present)
      }

    Strategy: build a ts→close lookup, then for each aligned 15M boundary
    grab the 4 price points.
    """
    # ts → open price of that minute
    ts_to_open  = {c["ts"]: c["open"]  for c in candles}
    ts_to_close = {c["ts"]: c["close"] for c in candles}

    # Find first aligned 15M boundary after first candle
    first_ts = candles[0]["ts"]
    last_ts  = candles[-1]["ts"]

    # Align to next :00/:15/:30/:45 minute boundary
    # Each 15M window starts at ms divisible by 900_000
    start_boundary = ((first_ts // 900_000) + 1) * 900_000
    end_boundary   = (last_ts // 900_000) * 900_000

    windows = []
    cur = start_boundary
    while cur + 900_000 <= last_ts + 60_000:
        w_open_ts  = cur
        w_t1_ts    = cur + T1  * 1000   # +300s = +5 min
        w_t2_ts    = cur + T2  * 1000   # +480s = +8 min
        w_t3_ts    = cur + T3  * 1000   # +600s = +10 min
        w_close_ts = cur + T_CLOSE * 1000  # +900s = +15 min

        # Use close of last candle before each timestamp as proxy for "price at Ts"
        # T+300s: price at minute-5 open (= minute 5 open = close of minute 4)
        # Minute-0 open = open of first candle of window
        # Minute-5 open = open of candle at cur+300_000
        # etc.

        p_open  = ts_to_open.get(w_open_ts)
        p_300   = ts_to_open.get(w_t1_ts)   # open of minute 5 = price at T+300s
        p_480   = ts_to_open.get(w_t2_ts)   # open of minute 8
        p_600   = ts_to_open.get(w_t3_ts)   # open of minute 10
        # p_close: open of minute 15 = close of minute 14
        # But minute 15 is the NEXT window's minute 0.
        # Better: close of the minute starting at cur+840_000 (minute 14)
        p_close_minute14_ts = cur + 840_000  # minute 14 open
        p_close = ts_to_close.get(p_close_minute14_ts)  # close of minute 14 = end of window

        # All price points must exist
        if all(p is not None for p in [p_open, p_300, p_480, p_600, p_close]):
            windows.append({
                "window_start_ms": w_open_ts,
                "p_open":  p_open,
                "p_300":   p_300,
                "p_480":   p_480,
                "p_600":   p_600,
                "p_close": p_close,
            })

        cur += 900_000  # next 15M window

    return windows


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

THRESHOLD = SIGNAL_THRESHOLD / 10_000  # 5 bps = 0.0005

def log_ret(p_new: float, p_ref: float) -> float:
    return math.log(p_new / p_ref)

def direction(ret: float) -> int:
    """+1 = UP, -1 = DOWN, 0 = no signal"""
    if ret > THRESHOLD:
        return 1
    if ret < -THRESHOLD:
        return -1
    return 0


def simulate(windows: list) -> dict:
    """
    Run strategies A, B, C on every window.

    Returns aggregated stats dict.
    """
    total = 0
    A_wins = A_losses = A_skipped = 0
    B_wins = B_losses = B_skipped = 0
    B_t2_deployed = B_t2_skipped = 0
    C_wins = C_losses = C_skipped = 0
    C_t2_deployed = C_t2_skipped = 0
    C_t3_deployed = C_t3_skipped = 0

    A_pnl = 0.0
    B_pnl = 0.0
    C_pnl = 0.0

    # For conditional WR: when T2 direction matches T1
    confirm_wins = confirm_losses = 0   # windows where T2 confirmed
    flip_wins    = flip_losses    = 0   # windows where T2 flipped

    # Track WR by T2/T3 confirmation
    t2_conf_wins = t2_conf_loss = 0
    t2_flip_wins = t2_flip_loss = 0
    t3_conf_wins = t3_conf_loss = 0
    t3_flip_wins = t3_flip_loss = 0

    for w in windows:
        p_open  = w["p_open"]
        p_300   = w["p_300"]
        p_480   = w["p_480"]
        p_600   = w["p_600"]
        p_close = w["p_close"]

        r_300 = log_ret(p_300, p_open)
        r_480 = log_ret(p_480, p_open)
        r_600 = log_ret(p_600, p_open)
        r_close = log_ret(p_close, p_open)

        d1 = direction(r_300)
        d2 = direction(r_480)
        d3 = direction(r_600)
        d_final = 1 if r_close > 0 else -1

        # Skip if no signal at T1
        if d1 == 0:
            A_skipped += 1
            B_skipped += 1
            C_skipped += 1
            continue

        total += 1
        win_A = (d1 == d_final)

        # ── Strategy A ──────────────────────────────────────────────────────
        if win_A:
            A_wins += 1
            A_pnl += 100 * (WIN_PAYOUT - ENTRY_COST) / ENTRY_COST
        else:
            A_losses += 1
            A_pnl -= 100.0

        # ── Strategy B (60/40) ───────────────────────────────────────────────
        b_t1_frac = 0.60
        b_t2_frac = 0.40

        b_deploy_frac = b_t1_frac
        b_dir = d1

        if d2 == d1:   # T2 confirms → deploy T2
            b_deploy_frac += b_t2_frac
            B_t2_deployed += 1
        else:
            B_t2_skipped += 1

        win_B = (b_dir == d_final)
        if win_B:
            B_wins += 1
            B_pnl += 100 * b_deploy_frac * (WIN_PAYOUT - ENTRY_COST) / ENTRY_COST
        else:
            B_losses += 1
            B_pnl -= 100 * b_deploy_frac

        # Conditional WR analysis
        if d2 == d1:
            if win_A: t2_conf_wins += 1
            else:      t2_conf_loss += 1
        elif d2 != 0:  # flipped (non-zero)
            if win_A: t2_flip_wins += 1
            else:      t2_flip_loss += 1

        # ── Strategy C (50/30/20) ─────────────────────────────────────────────
        c_t1_frac = 0.50
        c_t2_frac = 0.30
        c_t3_frac = 0.20

        c_deploy_frac = c_t1_frac
        c_dir = d1

        if d2 == d1:
            c_deploy_frac += c_t2_frac
            C_t2_deployed += 1
            if d3 == d1:
                c_deploy_frac += c_t3_frac
                C_t3_deployed += 1
            else:
                C_t3_skipped += 1
        else:
            C_t2_skipped += 1
            C_t3_skipped += 1  # T3 also skipped if T2 already skipped

        if d3 == d1:
            if win_A: t3_conf_wins += 1
            else:      t3_conf_loss += 1
        elif d3 != 0:
            if win_A: t3_flip_wins += 1
            else:      t3_flip_loss += 1

        win_C = (c_dir == d_final)
        if win_C:
            C_wins += 1
            C_pnl += 100 * c_deploy_frac * (WIN_PAYOUT - ENTRY_COST) / ENTRY_COST
        else:
            C_losses += 1
            C_pnl -= 100 * c_deploy_frac

    # Aggregate
    def wr(w, l): return w / (w + l) if (w + l) > 0 else 0.0
    def avg_dep(deployed_count, skip_count, full_frac, partial_frac, n):
        # average fraction of budget deployed per qualifying window
        if n == 0: return 0.0
        return (deployed_count * full_frac + skip_count * partial_frac) / n

    n = total
    B_avg_dep = (B_t2_deployed * 1.00 + B_t2_skipped * 0.60) / n if n else 0
    C_avg_dep = (
        C_t3_deployed * 1.00
        + (C_t2_deployed - C_t3_deployed) * 0.80   # T2 deployed, T3 skipped
        + C_t2_skipped * 0.50                       # both T2+T3 skipped
    ) / n if n else 0

    return {
        "total_windows":        total,
        "signal_skip":          A_skipped,  # same for all strategies
        # Strategy A
        "A_wins":               A_wins,
        "A_losses":             A_losses,
        "A_wr":                 wr(A_wins, A_losses),
        "A_pnl_per100":         A_pnl / n if n else 0,
        "A_avg_deploy":         1.00,
        # Strategy B
        "B_wins":               B_wins,
        "B_losses":             B_losses,
        "B_wr":                 wr(B_wins, B_losses),
        "B_pnl_per100":         B_pnl / n if n else 0,
        "B_avg_deploy":         B_avg_dep,
        "B_t2_deployed":        B_t2_deployed,
        "B_t2_skipped":         B_t2_skipped,
        "B_t2_skip_rate":       B_t2_skipped / n if n else 0,
        # Strategy C
        "C_wins":               C_wins,
        "C_losses":             C_losses,
        "C_wr":                 wr(C_wins, C_losses),
        "C_pnl_per100":         C_pnl / n if n else 0,
        "C_avg_deploy":         C_avg_dep,
        "C_t2_deployed":        C_t2_deployed,
        "C_t2_skipped":         C_t2_skipped,
        "C_t2_skip_rate":       C_t2_skipped / n if n else 0,
        "C_t3_deployed":        C_t3_deployed,
        "C_t3_skipped":         C_t3_skipped,
        "C_t3_skip_rate":       C_t3_skipped / n if n else 0,
        # Conditional WR
        "t2_conf_wr":           wr(t2_conf_wins, t2_conf_loss),
        "t2_conf_n":            t2_conf_wins + t2_conf_loss,
        "t2_flip_wr":           wr(t2_flip_wins, t2_flip_loss),
        "t2_flip_n":            t2_flip_wins + t2_flip_loss,
        "t3_conf_wr":           wr(t3_conf_wins, t3_conf_loss),
        "t3_conf_n":            t3_conf_wins + t3_conf_loss,
        "t3_flip_wr":           wr(t3_flip_wins, t3_flip_loss),
        "t3_flip_n":            t3_flip_wins + t3_flip_loss,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def fmt_pct(v):  return f"{v*100:.1f}%"
def fmt_pnl(v):  return f"${v:+.3f}"
def fmt_n(v):    return f"{v:,}"


def print_report(s: dict) -> None:
    n = s["total_windows"]
    skipped = s["signal_skip"]

    print()
    print("=" * 65)
    print("  STAGED ENTRY SIMULATION — BTC 15M POLYMARKET")
    print("=" * 65)
    print(f"  Total 15M windows available : {fmt_n(n + skipped)}")
    print(f"  Skipped (|ret| < 5bps)      : {fmt_n(skipped)}  ({fmt_pct(skipped/(n+skipped) if n+skipped else 0)})")
    print(f"  Qualifying windows (signal) : {fmt_n(n)}")
    print()

    print("┌─────────────────────────────────────────────────────────────┐")
    print("│  Strategy          WR       Avg Deploy   PnL / $100 budget  │")
    print("├─────────────────────────────────────────────────────────────┤")
    print(f"│  A  1-shot (100%)  {fmt_pct(s['A_wr']):<8} {fmt_pct(s['A_avg_deploy']):<13} {fmt_pnl(s['A_pnl_per100']):<18} │")
    print(f"│  B  2-tranche      {fmt_pct(s['B_wr']):<8} {fmt_pct(s['B_avg_deploy']):<13} {fmt_pnl(s['B_pnl_per100']):<18} │")
    print(f"│  C  3-tranche      {fmt_pct(s['C_wr']):<8} {fmt_pct(s['C_avg_deploy']):<13} {fmt_pnl(s['C_pnl_per100']):<18} │")
    print("└─────────────────────────────────────────────────────────────┘")

    print()
    print("  Tranche skip rates:")
    print(f"    B: T2 skip rate = {fmt_pct(s['B_t2_skip_rate'])}  "
          f"(skipped {fmt_n(s['B_t2_skipped'])} / deployed {fmt_n(s['B_t2_deployed'])})")
    print(f"    C: T2 skip rate = {fmt_pct(s['C_t2_skip_rate'])}  "
          f"T3 skip rate = {fmt_pct(s['C_t3_skip_rate'])}")

    print()
    print("  KEY INSIGHT — Conditional WR of A based on T+480s / T+600s direction:")
    print()
    print(f"  When T+480s CONFIRMS T+300s direction  → WR = {fmt_pct(s['t2_conf_wr'])}  (n={fmt_n(s['t2_conf_n'])})")
    print(f"  When T+480s FLIPS    T+300s direction  → WR = {fmt_pct(s['t2_flip_wr'])}  (n={fmt_n(s['t2_flip_n'])})")
    delta_t2 = s['t2_conf_wr'] - s['t2_flip_wr']
    print(f"  => T+480s confirmation edge = {delta_t2*100:+.1f} ppt")

    print()
    print(f"  When T+600s CONFIRMS T+300s direction  → WR = {fmt_pct(s['t3_conf_wr'])}  (n={fmt_n(s['t3_conf_n'])})")
    print(f"  When T+600s FLIPS    T+300s direction  → WR = {fmt_pct(s['t3_flip_wr'])}  (n={fmt_n(s['t3_flip_n'])})")
    delta_t3 = s['t3_conf_wr'] - s['t3_flip_wr']
    print(f"  => T+600s confirmation edge = {delta_t3*100:+.1f} ppt")

    print()
    print("  NOTE: PnL column above uses cost=0.99 / win=1.00 (worst case).")
    print("  Lower entry price = better. Real Polymarket fills: 0.55–0.80.")
    print()

    # Real EV table at various entry prices
    wr_a  = s["A_wr"]
    wr_t2 = s["t2_conf_wr"]
    wr_t3 = s["t3_conf_wr"]
    wr_fl = s["t2_flip_wr"]
    print("  EV per $1 at different entry prices (payout = $1.00 on win):")
    print(f"  {'Price':<8} {'EV_overall':>11} {'EV_T2conf':>11} {'EV_T3conf':>11} {'EV_T2flip':>11}")
    print(f"  {'-'*52}")
    for p in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        ev_a  = wr_a  * (1 - p) - (1 - wr_a)  * p
        ev_t2 = wr_t2 * (1 - p) - (1 - wr_t2) * p
        ev_t3 = wr_t3 * (1 - p) - (1 - wr_t3) * p
        ev_fl = wr_fl * (1 - p) - (1 - wr_fl) * p
        flag = " ← break-even" if abs(p - wr_a) < 0.02 else ""
        print(f"  {p:<8.2f} {ev_a:>+11.4f} {ev_t2:>+11.4f} {ev_t3:>+11.4f} {ev_fl:>+11.4f}{flag}")
    print(f"  Break-even price = WR (need to buy < {wr_a:.3f} for overall edge)")
    print(f"  Break-even T2-confirmed = {wr_t2:.3f}  |  T3-confirmed = {wr_t3:.3f}")

    print()
    # Interpretation
    print("  INTERPRETATION:")

    if delta_t2 > 0.01:
        print(f"  T+480s confirmation is a POSITIVE predictor (+{delta_t2*100:.1f} ppt WR lift)")
    elif delta_t2 < -0.01:
        print(f"  T+480s confirmation is NEGATIVE (momentum reversal regime, {delta_t2*100:.1f} ppt)")
    else:
        print(f"  T+480s confirmation is NEUTRAL (no significant WR difference)")

    if delta_t3 > 0.01:
        print(f"  T+600s confirmation is a POSITIVE predictor (+{delta_t3*100:.1f} ppt WR lift)")

    print()
    print("  Staging conclusion:")
    if delta_t2 > 0.03:
        print("  => STAGING HELPS: skip T2 when direction flips saves budget on losing bets.")
        print(f"     T2-flip WR={fmt_pct(s['t2_flip_wr'])} is significantly WORSE than overall WR.")
        print("     B/C strategies reduce capital at risk when momentum is weak.")
    else:
        print("  => STAGING NEUTRAL: T2/T3 confirmation does not filter losers effectively.")

    print("=" * 65)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    t0 = time.time()

    # 1. Load / fetch data
    candles = load_or_fetch_data()

    # 2. Build 15M windows
    print("[SIM] Building 15M windows...")
    windows = build_windows(candles)
    print(f"[SIM] {len(windows):,} complete 15M windows")

    # 3. Run simulation
    print("[SIM] Running strategy simulation...")
    stats = simulate(windows)

    # 4. Report
    print_report(stats)

    elapsed = time.time() - t0
    print(f"  Completed in {elapsed:.1f}s")
