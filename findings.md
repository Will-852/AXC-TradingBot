# Findings: Modular Split Audit
> Created: 2026-03-25

## run_mm_live.py Deep Audit (3,980 lines, SEEN)

### Module-Level Mutable State (ownership assignment)
| State | Line | Owner Module | Readers | Writers |
|-------|------|-------------|---------|---------|
| `_tg_last_alert` | 60 | orchestrator | _tg_alert | _tg_alert |
| `_api_calls` | 116 | data_feeds | _rate_ok | _track_call |
| `_mkt_fetcher` | 118 | orchestrator | run_cycle | main |
| `_ws_binance` | 119 | orchestrator | _price, run_cycle | main |
| `_ws_poly` | 120 | orchestrator | _poly_midpoint, _poly_ob_imbalance | main |
| `_ws_user` | 121 | orchestrator | _check_fills, cancel defense, reprice | main |
| `_endgame_mid_buf` | 122 | exit_logic | endgame case 2 | endgame append, cleanup |
| `_btc_price_buf` | 124 | risk_guards | hedge trigger | run_cycle append |
| `_cache` | 149 | data_feeds | all fetch fns | all fetch fns |
| `_holder_cache` | 152 | data_feeds | _holder_imbalance | _holder_imbalance |
| `_last_heavy_ts` | 1389 | orchestrator | run_cycle | run_cycle |
| `_post_fill_checks` | 1155 | order_lifecycle | AS measurement | _check_fills |

### Bugs Found During Audit
1. **`_ts_hkt()` undefined** — called at lines 2617, 2641 (Door-B cancel defense). NameError at runtime. Fix: define as `datetime.now(tz=_HKT).strftime("%H:%M:%S")` in constants.py
2. **`dry_run` closure leak** — `_check_fills` references `dry_run` (line 779) without it being a parameter. Works accidentally because Python closure captures caller's local scope when nested. After split, must pass explicitly.

### Cross-Cutting Functions (used by 3+ target modules)
| Function | Used By | Resolution |
|----------|---------|-----------|
| `_cache` dict | data_feeds (all), signal_pipeline (via imports) | Own in data_feeds |
| `_price()` | data_feeds, signal_pipeline, order_lifecycle, exit_logic | Own in data_feeds, import |
| `_poly_midpoint()` | data_feeds, exit_logic, risk_guards | Own in data_feeds, import |
| `_execute()` | entry_logic, exit_logic (endgame, hedge, re-entry) | Own in order_lifecycle, import |
| `_bump_fill()` | order_lifecycle, exit_logic | Own in state_io, import |
| `_log_order()` | order_lifecycle, exit_logic | Own in state_io, import |
| `_tg_alert()` | risk_guards, orchestrator | Own in orchestrator, pass as callback |
| `_HKT`, `_ET` | everywhere | Own in constants.py |

### Exit Thresholds (currently buried in function body, line 3265-3268)
```python
_EXIT_STOP_PCT = 0.25
_BLACK_SWAN_MID = 0.96
_BLACK_SWAN_SELL_PCT = 0.96
_COST_RECOVERY_MID = 0.64
```
→ Promote to constants.py during split

### Inline Classes to Extract
- `_PaperClient` (lines 2143-2151) → order_lifecycle.py
- `_T2Paper` (lines 2333-2337) → order_lifecycle.py
- `_find_directional_orders` nested fn (lines 2407-2423) → order_lifecycle.py

## Other Files Quick Audit

### tg_bot.py (2,291 lines, SEEN)
- 34 constants, 8 mutable globals
- 53 functions, biggest: `check_and_push_alerts` (273 lines), `handle_free_text` (83 lines)
- Split: llm_gateway + commands + exchange_ops

### run_1h_live.py (1,960 lines, SEEN)
- 52 constants, 10 mutable globals
- 33 functions, biggest: `_reprice_1h` (255 lines), `run_cycle` (393 lines)
- Split: data_feeds + paper

### run_5m_live.py (1,660 lines, SEEN)
- 36 constants, 9 mutable globals
- 30 functions, biggest: `_w4_entry` (206 lines), `run_cycle` (201 lines)
- Split: data_feeds + signal

### backtest/engine.py (1,554 lines, SEEN)
- 20 constants, 0 mutable globals (all instance state)
- BacktestEngine class with 18 methods, biggest: `_summary` (314 lines)
- Split: engine_types + engine_reporting (facade re-export)
- **Config divergence**: REGIME_ADJUST_ENABLED=True here vs False in config/params.py

