# Task Plan — v3: Strategy C (Anon/LampStore 模式)

## 同 v1/v2 嘅分別

| | v1 (蝕 $49) | v2 (未跑) | v3 (而家) |
|---|---|---|---|
| 策略 | k9q 式 MM + 管理 | 純 spread capture | **Anon/LampStore 式** |
| Spread | 5%（$0.45 bid） | 5% | **2.5%（$0.475 bid）** |
| Combined | $0.90 | $0.90 | **$0.95** |
| Edge | 10%（假） | 10%（假） | **5%（真實驗證）** |
| 管理 | unwind + add_winner | 冇 | **冇** |
| 收入 | spread only | spread only | **spread + rebate + liquidity rewards** |
| Order type | GTC（假設 fill） | GTC | **Maker limit（真 maker）** |
| 驗證 | backtest only | backtest only | **6 個真錢包 + real order book** |

## 真實數據基礎

### Anon（$16K profit, 2,241 markets）
- Combined entry: avg $0.979
- Win rate: 79.3%
- Entry delay: 65 秒 after market open
- UP/DOWN gap: 22 秒 sequential
- 94.5% maker orders
- $7.18/market avg profit

### LampStore（$115K profit, 19,504 markets）
- Combined entry: avg $0.970
- Combined < $1.00: 85.9% of markets（100% win rate when < $0.97）
- Combined ≥ $1.00: 14.1%（0% win rate，但被 profit cover 3.2x）
- 94.5% maker
- $5.93/market avg profit

### Polymarket Rewards（3 個收入來源）
1. **Spread capture**: combined < $1.00 → 差額
2. **Maker rebate**: 20% of taker fees → 每 fill ~0.3% bonus
3. **Liquidity rewards**: rewardsMaxSpread=4.5¢, rewardsMinSize=$50, 每日派 USDC

### BTC 15M Market 參數
- rewardsMaxSpread: **4.5¢**（bid 必須喺 mid ± 4.5¢ 內先有 rewards）
- rewardsMinSize: **$50**（每 order ≥ $50 先有 rewards）
- minimum_order_size: **5 shares**
- taker delay: **250ms**（taker 有延遲，maker 有優勢）

## 策略設計

```
1. Market 開始後 30-60 秒，fetch BTC open price
2. Fair UP = 0.50, Fair DOWN = 0.50（開盤冇方向）
3. 掛 maker limit BUY:
   UP  @ $0.475（mid - 2.5¢，在 rewardsMaxSpread 4.5¢ 內）
   DOWN @ $0.475（同上）
   Combined = $0.95 → 5% structural edge
4. 等 fill。唔做任何管理。
5. Resolution → winning side $1.00, losing side $0
6. Payout $1.00 - cost $0.95 = +$0.05 per pair（if both fill）
7. 加上 maker rebate + liquidity rewards = 額外收入
```

### 風險場景

| 場景 | 概率 | 結果 |
|------|------|------|
| 兩邊 fill, combined < $1.00 | ~60% | **+5% profit** |
| 兩邊 fill, combined ≥ $1.00 | ~14% | **-2% loss（but covered by wins 3.2x）** |
| 只一邊 fill | ~20% | **50/50 方向性 bet** |
| 兩邊都冇 fill | ~6% | **$0（冇蝕冇賺）** |

### 單邊 fill 處理（v3 新增）
```
如果 UP fill 但 DOWN 冇 fill:
  → 30 秒後 check
  → DOWN 仲冇 fill → cancel DOWN order
  → 持有 UP position to resolution（接受 50/50 風險）
  → 因為買價 $0.475 < fair $0.50，expected value 仲係正
  → 但 variance 高
```

## Phases

### Phase 1: 重寫 market_maker.py `status: pending`
- 刪除所有 unwind / add_winner / management 邏輯
- 改 half_spread 到 2.5%（$0.475 bid）
- 加 rewardsMaxSpread check（唔好 bid outside reward zone）
- 加 rewardsMinSize check（order < $50 → log warning）
- 純 spread capture: open → hold → resolve

### Phase 2: 重寫 run_mm_live.py `status: pending`
- 簡化 cycle：discover → enter → wait → resolve
- 加 partial fill detection（30 秒後 check order status）
- 加 cancel unfilled orders logic
- Maker limit orders（唔係 FOK）

### Phase 3: Backtest with real parameters `status: pending`
- half_spread = 2.5%
- Fill rate = 30-60%（Anon 嘅 win rate 79% 暗示 fill rate ~80%）
- Fee = 0（maker）
- 30 天 + train/test split

### Phase 4: Paper run 24h `status: pending`
- **唔 skip。** 上次 skip paper 蝕 $49。
- 驗證：order placement、fill detection、resolution、PnL tracking

### Phase 5: Live micro-test `status: pending`
- 需要 ≥ $450 bankroll（1% × $450 = $4.50 → 5 shares minimum）
- 或 ≥ $5,000 bankroll（for $50/order liquidity rewards）
- 先用 $450 測 fill rate，再加錢攞 rewards

## Key Numbers

```
Bankroll: $450（minimum for 1% bet, 5 share min）
Bet: $4.50/market (1%)
Shares: 4.74/side → round to 5
Combined: $0.95
Edge: 5%
Profit/trade: $0.225（if both fill）

96 markets/day × 60% fill rate = 58 filled
58 × $0.225 = $13/day
Monthly: ~$390（87% ROI on $450）

如果加到 $5,000（for rewards）:
Bet: $50/market
Profit/trade: $2.50 + rebate + rewards
58 × $2.50 = $145/day + rewards ~$50/day = $195/day
Monthly: ~$5,850（117% ROI）
```

## Decisions

| # | Decision | Reason |
|---|----------|--------|
| 1 | half_spread = 2.5%（唔係 5%） | Real data: Anon/LampStore bid at $0.47-$0.49。5% 太遠，冇人 fill |
| 2 | 冇 management | Backtest 證明 management 拖低 Sharpe（43 vs 6）。Anon/LampStore 都冇 sell |
| 3 | Maker limit（唔係 FOK） | 94.5% 嘅成功 bot 用 maker。Maker = 零 fee + rebate + rewards |
| 4 | 唔 skip paper | 上次 skip 蝕 $49。Paper 驗 logic，live 驗 fill rate |
| 5 | Sequential UP → DOWN | Anon 數據：UP/DOWN 隔 22 秒。Check first fill 先落第二單 |
| 6 | $0.475 bid（唔係 $0.45） | $0.45 outside rewardsMaxSpread 4.5¢。$0.475 = mid-2.5¢ → inside |

## Errors from v1/v2（唔好重犯）

| Error | 點樣避免 |
|-------|---------|
| add_winner 炸倉 | v3 冇 management |
| Backtest 假設唔存在嘅 entry price | v3 用 real data 嘅 $0.475 |
| Skip paper | v3 強制 paper 24h |
| 冇查 order book | v3 用 real order book 數據設計 |
| GTC assumed filled | v3 加 fill detection logic |
| Fixed lot_size on small bankroll | v3 全部用 % of bankroll |
