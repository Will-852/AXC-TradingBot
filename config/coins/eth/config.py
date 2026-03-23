"""
ETH — Best performer。1.5x BTC vol。跟隨 BTC but 有自己嘅 rhythm。
Backtest 180d: 76 trades, WR 35.5%, PF 1.48, Sharpe 1.83 (BEST)

RSI 收緊到 32/68 (vs 1h default 40/60)：
  32 = LONG 入場需要 RSI 更低（更 oversold），68 = SHORT 需要更高（更 overbought）。
  比 1h default 更嚴格 → 減少 false signal。Backtest 180d Sharpe 1.83 驗證。
"""

COIN = {
    # === Identity ===
    "symbol": "ETHUSDT",
    "prefix": "ETH",
    "group": "crypto_correlated",
    "exchange": ["aster", "binance", "hyperliquid"],

    # === Sizing ===
    "vol_mult": 1.5,           # 1.5x BTC volatility
    "max_leverage": 20,
    "price_precision": 2,
    "qty_precision": 2,

    # === Pipeline ===
    "pair_priority": 3,
    "correlation_tracked": True,
    "liq_enabled": True,
    "regime_anchor": False,

    # === Indicator Overrides ===
    "indicator_params": {
        "rsi_long": 32,        # Stricter: need RSI ≤32 for LONG (1h default: 40)
        "rsi_short": 68,       # Stricter: need RSI ≥68 for SHORT (1h default: 60)
    },

    # === Strategies ===
    # ETH conf gates 全部 0.50 (stricter than default) — high vol = need higher confidence
    "strategies": {
        "range": {
            "enabled": True,
            "conf_gate": 0.50,
        },
        "trend": {
            "enabled": True,
            "conf_gate": 0.50,
        },
        "crash": {
            "enabled": True,
            "conf_gate": 0.50,
        },
        "squeeze": {
            "enabled": True,
            "conf_gate": 0.45,     # ETH 82% BTC contagion → squeeze signals highly reliable
        },
    },

    "notes": "Best Sharpe (1.83)。RSI 32/68 proven by backtest。82% BTC contagion rate",
}
