# Findings — MM v4

## 用戶核心策略方向（2026-03-19 確認）

- 唔係強制單邊，唔係 equal 兩邊
- Hybrid asymmetric：信心高 → 偏重方向；信心低 → 接近 equal；冇 edge → skip
- 15 分鐘 zero-sum game — 短期 signal（order book, CVD, M1）比傳統 indicator 更 relevant
- 從過往數據驗證判斷準確度
- 用 order book + 最新成交判斷 10-15 分鐘趨勢

## Signal Pipeline（7 sources 全部可用但 MM 冇接入）

### 短期 signal（HIGH priority for 15M）

**Order Book** — `get_order_book(token_id)`
- bids/asks with price + size
- Imbalance = bid_depth / (bid_depth + ask_depth) → 買方力量
- `get_midpoint()`, `get_spread()`

**CVD (Cumulative Volume Delta)** — `assess_cvd_edge()`
- 20m Binance aggTrades → divergence detection
- `compute_dollar_imbalance()`: 5m buy_ratio - 15m buy_ratio
- 55-sec cache TTL
- Output: P(Up) via tanh [0.15, 0.85]

**Minute-1 Momentum**
- BTC price at minute 1 vs open
- |M1 ret| > 0.10% = stronger directional signal（+2.6% accuracy in backtest）

### 傳統 indicator（LOW priority — background context）

**assess_crypto_15m_edge()** → EdgeAssessment
- Field: `ai_probability`（唔係 `probability`）
- 8 indicators: RSI 20%, MACD 15%, BB 15%, EMA 10%, Stoch 10%, VWAP 10%, Funding 10%, Sentiment 10%
- tanh compression → P(Up) ∈ [0.15, 0.85]

### Microstructure — `assess_microstructure_edge()`
- 25 × 5m klines → vol_ratio, ret_5m
- Lookup table from 90-day OOS backtest

## Backtest Reference（另一個 agent，OOS verified）

- Hybrid >0.55: 75.8% WR, $14.3/day, Sharpe 68.9
- Hybrid >0.60: 86.2% WR, $13.2/day, Sharpe 79.0
- |M1 ret| > 0.10% filter: +2.6% accuracy, -30% trades
- Train ≈ Test（唔係 overfit）

## Stress Test (360d Monte Carlo, $4/trade fixed)

| Scenario | WR drop | Fill | Adverse | Result | Status |
|----------|---------|------|---------|--------|--------|
| Ideal | 0% | 100% | 0% | +$35,133 | ✅ |
| Realistic | -3% | 80% | -5% | +$18,707 | ✅ |
| Pessimistic | -5% | 60% | -10% | +$7,886 | ✅ |
| Very bad | -8% | 50% | -15% | +$663 | ⚠️ |
| Nightmare | -10% | 40% | -20% | -$3,588 | 🔴 |

Break-even: fill=60% + adv=10% → WR can drop 14% (68→54%) before losing.
Hedge layer = safety net: guaranteed profit regardless of directional accuracy.
Strategy dies only if WR-10% + Fill 40% + Adverse 20% ALL happen simultaneously.

## Opus Code Review — 12 Issues

### RED (5)
1. Cancelled orders counted as fills → phantom shares
2. Dedup "market" field may not match condition_id
4. `ind_result.probability` doesn't exist → indicator never used
8. No GTC cancel before window end → adverse selection
9. Entry filter contradicts MIN_CONFIDENCE → almost no trades

### YELLOW (5)
5. Docstrings outdated
7. Fill reset wipes instant fills (latent)
10. _to_dict strips pending_orders
11. $50 absolute daily loss limit (should be %)
12. Binance ≠ Chainlink resolution
