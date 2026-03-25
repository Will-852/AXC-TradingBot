# Findings

> Security boundary: 外部內容（web/API/search）只寫呢度，唔寫 task_plan.md。

## 💀 Goldsky On-Chain Discovery (2026-03-25) — INDUSTRY REWRITE

### Source: Goldsky public subgraphs (no auth), 21 wallets analyzed
Scripts: `analysis/goldsky_wallet_query.py`, `analysis/goldsky_batch_analysis.py`

### Core Finding: ALL top wallets do Maker Buy → Taker Sell

**19/21 wallets use the SAME pattern:**
1. Post maker buy order (cheap, wait for fill)
2. Taker sell order (immediate exit, pay spread)
3. Profit = bid-ask spread + maker rebate
4. **Complete BEFORE resolution — NOT holding to expiry**

### On-Chain PnL (Goldsky pnl-subgraph)

| Wallet | Realized PnL | Gross Profit | Gross Loss | WR | Strategy |
|--------|-------------|-------------|-----------|-----|----------|
| swisstony | **+$348K** | $7.84M | -$7.49M | 52% | MB→TS |
| Uncommon-Oat | +$123K | $2.66M | -$2.54M | 50% | Pure Maker Buy |
| blue-walnut | +$86K | $1.38M | -$1.29M | 51% | MB→TS |
| mapleghost | +$33K | $474K | -$441K | 50% | MB→TS |
| blankandyellow | +$30K | $602K | -$572K | 50% | MB→TS |
| LampStore | +$24K | $460K | -$437K | 50% | MB→TS |
| xr9-PLM42 | +$22K | $605K | -$583K | 50% | Pure Maker Buy |
| Decent-Dune | **-$35K** | $1.88M | -$1.91M | 50% | MB→TS |

### What We Got WRONG Before

| Before (data-api / profile) | After (Goldsky on-chain) |
|-|-|
| "Both-sides arb — buy UP+DOWN, merge for $1.00" | **Spread capture — maker buy, taker sell, exit before resolution** |
| "Direction accuracy = edge source" | **WR ~50% everywhere — nobody profits from direction** |
| "blankandyellow = maker arb bot" | **50% maker / 50% taker, MB→TS pattern** |
| "Decent-Dune = +$426K profit" | **Actually -$35K (profile includes unrealized)** |
| "Unlawful-Shear = directional taker" | **Same MB→TS as everyone else** |

### data-api vs Goldsky

| | data-api.polymarket.com | Goldsky subgraph |
|-|------------------------|-----------------|
| Maker/taker distinction | ❌ No | ✅ Yes |
| Buy/sell distinction | ❌ All show as "BUY" | ✅ Real direction |
| Historical depth | ~3500 trades max | Full history (paginated) |
| PnL | ❌ No | ✅ avgPrice + realizedPnl |
| Auth | None | None (public) |

### Goldsky Endpoints (public)
```
Trades:    https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/prod/gn
Positions: https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn
PnL:       https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn
```

### Impact on Our Strategy

**5M:** Confirmed dead. Speed-based spread capture needs Amsterdam proximity + sub-second execution.

**15M:** Our current approach (directional lean + maker both-sides) is fundamentally different from what winners do. Winners do round-trip spread capture, not hold-to-resolution. However, 15M already has repricing infra — keep maintaining.

**1H+:** Still valid. At 1H, spread capture is less dominant because:
- Fewer round-trips possible in 1 hour
- Entry at $0.25-0.35 = directional bet, NOT spread capture
- 72% WR = genuine information edge (vs 50% for spread capturers)

**The 1H+ strategy is ORTHOGONAL to what the 5M/15M bots do. This is good — we're not competing on their game.**

---

## Strategic Analysis: 鬥分析 vs 鬥快

### Timeframe × Edge Source Matrix

