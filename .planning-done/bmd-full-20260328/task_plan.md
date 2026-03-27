# Task Plan: AXC BMD 修正 — Startup Validation + Pipeline Hardening
> Created: 2026-03-28
> Goal: 修復 BMD 發現嘅 3 致命 + 5 嚴重問題，加 guardrail 防止改動靜默炸全線

## Risk Legend
- 💀 **FATAL** — 改錯 = 真金白銀損失 / 執行錯 coin / state 損壞
- 🔴 **2CHECK** — 涉及交易邏輯 / 落單 / 資金流 / 依賴鏈
- 🟡 **VERIFY** — 行為改變，需要 test 確認
- 🟢 **SAFE** — 純文檔 / additive / 唔影響 runtime

---

## Current Phase
Phase 0 ✅ → Phase 1 ✅ → Phase 2 ✅ → Phase 2b ✅ → Phase 3 ✅ partial → **Phase 4 (Remaining fixes)**

---

## Phase 0: Startup Validation（💀 致命修復）

### Step 1: 建 validate.py — `COMPLETE` 🟡
**File:** `polymarket/mm/validate.py` (NEW)
- [ ] validate_constants(): coin set lowercase + BET_PCT match + known coin gate
- [ ] validate_params(): TIMEFRAME_PARAMS 4 keys × 13 sub-keys × numeric
- [ ] validate_state_paths(): logs dir exists
- [ ] run_all(): 集中入口

⚠️ **容易錯 #1**: import 必須 function-level（config.params 依賴 sys.path）
⚠️ **容易錯 #2**: 用 `constants._LIVE_TRADE_COINS`（module ref），唔好 `from ... import`
⚠️ **容易錯 #3**: validate_params 嘅 required_sub_keys set 必須同 params.py 完全一致

### Step 2: 修 state_io.py bankroll warning — `COMPLETE` 💀
**File:** `polymarket/mm/state_io.py`
- [ ] 加 logger
- [ ] load() success path 加 bankroll < MIN check → log.warning
- [ ] 唔改 except Exception 行為（P0 scope 外）

⚠️ **容易錯 #4**: warning 位置要喺 json.load 成功之後、return 之前
⚠️ **容易錯 #5**: bankroll 預設值 100.0 可能係合法初始值 — threshold 唔好設太高

### Step 3: 修 grid_search.py mutation BUG — `COMPLETE` 🔴
**File:** `backtest/grid_search.py:209-212`
- [ ] 改成 `.copy()` + try/finally restore
- [ ] 驗證唔影響 grid search 結果

⚠️ **容易錯 #6**: grid_search 仲有其他 monkey-patch（_ts, _cs, _sq, _ic）— P0 只修 TIMEFRAME_PARAMS
⚠️ **容易錯 #7**: 如果用 ProcessPoolExecutor，copy 要喺 worker 入面做（唔係 parent）

### Step 4: 修 4h_indicator_backtest.py reference leak — `COMPLETE` 🟡
**File:** `polymarket/analysis/4h_indicator_backtest.py:133`
- [ ] 加 `.copy()`

### Step 5: Wire validation into run_mm_live.py — `COMPLETE` 💀
**File:** `polymarket/run_mm_live.py` main()
- [ ] import validate
- [ ] call run_all() in main()
- [ ] fail fast with sys.exit(1)

⚠️ **容易錯 #8**: 必須放 logging.basicConfig() 之後、args.status 之前
⚠️ **容易錯 #9**: --status mode 都要 validate（否則 status 報告基於 corrupt state）
⚠️ **容易錯 #10**: 其他 runner (run_1h, run_4h, run_5m, run_daily) 都應該 validate — 但 P0 只做 MM

### Step 6: 建 test — `COMPLETE` 🟡 (14/14 passed)
**File:** `tests/test_mm_validate.py` (NEW)
- [ ] test happy path
- [ ] test 壞 coin set → raise
- [ ] test 缺 TIMEFRAME key → raise
- [ ] test bankroll warning

---

## Phase 1: 統一文檔（🟢 SAFE）

### Step 7: Fix model tier docs — `COMPLETE` 🟢
**Files:** CLAUDE.md, docs/architecture/AGENTS.md, agents/main/workspace/SOUL.md
- [ ] 統一 tier 定義
- [ ] heartbeat 標明 No LLM
- [ ] 更新 dates
- [ ] polymarket/CLAUDE.md:26 更新 _LIVE_TRADE_COINS 值

