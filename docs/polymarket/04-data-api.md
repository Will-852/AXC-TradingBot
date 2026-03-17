# Data API — Positions, Trades, Analytics

> Base URL: `https://data-api.polymarket.com`
> Auth: None (all public)

---

## Positions

### GET /positions — Current Open Positions
**Query**:
| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `user` | address | **Yes** | — | Wallet address |
| `market` | string | No | — | Comma-separated condition IDs (mutually exclusive with eventId) |
| `eventId` | string | No | — | Comma-separated event IDs (mutually exclusive with market) |
| `sizeThreshold` | number | No | 1 | Min position size |
| `redeemable` | bool | No | false | Only redeemable |
| `mergeable` | bool | No | false | Only mergeable |
| `limit` | int | No | 100 | Max 500 |
| `offset` | int | No | 0 | Max 10000 |
| `sortBy` | enum | No | TOKENS | CURRENT, INITIAL, TOKENS, CASHPNL, PERCENTPNL, TITLE, RESOLVING, PRICE, AVGPRICE |
| `sortDirection` | enum | No | DESC | ASC or DESC |
| `title` | string | No | — | Search by title (max 100 chars) |

**Response**: Position[]
```json
{
  "proxyWallet": "0x...",
  "asset": "71321045...",
  "conditionId": "0xabc...",
  "size": 150.0,
  "avgPrice": 0.42,
  "initialValue": 63.0,
  "currentValue": 97.5,
  "cashPnl": 34.5,
  "percentPnl": 54.76,
  "totalBought": 63.0,
  "realizedPnl": 0,
  "percentRealizedPnl": 0,
  "curPrice": 0.65,
  "redeemable": false,
  "mergeable": false,
  "title": "Will X happen?",
  "slug": "will-x-happen",
  "icon": "https://...",
  "eventSlug": "event-slug",
  "outcome": "Yes",
  "outcomeIndex": 0,
  "oppositeOutcome": "No",
  "oppositeAsset": "89234567...",
  "endDate": "2026-12-31T00:00:00Z",
  "negativeRisk": false
}
```

### GET /closed-positions — Closed Positions
**Query**:
| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `user` | address | **Yes** | — | Wallet address |
| `market` | string | No | — | Condition IDs (mutually exclusive with eventId) |
| `eventId` | string | No | — | Event IDs |
| `title` | string | No | — | Search |
| `limit` | int | No | 10 | Max 50 |
| `offset` | int | No | 0 | Max 100000 |
| `sortBy` | enum | No | REALIZEDPNL | REALIZEDPNL, TITLE, PRICE, AVGPRICE, TIMESTAMP |
| `sortDirection` | enum | No | DESC | ASC or DESC |

**Response**: ClosedPosition[]

### GET /value — Total Portfolio Value
**Query**: `user` (address, **required**), `market` (optional)
**Response**: `[{user: "0x...", value: 1234.56}]`

---

## Trades

### GET /trades — Trade History
**Query**:
| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `limit` | int | No | 100 | Max 10000 |
| `offset` | int | No | 0 | Max 10000 |
| `takerOnly` | bool | No | true | Taker trades only |
| `filterType` | enum | No | — | CASH or TOKENS |
| `filterAmount` | number | No | — | Min amount |
| `market` | string | No | — | Condition IDs (mutually exclusive with eventId) |
| `eventId` | string | No | — | Event IDs |
| `user` | address | No | — | Filter by user |
| `side` | enum | No | — | BUY or SELL |

**Response**: Trade[]
```json
{
  "proxyWallet": "0x...",
  "side": "BUY",
  "asset": "71321045...",
  "conditionId": "0xabc...",
  "size": 100.0,
  "price": 0.55,
  "timestamp": "2024-01-01T00:00:00Z",
  "title": "Will X happen?",
  "slug": "will-x-happen",
  "icon": "https://...",
  "eventSlug": "event-slug",
  "outcome": "Yes",
  "outcomeIndex": 0,
  "name": "Trader",
  "pseudonym": "Trader123",
  "transactionHash": "0x..."
}
```

---

## Activity

