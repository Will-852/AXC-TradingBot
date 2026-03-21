# 1H Conviction Bot Pipeline（基於 code）
> 來源：`run_1h_live.py` + `strategy/hourly_engine.py`
> 更新：2026-03-22
> 姊妹文件：`mm_v15_pipeline.md`（MM 15M bot）

---

## 時鐘：雙層循環

```
┌─ Fast Loop（每 10s）───────────────────────────────────┐
│  Fill Confirmation（get_trades / get_orders）           │
│  Resolution（window 結束 +2min 後）                     │
│  Profit Lock（mid ≥ 95¢ → sell 95%，每 cycle 都 check） │
└────────────────────────────────────────────────────────┘

┌─ Heavy Cycle（每 20s）─────────────────────────────────┐
│  Vol 1m（Binance 120 candles, 3s cache）                │
│  Bankroll refresh（CLOB balance）                       │
│  Discovery scan（每 300s，slug-based，BTC+ETH）          │
│  Conviction signal → ENTER / ADD / EXIT / HOLD / WAIT  │
│  Order submission（directional only，冇 hedge）          │
└────────────────────────────────────────────────────────┘
```

> **vs MM 15M**: MM 用 5s fast + 10s heavy。1H 用 10s fast + 20s heavy（因為 1H window 長啲，唔使咁頻密）。

---

## Discovery（每 300s）

**Slug pattern**:
```
{coin}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et
例：bitcoin-up-or-down-march-22-2026-3pm-et
```

- 掃 **3 個 hour** ahead（current + next 2）
- Coins：**BTC + ETH**（`_COIN_SLUGS = {"BTC": "bitcoin", "ETH": "ethereum"}`）
- 跳過已結束超過 300s 嘅 window
- Gamma API query by slug → `gamma.parse_market()` 取 condition_id + token IDs

---

## Signal Pipeline：Conviction Model

### Source 1 — Brownian Bridge（Normal CDF + haircut）
```
sigma = vol_1m × √minutes_remaining
d = log(price / open) / sigma
fair_up = Φ(d)                          ← Normal CDF（唔係 Student-t）
clamp [0.005, 0.995]

Fat-tail haircut（10%）:
  fair_up = 0.50 + (fair_up - 0.50) × 0.90
```

> **vs MM 15M**: MM 用 Student-t(ν=5) 直接做 CDF，冇 haircut。
> 1H 用 Normal + 10% haircut。兩者效果接近，但 Normal+haircut 更 conservative。

### Source 2 — OB Quality（唔直接改 fair，影響 conviction）
```
spread_factor = min(1.0, 0.02 / spread)
depth_factor = min(1.0, total_depth / 5000)
quality = √(spread_factor × depth_factor)      ← geometric mean

Override: depth ≥ 50,000 → quality floor 0.50
Unknown OB (spread=0, depth=0) → quality = 0.50
```

> **vs MM 15M**: MM 嘅 OB imbalance 直接加落 fair value（±5%）。
> 1H 嘅 OB quality 乘落 conviction（gate），唔改 fair value。

### Conviction 計算
```
direction = UP if fair_up ≥ 0.50 else DOWN
p_win = max(fair_up, 1 - fair_up)
confidence = (p_win - 0.50) × 2.0                    ← [0, 1]
time_trust = min(t_elapsed / 40, 1.0)                ← 飽和於 40 分鐘
ob_factor = quality if quality < 0.30 else 1.0        ← OB 差 = penalty
conviction = confidence × time_trust × ob_factor      ← [0, 1]
```

### 冇嘅嘢（vs MM 15M）
- ❌ CVD disagree override
- ❌ Cross-exchange divergence check
- ❌ M1 filter
- ❌ Traditional indicators（RSI/MACD/BB...）
- ❌ AI fallback（Claude/GPT）
- ❌ Signal conflict check

---

## Entry Gates

| Gate | 條件 | 結果 |
|------|------|------|
| Budget exhausted | `budget_remaining_frac ≤ 0` | HOLD（有 position）/ SKIP |
| Too late | `t_elapsed ≥ 56 min` | SKIP |
| Coin-flip | `|fair_up - 0.50| < 0.05` | WAIT |
| Conviction threshold | `conviction < dynamic_threshold` | WAIT |
| EV check | `EV ≤ 0` | SKIP |
| Min order size | `order_usd < $2.50` | SKIP |
| Mid sanity | `market_mid < $0.28` | SKIP |
| Budget hard block | `budget_left < $2.50` | SKIP |

