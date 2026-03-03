# NEWS_SOURCES.md — Aster DEX 確認可用 Endpoints
# 版本: 2026-03-02
# Base URL: https://fapi.asterdex.com

## 確認可用 Endpoints（無需 Auth）

| Endpoint | Auth | 信號用途 | 優先 |
|----------|------|---------|------|
| GET /fapi/v1/premiumIndex | ❌ | Funding Rate + Mark/Index 差距 | ⭐️⭐️⭐️ |
| GET /fapi/v1/depth | ❌ | 動態 S/R 識別（Order Book） | ⭐️⭐️⭐️ |
| GET /fapi/v1/klines | ❌ | 指標計算（RSI/MACD/MA/ATR） | ⭐️⭐️⭐️ |
| GET /fapi/v1/openInterest | ❌ | 市場熱度 | ⭐️⭐️ |
| GET /fapi/v1/aggTrades | ❌ | 鯨魚動向（大額成交） | ⭐️⭐️ |
| GET /fapi/v1/ticker/24hr | ❌ | 成交量判斷（vs 30日均值） | ⭐️⭐️ |
| GET /fapi/v1/exchangeInfo | ❌ | 下單規格（精度/lot size） | ⭐️ |
| GET /fapi/v1/forceOrders | ✅ | 黑天鵝預警（強平數據） | ⭐️⭐️ |
| POST /fapi/v1/order | ✅ | 落盤（Trader 專用） | — |

## ❌ 404 禁止使用

- globalLongShortAccountRatio
- takerlongshortRatio
- topLongShortAccountRatio

## 信號解讀

### premiumIndex（Funding Rate）
- 正常範圍：-0.07% ~ +0.07%
- 超出範圍 → Trend 信號
- >+0.18% 或 <-0.18% → 強烈 Trend 信號，可能觸發 light-scan

### depth（Order Book）
- 用於 Dynamic Entry Trigger（最優先）
- 識別最大 bid/ask cluster
- GET /fapi/v1/depth?symbol=[PAIR]&limit=20

### klines 參數
- 4H: RSI(14), MACD(12/26/9), 50MA, 200MA
- 1H: Entry timing, RSI, MACD crossover
- 15M: Scalp entry confirmation

### forceOrders（強平預警）
- 大量強平 → 市場極端情緒
- 觸發 Black Swan 評估

## 抓取優先順序（light-scan）

1. premiumIndex（Funding）
2. ticker/24hr（成交量）
3. klines 1H/4H（指標）
4. depth（Order Book，TRIGGER_PENDING 時才抓）
5. openInterest（趨勢確認）
6. aggTrades（可選，確認鯨魚）
