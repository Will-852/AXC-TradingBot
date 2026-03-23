#!/usr/bin/env python3
"""
W4 Signal Detection: Cross-reference Wallet 4's directional lean
with actual BTC price movement to reverse-engineer their signal.

All timestamps in UTC. ET = UTC-4.
"""

import json
import math
import re
import datetime
from collections import defaultdict
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────
BASE = Path("/Users/wai/projects/axc-trading")
W4_FILE = BASE / "data/wallet4_raw/wallet4_all_trades.json"
BTC_FILE = BASE / "polymarket/analysis/btc_1m_7days.json"
SOL_FILE = BASE / "polymarket/analysis/solusdt_1m_7days.json"
XRP_FILE = BASE / "polymarket/analysis/xrpusdt_1m_7days.json"

ET_OFFSET = -4 * 3600  # ET = UTC - 4 hours


# ─── HELPERS ──────────────────────────────────────────────────────────
def load_json(path):
    with open(path) as f:
        return json.load(f)


def build_kline_index(klines):
    """Build dict: ts_seconds -> kline for O(1) lookup."""
    return {k["ts"] // 1000: k for k in klines}


def get_kline_at(index, ts_sec):
    """Get the kline candle at or just before ts_sec."""
    # Round down to nearest minute
    ts_min = (ts_sec // 60) * 60
    if ts_min in index:
        return index[ts_min]
    # Try up to 5 minutes back
    for offset in range(1, 6):
        check = ts_min - offset * 60
        if check in index:
            return index[check]
    return None


def parse_window_from_slug(slug, title):
    """
    Extract window_start_utc, window_end_utc, coin, duration_minutes from slug/title.

    Slug patterns:
      btc-updown-5m-1774276800  -> start=1774276800, duration=5m
      btc-updown-15m-1774276200 -> start=1774276200, duration=15m
      bitcoin-up-or-down-march-23-2026-10am-et -> hourly, parse from title
    """
    coin_map = {
        "btc": "BTC", "bitcoin": "BTC",
        "eth": "ETH", "ethereum": "ETH",
        "sol": "SOL", "solana": "SOL",
        "xrp": "XRP",
    }

    # Try structured slug: {coin}-updown-{dur}-{ts}
    m = re.match(r"(\w+)-updown-(\d+)m-(\d{10})", slug)
    if m:
        coin_key, dur_str, ts_str = m.groups()
        coin = coin_map.get(coin_key, coin_key.upper())
        duration = int(dur_str)
        start_utc = int(ts_str)
        end_utc = start_utc + duration * 60
        return coin, start_utc, end_utc, duration

    # Hourly slug: bitcoin-up-or-down-march-23-2026-10am-et
    # Parse from title: "Bitcoin Up or Down - March 23, 10AM ET"
    m_title = re.match(
        r"(Bitcoin|Ethereum|Solana|XRP) Up or Down - (March) (\d+), (\d+)(AM|PM) ET",
        title,
    )
    if m_title:
        coin_name, month, day, hour, ampm = m_title.groups()
        coin = coin_map.get(coin_name.lower(), coin_name.upper())
        hour = int(hour)
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        # Construct UTC datetime (March 2026, ET = UTC-4)
        dt_et = datetime.datetime(2026, 3, int(day), hour, 0, 0, tzinfo=datetime.timezone.utc)
        start_utc = int(dt_et.timestamp()) - ET_OFFSET  # Convert ET to UTC
        end_utc = start_utc + 3600
        return coin, start_utc, end_utc, 60

    return None, None, None, None


# ═══════════════════════════════════════════════════════════════════════
# STEP 1: Parse W4 trades, group by market window
# ═══════════════════════════════════════════════════════════════════════
def step1_parse_trades(raw_trades):
    """Group trades by (coin, slug) = unique market window."""
    trades = [t for t in raw_trades if t["type"] == "TRADE"]

    windows = defaultdict(lambda: {
        "coin": None, "slug": None, "title": None,
        "window_start": None, "window_end": None, "duration_min": None,
        "up_usdc": 0.0, "down_usdc": 0.0,
        "up_shares": 0.0, "down_shares": 0.0,
        "up_prices": [], "down_prices": [],  # (usdc, price) for weighted avg
        "trade_timestamps": [],
        "first_trade_ts": float("inf"),
        "last_trade_ts": 0,
        "trade_count": 0,
    })

    for t in trades:
        slug = t["slug"]
        w = windows[slug]

        if w["coin"] is None:
            coin, start, end, dur = parse_window_from_slug(slug, t["title"])
            w["coin"] = coin
            w["slug"] = slug
            w["title"] = t["title"]
            w["window_start"] = start
            w["window_end"] = end
            w["duration_min"] = dur

        outcome = t["outcome"]  # "Up" or "Down"
        usdc = t["usdcSize"]
        shares = t["size"]
        price = t["price"]
        ts = t["timestamp"]

        if outcome == "Up":
            w["up_usdc"] += usdc
            w["up_shares"] += shares
            w["up_prices"].append((usdc, price))
        elif outcome == "Down":
            w["down_usdc"] += usdc
            w["down_shares"] += shares
            w["down_prices"].append((usdc, price))

        w["trade_timestamps"].append(ts)
        w["first_trade_ts"] = min(w["first_trade_ts"], ts)
        w["last_trade_ts"] = max(w["last_trade_ts"], ts)
        w["trade_count"] += 1

    # Calculate derived fields
    result = []
    for slug, w in windows.items():
        if w["window_start"] is None:
            continue

        total = w["up_usdc"] + w["down_usdc"]
        if total == 0:
            continue

        w["total_usdc"] = total

        if w["up_usdc"] > w["down_usdc"]:
            w["lean_direction"] = "UP"
            w["lean_ratio"] = w["up_usdc"] / max(w["down_usdc"], 0.01)
        elif w["down_usdc"] > w["up_usdc"]:
            w["lean_direction"] = "DOWN"
            w["lean_ratio"] = w["down_usdc"] / max(w["up_usdc"], 0.01)
        else:
            w["lean_direction"] = "NEUTRAL"
            w["lean_ratio"] = 1.0

        # Weighted average entry price per side
        if w["up_prices"]:
            total_w = sum(u for u, _ in w["up_prices"])
            w["up_wavg_price"] = sum(u * p for u, p in w["up_prices"]) / total_w if total_w > 0 else 0
        else:
            w["up_wavg_price"] = 0

        if w["down_prices"]:
            total_w = sum(u for u, _ in w["down_prices"])
            w["down_wavg_price"] = sum(u * p for u, p in w["down_prices"]) / total_w if total_w > 0 else 0
        else:
            w["down_wavg_price"] = 0

        result.append(w)

    result.sort(key=lambda w: w["window_start"])
    return result


# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Get BTC price context at W4's entry time
# ═══════════════════════════════════════════════════════════════════════
def step2_btc_context(windows, btc_index):
    """Enrich each window with BTC price data at entry time."""
    for w in windows:
        ws = w["window_start"]
        first_ts = w["first_trade_ts"]

        # BTC at window open
        k_open = get_kline_at(btc_index, ws)
        if k_open:
            w["btc_at_window_open"] = k_open["open"]
        else:
            w["btc_at_window_open"] = None

        # BTC at first trade
        k_entry = get_kline_at(btc_index, first_ts)
        if k_entry:
            w["btc_at_entry"] = k_entry["close"]
            w["btc_entry_candle_ts"] = (k_entry["ts"] // 1000)
        else:
            w["btc_at_entry"] = None
            w["btc_entry_candle_ts"] = None

        # M1 return at entry (log return of current vs previous candle)
        if k_entry:
            entry_min = k_entry["ts"] // 1000
            prev_min = entry_min - 60
            k_prev = btc_index.get(prev_min)
            if k_prev:
                w["btc_m1_return"] = math.log(k_entry["close"] / k_prev["close"])
            else:
                w["btc_m1_return"] = None
        else:
            w["btc_m1_return"] = None

        # Cumulative return since window open at entry
        if w["btc_at_window_open"] and w["btc_at_entry"]:
            w["btc_cum_return"] = math.log(w["btc_at_entry"] / w["btc_at_window_open"])
        else:
            w["btc_cum_return"] = None

        # 5-minute momentum at entry (log return over last 5 candles)
        if k_entry:
            entry_min = k_entry["ts"] // 1000
            k_5ago = btc_index.get(entry_min - 300)
            if k_5ago:
                w["btc_5m_momentum"] = math.log(k_entry["close"] / k_5ago["close"])
            else:
                w["btc_5m_momentum"] = None
        else:
            w["btc_5m_momentum"] = None

        # BTC direction: UP if current price > window open
        if w["btc_at_window_open"] and w["btc_at_entry"]:
            w["btc_direction"] = "UP" if w["btc_at_entry"] > w["btc_at_window_open"] else "DOWN"
        else:
            w["btc_direction"] = None

    return windows


# ═══════════════════════════════════════════════════════════════════════
# STEP 3: Cross-reference W4 lean vs BTC momentum
# ═══════════════════════════════════════════════════════════════════════
def step3_cross_reference(windows):
    """Analyze correlation between W4 lean and BTC direction."""

    print("\n" + "=" * 80)
    print("STEP 1: W4 TRADE SUMMARY BY MARKET WINDOW")
    print("=" * 80)

    for w in windows:
        ws_dt = datetime.datetime.fromtimestamp(w["window_start"], tz=datetime.timezone.utc)
        fe_dt = datetime.datetime.fromtimestamp(w["first_trade_ts"], tz=datetime.timezone.utc)
        le_dt = datetime.datetime.fromtimestamp(w["last_trade_ts"], tz=datetime.timezone.utc)

        print(f"\n{'─' * 70}")
        print(f"  {w['title']}")
        print(f"  Slug: {w['slug']} | Duration: {w['duration_min']}m")
        print(f"  Window: {ws_dt.strftime('%H:%M:%S')} UTC")
        print(f"  Trades: {w['trade_count']} | First: {fe_dt.strftime('%H:%M:%S')} | Last: {le_dt.strftime('%H:%M:%S')} UTC")
        entry_offset = w["first_trade_ts"] - w["window_start"]
        print(f"  Entry offset from window open: {entry_offset}s ({entry_offset/60:.1f}m)")
        print(f"  UP:   ${w['up_usdc']:8.2f} ({w['up_shares']:8.2f} shares) @ avg {w['up_wavg_price']:.4f}")
        print(f"  DOWN: ${w['down_usdc']:8.2f} ({w['down_shares']:8.2f} shares) @ avg {w['down_wavg_price']:.4f}")
        print(f"  LEAN: {w['lean_direction']} (ratio: {w['lean_ratio']:.2f}x) | Total: ${w['total_usdc']:.2f}")

    print("\n" + "=" * 80)
    print("STEP 2: BTC PRICE CONTEXT AT W4 ENTRY")
    print("=" * 80)

    for w in windows:
        print(f"\n{'─' * 70}")
        print(f"  {w['title']}")
        btc_open = w.get("btc_at_window_open")
        btc_entry = w.get("btc_at_entry")
        m1 = w.get("btc_m1_return")
        cum = w.get("btc_cum_return")
        mom5 = w.get("btc_5m_momentum")
        btc_dir = w.get("btc_direction")

        if btc_open:
            print(f"  BTC at window open: ${btc_open:,.2f}")
        else:
            print(f"  BTC at window open: N/A")
        if btc_entry:
            print(f"  BTC at first trade: ${btc_entry:,.2f}")
        else:
            print(f"  BTC at first trade: N/A")
        if m1 is not None:
            print(f"  M1 return at entry:  {m1*100:+.4f}%")
        if cum is not None:
            print(f"  Cum return from open: {cum*100:+.4f}%")
        if mom5 is not None:
            print(f"  5m momentum:          {mom5*100:+.4f}%")
        if btc_dir:
            print(f"  BTC direction:        {btc_dir}")

    print("\n" + "=" * 80)
    print("STEP 3: CROSS-REFERENCE — W4 LEAN vs BTC DIRECTION")
    print("=" * 80)

    # Filter to BTC windows only for primary analysis
    btc_windows = [w for w in windows if w["coin"] == "BTC"]
    all_windows = windows

    print(f"\n--- BTC WINDOWS ONLY ({len(btc_windows)} windows) ---")

    with_momentum = 0
    against_momentum = 0
    neutral_or_missing = 0

    lean_vs_btc = []

    for w in btc_windows:
        lean = w["lean_direction"]
        btc_dir = w.get("btc_direction")
        cum = w.get("btc_cum_return")
        mom5 = w.get("btc_5m_momentum")
        m1 = w.get("btc_m1_return")

        if lean == "NEUTRAL" or btc_dir is None:
            neutral_or_missing += 1
            alignment = "N/A"
        elif lean == btc_dir:
            with_momentum += 1
            alignment = "WITH"
        else:
            against_momentum += 1
            alignment = "AGAINST"

        lean_vs_btc.append({
            "title": w["title"],
            "lean": lean,
            "btc_dir": btc_dir,
            "alignment": alignment,
            "cum_return": cum,
            "mom5": mom5,
            "m1": m1,
            "lean_ratio": w["lean_ratio"],
            "total_usdc": w["total_usdc"],
        })

        cum_str = f"{cum*100:+.4f}%" if cum is not None else "N/A"
        mom5_str = f"{mom5*100:+.4f}%" if mom5 is not None else "N/A"
        m1_str = f"{m1*100:+.4f}%" if m1 is not None else "N/A"

        print(f"\n  {w['title']}")
        print(f"    W4 Lean: {lean} ({w['lean_ratio']:.1f}x) | BTC Dir: {btc_dir} | Alignment: {alignment}")
        print(f"    BTC cum: {cum_str} | 5m mom: {mom5_str} | M1: {m1_str}")
        print(f"    UP=${w['up_usdc']:.2f} vs DOWN=${w['down_usdc']:.2f} | Total ${w['total_usdc']:.2f}")

    total_classified = with_momentum + against_momentum
    print(f"\n{'═' * 70}")
    print(f"  SUMMARY (BTC windows):")
    print(f"  WITH momentum:    {with_momentum}/{total_classified} ({with_momentum/max(total_classified,1)*100:.0f}%)")
    print(f"  AGAINST momentum: {against_momentum}/{total_classified} ({against_momentum/max(total_classified,1)*100:.0f}%)")
    print(f"  Neutral/Missing:  {neutral_or_missing}")

    # All coins analysis
    print(f"\n--- ALL COINS ({len(all_windows)} windows) ---")

    # For non-BTC coins, use BTC direction at same timestamp as reference
    all_with = 0
    all_against = 0
    all_na = 0

    for w in all_windows:
        lean = w["lean_direction"]
        btc_dir = w.get("btc_direction")

        if lean == "NEUTRAL" or btc_dir is None:
            all_na += 1
            alignment = "N/A"
        elif lean == btc_dir:
            all_with += 1
            alignment = "WITH_BTC"
        else:
            all_against += 1
            alignment = "AGAINST_BTC"

        cum_str = f"{w.get('btc_cum_return', 0)*100:+.4f}%" if w.get("btc_cum_return") is not None else "N/A"

        print(f"  {w['coin']:4s} | {w['title'][:55]:55s} | Lean: {lean:6s} ({w['lean_ratio']:5.1f}x) | BTC: {str(btc_dir):5s} | {alignment:11s} | BTC cum: {cum_str}")

    all_classified = all_with + all_against
    print(f"\n  ALL COINS SUMMARY:")
    print(f"  WITH BTC momentum:    {all_with}/{all_classified} ({all_with/max(all_classified,1)*100:.0f}%)")
    print(f"  AGAINST BTC momentum: {all_against}/{all_classified} ({all_against/max(all_classified,1)*100:.0f}%)")
    print(f"  Neutral/Missing:      {all_na}")

    # Analyze by BTC return magnitude
    print(f"\n--- W4 LEAN vs BTC RETURN MAGNITUDE (BTC windows) ---")
    print(f"  {'BTC cum return':>20s} | {'Lean':>6s} | {'BTC dir':>7s} | {'Match':>6s} | {'Lean ratio':>10s} | USDC")

    for item in sorted(lean_vs_btc, key=lambda x: abs(x["cum_return"] or 0)):
        cum = item["cum_return"]
        cum_str = f"{cum*100:+.4f}%" if cum is not None else "N/A"
        match = "YES" if item["alignment"] == "WITH" else ("NO" if item["alignment"] == "AGAINST" else "N/A")
        print(f"  {cum_str:>20s} | {item['lean']:>6s} | {str(item['btc_dir']):>7s} | {match:>6s} | {item['lean_ratio']:>10.1f}x | ${item['total_usdc']:.0f}")

    # "Buy expensive side" analysis
    print(f"\n--- DOES W4 'BUY THE EXPENSIVE SIDE'? (lean with momentum = expensive) ---")
    for w in btc_windows:
        lean = w["lean_direction"]
        up_price = w["up_wavg_price"]
        down_price = w["down_wavg_price"]

        if lean == "UP":
            lean_price = up_price
            other_price = down_price
        elif lean == "DOWN":
            lean_price = down_price
            other_price = up_price
        else:
            lean_price = 0
            other_price = 0

        expensive = "EXPENSIVE" if lean_price > other_price else "CHEAP"

        print(f"  {w['title'][:55]:55s} | Lean: {lean:4s} @ {lean_price:.3f} vs {other_price:.3f} | Buying {expensive} side")

    return lean_vs_btc


# ═══════════════════════════════════════════════════════════════════════
# STEP 4: Backtest the discovered signal over full 7 days
# ═══════════════════════════════════════════════════════════════════════
def step4_backtest(btc_klines, btc_index):
    """Simulate all 5M windows over 7 days and test the signal."""

    print("\n" + "=" * 80)
    print("STEP 4: BACKTEST — SIMULATING ALL 5M WINDOWS OVER 7 DAYS")
    print("=" * 80)

    # Find the range of our BTC data
    first_ts = btc_klines[0]["ts"] // 1000
    last_ts = btc_klines[-1]["ts"] // 1000

    # Generate all 5-minute windows
    # Align to 5-minute boundaries
    start_5m = ((first_ts + 299) // 300) * 300  # Round up to next 5m boundary
    end_5m = (last_ts // 300) * 300  # Round down

    windows_5m = []
    t = start_5m
    while t + 300 <= last_ts:
        windows_5m.append(t)
        t += 300

    print(f"  Total 5M windows in data: {len(windows_5m)}")
    print(f"  Range: {datetime.datetime.fromtimestamp(start_5m, tz=datetime.timezone.utc)} to {datetime.datetime.fromtimestamp(end_5m, tz=datetime.timezone.utc)}")

    # For each window, determine BTC direction at various entry delays
    # Signal hypothesis: "lean WITH BTC momentum when |cum_return| > threshold"

    results_by_threshold = {}
    entry_delays = [60, 120, 180, 240]  # seconds after window open to check signal

    for delay in entry_delays:
        for threshold_bps in [0, 1, 2, 3, 5, 7, 10, 15, 20]:
            threshold = threshold_bps / 10000.0

            correct = 0
            wrong = 0
            skipped = 0
            total_pnl = 0.0
            trades_taken = 0

            for ws in windows_5m:
                we = ws + 300  # Window end
                entry_ts = ws + delay

                # Get BTC at window open
                k_open = btc_index.get(ws)
                if not k_open:
                    skipped += 1
                    continue
                btc_open = k_open["open"]

                # Get BTC at entry time
                entry_min = (entry_ts // 60) * 60
                k_entry = btc_index.get(entry_min)
                if not k_entry:
                    skipped += 1
                    continue
                btc_entry = k_entry["close"]

                # Calculate cumulative return
                if btc_open == 0:
                    skipped += 1
                    continue
                cum_ret = math.log(btc_entry / btc_open)

                # Apply threshold
                if abs(cum_ret) < threshold:
                    skipped += 1
                    continue

                # Signal: lean WITH momentum
                signal_dir = "UP" if cum_ret > 0 else "DOWN"

                # Get actual outcome: BTC at window end
                k_end = btc_index.get(we)
                if not k_end:
                    # Try nearby
                    for off in range(-2, 3):
                        k_end = btc_index.get(we + off * 60)
                        if k_end:
                            break
                if not k_end:
                    skipped += 1
                    continue

                btc_close = k_end["close"]
                actual_dir = "UP" if btc_close > btc_open else "DOWN"

                is_correct = signal_dir == actual_dir
                if is_correct:
                    correct += 1
                else:
                    wrong += 1

                trades_taken += 1

                # PnL: buy lean side at $0.48, other side at $0.48 (total cost = $0.96)
                # If correct: lean side pays $1, cost was $0.48 -> profit $0.52
                # If wrong: other side pays $1, cost was $0.48 -> profit $0.52... wait
                # Actually for a DIRECTIONAL bet:
                # Buy lean side at market price. If correct, pays $1. If wrong, pays $0.
                # Use average price of ~$0.50 for simulation
                # PnL per trade = $1.00 - $0.50 = $0.50 if correct, -$0.50 if wrong
                entry_price = 0.50
                pnl = (1.0 - entry_price) if is_correct else (-entry_price)
                total_pnl += pnl

            total = correct + wrong
            if total > 0:
                wr = correct / total * 100
            else:
                wr = 0

            key = (delay, threshold_bps)
            results_by_threshold[key] = {
                "delay": delay,
                "threshold_bps": threshold_bps,
                "correct": correct,
                "wrong": wrong,
                "total": total,
                "skipped": skipped,
                "win_rate": wr,
                "total_pnl": total_pnl,
                "pnl_per_trade": total_pnl / total if total > 0 else 0,
            }

    # Print results table
    print(f"\n--- SIGNAL: 'Lean WITH BTC momentum' | Entry at $0.50 ---")
    print(f"  Correct = BTC continues in momentum direction through window end")
    print(f"  Entry price assumed: $0.50 (midpoint)")
    print()

    for delay in entry_delays:
        print(f"  === Entry delay: {delay}s after window open ===")
        print(f"  {'Threshold':>10s} | {'Trades':>7s} | {'Win':>5s} | {'Lose':>5s} | {'WR':>6s} | {'PnL':>10s} | {'PnL/trade':>10s}")
        print(f"  {'-'*10} | {'-'*7} | {'-'*5} | {'-'*5} | {'-'*6} | {'-'*10} | {'-'*10}")

        for threshold_bps in [0, 1, 2, 3, 5, 7, 10, 15, 20]:
            r = results_by_threshold[(delay, threshold_bps)]
            thr_str = f"{threshold_bps}bps" if threshold_bps > 0 else "0 (all)"
            print(f"  {thr_str:>10s} | {r['total']:>7d} | {r['correct']:>5d} | {r['wrong']:>5d} | {r['win_rate']:>5.1f}% | ${r['total_pnl']:>+9.2f} | ${r['pnl_per_trade']:>+9.4f}")
        print()

    # Now test with REALISTIC pricing (not midpoint)
    # When momentum is with you, the lean side is EXPENSIVE (e.g., 0.55-0.65)
    # So let's test at various entry prices
    print(f"\n--- SENSITIVITY: Same signal, different entry prices ---")
    print(f"  (Using delay=120s, threshold=5bps as base)")
    print()
    print(f"  {'Entry Price':>12s} | {'Trades':>7s} | {'WR':>6s} | {'Profit if Win':>14s} | {'Loss if Lose':>13s} | {'EV/trade':>10s} | {'Total PnL':>10s}")
    print(f"  {'-'*12} | {'-'*7} | {'-'*6} | {'-'*14} | {'-'*13} | {'-'*10} | {'-'*10}")

    base = results_by_threshold[(120, 5)]
    wr_frac = base["win_rate"] / 100.0
    for price in [0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65]:
        profit_if_win = 1.0 - price
        loss_if_lose = price
        ev = wr_frac * profit_if_win - (1 - wr_frac) * loss_if_lose
        total = ev * base["total"]
        print(f"  ${price:>10.2f} | {base['total']:>7d} | {base['win_rate']:>5.1f}% | ${profit_if_win:>13.2f} | ${loss_if_lose:>12.2f} | ${ev:>+9.4f} | ${total:>+9.2f}")

    # Also: test AGAINST momentum (contrarian)
    print(f"\n--- CONTRARIAN SIGNAL: 'Lean AGAINST BTC momentum' ---")
    print(f"  === Entry delay: 120s ===")
    print(f"  {'Threshold':>10s} | {'Trades':>7s} | {'Win':>5s} | {'Lose':>5s} | {'WR':>6s}")
    print(f"  {'-'*10} | {'-'*7} | {'-'*5} | {'-'*5} | {'-'*6}")

    for threshold_bps in [0, 1, 2, 3, 5, 7, 10, 15, 20]:
        r = results_by_threshold[(120, threshold_bps)]
        # Contrarian = flip correct/wrong
        contr_wr = 100.0 - r["win_rate"] if r["total"] > 0 else 0
        thr_str = f"{threshold_bps}bps" if threshold_bps > 0 else "0 (all)"
        print(f"  {thr_str:>10s} | {r['total']:>7d} | {r['wrong']:>5d} | {r['correct']:>5d} | {contr_wr:>5.1f}%")

    # Direction accuracy by return magnitude bins
    print(f"\n--- DIRECTION ACCURACY BY BTC RETURN MAGNITUDE (delay=120s) ---")

    magnitude_bins = [
        (0, 2, "0-2 bps"),
        (2, 5, "2-5 bps"),
        (5, 10, "5-10 bps"),
        (10, 20, "10-20 bps"),
        (20, 50, "20-50 bps"),
        (50, 100, "50-100 bps"),
        (100, 500, "100+ bps"),
    ]

    delay = 120

    # Recompute with magnitude tracking
    bin_results = {label: {"correct": 0, "wrong": 0} for _, _, label in magnitude_bins}

    for ws in windows_5m:
        we = ws + 300
        entry_ts = ws + delay

        k_open = btc_index.get(ws)
        if not k_open:
            continue
        btc_open = k_open["open"]

        entry_min = (entry_ts // 60) * 60
        k_entry = btc_index.get(entry_min)
        if not k_entry:
            continue
        btc_entry = k_entry["close"]

        if btc_open == 0:
            continue
        cum_ret = math.log(btc_entry / btc_open)
        abs_ret_bps = abs(cum_ret) * 10000

        signal_dir = "UP" if cum_ret > 0 else "DOWN"

        k_end = btc_index.get(we)
        if not k_end:
            for off in range(-2, 3):
                k_end = btc_index.get(we + off * 60)
                if k_end:
                    break
        if not k_end:
            continue

        btc_close = k_end["close"]
        actual_dir = "UP" if btc_close > btc_open else "DOWN"

        is_correct = signal_dir == actual_dir

        for lo, hi, label in magnitude_bins:
            if lo <= abs_ret_bps < hi:
                if is_correct:
                    bin_results[label]["correct"] += 1
                else:
                    bin_results[label]["wrong"] += 1
                break

    print(f"  {'Magnitude':>12s} | {'Correct':>8s} | {'Wrong':>6s} | {'Total':>6s} | {'WR':>6s} | {'Edge (vs 50%)':>13s}")
    print(f"  {'-'*12} | {'-'*8} | {'-'*6} | {'-'*6} | {'-'*6} | {'-'*13}")

    for _, _, label in magnitude_bins:
        r = bin_results[label]
        total = r["correct"] + r["wrong"]
        if total > 0:
            wr = r["correct"] / total * 100
            edge = wr - 50
        else:
            wr = 0
            edge = 0
        print(f"  {label:>12s} | {r['correct']:>8d} | {r['wrong']:>6d} | {total:>6d} | {wr:>5.1f}% | {edge:>+12.1f}%")

    return results_by_threshold


# ═══════════════════════════════════════════════════════════════════════
# STEP 5: SOL/XRP cross-reference
# ═══════════════════════════════════════════════════════════════════════
def step5_cross_coin(windows, btc_index, sol_index, xrp_index):
    """For non-BTC windows, check if SOL/XRP follows BTC direction."""

    print("\n" + "=" * 80)
    print("STEP 5: SOL/XRP CROSS-REFERENCE — DO THEY FOLLOW BTC?")
    print("=" * 80)

    coin_index_map = {
        "SOL": sol_index,
        "XRP": xrp_index,
    }

    sol_windows = [w for w in windows if w["coin"] == "SOL"]
    xrp_windows = [w for w in windows if w["coin"] == "XRP"]
    eth_windows = [w for w in windows if w["coin"] == "ETH"]

    for coin_label, coin_windows in [("SOL", sol_windows), ("XRP", xrp_windows), ("ETH", eth_windows)]:
        print(f"\n--- {coin_label} WINDOWS ({len(coin_windows)}) ---")

        coin_idx = coin_index_map.get(coin_label)

        follow_btc = 0
        diverge_btc = 0
        lean_with_btc = 0
        lean_against_btc = 0
        lean_with_own = 0
        lean_against_own = 0

        for w in coin_windows:
            ws = w["window_start"]
            first_ts = w["first_trade_ts"]
            entry_min = (first_ts // 60) * 60

            # BTC direction
            btc_open_k = get_kline_at(btc_index, ws)
            btc_entry_k = get_kline_at(btc_index, first_ts)

            btc_dir = None
            btc_cum = None
            if btc_open_k and btc_entry_k:
                btc_cum = math.log(btc_entry_k["close"] / btc_open_k["open"])
                btc_dir = "UP" if btc_cum > 0 else "DOWN"

            # Own coin direction
            own_dir = None
            own_cum = None
            if coin_idx:
                own_open_k = get_kline_at(coin_idx, ws)
                own_entry_k = get_kline_at(coin_idx, first_ts)
                if own_open_k and own_entry_k:
                    own_cum = math.log(own_entry_k["close"] / own_open_k["open"])
                    own_dir = "UP" if own_cum > 0 else "DOWN"

            # Does own coin follow BTC?
            if btc_dir and own_dir:
                if btc_dir == own_dir:
                    follow_btc += 1
                else:
                    diverge_btc += 1

            # W4 lean vs BTC direction
            lean = w["lean_direction"]
            if lean != "NEUTRAL" and btc_dir:
                if lean == btc_dir:
                    lean_with_btc += 1
                else:
                    lean_against_btc += 1

            # W4 lean vs own coin direction
            if lean != "NEUTRAL" and own_dir:
                if lean == own_dir:
                    lean_with_own += 1
                else:
                    lean_against_own += 1

            btc_cum_str = f"{btc_cum*100:+.4f}%" if btc_cum is not None else "N/A"
            own_cum_str = f"{own_cum*100:+.4f}%" if own_cum is not None else "N/A"
            btc_follow = "FOLLOW" if (btc_dir and own_dir and btc_dir == own_dir) else ("DIVERGE" if (btc_dir and own_dir) else "N/A")
            lean_match = "WITH_BTC" if (lean != "NEUTRAL" and lean == btc_dir) else ("AGAINST_BTC" if (lean != "NEUTRAL" and btc_dir) else "N/A")

            print(f"  {w['title'][:55]:55s}")
            print(f"    W4 lean: {lean:4s} ({w['lean_ratio']:.1f}x) | BTC: {str(btc_dir):4s} ({btc_cum_str}) | {coin_label}: {str(own_dir):4s} ({own_cum_str}) | {btc_follow} | W4→BTC: {lean_match}")

        total_follow = follow_btc + diverge_btc
        total_lean_btc = lean_with_btc + lean_against_btc
        total_lean_own = lean_with_own + lean_against_own

        print(f"\n  {coin_label} SUMMARY:")
        if total_follow > 0:
            print(f"    {coin_label} follows BTC: {follow_btc}/{total_follow} ({follow_btc/total_follow*100:.0f}%)")
            print(f"    {coin_label} diverges from BTC: {diverge_btc}/{total_follow} ({diverge_btc/total_follow*100:.0f}%)")
        if total_lean_btc > 0:
            print(f"    W4 lean WITH BTC:    {lean_with_btc}/{total_lean_btc} ({lean_with_btc/total_lean_btc*100:.0f}%)")
            print(f"    W4 lean AGAINST BTC: {lean_against_btc}/{total_lean_btc} ({lean_against_btc/total_lean_btc*100:.0f}%)")
        if total_lean_own > 0:
            print(f"    W4 lean WITH {coin_label}:    {lean_with_own}/{total_lean_own} ({lean_with_own/total_lean_own*100:.0f}%)")
            print(f"    W4 lean AGAINST {coin_label}: {lean_against_own}/{total_lean_own} ({lean_against_own/total_lean_own*100:.0f}%)")

    # Multi-coin consistency: for windows where W4 trades multiple coins simultaneously
    print(f"\n--- MULTI-COIN CONSISTENCY ---")

    # Group by window_start to find overlapping windows
    by_start = defaultdict(list)
    for w in windows:
        by_start[w["window_start"]].append(w)

    multi = {k: v for k, v in by_start.items() if len(v) > 1}
    print(f"  Windows with multi-coin trades: {len(multi)}")

    all_same = 0
    mixed = 0

    for ws, ws_windows in sorted(multi.items()):
        leans = {w["coin"]: w["lean_direction"] for w in ws_windows}
        lean_vals = [v for v in leans.values() if v != "NEUTRAL"]
        is_consistent = len(set(lean_vals)) <= 1 if lean_vals else True

        dt = datetime.datetime.fromtimestamp(ws, tz=datetime.timezone.utc)
        leans_str = " | ".join(f"{c}:{d}" for c, d in sorted(leans.items()))
        consistency = "SAME" if is_consistent else "MIXED"

        if is_consistent:
            all_same += 1
        else:
            mixed += 1

        # BTC direction at this time
        btc_k = get_kline_at(btc_index, ws + 120)  # 2 min in
        btc_open_k = btc_index.get(ws)
        if btc_k and btc_open_k:
            btc_ret = math.log(btc_k["close"] / btc_open_k["open"])
            btc_str = f"BTC: {btc_ret*100:+.4f}%"
        else:
            btc_str = "BTC: N/A"

        print(f"  {dt.strftime('%H:%M')} UTC | {leans_str:50s} | {consistency:5s} | {btc_str}")

    total_multi = all_same + mixed
    if total_multi > 0:
        print(f"\n  Multi-coin consistency: {all_same}/{total_multi} ({all_same/total_multi*100:.0f}%) all lean same direction")
        print(f"  Mixed leans: {mixed}/{total_multi} ({mixed/total_multi*100:.0f}%)")


# ═══════════════════════════════════════════════════════════════════════
# STEP 6: Additional deep analysis
# ═══════════════════════════════════════════════════════════════════════
def step6_deep_analysis(windows, btc_index):
    """Additional pattern analysis."""

    print("\n" + "=" * 80)
    print("STEP 6: DEEP ANALYSIS — TIMING, SIZING, AND CONFIDENCE")
    print("=" * 80)

    btc_windows = [w for w in windows if w["coin"] == "BTC"]

    # Entry timing analysis: how far into the window does W4 enter?
    print(f"\n--- ENTRY TIMING ANALYSIS (BTC windows) ---")
    for w in btc_windows:
        entry_offset = w["first_trade_ts"] - w["window_start"]
        last_offset = w["last_trade_ts"] - w["window_start"]
        duration = w["duration_min"] * 60

        pct_in = entry_offset / duration * 100
        spread = last_offset - entry_offset

        print(f"  {w['title'][:55]:55s}")
        print(f"    First trade: {entry_offset}s into window ({pct_in:.0f}%)")
        print(f"    Last trade: {last_offset}s | Spread: {spread}s")
        print(f"    Trades: {w['trade_count']} over {spread}s = {w['trade_count']/max(spread,1)*60:.1f} trades/min")

    # Sizing pattern: does W4 size bigger with stronger conviction?
    print(f"\n--- SIZING vs CONVICTION (ALL windows, sorted by lean ratio) ---")
    print(f"  {'Title':55s} | {'Coin':4s} | {'Lean':4s} | {'Ratio':>6s} | {'Total $':>8s} | {'Lean $':>8s} | {'Hedge $':>8s}")
    print(f"  {'-'*55} | {'-'*4} | {'-'*4} | {'-'*6} | {'-'*8} | {'-'*8} | {'-'*8}")

    for w in sorted(windows, key=lambda x: x["lean_ratio"], reverse=True):
        lean = w["lean_direction"]
        if lean == "UP":
            lean_usd = w["up_usdc"]
            hedge_usd = w["down_usdc"]
        elif lean == "DOWN":
            lean_usd = w["down_usdc"]
            hedge_usd = w["up_usdc"]
        else:
            lean_usd = 0
            hedge_usd = 0

        print(f"  {w['title'][:55]:55s} | {w['coin']:4s} | {lean:4s} | {w['lean_ratio']:>5.1f}x | ${w['total_usdc']:>7.0f} | ${lean_usd:>7.0f} | ${hedge_usd:>7.0f}")

    # Price analysis: what prices does W4 buy at?
    print(f"\n--- ENTRY PRICE DISTRIBUTION (BTC windows) ---")
    for w in btc_windows:
        up_p = w["up_wavg_price"]
        down_p = w["down_wavg_price"]
        spread = abs(up_p + down_p - 1.0)  # How far from fair (should sum to ~1.0)

        print(f"  {w['title'][:55]:55s}")
        print(f"    UP avg: {up_p:.4f} | DOWN avg: {down_p:.4f} | Sum: {up_p+down_p:.4f} | Spread from 1.0: {spread:.4f}")

    # Time-based pattern: does W4 trade more aggressively at certain times?
    print(f"\n--- TRADE INTENSITY BY WINDOW ---")
    for w in sorted(windows, key=lambda x: x["total_usdc"], reverse=True):
        dt = datetime.datetime.fromtimestamp(w["window_start"], tz=datetime.timezone.utc)
        et_hour = (dt.hour - 4) % 24  # Convert to ET
        print(f"  ${w['total_usdc']:>8.0f} | {w['coin']:4s} {w['duration_min']:>2d}m | {dt.strftime('%H:%M')} UTC ({et_hour}:{dt.strftime('%M')} ET) | {w['trade_count']:>3d} trades | Lean: {w['lean_direction']} {w['lean_ratio']:.1f}x")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    print("=" * 80)
    print("W4 SIGNAL DETECTION — Cross-referencing W4 lean vs BTC price movement")
    print("=" * 80)

    # Load data
    print("\nLoading data...")
    raw_trades = load_json(W4_FILE)
    btc_klines = load_json(BTC_FILE)
    sol_klines = load_json(SOL_FILE)
    xrp_klines = load_json(XRP_FILE)

    print(f"  W4 trades: {len(raw_trades)} (TRADE: {sum(1 for t in raw_trades if t['type'] == 'TRADE')})")
    print(f"  BTC 1m klines: {len(btc_klines)}")
    print(f"  SOL 1m klines: {len(sol_klines)}")
    print(f"  XRP 1m klines: {len(xrp_klines)}")

    # Build kline indexes
    btc_index = build_kline_index(btc_klines)
    sol_index = build_kline_index(sol_klines)
    xrp_index = build_kline_index(xrp_klines)

    # Verify kline range
    btc_ts_range = (btc_klines[0]["ts"] // 1000, btc_klines[-1]["ts"] // 1000)
    print(f"  BTC range: {datetime.datetime.fromtimestamp(btc_ts_range[0], tz=datetime.timezone.utc)} to {datetime.datetime.fromtimestamp(btc_ts_range[1], tz=datetime.timezone.utc)}")

    # Step 1: Parse trades
    windows = step1_parse_trades(raw_trades)
    print(f"\n  Parsed {len(windows)} unique market windows")

    # Step 2: BTC context
    windows = step2_btc_context(windows, btc_index)

    # Step 3: Cross-reference
    lean_vs_btc = step3_cross_reference(windows)

    # Step 4: Backtest
    results = step4_backtest(btc_klines, btc_index)

    # Step 5: SOL/XRP cross-reference
    step5_cross_coin(windows, btc_index, sol_index, xrp_index)

    # Step 6: Deep analysis
    step6_deep_analysis(windows, btc_index)

    # ═══════════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("FINAL VERDICT — W4 SIGNAL SUMMARY")
    print("=" * 80)

    btc_windows = [w for w in windows if w["coin"] == "BTC"]

    # Count leans
    with_count = sum(1 for w in btc_windows if w.get("btc_direction") and w["lean_direction"] == w["btc_direction"])
    against_count = sum(1 for w in btc_windows if w.get("btc_direction") and w["lean_direction"] != w["btc_direction"] and w["lean_direction"] != "NEUTRAL")
    total_btc = with_count + against_count

    # USDC-weighted
    with_usdc = sum(w["total_usdc"] for w in btc_windows if w.get("btc_direction") and w["lean_direction"] == w["btc_direction"])
    against_usdc = sum(w["total_usdc"] for w in btc_windows if w.get("btc_direction") and w["lean_direction"] != w["btc_direction"] and w["lean_direction"] != "NEUTRAL")

    print(f"\n  W4 BTC LEAN ALIGNMENT:")
    print(f"    Window count: WITH momentum {with_count}/{total_btc} ({with_count/max(total_btc,1)*100:.0f}%) | AGAINST {against_count}/{total_btc} ({against_count/max(total_btc,1)*100:.0f}%)")
    print(f"    USDC weighted: WITH ${with_usdc:.0f} ({with_usdc/max(with_usdc+against_usdc,1)*100:.0f}%) | AGAINST ${against_usdc:.0f} ({against_usdc/max(with_usdc+against_usdc,1)*100:.0f}%)")

    # Best backtest result
    best = max(results.values(), key=lambda r: r["win_rate"] if r["total"] > 20 else 0)
    print(f"\n  BEST MOMENTUM SIGNAL (min 20 trades):")
    print(f"    Delay: {best['delay']}s | Threshold: {best['threshold_bps']}bps | WR: {best['win_rate']:.1f}% | Trades: {best['total']} | PnL@0.50: ${best['total_pnl']:+.2f}")

    # Is momentum persistent? Compare different thresholds at delay=120
    print(f"\n  MOMENTUM PERSISTENCE (delay=120s):")
    for bps in [0, 5, 10, 20]:
        r = results[(120, bps)]
        thr = f"{bps}bps" if bps > 0 else "all"
        edge = r["win_rate"] - 50
        print(f"    Threshold {thr:>5s}: WR={r['win_rate']:5.1f}% (edge={edge:+.1f}%) | n={r['total']}")

    # Key question: is W4 a momentum trader or contrarian?
    all_with = sum(1 for w in windows if w.get("btc_direction") and w["lean_direction"] == w["btc_direction"])
    all_against = sum(1 for w in windows if w.get("btc_direction") and w["lean_direction"] != w["btc_direction"] and w["lean_direction"] != "NEUTRAL")
    all_total = all_with + all_against

    print(f"\n  ALL COINS vs BTC DIRECTION:")
    print(f"    WITH BTC: {all_with}/{all_total} ({all_with/max(all_total,1)*100:.0f}%)")
    print(f"    AGAINST BTC: {all_against}/{all_total} ({all_against/max(all_total,1)*100:.0f}%)")

    if all_with > all_against:
        print(f"\n  >>> W4 is a MOMENTUM trader — leans WITH BTC direction")
    elif all_against > all_with:
        print(f"\n  >>> W4 is a CONTRARIAN trader — leans AGAINST BTC direction")
    else:
        print(f"\n  >>> W4 shows NO CLEAR directional bias relative to BTC momentum")

    # Multi-coin verdict
    by_start = defaultdict(list)
    for w in windows:
        by_start[w["window_start"]].append(w)
    multi = {k: v for k, v in by_start.items() if len(v) > 1}

    consistent = 0
    for ws, ws_windows in multi.items():
        leans = [w["lean_direction"] for w in ws_windows if w["lean_direction"] != "NEUTRAL"]
        if leans and len(set(leans)) == 1:
            consistent += 1

    if multi:
        print(f"\n  MULTI-COIN: {consistent}/{len(multi)} ({consistent/len(multi)*100:.0f}%) windows have consistent lean across coins")
        if consistent / len(multi) > 0.7:
            print(f"  >>> W4 trades a UNIFIED directional view across coins (likely BTC-driven)")
        else:
            print(f"  >>> W4 has INDEPENDENT views per coin (not purely BTC-driven)")


if __name__ == "__main__":
    main()
