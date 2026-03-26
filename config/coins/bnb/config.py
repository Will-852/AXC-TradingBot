"""
BNB — Binance native token。Mid-cap crypto, correlated with BTC/ETH.
AggtTrades already collected (ws_aggtrade_recorder). No backtest data yet.

Starting conservative: squeeze + burst only (event-driven).
Range/trend/crash disabled until backtest confirms edge.
"""

COIN = {
    # === Identity ===
    "symbol": "BNBUSDT",
    "prefix": "BNB",
    "group": "crypto_correlated",
    "exchange": ["binance"],

    # === Sizing ===
    "vol_mult": 1.5,           # ~1.5x BTC volatility (estimate, refine with data)
    "max_leverage": 15,        # Conservative: no backtest yet
    "price_precision": 2,      # Binance tick=0.010
    "qty_precision": 2,

    # === Pipeline ===
    "pair_priority": 2,         # Lower than BTC(5)/ETH(4)/SOL(3) until proven
    "correlation_tracked": True,
    "liq_enabled": True,
    "regime_anchor": False,

    # === Session ===
    "session_preference": "non_us",

    # === Indicator Overrides ===
    "indicator_params": {},

    # === Strategies ===
    "strategies": {
        "range": {
            "enabled": False,    # No backtest data — enable after 30d observation
            "conf_gate": 0.45,
        },
        "trend": {
            "enabled": False,
            "conf_gate": 0.50,
        },
        "crash": {
            "enabled": False,
            "conf_gate": 0.35,
        },
        "squeeze": {
            "enabled": True,     # Event-driven, lower risk
            "conf_gate": 0.45,
        },
        "burst": {
            "enabled": True,     # Volume spike, lower risk
            "conf_gate": 0.30,
        },
    },

    "notes": "New addition 2026-03-26. Squeeze+burst only. Review after 30d data.",
}
