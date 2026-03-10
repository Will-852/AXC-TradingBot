<!--
title: AXC 係咩？
section: 快速入門
order: 1
audience: human,claude,github
-->

# AXC 係咩？

一個 24 小時自動運行嘅交易系統，幫你監察加密貨幣 + 商品市場、分析走勢、自動下單、Telegram 通知結果。你唔需要盯住電腦。

## AXC vs OpenClaw

| | AXC | OpenClaw |
|--|-----|----------|
| 係咩 | 交易系統（掃描 + 策略 + 下單） | 底層開源平台 |
| 類比 | 你間鋪頭 | 商場嘅水電系統 |
| 你改咩 | 交易參數、幣種、風控 | 通常唔使改 |
| 用嚟 | 賺錢 | 提供 Agent 框架 + Telegram 橋接 |

簡單講：AXC 係你嘅交易機器人，OpenClaw 係佢跑緊嘅平台。

## 核心功能

- 每隔幾分鐘掃描 9 個交易所，搵大波動嘅幣種
- 用 25+ 技術指標分析（BB、ATR、RSI、MACD、EMA、支撐阻力）
- 自動判斷 RANGE 定 TREND 策略，自動下單，設好 SL / TP
- Telegram 通知你每一個動作
- 新聞情緒分析，輔助判斷

## 支持嘅幣種（7 pairs, 3 groups）

| 幣種 | Aster | Binance | 組別 |
|------|-------|---------|------|
| BTCUSDT | ✅ | ✅ | crypto_correlated |
| ETHUSDT | ✅ | ✅ | crypto_correlated |
| SOLUSDT | — | ✅ | crypto_correlated |
| XRPUSDT | ✅ | — | crypto_independent |
| POLUSDT | — | ✅ | crypto_independent |
| XAGUSDT | ✅ | — | commodity |
| XAUUSDT | ✅ | — | commodity |

每組最多 1 倉，最多 3 倉同時。

## 邊啲功能用 AI（要錢）？邊啲係免費？

**核心交易 = 100% 純 Python，零 AI 費用：**
- 市場掃描（async_scanner.py）
- 技術指標計算（indicator_calc.py）
- 策略信號（range_strategy.py / trend_strategy.py）
- 模式偵測（mode_detector.py）
- 下單執行（execute_trade.py）
- 風控管理（risk_manager.py）

**需要 AI API Key 嘅功能（選填）：**

| 功能 | 模型 | 頻率 | 估算月費 |
|------|------|------|----------|
| 新聞情緒分析 | Haiku | 每 15 分鐘 | ~$3-4 |
| Telegram 自然語言對話 | Haiku | 你問先答 | ~$1-2 |
| 每週策略回顧 | Sonnet | 每週一次 | ~$0.50 |

冇 API Key？系統照跑，只係冇新聞情緒 + Telegram AI 對話功能。

## 適合邊類用戶？

- 有加密貨幣交易經驗，想自動化執行策略
- 唔想 24 小時盯住市場
- 願意接受系統性風險管理
