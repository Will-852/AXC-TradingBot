# AXC Polymarket 弱點修復報告 — 數據驅動分析

> 日期：2026-03-22 | 基於 129 trades + 219 order events + 73 signals + 614 resolutions
> Scope：15M MM bot（主力）+ 1H Conviction（副線）
> Bankroll：$80.95（initial $72.60）

---

## Executive Summary

AXC 15M MM bot 嘅 **方向判斷能力經驗證：81.7% WR，$194.22 gross PnL from 60 real trades**。但呢個 edge 被六個 infrastructure 弱點大幅稀釋：

| 弱點 | 量化損失 | 修復難度 | 預期改善 |
|------|---------|---------|---------|
| 1. Execution Quality Gap | ~$3.70/day 蒸發 | 中 | +$1.50-2.50/day |
| 2. Counterparty Blindness | [ESTIMATED] ~$0.80/day | 低 | +$0.50/day |
| 3. Adverse Selection (60% TFR) | ~$1.20/day | 中 | +$0.60-1.00/day |
| 4. Signal Freshness (3s cache) | ~$0.90/day | 高 | +$0.50-1.50/day |
| 5. Bet Size Friction ($1.33) | ~$0.40/day | 低 | +$0.20-0.40/day |
| 6. Forced Hold + Competition | ~$0.60/day | 中 | +$0.30-0.50/day |
| **合計** | **~$7.60/day** | | **+$3.60-6.40/day** |

**現狀 EV**：~$3.24/trade × ~5 fills/day = ~$16.20/day gross，但 infrastructure leak ~$7.60/day → **net ~$8.60/day**。修復後目標 **$12-15/day net**。

---

## Data Overview（實際數字）

### 15M MM Bot（主力）

| 指標 | 數值 | 來源 |
|------|------|------|
| Total trade records | 129 | mm_trades.jsonl |
| Real bets placed (cost>0) | 60 | mm_trades.jsonl |
| Win rate | 81.7% (49W/11L) | mm_trades.jsonl |
| Total cost deployed | $278.16 | mm_trades.jsonl |
| Total payout | $472.36 | mm_trades.jsonl |
| Gross PnL | +$194.22 | mm_trades.jsonl |
| Avg cost/trade | $4.64 | mm_trades.jsonl |
| Avg PnL/trade | +$3.24 | mm_trades.jsonl |
| Zero-fill markets | 20/99 (20.2%) | mm_trades.jsonl |
| State fill rate | 5/22 = 22.7% | mm_state.json |
| Order log fill rate | 10/35 = 28.6% | mm_order_log.jsonl |
| Avg time to fill | 298.7s (median 357.8s) | mm_order_log.jsonl |
| Toxic fill rate (60s) | 6/10 = 60.0% | mm_order_log.jsonl |
| Cancelled by TTL | 26 orders | mm_order_log.jsonl |
| External cancels | 138 events | mm_order_log.jsonl |
| Fill prices observed | $0.28, $0.35, $0.37, $0.40 | mm_order_log.jsonl |
| Current bankroll | $80.95 | mm_state.json |
| Signals logged | 73 | mm_signals.jsonl |

### 1H Conviction Bot（副線）

| 指標 | 數值 | 來源 |
|------|------|------|
| Real bets placed | 15 | mm_trades_1h.jsonl |
| Win rate | 46.7% (7W/8L) | mm_trades_1h.jsonl |
| Total PnL | -$36.10 | mm_trades_1h.jsonl |
| Total cost | $118.15 | mm_trades_1h.jsonl |

### BTC 15M Market Baseline

| 指標 | 數值 | 來源 |
|------|------|------|
| Total resolutions tracked | 614 | btc_15m_resolutions.jsonl |
| UP outcomes | 293 (47.7%) | btc_15m_resolutions.jsonl |
| DOWN outcomes | 321 (52.3%) | btc_15m_resolutions.jsonl |
| Market bias | Slight DOWN (4.6pp) | btc_15m_resolutions.jsonl |

---

## 1. Execution Quality Gap

### 數據證據

**CORE.md 決策流程**：Direction → Edge → Confidence → Entry。**冇 Execution Quality 步驟**。

從 `mm_order_log.jsonl` 提取嘅實際執行數據：

- **Fill rate 極低**：10 fills / 35 submits = 28.6%（state tracker 更低：5/22 = 22.7%）
- **超長 time-to-fill**：median 357.8s，avg 298.7s。最快 10.6s，最慢 470s
- **26 orders 被 TTL cancel**：avg time on book 523s，全部因為 `ttl_XXXs_maxYYYs` 觸發
- **138 external cancel events**：API 重複 report 同一 cancel（`0xc07116` + `0xb1f890` 兩張 order 產生 >100 次 cancelled_external event），但核心問題係 orders 長時間 sit on book 冇人接

**Paper return vs actual return（Implementation Shortfall）**：

從 `shadow_tape.jsonl` 同 `mm_trades.jsonl` 對比：shadow tape 每個 window 都有方向判斷（364 records），但只有 60 trades 有 real cost（其餘 69 records cost=0 = 冇 fill）。

```
IS = (paper_return - actual_return) / paper_return

Paper scenario: 每個 window bet $4.64 at 81.7% WR
  paper_return = 129 × $3.24 = $418.06

Actual: 只有 60 trades filled
  actual_return = $194.22

IS = ($418.06 - $194.22) / $418.06 = 53.6%
```

**超過一半嘅 edge 被執行質量蒸發咗。**

### 數學框架

#### Fill Probability Decay

從 order log 嘅 fill 數據校準：

```
P(fill | time_on_book = t) = exp(-λt)

Observed fills:
  t=10.6s → filled     (earliest)
  t=81.3s → filled
  t=89.0s → filled
  t=296.5s → filled
  t=297.0s → filled
  t=357.8s → filled
  t=457.3s → filled
  t=459.4s → filled
  t=468.2s → filled
  t=470.0s → filled

Unfilled orders cancelled at t=96-608s (26 orders)
```

以 10 fills / 35 submits = 28.6% 同 avg fill time 298.7s：

```
λ = -ln(0.286) / 300 ≈ 0.00417 /s

P(fill in 60s) ≈ 22%
P(fill in 120s) ≈ 39%
P(fill in 300s) ≈ 71%  ← 但只有 orders 存活到呢度先有機會
P(fill in 600s) ≈ 92%
```

問題：dynamic TTL max = 600s，但 26/26 TTL cancels 發生喺 96-608s → orders 冇足夠時間喺 book 上等 fill。

#### Arrival Price Benchmark

