# Findings — v3 Strategy C

## 真實數據總結（6 個錢包）

### 4 種策略分類
| 策略 | 代表 | 兩邊買 | 管理 | Avg Combined | $/trade |
|------|------|--------|------|-------------|---------|
| A: MM+管理 | k9q, BBB | ✅ | ✅ | $0.94-0.98 | $17-34 |
| B: Near-certain | BoneReader | ❌ | ❌ | N/A | $21 |
| **C: 兩邊+hold** | **Anon, LampStore** | **✅** | **❌** | **$0.97-0.98** | **$6-7** |
| D: 單邊 | j2f2 | ❌ | ❌ | N/A | -$8 |

### Strategy C 關鍵數據
- Anon: 79.3% WR, avg combined $0.979, 60% markets < $1.00
- LampStore: 68.6% WR, avg combined $0.970, 86% markets < $1.00
- Combined < $0.97 = **100% win rate**
- Combined > $1.00 = **0% win rate** but only 14% of markets
- Profit from < $1.00 > Loss from > $1.00 by **3.2x**
- 94.5% maker orders（零 fee）

### Polymarket 3 Revenue Streams
1. **Spread capture**: combined < $1.00 → 差額
2. **Maker rebate**: 20% of taker fees（~0.3% per fill at p=0.50）
3. **Liquidity rewards**: rewardsMaxSpread=4.5¢, rewardsMinSize=$50, daily USDC

### BTC 15M Market 參數
- rewardsMaxSpread: 4.5¢ → bid 必須 ≥ $0.455（if mid=0.50）
- rewardsMinSize: $50/order → 需要 $5K+ bankroll at 1%
- Taker delay: 250ms（maker 有優勢）
- CLOB min: 5 shares, tick: $0.01

### Order Book 真相
- 未開始嘅 market: $0.01 bid / $0.99 ask（98% spread）
- Active market: liquidity 集中喺 $0.01-$0.11 同 $0.89-$0.99
- 但 real trades 發生喺 $0.43-$0.57（near fair）
- 因為 bot 嘅 limit orders 就係 book 嘅一部分

## v1/v2 錯誤教訓
- half_spread 5% = 太遠，real data 顯示 2-3%
- add_winner = 拖低 Sharpe（43 → 6）
- Skip paper = $49 loss
- 假設 100% fill = unrealistic
- 冇查 order book = 建基於幻想
