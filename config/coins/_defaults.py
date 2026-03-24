"""
_defaults.py — Per-coin config 嘅 shared defaults。

所有 coin config 繼承呢度。Coin-specific overrides 喺各自嘅 config.py。
唔需要改呢個文件嚟加/改某個幣 — 改嗰個幣嘅 config.py 就得。

2026-03-23: 從 pairs.py + params.py + evaluate.py + settings.py 整合而來。
"""

# ─── Identity defaults ───
COIN_DEFAULTS = {
    # Identity (必須 override)
    "symbol": "",
    "prefix": "",
    "group": "crypto_correlated",
    "exchange": ["binance"],

    # ─── Sizing ───
    "vol_mult": 1.0,           # SL/TP scaling relative to BTC
    "max_leverage": 20,        # Per-coin hard cap
    "price_precision": 2,
    "qty_precision": 3,

    # ─── Pipeline ───
    "pair_priority": 1,        # Signal selection priority (higher = preferred)
    "correlation_tracked": False,  # Cross-pair correlation boost
    "liq_enabled": False,      # Liquidation monitor
    "regime_anchor": False,    # Primary for HMM/mode detection

    # ─── Indicator overrides (None = use TIMEFRAME_PARAMS default) ───
    "indicator_params": {
        # 只有同 default 唔同嘅先需要寫
        # e.g. "rsi_long": 32, "rsi_short": 68, "bb_touch_tol": 0.008
    },

    # ─── Indicators toggle (True = compute, False = skip) ───
    "indicators": {
        # Active — used in signal generation
        "bb": True,              # Bollinger Bands (touch, width, basis)
        "rsi": True,             # RSI 14
        "adx": True,             # ADX (soft penalty in range)
        "atr": True,             # ATR (SL fallback, trailing SL)
        "macd_hist": True,       # MACD histogram (trend + crash + mode)
        "obv": True,             # OBV + EMA (range + trend scoring)
        "ma": True,              # MA50/MA200 (trend alignment)
        "sr": True,              # Rolling support/resistance
        "volume_ratio": True,    # Volume vs 30-SMA
        # Unused — keep for record, easy to re-enable
        "di": False,             # DI+ / DI- (ADX 用咗但 DI 冇 strategy 讀)
        "ema_legacy": False,     # EMA fast/slow (舊 range filter 殘留)
        "stoch": False,          # Stochastic K/D (只有舊 evaluate_range_signal 用)
        "macd_line_signal": False,  # MACD line + signal (只用 hist)
        "vwap": False,           # VWAP + bands (零引用)
        "vol_spike": False,      # Volume spike flag (liq_monitor 自己算)
        "z_robust": False,       # Robust z-score (零引用)
        "bb_width_pctl": True,   # BB width percentile — squeeze detection core
    },

    # ─── Strategies ───
    "strategies": {
        "range": {
            "enabled": True,
            "conf_gate": 0.40,    # Minimum confidence to pass signal filter
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
            "enabled": False,     # Opt-in: new/untested strategy, explicitly enable per coin
            "conf_gate": 0.40,
        },
        "burst": {
            "enabled": False,     # Opt-in: volume spike strategy, explicitly enable per coin
            "conf_gate": 0.30,
        },
    },

    # ─── Session preference (squeeze strategy) ───
    # "non_us" = bonus for ASIA/EU sessions (BTC/ETH backtest-proven)
    # "us" = bonus for US_PRE/US_OPEN sessions
    "session_preference": "non_us",

    # ─── Legacy (backward compat) ───
    "sl_mult_override": None,  # Override ATR SL multiplier
    "notes": "",
}
