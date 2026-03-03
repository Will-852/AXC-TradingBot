# RISK_PROTOCOL.md — 風險控制協議
# 版本: 2026-03-02
# 優先級: 最高（任何情況下不可 override）

## 熔斷規則（不可協商）

```
單倉虧損 ≥25%         → 立即平倉，Telegram URGENT 警報
單日虧損 ≥15%         → 停止所有交易，Telegram URGENT 警報
```

## 冷卻期

```
連續 2 單輸            → 暫停交易 30 分鐘
連續 3 單輸            → 暫停交易 2 小時
```

## 倉位上限

```
BTC + ETH（同一組）   → 最多 1 個倉位
XRP                   → 獨立，可同時開
XAG                   → 完全獨立
同時最多              → 2 個 crypto + 1 個 XAG
```

## Funding 成本控制

```
Funding cost > 50% unrealized profit → 強制平倉評估
最長持倉時間          → 72 小時（3日）
```

## No-Trade 條件（任何一個激活 → 唔入場）

- 主要新聞前後 1 小時（Fed/CPI/SEC/重大加密新聞）
- 成交量 < 30日均值 50%（死市）
- Exchange API 報錯或數據 stale
- 日虧損限額已達
- MACD 和 RSI 完全相反信號且無法解析
- Funding Rate > +0.2% 或 < -0.2%（極端）

## Re-entry 規則

```
等下一個 cycle signal（最少 10 分鐘）
需要 5/5 指標確認（比正常嚴格）
倉位減 30%
每對每 session 最多 1 次 re-entry
```

## Black Swan 協議

| Phase | 觸發 | 行動 |
|-------|------|------|
| Phase 1 | ATR >4× 正常 OR spread 異常 | 預設唔入場，等用戶確認 |
| Phase 2 | ATR 2-4× 正常，方向確認 | SHORT only，5% capital，5x 槓桿 |
| Phase 3 | ATR 回落 <2× | 正常交易，先用 HIGH VOL 參數 |

## 市場 Regime 定義

```
NORMAL:     ATR 0.5×–2× of 30d average
HIGH VOL:   ATR 2×–4× of 30d average
BLACK SWAN: ATR >4× OR NEWS score ≥5
```

## SL/TP 確認機制

```
落盤後 30 秒內確認 SL/TP active
未確認 → Telegram URGENT 警報
等用戶人手處理，唔自動平倉
```

## Kelly Criterion 參考

```
f* = (p×b - q) / b
p = 勝率, b = R:R, q = 1-p

Trend 模式（45% 勝率，1:3 R:R）→ f* = 26.7%
實際使用 2%（Half Kelly 保守版）
```

## 每 50 單後

重新計算真實勝率，調整 Kelly fraction。
