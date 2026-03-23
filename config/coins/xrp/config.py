"""
XRP — 2x BTC vol。獨立走勢（唔跟 BTC correlation group）。
Backtest 180d: 56 trades, WR 39.3%, PF 1.34, Sharpe 1.19

BB tolerance 0.008 (wider than default 0.006):
  XRP spread/noise 大，用 default 0.006 會 false trigger。
SL mult override 1.0:
  XRP 已經用 vol_mult=2.0 scale SL，ATR fallback 唔使再乘。
"""

COIN = {
    # === Identity ===
    "symbol": "XRPUSDT",
    "prefix": "XRP",
    "group": "crypto_independent",
    "exchange": ["aster", "binance"],

    # === Sizing ===
    "vol_mult": 2.0,           # 2x BTC volatility
    "max_leverage": 15,        # Capped: high vol
    "price_precision": 4,
    "qty_precision": 0,

    # === Pipeline ===
    "pair_priority": 2,
    "correlation_tracked": False,  # Independent — no cross-pair boost
    "liq_enabled": False,
    "regime_anchor": False,

    # === Indicator Overrides ===
    "indicator_params": {
        "bb_touch_tol": 0.008,   # Wider BB tolerance (default: 0.006)
    },

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

    "sl_mult_override": 1.0,  # ATR fallback SL multiplier override
    "notes": "2x vol, independent。BB tol wider for noise",
}
