#!/usr/bin/env python3
"""
compare_configs.py — A/B test different strategy parameter combinations.

Runs 180d backtest on BTC/ETH/XRP with multiple configs, outputs comparison table.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES

DAYS = 180
PAIRS = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT",
    "SOLUSDT", "DOGEUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT",
]

# ─── Configs to test ───
# param_overrides: passed directly to BacktestEngine (no monkey-patching)
CONFIGS = {
    "A_baseline": {
        "desc": "Current production (BB_WIDTH_MIN=0.05, XRP all modes)",
        "param_overrides": {},
        "xrp_modes": None,
    },
    "B_xrp_range_only": {
        "desc": "XRP range-only (disable trend)",
        "param_overrides": {},
        "xrp_modes": ["RANGE"],
    },
    "C_relaxed_range": {
        "desc": "BB_WIDTH=0.07, bb_touch_tol=0.008, adx_max=25",
        "param_overrides": {
            "bb_width_min": 0.07,
            "bb_touch_tol": 0.008,
            "adx_range_max": 25,
        },
        "xrp_modes": ["RANGE"],
    },
    "D_moderate": {
        "desc": "BB_WIDTH=0.06, bb_touch_tol=0.007, adx_max=22, XRP range-only",
        "param_overrides": {
            "bb_width_min": 0.06,
            "bb_touch_tol": 0.007,
            "adx_range_max": 22,
        },
        "xrp_modes": ["RANGE"],
    },
}


def fetch_data(days: int) -> dict:
    """Fetch and cache all pair data once."""
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    data = {}

    for pair in PAIRS:
        s1h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES)).timestamp() * 1000)
        s4h = int((now - timedelta(hours=days * 24 + WARMUP_CANDLES * 4)).timestamp() * 1000)

        df_1h = fetch_klines_range(pair, "1h", s1h, end_ms)
        df_4h = fetch_klines_range(pair, "4h", s4h, end_ms)
        data[pair] = (df_1h, df_4h)

    return data


def run_config(config: dict, data: dict) -> dict:
    """Run one config across all pairs via param_overrides (no monkey-patching)."""
    results = {}
    for pair in PAIRS:
        df_1h, df_4h = data[pair]

        allowed_modes = None
        if pair == "XRPUSDT" and config.get("xrp_modes"):
            allowed_modes = config["xrp_modes"]

        engine = BacktestEngine(
            symbol=pair,
            df_1h=df_1h.copy(),
            df_4h=df_4h.copy(),
            allowed_modes=allowed_modes,
            param_overrides=config.get("param_overrides", {}),
        )
        results[pair] = engine.run()

    return results


def print_comparison(all_results: dict):
    """Print comparison table."""
    print(f"\n{'=' * 100}")
    print(f"  BACKTEST COMPARISON — {DAYS}d × {len(PAIRS)} pairs")
    print(f"{'=' * 100}")

    # Header
    print(f"\n{'Config':<22} {'Pair':<8} {'Trades':>6} {'W/L':>7} {'WR%':>5} "
          f"{'AdjWR':>6} {'PnL':>10} {'PF':>6} {'MaxDD':>6} {'Range':>7} {'Trend':>7}")
    print("─" * 100)

    for cfg_name, cfg in CONFIGS.items():
        results = all_results[cfg_name]

        total_trades = 0
        total_pnl = 0
        total_wins = 0
        total_losses = 0
        total_indep = 0

        for pair in PAIRS:
            r = results[pair]
            t = r["total_trades"]
            w = r["winners"]
            l = r["losers"]
            pnl = r["final_balance"] - 10000
            pf = r["profit_factor"]
            dd = r["max_drawdown_pct"]
            adj_wr = r.get("cluster_adj_wr", 0.0)

            range_t = [x for x in r["trades"] if x.strategy == "range"]
            trend_t = [x for x in r["trades"] if x.strategy == "trend"]
            rw = sum(1 for x in range_t if x.pnl > 0)
            tw = sum(1 for x in trend_t if x.pnl > 0)

            range_str = f"{rw}W/{len(range_t)-rw}L" if range_t else "—"
            trend_str = f"{tw}W/{len(trend_t)-tw}L" if trend_t else "—"
            pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) and pf != float("inf") else str(pf)
            wr = f"{r['win_rate']:.0f}" if t > 0 else "—"
            adj_str = f"{adj_wr:.0f}" if t > 0 else "—"

            label = cfg_name if pair == PAIRS[0] else ""
            print(f"  {label:<20} {pair:<8} {t:>6} {w}W/{l}L  {wr:>4} "
                  f"{adj_str:>5} ${pnl:>+9.0f} {pf_str:>6} {dd:>5.1f}% {range_str:>7} {trend_str:>7}")

            total_trades += t
            total_pnl += pnl
            total_wins += w
            total_losses += l
            total_indep += r.get("independent_decisions", t)

        # Subtotal
        wr_total = f"{total_wins/total_trades*100:.0f}" if total_trades > 0 else "—"
        print(f"  {'':20} {'TOTAL':<8} {total_trades:>6} "
              f"{total_wins}W/{total_losses}L {wr_total:>4} "
              f"{'':>5} ${total_pnl:>+9.0f}  (indep={total_indep})")
        print("─" * 100)

    # Config descriptions
    print(f"\nConfig descriptions:")
    for name, cfg in CONFIGS.items():
        print(f"  {name}: {cfg['desc']}")


def main():
    print(f"Fetching {DAYS}d data for {', '.join(PAIRS)}...")
    data = fetch_data(DAYS)

    all_results = {}
    for cfg_name, cfg in CONFIGS.items():
        print(f"\n--- Running config: {cfg_name} ---")
        all_results[cfg_name] = run_config(cfg, data)

    print_comparison(all_results)


if __name__ == "__main__":
    main()
