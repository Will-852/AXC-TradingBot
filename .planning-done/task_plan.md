# Task: AXC Trading — Session 3 Full Plan
> Created: 2026-03-24 HKT
> Previous: Per-coin architecture + squeeze strategy (5 commits, Session 2)
> Direction: Option B — 兩個獨立 strategy，共享 signal detection module

---

## Goal
將 AXC 從「有策略但散亂」推進到「生產就緒 + 可驗證」：
1. 共享 signal module 消除重複
2. Event-driven trigger 捉到 85-90% explosive move（vs 1H close 嘅 56%）
3. 57 scripts → unified CLI
4. Integration test + paper trade 驗證

---

## Phase 0: Signal Detection Module（共享層）
> Status: pending
> Files: NEW `scripts/signals/` module
> Est: 3-4 files, ~300 lines

**Why**: squeeze 同 burst 都用 volume_ratio、OBV、BB — 而家各自 hardcode。抽出共享層：
- 兩個 strategy 各自保持獨立決策（唔撈亂）
- Signal detection 邏輯只維護一份
- 後續加新 strategy 直接 import

**Architecture**:
```
scripts/signals/
├── __init__.py
├── volume.py      ← volume_ratio scoring, volume spike detection, projected volume
├── squeeze.py     ← BB squeeze detection (bb_width_pctl, ADX, quiet volume)
└── obv.py         ← OBV confirmation scoring (OBV vs OBV_EMA, divergence)
```

**Steps**:
- [ ] 0.1 Create `scripts/signals/volume.py`:
  - `score_volume_ratio(ratio, low=0.8, high=5.0) → float` — 歸一化 0-1
  - `detect_volume_spike(ratio, threshold=2.5) → bool`
  - `project_volume(current_vol, elapsed_pct, avg_vol) → float` — for event-driven
- [ ] 0.2 Create `scripts/signals/squeeze.py`:
  - `detect_squeeze(bb_width_pctl, adx, volume_ratio, gates) → SqueezeState`
  - `score_squeeze(bb_width_pctl, adx, volume_ratio) → float` — weighted confidence
  - Gates configurable: `{bb_pctl_max: 30, adx_max: 25, vol_ratio_max: 0.8}`
- [ ] 0.3 Create `scripts/signals/obv.py`:
  - `score_obv_confirmation(obv, obv_ema, direction) → float` — 0-1
  - `detect_obv_divergence(obv, obv_ema, price_direction) → bool`
- [ ] 0.4 Refactor `squeeze_strategy.py` → import from `scripts/signals/`
  - 保持所有 thresholds + weights 不變
  - 只改 internal helper → signal module calls
  - 驗證：same output for same input
- [ ] 0.5 Refactor `bt_burst_strategy.py` → import from `scripts/signals/`
  - volume scoring → `signals.volume.score_volume_ratio()`
  - OBV scoring → `signals.obv.score_obv_confirmation()`
  - 保持 burst-specific logic（cooldown, price_change_pct gate, volatility_regime gate）

**Acceptance**: 兩個 strategy 各自產生 identical signals（before vs after refactor）

---

## Phase 1: Burst Strategy → Production
> Status: pending
> Files: bt_burst_strategy.py → scripts/trader_cycle/strategies/burst_strategy.py
> Depends: Phase 0

**Why**: bt_burst 目前只喺 backtest engine，唔喺 production。要搬過去先可以 live test。

**Key blocker**: `prev_close` field — backtest engine injects it (engine.py:506-510) 但 production indicator_calc 冇。

**Steps**:
- [ ] 1.1 Add `prev_close` to `indicator_calc.py` — 用 df.iloc[-2]["close"]，同 backtest engine 同一邏輯
- [ ] 1.2 Create `scripts/trader_cycle/strategies/burst_strategy.py`:
  - Copy from bt_burst，改 imports → `scripts/signals/`
  - 保持 cooldown mechanism（4 candles）
  - 保持 volatility_regime gate
  - Mode affinity: `mode = "BURST"`
- [ ] 1.3 Register in `main.py` — `StrategyRegistry.register(BurstStrategy())`
  - Default: `enabled: False`（同 squeeze 一樣 opt-in）
- [ ] 1.4 Add per-coin burst config to `config/coins/*/config.py`
- [ ] 1.5 Backtest 驗證：new burst_strategy.py vs old bt_burst_strategy.py → identical signals

**Acceptance**: burst strategy 可以喺 trader_cycle 跑（disabled by default），backtest 結果 identical