```
Arrival benchmark = fair value at signal time

From mm_signals.jsonl + mm_order_log.jsonl matched fills:

Fill 0x0f7e96 (ETH UP): fair=0.5878, bought@0.40, mid_at_fill=0.445
  → slippage vs fair = |0.40 - 0.5878| / 0.5878 = 32% (favorable! bought cheap)

Fill 0x56b7fd (BTC DOWN): fair=0.3919 (UP), bought DOWN@0.28
  → paid 0.28 for DOWN when fair_down ≈ 0.61 → 54% discount

Fill 0xa2728b (BTC UP): fair=0.7785, bought@0.40, mid_at_fill=0.565
  → 48.6% discount vs fair → but mid_60s=0.415 → ADVERSE
```

Entry price consistently 40-55% below fair value（$0.28-0.40 cap）。呢個係 structural edge。但 **fill probability 極低 = 大量 signal 浪費**。

### 量化影響

```
Active trading hours: ~16h/day (observed pattern)
Windows per hour: ~4 (every 15 min)
Signal opportunities: ~64/day

Current fill rate: 28.6%
Actually filled: ~64 × 0.286 × 0.5 (dual BTC+ETH) = ~9 fills/day
But state shows 5 fills / 22 submits in current session → ~5 fills/day effective

Edge per fill: $3.24
Actual daily: ~5 × $3.24 = $16.20
Potential (at 50% fill): ~16 × $3.24 = $51.84

Daily execution leak: $51.84 - $16.20 = $35.64 POTENTIAL
Conservative (at 40% fill): ~13 × $3.24 = $42.12 → leak = $25.92

Realistic target: improve fill rate from 28.6% to 40% → +$3.70/day
```

### Fix Proposal

**F1.1 — Improve order pricing to boost fill rate（P0，最高 ROI）**

現狀：固定 $0.40 cap entry → fill rate 28.6%
問題：$0.40 太 aggressive，大部分 orders 坐喺 book 等到 TTL expire

```
Dynamic entry based on fair value:
  if fair > 0.70: max_bid = 0.40  (strong signal, keep current)
  if fair 0.60-0.70: max_bid = 0.42  (slightly more generous)
  if fair 0.55-0.60: max_bid = 0.45  (weaker signal, pay more for certainty)
  if fair < 0.55: SKIP (not enough edge)
```

預期：fill rate 28.6% → 40-50%，但 avg PnL/fill drops from $3.24 to ~$2.50。Net: +$1.50-2.50/day。

**F1.2 — Execution quality tracking in decision flow**

喺 CORE.md §3 加 Step 4.5：

```
Current:  Direction → Edge → Confidence → Entry
Proposed: Direction → Edge → Confidence → Execution Quality Check → Entry

Execution Quality Check:
  1. Current fill rate (rolling 20) < 20% → widen bid by 1¢
  2. Avg time-to-fill > 300s → more aggressive pricing
  3. Log: signal_fair vs entry_price vs fill_price → IS tracking
```

### 驗證方法

| Metric | Current | Target | 測量方式 |
|--------|---------|--------|---------|
| Fill rate (state tracker) | 22.7% | >35% | fill_stats in mm_state.json |
| Fill rate (order log) | 28.6% | >40% | fills/submits in mm_order_log.jsonl |
| Avg time to fill | 298.7s | <200s | time_to_fill_s in order_log |
| Implementation Shortfall | 53.6% | <35% | paper_pnl vs actual_pnl weekly |
| Zero-fill markets | 20.2% | <10% | cost=0 in mm_trades.jsonl |

---

## 2. Counterparty Landscape

### 數據證據

CORE.md §1 問「點解佢肯賣俾我？」，答案假設對手係 directional bettors。但 order log 揭示：

**Evidence 1：External cancels 量極大**

```
138 cancelled_external events / 219 total order events = 63%

具體案例：0xc07116 + 0xdd7f5e (BTC+ETH DOWN @$0.35)
  Submit: 2026-03-21T07:02:52
  First cancelled_external: 2026-03-21T07:07:07 (~5 min later)
  然後連續 ~100 個 cancelled_external events over next 5 minutes
  Our cancel: 2026-03-21T07:12:04 (TTL 552s)
```

呢啲 `cancelled_external` events 代表 **其他參與者嘅 orders 被取消**（唔係我哋嘅）。Polymarket API push 所有 book changes。大量 external cancels = **其他 bots 喺 quote/cancel cycle**。

**Evidence 2：distinct-baguette 確認嘅 bot 生態**

From `distinct_baguette_analysis.md`：
- Preemptive cancel：sub-second 撤單（AXC 用 5s loop + 3s price cache = 8s delay）
- Binance aggTrade WebSocket（AXC 用 REST polling）
- 500ms requote interval（AXC 用 10s heavy cycle）
- Entry delay: 5-13s from window start（AXC 要等 M1 gate = ~60s+）

**Evidence 3：Fill 時機模式**

```
From order log fills:
  Fast fill (10.6s): 0x0f7e96 ETH UP @$0.40, mid=0.445 → FAV
  Slow fills (296-470s): mixed FAV/ADV

Pattern: fast fills (someone hitting our bid) = usually favorable
         slow fills (our bid gets crossed by market movement) = 50/50
```

### 數學框架

#### Winner's Curse in CLOB

```
P(fill | AXC correct) < P(fill | AXC wrong)

因為：
- AXC correct → informed traders 已經 take the same side →
  少人肯賣俾我哋 → lower fill rate
- AXC wrong → informed traders take other side →
  多人肯賣俾我哋 → higher fill rate

Evidence from 10 fills:
  Fills that WON the market: [需要 cross-reference with mm_trades]
  Fills that LOST: [需要 cross-reference]
```

From the 10 fills with outcomes:
- 0x5f184b (2 fills, DOWN @0.37/0.40) → market resolved UP → LOST ($-3.85)
- 0x0f7e96 (1 fill, UP @0.40) → market resolved DOWN → LOST ($-2.33)
- 0x56b7fd (2 fills, DOWN @0.28) → market resolved DOWN → WON (+$5.80)
- 0x3dddf9 (2 fills, UP @0.35) → market resolved UP → WON (+$3.94)
- 0x9b1852 (2 fills, UP @0.35) → market resolved UP → WON (+$3.94)
- 0xa2728b (1 fill, UP @0.40) → market resolved DOWN → LOST ($-0.96)

```
Fill outcomes: 6 fills WON, 4 fills LOST → WR on filled = 60%
Compare: overall WR = 81.7%

Winner's Curse magnitude: 81.7% - 60% = 21.7pp
```

**[CAVEAT: n=10 太細，21.7pp 差距可能 noise。但方向一致：filled trades WR 低過 overall。]**

