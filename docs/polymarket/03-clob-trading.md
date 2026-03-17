# CLOB API — Trading, Auth & Account (Authenticated)

> Base URL: `https://clob.polymarket.com`
> All endpoints require **L2 Auth** unless noted otherwise.

---

## Order Placement

### POST /order — Place Single Order
**Auth**: L2
**Body** (SendOrder):
```json
{
  "order": {
    "maker": "0x...",
    "signer": "0x...",
    "taker": "0x0000000000000000000000000000000000000000",
    "tokenId": "71321045...",
    "makerAmount": "50000000",
    "takerAmount": "100000000",
    "side": "BUY",
    "expiration": "0",
    "nonce": "0",
    "feeRateBps": "0",
    "signature": "0x...",
    "salt": 123456789,
    "signatureType": 2
  },
  "owner": "uuid-of-api-key",
  "orderType": "GTC",
  "deferExec": false
}
```

**Order fields**:
| Field | Type | Description |
|-------|------|-------------|
| `maker` | address | Order creator |
| `signer` | address | Signature creator |
| `taker` | address | Counterparty (0x0 = any) |
| `tokenId` | string | CLOB token ID |
| `makerAmount` | string | Amount maker gives (raw units) |
| `takerAmount` | string | Amount maker wants (raw units) |
| `side` | enum | `BUY` or `SELL` |
| `expiration` | string | Unix timestamp (`"0"` = no expiry) |
| `nonce` | string | Unique nonce |
| `feeRateBps` | string | Fee rate in bps |
| `signature` | hex | EIP-712 order signature |
| `salt` | int | Random salt |
| `signatureType` | int | 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE |

**Wrapper fields**:
| Field | Type | Description |
|-------|------|-------------|
| `owner` | UUID | API key owner ID |
| `orderType` | enum | `GTC`, `GTD`, `FOK`, `FAK` |
| `deferExec` | bool | Defer execution (rare) |

**Response 200** (SendOrderResponse):
```json
{
  "success": true,
  "orderID": "0xabc...",
  "status": "live",
  "makingAmount": "50000000",
  "takingAmount": "100000000",
  "transactionsHashes": ["0x..."],
  "tradeIDs": ["trade-id"],
  "errorMsg": ""
}
```
- `status`: `live` (resting) | `matched` (filled) | `delayed` (queued)

**Errors**: 400 (invalid payload / owner mismatch / banned / closed-only / post-only crosses book / tick size / min size / insufficient balance / invalid nonce / FOK unfilled), 401, 500, 503

### POST /orders — Place Multiple Orders (max 15)
**Auth**: L2
**Body**: SendOrder[] (maxItems: 15)
**Response**: SendOrderResponse[]
**Errors**: 400 (empty / >15), 401, 500, 503

---

## Order Cancellation

### DELETE /order — Cancel Single Order
**Auth**: L2
**Body**: `{"orderID": "0xabc..."}`
**Response**:
```json
{
  "canceled": ["0xabc..."],
  "not_canceled": {"0xdef...": "reason"}
}
```
Note: Works even in cancel-only mode.

### DELETE /orders — Cancel Multiple Orders (max 3000)
**Auth**: L2
**Body**: `["order_id_1", "order_id_2", ...]` (maxItems: 3000)
**Response**: CancelOrdersResponse
Note: Duplicate IDs ignored.

### DELETE /cancel-all — Cancel All Orders
**Auth**: L2
No body.
**Response**: CancelOrdersResponse

### DELETE /cancel-market-orders — Cancel All for Market
**Auth**: L2
**Body**: `{"market": "condition_id", "asset_id": "token_id"}`
**Response**: CancelOrdersResponse

---

## Order Query

