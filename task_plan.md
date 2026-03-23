# Task: AXC Per-Coin Architecture + WS Expansion + Mode Impact
> Created: 2026-03-23 HKT (Session 2)

## Goal
4 symbols (BTC/ETH/XRP/SOL) × 3 strategies (range/trend/crash) × 2 zones (A/B) = **24 combos**。
每個幣種獨立管理，共用 pipeline。清晰到加減改都係一個 file 嘅事。

## Scope
- **Focus**: BTC, ETH, XRP, SOL — full analysis + per-coin config
- **Keep**: POL, XAG, XAU — minimal config, inherit defaults
- **8 unused indicators**: Keep for record, mark `enabled: False`

---

## Analysis Summary

### A. Per-Coin Config（現狀問題）
Config 散落 **12+ 個位置**：
| 位置 | 存咗咩 | 問題 |
|------|--------|------|
| pairs.py | vol_mult, max_leverage, RSI/BB overrides | ✅ 正確位置 |
| indicator_calc.py PRODUCT_OVERRIDES | ETH RSI, XRP BB tolerance | ❌ 同 pairs.py 重複 |
| settings.py PAIRS/PAIR_PREFIX | Symbol list + prefix map | ❌ 同 pairs.py 重複 |
| settings.py POSITION_GROUPS | 幣種分組 | ❌ 應該喺 coin config |
| params.py ASTER/BINANCE/HL_SYMBOLS | 交易所 symbol list | ⚠️ 需要保留但可 auto-derive |
| params.py SIGNAL_CONF_GATE_PER_SYMBOL | ETH conf gate override | ❌ 應該喺 coin config |
| params.py REGIME_SIGNAL_RULES | per-(coin,regime,mode,strategy) | ❌ 全部 disabled（dead code） |
| evaluate.py _CRYPTO_PAIRS | Correlation boost set | ❌ 硬編碼 |
| evaluate.py PAIR_PRIORITY | Signal selection priority | ❌ 硬編碼 |
| mode_detector.py line 263 | BTC as primary | ⚠️ 架構決定 |
| liq_params.py LIQ_COINS | Liq monitor coins | ⚠️ 可 auto-derive |

### B. WS Data Layer（現狀 → 目標）
| | 現狀 | 目標 |
|---|---|---|
| ws_manager.py | BTC only (5 streams) | 4 coins × 5 = 20 streams |
| indicator_engine.py | BTC only (single-symbol state) | 4-coin parallel compute |
| indicator_calc.py | ✅ Already symbol-agnostic | 零改動 |
| indicator_cache.json | `{BTCUSDT: {...}}` | `{BTCUSDT: {...}, ETHUSDT: {...}, ...}` |
| trader_cycle | ✅ Already loops all pairs | 零改動 |
| **Binance WS limit** | 1024 streams/connection | 20 streams = 2% usage |

### C. Mode Detection Impact（深度分析）
```
MODE DETECTION
│
├─► SignalFilterStep (HIGH impact)
│   SIGNAL_MODE_AFFINITY penalties:
│   - RANGE mode → trend signal needs ≥0.90 conf (effectively blocked)
│   - TREND mode → range signal needs ≥0.63 conf (heavily penalized)
│   - CRASH mode → range ≥0.70, trend ≥0.56
│   - Crash strategy: 0 penalty in ALL modes (always passes)
│   THIS IS THE DOMINANT MODE MECHANISM
│
├─► Kelly Criterion (HIGH impact)
│   compute_kelly_base_risk(mode) — if f*≤0 → trade CANCELLED
│   But requires 30+ historical trades per mode to activate
│
├─► Zone Selection (MEDIUM)
│   regime_confidence ≥0.70 for 2 cycles → Zone B
│   Signal confidence can override (0.70 upgrade / 0.50 downgrade)
│
├─► Strategy evaluate() — NONE
│   All 3 strategies are MODE-BLIND
│   They score on indicators only, never read ctx.market_mode
│
└─► REGIME_SIGNAL_RULES — DEAD CODE
    get_regime_rule() returns None unconditionally
    Disabled 2026-03-19 ("net effect negative")
```

