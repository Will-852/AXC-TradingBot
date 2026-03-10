"""
conservative.py — 保守 profile。

策略理念：只做 RANGE，唔做 TREND。低風險、嚴格 SL、單倉。
適合低波動市場或資金保護階段。
"""

PROFILE = {
    "description":            "保守：等待 RANGE 機會，最低風險",
    "trigger_pct":            0.03,     # 3%（較嚴格，減少噪音）
    "risk_per_trade_pct":     0.01,     # 1% 風險（base 嘅一半）
    "sl_atr_mult":            1.5,      # 更寬 SL 畀多啲空間
    "max_open_positions":     1,        # 單倉
    "allow_trend":            False,    # 唔做 trend
    "trend_min_change_pct":   None,     # N/A — trend disabled

    # Tier 2 overrides
    "range_leverage":                5,      # 低槓桿
    "trend_leverage":                3,      # 就算打開 trend 都用低槓桿
    "confidence_risk_high":          1.0,    # 唔加碼，保守
    "confidence_risk_cap":           0.015,  # 絕對上限 1.5%
    "trailing_sl_breakeven_atr":     0.8,    # 更早移 SL 到 breakeven
    "reentry_cooldown_cycles":       5,      # 更長冷卻期
}