### GET /orders — List Orders
**Auth**: L2 or Builder Auth
**Query**: `id` (optional), `market` (optional), `asset_id` (optional), `next_cursor` (optional)
**Response**:
```json
{
  "limit": 100,
  "next_cursor": "...",
  "count": 50,
  "data": [
    {
      "id": "0xabc...",
      "status": "ORDER_STATUS_LIVE",
      "owner": "uuid",
      "maker_address": "0x...",
      "market": "condition_id",
      "asset_id": "token_id",
      "side": "BUY",
      "original_size": "100",
      "size_matched": "50",
      "price": "0.55",
      "outcome": "Yes",
      "expiration": "0",
      "order_type": "GTC",
      "associate_trades": ["trade_id"],
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

### GET /order/{orderID} — Single Order
**Auth**: L2 or Builder Auth
**Path**: `orderID` (string, **required**)
**Response**: OpenOrder | 404

---

## Trade History

### GET /trades — User Trades
**Auth**: L2
**Query**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | No | Trade ID |
| `maker_address` | address | **Yes** | Pattern: `^0x[a-fA-F0-9]{40}$` |
| `market` | string | No | Condition ID |
| `asset_id` | string | No | Token ID |
| `before` | string | No | Before cursor |
| `after` | string | No | After cursor |
| `next_cursor` | string | No | Pagination |

**Response**:
```json
{
  "limit": 100,
  "next_cursor": "...",
  "count": 25,
  "data": [
    {
      "id": "trade_id",
      "taker_order_id": "0x...",
      "market": "condition_id",
      "asset_id": "token_id",
      "side": "BUY",
      "size": "100",
      "price": "0.55",
      "fee_rate_bps": "0",
      "status": "CONFIRMED",
      "match_time": "2024-01-01T00:00:00Z",
      "last_update": "2024-01-01T00:00:01Z",
      "outcome": "Yes",
      "owner": "uuid",
      "maker_address": "0x...",
      "transaction_hash": "0x...",
      "trader_side": "TAKER"
    }
  ]
}
```
- `status`: CONFIRMED | FAILED | RETRYING | MATCHED | MINED
- `trader_side`: TAKER | MAKER

### GET /builder/trades — Builder Trades
**Auth**: Builder Auth
**Query**: `id`, `builder`, `market`, `asset_id`, `before`, `after`, `next_cursor`
**Response**: BuilderTradesResponse

---

## Account Management

### POST /auth/api-key — Create API Key
**Auth**: **L1** (EIP-712)
**Response**: `{apiKey: "uuid", secret: "base64...", passphrase: "hex..."}`

### GET /auth/derive-api-key — Derive Existing Key
**Auth**: **L1**
**Response**: `{apiKey, secret, passphrase}`

### DELETE /auth/api-key — Delete API Key
**Auth**: L2
**Response**: `"OK"`

### GET /auth/api-keys — List All API Keys
**Auth**: **L1**
**Response**: `{apiKeys: ["uuid1", "uuid2"]}`

### GET /auth/ban-status/closed-only — Check Restrictions
**Auth**: L2
**Response**: `{closed_only: false}`

### POST /auth/builder-api-key — Create Builder Key
**Auth**: L2
**Response**: `{key: "uuid", secret: "base64", passphrase: "hex"}`

### GET /auth/builder-api-key — List Builder Keys
**Auth**: L2
**Response**: `["uuid1", "uuid2"]`

### DELETE /auth/builder-api-key — Delete Builder Key
**Auth**: Builder Auth
**Response**: `"OK"`

---

## Balance & Allowance

### GET /balance-allowance — Query Balance
**Auth**: L2
**Query**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `asset_type` | enum | **Yes** | `COLLATERAL` (USDC) or `CONDITIONAL` (tokens) |
| `token_id` | string | No | Token ID (default: "-1") |
| `signature_type` | int | No | 0/1/2 (default: 0) |

**Response**: `{balance: "1000000", allowances: {"0x...": "999999999"}}`

### PUT /balance-allowance — Force Update
**Auth**: L2
Same query params. **Response**: `{}`

### GET /balance-allowance/update — Force Update (GET)
**Auth**: L2
Same query params. **Response**: BalanceAllowanceResponse

---

## Notifications

### GET /notifications — Unread Notifications
**Auth**: L2
**Query**: `signature_type` (int 0/1/2, **required**)
**Response**: `[{id, owner, type, payload, timestamp}]`

### DELETE /notifications — Mark as Read
**Auth**: L2
**Query**: `ids` (comma-separated, **required**)
**Response**: `"OK"`

---

## Rewards

### GET /rewards/markets/current — Active Reward Configs (public)
**Query**: `sponsored` (bool), `next_cursor`
**Response**: PaginatedCurrentReward

### GET /rewards/markets/{condition_id} — Market Rewards (public)
**Path**: `condition_id` (string, **required**)
**Query**: `sponsored`, `next_cursor`
**Response**: PaginatedMarketReward

### GET /rewards/markets/multi — Multiple Market Rewards (public)
**Query**: `q`, `tag_slug`, `event_id`, `event_title`, `order_by`, `position`, `min/max_volume_24hr`, `min/max_spread`, `min/max_price`, `next_cursor`, `page_size` (max 500)
**Response**: PaginatedMultiMarketInfo

### GET /rewards/user — User Earnings by Date
**Auth**: L2
**Query**: `date` (YYYY-MM-DD, **required**), `signature_type`, `maker_address`, `sponsored`, `next_cursor`
**Response**: PaginatedUserEarnings

### GET /rewards/user/total — Total Earnings by Date
**Auth**: L2
**Query**: `date` (**required**), `signature_type`, `maker_address`, `sponsored`
**Response**: TotalUserEarning[]

### GET /rewards/user/percentages — Reward Percentages
**Auth**: L2
**Query**: `signature_type`, `maker_address`
**Response**: `{condition_id: percentage}`

### GET /rewards/user/markets — User Earnings + Market Config
**Auth**: L2
**Query**: `date`, `signature_type`, `maker_address`, `sponsored`, `next_cursor`, `page_size`, `q`, `tag_slug`, `favorite_markets`, `no_competition`, `only_mergeable`, `only_open_orders`, `only_open_positions`, `order_by`, `position`
**Response**: PaginatedUserRewardsMarkets

---

## Rebates

### GET /rebates/current — Rebated Fees (public)
**Query**: `date` (YYYY-MM-DD, **required**), `maker_address` (address, **required**)
**Response**: `[{date, condition_id, asset_address, maker_address, rebated_fees_usdc}]`

---

## Misc

### POST /heartbeats — Keep Session Alive
**Auth**: L2
No body. **Response**: `{status: "ok"}`
Purpose: Prevent automatic cancellation of resting orders.

### GET /order-scoring — Order Scoring Status
**Auth**: L2
**Query**: `order_id` (string, **required**)
**Response**: `{scoring: true}`