### Dynamic Conviction Threshold
```
threshold = max(0.12, 0.33 - t_elapsed × 0.005)
```
- t=0: threshold = 0.33（嚴格）
- t=30min: threshold = 0.18
- t=42min+: threshold = 0.12（floor）

> **vs MM 15M**: MM 冇 conviction threshold。用 G1-G6 gate chain 代替。

---

## Pricing — Dynamic Spread

```
dynamic_spread = 0.15 × (1 - conviction × 0.7)
entry_price = p_win - dynamic_spread
```

然後 cap:
| Cap | Formula | Range |
|-----|---------|-------|
| Dynamic price cap | `0.25 + conviction × 0.12` | $0.25 – $0.37 |
| Hard ceiling | `max_entry_price = 0.39` | 硬上限（absolute） |
| EV cap | `p_win - 0.05` | min 5¢ EV per share |
| Floor | $0.20 | 硬底 |
| Sanity | `entry_price < p_win` | 否則 set `p_win - 0.02` |

> **vs MM 15M**: MM 用 Zone 1/2/3（hedge + directional），2-rung ladder，cap by confidence（$0.24-$0.40）。
> 1H 用 single directional order，cap by conviction（$0.25-$0.37），加 $0.39 硬上限。

---

## Sizing — Conviction² Scaling

```
size_fraction = 0.05 × conviction² × ob_quality
early_dampen = min(1.0, max(0.3, t_elapsed / 30))    ← t=0: 0.3×, t=30: 1.0×
size_fraction × = early_dampen
size_fraction = clamp(0.01, 0.05)
size_fraction × = budget_remaining_frac

size_usd = clamp($2.50, min(size_fraction × bankroll, budget_left))
```

**Budget per window**:
```
window_budget = bankroll × max_size_fraction          ← bankroll × 5%
budget_spent = filled_cost + pending_cost
budget_left = window_budget - budget_spent
```

**Multi-entry**: 唔用 tranche。Engine 每 cycle 重新評估，返回 ADD（同方向 + conviction 仲夠）→ 自然加注。

> **vs MM 15M**: MM 用 phased tranches（tranche 2-4，每 30s）。
> 1H 用 organic ADD（每 20s heavy cycle 自動評估）。

---

## Cancel Defense

**1H bot 冇 active cancel defense。**

| MM 15M 有 | 1H 有？ |
|-----------|---------|
| Window-end 2min cancel | ❌ |
| Adverse spot move cancel | ❌ |
| Dynamic TTL cancel | ❌ |
| Expired order cleanup | ✅（window 結束時清理） |

> 1H window 長（60 min），spot move 嘅影響比 15M window 低。
> 唯一 cleanup：window 結束後，unfilled orders 標記 EXPIRED + CLOB cancel。

---

## Fill Confirmation

同 MM 15M 相同 pattern:
```
get_trades(market=cid) → trade_order_ids
get_orders(market=cid) → open_ids

order_id ∈ trades     → FILLED（VWAP 累計 shares + avg_price）
order_id ∈ open_ids   → still on book
both 冇               → likely CANCELLED
window 結束           → EXPIRED（cancel on CLOB + log）
```

Fill 後記錄 `time_to_fill` → `mm_order_log_1h.jsonl`。

---

## Early Exit（2-Layer）

> **vs MM 15M 嘅 3-Layer**: 1H 冇 Cost Recovery layer。

### Layer 1 — Profit Lock（每 cycle check，唔止 heavy）
```
mid ≥ 0.95 →
  sell 95% shares at mid × 0.96（4% slippage）
  keep 5% as free roll
  + greed hedge: buy opposite side 2 shares at aggressive limit（mid × 2.0）
```

### Layer 2 — Stop Loss（via engine EXIT signal）
```
unrealized_pnl_pct < -49% → EXIT
```

> **vs MM 15M**: MM stop loss = -25%。1H = -49%（wider tolerance，因為 1H noise 大啲）。

### Strong Fair Flip → EXIT
```
Position = UP，但 fair < 0.40 → EXIT
Position = DOWN，但 fair > 0.60 → EXIT
```

