# TRADE_LOG.md — 交易記錄
# 版本: 2026-03-02
# 寫入: Trader Agent 專用
# 格式: 每單記錄，永不刪除

## 統計摘要

TOTAL_TRADES: 2
TOTAL_WINS: 0
TOTAL_LOSSES: 1
OPEN_TRADES: 1
WIN_RATE: 0%（樣本量不足 — 需 50 筆才有統計意義）
TOTAL_PNL: +$39.87（$60 → $99.87，+66.5%）
STARTING_BALANCE: $60.00
NOTE: $39.87 利潤來自更早期交易，本 log 開始前已實現

## 交易記錄

| # | 日期時間 | 交易對 | 方向 | 入場 | 出場 | P&L | 原因 | 模式 |
|---|---------|--------|------|------|------|-----|------|------|
| 1 | 2026-02-28 | XRP/USDT | LONG | $1.3473 | $1.3263 (SL) | -$0.084 | Freefall, no clear support | Range |
| 2 | 2026-03-01 | XAG/USDT | LONG | $94.30 | OPEN | -$0.21 (unrealized) | 4H breakout signal | Trend |

## 當前持倉（待確認）

```
持倉: XAG/USDT LONG（2026-02-28 開倉，狀態待確認）
入場: $94.30 | SL: $93.36 | TP: $103.00
大小: 1.059 XAG
Funding: +0.214%（每8小時侵蝕）
⚠️ 週末低流動性：ask side ~10 XAG only
```

## Kelly Criterion 狀態

- 最少需要 50 筆交易才有統計意義
- 目前樣本：1 筆已平倉 — 太少
- 使用保守 1.5-2% risk 直到 50-trade 數據可用

## 記錄格式

每筆交易記錄：
```
#[N] | [YYYY-MM-DD HH:MM UTC+8] | [PAIR] [LONG/SHORT]
Entry: [price] | Exit: [price] | Size: [amount]
Leverage: [x] | P&L: $[amount] ([%])
Mode: [Range/Trend/Scalp] | Reason: [原因]
Exit reason: [TP/SL/Trailing/Manual/Timeout]
```
[2026-03-03 02:57] [DRY_RUN] ENTRY LONG BTCUSDT qty=0.001 @ 68920.0 SL=67106.3 TP=0.0 leverage=8x margin=$6.54
