#!/usr/bin/env python3
"""
param_shootout.py — 多套參數 A/B/C/D 對比，搵每個 symbol 最優配置。

用法: python3 backtest/param_shootout.py [--days 180]

目的: 用同一段數據跑多套參數，直接比較盈虧。
      如果所有配置都虧 → 公式有根本性問題。
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta

AXC_HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
sys.path.insert(0, AXC_HOME)
sys.path.insert(0, os.path.join(AXC_HOME, "scripts"))

from backtest.fetch_historical import fetch_klines_range
from backtest.engine import BacktestEngine, WARMUP_CANDLES

_DAYS = 180
PAIRS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

# ═══════════════════════════════════════════════════════════════
# 參數配置：每套 config 包含 tuning_params (引擎行為) + param_overrides (指標)
# ═══════════════════════════════════════════════════════════════

CONFIGS = {
    # ── A: 現有 production defaults ──
    "A_default": {
        "desc": "Current production defaults",
        "tuning_params": {},
        "param_overrides": {},
    },

    # ── B: Cross-symbol consensus（6/8 optimizer runs 共同方向）──
    # 方向：higher persistence, longer cooldown, lighter penalties
    "B_consensus": {
        "desc": "Cross-symbol optimizer consensus (median values)",
        "tuning_params": {
            "conf_gate_range": 0.55,
            "conf_gate_trend": 0.52,
            "conf_gate_crash": 0.55,
            "mode_pen_trend_in_range": -0.33,
            "mode_pen_trend_in_crash": -0.10,   # lighter (was -0.20)
            "mode_pen_range_in_trend": -0.24,
            "mode_pen_range_in_crash": -0.28,
            "mode_pen_default_trend": -0.15,     # lighter (was -0.25)
            "mode_pen_default_range": -0.12,
            "persist_range": 4,                  # up from 3
            "persist_trend": 6,                  # up from 4
            "persist_crash": 2,                  # up from 1
            "cooldown": 10,                      # up from 8
        },
        "param_overrides": {},
    },

    # ── C: ETH 720d WF-passed（唯一 OOS > IS 嘅配置）──
    # 特徵：低 trend gate, 重 range-in-trend penalty, 極長 cooldown
    "C_eth_wf": {
        "desc": "ETH 720d walk-forward passed (OOS > IS)",
        "tuning_params": {
            "conf_gate_range": 0.56,
            "conf_gate_trend": 0.44,             # below default!
            "conf_gate_crash": 0.57,
            "mode_pen_trend_in_range": -0.39,
            "mode_pen_trend_in_crash": -0.03,    # near zero
            "mode_pen_range_in_trend": -0.40,    # very heavy
            "mode_pen_range_in_crash": -0.37,
            "mode_pen_default_trend": -0.11,
            "mode_pen_default_range": -0.05,
            "persist_range": 6,
            "persist_trend": 7,
            "persist_crash": 1,
            "cooldown": 15,
        },
        "param_overrides": {},
    },

    # ── D: XRP 180d WF-passed ──
    # 特徵：極低 gate, 極快 trend persist, 長 cooldown
    "D_xrp_wf": {
        "desc": "XRP 180d walk-forward passed",
        "tuning_params": {
            "conf_gate_range": 0.40,
            "conf_gate_trend": 0.48,
            "conf_gate_crash": 0.33,             # very low
            "mode_pen_trend_in_range": -0.42,
            "mode_pen_trend_in_crash": -0.08,
            "mode_pen_range_in_trend": -0.23,
            "mode_pen_range_in_crash": -0.30,
            "mode_pen_default_trend": -0.18,
            "mode_pen_default_range": -0.20,
            "persist_range": 3,
            "persist_trend": 1,                  # very fast
            "persist_crash": 2,
            "cooldown": 12,
        },
        "param_overrides": {},
    },

    # ── E: SL tight range（grid search winner）──
    "E_tight_sl": {
        "desc": "SL ATR range=0.8 (grid search winner) + consensus tuning",
        "tuning_params": {
            "conf_gate_range": 0.55,
            "conf_gate_trend": 0.52,
            "conf_gate_crash": 0.55,
            "mode_pen_trend_in_crash": -0.10,
            "mode_pen_default_trend": -0.15,
            "persist_range": 4,
            "persist_trend": 6,
            "persist_crash": 2,
            "cooldown": 10,
        },
        "param_overrides": {
            "sl_atr_mult_range": 0.8,
        },
    },
}

# ═══════════════════════════════════════════════════════════════
# Per-symbol best（每個 symbol 用唔同嘅參數）
# ═══════════════════════════════════════════════════════════════

PER_SYMBOL_TUNING = {
    # BTC: 360d optimizer direction (higher gates, longer persist/cooldown)
    "BTCUSDT": {
        "conf_gate_range": 0.59,
        "conf_gate_trend": 0.57,
        "conf_gate_crash": 0.55,
        "mode_pen_trend_in_range": -0.27,
        "mode_pen_trend_in_crash": -0.10,
        "mode_pen_range_in_trend": -0.31,
        "mode_pen_range_in_crash": -0.24,
        "mode_pen_default_trend": -0.14,
        "mode_pen_default_range": -0.12,
        "persist_range": 4,
        "persist_trend": 6,
        "persist_crash": 2,
        "cooldown": 10,
    },
    # ETH: 720d WF-passed
    "ETHUSDT": {
        "conf_gate_range": 0.56,
        "conf_gate_trend": 0.44,
        "conf_gate_crash": 0.57,
        "mode_pen_trend_in_range": -0.39,
        "mode_pen_trend_in_crash": -0.03,
        "mode_pen_range_in_trend": -0.40,
        "mode_pen_range_in_crash": -0.37,
        "mode_pen_default_trend": -0.11,
        "mode_pen_default_range": -0.05,
        "persist_range": 6,
        "persist_trend": 7,
        "persist_crash": 1,
        "cooldown": 15,
    },
    # XRP: 180d WF-passed
    "XRPUSDT": {
        "conf_gate_range": 0.40,
        "conf_gate_trend": 0.48,
        "conf_gate_crash": 0.33,
        "mode_pen_trend_in_range": -0.42,
        "mode_pen_trend_in_crash": -0.08,
        "mode_pen_range_in_trend": -0.23,
        "mode_pen_range_in_crash": -0.30,
        "mode_pen_default_trend": -0.18,
        "mode_pen_default_range": -0.20,
        "persist_range": 3,
        "persist_trend": 1,
        "persist_crash": 2,
        "cooldown": 12,
    },
    # SOL: consensus (冇 optimizer data)
    "SOLUSDT": {
        "conf_gate_range": 0.55,
        "conf_gate_trend": 0.52,
        "conf_gate_crash": 0.55,
        "mode_pen_trend_in_crash": -0.10,
        "mode_pen_default_trend": -0.15,
        "persist_range": 4,
        "persist_trend": 6,
        "persist_crash": 2,
        "cooldown": 10,
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

        print(f"  Fetching {pair}...", end=" ", flush=True)
        df_1h = fetch_klines_range(pair, "1h", s1h, end_ms)
        df_4h = fetch_klines_range(pair, "4h", s4h, end_ms)
        data[pair] = (df_1h, df_4h)
        print(f"1h={len(df_1h)}, 4h={len(df_4h)} candles")

    return data


def run_single(pair: str, data: dict, tuning_params: dict,
               param_overrides: dict) -> dict:
    """Run one backtest for a single pair with given params."""
    df_1h, df_4h = data[pair]
    engine = BacktestEngine(
        symbol=pair,
        df_1h=df_1h.copy(),
        df_4h=df_4h.copy(),
        tuning_params=tuning_params or None,
        param_overrides=param_overrides or None,
        quiet=True,
    )
    return engine.run()


def run_config(cfg: dict, data: dict) -> dict:
    """Run one config across all pairs."""
    results = {}
    tp = cfg.get("tuning_params", {})
    po = cfg.get("param_overrides", {})
    for pair in PAIRS:
        results[pair] = run_single(pair, data, tp, po)
    return results


def run_per_symbol(data: dict) -> dict:
    """Run per-symbol best config (each pair uses its own optimal params)."""
    results = {}
    for pair in PAIRS:
        tp = PER_SYMBOL_TUNING.get(pair, {})
        results[pair] = run_single(pair, data, tp, {})
    return results


def extract_stats(result: dict) -> dict:
    """Extract key stats from a backtest result."""
    trades = result.get("trades", [])
    pnl = result["final_balance"] - 10000
    t = result["total_trades"]

    by_strat = {}
    for s in ["range", "trend", "crash"]:
        st = [x for x in trades if x.strategy == s]
        sw = sum(1 for x in st if x.pnl > 0)
        sp = sum(x.pnl for x in st)
        by_strat[s] = {"n": len(st), "w": sw, "pnl": sp}

    return {
        "trades": t,
        "wins": result["winners"],
        "losses": result["losers"],
        "pnl": pnl,
        "wr": result["win_rate"] if t > 0 else 0,
        "pf": result["profit_factor"],
        "max_dd": result["max_drawdown_pct"],
        "sharpe": result.get("sharpe_ratio", 0),
        "by_strat": by_strat,
    }


def print_results(all_results: dict, days: int = 180):
    """Print comparison table."""
    print(f"\n{'=' * 110}")
    print(f"  PARAMETER SHOOTOUT — {days}d × {len(PAIRS)} pairs")
    print(f"{'=' * 110}")

    print(f"\n{'Config':<16} {'Pair':<9} {'Trades':>6} {'W/L':>8} {'WR%':>5} "
          f"{'PnL':>10} {'PF':>6} {'MaxDD':>6} {'Sharpe':>7} "
          f"{'range':>9} {'trend':>9} {'crash':>9}")
    print("─" * 110)

    config_totals = {}

    for cfg_name in all_results:
        results = all_results[cfg_name]
        total_pnl = 0
        total_trades = 0
        total_wins = 0
        total_losses = 0

        for pair in PAIRS:
            s = extract_stats(results[pair])
            total_pnl += s["pnl"]
            total_trades += s["trades"]
            total_wins += s["wins"]
            total_losses += s["losses"]

            pf_str = f"{s['pf']:.2f}" if isinstance(s['pf'], (int, float)) and s['pf'] != float('inf') else "∞"
            label = cfg_name if pair == PAIRS[0] else ""

            # Strategy breakdown
            strat_strs = []
            for st in ["range", "trend", "crash"]:
                bs = s["by_strat"][st]
                if bs["n"] > 0:
                    strat_strs.append(f"${bs['pnl']:+.0f}({bs['n']})")
                else:
                    strat_strs.append("—")

            print(f"  {label:<14} {pair:<9} {s['trades']:>6} "
                  f"{s['wins']}W/{s['losses']}L {s['wr']:>4.0f} "
                  f"${s['pnl']:>+9.0f} {pf_str:>6} {s['max_dd']:>5.1f}% {s['sharpe']:>6.2f} "
                  f"{strat_strs[0]:>9} {strat_strs[1]:>9} {strat_strs[2]:>9}")

        wr_total = total_wins / total_trades * 100 if total_trades > 0 else 0
        config_totals[cfg_name] = total_pnl
        print(f"  {'':14} {'TOTAL':<9} {total_trades:>6} "
              f"{total_wins}W/{total_losses}L {wr_total:>4.0f} "
              f"${total_pnl:>+9.0f}")
        print("─" * 110)

    # Summary ranking
    print(f"\n{'=' * 60}")
    print(f"  RANKING")
    print(f"{'=' * 60}")
    ranked = sorted(config_totals.items(), key=lambda x: x[1], reverse=True)
    for i, (name, pnl) in enumerate(ranked, 1):
        cfg = CONFIGS.get(name, {"desc": "Per-symbol best params"})
        desc = cfg.get("desc", "Per-symbol optimal")
        marker = " ← BEST" if i == 1 else ""
        print(f"  #{i}  ${pnl:>+9.0f}  {name:<16} {desc}{marker}")

    # Verdict
    print(f"\n{'=' * 60}")
    best_name, best_pnl = ranked[0]
    worst_name, worst_pnl = ranked[-1]
    if best_pnl <= 0:
        print("  ⚠️  ALL CONFIGS NEGATIVE — 公式可能有根本性問題")
        print("     需要重新檢視：信號邏輯、entry timing、或 market regime 判斷")
    elif best_pnl > 0 and worst_pnl <= 0:
        print(f"  ✓  最優配置 {best_name} 盈利 ${best_pnl:+.0f}")
        print(f"     公式邏輯 OK，但參數敏感度高 — 需要 robust 配置")
    else:
        print(f"  ✓  全部配置盈利！最優 {best_name} ${best_pnl:+.0f}")
        print(f"     公式邏輯 solid，參數調整空間大")
    print(f"{'=' * 60}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Parameter shootout — multi-config comparison")
    parser.add_argument("--days", type=int, default=_DAYS, help=f"Backtest period (default {_DAYS})")
    args = parser.parse_args()

    days = args.days

    print(f"\n📊 Parameter Shootout — {days}d × {len(PAIRS)} pairs × {len(CONFIGS) + 1} configs\n")
    print("Fetching data (one-time)...")
    data = fetch_data(days)

    all_results = {}

    # Run uniform configs
    for cfg_name, cfg in CONFIGS.items():
        print(f"\n  Running {cfg_name}: {cfg['desc']}...")
        all_results[cfg_name] = run_config(cfg, data)

    # Run per-symbol best
    print(f"\n  Running F_per_symbol: Per-symbol optimal params...")
    all_results["F_per_symbol"] = run_per_symbol(data)

    print_results(all_results, days)

    # Save raw results
    out_path = os.path.join(AXC_HOME, "backtest", "data",
                            f"shootout_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    save_data = {}
    for cfg_name, results in all_results.items():
        save_data[cfg_name] = {}
        if cfg_name == "F_per_symbol":
            save_data[cfg_name]["_meta"] = {
                "desc": "Per-symbol optimal params",
                "tuning_params": PER_SYMBOL_TUNING,
                "param_overrides": {},
            }
        else:
            save_data[cfg_name]["_meta"] = {
                "desc": CONFIGS[cfg_name]["desc"],
                "tuning_params": CONFIGS[cfg_name].get("tuning_params", {}),
                "param_overrides": CONFIGS[cfg_name].get("param_overrides", {}),
            }
        for pair, r in results.items():
            save_data[cfg_name][pair] = extract_stats(r)
    with open(out_path, "w") as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"Results saved: {out_path}")


if __name__ == "__main__":
    main()