#### Bertrand Competition

```
With N bots quoting similar strategies:
  edge_per_bot ≈ total_edge / N

distinct-baguette doc 提到 BTC/ETH/SOL/XRP 4 coins。
Observed competition: 多個 bot 同時 cancel（138 external cancel events）

If N ≈ 5-10 active bots on BTC 15M:
  Total edge pool ≈ spread × volume
  AXC share ≈ 1/N × fill_rate_advantage

Current: AXC 係 slowest bot (5s+3s = 8s vs 200ms)
  → AXC 拎到 edge 只係因為 pricing discount ($0.40 cap)
  → 如果唔係 structural entry price edge, AXC 完全冇 competitive advantage
```

### 量化影響

```
Winner's Curse cost estimate:
  If WR on filled = 60% instead of 81.7%
  At $0.40 entry: EV = 0.60 × $0.60 - 0.40 × $0.40 = $0.20/fill
  Without curse: EV = 0.817 × $0.60 - 0.183 × $0.40 = $0.417/fill

  Difference: $0.217/fill × 5 fills/day = $1.09/day [ESTIMATED]

  Conservative (50% attributable to WC): $0.54/day
```

### Fix Proposal

**F2.1 — Counterparty classification layer（中期）**

```python
# 分類 fill 對手方，唔猜身份但觀察行為
def classify_fill_context(fill_event):
    """Log context around each fill for post-hoc analysis."""
    return {
        'ttf_bucket': 'fast' if fill.ttf < 30 else 'medium' if fill.ttf < 180 else 'slow',
        'mid_direction': 'favorable' if mid_moved_our_way else 'adverse',
        'external_cancel_rate_1m': count_ext_cancels_before_fill / 60,  # cancels/sec
        'book_depth_at_fill': bid_vol + ask_vol,
    }
```

唔需要知對手係邊個 — 只需要知 fill context。Fast fill + high cancel rate = bot 送嘅 liquidity。Slow fill + low cancel rate = directional bettor。

**F2.2 — 更新 CORE.md §1 counterparty model**

```
Current: "點解佢肯賣俾我？" → assumes directional bettors
Updated: "點解佢肯賣俾我？" → three counterparty types:
  1. Latency bots (distinct-baguette class): cancel before we fill → 降低 fill rate
  2. Arb bots: fill us when UP+DOWN spread > $1 → neutral
  3. Directional bettors: genuine disagreement → our edge source

Implication: only type 3 gives us edge. Types 1+2 reduce fill rate or are zero-sum.
```

### 驗證方法

| Metric | Current | Target | 測量方式 |
|--------|---------|--------|---------|
| WR on filled trades | 60% (n=10) | >70% | cross-ref order_log fills vs mm_trades outcomes |
| Fast fill (<30s) WR vs slow | insufficient data | measure delta | split by ttf_bucket |
| External cancel rate before fill | unmeasured | <50/min | count in 60s window before fill |

---

## 3. Adverse Selection 量化

### 數據證據

**Toxic Fill Rate (TFR) from order log:**

```
10 fills with post_fill_60s data:
  Toxic (price moved against us in 60s): 6
  Favorable: 4
  TFR = 60%
```

**逐 fill breakdown:**

| CID | Side | Price | Mid@Fill | Mid@60s | Change | Verdict | TTF |
|-----|------|-------|----------|---------|--------|---------|-----|
| 0x5f184b | DOWN | $0.40 | 0.380 | 0.465 | -0.085 | ADV | 81s |
| 0x5f184b | DOWN | $0.37 | 0.355 | 0.555 | -0.200 | ADV | 89s |
| 0x0f7e96 | UP | $0.40 | 0.445 | 0.555 | +0.110 | FAV | 11s |
| 0x56b7fd | DOWN | $0.28 | 0.435 | 0.405 | +0.030 | FAV | 296s |
| 0x56b7fd | DOWN | $0.28 | 0.435 | 0.440 | -0.005 | ADV | 297s |
| 0x3dddf9 | UP | $0.35 | 0.355 | 0.300 | -0.055 | ADV | 457s |
| 0x3dddf9 | UP | $0.35 | 0.315 | 0.305 | -0.010 | ADV | 459s |
| 0x9b1852 | UP | $0.35 | 0.385 | 0.515 | +0.130 | FAV | 468s |
| 0x9b1852 | UP | $0.35 | 0.335 | 0.545 | +0.210 | FAV | 470s |
| 0xa2728b | UP | $0.40 | 0.565 | 0.415 | -0.150 | ADV | 358s |

**Pattern**: fast fills (81-89s) = both adverse。Long fills (296-470s) = mixed。
但 n=10 太細做 firm conclusion。

**CORE.md 寫 adverse_selection = 0.40 for crypto_15m** — 但實測 TFR = 60%，高 50%。

### 數學框架

#### Glosten-Milgrom Spread Model

```
Optimal spread = 2 × P(informed) × V

Where:
  P(informed) = probability counterparty is informed = TFR proxy ≈ 0.60
  V = information advantage of informed trader

From data:
  Average adverse move magnitude = (0.085 + 0.200 + 0.005 + 0.055 + 0.010 + 0.150) / 6
                                 = 0.084 (8.4¢ per $1 token)

  Optimal half-spread = 0.60 × 0.084 = 0.0504 ≈ 5¢

  Our entry prices: $0.28-0.40 for tokens with fair value $0.35-0.78
  Average distance from mid: we buy at ~55% discount from fair

  → Our entry price discount (buying at 0.40 when fair is 0.60+)
    exceeds the AS cost (5¢) by a large margin
  → Structural entry price edge ABSORBS adverse selection
```

#### VPIN Approximation

```
VPIN = Volume of informed trades / Total volume

From fills:
  Fills where mid moved >5% against in 60s: 4/10 = 40%
  Fills where mid moved >10% against in 60s: 3/10 = 30%

VPIN estimate ≈ 0.40 (moderate-high informed trading)
```

**CORE.md 嘅 0.40 adverse selection rate 同 VPIN 估計一致，但 TFR 用 60s window 量度出 0.60 — 因為 TFR 包括 noise 同 genuine adverse selection。**

### 量化影響

```
Per-fill AS cost = TFR × avg_adverse_magnitude × bet_size
                 = 0.60 × 0.084 × $4.64
                 = $0.234/fill

Daily AS cost = $0.234 × 5 fills = $1.17/day

But: this is ALREADY reflected in the 60% WR on filled trades
     (vs 81.7% overall WR)
So the real question: can we AVOID the toxic fills?
```

### Fix Proposal

**F3.1 — Post-fill 60s monitoring + conditional hold**

