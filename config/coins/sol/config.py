"""
SOL — 1.9x BTC vol。Correlated with BTC/ETH but higher beta。
Backtest 180d: 68 trades, WR 42.6%, PF 1.18, Sharpe 0.79

Highest WR of 4 focus coins but lower PF — small winners, tight SL works。
Binance only (no Aster listing)。
"""

COIN = {
    # === Identity ===
    "symbol": "SOLUSDT",
    "prefix": "SOL",
    "group": "crypto_correlated",
    "exchange": ["binance", "hyperliquid"],

    # === Sizing ===
    "vol_mult": 1.9,           # 1.9x BTC volatility
    "max_leverage": 15,        # Capped: high vol
    "price_precision": 2,
    "qty_precision": 0,

    # === Pipeline ===
    "pair_priority": 3,         # Same tier as ETH
    "correlation_tracked": True,
    "liq_enabled": True,
    "regime_anchor": False,

    # === Session ===
    "session_preference": "us",  # 360d backtest: SOL squeeze US PF 1.28 vs Non-US 0.91

    # === Indicator Overrides ===
    "indicator_params": {},

    # === Strategies ===
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
        "squeeze": {
            "enabled": True,
            "conf_gate": 0.45,     # SOL 63% BTC contagion, downside bias (46.6% up)
        },
    },

    "notes": "Highest WR (42.6%)。63% BTC contagion, slight downside bias in explosions",
}
