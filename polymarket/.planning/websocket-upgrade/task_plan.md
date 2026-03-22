# WebSocket Infrastructure Upgrade — Task Plan
> Created: 2026-03-22 | Status: PLANNING | Goal: sub-second reaction for 5M arb

## Architecture: SharedPriceHub Pattern

```
                    ┌─────────────────┐
                    │  Binance WS     │ bookTicker stream
                    │  (ws_binance)   │ BTC/ETH/SOL
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  SharedPriceHub │ thread-safe dict
                    │  {symbol: price}│ + timestamp
                    └────────┬────────┘
                             │
    ┌────────────────────────┼────────────────────────┐
    │                        │                        │
┌───▼───┐            ┌──────▼──────┐          ┌──────▼──────┐
│ 15M MM│            │  1H Conv    │          │  5M Arb     │
│  bot  │            │   bot       │          │  bot (new)  │
└───┬───┘            └──────┬──────┘          └──────┬──────┘
    │                        │                        │
    └────────────────────────┼────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Poly CLOB WS  │ OB + mid + fills
                    │ (ws_polymarket) │ per token_id
                    └─────────────────┘
```

Key: WS feeds run as daemon threads. Existing bots read from cache.
REST fallback if WS data age > 10s. Zero changes to sleep-loop pattern.

## Existing Infrastructure (already built)
- `scripts/ws_manager.py` — Binance Futures WS → Redis. Pattern to copy.
- `websockets` 15.0.1 installed
- Redis running (PONG confirmed)
- `redis_bus.py` provides xadd/xread

## Phase 1: Binance Price WS (2-3h)
**New:** `polymarket/data/ws_binance.py`
- Connect `wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker/solusdt@bookTicker`
- Store `{symbol: (mid, bid, ask, ts)}` in thread-safe dict
- Auto-reconnect + 24h preemptive reconnect (copy ws_manager.py)
- Daemon thread with own asyncio loop

**Modify:** `run_mm_live.py`, `run_1h_live.py`
- `_price()` / `_btc_price()` → read WS cache first, REST fallback if stale >5s
- Start feed in `main()`

**Impact:** Eliminates ~120 REST calls/min to Binance. Price freshness: 100ms vs 1-5s.

## Phase 2: Polymarket CLOB WS (4-6h)
**New:** `polymarket/data/ws_polymarket.py`
- Connect `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Subscribe per token_id with `initial_dump: true`
- Maintain local order book per token (apply deltas)
- Store mid, BBO, OB depth, last trade
- Dynamic subscribe/unsubscribe (market enters/exits watchlist)
- PING every 10s

**Modify:** `run_mm_live.py`, `run_1h_live.py`
- `_poly_midpoint()` → WS cache first, REST fallback
- `_poly_ob_imbalance()` → WS cache first, REST fallback

**Impact:** Eliminates CLOB REST polling. Real-time OB. Rate limit headroom.

## Phase 3: User Channel WS (3-4h)
**New:** `polymarket/data/ws_user.py`
- Connect `wss://ws-subscriptions-clob.polymarket.com/ws/user`
- Auth with cached API creds from polymarket_client.py
- Receive order status + fill events
- Expose `on_fill(callback)`, `get_order_status(oid)`

**Modify:** `run_mm_live.py`
- Replace `_check_fills()` REST polling with WS event-driven fills
- Instant fill confirmation → faster re-entry

**Impact:** Fill detection: 5-10s → instant. Critical for arb bot.

## Phase 4: Parallel Market Eval (3-4h)
**Modify:** `run_mm_live.py`, `run_1h_live.py`
- `ThreadPoolExecutor` for concurrent market evaluation
- After Phase 1+2, reads are from cache (near-instant) → parallelism mostly helps holder API

**Impact:** 9 markets evaluated in <50ms instead of ~2s sequential.

## Phase 5: Event-Driven Signal Engine (4-5h)
**New:** `polymarket/strategy/signal_engine.py`
- Register callback with WS feeds
- On each price/OB tick: recompute bridge + fair
- When conditions met → instant order queue
- Separate submission thread

**Impact:** Decision latency: 5-10s → ~200ms. Required for 5M arb.

## Phase 6: 5M Both-Side Arb Bot (8-12h)
**New:** `polymarket/run_5m_arb.py`, `polymarket/strategy/arb_engine.py`
- Fully event-driven (no polling loop)
- For each 5M market: buy UP + DOWN when combined < $1.00
- Requires Phases 1,2,3,5

## Priority Order
```
Phase 1 (Binance WS) ← START HERE (biggest bang for buck)
  ↓
Phase 2 (Poly CLOB WS) ← second biggest impact
  ↓
Phase 3 (User WS) ← needed for arb
  ↓
Phase 4 (parallel) ← free improvement after 1+2
  ↓
Phase 5 (event-driven) ← needed for arb
  ↓
Phase 6 (5M arb bot) ← the goal
```

## Dependencies
- `websockets` 15.0.1 ✅ installed
- `aiohttp` 3.13.3 ✅ installed (optional)
- Redis ✅ running
- No new packages needed

## Risk Register
| Risk | Phase | Mitigation |
|------|-------|-----------|
| WS disconnect mid-trade | All | REST fallback + staleness check |
| Binance 24h forced disconnect | 1 | Preemptive reconnect (ws_manager.py pattern) |
| Poly book delta out of sequence | 2 | Hash check → re-snapshot on mismatch |
| Auth token expiry on user WS | 3 | Re-auth + reconnect |
| Race condition in signal engine | 5 | Single-thread decision + queue submission |