---

## Phase 2: Event-Driven Volume Spike Trigger
> Status: pending
> Files: indicator_engine.py, NEW scripts/signals/trigger.py
> Depends: Phase 0

**Why**: 1H close 只捉到 56% explosive move。Real-time volume projection 可以捉到 85-90%。

**Architecture**:
```
ws_manager (已有)
  └─ xadd("market:klines", {is_closed: "0"/"1"})

indicator_engine.py (改動)
  ├─ is_closed == "1" → full indicator recalc (不變)
  └─ is_closed == "0" → NEW: _monitor_volume_projection()
       ├─ Per-symbol state: { candle_open_time, accumulated_vol, avg_vol_30 }
       ├─ elapsed_pct = (now - candle_open_time) / interval_duration
       ├─ projected = signals.volume.project_volume(acc_vol, elapsed_pct, avg_30)
       ├─ IF projected > 5.0 AND squeeze_ready[symbol]:
       │    xadd("market:vol_trigger", {symbol, projected_ratio, direction, squeeze_conf})
       └─ Debounce: 每個 candle 每個 symbol 最多 trigger 1 次

trader_cycle (Phase 2A — minimal change)
  └─ Pipeline start: xread("market:vol_trigger", count=10)
     → If trigger found → evaluate squeeze/burst strategy immediately
     → 唔使等 15min cycle

Phase 2B (future — 唔做住)
  └─ Dedicated fast listener daemon（copy signal_engine.py 100ms pattern）
```

**Steps**:
- [ ] 2.1 Add `_squeeze_ready: dict[str, bool]` state to indicator_engine
  - Updated on every 1H close: `bb_width_pctl < 30 AND adx < 25`
  - Per-symbol tracking
- [ ] 2.2 Modify `_redis_consumer_loop()` — 目前 filter `is_closed == "1"` only
  - Add path for `is_closed == "0"`: call `_monitor_volume_projection(symbol, tf, fields)`
  - Only for 1H klines（3m/15m 太 noisy，4H 太慢）
- [ ] 2.3 Implement `_monitor_volume_projection()`:
  - Use `signals.volume.project_volume()`
  - Track per-symbol state dict: `{open_time, acc_vol, triggered_this_candle}`
  - Threshold: `projected_ratio > 5.0` AND `squeeze_ready == True`
  - xadd to `market:vol_trigger` stream（maxlen=100）
- [ ] 2.4 Add `taker_buy_volume` to ws_manager normalization
  - Binance kline data 有 `k["V"]`（taker buy vol）但目前 zeroed out (ws_manager line 370)
  - 加入 → 可以計 buy/sell imbalance for direction
- [ ] 2.5 trader_cycle pipeline: add `_check_vol_triggers()` at start
  - xread `market:vol_trigger` → if found, fast-path evaluate
  - 唔改 15min cycle timing，只係 cycle 開始時多 check 一個 stream

**Acceptance**:
- indicator_engine 收到 open kline 時計 projected volume
- Squeeze ready + volume spike → trigger 寫入 Redis
- trader_cycle 下次 cycle 讀到 trigger 並 evaluate

---

## Phase 3: Tool Unification
> Status: pending
> Files: NEW `cli/`, modifications to backtest/, scripts/
> Depends: Phase 0-1（strategy sharing 先做完）

**Why**: 57 scripts 散落，4 個 squeeze backtest 幾乎 copy-paste，3 個 grid search 做類似嘅事。

**Sub-phases**:

### 3A: Squeeze Backtest Consolidation
- [ ] 3A.1 Merge 4 squeeze backtests → 1 parameterized `backtest/bt_squeeze.py`
  - `--coin BTC/ETH/SOL` arg
  - `--days 360` arg
  - Per-coin params from `config/coins/`
  - Delete: bt_squeeze_btc_360d.py, bt_squeeze_eth_360d.py, bt_squeeze_sol_360d.py, bt_squeeze_sol_360d_v2.py

### 3B: Backtest CLI Entry Point
- [ ] 3B.1 Create `cli/__init__.py` + `cli/backtest.py` (click/argparse)
  ```
  python3 -m cli.backtest run --strategy squeeze --coin BTC --days 360
  python3 -m cli.backtest run --strategy burst --coin ETH --days 180
  python3 -m cli.backtest sweep --strategy squeeze --params bb_pctl,adx
  python3 -m cli.backtest compare --configs A,B --coins BTC,ETH
  ```