### GET /activity — User Activity Stream
**Query**:
| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `user` | address | **Yes** | — | Wallet address |
| `limit` | int | No | 100 | Max 500 |
| `offset` | int | No | 0 | Max 10000 |
| `market` | string | No | — | Condition IDs |
| `eventId` | string | No | — | Event IDs |
| `type` | string | No | — | Comma-separated: TRADE, SPLIT, MERGE, REDEEM, REWARD, CONVERSION, MAKER_REBATE |
| `start` | int | No | — | Unix timestamp |
| `end` | int | No | — | Unix timestamp |
| `sortBy` | enum | No | TIMESTAMP | TIMESTAMP, TOKENS, CASH |
| `sortDirection` | enum | No | DESC | ASC or DESC |
| `side` | enum | No | — | BUY or SELL |

**Response**: Activity[]

---

## Market Analytics

### GET /holders — Top Holders
**Query**: `market` (comma-separated condition IDs, **required**), `limit` (default 20, max 20), `minBalance` (default 1, max 999999)
**Response**:
```json
[{
  "token": "71321045...",
  "holders": [{
    "proxyWallet": "0x...",
    "bio": "...",
    "asset": "71321045...",
    "pseudonym": "Trader123",
    "amount": 5000,
    "displayUsernamePublic": true,
    "outcomeIndex": 0,
    "name": "John"
  }]
}]
```

### GET /v1/market-positions — All Positions for Market
**Query**:
| Param | Type | Required | Default |
|-------|------|----------|---------|
| `market` | Hash64 | **Yes** | — |
| `user` | address | No | — |
| `status` | enum | No | ALL (OPEN, CLOSED, ALL) |
| `sortBy` | enum | No | TOTAL_PNL (TOKENS, CASH_PNL, REALIZED_PNL, TOTAL_PNL) |
| `sortDirection` | enum | No | DESC |
| `limit` | int | No | 50 (max 500) |
| `offset` | int | No | 0 (max 10000) |

**Response**: MetaMarketPositionV1[]

### GET /oi — Open Interest
**Query**: `market` (comma-separated condition IDs, optional)
**Response**: `[{market: "condition_id", value: 150000}]`

### GET /live-volume — Live Volume for Event
**Query**: `id` (int, **required**, min 1)
**Response**: `[{total: 500000, markets: [{market: "condition_id", value: 250000}]}]`

### GET /traded — Total Markets Traded
**Query**: `user` (address, **required**)
**Response**: `{user: "0x...", traded: 42}`

---

## Leaderboard

### GET /v1/leaderboard — Trader Leaderboard
**Query**:
| Param | Type | Required | Default | Options |
|-------|------|----------|---------|---------|
| `category` | enum | No | OVERALL | OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, MENTIONS, WEATHER, ECONOMICS, TECH, FINANCE |
| `timePeriod` | enum | No | DAY | DAY, WEEK, MONTH, ALL |
| `orderBy` | enum | No | PNL | PNL, VOL |
| `limit` | int | No | 25 | Max 50 |
| `offset` | int | No | 0 | Max 1000 |
| `user` | address | No | — | Filter specific user |
| `userName` | string | No | — | Search by name |

**Response**: TraderLeaderboardEntry[]
```json
{
  "rank": 1,
  "proxyWallet": "0x...",
  "userName": "TopTrader",
  "vol": 1500000,
  "pnl": 250000,
  "profileImage": "https://...",
  "xUsername": "toptrader_x",
  "verifiedBadge": true
}
```

---

## Builders

### GET /v1/builders/leaderboard — Builder Leaderboard
**Query**: `timePeriod` (DAY/WEEK/MONTH/ALL, default DAY), `limit` (max 50), `offset` (max 1000)
**Response**: LeaderboardEntry[]

### GET /v1/builders/volume — Builder Volume Time-Series
**Query**: `timePeriod` (DAY/WEEK/MONTH/ALL, default DAY)
**Response**: BuilderVolumeEntry[]

---

## Accounting

### GET /v1/accounting/snapshot — Download Snapshot (ZIP)
**Query**: `user` (address, **required**)
**Response**: ZIP file containing `positions.csv` and `equity.csv`

---

## Health

### GET /
No params. **Response**: `{data: "OK"}`