**核心發現**：Mode 主要透過 affinity penalty 影響邊隻 strategy 過到 gate。
但而家係**全局 mode**（BTC-primary），唔係 per-coin mode。
BTC in RANGE 唔代表 ETH in RANGE — 呢個係最大嘅架構問題。

---

## Phases

### Phase 1: Per-Coin Config Architecture — `complete`
建 `config/coins/` 結構，統一所有 per-coin config。

```
config/coins/
├── _defaults.py          # 所有 coin 共享嘅 base defaults
├── loader.py             # get_coin("BTCUSDT") → merged config dict
├── btc/
│   ├── __init__.py       # re-export COIN_CONFIG
│   ├── config.py         # identity, sizing, precision, exchange, group
│   └── strategies.py     # per-strategy weights, gates, indicator toggles
├── eth/
│   ├── __init__.py
│   ├── config.py
│   └── strategies.py
├── xrp/
│   └── ...
├── sol/
│   └── ...
├── pol/
│   └── config.py         # minimal — inherits all strategy defaults
├── xag/
│   └── config.py
└── xau/
    └── config.py
```

Steps:
- [x] 1.1 建 `_defaults.py` — 從 _base.py + params.py 提取所有 shared defaults ✅
- [x] 1.2 建 4 focus coins 完整 config (btc/eth/xrp/sol) ✅
- [x] 1.3 建 3 minimal coins (pol/xag/xau) — POL+XAG disabled ✅
- [x] 1.4 建 `loader.py` — 17 helper functions, deep merge, cache ✅
- [x] 1.5 8 unused indicators: 每個 coin 標記 `enabled: False` in defaults ✅
- [x] 1.8 Tests: 28/28 passed + 13/13 regime tests green ✅
- [x] 1.6 遷移消費者 → 讀 coin loader ✅
  - signal_filter.py: SIGNAL_CONF_GATE_PER_SYMBOL → get_conf_gate()
  - evaluate.py: _CRYPTO_PAIRS → get_correlation_set(), PAIR_PRIORITY → coin config
  - range_strategy.py: PRODUCT_OVERRIDES → get_coin()["indicator_params"]
  - mode_detector.py: "BTCUSDT" → get_regime_anchor()
  - indicator_calc.py: PRODUCT_OVERRIDES → _get_product_overrides()
- [x] 1.7 清理: deprecated comment on params.py, fix ETH comment, fix fallbacks, add thread lock ✅
- [x] 1.8 Tests: 41/41 passed + 5 consumer verifications ✅

### Phase 2: WS Multi-Coin Expansion — `complete`
ws_manager + indicator_engine 支援 4 coins real-time。

Steps:
- [x] 2.1 ws_manager.py: SYMBOL → SYMBOLS list from coin loader, 25 streams built ✅
- [x] 2.2 indicator_engine.py: per-symbol `_dataframes` + `_indicators` state dicts ✅
- [x] 2.3 indicator_engine.py: `_backfill()` loops N symbols × 4 TFs ✅
- [x] 2.4 indicator_engine.py: consumer routes by `fields["symbol"]` with _SYMBOLS_LOWER set ✅
- [x] 2.5 indicator_engine.py: `_write_cache()` outputs all symbols ✅
- [x] 2.6 indicator_engine.py: `_fallback_loop()` covers all symbols ✅
- [ ] 2.7 Integration test: ws_manager + indicator_engine (needs live Redis)
- [ ] 2.8 Verify cache size after restart
- [ ] 2.9 Restart test: ws_manager → indicator_engine 啟動順序

### Phase 3: Per-Coin Mode Detection — `complete`
每個 coin 獨立 voter-based mode detection。HMM 保持 BTC-primary。

**Architecture**:
```
DetectModeStep.run():
  1. HMM/BOCPD on BTC anchor → ctx.volatility_regime, ctx.regime_confidence (scalar, global)
  2. FOR EACH active coin:
     - Read coin's 4H indicators
     - Run 5 voters → detect_mode_for_pair()
     - Write to ctx.coin_market_mode[symbol]
  3. ctx.market_mode (scalar) = BTC anchor (backward compat)
  4. Brake check per-coin (optional: per-coin downgrade)
```
**SignalFilterStep**: `ctx.market_mode` → `ctx.coin_market_mode.get(signal.pair, ctx.market_mode)`

