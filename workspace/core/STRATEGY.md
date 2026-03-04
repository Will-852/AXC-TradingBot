# core/STRATEGY.md — 完整交易策略
# 版本: 2026-03-02
# 來源: STRATEGY_FINAL.md（2026-02-28）
# 交易對: BTC/USDT, ETH/USDT, XRP/USDT, XAG/USDT

---

## 優先順序（衝突時按此執行）

```
1. SAFETY RULES         → 永遠執行，不可 override
2. NO-TRADE CONDITIONS  → 封鎖入場，無論信號多強
3. MARKET MODE          → 偵測 Range / Trend / Scalp
4. STRATEGY EXECUTION   → 按模式執行正確策略
5. DYNAMIC ADJUSTMENT   → 入場後才微調
```

---

## 模式偵測（4H，5 指標）

| 指標 | TREND 信號 | RANGE 信號 |
|------|-----------|-----------|
| RSI(14) | <32 或 >68 | 32-68 |
| MACD | Histogram 擴大 | Histogram 收窄/近零 |
| Volume | <50% 或 >150% avg | 50-150% avg |
| MA(50+200) | Price clearly 上/下穿兩條 MA | Price 在兩條 MA 之間 |
| Funding | >±0.07% | -0.07% ~ +0.07% |

- 3+ TREND 信號 → TREND MODE
- 3+ RANGE 信號 → RANGE MODE
- 平手 → 維持當前模式
- **模式切換需連續 2 次確認**

---

## Mode A — Range Trading

### Range 前置條件（R0 + R1 + R2 全部成立才評估入場）

```
R0: BB 寬度收斂 → bb_width = (bb_upper - bb_lower) / bb_basis < 0.05
R1: 趨勢強度低 → ADX(14) < 20（4H）/ < 18（15m/1h）
R2: 價格拉鋸   → |ema_slow - ema_slow.shift(10)| / close < 0.015

任何一個 FAIL → 跳過 Range 入場，評估 Trend 或 HOLD
工具: python3.11 tools/indicator_calc.py --mode range 可自動判斷
```

### 入場條件（強信號 = C1+C2+C3+C4 全中 / 弱信號 = C1+C2+C3）

**LONG:**

| 條件 | 公式 | 說明 |
|------|------|------|
| C1 | close ≤ bb_lower × (1 + 0.005) | 觸及 BB 下軌 |
| C2 | RSI < rsi_long AND RSI > RSI_prev | RSI 超賣回升 |
| C3 | close ≥ rolling_low × 0.995 | 接近支撐區 |
| C4 | Stoch %K < 20, %K 上穿 %D | Stoch 確認（可選） |

**SHORT:**

| 條件 | 公式 | 說明 |
|------|------|------|
| C1 | close ≥ bb_upper × (1 - 0.005) | 觸及 BB 上軌 |
| C2 | RSI > rsi_short AND RSI < RSI_prev | RSI 超買回落 |
| C3 | close ≤ rolling_high × 1.005 | 接近阻力區 |
| C4 | Stoch %K > 80, %K 下穿 %D | Stoch 確認（可選） |

**RSI 閾值（按產品/時間框）：**

| 時間框 | 預設 rsi_long | 預設 rsi_short | ETH 覆蓋 | XRP 覆蓋 |
|--------|-------------|--------------|---------|---------|
| 15m | 30 | 70 | — | — |
| 1h | 35 | 65 | 32 / 68 | — |
| 4h | 35 | 65 | 32 / 68 | — |

### 兼容現有 KEY 系統

原有 3 KEY 仍然有效作為補充確認：
- RSI(1H) < 40 / > 60（KEY 1 — 與 C2 重疊）
- MACD(1H) crossover（KEY 2 — 趨勢確認）
- Price ±0.5% of S/R（KEY 3 — 與 C1+C3 重疊）
- Volume 在 S/R 位置增加（Supporting）
- MA 位置確認方向（Supporting）
- Funding 方向正確（Supporting）

### Range 倉位設定

```
Risk per trade:  2% capital
Stop Loss:       1.2×ATR from entry（XRP: 1.0×ATR）
Take Profit 1:   BB 中軌（basis）— 先平 50%
Take Profit 2:   對面 BB 軌（做多→上軌 / 做空→下軌）
最低 R:R:        1:2.3
Leverage:        8x
```

### Trailing Stop（Range）

```
+1×R profit    → SL 移到 breakeven，平 50% 倉位
Remaining 50%  → Trailing stop 1×ATR，直至 TP2 或止損
```

### Range 識別規則

- R0+R1+R2 前置條件（自動，indicator_calc.py）
- Support/Resistance 需至少 3 個 touches
- Range 最少 3% 寬度
- 4H 定邊界 + BB 確認，1H/15m 定入場時機
- 提早退出：R1 不成立 → 跳過 R2 及後續（省 token）

---

## Mode B — Trend Trading

