"""
zone_a.py — 低槓桿 zone (1-10x)。

觸發：signal confidence < 0.7。穩陣，較寬 SL。
SL baseline 1.0% (BTC)，per-coin 按 vol_mult 調整。
Margin per trade: 3% of account。
"""

PROFILE = {
    "description":            "Zone A：低槓桿 (1-10x)，穩陣",
    "trigger_pct":            0.025,   # 信號觸發閾值
    "margin_pct":             0.25,    # 25% account as margin per trade ($30 account = $7.50/trade)
    "sl_pct_base":            0.010,   # 1.0% SL (BTC baseline, scaled by pair vol_mult)
    "tp_pct_base":            0.015,   # 1.5% TP (scaled by vol_mult, same as SL)
    "range_leverage":         20,      # $30 account: need 20x to pass exchange minimums
    "trend_leverage":         20,      # same
    "max_open_positions":     2,
    "allow_trend":            True,
    "allow_range":            True,

    # ─── 保留嘅 Tier 2 params（同 base 一樣） ───
    "entry_volume_min":              0.40,
    "trailing_sl_breakeven_atr":     1.0,
    "trailing_sl_lock_profit_atr":   2.0,
    "early_exit_rsi_overbought":     70,
    "early_exit_rsi_oversold":       30,
    "reentry_size_reduction":        0.30,
    "reentry_cooldown_cycles":       3,
    "bias_threshold":                3.5,
}
