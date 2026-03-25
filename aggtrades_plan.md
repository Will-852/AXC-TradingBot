# Task: Multi-Source aggTrades — CDN hardening + Bybit backup + WS persistence

## Goal
解決 Binance IP rate limit (418/429) 導致 Flow data fetch 失敗。
三層方案：CDN 優先 → Bybit backup → WS 本地持久化。

## Architecture Decision
- **Source priority**: WS cache > CDN > Binance API > Bybit API（單一 source per day，唔 merge）
- **WS daemon**: 獨立 process + LaunchAgent，唔混 ws_manager
- **Bybit mapping**: `side="Buy"` → `is_buyer_maker=False`（buy taker = buy aggressor）
- **Bybit agg_id**: 用負數 counter（-1, -2...），避免同 Binance 撞

## Phases

### Phase 1: CDN Hardening + Source Abstraction `status: pending`
- Extract 4 internal fetch functions with consistent signature
- CDN: retry 3x with 5s backoff, stream to temp file
- Waterfall: cache → WS cache → CDN → Binance API → Bybit API
- Aggregation functions 唔動
- **File**: `backtest/fetch_agg_trades.py`

### Phase 2: Bybit Backup Source `status: pending`
- `_fetch_from_bybit_api(symbol, day)` — cursor pagination
- Field mapping: side → is_buyer_maker, execId → negative counter
- Cache: `{SYMBOL}_{YYYYMMDD}_agg_bybit.csv`
- **File**: `backtest/fetch_agg_trades.py`

### Phase 3: WS Persistence Daemon `status: pending`
- New: `scripts/ws_aggtrade_recorder.py`
- `.live.csv` for today, rename at midnight
- Buffer 5s/1000 trades, 30-day retention
- New LaunchAgent: `ai.openclaw.aggtrades`

### Phase 4: 2check + Integration `status: pending`
- CDN chain test, Bybit mapping validation, WS daemon e2e

## Key Data
- Binance: 2400 weight/min (IP), 418=ban, CDN=T-1 zero limit
- Bybit: 120 req/min, cursor pagination
- Disk: ~117MB/day BTC, 30d retention ~10GB