### 入場條件（4 KEY 全中才入）

**LONG（買回調）：**
| 指標 | 要求 |
|------|------|
| MA(4H) | Price above 50MA AND 200MA |
| MACD(4H) | Positive, histogram expanding |
| RSI(1H) | 40-55（唔超買）|
| Price | Pulling back to 1H 50MA |

**SHORT（賣反彈）：**
| 指標 | 要求 |
|------|------|
| MA(4H) | Price below 50MA AND 200MA |
| MACD(4H) | Negative, histogram expanding downward |
| RSI(1H) | 45-60（唔超賣）|
| Price | Bouncing up to 1H 50MA |

特殊 bias：
- 週四 21:00-01:00 UTC+8 → SHORT bias，3.5/5 即可
- 週五 21:00-03:00 UTC+8 → LONG bias，3.5/5 即可

### Trend 倉位設定

```
Risk per trade:  2% capital
Stop Loss:       1.5×ATR from entry
Take Profit:     Next major S/R（minimum 1:3 R:R）
Leverage:        7x
```

### Trailing Stop（Trend）

```
+1×R profit    → SL 移到 breakeven，平 50% 倉位
Remaining 50%  → Trailing stop 1×ATR，讓利潤跑
```

### Trend 平倉規則

- Trailing stop hit
- MACD 在 4H 反向穿越
- Price 回穿 50MA AND 200MA
- 3+ 指標翻到 Range 信號

---

## Mode C — Scalp Trading

### 只在特定時間窗口激活

| 窗口 | 時間（UTC+8） | 交易對 |
|------|-------------|--------|
| Asia Open | 09:00-09:30 | XAG, XRP, BTC |
| London Open | 15:00-15:30 | XAG, BTC |
| US Pre-Market | 21:00-21:30 | BTC, ETH, XRP |
| US Market Open | 21:30-22:00 | BTC, ETH, XRP |
| US Data Release | CPI/Fed/NFP ±15min | BTC, ETH, XRP |

**時間窗口外 → 所有 scalp 信號忽略**

### Scalp 觸發條件（需在窗口內）

| 觸發 | 閾值 |
|------|------|
| 清算瀑布 | >$15M in 5 minutes |
| 價格急升急跌 | >1.5% in 10 minutes |
| 成交量爆發 | >300% of 30d avg |
| 主要新聞確認 | Researcher 驗證 |
| 鯨魚鏈上移動 | >$25M |
| Long/Short ratio 翻轉 | >10% shift in 15 min |

### Scalp 入場（觸發後 3 分鐘內確認所有條件）

**LONG：**
- Trigger confirmed ✅
- Price above 1H 50MA ✅
- 15M RSI 35-55 ✅
- 15M MACD turning bullish ✅
- 唔對抗當前 Trend mode ✅

**SHORT：**
- Trigger confirmed ✅
- Price below 1H 50MA ✅
- 15M RSI 45-65 ✅
- 15M MACD turning bearish ✅
- 唔對抗當前 Trend mode ✅

3 分鐘內條件未達 → 跳過，等下個窗口

### Scalp 倉位設定

```
Risk per trade:  1% capital
Stop Loss:       1×ATR from entry
Take Profit:     2.5×ATR（R:R 1:2.5）
Leverage:        5x
```

### Scalp 平倉規則

| 條件 | 行動 |
|------|------|
| TP hit | 全平 |
| SL hit | 全平 |
| 觸發信號反轉 | 立即平 |
| 時間窗口關閉 | 立即平 |

---

## Dynamic Entry Trigger（優先順序）

```
1. Order Book depth 最大 bid/ask cluster
   GET https://fapi.asterdex.com/fapi/v1/depth?symbol=[PAIR]&limit=20
2. 最近 20 根 1H swing high/low
3. 1H 50MA 和 200MA

Entry trigger = 最近的確認位
最大入場距離：2.5%（正常）/ 1.5%（BLACK SWAN）

每次報三個級別：
  Conservative (0.5-1%) / Standard (1-2%) / Ideal (2-2.5%)
```

---

## S/R 識別規則

```
回望：200 candles（BLACK SWAN）/ 100 candles（正常）
Swing Low：N < N-1 AND N < N+1 AND N < N-2 AND N < N+2
Cluster：±0.5×ATR(14) 內視為同一 zone
有效 zone：4+ touches，body ≥50% 在 zone 內
最近 + 高成交量 = 最強 zone
```

---

## ATR 動態參數

| 參數 | 公式 |
|------|------|
| S/R zone 寬度 | ±0.5×ATR(14) |
| SL 距離（Range/Trend）| 1.5×ATR |
| SL 距離（Scalp）| 1×ATR |
| TP 距離（Scalp）| 2.5×ATR |
| Entry 精度 | ±0.25×ATR |

---

## 每個 Cycle 執行 Checklist

