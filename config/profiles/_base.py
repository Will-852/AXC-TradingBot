"""
_base.py — Profile 預設值。所有 profile 繼承呢度。

新增參數：加喺 DEFAULT_PROFILE + 寫 docstring 解釋點解需要呢個值。
Profile 只需 override 同 base 唔同嘅 key，其餘自動繼承。
"""

# Balanced profile 嘅值作為 base（中位數風險）
DEFAULT_PROFILE = {
    # ─── Tier 1: 原有 TRADING_PROFILES keys ───
    "description":            "",
    "trigger_pct":            0.025,   # 信號觸發閾值（2.5%）
    "risk_per_trade_pct":     0.02,    # 每筆風險 2%
    "sl_atr_mult":            1.2,     # SL = 1.2 × ATR
    "tp_atr_mult":            2.0,     # TP = N × ATR（reserved）
    "range_min_rr":           2.3,     # Range 最低 reward:risk
    "trend_min_rr":           3.0,     # Trend 最低 reward:risk
    "max_open_positions":     2,       # 最多倉位
    "allow_trend":            True,    # 允許 trend 策略
    "allow_range":            True,    # 允許 range 策略
    "trend_min_change_pct":   5.0,     # Trend 最低變動%

    # ─── Tier 2: 從 settings.py 升級為 per-profile ───
    "range_leverage":                8,      # Range 槓桿
    "trend_leverage":                7,      # Trend 槓桿
    "confidence_risk_high":          1.25,   # 高信心 → risk × 1.25
    "confidence_risk_normal":        1.0,    # 普通信心 → risk × 1.0
    "confidence_risk_low":           0.6,    # 低信心 → risk × 0.6
    "confidence_risk_cap":           0.03,   # 風險絕對上限 3%
    "entry_volume_min":              0.8,    # volume_ratio < 0.8 → skip
    "trailing_sl_breakeven_atr":     1.0,    # profit > 1×ATR → SL 移到 entry
    "trailing_sl_lock_profit_atr":   2.0,    # profit > 2×ATR → 鎖利
    "early_exit_rsi_overbought":     70,     # LONG 提早離場 RSI
    "early_exit_rsi_oversold":       30,     # SHORT 提早離場 RSI
    "reentry_size_reduction":        0.30,   # 再入場縮倉 30%
    "reentry_cooldown_cycles":       3,      # 再入場冷卻 3 cycles
    "bias_threshold":                3.5,    # 星期偏向閾值
}