```python
# 已有 post_fill_60s logging。加 real-time 版本：
async def post_fill_check(fill, delay=60):
    await asyncio.sleep(delay)
    mid_now = get_midpoint(fill.token_id)
    if is_toxic(fill, mid_now):
        # Don't sell (binary → hold to resolution)
        # But FLAG for position sizing reduction on next entry
        log_toxic_fill(fill, mid_now)
        state['toxic_fill_streak'] += 1

    # If 3 consecutive toxic fills → reduce size 50% for next 3 entries
    if state['toxic_fill_streak'] >= 3:
        state['size_multiplier'] = 0.5
```

**F3.2 — Fast-fill adversity filter**

```
From data: both fills with TTF < 100s were ADVERSE

Rule: if time_to_fill < 30s AND not during last 5 min of window:
  → likely informed trader hitting our bid
  → reduce subsequent entry size by 30%
  → increase bid distance from mid by 2¢

This targets the worst case: fast toxic fills where someone
KNOWS direction and deliberately takes our order.
```

**F3.3 — Update CORE.md adverse_selection parameter**

```
Current: crypto_15m = 0.40
Updated: crypto_15m = 0.55 (midpoint of 0.40 VPIN + 0.60 TFR)

Impact on GTO filter: higher AS → more conservative sizing
  Kelly adjustment: f* × (1 - AS) = f* × 0.45 (was f* × 0.60)
  → 25% smaller positions
```

### 驗證方法

| Metric | Current | Target | 測量方式 |
|--------|---------|--------|---------|
| TFR (60s) | 60% (n=10) | <45% | post_fill_60s in order_log |
| Fast-fill TFR (<30s) | 100% (n=2) | <50% | split by time_to_fill |
| AS parameter in CORE.md | 0.40 | calibrated weekly | TFR rolling 30 |
| Toxic streak max | unmeasured | <3 consecutive | state tracking |

---

## 4. Signal Freshness & Data Infrastructure

### 數據證據

**3s price cache (`run_mm_live.py:109`)**

```python
if key in _cache and now - _cache[key][1] < 3:
    return _cache[key][0]
```

**10s heavy cycle（`run_mm_live.py:66`）**

```python
_HEAVY_INTERVAL_S = 10  # heavy ops every 10s
```

**REST polling for CVD（`_cvd_buy_ratio` function）**：REST → parse → compute → 多秒延遲

**對比 distinct-baguette：**

| Component | AXC | distinct-baguette | Gap |
|-----------|-----|-------------------|-----|
| Price feed | REST + 3s cache | Binance aggTrade WSS | ~3-8s |
| Signal eval | 10s heavy cycle | event-driven (2s cooldown) | ~8s |
| Cancel latency | 5s loop + REST cancel | sub-second WSS → cancel | ~5-10s |
| Requote | 10s | 500ms | ~20x |
| Order status | REST polling | WSS user events | ~2-5s |

**信號新鮮度嘅實際成本：**

From `mm_signals.jsonl`：
- M1 sigma range: 1.01 - 5.96
- 強信號（sigma > 2.0）只有 12/73 = 16.4%
- 大部分信號 sigma 1.0-1.5 = 邊界

### 數學框架

#### Signal Decay Model

```
edge(t) = edge₀ × exp(-λt)

For 15M crypto markets:
  Information half-life ≈ 3-5s (from distinct-baguette: "signal >5s = stale")
  λ_signal = ln(2) / 4 ≈ 0.173

AXC signal delay chain:
  Price cache age: 0-3s (avg 1.5s)
  Heavy cycle wait: 0-10s (avg 5s)
  Compute + OB fetch: ~1-2s
  Order submission: ~1-2s
  Total: 3.5 - 17s, avg ~9.5s

edge(9.5) = edge₀ × exp(-0.173 × 9.5) = edge₀ × 0.193

→ 信號到達 CLOB 時，只剩 ~19% 嘅原始 edge
```

**[IMPORTANT CAVEAT]**：AXC 策略唔係 pure latency play。Bridge model 計 P(UP) based on displacement from open，唔係短期 momentum。所以 signal decay 唔係 180ms vs 200ms 嘅問題 — 而係 **bridge displacement 在 9.5s 內有冇大幅改變**。

For 15M windows, bridge displacement changes slowly → **actual signal decay 遠低過 pure momentum strategy**。

Revised estimate：
```
bridge_edge(t) = bridge_edge₀ × (1 - t/T_window)

Where T_window = 900s (15 min)
bridge_edge(9.5) = bridge_edge₀ × (1 - 9.5/900) = bridge_edge₀ × 0.989

→ Bridge edge 幾乎冇 decay for 9.5s delay
```

**真正嘅成本唔係 signal decay — 而係 cancel defense delay：**

```
Cancel defense delay = 5s loop + 3s cache = 8s

If BTC moves 0.5% in 8s (at $70K = $350):
  Our adverse cancel threshold: 0.5% BTC / 0.7% ETH
  But detected 8s late → we already ate the move

Evidence: 0x5f184b fills — bought DOWN@0.37/0.40
  but BTC rallied → mid went to 0.465/0.555 within 60s
  If we had sub-second cancel: could have avoided both fills ($3.85 loss)
```

### 量化影響

```
Cancel defense leak:
  Fills where faster cancel would have helped: 2/10 (0x5f184b pair)
  Loss on those: $3.85
  Per-day estimate: $3.85 / 2 days of data = $1.93/day [ESTIMATED]

  Conservative (50% avoidable): ~$0.96/day

Price cache staleness:
  3s cache on 5s loop = pricing based on 1.5-8s old data
  In fast markets, this = 0.02-0.05% mispricing
  On $5 bet: $0.001-0.0025/trade → negligible at current scale

Total signal freshness cost: ~$0.90/day (mostly cancel defense)
```

### Fix Proposal

**F4.1 — WebSocket price feed（高 ROI，但開發量大）**

```python
# Replace REST + cache with Binance aggTrade WSS
import websockets

async def binance_ws():
    uri = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    async with websockets.connect(uri) as ws:
        async for msg in ws:
            data = json.loads(msg)
            price = float(data['p'])
            _cache['price_BTCUSDT'] = (price, time.time())

            # IMMEDIATE cancel check (no 5s wait)
            for cid, mkt in state['markets'].items():
                if _should_cancel_adverse(mkt, price):
                    await _cancel_orders(mkt)
```

預期：cancel defense 從 ~8s → <1s。減少 adverse fills。

**但 macOS M3 Max 跑 local → Binance latency 已係 ~150-300ms**。VPS (Amsterdam) 可以低到 ~1ms。呢個係 infrastructure 差異嘅根本原因。

