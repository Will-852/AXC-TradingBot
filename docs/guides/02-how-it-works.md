<!--
title: 系統運作流程
section: 快速入門
order: 2
audience: human,claude,github
-->

# 系統運作流程

## 兩套系統

原始設計用 AI Agent Pipeline（掃描員→分析員→決策員→交易員），但已被 **trader_cycle 16 步 pipeline** 取代做交易決策。trader_cycle 係純 Python，零 AI cost。

```
兩層掃描 ──→ trader_cycle 16 步 ──→ Telegram 通知
  ↑                                         ↓
心跳監察                              自動 SL/TP 管理

新聞員 ──→ 情緒分析 ──→ 輔助 trader_cycle 判斷
```

## Trader Cycle 16 步（核心）

| 步驟 | 做咩 |
|------|------|
| 1. LoadState | 讀 TRADE_STATE.md + 持倉狀態 |
| 2. SafetyCheck | 熔斷器 + 冷卻期檢查 |
| 3. NoTradeCheck | 成交量 / 資金費率 / 持倉上限 |
| 4. FetchMarketData | ticker + funding（按幣種路由 Aster 或 Binance API） |
| 5. CalcIndicators | 計算 4H + 1H 技術指標 |
| 6. DetectMode | 5 票制判斷 RANGE / TREND |
| 7-8. Strategy | Range 或 Trend 策略產生信號 |
| 9. NewsFilter | 讀取新聞情緒 |
| 10. EvaluateSignals | 評分 + 排名 + 選最佳信號 |
| 11. PositionSizer | ATR-based SL/TP + Kelly-inspired sizing |
| 12. AdjustPositions | 移動止蝕、TP 延伸、提前出場 |
| 13. ExecuteTrade | 7 步下單流程 |
| 14. ManagePositions | 超時平倉、資金費率檢查 |
| 15. WriteState | 更新 TRADE_STATE.md + SCAN_CONFIG.md |
| 16. SendAlerts | Telegram 通知 |

## 兩層掃描

| 層 | Script | 頻率 | 範圍 |
|----|--------|------|------|
| Layer 1 | async_scanner.py（常駐 daemon） | 9 exchanges × 20s = 180s 一輪 | 全部交易所 |
| Layer 2 | light_scan.py（cron） | 每 3 分鐘 | Aster only（BTC/ETH/XRP/XAG/XAU） |

## 平台路由

market_data.py 會自動判斷幣種用邊個 API：
- Aster pairs（BTC/ETH/XRP/XAG/XAU）→ Aster API
- Binance pairs（BTC/ETH/SOL/POL）→ Binance API
