# Range Strategy Spec — Agent Automation

> 目標：短期區間波動入場訊號。適用：BTCUSDT、ETHUSDT、XRPUSDT。時間框：15m、1h、4h。

---

## 1. 前置要求

- **DataFrame 欄位**：`open`、`high`、`low`、`close`（小寫）
- **套件**：`tradingview_indicators`（Python 3.11+）
- **只計算必要指標**：按時間框只載入該時間框所需參數，避免重複計算

---

## 2. 指標計算規格（tradingview_indicators API）

| 指標 | 函數 | 輸出 | 備註 |
|------|------|------|------|
| BB | `ta.bollinger_bands(source, length, mult, ma_method="sma")` | `upper`, `basis`, `lower` | basis=中軌；必須用 SMA |
| RSI | `ta.RSI(source, period)` | Series | |
| DMI/ADX | `ta.DMI(df, "close")` 需 high/low/close | `adx()[0]`=ADX, `[1]`=DI+, `[2]`=DI- | |
| EMA | `ta.ema(source, length)` | Series | |
| Stoch | `ta.slow_stoch(close, high, low, k_length, k_smoothing=1, d_smoothing=3)` | `(k, d)` tuple | 可選，用於 timing |
| ATR | `ta.rma(tr, 14)` 其中 `tr = max(high-low, abs(high-prev_close), abs(low-prev_close))` | Series | 需自算 tr |

---

## 3. 時間框參數表（單一來源）

```yaml
15m:
  bb: { length: 20, mult: 2 }
  rsi: 14
  adx: 14
  ema_fast: 8
  ema_slow: 20
  atr: 14
  rsi_long: 30
  rsi_short: 70
  adx_range_max: 20
  bb_touch_tol: 0.005
  lookback_support: 50

1h:
  bb: { length: 20, mult: 2 }
  rsi: 14
  adx: 14
  ema_fast: 10
  ema_slow: 30
  atr: 14
  rsi_long: 35
  rsi_short: 65
  adx_range_max: 20
  bb_touch_tol: 0.005
  lookback_support: 30

4h:
  bb: { length: 20, mult: 2 }
  rsi: 14
  adx: 14
  ema_fast: 10
  ema_slow: 50
  atr: 14
  rsi_long: 35
  rsi_short: 65
  adx_range_max: 18
  bb_touch_tol: 0.005
  lookback_support: 30
  adx_min_bars: 30
```

---

## 4. 區間市前置條件（R0 + R1 + R2）

**R0（BB 寬度）**：  
`bb_width = (bb_upper - bb_lower) / bb_basis`  
僅當 `bb_width < 0.05` 時，才視為「波幅收斂、適合做區間」，否則直接跳過後續判斷。

**R1（趨勢強度）**：`ADX < adx_range_max`（見上表）

**R2（價格拉鋸）**：`abs(ema_slow - ema_slow.shift(10)) / close < 0.015`

- 僅當 R0 ∧ R1 ∧ R2 全部成立時，才評估入場訊號。

---

## 5. 入場訊號（布林＋RSI）

### 做多 (signal_long = 1)

| 條件 | 公式 |
|------|------|
| C1 | `close <= bb_lower * (1 + bb_touch_tol)` |
| C2 | `rsi < rsi_long` 且 `rsi.shift(1) < rsi`（RSI 回升） |
| C3 | `close >= rolling(low, lookback_support).min() * 0.995`（近支撐區） |
| C4（可選 Stoch timing） | `stoch_k < 20` 且 `stoch_k > stoch_d` 且 `stoch_k.shift(1) <= stoch_d.shift(1)`（%K 由下向上穿 %D） |

**強訊號**：R0 ∧ R1 ∧ R2 ∧ C1 ∧ C2 ∧ C3 ∧ C4 → `signal_long = 1`  
**弱訊號（不含 Stoch）**：R0 ∧ R1 ∧ R2 ∧ C1 ∧ C2 ∧ C3 → 可記為 `signal_long_score` 或降低權重。

### 做空 (signal_short = -1)

| 條件 | 公式 |
|------|------|
| C1 | `close >= bb_upper * (1 - bb_touch_tol)` |
| C2 | `rsi > rsi_short` 且 `rsi.shift(1) > rsi`（RSI 回落） |
| C3 | `close <= rolling(high, lookback_support).max() * 1.005`（近阻力區） |
| C4（可選 Stoch timing） | `stoch_k > 80` 且 `stoch_k < stoch_d` 且 `stoch_k.shift(1) >= stoch_d.shift(1)`（%K 由上向下穿 %D） |

**強訊號**：R0 ∧ R1 ∧ R2 ∧ C1 ∧ C2 ∧ C3 ∧ C4 → `signal_short = -1`  
**弱訊號**：R0 ∧ R1 ∧ R2 ∧ C1 ∧ C2 ∧ C3 → 可記為 `signal_short_score`。

---

## 6. 止損／止盈（參考）

| 項目 | 公式 |
|------|------|
| 止損距離 | `1.2 * ATR`（做多：入場 - 止損；做空：入場 + 止損） |
| 止盈1 | 中軌（BB basis）先平一半 |
| 止盈2 | 對側軌（做多→上軌；做空→下軌） |

---

## 7. 產品差異（覆寫參數）

| 產品 | 覆寫 |
|------|------|
| ETHUSDT | `rsi_long: 32`, `rsi_short: 68` |
| XRPUSDT | `bb_touch_tol: 0.008`, `stop_loss_mult: 1.0`（較保守，取代預設 1.2） |

---

## 8. 輸出欄位（DataFrame）

每時間框只新增該框所需欄位，避免冗餘：

```
bb_upper, bb_basis, bb_lower, rsi, adx, ema_fast, ema_slow, atr,
signal_long, signal_short
```

- `signal_long` ∈ {0, 1}
- `signal_short` ∈ {0, -1} 或 {0, 1}（視實作約定）

---

## 9. 可選增強（減少假訊號）

| 指標 | 用途 | 計算 |
|------|------|------|
| BB 寬度 | 區間 filter | 已提升為 R0：`bb_width = (bb_upper - bb_lower) / bb_basis`，`bb_width < 0.05` 才評估訊號 |
| Stoch | 入場 timing | `stoch_k, stoch_d = slow_stoch(close, high, low, 14)`；做多需 `%K<20` 且 `%K` 由下向上穿 `%D`，做空反向 |

---

## 10. 資源優化指引

1. **按需計算**：只對當前時間框計算指標，不預先計算其他時間框。
2. **共用欄位**：若多時間框合併，用前綴區分（如 `rsi_15m`, `rsi_1h`），避免重複欄位名。
3. **快取**：同一 request 內相同 (symbol, tf) 的 OHLC 只算一次指標。
4. **提早退出**：若 R1 不成立，跳過 R2 及後續條件判斷。
