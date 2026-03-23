"""
POL — Backtest failure。WR 18.2%, PF 0.43, MDD 31.4%。
保留 config 但建議 disable all strategies。
"""

COIN = {
    "symbol": "POLUSDT",
    "prefix": "POL",
    "group": "crypto_independent",
    "exchange": ["binance"],
    "vol_mult": 2.0,
    "max_leverage": 15,
    "price_precision": 7,
    "qty_precision": 0,
    "pair_priority": 1,        # Lowest

    # All strategies disabled — backtest-proven failure
    "strategies": {
        "range": {"enabled": False, "conf_gate": 0.40},
        "trend": {"enabled": False, "conf_gate": 0.48},
        "crash": {"enabled": False, "conf_gate": 0.33},
        "squeeze": {"enabled": False, "conf_gate": 0.40},
    },

    "notes": "DISABLED: Backtest 180d WR 18.2%, PF 0.43, -14.83%. 只有 11 trades",
}
