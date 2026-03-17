# Relayer & Bridge APIs

---

## Relayer API — Gasless Transaction Submission

> Base URL: `https://relayer-v2.polymarket.com`
> Auth: Builder API Key or Relayer API Key

### POST /submit — Submit Transaction
**Auth**: Builder/Relayer API Key
**Body**:
```json
{
  "from": "0x...",
  "to": "0x...",
  "proxyWallet": "0x...",
  "data": "0x...",
  "nonce": "42",
  "signature": "0x...",
  "signatureParams": {
    "gasPrice": "0",
    "operation": 0,
    "safeTxnGas": "0",
    "baseGas": "0",
    "gasToken": "0x0000000000000000000000000000000000000000",
    "refundReceiver": "0x0000000000000000000000000000000000000000"
  },
  "type": "SAFE"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | address | Yes | Signer address |
| `to` | address | Yes | Target contract |
| `proxyWallet` | address | Yes | User proxy wallet |
| `data` | hex | Yes | Encoded tx data |
| `nonce` | string | Yes | Current nonce |
| `signature` | hex | Yes | Transaction signature |
| `signatureParams` | object | Yes | Gas and safety params |
| `type` | enum | Yes | `SAFE` or `PROXY` |

**Response 200**:
```json
{
  "transactionID": "uuid",
  "transactionHash": "0x...",
  "state": "STATE_NEW"
}
```
**Errors**: 400, 401, 429 (quota exceeded — 25/min), 500

### GET /transaction — Get Transaction by ID
**Query**: `id` (UUID, **required**)
**Response**:
```json
[{
  "transactionID": "uuid",
  "transactionHash": "0x...",
  "from": "0x...",
  "to": "0x...",
  "proxyAddress": "0x...",
  "data": "0x...",
  "nonce": "42",
  "value": "0",
  "signature": "0x...",
  "state": "STATE_CONFIRMED",
  "type": "SAFE",
  "owner": "uuid",
  "createdAt": "2024-01-01T00:00:00Z",
  "updatedAt": "2024-01-01T00:00:05Z"
}]
```

**Transaction states**:
- `STATE_NEW` — Submitted
- `STATE_EXECUTED` — Sent to chain
- `STATE_MINED` — In a block
- `STATE_CONFIRMED` — Finalized
- `STATE_INVALID` — Invalid data
- `STATE_FAILED` — Execution failed

### GET /transactions — Recent User Transactions
**Auth**: Builder/Relayer API Key
**Response**: Transaction[]

### GET /nonce — Current Nonce
**Query**: `address` (address, **required**), `type` (enum: PROXY | SAFE, **required**)
**Response**: `{nonce: "42"}`

### GET /relay-payload — Relayer Address + Nonce
**Query**: `address` (address, **required**), `type` (enum: PROXY | SAFE, **required**)
**Response**: `{address: "0x...", nonce: "42"}`

### GET /deployed — Check Safe Deployment
**Query**: `address` (address, **required**)
**Response**: `{deployed: true}`

### GET /relayer/api/keys — List Relayer API Keys
**Auth**: Relayer API Key
**Response**: `[{apiKey: "uuid", address: "0x...", createdAt: "...", updatedAt: "..."}]`

---

## Bridge API — Deposits & Withdrawals

> Base URL: `https://bridge.polymarket.com`
> Auth: None (all public)
> Note: Bridges are proxied via fun.xyz, not handled by Polymarket directly.

### GET /supported-assets — Supported Assets
No params.
**Response**:
```json
{
  "supportedAssets": [{
    "chainId": 137,
    "chainName": "Polygon",
    "token": {
      "name": "USD Coin",
      "symbol": "USDC",
      "address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
      "decimals": 6
    },
    "minCheckoutUsd": 5
  }]
}
```

### POST /quote — Get Bridge Quote
**Body**:
```json
{
  "fromAmountBaseUnit": "1000000",
  "fromChainId": 1,
  "fromTokenAddress": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
  "recipientAddress": "0x...",
  "toChainId": 137,
  "toTokenAddress": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
}
```

**Response**:
```json
{
  "estCheckoutTimeMs": 120000,
  "estFeeBreakdown": {
    "appFeeLabel": "Polymarket",
    "appFeePercent": 0,
    "appFeeUsd": 0,
    "fillCostPercent": 0.1,
    "fillCostUsd": 0.10,
    "gasUsd": 0.50,
    "maxSlippage": 0.5,
    "minReceived": "999000",
    "swapImpact": 0,
    "totalImpact": 0.6,
    "totalImpactUsd": 0.60
  },
  "estInputUsd": 1.00,
  "estOutputUsd": 0.99,
  "estToTokenBaseUnit": "990000",
  "quoteId": "uuid"
}
```

### POST /deposit — Create Deposit Address
**Body**: `{address: "0x..."}`
**Response 201**:
```json
{
  "address": {
    "evm": "0x...",
    "svm": "...",
    "btc": "..."
  },
  "note": "Send USDC to this address"
}
```

### POST /withdraw — Create Withdrawal
**Body**:
```json
{
  "address": "0x...",
  "toChainId": 1,
  "toTokenAddress": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
  "recipientAddr": "0x..."
}
```
**Response 201**: Same address format as deposit.

### GET /status/{address} — Transaction Status
**Path**: `address` (string, **required**)
**Response**:
```json
{
  "transactions": [{
    "fromChainId": 1,
    "fromTokenAddress": "0x...",
    "fromAmountBaseUnit": "1000000",
    "toChainId": 137,
    "toTokenAddress": "0x...",
    "status": "COMPLETED",
    "txHash": "0x...",
    "createdTimeMs": 1700000000000
  }]
}
```

**Transaction statuses**:
- `DEPOSIT_DETECTED`
- `PROCESSING`
- `ORIGIN_TX_CONFIRMED`
- `SUBMITTED`
- `COMPLETED`
- `FAILED`