## External References That Must NOT Break
| File | References | Would Break If Renamed |
|------|-----------|----------------------|
| `poly_bot_control.py:20-21` | run_mm_live.py, run_1h_live.py paths + process keys | YES |
| `schedules.json` | run_mm_live, run_1h_live keys | YES |
| `ai.openclaw.telegram.plist` | scripts/tg_bot.py path | YES |
| `health_check.sh:36` | scripts/tg_bot.py existence | YES |
| `build_axc_zip.sh:27` | scripts/tg_bot.py path | YES |
| 14 `from backtest.engine import` sites | BacktestEngine, WARMUP_CANDLES etc | YES (facade fixes) |

---

## 4H Multi-Indicator Backtest Script Design

> Added 2026-03-25 (long-term strategy session, separate from modular split audit above)

### Goal
Test 10+ indicators against 365 days of 4H windows → find which predict UP/DOWN best → build scoring model

### Data
- Source: Binance 1m klines (BTCUSDT), 365 days
- Windows: 6/day × 365 = ~2,190 (statistically robust)
- Cache: `analysis/data/btc_1m_klines_365d.json` (~525K candles)
- Fetch: ~63s first run, instant cached
- ⚠️ [VERIFY-LATER] 365d of 1m klines = large. May need day-by-day fetch.

### 10 Indicators to Test

| # | Indicator | Source | Vote Logic |
|---|-----------|--------|-----------|
| 1 | Momentum (T+15m) | inline | >8bps→UP, <-8bps→DOWN |
| 2 | RSI-14 | `calc_indicators()` | <40→UP, >60→DOWN |
| 3 | MACD histogram | `calc_indicators()` | >0→UP, <0→DOWN |
| 4 | BB squeeze pctl | `calc_bb_width_pctl()` | <15→squeeze+breakout dir |
| 5 | Volume spike | `detect_volume_spike()` | spike+green→UP |
| 6 | ADX+DI | `calc_indicators()` | ADX>25+DI+>DI-→UP |
| 7 | Session | `get_session_tag()` | per-session bias from data |
| 8 | Bridge fair | `bridge_fair()` | >0.55→UP, <0.45→DOWN |
| 9 | Funding rate | Binance API | ⚠️ [VERIFY-LATER] availability |
| 10 | EMA trend | `calc_indicators()` | price>EMA50→UP |

All functions already exist. Zero new indicator code.

### Pipeline (5 phases)

```
Phase 1: DATA — fetch 1m klines, cache
Phase 2: WINDOWS — define 4H boundaries, compute open/close/resolution
Phase 3: FEATURES — calc_indicators() + bridge_fair() per window
Phase 4: SCORING — per-indicator accuracy + combo accuracy
Phase 5: OUTPUT — ranked tables + recommended weights
```

### Key Reusable Functions

| Need | Function | File |
|------|----------|------|
| All 15+ indicators | `calc_indicators(df, params)` | `scripts/indicator_calc.py:206` |
| Squeeze detection | `detect_squeeze(df)` | `scripts/signals/squeeze.py:26` |
| Volume spike | `detect_volume_spike(df)` | `scripts/signals/volume.py:50` |
| Bridge fair value | `bridge_fair(current, open, vol, t_remain)` | `polymarket/backtest/bridge_weight_bt.py:61` |
| Session tag | `get_session_tag(ts)` | `scripts/indicator_calc.py:173` |
| Kline fetch | `fetch_klines(symbol, "1m", limit)` | `scripts/indicator_calc.py:92` |

### Implementation

```
New file: polymarket/analysis/4h_indicator_backtest.py
~400 lines (data ~100, windows ~50, indicators ~100, scoring ~100, output ~50)
Runtime: ~2 min first, ~30s cached
Output: analysis/data/4h_indicator_backtest_results.json + console tables
```

### ⚠️ [VERIFY-LATER] Markers

| Item | Risk | Check |
|------|------|-------|
| 1m klines 365d fetch size | May hit rate limits | Day-by-day with sleep |
| calc_indicators needs 200 candle lookback | 200 × 1m = 3.3h preceding data | Fetch window_start - 4h |
| Funding rate historical API | May not have 365d history | Use Binance fundingRate endpoint |
| Session bias may not exist at 4H | 4H windows span multiple sessions | Test first, may be noise |
| 2,190 windows vs 56 combos → overfitting | More combos = more false positives | Use train/test split (70/30) |
