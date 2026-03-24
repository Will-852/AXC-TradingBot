# Findings — Session 3 Recon
> Updated: 2026-03-24 HKT

## Codebase Recon (Phase 0 prep)

### squeeze_strategy.py vs bt_burst_strategy.py — Shared vs Unique

| Concept | squeeze | burst | Notes |
|---------|---------|-------|-------|
| `volume_ratio` | gate: < 0.80 (quiet) | gate: > 2.5× (spike) | Same field, opposite intent |
| `obv` + `obv_ema` | bonus: +0.05 | hard gate + W=0.25 | Burst mandatory, squeeze optional |
| `bb_upper/lower` | hard gate + scored | NO | Squeeze-only |
| `bb_width_pctl` | hard gate + scored | NO | Squeeze-only |
| `adx` | hard gate + scored | NO | Squeeze-only |
| `price_change_pct` | NO | hard gate + scored | Burst-only (needs prev_close) |
| `prev_close` | NO | YES | Burst-only; backtest engine injects, production 冇 |
| `volatility_regime` | NO | YES (safety gate) | Burst-only |
| Confidence threshold | 0.30 | 0.30 | IDENTICAL |
| SL multiplier | 1.5× ATR | 1.5× ATR | IDENTICAL |
| min_rr | 2.0 | 2.0 | IDENTICAL |
| Cooldown | None | 4 candles | Burst-only |
| Environment | Production (main.py) | Backtest only (engine.py) | Burst NOT in prod |

### indicator_engine.py — Volume Logic Already Present

1. `_calc_volume_ratio()` (line 146): `current_vol / 30-bar avg` → stored as `volume_ratio`
2. `_detect_vol_spike()` in indicator_calc.py (line 133): `vol > SMA(20) * 2.0` → stored as `vol_spike` bool
3. Both computed passively on every kline close — NO events emitted
4. `taker_buy_volume` zeroed out in ws_manager normalization (line 370) — available in Binance data as `k["V"]`

### Data Flow: WS → indicator_engine
```
Binance WS → ws_manager._process_message()
  → _normalize_kline() → xadd("market:klines")
  → indicator_engine._redis_consumer_loop()
    → filter: is_closed=="1" AND interval in TIMEFRAMES
    → _process_kline_close() → _calc_indicators_for() → _write_cache()
```
- No callbacks/pub-sub between processes — only Redis Stream
- Open klines (is_closed=="0") currently filtered OUT

### Script Inventory — Duplicate Groups

| Group | Files | Action |
|-------|-------|--------|
| Squeeze backtests | 4 files (btc/eth/sol/sol_v2) | Merge → 1 parameterized |
| MM backtest versions | v1, v3, v4, v9 | Delete v1+v3, keep v4+v9 |
| Dashboards | dashboard/ (18 files) + dashboard_ng/ (28 files) | Archive old if ng is live |
| LampStore analysis | 3 files (analysis/deep/final) | Keep _final only |
| Ladder backtest | 2 files (2 locations) | Merge |
| W4 analysis | 8 files | Consolidate post-research |

Total: 313 .py files, 37 __init__.py, 276 substantive