Steps:
- [x] 3.1 分析：detect_mode_for_pair() 已經 symbol-agnostic, voters 已經 agnostic ✅
- [x] 3.2 context.py: 加 coin_market_mode + coin_mode_votes dict fields ✅
- [x] 3.3 mode_detector.py: _detect_per_coin_modes() loops all coins with 4H data ✅
- [x] 3.4 signal_filter.py: coin_mode fallback → ctx.market_mode (per-coin penalty) ✅
- [x] 3.5 Tests: 9/9 passed — BTC=TREND + ETH=RANGE 同時存在 ✅
- [x] 3.6 2CHECK complete: 7 subagent audit, 2 bugs fixed ✅
  - Q2 FIX: non-anchor coins → hmm_regime=None (pure 5-voter, no BTC HMM leak)
  - Q5 FIX: backfill 50ms inter-call delay (rate limit protection)

### Phase 4: Squeeze-Explosion Strategy — `in_progress`
BMD 分析：42.5% 嘅 BTC 波動集中在 16.1% 嘅 candles（explosive >2%）。
SQUEEZE pattern (13-16%) + QUIET_THEN_BOOM (5-8%) = 最可操作嘅前兆。

**Architecture**:
```
SqueezeStrategy.evaluate(pair, indicators, ctx):
  1. BB width percentile < 20%      ← squeeze detected
  2. Volume ratio < 0.7 (≥2 candles) ← quiet accumulation
  3. ADX < 20                       ← no directional energy
  4. Trigger: price breaks BB upper → LONG / BB lower → SHORT
  5. Confidence bonus: US session (12-20 UTC) +0.10

  SL: ATR × 1.0 (tight — squeeze breakout should be decisive)
  TP: ATR × 3.0 (ride the explosion)
  R:R = 3.0 minimum → BE = 25%
```

Steps:
- [ ] 4.1 Re-enable `bb_width_pctl` in _defaults.py indicators
- [ ] 4.2 Add `session_tag` to CycleContext (use existing get_session_tag)
- [ ] 4.3 Create `squeeze_strategy.py` — StrategyBase subclass
- [ ] 4.4 Register in main.py + add SQUEEZE to SIGNAL_MODE_AFFINITY
- [ ] 4.5 Per-coin config: BTC squeeze-only, ETH/XRP/SOL squeeze + existing
- [ ] 4.6 Add squeeze conf_gate to coin configs
- [ ] 4.7 Tests
- [ ] 4.8 ⚠️ 2CHECK: squeeze strategy + signal filter interaction

### Phase 5: Validation — `deferred`
- [ ] 5.1 Paper trade 每個 active combo ≥50 trades
- [ ] 5.2 Compare paper vs backtest WR/PF
- [ ] 5.3 Overfitting validation (DSR, PBO) per combo

---

## Decisions
| Decision | Rationale |
|----------|-----------|
| Subfolder per coin (唔係 single file) | 用戶要求：「subfolder再細分唔同指標」，方便 toggle |
| Focus 4 coins, keep 7 | POL/XAG/XAU backtest 差但保留 config，唔 deep-dive |
| Per-coin voter mode, BTC-primary HMM | Voter 用 per-coin indicators 做到；HMM 需要長歷史，只有 BTC 夠 |
| 8 unused indicators → enabled:False | 用戶要求 keep for record，唔刪除 |
| REGIME_SIGNAL_RULES → 移除 dead code | 2026-03-19 已禁用，保留只會混淆 |
| _macro 保持 BTC-only | 用於全局 regime detection，唔需要 per-coin |

## Errors
| # | Error | Resolution |
|---|-------|------------|
| — | — | — |

## Risk
| Risk | Mitigation |
|------|-----------|
| 24 combos = 24× overfitting 風險 | Phase 4 只調 enabled/disabled，唔 tune weights |
| WS 4× data → Redis pressure | 40 msg/min 仍然好低，monitoring 確認 |
| Per-coin mode 同 global mode 矛盾 | HMM 保持 global，voter per-coin，分清用途 |
| Migration 期間 pipeline 壞 | Phase 1 用 adapter pattern，舊路徑 fallback |
