"""
BTC — Baseline coin。Primary regime anchor。
vol_mult = 1.0 (所有其他幣相對 BTC)。
Backtest 180d: 27 trades, WR 37%, PF 0.96, Sharpe -0.07 (flat, signal 太少)
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
    # BTC uses all TIMEFRAME_PARAMS defaults — no overrides needed
    "indicator_params": {},

    # === Indicators ===
    # (inherits all defaults: bb/rsi/adx/atr/macd_hist/obv/ma/sr/volume_ratio ON)
    # (di/ema_legacy/stoch/macd_line_signal/vwap/vol_spike/z_robust/bb_width_pctl OFF)

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
    },

    "notes": "Baseline vol。Backtest flat — investigate low trade count (27 vs ETH 76)",
}
