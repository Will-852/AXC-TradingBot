# MM v15 Signal Pipeline（基於 code）
> 來源：`run_mm_live.py` + `strategy/market_maker.py`
> 更新：2026-03-21
> 取代：`mm_v9_pipeline_correct.md`（已過時）

---

## 時鐘：雙層循環

```
┌─ Fast Loop（每 5s）────────────────────────────────────┐
│  BTC/ETH 價格（3s cache）                               │
│  OB imbalance 監控（活躍持倉）                           │
│  Cancel Defense（3 個 trigger）                          │
│  Fill Confirmation（get_trades）                         │
│  Per-order lifecycle log → mm_order_log.jsonl            │
│  Post-fill 60s midpoint check（AS measurement）          │
│  Early Exit（3-layer: profit lock / cost recovery / SL） │
│  Resolution（window 結束 +2min 後）                      │
└────────────────────────────────────────────────────────┘

┌─ Heavy Cycle（每 10s）─────────────────────────────────┐
│  Vol 1m（60s cache）                                    │
│  Bankroll refresh（CLOB balance）                        │
│  Risk mode 計算（rolling WR）                            │
│  Discovery scan（每 300s，slug-based，BTC+ETH）           │
│  Signal Pipeline → plan_opening → Submit                │
│  Phased entry（tranche 2-4）                             │
└────────────────────────────────────────────────────────┘
```

---

## Gate 1-4：入場過濾（Heavy Cycle 內）

從 watchlist 逐個 market 過 gate：

```
G1  M1 Wait          elapsed < 60s?        → 留 watchlist，下個 cycle 再睇
G2  Late Gate         remaining < 4min?     → SKIP 刪出 watchlist
G3  M1 Filter         |M1 return| < 1σ?    → elapsed < 3min：等
                                            → elapsed ≥ 3min：SKIP
                      σ = max(0.0005, vol_1m × 1.0)
G4  Cross-exchange    divergence > 0.3%?    → 留 watchlist（唔 skip，等 recover）
    3 所：Binance + OKX + Bybit，取 median
```

> **--continuous-momentum 模式**（可選 flag）：
> 取代 M1 return，改用 `log(current/open)`，threshold = `max(0.0005, vol_1m × √elapsed_mins × 0.7)`

全部過晒 → 入 Signal Pipeline。

---

## Signal Pipeline：Bridge + OB

> ⚠️ v9 嘅 Triple Signal（assess_edge: indicator/CVD/microstructure + AI fallback）**已移除**。
> 原因：傳統指標（RSI/MACD/BB/EMA）係 backward-looking，cause mean-reversion bias in trending markets。
> v15 只用 Bridge + OB，足夠 15M binary。

### Source 1 — Brownian Bridge（永遠有）
```
d = log(price / open) / (vol_1m × √mins_left)
P(Up) = T₅(d)       ← Student-t CDF, ν=5（fat-tail correction）
clamp [0.005, 0.995]
```

> **Student-t vs Normal（v15 改動）：**
> Normal CDF + 10% haircut 過度 conservative → 丟失 3-5pp edge。
> Student-t(ν=5) 自然有 fat tails，回收呢部分 edge。

### Source 2 — OB Imbalance（Polymarket order book）
```
imbalance = (bid_vol − ask_vol) / (bid_vol + ask_vol)    ← ∈ [-1, +1]
ob_adjustment = imbalance × 0.05                          ← ±5% max
```

### 混合
```
fair = bridge + ob_adjustment
clamp fair ∈ [0.05, 0.95]
```

### CVD Disagree Override
```
如果 CVD 強烈反對 fair 方向：
  → 覆蓋所有 orders 為 single cheap rung at fair × 0.60
  → 減少 exposure when flow disagrees
```

---

## Gate 5-6：方向確認

```
G5  M1 vs fair       |M1| ≥ 0.001 且方向相反?  → SKIP（留 watchlist）
G6  Poly mid check   我哋買嗰邊 mid < $0.38?    → SKIP（留 watchlist）
    （live 先 check，dry-run skip）
```

---

## Signal log

過晒所有 gate 後、落注前，寫一行去 `mm_signals.jsonl`：
```json
{"ts", "cid", "sym", "m1", "m1_sigma", "bridge", "signal", "fair", "xdiv", "ob_adj"}
```

---

## plan_opening — Dual-Layer 定價

### Pricing Cap — Dynamic（v15 改動）
| Layer | Default cap | 邏輯 |
|-------|------------|------|
| Hedge（兩邊） | $0.475 | combined ≤ $0.95 → guaranteed 5%+ edge |
| Directional（單邊） | **Dynamic** | 按 confidence 調整（見下） |
| Floor | $0.25 | 低過呢個 = 市場強烈反對 |

**Directional cap by confidence（v15 新增）：**
| confidence | max_directional_bid |
|-----------|-------------------|
| ≥ 0.70 | $0.40 |
| ≥ 0.62 | $0.35 |
| ≥ 0.57 | $0.28 |
| < 0.57 | $0.24 |

Bid = min(cap, max(floor, fair − spread))，spread = 2.5%

