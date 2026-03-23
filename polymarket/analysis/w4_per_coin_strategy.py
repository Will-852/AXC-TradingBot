#!/usr/bin/env python3
"""
W4 Per-Coin Strategy Analysis: How does Wallet 4 trade DIFFERENTLY across coins and timeframes?

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
    ts_min = (ts_sec // 60) * 60
    if ts_min in index:
        return index[ts_min]
    for offset in range(1, 6):
        check = ts_min - offset * 60
        if check in index:
            return index[check]
    return None


def parse_window_from_slug(slug, title):
    """Extract window_start_utc, window_end_utc, coin, duration_minutes from slug/title."""
    coin_map = {
        "btc": "BTC", "bitcoin": "BTC",
        "eth": "ETH", "ethereum": "ETH",
        "sol": "SOL", "solana": "SOL",
        "xrp": "XRP",
    }

    # Structured slug: {coin}-updown-{dur}-{ts}
    m = re.match(r"(\w+)-updown-(\d+)m-(\d{10})", slug)
    if m:
        coin_key, dur_str, ts_str = m.groups()
        coin = coin_map.get(coin_key, coin_key.upper())
        duration = int(dur_str)
        start_utc = int(ts_str)
        end_utc = start_utc + duration * 60
        return coin, start_utc, end_utc, duration

    # Hourly slug: bitcoin-up-or-down-march-23-2026-10am-et
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
        dt_et = datetime.datetime(2026, 3, int(day), hour, 0, 0, tzinfo=datetime.timezone.utc)
        start_utc = int(dt_et.timestamp()) - ET_OFFSET
        end_utc = start_utc + 3600
        return coin, start_utc, end_utc, 60

    return None, None, None, None


def wavg_price(price_list):
    """Weighted average price from [(usdc, price), ...]."""
    total_w = sum(u for u, _ in price_list)
    if total_w == 0:
        return 0.0
    return sum(u * p for u, p in price_list) / total_w


# ─── STEP 1: Parse all W4 trades into windows ────────────────────────
def parse_all_windows(raw_trades):
    """Group trades by slug -> unique market window."""
    trades = [t for t in raw_trades if t["type"] == "TRADE"]

    windows = defaultdict(lambda: {
        "coin": None, "slug": None, "title": None,
        "window_start": None, "window_end": None, "duration_min": None,
        "up_usdc": 0.0, "down_usdc": 0.0,
        "up_shares": 0.0, "down_shares": 0.0,
        "up_prices": [], "down_prices": [],
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

        outcome = t["outcome"]
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

    result = []
    for slug, w in windows.items():
        if w["window_start"] is None:
            continue
        total = w["up_usdc"] + w["down_usdc"]
        if total == 0:
            continue

        w["total_usdc"] = total
        w["up_wavg_price"] = wavg_price(w["up_prices"])
        w["down_wavg_price"] = wavg_price(w["down_prices"])

        if w["up_usdc"] > w["down_usdc"]:
            w["lean_direction"] = "UP"
            w["lean_ratio"] = w["up_usdc"] / max(w["down_usdc"], 0.01)
        elif w["down_usdc"] > w["up_usdc"]:
            w["lean_direction"] = "DOWN"
            w["lean_ratio"] = w["down_usdc"] / max(w["up_usdc"], 0.01)
        else:
            w["lean_direction"] = "NEUTRAL"
            w["lean_ratio"] = 1.0

        # Combined cost
        w["combined_cost"] = w["up_wavg_price"] + w["down_wavg_price"]

        result.append(w)

    result.sort(key=lambda w: w["window_start"])
    return result


# ─── Enrich with BTC context ─────────────────────────────────────────
def enrich_btc_context(windows, btc_index):
    """Add BTC price data at each window's entry time."""
    for w in windows:
        ws = w["window_start"]
        first_ts = w["first_trade_ts"]

        k_open = get_kline_at(btc_index, ws)
        w["btc_at_window_open"] = k_open["open"] if k_open else None

        k_entry = get_kline_at(btc_index, first_ts)
        if k_entry:
            w["btc_at_entry"] = k_entry["close"]
        else:
            w["btc_at_entry"] = None

        if w["btc_at_window_open"] and w["btc_at_entry"]:
            w["btc_cum_return"] = math.log(w["btc_at_entry"] / w["btc_at_window_open"])
            w["btc_direction"] = "UP" if w["btc_at_entry"] > w["btc_at_window_open"] else "DOWN"
        else:
            w["btc_cum_return"] = None
            w["btc_direction"] = None

        # 5m momentum
        if k_entry:
            entry_min = k_entry["ts"] // 1000
            k_5ago = btc_index.get(entry_min - 300)
            w["btc_5m_momentum"] = math.log(k_entry["close"] / k_5ago["close"]) if k_5ago else None
        else:
            w["btc_5m_momentum"] = None

    return windows


