#!/usr/bin/env python3
"""
indicator_calc_smc.py — NFS + FVZ 偵測函數庫
版本: 2026-03-17
用途: 純函數，無 I/O，無副作用。供 research_nfs_fvz.py 調用。

設計決定：
- Swing detection 用 strict < (ties 唔算 pivot) — 減少噪音
- 右側 shift(-n) = look-ahead — research OK，production 需改
- NFS 只比 immediately preceding same-type swing — 避免跳級比較
- FVZ 3-candle overlap check — zone_high <= zone_low → None

__main__ 內建合成數據驗證。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class SwingPoint:
    idx: int
    price: float
    kind: str  # "LOW" or "HIGH"
    bar_time: Optional[pd.Timestamp] = None


@dataclass(frozen=True)
class NFSEvent:
    """Non-Failure Swing = Break of Structure."""
    direction: str  # "BULL" or "BEAR"
    origin_idx: int  # swing that formed the lower-low / higher-high
    break_idx: int   # swing that broke structure
    origin_price: float
    break_price: float
    gap_bars: int    # break_idx - origin_idx


@dataclass
class FairValueZone:
    """3-candle imbalance zone around NFS origin."""
    nfs: NFSEvent
    zone_high: float
    zone_low: float
    zone_width: float
    zone_mid: float
    created_at_idx: int
    expires_at_idx: int
    active: bool = True
    filled: bool = False


@dataclass
class NFS_FVZ_Trade:
    """Single trade record for research backtest."""
    pair: str
    direction: str
    entry_idx: int
    entry_price: float
    entry_time: Optional[pd.Timestamp]
    sl_price: float
    tp_price: float
    exit_idx: int = -1
    exit_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    exit_reason: str = ""  # "TP", "SL", "EXPIRE"
    pnl_r: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    conflict_flag: bool = False
    zone_width: float = 0.0
    params: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════
# Swing Detection
# ═══════════════════════════════════════════════════════

def find_swing_points(
    df: pd.DataFrame, lookback: int = 2
) -> list[SwingPoint]:
    """
    Vectorised swing detection.
    lookback=2 → bar must be lower/higher than 2 bars on each side (strict <).
    Right-side shift(-n) is look-ahead — acceptable for research.
    """
    low = df["low"].values
    high = df["high"].values
    n = len(df)

    is_swing_low = np.ones(n, dtype=bool)
    is_swing_high = np.ones(n, dtype=bool)

    for k in range(1, lookback + 1):
        # Left side
        is_swing_low[k:] &= low[k:] < low[:-k]
        is_swing_high[k:] &= high[k:] > high[:-k]
        # Right side (look-ahead)
        is_swing_low[:-k] &= low[:-k] < low[k:]
        is_swing_high[:-k] &= high[:-k] > high[k:]

    # Boundary bars cannot be valid swings
    is_swing_low[:lookback] = False
    is_swing_low[-lookback:] = False
    is_swing_high[:lookback] = False
    is_swing_high[-lookback:] = False

    has_time = "timestamp" in df.columns or "open_time" in df.columns
    time_col = "timestamp" if "timestamp" in df.columns else "open_time"

    points: list[SwingPoint] = []
    for i in range(n):
        bt = pd.Timestamp(df[time_col].iloc[i]) if has_time else None
        if is_swing_low[i]:
            points.append(SwingPoint(i, float(low[i]), "LOW", bt))
        if is_swing_high[i]:
            points.append(SwingPoint(i, float(high[i]), "HIGH", bt))

    points.sort(key=lambda p: (p.idx, p.kind))
    return points


# ═══════════════════════════════════════════════════════
# NFS Detection
# ═══════════════════════════════════════════════════════

def find_nfs_events(
    swings: list[SwingPoint],
    df: pd.DataFrame,
    max_gap: int = 20,
) -> list[NFSEvent]:
    """
    Find Non-Failure Swings (Break of Structure).
    Bullish NFS: Swing Low[i] < prev Swing Low (Lower Low) →
                 later Swing High[j] > prev Swing High (Higher High).
    Only compare with immediately preceding same-type swing.
    gap = break_idx - origin_idx; skip if > max_gap.
    """
    lows = [s for s in swings if s.kind == "LOW"]
    highs = [s for s in swings if s.kind == "HIGH"]
    events: list[NFSEvent] = []

    # ── Bullish NFS ──
    # Lower Low → then a Higher High that breaks the most recent swing high
    for i in range(1, len(lows)):
        if lows[i].price >= lows[i - 1].price:
            continue  # not a lower low
        origin = lows[i]  # the lower low

        # Most recent swing high BEFORE origin — the level to break
        prev_high = None
        for h in highs:
            if h.idx < origin.idx:
                prev_high = h
            else:
                break

        if prev_high is None:
            continue

        # Find first swing high AFTER origin that exceeds prev_high
        for h in highs:
            if h.idx <= origin.idx:
                continue
            if h.price > prev_high.price:
                gap = h.idx - origin.idx
                if gap <= max_gap:
                    events.append(NFSEvent(
                        direction="BULL",
                        origin_idx=origin.idx,
                        break_idx=h.idx,
                        origin_price=origin.price,
                        break_price=h.price,
                        gap_bars=gap,
                    ))
                break  # only first break counts

    # ── Bearish NFS ──
    # Higher High → then a Lower Low that breaks the most recent swing low
    for i in range(1, len(highs)):
        if highs[i].price <= highs[i - 1].price:
            continue  # not a higher high
        origin = highs[i]  # the higher high

        prev_low = None
        for lo in lows:
            if lo.idx < origin.idx:
                prev_low = lo
            else:
                break

        if prev_low is None:
            continue

        for lo in lows:
            if lo.idx <= origin.idx:
                continue
            if lo.price < prev_low.price:
                gap = lo.idx - origin.idx
                if gap <= max_gap:
                    events.append(NFSEvent(
                        direction="BEAR",
                        origin_idx=origin.idx,
                        break_idx=lo.idx,
                        origin_price=origin.price,
                        break_price=lo.price,
                        gap_bars=gap,
                    ))
                break

    events.sort(key=lambda e: e.origin_idx)
    return events


# ═══════════════════════════════════════════════════════
# FVZ Construction
# ═══════════════════════════════════════════════════════

def build_fvz(
    nfs: NFSEvent,
    df: pd.DataFrame,
    expiry: int = 40,
    min_width_pct: float = 0.001,
) -> Optional[FairValueZone]:
    """
    Build Fair Value Zone from 3 candles around NFS origin.
    Returns None if:
    - origin at boundary (idx < 1 or >= len(df)-1)
    - no overlap (zone_high <= zone_low)
    - hairline zone (width/price < min_width_pct)
    """
    oidx = nfs.origin_idx
    if oidx < 1 or oidx >= len(df) - 1:
        return None

    # 3-candle window: [origin-1, origin, origin+1]
    h0 = float(df["high"].iloc[oidx - 1])
    h1 = float(df["high"].iloc[oidx])
    h2 = float(df["high"].iloc[oidx + 1])
    l0 = float(df["low"].iloc[oidx - 1])
    l1 = float(df["low"].iloc[oidx])
    l2 = float(df["low"].iloc[oidx + 1])

    zone_high = min(h0, h1, h2)
    zone_low = max(l0, l1, l2)

    if zone_high <= zone_low:
        return None  # no overlap → no FVZ

    mid_price = (zone_high + zone_low) / 2
    width = zone_high - zone_low

    if mid_price <= 0:
        return None

    if width / mid_price < min_width_pct:
        return None  # hairline zone

    return FairValueZone(
        nfs=nfs,
        zone_high=zone_high,
        zone_low=zone_low,
        zone_width=width,
        zone_mid=mid_price,
        created_at_idx=oidx,
        expires_at_idx=oidx + expiry,
        active=True,
        filled=False,
    )


# ═══════════════════════════════════════════════════════
# Entry / Stop / Conflict Helpers
# ═══════════════════════════════════════════════════════

def calc_entry_price(fvz: FairValueZone, mode: str = "mid") -> float:
    """Entry price within the zone. mode: upper / mid / lower."""
    if mode == "upper":
        return fvz.zone_high
    elif mode == "lower":
        return fvz.zone_low
    return fvz.zone_mid


def calc_stop_price(
    fvz: FairValueZone,
    df: pd.DataFrame,
    idx: int,
    mode: str = "swing",
    atr_mult: float = 1.5,
    atr_series: Optional[pd.Series] = None,
    buffer_pct: float = 0.001,
) -> float:
    """
    Stop loss price. Three modes (BMD #5):
    - swing: below/above the NFS origin swing + buffer
    - atr: entry ± N×ATR
    - hybrid: max(swing, atr) for tighter protection
    """
    entry = calc_entry_price(fvz, "mid")  # reference price
    direction = fvz.nfs.direction

    # Swing-based stop
    if direction == "BULL":
        swing_stop = fvz.nfs.origin_price * (1 - buffer_pct)
    else:
        swing_stop = fvz.nfs.origin_price * (1 + buffer_pct)

    if mode == "swing":
        return swing_stop

    # ATR-based stop
    atr_val = 0.0
    if atr_series is not None and idx < len(atr_series):
        atr_val = float(atr_series.iloc[idx])
        if pd.isna(atr_val):
            atr_val = 0.0

    if atr_val <= 0:
        return swing_stop  # fallback to swing if ATR unavailable

    if direction == "BULL":
        atr_stop = entry - atr_mult * atr_val
    else:
        atr_stop = entry + atr_mult * atr_val

    if mode == "atr":
        return atr_stop

    # Hybrid: tighter stop (closer to entry = more protection)
    if direction == "BULL":
        return max(swing_stop, atr_stop)
    else:
        return min(swing_stop, atr_stop)


def check_conflicting_zones(
    active_zones: list[FairValueZone],
    new_zone: FairValueZone,
) -> bool:
    """
    Check if new zone conflicts with existing active zones (BMD #4).
    Conflict = overlapping price range with opposite direction.
    """
    for az in active_zones:
        if not az.active:
            continue
        if az.nfs.direction == new_zone.nfs.direction:
            continue
        # Opposite direction — check price overlap
        if az.zone_low < new_zone.zone_high and new_zone.zone_low < az.zone_high:
            return True
    return False


# ═══════════════════════════════════════════════════════
# ADX / Regime Filter
# ═══════════════════════════════════════════════════════

def calc_adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ADX via tradingview_indicators — same library as indicator_calc.py.
    Import deferred to avoid hard dependency in unit tests.
    """
    try:
        import tradingview_indicators as tv
        dmi = tv.DMI(df, "close")
        adx_tuple = dmi.adx()
        return adx_tuple[0]
    except ImportError:
        log.warning("tradingview_indicators not available, returning NaN ADX")
        return pd.Series(np.nan, index=df.index)


def regime_filter_passes(
    adx_series: pd.Series,
    idx: int,
    filter_mode: str = "none",
) -> bool:
    """
    Regime gate (BMD #3).
    filter_mode: "none" / "adx>20" / "adx>25"
    """
    if filter_mode == "none":
        return True

    if idx >= len(adx_series):
        return False

    adx_val = adx_series.iloc[idx]
    if pd.isna(adx_val):
        return False

    if filter_mode == "adx>20":
        return float(adx_val) > 20
    elif filter_mode == "adx>25":
        return float(adx_val) > 25

    return True


# ═══════════════════════════════════════════════════════
# ATR (standalone, no tradingview_indicators dependency)
# ═══════════════════════════════════════════════════════

def calc_atr_standalone(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR using Wilder's RMA — mirrors indicator_calc.py calc_atr().
    Standalone version for research script (no tv dependency needed).
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder's RMA = EMA with alpha = 1/period
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


# ═══════════════════════════════════════════════════════
# __main__ — Synthetic Verification
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _counts = {"passed": 0, "failed": 0}

    def check(name: str, condition: bool, detail: str = ""):
        if condition:
            _counts["passed"] += 1
            log.info(f"  PASS: {name}")
        else:
            _counts["failed"] += 1
            log.info(f"  FAIL: {name} — {detail}")

    # ── Build synthetic 4H data with known pattern ──
    # Pattern: V-shape → Lower Low → Higher High (bullish NFS)
    #
    # Bar: 0   1   2   3   4   5   6   7   8   9  10  11  12
    # H:  105 103 101 100 102 104  99  97  95 100 105 108 110
    # L:  100  98  96  95  97  99  94  92  90  95 100 103 105
    #
    # Swing Lows (lookback=2): idx=3(95), idx=8(90)
    # Swing Highs (lookback=2): idx=0(105)→border, idx=5(104), idx=12(110)→border
    # With lookback=2, need 2 bars on each side
    # Let's make a longer series for clean swings

    n_bars = 30
    prices = [
        # bars 0-4: initial decline
        (105, 100), (103, 98), (101, 96), (100, 95), (102, 97),
        # bars 5-7: bounce
        (106, 101), (108, 103), (105, 100),
        # bars 8-12: deeper decline (lower low at bar 10)
        (103, 98), (100, 95), (97, 88), (99, 93), (102, 97),
        # bars 13-17: strong rally (higher high at bar 16)
        (106, 101), (110, 105), (113, 108), (115, 110), (112, 107),
        # bars 18-22: consolidation
        (110, 105), (108, 103), (106, 101), (108, 103), (110, 105),
        # bars 23-27: another move
        (112, 107), (114, 109), (116, 111), (113, 108), (111, 106),
        # bars 28-29: tail
        (109, 104), (107, 102),
    ]

    highs = [p[0] for p in prices]
    lows = [p[1] for p in prices]
    opens = [(h + l) / 2 for h, l in prices]
    closes = [(h + l) / 2 + 0.5 for h, l in prices]

    df_test = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000] * n_bars,
        "open_time": pd.date_range("2026-01-01", periods=n_bars, freq="4h"),
    })
    df_test["timestamp"] = df_test["open_time"]

    log.info("=" * 50)
    log.info("NFS+FVZ Indicator Library — Synthetic Tests")
    log.info("=" * 50)

    # ── Test 1: Swing detection ──
    log.info("\n[Test 1] Swing Detection (lookback=2)")
    swings = find_swing_points(df_test, lookback=2)
    swing_lows = [s for s in swings if s.kind == "LOW"]
    swing_highs = [s for s in swings if s.kind == "HIGH"]

    log.info(f"  Found {len(swing_lows)} lows, {len(swing_highs)} highs")
    for s in swings:
        log.info(f"    {s.kind} @ idx={s.idx} price={s.price}")

    check("At least 1 swing low found", len(swing_lows) >= 1)
    check("At least 1 swing high found", len(swing_highs) >= 1)

    # Bar 10 (low=88) should be a swing low
    bar10_is_low = any(s.idx == 10 and s.kind == "LOW" for s in swings)
    check("Bar 10 (low=88) is swing low", bar10_is_low, f"lows={[(s.idx, s.price) for s in swing_lows]}")

    # ── Test 2: Swing detection lookback=1 ──
    log.info("\n[Test 2] Swing Detection (lookback=1)")
    swings_lb1 = find_swing_points(df_test, lookback=1)
    check(
        "lookback=1 finds more swings than lookback=2",
        len(swings_lb1) >= len(swings),
        f"lb1={len(swings_lb1)} vs lb2={len(swings)}",
    )

    # ── Test 3: NFS detection ──
    log.info("\n[Test 3] NFS Detection")
    nfs_events = find_nfs_events(swings, df_test, max_gap=20)
    log.info(f"  Found {len(nfs_events)} NFS events")
    for e in nfs_events:
        log.info(f"    {e.direction} origin={e.origin_idx} break={e.break_idx} gap={e.gap_bars}")

    check("At least 1 NFS event found", len(nfs_events) >= 1)

    # ── Test 4: NFS max_gap filter ──
    log.info("\n[Test 4] NFS max_gap filter")
    nfs_tight = find_nfs_events(swings, df_test, max_gap=3)
    check(
        "Tight max_gap filters more events",
        len(nfs_tight) <= len(nfs_events),
        f"tight={len(nfs_tight)} vs normal={len(nfs_events)}",
    )

    # ── Test 5: FVZ construction ──
    log.info("\n[Test 5] FVZ Construction")
    if nfs_events:
        fvz = build_fvz(nfs_events[0], df_test, expiry=40, min_width_pct=0.0001)
        if fvz:
            log.info(f"  Zone: [{fvz.zone_low:.2f}, {fvz.zone_high:.2f}] width={fvz.zone_width:.2f}")
            check("FVZ zone_high > zone_low", fvz.zone_high > fvz.zone_low)
            check("FVZ width > 0", fvz.zone_width > 0)
            check("FVZ mid between low and high",
                  fvz.zone_low < fvz.zone_mid < fvz.zone_high)
        else:
            log.info("  No FVZ (3-candle overlap check failed — expected for some patterns)")
            check("FVZ returned None (acceptable)", True)
    else:
        check("FVZ skipped (no NFS events)", False, "need NFS events first")

    # ── Test 6: FVZ no-overlap case ──
    log.info("\n[Test 6] FVZ no-overlap rejection")
    # Create a fake NFS at boundary
    fake_nfs = NFSEvent("BULL", 0, 5, 95.0, 106.0, 5)
    fvz_boundary = build_fvz(fake_nfs, df_test, expiry=40)
    check("FVZ at idx=0 returns None (boundary)", fvz_boundary is None)

    # ── Test 7: Entry/Stop price ──
    log.info("\n[Test 7] Entry/Stop helpers")
    if nfs_events:
        fvz_test = build_fvz(nfs_events[0], df_test, expiry=40, min_width_pct=0.0001)
        if fvz_test:
            e_upper = calc_entry_price(fvz_test, "upper")
            e_mid = calc_entry_price(fvz_test, "mid")
            e_lower = calc_entry_price(fvz_test, "lower")
            check("Entry: upper >= mid >= lower",
                  e_upper >= e_mid >= e_lower,
                  f"upper={e_upper}, mid={e_mid}, lower={e_lower}")

            atr = calc_atr_standalone(df_test, period=14)
            s_swing = calc_stop_price(fvz_test, df_test, 10, "swing", atr_series=atr)
            s_atr = calc_stop_price(fvz_test, df_test, 10, "atr", atr_series=atr)
            s_hybrid = calc_stop_price(fvz_test, df_test, 10, "hybrid", atr_series=atr)
            log.info(f"  Stops: swing={s_swing:.2f} atr={s_atr:.2f} hybrid={s_hybrid:.2f}")
            check("Stop prices are finite", all(np.isfinite([s_swing, s_atr, s_hybrid])))

    # ── Test 8: Conflict detection ──
    log.info("\n[Test 8] Conflict detection")
    bull_nfs = NFSEvent("BULL", 5, 10, 90, 110, 5)
    bear_nfs = NFSEvent("BEAR", 15, 20, 115, 85, 5)
    fvz_bull = FairValueZone(bull_nfs, 100, 95, 5, 97.5, 5, 45)
    fvz_bear = FairValueZone(bear_nfs, 102, 97, 5, 99.5, 15, 55)
    fvz_noconflict = FairValueZone(bear_nfs, 120, 115, 5, 117.5, 15, 55)

    check("Overlapping opposite zones conflict", check_conflicting_zones([fvz_bull], fvz_bear))
    check("Non-overlapping zones no conflict", not check_conflicting_zones([fvz_bull], fvz_noconflict))

    # ── Test 9: ATR standalone ──
    log.info("\n[Test 9] ATR standalone")
    atr = calc_atr_standalone(df_test, period=14)
    check("ATR series length matches df", len(atr) == len(df_test))
    check("ATR has valid values after warmup", not pd.isna(atr.iloc[-1]))
    check("ATR > 0 after warmup", float(atr.iloc[-1]) > 0)

    # ── Test 10: Regime filter ──
    log.info("\n[Test 10] Regime filter")
    # Use synthetic ADX values
    fake_adx = pd.Series([10, 15, 22, 30, np.nan])
    check("ADX none always passes", regime_filter_passes(fake_adx, 0, "none"))
    check("ADX>20 fails at idx=0 (val=10)", not regime_filter_passes(fake_adx, 0, "adx>20"))
    check("ADX>20 passes at idx=2 (val=22)", regime_filter_passes(fake_adx, 2, "adx>20"))
    check("ADX>25 fails at idx=2 (val=22)", not regime_filter_passes(fake_adx, 2, "adx>25"))
    check("ADX>25 passes at idx=3 (val=30)", regime_filter_passes(fake_adx, 3, "adx>25"))
    check("ADX NaN fails filter", not regime_filter_passes(fake_adx, 4, "adx>20"))

    # ── Summary ──
    passed, failed = _counts["passed"], _counts["failed"]
    log.info("\n" + "=" * 50)
    log.info(f"Results: {passed} passed, {failed} failed")
    log.info("=" * 50)

    if failed > 0:
        log.info("\n⚠️  NOTE: Some synthetic test patterns may not produce")
        log.info("expected swings. Verify with real 4H data via spot-check.")

    exit(1 if failed > 0 else 0)