### Zone 分配
| Zone | confidence | NORMAL | DEFENSIVE |
|------|-----------|--------|-----------|
| 1 | 0.50-0.57 | 100% hedge / 0% dir | same |
| 2 | 0.57-0.65 | 50% hedge / 50% dir | 70% / 30% |
| 3 | > 0.65 | 25% hedge / 75% dir | 40% / 60% |

Zone 1 + bankroll 唔夠 hedge（< ~$48）→ fallback directional（conf > 0.52 先）

### 2-Rung Directional Ladder（v15 新增）
Zone 2/3 有足夠 budget 時，directional orders 分 2 rung：
```
Rung 1 (bottom): dir_bid − 0.03（3¢ cheaper）
Rung 2 (top):    dir_bid
```

### Budget
```
full_budget = min(bankroll × bet_pct, bankroll × 5%)
per_tranche = full_budget / total_tranches
CLOB minimum = 5 shares/order
```

### Risk Mode（基於 rolling 30 場 WR）
| WR | Mode | 行為 |
|----|------|------|
| ≥ 58% | NORMAL | 正常 dual-layer |
| 54-58% | DEFENSIVE | hedge 比例加大 |
| 48-54% | HEDGE_ONLY | 只 hedge，0% directional |
| < 48% | STOPPED | 完全停止，要人手 review |

---

## Submit → Position Management（5s loop）

### Cancel Defense — 3 個獨立 Trigger
| # | 條件 | 取消範圍 | 備注 |
|---|------|---------|------|
| 1 | window end 前 2 min | ALL pending | 最後防線 |
| 2 | 逆向 spot move > **0.5%**（BTC）/ **0.7%**（ETH） | DIRECTIONAL only | hedge 留低 |
| 3 | 掛單 > **dynamic TTL**（60s–600s） | DIRECTIONAL only | min(10min, window_end−3min−entry_ts) |

> **v15 改動：** T2 由 0.3%/0.5% 放寬至 0.5%/0.7%（v14 live 太 tight，6/6 orders 全 cancel）。
> T3 由固定 5min 改為 dynamic TTL，按 entry 時間同 window 剩餘時間計算。

Directional = 唔屬於等量 UP+DN hedge pair 嘅 order。

### Per-Order Lifecycle Log（v15 新增）
每個 order 嘅 submit/fill/cancel/post_fill 事件寫入 `mm_order_log.jsonl`：
```json
{"ts", "event", "order_id", "cid", "side", "price", "size", "time_to_fill", "mid_at_fill"}
```
用途：AS diagnostic（time_to_fill vs WR），fill rate by cancel reason。

### Fill Confirmation
```
get_trades(market=cid) → trade_order_ids
get_orders(market=cid) → open_ids

order_id ∈ trades     → FILLED（confirmed）
order_id ∈ open_ids   → still on book
both 冇               → likely CANCELLED
window 結束           → EXPIRED（唔當 filled）
```

**Post-fill 60s midpoint check**：fill 後 60 秒記錄 mid price，計算 adverse selection cost。

### Phased Entry（tranche 2+，heavy cycle only）
```
條件：
  - phase = OPEN
  - tranches_done < tranches_total
  - elapsed ≥ tranche_interval(30s) × tranches_done
  - remaining > 3 min
  - Poly mid 我哋嗰邊 ≥ $0.38
  - 方向未反轉（UP: fair ≥ 0.45 / DOWN: fair ≤ 0.55）
```

---

## Early Exit（5s loop，fills_confirmed = True 先 check）

> ⚠️ **v15 完全重新設計。** v9 嘅 pnl_pct +30%/-25% 邏輯已替換。

### 時間窗口
```
可以 sell：window start 到 window end − 5min
唔可以 sell：最後 5 分鐘 = forced hold
```

### 3-Layer Exit（每個 side UP/DOWN 獨立 check）

**Layer 1 — Profit Lock（mid ≥ 95¢）**
```
mid ≥ 0.95 →
  sell 90% shares（lock profit）
  keep 10%（free roll to resolution）
  + buy opposite side 5 shares at aggressive limit（mid × 1.50, cap $0.15）= greed hedge
```

**Layer 2 — Cost Recovery（mid ≥ 64¢）**
```
mid ≥ 0.64 →
  sell enough shares to recover full entry cost
  keep remaining shares as free roll（zero-risk position）
```

**Layer 3 — Stop Loss（pnl_pct < -25%）**
```
pnl_pct = (mid − avg) / avg
pnl_pct < −0.25 →
  sell ALL at mid × 0.97（3% discount for immediate fill）
```

### Scalp Re-Entry（v15 新增，Stop Loss 後觸發）
```
Stop loss 後唔直接放棄 → 重跑 signal pipeline：
  - 最多 3 rounds per window（_MAX_ROUNDS = 3）
  - Round 2: bid × 0.90（10% discount）
  - Round 3: bid × 0.80（20% discount）
  - 每 round 都要通過 regime change check
```

