# Findings: Data Diversity Layer

## Research Date: 2026-03-22

## 1. Exchange API Audit (Opus subagent)

### Available Endpoints (confirmed working, free, no auth)

**Binance Futures (最多未用 endpoints):**
- `GET /fapi/v1/premiumIndex?symbol=BTCUSDT` — funding rate + mark/index price (weight: 1)
- `GET /fapi/v1/openInterest?symbol=BTCUSDT` — aggregate OI (weight: 1)
- `GET /futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m` — L/S ratio
- `GET /futures/data/topLongShortPositionRatio?symbol=BTCUSDT&period=5m` — top trader L/S
- `GET /futures/data/takerlongshortRatio?symbol=BTCUSDT&period=5m` — futures taker ratio
- `GET /fapi/v1/depth?symbol=BTCUSDT&limit=50` — perp order book

**OKX:**
- `GET /v5/public/funding-rate?instId=BTC-USDT-SWAP` — funding rate
- `GET /v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP` — OI
- `GET /v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=5m` — L/S ratio
- `GET /v5/rubik/stat/taker-volume?ccy=BTC&instType=CONTRACTS` — taker volume
- `GET /v5/market/books?instId=BTC-USDT-SWAP&sz=50` — perp OB

**Bybit:**
- `GET /v5/market/funding/history?category=linear&symbol=BTCUSDT` — funding rate
- `GET /v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min` — OI
- `GET /v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=5min` — L/S ratio
- `GET /v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=50` — perp OB

**Deribit:**
- `GET /public/ticker?instrument_name=BTC-PERPETUAL` — funding + OI + IV
- `GET /public/get_volatility_index_data?currency=BTC&resolution=1` — DVOL (1s resolution)

**Hyperliquid (SDK already connected):**
- `Info.funding_history(coin, startTime)` — funding rate
- `Info.l2_snapshot(coin)` — L2 order book (DEX = on-chain commitment)
- `Info.meta_and_asset_ctxs()` — OI + mark price (already in liq_monitor)

## 2. Current Data Flow Gap

**MM Bot (run_mm_live.py) 而家用嘅：**
- Binance spot bookTicker → price (1s cache)
- Binance futures 1m klines → vol_1m (60s cache)
- Binance spot 1m klines → M1 return + CVD ratio (0s / 15s cache)
- OKX + Bybit spot ticker → crash guard only (10s cache)
- Polymarket CLOB → OB imbalance (5s cache)

**完全冇用嘅（available but NOT wired）：**
- Funding rate (5 exchanges)
- Open interest (4 exchanges)
- L/S ratio (3 exchanges)
- Futures taker ratio (Binance)
- DVOL (Deribit)
- Perp order book (4 exchanges)

## 3. Integration Architecture

**Key constraint:** MM bot 5s fast loop / 10s heavy cycle。每個 heavy cycle budget ~3s for all fetches。
**Solution:** ThreadPoolExecutor parallel fetch — 10 calls in 500ms instead of 5s sequential。

**Integration points (file:line):**
- Fair value offset: `run_mm_live.py:1079` (alongside OB adj)
- Sizing modifier: `run_mm_live.py:1110-1168` (CVD disagree pattern)
- Entry gate: `run_mm_live.py:1100-1108` (M1/fair conflict area)
- 1H conviction: `hourly_engine.py:218` (multiply into conviction score)
- Signal log: `run_mm_live.py:1086-1098` (_SIGNAL_LOG)

## 4. Rate Limit Headroom
All exchanges have 6x-100x headroom. Adding 25 new endpoints at 10s interval = trivial load.

## 5. Existing Code to Reuse
- `backtest/fetch_funding_oi.py` — has Binance funding/OI/L-S fetchers (backtest only, can port)
- `scripts/liq_monitor.py` — has HL OI via `meta_and_asset_ctxs()` (can extract pattern)
- `scripts/public_feeds.py` — has 9-exchange ticker pattern (bulk fetch reference)
- `run_mm_live.py:158-219` — has cross-exchange price pattern (extend for funding)