# ─── Enrich with SOL/XRP context ─────────────────────────────────────
def enrich_altcoin_context(windows, sol_index, xrp_index):
    """Add SOL/XRP price data at each window's entry time."""
    for w in windows:
        ws = w["window_start"]
        first_ts = w["first_trade_ts"]

        # SOL context
        sol_open_k = get_kline_at(sol_index, ws)
        sol_entry_k = get_kline_at(sol_index, first_ts)
        if sol_open_k and sol_entry_k:
            w["sol_at_window_open"] = sol_open_k["open"]
            w["sol_at_entry"] = sol_entry_k["close"]
            w["sol_cum_return"] = math.log(sol_entry_k["close"] / sol_open_k["open"])
            w["sol_direction"] = "UP" if sol_entry_k["close"] > sol_open_k["open"] else "DOWN"
        else:
            w["sol_at_window_open"] = None
            w["sol_at_entry"] = None
            w["sol_cum_return"] = None
            w["sol_direction"] = None

        # XRP context
        xrp_open_k = get_kline_at(xrp_index, ws)
        xrp_entry_k = get_kline_at(xrp_index, first_ts)
        if xrp_open_k and xrp_entry_k:
            w["xrp_at_window_open"] = xrp_open_k["open"]
            w["xrp_at_entry"] = xrp_entry_k["close"]
            w["xrp_cum_return"] = math.log(xrp_entry_k["close"] / xrp_open_k["open"])
            w["xrp_direction"] = "UP" if xrp_entry_k["close"] > xrp_open_k["open"] else "DOWN"
        else:
            w["xrp_at_window_open"] = None
            w["xrp_at_entry"] = None
            w["xrp_cum_return"] = None
            w["xrp_direction"] = None

    return windows


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 1: Per-Coin Breakdown
# ═══════════════════════════════════════════════════════════════════════
def analysis1_per_coin(windows):
    print("\n" + "=" * 100)
    print("ANALYSIS 1: PER-COIN BREAKDOWN")
    print("=" * 100)

    coins = sorted(set(w["coin"] for w in windows))

    header = f"{'Coin':>5} | {'Windows':>7} | {'Trades':>6} | {'Total USDC':>10} | {'%BUY Up':>8} | {'%BUY Dn':>8} | {'AvgUp$':>7} | {'AvgDn$':>7} | {'Combined':>8} | {'Lean Ratio':>10} | {'Lean UP':>7} | {'Lean DN':>7} | {'Neutral':>7}"
    print(f"\n{header}")
    print("-" * len(header))

    coin_stats = {}
    for coin in coins:
        cw = [w for w in windows if w["coin"] == coin]
        n_windows = len(cw)
        n_trades = sum(w["trade_count"] for w in cw)
        total_usdc = sum(w["total_usdc"] for w in cw)
        total_up_usdc = sum(w["up_usdc"] for w in cw)
        total_down_usdc = sum(w["down_usdc"] for w in cw)
        pct_up = total_up_usdc / total_usdc * 100 if total_usdc > 0 else 0
        pct_down = total_down_usdc / total_usdc * 100 if total_usdc > 0 else 0

        # Average entry prices across all windows
        all_up_prices = []
        all_down_prices = []
        for w in cw:
            all_up_prices.extend(w["up_prices"])
            all_down_prices.extend(w["down_prices"])

        avg_up = wavg_price(all_up_prices)
        avg_down = wavg_price(all_down_prices)
        combined = avg_up + avg_down

        # Lean counts
        lean_up = sum(1 for w in cw if w["lean_direction"] == "UP")
        lean_down = sum(1 for w in cw if w["lean_direction"] == "DOWN")
        lean_neutral = sum(1 for w in cw if w["lean_direction"] == "NEUTRAL")

        # Average lean ratio
        ratios = [w["lean_ratio"] for w in cw if w["lean_direction"] != "NEUTRAL"]
        avg_lean_ratio = sum(ratios) / len(ratios) if ratios else 1.0

        print(f"{coin:>5} | {n_windows:>7} | {n_trades:>6} | ${total_usdc:>9.2f} | {pct_up:>7.1f}% | {pct_down:>7.1f}% | {avg_up:>7.4f} | {avg_down:>7.4f} | {combined:>8.4f} | {avg_lean_ratio:>9.2f}x | {lean_up:>7} | {lean_down:>7} | {lean_neutral:>7}")

        coin_stats[coin] = {
            "windows": cw, "n_windows": n_windows, "n_trades": n_trades,
            "total_usdc": total_usdc, "pct_up": pct_up, "pct_down": pct_down,
            "avg_up": avg_up, "avg_down": avg_down, "combined": combined,
            "avg_lean_ratio": avg_lean_ratio, "lean_up": lean_up, "lean_down": lean_down,
        }

    # Per-coin USDC distribution by lean direction
    print(f"\n--- Per-Coin USDC by Lean Direction ---")
    header2 = f"{'Coin':>5} | {'UP USDC':>10} | {'DN USDC':>10} | {'UP-DN diff':>10} | {'Lean Bias':>10}"
    print(header2)
    print("-" * len(header2))
    for coin in coins:
        s = coin_stats[coin]
        up_total = sum(w["up_usdc"] for w in s["windows"])
        dn_total = sum(w["down_usdc"] for w in s["windows"])
        diff = up_total - dn_total
        bias = "UP" if diff > 0 else "DOWN"
        print(f"{coin:>5} | ${up_total:>9.2f} | ${dn_total:>9.2f} | ${diff:>+9.2f} | {bias:>10}")

    return coin_stats


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 2: Per-Timeframe Breakdown
# ═══════════════════════════════════════════════════════════════════════
def analysis2_per_timeframe(windows):
    print("\n" + "=" * 100)
    print("ANALYSIS 2: PER-TIMEFRAME BREAKDOWN")
    print("=" * 100)

    tfs = sorted(set(w["duration_min"] for w in windows))
    tf_labels = {5: "5M", 15: "15M", 60: "1H"}

    header = f"{'TF':>5} | {'Windows':>7} | {'Trades':>6} | {'Total USDC':>10} | {'%BUY Up':>8} | {'%BUY Dn':>8} | {'AvgUp$':>7} | {'AvgDn$':>7} | {'Combined':>8} | {'Avg$/Win':>8} | {'Fills/Win':>9} | {'LeanRatio':>9}"
    print(f"\n{header}")
    print("-" * len(header))

    tf_stats = {}
    for tf in tfs:
        tw = [w for w in windows if w["duration_min"] == tf]
        n_windows = len(tw)
        n_trades = sum(w["trade_count"] for w in tw)
        total_usdc = sum(w["total_usdc"] for w in tw)
        total_up_usdc = sum(w["up_usdc"] for w in tw)
        total_down_usdc = sum(w["down_usdc"] for w in tw)
        pct_up = total_up_usdc / total_usdc * 100 if total_usdc > 0 else 0
        pct_down = total_down_usdc / total_usdc * 100 if total_usdc > 0 else 0

        all_up_prices = []
        all_down_prices = []
        for w in tw:
            all_up_prices.extend(w["up_prices"])
            all_down_prices.extend(w["down_prices"])

        avg_up = wavg_price(all_up_prices)
        avg_down = wavg_price(all_down_prices)
        combined = avg_up + avg_down

        avg_usdc_per_win = total_usdc / n_windows if n_windows > 0 else 0
        fills_per_win = n_trades / n_windows if n_windows > 0 else 0

        ratios = [w["lean_ratio"] for w in tw if w["lean_direction"] != "NEUTRAL"]
        avg_lean_ratio = sum(ratios) / len(ratios) if ratios else 1.0

        label = tf_labels.get(tf, f"{tf}m")
        print(f"{label:>5} | {n_windows:>7} | {n_trades:>6} | ${total_usdc:>9.2f} | {pct_up:>7.1f}% | {pct_down:>7.1f}% | {avg_up:>7.4f} | {avg_down:>7.4f} | {combined:>8.4f} | ${avg_usdc_per_win:>7.2f} | {fills_per_win:>9.1f} | {avg_lean_ratio:>8.2f}x")

        tf_stats[tf] = {"windows": tw, "n_windows": n_windows, "total_usdc": total_usdc}

    # Per-TF per-coin breakdown
    print(f"\n--- Per-Timeframe Per-Coin Breakdown ---")
    coins = sorted(set(w["coin"] for w in windows))
    header3 = f"{'TF':>5} | {'Coin':>5} | {'Windows':>7} | {'Trades':>6} | {'USDC':>10} | {'Avg$/Win':>8} | {'%Up':>6} | {'Lean UP':>7} | {'Lean DN':>7}"
    print(header3)
    print("-" * len(header3))
    for tf in tfs:
        for coin in coins:
            cw = [w for w in windows if w["duration_min"] == tf and w["coin"] == coin]
            if not cw:
                continue
            n = len(cw)
            trades = sum(w["trade_count"] for w in cw)
            usdc = sum(w["total_usdc"] for w in cw)
            up_usdc = sum(w["up_usdc"] for w in cw)
            pct_up = up_usdc / usdc * 100 if usdc > 0 else 0
            avg_per_win = usdc / n
            lu = sum(1 for w in cw if w["lean_direction"] == "UP")
            ld = sum(1 for w in cw if w["lean_direction"] == "DOWN")
            label = tf_labels.get(tf, f"{tf}m")
            print(f"{label:>5} | {coin:>5} | {n:>7} | {trades:>6} | ${usdc:>9.2f} | ${avg_per_win:>7.2f} | {pct_up:>5.1f}% | {lu:>7} | {ld:>7}")

    return tf_stats


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 3: Cross-Coin Direction Correlation
# ═══════════════════════════════════════════════════════════════════════
def analysis3_cross_coin_correlation(windows):
    print("\n" + "=" * 100)
    print("ANALYSIS 3: CROSS-COIN DIRECTION CORRELATION")
    print("=" * 100)

    # Group windows by (window_start, duration) to find concurrent windows
    concurrent = defaultdict(dict)
    for w in windows:
        key = (w["window_start"], w["duration_min"])
        concurrent[key][w["coin"]] = w

    # Find windows with multiple coins
    multi_coin_windows = {k: v for k, v in concurrent.items() if len(v) >= 2}
    print(f"\nWindows with multiple coins: {len(multi_coin_windows)}")
    print(f"Total concurrent windows: {len(concurrent)}")

    if not multi_coin_windows:
        print("  No multi-coin windows found.")
        return

    # Check: do all coins lean the same direction?
    all_same = 0
    mixed = 0
    for key, coin_dict in sorted(multi_coin_windows.items()):
        leans = {c: w["lean_direction"] for c, w in coin_dict.items()}
        unique_leans = set(v for v in leans.values() if v != "NEUTRAL")
        if len(unique_leans) <= 1:
            all_same += 1
        else:
            mixed += 1

    total = all_same + mixed
    print(f"\n--- All coins lean same direction? ---")
    print(f"  ALL SAME direction: {all_same}/{total} ({all_same/total*100:.0f}%)")
    print(f"  MIXED directions:   {mixed}/{total} ({mixed/total*100:.0f}%)")

    # Detailed: for each multi-coin window, show lean per coin
    print(f"\n--- Detailed Multi-Coin Windows (last 30) ---")
    header = f"{'Window Start':>20} | {'TF':>3} | {'Coins':>20} | {'Leans':>30} | {'Same?':>5}"
    print(header)
    print("-" * len(header))

    sorted_keys = sorted(multi_coin_windows.keys())
    for key in sorted_keys[-30:]:
        ws, dur = key
        coin_dict = multi_coin_windows[key]
        dt = datetime.datetime.fromtimestamp(ws, tz=datetime.timezone.utc)
        coins_str = ",".join(sorted(coin_dict.keys()))
        leans_str = ",".join(f"{c}={coin_dict[c]['lean_direction']}" for c in sorted(coin_dict.keys()))
        unique = set(w["lean_direction"] for w in coin_dict.values() if w["lean_direction"] != "NEUTRAL")
        same = "YES" if len(unique) <= 1 else "NO"
        print(f"{dt.strftime('%m-%d %H:%M'):>20} | {dur:>3} | {coins_str:>20} | {leans_str:>30} | {same:>5}")

    # BTC vs SOL correlation
    print(f"\n--- BTC vs SOL Lean Correlation ---")
    btc_down_sol = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    btc_up_sol = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    for key, coin_dict in multi_coin_windows.items():
        if "BTC" in coin_dict and "SOL" in coin_dict:
            btc_lean = coin_dict["BTC"]["lean_direction"]
            sol_lean = coin_dict["SOL"]["lean_direction"]
            if btc_lean == "DOWN":
                btc_down_sol[sol_lean] += 1
            elif btc_lean == "UP":
                btc_up_sol[sol_lean] += 1

    print(f"  When BTC leans DOWN: SOL leans UP={btc_down_sol['UP']}, DOWN={btc_down_sol['DOWN']}, NEUTRAL={btc_down_sol['NEUTRAL']}")
    print(f"  When BTC leans UP:   SOL leans UP={btc_up_sol['UP']}, DOWN={btc_up_sol['DOWN']}, NEUTRAL={btc_up_sol['NEUTRAL']}")
    btc_dn_total = btc_down_sol["UP"] + btc_down_sol["DOWN"]
    if btc_dn_total > 0:
        print(f"  -> BTC DOWN -> SOL goes AGAINST BTC: {btc_down_sol['UP']}/{btc_dn_total} ({btc_down_sol['UP']/btc_dn_total*100:.0f}%)")
    btc_up_total = btc_up_sol["UP"] + btc_up_sol["DOWN"]
    if btc_up_total > 0:
        print(f"  -> BTC UP -> SOL follows BTC: {btc_up_sol['UP']}/{btc_up_total} ({btc_up_sol['UP']/btc_up_total*100:.0f}%)")

    # BTC vs XRP correlation
    print(f"\n--- BTC vs XRP Lean Correlation ---")
    btc_down_xrp = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    btc_up_xrp = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    for key, coin_dict in multi_coin_windows.items():
        if "BTC" in coin_dict and "XRP" in coin_dict:
            btc_lean = coin_dict["BTC"]["lean_direction"]
            xrp_lean = coin_dict["XRP"]["lean_direction"]
            if btc_lean == "DOWN":
                btc_down_xrp[xrp_lean] += 1
            elif btc_lean == "UP":
                btc_up_xrp[xrp_lean] += 1

    print(f"  When BTC leans DOWN: XRP leans UP={btc_down_xrp['UP']}, DOWN={btc_down_xrp['DOWN']}, NEUTRAL={btc_down_xrp['NEUTRAL']}")
    print(f"  When BTC leans UP:   XRP leans UP={btc_up_xrp['UP']}, DOWN={btc_up_xrp['DOWN']}, NEUTRAL={btc_up_xrp['NEUTRAL']}")
    btc_dn_xrp_total = btc_down_xrp["UP"] + btc_down_xrp["DOWN"]
    if btc_dn_xrp_total > 0:
        print(f"  -> BTC DOWN -> XRP goes AGAINST BTC: {btc_down_xrp['UP']}/{btc_dn_xrp_total} ({btc_down_xrp['UP']/btc_dn_xrp_total*100:.0f}%)")
    btc_up_xrp_total = btc_up_xrp["UP"] + btc_up_xrp["DOWN"]
    if btc_up_xrp_total > 0:
        print(f"  -> BTC UP -> XRP follows BTC: {btc_up_xrp['UP']}/{btc_up_xrp_total} ({btc_up_xrp['UP']/btc_up_xrp_total*100:.0f}%)")

    # BTC vs ETH correlation
    print(f"\n--- BTC vs ETH Lean Correlation ---")
    btc_down_eth = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    btc_up_eth = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    for key, coin_dict in multi_coin_windows.items():
        if "BTC" in coin_dict and "ETH" in coin_dict:
            btc_lean = coin_dict["BTC"]["lean_direction"]
            eth_lean = coin_dict["ETH"]["lean_direction"]
            if btc_lean == "DOWN":
                btc_down_eth[eth_lean] += 1
            elif btc_lean == "UP":
                btc_up_eth[eth_lean] += 1

    print(f"  When BTC leans DOWN: ETH leans UP={btc_down_eth['UP']}, DOWN={btc_down_eth['DOWN']}, NEUTRAL={btc_down_eth['NEUTRAL']}")
    print(f"  When BTC leans UP:   ETH leans UP={btc_up_eth['UP']}, DOWN={btc_up_eth['DOWN']}, NEUTRAL={btc_up_eth['NEUTRAL']}")
    btc_dn_eth_total = btc_down_eth["UP"] + btc_down_eth["DOWN"]
    if btc_dn_eth_total > 0:
        print(f"  -> BTC DOWN -> ETH goes AGAINST BTC: {btc_down_eth['UP']}/{btc_dn_eth_total} ({btc_down_eth['UP']/btc_dn_eth_total*100:.0f}%)")
    btc_up_eth_total = btc_up_eth["UP"] + btc_up_eth["DOWN"]
    if btc_up_eth_total > 0:
        print(f"  -> BTC UP -> ETH follows BTC: {btc_up_eth['UP']}/{btc_up_eth_total} ({btc_up_eth['UP']/btc_up_eth_total*100:.0f}%)")

    # Cross-coin: W4 lean vs actual BTC direction at that timestamp
    print(f"\n--- W4 Lean vs ACTUAL BTC Direction (per coin) ---")
    coin_vs_btc = defaultdict(lambda: {"with": 0, "against": 0, "na": 0})
    for w in windows:
        btc_dir = w.get("btc_direction")
        lean = w["lean_direction"]
        if lean == "NEUTRAL" or btc_dir is None:
            coin_vs_btc[w["coin"]]["na"] += 1
        elif lean == btc_dir:
            coin_vs_btc[w["coin"]]["with"] += 1
        else:
            coin_vs_btc[w["coin"]]["against"] += 1

    header4 = f"{'Coin':>5} | {'With BTC':>8} | {'Against BTC':>11} | {'N/A':>4} | {'% With':>7}"
    print(header4)
    print("-" * len(header4))
    for coin in sorted(coin_vs_btc.keys()):
        s = coin_vs_btc[coin]
        total = s["with"] + s["against"]
        pct = s["with"] / total * 100 if total > 0 else 0
        print(f"{coin:>5} | {s['with']:>8} | {s['against']:>11} | {s['na']:>4} | {pct:>6.0f}%")


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 4: SOL Contrarian Pattern Deep Dive
# ═══════════════════════════════════════════════════════════════════════
def analysis4_sol_contrarian(windows, btc_index, sol_index):
    print("\n" + "=" * 100)
    print("ANALYSIS 4: SOL CONTRARIAN PATTERN DEEP DIVE")
    print("=" * 100)

    sol_windows = [w for w in windows if w["coin"] == "SOL"]
    print(f"\nTotal SOL windows: {len(sol_windows)}")

    if not sol_windows:
        print("  No SOL windows found.")
        return

    # For each SOL window: W4 lean vs BTC direction vs SOL OWN direction
    print(f"\n--- SOL Windows: W4 Lean vs BTC Direction vs SOL Own Direction ---")
    header = f"{'Window':>20} | {'W4 Lean':>7} | {'BTC Dir':>7} | {'SOL Dir':>7} | {'W4=BTC?':>7} | {'W4=SOL?':>7} | {'BTC=SOL?':>8} | {'SOL cum%':>8} | {'BTC cum%':>8}"
    print(header)
    print("-" * len(header))

    w4_follows_btc = 0
    w4_follows_sol = 0
    w4_against_btc = 0
    w4_against_sol = 0
    btc_sol_same = 0
    btc_sol_diff = 0
    classified = 0

    for w in sol_windows:
        lean = w["lean_direction"]
        btc_dir = w.get("btc_direction")
        sol_dir = w.get("sol_direction")
        sol_cum = w.get("sol_cum_return")
        btc_cum = w.get("btc_cum_return")

        if lean == "NEUTRAL" or btc_dir is None or sol_dir is None:
            continue
        classified += 1

        w4_btc = "YES" if lean == btc_dir else "NO"
        w4_sol = "YES" if lean == sol_dir else "NO"
        btc_sol = "SAME" if btc_dir == sol_dir else "DIFF"

        if lean == btc_dir:
            w4_follows_btc += 1
        else:
            w4_against_btc += 1

        if lean == sol_dir:
            w4_follows_sol += 1
        else:
            w4_against_sol += 1

        if btc_dir == sol_dir:
            btc_sol_same += 1
        else:
            btc_sol_diff += 1

        dt = datetime.datetime.fromtimestamp(w["window_start"], tz=datetime.timezone.utc)
        sol_cum_str = f"{sol_cum*100:+.3f}%" if sol_cum is not None else "N/A"
        btc_cum_str = f"{btc_cum*100:+.3f}%" if btc_cum is not None else "N/A"
        print(f"{dt.strftime('%m-%d %H:%M'):>20} | {lean:>7} | {btc_dir:>7} | {sol_dir:>7} | {w4_btc:>7} | {w4_sol:>7} | {btc_sol:>8} | {sol_cum_str:>8} | {btc_cum_str:>8}")

    print(f"\n--- SUMMARY ({classified} classified SOL windows) ---")
    if classified > 0:
        print(f"  W4 follows BTC direction: {w4_follows_btc}/{classified} ({w4_follows_btc/classified*100:.0f}%)")
        print(f"  W4 against BTC direction: {w4_against_btc}/{classified} ({w4_against_btc/classified*100:.0f}%)")
        print(f"  W4 follows SOL OWN direction: {w4_follows_sol}/{classified} ({w4_follows_sol/classified*100:.0f}%)")
        print(f"  W4 against SOL OWN direction: {w4_against_sol}/{classified} ({w4_against_sol/classified*100:.0f}%)")
        print(f"  BTC and SOL move same direction: {btc_sol_same}/{classified} ({btc_sol_same/classified*100:.0f}%)")
        print(f"  BTC and SOL diverge: {btc_sol_diff}/{classified} ({btc_sol_diff/classified*100:.0f}%)")

    # Check T+120s outcome for SOL
    print(f"\n--- SOL T+120s Outcome vs BTC ---")
    sol_t120_follows_btc = 0
    sol_t120_diverges = 0
    sol_t120_total = 0

    for w in sol_windows:
        ws = w["window_start"]
        t120 = ws + 120

        btc_open_k = get_kline_at(btc_index, ws)
        btc_120_k = get_kline_at(btc_index, t120)
        sol_open_k = get_kline_at(sol_index, ws)
        sol_120_k = get_kline_at(sol_index, t120)

        if not all([btc_open_k, btc_120_k, sol_open_k, sol_120_k]):
            continue

        btc_ret = math.log(btc_120_k["close"] / btc_open_k["open"])
        sol_ret = math.log(sol_120_k["close"] / sol_open_k["open"])

        btc_dir_120 = "UP" if btc_ret > 0 else "DOWN"
        sol_dir_120 = "UP" if sol_ret > 0 else "DOWN"

        sol_t120_total += 1
        if btc_dir_120 == sol_dir_120:
            sol_t120_follows_btc += 1
        else:
            sol_t120_diverges += 1

    if sol_t120_total > 0:
        print(f"  SOL follows BTC at T+120s: {sol_t120_follows_btc}/{sol_t120_total} ({sol_t120_follows_btc/sol_t120_total*100:.0f}%)")
        print(f"  SOL diverges from BTC at T+120s: {sol_t120_diverges}/{sol_t120_total} ({sol_t120_diverges/sol_t120_total*100:.0f}%)")

    # HYPOTHESIS: Is W4 looking at SOL's OWN momentum?
    # Compare: (a) W4 lean vs BTC 5m momentum (b) W4 lean vs SOL momentum at entry
    print(f"\n--- HYPOTHESIS: Does W4 follow SOL's OWN momentum (not BTC)? ---")

    sol_own_mom_with = 0
    sol_own_mom_against = 0
    btc_mom_with = 0
    btc_mom_against = 0

    for w in sol_windows:
        lean = w["lean_direction"]
        if lean == "NEUTRAL":
            continue

        # SOL's own momentum (from cumulative return)
        sol_cum = w.get("sol_cum_return")
        if sol_cum is not None:
            sol_mom_dir = "UP" if sol_cum > 0 else "DOWN"
            if lean == sol_mom_dir:
                sol_own_mom_with += 1
            else:
                sol_own_mom_against += 1

        # BTC momentum
        btc_cum = w.get("btc_cum_return")
        if btc_cum is not None:
            btc_mom_dir = "UP" if btc_cum > 0 else "DOWN"
            if lean == btc_mom_dir:
                btc_mom_with += 1
            else:
                btc_mom_against += 1

    sol_total = sol_own_mom_with + sol_own_mom_against
    btc_total = btc_mom_with + btc_mom_against
    if sol_total > 0:
        print(f"  W4 SOL lean FOLLOWS SOL's own momentum: {sol_own_mom_with}/{sol_total} ({sol_own_mom_with/sol_total*100:.0f}%)")
    if btc_total > 0:
        print(f"  W4 SOL lean FOLLOWS BTC momentum:       {btc_mom_with}/{btc_total} ({btc_mom_with/btc_total*100:.0f}%)")
    if sol_total > 0 and btc_total > 0:
        sol_pct = sol_own_mom_with / sol_total * 100
        btc_pct = btc_mom_with / btc_total * 100
        if sol_pct > btc_pct:
            print(f"  >>> HYPOTHESIS CONFIRMED: W4 follows SOL's OWN momentum ({sol_pct:.0f}%) more than BTC ({btc_pct:.0f}%)")
        else:
            print(f"  >>> HYPOTHESIS REJECTED: W4 follows BTC momentum ({btc_pct:.0f}%) more than SOL own ({sol_pct:.0f}%)")


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 5: Entry Timing Per Coin
# ═══════════════════════════════════════════════════════════════════════
def analysis5_entry_timing(windows):
    print("\n" + "=" * 100)
    print("ANALYSIS 5: ENTRY TIMING PER COIN")
    print("=" * 100)

    coins = sorted(set(w["coin"] for w in windows))
    tfs = sorted(set(w["duration_min"] for w in windows))
    tf_labels = {5: "5M", 15: "15M", 60: "1H"}

    # Per coin: entry offset from window open
    print(f"\n--- Average Entry Offset from Window Open (seconds) ---")
    header = f"{'Coin':>5} | {'TF':>5} | {'Windows':>7} | {'Avg Offset':>10} | {'Median':>7} | {'Min':>5} | {'Max':>5} | {'Avg Fills':>9} | {'Avg USDC/Fill':>13}"
    print(header)
    print("-" * len(header))

    for coin in coins:
        for tf in tfs:
            cw = [w for w in windows if w["coin"] == coin and w["duration_min"] == tf]
            if not cw:
                continue
            offsets = [w["first_trade_ts"] - w["window_start"] for w in cw]
            offsets_sorted = sorted(offsets)
            avg_off = sum(offsets) / len(offsets)
            median_off = offsets_sorted[len(offsets_sorted) // 2]
            min_off = min(offsets)
            max_off = max(offsets)
            avg_fills = sum(w["trade_count"] for w in cw) / len(cw)
            avg_usdc_per_fill = sum(w["total_usdc"] for w in cw) / sum(w["trade_count"] for w in cw) if sum(w["trade_count"] for w in cw) > 0 else 0
            label = tf_labels.get(tf, f"{tf}m")
            print(f"{coin:>5} | {label:>5} | {len(cw):>7} | {avg_off:>9.0f}s | {median_off:>6.0f}s | {min_off:>4.0f}s | {max_off:>4.0f}s | {avg_fills:>9.1f} | ${avg_usdc_per_fill:>12.3f}")

    # Is there a consistent order? (BTC first, then ETH, etc.)
    print(f"\n--- Entry Order Within Concurrent Windows ---")

    # Group by (window_start, duration)
    concurrent = defaultdict(dict)
    for w in windows:
        key = (w["window_start"], w["duration_min"])
        concurrent[key][w["coin"]] = w

    multi = {k: v for k, v in concurrent.items() if len(v) >= 2}

    # For each multi-coin window, rank coins by first_trade_ts
    first_coin_counts = defaultdict(int)
    order_patterns = defaultdict(int)

    for key, coin_dict in multi.items():
        ranked = sorted(coin_dict.items(), key=lambda x: x[1]["first_trade_ts"])
        first_coin_counts[ranked[0][0]] += 1
        pattern = " -> ".join(c for c, _ in ranked)
        order_patterns[pattern] += 1

    print(f"\n  Which coin trades FIRST in multi-coin windows?")
    for coin, count in sorted(first_coin_counts.items(), key=lambda x: -x[1]):
        total = sum(first_coin_counts.values())
        print(f"    {coin}: {count}/{total} ({count/total*100:.0f}%)")

    print(f"\n  Most common order patterns:")
    for pattern, count in sorted(order_patterns.items(), key=lambda x: -x[1])[:10]:
        print(f"    {pattern}: {count}")

    # Time gap between first and last coin
    print(f"\n--- Time Spread Within Concurrent Windows ---")
    spreads = []
    for key, coin_dict in multi.items():
        first_ts = min(w["first_trade_ts"] for w in coin_dict.values())
        last_ts = max(w["first_trade_ts"] for w in coin_dict.values())
        spread = last_ts - first_ts
        spreads.append(spread)

    if spreads:
        spreads_sorted = sorted(spreads)
        print(f"  Mean time spread: {sum(spreads)/len(spreads):.1f}s")
        print(f"  Median: {spreads_sorted[len(spreads_sorted)//2]:.0f}s")
        print(f"  Min: {min(spreads):.0f}s | Max: {max(spreads):.0f}s")
        print(f"  >10s spread: {sum(1 for s in spreads if s > 10)}/{len(spreads)} ({sum(1 for s in spreads if s > 10)/len(spreads)*100:.0f}%)")

    # Fill size by coin
    print(f"\n--- Average Fill Size (USDC) Per Coin ---")
    for coin in coins:
        cw = [w for w in windows if w["coin"] == coin]
        total_usdc = sum(w["total_usdc"] for w in cw)
        total_fills = sum(w["trade_count"] for w in cw)
        avg_fill = total_usdc / total_fills if total_fills > 0 else 0
        avg_per_window = total_usdc / len(cw) if cw else 0
        print(f"  {coin}: avg ${avg_fill:.3f}/fill, avg ${avg_per_window:.2f}/window, {total_fills} total fills")


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS 6: Price Pattern Per Coin (Momentum Persistence)
# ═══════════════════════════════════════════════════════════════════════
def analysis6_price_patterns(btc_klines, sol_klines, xrp_klines, btc_index, sol_index, xrp_index):
    print("\n" + "=" * 100)
    print("ANALYSIS 6: PRICE PATTERN PER COIN — MOMENTUM PERSISTENCE")
    print("=" * 100)

    coin_data = [
        ("BTC", btc_klines, btc_index),
        ("SOL", sol_klines, sol_index),
        ("XRP", xrp_klines, xrp_index),
    ]

    # For each coin, compute: at T+120s (2min), T+180s (3min), T+300s (5min)
    # If 5M momentum is positive, what % of time is T+delay also positive?
    # This measures momentum persistence

    delays = [60, 120, 180, 240, 300]
    lookbacks = [300]  # 5m lookback for momentum

    print(f"\n--- 5M Momentum Persistence: if mom(T-5min,T) > 0, what % of time is price higher at T+delay? ---")

    header = f"{'Coin':>5} | {'Delay':>7} | {'Windows':>7} | {'Persist%':>8} | {'Reverse%':>8} | {'Avg ret if persist':>18} | {'Avg ret if reverse':>18}"
    print(header)
    print("-" * len(header))

    for coin_name, klines, kindex in coin_data:
        # Get all timestamps
        all_ts = sorted(kindex.keys())
        if len(all_ts) < 400:
            print(f"  {coin_name}: insufficient data ({len(all_ts)} candles)")
            continue

        for delay in delays:
            persist = 0
            reverse = 0
            persist_rets = []
            reverse_rets = []

            for ts in all_ts:
                ts_5ago = ts - 300
                ts_delay = ts + delay

                if ts_5ago not in kindex or ts_delay not in kindex:
                    continue

                mom_5m = math.log(kindex[ts]["close"] / kindex[ts_5ago]["close"])
                future_ret = math.log(kindex[ts_delay]["close"] / kindex[ts]["close"])

                if abs(mom_5m) < 1e-8:  # Skip near-zero momentum
                    continue

                if (mom_5m > 0 and future_ret > 0) or (mom_5m < 0 and future_ret < 0):
                    persist += 1
                    persist_rets.append(abs(future_ret))
                else:
                    reverse += 1
                    reverse_rets.append(abs(future_ret))

            total = persist + reverse
            if total == 0:
                continue
            pct_persist = persist / total * 100
            pct_reverse = reverse / total * 100
            avg_persist = sum(persist_rets) / len(persist_rets) * 100 if persist_rets else 0
            avg_reverse = sum(reverse_rets) / len(reverse_rets) * 100 if reverse_rets else 0

            print(f"{coin_name:>5} | {delay:>5}s | {total:>7} | {pct_persist:>7.1f}% | {pct_reverse:>7.1f}% | {avg_persist:>17.4f}% | {avg_reverse:>17.4f}%")

    # Should we use same parameters for all coins?
    print(f"\n--- Momentum Persistence Comparison at T+120s ---")
    comparison = {}
    for coin_name, klines, kindex in coin_data:
        all_ts = sorted(kindex.keys())
        persist = 0
        reverse = 0
        for ts in all_ts:
            ts_5ago = ts - 300
            ts_delay = ts + 120
            if ts_5ago not in kindex or ts_delay not in kindex:
                continue
            mom_5m = math.log(kindex[ts]["close"] / kindex[ts_5ago]["close"])
            future_ret = math.log(kindex[ts_delay]["close"] / kindex[ts]["close"])
            if abs(mom_5m) < 1e-8:
                continue
            if (mom_5m > 0 and future_ret > 0) or (mom_5m < 0 and future_ret < 0):
                persist += 1
            else:
                reverse += 1
        total = persist + reverse
        comparison[coin_name] = persist / total * 100 if total > 0 else 0

    print(f"\n  Persistence at T+120s:")
    for coin, pct in sorted(comparison.items()):
        print(f"    {coin}: {pct:.1f}%")

    if comparison:
        spread = max(comparison.values()) - min(comparison.values())
        print(f"\n  Spread across coins: {spread:.1f}pp")
        if spread > 3:
            print(f"  >>> SIGNIFICANT difference — should NOT use same parameters for all coins")
        else:
            print(f"  >>> Similar persistence — CAN use same parameters for all coins")

    # Detailed: momentum magnitude buckets
    print(f"\n--- Momentum Persistence by Magnitude Bucket (at T+120s) ---")
    buckets = [(0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.0)]
    bucket_labels = ["0-2bps", "2-5bps", "5-10bps", "10-20bps", "20+bps"]

    header2 = f"{'Coin':>5} | {'Bucket':>8} | {'N':>6} | {'Persist%':>8} | {'AvgMom%':>8}"
    print(header2)
    print("-" * len(header2))

    for coin_name, klines, kindex in coin_data:
        all_ts = sorted(kindex.keys())
        for bi, (lo, hi) in enumerate(buckets):
            persist = 0
            reverse = 0
            moms = []
            for ts in all_ts:
                ts_5ago = ts - 300
                ts_delay = ts + 120
                if ts_5ago not in kindex or ts_delay not in kindex:
                    continue
                mom_5m = kindex[ts]["close"] / kindex[ts_5ago]["close"] - 1.0
                mom_abs = abs(mom_5m) * 100  # to percent
                if mom_abs < lo or mom_abs >= hi:
                    continue
                future_ret = kindex[ts_delay]["close"] / kindex[ts]["close"] - 1.0
                moms.append(mom_abs)
                if (mom_5m > 0 and future_ret > 0) or (mom_5m < 0 and future_ret < 0):
                    persist += 1
                else:
                    reverse += 1
            total = persist + reverse
            if total < 10:
                continue
            pct = persist / total * 100
            avg_mom = sum(moms) / len(moms) if moms else 0
            print(f"{coin_name:>5} | {bucket_labels[bi]:>8} | {total:>6} | {pct:>7.1f}% | {avg_mom:>7.3f}%")

    # BTC-SOL correlation on actual 1M returns
    print(f"\n--- BTC vs SOL 1M Return Correlation ---")
    btc_ts = set(btc_index.keys())
    sol_ts = set(sol_index.keys())
    common_ts = sorted(btc_ts & sol_ts)

    btc_rets = []
    sol_rets = []
    same_dir = 0
    diff_dir = 0

    for ts in common_ts:
        prev = ts - 60
        if prev not in btc_index or prev not in sol_index:
            continue
        br = math.log(btc_index[ts]["close"] / btc_index[prev]["close"])
        sr = math.log(sol_index[ts]["close"] / sol_index[prev]["close"])
        btc_rets.append(br)
        sol_rets.append(sr)
        if abs(br) < 1e-8 or abs(sr) < 1e-8:
            continue
        if (br > 0 and sr > 0) or (br < 0 and sr < 0):
            same_dir += 1
        else:
            diff_dir += 1

    total = same_dir + diff_dir
    if total > 0:
        print(f"  BTC and SOL move same direction (1M): {same_dir}/{total} ({same_dir/total*100:.0f}%)")
        print(f"  BTC and SOL diverge (1M): {diff_dir}/{total} ({diff_dir/total*100:.0f}%)")

    # Pearson correlation
    if len(btc_rets) > 10:
        n = len(btc_rets)
        mean_b = sum(btc_rets) / n
        mean_s = sum(sol_rets) / n
        cov = sum((b - mean_b) * (s - mean_s) for b, s in zip(btc_rets, sol_rets)) / n
        std_b = (sum((b - mean_b) ** 2 for b in btc_rets) / n) ** 0.5
        std_s = (sum((s - mean_s) ** 2 for s in sol_rets) / n) ** 0.5
        corr = cov / (std_b * std_s) if std_b > 0 and std_s > 0 else 0
        print(f"  Pearson correlation (1M returns): {corr:.4f}")

    # BTC vs XRP
    print(f"\n--- BTC vs XRP 1M Return Correlation ---")
    xrp_ts = set(xrp_index.keys())
    common_bx = sorted(btc_ts & xrp_ts)

    btc_rets2 = []
    xrp_rets2 = []
    same2 = 0
    diff2 = 0

    for ts in common_bx:
        prev = ts - 60
        if prev not in btc_index or prev not in xrp_index:
            continue
        br = math.log(btc_index[ts]["close"] / btc_index[prev]["close"])
        xr = math.log(xrp_index[ts]["close"] / xrp_index[prev]["close"])
        btc_rets2.append(br)
        xrp_rets2.append(xr)
        if abs(br) < 1e-8 or abs(xr) < 1e-8:
            continue
        if (br > 0 and xr > 0) or (br < 0 and xr < 0):
            same2 += 1
        else:
            diff2 += 1

    total2 = same2 + diff2
    if total2 > 0:
        print(f"  BTC and XRP move same direction (1M): {same2}/{total2} ({same2/total2*100:.0f}%)")

    if len(btc_rets2) > 10:
        n = len(btc_rets2)
        mean_b = sum(btc_rets2) / n
        mean_x = sum(xrp_rets2) / n
        cov = sum((b - mean_b) * (x - mean_x) for b, x in zip(btc_rets2, xrp_rets2)) / n
        std_b = (sum((b - mean_b) ** 2 for b in btc_rets2) / n) ** 0.5
        std_x = (sum((x - mean_x) ** 2 for x in xrp_rets2) / n) ** 0.5
        corr = cov / (std_b * std_x) if std_b > 0 and std_x > 0 else 0
        print(f"  Pearson correlation (1M returns): {corr:.4f}")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    print("Loading data...")
    raw_trades = load_json(W4_FILE)
    btc_klines = load_json(BTC_FILE)
    sol_klines = load_json(SOL_FILE)
    xrp_klines = load_json(XRP_FILE)

    print(f"  W4 trades: {len(raw_trades)}")
    print(f"  BTC 1M candles: {len(btc_klines)}")
    print(f"  SOL 1M candles: {len(sol_klines)}")
    print(f"  XRP 1M candles: {len(xrp_klines)}")

    btc_index = build_kline_index(btc_klines)
    sol_index = build_kline_index(sol_klines)
    xrp_index = build_kline_index(xrp_klines)

    # Parse all W4 trades into windows
    print("\nParsing W4 trades...")
    windows = parse_all_windows(raw_trades)
    print(f"  Total windows: {len(windows)}")
    coins = set(w["coin"] for w in windows)
    print(f"  Coins: {sorted(coins)}")
    durations = set(w["duration_min"] for w in windows)
    print(f"  Timeframes: {sorted(durations)}")

    # Enrich with price context
    print("Enriching with BTC context...")
    windows = enrich_btc_context(windows, btc_index)
    print("Enriching with SOL/XRP context...")
    windows = enrich_altcoin_context(windows, sol_index, xrp_index)

    # Run all analyses
    coin_stats = analysis1_per_coin(windows)
    tf_stats = analysis2_per_timeframe(windows)
    analysis3_cross_coin_correlation(windows)
    analysis4_sol_contrarian(windows, btc_index, sol_index)
    analysis5_entry_timing(windows)
    analysis6_price_patterns(btc_klines, sol_klines, xrp_klines, btc_index, sol_index, xrp_index)

    # Final summary
    print("\n" + "=" * 100)
    print("FINAL SUMMARY — KEY FINDINGS")
    print("=" * 100)

    # Compute key metrics for summary
    for coin in sorted(coins):
        cw = [w for w in windows if w["coin"] == coin]
        total = len(cw)
        with_btc = sum(1 for w in cw if w["lean_direction"] != "NEUTRAL" and w.get("btc_direction") is not None and w["lean_direction"] == w.get("btc_direction"))
        against_btc = sum(1 for w in cw if w["lean_direction"] != "NEUTRAL" and w.get("btc_direction") is not None and w["lean_direction"] != w.get("btc_direction"))
        classified = with_btc + against_btc
        pct_with = with_btc / classified * 100 if classified > 0 else 0
        usdc = sum(w["total_usdc"] for w in cw)
        avg_per_win = usdc / total if total > 0 else 0
        print(f"\n  {coin}: {total} windows, ${usdc:.0f} total, ${avg_per_win:.2f}/window, {pct_with:.0f}% follows BTC ({classified} classified)")

    print("\n")


if __name__ == "__main__":
    main()