### Exit 後 vs Resolution
- PROFIT LOCK → 90% sold, 10% kept → resolution payout on remaining
- COST RECOVERY → partial sold, rest = free roll → resolution payout
- STOP LOSS → all sold, possible scalp re-entry
- FORCED HOLD → all shares kept → resolution payout = $1/share（贏）或 $0（輸）

---

## Resolution

```
觸發：now > window_end + 2min
數據：Binance kline（BTC/ETH，15m interval，startTime = window_start）
結果：close ≥ open → "UP"；close < open → "DOWN"

payout = up_shares（如果 UP）或 down_shares（如果 DOWN）
PnL = payout − entry_cost
```

### 連敗保護
```
PnL < 0 → consecutive_losses + 1
  ≥ 5 consecutive → 24h cooldown

PnL ≥ 0 → consecutive_losses = 0
```

---

## Kill Switch（每個 cycle check）

| 條件 | 行為 |
|------|------|
| daily PnL < −20% of wallet | 當日停止 |
| total PnL < −20% of initial bankroll | **HARD STOP**（要手動清 flag 先 resume） |
| 5 consecutive losses | 24h cooldown |
| WR < 48%（30 場 rolling） | STOPPED mode |

### Newbie Protection（首 3 小時 live）
```
bet_pct = 1%（正常都係 1%，但 max_markets = 1）
max_concurrent_markets = 1
```

---

## 完整流程一圖

```
Every 5s
│
├─ [Fast] BTC/ETH price, OB monitor, cancel, fills, exit, resolution
│         per-order log, post-fill AS check
│
└─ [Heavy 每 10s]
    │
    ├─ Kill switch check
    ├─ Risk mode (WR rolling 30)
    ├─ Discovery (每 300s, slug-based, BTC+ETH)
    │
    └─ Watchlist → 逐個 market:
        │
        G1  elapsed > 60s?          ─ No → wait
        G2  remaining > 4min?       ─ No → SKIP
        G3  M1 ≥ 1σ?               ─ Weak < 3min → wait
                                    ─ Weak ≥ 3min → SKIP
            (or --continuous-momentum: log(cur/open) vs vol×√t×0.7)
        G4  cross-ex div < 0.3%?    ─ High → wait
        │
        Signal: bridge(Student-t ν=5) + OB(×0.05)
        CVD disagree? → single cheap rung override
        │
        G5  M1 同 fair 方向一致?     ─ No → SKIP
        G6  Poly mid ≥ $0.38?       ─ No → SKIP
        │
        ▼
        plan_opening (Zone 1/2/3, risk_mode)
        → Dynamic directional cap (0.40/0.35/0.28/0.24)
        → 2-rung ladder (Zone 2/3)
        → Submit GTC limit orders
        → Log signal → mm_signals.jsonl
        │
        Position Management (5s loop):
          Cancel (window_end 2min / adverse BTC 0.5% ETH 0.7% / dynamic TTL)
          Fill confirm (get_trades) + per-order log
          Post-fill 60s AS check
          Tranche 2+ (heavy cycle)
          Early exit:
            L1 Profit Lock (mid≥95¢ → sell 90% + greed hedge)
            L2 Cost Recovery (mid≥64¢ → recover cost, keep free roll)
            L3 Stop Loss (<-25% → sell all @ mid×0.97)
          Scalp re-entry (after SL, up to 3 rounds, R2×0.90 R3×0.80)
          Forced hold (last 5 min)
          │
          ▼
        Resolution (+2min after window end)
          → Win = $1/share, Lose = $0
          → PnL → state → trade log
```

---

## v9 → v15 Changelog

| 項目 | v9 | v15 |
|------|-----|-----|
| Heavy cycle | 30s | **10s** |
| Bridge CDF | Normal Φ(d) | **Student-t(ν=5)** |
| Signal blend | signal×0.70 + bridge×0.30 + OB | **bridge + OB only**（assess_edge 移除） |
| Cancel T2 threshold | BTC 0.3% / ETH 0.5% | **BTC 0.5% / ETH 0.7%** |
| Cancel T3 TTL | 固定 5min | **Dynamic 60s–600s** |
| Directional cap | 固定 $0.40 | **Dynamic 0.40/0.35/0.28/0.24** |
| Directional orders | Single order | **2-rung ladder**（3¢ step） |
| Early exit profit | pnl_pct > +30% | **Profit Lock (mid≥95¢) + Cost Recovery (mid≥64¢)** |
| Early exit stop | pnl_pct < -25% | pnl_pct < -25%（unchanged） |
| Forced hold | Last 2 min | **Last 5 min** |
| G5 deadzone | "3% deadzone" | **唔存在**（any conflict = skip） |
| Post-SL | 放棄 | **Scalp re-entry**（3 rounds, R2×0.90, R3×0.80） |
| Order logging | 冇 | **mm_order_log.jsonl**（submit/fill/cancel/post_fill） |
| AS measurement | 冇 | **Post-fill 60s midpoint check** |
| CVD override | 冇 | **Strong disagree → single cheap rung** |
| Greed hedge | 冇 | **Profit lock → buy opposite 5 shares** |
