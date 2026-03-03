---
name: scan-rules
description: Entry criteria and signal scoring rules for all trading pairs
---

# Scan Rules — Entry Criteria & Signal Scoring

## 市場模式（4H Timeframe 投票）

### RANGE 模式條件
- RSI 40-60（無明確方向）
- MACD histogram 接近零
- 成交量低於平均
- MA50 同 MA200 水平排列
- Funding rate 正常（<0.01%）

### TREND 模式條件
- RSI >60 或 <40（有方向）
- MACD histogram 持續擴大
- 成交量高於平均
- MA50 同 MA200 有明確斜率
- Funding rate 極端

---

## RANGE 策略（Mode A）入場條件

### 前置條件（必須全部通過）
- R0: BB width < 0.05（1H）— 確認區間收窄
- R1: ADX < 20（1H）— 確認無趨勢

### LONG 信號（需 C1+C2+C3）
- C1: price ≤ bb_lower × 1.005 — BB 下軌觸碰
- C2: RSI < 35 且 RSI > prev_RSI — 超賣反轉
- C3: price ≤ rolling_low × 1.005 — 支撐位附近
- C4（可選）: stoch_k < 20 且 stoch_k > stoch_d — 隨機指標確認 → STRONG

### SHORT 信號（需 C1+C2+C3）
- C1: price ≥ bb_upper × 0.995 — BB 上軌觸碰
- C2: RSI > 65 且 RSI < prev_RSI — 超買反轉
- C3: price ≥ rolling_high × 0.995 — 阻力位附近
- C4（可選）: stoch_k > 80 且 stoch_k < stoch_d → STRONG

### RANGE 評分
- WEAK signal: C1+C2+C3 → score 3.0
- STRONG signal: C1+C2+C3+C4 → score 4.0

---

## TREND 策略（Mode B）入場條件

### LONG（4 KEY 全部通過，或 3/4 有日期偏差）
- KEY1: price > MA50(4H) AND price > MA200(4H) — 上升趨勢結構
- KEY2: MACD histogram > 0 且持續擴大 — 動能確認
- KEY3: 1H RSI 40-55 — 唔超買，有空間
- KEY4: price 距離 1H MA50 < 1.5% — 回調入場點

### SHORT（4 KEY 全部通過，或 3/4 有日期偏差）
- KEY1: price < MA50(4H) AND price < MA200(4H) — 下降趨勢結構
- KEY2: MACD histogram < 0 且持續擴大
- KEY3: 1H RSI 45-60
- KEY4: price 距離 1H MA50 < 1.5%

### 日期偏差（降低至 3/4 KEY）
- 週四 21:00-01:00 UTC+8 → SHORT 偏差
- 週五 21:00-03:00 UTC+8 → LONG 偏差

### TREND 評分
- BIAS signal: 3/4 KEY → score 3.5
- STRONG signal: 4/4 KEY → score 5.0

---

## 信號選擇優先級

1. 最高 score 勝出
2. 同分時：BTC(4) > ETH(3) > XRP(2) > XAG(1)

## Pair-specific 覆蓋

| Pair | RSI Long | RSI Short | BB Touch Tolerance |
|------|----------|-----------|-------------------|
| BTCUSDT | 30 | 70 | 0.005 |
| ETHUSDT | 30 | 70 | 0.005 |
| XRPUSDT | 35 | 65 | 0.008 |
| XAGUSDT | 30 | 70 | 0.005 |
