"""
XAG — Backtest failure。WR 25%, PF 0.65, Sharpe -1.90。
max_leverage=10 → Zone A only (< Zone B 下限 11x)。
保留 config 但建議 disable all strategies。
"""

COIN = {
    "symbol": "XAGUSDT",
    "prefix": "XAG",
    "group": "commodity",
    "exchange": ["aster"],
    "vol_mult": 0.47,
    "max_leverage": 10,        # Zone A only (< Zone B minimum 11x)
    "price_precision": 2,
    "qty_precision": 3,
    "pair_priority": 1,

    # All strategies disabled — backtest-proven failure
    "strategies": {
        "range": {"enabled": False, "conf_gate": 0.40},
        "trend": {"enabled": False, "conf_gate": 0.48},
        "crash": {"enabled": False, "conf_gate": 0.33},
        "squeeze": {"enabled": False, "conf_gate": 0.40},
    },

    "notes": "DISABLED: Backtest 180d WR 25%, PF 0.65, -6.65%. Sharpe -1.90",
}