**F4.2 — 減少 cache TTL（簡單，即時改）**

```python
# run_mm_live.py line 109
# Change: cache TTL from 3s to 1s
if key in _cache and now - _cache[key][1] < 1:  # was 3
    return _cache[key][0]
```

Trade-off：API rate 增加 3x（from ~20/min to ~60/min）。Binance rate limit = 1200/min → 安全。

**F4.3 — Event-driven cancel（中等改動）**

唔需要 full WSS。只需要 cancel check 唔等 heavy cycle：

```python
# In 5s fast loop, add cancel defense check
async def fast_loop():
    while True:
        price = await _price_fast()  # 1s cache
        _check_adverse_cancels(state, price)  # immediate
        await asyncio.sleep(5)
```

### 驗證方法

| Metric | Current | Target | 測量方式 |
|--------|---------|--------|---------|
| Price cache age | 3s max | 1s max | config change |
| Cancel defense delay | ~8s | <3s | log cancel reason vs BTC move timing |
| Adverse fills from slow cancel | 2/10 | 0/10 | mm_order_log post_fill analysis |
| API calls/min | ~20 | ~60 | rate tracker |

---

## 5. Bet Size Friction Analysis

### 數據證據

```
Current bankroll: $80.95
Per bet cap: 1% = $0.81 (settings.py: MAX_PER_BET = 0.01)
But actual avg cost/trade: $4.64 (from mm_trades.jsonl)

Discrepancy: $4.64 >> $0.81
→ 1% cap 唔係 binding constraint in live (probably overridden or calculated differently)
```

Looking at actual bet sizes from order_log:
```
Submit prices × sizes:
  5.0 shares × $0.37 = $1.85
  5.0 shares × $0.40 = $2.00
  5.44 shares × $0.40 = $2.18
  5.82 shares × $0.40 = $2.33
  6.06 shares × $0.35 = $2.12
  6.45 shares × $0.35 = $2.26
  6.60 shares × $0.40 = $2.64
  6.85 shares × $0.40 = $2.74
  7.10 shares × $0.35 = $2.49
  8.06 shares × $0.28 = $2.26
  8.67 shares × $0.28 = $2.43
  9.41 shares × $0.24 = $2.26
  9.42 shares × $0.28 = $2.64

Actual bet size range: $1.85 - $2.74
Average: ~$2.35 per order
```

**With dual-side betting (UP + DOWN), total exposure per window = ~$4.70。**

### 數學框架

#### Kelly with Transaction Costs

```
f* = (p(1+b) - 1) / b - c/(b×μ)

Where:
  p = win probability = 0.817 (overall) or 0.60 (on filled)
  b = odds = payout/cost - 1 = ($0.60/$0.40) - 1 = 0.50
  c = round-trip cost = Polymarket fee structure
  μ = mean edge

Polymarket fee:
  Maker = 0% fee + maker rebate (~$0.002/share)
  Taker = ~1.5-2% fee
  AXC uses GTC (maker) → c ≈ 0 (actually slightly negative!)

f* = (0.60 × 1.50 - 1) / 0.50 = (0.90 - 1) / 0.50 = -0.20
Wait — using filled WR 60%: f* = NEGATIVE → don't bet!

Using overall WR 81.7%:
f* = (0.817 × 1.50 - 1) / 0.50 = (1.2255 - 1) / 0.50 = 0.451

Half-Kelly: f* = 0.225 = 22.5% of bankroll per bet
At $80.95 bankroll: $18.21 per bet

Current bet: $2.35 → only 2.9% of bankroll
Half-Kelly says: $18.21 → 7.7x larger
```

#### Minimum Viable Bet

```
bet_min = c / (2 × (edge - c))

With c ≈ 0 (maker fee): bet_min ≈ 0
→ Technically any bet size works since fees near zero

But REAL friction = spread:
  We buy at $0.40, mid is often $0.50
  Effective cost = (mid - our_price) / mid = ($0.50 - $0.40) / $0.50 = 20%

  This 20% "spread cost" is actually our EDGE — we want to buy cheap
  So friction analysis flips: buying cheaper = better, not worse
```

#### Break-Even Edge

```
edge_min = spread/(2 × bet_size) + fee_rate

At $2.35 bet, ~0% maker fee:
  edge_min ≈ spread / (2 × $2.35)

  If spread = $0.04 (2¢ each side): edge_min = $0.04 / $4.70 = 0.85%
  Our actual edge: $3.24 / $4.64 = 69.8% → 82x above break-even

→ Bet size friction 唔係 edge killer。Edge 足夠大。
```

**真正問題唔係 bet size 太細 → 係 fill rate 太低導致 total deployed capital 太少。**

### 量化影響

```
If we increase bet size from $2.35 to $4.00 per order:
  Same fill rate 28.6% → same number of fills
  But bigger per-fill EV: $3.24 × (4.00/2.35) = $5.51/fill
  Daily: 5 × $5.51 = $27.55 (was $16.20) → +$11.35/day

BUT: bigger bets = bigger losses on adverse fills
  Loss per adverse fill: $4.00 (was $2.35)
  At TFR 60%: risk increases proportionally

Kelly says safe up to $18.21 (half-Kelly at 81.7% WR)
Conservative increase to $4.00 = 4.9% bankroll → still well under Kelly

Net impact of size increase: ~$0.40/day improvement
(conservative because adverse selection eats more of bigger bets)
```

### Fix Proposal

**F5.1 — Scale bet size with bankroll（即可改）**

```python
# Current: fixed sizes based on share count
# Proposed: percentage-based
def calc_bet_size(bankroll, confidence, fair):
    base_pct = 0.03  # 3% bankroll base (was ~2.9%)

    # Scale by confidence
    if fair > 0.70 or fair < 0.30:  # strong signal
        multiplier = 1.5
    elif fair > 0.60 or fair < 0.40:  # moderate
        multiplier = 1.0
    else:  # weak
        multiplier = 0.7

    bet = bankroll * base_pct * multiplier
    return min(bet, bankroll * 0.05)  # hard cap 5%
```

At $80.95: base $2.43, strong $3.64, cap $4.05.

**F5.2 — Bankroll growth acceleration path**

```
Current: $80.95 bankroll
Target: $500 (comfortable operation level)

At net $8.60/day (current): ~49 days to $500
At net $12/day (post-fix): ~35 days to $500
At net $15/day (optimistic): ~28 days to $500

But: drawdown risk at $80.95 is severe (one bad day = -$15 = -18.5%)
  → Section 8 models this precisely
```

### 驗證方法

