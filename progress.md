# Progress Log — Per-Coin Architecture + WS + Mode Impact

## Session: 2026-03-23 (Session 2)

### Phase 0: 偵察 + 分析 — `complete`
- Subagent audit: 207 total params, 8 unused indicators, 12+ config scatter points
- Subagent WS analysis: ws_manager BTC-only, indicator_engine BTC-only, pipeline already multi-coin ready
- Subagent mode impact: 2 live mechanisms (affinity penalty HIGH, Kelly veto HIGH), rest dead/disabled
- Pipeline SVG generated → `pipeline.svg`
- Full plan written → `task_plan.md`

### Phase 1: Per-Coin Config Architecture — `complete`
- Created `config/coins/` structure: 7 coin subfolders + _defaults.py + loader.py
- loader.py: 17 helper functions (get_coin, get_active_symbols, get_conf_gate, etc.)
- pairs.py → adapter: reads from coin loader, PairConfig backward compat 7/7 match
- POL + XAG: all strategies disabled in config
- 5 consumers migrated:
  - signal_filter.py → get_conf_gate() (replaces SIGNAL_CONF_GATE_PER_SYMBOL)
  - evaluate.py → get_correlation_set() + coin pair_priority (replaces hardcoded)
  - range_strategy.py → coin indicator_params (replaces PRODUCT_OVERRIDES)
  - mode_detector.py → get_regime_anchor() (replaces hardcoded "BTCUSDT")
  - indicator_calc.py → _get_product_overrides() (replaces PRODUCT_OVERRIDES dict)
- Tests: 41/41 passed, 5 consumer verifications green
- 2check: 2 BUG + 4 WARN found and fixed
  - ETH RSI comment wrong direction → fixed
  - range_strategy fallback 用 4h 值 → fixed to 1h values
  - loader.py no thread lock → added threading.Lock + double-check
  - SIGNAL_CONF_GATE_PER_SYMBOL → deprecated comment
- All 56 tests passed (coin_loader 28 + regime 13 + engine 15)
### Phase 2: WS Multi-Coin Expansion — `complete`
- ws_manager.py: SYMBOL scalar → SYMBOLS list from config/coins/ (Binance exchange)
  - 5 coins × 5 streams = 25 streams (Binance limit 1024 = 2.4% usage)
  - Fallback hardcoded list if coin loader unavailable
- indicator_engine.py: complete multi-coin refactor
  - State: `_dataframes[symbol][tf]`, `_indicators[symbol][tf]`
  - _backfill(): loops N symbols × 4 TFs, requires regime anchor 4h+1h
  - Consumer: routes by fields["symbol"] via _SYMBOLS_LOWER fast lookup
  - _write_cache(): all symbols in one JSON, _macro remains BTC-primary
  - _fallback_loop(): all symbols × all TFs
  - _get_params(): per-symbol overrides via PRODUCT_OVERRIDES compat
- 56/56 tests passed (coin_loader + regime + engine)
### Phase 3: Per-Coin Mode Detection — `complete`
- context.py: added `coin_market_mode: dict[str, str]` + `coin_mode_votes: dict[str, dict]`
- mode_detector.py: added `_detect_per_coin_modes()` — loops all coins with 4H data
  - HMM/BOCPD: BTC-primary only (single instance, global regime)
  - Voters: per-coin independent (each coin gets its own RSI/MACD/Vol/MA/Funding vote)
  - Anchor (BTC): reuses global result → ctx.coin_market_mode["BTCUSDT"] = ctx.market_mode
  - Other coins: independent voter result
  - detect_mode_for_pair() already symbol-agnostic — zero changes needed
- signal_filter.py: `ctx.market_mode` → `ctx.coin_market_mode.get(signal.pair, ctx.market_mode)`
  - ETH in RANGE → trend signal penalized -0.42 independently of BTC mode
  - BTC in TREND → range signal penalized -0.23 independently of ETH mode
- Tests: 9/9 passed (pure function, context fields, step integration, filter penalty)
- 2check (7 subagent audit):
  - Q1: 23 ctx.market_mode readers → ALL SAFE ✅
  - Q2: detect_mode_for_pair() PURE ✅ — **BUG FIXED**: non-anchor coins 改為 hmm_regime=None（獨立 5-voter）
  - Q3: funding_rate available for all 7 coins, 0.0 fallback = LOW impact ✅
  - Q4: WS URL 480 chars / 25 streams = 2.4% limit ✅
  - Q5: backfill 20 req = 1.7% rate limit ✅ — **FIXED**: added 50ms inter-call delay
  - Q6: cache format fully backward compat (strict superset) ✅
  - Q7: range_strategy RSI fallback correct (40/60/0.006 = 1h default) ✅
- All 65 tests green after fixes
### Phase 4: 24-Combo Parameter Tuning — `pending`
### Phase 5: Validation — `deferred`

## Reboot Check
| Question | Answer |
|----------|--------|
| 做緊咩？ | Per-coin 架構重組 + WS 4-coin expansion + mode impact analysis |
| 目標？ | 4 coins × 3 strategies × 2 zones = 24 combos，每個 coin 獨立管理 |
| 學到咩？ | Mode 主要透過 affinity penalty 影響；WS 改動只需 ws_manager + indicator_engine |
| 做咗咩？ | Phase 0 偵察完成，plan 寫好 |
| 下一步？ | 確認 plan → Phase 1 開始寫 code |
