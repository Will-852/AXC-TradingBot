# MM v9 Signal Pipeline — 正確版（基於 code）
> 來源：`run_mm_live.py` + `strategy/market_maker.py`
> 更新：2026-03-20

---

## 時鐘：雙層循環

```
┌─ Fast Loop（每 5s）────────────────────────────────────┐
│  BTC 價格（3s cache）                                   │
│  OB imbalance 監控（活躍持倉）                           │
│  Cancel Defense（3 個 trigger）                          │
│  Fill Confirmation（get_trades）                         │
│  Early Exit（止蝕 / 止賺 / hold）                        │
│  Resolution（window 結束 +2min 後）                      │
└────────────────────────────────────────────────────────┘

┌─ Heavy Cycle（每 30s）─────────────────────────────────┐
│  Vol 1m（60s cache）                                    │
│  Bankroll refresh（CLOB balance）                        │
│  Risk mode 計算（rolling WR）                            │
│  Discovery scan（每 300s，slug-based）                    │
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

全部過晒 → 入 Signal Pipeline。

---

## Signal Pipeline：三源 + 混合

### Source 1 — Brownian Bridge（永遠有）
```
d = log(price / open) / (vol_1m × √mins_left)
P(Up) = Φ(d)        ← 標準正態 CDF
clamp [0.005, 0.995]
```

### Source 2 — Triple Signal（assess_edge）
```
3 選最佳：Indicator 8-指標 / CVD divergence / Microstructure
fallback：Claude AI（sonnet，temperature 0.3）

8 指標加權（assess_edge 內部）：
  RSI 20% | MACD 15% | BB 15% | EMA 10%
  Stoch 10% | VWAP 10% | Funding 10% | Sentiment 10%

output → signal_p_up ∈ [0, 1]，0 = 冇 signal
```

### Source 3 — OB Imbalance（Polymarket order book）
```
imbalance = (bid_vol − ask_vol) / (bid_vol + ask_vol)    ← ∈ [-1, +1]
ob_adjustment = imbalance × 0.05                          ← ±5% max
```

### 混合
```
如果 signal_p_up > 0：
  先 check conflict：signal 同 bridge 方向相反
    AND |signal_p_up − 0.50| > 0.03
    → SKIP（留 watchlist）

  冇 conflict → fair = signal × 0.70 + bridge × 0.30 + ob_adjustment

如果 signal_p_up = 0（冇 signal）：
  fair = bridge + ob_adjustment

clamp fair ∈ [0.05, 0.95]
```

> ⚠️ conflict 判斷：唔係「方向唔同就 skip」。signal 離 50% < 3% 嘅話，即使方向反都照入（deadzone）。

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
用途：Phase 2 taker 研究。

---

## plan_opening — Dual-Layer 定價

### Pricing Cap
| Layer | Max bid | 邏輯 |
|-------|---------|------|
| Hedge（兩邊） | $0.475 | combined ≤ $0.95 → guaranteed 5%+ edge |
| Directional（單邊） | $0.40 | win/loss = 1.5x（唔係 1.1x） |
| Floor | $0.25 | 低過呢個 = 市場強烈反對 |

Bid = min(cap, max(floor, fair − spread))，spread = 2.5%

### Zone 分配
| Zone | confidence | NORMAL | DEFENSIVE |
|------|-----------|--------|-----------|
| 1 | 0.50-0.57 | 100% hedge / 0% dir | same |
| 2 | 0.57-0.65 | 50% hedge / 50% dir | 70% / 30% |
| 3 | > 0.65 | 25% hedge / 75% dir | 40% / 60% |

Zone 1 + bankroll 唔夠 hedge（< ~$48）→ fallback directional（conf > 0.52 先）

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
| 2 | 逆向 spot move > 0.3%（BTC）/ 0.5%（ETH） | DIRECTIONAL only | hedge 留低 |
| 3 | 掛單 > 5 min 未成交 | DIRECTIONAL only | TTL 過期 |

Directional = 唔屬於等量 UP+DN hedge pair 嘅 order。

### Fill Confirmation
```
get_trades(market=cid) → trade_order_ids
get_orders(market=cid) → open_ids

order_id ∈ trades     → FILLED（confirmed）
order_id ∈ open_ids   → still on book
both 冇               → likely CANCELLED
window 結束           → EXPIRED（唔當 filled）
```

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

### 時間窗口
```
可以 sell：window start 到 window end − 2min
唔可以 sell：最後 2 分鐘 = forced hold
```
> 15 min window → 可 sell 範圍 = min 0 ~ min 13（唔係 2-11）

### Exit 邏輯
```
每個 side (UP / DOWN) 獨立 check：
  shares < 1 或 avg ≤ 0  → skip
  mid = Polymarket midpoint

  pnl_pct = (mid − avg) / avg

  LOSING:
    pnl_pct < −25%        → STOP LOSS sell @ mid × 0.97（3% discount）

  WINNING + EARLY (elapsed < 7min):
    pnl_pct > +30%         → TAKE PROFIT sell

  WINNING + LATE (elapsed ≥ 7min):
    → HOLD to resolution（趨勢加速，等 $1 payout）

  LAST 2 MIN:
    → Forced hold（sell 唔到）
```

### Exit 後 vs Resolution
- STOP / TAKE → shares 歸零，resolution payout = $0（已經 sell 咗）
- HOLD / FORCED → shares 保留，resolution payout = $1/share（贏）或 $0（輸）

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
├─ [Fast] BTC price, OB monitor, cancel, fills, exit, resolution
│
└─ [Heavy 每 30s]
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
        G4  cross-ex div < 0.3%?    ─ High → wait
        │
        Signal: bridge + triple + OB
        Blend: signal×70% + bridge×30% + OB
        │
        G5  M1 同 fair 方向一致?     ─ No → SKIP
        G6  Poly mid ≥ $0.38?       ─ No → SKIP
        │
        ▼
        plan_opening (Zone 1/2/3, risk_mode)
        → Submit GTC limit orders
        → Log signal → mm_signals.jsonl
        │
        Position Management (5s loop):
          Cancel (window_end/adverse/TTL)
          Fill confirm (get_trades)
          Tranche 2+ (heavy cycle)
          Early exit (stop/take/hold/forced)
          │
          ▼
        Resolution (+2min after window end)
          → Win = $1/share, Lose = $0
          → PnL → state → trade log
```