### Mild Fair Flip → HOLD（唔 panic）
```
Direction changed 但唔 strong → HOLD，等下個 cycle 再評估
```

### 冇 Forced Hold
> **vs MM 15M**: MM 有 last 5 min forced hold。1H 冇。
> 但 `late_cutoff_min = 56` 阻止 minute 56+ 嘅 NEW entries。

### 冇 Scalp Re-Entry
> **vs MM 15M**: MM stop loss 後有 scalp re-entry（up to 3 rounds）。1H 冇。

---

## Resolution

```
觸發：now > window_end + 2min
數據：Binance kline（BTC/ETH，interval=1h，startTime = window_start）
結果：close ≥ open → "UP"；close < open → "DOWN"

PnL = resolve_market()  ← 共用 market_maker.py
payout = up_shares（如果 UP）或 down_shares（如果 DOWN）
realized_pnl = payout - total_cost
```

### 連敗保護（per-hour counting）
```
PnL < 0 → consecutive_losses + 1
  ≥ 5 consecutive hour-losses → 4h cooldown

PnL ≥ 0 → consecutive_losses = 0
```

> **vs MM 15M**: MM 按 per-market 計，5 consecutive → 24h cooldown。
> 1H 按 per-hour 計（同一小時嘅 BTC+ETH = 1 event），5 → 4h cooldown（更短）。

---

## Kill Switch

| 條件 | 行為 |
|------|------|
| daily PnL loss > **15%** of bankroll | 當日停止 |
| total PnL loss > **22%** of initial bankroll | **SOFT FUSE**: 切 dry-run + TG alert |
| 5 consecutive hour-losses | 4h cooldown |

> **vs MM 15M**:
> - MM daily = -20%, total = -20% hard stop。
> - 1H daily = -15%（更 tight），total = -22% soft fuse（唔 hard stop，切 dry-run 繼續收 data）。

**Soft fuse 機制**（`_TOTAL_LOSS_FUSE_PCT = 0.22`）:
- 第一次 live run 記錄 `initial_bankroll`
- 當 `total_pnl < -(initial_bankroll × 0.22)` → replace live client with mock → TG alert
- Bot 繼續跑（dry-run mode），唔完全停

---

## State Management

| 文件 | 路徑 | 用途 |
|------|------|------|
| State | `polymarket/logs/mm_state_1h.json` | 主 state（atomic write） |
| Trades | `polymarket/logs/mm_trades_1h.jsonl` | JSONL trade log |
| Orders | `polymarket/logs/mm_order_log_1h.jsonl` | Per-order lifecycle log |

**State schema**:
```json
{
  "markets": {},
  "watchlist": {},
  "daily_pnl": 0.0,
  "total_pnl": 0.0,
  "total_markets": 0,
  "bankroll": 100.0,
  "consecutive_losses": 0,
  "cooldown_until": "",
  "daily_pnl_date": "",
  "fill_stats": {"submitted": 0, "filled": 0, "cancelled": 0, "expired": 0}
}
```

Daily reset: `daily_pnl` 歸零 when date changes。

---

## CLI Flags

| Flag | 用途 |
|------|------|
| `--dry-run` | Mock client，冇真 orders |
| `--live` | 真 CLOB client |
| `--status` | Print state and exit |
| `--cycle` | Run 1 cycle then exit |
| `--verbose` | DEBUG logging |
| `--bankroll FLOAT` | Override bankroll |
| `--bet-pct FLOAT` | Override max_size_fraction |

`--dry-run` / `--live` / `--status` 互斥。

---

## HourlyConfig 全部參數