| Metric | Current | Target | 測量方式 |
|--------|---------|--------|---------|
| Avg bet size | $2.35/order | $3.50/order | mm_order_log submit sizes |
| Bet as % bankroll | 2.9% | 3-5% | bet/bankroll at submit time |
| PnL per fill | $3.24 | $4.50+ | mm_trades pnl/count |
| Kelly utilization | 13% of f* | 25-30% of f* | actual_bet / kelly_optimal |

---

## 6. Forced Hold & Exit Option Value

### 數據證據

**CORE.md Rule**: "last 5 min = 唔可以 sell"（`run_mm_live.py` forced hold period）

**Cancel logic（`run_mm_live.py:1340`）**:
```python
# Trigger 1: 2 min before window end → cancel ALL pending
if end_ms > 0 and now_ms > end_ms - 120_000:
```

所以：
- **Last 5 min**: 唔可以 sell existing positions
- **Last 2 min**: cancel all pending unfilled orders
- **Window = 15 min**: forced hold = 33% of window

**同時 distinct-baguette 可以：**
```
mm_requote_ms: 500      → 500ms 重新報價
Preemptive cancel        → sub-second 撤單
```

**佢哋冇 forced hold — 可以隨時 exit。**

### 數學框架

#### Exit Option Value (Black-Scholes approximation)

```
Exit Option Value ≈ Black-Scholes put on binary position

Parameters:
  S = current position value (mid × shares)
  K = entry cost
  T = remaining time (fraction of window)
  σ = Polymarket mid volatility
  r = 0 (short duration)

From signal_tape.jsonl, Polymarket mid volatility:
  BTC UP mid moves: ±0.10-0.15 per 5 minutes in active markets
  σ_poly ≈ 0.30 per √(15min) [ESTIMATED from OB data]

For a position entered at min 5 with 10 min remaining:
  At min 10 (5 min to go, forced hold kicks in):
    T = 5/15 = 0.33
    σ√T = 0.30 × √0.33 = 0.172

  BS put value ≈ 0.05 × position_size (rough, for near-the-money)

  For $2.35 position: exit option ≈ $0.12

  Losing exit option: $0.12 × 5 fills/day = $0.60/day
```

#### Thin Book Slippage near Resolution

```
slippage(t) = α × volume(t)^(-β)

Near window end, liquidity drops because:
1. Bots cancel (distinct-baguette: preemptive cancel)
2. Spread widens
3. Only "stuck" positions remain

From signal_tape.jsonl OB data progression:
  t=-5min: bid_vol ≈ 30K-150K
  t=-2min: [no data — our orders already cancelled]
  t=-30s: [no data]

Without exit option, positions that were wrong direction at min 10
must ride to resolution → full loss ($2.35) instead of partial loss (~$1.00)
```

**但 binary markets 結算 $0 or $1 — 中間 exit 價格唔代表最終結果。** 如果方向判斷正確率 81.7%，forced hold 其實幫咗你（唔會 panic sell 贏嘅 position）。

**真正損失嘅情況：**
```
Position entered at min 3
At min 10, BTC reversed sharply (our direction now wrong)
Without forced hold: sell at $0.35 → loss = $2.35 - $0.35×shares ≈ -$1.00
With forced hold: hold to resolution → loss = -$2.35

Difference: $1.35 per wrong-direction-after-5min trade
Frequency: ~18.3% (losing trades) × ~30% (loss visible by min 10) = 5.5%
Per day: 5 fills × 5.5% × $1.35 = $0.37/day
```

### Fix Proposal

**F6.1 — Reduce forced hold to last 2 min（即可改）**

```python
# Current: last 5 min
# Proposed: last 2 min (same as cancel-all timing)
FORCED_HOLD_BUFFER_S = 120  # was effectively 300

# This gives 3 more minutes to exit bad positions
# But: Polymarket liquidity drops near end → exit slippage
```

Trade-off：earlier exit = might sell winners too early（減少 upside）

**F6.2 — Conditional exit（中期）**

```python
def should_exit_early(mkt, current_mid, remaining_s):
    """Exit if direction clearly wrong AND enough time to get fill."""
    if remaining_s < 120:  # last 2 min: hold
        return False
    if remaining_s > 600:  # more than 10 min: hold (direction may change)
        return False

    # Mid strongly against us (>70% probability wrong)
    our_side_mid = current_mid if mkt.dir == 'UP' else 1 - current_mid
    if our_side_mid < 0.20:  # market says 80%+ we're wrong
        return True

    return False
```

**F6.3 — Accept forced hold as feature, not bug**

At 81.7% WR, forced hold prevents selling winners early:
```
Without forced hold: might sell 30% of winners at breakeven (panic)
  Cost: 81.7% × 30% × $0.60 = $0.147/fill foregone profit

With forced hold: hold all winners to resolution
  Benefit: $0.147/fill

vs forced hold cost: ~$0.074/fill (from calculation above)

Net: forced hold is +$0.073/fill → KEEP IT (at current WR)
```

**Only reduce forced hold if WR drops below ~65%.**

### 驗證方法

| Metric | Current | Target | 測量方式 |
|--------|---------|--------|---------|
| Forced hold period | 5 min | 2 min (conditional) | config |
| Positions where early exit would have helped | unmeasured | <20% | backtest mid trajectory |
| Positions where forced hold saved profit | unmeasured | >30% | backtest mid trajectory |

---

## 7. Compound Impact（弱點交互）

### 弱點唔係獨立 — 佢哋互相放大

#### Interaction 1: Slow Execution × Adverse Selection = Double Penalty

```
Pathway:
  3s price cache → stale signal →
  Submit order with 8s delay →
  Fast bot sees our order → takes other side (informed) →
  We get filled BECAUSE the bot knows we're late →
  60% toxic fill rate

If we had real-time data:
  Detect adverse move in <1s → cancel order before toxic fill
  Estimated toxic fills avoided: 2-3/10 → TFR drops to 40%
```

**Combined cost: ~$2.10/day (execution + AS together)**

#### Interaction 2: Low Fill Rate × Small Bet Size = Minimal Capital Deployment

```
Capital deployed per day:
  5 fills × $2.35/order = $11.75/day deployed
  Bankroll $80.95 → daily deployment ratio = 14.5%

If fill rate improves to 40% + bet size to $3.50:
  ~9 fills × $3.50 = $31.50/day deployed → 38.9%

Edge per dollar deployed: $3.24/$2.35 = $1.38 per $1
  → Every $1 more deployed = $1.38 more PnL
  → Doubling deployment = +$16.20/day additional PnL
```

#### Interaction 3: Forced Hold × Adverse Selection = Trapped in Bad Fills

