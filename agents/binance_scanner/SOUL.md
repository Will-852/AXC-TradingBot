# SOUL.md — Binance Scanner Agent
# 版本: 2026-03-06

## 身份

我係 OpenClaw Binance Scanner，負責 Binance Futures 市場掃描同信號偵測。
純 Python 執行，唔需要 LLM 判斷。同 Aster Scanner 一致嘅邏輯。

## 功能

### 整合方式
Binance 掃描已整合入 `async_scanner.py`，同 Aster 掃描並行運行。
唔係獨立 process，而係同一個掃描器嘅額外數據源。

### 數據獲取
```
Binance Futures API: https://fapi.binance.com/fapi/v1
Endpoint: /ticker/24hr?symbol={symbol}
認證: 唔需要（公開數據）
```

### 返回格式
同 Aster 一模一樣（Aster 抄 Binance API），fields:
- lastPrice, priceChangePercent, highPrice, lowPrice, quoteVolume

## 掃描嘅 Pairs

| Pair | 描述 |
|------|------|
| BTCUSDT | Bitcoin 永續合約 |
| ETHUSDT | Ethereum 永續合約 |
| SOLUSDT | Solana 永續合約 |

配置: `config/params.py` → `BINANCE_SYMBOLS`
修改後需重啟掃描器。

## 信號流程

1. `async_scanner.py` 並行 fetch Aster + Binance symbols
2. 同一套 `evaluate_signal()` 判斷（24H 變化幅度）
3. 結果寫入 `shared/SIGNAL.md` + `shared/prices_cache.json`
4. platform 欄位標記 "binance"

## Model
唔需要 LLM — 純數學信號偵測。

## 共享狀態路徑
- SIGNAL: ~/.openclaw/shared/SIGNAL.md
- prices_cache: ~/.openclaw/shared/prices_cache.json
- SCAN_LOG: ~/.openclaw/shared/SCAN_LOG.md
