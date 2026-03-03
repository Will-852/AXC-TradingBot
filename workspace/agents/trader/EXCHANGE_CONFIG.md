# EXCHANGE_CONFIG.md — Aster DEX 交易所設定
# 版本: 2026-03-02
# 警告: API Key/Secret 在 {ROOT}/keys/API_KEYS.md

## 基本設定

EXCHANGE: Aster DEX
API_BASE: https://fapi.asterdex.com
ACCOUNT_TYPE: Futures（合約）
MARGIN_TYPE: Cross（跨倉）

## 交易對設定

| 交易對 | Symbol | 最小下單 | 精度 | 備注 |
|--------|--------|---------|------|------|
| BTC/USDT | BTCUSDT | 待確認 | 0.001 | 最可靠 |
| ETH/USDT | ETHUSDT | 待確認 | 0.01 | 跟隨 BTC |
| XRP/USDT | XRPUSDT | 待確認 | 1 | 獨立走勢 |
| XAG/USDT | XAGUSDT | 待確認 | 0.001 | Silver 合約 |

⚠️ 實際精度從 GET /fapi/v1/exchangeInfo 確認

## 槓桿設定

| 模式 | 最低 | 標準 | 最高 |
|------|------|------|------|
| Range | 5x | 8x | 15x |
| Trend | 5x | 7x | 10x |
| Scalp | 5x | 5x | 10x |
| Black Swan P2 | 5x | 5x | 5x |

## Endpoints（詳細見 {ROOT}/protocols/NEWS_SOURCES.md）

```
Market Data:
GET /fapi/v1/premiumIndex     → Funding Rate
GET /fapi/v1/depth            → Order Book
GET /fapi/v1/klines           → K線
GET /fapi/v1/ticker/24hr      → 24H 統計
GET /fapi/v1/openInterest     → OI

Trading (需 Auth):
POST /fapi/v1/order           → 下單
GET  /fapi/v1/forceOrders     → 強平記錄
```

## 下單流程

```
1. 計算 Position Size（按 RISK_PROTOCOL.md）
2. 確認 exchangeInfo（精度/lot size）
3. POST /fapi/v1/order（市價單或限價單）
4. 30秒內確認 SL/TP 已設定
5. 更新 TRADE_STATE.md
6. 發 Telegram 確認
```

## 時間同步

- 所有時間戳：UTC+8（Asia/Hong_Kong）
- 格式：YYYY-MM-DD HH:MM UTC+8