| 參數 | 默認值 | 用途 |
|------|--------|------|
| `time_saturation_min` | 40 | time_trust 飽和分鐘數 |
| `min_conviction_start` | 0.33 | t=0 嘅 conviction threshold |
| `min_conviction_decay` | 0.005 | threshold 每分鐘降幾多 |
| `min_conviction_floor` | 0.12 | threshold floor |
| `price_cap_base` | 0.25 | conviction=0 嘅 price cap |
| `price_cap_scale` | 0.12 | conviction 加幾多 cap |
| `max_entry_price` | 0.39 | 硬上限（absolute ceiling） |
| `min_entry_price` | 0.20 | 硬底 |
| `min_ev_per_share` | 0.05 | 最低 5¢ EV |
| `base_spread` | 0.15 | conviction=0 嘅 spread |
| `spread_compression` | 0.70 | conviction 壓縮 spread 嘅程度 |
| `max_size_fraction` | 0.05 | bankroll 5% per window |
| `min_size_fraction` | 0.01 | bankroll 1% minimum |
| `ob_spread_baseline` | 0.02 | "好" spread = 2¢ |
| `ob_depth_baseline` | 5000 | "好" depth = 5000 shares |
| `ob_bad_threshold` | 0.30 | quality < 0.30 = penalty |
| `ob_depth_override_mult` | 10.0 | 10× depth overrides spread penalty |
| `late_cutoff_min` | 56 | minute 56+ 唔入新 position |
| `min_fair_deviation` | 0.05 | fair 離 50% < 5¢ = coin-flip guard |
| `fat_tail_haircut` | 0.10 | 10% pull toward 0.50 |
| `stop_loss_pct` | -0.49 | -49% unrealized → EXIT |
| `min_market_mid` | 0.28 | Poly mid ≥ 28¢ |
| `min_order_usd` | 2.50 | 最低 order $2.50 |

---

## 完整流程一圖

```
Every 10s
│
├─ [Fast] Fill confirm, resolution, profit lock (mid≥95¢)
│
└─ [Heavy 每 20s]
    │
    ├─ Kill switch check (daily -15% / total -22% fuse)
    ├─ Cooldown check (4h after 5 consecutive hour-losses)
    ├─ Discovery (每 300s, slug-based, BTC+ETH, 3 hours ahead)
    │
    └─ Active markets → 逐個:
        │
        ▼
        conviction_signal(hourly_engine):
        │
        ├─ Budget exhausted?         → HOLD/SKIP
        ├─ t_elapsed ≥ 56 min?       → SKIP
        ├─ Bridge fair (Normal CDF + 10% haircut)
        ├─ Has position?             → check EXIT/HOLD
        ├─ |fair - 0.50| < 0.05?     → WAIT (coin-flip)
        ├─ conviction < threshold?    → WAIT
        ├─ Pricing: dynamic spread + caps
        ├─ Sizing: conviction² × ob_quality × early_dampen
        ├─ EV ≤ 0?                   → SKIP
        │
        └─ Action:
            ENTER → submit limit order (directional only)
            ADD   → submit additional order (same direction)
            EXIT  → sell at mid × 0.96
            HOLD  → do nothing
            WAIT  → re-evaluate next cycle
        │
        Position Management (10s loop):
          Fill confirm (get_trades) + per-order log
          Profit Lock (mid≥95¢ → sell 95% + greed hedge)
          │
          ▼
        Resolution (+2min after window end)
          → Win = $1/share, Lose = $0
          → PnL → state → trade log
```

---

## 1H vs MM 15M 完整對比

| 項目 | MM 15M Bot | 1H Conviction Bot |
|------|-----------|-------------------|
| Bridge CDF | Student-t(ν=5) | **Normal + 10% haircut** |
| OB effect | ±5% fair adjustment | **Conviction multiplier (gate)** |
| CVD | Disagree override | **None** |
| Indicators | Removed in v15 | **Never had** |
| AI fallback | Removed in v15 | **Never had** |
| Loop | 5s fast + 10s heavy | **10s fast + 20s heavy** |
| Cancel defense | 3 triggers | **None** |
| Pricing | Zone 1/2/3 + 2-rung ladder | **Single order, dynamic spread (cap $0.25-$0.37, ceiling $0.39)** |
| Hedge orders | Dual-Layer (UP+DOWN) | **Pure directional** |
| Risk modes | 4 modes by WR | **None** |
| Exit layers | 3 (profit lock + cost recovery + SL) | **2 (profit lock + SL)** |
| Stop loss | -25% | **-49%** |
| Forced hold | Last 5 min | **None** (late cutoff = 56 min) |
| Scalp re-entry | 3 rounds | **None** |
| Tranches | Phased (30s interval) | **Organic ADD** |
| Consecutive loss | Per-market, 24h cooldown | **Per-hour, 4h cooldown** |
| Daily loss limit | -20% | **-15%** |
| Total loss action | -20% hard stop | **-22% soft fuse (→ dry-run)** |
| Per-window budget | bankroll × bet_pct | **bankroll × 5%** |
| State file | mm_state.json | **mm_state_1h.json** |