- [ ] 3B.2 Wire up existing backtest engine behind CLI

### 3C: Strategy Sharing（Live ↔ Backtest）
- [ ] 3C.1 Backtest engine imports strategies from `scripts/trader_cycle/strategies/`
  - 而家 backtest/strategies/ 同 scripts/trader_cycle/strategies/ 各有一份
  - Target: backtest engine 用 live strategy class + thin adapter
  - bt_burst_strategy.py → adapter wrapping burst_strategy.py
- [ ] 3C.2 Remove backtest-only strategy duplicates（keep adapters only）

### 3D: Dead Code Cleanup
- [ ] 3D.1 Archive old dashboard → `.archive/dashboard_v1/`（if dashboard_ng is live）
- [ ] 3D.2 Archive superseded files:
  - `analysis/lampstore_analysis.py`, `analysis/lampstore_deep.py`（_final.py is authoritative）
  - `polymarket/backtest/mm_backtest.py`, `mm_backtest_v3.py`（v4 supersedes）
  - `analysis/ladder_backtest_1h.py`（duplicate of backtest/ladder_backtest.py）
- [ ] 3D.3 Confirm no imports reference archived files → grep verify

**Acceptance**: `python3 -m cli.backtest run --strategy squeeze --coin BTC` works end-to-end

---

## Phase 4: Integration Test + Paper Trade
> Status: pending
> Depends: Phase 0-2

### 4A: Multi-coin Integration Test
- [ ] 4A.1 Restart ws_manager → verify 25 streams connect
- [ ] 4A.2 Restart indicator_engine → verify 5-coin backfill + cache write
- [ ] 4A.3 Verify volume trigger pipeline: ws → indicator_engine → Redis trigger → trader_cycle reads
- [ ] 4A.4 Dry-run trader_cycle → verify squeeze + burst signals detected（唔 execute）

### 4B: Paper Trade
- [ ] 4B.1 Enable squeeze strategy in paper mode
- [ ] 4B.2 Enable burst strategy in paper mode
- [ ] 4B.3 Collect 50+ signals → compare WR vs backtest
- [ ] 4B.4 Specifically validate: session edge（BTC/ETH Non-US vs US）

### 4C: Remaining Fixes
- [ ] 4C.1 `position_sizer._adjust_tp_for_funding()` — add squeeze/burst case
- [ ] 4C.2 `get_session_tag` import location fix
- [ ] 4C.3 `BB_TOUCH_TOL_XRP` dead import cleanup

**Acceptance**: 50+ paper signals collected, WR within 5% of backtest expectation

---

## Phase Execution Order
```
Phase 0 (Signal Module)     ← 基礎，所有嘢 depend on this
    ↓
Phase 1 (Burst → Prod)     ← 需要 signal module
Phase 2 (Event Trigger)    ← 需要 signal module，可以同 Phase 1 parallel
    ↓
Phase 3 (Tool Unification) ← 需要 Phase 0-1 done（strategy sharing）
    ↓
Phase 4 (Integration + Paper) ← 需要全部 done
```

## Decisions
| Decision | Rationale |
|----------|-----------|
| Option B: 兩個獨立 strategy + 共享 signal module | 訊號明確不同，分開方便後續修改，避免撈亂 |
| Signal module 放 scripts/signals/ | 既非 strategy 亦非 infra，獨立 namespace |
| Burst 要搬入 production | 目前只喺 backtest，冇理由分開維護 |
| Event trigger 用 Phase 2A（trader_cycle check）先 | 最少改動驗證概念，Phase 2B（fast listener）等 paper data 再決定 |
| Tool unification 排 Phase 3 | 功能先行，清理之後 |
| 只 monitor 1H open klines for volume projection | 3m 太 noisy，15m 可能漏，1H 同 strategy timeframe match |

## Errors
| Error | Cause | Fix |
|-------|-------|-----|
| (none yet) | | |

## Risks
| Risk | Mitigation |
|------|-----------|
| Signal module refactor 改變 output | Before/after comparison on same data |
| prev_close 加入 indicator_calc 影響其他 strategy | 新 field，唔覆蓋現有 — zero risk |
| Volume projection false positive（candle 初段 volume spike 但最終 normal） | Debounce: elapsed_pct > 30% 先計算 + 每 candle 只 trigger 1 次 |
| Tool unification 打破 import paths | Phase 3C 用 adapter pattern，唔刪原 file 住 |