---

## Phase 2: SharedWSManager + Validation Wiring + Utility Extract（🔴 重構）

> ❌ **BaseRunner DESCOPED** — Scout 發現 5 runners 差異太大（WS/State/Fuse/Signal/TG/Loop 全唔同）。
> 強行抽 base class = 牽一髮動全身。改為只抽工具函數 + wire validation。

### Step 8: SharedWSManager — `COMPLETE` 🔴
**File:** `polymarket/data/ws_shared.py` (NEW)
**Scope:** 只 share BinancePriceFeed + PolymarketBookFeed（ws_user 已 singleton 唔使改）
- [ ] Lazy singleton: 第一個 caller create, 後續 reuse
- [ ] Refcount lifecycle: stop 只有 last user disconnect 先觸發
- [ ] Wire MM → 1H → 5M（4H/Daily 冇 WS，唔使改）
- [ ] PolymarketBookFeed subscribe race handling

⚠️ **容易錯 #11**: reconnect logic 全部保留 — ws_binance 有 23h preemptive reconnect
⚠️ **容易錯 #12**: runner crash 唔可以斷其他 — refcount decrement 喺 finally block
⚠️ **容易錯 #16**: PolymarketBookFeed.subscribe() 觸發 reconnect → clears _data/_books → 其他 runner 短暫 data gap
⚠️ **容易錯 #17**: set_ws_feeds() injection — conv_1h/data_feeds.py 同 mom_5m/data.py 各有 module-level ref

### Step 9: Wire validation into 1H/5M/4H/Daily runners — `COMPLETE` 💀
**Files:** 4 個 run_*_live.py
- [ ] 加 `from polymarket.mm.validate import run_all` + startup call
- [ ] 位置：logging.basicConfig() 之後、trading logic 之前

⚠️ **容易錯 #18**: 4H/Daily 冇 `from polymarket.mm import` — validate import path 要 check sys.path
⚠️ **容易錯 #19**: 4H/Daily 嘅 argparse 用 `parser`（唔係 `ap`），插入位置唔同

### Step 10: Extract shared utilities — `DESCOPED` 🟡
**File:** `polymarket/utils/runner_helpers.py` (NEW)
- [ ] `setup_argparse()` — common flags
- [ ] `setup_logging(verbose)` — 統一 format
- [ ] `make_mock_client(prefix)` — paper _Mock class

⚠️ **容易錯 #20**: 抽完 utility 唔好刪 runner 入面嘅原有 code — 先 wire 新 code 再 deprecate 舊 code

---

## Phase 3: State Migration + Async HTTP（🟡 性能優化）

### Step 10b: State migration layer — `COMPLETE` 💀
**File:** `polymarket/mm/state_io.py` (extend)
- [ ] schema version field
- [ ] migration functions (v0 → v1)

⚠️ **容易錯 #15**: migration 失敗要 fallback default，唔好 crash loop

### Step 11: async HTTP / subprocess→module — `PARTIAL` 🟡
- [x] indicator_calc.py importable (direct import, subprocess removed)
- [ ] SharedHTTPClient — DEFERRED (needs aiohttp dep)

---

## Phase 4: Remaining Fixes（2check + regression 發現）

### Step 12: Fix 1H + 4H orphan cancel — `COMPLETE` 💀
**File:** `polymarket/run_1h_live.py`
- [ ] 加 CID filter（同 run_mm_live.py 一致）— 只 cancel 自己嘅 orders
- [ ] 確認 1H state 嘅 market keys 格式同 Poly order market field 一致

⚠️ **容易錯 #21**: 1H state key 格式可能同 MM 唔同（condition_id vs token_id）
⚠️ **容易錯 #22**: 如果 filter 太 strict，orphan orders 永遠唔被 cancel → 資金卡住
💀 **真金白銀**: 冇 filter = 1H startup 殺 MM 嘅 live orders → MM 以為 order 仲在但實際已 cancel