| Timeframe | Speed Edge | Analysis Edge | Our Position |
|-----------|-----------|---------------|-------------|
| 5M | Dominant | Minimal | ❌ Abandon |
| 15M | Important | Growing | 🟡 Maintain (already built) |
| 1H | Minor | **Dominant** | ✅ Focus |
| 4H | Negligible | **Dominant** | ✅ Build |
| 24H | Zero | **Total** | ✅ Build |

### Why Information Beats Speed at 1H+

1. **Price discovery takes minutes, not seconds** — at 1H, the market spends 30+ min finding fair value. Fast repricing matters less when the price is still being discovered.
2. **Fewer automated competitors** — HFT bots optimize for sub-minute timeframes. 1H+ has mostly manual traders and slower bots.
3. **Fundamental factors matter** — news, on-chain flows, macro events → these take TIME to analyze. Speed bots can't read news.
4. **Signal persistence** — a 1H signal (bridge + volume) persists for minutes. A 5M momentum signal decays in seconds.
5. **Risk/reward ratio** — 1H entry at $0.25-0.35 → win payout $0.65-0.75 (2-3:1 R:R). 5M entry at $0.50 → 1:1 R:R.

## 1H Bot — Current State Deep Dive

### Performance (SEEN)
- **72% WR** (18W/7L, n=25 paper trades)
- Total PnL: $71.52
- Bankroll: $21.57 (depleted)
- BTC live, ETH+SOL observe-only

