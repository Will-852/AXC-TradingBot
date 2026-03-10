# AXC params.py vs 業界標準對比
> 更新：2026-03-10
> 用途：快速查看現有參數是否合理

### talk16 — 呢個文件係咩
純對照表。AXC 系統入面每個指標參數嘅實際值 vs 業界建議值，一眼睇到邊個合理（✅）邊個要改（⚠️）。改參數前嚟呢度查，唔使翻晒其他文件。

---

## BB 參數
| 參數 | AXC 值 | 業界標準 | 評估 |
|---|---|---|---|
| bb_length | 20（全 TF）| 20 | ✅ 標準 |
| bb_mult | 2.0（全 TF）| 2.0-2.5（crypto）| ⚠️ 4H 可考慮 2.5 |
| BB_TOUCH_TOL | 0.005 / 0.008(XRP) | 0.5-1.0% | ✅ 合理 |
| BB_WIDTH_MIN | 0.05 固定 | 相對值更好 | ⚠️ 建議改用 percentile |

## RSI 參數
| 參數 | AXC 值 | 業界標準 | 評估 |
|---|---|---|---|
| rsi_period | 14（全 TF）| 14 | ✅ 標準 |
| 15m OB/OS | 70/30 | 70/30 | ✅ |
| 1h/4h OB/OS | 65/35 | 65/35 | ✅ 已調整 |

## MACD 參數
| 參數 | AXC 值 | 業界標準 | 評估 |
|---|---|---|---|
| MACD_FAST | 12 | 12（1h/4h）, 8（15m）| ⚠️ 15m 可試 8 |
| MACD_SLOW | 26 | 26（1h/4h）, 17（15m）| ⚠️ 15m 可試 17 |
| MACD_SIGNAL | 9 | 9 | ✅ |

## STOCH 參數
| 參數 | AXC 值 | 業界標準 | 評估 |
|---|---|---|---|
| K Period | 14 | 14 | ✅ |
| K Smooth | 1 | 1-3 | ✅ Fast Stochastic |
| D Smooth | 3 | 3 | ✅ |
| OB/OS | 80/20 | 80/20 | ✅ |
| — | — | — | ⚠️ 同 RSI 冗餘 |

## EMA 參數
| TF | AXC Fast/Slow | 業界建議 | 評估 |
|---|---|---|---|
| 15m | 8/20 | 8/20-21 | ✅ |
| 1h | 10/30 | 10/30 | ✅ |
| 4h | 10/50 | 10/50 | ✅ |

## ADX 參數
| 參數 | AXC 值 | 業界標準 | 評估 |
|---|---|---|---|
| adx_period | 14（全 TF）| 14 | ✅ |
| adx_range_max 15m/1h | 20 | 20 | ✅ |
| adx_range_max 4h | 18 | 18-20 | ✅ 合理 |

## SL/TP ATR Multiplier
| Profile | sl_atr_mult | 建議範圍 | 評估 |
|---|---|---|---|
| CONSERVATIVE | 1.5 | 1.5-2.0 | ✅ |
| BALANCED | 1.2 | 1.5-2.0 | ⚠️ 偏緊 |
| AGGRESSIVE | 1.0 | 1.0-1.5 | ⚠️ 非常緊 |
| tp_atr_mult | 2.0-3.0 | 2.0-3.0 | ✅ 但未接入 |

## Risk Management
| 參數 | AXC 值 | 建議 | 評估 |
|---|---|---|---|
| RISK_PER_TRADE_PCT | 2% | 1-2% | ✅ |
| MAX_OPEN_POSITIONS | 3 | 2-5 | ✅ |
| MAX_POSITION_SIZE_USDT | 50 | 視賬戶大小 | ✅ |

## Mode Detection
| 參數 | AXC 值 | 備註 |
|---|---|---|
| MODE_RSI_TREND_LOW/HIGH | 32/68 | 合理 |
| MODE_VOLUME_LOW/HIGH | 0.50/1.50 | 合理 |
| MODE_FUNDING_THRESHOLD | 0.0007 | 0.07%，保守 |
| MODE_CONFIRMATION_REQUIRED | 2 | 防抖動，合理 |

---

## 建議修改優先級

### 高優先（影響大、風險低）
1. **BALANCED sl_atr_mult 1.2 → 1.5**：太緊容易被噪音踢走
2. **加 Volume 指標**：系統完全冇 volume 確認

### 中優先（需要 backtest）
3. **15m MACD 改 8-17-9**：研究顯示更適合 crypto 短線
4. **BB_WIDTH_MIN 改用 percentile**：唔同幣種需要唔同閾值
5. **ADX 做 strategy mode selector**：ADX < 20 = range only, > 25 = trend only

### 低優先（nice to have）
6. **4h BB mult 試 2.5**：減少假觸碰訊號
7. **考慮移除 STOCH**：同 RSI 冗餘，slot 俾 volume
8. **ATR position sizing**：替代固定 USDT 限額
