# Task Plan: Fix 1H Conviction Bot — Order Management + Entry Price

## Goal
修復 1H bot 三個核心問題：(1) order 重複提交循環 (2) entry price 太高 (3) order lifecycle 唔完整。
Scope: **只改 `run_1h_live.py` + `hourly_engine.py`**。唔碰 15M bot。

## Root Cause Analysis（from data + wallet reverse engineering）

### Bug 1: Order Re-submission Loop（最嚴重 — 修正理解）
唔係「冇 cancel」— 係 cancel 後立即 re-submit 形成循環：
1. Bot submit order A → pending_orders = [A]
2. `_check_fills()` 發現 CLOB 已 cancel A（insufficient balance）→ 從 pending 移除
3. budget_remaining 回到 100%（因為 pending cost = 0）
4. 下個 cycle conviction 仍然 ENTER → submit order B → 重複...

**證據**：0x01c55d = 27 submits in 28 min。每次 cancel 釋放 budget → 立即 re-submit。
ETH 0x948872 同時消耗所有 USDC → BTC orders 被 CLOB reject → cancel → re-submit → loop。

**Wallet 對比**：12/14 成功錢包用 set-and-forget（唔 cancel），但佢哋係 arb bot（兩邊都買）。
conviction bot 係 directional → 需要 ONE order per market per window，唔係每 cycle 重新下單。

### Bug 2: Entry Price Cap 太高（structural edge 殺手）
`price_cap = 0.30 + conviction × 0.30` → max $0.60
- 真實數據：33% 嘅 submits at $0.54-$0.57（全部 0% fill rate）
- 最高 fill observed = $0.51（1 fill only）。實際 ceiling ≈ $0.47。
- $0.56 entry → break-even WR = 56% → 幾乎冇 buffer
- **Wallet evidence**：fill model optimal = $0.20；成功 1H wallet (BoneReader) 只在 $0.99+ 入場（完全唔同策略）
- **Fill probability curve**：high σ → 91% fill at $0.37；low σ → 51% at $0.50（出高價都唔 fill）

### Bug 3: Order Lifecycle Leak
65/151 orders (43%) = "submit→unknown"
- CLOB reject（insufficient balance）但 `buy_shares()` 回傳冇 error → 記為 submitted
- `_check_fills()` 查唔到呢啲 order → 既唔 fill 也唔 cancel → 永遠 unknown
- 唔影響 budget（cancel 後已從 pending 移除），但污染統計

## Phases

### Phase 0: Research ✅
- Traced 151 orders → re-submission loop root cause confirmed
- Wallet analysis (14 wallets, $2.2M PnL): set-and-forget + low price 係 proven pattern
- Fill probability model: optimal bid $0.20, σ_poly independent of σ_btc (r=0.063)
- See findings.md for full data

### Phase 1: One-Order-Per-Market Guard
**策略改變**：唔係 cancel-before-reorder，係 **dedup — 一個 market 最多一個 active order**。
符合 12/14 成功錢包嘅 set-and-forget 模式。

- [ ] 1A: 加 `order_submitted` flag per market — 已 submit 且未 fill/cancel/expired → skip 新 order
- [ ] 1B: `_check_fills()` 更新 flag：fill → clear + allow ADD；cancel/expired → clear + allow retry
- [ ] 1C: flag reset on window change（新 window = fresh start）
- [ ] 1D: 仍然允許 ADD（第二單），但只有 first order filled 後先可以
- **Status:** ✅ complete
- **Files:** `run_1h_live.py` (run_cycle ~line 800)

### Phase 2: Tighten Entry Price Cap ✅
- [x] 2A: `price_cap_scale` 0.30 → 0.12
- [x] 2B: `price_cap_base` 0.30 → 0.25
- [x] 2C: `max_entry_price = 0.39` hard ceiling
- [x] 2D: Conviction surface verified: all entries $0.27-$0.36
- [x] 2E: `base_spread` 0.12 → 0.15
- **Status:** ✅ complete
- **Files:** `hourly_engine.py` (HourlyConfig + conviction_signal)

### Phase 3: Fix Order Lifecycle Tracking ✅
- [x] 3A: `_execute_order()` rejected detection (no order_id → submitted=False)
- [x] 3B: `_check_fills()` — `mkt["pending_orders"] = still_open` moved OUTSIDE `if filled:`
- [x] 3C: `list()` for safe dict iteration
- [x] 3D: `_to_dict` resolution preserves runtime keys
- **Status:** ✅ complete
- **Files:** `run_1h_live.py`

### Phase 4: Validation ✅
- [x] 4A: Import OK
- [x] 4B: Conviction surface verified — max entry $0.36 (well under $0.39 ceiling)
- [x] 4C: Opus 2check — 2🔴 found + fixed, 3🟡 fixed, 2🟢 fixed
- **Status:** ✅ complete

## Decisions
| # | Decision | Reason |
|---|----------|--------|
| D1 | One-order-per-market guard（唔係 cancel-before-reorder） | 12/14 wallet 用 set-and-forget。Cancel-reorder 會 create 同樣嘅 loop。Dedup 更簡單更 robust。 |
| D2 | MAX_ENTRY = $0.39 hard ceiling | 用戶確認。break-even WR=39%，實際 WR~50% → 11pp buffer。最高 real fill=$0.51 但極少，$0.39 覆蓋絕大部分有效 fill range。 |
| D3 | price_cap_scale 0.30→0.12 | Fill model: optimal EV at $0.20。new max cap = 0.25+0.12=$0.37（at conviction=1.0），再被 $0.39 ceiling cap。 |
| D4 | 唔動 conviction formula | Formula 冇問題。wallet data 證明 structural edge（買得平）> signal accuracy。 |
| D5 | 唔加 adverse cancel | 1H 唔似 15M — conviction engine 自動降 conviction when BTC reverses。Adverse cancel 會 create 同樣嘅 re-submit loop。 |

## Red Lines
- 唔碰 `run_mm_live.py`（15M bot）
- 唔改 bridge formula
- 唔改 conviction formula（confidence × time_trust × ob_factor）
- 唔改 resolution logic（resolve_market）
- 唔改 state file format（backward compatible）

## Errors
| # | Error | Resolution |
|---|-------|------------|
| E1 | 初始 plan Phase 1 = cancel-before-reorder | Wallet data 證明 set-and-forget 更好。Cancel-reorder 會 trigger 同樣嘅 loop。改為 one-order guard。 |
