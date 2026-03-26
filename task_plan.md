# Task Plan: Split run_1h_live.py + run_5m_live.py
> Created: 2026-03-26
> Goal: 拆 2 個 bot entry point 做 modular 架構，同 mm/ pattern 一致

## Risk Legend
- 🔴 **2CHECK** — 涉及交易邏輯 / 資金流 / 落單
- 🟡 **VERIFY** — 涉及狀態讀寫 / 信號計算 / import 路徑
- 🟢 **SAFE** — 純常數 / 純 logging
- ⚠️ **DOWNSTREAM** — 後續改動可能影響呢個位，需要回頭檢查

## Key Decision: Copy vs Share
7 個 state management functions (`_load`, `_save`, `_to_dict`, `_from_dict`, `_log_trade`, `_log_order`, `_bump_fill`) 喺 3 個 bot 完全一樣。
**Decision: Copy first, share later。** 原因：
1. $106 + $35 real loss history from shared-code changes
2. Copy 零 risk，share 需要改 mm/ 嘅 import
3. Share 可以做 follow-on refactor（建 `polymarket/shared/bot_state.py`）

---

## Phase 3: run_1h_live.py (2,069 → ~250 orchestrator + 8 modules) — `pending`

```
polymarket/
├── run_1h_live.py           ← thin orchestrator (~250 lines)
├── conv_1h/
│   ├── __init__.py
│   ├── constants.py         ← 28 constants (~50 lines) 🟢
│   ├── state_io.py          ← load/save/log/bump_fill/to_dict/from_dict + signal_tape (~120 lines) 🟡
│   ├── data_feeds.py        ← btc_price/holder_imbalance/vol_imbalance/poly_mid/poly_ob (~250 lines) 🟡
│   ├── analysis.py          ← _collect_analysis (~110 lines) 🟢
│   ├── paper_trading.py     ← paper_enter/resolve/status (~130 lines) 🟡
│   ├── order_lifecycle.py   ← execute_order/check_fills/check_fills_paper/reprice_1h (~520 lines) 🔴
│   ├── exit_logic.py        ← try_sell_partial/check_black_swan/check_resolutions (~150 lines, NON-CONTIGUOUS) 🔴
│   └── discovery.py         ← build_slug/discover (~45 lines) 🟢
```

### Step 3.1: constants.py — 🟢 SAFE
所有 module-level constants。

### Step 3.2: state_io.py — 🟡 VERIFY
- `_load`, `_save`, `_to_dict`, `_from_dict`, `_log_trade`, `_log_order`, `_bump_fill`
- `_record_signal_tape`（1H 專用，write to signal_tape_1h.jsonl）
- ⚠️ **DOWNSTREAM**：如果日後建 shared bot_state.py，呢度要改 import

### Step 3.3: data_feeds.py — 🟡 VERIFY
- `_get_json`, `_btc_price`, `_binance_open`, `_vol_1m`, `_poly_midpoint`, `_poly_ob`
- `_vol_imbalance`, `_holder_imbalance`
- ⚠️ **DOWNSTREAM**：`_btc_price`, `_vol_1m`, `_poly_midpoint` 同 mm/data_feeds.py 幾乎一樣 → share candidate

### Step 3.4: analysis.py — 🟢 SAFE
- `_collect_analysis`（pure data collection to jsonl, zero trading impact）

### Step 3.5: paper_trading.py — 🟡 VERIFY
- `_paper_enter`, `_paper_resolve`, `_paper_status`
- ⚠️ **DOWNSTREAM**：paper resolve 嘅 PnL 計算邏輯如果改咗 → 會影響 paper tracking accuracy

### Step 3.6: order_lifecycle.py — 🔴 2CHECK
- `_execute_order` — 直接落單
- `_check_fills` — fill confirmation，更新 shares/cost
- `_check_fills_paper` — paper fill simulation
- `_reprice_1h` (~270 lines) — 最複雜：conviction-driven repricing
  - 🔴 Cancel-safety checks（WS pre-check, phantom fill recovery）
  - 🔴 `_repricing_cid` guard（prevent concurrent reprice）
  - ⚠️ **DOWNSTREAM**：reprice 邏輯同 mm/ 嘅唔同（1H 用 conviction signal，mm 用 OB mid drift）

### Step 3.7: exit_logic.py — 🔴 2CHECK
- `_try_sell_partial` — 實際賣出 shares
- `_check_black_swan` — mid ≥ 95¢ 觸發 sell + hedge
  - 🔴 注意：1H `_BLACK_SWAN_MID = 0.95`，mm 用 `0.96` — 有意唔同
- `_check_resolutions` — PnL 結算
  - ⚠️ **DOWNSTREAM**：resolution 邏輯同 mm/ 類似但用 1H kline（唔係 15M）

### Step 3.8: discovery.py — 🟢 SAFE
- `_build_slug`, `_discover`（1H 專用 slug format）

### Step 3.9: Slim run_1h_live.py — 🟡 VERIFY
- `run_cycle` dispatcher + `_status` + `main`
- ⚠️ **DOWNSTREAM**：run_cycle 嘅 call 順序必須同原版一致

### Step 3.10: Test — 🔴 2CHECK

---

