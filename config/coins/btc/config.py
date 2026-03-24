"""
BTC — Baseline coin。Primary regime anchor。
vol_mult = 1.0 (所有其他幣相對 BTC)。

BMD 分析 (2026-03-24):
  Range/Trend: PF 0.96, Kelly -0.015 → 負 EV，每單蝕。Disabled。
  Squeeze: 360日數據顯示 ~85 次 SQUEEZE/QUIET_THEN_BOOM per year。
  42.5% 嘅波動集中在 16.1% 嘅 candles — 必須捉爆發，唔係磨小利。
"""

COIN = {
    # === Identity ===
    "symbol": "BTCUSDT",
    "prefix": "BTC",
    "group": "crypto_correlated",
    "exchange": ["aster", "binance", "hyperliquid"],

    # === Sizing ===
    "vol_mult": 1.0,           # Baseline
    "max_leverage": 20,
    "price_precision": 1,
    "qty_precision": 3,

    # === Pipeline ===
    "pair_priority": 4,         # Highest priority
    "correlation_tracked": True,
    "liq_enabled": True,
    "regime_anchor": True,      # HMM / mode detection primary

    # === Indicator Overrides ===
    "indicator_params": {},

    # === Indicators ===
    # bb_width_pctl now enabled (via defaults) for squeeze detection

    # === Strategies ===
    # Range/Trend DISABLED — BMD: PF 0.96, Kelly -0.015, negative EV
    # Squeeze ENABLED — targeting explosive moves (42.5% of vol in 16.1% of candles)
    "strategies": {
        "range": {
            "enabled": False,      # DISABLED: backtest PF < 1.0
            "conf_gate": 0.40,
        },
        "trend": {
            "enabled": False,      # DISABLED: backtest PF < 1.0
            "conf_gate": 0.48,
        },
        "crash": {
            "enabled": False,      # DISABLED: PF=0.90, no edge at 20x leverage
            "conf_gate": 0.33,
        },
        "squeeze": {
            "enabled": True,       # NEW: squeeze-explosion strategy
            "conf_gate": 0.40,
        },
        "burst": {
            "enabled": True,       # Paper testing: volume spike + momentum
            "conf_gate": 0.30,
        },
    },

    "notes": "Squeeze+burst: range/trend disabled (BMD: negative EV). Crash kept for regime safety",
}
