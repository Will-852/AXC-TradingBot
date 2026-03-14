"""
aggressive.py — 進取 profile。

策略理念：追趨勢，最高風險最高回報。寬鬆入場條件。
適合高波動市場、有明確趨勢時。
"""

PROFILE = {
    "description":            "進取：追趨勢，最高風險最高回報",
    "trigger_pct":            0.02,     # 2%（最敏感）
    "risk_per_trade_pct":     0.03,     # 3% 風險
    "sl_atr_mult_range":      0.8,      # 最窄 Range SL（180d grid top 1）
    "sl_atr_mult_trend":      1.4,      # Trend 底線（180d grid: <1.3 斷崖）
    "tp_atr_mult":            3.0,      # 更大 TP 目標
    "range_min_rr":           2.0,      # 較鬆 RR
    "trend_min_rr":           2.5,      # Trend RR 仍高於 Range
    "max_open_positions":     3,        # 最多 3 倉
    "trend_min_change_pct":   2.0,      # 2% 就追 trend

    # Tier 2 overrides
    "range_leverage":                10,     # 高槓桿
    "trend_leverage":                8,      # Trend 都用高槓桿
    "confidence_risk_high":          1.5,    # 高信心加碼更多
    "confidence_risk_cap":           0.04,   # 絕對上限 4%
    "entry_volume_min":              0.6,    # 更鬆嘅 volume gate
    "early_exit_rsi_overbought":     75,     # 畀多啲空間跑
    "early_exit_rsi_oversold":       25,     # 畀多啲空間跑
    "reentry_cooldown_cycles":       2,      # 更短冷卻期
    "bias_threshold":                3.0,    # 更鬆嘅偏向閾值
}