### Step 13: Fix pre-existing test failures — `COMPLETE` 🟡
**File:** `tests/test_regime_risk.py`
- [ ] test_zone_a_loads: expects margin_pct=0.03, actual=0.25
- [ ] test_zone_b_loads: expects margin_pct=0.03, actual=0.25
- [ ] 確認 config/profiles/zone_a.py 同 zone_b.py 嘅值邊個啱

⚠️ **容易錯 #23**: 可能係 test 過時（profile 值已改），唔好改 profile 去 fit test

### Step 14: Fix grid_search monkey-patch leaks — `COMPLETE` 🟡
**File:** `backtest/grid_search.py`
- [ ] _ts, _cs, _sq, _ic module globals 嘅 mutation 加 try/finally restore
- [ ] 同 TIMEFRAME_PARAMS fix 一樣 pattern

⚠️ **容易錯 #24**: 部分 monkey-patch 係 ProcessPoolExecutor worker 獨立 process，restore 可能冇意義但 still good practice

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| 1 | validate.py 獨立新文件 | 唔污染現有 import chain，可獨立 test |
| 2 | function-level import inside validate.py | 避免 sys.path 依賴問題 |
| 3 | P0 只 wire MM runner | 降低 blast radius，成功後再擴展到其他 runner |
| 4 | bankroll check 用 warning 唔用 abort | 初始 100.0 可能係合法值，唔好 block startup |
| 5 | grid_search fix 用 .copy() + try/finally | 最小改動，唔重構 worker 結構 |
| 6 | BaseRunner DESCOPED | Scout 發現 5 runners 差異太大（WS/State/Fuse/Signal 全唔同），強行抽 = 高風險低回報 |
| 7 | SharedWSManager 只 share Binance+PolyBook | ws_user 已 singleton（只 MM 用），唔使改 |
| 8 | 4H/Daily 冇 WS，唔使入 SharedWSManager | 佢哋用 REST _get_json()，唔同 data path |

## Errors
| # | Phase | Error | Resolution |
|---|-------|-------|------------|

## ⚠️ 容易錯總覽（2check 清單）
| # | 位置 | 風險 | Phase | Status |
|---|------|------|-------|--------|
| 1 | validate.py import level | config.params 依賴 sys.path | P0 | ✅ |
| 2 | validate.py module ref vs from-import | monkeypatch test 唔 work | P0 | ✅ |
| 3 | validate_params sub-keys 要完全一致 | 漏一個 = false negative | P0 | ✅ |
| 4 | state_io warning 位置 | json.load 之後 return 之前 | P0 | ✅ |
| 5 | bankroll threshold 唔好太高 | 100.0 可能合法 | P0 | ✅ |
| 6 | grid_search 其他 monkey-patch | P0 只修 TIMEFRAME | P0 | ✅ noted |
| 7 | ProcessPoolExecutor copy 位置 | worker 入面做唔係 parent | P0 | ✅ |
| 8 | run_mm_live validation 位置 | logging 之後 status 之前 | P0 | ✅ |
| 9 | --status mode 嘅 validation | status 報告 corrupt state | P0 | ✅ |
| 10 | 其他 runner 未 wire | P0 只做 MM → P2-Step9 | P0→P2 | pending |
| 11 | WS reconnect logic 保留 | 23h preemptive reconnect | P2-Step8 | pending |
| 12 | 共用 WS crash isolation | refcount 喺 finally block | P2-Step8 | pending |
| 13 | ~~run_4h vs run_daily TG 差異~~ | ~~BaseRunner descoped~~ | ~~P2~~ | ❌ N/A |
| 14 | ~~LaunchAgent plist entry point~~ | ~~BaseRunner descoped~~ | ~~P2~~ | ❌ N/A |
| 15 | State migration fallback | 失敗要 default 唔好 loop | P3 | pending |
| 16 | PolyBookFeed subscribe race | reconnect clears _data → brief gap | P2-Step8 | pending |
| 17 | set_ws_feeds() module-level ref | conv_1h + mom_5m 各有自己嘅 ref | P2-Step8 | pending |
| 18 | 4H/Daily validate import path | 冇 `from polymarket.mm` 慣例 | P2-Step9 | pending |
| 19 | 4H/Daily argparse 變量名 | `parser` 唔係 `ap` | P2-Step9 | pending |
| 20 | Utility extract 唔好刪原有 code | 先 wire 新再 deprecate 舊 | P2-Step10 | pending |
