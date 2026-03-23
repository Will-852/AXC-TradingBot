"""
_base.py — Profile 預設值。所有 profile (zone) 繼承呢度。

2-Zone system (2026-03-23):
  Zone A (1-10x):  confidence < 0.7, SL 1.0% BTC baseline
  Zone B (11-20x): confidence >= 0.7, SL 0.6% BTC baseline
  Margin: 3% of account per trade (fixed)
  Per-coin SL scaling via pairs.py vol_mult
"""

# Zone A (低槓桿) 作為 safe fallback base
DEFAULT_PROFILE = {
    # ─── Zone Identity ───
    "description":            "",
    "zone":                   "A",      # "A" (1-10x) or "B" (11-20x)

    # ─── Entry ───
    "trigger_pct":            0.025,    # 信號觸發閾值

    # ─── Sizing（2-Zone: 固定 margin，唔用 risk_per_trade_pct） ───
    "margin_pct":             0.03,     # 3% of account as margin per trade
    "sl_pct_base":            0.010,    # 1.0% SL (BTC baseline, pairs.py vol_mult scales)
    "tp_pct_base":            0.015,    # 1.5% TP (BTC baseline, pairs.py vol_mult scales — 保持 R:R constant)

    # ─── Legacy sizing（保留 backward compat，新邏輯用 margin_pct） ───
    "risk_per_trade_pct":     0.02,     # fallback if margin_pct missing
    "sl_atr_mult_range":      1.0,      # ATR-based SL fallback
    "sl_atr_mult_trend":      1.5,
    "tp_atr_mult":            2.0,
    "range_min_rr":           1.5,      # min R:R (Zone A: TP 1.5% / SL 1.0% = 1.5:1)
    "range_tp_mid_fraction":  0.50,
    "trend_min_rr":           1.5,

    # ─── Position ───
    "max_open_positions":     2,
    "allow_trend":            True,
    "allow_range":            True,
    "trend_min_change_pct":   5.0,

    # ─── Leverage ───
    "range_leverage":                8,
    "trend_leverage":                7,

    # ─── Confidence（保留 legacy，zone 選擇已取代 confidence_risk_*） ───
    "confidence_risk_high":          1.25,
    "confidence_risk_normal":        1.0,
    "confidence_risk_low":           0.6,
    "confidence_risk_cap":           0.03,

    # ─── Filters + Trailing ───
    "entry_volume_min":              0.40,
    "trailing_sl_breakeven_atr":     1.0,
    "trailing_sl_lock_profit_atr":   2.0,
    "early_exit_rsi_overbought":     70,
    "early_exit_rsi_oversold":       30,
    "reentry_size_reduction":        0.30,
    "reentry_cooldown_cycles":       3,
    "bias_threshold":                3.5,
}