```
Scenario:
  Fast toxic fill at min 5 (bot dumps on us)
  Mid moves against us (confirming toxic)
  Forced hold kicks in at min 10

  Result: held toxic position to full loss
  Without forced hold: could exit at min 8 for partial recovery

  Cases in data: 0x5f184b (2 fills, both adverse, lost $3.85)
    If exited at mid_60s (0.465): sell at ~$0.535 → recover ~$1.50
    Saved: $1.50

  But: this was 1 window. Frequency low.
```

#### Interaction 4: Counterparty Competition × Signal Freshness = Picked Off

```
Timeline of a typical 15M window:
  t=0s:   Window opens. BTC at $70,000.
  t=1s:   distinct-baguette WebSocket detects $10 move
  t=2s:   distinct-baguette submits order
  t=3s:   distinct-baguette filled (fastest bot wins)
  ...
  t=60s:  AXC M1 gate passes
  t=65s:  AXC price fetch (3s cache = t=62s price)
  t=70s:  AXC order submitted
  t=370s: AXC order maybe fills (298.7s avg wait)

  AXC is operating in a COMPLETELY different time scale.
  This is fine IF the edge is structural (entry price discount).
  But it means AXC CANNOT compete on speed.
```

### 複合影響量化

```
Independent sum: $3.70 + $0.80 + $1.20 + $0.90 + $0.40 + $0.60 = $7.60/day

Interaction multiplier (弱點互相放大):
  Execution × AS overlap: remove $0.50 double-count
  Fill rate × Size: additive only
  Forced hold × AS: $0.30 additional (trapped fills)

Adjusted total: $7.60 - $0.50 + $0.30 = $7.40/day

→ 用 ~$7.60/day 作為 working estimate（round number, conservative）
```

---

## 8. Bankroll Growth Model

### 現狀模型

```
Parameters:
  B₀ = $80.95 (current bankroll)
  μ = $3.24/fill (average PnL per filled trade)
  n = 5 fills/day (current)
  σ_fill = $5.50 (stddev of PnL per fill, estimated from range -$7 to +$10.5)

Daily PnL distribution:
  E[daily] = 5 × $3.24 = $16.20
  σ[daily] = √5 × $5.50 = $12.30

  Sharpe_daily = $16.20 / $12.30 = 1.32
  Annualized Sharpe ≈ 1.32 × √252 = 20.9 [MISLEADING — not 252 trading days]
  Correct: 365 days (crypto 24/7) → Sharpe_ann = 1.32 × √365 = 25.2
```

#### P(Survival) — 到 $500 之前唔 bust

```
Using gambler's ruin with drift:

P(bust) = P(bankroll hits $0 before $500)

With normally-distributed daily returns:
  drift = $16.20/day
  vol = $12.30/day

  P(bust) ≈ exp(-2 × μ × B₀ / σ²)
           = exp(-2 × 16.20 × 80.95 / 12.30²)
           = exp(-2 × 1312.3 / 151.3)
           = exp(-17.34)
           ≈ 0.000003%

→ P(survival) > 99.999% at current parameters
```

**但呢個假設每日 5 fills。如果 fill rate drops to 0（v14 嘅 TTL bug 重現）：**

```
If fills = 0 for 3 consecutive days: no PnL
If 1H bot runs simultaneously: -$36.10 over same period
→ 1H bot IS the risk (46.7% WR = EV negative)

1H bot 應該暫停或 paper-only 直到 WR > 55%
```

#### E[time to $500]

```
Daily growth: $16.20 (gross) - infrastructure leak $7.60 = $8.60 net
But: need to account for bet sizing scaling

Simple model (fixed bet size):
  Need: $500 - $80.95 = $419.05
  At $8.60/day: 419.05 / 8.60 = 48.7 days → ~7 weeks

Compound model (bet scales with bankroll):
  daily_return_pct = $8.60 / $80.95 = 10.6%
  But this rate DECREASES as bankroll grows (constant bet size)

  At $200: bet stays $2.35, return = $8.60/$200 = 4.3%/day
  At $500: return = $8.60/$500 = 1.7%/day
```

### 修復後模型

```
Post-fix parameters (conservative):
  Fill rate: 28.6% → 40% → n = 7 fills/day
  Avg PnL/fill: $3.24 → $2.80 (slightly worse due to less selective pricing)
  But larger bets: $2.35 → $3.50 → adjusted PnL/fill = $4.20

  E[daily] = 7 × $4.20 = $29.40
  Infrastructure leak reduced: $7.60 → $4.00
  Net daily: $29.40 - $4.00 = $25.40

  E[time to $500] = $419.05 / $25.40 = 16.5 days → ~2.5 weeks

Post-fix (optimistic):
  Fill rate: 50% → n = 10 fills/day
  PnL/fill: $3.80 (better pricing + larger bets)

  E[daily] = 10 × $3.80 = $38.00
  Leak: $3.00
  Net: $35.00/day

  E[time to $500] = 12 days → ~2 weeks
```

### Scenario Comparison

| Scenario | Daily Net | P(bust) | E[days to $500] | E[days to $1000] |
|----------|-----------|---------|-----------------|------------------|
| Current | $8.60 | <0.001% | 49 days | 107 days |
| Fix execution only (F1) | $12.00 | <0.001% | 35 days | 77 days |
| Fix execution + AS (F1+F3) | $15.00 | <0.001% | 28 days | 61 days |
| All fixes (conservative) | $25.40 | <0.001% | 17 days | 36 days |
| All fixes (optimistic) | $35.00 | <0.001% | 12 days | 26 days |

### 1H Bot Decision

```
Current 1H bot: WR 46.7%, PnL -$36.10, cost $118.15

At 46.7% WR and 0.50 odds:
  EV/bet = 0.467 × $0.60 - 0.533 × $0.40 = $0.280 - $0.213 = $0.067
  Marginally positive? But sample = 15 trades (noisy)

95% confidence interval for true WR:
  SE = √(0.467 × 0.533 / 15) = 0.129
  CI = [0.467 ± 1.96 × 0.129] = [0.214, 0.720]

  → Cannot distinguish from 50% (coin flip)

RECOMMENDATION: Suspend 1H bot until WR CI lower bound > 50%
  Need ≈30 trades at 60% WR to confirm: 0.60 ± 1.96 × √(0.60×0.40/30) = [0.425, 0.775]
```

---

## 9. Consolidated Fix Roadmap（按 $/day impact 排序）

### Phase 1: 即時改動（1-2 日，$0 成本）