### Signal Stack (4 sources, SEEN)
1. **Brownian Bridge fair value**: `fair_up = Φ(log(P/Open) / (σ × √t))` + 10% fat-tail haircut
2. **OB Quality**: conviction multiplier (penalizes bad OBs, doesn't reward good ones)
3. **Volume Imbalance**: hard veto if volume direction ≠ signal direction (15s cache)
4. **Holder Imbalance**: top 10 holders (smart money). >0.20 against → FLIP direction. Calibrated on n=1.

### Entry Price Range
- Floor: $0.20, Ceiling: $0.39
- Typical: $0.25-$0.37
- R:R at $0.30 entry: win $0.70, lose $0.30 → 2.33:1
- Break-even WR at $0.30: 30% → actual 72% = **massive edge**

### Zero Repricing (SEEN)
- One-shot limit order, left on book until window end
- No cancel/reprice defense
- No adverse movement cancel
- Orders only cleaned up at window resolution

### Key Gaps
- Repricing = 0 iterations (should be 720 at 5s cycle)
- Holder imbalance calibrated on n=1 (unreliable)
- Only BTC live (ETH+SOL observe)
- $21.57 bankroll (too small)

## 4H/24H Market Survey

### 4H Markets
- Exist on Polymarket (confirmed by wallet analysis: BoneReader $881K trades 4H)
- Slug format: unknown — contains `-4h-` substring
- 6 windows/day per coin
- Multi-TF cascade (15M+1H+4H) showed 77.8% WR on 9 events
- **Action needed**: Fetch Gamma API to discover slug format

### Daily Markets
- Exist on Polymarket (confirmed: Brundle $131K does "Hourly+Daily")
- Slug format: unknown — contains `daily` or `-daily-`
- ~1 window/day per coin
- No implementation code exists
- **Action needed**: Same as 4H — Gamma API discovery

### Fee Structure (all timeframes)
- Taker fee: current 1.53%, after 2026-03-30 → 1.78%
- Maker fee: 0% + 20% rebate on taker fees
- Same for all crypto timeframes (5M through weekly)

## 15M Reprice Cooldown — Technical Details

### Current State
- `_REPRICE_COOLDOWN_S = 30` at `run_mm_live.py:439`
- Per-market tracking: `mkt["_last_reprice_ts"]` checked at line 2730
- Heavy cycle already 5s → reducing cooldown to 5s = reprice every heavy cycle
- `_REPRICE_MAX_PER_ORDER = 3` lifetime cap (unchanged)
- Window-end guard: no reprice within 3min of close (line 2732-2735, unchanged)

### Risk Assessment for 5s Cooldown
| Risk | Severity | Mitigation |
|------|----------|------------|
| API rate limit | 🟡 | _REPRICE_MAX_PER_ORDER=3 caps total calls per order |
| Phantom fill (cancel race) | 🔴 | Already mitigated: pre+post cancel WS check (line 2784-2851) |
| Double exposure | 🔴 | Already mitigated: cancel verified before replace |
| Order ID tracking | 🟡 | New order_id written to pending_orders (line 2869) |

## Data Advantage at 1H+ (Future Edge Sources)

### Available Now
- Brownian Bridge fair value (deployed)
- Binance volume + OB data (deployed)
- Polymarket holder data (deployed, n=1 calibration)
- Cross-market signal (15M confirms 1H)

### User Can Provide
- News data (real-time headlines, sentiment)
- On-chain wallet movements (large transfers, exchange inflows)
- Macro calendar events (FOMC, CPI, etc.)

### Can Build
- News sentiment API pipeline
- Whale alert / on-chain monitoring
- Cross-timeframe cascade (15M→1H→4H signal alignment)
- Funding rate divergence (perp vs spot)
- Social sentiment (LunarCrush MCP already connected)

## Mathematical Framework: 1H Edge Estimation

### Current (zero repricing)
```
Entry: $0.30 avg, WR: 72%, R:R: 2.33:1
EV = 0.72 × $0.70 - 0.28 × $0.30 - fee
EV = $0.504 - $0.084 - $0.005
EV = $0.415 per share (!)
```

### With repricing (fill rate improvement)
Current fill rate unknown (one-shot). If fills 40% of signals:
```
Without repricing: 40% × $0.415 = $0.166 per signal
With repricing (90% fill): 90% × $0.415 = $0.374 per signal
→ 2.25x improvement from repricing alone
```

### 4H/24H (estimated)
```
If WR similar (65-75%) at entry $0.25-0.35:
EV = 0.65 × $0.70 - 0.35 × $0.30 ≈ $0.35/share (conservative)
Fewer trades but bigger edge per trade
```

## 15M Baseline (BEFORE 5s cooldown, recorded 2026-03-25)

| Metric | Value | Notes |
|--------|-------|-------|
| Orders submitted | 317 | 2026-03-21 to 2026-03-25 |
| Orders filled | 132 | |
| **Fill rate** | **41.6%** | per order |
| Fill time median | 38s | |
| Fill time avg | 131s | long tail >120s: 37 fills |
| 0 reprices fill rate | **18.8%** (6/32) | ← key: no reprice = bad |
| 1 reprice fill rate | **72.7%** (40/55) | ← reprice = 4x improvement |
| 2 reprices fill rate | 85.7% (6/7) | |
| 3 reprices fill rate | 72.0% (18/25) | |
| Edge (fill - fair) | -4.14pp | adverse selection expected |
| UP edge | -12.29pp | more adverse |
| DOWN edge | +5.06pp | less adverse |

**After 24h target**: fill rate > 50%, fill time median < 25s, fewer 0-reprice orders.

## Phase 2A: 1H Mild Repricing — Architecture Design

### Core Insight: 1H repricing ≠ 15M repricing

| | 15M (speed game) | 1H (analysis game) |
|--|-------------------|---------------------|
| **Repricing driver** | Mid-price drift (market moved) | **Conviction growth** (our analysis improved) |
| **Price direction** | Chase market (mid → ask) | Follow confidence ($0.25 → $0.35) |
| **Cap** | Fair value or ask+2¢ | **$0.39 hard ceiling** (stay in cheap zone) |
| **Speed** | 5s (as fast as possible) | **20s** (heavy cycle, no rush) |
| **Goal** | Fill at any price | Fill at CHEAP price (positive selection bias) |

### Why Conviction-Driven Repricing

The 1H bot already re-evaluates `conviction_signal()` every 20s heavy cycle. As the window progresses:

```
t=5min:  time_trust=0.13, conviction=0.18 → entry_price=$0.25 (conservative)
t=15min: time_trust=0.38, conviction=0.30 → entry_price=$0.29 (more confident)
t=25min: time_trust=0.63, conviction=0.38 → entry_price=$0.32 (confident)
t=40min: time_trust=1.00, conviction=0.45 → entry_price=$0.35 (max confidence)
```

**time_trust = min(t_elapsed / 40min, 1.0)** saturates at 40 min. More data → higher conviction → willing to pay slightly more. But NEVER above $0.39.

This is NOT chasing the market. This is adjusting our bid as our ANALYSIS improves. Fits "鬥分析" perfectly.

### Repricing Flow (within heavy cycle)

```
Every 20s heavy cycle:
  1. _check_fills()           ← existing, runs first
  2. IF has pending unfilled order:
     a. Recompute conviction_signal() → new_price
     b. IF new_price > current_price + threshold (2¢):
        - REST verify order still LIVE (get_orders)
        - Cancel old order
        - Wait 50ms
        - REST verify not phantom-filled
        - Submit new order at new_price
        - Update pending_orders
     c. IF new_price ≤ current_price + threshold:
        - Keep current order (price hasn't moved enough)
     d. IF window remaining < 10 min:
        - Stop repricing (let order sit for last-chance fill)
  3. IF no pending order:
     - Evaluate new signal (existing flow, unchanged)
```

### Config Constants (to add)

```python
# ── 1H Repricing ──
_1H_REPRICE_ENABLED = True
_1H_REPRICE_THRESHOLD = 0.02    # 2¢ minimum drift to trigger reprice
_1H_REPRICE_MAX_PER_ORDER = 5   # max reprices per order lifetime (more than 15M: longer window)
_1H_REPRICE_STOP_BEFORE_END_S = 600  # stop repricing 10 min before window end
_1H_REPRICE_MIN_AGE_S = 60      # don't reprice orders younger than 60s (let them breathe)
```

### Safety Checks (from gotchas.md)

| Check | Implementation | Source |
|-------|---------------|--------|
| Pre-cancel verify | `client.get_orders(market=cid)` → confirm order_id exists | REST (no WS in 1H yet) |
| Post-cancel verify | `client.get_trades(market=cid)` → check if filled during cancel RTT | REST |
| Budget accounting | Cancel releases `pending_cost` → resubmit reclaims it | Existing budget tracking |
| One-order guard | Mark order as "repricing" → bypass new-entry guard | New flag in pending_order dict |
| Max reprices | `po.get("_reprice_count", 0) < _1H_REPRICE_MAX_PER_ORDER` | Counter in pending_order |

### What We're NOT Doing (deliberate)

1. **NOT adding WS to 1H** — REST polling every 10s is fine for 1H (3600s window). WS adds complexity for minimal gain.
2. **NOT chasing above $0.39** — positive selection bias is a FEATURE. Cheap fills = high WR.
3. **NOT repricing in last 10 min** — let orders sit for last-chance fills.
4. **NOT extracting to separate module** — add directly to run_1h_live.py first. Extract later if 4H/24H need it.

### Code Locations (where to add)

| Component | Location | Action |
|-----------|----------|--------|
| Config constants | `run_1h_live.py` top section | Add ~5 lines |
| `_reprice_1h()` function | After `_check_fills()` (~line 930) | New function ~60 lines |
| Heavy cycle wiring | `run_1h_live.py:~1200` (heavy cycle block) | Add 3-line call after _check_fills |
| Budget accounting | `run_1h_live.py:1228-1233` | Make repricing-aware |
| One-order guard | `run_1h_live.py:1329-1341` | Add repricing exception |

### Expected Behavior Change

```
BEFORE (one-shot):
  Signal → order at $0.28 → sits 55min → fills/expires
  Fill rate: ~40% (estimated from 15M baseline pattern)

AFTER (mild repricing):
  Signal → order at $0.25 → 20min → reprice to $0.29 → 20min → reprice to $0.32 → fills/expires
  Fill rate: ~60-70% (estimated: 15M data shows 1+ reprice = 72% fill rate)
  WR: ~65-70% (slightly lower than 72% due to higher avg fill price, but still great R:R)

  EV/signal improvement:
  Before: 40% × $0.420 = $0.168
  After:  65% × $0.330 = $0.215 (+28%)
```

### BMD Results (Opus Challenger, 5 Attacks)

#### 💀 FATAL #1: 72% WR 來自污染數據
- 43 trades 中只有 **8 個 entry < $0.40**（全部 win，但 n=8 冇統計意義）
- **27 trades entry $0.57-$0.61**（78% WR）= 大部分 win 嚟自貴價 fill
- "Positive selection bias at cheap prices" 係 plausible 但 **unproven at n=8**
- **⚠️ FIX**: 唔好用 72% 做 baseline。Expected WR range = **55-72%**（wide CI）
- **⚠️ MARKER [WR-ASSUMPTION]**: 所有用 72% 或 65-70% WR 嘅計算要標注

#### 💀 FATAL #5: REST-only cancel 違反已知 gotcha
- `feedback_reprice_race_condition.md`: "-$9.38 real loss from phantom fill during cancel"
- gotchas.md: "Cancel 前必須 WS check"
- Design 話「唔加 WS」但又引用 15M 嘅 WS safety = 自相矛盾
- **⚠️ FIX**: 用 **REST double-check pattern**:
  ```
  get_trades() → wait 500ms → get_trades() again → only proceed if BOTH return no fill
  ```
  或者加 ws_user（更安全但更複雜）
- **⚠️ MARKER [CANCEL-SAFETY]**: cancel 路徑必須有 double-check

#### 🔴 SERIOUS #3: One-order guard race condition
- Cancel 清空 pending → guard 以為冇 order → normal flow 落新 order → reprice submit = 2 orders
- 同 $106 duplicate entry bug 係同一 pattern
- **⚠️ FIX**: 加 `_repricing_in_progress` flag，block new ENTER during reprice cycle
- **⚠️ MARKER [REPRICE-LOCK]**: cancel→submit 之間必須有 lock

#### 🟡 WEAKENS #2: 15M data 唔適用 1H
- 15M repricing = chase mid (speed game)
- 1H repricing = follow conviction (analysis game)
- 1H book 比 15M thin → $0.25-$0.35 可能零 counterparty
- **⚠️ FIX**: 唔好引用 15M 18.8%→72% 做 1H 證據。獨立追蹤。
- **⚠️ MARKER [DATA-SOURCE]**: 任何引用 15M data 嘅地方要標注

#### 🟡 WEAKENS #4: EV +28% 係 optimistic guess
- Break-even WR at 65% fill rate + $0.33 entry = 58.8%
- 如果 fill rate 只到 50% → break-even WR = 66.6%
- Repricing 加入嘅 marginal fills 係「本來填唔到」嘅 = 最差嘅 fill
- **⚠️ FIX**: Relabel "+28%" → "range -15% to +32% depending on WR"
- **⚠️ MARKER [EV-ESTIMATE]**: EV 計算要帶 range，唔好用 point estimate

### Revised Risk Assessment (post-BMD)

| Risk | Severity | Mitigation | Marker |
|------|----------|------------|--------|
| WR data contaminated (n=8 at cheap) | 💀 | Paper test 48h, track WR by price bucket | [WR-ASSUMPTION] |
| Phantom fill during REST cancel | 💀 | REST double-check (500ms gap) | [CANCEL-SAFETY] |
| Order guard race → duplicate | 🔴 | `_repricing_in_progress` lock flag | [REPRICE-LOCK] |
| 15M evidence inapplicable | 🟡 | Don't cite, track independently | [DATA-SOURCE] |
| EV estimate too optimistic | 🟡 | Use range, not point estimate | [EV-ESTIMATE] |
| 1H book too thin at $0.25-$0.35 | 🟡 | Monitor fills per price level | [BOOK-DEPTH] |

## Phase 3: 4H Market — Discovery + Design

### 4H Slug Format (CONFIRMED from Gamma API)
```
Pattern: {coin}-updown-4h-{unix_timestamp}
Increment: 14400 seconds (4 × 3600)
Coins: btc, eth, bnb, sol, doge, xrp, hype (7 coins!)
Windows/day: 6 (4AM, 8AM, 12PM, 4PM, 8PM, 12AM ET)

Example: btc-updown-4h-1774526400
  → "Bitcoin Up or Down - March 26, 8:00AM-12:00PM ET"
  → conditionId: 0x867e6d57...
```

### Daily Slug Format (CONFIRMED)
```
Pattern: bitcoin-up-or-down-{month}-{day}-{year}-9am-et
Always 9AM ET start
Created ~2 days in advance
```

### 4H vs 1H: Why Different Strategy?

| Dimension | 1H | 4H | Implication |
|-----------|-----|-----|-------------|
| Window | 3,600s | 14,400s | 4x more analysis time |
| BTC σ per window | ~0.5% | ~1.0% | More price movement → harder to predict early |
| Available indicators | 4 (bridge+OB+vol+holder) | **6+** (add cross-TF + news) | More data = better decisions |
| Competition | Some bots | **Fewer bots** | Less speed pressure |
| Entry price (whale data) | $0.25-$0.37 | $0.80-$0.95 (BoneReader) | Whales wait for certainty |
| Trades/day | ~12 | ~6 | Fewer but bigger edge |

### Strategic Question: Early vs Late Entry?

**Option A: Early entry ($0.25-$0.40) — 1H clone**
- Pro: high R:R (2:1+), leverages bridge model
- Con: 4H has more uncertainty early → bridge less reliable at t=5min
- ⚠️ [VERIFY-LATER] Bridge model at 4H vol may need recalibration

**Option B: Late entry ($0.80-$0.95) — BoneReader style**
- Pro: very high WR (>85%), direction clear
- Con: terrible R:R (0.1:1), need WR>85% to break even
- Not our style ("鬥分析" ≠ "等到穩陣先入")

**Option C: Cross-TF cascade — OUR UNIQUE EDGE**
- Wait for 15M + 1H to BOTH confirm direction → enter 4H
- Entry when confirmed: $0.40-$0.60 (mid-price, moderate R:R)
- 77.8% WR on triple lock (⚠️ [DATA-SOURCE] n=9, need 50+ validation)
- We ALREADY HAVE 15M + 1H signals running → zero new infra for signal source

**Option D: HYBRID — early conviction + cross-TF size-up ★ RECOMMENDED**
```
Phase 1 (t=0-30min):
  Bridge signal → low conviction → enter small at $0.30-0.40
  (Same as 1H but higher floor — 4H has more uncertainty)

Phase 2 (t=30-120min):
  15M windows resolve → accumulate directional evidence
  1H window resolves → if SAME direction → conviction boost
  Cross-TF cascade → ADD to position at $0.40-0.55

Phase 3 (t=120-210min):
  Stop repricing. Let orders sit for last-chance fill.
  News/macro events during this period → manual override possible

Phase 4 (t=210-240min):
  Resolution approaching. Monitor only.
```

### 4H Signal Stack (6 sources)

| # | Signal | Source | Time to process | Status |
|---|--------|--------|----------------|--------|
| 1 | Brownian Bridge fair value | Binance 1s klines | 0.1s | ✅ Reuse from 1H (recalibrate vol) |
| 2 | OB Quality | WS polymarket | 0s (live) | ✅ Reuse from 1H |
| 3 | Volume Imbalance | Binance 1m klines | 1s | ✅ Reuse from 1H |
| 4 | Holder Imbalance | Data API / Goldsky | 3-5s | ✅ Reuse from 1H (⚠️ n=1 calibration) |
| 5 | **Cross-TF Cascade** | 15M + 1H bot state | 0s (local read) | 🆕 Read 15M + 1H state files |
| 6 | **News Sentiment** | User-provided / API | 10-30s | 🆕 Phase 4 (future) |

### Cross-TF Cascade Design (Signal #5)

```python
# Read 15M bot state: last N resolved windows for this 4H's coin
# Count: how many resolved UP vs DOWN
# If 8/10 last 15M windows = UP → strong UP cascade signal

# Read 1H bot state: current hour's signal
# If 1H conviction direction = same as 4H bridge direction → boost

cascade_score = 0
if last_10_15m_wr > 0.70 and same_direction:
    cascade_score += 0.3  # strong 15M cascade
if h1_conviction > 0.30 and same_direction:
    cascade_score += 0.2  # 1H confirms

# cascade_score modifies 4H conviction:
# 4h_conviction = base_conviction + cascade_score
```

**⚠️ [VERIFY-LATER] 15M state file format**: `shared/POLYMARKET_STATE.json` vs `logs/mm_state.json` — need to confirm which has resolved market history.
**⚠️ [VERIFY-LATER] 1H state file**: `logs/mm_state_1h.json` — confirmed has market history.
**⚠️ [VERIFY-LATER] Cross-TF correlation**: 15M UP ≠ 4H UP necessarily (different windows, different resolution mechanics).

### Architecture Decision: Reuse vs New

| Component | Reuse from 1H? | Changes needed |
|-----------|----------------|----------------|
| Discovery (slug scan) | Pattern only | New slug builder (timestamp-based like 15M, not date-based like 1H) |
| Signal (bridge) | ✅ hourly_engine.py | New HourlyConfig for 4H vol |
| OB / WS feeds | ✅ ws_polymarket + ws_binance | Just subscribe new tokens |
| Fill detection | ✅ _check_fills pattern | Copy from 1H |
| Repricing | ✅ _reprice_1h pattern | Adjust caps ($0.30-$0.45) |
| Cross-TF cascade | 🆕 | Read 15M + 1H state files |
| State management | ✅ pattern | New state file (mm_state_4h.json) |

**Implementation approach**: New file `run_4h_live.py`, import hourly_engine.py with 4H config, add cross-TF cascade module.

### 4H Config (estimated, ⚠️ [VERIFY-LATER] all values need paper test)

```python
# 4H-specific parameters
_4H_ENTRY_PRICE_FLOOR = 0.25       # slightly higher than 1H (more uncertainty)
_4H_ENTRY_PRICE_CEILING = 0.45     # higher ceiling (longer window, more room)
_4H_REPRICE_THRESHOLD = 0.03       # 3¢ drift (wider than 1H's 2¢)
_4H_REPRICE_MAX_PER_ORDER = 8      # more reprices (longer window)
_4H_REPRICE_STOP_BEFORE_END_S = 1800  # stop 30 min before end
_4H_CASCADE_ENABLED = True
_4H_CASCADE_15M_LOOKBACK = 10      # last 10 resolved 15M windows
_4H_CASCADE_15M_THRESHOLD = 0.70   # 7/10 same direction = strong
_4H_CASCADE_1H_THRESHOLD = 0.30    # 1H conviction > 0.30 = confirms
```

### Expected Performance (⚠️ [EV-ESTIMATE] wide range)

| Metric | Conservative | Optimistic |
|--------|-------------|-----------|
| WR (bridge only) | 55% | 65% |
| WR (bridge + cascade) | 62% | 75% |
| Entry price avg | $0.35 | $0.30 |
| R:R | 1.86:1 | 2.33:1 |
| EV/share | $0.10 | $0.25 |
| Trades/day | 3 | 6 |
| Daily EV | $2.58 | $12.90 |

**Break-even at $0.35 entry: WR > 35%.** Very forgiving due to high R:R.

## Resources
- 15M bot: `polymarket/run_mm_live.py`
- 1H bot: `polymarket/run_1h_live.py` + `polymarket/hourly_engine.py`
- WS feeds: `data/ws_polymarket.py`, `data/ws_user.py`
- 1H paper log: `logs/paper_pnl_1h.jsonl` (25 entries)
- 1H state: `logs/mm_state_1h.json` (total_pnl $71.52)
- Wallet analysis: `polymarket/analysis/wallet_analysis/`