## Phase 4: run_5m_live.py (1,660 → ~300 orchestrator + 5 modules) — `in_progress`

```
polymarket/
├── run_5m_live.py           ← thin orchestrator (~300 lines)
├── mom_5m/
│   ├── __init__.py
│   ├── config.py            ← CoinConfig + COIN_CONFIG + constants + _tier_lean_ratio + TG (~115 lines) 🟢
│   ├── data.py              ← _get_json + coin_price/open_at/vol_1m/taker_ratio/midpoint + 4 caches (~120 lines) 🟡
│   ├── signal.py            ← w4_signal + discover_5m (~100 lines) 🟡
│   ├── execution.py         ← w4_entry(200L)/execute_order/check_fills/cancel_before_end/check_profit_lock (~500 lines) 🔴
│   └── state.py             ← load/save/to_dict/from_dict/log_trade/log_order/bump_fill/kill_switches/check_resolutions (~200 lines) 🟡
```

### Step 4.1: config.py — 🟢 SAFE
- CoinConfig dataclass + COIN_CONFIG dict + _LEAN_TIERS + _tier_lean_ratio
- All path/URL/timing constants + _FILL_STATS_DEFAULT
- TG credential loader + _tg_alert
- _HKT timezone, _COIN_SYMBOLS

### Step 4.2: data.py — 🟡 VERIFY
- `_get_json`, `_coin_price`, `_open_at`, `_vol_1m`, `_binance_taker_ratio`, `_poly_midpoint`
- 4 mutable caches: _price_cache, _vol_cache, _open_cache, _taker_cache
- ⚠️ `_coin_price` + `_poly_midpoint` use WS globals (`_ws_binance`/`_ws_poly`)
  Decision: pass WS as optional param (same pattern as conv_1h/data_feeds.py)

### Step 4.3: signal.py — 🟡 VERIFY
- `_w4_signal` (37L): calls _open_at, _coin_price from data.py
- `_discover_5m` (59L): calls _get_json from data.py + GammaClient

### Step 4.4: execution.py — 🔴 2CHECK
- `_w4_entry` (200L) — calls signal + data + execution functions
  ⚠️ Returns Union[None, "EXPIRED", "ABORT", "VETO", "DEAD_HOUR", list[dict]] — implicit protocol
- `_execute_order` (53L) — direct order placement
- `_check_fills` (93L) — fill confirmation
- `_cancel_before_end` (34L) — cancel near window end
- `_check_profit_lock` (107L) — mid ≥ 99¢ sell
  🔴 `_PROFIT_LOCK_MID = 0.99` (5M) vs 0.96 (mm) vs 0.95 (1H) — intentional

### Step 4.5: state.py — 🟡 VERIFY
- `_bump_fill`, `_load`, `_default_state`, `_save`, `_to_dict`, `_from_dict`
- `_log_trade`, `_log_order` — CHECK: per-coin paths like conv_1h
- `_check_kill_switches` (26L) — daily loss / consecutive loss / cooldown
- `_check_resolutions` (111L) — straddles state+data (calls _get_json + resolve_market)
  Decision: keep in state.py, import _get_json from data.py

### Step 4.6: Slim run_5m_live.py — 🟡 VERIFY
- `run_cycle` (195L), `_status` (49L), `main` (206L), `_shutdown` (4L)
- import-as aliases (same pattern as Phase 3 conv_1h)

### Step 4.7: Test — 🔴 2CHECK

---

## Quick Fixes (done in this round)
- [x] A.4: Python path hardcode → env var (4 files)
- [x] Dead code archive: signal_engine.py, ob_recorder.py, polymarket/config/params.py → .archive/

## ⚠️ DOWNSTREAM Markers（後續需要回頭檢查）
| Marker | Location | Trigger | Check When |
|--------|----------|---------|------------|
| D1 | conv_1h/state_io.py | 建 shared bot_state.py | Phase 3+4 完成後 |
| D2 | conv_1h/data_feeds.py | 建 shared data_feeds.py | Phase 3+4 完成後 |
| D3 | conv_1h/exit_logic.py `_BLACK_SWAN_MID=0.95` | mm 改 exit threshold | 任何 exit 改動時 |
| D4 | mom_5m/execution.py `_PROFIT_LOCK_MID=0.99` | mm 改 exit threshold | 任何 exit 改動時 |
| D5 | 全部 bot 嘅 `_to_dict` field list | MMMarketState 加新 field | dataclass 改動時 |
| D6 | mm/entry_logic.py `_TICK=0.02` | 50 場 data 返嚟 | ~2-3 天後 |

## Errors
| # | Phase | Error | Resolution |
|---|-------|-------|------------|

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| 1 | Copy state functions，唔 share | $106+$35 loss history，safety first |
| 2 | 每個 bot 獨立 subfolder | 唔互相污染 |
| 3 | 保留唔同 exit thresholds | 各 bot 有意用唔同值（0.95/0.96/0.99） |
| 4 | Reprice 邏輯唔 share | 1H conviction vs mm OB drift，根本唔同 |
| 5 | WS globals → optional params | _ws_binance/_ws_poly 由 orchestrator 注入，module 唔 own global |
| 6 | _check_resolutions 放 state.py | 雖然 call data layer，但主要 mutate state；import _get_json from data |
