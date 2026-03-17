# WebSocket Channels

---

## 1. Market Channel (Public)

**URL**: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
**Auth**: None

### Subscribe
```json
{
  "assets_ids": ["token_id_1", "token_id_2"],
  "type": "market",
  "initial_dump": true,
  "level": 2,
  "custom_feature_enabled": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `assets_ids` | string[] | Token IDs to subscribe |
| `type` | string | Always `"market"` |
| `initial_dump` | bool | Send initial orderbook snapshot |
| `level` | int | Orderbook depth (2 = full) |
| `custom_feature_enabled` | bool | Enable extra events (new_market, market_resolved, best_bid_ask) |

### Dynamic Subscribe/Unsubscribe (no reconnect needed)
```json
{
  "operation": "subscribe",
  "assets_ids": ["new_token_id"],
  "level": 2
}
```
```json
{
  "operation": "unsubscribe",
  "assets_ids": ["old_token_id"]
}
```

### Heartbeat
Client sends `PING` every 10 seconds → server replies `PONG`.

### Event Types

#### `book` — Full Orderbook Snapshot
```json
{
  "event_type": "book",
  "asset_id": "71321045...",
  "market": "condition_id",
  "bids": [{"price": "0.45", "size": "100"}],
  "asks": [{"price": "0.46", "size": "150"}],
  "timestamp": "1700000000",
  "hash": "a1b2c3d4..."
}
```

#### `price_change` — Delta Update
```json
{
  "event_type": "price_change",
  "market": "condition_id",
  "price_changes": [{
    "asset_id": "71321045...",
    "price": "0.55",
    "size": "200",
    "side": "BUY",
    "hash": "...",
    "best_bid": "0.54",
    "best_ask": "0.56"
  }],
  "timestamp": "1700000000"
}
```

#### `last_trade_price` — Trade Executed
```json
{
  "event_type": "last_trade_price",
  "asset_id": "71321045...",
  "market": "condition_id",
  "price": "0.55",
  "size": "50",
  "fee_rate_bps": "0",
  "side": "BUY",
  "timestamp": "1700000000",
  "transaction_hash": "0x..."
}
```

#### `tick_size_change` — Tick Size Update
```json
{
  "event_type": "tick_size_change",
  "asset_id": "71321045...",
  "market": "condition_id",
  "old_tick_size": "0.01",
  "new_tick_size": "0.001",
  "timestamp": "1700000000"
}
```

#### `best_bid_ask` — BBO Update (requires custom_feature_enabled)
```json
{
  "event_type": "best_bid_ask",
  "asset_id": "71321045...",
  "market": "condition_id",
  "best_bid": "0.54",
  "best_ask": "0.56",
  "spread": "0.02",
  "timestamp": "1700000000"
}
```

#### `new_market` — New Market Created (requires custom_feature_enabled)
```json
{
  "event_type": "new_market",
  "id": "12345",
  "question": "Will X happen?",
  "market": "condition_id",
  "slug": "will-x-happen",
  "assets_ids": ["yes_token_id", "no_token_id"],
  "outcomes": ["Yes", "No"],
  "event_message": "...",
  "timestamp": "1700000000",
  "tags": ["politics"]
}
```

#### `market_resolved` — Market Resolved (requires custom_feature_enabled)
```json
{
  "event_type": "market_resolved",
  "id": "12345",
  "market": "condition_id",
  "assets_ids": ["yes_token_id", "no_token_id"],
  "winning_asset_id": "yes_token_id",
  "winning_outcome": "Yes",
  "timestamp": "1700000000",
  "tags": ["politics"]
}
```

---

## 2. User Channel (Authenticated)

**URL**: `wss://ws-subscriptions-clob.polymarket.com/ws/user`
**Auth**: API credentials in subscription message

### Subscribe
```json
{
  "auth": {
    "apiKey": "uuid",
    "secret": "base64...",
    "passphrase": "hex..."
  },
  "type": "user",
  "markets": ["condition_id_1", "condition_id_2"]
}
```

Note: `markets` is optional. If omitted, receives updates for ALL user markets.

### Heartbeat
Same as market channel: `PING` / `PONG` every 10 seconds.

### Event Types

#### Order Events
```json
{
  "event_type": "order",
  "id": "order_id",
  "owner": "uuid",
  "market": "condition_id",
  "asset_id": "token_id",
  "side": "BUY",
  "original_size": "100",
  "size_matched": "50",
  "price": "0.55",
  "status": "LIVE",
  "order_type": "GTC",
  "timestamp": "1700000000"
}
```

**Order statuses**: `LIVE`, `MATCHED`, `CANCELED`

#### Trade Events
```json
{
  "event_type": "trade",
  "id": "trade_id",
  "taker_order_id": "0x...",
  "market": "condition_id",
  "asset_id": "token_id",
  "side": "BUY",
  "size": "50",
  "price": "0.55",
  "status": "CONFIRMED",
  "trader_side": "TAKER",
  "maker_orders": ["maker_order_id"],
  "timestamp": "1700000000"
}
```

**Trade statuses**: `MATCHED` → `MINED` → `CONFIRMED` | `RETRYING` | `FAILED`
**Trader sides**: `TAKER`, `MAKER`

---

## 3. Sports Channel (Public)

**URL**: `wss://sports-api.polymarket.com/ws`
**Auth**: None
**No subscription message** — connects and immediately streams all sports updates.

### Heartbeat
Server sends `ping` every 5 seconds → client must reply `pong` within 10 seconds.

### Sports Update Event
```json
{
  "slug": "nba-lakers-vs-celtics-2024-01-01",
  "live": true,
  "ended": false,
  "score": "105-98",
  "period": "4th Quarter",
  "elapsed": "08:30",
  "last_update": "1700000000",
  "finished_timestamp": null,
  "turn": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `slug` | string | **Required** — game identifier |
| `live` | bool | Game in progress |
| `ended` | bool | Game finished |
| `score` | string | Current score |
| `period` | string | Current period/quarter/half |
| `elapsed` | string | Time elapsed (MM:SS) |
| `last_update` | string | Unix timestamp |
| `finished_timestamp` | string | When game ended (null if ongoing) |
| `turn` | string | NFL-specific: which team has possession |

---

## Connection Best Practices

1. **Reconnect on disconnect** — Use exponential backoff (1s, 2s, 4s, 8s...)
2. **Heartbeat discipline** — Missing heartbeats will cause disconnection
3. **Dynamic subscriptions** — Prefer subscribe/unsubscribe over reconnecting
4. **Initial dump** — Set `initial_dump: true` to get current state on connect
5. **Token ID mapping** — Market WS uses `token_id` (per outcome), User WS uses `condition_id` (per market)