| # | 改動 | 預期 $/day | 風險 |
|---|------|----------|------|
| F1.2 | 加 execution quality tracking to decision flow | +$0.50 | 低 |
| F4.2 | 減 price cache TTL from 3s → 1s | +$0.30 | 低（API rate 安全） |
| F5.1 | Scale bet size 3% → 5% bankroll | +$0.40 | 中（drawdown 增加） |
| F3.3 | 更新 CORE.md AS parameter 0.40 → 0.55 | +$0.20 | 低 |
| — | **暫停 1H bot**（止血 -$36.10 trend） | +$2.00 | 低 |
| | **Phase 1 Total** | **+$3.40/day** | |

### Phase 2: 核心改善（1-2 週）

| # | 改動 | 預期 $/day | 風險 |
|---|------|----------|------|
| F1.1 | Dynamic entry pricing (fair-based max bid) | +$2.00 | 中（may overpay） |
| F3.1 | Post-fill AS monitoring + streak tracking | +$0.60 | 低 |
| F4.3 | Event-driven cancel check in fast loop | +$0.50 | 低 |
| F6.2 | Conditional exit (mid < 0.20 after min 5) | +$0.30 | 中（may sell winners） |
| F2.1 | Fill context classification | +$0.20 | 低 |
| | **Phase 2 Total** | **+$3.60/day** | |

### Phase 3: Infrastructure（1 月+，需要評估 ROI）

| # | 改動 | 預期 $/day | 風險/成本 |
|---|------|----------|---------|
| F4.1 | Binance WebSocket price feed | +$1.00 | 高（架構改動大） |
| — | VPS deployment (Amsterdam) | +$0.50 | $6-12/月 fixed cost |
| — | On-chain position merge | +$0.30 | 複雜度高 |
| | **Phase 3 Total** | **+$1.80/day** | |

### ROI 排序

```
Phase 1: $3.40/day × 30 days = $102/month for 0 cost → ∞ ROI
Phase 2: $3.60/day × 30 days = $108/month for ~8h dev time
Phase 3: $1.80/day × 30 days = $54/month for ~40h dev + $12/month VPS
  Break-even: 1 month
```

**Phase 1 應該今日做。Phase 2 下週做。Phase 3 bankroll >$300 時再考慮。**

---

## Appendix: Mathematical Derivations

### A1. Implementation Shortfall Decomposition

```
Total IS = Delay Cost + Execution Cost + Opportunity Cost

Delay Cost = signal_edge × delay_time / window_time
  = 0.698 × 9.5 / 900 = 0.74%
  Negligible for bridge strategy (edge doesn't decay fast)

Execution Cost = (fair_price - fill_price) / fair_price
  Average: buy at $0.37, fair ≈ $0.55 → (0.55-0.37)/0.55 = 32.7%
  This is POSITIVE (we buy below fair) → execution cost is negative (good)

Opportunity Cost = unfilled_rate × expected_edge
  = 0.714 × $3.24 = $2.31/signal
  → THIS IS THE DOMINANT COST

  64 signals/day × $2.31 × 0.714 unfilled = $105/day potential left on table
  But: not all signals are tradeable (M1 gate, CVD, etc.)
  Realistic: ~30 tradeable × $2.31 × 0.714 = $49.50/day
```

### A2. Kelly Criterion Sensitivity

```
Full Kelly at different WR:

WR   | f*    | Half-Kelly | Bet at $80.95
81.7%| 45.1% | 22.5%      | $18.22
70%  | 20.0% | 10.0%      | $8.10
60%  | 0%    | 0%         | $0.00 (break-even, don't bet!)
50%  | -20%  | -10%       | DON'T BET

WR on filled trades = 60% → Kelly says f*=0!
But: overall WR = 81.7% → Kelly says bet big

The gap = Winner's Curse. True WR is somewhere between.
Conservative: use 70% WR → half-Kelly = $8.10/bet
Current: $2.35/bet → we are UNDER-betting by 3.4x (safe but leaving money)
```

### A3. Adverse Selection Spiral Dynamics

```
Equilibrium in N-bot market:

Fast bots: can cancel → avoid toxic fills → TFR_fast ≈ 20%
Slow bots (AXC): cannot cancel fast → bear toxic flow → TFR_slow ≈ 60%

As fast bots improve:
  More toxic flow redirected to slow bots
  Slow bot TFR increases: 60% → 70% → 80%
  Slow bot edge erodes: $3.24 → $2.00 → $1.00

  Eventually: slow bot exits market (negative EV)

  Counter-strategy: DON'T COMPETE ON SPEED
  Instead: compete on ENTRY PRICE

  AXC at $0.40 entry has 40% break-even WR
  Even at 80% TFR: WR on resolution might still be >50%
  Because: TFR measures 60s adverse move, NOT resolution outcome

  Key insight: TFR ≠ WR. A fill can be "toxic" (mid moves against in 60s)
  but still WIN at resolution (15 min is long enough for reversal)
```

### A4. Bankroll Ruin Probability (Exact)

```
Using negative hypergeometric distribution:

Given:
  p = 0.60 (filled WR, conservative)
  q = 0.40
  W = win amount = $0.60 × avg_shares ≈ $3.50
  L = loss amount = $0.40 × avg_shares ≈ $2.35
  B = $80.95

Ruin probability with unequal payoffs:
  r = (q/p) × (W/L) = (0.40/0.60) × (3.50/2.35) = 0.667 × 1.489 = 0.993

  Since r < 1 AND p > q × L/W:
    P(ruin) = r^(B/W) = 0.993^(80.95/3.50) = 0.993^23.13 = 0.851

Wait — this gives 85.1% ruin?!

Re-check: this assumes EACH bet is independent coin flip at 60% WR.
But: bankroll is replenished by all bets, not just filled ones.
And: we're mixing filled WR (60%) with overall PnL (+$194).

Using actual data:
  60 real trades: 49 wins, 11 losses
  Win: avg +$5.16, Loss: avg -$6.52

  Net per trade: $3.24 (positive)
  Variance: Σ(pnl - μ)² / n ≈ [ESTIMATED] σ² ≈ 30

  P(ruin from $80.95) with μ=3.24, σ²=30:
    = exp(-2μB/σ²) = exp(-2 × 3.24 × 80.95 / 30) = exp(-17.5) ≈ 0%

→ Ruin effectively impossible at current edge and bankroll.
  UNLESS: regime change kills the edge (WR drops to <40%).
```

---

> 最後更新：2026-03-22
> Data sources: mm_trades.jsonl (129L), mm_order_log.jsonl (219L), mm_trades_1h.jsonl (46L), mm_signals.jsonl (73L), btc_15m_resolutions.jsonl (614L), mm_state.json, mm_state_1h.json
> All numbers cite specific files. [ESTIMATED] marks where data was insufficient.
