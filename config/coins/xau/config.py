"""
XAU — 0.31x BTC vol (最低)。Aster only。
max_leverage=10 → Zone A only (< Zone B 下限 11x)。
Backtest 180d: 45 trades, WR 53.3%, PF 1.21, Sharpe 0.91 — profitable but low vol。
"""

COIN = {
    "symbol": "XAUUSDT",
    "prefix": "XAU",
    "group": "commodity",
    "exchange": ["aster"],
    "vol_mult": 0.31,
    "max_leverage": 10,        # Zone A only
    "price_precision": 2,
    "qty_precision": 2,
    "pair_priority": 1,

    # XAU is profitable but not a focus coin — keep enabled with defaults
    "strategies": {
        "range": {
            "enabled": True,
            "conf_gate": 0.40,
        },
        "trend": {
            "enabled": True,
            "conf_gate": 0.48,
        },
        "crash": {
            "enabled": True,
            "conf_gate": 0.33,
        },
    },

    "notes": "Profitable (Sharpe 0.91) but low vol. Zone A only. Aster only",
}
