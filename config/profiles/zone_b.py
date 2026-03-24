"""
zone_b.py — 高槓桿 zone (11-20x)。

觸發：signal confidence >= 0.7。確定性高，較窄 SL。
SL baseline 0.6% (BTC)，per-coin 按 vol_mult 調整。
Margin per trade: 3% of account。
Max leverage 20x（user approved, 25x rejected by BMD tail risk analysis）。
"""

PROFILE = {
    "description":            "Zone B：高槓桿 (11-20x)，確定性高",
    "zone":                   "B",
    "trigger_pct":            0.020,   # 較敏感（高 confidence signal 唔應該等）
    "margin_pct":             0.25,    # 25% account as margin per trade ($30 account = $7.50/trade)
    "sl_pct_base":            0.006,   # 0.6% SL (BTC baseline, scaled by pair vol_mult)
    "tp_pct_base":            0.015,   # 1.5% TP (scaled by vol_mult, same as SL)
    "range_leverage":         18,      # Range 策略高槓桿
    "trend_leverage":         15,      # Trend 策略高槓桿
    "max_open_positions":     1,       # 高槓桿只做一注
    "allow_trend":            True,
    "allow_range":            True,

    # ─── 收緊嘅 Tier 2 params ───
    "entry_volume_min":              0.50,    # 高槓桿要更好嘅 volume 確認
    "trailing_sl_breakeven_atr":     0.8,     # 更早鎖 breakeven
    "trailing_sl_lock_profit_atr":   1.5,     # 更早鎖利
    "early_exit_rsi_overbought":     72,
    "early_exit_rsi_oversold":       28,
    "reentry_size_reduction":        0.40,    # 再入場縮倉更多
    "reentry_cooldown_cycles":       4,       # 更長冷卻期
    "bias_threshold":                3.5,
}
