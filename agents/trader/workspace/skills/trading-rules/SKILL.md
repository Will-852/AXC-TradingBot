---
name: trading-rules
description: Complete entry/exit rules, position sizing, risk management, and circuit breaker conditions
---

# Trading Rules — 完整交易規則

## RANGE 策略入場（Mode A）

### 前置條件
- R0: BB width < 0.05（1H）
- R1: ADX < 20（1H）

### LONG 入場
- C1: price ≤ bb_lower × 1.005
- C2: RSI < 35 且 RSI > prev_RSI（反轉）
- C3: price ≤ rolling_low × 1.005
- C4（STRONG）: stoch_k < 20 且 stoch_k > stoch_d

### SHORT 入場
- C1: price ≥ bb_upper × 0.995
- C2: RSI > 65 且 RSI < prev_RSI
- C3: price ≥ rolling_high × 0.995
- C4（STRONG）: stoch_k > 80 且 stoch_k < stoch_d

### RANGE 出場
- TP1: price 到 BB basis → 平 50%
- TP2: price 到對面 BB band → 平剩餘
- SL: 由 exchange order 管理

---

## TREND 策略入場（Mode B）

### LONG（需 4/4 KEY，或 3/4 有日期偏差）
- KEY1: price > MA50(4H) AND price > MA200(4H)
- KEY2: MACD histogram > 0 且持續擴大
- KEY3: 1H RSI 40-55
- KEY4: price 距離 1H MA50 < 1.5%

### SHORT（需 4/4 KEY，或 3/4 有日期偏差）
- KEY1: price < MA50(4H) AND price < MA200(4H)
- KEY2: MACD histogram < 0 且持續擴大
- KEY3: 1H RSI 45-60
- KEY4: price 距離 1H MA50 < 1.5%

### 日期偏差
- 週四 21:00-01:00 UTC+8 → SHORT（3/4 足夠）
- 週五 21:00-03:00 UTC+8 → LONG（3/4 足夠）

### TREND 出場
- MACD 4H histogram 反轉（sign flip）
- Price 返回 MA50 同 MA200 之間
- 3+ mode votes 轉為 RANGE

---

## Position Sizing

### 計算公式
```
risk_amount = balance × risk_pct
sl_distance = entry - sl_price  (for LONG)
position_size = risk_amount / sl_distance
notional = position_size × entry_price
margin = notional / leverage
```

### SL 計算
- Range: SL = entry ± 1.2 × ATR(14)
- Trend: SL = entry ± 1.5 × ATR(14)

### TP 計算
- TP1 = entry + (sl_distance × min_rr)
- TP2 = next S/R level

### 再入場 Size 調整
| 連續虧損 | Size 倍率 |
|----------|-----------|
| 0 | 100% |
| 1 | 70% |
| 2 | 50% |
| ≥3 | 冷卻 4 小時 |

---

## Risk Management

### 每筆交易
- 最大風險: 2% of balance
- 止損距離: 1.2-1.5 × ATR
- 最低 R:R: 2.3 (Range), 3.0 (Trend)

### 每日限制
- 日虧上限: 15% of balance
- 超過即停止所有交易至次日

### 倉位限制
- BTC + ETH: 最多 1 倉（共用）
- XRP: 最多 1 倉
- XAG: 最多 1 倉
- 全部: 最多 3 倉同時

---

## Circuit Breaker 熔斷條件

### 自動觸發
| 條件 | 動作 | 冷卻時間 |
|------|------|----------|
| 連續 3 次虧損 | 停止交易 | 4 小時 |
| 日虧 >15% | 停止交易 | 至次日 |
| 單倉虧損 >25% | 立即平倉 | 1 小時 |
| API 連續 3 次失敗 | 停止交易 | 30 分鐘 |
| 餘額 < $20 | 停止交易 | 無限期 |

### 冷卻期行為
- 唔開新倉
- 繼續監控現有倉位
- 繼續執行 SL/TP
- 冷卻結束後自動恢復

---

## Exchange Config (Aster DEX)

- API: https://fapi.asterdex.com/fapi/v1
- Order types: LIMIT, MARKET, STOP_MARKET
- 永續合約，USDT 結算
- Funding interval: 8 小時
- 最小 order size: 見 EXCHANGE_CONFIG.md