```
1.  SAFETY CHECK      → 任何限額達到？→ 停止
2.  NO-TRADE CHECK    → 任何條件激活？→ 等待
3.  MODE DETECTION    → 4H 5 指標偵測 → 記錄
4.  SCAN PAIRS        → 有符合當前模式的 setup？
5.  KEY INDICATORS    → 所有 KEY 指標對齊？→ 否則 HOLD
6.  MARKET DATA       → 成交量/資金/Order Book 確認
7.  SIZE POSITION     → 正確計算倉位
8.  EXECUTE           → 入場，立即設 SL/TP
9.  LOG               → 更新 TRADE_STATE.md + TRADE_LOG.md
10. REPORT            → Telegram 繁體中文匯報
```

---

## 交易對特殊規則

| 交易對 | 說明 |
|--------|------|
| BTC/USDT | 最可靠，優先分析 |
| ETH/USDT | 跟隨 BTC，第二優先 |
| XRP/USDT | 獨立走勢，可同時開 |
| XAG/USDT | Scalp 只限 Asia+London 窗口；先查 XAUUSD 方向 |

### XAG/USDT 長線策略（2026-03-04 用戶指示）

**基本面考量：**
- XAG 係實物商品，供需長遠推動價格上升
- 短期震盪下跌係入場機會，唔係趨勢反轉

**MA 參數調整：**
- 日線 MA(50) 和 MA(200) 作為長線趨勢參考
- 4H MA 只用於短線入場時機，唔作為主要方向判斷

**LONG 入場條件（長線倉）：**
1. 日線 MA(50) > MA(200)（長線上升趨勢確認）
2. 價格完成震盪下跌，接近日線 MA(50) 或關鍵支撐
3. 4H RSI < 35（超賣）
4. 4H MACD 開始轉正
5. Order Book 支撐增強

**倉位設定（長線）：**
- Risk: 2% capital
- Leverage: 5x（降低，因為持倉時間長）
- SL: 日線 MA(200) 下方 1%（寬鬆止損）
- TP: $90.00（目標價）
- 預期持倉：數日至數週

**唔入場條件：**
- 日線 MA(50) < MA(200)（長線下降趨勢）
- 價格仍在急跌中（4H 連續陰線）
- Funding rate 持續高企（>+0.15%）

---

## Telegram 報告格式

**無倉位：**
```
Cycle #X | Mode: [Range/Trend]
Indicators: RSI [x] | MACD [signal] | Volume [%] | MA [above/below] | Funding [%]
Action: HOLD | Reason: [具體原因]
Watching: [什麼條件會觸發]
```

**已入場：**
```
Cycle #X | Mode: [Range/Trend/Scalp] | [LONG/SHORT]
Pair: [pair] | Entry: [price] | SL: [price] | TP: [price]
Size: $[amount] | Leverage: [x] | Margin: $[amount]
Key signals: [哪些指標觸發]
```

**已平倉：**
```
Cycle #X | CLOSED [pair]
Result: [WIN/LOSS] | P&L: $[amount] ([%])
Reason: [TP/SL/Trailing/Timeout/Mode switch]
Running total: $[P&L] ([%])
```

---

## Production Patterns Reference（來源: freqtrade + TradingAgents）

> 完整知識庫: knowledge/TRADING_BOT_PATTERNS.md

### Order Execution 關鍵 Pattern
```
1. Quadratic Backoff Retry: (retries)² + 1 秒
   API fail → retry 1s, 2s, 5s, 10s, 17s → 5 次後放棄

2. Unfilled Order Management:
   每 cycle 檢查所有 open orders
   超時 5min → cancel → 檢查 partial fill → re-evaluate
   Dust prevention: 剩餘太少 → delete entire trade

3. Order-Driven State Rebuild:
   Trade state 從 order list 計算（唔係 manual tracking）
   DB vs exchange 不一致 → 以 exchange order history 為準

4. Safe Accessors: order.safe_price / order.safe_filled
   Exchange API 回傳可能有 None → 防 crash
```

### Stoploss 四層架構
```
Layer 1: Static（settings.py 嘅 ATR multiplier）
Layer 2: Dynamic callback（按 profit 收緊 SL）
Layer 3: Trailing（+1R → breakeven → trail 1×ATR）
Layer 4: On-exchange order（bot 停機仍有效）
規則: Stop losses only walk up, NEVER down
```

### Time-Decaying ROI（考慮後續加入）
```
越耐冇到 TP → 降低要求:
  0min:  4% → close
  60min: 2% → close
  120min: 1% → close
  240min: 0% → close（breakeven）
避免「等 TP 永遠到唔到」問題
```

### Funding Cost TP 調整（已實施 position_sizer.py）
```
如果 funding 逆向（LONG + positive / SHORT + negative）:
  估算持有期間 funding cost（Range ~24h, Trend ~48h）
  TP 向遠離 entry 方向移動，補償 funding 損失
  XAG +0.214%/8h 特別顯著
```
