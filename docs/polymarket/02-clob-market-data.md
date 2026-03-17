# CLOB API — Market Data (Public, No Auth)

> Base URL: `https://clob.polymarket.com`
> All endpoints in this file require NO authentication.

---

## Order Book

### GET /book — Single Order Book
**Query**: `token_id` (string, **required**)
**Response** (OrderBookSummary):
```json
{
  "market": "0x1234...condition_id",
  "asset_id": "71321045...token_id",
  "timestamp": "1700000000",
  "hash": "a1b2c3d4...",
  "bids": [{"price": "0.45", "size": "100"}, {"price": "0.44", "size": "200"}],
  "asks": [{"price": "0.46", "size": "150"}, {"price": "0.47", "size": "250"}],
  "min_order_size": "1",
  "tick_size": "0.01",
  "neg_risk": false,
  "last_trade_price": "0.45"
}
```
**Errors**: 400 (invalid token_id), 404 (no book), 500

### GET /books — Multiple Order Books (query)
**Query**: `token_ids` (comma-separated, **required**)
**Response**: OrderBookSummary[]

### POST /books — Multiple Order Books (body)
**Body**: `[{"token_id": "..."}, {"token_id": "..."}]`
**Response**: OrderBookSummary[]

---

## Pricing

### GET /price — Best Price
**Query**: `token_id` (string, **required**), `side` (enum: BUY | SELL, **required**)
**Response**: `{price: number}`

### GET /prices — Multiple Prices (query)
**Query**: `token_ids` (comma-separated, **required**), `sides` (comma-separated BUY/SELL, **required**)
**Response**: Map `{token_id: {side: price}}`

### POST /prices — Multiple Prices (body)
**Body**: `[{"token_id": "...", "side": "BUY"}]`
**Response**: Map `{token_id: {side: price}}`

### GET /midpoint — Mid-price
**Query**: `token_id` (string, **required**)
**Response**: `{mid_price: "0.50"}`

### GET /midpoints — Multiple Midpoints (query)
**Query**: `token_ids` (comma-separated, **required**)
**Response**: Map `{token_id: "0.50"}`

### POST /midpoints — Multiple Midpoints (body)
**Body**: `[{"token_id": "..."}]`
**Response**: Map `{token_id: "0.50"}`

### GET /spread — Bid-Ask Spread
**Query**: `token_id` (string, **required**)
**Response**: `{spread: "0.02"}`

### POST /spreads — Multiple Spreads
**Body**: `[{"token_id": "..."}]`
**Response**: Map `{token_id: "0.02"}`

### GET /last-trade-price — Last Execution Price
**Query**: `token_id` (string, **required**)
**Response**: `{price: "0.55", side: "BUY"}`
- `side` can be `"BUY"`, `"SELL"`, or `""` (no trades)

### GET /last-trades-prices — Multiple Last Prices (query)
**Query**: `token_ids` (comma-separated, **required**, max 500)
**Response**: `[{token_id, price, side}]`

### POST /last-trades-prices — Multiple Last Prices (body)
**Body**: `[{"token_id": "..."}]` (max 500)
**Response**: `[{token_id, price, side}]`

---

## Market Metadata

### GET /fee-rate — Fee Rate (query)
**Query**: `token_id` (string, optional)
**Response**: `{base_fee: 0}` (integer, basis points)

### GET /fee-rate/{token_id} — Fee Rate (path)
**Path**: `token_id` (string, **required**)
**Response**: `{base_fee: 0}`

### GET /tick-size — Tick Size (query)
**Query**: `token_id` (string, optional)
**Response**: `{minimum_tick_size: 0.01}`

### GET /tick-size/{token_id} — Tick Size (path)
**Path**: `token_id` (string, **required**)
**Response**: `{minimum_tick_size: 0.01}`

### GET /neg-risk — Negative Risk Flag (query)
**Query**: `token_id` (string, optional)
**Response**: `{neg_risk: false}`

### GET /neg-risk/{token_id} — Negative Risk Flag (path)
**Path**: `token_id` (string, **required**)
**Response**: `{neg_risk: false}`

---

## Price History

### GET /prices-history — Historical Prices
**Query**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `market` | string | **Yes** | Asset/token ID |
| `startTs` | number | No | Unix timestamp lower bound |
| `endTs` | number | No | Unix timestamp upper bound |
| `interval` | enum | No | `1h`, `6h`, `1d`, `1w`, `1m`, `max`, `all` |
| `fidelity` | int | No | Accuracy in minutes (default: 1) |

**Response**:
```json
{
  "history": [
    {"t": 1700000000, "p": 0.55},
    {"t": 1700003600, "p": 0.57}
  ]
}
```

---

## Market Listings

### GET /simplified-markets — Condensed Market List
**Query**: `next_cursor` (string, optional)
**Response**: PaginatedSimplifiedMarkets

### GET /sampling-markets — Liquidity-Reward Eligible Markets
**Query**: `next_cursor` (string, optional)
**Response**: PaginatedMarkets

### GET /sampling-simplified-markets — Condensed Reward Markets
**Query**: `next_cursor` (string, optional)
**Response**: PaginatedSimplifiedMarkets

### POST /markets/live-activity — Live Activity (batch)
**Body**: Array of condition_id strings
**Response**: LiveActivityMarket[]

### GET /markets/live-activity/{condition_id} — Live Activity (single)
**Path**: `condition_id` (string, **required**)
**Response**: LiveActivityMarket

---

## Server

### GET /time — Server Time
No auth, no params.
**Response**: Unix timestamp (int64)
